"""Quantify Cooling and CHP equipment-system anomaly candidates in 1h EMS data.

Definitions:
- Uses reduced 1h relation residuals as screening evidence.
- Uses robust z-scores from train-period residuals.
- Clusters adjacent candidate hours into events.
- Labels candidates as equipment-system anomaly candidates, not confirmed faults.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from run_equipment_relation_strength_1h import OUT, fetch_reduced_1h

TRAIN_UNTIL = pd.Timestamp("2022-01-01", tz="UTC")
SEVERE_Z = 6.0
MODERATE_Z = 4.0
CHP_PUBLIC_POWER_TO_HEAT_RATIO = 0.677
MAX_CLUSTER_GAP_HOURS = 2


@dataclass
class ModelResult:
    module: str
    frame: pd.DataFrame
    target: str
    features: list[str]
    robust_scale: float
    train_median: float
    train_r2: float
    test_r2: float
    train_mae: float
    test_mae: float


def fit_relation_model(data: pd.DataFrame, module: str, target: str, features: list[str]) -> ModelResult:
    cols = [target, *features]
    frame = data.dropna(subset=cols).copy()
    train = frame[frame["ts"] < TRAIN_UNTIL].copy()
    test = frame[frame["ts"] >= TRAIN_UNTIL].copy()
    x_train = np.column_stack([np.ones(len(train)), train[features].to_numpy(dtype=float)])
    x_all = np.column_stack([np.ones(len(frame)), frame[features].to_numpy(dtype=float)])
    coef = np.linalg.lstsq(x_train, train[target].to_numpy(dtype=float), rcond=None)[0]
    frame["pred"] = x_all @ coef
    frame["residual"] = frame[target] - frame["pred"]
    train_resid = frame.loc[frame["ts"] < TRAIN_UNTIL, "residual"]
    median = float(train_resid.median())
    mad = float(np.median(np.abs(train_resid - median)))
    robust_scale = 1.4826 * mad if mad > 0 else float(train_resid.std(ddof=0))
    frame["robust_z"] = (frame["residual"] - median) / robust_scale if robust_scale > 0 else np.nan
    frame["abs_robust_z"] = frame["robust_z"].abs()

    def metrics(subset: pd.DataFrame) -> tuple[float, float]:
        residual = subset[target] - subset["pred"]
        denom = float(((subset[target] - subset[target].mean()) ** 2).sum())
        r2 = float(1 - (residual**2).sum() / denom) if denom > 0 else np.nan
        mae = float(residual.abs().mean())
        return r2, mae

    train_eval = frame[frame["ts"] < TRAIN_UNTIL]
    test_eval = frame[frame["ts"] >= TRAIN_UNTIL]
    train_r2, train_mae = metrics(train_eval)
    test_r2, test_mae = metrics(test_eval)
    return ModelResult(module, frame, target, features, robust_scale, median, train_r2, test_r2, train_mae, test_mae)


def positive_quantile(series: pd.Series, q: float) -> float:
    positive = series.dropna()
    positive = positive[positive > 0]
    if len(positive) == 0:
        return np.nan
    return float(positive.quantile(q))


def cluster_events(candidates: pd.DataFrame, module: str, candidate_type: str) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()
    frame = candidates.sort_values("ts").copy().reset_index(drop=True)
    cluster_ids = []
    cid = 0
    prev_ts = None
    for ts in frame["ts"]:
        if prev_ts is None or (ts - prev_ts) > pd.Timedelta(hours=MAX_CLUSTER_GAP_HOURS):
            cid += 1
        cluster_ids.append(cid)
        prev_ts = ts
    frame["cluster_num"] = cluster_ids
    rows = []
    for cluster_num, group in frame.groupby("cluster_num"):
        rows.append(
            {
                "module": module,
                "candidate_type": candidate_type,
                "event_id": f"{module.lower().replace(' ', '_')}_{candidate_type}_{cluster_num:03d}",
                "start_ts": group["ts"].min(),
                "end_ts": group["ts"].max(),
                "start_local_ts": group["local_ts"].min(),
                "end_local_ts": group["local_ts"].max(),
                "duration_hours_observed": int(len(group)),
                "max_abs_robust_z": float(group["abs_robust_z"].max()) if "abs_robust_z" in group else np.nan,
                "median_abs_robust_z": float(group["abs_robust_z"].median()) if "abs_robust_z" in group else np.nan,
                "min_actual": float(group["actual"].min()) if "actual" in group else np.nan,
                "max_actual": float(group["actual"].max()) if "actual" in group else np.nan,
                "min_pred": float(group["pred"].min()) if "pred" in group else np.nan,
                "max_pred": float(group["pred"].max()) if "pred" in group else np.nan,
                "evidence_type": ";".join(sorted(set(group.get("evidence_type", pd.Series(dtype=str)).dropna().astype(str)))),
                "interpretation_boundary": "equipment-system anomaly candidate; not confirmed equipment fault",
            }
        )
    return pd.DataFrame(rows)


def summarize_candidates(candidates: pd.DataFrame, module: str, candidate_type: str, threshold_desc: str) -> dict:
    events = cluster_events(candidates, module, candidate_type)
    return {
        "module": module,
        "candidate_type": candidate_type,
        "threshold_desc": threshold_desc,
        "candidate_hours": int(len(candidates)),
        "event_count": int(len(events)),
        "total_observed_event_hours": int(events["duration_hours_observed"].sum()) if len(events) else 0,
        "max_abs_robust_z": float(candidates["abs_robust_z"].max()) if len(candidates) else np.nan,
        "first_local_ts": candidates["local_ts"].min() if len(candidates) else pd.NaT,
        "last_local_ts": candidates["local_ts"].max() if len(candidates) else pd.NaT,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data = fetch_reduced_1h()

    cooling_model = fit_relation_model(
        data,
        "Cooling efficiency",
        "cooling_elec_P",
        ["cooling_thermal_P", "Ta", "hour_sin", "hour_cos", "month_sin", "month_cos"],
    )
    chp_model = fit_relation_model(
        data,
        "CHP operation",
        "chp_elec_P",
        ["chp_heat_P", "heating_total_P", "Ta", "hour_sin", "hour_cos", "month_sin", "month_cos"],
    )

    thresholds = []
    event_frames = []
    summary_rows = []

    # Cooling operating thresholds from observed distribution.
    c = cooling_model.frame.copy()
    c["actual"] = c["cooling_elec_P"]
    c_thermal_active = positive_quantile(c["cooling_thermal_P"], 0.50)
    c_elec_active = positive_quantile(c["cooling_elec_P"], 0.50)
    thresholds.extend(
        [
            {"module": "Cooling efficiency", "threshold": "thermal_active_p50_positive", "value": c_thermal_active, "unit": "W_or_dataset_unit"},
            {"module": "Cooling efficiency", "threshold": "electric_active_p50_positive", "value": c_elec_active, "unit": "W_or_dataset_unit"},
            {"module": "Cooling efficiency", "threshold": "moderate_abs_robust_z", "value": MODERATE_Z, "unit": "robust_z"},
            {"module": "Cooling efficiency", "threshold": "severe_abs_robust_z", "value": SEVERE_Z, "unit": "robust_z"},
        ]
    )
    c_active = c[c["cooling_thermal_P"] >= c_thermal_active].copy()
    c_over = c_active[(c_active["robust_z"] >= SEVERE_Z)].copy()
    c_over["evidence_type"] = "relation_residual;cooling_electric_above_expected;active_thermal_load"
    c_under = c_active[(c_active["robust_z"] <= -SEVERE_Z)].copy()
    c_under["evidence_type"] = "relation_residual;cooling_electric_below_expected_or_thermal_scaling;active_thermal_load"
    c_moderate = c_active[(c_active["abs_robust_z"] >= MODERATE_Z)].copy()
    c_moderate["evidence_type"] = "relation_residual;moderate_or_severe;active_thermal_load"

    for cand, ctype, desc in [
        (c_moderate, "moderate_relation_candidate", f"active cooling thermal >= {c_thermal_active:.3f} and abs robust_z >= {MODERATE_Z}"),
        (c_over, "severe_over_electricity_candidate", f"active cooling thermal >= {c_thermal_active:.3f} and robust_z >= {SEVERE_Z}"),
        (c_under, "severe_under_electricity_or_thermal_scaling_candidate", f"active cooling thermal >= {c_thermal_active:.3f} and robust_z <= -{SEVERE_Z}"),
    ]:
        summary_rows.append(summarize_candidates(cand, "Cooling efficiency", ctype, desc))
        ev = cluster_events(cand, "Cooling efficiency", ctype)
        if len(ev):
            event_frames.append(ev)

    # CHP thresholds and candidates.
    h = chp_model.frame.copy()
    h["actual"] = h["chp_elec_P"]
    h["abs_chp_elec_P"] = h["chp_elec_P"].abs()
    h["chp_el_heat_ratio_abs"] = np.where(h["chp_heat_P"] > 0, h["abs_chp_elec_P"] / h["chp_heat_P"], np.nan)
    heat_p50 = positive_quantile(h["chp_heat_P"], 0.50)
    heat_p75 = positive_quantile(h["chp_heat_P"], 0.75)
    heat_p10 = positive_quantile(h["chp_heat_P"], 0.10)
    elec_p50 = positive_quantile(h["abs_chp_elec_P"], 0.50)
    elec_p75 = positive_quantile(h["abs_chp_elec_P"], 0.75)
    elec_p10 = positive_quantile(h["abs_chp_elec_P"], 0.10)
    ratio_context = h[(h["chp_heat_P"] >= heat_p50) & (h["abs_chp_elec_P"] >= elec_p50)].copy()
    ratio_diff = ratio_context["chp_el_heat_ratio_abs"] - CHP_PUBLIC_POWER_TO_HEAT_RATIO
    ratio_median = float(ratio_diff.median())
    ratio_mad = float(np.median(np.abs(ratio_diff - ratio_median)))
    ratio_scale = 1.4826 * ratio_mad if ratio_mad > 0 else float(ratio_diff.std(ddof=0))
    h["ratio_robust_z"] = (h["chp_el_heat_ratio_abs"] - CHP_PUBLIC_POWER_TO_HEAT_RATIO - ratio_median) / ratio_scale
    h["abs_ratio_robust_z"] = h["ratio_robust_z"].abs()
    h["abs_robust_z"] = h["robust_z"].abs()

    thresholds.extend(
        [
            {"module": "CHP operation", "threshold": "heat_active_p50_positive", "value": heat_p50, "unit": "W_or_dataset_unit"},
            {"module": "CHP operation", "threshold": "heat_high_p75_positive", "value": heat_p75, "unit": "W_or_dataset_unit"},
            {"module": "CHP operation", "threshold": "heat_low_p10_positive", "value": heat_p10, "unit": "W_or_dataset_unit"},
            {"module": "CHP operation", "threshold": "abs_electric_active_p50_positive", "value": elec_p50, "unit": "W_or_dataset_unit"},
            {"module": "CHP operation", "threshold": "abs_electric_high_p75_positive", "value": elec_p75, "unit": "W_or_dataset_unit"},
            {"module": "CHP operation", "threshold": "abs_electric_low_p10_positive", "value": elec_p10, "unit": "W_or_dataset_unit"},
            {"module": "CHP operation", "threshold": "public_power_to_heat_ratio", "value": CHP_PUBLIC_POWER_TO_HEAT_RATIO, "unit": "abs(chp_elec_P)/chp_heat_P"},
            {"module": "CHP operation", "threshold": "severe_abs_residual_robust_z", "value": SEVERE_Z, "unit": "robust_z"},
            {"module": "CHP operation", "threshold": "severe_abs_ratio_robust_z", "value": SEVERE_Z, "unit": "robust_z"},
        ]
    )
    h_active = h[(h["chp_heat_P"] >= heat_p50) | (h["abs_chp_elec_P"] >= elec_p50)].copy()
    h_resid = h_active[h_active["abs_robust_z"] >= SEVERE_Z].copy()
    h_resid["evidence_type"] = "relation_residual;chp_electric_heat_relation"
    h_heat_without = h[(h["chp_heat_P"] >= heat_p75) & (h["abs_chp_elec_P"] <= elec_p10)].copy()
    h_heat_without["evidence_type"] = "physical_relation;high_heat_low_electric"
    h_elec_without = h[(h["abs_chp_elec_P"] >= elec_p75) & (h["chp_heat_P"] <= heat_p10)].copy()
    h_elec_without["evidence_type"] = "physical_relation;high_electric_low_heat"
    h_ratio = h[(h["chp_heat_P"] >= heat_p50) & (h["abs_chp_elec_P"] >= elec_p50) & (h["abs_ratio_robust_z"] >= SEVERE_Z)].copy()
    h_ratio["evidence_type"] = "physical_ratio;public_power_to_heat_ratio_deviation"
    # For ratio events, expose ratio z through the common abs_robust_z field for event ranking.
    h_ratio["abs_robust_z"] = h_ratio["abs_ratio_robust_z"]

    for cand, ctype, desc in [
        (h_resid, "severe_relation_residual_candidate", f"active CHP heat/electric and abs residual robust_z >= {SEVERE_Z}"),
        (h_heat_without, "heat_without_electricity_candidate", "chp_heat_P >= heat positive p75 and abs(chp_elec_P) <= abs electric positive p10"),
        (h_elec_without, "electricity_without_heat_candidate", "abs(chp_elec_P) >= abs electric positive p75 and chp_heat_P <= heat positive p10"),
        (h_ratio, "power_to_heat_ratio_deviation_candidate", f"active heat/electric and abs ratio robust_z >= {SEVERE_Z}; public ratio={CHP_PUBLIC_POWER_TO_HEAT_RATIO}"),
    ]:
        summary_rows.append(summarize_candidates(cand, "CHP operation", ctype, desc))
        ev = cluster_events(cand, "CHP operation", ctype)
        if len(ev):
            event_frames.append(ev)

    threshold_df = pd.DataFrame(thresholds)
    threshold_df.to_csv(OUT / "06_anomaly_quantification_thresholds_1h.csv", index=False)
    model_df = pd.DataFrame(
        [
            {
                "module": m.module,
                "target": m.target,
                "features": ", ".join(m.features),
                "n_total": len(m.frame),
                "train_r2": m.train_r2,
                "test_r2": m.test_r2,
                "train_mae": m.train_mae,
                "test_mae": m.test_mae,
                "train_residual_median": m.train_median,
                "train_residual_robust_scale": m.robust_scale,
            }
            for m in [cooling_model, chp_model]
        ]
    )
    model_df.to_csv(OUT / "06_relation_model_quality_1h.csv", index=False)
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(OUT / "06_anomaly_quantification_summary_1h.csv", index=False)
    events = pd.concat(event_frames, ignore_index=True) if event_frames else pd.DataFrame()
    events.to_csv(OUT / "06_anomaly_event_clusters_1h.csv", index=False)

    # Extract a compact top-event table for briefing.
    top_events = events.sort_values(["module", "max_abs_robust_z"], ascending=[True, False]).groupby("module").head(10)
    top_events.to_csv(OUT / "06_top_anomaly_events_1h.csv", index=False)

    brief = [
        "# Step 6 Cooling/CHP 이상 후보 정량화\n",
        f"- 생성 시각(UTC): {datetime.now(timezone.utc).isoformat()}",
        "- 기준 relation: `ems.reduced_measurement_1h`",
        f"- moderate threshold: abs robust z >= {MODERATE_Z}",
        f"- severe threshold: abs robust z >= {SEVERE_Z}",
        "- 해석 경계: 설비계통 이상 후보 정량화이며 설비 고장 확정이 아님",
        "\n## 후보 수 요약\n",
    ]
    for row in summary.itertuples():
        brief.append(
            f"- {row.module} / {row.candidate_type}: hours={row.candidate_hours}, events={row.event_count}, max_abs_z={row.max_abs_robust_z:.2f}, first={row.first_local_ts}, last={row.last_local_ts}"
        )
    brief.append("\n## 상위 event 예시\n")
    for row in top_events.head(20).itertuples():
        brief.append(
            f"- {row.event_id}: {row.start_local_ts}~{row.end_local_ts}, hours={row.duration_hours_observed}, max_abs_z={row.max_abs_robust_z:.2f}, evidence={row.evidence_type}"
        )
    brief.append("\n## 생성 파일\n")
    for name in [
        "06_anomaly_quantification_thresholds_1h.csv",
        "06_relation_model_quality_1h.csv",
        "06_anomaly_quantification_summary_1h.csv",
        "06_anomaly_event_clusters_1h.csv",
        "06_top_anomaly_events_1h.csv",
        "STEP6_ANOMALY_QUANTIFICATION_BRIEF.md",
    ]:
        brief.append(f"- `{OUT / name}`")
    (OUT / "STEP6_ANOMALY_QUANTIFICATION_BRIEF.md").write_text("\n".join(brief) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "summary": summary.to_dict(orient="records"),
                "model_quality": model_df.to_dict(orient="records"),
                "event_count_total": int(len(events)),
                "out_dir": str(OUT),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
