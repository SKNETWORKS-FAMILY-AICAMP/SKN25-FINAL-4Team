"""
LSTM-AE 단일 계량기 이상탐지
V.Z81 P값 단변량으로 LSTM-AE 학습 후 2023년 이상탐지
기존 통계 기반과 공정한 비교를 위해 단일 계량기 기준으로 실행
"""

import os
import warnings
import numpy as np
import pandas as pd
import psycopg2
from datetime import datetime, timezone
from dotenv import load_dotenv
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from sklearn.preprocessing import MinMaxScaler
from tqdm import tqdm

warnings.filterwarnings("ignore")
load_dotenv()

DB_URL = f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
TZ_LOCAL = "Europe/Berlin"
DEVICE   = torch.device("cuda" if torch.cuda.is_available() else "cpu")

TIMESTEP   = 24
EPOCHS     = 50
BATCH_SIZE = 8
LR         = 0.001
K_MSD      = 6   # k=6으로 통계 기반과 이상 비율 맞춤

METER_URN   = "V.Z81"
MEASUREMENT = "P"

GATEWAY_FAILURE_RANGES = [
    ("2020-02-13", "2020-03-06"),
    ("2020-08-20", "2020-09-17"),
    ("2021-11-15", "2021-12-10"),
    ("2022-05-06", "2022-07-14"),
]

TRAIN_START = "2018-01-01"
TRAIN_END   = "2022-01-01"
TEST_START  = "2023-01-01"
TEST_END    = "2024-01-01"


def load_series(meter_urn, measurement, start_str, end_str):
    tz = timezone.utc
    start = datetime.fromisoformat(start_str).replace(tzinfo=tz)
    end   = datetime.fromisoformat(end_str).replace(tzinfo=tz)
    sql = """
        SELECT ts, value
        FROM ems.cr_measurement_1h
        WHERE meter_urn = %s AND measurement = %s
          AND ts >= %s AND ts < %s
        ORDER BY ts;
    """
    conn = psycopg2.connect(DB_URL)
    try:
        cur = conn.cursor()
        cur.execute(sql, (meter_urn, measurement, start, end))
        rows = cur.fetchall()
    finally:
        conn.close()
    df = pd.DataFrame(rows, columns=["ts", "value"])
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(TZ_LOCAL)
    return df


def mask_gateway(df):
    ts = pd.to_datetime(df["ts"])
    mask = pd.Series(False, index=df.index)
    for s, e in GATEWAY_FAILURE_RANGES:
        mask |= (ts >= s) & (ts < e)
    n = mask.sum()
    if n > 0:
        print(f"  게이트웨이 장애 구간 제외: {n}행")
    return df[~mask].copy()


def make_sequences(data, timestep):
    return np.array([data[i:i+timestep] for i in range(len(data) - timestep)])


class LSTMAutoencoder(nn.Module):
    def __init__(self, hidden_dim=32, latent_dim=16, seq_len=TIMESTEP):
        super().__init__()
        self.encoder_lstm = nn.LSTM(1, hidden_dim, batch_first=True)
        self.encoder_fc   = nn.Linear(hidden_dim, latent_dim)
        self.decoder_fc   = nn.Linear(latent_dim, hidden_dim)
        self.decoder_lstm = nn.LSTM(hidden_dim, hidden_dim, batch_first=True)
        self.out          = nn.Linear(hidden_dim, 1)
        self.seq_len      = seq_len

    def forward(self, x):
        _, (h, _) = self.encoder_lstm(x)
        z = self.encoder_fc(h[-1])
        d = self.decoder_fc(z).unsqueeze(1).repeat(1, self.seq_len, 1)
        out, _ = self.decoder_lstm(d)
        return self.out(out)


def main():
    print(f"Device: {DEVICE}")
    print(f"대상 계량기: {METER_URN} / {MEASUREMENT}")

    print("\n[1/5] 데이터 로드 중...")
    df_train = load_series(METER_URN, MEASUREMENT, TRAIN_START, TRAIN_END)
    df_test  = load_series(METER_URN, MEASUREMENT, TEST_START,  TEST_END)
    print(f"  train: {len(df_train)}행, test: {len(df_test)}행")

    print("\n[2/5] 전처리 중...")
    df_train = mask_gateway(df_train)
    df_train["value"] = df_train["value"].fillna(0)
    df_test["value"]  = df_test["value"].fillna(0)

    scaler = MinMaxScaler()
    train_scaled = scaler.fit_transform(df_train[["value"]]).flatten()
    test_scaled  = scaler.transform(df_test[["value"]]).flatten()

    print("\n[3/5] 시퀀스 생성 중...")
    X_train = make_sequences(train_scaled, TIMESTEP)
    X_test  = make_sequences(test_scaled,  TIMESTEP)
    print(f"  train: {X_train.shape}, test: {X_test.shape}")

    print("\n[4/5] LSTM-AE 학습 중...")
    Xt      = torch.FloatTensor(X_train).unsqueeze(-1).to(DEVICE)
    ds      = TensorDataset(Xt)
    loader  = DataLoader(ds, batch_size=BATCH_SIZE, shuffle=True)
    model   = LSTMAutoencoder().to(DEVICE)
    opt     = torch.optim.Adam(model.parameters(), lr=LR)
    loss_fn = nn.MSELoss()

    model.train()
    for epoch in tqdm(range(EPOCHS), desc="  학습"):
        for (xb,) in loader:
            opt.zero_grad()
            loss_fn(model(xb), xb).backward()
            opt.step()

    print("\n[5/5] 이상탐지 중...")
    model.eval()
    train_errors = []
    with torch.no_grad():
        for i in range(0, len(X_train), 256):
            xb = torch.FloatTensor(X_train[i:i+256]).unsqueeze(-1).to(DEVICE)
            recon = model(xb)
            mse = ((xb - recon) ** 2).mean(dim=(1, 2))
            train_errors.extend(mse.cpu().numpy().tolist())
    train_errors = np.array(train_errors)

    threshold = train_errors.mean() + K_MSD * train_errors.std()
    print(f"  학습 오차 평균: {train_errors.mean():.6f}")
    print(f"  학습 오차 std:  {train_errors.std():.6f}")
    print(f"  MSD 임계치 (k={K_MSD}): {threshold:.6f}")

    test_errors = []
    with torch.no_grad():
        for i in range(0, len(X_test), 256):
            xb = torch.FloatTensor(X_test[i:i+256]).unsqueeze(-1).to(DEVICE)
            recon = model(xb)
            mse = ((xb - recon) ** 2).mean(dim=(1, 2))
            test_errors.extend(mse.cpu().numpy().tolist())
    test_errors = np.array(test_errors)

    test_ts = df_test["ts"].iloc[TIMESTEP:].reset_index(drop=True)
    anomaly_idx = np.where(test_errors > threshold)[0]
    anomaly_ts  = test_ts.iloc[anomaly_idx]

    anomaly_df = pd.DataFrame({
        "timestamp":   anomaly_ts.values,
        "recon_error": test_errors[anomaly_idx],
        "threshold":   threshold,
    })

    out_path = f"outputs/anomaly/lstm_ae_{METER_URN.replace('.', '_')}_{MEASUREMENT}_k{K_MSD}.csv"
    anomaly_df.to_csv(out_path, index=False)

    print(f"\n[결과]")
    print(f"  임계치:         {threshold:.6f}")
    print(f"  이상 탐지 건수: {len(anomaly_df)}건")
    print(f"  이상 탐지 비율: {len(anomaly_df)/len(test_errors)*100:.2f}%")
    print(f"  저장 완료: {out_path}")


if __name__ == "__main__":
    main()
