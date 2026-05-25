"""Run a 1h hypothesis-gate analysis for EMS data insight work.

This script checks whether the peak/co-high-load insight hypothesis has enough
empirical signal to justify a fuller analysis stage. It writes compact tables and
one markdown note under outputs/tables/data_insight_hypothesis_gate_1h/.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs" / "tables" / "data_insight_hypothesis_gate_1h"
TZ = "Europe/Berlin"


def load_env(path: Path) -> None:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connect() -> psycopg.Connection:
    load_env(ROOT / ".env")
    return psycopg.connect(
        host=os.environ["DB_HOST"],
        port=os.environ["DB_PORT"],
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


def frame_from_query(conn: psycopg.Connection, sql: str, params: tuple = ()) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        cols = [desc.name for desc in cur.description]
    return pd.DataFrame(rows, columns=cols)


def pct(value: float) -> float:
    if pd.isna(value):
        return np.nan
    return float(value) * 100.0


def safe_ratio(num: float, den: float) -> float:
    if den == 0 or pd.isna(den):
        return np.nan
    return float(num) / float(den)


def bool_object_frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if "passed" in frame.columns:
        frame["passed"] = frame["passed"].astype(object)
    return frame


def build_cr_quality_gate(
    frame: pd.DataFrame,
    load_balance: pd.DataFrame,
    *,
    min_site_coverage_pct: float = 99.0,
    min_weather_coverage_pct: float = 95.0,
    max_null_row_rate_pct: float = 1.0,
) -> pd.DataFrame:
    """Build CR mart quality checks used for the hypothesis gate."""
    total_rows = len(frame)
    site_coverage = pct(frame["site_p"].notna().mean()) if total_rows else 0.0
    ta_coverage = pct(frame["ta_c"].notna().mean()) if "ta_c" in frame else 0.0
    igm_coverage = pct(frame["igm"].notna().mean()) if "igm" in frame else 0.0

    resolution_col = "resolution_code" if "resolution_code" in load_balance.columns else "resolution"
    cr_1h = load_balance.copy()
    if resolution_col in cr_1h.columns:
        cr_1h = cr_1h[cr_1h[resolution_col].astype(str) == "1h"]
    if "processing_level" in cr_1h.columns:
        cr_1h = cr_1h[cr_1h["processing_level"] == "corrected_resampled"]

    files = int(cr_1h["files"].sum()) if "files" in cr_1h else int(len(cr_1h))
    csv_rows = float(cr_1h["csv_rows"].sum()) if "csv_rows" in cr_1h else 0.0
    null_rows = float(cr_1h["null_value_rows"].sum()) if "null_value_rows" in cr_1h else 0.0
    unaccounted_rows = float(cr_1h["unaccounted_rows"].sum()) if "unaccounted_rows" in cr_1h else np.nan
    status_loaded_pct = pct((cr_1h["status"] == "loaded").mean()) if "status" in cr_1h and len(cr_1h) else 0.0
    null_rate = pct(null_rows / csv_rows) if csv_rows else 0.0

    rows = [
        {
            "check": "site_total_p_coverage",
            "value": site_coverage,
            "threshold": min_site_coverage_pct,
            "unit": "pct",
            "passed": bool(site_coverage >= min_site_coverage_pct),
            "basis": "ems.reduced_measurement_1h electricity/total/P",
        },
        {
            "check": "weather_ta_coverage",
            "value": ta_coverage,
            "threshold": min_weather_coverage_pct,
            "unit": "pct",
            "passed": bool(ta_coverage >= min_weather_coverage_pct),
            "basis": "ems.reduced_measurement_1h weather/Ta",
        },
        {
            "check": "weather_igm_coverage",
            "value": igm_coverage,
            "threshold": min_weather_coverage_pct,
            "unit": "pct",
            "passed": bool(igm_coverage >= min_weather_coverage_pct),
            "basis": "ems.reduced_measurement_1h weather/Igm",
        },
        {
            "check": "cr_1h_status_loaded",
            "value": status_loaded_pct,
            "threshold": 100.0,
            "unit": "pct",
            "passed": bool(files > 0 and status_loaded_pct == 100.0),
            "basis": "ems.full_file_load_balance status",
        },
        {
            "check": "cr_1h_load_balance",
            "value": unaccounted_rows,
            "threshold": 0.0,
            "unit": "rows",
            "passed": bool(files > 0 and unaccounted_rows == 0),
            "basis": "ems.full_file_load_balance unaccounted_rows",
        },
        {
            "check": "cr_1h_null_row_rate",
            "value": null_rate,
            "threshold": max_null_row_rate_pct,
            "unit": "pct",
            "passed": bool(null_rate <= max_null_row_rate_pct),
            "basis": "ems.full_file_load_balance null_value_rows/csv_rows",
        },
    ]
    return bool_object_frame(rows)


def build_group_quality_gate(groups: pd.DataFrame, *, min_group_coverage_pct: float = 95.0) -> pd.DataFrame:
    """Report coverage/null rate for each equipment-group P series."""
    rows = []
    for group in groups.columns:
        coverage = pct(groups[group].notna().mean())
        null_pct = pct(groups[group].isna().mean())
        rows.append(
            {
                "group": group,
                "coverage_pct": coverage,
                "null_pct": null_pct,
                "threshold_pct": min_group_coverage_pct,
                "passed": bool(coverage >= min_group_coverage_pct),
            }
        )
    return bool_object_frame(rows).sort_values(["passed", "coverage_pct", "group"], ascending=[True, True, True])


def build_redundancy_quality_gate(
    pair_stats: pd.DataFrame,
    *,
    min_corr: float = 0.90,
    min_overlap_hours: int = 24,
) -> pd.DataFrame:
    """Classify redundancy pair consistency for quality context."""
    rows = []
    for row in pair_stats.to_dict(orient="records"):
        primary = row["primary_meter_urn"]
        redundant = row["redundant_meter_urn"]
        overlap_hours = int(row.get("overlap_hours") or 0)
        corr = row.get("corr")
        corr_value = float(corr) if corr is not None and not pd.isna(corr) else np.nan
        passed = bool(overlap_hours >= min_overlap_hours and not pd.isna(corr_value) and corr_value >= min_corr)
        rows.append(
            {
                "pair": f"{primary}__{redundant}",
                "primary_meter_urn": primary,
                "redundant_meter_urn": redundant,
                "equipment_group": row.get("equipment_group"),
                "overlap_hours": overlap_hours,
                "corr": corr_value,
                "mae": row.get("mae"),
                "min_corr": min_corr,
                "min_overlap_hours": min_overlap_hours,
                "passed": passed,
            }
        )
    return bool_object_frame(rows).sort_values(["passed", "corr", "overlap_hours"], ascending=[True, True, True])


def build_hypothesis_decision_table(
    *,
    top_hour_share: float,
    top_month_share: float,
    repeated_groups_60: int,
    baseload_candidate_count: int,
    group_site_accounting_verified: bool,
    business_data_available: bool,
    fault_labels_available: bool,
) -> pd.DataFrame:
    """Build the progress/hold/excluded decision table for insight hypotheses."""
    rows = [
        {
            "hypothesis": "피크 시간대 집중",
            "decision": "진행" if top_hour_share >= 0.50 else "보류",
            "basis": f"상위 6개 로컬 시간 피크 점유율 {pct(top_hour_share):.1f}%",
        },
        {
            "hypothesis": "계절 피크 집중",
            "decision": "진행" if top_month_share >= 0.50 else "보류",
            "basis": f"상위 3개 월 피크 점유율 {pct(top_month_share):.1f}%",
        },
        {
            "hypothesis": "피크 동시상승 계통",
            "decision": "진행" if repeated_groups_60 >= 2 else "보류",
            "basis": f"site 피크 중 자기 기준 고부하 60% 이상 계통 {repeated_groups_60}개",
        },
        {
            "hypothesis": "상시부하 후보",
            "decision": "진행" if baseload_candidate_count >= 1 else "보류",
            "basis": f"야간/전체 평균 비율 상위 후보 {baseload_candidate_count}개",
        },
        {
            "hypothesis": "계통별 site 기여율",
            "decision": "진행" if group_site_accounting_verified else "보류",
            "basis": "group-site 회계 관계 검증 필요" if not group_site_accounting_verified else "group-site 회계 관계 검증 완료",
        },
        {
            "hypothesis": "비용/계약전력 해석",
            "decision": "진행" if business_data_available else "제외",
            "basis": "요금·계약 데이터 확인 필요" if not business_data_available else "요금·계약 데이터 확인됨",
        },
        {
            "hypothesis": "설비 고장 판단",
            "decision": "진행" if fault_labels_available else "제외",
            "basis": "고장 라벨·조치 이력 없음" if not fault_labels_available else "고장 라벨·조치 이력 확인됨",
        },
    ]
    return pd.DataFrame(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    with connect() as conn:
        reduced = frame_from_query(
            conn,
            """
            select ts, category, subcategory, measurement, value
            from ems.reduced_measurement_1h
            where (
                category = 'electricity' and subcategory in ('total', 'pv', 'chp') and measurement = 'P'
            ) or (
                category = 'weather' and subcategory = 'weather' and measurement in ('Ta', 'Igm')
            )
            order by ts
            """,
        )
        groups = frame_from_query(
            conn,
            """
            select c.ts,
                   md.equipment_group,
                   count(distinct c.meter_urn) as meter_count,
                   sum(c.value) as group_p
            from ems.cr_measurement_1h c
            join ems.meter_definition md on md.meter_urn = c.meter_urn
            where c.measurement = 'P'
              and c.value is not null
              and md.meter_domain = 'electricity'
              and md.equipment_group is not null
            group by c.ts, md.equipment_group
            order by c.ts, md.equipment_group
            """,
        )
        group_meters = frame_from_query(
            conn,
            """
            select equipment_group,
                   count(distinct meter_urn) as meters,
                   string_agg(distinct building_code, ',' order by building_code) as buildings
            from ems.meter_definition
            where meter_domain = 'electricity'
              and equipment_group is not null
            group by equipment_group
            order by equipment_group
            """,
        )
        load_balance = frame_from_query(
            conn,
            """
            select processing_level,
                   resolution_code,
                   status,
                   count(*) as files,
                   sum(csv_rows) as csv_rows,
                   sum(inserted_rows) as inserted_rows,
                   sum(null_value_rows) as null_value_rows,
                   sum(unaccounted_rows) as unaccounted_rows
            from ems.full_file_load_balance
            where processing_level = 'corrected_resampled'
              and resolution_code = '1h'
            group by processing_level, resolution_code, status
            order by processing_level, resolution_code, status
            """,
        )
        pair_stats = frame_from_query(
            conn,
            """
            select r.primary_meter_urn,
                   r.redundant_meter_urn,
                   r.equipment_group,
                   count(*) as overlap_hours,
                   corr(p.value, q.value) as corr,
                   avg(abs(p.value - q.value)) as mae
            from ems.meter_redundancy r
            join ems.cr_measurement_1h p
              on p.meter_urn = r.primary_meter_urn
             and p.measurement = 'P'
             and p.value is not null
            join ems.cr_measurement_1h q
              on q.meter_urn = r.redundant_meter_urn
             and q.measurement = 'P'
             and q.ts = p.ts
             and q.value is not null
            group by r.primary_meter_urn, r.redundant_meter_urn, r.equipment_group
            order by r.equipment_group, r.primary_meter_urn, r.redundant_meter_urn
            """,
        )

    reduced["ts"] = pd.to_datetime(reduced["ts"], utc=True)
    groups["ts"] = pd.to_datetime(groups["ts"], utc=True)

    reduced["series"] = reduced["category"] + "_" + reduced["subcategory"] + "_" + reduced["measurement"]
    site = reduced.pivot_table(index="ts", columns="series", values="value", aggfunc="mean").reset_index()
    site = site.rename(
        columns={
            "electricity_total_P": "site_p",
            "electricity_pv_P": "pv_p",
            "electricity_chp_P": "chp_p",
            "weather_weather_Ta": "ta_c",
            "weather_weather_Igm": "igm",
        }
    )
    required_cols = ["site_p", "ta_c", "igm"]
    missing = [c for c in required_cols if c not in site.columns]
    if missing:
        raise RuntimeError(f"Missing required reduced series: {missing}")

    groups_pivot = groups.pivot_table(index="ts", columns="equipment_group", values="group_p", aggfunc="sum")
    frame = site.set_index("ts").join(groups_pivot, how="left")
    group_cols = list(groups_pivot.columns)

    local_ts = frame.index.tz_convert(TZ)
    frame["local_ts"] = local_ts
    frame["local_hour"] = local_ts.hour
    frame["local_month"] = local_ts.month
    frame["local_year"] = local_ts.year
    frame["local_weekday"] = local_ts.dayofweek
    frame["is_weekend"] = frame["local_weekday"] >= 5

    site_nonnull = frame["site_p"].dropna()
    peak_threshold = float(site_nonnull.quantile(0.99))
    frame["is_site_peak"] = frame["site_p"] >= peak_threshold

    local_hour_distribution = (
        frame.loc[frame["is_site_peak"], "local_hour"]
        .value_counts()
        .rename_axis("local_hour")
        .reset_index(name="peak_hours")
        .sort_values(["peak_hours", "local_hour"], ascending=[False, True])
    )
    local_month_distribution = (
        frame.loc[frame["is_site_peak"], "local_month"]
        .value_counts()
        .rename_axis("local_month")
        .reset_index(name="peak_hours")
        .sort_values(["peak_hours", "local_month"], ascending=[False, True])
    )
    local_year_distribution = (
        frame.loc[frame["is_site_peak"], "local_year"]
        .value_counts()
        .rename_axis("local_year")
        .reset_index(name="peak_hours")
        .sort_values("local_year")
    )

    lift_rows = []
    overlap_rows = []
    baseload_rows = []
    for group in group_cols:
        s = frame[group]
        valid = s.notna()
        if valid.sum() == 0:
            continue
        group_p95 = float(s.quantile(0.95))
        peak_s = s[frame["is_site_peak"]]
        nonpeak_s = s[~frame["is_site_peak"]]
        lift_rows.append(
            {
                "group": group,
                "peak_mean_p": float(peak_s.mean()),
                "nonpeak_mean_p": float(nonpeak_s.mean()),
                "lift_ratio": safe_ratio(float(peak_s.mean()), float(nonpeak_s.mean())),
                "corr_with_site_p": float(frame[["site_p", group]].corr().iloc[0, 1]),
                "coverage_pct": pct(valid.mean()),
            }
        )
        overlap_rows.append(
            {
                "group": group,
                "group_p95": group_p95,
                "overlap_hours": int(((s >= group_p95) & frame["is_site_peak"]).sum()),
                "site_peak_hours": int(frame["is_site_peak"].sum()),
                "overlap_pct_of_site_peak": pct(((s >= group_p95) & frame["is_site_peak"]).sum() / frame["is_site_peak"].sum()),
                "baseline_high_pct": pct((s >= group_p95).mean()),
            }
        )
        night = s[frame["local_hour"].between(0, 5)]
        baseload_rows.append(
            {
                "group": group,
                "night_mean_p": float(night.mean()),
                "overall_mean_p": float(s.mean()),
                "night_to_overall_ratio": safe_ratio(float(night.mean()), float(s.mean())),
                "coverage_pct": pct(valid.mean()),
            }
        )

    lift = pd.DataFrame(lift_rows).sort_values("lift_ratio", ascending=False)
    overlap = pd.DataFrame(overlap_rows).sort_values("overlap_pct_of_site_peak", ascending=False)
    baseload = pd.DataFrame(baseload_rows).sort_values("night_to_overall_ratio", ascending=False)
    cr_quality_gate = build_cr_quality_gate(frame, load_balance)
    group_quality_gate = build_group_quality_gate(frame[group_cols])
    redundancy_quality_gate = build_redundancy_quality_gate(pair_stats)

    by_year_rows = []
    for year, yframe in frame.groupby("local_year"):
        peak_count = int(yframe["is_site_peak"].sum())
        if peak_count < 5:
            continue
        for group in group_cols:
            s = yframe[group]
            if s.notna().sum() == 0:
                continue
            group_p95_all = float(frame[group].quantile(0.95))
            peak_mean = float(s[yframe["is_site_peak"]].mean())
            nonpeak_mean = float(s[~yframe["is_site_peak"]].mean())
            by_year_rows.append(
                {
                    "local_year": int(year),
                    "group": group,
                    "site_peak_hours": peak_count,
                    "lift_ratio": safe_ratio(peak_mean, nonpeak_mean),
                    "overlap_pct_of_site_peak": pct(((s >= group_p95_all) & yframe["is_site_peak"]).sum() / peak_count),
                }
            )
    by_year = pd.DataFrame(by_year_rows)
    if not by_year.empty:
        by_year = by_year.sort_values(["local_year", "overlap_pct_of_site_peak"], ascending=[True, False])

    group_sum = frame[group_cols].sum(axis=1, min_count=1)
    relation = pd.DataFrame(
        [
            {
                "metric": "corr_group_sum_site_p",
                "value": float(pd.concat([group_sum, frame["site_p"]], axis=1).corr().iloc[0, 1]),
            },
            {
                "metric": "median_group_sum_to_site_ratio",
                "value": float((group_sum / frame["site_p"].replace(0, np.nan)).median()),
            },
            {
                "metric": "p95_group_sum_to_site_ratio",
                "value": float((group_sum / frame["site_p"].replace(0, np.nan)).quantile(0.95)),
            },
            {
                "metric": "peak_median_group_sum_to_site_ratio",
                "value": float((group_sum[frame["is_site_peak"]] / frame.loc[frame["is_site_peak"], "site_p"].replace(0, np.nan)).median()),
            },
            {
                "metric": "hours_group_sum_exceeds_site_p",
                "value": int((group_sum > frame["site_p"]).sum()),
            },
        ]
    )

    weather = pd.DataFrame(
        [
            {
                "segment": "site_peak",
                "rows": int(frame["is_site_peak"].sum()),
                "site_p_mean": float(frame.loc[frame["is_site_peak"], "site_p"].mean()),
                "ta_median": float(frame.loc[frame["is_site_peak"], "ta_c"].median()),
                "ta_p90": float(frame.loc[frame["is_site_peak"], "ta_c"].quantile(0.90)),
                "igm_median": float(frame.loc[frame["is_site_peak"], "igm"].median()),
                "weekend_pct": pct(frame.loc[frame["is_site_peak"], "is_weekend"].mean()),
            },
            {
                "segment": "non_peak",
                "rows": int((~frame["is_site_peak"]).sum()),
                "site_p_mean": float(frame.loc[~frame["is_site_peak"], "site_p"].mean()),
                "ta_median": float(frame.loc[~frame["is_site_peak"], "ta_c"].median()),
                "ta_p90": float(frame.loc[~frame["is_site_peak"], "ta_c"].quantile(0.90)),
                "igm_median": float(frame.loc[~frame["is_site_peak"], "igm"].median()),
                "weekend_pct": pct(frame.loc[~frame["is_site_peak"], "is_weekend"].mean()),
            },
        ]
    )
    ta_corr = float(frame[["site_p", "ta_c"]].corr().iloc[0, 1])
    igm_corr = float(frame[["site_p", "igm"]].corr().iloc[0, 1])
    weather_corr = pd.DataFrame(
        [
            {"pair": "site_p__ta_c", "pearson_corr": ta_corr},
            {"pair": "site_p__igm", "pearson_corr": igm_corr},
        ]
    )

    # Gate criteria: signal and CR mart quality are enough for fuller analysis when these are true.
    top_month_share = float(local_month_distribution.head(3)["peak_hours"].sum() / frame["is_site_peak"].sum())
    top_hour_share = float(local_hour_distribution.head(6)["peak_hours"].sum() / frame["is_site_peak"].sum())
    repeated_groups_60 = int((overlap["overlap_pct_of_site_peak"] >= 60.0).sum())
    operational_baseload = baseload[~baseload["group"].isin(["grid_transformer", "chp", "pv"])]
    baseload_candidate_count = int((operational_baseload["night_to_overall_ratio"] >= 0.85).sum())
    persistent_groups = 0
    if not by_year.empty:
        yearly_counts = by_year[by_year["overlap_pct_of_site_peak"] >= 40.0].groupby("group")["local_year"].nunique()
        persistent_groups = int((yearly_counts >= 3).sum())

    cr_quality_gate_passed = bool(cr_quality_gate["passed"].all())
    operational_group_quality_gate = group_quality_gate[~group_quality_gate["group"].isin(["grid_transformer", "chp", "pv"])]
    group_quality_gate_passed = bool(operational_group_quality_gate["passed"].all())
    redundancy_pairs_checked = bool(len(redundancy_quality_gate) > 0)
    group_site_accounting_verified = False
    business_data_available = False
    fault_labels_available = False

    criteria = {
        "cr_quality_gate_passed": cr_quality_gate_passed,
        "group_quality_gate_passed": group_quality_gate_passed,
        "site_coverage_full": bool(frame["site_p"].notna().mean() >= 0.99),
        "seasonal_concentration": bool(top_month_share >= 0.50),
        "local_hour_concentration": bool(top_hour_share >= 0.50),
        "repeated_co_high_load_groups": bool(repeated_groups_60 >= 2),
        "multi_year_repeatability": bool(persistent_groups >= 2),
        "weather_context_available": bool(frame["ta_c"].notna().mean() >= 0.95 and frame["igm"].notna().mean() >= 0.95),
        "redundancy_pairs_checked": redundancy_pairs_checked,
    }
    blocking_for_gate = [
        name
        for name in [
            "cr_quality_gate_passed",
            "group_quality_gate_passed",
            "site_coverage_full",
            "seasonal_concentration",
            "local_hour_concentration",
            "repeated_co_high_load_groups",
            "multi_year_repeatability",
            "weather_context_available",
        ]
        if not criteria[name]
    ]
    analysis_ready = len(blocking_for_gate) == 0
    decision_table = build_hypothesis_decision_table(
        top_hour_share=top_hour_share,
        top_month_share=top_month_share,
        repeated_groups_60=repeated_groups_60,
        baseload_candidate_count=baseload_candidate_count,
        group_site_accounting_verified=group_site_accounting_verified,
        business_data_available=business_data_available,
        fault_labels_available=fault_labels_available,
    )

    summary = {
        "dataset": {
            "root": str(ROOT),
            "timezone_for_reporting": TZ,
            "min_ts_utc": str(frame.index.min()),
            "max_ts_utc": str(frame.index.max()),
            "rows": int(len(frame)),
            "site_nonnull_rows": int(frame["site_p"].notna().sum()),
            "site_peak_threshold_p99_w": peak_threshold,
            "site_peak_hours": int(frame["is_site_peak"].sum()),
        },
        "gate": {
            "analysis_ready": analysis_ready,
            "criteria": criteria,
            "blocking_for_gate": blocking_for_gate,
            "advisory_limitations": {
                "strict_group_site_accounting_verified": group_site_accounting_verified,
                "business_data_available": business_data_available,
                "fault_labels_available": fault_labels_available,
                "known_issue_policy": "대표 사례 해석 보조 자료로만 사용",
                "redundancy_pairs_passed": int(redundancy_quality_gate["passed"].sum()),
                "redundancy_pairs_total": int(len(redundancy_quality_gate)),
            },
        },
        "signals": {
            "top_3_month_peak_share_pct": pct(top_month_share),
            "top_6_local_hour_peak_share_pct": pct(top_hour_share),
            "groups_with_overlap_ge_60pct": repeated_groups_60,
            "groups_repeating_ge_40pct_overlap_in_ge_3_years": persistent_groups,
            "site_p_ta_corr": ta_corr,
            "site_p_igm_corr": igm_corr,
        },
        "top_groups": {
            "overlap": overlap.head(8).to_dict(orient="records"),
            "lift": lift.head(8).to_dict(orient="records"),
            "baseload_proxy": baseload.head(8).to_dict(orient="records"),
        },
    }

    local_hour_distribution.to_csv(OUT / "site_peak_local_hour_distribution.csv", index=False)
    local_month_distribution.to_csv(OUT / "site_peak_local_month_distribution.csv", index=False)
    local_year_distribution.to_csv(OUT / "site_peak_local_year_distribution.csv", index=False)
    overlap.to_csv(OUT / "site_peak_group_overlap.csv", index=False)
    lift.to_csv(OUT / "site_peak_group_lift.csv", index=False)
    baseload.to_csv(OUT / "night_baseload_proxy.csv", index=False)
    if not by_year.empty:
        by_year.to_csv(OUT / "site_peak_group_repeatability_by_year.csv", index=False)
    relation.to_csv(OUT / "group_site_relation_diagnostic.csv", index=False)
    weather.to_csv(OUT / "weather_peak_context.csv", index=False)
    weather_corr.to_csv(OUT / "weather_site_correlation.csv", index=False)
    group_meters.to_csv(OUT / "group_meter_inventory.csv", index=False)
    cr_quality_gate.to_csv(OUT / "cr_quality_gate.csv", index=False)
    group_quality_gate.to_csv(OUT / "group_quality_gate.csv", index=False)
    redundancy_quality_gate.to_csv(OUT / "redundancy_quality_gate.csv", index=False)
    decision_table.to_csv(OUT / "hypothesis_decision_table.csv", index=False)
    (OUT / "hypothesis_gate_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    top_overlap_names = ", ".join(overlap.head(4)["group"].tolist())
    top_base_names = ", ".join(operational_baseload.head(4)["group"].tolist())
    markdown = f"""# 데이터 인사이트 가설 진입 게이트 1h

