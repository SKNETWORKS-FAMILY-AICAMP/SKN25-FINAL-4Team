#!/usr/bin/env python3
"""Build a 15-minute grid peak demand dataset for FEMS peak-risk experiments."""
from __future__ import annotations

import argparse
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
DEFAULT_OUT_DIR = PROJECT_ROOT / "outputs/modeling/grid_peak_demand_15min"
DEFAULT_ENV = PROJECT_ROOT / ".env"
START_TS = pd.Timestamp("2018-01-01 00:00:00+00:00")
END_TS = pd.Timestamp("2024-01-01 00:00:00+00:00")

SERIES = {
    "raw_grid_P": ("electricity", "total", "P"),
    "pv_P": ("electricity", "pv", "P"),
    "chp_P": ("electricity", "chp", "P"),
    "Ta": ("weather", "weather", "Ta"),
    "Igm": ("weather", "weather", "Igm"),
}

TOP_GRID_METER_COLUMNS = {
    "V.Z81": "grid_V_Z81_P",
    "V.Z82": "grid_V_Z82_P",
    "H2.Z35": "grid_H2_Z35_P",
    "H2.Z351": "grid_H2_Z351_P",
    "H2.Z36": "grid_H2_Z36_P",
    "H2.Z361": "grid_H2_Z361_P",
}
PV_CONTEXT_METER_COLUMNS = {
    "V.Z84": "pv_V_Z84_P",
    "H1.Z310": "pv_H1_Z310_P",
    "H2.Z311": "pv_H2_Z311_P",
}
TOP_GRID_COLUMNS = list(TOP_GRID_METER_COLUMNS.values())
H2_OLD_GRID_COLUMNS = ["grid_H2_Z35_P", "grid_H2_Z36_P"]
H2_NEW_GRID_COLUMNS = ["grid_H2_Z351_P", "grid_H2_Z361_P"]
V_GRID_COLUMNS = ["grid_V_Z81_P", "grid_V_Z82_P"]

# Offenbach Netzentgelt proxy rate used for monthly peak-increment cost proxy labels.
DEMAND_RATE_EUR_PER_KW = {
    2018: 87.38,
    2019: 100.01,
    2020: 109.77,
    2021: 111.62,
    2022: 16.31,
    2023: 17.53,
}
DEMAND_RATE_SOURCE = "Offenbach Energienetze Mittelspannung Lastgangmessung Leistungspreis, positive-import tariff-regime proxy"
H2_REPLACEMENT_GAP_START = pd.Timestamp("2020-09-09 11:45:00+00:00")
H2_REPLACEMENT_GAP_END = pd.Timestamp("2020-09-15 09:45:00+00:00")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--env-path", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--start-ts", default=START_TS.isoformat())
    parser.add_argument("--end-ts", default=END_TS.isoformat())
    parser.add_argument("--check-only", action="store_true")
    return parser.parse_args()


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connect() -> psycopg.Connection:
    keys = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"]
    missing = [key for key in keys if not os.environ.get(key)]
    if missing:
        raise RuntimeError(f"Missing DB environment keys: {', '.join(missing)}")
    conn = psycopg.connect(
        host=os.environ["DB_HOST"],
        port=os.environ["DB_PORT"],
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        connect_timeout=10,
    )
    conn.execute("SET statement_timeout = '180s'")
    conn.execute("SET TIME ZONE 'UTC'")
    return conn


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def query_reduced_series(conn: psycopg.Connection, start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> pd.DataFrame:
    series_keys = ["/".join(parts) for parts in SERIES.values()]
    sql = """
        SELECT ts, category, subcategory, measurement, value
        FROM ems.reduced_measurement_15min
        WHERE ts >= %(start_ts)s
          AND ts < %(end_ts)s
          AND category || '/' || subcategory || '/' || measurement = ANY(%(series_keys)s)
        ORDER BY ts, category, subcategory, measurement
    """
    with conn.cursor() as cur:
        cur.execute(sql, {"start_ts": start_ts.to_pydatetime(), "end_ts": end_ts.to_pydatetime(), "series_keys": series_keys})
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=["ts", "category", "subcategory", "measurement", "value"])


def query_meter_power(conn: psycopg.Connection, start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> pd.DataFrame:
    meter_urns = list(TOP_GRID_METER_COLUMNS) + list(PV_CONTEXT_METER_COLUMNS)
    sql = """
        SELECT ts, meter_urn, value
        FROM ems.cr_measurement_15min
        WHERE ts >= %(start_ts)s
          AND ts < %(end_ts)s
          AND measurement = 'P'
          AND meter_urn = ANY(%(meter_urns)s)
        ORDER BY ts, meter_urn
    """
    with conn.cursor() as cur:
        cur.execute(sql, {"start_ts": start_ts.to_pydatetime(), "end_ts": end_ts.to_pydatetime(), "meter_urns": meter_urns})
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=["ts", "meter_urn", "value"])


