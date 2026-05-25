#!/usr/bin/env python3
"""Strict integrity verification for A-clean multi-horizon experiment outputs.

Checks are intentionally deterministic and fail-fast in the final status:
- source target/feature key uniqueness and hourly grid
- target/origin split boundaries
- all lag columns 1..lookback against the source target series
- target value lookup at target_ts
- split/gateway assignment by target_ts for usable rows
- saved best predictions match freshly rebuilt frames
- saved best model metrics match recomputation from best_predictions.parquet
- manifest and candidate_metrics agree on the selected best candidate

The script does not retrain models. It verifies data construction and saved output integrity.
"""
from __future__ import annotations

import importlib.util
import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_SCRIPT = ROOT / "scripts/modeling/train_a_clean_huang2022_multi_horizon.py"
DEFAULT_DATASET_DIR = ROOT / "outputs/modeling/a_clean_targets_1h"
DEFAULT_OUTPUT_DIR = ROOT / "outputs/modeling/a_clean_huang2022_multi_horizon"
LOOKBACK = 24
HORIZONS = [1, 24, 168]
TARGETS = [
    "T1_group__central_cooling__P",
    "T1_group__local_cooling__P",
    "T1_group__server_power__P",
    "T1_group__ventilation__P",
]
LABELS = {
    "T1_group__central_cooling__P": "중앙 냉방",
    "T1_group__local_cooling__P": "국소 냉방",
    "T1_group__server_power__P": "서버 전원",
    "T1_group__ventilation__P": "환기 계통",
}
METRIC_TOL = 1e-6
VALUE_TOL = 1e-8


