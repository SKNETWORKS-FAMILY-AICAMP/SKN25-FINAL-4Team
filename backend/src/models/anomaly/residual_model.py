"""VMD-LSTM 잔차 + Isolation Forest 이상탐지 래퍼.

ML 팀의 현행 접근법(residual-based)을 ems-agent에서 호출.
predict_anomaly(df, start, end) → DataFrame 반환.
"""
from __future__ import annotations

import os
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import torch

ML_MODEL_DIR = Path(
    os.getenv(
        "ML_MODEL_DIR",
        str(Path(__file__).parents[4] / "ML" / "outputs" / "models"),
    )
)

# IF 스케일러가 학습된 11개 기본 피처 (VMD 컬럼 제외)
_IF_FEATURE_COLS = [
    "grid_P", "pv_P", "chp_P", "Ta", "Igm",
    "hour_sin", "hour_cos", "dow_sin", "dow_cos", "month_sin", "month_cos",
]


def predict_anomaly(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """잔차 임계치 + IF 앙상블 이상탐지.

    Parameters
    ----------
    df    : load_range() / load_reduced() 출력 (ts 컬럼 포함)
    start : 분석 시작일 (YYYY-MM-DD)
    end   : 분석 종료일 (YYYY-MM-DD)

    Returns
    -------
    DataFrame with columns:
        ts, actual_w, predicted_w, residual_w,
        res_flag, if_flag, vote, anomaly_level
    """
    # 모델 파일 로드
    with open(ML_MODEL_DIR / "residual_threshold.pkl", "rb") as f:
        art = pickle.load(f)
    with open(ML_MODEL_DIR / "residual_iforest.pkl", "rb") as f:
        iso = pickle.load(f)
    with open(ML_MODEL_DIR / "residual_if_scaler.pkl", "rb") as f:
        scaler_if = pickle.load(f)

    threshold = art["threshold"]

    # VMD-LSTM 예측 → 잔차
    from models.forecasting.vmd_lstm_model import (
        _add_time_features, _build_features, _to_index_df, _load_model,
        predict_historical,
    )

    fc = predict_historical(df, start, end)
    if fc.empty:
        return pd.DataFrame()

    residuals = fc["error_w"].abs().values
    ts_vals   = fc["ts"].values

    # IF 플래그: 기본 11 피처 사용
    _, _, cfg = _load_model()
    seq_len   = cfg["seq_len"]

    d = _to_index_df(df)
    for col in ["grid_P", "pv_P", "chp_P", "Ta", "Igm"]:
        if col in d.columns:
            d[col] = d[col].fillna(0)
    d = _add_time_features(d)
    d.index = pd.to_datetime(d.index, utc=True)

    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts   = pd.Timestamp(end,   tz="UTC")
    target  = d[(d.index >= start_ts) & (d.index <= end_ts)]
    avail   = [c for c in _IF_FEATURE_COLS if c in target.columns]
    X_if    = scaler_if.transform(target[avail])

    n       = len(residuals)
    if_pred = iso.predict(X_if[seq_len: seq_len + n])
    if_flag = (if_pred == -1).astype(int)

    res_flag = (residuals > threshold).astype(int)
    vote     = res_flag + if_flag
    # HIGH: 두 모델 모두 탐지
    # MEDIUM: 잔차 임계치 초과 + 잔차가 임계치의 1.5배 이상 (신뢰도 높음)
    # LOW: IF만 탐지, 또는 잔차가 임계치 근방
    level    = np.where(
        vote == 2, "HIGH",
        np.where(
            (res_flag == 1) & (residuals > threshold * 1.5), "MEDIUM",
            np.where(vote == 1, "LOW", "NORMAL"),
        ),
    )

    return pd.DataFrame({
        "ts":            ts_vals,
        "actual_w":      fc["actual_w"].values,
        "predicted_w":   fc["predicted_w"].values,
        "residual_w":    residuals,
        "res_flag":      res_flag,
        "if_flag":       if_flag,
        "vote":          vote,
        "anomaly_level": level,
    })


def is_available() -> bool:
    """Residual+IF 모델 파일 모두 존재하는지 확인."""
    needed = [
        "residual_threshold.pkl",
        "residual_iforest.pkl",
        "residual_if_scaler.pkl",
        "vmd_lstm_grid_electricity.pt",
    ]
    return all((ML_MODEL_DIR / f).exists() for f in needed)
