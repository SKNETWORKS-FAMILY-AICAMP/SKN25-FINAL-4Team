"""VMD-LSTM 예측 모델 래퍼.

ML/outputs/models/ 의 학습된 가중치를 로드해 추론만 수행.
- predict_historical(df, start, end): 과거 구간 actual vs predicted 반환
- predict_future(df, hours):         최근 데이터 기반 미래 N시간 예측
"""
from __future__ import annotations

import os
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

ML_MODEL_DIR = Path(
    os.getenv(
        "ML_MODEL_DIR",
        str(Path(__file__).parents[4] / "ML" / "outputs" / "models"),
    )
)
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


# ── 모델 클래스 (ML/inference.py와 동일) ─────────────────────────

class _Attention(nn.Module):
    def __init__(self, hidden_dim: int):
        super().__init__()
        self.w = nn.Linear(hidden_dim, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return (x * torch.softmax(self.w(x), dim=1)).sum(dim=1)


class LSTMForecaster(nn.Module):
    def __init__(self, input_dim, hidden_dim, num_layers, dropout, **_):
        super().__init__()
        self.lstm = nn.LSTM(input_dim, hidden_dim, num_layers,
                            batch_first=True,
                            dropout=dropout if num_layers > 1 else 0.0)
        self.attn = _Attention(hidden_dim)
        self.fc = nn.Sequential(
            nn.Linear(hidden_dim, 128), nn.GELU(),
            nn.Dropout(dropout), nn.Linear(128, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm(x)
        return self.fc(self.attn(out)).squeeze(-1)


# ── 모델 / 스케일러 로드 ─────────────────────────────────────────

def _load_model():
    ckpt = torch.load(
        ML_MODEL_DIR / "vmd_lstm_grid_electricity.pt",
        map_location=DEVICE, weights_only=False,
    )
    cfg = ckpt["model_config"]
    model = LSTMForecaster(**cfg)
    model.load_state_dict(ckpt["model_state"])
    model.to(DEVICE).eval()

    with open(ML_MODEL_DIR / "vmd_lstm_scaler.pkl", "rb") as f:
        scalers = pickle.load(f)

    return model, scalers, cfg


# ── 피처 엔지니어링 ──────────────────────────────────────────────

def _add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    """시간 sin/cos 6개 피처 추가 (ML/data_loader.add_features 동일)."""
    if "ts" in df.columns:
        idx = pd.to_datetime(df["ts"])
    else:
        idx = df.index

    d = df.copy()
    d["hour_sin"]  = np.sin(2 * np.pi * idx.hour / 24)
    d["hour_cos"]  = np.cos(2 * np.pi * idx.hour / 24)
    d["dow_sin"]   = np.sin(2 * np.pi * idx.dayofweek / 7)
    d["dow_cos"]   = np.cos(2 * np.pi * idx.dayofweek / 7)
    d["month_sin"] = np.sin(2 * np.pi * (idx.month - 1) / 12)
    d["month_cos"] = np.cos(2 * np.pi * (idx.month - 1) / 12)
    return d


def _apply_vmd(signal: np.ndarray, k: int = 4) -> np.ndarray:
    from vmdpy import VMD
    u, _, _ = VMD(signal, 2000, 0, k, 0, 1, 1e-7)
    return u  # shape: (k, len(signal))


def _build_features(df: pd.DataFrame, vmd_k: int, feature_cols: list[str]) -> pd.DataFrame:
    """VMD IMF lag 피처 + 168h/336h lag 추가."""
    d = df.copy()
    for col in ["grid_P", "pv_P", "chp_P", "Ta", "Igm"]:
        if col in d.columns:
            d[col] = d[col].fillna(0)

    d = _add_time_features(d)

    signal = d["grid_P"].values.astype(float)
    u = _apply_vmd(signal, k=vmd_k)
    for i in range(vmd_k):
        d[f"imf_{i+1}_lag1"] = np.concatenate([[0.0], u[i][:-1]])

    d["grid_P_lag168h"] = d["grid_P"].shift(168).fillna(0)
    d["grid_P_lag336h"] = d["grid_P"].shift(336).fillna(0)

    missing = [c for c in feature_cols if c not in d.columns]
    if missing:
        raise ValueError(f"피처 누락: {missing}")

    return d


def _to_index_df(df: pd.DataFrame) -> pd.DataFrame:
    """ts 컬럼을 DatetimeIndex로 변환. 이미 인덱스면 그대로."""
    d = df.copy()
    if "ts" in d.columns:
        d = d.set_index("ts")
    d.index = pd.to_datetime(d.index, utc=True)
    return d


# ── 추론 ────────────────────────────────────────────────────────

def predict_historical(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """과거 구간에서 VMD-LSTM 예측 실행.

    Returns
    -------
    DataFrame with columns: ts, actual_w, predicted_w, error_w
    """
    model, scalers, cfg = _load_model()
    scaler_X  = scalers["scaler_X"]
    scaler_y  = scalers["scaler_y"]
    seq_len   = cfg["seq_len"]
    feat_cols = cfg["feature_cols"]
    vmd_k     = cfg["vmd_k"]

    d = _build_features(_to_index_df(df), vmd_k, feat_cols)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts   = pd.Timestamp(end,   tz="UTC")
    target = d[(d.index >= start_ts) & (d.index <= end_ts)]

    if len(target) < seq_len + 1:
        return pd.DataFrame()

    X       = scaler_X.transform(target[feat_cols])
    y_true_s = scaler_y.transform(target[["grid_P"]]).ravel()

    seqs = np.array([X[i: i + seq_len] for i in range(len(X) - seq_len)], dtype=np.float32)
    ts   = target.index[seq_len:]

    with torch.no_grad():
        preds_s = model(torch.from_numpy(seqs).to(DEVICE)).cpu().numpy()

    y_pred_w = np.maximum(scaler_y.inverse_transform(preds_s.reshape(-1, 1)).ravel(), 0)
    y_true_w = scaler_y.inverse_transform(y_true_s[seq_len:].reshape(-1, 1)).ravel()

    return pd.DataFrame({
        "ts":          ts,
        "actual_w":    y_true_w,
        "predicted_w": y_pred_w,
        "error_w":     y_true_w - y_pred_w,
    })


def predict_future(df: pd.DataFrame, hours: int = 24) -> pd.DataFrame:
    """최근 데이터 기반 미래 N시간 rolling 예측.

    IMF 피처는 마지막 알려진 값으로 고정 (단기 예측용).

    Returns
    -------
    DataFrame with columns: ts, predicted_w, predicted_kw
    """
    model, scalers, cfg = _load_model()
    scaler_X  = scalers["scaler_X"]
    scaler_y  = scalers["scaler_y"]
    seq_len   = cfg["seq_len"]
    feat_cols = cfg["feature_cols"]
    vmd_k     = cfg["vmd_k"]

    d = _build_features(_to_index_df(df), vmd_k, feat_cols)

    if len(d) < seq_len:
        raise ValueError(f"데이터 부족 (최소 {seq_len}행 필요, 현재 {len(d)})")

    X_hist    = scaler_X.transform(d[feat_cols])
    grid_hist = d["grid_P"].values.tolist()
    imf_lasts = [float(d[f"imf_{i+1}_lag1"].iloc[-1]) for i in range(vmd_k)]
    ta_last   = float(d["Ta"].iloc[-1])
    igm_last  = float(d["Igm"].iloc[-1])
    last_ts   = d.index[-1]

    window  = list(X_hist[-seq_len:])
    records = []

    for step in range(1, hours + 1):
        x = torch.from_numpy(
            np.array(window[-seq_len:], dtype=np.float32)
        ).unsqueeze(0).to(DEVICE)

        with torch.no_grad():
            pred_s = model(x).cpu().item()
        pred_w = max(float(scaler_y.inverse_transform([[pred_s]])[0][0]), 0)
        grid_hist.append(pred_w)

        next_ts = last_ts + pd.Timedelta(hours=step)
        lag168  = grid_hist[-169] if len(grid_hist) >= 169 else grid_hist[0]
        lag336  = grid_hist[-337] if len(grid_hist) >= 337 else grid_hist[0]

        row_dict = {
            "grid_P":       pred_w,
            "pv_P":         0.0,
            "chp_P":        0.0,
            "Ta":           ta_last,
            "Igm":          igm_last if 6 <= next_ts.hour <= 20 else 0.0,
            "hour_sin":     np.sin(2 * np.pi * next_ts.hour / 24),
            "hour_cos":     np.cos(2 * np.pi * next_ts.hour / 24),
            "dow_sin":      np.sin(2 * np.pi * next_ts.dayofweek / 7),
            "dow_cos":      np.cos(2 * np.pi * next_ts.dayofweek / 7),
            "month_sin":    np.sin(2 * np.pi * (next_ts.month - 1) / 12),
            "month_cos":    np.cos(2 * np.pi * (next_ts.month - 1) / 12),
            **{f"imf_{i+1}_lag1": imf_lasts[i] for i in range(vmd_k)},
            "grid_P_lag168h": lag168,
            "grid_P_lag336h": lag336,
        }
        row_s = scaler_X.transform([[row_dict[c] for c in feat_cols]])[0]
        window.append(row_s)

        records.append({
            "ts":           next_ts,
            "predicted_w":  round(pred_w, 1),
            "predicted_kw": round(pred_w / 1000, 2),
        })

    return pd.DataFrame(records)


def is_available() -> bool:
    """VMD-LSTM 모델 파일 존재 여부 확인."""
    return (ML_MODEL_DIR / "vmd_lstm_grid_electricity.pt").exists()
