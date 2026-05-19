#!/usr/bin/env python3
"""Train A-clean champion forecast candidates with validation-based selection.

This script is intentionally separate from the previous exploratory paper-model run.
It creates a rollback-friendly champion artifact set for branch
`exp/a-clean-champion-models-20260520`.

Key rules:
- Input is the fixed A-clean cache; no DB query.
- One independent champion per target.
- Model selection uses validation non-gateway MAE only; test is held out for final reporting.
- Persistence baselines are included as non-learned competitors, so the reported champion can honestly remain
  `last_value` if learned models fail to beat it.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import RobustScaler, StandardScaler

TARGET_FILE = "target_timeseries_1h.parquet"
FEATURE_FILE = "feature_timeseries_1h.parquet"

DEFAULT_TARGETS = [
    "T1_group__central_cooling__P",
    "T1_group__local_cooling__P",
    "T1_group__server_power__P",
    "T1_group__ventilation__P",
]

TARGET_LABELS = {
    "T1_group__central_cooling__P": "central_cooling",
    "T1_group__local_cooling__P": "local_cooling",
    "T1_group__server_power__P": "server_power",
    "T1_group__ventilation__P": "ventilation",
}

LAGS = [1, 2, 3, 6, 12, 24, 48, 72, 168]
ROLLING_WINDOWS = [3, 6, 12, 24, 168]
WEATHER_COLS = ["Ta", "Igm"]
CALENDAR_COLS = ["hour_sin", "hour_cos", "dow_sin", "dow_cos", "month_sin", "month_cos"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)


def safe_name(s: str) -> str:
    return s.replace("/", "_").replace(" ", "_")


def metric_dict(actual: np.ndarray, pred: np.ndarray, prefix: str) -> dict[str, Any]:
    actual = np.asarray(actual, dtype=float)
    pred = np.asarray(pred, dtype=float)
    mask = np.isfinite(actual) & np.isfinite(pred)
    actual = actual[mask]
    pred = pred[mask]
    if actual.size == 0:
        return {
            f"{prefix}_rows": 0,
            f"{prefix}_mae": None,
            f"{prefix}_rmse": None,
            f"{prefix}_mape": None,
            f"{prefix}_smape": None,
            f"{prefix}_bias": None,
        }
    err = pred - actual
    abs_err = np.abs(err)
    nz = np.abs(actual) > 1e-6
    denom = (np.abs(actual) + np.abs(pred)) / 2.0
    smape_mask = denom > 1e-6
    return {
        f"{prefix}_rows": int(actual.size),
        f"{prefix}_mae": float(mean_absolute_error(actual, pred)),
        f"{prefix}_rmse": float(math.sqrt(mean_squared_error(actual, pred))),
        f"{prefix}_mape": float(np.mean(abs_err[nz] / np.abs(actual[nz])) * 100.0) if np.any(nz) else None,
        f"{prefix}_smape": float(np.mean(abs_err[smape_mask] / denom[smape_mask]) * 100.0) if np.any(smape_mask) else None,
        f"{prefix}_bias": float(np.mean(err)),
    }


def feature_columns() -> list[str]:
    cols: list[str] = []
    cols += [f"target_lag_{lag}" for lag in LAGS]
    for window in ROLLING_WINDOWS:
        cols += [
            f"target_roll_mean_{window}",
            f"target_roll_std_{window}",
            f"target_roll_min_{window}",
            f"target_roll_max_{window}",
        ]
    for col in WEATHER_COLS:
        cols += [f"{col}_lag_1", f"{col}_lag_3", f"{col}_lag_24", f"{col}_roll_mean_24", f"{col}_observed_float"]
    cols += ["Ta_lag_1_cdd18", "Ta_lag_1_cdd22", "Ta_lag_1_hdd18", "Igm_lag_1_log1p"]
    cols += ["is_weekend", "hour", "dow", "month"]
    cols += CALENDAR_COLS
    return cols


def make_target_frame(target: pd.DataFrame, feature: pd.DataFrame, target_id: str) -> pd.DataFrame:
    g = target[target["target_id"] == target_id].copy().sort_values("ts")
    if g.empty:
        raise RuntimeError(f"target_id not found: {target_id}")
    df = g.merge(feature, on=["ts", "split", "is_gateway_outage", "gateway_outage_name"], how="left")
    df = df.sort_values("ts").reset_index(drop=True)

    shifted = df["target_value"].shift(1)
    for lag in LAGS:
        df[f"target_lag_{lag}"] = df["target_value"].shift(lag)
    for window in ROLLING_WINDOWS:
        min_periods = max(2, min(window, window // 4))
        roll = shifted.rolling(window, min_periods=min_periods)
        df[f"target_roll_mean_{window}"] = roll.mean()
        df[f"target_roll_std_{window}"] = roll.std()
        df[f"target_roll_min_{window}"] = roll.min()
        df[f"target_roll_max_{window}"] = roll.max()

    for col in WEATHER_COLS:
        for lag in [1, 3, 24]:
            df[f"{col}_lag_{lag}"] = df[col].shift(lag)
        df[f"{col}_roll_mean_24"] = df[col].shift(1).rolling(24, min_periods=6).mean()
        obs_col = f"{col}_observed"
        df[f"{col}_observed_float"] = df[obs_col].fillna(False).astype(float) if obs_col in df else 0.0

    ta = df["Ta_lag_1"]
    df["Ta_lag_1_cdd18"] = (ta - 18.0).clip(lower=0.0)
    df["Ta_lag_1_cdd22"] = (ta - 22.0).clip(lower=0.0)
    df["Ta_lag_1_hdd18"] = (18.0 - ta).clip(lower=0.0)
    df["Igm_lag_1_log1p"] = np.log1p(df["Igm_lag_1"].clip(lower=0.0))

    ts = pd.to_datetime(df["ts"])
    df["hour"] = ts.dt.hour.astype(float)
    df["dow"] = ts.dt.dayofweek.astype(float)
    df["month"] = ts.dt.month.astype(float)
    df["is_weekend"] = (ts.dt.dayofweek >= 5).astype(float)

    # Input-only missing value policy: causal ffill followed by train median fallback. Targets are never filled.
    train_clean = (df["split"] == "train") & (~df["is_gateway_outage"].fillna(False))
    for col in feature_columns():
        if col not in df.columns:
            df[col] = np.nan
        if df[col].isna().any():
            df[col] = df[col].ffill()
            median = df.loc[train_clean, col].median()
            if pd.isna(median):
                median = 0.0
            df[col] = df[col].fillna(float(median))
        df[col] = df[col].astype(float)
    return df


@dataclass
class Candidate:
    name: str
    kind: str
    estimator: Any | None = None
    prediction_col: str | None = None


def selection_priority(candidate_name: str) -> int:
    """Lower is simpler/preferred when validation scores are practically tied."""
    if candidate_name == "last_value":
        return 0
    if candidate_name.startswith("seasonal_"):
        return 1
    if candidate_name.startswith("ridge"):
        return 2
    if candidate_name.startswith("hgb"):
        return 3
    if candidate_name.startswith("extra_trees"):
        return 4
    if candidate_name.startswith("random_forest"):
        return 5
    if candidate_name.startswith("mlp"):
        return 6
    return 99


def build_candidates(seed: int, quick: bool = False) -> list[Candidate]:
    candidates: list[Candidate] = [
        Candidate("last_value", "baseline", prediction_col="target_lag_1"),
        Candidate("seasonal_24h", "baseline", prediction_col="target_lag_24"),
        Candidate("seasonal_168h", "baseline", prediction_col="target_lag_168"),
    ]
    ridge_alphas = [0.1, 1.0, 10.0, 100.0, 1000.0]
    for alpha in ridge_alphas:
        candidates.append(Candidate(
            f"ridge_alpha_{alpha:g}",
            "learned",
            Pipeline([("scale", StandardScaler()), ("model", Ridge(alpha=alpha, random_state=seed))]),
        ))
    for alpha in [0.1, 1.0, 10.0, 100.0]:
        candidates.append(Candidate(
            f"ridge_robust_alpha_{alpha:g}",
            "learned",
            Pipeline([("scale", RobustScaler()), ("model", Ridge(alpha=alpha, random_state=seed))]),
        ))

    hgb_iter = [160] if quick else [180, 320]
    for max_iter in hgb_iter:
        for lr in ([0.05] if quick else [0.03, 0.06]):
            candidates.append(Candidate(
                f"hgb_iter_{max_iter}_lr_{lr:g}",
                "learned",
                HistGradientBoostingRegressor(
                    max_iter=max_iter,
                    learning_rate=lr,
                    max_leaf_nodes=31,
                    l2_regularization=0.01,
                    early_stopping=True,
                    validation_fraction=0.1,
                    n_iter_no_change=25,
                    random_state=seed,
                ),
            ))
    et_n = 180 if quick else 360
    for depth, leaf in ([(16, 2)] if quick else [(16, 2), (24, 2), (None, 3)]):
        depth_name = "none" if depth is None else str(depth)
        candidates.append(Candidate(
            f"extra_trees_n_{et_n}_depth_{depth_name}_leaf_{leaf}",
            "learned",
            ExtraTreesRegressor(
                n_estimators=et_n,
                max_depth=depth,
                min_samples_leaf=leaf,
                n_jobs=-1,
                random_state=seed,
            ),
        ))
    rf_n = 120 if quick else 240
    candidates.append(Candidate(
        f"random_forest_n_{rf_n}_depth_18_leaf_3",
        "learned",
        RandomForestRegressor(
            n_estimators=rf_n,
            max_depth=18,
            min_samples_leaf=3,
            n_jobs=-1,
            random_state=seed,
        ),
    ))
    mlp_iters = 120 if quick else 240
    for layers in ([(96, 48)] if quick else [(128, 64), (96, 48)]):
        lname = "x".join(str(x) for x in layers)
        candidates.append(Candidate(
            f"mlp_{lname}",
            "learned",
            Pipeline([
                ("scale", StandardScaler()),
                ("model", MLPRegressor(
                    hidden_layer_sizes=layers,
                    activation="relu",
                    solver="adam",
                    alpha=1e-4,
                    batch_size=256,
                    learning_rate_init=8e-4,
                    max_iter=mlp_iters,
                    early_stopping=True,
                    validation_fraction=0.1,
                    n_iter_no_change=25,
                    random_state=seed,
                )),
            ]),
        ))
    return candidates


def evaluate_prediction(df: pd.DataFrame, pred: np.ndarray, candidate: Candidate) -> dict[str, Any]:
    pred = np.maximum(np.asarray(pred, dtype=float), 0.0)
    row: dict[str, Any] = {"candidate": candidate.name, "kind": candidate.kind}
    for split in ["validation", "test"]:
        mask = (df["split"] == split).to_numpy()
        row.update(metric_dict(df.loc[mask, "target_value"].to_numpy(), pred[mask], split))
        ng_mask = mask & (~df["is_gateway_outage"].fillna(False).to_numpy(dtype=bool))
        row.update(metric_dict(df.loc[ng_mask, "target_value"].to_numpy(), pred[ng_mask], f"{split}_non_gateway"))
    return row


def fit_and_predict(df: pd.DataFrame, candidate: Candidate, cols: list[str]) -> tuple[np.ndarray, Any | None, float]:
    if candidate.kind == "baseline":
        if candidate.prediction_col is None:
            raise RuntimeError("baseline candidate missing prediction_col")
        return df[candidate.prediction_col].to_numpy(dtype=float), None, 0.0

    estimator = clone(candidate.estimator)
    usable = (
        df["target_observed"].fillna(False)
        & df["is_full_component_observed"].fillna(False)
        & (~df["is_replacement_gap"].fillna(False))
    )
    train_mask = usable & (df["split"] == "train") & (~df["is_gateway_outage"].fillna(False))
    X_train = df.loc[train_mask, cols].to_numpy(dtype=float)
    y_train = df.loc[train_mask, "target_value"].to_numpy(dtype=float)
    start = time.time()
    estimator.fit(X_train, y_train)
    fit_seconds = time.time() - start
    pred = estimator.predict(df[cols].to_numpy(dtype=float))
    return pred, estimator, fit_seconds


def train_target(target: pd.DataFrame, feature: pd.DataFrame, target_id: str, out_dir: Path, seed: int, quick: bool) -> dict[str, Any]:
    target_out = out_dir / target_id
    target_out.mkdir(parents=True, exist_ok=True)
    df = make_target_frame(target, feature, target_id)
    cols = feature_columns()
    candidates = build_candidates(seed, quick=quick)
    rows: list[dict[str, Any]] = []
    estimators: dict[str, Any] = {}
    predictions: dict[str, np.ndarray] = {}

    for cand in candidates:
        pred, estimator, fit_seconds = fit_and_predict(df, cand, cols)
        row = evaluate_prediction(df, pred, cand)
        row.update({
            "target_id": target_id,
            "feature_count": len(cols),
            "fit_seconds": float(fit_seconds),
        })
        rows.append(row)
        predictions[cand.name] = np.maximum(pred, 0.0)
        if estimator is not None:
            estimators[cand.name] = estimator
        print(json.dumps({
            "target_id": target_id,
            "candidate": cand.name,
            "kind": cand.kind,
            "validation_non_gateway_mae": row.get("validation_non_gateway_mae"),
            "test_non_gateway_mae": row.get("test_non_gateway_mae"),
            "fit_seconds": round(float(fit_seconds), 2),
        }, ensure_ascii=False), flush=True)

    metrics = pd.DataFrame(rows)
    metrics["selection_priority"] = metrics["candidate"].map(selection_priority).astype(int)
    metrics = metrics.sort_values(["validation_non_gateway_mae", "selection_priority", "candidate"]).reset_index(drop=True)
    metrics.to_csv(target_out / "candidate_metrics.csv", index=False)

    # Champion selection uses a one-standard-error-style simplicity rule:
    # choose the simplest candidate within tolerance of the best validation MAE.
    # This avoids selecting an overfit MLP/tree when a simpler lag regression is statistically close.
    best_val = float(metrics["validation_non_gateway_mae"].min())
    tolerance = 1.03
    eligible = metrics[metrics["validation_non_gateway_mae"] <= best_val * tolerance].copy()
    eligible = eligible.sort_values(["selection_priority", "validation_non_gateway_mae", "candidate"]).reset_index(drop=True)
    champion = eligible.iloc[0].to_dict()
    champion["selection_best_validation_mae"] = best_val
    champion["selection_tolerance"] = tolerance
    champion["selection_rule"] = "simplest_candidate_within_3pct_of_best_validation_non_gateway_mae"
    champion_name = str(champion["candidate"])

    pred_cols = ["ts", "target_id", "target_version_id", "split", "target_value", "is_gateway_outage", "gateway_outage_name"]
    pred_df = df[pred_cols].copy()
    pred_df["prediction"] = predictions[champion_name]
    pred_df["champion_candidate"] = champion_name
    pred_df.to_parquet(target_out / "champion_predictions.parquet", index=False)

    if champion_name in estimators:
        joblib.dump({
            "target_id": target_id,
            "candidate": champion_name,
            "model": estimators[champion_name],
            "feature_columns": cols,
            "created_at_utc": utc_now(),
        }, target_out / "champion_model.joblib", compress=3)

    with (target_out / "feature_columns.json").open("w", encoding="utf-8") as f:
        json.dump(cols, f, ensure_ascii=False, indent=2)
    with (target_out / "champion_manifest.json").open("w", encoding="utf-8") as f:
        json.dump({
            "created_at_utc": utc_now(),
            "target_id": target_id,
            "target_label": TARGET_LABELS.get(target_id, target_id),
            "selection_metric": "validation_non_gateway_mae",
            "champion": champion,
            "feature_columns": cols,
        }, f, ensure_ascii=False, indent=2, default=str)
    return champion


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=Path("outputs/modeling/a_clean_targets_1h"))
    parser.add_argument("--out-dir", type=Path, default=Path("outputs/modeling/a_clean_champion_models_1h"))
    parser.add_argument("--target-id", action="append", default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--quick", action="store_true")
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

    champions = []
    for target_id in targets:
        champions.append(train_target(target, feature, target_id, out_dir, seed=args.seed, quick=args.quick))

    champ_df = pd.DataFrame(champions)
    champ_df.to_csv(out_dir / "champion_summary.csv", index=False)
    summary = {
        "created_at_utc": utc_now(),
        "run_label": "a_clean_champion_models_20260520",
        "branch": "exp/a-clean-champion-models-20260520",
        "dataset_dir": str(dataset_dir),
        "out_dir": str(out_dir),
        "targets": targets,
        "selection_metric": "validation_non_gateway_mae",
        "feature_policy": {
            "target_lags": LAGS,
            "rolling_windows": ROLLING_WINDOWS,
            "weather": "lag_1/3/24, 24h rolling mean, observed masks, causal ffill then train median fallback for input only",
            "calendar": CALENDAR_COLS + ["hour", "dow", "month", "is_weekend"],
            "temperature_features": ["CDD18", "CDD22", "HDD18"],
        },
        "candidate_count_per_target": len(build_candidates(args.seed, quick=args.quick)),
        "champions": champ_df.to_dict(orient="records"),
    }
    with (out_dir / "run_manifest.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps({"status": "ok", **summary}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
