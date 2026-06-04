from __future__ import annotations

import argparse
import html
import json
import os
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import matplotlib
import numpy as np
import pandas as pd
from catboost import CatBoostRegressor
from dotenv import load_dotenv
from lightgbm import LGBMRegressor, early_stopping, log_evaluation
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.multioutput import MultiOutputRegressor
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from xgboost import XGBRegressor

matplotlib.use("Agg")
import matplotlib.pyplot as plt


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_TABLE = "mart.peak_feature_15min"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "comparison_outputs"
WEATHER_METER_URN = "WeatherStation.Weather"

STEP_MINUTES = 15
INPUT_WINDOW_HOURS = [24, 48]
OUTPUT_HORIZON_STEPS = {"15min": 1, "60min": 4}
MAX_FIT_WINDOWS = 10_000
TRAIN_START = pd.Timestamp("2018-01-01 00:00:00", tz="UTC")
VAL_START = pd.Timestamp("2022-01-01 00:00:00", tz="UTC")
TEST_START = pd.Timestamp("2023-01-01 00:00:00", tz="UTC")
END_TS = pd.Timestamp("2024-01-01 00:00:00", tz="UTC")

FEATURE_COLUMNS = [
    "P_mean",
    "P_max",
    "P_std",
    "U1_mean",
    "PF_mean",
    "Ta_mean",
    "Igm_mean",
    "hour_sin",
    "hour_cos",
    "dayofweek_sin",
    "dayofweek_cos",
    "month_sin",
    "month_cos",
    "is_weekend",
]
TARGET_COLUMN = "P_max"

LOGICAL_METERS = {
    "V.Z81": [("V.Z81", 1)],
    "V.Z82": [("V.Z82", 1)],
    "H2.Z35x": [("H2.Z35", 1), ("H2.Z351", 2)],
    "H2.Z36x": [("H2.Z36", 1), ("H2.Z361", 2)],
}

TABLE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class DatasetBundle:
    x_train: np.ndarray
    y_train: np.ndarray
    x_val: np.ndarray
    y_val: np.ndarray
    x_test: np.ndarray
    y_test: np.ndarray
    meta_train: pd.DataFrame
    meta_val: pd.DataFrame
    meta_test: pd.DataFrame
    skipped_windows: int
    dropped_rows: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Forecast 15-minute P max from mart.peak_feature_15min.")
    parser.add_argument("--table", default=DEFAULT_TABLE)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--meters", nargs="*", default=list(LOGICAL_METERS), choices=list(LOGICAL_METERS))
    parser.add_argument("--input-hours", nargs="*", type=int, default=INPUT_WINDOW_HOURS, choices=INPUT_WINDOW_HOURS)
    parser.add_argument("--outputs", nargs="*", default=list(OUTPUT_HORIZON_STEPS), choices=list(OUTPUT_HORIZON_STEPS))
    parser.add_argument("--methods", nargs="*", default=["best_single", "v28", "v29", "v30"])
    parser.add_argument("--device", choices=["cpu", "gpu"], default="cpu")
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def validate_table_name(table_name: str) -> str:
    if not TABLE_NAME_PATTERN.fullmatch(table_name):
        raise ValueError(f"Invalid schema-qualified table name: {table_name!r}")
    return table_name


def build_engine():
    load_dotenv()
    db_host = os.getenv("DB_HOST", "localhost")
    db_port = os.getenv("DB_PORT", "5432")
    db_name = os.getenv("DB_NAME", "ems")
    db_user = os.getenv("DB_USER", "postgres")
    db_pass = os.getenv("DB_PASS", "")
    database_url = URL.create(
        "postgresql+psycopg2",
        username=db_user,
        password=db_pass,
        host=db_host,
        port=int(db_port),
        database=db_name,
    )
    return create_engine(database_url, pool_pre_ping=True)


def feature_sql(table_name: str, meter_urn: str) -> str:
    return f"""
WITH latest AS (
    SELECT DISTINCT ON (window_ts, meter_urn, measurement)
        window_ts,
        meter_urn,
        measurement,
        mean_value,
        max_value,
        std_value,
        coverage_ratio
    FROM {table_name}
    WHERE meter_urn = :meter_urn
      AND measurement IN ('P', 'U1', 'PF')
      AND window_ts >= :start_ts
      AND window_ts < :end_ts
    ORDER BY window_ts, meter_urn, measurement, created_at DESC, run_id DESC
)
SELECT
    window_ts,
    meter_urn,
    MAX(CASE WHEN measurement = 'P'  THEN mean_value END) AS "P_mean",
    MAX(CASE WHEN measurement = 'P'  THEN max_value END) AS "P_max",
    MAX(CASE WHEN measurement = 'P'  THEN std_value END) AS "P_std",
    MAX(CASE WHEN measurement = 'P'  THEN coverage_ratio END) AS "P_coverage",
    MAX(CASE WHEN measurement = 'U1' THEN mean_value END) AS "U1_mean",
    MAX(CASE WHEN measurement = 'U1' THEN coverage_ratio END) AS "U1_coverage",
    MAX(CASE WHEN measurement = 'PF' THEN mean_value END) AS "PF_mean",
    MAX(CASE WHEN measurement = 'PF' THEN coverage_ratio END) AS "PF_coverage"
FROM latest
GROUP BY window_ts, meter_urn
ORDER BY window_ts
"""


def weather_sql(table_name: str) -> str:
    return f"""
WITH latest AS (
    SELECT DISTINCT ON (window_ts, meter_urn, measurement)
        window_ts,
        meter_urn,
        measurement,
        mean_value,
        coverage_ratio
    FROM {table_name}
    WHERE meter_urn = :weather_meter_urn
      AND measurement IN ('Ta', 'Igm')
      AND window_ts >= :start_ts
      AND window_ts < :end_ts
    ORDER BY window_ts, meter_urn, measurement, created_at DESC, run_id DESC
)
SELECT
    window_ts,
    MAX(CASE WHEN measurement = 'Ta'  THEN mean_value END) AS "Ta_mean",
    MAX(CASE WHEN measurement = 'Ta'  THEN coverage_ratio END) AS "Ta_coverage",
    MAX(CASE WHEN measurement = 'Igm' THEN mean_value END) AS "Igm_mean",
    MAX(CASE WHEN measurement = 'Igm' THEN coverage_ratio END) AS "Igm_coverage"
FROM latest
GROUP BY window_ts
ORDER BY window_ts
"""


