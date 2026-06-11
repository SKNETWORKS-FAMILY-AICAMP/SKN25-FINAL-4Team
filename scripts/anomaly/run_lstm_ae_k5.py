"""
LSTM-Autoencoder 이상탐지 모델
논문: Shrestha et al. (2024) "Anomaly detection based on LSTM and autoencoders
      using federated learning in smart electric grid",
      Journal of Parallel and Distributed Computing

적용 대상: Honda R&D Europe GmbH 스마트빌딩 에너지 데이터
탐지 방식: 재구성 오차(MSE) 기반 비지도 이상탐지
임계치  : MSD (mean + k*std, k=3) 방식 사용
해상도  : 1시간 단위
분할    : train 2018~2021 (정상 데이터만) / test 2023

실행 방법:
    python run_lstm_ae.py

환경 변수 (.env):
    DATABASE_URL=postgresql://user:password@host:5432/dbname
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
from sklearn.metrics import (accuracy_score, precision_score,
                             recall_score, f1_score,
                             confusion_matrix, roc_auc_score)
import matplotlib.pyplot as plt
import mlflow

warnings.filterwarnings("ignore")
load_dotenv()

# ── 설정 ──────────────────────────────────────────────────────────

DB_URL   = os.getenv("DATABASE_URL")
TZ_LOCAL = "Europe/Berlin"
DEVICE   = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# 논문 확정 파라미터
TIMESTEP   = 24       # 시퀀스 길이
EPOCHS     = 50       # 논문 global epochs
BATCH_SIZE = 8        # 논문 batch_size
LR         = 0.001    # Adam learning rate
K_MSD      = 5        # MSD 임계치 배수 (논문 k=3)

# 이상 구간 마스킹 (app/ems-agent anomaly_agent.py에서 가져옴)
# 해당 구간은 인공 보정 데이터이므로 학습에서 제외
GATEWAY_FAILURE_RANGES = [
    ("2020-02-13", "2020-03-06"),
    ("2020-08-20", "2020-09-17"),
    ("2021-11-15", "2021-12-10"),
    ("2022-05-06", "2022-07-14"),
]

# 데이터 분할 기간 (train 4년 / validation 1년 / test 1년)
TRAIN_START = "2018-01-01"
TRAIN_END   = "2022-01-01"
TEST_START  = "2023-01-01"
TEST_END    = "2024-01-01"

# ── DB 로더 ───────────────────────────────────────────────────────

_GRID_METERS      = ["V.Z81", "V.Z82", "H2.Z35", "H2.Z36", "H2.Z351", "H2.Z361"]
_PV_METERS        = ["H1.Z310", "H2.Z311", "H3.Z312", "V.Z84"]
_CHP_ELEC_PRIMARY = ["H1.ZE20"]
_CHP_ELEC_FALLBACK= ["H1.Z20"]
_HEAT_TOTAL       = ["H1.W11"]
_COOL_ELEC        = ["H1.Z11", "H1.Z12", "H1.Z16", "H1.Z24", "H1.Z25"]
_COOL_OUTPUT      = ["V.K21"]
_WEATHER          = ["WeatherStation.Weather"]
_ALL_METERS = (_GRID_METERS + _PV_METERS + _CHP_ELEC_PRIMARY + _CHP_ELEC_FALLBACK
               + _HEAT_TOTAL + _COOL_ELEC + _COOL_OUTPUT + _WEATHER)


def load_range(start_str: str, end_str: str) -> pd.DataFrame:
    tz = timezone.utc
    start = datetime.fromisoformat(start_str).replace(tzinfo=tz)
    end   = datetime.fromisoformat(end_str).replace(tzinfo=tz)

    meters_sql = ",".join(f"'{m}'" for m in _ALL_METERS)
    sql = f"""
        SELECT ts, meter_urn, measurement, value
        FROM ems.cr_measurement_1h
        WHERE ts >= %s AND ts < %s
          AND meter_urn IN ({meters_sql})
          AND measurement IN ('P', 'W', 'Igm', 'Ta')
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

    def _sum_abs(cols):
        ex = [c for c in cols if c in pivot.columns]
        return pivot[ex].abs().sum(axis=1, min_count=1) if ex else pd.Series(np.nan, index=pivot.index)

    def _sum(cols):
        ex = [c for c in cols if c in pivot.columns]
        return pivot[ex].sum(axis=1, min_count=1) if ex else pd.Series(np.nan, index=pivot.index)

    df = pd.DataFrame(index=pivot.index)
    df["grid_P"]      = _sum([f"{m}__P" for m in _GRID_METERS])
    df["pv_P"]        = _sum_abs([f"{m}__P" for m in _PV_METERS])
    df["heat_total_P"]= _sum([f"{m}__P" for m in _HEAT_TOTAL])
    df["cool_output_P"]= _sum([f"{m}__P" for m in _COOL_OUTPUT])
    df["cool_elec_P"] = _sum([f"{m}__P" for m in _COOL_ELEC])

    _ze20, _z20 = "H1.ZE20__P", "H1.Z20__P"
    if _ze20 in pivot.columns and _z20 in pivot.columns:
        df["chp_P"] = pivot[_ze20].fillna(pivot[_z20]).abs()
    elif _ze20 in pivot.columns:
        df["chp_P"] = pivot[_ze20].abs()
    elif _z20 in pivot.columns:
        df["chp_P"] = pivot[_z20].abs()
    else:
        df["chp_P"] = np.nan

    df["Ta"]  = pivot.get("WeatherStation.Weather__Ta",  np.nan)
    df["Igm"] = pivot.get("WeatherStation.Weather__Igm", np.nan)

    # COP
    df["cop"] = df["cool_output_P"] / df["cool_elec_P"].replace(0, np.nan)

    return df.reset_index()


