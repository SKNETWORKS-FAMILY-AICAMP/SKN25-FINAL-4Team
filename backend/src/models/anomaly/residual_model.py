"""v84 앙상블 잔차 기반 이상탐지 래퍼.

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

# 잔차 탐지 대상 계량기 (1h artifacts 기준)
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
    start: str,
    end: str,
) -> pd.DataFrame:
    """
    단일 계량기의 [start, end] 구간 잔차를 배치로 계산.
    DB에서 직접 계량기별 데이터를 가져온다.

    Returns DataFrame(ts, actual_w, predicted_w, residual_w, threshold)
    또는 빈 DataFrame (데이터 부족 / 모델 없음).
    """
    if meter_urn not in METER_SPECS_BY_URN:
        return pd.DataFrame()
    if not _v84_available(meter_urn, 1):
        return pd.DataFrame()

    spec = METER_SPECS_BY_URN[meter_urn]

    # 계량기별 원본 데이터 직접 조회 (집계 데이터 대신)
    try:
        from ml.pipeline.common.db import build_engine, fetch_meter_window
        engine = build_engine()
        end_ts = pd.Timestamp(end, tz="UTC")
        # start 기준 400h 앞부터 end까지 로드
        start_ts = pd.Timestamp(start, tz="UTC")
        total_hours = int((end_ts - start_ts).total_seconds() / 3600) + 400
        raw_df = fetch_meter_window(engine, spec, end_ts=end_ts, window_hours=total_hours)
    except Exception as e:
        print(f"[residual_model] {meter_urn} 데이터 로드 실패: {e}")
        return pd.DataFrame()

    if raw_df is None or raw_df.empty:
        return pd.DataFrame()

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

    # checkpoint weight shape으로 실제 입력 크기 감지
    try:
        pt_path = ARTIFACTS_DIR / "1h" / meter_urn / f"lstm_{version}.pt"
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
    except Exception as e:
        print(f"[residual_model] {meter_urn} prepare_model_frame 실패: {e}")
        return pd.DataFrame()

    scaler_cols = scaled_feature_columns(feature_columns)
    clean = frame.dropna(subset=feature_columns).reset_index(drop=True)
    if len(clean) < WINDOW_SIZE + 1:
        return pd.DataFrame()

    x_2d = transform_input(clean, feature_columns, scaler_cols, input_scaler)
    n_windows = len(clean) - WINDOW_SIZE
    if n_windows <= 0:
        return pd.DataFrame()

    windows = np.stack(
        [x_2d[i: i + WINDOW_SIZE] for i in range(n_windows)]
    ).astype(np.float32)

    with torch.no_grad():
        preds_scaled = predict_scaled(model, windows, batch_size=512)

    pred_step1 = preds_scaled[:, 0]

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
    df: load_reduced 집계 데이터 (센서 컬럼 병합용으로만 사용).
    계량기별 원본 데이터는 내부에서 직접 조회.
    """
    meter_frames = []
    for meter_urn in _RESIDUAL_METERS:
        try:
            mdf = _batch_predict_meter(meter_urn, start, end)
            if not mdf.empty:
                mdf["meter_urn"] = meter_urn
                meter_frames.append(mdf)
        except Exception as exc:
            print(f"[residual_model] {meter_urn} 건너뜀: {exc}")

    if not meter_frames:
        return pd.DataFrame()

    combined = pd.concat(meter_frames, ignore_index=True)
    combined["ratio"] = combined["residual_w"] / combined["threshold"].clip(lower=1.0)
    best_idx = combined.groupby("ts", sort=False)["ratio"].idxmax()
    result = combined.loc[best_idx].reset_index(drop=True)

    result["res_flag"] = (result["ratio"] >= 1.0).astype(int)
    result["if_flag"]  = 0
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