def source_series_check(rows: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    out_dir.mkdir(parents=True, exist_ok=True)
    if rows.empty:
        check = pd.DataFrame(columns=["series", "rows", "min_ts", "max_ts", "missing_value_rows", "negative_rows", "p95", "p99", "max"])
    else:
        rows = rows.copy()
        rows["ts"] = pd.to_datetime(rows["ts"], utc=True)
        rows["series"] = rows["category"] + "/" + rows["subcategory"] + "/" + rows["measurement"]
        check = (
            rows.groupby("series", dropna=False)
            .agg(
                rows=("value", "size"),
                min_ts=("ts", "min"),
                max_ts=("ts", "max"),
                missing_value_rows=("value", lambda s: int(s.isna().sum())),
                negative_rows=("value", lambda s: int((s < 0).sum())),
                p95=("value", lambda s: float(s.dropna().quantile(0.95)) if s.notna().any() else np.nan),
                p99=("value", lambda s: float(s.dropna().quantile(0.99)) if s.notna().any() else np.nan),
                max=("value", "max"),
            )
            .reset_index()
        )
    check.to_csv(out_dir / "source_series_check.csv", index=False)
    return check


def meter_power_check(rows: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    out_dir.mkdir(parents=True, exist_ok=True)
    if rows.empty:
        check = pd.DataFrame(columns=["meter_urn", "rows", "min_ts", "max_ts", "missing_value_rows", "negative_rows", "p95", "p99", "max"])
    else:
        rows = rows.copy()
        rows["ts"] = pd.to_datetime(rows["ts"], utc=True)
        check = (
            rows.groupby("meter_urn", dropna=False)
            .agg(
                rows=("value", "size"),
                min_ts=("ts", "min"),
                max_ts=("ts", "max"),
                missing_value_rows=("value", lambda s: int(s.isna().sum())),
                negative_rows=("value", lambda s: int((s < 0).sum())),
                p95=("value", lambda s: float(s.dropna().quantile(0.95)) if s.notna().any() else np.nan),
                p99=("value", lambda s: float(s.dropna().quantile(0.99)) if s.notna().any() else np.nan),
                max=("value", "max"),
            )
            .reset_index()
        )
    check.to_csv(out_dir / "meter_power_check.csv", index=False)
    return check


def build_source_frame(rows: pd.DataFrame, start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> pd.DataFrame:
    full_index = pd.date_range(start_ts, end_ts, freq="15min", inclusive="left")
    frame = pd.DataFrame({"ts": full_index})
    if rows.empty:
        for name in SERIES:
            frame[name] = np.nan
            frame[f"{name}_observed"] = False
        return frame

    rows = rows.copy()
    rows["ts"] = pd.to_datetime(rows["ts"], utc=True)
    rows["series_key"] = list(zip(rows["category"], rows["subcategory"], rows["measurement"]))
    key_to_name = {v: k for k, v in SERIES.items()}
    rows["name"] = rows["series_key"].map(key_to_name)
    wide = rows.pivot_table(index="ts", columns="name", values="value", aggfunc="first").reindex(full_index)
    for name in SERIES:
        # Electricity P rows are stored as W in the DB; model frame uses kW. Weather rows keep source units.
        scale = 1000.0 if name in {"raw_grid_P", "pv_P", "chp_P"} else 1.0
        frame[name] = wide[name].to_numpy(dtype="float64") / scale if name in wide else np.nan
        frame[f"{name}_observed"] = ~pd.isna(frame[name])
    return frame


def attach_meter_power_frame(frame: pd.DataFrame, meter_rows: pd.DataFrame, start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> pd.DataFrame:
    full_index = pd.date_range(start_ts, end_ts, freq="15min", inclusive="left")
    out = frame.copy()
    meter_columns = {**TOP_GRID_METER_COLUMNS, **PV_CONTEXT_METER_COLUMNS}
    if meter_rows.empty:
        for col in meter_columns.values():
            out[col] = np.nan
            out[f"{col}_observed"] = False
        return out

    rows = meter_rows.copy()
    rows["ts"] = pd.to_datetime(rows["ts"], utc=True)
    rows["name"] = rows["meter_urn"].map(meter_columns)
    wide = rows.pivot_table(index="ts", columns="name", values="value", aggfunc="first").reindex(full_index)
    for col in meter_columns.values():
        out[col] = wide[col].to_numpy(dtype="float64") / 1000.0 if col in wide else np.nan
        out[f"{col}_observed"] = ~pd.isna(out[col])
    return out


def future_max(series: pd.Series, steps: int) -> pd.Series:
    shifted = pd.concat([series.shift(-i) for i in range(1, steps + 1)], axis=1)
    values = shifted.max(axis=1, skipna=True)
    values[shifted.notna().sum(axis=1) < steps] = np.nan
    return values


def same_split_future_mask(split: pd.Series, steps: int) -> pd.Series:
    shifted = pd.concat([split.shift(-i) for i in range(1, steps + 1)], axis=1)
    same = shifted.eq(split, axis=0).all(axis=1)
    complete = shifted.notna().all(axis=1)
    return (same & complete).astype(bool)


def future_all_true_mask(flag: pd.Series, steps: int) -> pd.Series:
    shifted = pd.concat([flag.shift(-i) for i in range(1, steps + 1)], axis=1)
    return shifted.astype("boolean").fillna(False).all(axis=1)


def same_segment_future_mask(segment: pd.Series, steps: int) -> pd.Series:
    shifted = pd.concat([segment.shift(-i) for i in range(1, steps + 1)], axis=1)
    same = shifted.eq(segment, axis=0).all(axis=1)
    complete = shifted.notna().all(axis=1) & segment.notna()
    return (same & complete).astype(bool)


def add_site_grid_context(df: pd.DataFrame) -> pd.DataFrame:
    df["raw_grid_P"] = pd.to_numeric(df["raw_grid_P"], errors="coerce")
    has_top_grid = all(col in df.columns for col in TOP_GRID_COLUMNS)
    if has_top_grid:
        for col in TOP_GRID_COLUMNS:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if not has_top_grid:
        observed = df.get("raw_grid_P_observed", df["raw_grid_P"].notna()).astype(bool)
        replacement_gap = df["ts"].between(H2_REPLACEMENT_GAP_START, H2_REPLACEMENT_GAP_END, inclusive="both")
        df["h2_grid_signed_P"] = np.nan
        df["h2_grid_transformer_gap"] = False
        df["h2_grid_replacement_gap"] = replacement_gap
        df["h2_reverse_flow_context"] = False
        df["site_grid_signed_P"] = df["raw_grid_P"]
        df["site_grid_import_P"] = df["site_grid_signed_P"].clip(lower=0)
        df["site_grid_transformer_complete"] = observed
        df["site_grid_import_clipped"] = df["site_grid_signed_P"] < 0
        df["site_grid_signed_P_negative"] = df["site_grid_signed_P"] < 0
        df["reduced_total_vs_transformer_sum_delta_kw"] = np.nan
    else:
        v_complete = df[V_GRID_COLUMNS].notna().all(axis=1)
        old_h2_complete = df[H2_OLD_GRID_COLUMNS].notna().all(axis=1)
        new_h2_complete = df[H2_NEW_GRID_COLUMNS].notna().all(axis=1)
        h2_complete = old_h2_complete | new_h2_complete
        replacement_gap = df["ts"].between(H2_REPLACEMENT_GAP_START, H2_REPLACEMENT_GAP_END, inclusive="both")
        df["h2_grid_transformer_gap"] = ~h2_complete
        df["h2_grid_replacement_gap"] = replacement_gap & ~h2_complete
        df["h2_grid_signed_P"] = df[H2_OLD_GRID_COLUMNS + H2_NEW_GRID_COLUMNS].sum(axis=1, min_count=2)
        df.loc[~h2_complete, "h2_grid_signed_P"] = np.nan
        df["h2_reverse_flow_context"] = df["h2_grid_signed_P"] < 0
        df["site_grid_transformer_complete"] = v_complete & h2_complete
        transformer_sum = df[TOP_GRID_COLUMNS].sum(axis=1, min_count=4)
        df["site_grid_signed_P"] = transformer_sum.where(df["site_grid_transformer_complete"])
        df["site_grid_import_P"] = df["site_grid_signed_P"].clip(lower=0)
        df["site_grid_import_clipped"] = df["site_grid_signed_P"] < 0
        df["site_grid_signed_P_negative"] = df["site_grid_signed_P"] < 0
        df["reduced_total_vs_transformer_sum_delta_kw"] = df["raw_grid_P"] - df["site_grid_signed_P"]

    df["grid_import_P"] = df["site_grid_import_P"]
    valid = df["site_grid_transformer_complete"].fillna(False).astype(bool)
    start_of_segment = valid & ~valid.shift(fill_value=False)
    segment = start_of_segment.cumsum().astype("Int64")
    df["sequence_segment_id"] = segment.where(valid, pd.NA)
    return df


def segment_shift(df: pd.DataFrame, col: str, lag: int) -> pd.Series:
    return df.groupby("sequence_segment_id", dropna=True)[col].shift(lag)


def segment_rolling(df: pd.DataFrame, col: str, window: int, agg: str) -> pd.Series:
    grouped = df.groupby("sequence_segment_id", dropna=True)[col]
    if agg == "mean":
        return grouped.transform(lambda s: s.shift(1).rolling(window, min_periods=window).mean())
    if agg == "max":
        return grouped.transform(lambda s: s.shift(1).rolling(window, min_periods=window).max())
    raise ValueError(f"Unsupported rolling agg: {agg}")


def add_split(df: pd.DataFrame) -> pd.DataFrame:
    ts = pd.to_datetime(df["ts"], utc=True)
    df["split"] = np.select(
        [
            (ts >= pd.Timestamp("2018-01-01", tz="UTC")) & (ts < pd.Timestamp("2022-01-01", tz="UTC")),
            (ts >= pd.Timestamp("2022-01-01", tz="UTC")) & (ts < pd.Timestamp("2023-01-01", tz="UTC")),
            (ts >= pd.Timestamp("2023-01-01", tz="UTC")) & (ts < pd.Timestamp("2024-01-01", tz="UTC")),
        ],
        ["train", "validation", "test"],
        default="exclude",
    )
    return df


def add_calendar(df: pd.DataFrame) -> pd.DataFrame:
    ts = pd.to_datetime(df["ts"], utc=True)
    hour = ts.dt.hour + ts.dt.minute / 60.0
    dow = ts.dt.dayofweek
    month0 = ts.dt.month - 1
    df["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    df["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    df["dow_sin"] = np.sin(2 * np.pi * dow / 7)
    df["dow_cos"] = np.cos(2 * np.pi * dow / 7)
    df["month_sin"] = np.sin(2 * np.pi * month0 / 12)
    df["month_cos"] = np.cos(2 * np.pi * month0 / 12)
    df["is_weekend"] = dow.isin([5, 6])
    df["is_working_hour"] = (~df["is_weekend"]) & (hour >= 8) & (hour < 18)
    return df


def build_model_frame(source: pd.DataFrame) -> pd.DataFrame:
    df = source.copy().sort_values("ts").reset_index(drop=True)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = add_site_grid_context(df)
    df = add_split(df)

    df["pv_available"] = df.get("pv_P_observed", df["pv_P"].notna()).astype(bool)
    df["chp_available"] = df.get("chp_P_observed", df["chp_P"].notna()).astype(bool)
    df["weather_available"] = (df.get("Ta_observed", df["Ta"].notna()).astype(bool) & df.get("Igm_observed", df["Igm"].notna()).astype(bool))
    df["source_complete_row"] = df["site_grid_transformer_complete"].astype(bool)

    for lag in [1, 2, 4, 8, 16, 96, 672]:
        df[f"grid_import_P_lag_{lag}"] = segment_shift(df, "grid_import_P", lag)
    for window, label in [(4, "1h"), (16, "4h"), (96, "24h")]:
        df[f"grid_import_P_roll_{label}_mean"] = segment_rolling(df, "grid_import_P", window, "mean")
        df[f"grid_import_P_roll_{label}_max"] = segment_rolling(df, "grid_import_P", window, "max")

    for name in ["Ta", "Igm", "pv_P", "chp_P"]:
        for lag in [1, 4]:
            df[f"{name}_lag_{lag}"] = segment_shift(df, name, lag)
    df["onsite_generation_proxy"] = -(df["pv_P"].fillna(0) + df["chp_P"].fillna(0))
    df["onsite_generation_proxy_observed"] = (df["pv_available"] & df["chp_available"]).astype(bool)
    df["onsite_generation_proxy_lag_1"] = segment_shift(df, "onsite_generation_proxy", 1)

    month_key = df["ts"].dt.strftime("%Y-%m")
    year_key = df["ts"].dt.year
    df["current_month_peak_kw"] = df.groupby(month_key)["grid_import_P"].cummax()
    df["current_year_peak_kw"] = df.groupby(year_key)["grid_import_P"].cummax()
    df["headroom_to_month_peak_kw"] = df["current_month_peak_kw"] - df["grid_import_P"]
    df["headroom_to_year_peak_kw"] = df["current_year_peak_kw"] - df["grid_import_P"]

    full_p95 = float(df["grid_import_P"].quantile(0.95))
    full_p99 = float(df["grid_import_P"].quantile(0.99))
    train_grid_import = df.loc[df["split"] == "train", "grid_import_P"].dropna()
    threshold_basis = train_grid_import if not train_grid_import.empty else df["grid_import_P"].dropna()
    train_p95 = float(threshold_basis.quantile(0.95))
    train_p99 = float(threshold_basis.quantile(0.99))
    df["is_above_train_p95"] = df["grid_import_P"] >= train_p95
    df["is_above_train_p99"] = df["grid_import_P"] >= train_p99
    df["is_near_month_peak_25kw"] = df["headroom_to_month_peak_kw"] <= 25

    df["target_grid_import_P_t_plus_1_ts"] = df["ts"].shift(-1)
    df["target_grid_import_P_t_plus_4_ts"] = df["ts"].shift(-4)
    df["target_next_1h_window_end_ts"] = df["ts"].shift(-4)
    df["target_next_4h_window_end_ts"] = df["ts"].shift(-16)
    df["target_grid_import_P_t_plus_1_same_split"] = same_split_future_mask(df["split"], 1)
    df["target_grid_import_P_t_plus_4_same_split"] = same_split_future_mask(df["split"], 4)
    df["target_next_1h_same_split"] = same_split_future_mask(df["split"], 4)
    df["target_next_4h_same_split"] = same_split_future_mask(df["split"], 16)

    origin_valid = df["site_grid_transformer_complete"].fillna(False).astype(bool)
    df["target_grid_import_P_t_plus_1_valid"] = origin_valid & df["target_grid_import_P_t_plus_1_same_split"] & same_segment_future_mask(df["sequence_segment_id"], 1) & future_all_true_mask(origin_valid, 1)
    df["target_grid_import_P_t_plus_4_valid"] = origin_valid & df["target_grid_import_P_t_plus_4_same_split"] & same_segment_future_mask(df["sequence_segment_id"], 4) & future_all_true_mask(origin_valid, 4)
    df["target_next_1h_valid"] = origin_valid & df["target_next_1h_same_split"] & same_segment_future_mask(df["sequence_segment_id"], 4) & future_all_true_mask(origin_valid, 4)
    df["target_next_4h_valid"] = origin_valid & df["target_next_4h_same_split"] & same_segment_future_mask(df["sequence_segment_id"], 16) & future_all_true_mask(origin_valid, 16)

    df["target_grid_import_P_t_plus_1"] = df["grid_import_P"].shift(-1).where(df["target_grid_import_P_t_plus_1_valid"])
    df["target_grid_import_P_t_plus_4"] = df["grid_import_P"].shift(-4).where(df["target_grid_import_P_t_plus_4_valid"])
    df["target_next_1h_max_grid_import_P"] = future_max(df["grid_import_P"], 4).where(df["target_next_1h_valid"])
    df["target_next_4h_max_grid_import_P"] = future_max(df["grid_import_P"], 16).where(df["target_next_4h_valid"])

    for label, target_col in [("1h", "target_next_1h_max_grid_import_P"), ("4h", "target_next_4h_max_grid_import_P")]:
        exceed_col = f"target_exceed_month_peak_next_{label}"
        inc_col = f"target_peak_increment_kw_next_{label}"
        cost_col = f"target_month_peak_increment_cost_proxy_eur_next_{label}"
        increment = (df[target_col] - df["current_month_peak_kw"]).clip(lower=0)
        df[exceed_col] = (increment > 0).astype("boolean")
        df.loc[df[target_col].isna(), exceed_col] = pd.NA
        df[inc_col] = increment
        df.loc[df[target_col].isna(), inc_col] = np.nan
        rate = df["ts"].dt.year.map(DEMAND_RATE_EUR_PER_KW).astype("float64")
        df[cost_col] = df[inc_col] * rate

    df = add_calendar(df)
    df.attrs["threshold_fit_scope"] = "train_split_only"
    df.attrs["full_period_p95_grid_import_P"] = full_p95
    df.attrs["full_period_p99_grid_import_P"] = full_p99
    df.attrs["train_p95_grid_import_P"] = train_p95
    df.attrs["train_p99_grid_import_P"] = train_p99
    return df


def write_summaries(df: pd.DataFrame, out_dir: Path, full_p95: float, full_p99: float, train_p95: float, train_p99: float) -> dict[str, Any]:
    split_summary = df.groupby("split", dropna=False).agg(
        rows=("ts", "size"),
        min_ts=("ts", "min"),
        max_ts=("ts", "max"),
        mean_grid_import_P=("grid_import_P", "mean"),
        max_grid_import_P=("grid_import_P", "max"),
        exceed_1h_rows=("target_exceed_month_peak_next_1h", lambda s: int(s.fillna(False).sum())),
        exceed_4h_rows=("target_exceed_month_peak_next_4h", lambda s: int(s.fillna(False).sum())),
    ).reset_index()
    label_summary = pd.DataFrame([
        {"label": "target_grid_import_P_t_plus_1", "non_null_rows": int(df["target_grid_import_P_t_plus_1"].notna().sum()), "mean": float(df["target_grid_import_P_t_plus_1"].mean()), "max": float(df["target_grid_import_P_t_plus_1"].max())},
        {"label": "target_next_1h_max_grid_import_P", "non_null_rows": int(df["target_next_1h_max_grid_import_P"].notna().sum()), "mean": float(df["target_next_1h_max_grid_import_P"].mean()), "max": float(df["target_next_1h_max_grid_import_P"].max())},
        {"label": "target_next_4h_max_grid_import_P", "non_null_rows": int(df["target_next_4h_max_grid_import_P"].notna().sum()), "mean": float(df["target_next_4h_max_grid_import_P"].mean()), "max": float(df["target_next_4h_max_grid_import_P"].max())},
    ])
    event_summary = pd.DataFrame([
        {"event": "full_period_p95_grid_import_P", "threshold_kw": full_p95, "threshold_scope": "full_period_descriptive", "rows": int((df["grid_import_P"] >= full_p95).sum()), "hours": float((df["grid_import_P"] >= full_p95).sum() * 0.25)},
        {"event": "full_period_p99_grid_import_P", "threshold_kw": full_p99, "threshold_scope": "full_period_descriptive", "rows": int((df["grid_import_P"] >= full_p99).sum()), "hours": float((df["grid_import_P"] >= full_p99).sum() * 0.25)},
        {"event": "train_p95_grid_import_P", "threshold_kw": train_p95, "threshold_scope": "train_split_only", "rows": int((df["grid_import_P"] >= train_p95).sum()), "hours": float((df["grid_import_P"] >= train_p95).sum() * 0.25)},
        {"event": "train_p99_grid_import_P", "threshold_kw": train_p99, "threshold_scope": "train_split_only", "rows": int((df["grid_import_P"] >= train_p99).sum()), "hours": float((df["grid_import_P"] >= train_p99).sum() * 0.25)},
        {"event": "month_peak_exceed_next_1h", "threshold_kw": np.nan, "threshold_scope": "monthly_current_peak", "rows": int(df["target_exceed_month_peak_next_1h"].fillna(False).sum()), "hours": np.nan},
        {"event": "month_peak_exceed_next_4h", "threshold_kw": np.nan, "threshold_scope": "monthly_current_peak", "rows": int(df["target_exceed_month_peak_next_4h"].fillna(False).sum()), "hours": np.nan},
    ])
    missing = pd.DataFrame([{"column": col, "missing_rows": int(df[col].isna().sum()), "missing_share": float(df[col].isna().mean())} for col in df.columns])

    split_summary.to_csv(out_dir / "grid_peak_split_summary.csv", index=False)
    label_summary.to_csv(out_dir / "grid_peak_label_summary.csv", index=False)
    event_summary.to_csv(out_dir / "grid_peak_event_summary.csv", index=False)
    audit_dir = out_dir / "audits"
    audit_dir.mkdir(parents=True, exist_ok=True)
    missing.to_csv(audit_dir / "feature_missingness_summary.csv", index=False)
    if {"site_grid_transformer_complete", "h2_grid_transformer_gap", "h2_reverse_flow_context"}.issubset(df.columns):
        completeness = pd.DataFrame([
            {"metric": "rows", "value": int(len(df))},
            {"metric": "site_grid_transformer_complete_rows", "value": int(df["site_grid_transformer_complete"].fillna(False).sum())},
            {"metric": "h2_grid_transformer_gap_rows", "value": int(df["h2_grid_transformer_gap"].fillna(False).sum())},
            {"metric": "h2_grid_replacement_gap_rows", "value": int(df.get("h2_grid_replacement_gap", pd.Series(False, index=df.index)).fillna(False).sum())},
            {"metric": "h2_reverse_flow_context_rows", "value": int(df["h2_reverse_flow_context"].fillna(False).sum())},
            {"metric": "site_grid_signed_P_negative_rows", "value": int(df["site_grid_signed_P_negative"].fillna(False).sum())},
            {"metric": "site_grid_import_clipped_rows", "value": int(df["site_grid_import_clipped"].fillna(False).sum())},
        ])
        completeness.to_csv(audit_dir / "grid_transformer_completeness_check.csv", index=False)
        gap_rows = df.loc[df["h2_grid_transformer_gap"].fillna(False), ["ts", "split", "raw_grid_P", "site_grid_signed_P", "grid_import_P", "sequence_segment_id"]]
        gap_rows.to_csv(audit_dir / "h2_grid_gap_check.csv", index=False)
        replacement_gap_rows = df.loc[df.get("h2_grid_replacement_gap", pd.Series(False, index=df.index)).fillna(False), ["ts", "split", "raw_grid_P", "site_grid_signed_P", "grid_import_P", "sequence_segment_id"]]
        replacement_gap_rows.to_csv(audit_dir / "h2_grid_replacement_gap_check.csv", index=False)
        reverse_cols = ["ts", "split", "h2_grid_signed_P", "site_grid_signed_P", "grid_import_P", "sequence_segment_id"]
        df.loc[df["h2_reverse_flow_context"].fillna(False), reverse_cols].to_csv(audit_dir / "reverse_flow_context_check.csv", index=False)
        if "reduced_total_vs_transformer_sum_delta_kw" in df.columns:
            delta = df[["ts", "raw_grid_P", "site_grid_signed_P", "reduced_total_vs_transformer_sum_delta_kw", "site_grid_transformer_complete"]].copy()
            delta.to_csv(audit_dir / "reduced_total_vs_transformer_sum_check.csv", index=False)
    target_validity = pd.DataFrame([
        {"target": "target_grid_import_P_t_plus_1", "valid_rows": int(df.get("target_grid_import_P_t_plus_1_valid", pd.Series(False, index=df.index)).fillna(False).sum()), "non_null_rows": int(df["target_grid_import_P_t_plus_1"].notna().sum())},
        {"target": "target_grid_import_P_t_plus_4", "valid_rows": int(df.get("target_grid_import_P_t_plus_4_valid", pd.Series(False, index=df.index)).fillna(False).sum()), "non_null_rows": int(df["target_grid_import_P_t_plus_4"].notna().sum())},
        {"target": "target_next_1h_max_grid_import_P", "valid_rows": int(df.get("target_next_1h_valid", pd.Series(False, index=df.index)).fillna(False).sum()), "non_null_rows": int(df["target_next_1h_max_grid_import_P"].notna().sum())},
        {"target": "target_next_4h_max_grid_import_P", "valid_rows": int(df.get("target_next_4h_valid", pd.Series(False, index=df.index)).fillna(False).sum()), "non_null_rows": int(df["target_next_4h_max_grid_import_P"].notna().sum())},
    ])
    target_validity["status"] = np.where(target_validity["valid_rows"] == target_validity["non_null_rows"], "pass", "fail")
    target_validity.to_csv(audit_dir / "target_validity_check.csv", index=False)
    return {
        "split_summary": split_summary,
        "label_summary": label_summary,
        "event_summary": event_summary,
    }


def main() -> None:
    args = parse_args()
    load_env(args.env_path)
    start_ts = pd.Timestamp(args.start_ts).tz_convert("UTC") if pd.Timestamp(args.start_ts).tzinfo else pd.Timestamp(args.start_ts, tz="UTC")
    end_ts = pd.Timestamp(args.end_ts).tz_convert("UTC") if pd.Timestamp(args.end_ts).tzinfo else pd.Timestamp(args.end_ts, tz="UTC")
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    with connect() as conn:
        rows = query_reduced_series(conn, start_ts, end_ts)
        meter_rows = query_meter_power(conn, start_ts, end_ts)
    check = source_series_check(rows, out_dir / "audits")
    meter_check = meter_power_check(meter_rows, out_dir / "audits")
    if args.check_only:
        print(json.dumps({"status": "check_only_ok", "out_dir": str(out_dir), "series_rows": int(len(check)), "meter_rows": int(len(meter_check))}, ensure_ascii=False, indent=2))
        return

    source = build_source_frame(rows, start_ts, end_ts)
    source = attach_meter_power_frame(source, meter_rows, start_ts, end_ts)
    frame = build_model_frame(source)
    full_p95 = float(frame.attrs["full_period_p95_grid_import_P"])
    full_p99 = float(frame.attrs["full_period_p99_grid_import_P"])
    train_p95 = float(frame.attrs["train_p95_grid_import_P"])
    train_p99 = float(frame.attrs["train_p99_grid_import_P"])

    frame_path = out_dir / "grid_peak_frame_15min.parquet"
    frame.to_parquet(frame_path, index=False)
    summaries = write_summaries(frame, out_dir, full_p95, full_p99, train_p95, train_p99)
    source_max_ts = frame.loc[frame["source_complete_row"], "ts"].max()
    terminal_source_gap_rows = int((frame["ts"] > source_max_ts).sum()) if pd.notna(source_max_ts) else int(len(frame))
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_relation": "ems.reduced_measurement_15min plus ems.cr_measurement_15min top grid transformer P audit",
        "time_window": {"start_ts": start_ts.isoformat(), "end_ts_exclusive": end_ts.isoformat()},
        "unit_policy": {"electricity_P_columns": "kW", "weather_columns": "source units"},
        "target_policy": "business-site grid import peak risk; site_grid_signed_P=sum(top grid-transformer signed P); grid_import_P=max(site_grid_signed_P,0)",
        "split_policy": "origin timestamp split: train 2018-2021, validation 2022, test 2023; future targets crossing split or invalid transformer-completeness boundaries are masked",
        "control_timing_policy": "current 15-minute interval is observed before predicting the next 1h/4h peak-risk labels",
        "grid_transformer_policy": {
            "top_grid_meters": list(TOP_GRID_METER_COLUMNS.keys()),
            "h2_old_pair": ["H2.Z35", "H2.Z36"],
            "h2_new_pair": ["H2.Z351", "H2.Z361"],
            "h2_replacement_gap_start": H2_REPLACEMENT_GAP_START.isoformat(),
            "h2_replacement_gap_end": H2_REPLACEMENT_GAP_END.isoformat(),
            "negative_branch_policy": "observed signed reverse-flow context; included in site signed total, not an invalid target condition",
            "imputation_policy": "not used for primary target; repair estimates are audit/sensitivity only",
        },
        "threshold_policy": {"model_feature_scope": "train_split_only", "descriptive_scope": "full_period"},
        "full_period_p95_grid_import_P": full_p95,
        "full_period_p99_grid_import_P": full_p99,
        "train_p95_grid_import_P": train_p95,
        "train_p99_grid_import_P": train_p99,
        "demand_rate_source": DEMAND_RATE_SOURCE,
        "demand_rate_eur_per_kw": DEMAND_RATE_EUR_PER_KW,
        "cost_proxy_policy": "monthly peak increment cost proxy, not a reconstructed bill or confirmed savings value",
        "source_max_complete_ts": source_max_ts.isoformat() if pd.notna(source_max_ts) else None,
        "terminal_source_gap_rows": terminal_source_gap_rows,
        "rows": int(len(frame)),
        "columns": int(len(frame.columns)),
        "outputs": {
            "grid_peak_frame_15min.parquet": {"path": str(frame_path.relative_to(PROJECT_ROOT)), "bytes": frame_path.stat().st_size, "sha256": sha256_file(frame_path)},
        },
        "split_counts": {str(k): int(v) for k, v in frame["split"].value_counts(dropna=False).sort_index().items()},
        "flag_counts": {
            "site_grid_transformer_complete_rows": int(frame["site_grid_transformer_complete"].fillna(False).sum()),
            "h2_grid_transformer_gap_rows": int(frame["h2_grid_transformer_gap"].fillna(False).sum()),
            "h2_grid_replacement_gap_rows": int(frame["h2_grid_replacement_gap"].fillna(False).sum()),
            "h2_reverse_flow_context_rows": int(frame["h2_reverse_flow_context"].fillna(False).sum()),
            "site_grid_signed_P_negative_rows": int(frame["site_grid_signed_P_negative"].fillna(False).sum()),
        },
        "target_valid_true_rows": {
            col: int(frame[col].fillna(False).sum()) for col in frame.columns if col.startswith("target_") and col.endswith("_valid")
        },
        "label_non_null_rows": {col: int(frame[col].notna().sum()) for col in frame.columns if col.startswith("target_") and not col.endswith("_valid")},
    }
    (out_dir / "grid_peak_dataset_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": "ok",
        "out_dir": str(out_dir),
        "rows": manifest["rows"],
        "columns": manifest["columns"],
        "full_period_p95_grid_import_P": full_p95,
        "full_period_p99_grid_import_P": full_p99,
        "train_p95_grid_import_P": train_p95,
        "train_p99_grid_import_P": train_p99,
        "split_counts": manifest["split_counts"],
        "event_summary_rows": len(summaries["event_summary"]),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