def mask_gateway_failures(df: pd.DataFrame) -> pd.DataFrame:
    """게이트웨이 장애 구간 마스킹 (학습 데이터에서 제외)."""
    ts = pd.to_datetime(df["ts"])
    mask = pd.Series(False, index=df.index)
    for s, e in GATEWAY_FAILURE_RANGES:
        mask |= (ts >= s) & (ts < e)
    n_masked = mask.sum()
    if n_masked > 0:
        print(f"  게이트웨이 장애 구간 제외: {n_masked}행")
    return df[~mask].copy()


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """이상탐지 입력 피처 구성."""
    ts = pd.to_datetime(df["ts"])
    df["hour_sin"]  = np.sin(2 * np.pi * ts.dt.hour / 24)
    df["hour_cos"]  = np.cos(2 * np.pi * ts.dt.hour / 24)
    df["dow_sin"]   = np.sin(2 * np.pi * ts.dt.dayofweek / 7)
    df["dow_cos"]   = np.cos(2 * np.pi * ts.dt.dayofweek / 7)
    df["month_sin"] = np.sin(2 * np.pi * ts.dt.month / 12)
    df["month_cos"] = np.cos(2 * np.pi * ts.dt.month / 12)
    return df


# ── LSTM-AE 모델 ──────────────────────────────────────────────────

class LSTMEncoder(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int, latent_dim: int):
        super().__init__()
        self.lstm = nn.LSTM(input_dim, hidden_dim, batch_first=True)
        self.fc   = nn.Linear(hidden_dim, latent_dim)

    def forward(self, x):
        _, (h, _) = self.lstm(x)
        return self.fc(h[-1])


class LSTMDecoder(nn.Module):
    def __init__(self, latent_dim: int, hidden_dim: int,
                 output_dim: int, seq_len: int):
        super().__init__()
        self.seq_len = seq_len
        self.fc      = nn.Linear(latent_dim, hidden_dim)
        self.lstm    = nn.LSTM(hidden_dim, hidden_dim, batch_first=True)
        self.out     = nn.Linear(hidden_dim, output_dim)

    def forward(self, z):
        h = self.fc(z).unsqueeze(1).repeat(1, self.seq_len, 1)
        out, _ = self.lstm(h)
        return self.out(out)


class LSTMAutoencoder(nn.Module):
    """
    논문 Fig. 2 기반 LSTM Encoder-Decoder 구조.
    Encoder: LSTM → latent space
    Decoder: latent space → LSTM → 재구성
    """
    def __init__(self, input_dim: int, hidden_dim: int = 64,
                 latent_dim: int = 32, seq_len: int = TIMESTEP):
        super().__init__()
        self.encoder = LSTMEncoder(input_dim, hidden_dim, latent_dim)
        self.decoder = LSTMDecoder(latent_dim, hidden_dim, input_dim, seq_len)

    def forward(self, x):
        z    = self.encoder(x)
        recon = self.decoder(z)
        return recon


# ── 임계치 계산 ───────────────────────────────────────────────────

