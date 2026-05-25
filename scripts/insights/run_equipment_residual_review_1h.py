"""First-pass review labels for top equipment residual candidates.

This script joins the top residual candidates with 1h reduced-view context signals,
public event windows, and rule-based interpretation labels. The labels are a
screening aid for manual review, not final equipment-fault labels.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from run_equipment_relation_strength_1h import OUT, TZ, fetch_reduced_1h

INPUT = OUT / "03_top_residual_candidates_1h.csv"

EVENT_WINDOWS = [
    {
        "event_id": "chp_control_mode_update_2019_02_19",
        "module_match": "CHP operation",
        "start_local": "2019-02-18 00:00:00",
        "end_local": "2019-02-21 00:00:00",
        "event_context": "public_chp_control_update_context",
    },
    {
        "event_id": "pv_group_1_2_commissioning_2019_06",
        "module_match": "PV performance",
        "start_local": "2019-06-01 00:00:00",
        "end_local": "2019-07-15 00:00:00",
        "event_context": "public_pv_commissioning_context",
    },
    {
        "event_id": "pv_group_4_6_commissioning_2020_06",
        "module_match": "PV performance",
        "start_local": "2020-05-15 00:00:00",
        "end_local": "2020-07-15 00:00:00",
        "event_context": "public_pv_commissioning_context",
    },
    {
        "event_id": "transformer_replacement_context_2020_09",
        "module_match": "*",
        "start_local": "2020-09-01 00:00:00",
        "end_local": "2020-09-21 00:00:00",
        "event_context": "public_transformer_replacement_context",
    },
    {
        "event_id": "heating_chp_modernization_2023_06",
        "module_match": "CHP operation",
        "start_local": "2023-05-15 00:00:00",
        "end_local": "2023-07-01 00:00:00",
        "event_context": "public_heating_chp_modernization_context",
    },
    {
        "event_id": "cooling_load_scaling_issue_candidate_2023_09",
        "module_match": "Cooling efficiency",
        "start_local": "2023-09-19 18:00:00",
        "end_local": "2023-09-21 06:00:00",
        "event_context": "public_cooling_load_scaling_issue_context",
    },
]


def event_overlap(module: str, local_ts: pd.Timestamp) -> tuple[str, str]:
    hits = []
    contexts = []
    for event in EVENT_WINDOWS:
        if event["module_match"] not in {"*", module}:
            continue
        start = pd.Timestamp(event["start_local"], tz=TZ)
        end = pd.Timestamp(event["end_local"], tz=TZ)
        if start <= local_ts < end:
            hits.append(event["event_id"])
            contexts.append(event["event_context"])
    return ";".join(hits), ";".join(contexts)


def classify(row: pd.Series) -> tuple[str, str, str, str]:
    module = str(row["module"])
    residual = float(row["residual"])
    abs_z = float(row["abs_z"])
    event_ids = str(row.get("event_overlap", ""))
    labels = []
    evidence = []
    confidence = "medium"
    actionability = "manual_review"

    if event_ids and event_ids != "nan":
        labels.append("regime_or_known_event_context")
        evidence.append("public_event_window")
        confidence = "medium_high"

    if abs_z >= 10:
        evidence.append("large_residual_abs_z_ge_10")
    elif abs_z >= 5:
        evidence.append("moderate_residual_abs_z_ge_5")
    else:
        evidence.append("residual_abs_z_ge_top30")

    if module == "Cooling efficiency":
        thermal = row.get("cooling_thermal_P", np.nan)
        actual = row.get("cooling_elec_P", np.nan)
        ta = row.get("Ta", np.nan)
        if residual > 0:
            labels.append("cooling_over_electricity_candidate")
            actionability = "efficiency_review_candidate"
        else:
            labels.append("cooling_under_electricity_or_thermal_scaling_candidate")
            actionability = "data_quality_or_thermal_relation_review"
        if pd.notna(thermal) and pd.notna(actual) and thermal > 50000:
            evidence.append("cooling_thermal_load_high")
        if pd.notna(ta) and ta >= 20:
            evidence.append("warm_weather_context")

    elif module == "CHP operation":
        actual = row.get("chp_elec_P", np.nan)
        heat = row.get("chp_heat_P", np.nan)
        heating_total = row.get("heating_total_P", np.nan)
        if pd.notna(actual) and pd.notna(heat):
            if abs(actual) > 50000 and abs(heat) < 20000:
                labels.append("chp_electric_heat_mismatch_candidate")
                actionability = "physical_relation_review_candidate"
            elif abs(actual) < 10000 and abs(heat) > 50000:
                labels.append("chp_heat_without_electricity_candidate")
                actionability = "physical_relation_review_candidate"
            else:
                labels.append("chp_operation_residual_candidate")
        else:
            labels.append("chp_operation_residual_candidate")
        if pd.notna(heating_total) and abs(heating_total) > 100000:
            evidence.append("heating_demand_proxy_present")

    elif module == "PV performance":
        igm = row.get("Igm", np.nan)
        hour = row.get("hour", np.nan)
        if residual > 0:
            labels.append("pv_underperformance_candidate")
            actionability = "pv_performance_review_candidate"
        else:
            labels.append("pv_high_generation_candidate")
            actionability = "extreme_but_explainable_review"
        if pd.notna(igm) and igm > 200:
            evidence.append("high_irradiance_context")
        if pd.notna(hour) and 10 <= int(hour) <= 15:
            evidence.append("midday_context")

    if not labels:
        labels.append("unclear_residual_candidate")
        confidence = "low"

    # Known issue registry not found in project search; keep this explicit.
    known_issue_overlap = "not_checked_registry_absent_in_project_search"
    return ";".join(labels), ";".join(dict.fromkeys(evidence)), confidence, actionability + f"; known_issue={known_issue_overlap}"


def cluster_candidates(candidates: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for module, group in candidates.sort_values(["module", "ts"]).groupby("module"):
        group = group.copy().reset_index(drop=True)
        cluster_id = 0
        previous_ts = None
        cluster_ids = []
        for ts in group["ts"]:
            if previous_ts is None or (ts - previous_ts) > pd.Timedelta(hours=2):
                cluster_id += 1
            cluster_ids.append(cluster_id)
            previous_ts = ts
        group["cluster_id"] = cluster_ids
        for cid, cg in group.groupby("cluster_id"):
            dominant_label = cg["review_label"].value_counts().idxmax()
            rows.append(
                {
                    "module": module,
                    "cluster_id": f"{module.replace(' ', '_').lower()}_{cid:02d}",
                    "start_ts": cg["ts"].min(),
                    "end_ts": cg["ts"].max(),
                    "start_local_ts": cg["local_ts"].min(),
                    "end_local_ts": cg["local_ts"].max(),
                    "candidate_count": int(len(cg)),
                    "max_abs_z": float(cg["abs_z"].max()),
                    "mean_abs_z": float(cg["abs_z"].mean()),
                    "dominant_label": dominant_label,
                    "event_overlap": ";".join(sorted(set(";".join(cg["event_overlap"].fillna("")).split(";")) - {""})),
                    "actionability": cg["actionability"].value_counts().idxmax(),
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    candidates = pd.read_csv(INPUT)
    candidates["ts"] = pd.to_datetime(candidates["ts"], utc=True)
    candidates["local_ts"] = pd.to_datetime(candidates["local_ts"], utc=True).dt.tz_convert(TZ)
    context = fetch_reduced_1h()
    context_cols = [
        "ts",
        "local_ts",
        "hour",
        "month",
        "cooling_thermal_P",
        "cooling_elec_P",
        "chp_elec_P",
        "chp_heat_P",
        "heating_total_P",
        "pv_P",
        "Igm",
        "Ta",
    ]
    enriched = candidates.merge(context[context_cols], on="ts", how="left", suffixes=("", "_ctx"))
    if "local_ts_ctx" in enriched.columns:
        enriched = enriched.drop(columns=["local_ts_ctx"])

    overlaps = enriched.apply(lambda row: event_overlap(str(row["module"]), row["local_ts"]), axis=1)
    enriched["event_overlap"] = [item[0] for item in overlaps]
    enriched["event_context"] = [item[1] for item in overlaps]
    labels = enriched.apply(classify, axis=1)
    enriched["review_label"] = [item[0] for item in labels]
    enriched["evidence_type"] = [item[1] for item in labels]
    enriched["confidence"] = [item[2] for item in labels]
    enriched["actionability"] = [item[3] for item in labels]
    enriched["data_layer"] = "ems.reduced_measurement_1h"
    enriched["requires_raw_check"] = True
    enriched["manual_review_status"] = "auto_prelabel_only"

    enriched.to_csv(OUT / "05_top_residual_review_prelabels_1h.csv", index=False)
    clusters = cluster_candidates(enriched)
    clusters.to_csv(OUT / "05_residual_candidate_clusters_1h.csv", index=False)

    label_summary = (
        enriched.groupby(["module", "review_label"], dropna=False)
        .size()
        .reset_index(name="candidate_count")
        .sort_values(["module", "candidate_count"], ascending=[True, False])
    )
    label_summary.to_csv(OUT / "05_review_label_summary_1h.csv", index=False)

    module_summary = (
        enriched.groupby("module")
        .agg(
            candidate_count=("module", "size"),
            event_overlap_count=("event_overlap", lambda s: int((s.fillna("") != "").sum())),
            max_abs_z=("abs_z", "max"),
            median_abs_z=("abs_z", "median"),
            dominant_label=("review_label", lambda s: s.value_counts().idxmax()),
        )
        .reset_index()
    )
    module_summary["interpretation_boundary"] = "자동 사전 라벨이며 현장·raw·known issue 검토 전 확정 금지"
    module_summary.to_csv(OUT / "05_module_review_summary_1h.csv", index=False)

    brief = [
        "# Step 5 상위 residual 후보 1차 라벨링\n",
        f"- 생성 시각(UTC): {datetime.now(timezone.utc).isoformat()}",
        "- 입력: `03_top_residual_candidates_1h.csv` 모듈별 상위 30개",
        "- known issue registry: 현재 SKN/EMS 프로젝트 파일 검색에서는 별도 issue registry 파일을 찾지 못함",
        "- 라벨 성격: 자동 사전 분류. 현장·raw·BMS·정비 이력 검토 전 확정 판단 금지",
        "\n## 모듈별 요약\n",
    ]
    for row in module_summary.itertuples():
        brief.append(
            f"- {row.module}: candidates={row.candidate_count}, event_overlap={row.event_overlap_count}, max_abs_z={row.max_abs_z:.2f}, dominant_label={row.dominant_label}"
        )
    brief.append("\n## cluster 상위 예시\n")
    for row in clusters.sort_values("max_abs_z", ascending=False).head(10).itertuples():
        brief.append(
            f"- {row.cluster_id}: {row.start_local_ts}~{row.end_local_ts}, n={row.candidate_count}, max_abs_z={row.max_abs_z:.2f}, label={row.dominant_label}, event={row.event_overlap}"
        )
    brief.append("\n## 생성 파일\n")
    for name in [
        "05_top_residual_review_prelabels_1h.csv",
        "05_residual_candidate_clusters_1h.csv",
        "05_review_label_summary_1h.csv",
        "05_module_review_summary_1h.csv",
        "STEP5_RESIDUAL_REVIEW_BRIEF.md",
    ]:
        brief.append(f"- `{OUT / name}`")
    (OUT / "STEP5_RESIDUAL_REVIEW_BRIEF.md").write_text("\n".join(brief) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "module_summary": module_summary.to_dict(orient="records"),
                "cluster_count": int(len(clusters)),
                "out_dir": str(OUT),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
