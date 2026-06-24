"""
v36/v52 bias 보정 및 broad-source 게이트 레이어.

v36: 전체 validation residual 중앙값으로 bias 보정
v52: v25 대비 broad 소스(v3,v10,v12,v15,v19,v24,v25) 비교 후 최적 선택 + bias gate
"""
from __future__ import annotations

import numpy as np

from cms.modeling.anomaly.config import (
    V52_BROAD_GATE_MIN_MEDIAN_IMPROVEMENT,
    V52_BIAS_GATE_EVAL_FRACTION,
    V52_BIAS_GATE_MIN_MAE_IMPROVEMENT,
)
from cms.modeling.anomaly.config import MeterSpec


def _mae(pred: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean(np.abs(pred - y)))


# ── v36: median residual bias correction ────────────────────────────────────

def compute_median_residual_bias(val_pred_scaled: np.ndarray, y_val: np.ndarray) -> np.ndarray:
    """
    step별 validation residual 중앙값. shape: (horizon,)
    bias = median(actual - pred) per step
    """
    residuals = y_val - val_pred_scaled  # (N, horizon)
    return np.median(residuals, axis=0)  # (horizon,)


def apply_bias(pred_scaled: np.ndarray, bias: np.ndarray) -> np.ndarray:
    return pred_scaled + bias  # broadcast over N


def v36_bias_correct(val_pred_scaled, test_pred_scaled, y_val):
    bias = compute_median_residual_bias(val_pred_scaled, y_val)
    return {
        "val_pred_scaled": apply_bias(val_pred_scaled, bias),
        "test_pred_scaled": apply_bias(test_pred_scaled, bias),
        "bias": bias,
    }


# ── v52: broad-source gate + bias gate ──────────────────────────────────────

def _broad_source_select(
    all_preds: dict[str, dict],  # {version: {"val_pred_scaled", "test_pred_scaled", "y_val"}}
    spec: MeterSpec,
    all_specs,
    base_version: str = "v25",
    min_median_improvement: float = V52_BROAD_GATE_MIN_MEDIAN_IMPROVEMENT,
) -> str:
    """
    group+role 계량기들의 median validation MAE 기준
    base_version 대비 min_median_improvement 이상 개선되면 해당 candidate 선택.
    개선 없으면 base_version 유지.
    """
    group_role_specs = [s for s in all_specs if s.group == spec.group and s.role == spec.role]

    def group_median_mae(version: str) -> float:
        maes = []
        for s in group_role_specs:
            if s.meter_urn in all_preds.get(version, {}):
                d = all_preds[version][s.meter_urn]
                maes.append(_mae(d["val_pred_scaled"], d["y_val"]))
        return float(np.median(maes)) if maes else float("inf")

    base_mae = group_median_mae(base_version)
    best_version = base_version
    best_mae = base_mae

    for v in all_preds:
        if v == base_version:
            continue
        candidate_mae = group_median_mae(v)
        improvement = (base_mae - candidate_mae) / max(base_mae, 1e-9)
        if improvement >= min_median_improvement and candidate_mae < best_mae:
            best_mae = candidate_mae
            best_version = v

    return best_version


def _bias_gate(
    val_pred_scaled: np.ndarray,
    y_val: np.ndarray,
    eval_fraction: float = V52_BIAS_GATE_EVAL_FRACTION,
    min_improvement: float = V52_BIAS_GATE_MIN_MAE_IMPROVEMENT,
) -> np.ndarray:
    """
    val 후반 eval_fraction 구간에서 bias 보정 효과 검증 후
    개선되면 full val로 bias 재계산, 아니면 0.
    """
    n = len(val_pred_scaled)
    split = max(1, int(n * (1.0 - eval_fraction)))
    split = min(split, n - 1)

    fit_pred, fit_y = val_pred_scaled[:split], y_val[:split]
    eval_pred, eval_y = val_pred_scaled[split:], y_val[split:]

    bias_candidate = compute_median_residual_bias(fit_pred, fit_y)
    corrected_eval = apply_bias(eval_pred, bias_candidate)

    baseline_mae = _mae(eval_pred, eval_y)
    corrected_mae = _mae(corrected_eval, eval_y)
    improvement = (baseline_mae - corrected_mae) / max(baseline_mae, 1e-9)

    if improvement >= min_improvement:
        return compute_median_residual_bias(val_pred_scaled, y_val)
    return np.zeros(val_pred_scaled.shape[1])


def v52_broad_bias_gate(
    all_preds: dict,
    spec: MeterSpec,
    all_specs,
    base_version: str = "v25",
) -> dict:
    """
    1) broad source 선택 (group median MAE 기준)
    2) 선택된 소스에 bias gate 적용
    반환: val_pred_scaled, test_pred_scaled, selected_version, bias
    """
    selected_version = _broad_source_select(all_preds, spec, all_specs, base_version)

    chosen = all_preds[selected_version][spec.meter_urn]
    val_pred = chosen["val_pred_scaled"]
    test_pred = chosen["test_pred_scaled"]
    y_val = chosen["y_val"]

    bias = _bias_gate(val_pred, y_val)
    return {
        "val_pred_scaled": apply_bias(val_pred, bias),
        "test_pred_scaled": apply_bias(test_pred, bias),
        "selected_version": selected_version,
        "bias": bias,
    }
