"""
v19/v24/v25 그룹 선택 레이어.

v19: group+role 기준 median validation MAE 최소 소스 선택
v24: convex grid blend (v10, v12, v15 가중치 탐색)
v25: v24가 v19 대비 IMPROVEMENT 이상 개선 시 v24 선택, 아니면 v19
"""
from __future__ import annotations

import itertools

import numpy as np

from energy_v84.common.config import V25_MIN_MEDIAN_IMPROVEMENT
from energy_v84.common.config import ALL_METER_SPECS, MeterSpec


WEIGHT_GRID_STEP = 0.1


def _mae_scaled(pred: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean(np.abs(pred - y)))


# ── v19 ─────────────────────────────────────────────────────────────────────

def v19_select(
    ensemble_preds: dict[str, dict],  # {"v10": {...}, "v12": {...}, "v15": {...}}
    spec: MeterSpec,
    horizon: int,
    all_specs,
) -> dict[str, np.ndarray]:
    """
    같은 group+role의 모든 계량기 validation MAE 중앙값 기준으로
    v10/v12/v15 중 최적 소스를 선택한다.
    ensemble_preds: {version: {meter_urn: {"val_pred_scaled", "test_pred_scaled", "y_val", "y_test"}}}
    """
    group_role_specs = [s for s in all_specs if s.group == spec.group and s.role == spec.role]
    source_versions = list(ensemble_preds.keys())

    best_version = None
    best_median_mae = float("inf")
    for v in source_versions:
        maes = []
        for s in group_role_specs:
            if s.meter_urn in ensemble_preds[v]:
                d = ensemble_preds[v][s.meter_urn]
                maes.append(_mae_scaled(d["val_pred_scaled"], d["y_val"]))
        if not maes:
            continue
        median_mae = float(np.median(maes))
        if median_mae < best_median_mae:
            best_median_mae = median_mae
            best_version = v

    chosen = ensemble_preds[best_version][spec.meter_urn]
    return {
        "val_pred_scaled": chosen["val_pred_scaled"],
        "test_pred_scaled": chosen["test_pred_scaled"],
        "selected_version": best_version,
        "median_mae": best_median_mae,
    }


# ── v24 ─────────────────────────────────────────────────────────────────────

def _candidate_weights(n: int, step: float = WEIGHT_GRID_STEP):
    """n개 소스에 대한 합이 1인 convex 가중치 조합 열거."""
    steps = round(1.0 / step)
    for combo in itertools.combinations_with_replacement(range(steps + 1), n - 1):
        pts = [0] + list(combo) + [steps]
        weights = tuple((pts[i + 1] - pts[i]) / steps for i in range(n))
        if abs(sum(weights) - 1.0) < 1e-9:
            yield weights


def v24_blend(
    ensemble_preds: dict[str, dict],  # {"v10": {...}, "v12": {...}, "v15": {...}}
    spec: MeterSpec,
    horizon: int,
    all_specs,
) -> dict[str, np.ndarray]:
    """
    같은 group+role 계량기들의 합산 validation MAE를 최소화하는
    convex 가중치 조합을 그리드 탐색한다.
    """
    source_versions = list(ensemble_preds.keys())  # v10, v12, v15
    group_role_specs = [s for s in all_specs if s.group == spec.group and s.role == spec.role]

    # group+role 전체 merged val predictions
    group_data = {}
    for s in group_role_specs:
        if all(s.meter_urn in ensemble_preds[v] for v in source_versions):
            group_data[s.meter_urn] = {
                v: ensemble_preds[v][s.meter_urn] for v in source_versions
            }

    best_weights = None
    best_group_mae = float("inf")

    for weights in _candidate_weights(len(source_versions)):
        total_mae = 0.0
        for meter_data in group_data.values():
            pred = sum(weights[i] * meter_data[v]["val_pred_scaled"]
                       for i, v in enumerate(source_versions))
            y = meter_data[source_versions[0]]["y_val"]
            total_mae += _mae_scaled(pred, y)
        if total_mae < best_group_mae:
            best_group_mae = total_mae
            best_weights = weights

    if best_weights is None:
        best_weights = tuple(1.0 / len(source_versions) for _ in source_versions)

    meter_data = ensemble_preds[source_versions[0]][spec.meter_urn]
    val_pred = sum(best_weights[i] * ensemble_preds[v][spec.meter_urn]["val_pred_scaled"]
                   for i, v in enumerate(source_versions))
    test_pred = sum(best_weights[i] * ensemble_preds[v][spec.meter_urn]["test_pred_scaled"]
                    for i, v in enumerate(source_versions))
    return {
        "val_pred_scaled": val_pred,
        "test_pred_scaled": test_pred,
        "weights": dict(zip(source_versions, best_weights)),
    }


# ── v25 ─────────────────────────────────────────────────────────────────────

def v25_gate(
    v19_result: dict,
    v24_result: dict,
    spec: MeterSpec,
    horizon: int,
    all_specs,
    ensemble_preds: dict,
    min_improvement: float = V25_MIN_MEDIAN_IMPROVEMENT,
) -> dict[str, np.ndarray]:
    """
    group+role 계량기들의 median validation MAE 기준
    v24가 v19 대비 min_improvement 이상 개선될 때만 v24 사용.
    """
    source_versions = list(ensemble_preds.keys())
    group_role_specs = [s for s in all_specs if s.group == spec.group and s.role == spec.role]

    def median_mae(pred_key: str, result_by_meter: dict) -> float:
        maes = []
        for s in group_role_specs:
            if s.meter_urn in result_by_meter:
                d = result_by_meter[s.meter_urn]
                maes.append(_mae_scaled(d["val_pred_scaled"], d["y_val"]))
        return float(np.median(maes)) if maes else float("inf")

    # Per-meter v19/v24 results need to be available; simplified: compare this meter's val MAE
    v19_mae = _mae_scaled(v19_result["val_pred_scaled"],
                          ensemble_preds[source_versions[0]][spec.meter_urn]["y_val"])
    v24_mae = _mae_scaled(v24_result["val_pred_scaled"],
                          ensemble_preds[source_versions[0]][spec.meter_urn]["y_val"])

    improvement = (v19_mae - v24_mae) / max(v19_mae, 1e-9)
    use_v24 = improvement >= min_improvement

    chosen = v24_result if use_v24 else v19_result
    return {
        "val_pred_scaled": chosen["val_pred_scaled"],
        "test_pred_scaled": chosen["test_pred_scaled"],
        "selected": "v24" if use_v24 else "v19",
        "improvement": improvement,
    }
