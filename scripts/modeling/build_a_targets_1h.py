#!/usr/bin/env python3
"""Build EMS A-family 1h forecasting target datasets.

This script is read-only against PostgreSQL. It materializes compact Parquet
caches for A_STRICT_BENCHMARK / A_VERSIONED_STRICT_BENCHMARK targets so model
training can read local artifacts instead of repeatedly querying the DB.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import psycopg

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_GROUPS = PROJECT_ROOT / "outputs/tables/modeling/target_policy_comparison_1h/policy_groups_1h.csv"
DEFAULT_TARGET_VERSIONS = PROJECT_ROOT / "outputs/tables/modeling/experiment_target_schema_1h/target_versions_1h.csv"
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs/modeling/a_targets_1h"
DEFAULT_ENV = PROJECT_ROOT / ".env"

START_TS = pd.Timestamp("2018-01-01 00:00:00+00:00")
END_TS = pd.Timestamp("2023-12-31 23:00:00+00:00")  # exclusive; DB observed data ends at 2023-12-31 22:00 UTC

GATEWAY_OUTAGES = [
    ("Workshop gateway failure #1", "2020-02-13 00:00:00+00", "2020-03-06 00:00:00+00"),
    ("Emission lab gateway failure", "2020-08-20 00:00:00+00", "2020-09-17 00:00:00+00"),
    ("Distribution gateway failure", "2021-11-15 00:00:00+00", "2021-12-10 00:00:00+00"),
    ("Workshop gateway failure #2", "2022-05-06 00:00:00+00", "2022-07-14 00:00:00+00"),
]

A_POLICIES = {"A_STRICT_BENCHMARK", "A_VERSIONED_STRICT_BENCHMARK"}
WEATHER_FEATURES = ["Ta", "Igm"]
CALENDAR_FEATURES = ["hour_sin", "hour_cos", "dow_sin", "dow_cos", "month_sin", "month_cos"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build A-family EMS 1h modeling dataset")
    parser.add_argument("--policy-groups", type=Path, default=DEFAULT_POLICY_GROUPS)
    parser.add_argument("--target-versions", type=Path, default=DEFAULT_TARGET_VERSIONS)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--env-path", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--start-ts", default=START_TS.isoformat())
    parser.add_argument("--end-ts", default=END_TS.isoformat())
    parser.add_argument("--dry-run", action="store_true", help="Validate inputs and DB connection only")
    return parser.parse_args()


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def require_env() -> dict[str, str]:
    keys = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"]
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"Missing DB environment keys: {', '.join(missing)}")
    return {k: os.environ[k] for k in keys}


def connect() -> psycopg.Connection:
    env = require_env()
    conn = psycopg.connect(
        host=env["DB_HOST"],
        port=env["DB_PORT"],
        dbname=env["DB_NAME"],
        user=env["DB_USER"],
        password=env["DB_PASSWORD"],
        connect_timeout=10,
    )
    conn.execute("SET statement_timeout = '120s'")
    conn.execute("SET lock_timeout = '5s'")
    conn.execute("SET TIME ZONE 'UTC'")
    return conn


def split_semicolon(value: Any) -> list[str]:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return []
    text = str(value).strip()
    if not text:
        return []
    return [part.strip() for part in text.split(";") if part.strip()]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def component_set_id(meters: list[str]) -> str:
    normalized = ";".join(sorted(meters))
    return "cs_" + hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]


def add_split_and_outage(ts: pd.Series) -> pd.DataFrame:
    out = pd.DataFrame(index=ts.index)
    out["split"] = np.select(
        [
            (ts >= pd.Timestamp("2018-01-01", tz="UTC")) & (ts < pd.Timestamp("2022-01-01", tz="UTC")),
            (ts >= pd.Timestamp("2022-01-01", tz="UTC")) & (ts < pd.Timestamp("2023-01-01", tz="UTC")),
            (ts >= pd.Timestamp("2023-01-01", tz="UTC")) & (ts < pd.Timestamp("2024-01-01", tz="UTC")),
        ],
        ["train", "validation", "test"],
        default="exclude",
    )
    outage = pd.Series(False, index=ts.index)
    outage_name = pd.Series("", index=ts.index, dtype="object")
    for name, start_s, end_s in GATEWAY_OUTAGES:
        mask = (ts >= pd.Timestamp(start_s)) & (ts < pd.Timestamp(end_s))
        outage |= mask
        outage_name.loc[mask] = name
    out["is_gateway_outage"] = outage.to_numpy(dtype=bool)
    out["gateway_outage_name"] = outage_name
    return out


def add_calendar_features(df: pd.DataFrame) -> pd.DataFrame:
    ts = pd.to_datetime(df["ts"], utc=True)
    hour = ts.dt.hour
    dow = ts.dt.dayofweek
    month0 = ts.dt.month - 1
    df["hour_sin"] = np.sin(2 * np.pi * hour / 24).astype("float32")
    df["hour_cos"] = np.cos(2 * np.pi * hour / 24).astype("float32")
    df["dow_sin"] = np.sin(2 * np.pi * dow / 7).astype("float32")
    df["dow_cos"] = np.cos(2 * np.pi * dow / 7).astype("float32")
    df["month_sin"] = np.sin(2 * np.pi * month0 / 12).astype("float32")
    df["month_cos"] = np.cos(2 * np.pi * month0 / 12).astype("float32")
    return df


def load_a_target_versions(policy_groups_path: Path, target_versions_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    policy = pd.read_csv(policy_groups_path)
    a_policy = policy[policy["final_policy"].isin(A_POLICIES)].copy()
    if a_policy.empty:
        raise RuntimeError("No A-family targets found in policy groups")

    versions = pd.read_csv(target_versions_path)
    versions = versions[versions["target_id"].isin(a_policy["target_id"])].copy()
    versions = versions[versions["final_policy"].isin(A_POLICIES)].copy()
    versions["version_start"] = pd.to_datetime(versions["version_start"], utc=True)
    versions["version_end_exclusive"] = pd.to_datetime(versions["version_end_exclusive"], utc=True)
    versions["expected_component_meters_list"] = versions["expected_component_meters"].map(split_semicolon)
    versions["expected_component_count_calc"] = versions["expected_component_meters_list"].map(len)
    bad = versions[versions["expected_component_count"].astype(int) != versions["expected_component_count_calc"]]
    if not bad.empty:
        raise RuntimeError(f"Expected component count mismatch for versions: {bad['target_version_id'].tolist()}")
    return a_policy, versions


def query_component_rows(conn: psycopg.Connection, meters: list[str], start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> pd.DataFrame:
    sql = """
        SELECT ts, meter_urn, value
        FROM ems.cr_measurement_1h
        WHERE measurement = 'P'
          AND ts >= %(start_ts)s
          AND ts < %(end_ts)s
          AND meter_urn = ANY(%(meters)s)
        ORDER BY ts, meter_urn
    """
    with conn.cursor() as cur:
        cur.execute(sql, {"start_ts": start_ts.to_pydatetime(), "end_ts": end_ts.to_pydatetime(), "meters": meters})
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=["ts", "meter_urn", "value"])


def query_weather_rows(conn: psycopg.Connection, start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> pd.DataFrame:
    sql = """
        SELECT ts, measurement, value
        FROM ems.reduced_measurement_1h
        WHERE category = 'weather'
          AND subcategory = 'weather'
          AND measurement = ANY(%(measurements)s)
          AND ts >= %(start_ts)s
          AND ts < %(end_ts)s
        ORDER BY ts, measurement
    """
    with conn.cursor() as cur:
        cur.execute(sql, {"start_ts": start_ts.to_pydatetime(), "end_ts": end_ts.to_pydatetime(), "measurements": WEATHER_FEATURES})
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=["ts", "measurement", "value"])


def build_feature_timeseries(weather_rows: pd.DataFrame, full_index: pd.DatetimeIndex) -> pd.DataFrame:
    df = pd.DataFrame({"ts": full_index})
    if weather_rows.empty:
        for col in WEATHER_FEATURES:
            df[col] = np.nan
            df[f"{col}_observed"] = False
    else:
        weather_rows = weather_rows.copy()
        weather_rows["ts"] = pd.to_datetime(weather_rows["ts"], utc=True)
        wide = weather_rows.pivot(index="ts", columns="measurement", values="value").reindex(full_index)
        for col in WEATHER_FEATURES:
            df[col] = wide[col].to_numpy(dtype="float64") if col in wide else np.nan
            df[f"{col}_observed"] = ~pd.isna(df[col])
    df = add_calendar_features(df)
    aux = add_split_and_outage(pd.to_datetime(df["ts"], utc=True))
    df = pd.concat([df, aux], axis=1)
    return df


def build_target_timeseries(component_rows: pd.DataFrame, versions: pd.DataFrame, full_index: pd.DatetimeIndex) -> pd.DataFrame:
    component_rows = component_rows.copy()
    if component_rows.empty:
        wide = pd.DataFrame(index=full_index)
    else:
        component_rows["ts"] = pd.to_datetime(component_rows["ts"], utc=True)
        wide = component_rows.pivot_table(index="ts", columns="meter_urn", values="value", aggfunc="first").reindex(full_index)

    parts: list[pd.DataFrame] = []
    for _, version in versions.sort_values(["target_id", "version_start"]).iterrows():
        meters = version["expected_component_meters_list"]
        mask_index = full_index[(full_index >= version["version_start"]) & (full_index < version["version_end_exclusive"])]
        if len(mask_index) == 0:
            continue
        sub = wide.reindex(mask_index)
        for meter in meters:
            if meter not in sub.columns:
                sub[meter] = np.nan
        sub = sub[meters]
        observed_count = sub.notna().sum(axis=1).astype("int16")
        target_value = sub.sum(axis=1, skipna=True, min_count=1)
        expected_count = int(version["expected_component_count"])
        out = pd.DataFrame(
            {
                "ts": mask_index,
                "target_id": version["target_id"],
                "target_name": version["target_name"],
                "target_value": target_value.to_numpy(dtype="float64"),
                "is_negative_target_value": target_value.to_numpy(dtype="float64") < 0,
                "target_observed": observed_count.to_numpy() > 0,
                "target_version_id": version["target_version_id"],
                "experiment_group": version["experiment_group"],
                "final_policy": version["final_policy"],
                "component_set_id": component_set_id(meters),
                "expected_component_count": expected_count,
                "observed_component_count": observed_count.to_numpy(),
                "is_full_component_observed": (observed_count.to_numpy() == expected_count),
                "target_usable_for_benchmark": str(version["target_usable_for_benchmark"]).lower() == "true",
                "is_replacement_gap": str(version["version_role"]) == "replacement_gap_exclude_or_flag",
                "expected_component_meters": ";".join(meters),
            }
        )
        parts.append(out)
    if not parts:
        raise RuntimeError("No target time-series rows generated")
    result = pd.concat(parts, ignore_index=True)
    aux = add_split_and_outage(pd.to_datetime(result["ts"], utc=True))
    result = pd.concat([result, aux], axis=1)
    return result


def build_target_metadata(versions: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for target_id, grp in versions.groupby("target_id", sort=True):
        rows.append(
            {
                "target_id": target_id,
                "target_name": grp["target_name"].iloc[0],
                "experiment_group": grp["experiment_group"].iloc[0],
                "final_policy": grp["final_policy"].iloc[0],
                "version_count": int(grp["target_version_id"].nunique()),
                "target_version_ids": ";".join(grp["target_version_id"].tolist()),
                "all_expected_component_meters": ";".join(sorted({m for meters in grp["expected_component_meters_list"] for m in meters})),
            }
        )
    return pd.DataFrame(rows)


def build_summaries(target_ts: pd.DataFrame, feature_ts: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    split_summary = (
        target_ts.groupby(["target_id", "target_version_id", "split"], dropna=False)
        .agg(
            rows=("target_value", "size"),
            observed_rows=("target_observed", "sum"),
            full_component_rows=("is_full_component_observed", "sum"),
            gateway_outage_rows=("is_gateway_outage", "sum"),
            min_observed_component_count=("observed_component_count", "min"),
            max_observed_component_count=("observed_component_count", "max"),
            negative_target_rows=("is_negative_target_value", "sum"),
            mean_target_value=("target_value", "mean"),
            p95_target_value=("target_value", lambda s: float(s.quantile(0.95)) if s.notna().any() else np.nan),
            max_target_value=("target_value", "max"),
        )
        .reset_index()
    )
    quality_summary = (
        target_ts.groupby(["target_id"], dropna=False)
        .agg(
            rows=("target_value", "size"),
            observed_rows=("target_observed", "sum"),
            full_component_rows=("is_full_component_observed", "sum"),
            partial_component_rows=("is_full_component_observed", lambda s: int((~s).sum())),
            gateway_outage_rows=("is_gateway_outage", "sum"),
            negative_target_rows=("is_negative_target_value", "sum"),
            target_min=("target_value", "min"),
            target_mean=("target_value", "mean"),
            target_p95=("target_value", lambda s: float(s.quantile(0.95)) if s.notna().any() else np.nan),
            target_max=("target_value", "max"),
        )
        .reset_index()
    )
    feature_summary = {
        col: {
            "rows": int(len(feature_ts)),
            "observed_rows": int(feature_ts[f"{col}_observed"].sum()),
            "missing_rows": int((~feature_ts[f"{col}_observed"]).sum()),
            "train_observed_non_outage_rows": int(((feature_ts["split"] == "train") & (~feature_ts["is_gateway_outage"]) & (feature_ts[f"{col}_observed"])).sum()),
        }
        for col in WEATHER_FEATURES
    }
    return split_summary, quality_summary, feature_summary


def build_scaler_manifest(target_ts: pd.DataFrame, feature_ts: pd.DataFrame) -> dict[str, Any]:
    target_scalers = []
    for target_id, grp in target_ts.groupby("target_id", sort=True):
        fit = grp[
            (grp["split"] == "train")
            & (~grp["is_gateway_outage"])
            & (grp["target_observed"])
            & (grp["is_full_component_observed"])
            & (~grp["is_replacement_gap"])
        ]
        vals = fit["target_value"].dropna()
        target_scalers.append(
            {
                "scaler_key": target_id,
                "scope": "target_id",
                "method": "MinMaxScaler_equivalent",
                "feature_range": [0, 1],
                "fit_split": "train",
                "fit_policy": "train & non_gateway_outage & target_observed & full_component_observed & not_replacement_gap",
                "fit_row_count": int(len(vals)),
                "fit_start_ts": str(fit["ts"].min()) if len(fit) else None,
                "fit_end_ts": str(fit["ts"].max()) if len(fit) else None,
                "data_min": float(vals.min()) if len(vals) else None,
                "data_max": float(vals.max()) if len(vals) else None,
            }
        )
    feature_scalers = []
    for col in WEATHER_FEATURES:
        fit = feature_ts[(feature_ts["split"] == "train") & (~feature_ts["is_gateway_outage"]) & (feature_ts[f"{col}_observed"])]
        vals = fit[col].dropna()
        feature_scalers.append(
            {
                "scaler_key": col,
                "scope": "weather_feature",
                "method": "MinMaxScaler_equivalent",
                "feature_range": [0, 1],
                "fit_split": "train",
                "fit_policy": "train & non_gateway_outage & observed",
                "fit_row_count": int(len(vals)),
                "fit_start_ts": str(fit["ts"].min()) if len(fit) else None,
                "fit_end_ts": str(fit["ts"].max()) if len(fit) else None,
                "data_min": float(vals.min()) if len(vals) else None,
                "data_max": float(vals.max()) if len(vals) else None,
            }
        )
    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "calendar_features": {col: "no_scaler_cyclic_-1_to_1" for col in CALENDAR_FEATURES},
        "target_scalers": target_scalers,
        "feature_scalers": feature_scalers,
    }


def write_outputs(out_dir: Path, target_ts: pd.DataFrame, feature_ts: pd.DataFrame, target_metadata: pd.DataFrame, split_summary: pd.DataFrame, quality_summary: pd.DataFrame, feature_summary: dict[str, Any], scaler_manifest: dict[str, Any], versions: pd.DataFrame, component_rows: pd.DataFrame) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    target_path = out_dir / "target_timeseries_1h.parquet"
    feature_path = out_dir / "feature_timeseries_1h.parquet"
    metadata_path = out_dir / "target_metadata.csv"
    split_path = out_dir / "target_split_summary.csv"
    quality_path = out_dir / "target_quality_summary.csv"
    scaler_path = out_dir / "scaler_manifest.json"
    manifest_path = out_dir / "manifest.json"

    target_ts.to_parquet(target_path, index=False)
    feature_ts.to_parquet(feature_path, index=False)
    target_metadata.to_csv(metadata_path, index=False)
    split_summary.to_csv(split_path, index=False)
    quality_summary.to_csv(quality_path, index=False)
    scaler_path.write_text(json.dumps(scaler_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    component_meters = sorted({m for meters in versions["expected_component_meters_list"] for m in meters})
    files = [target_path, feature_path, metadata_path, split_path, quality_path, scaler_path]
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_relations": ["ems.cr_measurement_1h", "ems.reduced_measurement_1h"],
        "target_policy_filter": sorted(A_POLICIES),
        "time_window": {"start_ts": START_TS.isoformat(), "end_ts_exclusive": END_TS.isoformat()},
        "target_count": int(target_ts["target_id"].nunique()),
        "target_version_count": int(target_ts["target_version_id"].nunique()),
        "target_ids": sorted(target_ts["target_id"].unique().tolist()),
        "component_meter_count": len(component_meters),
        "component_meters": component_meters,
        "component_source_rows": int(len(component_rows)),
        "target_rows": int(len(target_ts)),
        "feature_rows": int(len(feature_ts)),
        "feature_columns": WEATHER_FEATURES + CALENDAR_FEATURES,
        "feature_summary": feature_summary,
        "split_counts": target_ts.groupby("split").size().astype(int).to_dict(),
        "target_value_rule": "sum expected component meter P values per target_version_id; partial rows retain observed sum with is_full_component_observed flag",
        "scaler_policy": "target_id별 train non-outage full-observed rows; weather feature별 train non-outage observed rows; calendar cyclic features unscaled",
        "missing_policy": "target rows keep observed/full-component flags; weather values keep observed flags and are not filled in this dataset artifact",
        "negative_value_policy": "signed component sums are preserved; negative target rows are flagged with is_negative_target_value and summarized, not clipped",
        "outage_policy": "gateway outage flags included; train outage rows excluded later during scaler fit/training sequence creation",
        "outputs": {p.name: {"path": str(p.relative_to(PROJECT_ROOT)), "bytes": p.stat().st_size, "sha256": sha256_file(p)} for p in files},
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    args = parse_args()
    global START_TS, END_TS
    START_TS = pd.Timestamp(args.start_ts).tz_convert("UTC") if pd.Timestamp(args.start_ts).tzinfo else pd.Timestamp(args.start_ts, tz="UTC")
    END_TS = pd.Timestamp(args.end_ts).tz_convert("UTC") if pd.Timestamp(args.end_ts).tzinfo else pd.Timestamp(args.end_ts, tz="UTC")

    load_env(args.env_path)
    a_policy, versions = load_a_target_versions(args.policy_groups, args.target_versions)
    component_meters = sorted({m for meters in versions["expected_component_meters_list"] for m in meters})

    with connect() as conn:
        db_now = conn.execute("SELECT now() AT TIME ZONE 'UTC'").fetchone()[0]
        if args.dry_run:
            print(json.dumps({"status": "dry_run_ok", "db_snapshot_utc": str(db_now), "a_targets": int(len(a_policy)), "a_versions": int(len(versions)), "component_meter_count": len(component_meters)}, ensure_ascii=False, indent=2))
            return
        component_rows = query_component_rows(conn, component_meters, START_TS, END_TS)
        weather_rows = query_weather_rows(conn, START_TS, END_TS)

    full_index = pd.date_range(START_TS, END_TS, freq="1h", inclusive="left")
    feature_ts = build_feature_timeseries(weather_rows, full_index)
    target_ts = build_target_timeseries(component_rows, versions, full_index)
    target_metadata = build_target_metadata(versions)
    split_summary, quality_summary, feature_summary = build_summaries(target_ts, feature_ts)
    scaler_manifest = build_scaler_manifest(target_ts, feature_ts)
    manifest = write_outputs(args.out_dir, target_ts, feature_ts, target_metadata, split_summary, quality_summary, feature_summary, scaler_manifest, versions, component_rows)

    print(json.dumps({
        "status": "ok",
        "out_dir": str(args.out_dir),
        "target_count": manifest["target_count"],
        "target_version_count": manifest["target_version_count"],
        "component_meter_count": manifest["component_meter_count"],
        "component_source_rows": manifest["component_source_rows"],
        "target_rows": manifest["target_rows"],
        "feature_rows": manifest["feature_rows"],
        "split_counts": manifest["split_counts"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