def load_module(model_script: Path) -> Any:
    spec = importlib.util.spec_from_file_location("mh_model", model_script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load model script: {model_script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model-script", type=Path, default=DEFAULT_MODEL_SCRIPT)
    p.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--lookback", type=int, default=LOOKBACK)
    p.add_argument("--horizon-hours", type=int, action="append", default=None)
    p.add_argument("--target-id", action="append", default=None)
    return p.parse_args()


def finite_metric(actual: pd.Series, pred: pd.Series) -> dict[str, float | int | None]:
    y = actual.to_numpy(dtype=float)
    yhat = pred.to_numpy(dtype=float)
    mask = np.isfinite(y) & np.isfinite(yhat)
    y = y[mask]
    yhat = yhat[mask]
    if len(y) == 0:
        return {"rows": 0, "mae": None, "rmse": None, "r2": None}
    return {
        "rows": int(len(y)),
        "mae": float(mean_absolute_error(y, yhat)),
        "rmse": float(math.sqrt(mean_squared_error(y, yhat))),
        "r2": float(r2_score(y, yhat)) if len(y) >= 2 else None,
    }


def metric_close(a: Any, b: Any, tol: float = METRIC_TOL) -> bool:
    if a is None and b is None:
        return True
    if pd.isna(a) and pd.isna(b):
        return True
    return abs(float(a) - float(b)) <= tol


def add_error(errors: list[dict[str, Any]], check: str, detail: dict[str, Any]) -> None:
    errors.append({"check": check, **detail})


def main() -> None:
    args = parse_args()
    model_script = args.model_script.resolve()
    dataset_dir = args.dataset_dir.resolve()
    output_dir = args.output_dir.resolve()
    verify_dir = output_dir / "audit"
    horizons = args.horizon_hours or HORIZONS
    targets = args.target_id or TARGETS
    lookback = args.lookback
    mod = load_module(model_script)
    verify_dir.mkdir(parents=True, exist_ok=True)
    target_path = dataset_dir / mod.TARGET_FILE
    feature_path = dataset_dir / mod.FEATURE_FILE
    target = pd.read_parquet(target_path)
    feature = pd.read_parquet(feature_path)
    target["ts"] = pd.to_datetime(target["ts"])
    feature["ts"] = pd.to_datetime(feature["ts"])

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []

    # Source checks.
    if int(target.duplicated(["target_id", "ts"]).sum()) != 0:
        add_error(errors, "source_target_unique_key", {"duplicates": int(target.duplicated(["target_id", "ts"]).sum())})
    if int(feature.duplicated(["ts"]).sum()) != 0:
        add_error(errors, "source_feature_unique_ts", {"duplicates": int(feature.duplicated(["ts"]).sum())})
    fdiff = feature.sort_values("ts")["ts"].diff().dropna()
    if int(fdiff.ne(pd.Timedelta(hours=1)).sum()) != 0:
        add_error(errors, "source_feature_hourly_grid", {"non_1h_steps": int(fdiff.ne(pd.Timedelta(hours=1)).sum())})
    for tid, g in target.groupby("target_id"):
        diff = g.sort_values("ts")["ts"].diff().dropna()
        non1h = int(diff.ne(pd.Timedelta(hours=1)).sum())
        if non1h != 0:
            add_error(errors, "source_target_hourly_grid", {"target_id": tid, "non_1h_steps": non1h})

    feature_by_ts = feature.set_index("ts")
    target_lookup = {tid: g.set_index("ts")["target_value"].sort_index() for tid, g in target.groupby("target_id")}

    # Output inventory checks.
    expected_dirs = {(h, tid) for h in horizons for tid in targets}
    found_dirs = set()
    for h in horizons:
        for tid in targets:
            td = output_dir / f"h{h:03d}" / tid
            found_dirs.add((h, tid))
            for name in ["candidate_metrics.csv", "best_predictions.parquet", "manifest.json"]:
                if not (td / name).exists():
                    add_error(errors, "missing_output_file", {"horizon_hours": h, "target_id": tid, "file": str(td / name)})
    missing = expected_dirs - found_dirs
    if missing:
        add_error(errors, "missing_target_horizon_dir", {"missing": sorted(map(str, missing))})

    # Frame and saved output checks.
    for h in horizons:
        for tid in targets:
            label = LABELS.get(tid, tid)
            df = mod.make_frame(target, feature, tid, lookback, h)
            usable = mod.usable_mask(df)
            y_series = target_lookup[tid]
            record: dict[str, Any] = {"horizon_hours": h, "target_id": tid, "label": label}

            # Core alignment.
            offset_fail = int((df["target_ts"] - df["origin_ts"]).ne(pd.Timedelta(hours=h)).sum())
            if offset_fail:
                add_error(errors, "target_ts_offset", {**record, "failures": offset_fail})
            joined_target = df["target_ts"].map(y_series)
            mismatch_target = int((~np.isclose(df["target_value"].to_numpy(float), joined_target.to_numpy(float), equal_nan=True)).sum())
            if mismatch_target:
                add_error(errors, "target_value_lookup", {**record, "failures": mismatch_target})
            joined_split = df["target_ts"].map(feature_by_ts["split"])
            split_bad = df["split"].ne(joined_split) & usable
            if int(split_bad.sum()):
                add_error(errors, "split_by_target_ts", {**record, "failures": int(split_bad.sum())})
            joined_gateway = df["target_ts"].map(feature_by_ts["is_gateway_outage"])
            gateway_bad = df["is_gateway_outage"].ne(joined_gateway) & usable
            if int(gateway_bad.sum()):
                add_error(errors, "gateway_by_target_ts", {**record, "failures": int(gateway_bad.sum())})

            # Full lag verification, not just spot checks.
            lag_mismatch_total = 0
            lag_checked_total = 0
            for lag in range(1, lookback + 1):
                expected = (df["origin_ts"] - pd.Timedelta(hours=lag - 1)).map(y_series)
                exists = expected.notna()
                observed = df.loc[exists, f"target_lag_{lag}"].to_numpy(float)
                exp = expected.loc[exists].to_numpy(float)
                bad = int((~np.isclose(observed, exp, equal_nan=True, atol=VALUE_TOL, rtol=VALUE_TOL)).sum())
                lag_mismatch_total += bad
                lag_checked_total += int(exists.sum())
                if bad:
                    add_error(errors, "lag_lookup", {**record, "lag": lag, "failures": bad})

            # Split boundary audit.
            split_bounds: dict[str, Any] = {}
            for split in ["train", "validation", "test"]:
                s = df[usable & df["split"].eq(split)]
                split_bounds[split] = {
                    "rows": int(len(s)),
                    "origin_min": str(s["origin_ts"].min()),
                    "origin_max": str(s["origin_ts"].max()),
                    "target_min": str(s["target_ts"].min()),
                    "target_max": str(s["target_ts"].max()),
                }
            # Saved predictions must match rebuilt frame key columns exactly.
            td = output_dir / f"h{h:03d}" / tid
            pred_path = td / "best_predictions.parquet"
            metrics_path = td / "candidate_metrics.csv"
            manifest_path = td / "manifest.json"
            if pred_path.exists():
                pred = pd.read_parquet(pred_path)
                pred["origin_ts"] = pd.to_datetime(pred["origin_ts"])
                pred["target_ts"] = pd.to_datetime(pred["target_ts"])
                if len(pred) != len(df):
                    add_error(errors, "prediction_row_count", {**record, "expected": int(len(df)), "actual": int(len(pred))})
                else:
                    for col in ["origin_ts", "target_ts", "split", "target_id", "target_value", "origin_value", "is_gateway_outage"]:
                        if col in pred.columns:
                            if pd.api.types.is_numeric_dtype(df[col]) and pd.api.types.is_numeric_dtype(pred[col]):
                                mismatch = int((~np.isclose(df[col].to_numpy(float), pred[col].to_numpy(float), equal_nan=True)).sum())
                            else:
                                mismatch = int(df[col].fillna("__NA__").astype(str).ne(pred[col].fillna("__NA__").astype(str)).sum())
                            if mismatch:
                                add_error(errors, "prediction_frame_column_match", {**record, "column": col, "failures": mismatch})

            # Metrics and manifest consistency for selected model.
            if metrics_path.exists() and manifest_path.exists() and pred_path.exists():
                metrics = pd.read_csv(metrics_path)
                best = metrics.sort_values(["validation_non_gateway_rmse", "validation_non_gateway_mae", "candidate"]).iloc[0]
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest_best = manifest.get("best", {})
                if str(best["candidate"]) != str(manifest_best.get("candidate")):
                    add_error(errors, "manifest_best_candidate", {**record, "metrics_best": str(best["candidate"]), "manifest_best": str(manifest_best.get("candidate"))})
                pred = pd.read_parquet(pred_path)
                if str(pred["candidate"].dropna().iloc[0]) != str(best["candidate"]):
                    add_error(errors, "prediction_best_candidate", {**record, "metrics_best": str(best["candidate"]), "prediction_candidate": str(pred["candidate"].dropna().iloc[0])})
                # Recompute metrics for validation/test and non-gateway variants.
                saved_usable = usable.reset_index(drop=True) if len(pred) == len(df) else pred["target_value"].notna()
                for split in ["validation", "test"]:
                    base_mask = saved_usable & pred["split"].eq(split)
                    for suffix, mask in [(split, base_mask), (f"{split}_non_gateway", base_mask & (~pred["is_gateway_outage"].eq(True)) )]:
                        got = finite_metric(pred.loc[mask, "target_value"], pred.loc[mask, "prediction"].clip(lower=0))
                        expected_cols = {
                            "rows": f"{suffix}_rows",
                            "mae": f"{suffix}_mae",
                            "rmse": f"{suffix}_rmse",
                            "r2": f"{suffix}_r2",
                        }
                        for k, col in expected_cols.items():
                            if col not in best.index:
                                add_error(errors, "missing_metric_column", {**record, "column": col})
                            elif not metric_close(got[k], best[col]):
                                add_error(errors, "metric_recompute", {**record, "candidate": str(best["candidate"]), "metric": col, "recomputed": got[k], "saved": best[col]})

            checks.append({
                **record,
                "rows": int(len(df)),
                "usable_rows": int(usable.sum()),
                "lag_checked_values": lag_checked_total,
                "lag_mismatch_total": lag_mismatch_total,
                "split_bounds": split_bounds,
            })

    # Design warnings that are not data-integrity failures.
    for h in horizons:
        for tid in targets:
            td = output_dir / f"h{h:03d}" / tid
            metrics_path = td / "candidate_metrics.csv"
            manifest_path = td / "manifest.json"
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                channels = manifest.get("lstm_channels", [])
                if "target_hour_sin" not in channels or "target_hour_cos" not in channels:
                    warnings.append({"severity": "medium", "item": "model_family_input_inconsistency", "horizon_hours": h, "target_id": tid, "detail": "LSTM manifest does not include target-hour channels."})
            if not list(td.glob("lstm_seq*_loss_history.csv")):
                warnings.append({"severity": "medium", "item": "lstm_training_diagnostics_missing", "horizon_hours": h, "target_id": tid, "detail": "No LSTM loss-history CSV found."})
            if metrics_path.exists():
                metrics = pd.read_csv(metrics_path)
                candidates = set(metrics.get("candidate", pd.Series(dtype=str)).astype(str))
                required = {"baseline_origin_persistence"}
                if h >= 24:
                    required.add("baseline_same_hour_previous_day")
                if h >= 168:
                    required.add("baseline_same_hour_previous_week")
                missing_baselines = sorted(required - candidates)
                if missing_baselines:
                    warnings.append({"severity": "medium", "item": "seasonal_baseline_missing", "horizon_hours": h, "target_id": tid, "missing": missing_baselines})

    result = {
        "status": "passed" if not errors else "failed",
        "error_count": len(errors),
        "warning_count": len(warnings),
        "paths": {
            "root": str(ROOT),
            "target_file": str(dataset_dir / "target_timeseries_1h.parquet"),
            "feature_file": str(dataset_dir / "feature_timeseries_1h.parquet"),
            "output_dir": str(output_dir),
            "model_script": str(model_script),
        },
        "source_summary": {
            "target_shape": [int(x) for x in target.shape],
            "feature_shape": [int(x) for x in feature.shape],
            "target_duplicate_key_rows": int(target.duplicated(["target_id", "ts"]).sum()),
            "feature_duplicate_ts_rows": int(feature.duplicated(["ts"]).sum()),
            "split_counts": {str(k): int(v) for k, v in feature["split"].value_counts(dropna=False).sort_index().items()},
            "gateway_outage_rows": int(feature["is_gateway_outage"].eq(True).sum()),
        },
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
    }
    out_json = verify_dir / "multi_horizon_strict_integrity_verification.json"
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": result["status"],
        "error_count": result["error_count"],
        "warning_count": result["warning_count"],
        "output": str(out_json),
        "source_summary": result["source_summary"],
        "warnings": warnings,
    }, ensure_ascii=False, indent=2))
    if errors:
        print(json.dumps(errors[:20], ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
