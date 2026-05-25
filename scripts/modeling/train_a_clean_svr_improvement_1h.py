#!/usr/bin/env python3
"""SVR improvement run for EMS A-clean Huang-style 1h benchmark.

Purpose:
- Keep the Huang-style input boundary: historical target lag 1..24 + hour_sin/hour_cos.
- Improve SVR specifically by scaling the regression target y and expanding C/epsilon search.
- Keep outputs separate from the first Huang benchmark.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import TransformedTargetRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import LinearSVR, SVR

TARGET_FILE = "target_timeseries_1h.parquet"
FEATURE_FILE = "feature_timeseries_1h.parquet"
DEFAULT_TARGETS = [
    "T1_group__central_cooling__P",
    "T1_group__local_cooling__P",
    "T1_group__server_power__P",
    "T1_group__ventilation__P",
]


def make_frame(target: pd.DataFrame, feature: pd.DataFrame, target_id: str, lookback: int) -> pd.DataFrame:
    g = target[target["target_id"] == target_id].copy().sort_values("ts")
    if g.empty:
        raise RuntimeError(f"target_id not found: {target_id}")
    df = g.merge(
        feature[["ts", "split", "is_gateway_outage", "gateway_outage_name", "hour_sin", "hour_cos"]],
        on=["ts", "split", "is_gateway_outage", "gateway_outage_name"],
        how="left",
    ).sort_values("ts").reset_index(drop=True)
    for lag in range(1, lookback + 1):
        df[f"target_lag_{lag}"] = df["target_value"].shift(lag)
    train_clean = (df["split"] == "train") & (~df["is_gateway_outage"].fillna(False))
    for col in ["hour_sin", "hour_cos"]:
        df[col] = df[col].astype(float).ffill().fillna(0.0)
    for lag in range(1, lookback + 1):
        col = f"target_lag_{lag}"
        med = df.loc[train_clean, col].median()
        df[col] = df[col].fillna(float(med) if pd.notna(med) else 0.0)
    df["has_full_lookback"] = df.groupby("target_id").cumcount() >= lookback
    return df


def usable_mask(df: pd.DataFrame) -> pd.Series:
    return (
        df["has_full_lookback"].fillna(False)
        & df["target_observed"].fillna(False)
        & df["is_full_component_observed"].fillna(False)
        & (~df["is_replacement_gap"].fillna(False))
    )


def deterministic_subset(mask: pd.Series, max_rows: int) -> np.ndarray:
    idx = np.flatnonzero(mask.to_numpy())
    if max_rows <= 0 or len(idx) <= max_rows:
        return idx
    pos = np.linspace(0, len(idx) - 1, num=max_rows, dtype=int)
    return idx[pos]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def metric_dict(actual: np.ndarray, pred: np.ndarray, prefix: str) -> dict[str, Any]:
    actual = np.asarray(actual, dtype=float)
    pred = np.asarray(pred, dtype=float)
    mask = np.isfinite(actual) & np.isfinite(pred)
    actual = actual[mask]
    pred = pred[mask]
    if actual.size == 0:
        return {f"{prefix}_rows": 0, f"{prefix}_mae": None, f"{prefix}_rmse": None, f"{prefix}_r2": None}
    return {
        f"{prefix}_rows": int(actual.size),
        f"{prefix}_mae": float(mean_absolute_error(actual, pred)),
        f"{prefix}_rmse": float(math.sqrt(mean_squared_error(actual, pred))),
        f"{prefix}_r2": float(r2_score(actual, pred)) if actual.size >= 2 else None,
    }


@dataclass
class FitResult:
    row: dict[str, Any]
    prediction: np.ndarray
    model: Any


def fit_candidate(
    df: pd.DataFrame,
    cols: list[str],
    estimator: Any,
    name: str,
    kernel: str,
    target_id: str,
    max_train_rows: int,
) -> FitResult:
    train_mask = usable_mask(df) & (df["split"] == "train") & (~df["is_gateway_outage"].fillna(False))
    train_idx_all = np.flatnonzero(train_mask.to_numpy())
    train_idx = deterministic_subset(train_mask, max_train_rows) if max_train_rows else train_idx_all
    X_train = df.iloc[train_idx][cols].to_numpy(dtype=float)
    y_train = df.iloc[train_idx]["target_value"].to_numpy(dtype=float)
    X_all = df[cols].to_numpy(dtype=float)

    start = time.time()
    estimator.fit(X_train, y_train)
    fit_seconds = time.time() - start
    pred = np.maximum(estimator.predict(X_all), 0.0)

    row: dict[str, Any] = {
        "candidate": name,
        "model_family": "SVR",
        "kernel": kernel,
        "target_id": target_id,
        "fit_seconds": float(fit_seconds),
        "train_rows": int(len(train_idx_all)),
        "train_sample_rows": int(len(train_idx)),
        "target_scaling": "StandardScaler(y)",
        "x_scaling": "StandardScaler(X)",
    }
    for split in ["validation", "test"]:
        mask = (usable_mask(df) & (df["split"] == split)).to_numpy()
        row.update(metric_dict(df.loc[mask, "target_value"].to_numpy(), pred[mask], split))
        ng_mask = mask & (~df["is_gateway_outage"].fillna(False).to_numpy(dtype=bool))
        row.update(metric_dict(df.loc[ng_mask, "target_value"].to_numpy(), pred[ng_mask], f"{split}_non_gateway"))
    return FitResult(row=row, prediction=pred, model=estimator)


def make_linear_svr(C: float, eps: float, seed: int) -> Any:
    reg = Pipeline([
        ("scale", StandardScaler()),
        ("model", LinearSVR(C=C, epsilon=eps, loss="epsilon_insensitive", dual="auto", tol=1e-4, max_iter=30000, random_state=seed)),
    ])
    return TransformedTargetRegressor(regressor=reg, transformer=StandardScaler())


def make_rbf_svr(C: float, eps: float, cache_size: int) -> Any:
    reg = Pipeline([
        ("scale", StandardScaler()),
        ("model", SVR(kernel="rbf", C=C, epsilon=eps, gamma="scale", cache_size=cache_size)),
    ])
    return TransformedTargetRegressor(regressor=reg, transformer=StandardScaler())


def run_target(target: pd.DataFrame, feature: pd.DataFrame, target_id: str, out_dir: Path, lookback: int, seed: int, quick: bool) -> dict[str, Any]:
    target_out = out_dir / target_id
    target_out.mkdir(parents=True, exist_ok=True)
    df = make_frame(target, feature, target_id, lookback)
    cols = [f"target_lag_{i}" for i in range(1, lookback + 1)] + ["hour_sin", "hour_cos"]

    results: list[FitResult] = []
    linear_grid = [(0.1, 0.02), (1.0, 0.02), (10.0, 0.02)] if quick else [
        (0.1, 0.02), (1.0, 0.02), (10.0, 0.02),
        (0.1, 0.05), (1.0, 0.05), (10.0, 0.05),
        (0.1, 0.10), (1.0, 0.10), (10.0, 0.10),
    ]
    for C, eps in linear_grid:
        name = f"linear_svr_yz_C{C:g}_eps{eps:g}"
        res = fit_candidate(df, cols, make_linear_svr(C, eps, seed), name, "linear", target_id, max_train_rows=0)
        results.append(res)
        print(json.dumps({"target_id": target_id, "candidate": name, "validation_rmse": res.row["validation_non_gateway_rmse"], "test_rmse": res.row["test_non_gateway_rmse"]}), flush=True)

    rbf_grid = [(1.0, 0.05), (5.0, 0.05)] if quick else [
        (0.5, 0.02), (1.0, 0.02), (2.0, 0.02), (5.0, 0.02),
        (0.5, 0.05), (1.0, 0.05), (2.0, 0.05), (5.0, 0.05),
        (0.5, 0.10), (1.0, 0.10), (2.0, 0.10), (5.0, 0.10),
    ]
    first_sample = 4000 if quick else 8000
    for C, eps in rbf_grid:
        name = f"rbf_svr_yz_s{first_sample}_C{C:g}_eps{eps:g}"
        res = fit_candidate(df, cols, make_rbf_svr(C, eps, cache_size=2000), name, "rbf", target_id, max_train_rows=first_sample)
        results.append(res)
        print(json.dumps({"target_id": target_id, "candidate": name, "validation_rmse": res.row["validation_non_gateway_rmse"], "test_rmse": res.row["test_non_gateway_rmse"]}), flush=True)

    # Promote the best sampled RBF settings to a larger deterministic training subset.
    promote_n = 1 if quick else 4
    promoted_sample = 8000 if quick else 16000
    rbf_rows = [r for r in results if r.row["kernel"] == "rbf" and r.row["train_sample_rows"] == first_sample]
    rbf_rows = sorted(rbf_rows, key=lambda r: (r.row["validation_non_gateway_rmse"], r.row["validation_non_gateway_mae"]))[:promote_n]
    for prev in rbf_rows:
        # Parse C/eps from the candidate string to avoid maintaining a separate object map.
        parts = prev.row["candidate"].split("_")
        C = float([p[1:] for p in parts if p.startswith("C")][0])
        eps = float([p[3:] for p in parts if p.startswith("eps")][0])
        name = f"rbf_svr_yz_s{promoted_sample}_C{C:g}_eps{eps:g}"
        res = fit_candidate(df, cols, make_rbf_svr(C, eps, cache_size=4000), name, "rbf", target_id, max_train_rows=promoted_sample)
        results.append(res)
        print(json.dumps({"target_id": target_id, "candidate": name, "validation_rmse": res.row["validation_non_gateway_rmse"], "test_rmse": res.row["test_non_gateway_rmse"]}), flush=True)

    metrics = pd.DataFrame([r.row for r in results]).sort_values(["validation_non_gateway_rmse", "validation_non_gateway_mae", "candidate"]).reset_index(drop=True)
    metrics.to_csv(target_out / "svr_improvement_candidate_metrics.csv", index=False)
    best = metrics.iloc[0].to_dict()
    best_name = str(best["candidate"])
    best_res = next(r for r in results if r.row["candidate"] == best_name)
    pred_cols = ["ts", "target_id", "target_version_id", "split", "target_value", "is_gateway_outage", "gateway_outage_name"]
    pred_df = df[pred_cols].copy()
    pred_df["prediction"] = best_res.prediction
    pred_df["svr_candidate"] = best_name
    pred_df.to_parquet(target_out / "svr_improvement_best_predictions.parquet", index=False)
    joblib.dump(best_res.model, target_out / "svr_improvement_best_model.joblib", compress=3)
    with (target_out / "svr_improvement_manifest.json").open("w", encoding="utf-8") as f:
        json.dump({
            "created_at_utc": utc_now(),
            "target_id": target_id,
            "input_policy": "Huang-style: target_lag_1..24 + hour_sin/hour_cos only",
            "selection_metric": "validation_non_gateway_rmse",
            "main_change": "scale target y for SVR; expand C/epsilon; promote best sampled RBF settings",
            "best": best,
            "features": cols,
        }, f, ensure_ascii=False, indent=2, default=str)
    return best


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset-dir", type=Path, default=Path("outputs/modeling/a_clean_targets_1h"))
    p.add_argument("--out-dir", type=Path, default=Path("outputs/modeling/a_clean_svr_improvement_1h"))
    p.add_argument("--target-id", action="append", default=None)
    p.add_argument("--lookback", type=int, default=24)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--quick", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    dataset_dir = args.dataset_dir.resolve()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = pd.read_parquet(dataset_dir / TARGET_FILE)
    feature = pd.read_parquet(dataset_dir / FEATURE_FILE)
    targets = args.target_id or DEFAULT_TARGETS
    best_rows = [run_target(target, feature, tid, out_dir, args.lookback, args.seed, args.quick) for tid in targets]
    summary = pd.DataFrame(best_rows)
    summary.to_csv(out_dir / "svr_improvement_summary.csv", index=False)
    with (out_dir / "run_manifest.json").open("w", encoding="utf-8") as f:
        json.dump({
            "created_at_utc": utc_now(),
            "run_label": "a_clean_svr_improvement_1h",
            "dataset_dir": str(dataset_dir),
            "out_dir": str(out_dir),
            "targets": targets,
            "lookback": args.lookback,
            "selection_metric": "validation_non_gateway_rmse",
            "quick": bool(args.quick),
            "best": summary.to_dict(orient="records"),
        }, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps({"status": "ok", "out_dir": str(out_dir), "targets": targets}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