def read_sql_with_retry(engine, sql: str, params: dict[str, Any]) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            return pd.read_sql(text(sql), con=engine, params=params, parse_dates=["window_ts"])
        except Exception as exc:
            last_error = exc
            if attempt == 3:
                break
            time.sleep(5)
    assert last_error is not None
    raise last_error


def fetch_weather(engine, table_name: str) -> pd.DataFrame:
    weather = read_sql_with_retry(
        engine,
        weather_sql(table_name),
        {"weather_meter_urn": WEATHER_METER_URN, "start_ts": TRAIN_START, "end_ts": END_TS},
    )
    weather["window_ts"] = pd.to_datetime(weather["window_ts"], utc=True)
    for column in ["Ta_mean", "Igm_mean"]:
        weather[column] = pd.to_numeric(weather[column], errors="coerce")
    weather.loc[weather["Ta_mean"] < -273.15, "Ta_mean"] = np.nan
    weather.loc[weather["Igm_mean"] < 0, "Igm_mean"] = np.nan
    return weather.sort_values("window_ts").reset_index(drop=True)


def fetch_source_meter(engine, table_name: str, meter_urn: str, weather: pd.DataFrame) -> pd.DataFrame:
    meter = read_sql_with_retry(
        engine,
        feature_sql(table_name, meter_urn),
        {"meter_urn": meter_urn, "start_ts": TRAIN_START, "end_ts": END_TS},
    )
    meter["window_ts"] = pd.to_datetime(meter["window_ts"], utc=True)
    for column in ["P_mean", "P_max", "P_std", "U1_mean", "PF_mean"]:
        meter[column] = pd.to_numeric(meter[column], errors="coerce")
    meter.loc[meter["U1_mean"] <= 0, "U1_mean"] = np.nan
    meter.loc[meter["U1_mean"] > 1000, "U1_mean"] = np.nan
    meter.loc[meter["PF_mean"].abs() > 1.5, "PF_mean"] = np.nan
    merged = meter.merge(weather, on="window_ts", how="left")
    merged = merged.rename(columns={"window_ts": "ts"}).sort_values("ts").reset_index(drop=True)
    return merged


def add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    enriched = df.copy()
    minute_of_day = enriched["ts"].dt.hour * 60 + enriched["ts"].dt.minute
    dayofweek = enriched["ts"].dt.dayofweek
    month = enriched["ts"].dt.month
    enriched["hour_sin"] = np.sin(2 * np.pi * minute_of_day / (24 * 60))
    enriched["hour_cos"] = np.cos(2 * np.pi * minute_of_day / (24 * 60))
    enriched["dayofweek_sin"] = np.sin(2 * np.pi * dayofweek / 7)
    enriched["dayofweek_cos"] = np.cos(2 * np.pi * dayofweek / 7)
    enriched["month_sin"] = np.sin(2 * np.pi * (month - 1) / 12)
    enriched["month_cos"] = np.cos(2 * np.pi * (month - 1) / 12)
    enriched["is_weekend"] = dayofweek.isin([5, 6]).astype(float)
    return enriched


