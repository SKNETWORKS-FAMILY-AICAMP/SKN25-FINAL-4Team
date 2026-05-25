#!/usr/bin/env python3
"""A-clean paper-adjacent forecasting model suite.

Nature Scientific Data paper is a dataset descriptor and does not publish a single
forecasting benchmark table. For this autonomous run, we operationalize the paper's
modeling/control use cases as reproducible next-hour forecasting baselines:

- persistence/seasonal lag features
- linear regression with regularization (Ridge)
- tree ensemble regression (RandomForest, HistGradientBoosting)
- feed-forward neural regression (MLP)

All models use only the materialized A-clean Parquet cache and preserve target definitions.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor, ExtraTreesRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

TARGET_FILE = "target_timeseries_1h.parquet"
FEATURE_FILE = "feature_timeseries_1h.parquet"

DEFAULT_TARGETS = [
    "T1_group__central_cooling__P",
    "T1_group__local_cooling__P",
    "T1_group__server_power__P",
    "T1_group__ventilation__P",
]

LAGS = [1, 2, 3, 24, 48, 168]
ROLLING_WINDOWS = [3, 24, 168]
CALENDAR_COLS = ["hour_sin", "hour_cos", "dow_sin", "dow_cos", "month_sin", "month_cos"]
WEATHER_COLS = ["Ta", "Igm"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)


def make_features(target: pd.DataFrame, feature: pd.DataFrame, target_id: str) -> pd.DataFrame:
    g = target[target["target_id"] == target_id].copy().sort_values("ts")
    if g.empty:
        raise RuntimeError(f"target_id not found: {target_id}")
    df = g.merge(feature, on=["ts", "split", "is_gateway_outage", "gateway_outage_name"], how="left")
    df = df.sort_values("ts").reset_index(drop=True)

    for lag in LAGS:
        df[f"target_lag_{lag}"] = df["target_value"].shift(lag)
    for window in ROLLING_WINDOWS:
        shifted = df["target_value"].shift(1)
        df[f"target_roll_mean_{window}"] = shifted.rolling(window, min_periods=max(1, window // 4)).mean()
        df[f"target_roll_std_{window}"] = shifted.rolling(window, min_periods=max(2, window // 4)).std()
    for col in WEATHER_COLS:
        df[f"{col}_lag_1"] = df[col].shift(1)
        df[f"{col}_lag_24"] = df[col].shift(24)
        obs_col = f"{col}_observed"
        if obs_col in df.columns:
            df[f"{col}_observed_float"] = df[obs_col].fillna(False).astype(float)

    # Fill input weather gaps using causal ffill, then train-median fallback. Target values are never filled.
    input_cols = get_feature_columns(df)
    train_mask = (df["split"] == "train") & (~df["is_gateway_outage"].fillna(False))
    for col in input_cols:
        if df[col].isna().any():
            df[col] = df[col].ffill()
            median = df.loc[train_mask, col].median()
            if pd.isna(median):
                median = 0.0
            df[col] = df[col].fillna(float(median))
    return df


def get_feature_columns(df: pd.DataFrame | None = None) -> list[str]:
    cols = []
    cols += [f"target_lag_{lag}" for lag in LAGS]
    for window in ROLLING_WINDOWS:
        cols += [f"target_roll_mean_{window}", f"target_roll_std_{window}"]
    cols += [f"{col}_lag_1" for col in WEATHER_COLS]
    cols += [f"{col}_lag_24" for col in WEATHER_COLS]
    cols += [f"{col}_observed_float" for col in WEATHER_COLS]
    cols += CALENDAR_COLS
    if df is not None:
        return [c for c in cols if c in df.columns]
    return cols


def metric_dict(actual: np.ndarray, pred: np.ndarray, prefix: str) -> dict[str, Any]:
    actual = np.asarray(actual, dtype=float)
    pred = np.asarray(pred, dtype=float)
    mask = np.isfinite(actual) & np.isfinite(pred)
    actual = actual[mask]
    pred = pred[mask]
    if actual.size == 0:
        return {f"{prefix}_rows": 0, f"{prefix}_mae": None, f"{prefix}_rmse": None, f"{prefix}_mape": None, f"{prefix}_bias": None}
    err = pred - actual
    abs_err = np.abs(err)
    nz = np.abs(actual) > 1e-6
    return {
        f"{prefix}_rows": int(actual.size),
        f"{prefix}_mae": float(mean_absolute_error(actual, pred)),
        f"{prefix}_rmse": float(math.sqrt(mean_squared_error(actual, pred))),
        f"{prefix}_mape": float(np.mean(abs_err[nz] / np.abs(actual[nz])) * 100.0) if np.any(nz) else None,
        f"{prefix}_bias": float(np.mean(err)),
    }


def build_models(seed: int, quick: bool = False) -> dict[str, Any]:
    rf_estimators = 120 if quick else 300
    et_estimators = 120 if quick else 300
    mlp_iter = 120 if quick else 250
    return {
        "ridge": Pipeline([
            ("scale", StandardScaler()),
            ("model", Ridge(alpha=1.0, random_state=seed)),
        ]),
        "hist_gradient_boosting": HistGradientBoostingRegressor(
            max_iter=300 if not quick else 120,
            learning_rate=0.05,
            max_leaf_nodes=31,
            l2_regularization=0.01,
            random_state=seed,
            early_stopping=True,
            validation_fraction=0.1,
            n_iter_no_change=20,
        ),
        "random_forest": RandomForestRegressor(
            n_estimators=rf_estimators,
            max_depth=18,
            min_samples_leaf=3,
            n_jobs=-1,
            random_state=seed,
        ),
        "extra_trees": ExtraTreesRegressor(
            n_estimators=et_estimators,
            max_depth=24,
            min_samples_leaf=2,
            n_jobs=-1,
            random_state=seed,
        ),
        "mlp": Pipeline([
            ("scale", StandardScaler()),
            ("model", MLPRegressor(
                hidden_layer_sizes=(128, 64),
                activation="relu",
                solver="adam",
                alpha=1e-4,
                batch_size=256,
                learning_rate_init=1e-3,
                max_iter=mlp_iter,
                early_stopping=True,
                validation_fraction=0.1,
                n_iter_no_change=20,
                random_state=seed,
            )),
        ]),
    }


def train_target(df: pd.DataFrame, target_id: str, out_dir: Path, seed: int, quick: bool) -> list[dict[str, Any]]:
    out_target = out_dir / target_id
    out_target.mkdir(parents=True, exist_ok=True)
    feature_cols = get_feature_columns(df)
    usable = (
        df["target_observed"].fillna(False)
        & df["is_full_component_observed"].fillna(False)
        & (~df["is_replacement_gap"].fillna(False))
    )
    # Training excludes gateway outage. Evaluation reports validation/test all and non-gateway.
    train_mask = usable & (df["split"] == "train") & (~df["is_gateway_outage"].fillna(False))
    val_mask = usable & (df["split"] == "validation")
    test_mask = usable & (df["split"] == "test")
    if train_mask.sum() == 0:
        raise RuntimeError(f"no train rows for {target_id}")

    X_train = df.loc[train_mask, feature_cols].to_numpy(dtype=float)
    y_train = df.loc[train_mask, "target_value"].to_numpy(dtype=float)
    rows: list[dict[str, Any]] = []
    models = build_models(seed, quick=quick)

    for model_name, model in models.items():
        start = time.time()
        model.fit(X_train, y_train)
        fit_seconds = time.time() - start
        joblib.dump({"model": model, "feature_cols": feature_cols, "target_id": target_id}, out_target / f"{model_name}.joblib")

        model_row: dict[str, Any] = {
            "target_id": target_id,
            "model": model_name,
            "feature_count": len(feature_cols),
            "train_rows": int(train_mask.sum()),
            "fit_seconds": float(fit_seconds),
        }
        prediction_frames = []
        for split_name, mask in [("validation", val_mask), ("test", test_mask)]:
            split_df = df.loc[mask, ["ts", "target_id", "target_version_id", "split", "target_value", "is_gateway_outage"]].copy()
            pred = model.predict(df.loc[mask, feature_cols].to_numpy(dtype=float)) if mask.sum() else np.array([], dtype=float)
            pred = np.maximum(pred, 0.0)
            split_df["prediction"] = pred
            split_df["model"] = model_name
            prediction_frames.append(split_df)
            model_row.update(metric_dict(split_df["target_value"].to_numpy(), pred, split_name))
            non_gateway = split_df.loc[~split_df["is_gateway_outage"].fillna(False)]
            model_row.update(metric_dict(non_gateway["target_value"].to_numpy(), non_gateway["prediction"].to_numpy(), f"{split_name}_non_gateway"))
        pd.concat(prediction_frames, ignore_index=True).to_parquet(out_target / f"predictions_{model_name}.parquet", index=False)
        rows.append(model_row)
        print(json.dumps({"target_id": target_id, "model": model_name, "fit_seconds": round(fit_seconds, 2), "test_non_gateway_mae": model_row.get("test_non_gateway_mae")}, ensure_ascii=False), flush=True)
    pd.DataFrame(rows).to_csv(out_target / "model_metrics.csv", index=False)
    with (out_target / "feature_columns.json").open("w", encoding="utf-8") as f:
        json.dump(feature_cols, f, ensure_ascii=False, indent=2)
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=Path("outputs/modeling/a_clean_targets_1h"))
    parser.add_argument("--out-dir", type=Path, default=Path("outputs/modeling/a_clean_paper_models_1h"))
    parser.add_argument("--target-id", action="append", default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--quick", action="store_true", help="Use smaller model settings for smoke tests")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    set_seed(args.seed)
    dataset_dir = args.dataset_dir.resolve()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = pd.read_parquet(dataset_dir / TARGET_FILE)
    feature = pd.read_parquet(dataset_dir / FEATURE_FILE)
    targets = args.target_id or DEFAULT_TARGETS
    all_rows: list[dict[str, Any]] = []
    for target_id in targets:
        df = make_features(target, feature, target_id)
        rows = train_target(df, target_id, out_dir, args.seed, quick=args.quick)
        all_rows.extend(rows)
    metrics = pd.DataFrame(all_rows)
    metrics.to_csv(out_dir / "paper_model_metrics.csv", index=False)
    best = metrics.sort_values(["target_id", "test_non_gateway_mae"]).groupby("target_id", as_index=False).first()
    best.to_csv(out_dir / "paper_model_best_test_non_gateway.csv", index=False)
    summary = {
        "created_at_utc": utc_now(),
        "dataset_dir": str(dataset_dir),
        "out_dir": str(out_dir),
        "targets": targets,
        "models": list(build_models(args.seed, quick=args.quick).keys()),
        "feature_policy": {
            "target_lags": LAGS,
            "rolling_windows": ROLLING_WINDOWS,
            "weather": "causal lag_1 and lag_24 with ffill/median input imputation only",
            "calendar": CALENDAR_COLS,
            "train_filter": "train & non_gateway_outage & observed/full_component & not_replacement_gap",
        },
        "best_test_non_gateway": best[["target_id", "model", "test_non_gateway_mae", "test_non_gateway_rmse", "test_non_gateway_mape"]].to_dict(orient="records"),
    }
    with (out_dir / "run_manifest.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps({"status": "ok", **summary}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