def compute_reconstruction_errors(model: LSTMAutoencoder,
                                   X: np.ndarray) -> np.ndarray:
    """각 시퀀스의 MSE 재구성 오차 계산 (논문 수식 3)."""
    model.eval()
    errors = []
    with torch.no_grad():
        for i in range(0, len(X), 256):
            xb    = torch.FloatTensor(X[i:i+256]).to(DEVICE)
            recon = model(xb)
            mse   = ((xb - recon) ** 2).mean(dim=(1, 2))
            errors.extend(mse.cpu().numpy().tolist())
    return np.array(errors)


def compute_threshold_msd(errors: np.ndarray, k: float = K_MSD) -> float:
    """
    MSD 임계치 계산 (논문 수식 5):
    τ_MSD = mean(MSE_err) + k * std(MSE_err)
    """
    return errors.mean() + k * errors.std()


# ── 학습 및 평가 ──────────────────────────────────────────────────

def make_sequences(data: np.ndarray, timestep: int) -> np.ndarray:
    """슬라이딩 윈도우로 시퀀스 생성."""
    return np.array([data[i:i+timestep] for i in range(len(data) - timestep)])


def train_model(model: LSTMAutoencoder,
                X_train: np.ndarray,
                epochs: int = EPOCHS,
                batch_size: int = BATCH_SIZE,
                lr: float = LR) -> list[float]:
    """정상 데이터만으로 LSTM-AE 학습."""
    Xt     = torch.FloatTensor(X_train).to(DEVICE)
    ds     = TensorDataset(Xt)
    loader = DataLoader(ds, batch_size=batch_size, shuffle=True)
    opt    = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.MSELoss()
    losses = []

    model.train()
    for epoch in range(epochs):
        epoch_loss = 0.0
        for (xb,) in loader:
            opt.zero_grad()
            recon = model(xb)
            loss  = loss_fn(recon, xb)
            loss.backward()
            opt.step()
            epoch_loss += loss.item()
        avg_loss = epoch_loss / len(loader)
        losses.append(avg_loss)
        if (epoch + 1) % 10 == 0:
            print(f"  Epoch [{epoch+1}/{epochs}] loss: {avg_loss:.6f}")

    return losses


def evaluate_model(errors: np.ndarray,
                   threshold: float,
                   labels: np.ndarray | None = None) -> dict:
    """
    재구성 오차 기반 이상 판정 및 성능 평가.
    labels: 1=이상, 0=정상 (없으면 임계치 초과만 리포트)
    """
    preds = (errors > threshold).astype(int)
    result = {
        "threshold": threshold,
        "n_anomalies": preds.sum(),
        "anomaly_rate": preds.mean(),
    }

    if labels is not None:
        result["accuracy"]  = accuracy_score(labels, preds)
        result["precision"] = precision_score(labels, preds, zero_division=0)
        result["recall"]    = recall_score(labels, preds, zero_division=0)
        result["f1"]        = f1_score(labels, preds, zero_division=0)
        try:
            result["auc_roc"] = roc_auc_score(labels, errors)
        except Exception:
            result["auc_roc"] = None
        result["confusion_matrix"] = confusion_matrix(labels, preds).tolist()

    return result


# ── 메인 파이프라인 ───────────────────────────────────────────────