def fetch_logical_meter(engine, table_name: str, logical_meter: str, weather: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for source_meter, segment_id in LOGICAL_METERS[logical_meter]:
        df = fetch_source_meter(engine, table_name, source_meter, weather)
        df["logical_meter_id"] = logical_meter
        df["source_meter_urn"] = source_meter
        df["segment_id"] = segment_id
        frames.append(df)
    combined = pd.concat(frames, ignore_index=True)
    return add_time_features(combined.sort_values(["segment_id", "ts"]).reset_index(drop=True))


def split_name(target_ts: pd.Timestamp) -> str | None:
    if TRAIN_START <= target_ts < VAL_START:
        return "train"
    if VAL_START <= target_ts < TEST_START:
        return "val"
    if TEST_START <= target_ts < END_TS:
        return "test"
    return None


def iter_continuous_feature_segments(df: pd.DataFrame, window_size: int):
    expected_step = pd.Timedelta(minutes=STEP_MINUTES)
    required_columns = FEATURE_COLUMNS + [TARGET_COLUMN]
    for _, segment in df.groupby(["source_meter_urn", "segment_id"], sort=True):
        segment = segment.sort_values("ts").reset_index(drop=True)
        segment = segment.dropna(subset=required_columns).reset_index(drop=True)
        if segment.empty:
            continue
        continuity_group = segment["ts"].diff().ne(expected_step).cumsum()
        for _, continuous in segment.groupby(continuity_group, sort=True):
            if len(continuous) >= window_size + 1:
                yield continuous.reset_index(drop=True)


def flatten_windows(values: list[np.ndarray]) -> np.ndarray:
    stacked = np.stack(values)
    return stacked.reshape(stacked.shape[0], -1).astype(np.float32)


def build_windows(df: pd.DataFrame, input_hours: int, output_range: str) -> DatasetBundle:
    window_size = input_hours * 60 // STEP_MINUTES
    horizon_steps = OUTPUT_HORIZON_STEPS[output_range]
    buckets: dict[str, list[np.ndarray]] = {
        name: [] for name in ["train_x", "train_y", "val_x", "val_y", "test_x", "test_y"]
    }
    meta_rows: dict[str, list[dict[str, object]]] = {name: [] for name in ["train", "val", "test"]}
    skipped_windows = 0
    dropped_rows = int(df[FEATURE_COLUMNS + [TARGET_COLUMN]].isna().any(axis=1).sum())

    for segment in iter_continuous_feature_segments(df, window_size):
        feature_values = segment[FEATURE_COLUMNS].to_numpy(dtype=np.float32)
        target_values = segment[TARGET_COLUMN].to_numpy(dtype=np.float32)
        max_start = len(segment) - window_size - horizon_steps + 1
        for start_idx in range(max_start):
            input_end_idx = start_idx + window_size
            target_start_idx = input_end_idx
            target_end_idx = input_end_idx + horizon_steps
            target_ts = segment.loc[target_end_idx - 1, "ts"]
            bucket = split_name(target_ts)
            if bucket is None:
                continue
            x = feature_values[start_idx:input_end_idx]
            y = target_values[target_start_idx:target_end_idx]
            if np.isnan(x).any() or np.isnan(y).any():
                skipped_windows += 1
                continue
            buckets[f"{bucket}_x"].append(x)
            buckets[f"{bucket}_y"].append(y.astype(np.float32))
            meta_rows[bucket].append(
                {
                    "logical_meter_id": segment.loc[start_idx, "logical_meter_id"],
                    "source_meter_urn": segment.loc[start_idx, "source_meter_urn"],
                    "segment_id": int(segment.loc[start_idx, "segment_id"]),
                    "input_start_ts": segment.loc[start_idx, "ts"],
                    "input_end_ts": segment.loc[input_end_idx - 1, "ts"],
                    "target_start_ts": segment.loc[target_start_idx, "ts"],
                    "target_end_ts": target_ts,
                    "persistence_t_plus_1": float(segment.loc[input_end_idx - 1, TARGET_COLUMN]),
                }
            )

    def stack_y(name: str) -> np.ndarray:
        values = buckets[name]
        if not values:
            raise ValueError(f"No windows created for {name}")
        return np.stack(values).astype(np.float32)

    return DatasetBundle(
        x_train=flatten_windows(buckets["train_x"]),
        y_train=stack_y("train_y"),
        x_val=flatten_windows(buckets["val_x"]),
        y_val=stack_y("val_y"),
        x_test=flatten_windows(buckets["test_x"]),
        y_test=stack_y("test_y"),
        meta_train=pd.DataFrame(meta_rows["train"]),
        meta_val=pd.DataFrame(meta_rows["val"]),
        meta_test=pd.DataFrame(meta_rows["test"]),
        skipped_windows=skipped_windows,
        dropped_rows=dropped_rows,
    )


def fit_subset(x_train: np.ndarray, y_train: np.ndarray, seed: int) -> tuple[np.ndarray, np.ndarray]:
    if len(x_train) <= MAX_FIT_WINDOWS:
        return x_train, y_train
    rng = np.random.default_rng(seed)
    idx = np.sort(rng.choice(len(x_train), size=MAX_FIT_WINDOWS, replace=False))
    return x_train[idx], y_train[idx]


def predict_model(model: Any, x: np.ndarray) -> np.ndarray:
    if isinstance(model, list):
        return np.column_stack([estimator.predict(x) for estimator in model]).astype(np.float32)
    pred = model.predict(x)
    if pred.ndim == 1:
        pred = pred.reshape(-1, 1)
    return pred.astype(np.float32)


def make_v16(seed: int) -> MultiOutputRegressor:
    base = HistGradientBoostingRegressor(
        loss="squared_error",
        learning_rate=0.05,
        max_iter=50,
        max_leaf_nodes=15,
        l2_regularization=0.05,
        random_state=seed,
    )
    return MultiOutputRegressor(base)


def make_v20(seed: int) -> MultiOutputRegressor:
    base = LGBMRegressor(
        objective="regression",
        n_estimators=500,
        learning_rate=0.03,
        num_leaves=31,
        min_child_samples=40,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_lambda=1.0,
        n_jobs=-1,
        random_state=seed,
        verbosity=-1,
    )
    return MultiOutputRegressor(base, n_jobs=1)


def make_v25(seed: int, device: str) -> MultiOutputRegressor:
    params = {
        "objective": "reg:squarederror",
        "n_estimators": 500,
        "learning_rate": 0.03,
        "max_depth": 6,
        "min_child_weight": 5,
        "subsample": 0.85,
        "colsample_bytree": 0.85,
        "reg_lambda": 2.0,
        "tree_method": "hist",
        "n_jobs": -1,
        "random_state": seed,
        "verbosity": 0,
    }
    if device == "gpu":
        params["device"] = "cuda"
    return MultiOutputRegressor(XGBRegressor(**params), n_jobs=1)


def make_v27(seed: int, device: str) -> MultiOutputRegressor:
    params = {
        "loss_function": "RMSE",
        "iterations": 600,
        "learning_rate": 0.03,
        "depth": 6,
        "l2_leaf_reg": 5.0,
        "random_strength": 1.0,
        "random_state": seed,
        "verbose": False,
        "allow_writing_files": False,
    }
    if device == "gpu":
        params.update({"task_type": "GPU", "devices": "0"})
    else:
        params["thread_count"] = -1
    return MultiOutputRegressor(CatBoostRegressor(**params), n_jobs=1)


def train_v23(bundle: DatasetBundle, seed: int, x_fit: np.ndarray, y_fit: np.ndarray) -> list[LGBMRegressor]:
    models = []
    for target_idx in range(y_fit.shape[1]):
        model = LGBMRegressor(
            objective="regression",
            metric="rmse",
            n_estimators=2000,
            learning_rate=0.02,
            num_leaves=63,
            min_child_samples=30,
            subsample=0.9,
            colsample_bytree=0.9,
            reg_lambda=2.0,
            n_jobs=-1,
            random_state=seed + target_idx,
            verbosity=-1,
        )
        model.fit(
            x_fit,
            y_fit[:, target_idx],
            eval_set=[(bundle.x_val, bundle.y_val[:, target_idx])],
            eval_metric="rmse",
            callbacks=[early_stopping(stopping_rounds=100, verbose=False), log_evaluation(period=0)],
        )
        models.append(model)
    return models


CANDIDATE_VERSIONS = ["v16", "v20", "v23", "v25", "v27"]


def compute_metrics(actual: np.ndarray, pred: np.ndarray, split: str) -> dict[str, Any]:
    residual = actual - pred
    denom = np.where(np.abs(actual) < 1e-9, np.nan, np.abs(actual))
    return {
        "split": split,
        "mae": float(np.mean(np.abs(residual))),
        "rmse": float(np.sqrt(np.mean(residual**2))),
        "mape_pct": float(np.nanmean(np.abs(residual) / denom) * 100),
        "n_values": int(actual.size),
    }


def persistence_predictions(bundle: DatasetBundle, split: str) -> np.ndarray:
    meta = getattr(bundle, f"meta_{split}")
    actual = getattr(bundle, f"y_{split}")
    base = meta["persistence_t_plus_1"].to_numpy(dtype=np.float32)
    return np.repeat(base[:, None], actual.shape[1], axis=1).astype(np.float32)


def build_prediction_frame(meta: pd.DataFrame, actual: np.ndarray, pred: np.ndarray, persistence: np.ndarray) -> pd.DataFrame:
    rows = meta.copy()
    for idx in range(actual.shape[1]):
        step = idx + 1
        rows[f"actual_t_plus_{step}"] = actual[:, idx]
        rows[f"pred_t_plus_{step}"] = pred[:, idx]
        rows[f"persistence_t_plus_{step}"] = persistence[:, idx]
        rows[f"residual_t_plus_{step}"] = actual[:, idx] - pred[:, idx]
        rows[f"abs_error_t_plus_{step}"] = np.abs(actual[:, idx] - pred[:, idx])
        rows[f"persistence_abs_error_t_plus_{step}"] = np.abs(actual[:, idx] - persistence[:, idx])
    rows["anomaly_score_mae"] = np.mean(
        [rows[f"abs_error_t_plus_{idx + 1}"] for idx in range(actual.shape[1])], axis=0
    )
    return rows


def peak_metrics(predictions: pd.DataFrame) -> pd.DataFrame:
    actual_cols = [col for col in predictions.columns if col.startswith("actual_t_plus_")]
    pred_cols = [col for col in predictions.columns if col.startswith("pred_t_plus_")]
    persistence_cols = [col for col in predictions.columns if col.startswith("persistence_t_plus_")]
    actual = predictions[actual_cols].to_numpy(dtype=np.float64).reshape(-1)
    pred = predictions[pred_cols].to_numpy(dtype=np.float64).reshape(-1)
    pers = predictions[persistence_cols].to_numpy(dtype=np.float64).reshape(-1)
    rows: list[dict[str, Any]] = []
    for quantile in [0.95, 0.975, 0.99]:
        actual_threshold = float(np.nanquantile(actual, quantile))
        pred_threshold = float(np.nanquantile(pred, quantile))
        pers_threshold = float(np.nanquantile(pers, quantile))
        actual_peak = actual >= actual_threshold
        for name, values, threshold in [("model", pred, pred_threshold), ("persistence", pers, pers_threshold)]:
            candidate_peak = values >= threshold
            overlap = int(np.sum(actual_peak & candidate_peak))
            actual_count = int(np.sum(actual_peak))
            pred_count = int(np.sum(candidate_peak))
            precision = overlap / pred_count if pred_count else np.nan
            recall = overlap / actual_count if actual_count else np.nan
            f1 = 2 * precision * recall / (precision + recall) if precision + recall > 0 else np.nan
            rows.append(
                {
                    "metric": f"top_{int(round((1 - quantile) * 100))}pct_peak_capture",
                    "series": name,
                    "quantile": quantile,
                    "actual_threshold": actual_threshold,
                    "candidate_threshold": threshold,
                    "precision": precision,
                    "recall": recall,
                    "f1": f1,
                    "overlap_count": overlap,
                    "actual_peak_count": actual_count,
                    "candidate_peak_count": pred_count,
                }
            )

    t1 = predictions.copy()
    t1["target_end_ts"] = pd.to_datetime(t1["target_end_ts"], utc=True)
    t1["target_date"] = t1["target_end_ts"].dt.date
    model_errors = []
    persistence_errors = []
    for _, group in t1.groupby(["source_meter_urn", "segment_id", "target_date"], sort=False):
        if len(group) < 2:
            continue
        actual_row = group.loc[group["actual_t_plus_1"].idxmax()]
        pred_row = group.loc[group["pred_t_plus_1"].idxmax()]
        pers_row = group.loc[group["persistence_t_plus_1"].idxmax()]
        model_errors.append(abs((pd.Timestamp(actual_row["target_end_ts"]) - pd.Timestamp(pred_row["target_end_ts"])).total_seconds()) / 60)
        persistence_errors.append(
            abs((pd.Timestamp(actual_row["target_end_ts"]) - pd.Timestamp(pers_row["target_end_ts"])).total_seconds()) / 60
        )
    for name, values in [("model", model_errors), ("persistence", persistence_errors)]:
        if values:
            arr = np.array(values, dtype=np.float64)
            rows.append(
                {
                    "metric": "daily_t_plus_1_peak_time",
                    "series": name,
                    "mae_minutes": float(np.mean(arr)),
                    "median_minutes": float(np.median(arr)),
                    "within_15min_pct": float(np.mean(arr <= 15) * 100),
                    "within_30min_pct": float(np.mean(arr <= 30) * 100),
                    "within_60min_pct": float(np.mean(arr <= 60) * 100),
                    "days": int(len(arr)),
                }
            )
    return pd.DataFrame(rows)


def plot_outputs(output_dir: Path, predictions: pd.DataFrame, meter: str, method: str, output_range: str) -> None:
    plot_df = predictions.sort_values("target_end_ts").tail(7 * 24 * 4).copy()
    plot_df["target_end_ts"] = pd.to_datetime(plot_df["target_end_ts"], utc=True)
    plt.figure(figsize=(14, 5))
    plt.plot(plot_df["target_end_ts"], plot_df["actual_t_plus_1"], label="actual P_max", linewidth=1)
    plt.plot(plot_df["target_end_ts"], plot_df["pred_t_plus_1"], label="predicted P_max", linewidth=1)
    plt.plot(plot_df["target_end_ts"], plot_df["persistence_t_plus_1"], label="persistence", linewidth=1, alpha=0.65)
    plt.title(f"{meter} {method} {output_range}: actual vs predicted P_max")
    plt.xlabel("target_end_ts")
    plt.ylabel("P_max")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_dir / "actual_vs_predicted.png", dpi=150)
    plt.close()

    actual_q95 = plot_df["actual_t_plus_1"].quantile(0.95)
    pred_q95 = plot_df["pred_t_plus_1"].quantile(0.95)
    plt.figure(figsize=(14, 5))
    plt.plot(plot_df["target_end_ts"], plot_df["actual_t_plus_1"], label="actual P_max", linewidth=1)
    plt.plot(plot_df["target_end_ts"], plot_df["pred_t_plus_1"], label="predicted P_max", linewidth=1)
    plt.scatter(
        plot_df.loc[plot_df["actual_t_plus_1"] >= actual_q95, "target_end_ts"],
        plot_df.loc[plot_df["actual_t_plus_1"] >= actual_q95, "actual_t_plus_1"],
        color="#c43",
        s=12,
        label="actual top 5%",
    )
    plt.scatter(
        plot_df.loc[plot_df["pred_t_plus_1"] >= pred_q95, "target_end_ts"],
        plot_df.loc[plot_df["pred_t_plus_1"] >= pred_q95, "pred_t_plus_1"],
        color="#246",
        s=12,
        label="predicted top 5%",
    )
    plt.title(f"{meter} {method} {output_range}: peak-window focus")
    plt.xlabel("target_end_ts")
    plt.ylabel("P_max")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_dir / "peak_focus.png", dpi=150)
    plt.close()

    residual = predictions["actual_t_plus_1"] - predictions["pred_t_plus_1"]
    plt.figure(figsize=(8, 5))
    plt.hist(residual, bins=60)
    plt.title(f"{meter} {method} {output_range}: residual histogram")
    plt.xlabel("actual - predicted")
    plt.ylabel("count")
    plt.tight_layout()
    plt.savefig(output_dir / "residual_histogram.png", dpi=150)
    plt.close()


