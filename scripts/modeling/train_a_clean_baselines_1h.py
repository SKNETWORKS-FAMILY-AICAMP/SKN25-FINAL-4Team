#!/usr/bin/env python3
"""Naive/seasonal baselines for EMS A-clean 1h targets.

Inputs are the materialized Parquet files under outputs/modeling/a_clean_targets_1h.
This script does not query the database.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

TARGET_FILE = "target_timeseries_1h.parquet"
MANIFEST_FILE = "manifest.json"

BASELINE_LAGS = {
    "last_value": 1,
    "seasonal_24h": 24,
    "seasonal_168h": 168,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_rmse(err: pd.Series) -> float:
    return float(np.sqrt(np.mean(np.square(err.to_numpy(dtype=float)))))


def compute_metrics(df: pd.DataFrame, prediction_col: str, scope_name: str) -> dict[str, object]:
    valid = df["target_value"].notna() & df[prediction_col].notna()
    actual = df.loc[valid, "target_value"].astype(float)
    pred = df.loc[valid, prediction_col].astype(float)
    err = pred - actual
    abs_err = err.abs()
    near_zero_threshold = 1e-6
    mape_mask = actual.abs() > near_zero_threshold
    if len(actual) == 0:
        return {
            "scope": scope_name,
            "rows": 0,
            "mae": None,
            "rmse": None,
            "mape": None,
            "mape_rows": 0,
            "near_zero_rows": 0,
            "bias": None,
        }
    return {
        "scope": scope_name,
        "rows": int(len(actual)),
        "mae": float(abs_err.mean()),
        "rmse": safe_rmse(err),
        "mape": float((abs_err[mape_mask] / actual[mape_mask].abs()).mean() * 100.0) if mape_mask.any() else None,
        "mape_rows": int(mape_mask.sum()),
        "near_zero_rows": int((~mape_mask).sum()),
        "bias": float(err.mean()),
    }


def metric_scopes(df: pd.DataFrame) -> Iterable[tuple[str, pd.DataFrame]]:
    yield "all", df
    if "is_gateway_outage" in df.columns:
        yield "non_gateway", df.loc[~df["is_gateway_outage"].fillna(False)]


def run(dataset_dir: Path, out_dir: Path, target_ids: list[str] | None) -> None:
    dataset_dir = dataset_dir.resolve()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    target_path = dataset_dir / TARGET_FILE
    if not target_path.exists():
        raise FileNotFoundError(target_path)

    target = pd.read_parquet(target_path).sort_values(["target_id", "ts"]).reset_index(drop=True)
    if target_ids:
        target = target[target["target_id"].isin(target_ids)].copy()
    if target.empty:
        raise RuntimeError("No target rows selected")

    selected_targets = sorted(target["target_id"].unique().tolist())
    metrics_rows: list[dict[str, object]] = []
    summary: dict[str, object] = {
        "created_at_utc": utc_now(),
        "dataset_dir": str(dataset_dir),
        "out_dir": str(out_dir),
        "selected_targets": selected_targets,
        "baselines": BASELINE_LAGS,
        "metric_scopes": ["all", "non_gateway"],
        "mape_policy": "exclude rows where abs(actual) <= 1e-6; report near_zero_rows",
        "outputs": {},
    }

    for target_id, g in target.groupby("target_id", sort=True):
        g = g.sort_values("ts").copy()
        for name, lag in BASELINE_LAGS.items():
            g[f"pred_{name}"] = g["target_value"].shift(lag)

        pred_cols = ["ts", "target_id", "target_version_id", "split", "target_value", "is_gateway_outage"]
        pred_cols += [f"pred_{name}" for name in BASELINE_LAGS]
        pred = g.loc[g["split"].isin(["validation", "test"]), pred_cols].copy()

        target_out = out_dir / target_id
        target_out.mkdir(parents=True, exist_ok=True)
        pred_path = target_out / "baseline_predictions.parquet"
        pred.to_parquet(pred_path, index=False)
        summary["outputs"][target_id] = str(pred_path.relative_to(out_dir))

        for split in ["validation", "test"]:
            split_df = pred[pred["split"] == split].copy()
            for baseline_name in BASELINE_LAGS:
                prediction_col = f"pred_{baseline_name}"
                for scope, scoped_df in metric_scopes(split_df):
                    row = compute_metrics(scoped_df, prediction_col, scope)
                    row.update({
                        "target_id": target_id,
                        "split": split,
                        "baseline": baseline_name,
                    })
                    metrics_rows.append(row)

    metrics = pd.DataFrame(metrics_rows)
    metrics_path = out_dir / "baseline_metrics.csv"
    metrics.to_csv(metrics_path, index=False)
    summary["metrics_path"] = str(metrics_path.relative_to(out_dir))

    manifest_path = dataset_dir / MANIFEST_FILE
    if manifest_path.exists():
        with manifest_path.open("r", encoding="utf-8") as f:
            manifest = json.load(f)
        summary["input_manifest_sha256"] = manifest.get("output_files", {}).get(TARGET_FILE, {}).get("sha256")
        summary["input_target_family"] = manifest.get("target_family")

    with (out_dir / "run_manifest.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # Compact best-per-target table for quick inspection.
    best = (
        metrics[(metrics["scope"] == "non_gateway") & (metrics["split"] == "test")]
        .sort_values(["target_id", "mae"])
        .groupby("target_id", as_index=False)
        .first()
    )
    best.to_csv(out_dir / "baseline_best_test_non_gateway.csv", index=False)

    print(json.dumps({
        "status": "ok",
        "out_dir": str(out_dir),
        "target_count": len(selected_targets),
        "metrics_rows": len(metrics),
        "best_test_non_gateway": best[["target_id", "baseline", "mae", "rmse", "mape"]].to_dict(orient="records"),
    }, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=Path("outputs/modeling/a_clean_targets_1h"))
    parser.add_argument("--out-dir", type=Path, default=Path("outputs/modeling/a_clean_baselines_1h"))
    parser.add_argument("--target-id", action="append", default=None, help="Optional target_id filter; repeatable")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run(args.dataset_dir, args.out_dir, args.target_id)


if __name__ == "__main__":
    main()
