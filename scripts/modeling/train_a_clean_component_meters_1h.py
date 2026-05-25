#!/usr/bin/env python3
"""Train A-clean component-meter one-hour-ahead forecast models.

Purpose:
- Decompose the four A-clean group targets into their component meters.
- Train independent next-hour P forecasters per component meter.
- Compare group-level direct forecasts against summed component-meter forecasts.

The script queries `ems.cr_measurement_1h` for component meter P values and uses the
existing A-clean feature cache for split/calendar/weather/outage context.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import psycopg
from sklearn.base import clone
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import RobustScaler, StandardScaler

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "outputs/modeling/a_clean_targets_1h"
DEFAULT_DIRECT_DIR = PROJECT_ROOT / "outputs/modeling/a_clean_champion_models_1h"
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs/modeling/a_clean_component_meters_1h"
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"

DEFAULT_TARGETS = [
    "T1_group__central_cooling__P",
    "T1_group__local_cooling__P",
    "T1_group__server_power__P",
    "T1_group__ventilation__P",
]

LAGS = [1, 2, 3, 6, 12, 24, 48, 72, 168]
ROLLING_WINDOWS = [3, 6, 12, 24, 168]
WEATHER_COLS = ["Ta", "Igm"]
CALENDAR_COLS = ["hour_sin", "hour_cos", "dow_sin", "dow_cos", "month_sin", "month_cos"]


@dataclass
class Candidate:
    name: str
    kind: str
    estimator: Any | None = None
    prediction_col: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train A-clean component-meter models and compare summed forecasts")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--direct-dir", type=Path, default=DEFAULT_DIRECT_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--targets", nargs="*", default=DEFAULT_TARGETS)
    parser.add_argument("--quick", action="store_true", help="Use a smaller candidate grid for smoke testing")
    parser.add_argument("--refresh-cache", action="store_true", help="Re-query DB even when meter_timeseries cache exists")
    parser.add_argument("--save-models", action="store_true", help="Persist selected component models with joblib")
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


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
    cols += [f"meter_lag_{lag}" for lag in LAGS]
    for window in ROLLING_WINDOWS:
        cols += [
            f"meter_roll_mean_{window}",
            f"meter_roll_std_{window}",
            f"meter_roll_min_{window}",
            f"meter_roll_max_{window}",
        ]
    for col in WEATHER_COLS:
        cols += [f"{col}_lag_1", f"{col}_lag_3", f"{col}_lag_24", f"{col}_roll_mean_24", f"{col}_observed_float"]
    cols += ["Ta_lag_1_cdd18", "Ta_lag_1_cdd22", "Ta_lag_1_hdd18", "Igm_lag_1_log1p"]
    cols += ["is_weekend", "hour", "dow", "month", "meter_observed_lag_1", "meter_observed_lag_24"]
    cols += CALENDAR_COLS
    return cols


def build_candidates(seed: int, quick: bool) -> list[Candidate]:
    candidates = [
        Candidate("last_value", "baseline", prediction_col="meter_lag_1"),
        Candidate("seasonal_24h", "baseline", prediction_col="meter_lag_24"),
        Candidate("seasonal_168h", "baseline", prediction_col="meter_lag_168"),
        Candidate("ridge_alpha_1", "learned", Pipeline([("scale", StandardScaler()), ("model", Ridge(alpha=1.0))])),
        Candidate("ridge_alpha_100", "learned", Pipeline([("scale", StandardScaler()), ("model", Ridge(alpha=100.0))])),
        Candidate("ridge_robust_alpha_1", "learned", Pipeline([("scale", RobustScaler()), ("model", Ridge(alpha=1.0))])),
        Candidate("hgb_iter_160_lr_0.06", "learned", HistGradientBoostingRegressor(max_iter=160, learning_rate=0.06, max_leaf_nodes=31, l2_regularization=0.01, random_state=seed)),
    ]
    if not quick:
        candidates += [
            Candidate("ridge_alpha_1000", "learned", Pipeline([("scale", StandardScaler()), ("model", Ridge(alpha=1000.0))])),
            Candidate("ridge_robust_alpha_10", "learned", Pipeline([("scale", RobustScaler()), ("model", Ridge(alpha=10.0))])),
            Candidate("hgb_iter_320_lr_0.04", "learned", HistGradientBoostingRegressor(max_iter=320, learning_rate=0.04, max_leaf_nodes=31, l2_regularization=0.01, random_state=seed)),
            Candidate("extra_trees_n_240_leaf_3", "learned", ExtraTreesRegressor(n_estimators=240, min_samples_leaf=3, max_features=0.8, n_jobs=-1, random_state=seed)),
        ]
    return candidates


def selection_priority(name: str) -> int:
    if name == "last_value":
        return 0
    if name.startswith("seasonal"):
        return 1
    if name.startswith("ridge"):
        return 2
    if name.startswith("hgb"):
        return 3
    if name.startswith("extra_trees"):
        return 4
    return 99


def load_component_map(metadata_path: Path, targets: list[str]) -> pd.DataFrame:
    metadata = pd.read_csv(metadata_path)
    rows: list[dict[str, Any]] = []
    for _, row in metadata[metadata["target_id"].isin(targets)].iterrows():
        meters = [m.strip() for m in str(row["all_expected_component_meters"]).split(";") if m.strip()]
        for order, meter in enumerate(meters, start=1):
            rows.append({
                "target_id": row["target_id"],
                "target_name": row.get("target_name"),
                "meter_urn": meter,
                "component_order": order,
                "component_count": len(meters),
            })
    if not rows:
        raise RuntimeError("No component meters found for requested targets")
    return pd.DataFrame(rows)


def db_conninfo() -> dict[str, str]:
    required = ["DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"Missing DB environment variables: {missing}")
    return {
        "host": os.environ["DB_HOST"],
        "port": os.environ.get("DB_PORT", "5432"),
        "dbname": os.environ["DB_NAME"],
        "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"],
    }


def query_meter_timeseries(meters: list[str], start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> pd.DataFrame:
    placeholders = ",".join(["%s"] * len(meters))
    sql = f"""
        SELECT ts, meter_urn, value::double precision AS meter_value
        FROM ems.cr_measurement_1h
        WHERE measurement = 'P'
          AND meter_urn IN ({placeholders})
          AND ts >= %s
          AND ts <= %s
        ORDER BY meter_urn, ts
    """
    params = [*meters, start_ts.to_pydatetime(), end_ts.to_pydatetime()]
    with psycopg.connect(**db_conninfo(), connect_timeout=15) as conn:
        return pd.read_sql_query(sql, conn, params=params)


def load_or_query_meter_cache(out_dir: Path, component_map: pd.DataFrame, feature: pd.DataFrame, refresh: bool) -> pd.DataFrame:
    cache_path = out_dir / "meter_timeseries_1h.parquet"
    if cache_path.exists() and not refresh:
        return pd.read_parquet(cache_path)
    meters = sorted(component_map["meter_urn"].unique())
    start_ts = pd.to_datetime(feature["ts"]).min()
    end_ts = pd.to_datetime(feature["ts"]).max()
    meter_long = query_meter_timeseries(meters, start_ts, end_ts)
    meter_long["ts"] = pd.to_datetime(meter_long["ts"], utc=True)
    meter_long.to_parquet(cache_path, index=False)
    return meter_long


def make_meter_frame(base: pd.DataFrame, meter_series: pd.DataFrame, meter_urn: str) -> pd.DataFrame:
    df = base.merge(meter_series[["ts", "meter_value"]], on="ts", how="left")
    df["meter_urn"] = meter_urn
    df["meter_observed"] = df["meter_value"].notna()
    shifted = df["meter_value"].shift(1)
    for lag in LAGS:
        df[f"meter_lag_{lag}"] = df["meter_value"].shift(lag)
    for window in ROLLING_WINDOWS:
        min_periods = max(2, min(window, window // 4))
        roll = shifted.rolling(window, min_periods=min_periods)
        df[f"meter_roll_mean_{window}"] = roll.mean()
        df[f"meter_roll_std_{window}"] = roll.std()
        df[f"meter_roll_min_{window}"] = roll.min()
        df[f"meter_roll_max_{window}"] = roll.max()
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
    df["meter_observed_lag_1"] = df["meter_observed"].shift(1).fillna(False).astype(float)
    df["meter_observed_lag_24"] = df["meter_observed"].shift(24).fillna(False).astype(float)
    ts = pd.to_datetime(df["ts"], utc=True)
    df["hour"] = ts.dt.hour.astype(float)
    df["dow"] = ts.dt.dayofweek.astype(float)
    df["month"] = ts.dt.month.astype(float)
    df["is_weekend"] = (ts.dt.dayofweek >= 5).astype(float)

    train_clean = (df["split"] == "train") & (~df["is_gateway_outage"].fillna(False)) & df["meter_observed"]
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


def finite_xy(df: pd.DataFrame, cols: list[str]) -> pd.Series:
    mask = df["meter_value"].notna()
    for col in cols:
        mask &= np.isfinite(df[col].to_numpy(dtype=float))
    return mask


def fit_meter(meter_urn: str, df: pd.DataFrame, candidates: list[Candidate], cols: list[str], save_model_path: Path | None) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    train_mask = (df["split"] == "train") & (~df["is_gateway_outage"].fillna(False)) & finite_xy(df, cols)
    val_mask = (df["split"] == "validation") & df["meter_value"].notna()
    test_mask = (df["split"] == "test") & df["meter_value"].notna()
    scored_mask = df["meter_value"].notna()
    if int(train_mask.sum()) < 500 or int(val_mask.sum()) < 100 or int(test_mask.sum()) < 100:
        pred = df[["ts", "split", "is_gateway_outage", "meter_urn", "meter_value", "meter_observed"]].copy()
        pred["prediction"] = np.nan
        return pd.DataFrame([{"meter_urn": meter_urn, "status": "skipped_insufficient_rows", "train_rows": int(train_mask.sum()), "validation_rows": int(val_mask.sum()), "test_rows": int(test_mask.sum())}]), pred, {}

    X_train = df.loc[train_mask, cols].to_numpy(dtype=float)
    y_train = df.loc[train_mask, "meter_value"].to_numpy(dtype=float)
    rows: list[dict[str, Any]] = []
    fitted: dict[str, Any] = {}
    for cand in candidates:
        t0 = time.time()
        pred_all = np.full(len(df), np.nan, dtype=float)
        try:
            if cand.kind == "baseline":
                pred_all = df[cand.prediction_col].to_numpy(dtype=float)  # type: ignore[index]
            else:
                model = clone(cand.estimator)
                model.fit(X_train, y_train)
                pred_all[scored_mask.to_numpy()] = model.predict(df.loc[scored_mask, cols].to_numpy(dtype=float))
                fitted[cand.name] = model
            rec = {"meter_urn": meter_urn, "candidate": cand.name, "kind": cand.kind, "status": "completed", "fit_seconds": round(time.time() - t0, 3), "train_rows": int(train_mask.sum())}
            for split, mask in [("validation", val_mask), ("test", test_mask)]:
                rec.update(metric_dict(df.loc[mask, "meter_value"].to_numpy(dtype=float), pred_all[mask.to_numpy()], split))
                ng_mask = mask & (~df["is_gateway_outage"].fillna(False))
                rec.update(metric_dict(df.loc[ng_mask, "meter_value"].to_numpy(dtype=float), pred_all[ng_mask.to_numpy()], f"{split}_non_gateway"))
            rows.append(rec)
        except Exception as e:  # keep batch resilient
            rows.append({"meter_urn": meter_urn, "candidate": cand.name, "kind": cand.kind, "status": f"failed:{type(e).__name__}", "error": str(e), "fit_seconds": round(time.time() - t0, 3), "train_rows": int(train_mask.sum())})
    metrics = pd.DataFrame(rows)
    ok = metrics[(metrics["status"] == "completed") & metrics["validation_non_gateway_mae"].notna()].copy()
    if ok.empty:
        pred = df[["ts", "split", "is_gateway_outage", "meter_urn", "meter_value", "meter_observed"]].copy()
        pred["prediction"] = np.nan
        return metrics, pred, {}
    best_mae = float(ok["validation_non_gateway_mae"].min())
    eligible = ok[ok["validation_non_gateway_mae"] <= best_mae * 1.03].copy()
    eligible["selection_priority"] = eligible["candidate"].map(selection_priority)
    chosen = eligible.sort_values(["selection_priority", "validation_non_gateway_mae", "candidate"]).iloc[0]
    chosen_name = str(chosen["candidate"])
    chosen_kind = str(chosen["kind"])
    pred_all = np.full(len(df), np.nan, dtype=float)
    if chosen_kind == "baseline":
        col = next(c.prediction_col for c in candidates if c.name == chosen_name)
        pred_all = df[col].to_numpy(dtype=float)  # type: ignore[index]
    else:
        model = fitted.get(chosen_name)
        if model is None:
            cand = next(c for c in candidates if c.name == chosen_name)
            model = clone(cand.estimator)
            model.fit(X_train, y_train)
        pred_all[scored_mask.to_numpy()] = model.predict(df.loc[scored_mask, cols].to_numpy(dtype=float))
        if save_model_path is not None:
            save_model_path.parent.mkdir(parents=True, exist_ok=True)
            joblib.dump(model, save_model_path)
    pred = df[["ts", "split", "is_gateway_outage", "gateway_outage_name", "meter_urn", "meter_value", "meter_observed"]].copy()
    pred["prediction"] = pred_all
    pred["selected_candidate"] = chosen_name
    chosen_info = chosen.to_dict()
    return metrics, pred, chosen_info


def aggregate_component_predictions(component_map: pd.DataFrame, target_ts: pd.DataFrame, meter_predictions: pd.DataFrame, direct_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    pred_long = component_map.merge(meter_predictions, on="meter_urn", how="left")
    agg = (
        pred_long.groupby(["target_id", "ts"], dropna=False)
        .agg(
            component_actual_sum=("meter_value", "sum"),
            component_prediction_sum=("prediction", "sum"),
            predicted_component_count=("prediction", lambda s: int(s.notna().sum())),
            observed_component_count_from_db=("meter_observed", "sum"),
            expected_component_count=("meter_urn", "nunique"),
        )
        .reset_index()
    )
    target_eval = target_ts.merge(agg, on=["target_id", "ts"], how="left")
    direct_frames = []
    for target_id in sorted(component_map["target_id"].unique()):
        p = direct_dir / target_id / "champion_predictions.parquet"
        if p.exists():
            d = pd.read_parquet(p)[["target_id", "target_version_id", "ts", "prediction", "champion_candidate"]].copy()
            d = d.rename(columns={"prediction": "direct_prediction", "champion_candidate": "direct_candidate"})
            direct_frames.append(d)
    if direct_frames:
        direct = pd.concat(direct_frames, ignore_index=True)
        direct["ts"] = pd.to_datetime(direct["ts"], utc=True)
        target_eval = target_eval.merge(direct, on=["target_id", "target_version_id", "ts"], how="left")
    else:
        target_eval["direct_prediction"] = np.nan
        target_eval["direct_candidate"] = None

    metric_rows: list[dict[str, Any]] = []
    for (target_id, target_version_id, split), g in target_eval.groupby(["target_id", "target_version_id", "split"], dropna=False):
        for pred_col, label in [("component_prediction_sum", "component_sum"), ("direct_prediction", "direct")]:
            rec = {"target_id": target_id, "target_version_id": target_version_id, "split": split, "forecast_source": label}
            usable = g["target_value"].notna() & g[pred_col].notna()
            rec.update(metric_dict(g.loc[usable, "target_value"].to_numpy(dtype=float), g.loc[usable, pred_col].to_numpy(dtype=float), "all"))
            ng = usable & (~g["is_gateway_outage"].fillna(False))
            rec.update(metric_dict(g.loc[ng, "target_value"].to_numpy(dtype=float), g.loc[ng, pred_col].to_numpy(dtype=float), "non_gateway"))
            rec["target_vs_component_actual_max_abs_diff"] = float((g["target_value"] - g["component_actual_sum"]).abs().max()) if g["component_actual_sum"].notna().any() else None
            metric_rows.append(rec)
    metric_df = pd.DataFrame(metric_rows)
    return target_eval, agg, metric_df


def main() -> None:
    args = parse_args()
    random.seed(args.seed)
    np.random.seed(args.seed)
    input_dir = args.input_dir.resolve()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.env_file:
        load_env(args.env_file)

    target_ts = pd.read_parquet(input_dir / "target_timeseries_1h.parquet")
    feature = pd.read_parquet(input_dir / "feature_timeseries_1h.parquet")
    target_ts["ts"] = pd.to_datetime(target_ts["ts"], utc=True)
    feature["ts"] = pd.to_datetime(feature["ts"], utc=True)
    targets = [t for t in args.targets if t in set(target_ts["target_id"])]
    if not targets:
        raise RuntimeError("No requested target IDs exist in target_timeseries_1h.parquet")
    target_ts = target_ts[target_ts["target_id"].isin(targets)].copy()
    component_map = load_component_map(input_dir / "target_metadata.csv", targets)
    component_map.to_csv(out_dir / "target_component_map.csv", index=False)

    meter_long = load_or_query_meter_cache(out_dir, component_map, feature, args.refresh_cache)
    meter_long["ts"] = pd.to_datetime(meter_long["ts"], utc=True)

    base_cols = ["ts", "split", "is_gateway_outage", "gateway_outage_name", *CALENDAR_COLS, "Ta", "Igm"]
    for col in ["Ta_observed", "Igm_observed"]:
        if col in feature.columns:
            base_cols.append(col)
    base = feature[base_cols].sort_values("ts").reset_index(drop=True)
    candidates = build_candidates(args.seed, args.quick)
    cols = feature_columns()

    metric_frames: list[pd.DataFrame] = []
    prediction_frames: list[pd.DataFrame] = []
    chosen_rows: list[dict[str, Any]] = []
    meters = sorted(component_map["meter_urn"].unique())
    for idx, meter_urn in enumerate(meters, start=1):
        t0 = time.time()
        series = meter_long[meter_long["meter_urn"] == meter_urn].sort_values("ts")
        frame = make_meter_frame(base, series, meter_urn)
        model_path = out_dir / "model_binaries" / meter_urn.replace(".", "_") / "component_model.joblib" if args.save_models else None
        metrics, predictions, chosen = fit_meter(meter_urn, frame, candidates, cols, model_path)
        metrics["meter_order"] = idx
        metrics["meter_elapsed_seconds"] = round(time.time() - t0, 3)
        metric_frames.append(metrics)
        prediction_frames.append(predictions)
        if chosen:
            chosen_rows.append(chosen)
        print(json.dumps({"meter": meter_urn, "order": idx, "total": len(meters), "chosen": chosen.get("candidate") if chosen else None, "elapsed_seconds": round(time.time() - t0, 2)}, ensure_ascii=False), flush=True)

    component_metrics = pd.concat(metric_frames, ignore_index=True)
    meter_predictions = pd.concat(prediction_frames, ignore_index=True)
    chosen_summary = pd.DataFrame(chosen_rows)
    component_metrics.to_csv(out_dir / "component_candidate_metrics.csv", index=False)
    chosen_summary.to_csv(out_dir / "component_selected_models.csv", index=False)
    meter_predictions.to_parquet(out_dir / "component_predictions.parquet", index=False)

    target_eval, component_agg, comparison_metrics = aggregate_component_predictions(component_map, target_ts, meter_predictions, args.direct_dir.resolve())
    target_eval.to_parquet(out_dir / "group_direct_vs_component_predictions.parquet", index=False)
    component_agg.to_parquet(out_dir / "component_prediction_sums.parquet", index=False)
    comparison_metrics.to_csv(out_dir / "group_direct_vs_component_metrics.csv", index=False)

    manifest = {
        "created_at_utc": utc_now(),
        "purpose": "A-clean group target decomposition into component-meter forecasts",
        "targets": targets,
        "component_meter_count": int(component_map["meter_urn"].nunique()),
        "candidate_count": len(candidates),
        "quick": bool(args.quick),
        "input_dir": str(input_dir),
        "direct_dir": str(args.direct_dir.resolve()),
        "out_dir": str(out_dir),
        "outputs": {},
    }
    for name in [
        "target_component_map.csv",
        "meter_timeseries_1h.parquet",
        "component_candidate_metrics.csv",
        "component_selected_models.csv",
        "component_predictions.parquet",
        "component_prediction_sums.parquet",
        "group_direct_vs_component_predictions.parquet",
        "group_direct_vs_component_metrics.csv",
    ]:
        p = out_dir / name
        manifest["outputs"][name] = {"bytes": p.stat().st_size, "sha256": sha256_file(p)}
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    test_summary = comparison_metrics[(comparison_metrics["split"] == "test")][[
        "target_id", "target_version_id", "forecast_source", "non_gateway_rows", "non_gateway_mae", "non_gateway_rmse"
    ]]
    print(json.dumps({
        "status": "ok",
        "out_dir": str(out_dir),
        "component_meter_count": manifest["component_meter_count"],
        "candidate_count": manifest["candidate_count"],
        "test_summary": test_summary.to_dict(orient="records"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