## 판정

- 본분석 진입 판정: `{analysis_ready}`
- 기준 데이터: `ems.reduced_measurement_1h`, `ems.cr_measurement_1h`
- 품질 기준: corrected/resampled mart coverage, NULL, source/load counter, redundancy
- 보고 시간대: `{TZ}`
- 분석 기간: `{summary['dataset']['min_ts_utc']}` ~ `{summary['dataset']['max_ts_utc']}`
- site 피크 기준: 상위 1%, `{peak_threshold:,.1f} W`
- site 피크 시간 수: `{int(frame['is_site_peak'].sum()):,}` 시간

## CR mart 품질 gate

- CR 품질 gate 통과: `{cr_quality_gate_passed}`
- group 품질 gate 통과: `{group_quality_gate_passed}`
- redundancy pair 확인 수: `{len(redundancy_quality_gate)}`
- redundancy pair 통과 수: `{int(redundancy_quality_gate['passed'].sum())}`

## 확인된 신호

- 상위 3개 월 피크 점유율: `{pct(top_month_share):.1f}%`
- 상위 6개 로컬 시간 피크 점유율: `{pct(top_hour_share):.1f}%`
- site 피크 중 자기 기준 고부하가 60% 이상 반복된 계통 수: `{repeated_groups_60}`
- 3개년 이상 반복 신호가 있는 계통 수: `{persistent_groups}`
- 주요 동시상승 계통: `{top_overlap_names}`
- 야간 상시부하 proxy 상위 계통: `{top_base_names}`

## 해석 경계

- group 합산과 site total의 엄격한 회계 관계는 별도 검증 대상으로 남긴다.
- 현재 단계의 안전한 표현은 `피크 동시상승 계통`, `운영 점검 후보`, `상시부하 후보`이다.
- known issue는 대표 사례 해석 보조 자료로 사용한다.

## 다음 단계 후보

1. 피크 구조 본분석: 로컬 시간·월·요일·연도별 분포와 p95/p99 민감도
2. 동시상승 계통 본분석: overlap, lift, 연도별 반복성, 계통 분리
3. 상시부하 후보 본분석: 야간/전체 평균, 주말/평일 차이, 계절별 야간 부하
4. 해석 경계 정리: group-site 관계, redundancy pair, source/load 품질, regime 차이
5. 대표 피크 사례 5~10개 선정 및 그래프 확인
"""
    (OUT / "hypothesis_gate_note.md").write_text(markdown, encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
