#!/usr/bin/env python3
"""Materialize the EMS A-clean 1h forecasting dataset subset.

This script does not query PostgreSQL. It reads the previously materialized
A-family dataset and writes the clean-consumption subset used for the first
forecasting baselines.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "outputs/modeling/a_targets_1h"
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs/modeling/a_clean_targets_1h"

A_CLEAN_TARGET_IDS = [
    "T1_group__central_cooling__P",
    "T1_group__local_cooling__P",
    "T1_group__server_power__P",
    "T1_group__ventilation__P",
]

SIGNED_NET_REVIEW_TARGET_IDS = [
    "T1_group__emission_lab__P",
    "T2_building__H1__P",
    "T2_building__V__P",
]

EXCLUSION_REASONS = {
    "T1_group__emission_lab__P": "signed/net flow review: H1.Z15, H1.Z28, H1.Z29 have material negative P consistent with outflow direction",
    "T2_building__H1__P": "signed/net flow review: includes emission_lab signed/net component meters",
    "T2_building__V__P": "signed/net flow review: V.Z81 and V.Z82 are transformer/grid boundary meters with material negative P",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the EMS A-clean 1h modeling subset from A-family artifacts")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def require_file(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def filter_scaler_manifest(input_manifest: dict[str, Any]) -> dict[str, Any]:
    target_scalers = [
        item for item in input_manifest.get("target_scalers", [])
        if item.get("scaler_key") in A_CLEAN_TARGET_IDS
    ]
    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "derived_from": "outputs/modeling/a_targets_1h/scaler_manifest.json",
        "target_family": "A_CLEAN_CONSUMPTION_1H",
        "calendar_features": input_manifest.get("calendar_features", {}),
        "target_scalers": target_scalers,
        "feature_scalers": input_manifest.get("feature_scalers", []),
    }


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
            p95_target_value=("target_value", lambda s: float(s.quantile(0.95)) if s.notna().any() else pd.NA),
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
            target_p95=("target_value", lambda s: float(s.quantile(0.95)) if s.notna().any() else pd.NA),
            target_max=("target_value", "max"),
        )
        .reset_index()
    )
    feature_summary = {}
    for col in ["Ta", "Igm"]:
        observed_col = f"{col}_observed"
        if observed_col in feature_ts.columns:
            feature_summary[col] = {
                "rows": int(len(feature_ts)),
                "observed_rows": int(feature_ts[observed_col].sum()),
                "missing_rows": int((~feature_ts[observed_col]).sum()),
                "train_observed_non_outage_rows": int(((feature_ts["split"] == "train") & (~feature_ts["is_gateway_outage"]) & feature_ts[observed_col]).sum()),
            }
    return split_summary, quality_summary, feature_summary


def main() -> None:
    args = parse_args()
    input_dir = args.input_dir
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    target_path = require_file(input_dir / "target_timeseries_1h.parquet")
    feature_path = require_file(input_dir / "feature_timeseries_1h.parquet")
    metadata_path = require_file(input_dir / "target_metadata.csv")
    manifest_path = require_file(input_dir / "manifest.json")
    scaler_path = require_file(input_dir / "scaler_manifest.json")

    target_ts = pd.read_parquet(target_path)
    feature_ts = pd.read_parquet(feature_path)
    target_metadata = pd.read_csv(metadata_path)
    source_manifest = load_json(manifest_path)
    scaler_manifest = load_json(scaler_path)

    missing = sorted(set(A_CLEAN_TARGET_IDS) - set(target_ts["target_id"].unique()))
    if missing:
        raise RuntimeError(f"A-clean target ids missing from input dataset: {missing}")

    clean_target_ts = target_ts[target_ts["target_id"].isin(A_CLEAN_TARGET_IDS)].copy()
    clean_target_ts["target_family"] = "A_CLEAN_CONSUMPTION_1H"
    clean_target_ts["sign_risk_class"] = "clean_consumption"

    clean_metadata = target_metadata[target_metadata["target_id"].isin(A_CLEAN_TARGET_IDS)].copy()
    clean_metadata["target_family"] = "A_CLEAN_CONSUMPTION_1H"
    clean_metadata["sign_risk_class"] = "clean_consumption"

    split_summary, quality_summary, feature_summary = build_summaries(clean_target_ts, feature_ts)
    clean_scaler_manifest = filter_scaler_manifest(scaler_manifest)

    output_files = {
        "target_timeseries_1h.parquet": out_dir / "target_timeseries_1h.parquet",
        "feature_timeseries_1h.parquet": out_dir / "feature_timeseries_1h.parquet",
        "target_metadata.csv": out_dir / "target_metadata.csv",
        "target_split_summary.csv": out_dir / "target_split_summary.csv",
        "target_quality_summary.csv": out_dir / "target_quality_summary.csv",
        "scaler_manifest.json": out_dir / "scaler_manifest.json",
    }

    clean_target_ts.to_parquet(output_files["target_timeseries_1h.parquet"], index=False)
    feature_ts.to_parquet(output_files["feature_timeseries_1h.parquet"], index=False)
    clean_metadata.to_csv(output_files["target_metadata.csv"], index=False)
    split_summary.to_csv(output_files["target_split_summary.csv"], index=False)
    quality_summary.to_csv(output_files["target_quality_summary.csv"], index=False)
    output_files["scaler_manifest.json"].write_text(json.dumps(clean_scaler_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_family": "A_CLEAN_CONSUMPTION_1H",
        "purpose": "First forecasting baseline and recipe-selection subset after excluding signed/net A-family targets",
        "derived_from": {
            "input_dir": str(input_dir.relative_to(PROJECT_ROOT)) if input_dir.is_relative_to(PROJECT_ROOT) else str(input_dir),
            "source_manifest": "outputs/modeling/a_targets_1h/manifest.json",
            "source_manifest_sha256": sha256_file(manifest_path),
        },
        "source_relations": source_manifest.get("source_relations", []),
        "time_window": source_manifest.get("time_window", {}),
        "included_target_ids": A_CLEAN_TARGET_IDS,
        "excluded_signed_net_review_target_ids": SIGNED_NET_REVIEW_TARGET_IDS,
        "exclusion_reasons": EXCLUSION_REASONS,
        "target_count": int(clean_target_ts["target_id"].nunique()),
        "target_version_count": int(clean_target_ts["target_version_id"].nunique()),
        "target_rows": int(len(clean_target_ts)),
        "feature_rows": int(len(feature_ts)),
        "split_counts": {str(k): int(v) for k, v in clean_target_ts["split"].value_counts().sort_index().items()},
        "feature_split_counts": {str(k): int(v) for k, v in feature_ts["split"].value_counts().sort_index().items()},
        "quality_checks": {
            "negative_target_rows": int(clean_target_ts["is_negative_target_value"].sum()),
            "partial_component_rows": int((~clean_target_ts["is_full_component_observed"]).sum()),
            "unobserved_target_rows": int((~clean_target_ts["target_observed"]).sum()),
        },
        "feature_summary": feature_summary,
        "metric_guidance": {
            "primary_metrics": ["MAE", "RMSE"],
            "secondary_metrics": ["MAPE"],
            "note": "A-clean targets are nonnegative in the generated dataset, so MAPE can be reported with standard zero/near-zero guardrails.",
        },
        "outputs": {
            name: {
                "path": str(path.relative_to(PROJECT_ROOT)) if path.is_relative_to(PROJECT_ROOT) else str(path),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for name, path in output_files.items()
        },
    }
    manifest_file = out_dir / "manifest.json"
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "status": "ok",
        "out_dir": str(out_dir),
        "target_count": manifest["target_count"],
        "target_version_count": manifest["target_version_count"],
        "target_rows": manifest["target_rows"],
        "feature_rows": manifest["feature_rows"],
        "quality_checks": manifest["quality_checks"],
        "split_counts": manifest["split_counts"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