def main():
    print(f"Device: {DEVICE}")
    mlflow.set_experiment("LSTM-AE-Anomaly")

    with mlflow.start_run(run_name="lstm_ae_anomaly_detection"):
        mlflow.log_params({
            "timestep":   TIMESTEP,
            "epochs":     EPOCHS,
            "batch_size": BATCH_SIZE,
            "lr":         LR,
            "k_msd":      K_MSD,
        })

        # ── 1. 데이터 로드 ──
        print("학습 데이터 로드 중 (2018~2021)...")
        df_train = load_range(TRAIN_START, TRAIN_END)
        print("테스트 데이터 로드 중 (2023)...")
        df_test  = load_range(TEST_START,  TEST_END)

        # ── 2. 전처리 ──
        for df in [df_train, df_test]:
            build_features(df)
            df.fillna(0, inplace=True)

        # 게이트웨이 장애 구간 제외 (학습 데이터만)
        df_train = mask_gateway_failures(df_train)

        feat_cols = ["grid_P", "pv_P", "chp_P", "heat_total_P",
                     "cool_output_P", "cool_elec_P", "cop", "Ta", "Igm",
                     "hour_sin", "hour_cos", "dow_sin", "dow_cos",
                     "month_sin", "month_cos"]

        scaler = MinMaxScaler()
        X_train_raw = scaler.fit_transform(df_train[feat_cols].fillna(0))
        X_test_raw  = scaler.transform(df_test[feat_cols].fillna(0))

        # ── 3. 시퀀스 생성 ──
        X_train_seq = make_sequences(X_train_raw, TIMESTEP)
        X_test_seq  = make_sequences(X_test_raw,  TIMESTEP)

        print(f"학습 시퀀스: {X_train_seq.shape}")
        print(f"테스트 시퀀스: {X_test_seq.shape}")

        # ── 4. 모델 초기화 및 학습 ──
        input_dim = X_train_seq.shape[2]
        model = LSTMAutoencoder(input_dim=input_dim,
                                hidden_dim=64,
                                latent_dim=32,
                                seq_len=TIMESTEP).to(DEVICE)

        print(f"\n모델 파라미터 수: {sum(p.numel() for p in model.parameters()):,}")
        print("LSTM-AE 학습 중 (정상 데이터만)...")
        losses = train_model(model, X_train_seq)

        # ── 5. 임계치 계산 (학습 데이터 기반) ──
        print("\n임계치 계산 중...")
        train_errors = compute_reconstruction_errors(model, X_train_seq)
        threshold    = compute_threshold_msd(train_errors, k=K_MSD)
        print(f"  학습 오차 평균: {train_errors.mean():.6f}")
        print(f"  학습 오차 std:  {train_errors.std():.6f}")
        print(f"  MSD 임계치 (k={K_MSD}): {threshold:.6f}")

        mlflow.log_metric("threshold_msd", threshold)
        mlflow.log_metric("train_error_mean", train_errors.mean())
        mlflow.log_metric("train_error_std",  train_errors.std())

        # ── 6. 테스트 데이터 이상탐지 ──
        print("\n테스트 데이터 이상탐지 중...")
        test_errors = compute_reconstruction_errors(model, X_test_seq)
        result = evaluate_model(test_errors, threshold)

        print(f"\n[이상탐지 결과]")
        print(f"  임계치:         {result['threshold']:.6f}")
        print(f"  이상 탐지 건수: {result['n_anomalies']}건")
        print(f"  이상 탐지 비율: {result['anomaly_rate']*100:.2f}%")

        mlflow.log_metrics({
            "n_anomalies":   result["n_anomalies"],
            "anomaly_rate":  result["anomaly_rate"],
        })

        # ── 7. 이상 구간 타임스탬프 추출 ──
        test_ts = df_test["ts"].iloc[TIMESTEP:].reset_index(drop=True)
        anomaly_idx = np.where(test_errors > threshold)[0]
        if len(anomaly_idx) > 0:
            anomaly_ts = test_ts.iloc[anomaly_idx]
            anomaly_df = pd.DataFrame({
                "timestamp":   anomaly_ts.values,
                "recon_error": test_errors[anomaly_idx],
                "threshold":   threshold,
            })
            anomaly_df.to_csv("anomaly_results.csv", index=False)
            mlflow.log_artifact("anomaly_results.csv")
            print(f"\n이상 구간 저장 완료: anomaly_results.csv")
            print(anomaly_df.head(10).to_string(index=False))

        # ── 8. 시각화 ──
        fig, axes = plt.subplots(2, 1, figsize=(14, 8))

        # 학습 손실
        axes[0].plot(losses)
        axes[0].set_title("LSTM-AE Training Loss")
        axes[0].set_xlabel("Epoch")
        axes[0].set_ylabel("MSE Loss")
        axes[0].grid(True)

        # 재구성 오차 분포
        n_show = min(len(test_errors), 2000)
        axes[1].plot(test_errors[:n_show], label="Reconstruction Error", alpha=0.7)
        axes[1].axhline(threshold, color="red", linestyle="--",
                        label=f"Threshold (k={K_MSD})")
        axes[1].set_title("LSTM-AE Reconstruction Error (Test)")
        axes[1].set_xlabel("Time step (1h)")
        axes[1].set_ylabel("MSE")
        axes[1].legend()
        axes[1].grid(True)

        plt.tight_layout()
        plt.savefig("lstm_ae_result.png", dpi=150)
        mlflow.log_artifact("lstm_ae_result.png")
        print("\n시각화 저장 완료: lstm_ae_result.png")

        # ── 9. 모델 저장 ──
        torch.save(model.state_dict(), "lstm_ae_model.pt")
        mlflow.log_artifact("lstm_ae_model.pt")
        print("모델 저장 완료: lstm_ae_model.pt")

    print("\n완료.")


if __name__ == "__main__":
    main()