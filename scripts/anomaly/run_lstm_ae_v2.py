"""
LSTM-AE 이상탐지 v2 - 전체 계량기 전체 측정항목
논문: Shrestha et al. (2024)
기존 run_lstm_ae.py 대비: 그룹 합산 대신 전체 계량기 개별 피처 사용
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
import matplotlib.pyplot as plt
import mlflow
from tqdm import tqdm

warnings.filterwarnings("ignore")
load_dotenv()

DB_URL = (
    f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
)
TZ_LOCAL = "Europe/Berlin"
DEVICE   = torch.device("cuda" if torch.cuda.is_available() else "cpu")

TIMESTEP   = 24
EPOCHS     = 50
BATCH_SIZE = 8
LR         = 0.001
K_MSD      = 3

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


def load_all_meters(start_str: str, end_str: str) -> pd.DataFrame:
    """전체 계량기 전체 측정항목 로드 후 피벗."""
    tz = timezone.utc
    start = datetime.fromisoformat(start_str).replace(tzinfo=tz)
    end   = datetime.fromisoformat(end_str).replace(tzinfo=tz)

    sql = """
        SELECT ts, meter_urn, measurement, value
        FROM ems.cr_measurement_1h
        WHERE ts >= %s AND ts < %s
        ORDER BY ts;
    """
    conn = psycopg2.connect(DB_URL)
    try:
        cur = conn.cursor()
        cur.execute(sql, (start, end))
        cols = [d[0] for d in cur.description]
        raw  = pd.DataFrame(cur.fetchall(), columns=cols)
    finally:
        conn.close()

    if raw.empty:
        return pd.DataFrame()

    raw["ts"]  = pd.to_datetime(raw["ts"], utc=True).dt.tz_convert(TZ_LOCAL)
    raw["col"] = raw["meter_urn"] + "__" + raw["measurement"]
    pivot = raw.pivot_table(index="ts", columns="col", values="value", aggfunc="mean")
    return pivot.reset_index()


def mask_gateway_failures(df: pd.DataFrame) -> pd.DataFrame:
    ts = pd.to_datetime(df["ts"])
    mask = pd.Series(False, index=df.index)
    for s, e in GATEWAY_FAILURE_RANGES:
        mask |= (ts >= s) & (ts < e)
    n = mask.sum()
    if n > 0:
        print(f"  게이트웨이 장애 구간 제외: {n}행")
    return df[~mask].copy()


def make_sequences(data: np.ndarray, timestep: int) -> np.ndarray:
    return np.array([data[i:i+timestep] for i in range(len(data) - timestep)])


class LSTMAutoencoder(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int = 64,
                 latent_dim: int = 32, seq_len: int = TIMESTEP):
        super().__init__()
        self.encoder_lstm = nn.LSTM(input_dim, hidden_dim, batch_first=True)
        self.encoder_fc   = nn.Linear(hidden_dim, latent_dim)
        self.decoder_fc   = nn.Linear(latent_dim, hidden_dim)
        self.decoder_lstm = nn.LSTM(hidden_dim, hidden_dim, batch_first=True)
        self.out          = nn.Linear(hidden_dim, input_dim)
        self.seq_len      = seq_len

    def forward(self, x):
        _, (h, _) = self.encoder_lstm(x)
        z = self.encoder_fc(h[-1])
        d = self.decoder_fc(z).unsqueeze(1).repeat(1, self.seq_len, 1)
        out, _ = self.decoder_lstm(d)
        return self.out(out)


def compute_errors(model, X, batch_size=256):
    model.eval()
    errors = []
    with torch.no_grad():
        for i in range(0, len(X), batch_size):
            xb    = torch.FloatTensor(X[i:i+batch_size]).to(DEVICE)
            recon = model(xb)
            mse   = ((xb - recon) ** 2).mean(dim=(1, 2))
            errors.extend(mse.cpu().numpy().tolist())
    return np.array(errors)


def main():
    print(f"Device: {DEVICE}")
    mlflow.set_experiment("LSTM-AE-Anomaly-v2")

    with mlflow.start_run(run_name="lstm_ae_all_meters"):
        mlflow.log_params({
            "timestep": TIMESTEP, "epochs": EPOCHS,
            "batch_size": BATCH_SIZE, "lr": LR, "k_msd": K_MSD,
            "version": "v2_all_meters"
        })

        # 1. 데이터 로드
        print("\n[1/8] 학습 데이터 로드 중 (2018~2021) 전체 계량기...")
        df_train = load_all_meters(TRAIN_START, TRAIN_END)
        print(f"  train: {len(df_train)}행, 피처 수: {len(df_train.columns)-1}")

        print("[1/8] 테스트 데이터 로드 중 (2023)...")
        df_test = load_all_meters(TEST_START, TEST_END)
        print(f"  test: {len(df_test)}행, 피처 수: {len(df_test.columns)-1}")

        # 2. 전처리
        print("\n[2/8] 전처리 중...")
        df_train = mask_gateway_failures(df_train)
        feat_cols = [c for c in df_train.columns if c != "ts"]
        print(f"  사용 피처 수: {len(feat_cols)}")

        # 3. 공통 피처만 사용 (train/test 교집합)
        test_feat_cols = [c for c in df_test.columns if c != "ts"]
        common_cols = [c for c in feat_cols if c in test_feat_cols]
        print(f"  train/test 공통 피처 수: {len(common_cols)}")

        # 4. 정규화
        print("\n[3/8] 정규화 중...")
        scaler = MinMaxScaler()
        X_train_raw = scaler.fit_transform(df_train[common_cols].fillna(0))
        X_test_raw  = scaler.transform(df_test[common_cols].fillna(0))

        # 5. 시퀀스 생성
        print("\n[4/8] 시퀀스 생성 중...")
        X_train_seq = make_sequences(X_train_raw, TIMESTEP)
        X_test_seq  = make_sequences(X_test_raw,  TIMESTEP)
        print(f"  train: {X_train_seq.shape}, test: {X_test_seq.shape}")

        # 6. 모델 학습
        input_dim = X_train_seq.shape[2]
        model = LSTMAutoencoder(input_dim=input_dim).to(DEVICE)
        print(f"\n[5/8] 모델 학습 중... (파라미터 수: {sum(p.numel() for p in model.parameters()):,})")

        Xt      = torch.FloatTensor(X_train_seq).to(DEVICE)
        ds      = TensorDataset(Xt)
        loader  = DataLoader(ds, batch_size=BATCH_SIZE, shuffle=True)
        opt     = torch.optim.Adam(model.parameters(), lr=LR)
        loss_fn = nn.MSELoss()

        model.train()
        for epoch in tqdm(range(EPOCHS), desc="  학습"):
            epoch_loss = 0.0
            for (xb,) in loader:
                opt.zero_grad()
                l = loss_fn(model(xb), xb)
                l.backward()
                opt.step()
                epoch_loss += l.item()
            if (epoch + 1) % 10 == 0:
                print(f"  Epoch [{epoch+1}/{EPOCHS}] loss: {epoch_loss/len(loader):.6f}")

        # 7. 임계치 계산
        print("\n[6/8] 임계치 계산 중...")
        train_errors = compute_errors(model, X_train_seq)
        threshold    = train_errors.mean() + K_MSD * train_errors.std()
        print(f"  학습 오차 평균: {train_errors.mean():.6f}")
        print(f"  학습 오차 std:  {train_errors.std():.6f}")
        print(f"  MSD 임계치 (k={K_MSD}): {threshold:.6f}")

        # 8. 이상탐지
        print("\n[7/8] 테스트 데이터 이상탐지 중...")
        test_errors = compute_errors(model, X_test_seq)
        preds = (test_errors > threshold).astype(int)
        print(f"\n[이상탐지 결과]")
        print(f"  임계치:         {threshold:.6f}")
        print(f"  이상 탐지 건수: {preds.sum()}건")
        print(f"  이상 탐지 비율: {preds.mean()*100:.2f}%")

        mlflow.log_metrics({
            "threshold_msd": threshold,
            "n_anomalies": int(preds.sum()),
            "anomaly_rate": float(preds.mean()),
            "n_features": len(common_cols),
        })

        # 저장
        print("\n[8/8] 저장 중...")
        test_ts = df_test["ts"].iloc[TIMESTEP:].reset_index(drop=True)
        anomaly_idx = np.where(test_errors > threshold)[0]
        anomaly_df = pd.DataFrame({
            "timestamp":   test_ts.iloc[anomaly_idx].values,
            "recon_error": test_errors[anomaly_idx],
            "threshold":   threshold,
        })
        anomaly_df.to_csv("anomaly_results_v2.csv", index=False)
        mlflow.log_artifact("anomaly_results_v2.csv")

        # 시각화
        fig, axes = plt.subplots(2, 1, figsize=(14, 8))
        n_show = min(len(test_errors), 2000)
        axes[0].plot(test_errors[:n_show], alpha=0.7)
        axes[0].axhline(threshold, color="red", linestyle="--", label=f"Threshold (k={K_MSD})")
        axes[0].set_title(f"LSTM-AE v2 (전체 계량기 {len(common_cols)}개 피처) Reconstruction Error")
        axes[0].legend()
        axes[0].grid(True)

        axes[1].plot(test_errors[:n_show], alpha=0.3)
        axes[1].fill_between(range(n_show),
                             [threshold]*n_show,
                             [max(test_errors[:n_show])]*n_show,
                             where=test_errors[:n_show] > threshold,
                             color="red", alpha=0.3, label="Anomaly")
        axes[1].axhline(threshold, color="red", linestyle="--")
        axes[1].set_title("이상 구간 하이라이트")
        axes[1].legend()
        axes[1].grid(True)

        plt.tight_layout()
        plt.savefig("lstm_ae_result_v2.png", dpi=150)
        mlflow.log_artifact("lstm_ae_result_v2.png")

        torch.save(model.state_dict(), "lstm_ae_model_v2.pt")
        mlflow.log_artifact("lstm_ae_model_v2.pt")
        print("완료.")

    print("\n전체 완료.")


if __name__ == "__main__":
    main()
