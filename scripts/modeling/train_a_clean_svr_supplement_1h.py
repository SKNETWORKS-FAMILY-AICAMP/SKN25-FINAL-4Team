#!/usr/bin/env python3
"""Run an SVR supplement for EMS A-clean 1h targets.

This is a targeted follow-up to the A-clean champion run. It keeps the
existing target cache and feature policy, then evaluates SVR-style candidates
that were omitted from the champion suite.

Notes:
- LinearSVR is trained on all eligible train rows.
- RBF SVR is trained on a deterministic subset because full-kernel SVR is
  quadratic in train rows and is expensive for ~35k hourly samples.
- Selection remains validation-only; test is holdout reporting.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import LinearSVR, SVR

import importlib.util
import sys


def load_champion_module(script_path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("champion", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load champion script: {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def train_mask_for(df: pd.DataFrame) -> pd.Series:
    usable = (
        df["target_observed"].fillna(False)
        & df["is_full_component_observed"].fillna(False)
        & (~df["is_replacement_gap"].fillna(False))
    )
    return usable & (df["split"] == "train") & (~df["is_gateway_outage"].fillna(False))


def deterministic_subset(mask: pd.Series, max_rows: int) -> np.ndarray:
    idx = np.flatnonzero(mask.to_numpy())
    if max_rows <= 0 or len(idx) <= max_rows:
        return idx
    # Preserve the full training period shape without random variance.
    positions = np.linspace(0, len(idx) - 1, num=max_rows, dtype=int)
    return idx[positions]


def evaluate_candidate(champion: Any, df: pd.DataFrame, pred: np.ndarray, name: str, kind: str, target_id: str, feature_count: int, fit_seconds: float, train_rows: int, train_sample_rows: int) -> dict[str, Any]:
    pred = np.maximum(np.asarray(pred, dtype=float), 0.0)
    row: dict[str, Any] = {"candidate": name, "kind": kind}
    for split in ["validation", "test"]:
        mask = (df["split"] == split).to_numpy()
        row.update(champion.metric_dict(df.loc[mask, "target_value"].to_numpy(), pred[mask], split))
        ng_mask = mask & (~df["is_gateway_outage"].fillna(False).to_numpy(dtype=bool))
        row.update(champion.metric_dict(df.loc[ng_mask, "target_value"].to_numpy(), pred[ng_mask], f"{split}_non_gateway"))
    row.update({
        "target_id": target_id,
        "feature_count": feature_count,
        "fit_seconds": float(fit_seconds),
        "train_rows": int(train_rows),
        "train_sample_rows": int(train_sample_rows),
    })
    return row


def run_target(champion: Any, target: pd.DataFrame, feature: pd.DataFrame, target_id: str, out_dir: Path, max_rbf_train_rows: int, seed: int) -> dict[str, Any]:
    target_out = out_dir / target_id
    target_out.mkdir(parents=True, exist_ok=True)
    df = champion.make_target_frame(target, feature, target_id)
    cols = champion.feature_columns()
    mask = train_mask_for(df)
    X_all = df[cols].to_numpy(dtype=float)
    X_train_full = df.loc[mask, cols].to_numpy(dtype=float)
    y_train_full = df.loc[mask, "target_value"].to_numpy(dtype=float)

    candidates = [
        {
            "name": "linear_svr_C1_eps0.1",
            "kind": "svr_full_train",
            "estimator": Pipeline([
                ("scale", StandardScaler()),
                ("model", LinearSVR(C=1.0, epsilon=0.1, random_state=seed, max_iter=20000, tol=1e-4)),
            ]),
            "sample_rows": 0,
        },
        {
            "name": f"rbf_svr_C10_eps0.1_train{max_rbf_train_rows}",
            "kind": "svr_rbf_sampled_train",
            "estimator": Pipeline([
                ("scale", StandardScaler()),
                ("model", SVR(kernel="rbf", C=10.0, epsilon=0.1, gamma="scale", cache_size=1000)),
            ]),
            "sample_rows": max_rbf_train_rows,
        },
    ]

    rows = []
    estimators: dict[str, Any] = {}
    predictions: dict[str, np.ndarray] = {}
    train_idx_all = np.flatnonzero(mask.to_numpy())

    for cand in candidates:
        estimator = clone(cand["estimator"])
        if cand["sample_rows"]:
            train_idx = deterministic_subset(mask, int(cand["sample_rows"]))
            X_train = df.iloc[train_idx][cols].to_numpy(dtype=float)
            y_train = df.iloc[train_idx]["target_value"].to_numpy(dtype=float)
        else:
            train_idx = train_idx_all
            X_train = X_train_full
            y_train = y_train_full
        start = time.time()
        estimator.fit(X_train, y_train)
        fit_seconds = time.time() - start
        pred = estimator.predict(X_all)
        row = evaluate_candidate(
            champion, df, pred, cand["name"], cand["kind"], target_id, len(cols), fit_seconds,
            train_rows=len(train_idx_all), train_sample_rows=len(train_idx),
        )
        rows.append(row)
        predictions[cand["name"]] = np.maximum(pred, 0.0)
        estimators[cand["name"]] = estimator
        print(json.dumps({
            "target_id": target_id,
            "candidate": cand["name"],
            "validation_non_gateway_mae": row.get("validation_non_gateway_mae"),
            "test_non_gateway_mae": row.get("test_non_gateway_mae"),
            "fit_seconds": round(float(fit_seconds), 2),
            "train_sample_rows": len(train_idx),
        }, ensure_ascii=False), flush=True)

    metrics = pd.DataFrame(rows).sort_values(["validation_non_gateway_mae", "candidate"]).reset_index(drop=True)
    metrics.to_csv(target_out / "svr_candidate_metrics.csv", index=False)
    best = metrics.iloc[0].to_dict()
    best_name = str(best["candidate"])

    pred_cols = ["ts", "target_id", "target_version_id", "split", "target_value", "is_gateway_outage", "gateway_outage_name"]
    pred_df = df[pred_cols].copy()
    pred_df["prediction"] = predictions[best_name]
    pred_df["svr_candidate"] = best_name
    pred_df.to_parquet(target_out / "svr_best_predictions.parquet", index=False)
    joblib.dump({
        "target_id": target_id,
        "candidate": best_name,
        "model": estimators[best_name],
        "feature_columns": cols,
        "created_at_utc": utc_now(),
    }, target_out / "svr_best_model.joblib", compress=3)
    with (target_out / "svr_manifest.json").open("w", encoding="utf-8") as f:
        json.dump({
            "created_at_utc": utc_now(),
            "target_id": target_id,
            "selection_metric": "validation_non_gateway_mae",
            "best_svr": best,
            "candidates": metrics.to_dict(orient="records"),
            "feature_columns": cols,
            "notes": [
                "LinearSVR uses all eligible train rows.",
                "RBF SVR uses deterministic train subset because exact kernel SVR is quadratic in row count.",
            ],
        }, f, ensure_ascii=False, indent=2, default=str)
    return best


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=Path("outputs/modeling/a_clean_targets_1h"))
    parser.add_argument("--out-dir", type=Path, default=Path("outputs/modeling/a_clean_svr_supplement_1h"))
    parser.add_argument("--champion-script", type=Path, default=Path("scripts/modeling/train_a_clean_champion_models_1h.py"))
    parser.add_argument("--target-id", action="append", default=None)
    parser.add_argument("--max-rbf-train-rows", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    champion = load_champion_module(args.champion_script.resolve())
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    dataset_dir = args.dataset_dir.resolve()
    target = pd.read_parquet(dataset_dir / champion.TARGET_FILE)
    feature = pd.read_parquet(dataset_dir / champion.FEATURE_FILE)
    targets = args.target_id or champion.DEFAULT_TARGETS
    best_rows = []
    for target_id in targets:
        best_rows.append(run_target(champion, target, feature, target_id, out_dir, args.max_rbf_train_rows, args.seed))
    best_df = pd.DataFrame(best_rows)
    best_df.to_csv(out_dir / "svr_summary.csv", index=False)
    manifest = {
        "created_at_utc": utc_now(),
        "run_label": "a_clean_svr_supplement_20260520",
        "dataset_dir": str(dataset_dir),
        "out_dir": str(out_dir),
        "targets": targets,
        "max_rbf_train_rows": args.max_rbf_train_rows,
        "selection_metric": "validation_non_gateway_mae",
        "best_svr": best_df.to_dict(orient="records"),
    }
    with (out_dir / "run_manifest.json").open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps({"status": "ok", **manifest}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
