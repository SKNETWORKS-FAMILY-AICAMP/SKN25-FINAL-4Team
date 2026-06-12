"""
v84 앙상블 레이어: median(v63, v67, v71) + shrunk hour bias correction × gain 1.30

입력은 inverse-transform된 실제 단위(kWh) 예측 DataFrame.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from energy_v84.common.config import V84_CORRECTION_GAIN, V84_SHRINKAGE_PRIOR_ROWS


def _inv(scaler, arr: np.ndarray) -> np.ndarray:
    """StandardScaler가 단일 feature로 fit됐으므로 reshape 후 inverse."""
    return scaler.inverse_transform(arr.reshape(-1, 1)).reshape(arr.shape)


def build_prediction_df(
    meta: pd.DataFrame,  # bundle.meta_val 또는 meta_test
    actual_scaled: np.ndarray,  # (N, horizon)
    pred_scaled: np.ndarray,    # (N, horizon)
    target_scaler,
    horizon: int,
    anchor: np.ndarray | None = None,  # (N,) scaled P(t-1), USE_RESIDUAL_TARGET=True 시 전달
) -> pd.DataFrame:
    """scaled 예측을 원본 단위로 역변환해 DataFrame 생성.

    USE_RESIDUAL_TARGET=True 이면 actual/pred 가 잔차이므로
    anchor(scaled P(t-1))를 더해 원래 scaled P(t)로 복원 후 inverse_transform.
    """
    if anchor is not None:
        # anchor shape: (N,) → (N, 1) 브로드캐스트로 모든 horizon step에 더함
        anc = anchor.reshape(-1, 1)
        actual_scaled = actual_scaled + anc
        pred_scaled   = pred_scaled   + anc
    actual = _inv(target_scaler, actual_scaled)
    pred = _inv(target_scaler, pred_scaled)

    df = meta.copy()
    abs_errors = []
    for step in range(1, horizon + 1):
        df[f"actual_t_plus_{step}"] = actual[:, step - 1]
        df[f"pred_t_plus_{step}"] = pred[:, step - 1]
        df[f"residual_t_plus_{step}"] = actual[:, step - 1] - pred[:, step - 1]
        df[f"abs_error_t_plus_{step}"] = np.abs(df[f"residual_t_plus_{step}"])
        abs_errors.append(df[f"abs_error_t_plus_{step}"].to_numpy(dtype=float))
    df["anomaly_score_mae"] = np.mean(np.vstack(abs_errors), axis=0)
    return df


def build_median_ensemble(
    source_dfs: dict[str, pd.DataFrame],  # {"v63": df, "v67": df, "v71": df}
    horizon: int,
) -> pd.DataFrame:
    """각 소스의 예측을 step별 median으로 합친다."""
    base = list(source_dfs.values())[0].copy()
    abs_errors = []
    for step in range(1, horizon + 1):
        pred_col = f"pred_t_plus_{step}"
        preds = np.stack([df[pred_col].to_numpy(dtype=float) for df in source_dfs.values()], axis=1)
        base[pred_col] = np.median(preds, axis=1)
        base[f"residual_t_plus_{step}"] = base[f"actual_t_plus_{step}"].to_numpy(dtype=float) - base[pred_col].to_numpy(dtype=float)
        base[f"abs_error_t_plus_{step}"] = np.abs(base[f"residual_t_plus_{step}"])
        abs_errors.append(base[f"abs_error_t_plus_{step}"].to_numpy(dtype=float))
    base["anomaly_score_mae"] = np.mean(np.vstack(abs_errors), axis=0)
    return base


def fit_shrunk_hour_bias_corrections(
    val_df: pd.DataFrame,
    horizon: int,
    gain: float = V84_CORRECTION_GAIN,
    prior_rows: float = V84_SHRINKAGE_PRIOR_ROWS,
) -> pd.DataFrame:
    """validation 예측 DataFrame 기준 시간대별 shrunk bias 계산."""
    timestamps = pd.to_datetime(val_df["target_end_ts"], utc=True)
    rows = []
    for step in range(1, horizon + 1):
        residual = val_df[f"actual_t_plus_{step}"] - val_df[f"pred_t_plus_{step}"]
        global_correction = float(residual.median())
        for hour in range(24):
            hour_residual = residual[timestamps.dt.hour.eq(hour)]
            n = int(len(hour_residual))
            raw = float(hour_residual.median()) if n else global_correction
            alpha = n / (n + prior_rows) if n else 0.0
            shrunk = global_correction + alpha * (raw - global_correction)
            correction = gain * shrunk
            rows.append({
                "forecast_step": step,
                "target_hour_utc": hour,
                "median_residual_correction": correction,
                "shrunk_median_residual_correction": shrunk,
                "fallback_global_correction": global_correction,
                "correction_gain": gain,
                "shrinkage_alpha": alpha,
                "n_validation_rows": n,
            })
    return pd.DataFrame(rows)


def apply_hour_bias_corrections(
    df: pd.DataFrame,
    corrections: pd.DataFrame,
    horizon: int,
) -> pd.DataFrame:
    corrected = df.copy()
    timestamps = pd.to_datetime(corrected["target_end_ts"], utc=True)
    abs_errors = []
    for step in range(1, horizon + 1):
        step_corr = corrections[corrections["forecast_step"].eq(step)].set_index("target_hour_utc")
        fallback = float(step_corr["fallback_global_correction"].iloc[0])
        corr_map = step_corr["median_residual_correction"].to_dict()
        corr_vals = timestamps.dt.hour.map(corr_map).fillna(fallback).to_numpy(dtype=float)
        pred_col = f"pred_t_plus_{step}"
        actual_col = f"actual_t_plus_{step}"
        corrected[pred_col] = corrected[pred_col].to_numpy(dtype=float) + corr_vals
        corrected[f"residual_t_plus_{step}"] = corrected[actual_col].to_numpy(dtype=float) - corrected[pred_col].to_numpy(dtype=float)
        corrected[f"abs_error_t_plus_{step}"] = np.abs(corrected[f"residual_t_plus_{step}"])
        abs_errors.append(corrected[f"abs_error_t_plus_{step}"].to_numpy(dtype=float))
    corrected["anomaly_score_mae"] = np.mean(np.vstack(abs_errors), axis=0)
    return corrected


def compute_persistence_mae(df: pd.DataFrame) -> float:
    """
    persistence MAE: actual[t-1]을 예측으로 사용했을 때의 MAE.
    슬라이딩 윈도우 기준 → 이전 행의 actual_t_plus_1 = 현재 행의 t-1 값.
    """
    actual = df["actual_t_plus_1"].to_numpy(dtype=float)
    prev   = np.roll(actual, 1)
    return float(np.mean(np.abs(actual[1:] - prev[1:])))


def compute_metrics(df: pd.DataFrame, horizon: int, split: str) -> dict[str, Any]:
    actual = df[[f"actual_t_plus_{s}" for s in range(1, horizon + 1)]].to_numpy(dtype=float)
    pred   = df[[f"pred_t_plus_{s}" for s in range(1, horizon + 1)]].to_numpy(dtype=float)
    err    = np.abs(actual - pred)

    mae  = float(np.mean(err))
    rmse = float(np.sqrt(np.mean((actual - pred) ** 2)))

    # MAPE: actual=0인 행 제외 후 계산
    nonzero = actual != 0
    mape = float(np.mean(err[nonzero] / np.abs(actual[nonzero])) * 100) if nonzero.any() else float("nan")

    # WAPE: sum(|error|) / sum(|actual|) × 100 — actual=0 포함, MAPE보다 안정적
    sum_actual = float(np.sum(np.abs(actual)))
    wape = float(np.sum(err) / sum_actual * 100) if sum_actual > 0 else float("nan")

    # persistence MAE (비교 기준)
    persist_mae = round(compute_persistence_mae(df), 4)
    beats_persistence = mae < persist_mae

    return {
        "split": split,
        "n": len(df),
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "mape": round(mape, 4),
        "wape": round(wape, 4),
        "persistence_mae": persist_mae,
        "beats_persistence": beats_persistence,
    }