def save_html_report(
    output_dir: Path,
    method: str,
    meter: str,
    input_hours: int,
    output_range: str,
    metrics: pd.DataFrame,
    peak: pd.DataFrame,
    weights: pd.DataFrame | None,
    manifest: dict[str, Any],
) -> None:
    model_test = metrics[(metrics["split"] == "test") & (metrics["series"] == "model")].iloc[0]
    pers_test = metrics[(metrics["split"] == "test") & (metrics["series"] == "persistence")].iloc[0]
    rmse_improvement = (pers_test["rmse"] - model_test["rmse"]) / pers_test["rmse"] * 100 if pers_test["rmse"] else np.nan
    mae_improvement = (pers_test["mae"] - model_test["mae"]) / pers_test["mae"] * 100 if pers_test["mae"] else np.nan
    top5_model = peak[(peak["metric"] == "top_5pct_peak_capture") & (peak["series"] == "model")]
    top5_pers = peak[(peak["metric"] == "top_5pct_peak_capture") & (peak["series"] == "persistence")]
    daily = peak[peak["metric"] == "daily_t_plus_1_peak_time"]
    html_text = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>{html.escape(meter)} {html.escape(method)} P-max report</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 28px; line-height: 1.5; color: #222; }}
    table {{ border-collapse: collapse; margin: 16px 0; width: 100%; font-size: 13px; }}
    th, td {{ border: 1px solid #ccc; padding: 6px 8px; text-align: right; }}
    th:first-child, td:first-child {{ text-align: left; }}
    th {{ background: #f2f2f2; }}
    code {{ background: #f6f6f6; padding: 2px 4px; }}
    img {{ max-width: 100%; border: 1px solid #ddd; margin: 12px 0; }}
  </style>
</head>
<body>
  <h1>{html.escape(meter)} {html.escape(method)} P-max Forecast Report</h1>
  <p>이 모델은 <code>mart.peak_feature_15min</code>에서 15분 단위 <code>P.max_value</code>를 타깃으로 사용해 다음 {html.escape(output_range)} 피크 전력값을 예측합니다. 입력은 과거 {input_hours}시간의 15분 feature입니다.</p>
  <h2>Persistence 대비 성능</h2>
  <p>Persistence baseline은 직전 15분 <code>P_max</code>를 이후 예측값으로 반복하는 기준선입니다. Test RMSE 개선율은 <strong>{rmse_improvement:.2f}%</strong>, MAE 개선율은 <strong>{mae_improvement:.2f}%</strong>입니다.</p>
  <h2>Metrics</h2>
  {metrics.to_html(index=False)}
  <h2>Peak Metrics</h2>
  {peak.to_html(index=False)}
  <h2>Top 5% Peak Capture Summary</h2>
  {(pd.concat([top5_model, top5_pers]).to_html(index=False) if not top5_model.empty else "<p>Top 5% peak metric is unavailable.</p>")}
  <h2>Daily Peak Time Summary</h2>
  {(daily.to_html(index=False) if not daily.empty else "<p>Daily peak time metric is unavailable.</p>")}
  <h2>Ensemble Weights</h2>
  {(weights.to_html(index=False) if weights is not None else "<p>Single model method has no ensemble weights.</p>")}
  <h2>Manifest</h2>
  <pre>{html.escape(json.dumps(manifest, indent=2, ensure_ascii=False, default=str))}</pre>
  <h2>Plots</h2>
  <img src="actual_vs_predicted.png" alt="Actual vs predicted">
  <img src="peak_focus.png" alt="Peak focus">
  <img src="residual_histogram.png" alt="Residual histogram">
</body>
</html>
"""
    (output_dir / "report.html").write_text(html_text, encoding="utf-8")


def weight_table(method: str, versions: list[str], weights: np.ndarray, val_preds: dict[str, np.ndarray], actual: np.ndarray) -> pd.DataFrame:
    rows = []
    for weight, version in zip(weights, versions, strict=True):
        rows.append(
            {
                "method": method,
                "candidate_version": version,
                "validation_rmse": compute_metrics(actual, val_preds[version], "validation")["rmse"],
                "weight": float(weight),
            }
        )
    return pd.DataFrame(rows)


def write_method_readme(output_dir: Path, method: str) -> None:
    (output_dir / "README.md").write_text(
        f"""# {method}

This folder contains one P-max forecasting result.

Files:

- `metrics.csv`: train/validation/test metrics for the model and persistence baseline.
- `peak_metrics.csv`: top-peak capture and daily peak-time metrics.
- `validation_predictions.csv`: validation split predictions.
- `test_predictions.csv`: test split predictions.
- `actual_vs_predicted.png`: recent test-period actual/model/persistence plot.
- `peak_focus.png`: top 5% peak-focused plot.
- `residual_histogram.png`: one-step residual distribution.
- `manifest.json`: run configuration.
- `report.html`: detailed analysis report.
- `ensemble_weights.csv`: ensemble weights, only for ensemble methods.
""",
        encoding="utf-8",
    )


def write_method_artifacts(
    output_dir: Path,
    method: str,
    meter: str,
    input_hours: int,
    output_range: str,
    bundle: DatasetBundle,
    train_pred: np.ndarray,
    val_pred: np.ndarray,
    test_pred: np.ndarray,
    weights: pd.DataFrame | None,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    persistence_train = persistence_predictions(bundle, "train")
    persistence_val = persistence_predictions(bundle, "val")
    persistence_test = persistence_predictions(bundle, "test")
    metric_rows = []
    for split, actual, pred, pers in [
        ("train", bundle.y_train, train_pred, persistence_train),
        ("validation", bundle.y_val, val_pred, persistence_val),
        ("test", bundle.y_test, test_pred, persistence_test),
    ]:
        row = compute_metrics(actual, pred, split)
        row["series"] = "model"
        metric_rows.append(row)
        row = compute_metrics(actual, pers, split)
        row["series"] = "persistence"
        metric_rows.append(row)
    metrics = pd.DataFrame(metric_rows)
    val_predictions = build_prediction_frame(bundle.meta_val, bundle.y_val, val_pred, persistence_val)
    test_predictions = build_prediction_frame(bundle.meta_test, bundle.y_test, test_pred, persistence_test)
    peak = peak_metrics(test_predictions)
    metrics.to_csv(output_dir / "metrics.csv", index=False)
    peak.to_csv(output_dir / "peak_metrics.csv", index=False)
    val_predictions.to_csv(output_dir / "validation_predictions.csv", index=False)
    test_predictions.to_csv(output_dir / "test_predictions.csv", index=False)
    if weights is not None:
        weights.to_csv(output_dir / "ensemble_weights.csv", index=False)
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    plot_outputs(output_dir, test_predictions, meter, method, output_range)
    save_html_report(output_dir, method, meter, input_hours, output_range, metrics, peak, weights, manifest)
    write_method_readme(output_dir, method)

    model_test = metrics[(metrics["split"] == "test") & (metrics["series"] == "model")].iloc[0].to_dict()
    pers_test = metrics[(metrics["split"] == "test") & (metrics["series"] == "persistence")].iloc[0].to_dict()
    top5 = peak[(peak["metric"] == "top_5pct_peak_capture") & (peak["series"] == "model")]
    daily = peak[(peak["metric"] == "daily_t_plus_1_peak_time") & (peak["series"] == "model")]
    row = {
        **{f"model_{k}": v for k, v in model_test.items() if k not in {"split", "series"}},
        **{f"persistence_{k}": v for k, v in pers_test.items() if k not in {"split", "series"}},
        "method": method,
        "input_hours": input_hours,
        "output_range": output_range,
        "meter": meter,
    }
    row["rmse_improvement_pct"] = (
        (row["persistence_rmse"] - row["model_rmse"]) / row["persistence_rmse"] * 100
        if row["persistence_rmse"]
        else np.nan
    )
    row["mae_improvement_pct"] = (
        (row["persistence_mae"] - row["model_mae"]) / row["persistence_mae"] * 100 if row["persistence_mae"] else np.nan
    )
    if not top5.empty:
        row["top5_peak_f1"] = float(top5.iloc[0]["f1"])
        row["top5_peak_recall"] = float(top5.iloc[0]["recall"])
    if not daily.empty:
        row["daily_peak_time_mae_minutes"] = float(daily.iloc[0]["mae_minutes"])
    return row


def train_candidates(bundle: DatasetBundle, model_dir: Path, seed: int, device: str) -> tuple[dict[str, Any], pd.DataFrame]:
    x_fit, y_fit = fit_subset(bundle.x_train, bundle.y_train, seed)
    models: dict[str, Any] = {}
    rows = []
    factories = {
        "v16": lambda: make_v16(seed),
        "v20": lambda: make_v20(seed),
        "v25": lambda: make_v25(seed, device),
        "v27": lambda: make_v27(seed, device),
    }
    for version in ["v16", "v20", "v25", "v27"]:
        model = factories[version]()
        fit_device = device if version in {"v25", "v27"} else "cpu"
        try:
            model.fit(x_fit, y_fit)
        except Exception:
            if device != "gpu" or version not in {"v25", "v27"}:
                raise
            fit_device = "cpu_fallback"
            model = make_v25(seed, "cpu") if version == "v25" else make_v27(seed, "cpu")
            model.fit(x_fit, y_fit)
        models[version] = model
        joblib.dump(model, model_dir / f"{version}.joblib")
        rows.append(
            {
                "candidate_version": version,
                "fit_windows": int(len(x_fit)),
                "fit_device": fit_device,
                "validation_rmse": compute_metrics(bundle.y_val, predict_model(model, bundle.x_val), "validation")["rmse"],
            }
        )
    v23 = train_v23(bundle, seed, x_fit, y_fit)
    models["v23"] = v23
    joblib.dump(v23, model_dir / "v23.joblib")
    rows.append(
        {
            "candidate_version": "v23",
            "fit_windows": int(len(x_fit)),
            "fit_device": "cpu",
            "validation_rmse": compute_metrics(bundle.y_val, predict_model(v23, bundle.x_val), "validation")["rmse"],
        }
    )
    return models, pd.DataFrame(rows).sort_values("validation_rmse").reset_index(drop=True)


def prediction_stack(predictions: dict[str, np.ndarray], versions: list[str], row_idx: np.ndarray | None = None) -> np.ndarray:
    arrays = []
    for version in versions:
        pred = predictions[version]
        if row_idx is not None:
            pred = pred[row_idx]
        arrays.append(pred.reshape(-1))
    return np.stack(arrays, axis=1)


def actual_vector(actual: np.ndarray, row_idx: np.ndarray | None = None) -> np.ndarray:
    if row_idx is not None:
        actual = actual[row_idx]
    return actual.reshape(-1)


def compute_rmse(actual: np.ndarray, pred: np.ndarray) -> float:
    return float(np.sqrt(np.mean((actual - pred) ** 2)))


def inverse_rmse_weights(
    val_predictions: dict[str, np.ndarray], actual: np.ndarray, versions: list[str], row_idx: np.ndarray | None = None
) -> np.ndarray:
    raw = []
    actual_vec = actual_vector(actual, row_idx)
    for version in versions:
        pred = val_predictions[version]
        if row_idx is not None:
            pred = pred[row_idx]
        raw.append(1.0 / max(compute_rmse(actual_vec, pred.reshape(-1)), 1e-9) ** 2)
    weights = np.array(raw, dtype=np.float64)
    return weights / weights.sum()


def constrained_simplex_least_squares(pred: np.ndarray, actual: np.ndarray) -> tuple[np.ndarray, float]:
    n_candidates = pred.shape[1]
    best_weights = np.full(n_candidates, 1.0 / n_candidates, dtype=np.float64)
    best_mse = float(np.mean((pred @ best_weights - actual) ** 2))
    for mask in range(1, 1 << n_candidates):
        active = [idx for idx in range(n_candidates) if mask & (1 << idx)]
        x_active = pred[:, active]
        gram = x_active.T @ x_active
        rhs = x_active.T @ actual
        ones = np.ones(len(active), dtype=np.float64)
        kkt = np.block([[gram, ones[:, None]], [ones[None, :], np.zeros((1, 1))]])
        kkt_rhs = np.concatenate([rhs, np.array([1.0])])
        try:
            solution = np.linalg.solve(kkt, kkt_rhs)[: len(active)]
        except np.linalg.LinAlgError:
            solution = np.linalg.lstsq(kkt, kkt_rhs, rcond=None)[0][: len(active)]
        if np.any(solution < -1e-8):
            continue
        weights = np.zeros(n_candidates, dtype=np.float64)
        weights[active] = np.clip(solution, 0.0, None)
        weights = weights / weights.sum()
        mse = float(np.mean((pred @ weights - actual) ** 2))
        if mse < best_mse:
            best_mse = mse
            best_weights = weights
    return best_weights, float(np.sqrt(best_mse))


def v28_weights(val_predictions: dict[str, np.ndarray], actual: np.ndarray, versions: list[str]) -> np.ndarray:
    return inverse_rmse_weights(val_predictions, actual, versions)


def v29_weights(val_predictions: dict[str, np.ndarray], actual: np.ndarray, versions: list[str]) -> np.ndarray:
    stack = prediction_stack(val_predictions, versions)
    weights, _ = constrained_simplex_least_squares(stack, actual.reshape(-1))
    return weights


def v30_weights(val_predictions: dict[str, np.ndarray], actual: np.ndarray, meta: pd.DataFrame, versions: list[str]) -> np.ndarray:
    ordered = meta.copy()
    ordered["target_end_ts"] = pd.to_datetime(ordered["target_end_ts"], utc=True)
    ordered = ordered.sort_values("target_end_ts").reset_index()
    if len(ordered) < 4:
        return inverse_rmse_weights(val_predictions, actual, versions)
    midpoint = len(ordered) // 2
    fit_idx = ordered.iloc[:midpoint]["index"].to_numpy(dtype=int)
    tune_idx = ordered.iloc[midpoint:]["index"].to_numpy(dtype=int)
    inverse = inverse_rmse_weights(val_predictions, actual, versions, fit_idx)
    fit_stack = prediction_stack(val_predictions, versions, fit_idx)
    fit_actual = actual_vector(actual, fit_idx)
    stacked, _ = constrained_simplex_least_squares(fit_stack, fit_actual)
    tune_stack = prediction_stack(val_predictions, versions, tune_idx)
    tune_actual = actual_vector(actual, tune_idx)
    best_alpha = 0.0
    best_tune_rmse = float("inf")
    for alpha in np.linspace(0.0, 1.0, 21):
        weights = (1.0 - alpha) * inverse + alpha * stacked
        weights = weights / weights.sum()
        tune_rmse = compute_rmse(tune_actual, tune_stack @ weights)
        if tune_rmse < best_tune_rmse:
            best_alpha = float(alpha)
            best_tune_rmse = tune_rmse
    weights = (1.0 - best_alpha) * inverse + best_alpha * stacked
    return weights / weights.sum()


def weighted_predictions(predictions: dict[str, np.ndarray], versions: list[str], weights: np.ndarray) -> np.ndarray:
    out = np.zeros_like(next(iter(predictions.values())), dtype=np.float32)
    for version, weight in zip(versions, weights, strict=True):
        out += float(weight) * predictions[version]
    return out


def build_dataset_summary(bundle: DatasetBundle) -> pd.DataFrame:
    rows = []
    for split, x, y, meta in [
        ("train", bundle.x_train, bundle.y_train, bundle.meta_train),
        ("validation", bundle.x_val, bundle.y_val, bundle.meta_val),
        ("test", bundle.x_test, bundle.y_test, bundle.meta_test),
    ]:
        rows.append(
            {
                "split": split,
                "n_windows": int(len(x)),
                "n_targets": int(y.size),
                "start": meta["target_end_ts"].min() if not meta.empty else None,
                "end": meta["target_end_ts"].max() if not meta.empty else None,
                "target_mean": float(np.mean(y)),
                "target_max": float(np.max(y)),
            }
        )
    rows.append({"split": "dropped_rows", "n_windows": bundle.dropped_rows})
    rows.append({"split": "skipped_windows", "n_windows": bundle.skipped_windows})
    return pd.DataFrame(rows)


def select_best_single(candidate_metrics: pd.DataFrame) -> str:
    return str(candidate_metrics.sort_values("validation_rmse").iloc[0]["candidate_version"])


def run_one(
    engine,
    table_name: str,
    output_root: Path,
    meter: str,
    input_hours: int,
    output_range: str,
    weather: pd.DataFrame,
    methods: list[str],
    seed: int,
    device: str,
    data_cache: dict[str, pd.DataFrame],
) -> list[dict[str, Any]]:
    print(f"Running meter={meter}, input={input_hours}h, predict={output_range}", flush=True)
    meter_dir = output_root / f"input_{input_hours}h" / f"predict_{output_range}" / meter
    model_dir = meter_dir / "_candidate_models"
    model_dir.mkdir(parents=True, exist_ok=True)
    if meter not in data_cache:
        data_cache[meter] = fetch_logical_meter(engine, table_name, meter, weather)
    df = data_cache[meter]
    bundle = build_windows(df, input_hours, output_range)
    models, candidate_metrics = train_candidates(bundle, model_dir, seed, device)
    candidate_metrics.to_csv(meter_dir / "candidate_metrics.csv", index=False)
    build_dataset_summary(bundle).to_csv(meter_dir / "dataset_summary.csv", index=False)
    best_single = select_best_single(candidate_metrics)
    train_preds = {version: predict_model(model, bundle.x_train) for version, model in models.items()}
    val_preds = {version: predict_model(model, bundle.x_val) for version, model in models.items()}
    test_preds = {version: predict_model(model, bundle.x_test) for version, model in models.items()}
    manifest_common = {
        "table": table_name,
        "logical_meter": meter,
        "target": "measurement='P'.max_value as P_max",
        "input_hours": input_hours,
        "output_range": output_range,
        "horizon_steps": OUTPUT_HORIZON_STEPS[output_range],
        "step_minutes": STEP_MINUTES,
        "feature_columns": FEATURE_COLUMNS,
        "candidate_versions": CANDIDATE_VERSIONS,
        "best_single_candidate": best_single,
        "training_device": device,
        "max_fit_windows": MAX_FIT_WINDOWS,
        "split_policy": "train=2018-2021, validation=2022, test=2023 by target timestamp",
        "dropped_rows": bundle.dropped_rows,
        "skipped_windows": bundle.skipped_windows,
    }
    rows = []
    for method in methods:
        method_dir = meter_dir / method
        manifest = {**manifest_common, "method": method}
        if method == "best_single":
            version = best_single
            rows.append(
                write_method_artifacts(
                    method_dir,
                    method,
                    meter,
                    input_hours,
                    output_range,
                    bundle,
                    train_preds[version],
                    val_preds[version],
                    test_preds[version],
                    None,
                    {**manifest, "candidate_version": version},
                )
            )
            continue
        if method == "v28":
            weights = v28_weights(val_preds, bundle.y_val, CANDIDATE_VERSIONS)
        elif method == "v29":
            weights = v29_weights(val_preds, bundle.y_val, CANDIDATE_VERSIONS)
        elif method == "v30":
            weights = v30_weights(val_preds, bundle.y_val, bundle.meta_val, CANDIDATE_VERSIONS)
        else:
            raise ValueError(f"Unsupported method: {method}")
        weights_df = weight_table(method, CANDIDATE_VERSIONS, weights, val_preds, bundle.y_val)
        rows.append(
            write_method_artifacts(
                method_dir,
                method,
                meter,
                input_hours,
                output_range,
                bundle,
                weighted_predictions(train_preds, CANDIDATE_VERSIONS, weights),
                weighted_predictions(val_preds, CANDIDATE_VERSIONS, weights),
                weighted_predictions(test_preds, CANDIDATE_VERSIONS, weights),
                weights_df,
                manifest,
            )
        )
    return rows


def write_summary(rows: list[dict[str, Any]], output_dir: Path) -> None:
    if not rows:
        return
    df = pd.DataFrame(rows).sort_values(["input_hours", "output_range", "meter", "method"])
    df.to_csv(output_dir / "pmax_model_comparison_summary.csv", index=False)
    best_rmse = df.loc[df.groupby(["input_hours", "output_range", "meter"])["model_rmse"].idxmin()]
    best_rmse.to_csv(output_dir / "pmax_model_comparison_best_rmse.csv", index=False)
    best_improvement = df.loc[df.groupby(["input_hours", "output_range", "meter"])["rmse_improvement_pct"].idxmax()]
    best_improvement.to_csv(output_dir / "pmax_model_comparison_best_persistence_improvement.csv", index=False)
    html_text = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>P-max 모델 비교 요약</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 28px; line-height: 1.5; color: #222; }}
    table {{ border-collapse: collapse; margin: 16px 0; width: 100%; font-size: 13px; }}
    th, td {{ border: 1px solid #ccc; padding: 6px 8px; text-align: right; }}
    th:first-child, td:first-child {{ text-align: left; }}
    th {{ background: #f2f2f2; }}
  </style>
</head>
<body>
  <h1>15분 P-max 예측 모델 비교 요약</h1>
  <p>이 실험은 개별 logical meter의 다음 15분 피크 전력값, 즉 <code>P.max_value</code>를 예측합니다. Persistence baseline은 직전 15분 P_max 반복값입니다.</p>
  <h2>전체 결과</h2>
  {df.to_html(index=False)}
  <h2>조합별 Test RMSE 최저 모델</h2>
  {best_rmse.to_html(index=False)}
  <h2>조합별 Persistence 대비 RMSE 개선율 최고 모델</h2>
  {best_improvement.to_html(index=False)}
</body>
</html>
"""
    (output_dir / "pmax_model_comparison_summary.html").write_text(html_text, encoding="utf-8")


def write_root_readme(output_dir: Path) -> None:
    (output_dir / "README.md").write_text(
        """# P-max Model Comparison Outputs

This output folder contains model comparison artifacts for forecasting
`measurement='P'.max_value` from `mart.peak_feature_15min`.

Root files:

- `pmax_model_comparison_summary.csv`: all completed method rows.
- `pmax_model_comparison_best_rmse.csv`: best method by test RMSE for each meter/window/horizon.
- `pmax_model_comparison_best_persistence_improvement.csv`: best method by RMSE improvement over persistence.
- `pmax_model_comparison_summary.html`: readable summary report.

Nested folders follow:

`input_{hours}h/predict_{range}/{meter}/{method}/`
""",
        encoding="utf-8",
    )


def main() -> None:
    args = parse_args()
    table_name = validate_table_name(args.table)
    random.seed(args.seed)
    np.random.seed(args.seed)
    output_dir = args.output_dir or (SCRIPT_DIR / ("comparison_outputs_gpu" if args.device == "gpu" else "comparison_outputs"))
    output_dir.mkdir(parents=True, exist_ok=True)
    write_root_readme(output_dir)
    engine = build_engine()
    print(f"Fetching weather features from {table_name}", flush=True)
    weather = fetch_weather(engine, table_name)
    rows: list[dict[str, Any]] = []
    data_cache: dict[str, pd.DataFrame] = {}
    for input_hours in args.input_hours:
        for output_range in args.outputs:
            for meter in args.meters:
                rows.extend(
                    run_one(
                        engine=engine,
                        table_name=table_name,
                        output_root=output_dir,
                        meter=meter,
                        input_hours=input_hours,
                        output_range=output_range,
                        weather=weather,
                        methods=args.methods,
                        seed=args.seed,
                        device=args.device,
                        data_cache=data_cache,
                    )
                )
                write_summary(rows, output_dir)
    write_summary(rows, output_dir)
    print(f"Completed. Outputs written to {output_dir}", flush=True)


if __name__ == "__main__":
    main()
