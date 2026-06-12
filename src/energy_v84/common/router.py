"""
v57 계량기별 라우터 + v63 그룹별 라우터.

v57: v52 vs v53(CatBoost) vs v36 중 val MAE 0.25% 개선 시 교체, base=v52
v63: v57 vs v61(LightGBM) 중 group+horizon 평균 val MAE 0.1% 개선 시 교체, base=v57
     electric은 항상 v57, thermal은 v61(LightGBM) 별도 처리 필요
"""
from __future__ import annotations

import numpy as np

from energy_v84.common.config import (
    V57_MIN_METER_IMPROVEMENT,
    V63_MIN_GROUP_IMPROVEMENT,
)
from energy_v84.common.config import MeterSpec


def _mae(pred: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean(np.abs(pred - y)))


# ── v57: 계량기별 라우터 ─────────────────────────────────────────────────────

def v57_route_meter(
    candidates: dict[str, dict],  # {"v52": {...}, "v53": {...}, "v36": {...}}
    y_val: np.ndarray,
    base: str = "v52",
    min_improvement: float = V57_MIN_METER_IMPROVEMENT,
) -> tuple[str, np.ndarray, np.ndarray]:
    """
    base 대비 0.25% 이상 개선되는 candidate 선택.
    동률 시 base 유지.
    반환: (selected_version, val_pred_scaled, test_pred_scaled)
    """
    base_mae = _mae(candidates[base]["val_pred_scaled"], y_val)
    best_version = base
    best_mae = base_mae

    for v, d in candidates.items():
        if v == base:
            continue
        cand_mae = _mae(d["val_pred_scaled"], y_val)
        improvement = (base_mae - cand_mae) / max(base_mae, 1e-9)
        if improvement >= min_improvement and cand_mae < best_mae:
            best_mae = cand_mae
            best_version = v

    chosen = candidates[best_version]
    return best_version, chosen["val_pred_scaled"], chosen["test_pred_scaled"]


# ── v63: 그룹별 라우터 ──────────────────────────────────────────────────────

def v63_route_group(
    v57_preds: dict[str, dict],   # {meter_urn: {"val_pred_scaled", "test_pred_scaled", "y_val"}}
    v61_preds: dict[str, dict],   # {meter_urn: {...}}  — LightGBM; None for electric (not trained)
    group: str,
    horizon: int,
    all_specs,
    min_improvement: float = V63_MIN_GROUP_IMPROVEMENT,
) -> dict[str, str]:
    """
    그룹 내 모든 계량기의 평균 validation MAE 기준 v57 vs v61 선택.
    electric에서는 v61이 없으므로 항상 v57.
    반환: {meter_urn: selected_version}
    """
    group_specs = [s for s in all_specs if s.group == group]

    if v61_preds is None:
        return {s.meter_urn: "v57" for s in group_specs if s.meter_urn in v57_preds}

    v57_maes, v61_maes = [], []
    for s in group_specs:
        urn = s.meter_urn
        if urn in v57_preds and urn in v61_preds:
            v57_maes.append(_mae(v57_preds[urn]["val_pred_scaled"], v57_preds[urn]["y_val"]))
            v61_maes.append(_mae(v61_preds[urn]["val_pred_scaled"], v61_preds[urn]["y_val"]))

    if not v57_maes:
        return {s.meter_urn: "v57" for s in group_specs}

    v57_mean = float(np.mean(v57_maes))
    v61_mean = float(np.mean(v61_maes))
    improvement = (v57_mean - v61_mean) / max(v57_mean, 1e-9)
    selected = "v61" if improvement >= min_improvement else "v57"

    return {s.meter_urn: selected for s in group_specs if s.meter_urn in v57_preds}
