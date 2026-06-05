"""
v84 앙상블 추론 모듈.
학습된 artifacts를 로드해 단일 계량기의 t+1 ~ t+horizon 예측을 반환한다.

사용 예:
    from ml.pipeline.inference import predict_meter, is_available
    from ml.pipeline.common.config import METER_SPECS_BY_URN

    spec = METER_SPECS_BY_URN["H2.Z66"]
    df = predict_meter("H2.Z66", horizon=1, raw_df=raw_df, spec=spec)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import torch

from ml.pipeline.common.config import (
    ARTIFACTS_DIR,
    HIDDEN_SIZE,
    USE_RESIDUAL_TARGET,
    LSTM_VARIANTS,
    METER_SPECS_BY_URN,
    MeterSpec,
)
from ml.pipeline.common.artifacts import (
    load_lstm_model,
    load_catboost,
    load_ridge,
    load_lightgbm,
    load_scalers,
    load_routing,
    load_bias_corrections,
    load_feature_columns,
)
from ml.pipeline.common.preprocessing import (
    prepare_model_frame,
    transform_input,
    scaled_feature_columns,
)
from ml.pipeline.common.model import RecurrentPredictor, predict_scaled
from ml.pipeline.common.catboost_model import predict_catboost_scaled
from ml.pipeline.common.lightgbm_model import predict_lightgbm_scaled
from ml.pipeline.common.ridge import predict_ridge_scaled

_VARIANT_MAP = {v.version: v for v in LSTM_VARIANTS}


def is_available(meter_urn: str, horizon: int) -> bool:
    """해당 계량기·horizon의 학습 artifacts가 존재하는지 확인."""
    path = ARTIFACTS_DIR / f"{horizon}h" / meter_urn / "routing.json"
    return path.exists()


def _build_input_window(
    raw_df: pd.DataFrame,
    spec: MeterSpec,
    feature_columns: list[str],
    input_scaler,
    target_scaler,
    window_size: int = 24,
) -> tuple[np.ndarray, float, pd.Timestamp]:
    """
    최근 데이터 → 입력 윈도우 생성.
    반환: x (1, window_size, n_features), anchor_scaled, last_input_ts
    """
    frame, _, _ = prepare_model_frame(raw_df, spec, use_time_features=True)
    scaler_cols = scaled_feature_columns(feature_columns)

    clean = frame.dropna(subset=feature_columns).reset_index(drop=True)
    if len(clean) < window_size:
        raise ValueError(f"{spec.meter_urn}: 입력 데이터 부족 ({len(clean)} < {window_size})")

    window = clean.tail(window_size).reset_index(drop=True)
    last_ts = pd.Timestamp(window["ts"].iloc[-1])

    x_2d = transform_input(window, feature_columns, scaler_cols, input_scaler)
    x = x_2d[np.newaxis].astype(np.float32)  # (1, window_size, n_features)

    last_P = float(window["P"].iloc[-1])
    anchor_scaled = float(target_scaler.transform([[last_P]])[0, 0])

    return x, anchor_scaled, last_ts


def _lstm_ensemble_pred(
    x: np.ndarray,
    meter_urn: str,
    horizon: int,
    top2_versions: list[str],
    top2_weights: list[float],
    feature_columns: list[str],
) -> np.ndarray:
    """top-2 LSTM 가중 앙상블 예측 (잔차, scaled). shape: (1, horizon)"""
    pred = np.zeros((1, horizon), dtype=np.float32)
    for version, weight in zip(top2_versions, top2_weights):
        variant = _VARIANT_MAP[version]
        model = load_lstm_model(
            RecurrentPredictor,
            input_size=len(feature_columns),
            hidden_size=HIDDEN_SIZE,
            output_size=horizon,
            architecture=variant.model_architecture,
            dropout=variant.model_dropout,
            meter_urn=meter_urn,
            horizon=horizon,
            version=version,
        )
        model.eval()
        with torch.no_grad():
            pred += weight * predict_scaled(model, x, batch_size=1)
    return pred


def _naive_pred(
    x: np.ndarray,
    feature_columns: list[str],
    horizon: int,
    anchor_scaled: float,
) -> np.ndarray:
    """seasonal naive: 입력창 첫 horizon 타임스텝의 scaled P (24h 전 같은 시각). shape: (1, horizon)"""
    target_idx = feature_columns.index("P")
    naive_abs = x[0, :horizon, target_idx]  # (horizon,) scaled P
    if USE_RESIDUAL_TARGET:
        return (naive_abs - anchor_scaled).reshape(1, -1)
    return naive_abs.reshape(1, -1)


def predict_meter(
    meter_urn: str,
    horizon: int,
    raw_df: pd.DataFrame,
    spec: MeterSpec | None = None,
) -> pd.DataFrame:
    """
    단일 계량기 v84 앙상블 추론.

    반환 DataFrame 컬럼:
      ts (UTC)  — 예측 대상 시각
      pred_t_plus_{1..horizon}  — 원본 단위 예측값 (W)
      anomaly_threshold  — 학습 시 결정된 이상탐지 임계값
    """
    if spec is None:
        spec = METER_SPECS_BY_URN[meter_urn]

    if not is_available(meter_urn, horizon):
        raise FileNotFoundError(
            f"{meter_urn} horizon={horizon}h artifacts 없음. "
            f"먼저 학습을 실행하세요: python -m ml.pipeline.train --horizon {horizon} --meters {meter_urn}"
        )

    routing = load_routing(meter_urn, horizon)
    input_scaler, target_scaler = load_scalers(meter_urn, horizon)
    feature_columns = load_feature_columns(meter_urn, horizon)
    bias_corr = load_bias_corrections(meter_urn, horizon)

    v63_ver = routing["v63"]
    v57_ver = routing.get("v57", "v52")

    x, anchor_scaled, last_ts = _build_input_window(
        raw_df, spec, feature_columns, input_scaler, target_scaler
    )

    # ── v63 예측 ──────────────────────────────────────────────────
    if v63_ver == "v61":
        lgb_models = load_lightgbm(meter_urn, horizon, ARTIFACTS_DIR)
        v63_pred = predict_lightgbm_scaled(lgb_models, x)
    else:
        lstm_pred = _lstm_ensemble_pred(
            x, meter_urn, horizon,
            routing["lstm_top2_versions"],
            routing["lstm_top2_weights"],
            feature_columns,
        )
        if v57_ver == "v53":
            cb = load_catboost(meter_urn, horizon)
            v63_pred = predict_catboost_scaled(cb, x, horizon)
        elif v57_ver == "v36":
            bias = np.array(routing["v36_bias"], dtype=np.float32)
            v63_pred = lstm_pred + bias.reshape(1, -1)
        else:
            v63_pred = lstm_pred

    # ── v67 Ridge 예측 ───────────────────────────────────────────
    ridge = load_ridge(meter_urn, horizon)
    ridge_pred = predict_ridge_scaled(ridge, x)

    # ── v71 Seasonal Naive 예측 ──────────────────────────────────
    naive_pred = _naive_pred(x, feature_columns, horizon, anchor_scaled)

    # ── v84 median 앙상블 + anchor 복원 ──────────────────────────
    stacked = np.stack([v63_pred[0], ridge_pred[0], naive_pred[0]], axis=0)  # (3, horizon)
    ensemble_residual = np.median(stacked, axis=0)  # (horizon,)

    if USE_RESIDUAL_TARGET:
        scaled_abs = ensemble_residual + anchor_scaled
    else:
        scaled_abs = ensemble_residual
    pred_W = target_scaler.inverse_transform(scaled_abs.reshape(-1, 1)).ravel()

    # ── hour bias 보정 ────────────────────────────────────────────
    target_tss = [last_ts + pd.Timedelta(hours=i + 1) for i in range(horizon)]
    df = pd.DataFrame({"ts": target_tss})
    for i in range(horizon):
        df[f"pred_t_plus_{i + 1}"] = pred_W[i]

    timestamps = pd.to_datetime(df["ts"], utc=True)
    for step in range(1, horizon + 1):
        step_corr = bias_corr[bias_corr["forecast_step"] == step].set_index("target_hour_utc")
        fallback = float(step_corr["fallback_global_correction"].iloc[0])
        corr_map = step_corr["median_residual_correction"].to_dict()
        col = f"pred_t_plus_{step}"
        df[col] = df[col] + timestamps.dt.hour.map(corr_map).fillna(fallback).to_numpy(dtype=float)

    df["anomaly_threshold"] = routing.get("anomaly_threshold")
    return df
