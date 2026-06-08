"""v84 앙상블 잔차 기반 이상탐지 래퍼.

vmd_lstm_model 대신 ml.pipeline (v84)의 LSTM + artifacts를 사용한다.
배치 슬라이딩 윈도우로 전체 구간을 한 번의 forward pass에 처리.

인터페이스 (anomalies.py / simulator.py와 동일):
  is_available() → bool
  predict_anomaly(df, start, end) → DataFrame
    컬럼: ts, actual_w, predicted_w, residual_w, res_flag, if_flag, vote, anomaly_level
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch

# v84 파이프라인 루트 (backend/src/models/anomaly → backend/)
_PROJECT_ROOT = Path(__file__).parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ml.pipeline.common.config import (
    ARTIFACTS_DIR,
    HIDDEN_SIZE,
    WINDOW_SIZE,
    USE_RESIDUAL_TARGET,
    METER_SPECS_BY_URN,
    LSTM_VARIANTS,
)
from ml.pipeline.common.artifacts import (
    load_lstm_model,
    load_scalers,
    load_feature_columns,
    load_routing,
)
from ml.pipeline.common.preprocessing import (
    prepare_model_frame,
    transform_input,
    scaled_feature_columns,
)
from ml.pipeline.common.model import RecurrentPredictor, predict_scaled
from ml.pipeline.inference import is_available as _v84_available

# 잔차 탐지 대상 계량기 우선순위 (1h artifacts 기준)
_RESIDUAL_METERS = [
    "H2.Z66", "H2.Z64", "H1.Z10", "H1.Z12",
    "H3.Z43", "H4.Z50", "V.Z84",
]

_VARIANT_MAP = {v.version: v for v in LSTM_VARIANTS}


def is_available() -> bool:
    """v84 1h artifacts가 하나 이상 있으면 True."""
    try:
        return any(
            _v84_available(m, 1)
            for m in _RESIDUAL_METERS
            if m in METER_SPECS_BY_URN
        )
    except Exception:
        return False


def _batch_predict_meter(
    meter_urn: str,
    raw_df: pd.DataFrame,
    start: str,
    end: str,
) -> pd.DataFrame:
    """
    단일 계량기에 대해 [start, end] 구간의 잔차를 배치로 계산.

    Returns DataFrame(ts, actual_w, predicted_w, residual_w, threshold)
    또는 빈 DataFrame (데이터 부족 / 모델 없음).
    """
    if meter_urn not in METER_SPECS_BY_URN:
        return pd.DataFrame()
    if not _v84_available(meter_urn, 1):
        return pd.DataFrame()

    spec = METER_SPECS_BY_URN[meter_urn]

    routing = load_routing(meter_urn, 1)
    input_scaler, target_scaler = load_scalers(meter_urn, 1)
    feature_columns = load_feature_columns(meter_urn, 1)
    threshold = float(routing.get("anomaly_threshold") or 5000.0)

    top2_versions = routing.get("lstm_top2_versions") or []
    if not top2_versions:
        return pd.DataFrame()
    version = top2_versions[0]
    variant = _VARIANT_MAP.get(version)
    if variant is None:
        return pd.DataFrame()

    # checkpoint weight shape으로 실제 입력 크기 감지 (재학습 전/후 호환)
    try:
        pt_path = ARTIFACTS_DIR / f"1h" / meter_urn / f"lstm_{version}.pt"
        state = torch.load(pt_path, map_location="cpu", weights_only=True)
        n_lstm_features = state["recurrent.weight_ih_l0"].shape[1]
    except Exception:
        n_lstm_features = len(feature_columns)
    feature_columns = feature_columns[:n_lstm_features]

    model = load_lstm_model(
        RecurrentPredictor,
        input_size=n_lstm_features,
        hidden_size=HIDDEN_SIZE,
        output_size=1,
        architecture=variant.model_architecture,
        dropout=variant.model_dropout,
        meter_urn=meter_urn,
        horizon=1,
        version=version,
    )
    model.eval()

    try:
        frame, _, _ = prepare_model_frame(raw_df, spec, use_time_features=True)
    except Exception:
        return pd.DataFrame()

    scaler_cols = scaled_feature_columns(feature_columns)
    clean = frame.dropna(subset=feature_columns).reset_index(drop=True)
    if len(clean) < WINDOW_SIZE + 1:
        return pd.DataFrame()

    # 전 구간 피처 행렬 스케일 → 슬라이딩 윈도우 배치 생성
    x_2d = transform_input(clean, feature_columns, scaler_cols, input_scaler)
    n_windows = len(clean) - WINDOW_SIZE
    if n_windows <= 0:
        return pd.DataFrame()

    # (n_windows, WINDOW_SIZE, n_features) — 한 번에 배치 추론
    windows = np.stack(
        [x_2d[i: i + WINDOW_SIZE] for i in range(n_windows)]
    ).astype(np.float32)

    with torch.no_grad():
        preds_scaled = predict_scaled(model, windows, batch_size=512)  # (n_windows, 1)

    pred_step1 = preds_scaled[:, 0]

    # P(t-1) anchor로 residual target 복원
    anchor_P = clean["P"].values[WINDOW_SIZE - 1: WINDOW_SIZE - 1 + n_windows]
    anchor_scaled = target_scaler.transform(anchor_P.reshape(-1, 1)).ravel()

    if USE_RESIDUAL_TARGET:
        scaled_abs = pred_step1 + anchor_scaled
    else:
        scaled_abs = pred_step1

    pred_W = target_scaler.inverse_transform(scaled_abs.reshape(-1, 1)).ravel()
    actual_W = clean["P"].values[WINDOW_SIZE: WINDOW_SIZE + n_windows]
    ts_vals = clean["ts"].values[WINDOW_SIZE: WINDOW_SIZE + n_windows]

    result = pd.DataFrame({
        "ts":          ts_vals,
        "actual_w":    actual_W,
        "predicted_w": pred_W,
        "residual_w":  np.abs(actual_W - pred_W),
        "threshold":   threshold,
    })

    result["ts"] = pd.to_datetime(result["ts"], utc=True)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts   = pd.Timestamp(end,   tz="UTC")
    return result[
        (result["ts"] >= start_ts) & (result["ts"] <= end_ts)
    ].reset_index(drop=True)


def predict_anomaly(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """
    v84 잔차 기반 이상탐지.

    사용 가능한 계량기마다 배치 잔차를 계산하고,
    타임스텝별로 threshold 초과 비율이 가장 높은 계량기 결과를 대표값으로 사용.

    반환 컬럼:
      ts, actual_w, predicted_w, residual_w,
      res_flag, if_flag, vote, anomaly_level
    """
    meter_frames = []
    for meter_urn in _RESIDUAL_METERS:
        try:
            mdf = _batch_predict_meter(meter_urn, df, start, end)
            if not mdf.empty:
                mdf["meter_urn"] = meter_urn
                meter_frames.append(mdf)
        except Exception as exc:
            print(f"[residual_model] {meter_urn} 건너뜀: {exc}")

    if not meter_frames:
        return pd.DataFrame()

    combined = pd.concat(meter_frames, ignore_index=True)
    # 타임스텝별로 잔차/threshold 비율이 가장 높은 계량기를 대표로 선택
    combined["ratio"] = combined["residual_w"] / combined["threshold"].clip(lower=1.0)
    best_idx = combined.groupby("ts", sort=False)["ratio"].idxmax()
    result = combined.loc[best_idx].reset_index(drop=True)

    # anomalies.py / simulator.py 인터페이스와 동일한 컬럼 생성
    result["res_flag"] = (result["ratio"] >= 1.0).astype(int)
    result["if_flag"]  = 0   # v84 버전에서는 IF 미사용
    result["vote"]     = result["res_flag"]

    result["anomaly_level"] = np.where(
        result["ratio"] >= 2.0, "HIGH",
        np.where(
            result["ratio"] >= 1.5, "MEDIUM",
            np.where(result["ratio"] >= 1.0, "LOW", "NORMAL"),
        ),
    )

    return result[[
        "ts", "actual_w", "predicted_w", "residual_w",
        "res_flag", "if_flag", "vote", "anomaly_level",
    ]]
