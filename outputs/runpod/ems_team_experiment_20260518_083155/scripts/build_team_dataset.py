#!/usr/bin/env python3
"""Build the EMS team 1h modeling dataset on RunPod.

Contract:
- source: ems.reduced_measurement_1h, derived from ems.cr_measurement_1h
- split: train 2018-2021, validation 2022, test 2023
- target: grid_P
- features: grid_P, pv_P, chp_P, Ta, Igm, hour_sin/cos, dow_sin/cos, month_sin/cos
- preprocessing: missing values filled with 0, MinMaxScaler fit on train rows excluding gateway outage windows
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import psycopg
from dotenv import load_dotenv
from sklearn.preprocessing import MinMaxScaler

PROJECT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_DIR / "outputs" / "team_1h_dataset"
ENV_PATH = PROJECT_DIR / ".env"

FEATURE_COLS = [
    "grid_P",
    "pv_P",
    "chp_P",
    "Ta",
    "Igm",
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    "month_sin",
    "month_cos",
]
MODEL_VALUE_COLS = ["grid_P", "pv_P", "chp_P", "Ta", "Igm"]

GATEWAY_OUTAGES = [
    {"name": "Workshop gateway failure #1", "start": "2020-02-13 00:00:00+00", "end": "2020-03-06 00:00:00+00"},
    {"name": "Emission lab gateway failure", "start": "2020-08-20 00:00:00+00", "end": "2020-09-17 00:00:00+00"},
    {"name": "Distribution gateway failure", "start": "2021-11-15 00:00:00+00", "end": "2021-12-10 00:00:00+00"},
    {"name": "Workshop gateway failure #2", "start": "2022-05-06 00:00:00+00", "end": "2022-07-14 00:00:00+00"},
]


def connect() -> psycopg.Connection:
    load_dotenv(ENV_PATH)
    required = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise RuntimeError(f"Missing DB env keys: {missing}")
    return psycopg.connect(
        host=os.environ["DB_HOST"],
        port=os.environ["DB_PORT"],
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        connect_timeout=10,
    )


def load_reduced_series() -> pd.DataFrame:
    sql = """
    SELECT ts,
           CASE
             WHEN category='electricity' AND subcategory='total' AND measurement='P' THEN 'grid_P'
             WHEN category='electricity' AND subcategory='pv' AND measurement='P' THEN 'pv_P_raw'
             WHEN category='electricity' AND subcategory='chp' AND measurement='P' THEN 'chp_P_raw'
             WHEN category='weather' AND subcategory='weather' AND measurement='Ta' THEN 'Ta'
             WHEN category='weather' AND subcategory='weather' AND measurement='Igm' THEN 'Igm'
           END AS feature,
           value
    FROM ems.reduced_measurement_1h
    WHERE ts >= '2018-01-01 00:00:00+00'::timestamptz
      AND ts <  '2024-01-01 00:00:00+00'::timestamptz
      AND (category, subcategory, measurement) IN (
        ('electricity','total','P'),
        ('electricity','pv','P'),
        ('electricity','chp','P'),
        ('weather','weather','Ta'),
        ('weather','weather','Igm')
      )
    ORDER BY ts, feature;
    """
    with connect() as conn:
        return pd.read_sql(sql, conn)


def add_calendar_features(df: pd.DataFrame) -> pd.DataFrame:
    ts = pd.to_datetime(df["ts"], utc=True)
    hour = ts.dt.hour
    dow = ts.dt.dayofweek
    month0 = ts.dt.month - 1
    df["hour_sin"] = np.sin(2 * math.pi * hour / 24)
    df["hour_cos"] = np.cos(2 * math.pi * hour / 24)
    df["dow_sin"] = np.sin(2 * math.pi * dow / 7)
    df["dow_cos"] = np.cos(2 * math.pi * dow / 7)
    df["month_sin"] = np.sin(2 * math.pi * month0 / 12)
    df["month_cos"] = np.cos(2 * math.pi * month0 / 12)
    return df


def add_split_and_outage(df: pd.DataFrame) -> pd.DataFrame:
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
    outage = pd.Series(False, index=df.index)
    outage_name = pd.Series("", index=df.index, dtype="object")
    for item in GATEWAY_OUTAGES:
        start = pd.Timestamp(item["start"])
        end = pd.Timestamp(item["end"])
        mask = (ts >= start) & (ts < end)
        outage |= mask
        outage_name.loc[mask] = item["name"]
    df["is_gateway_outage"] = outage
    df["gateway_outage_name"] = outage_name
    return df


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    long_df = load_reduced_series()
    wide = long_df.pivot_table(index="ts", columns="feature", values="value", aggfunc="first")
    full_index = pd.date_range("2018-01-01 00:00:00+00:00", "2024-01-01 00:00:00+00:00", freq="1h", inclusive="left")
    wide = wide.reindex(full_index)
    wide.index.name = "ts"
    df = wide.reset_index()

    # Preserve EMS P sign convention for all power columns.
    # positive = consumption / grid import, negative = generation / grid export.
    df["raw_grid_P"] = df.get("grid_P")
    df["raw_pv_P"] = df.get("pv_P_raw")
    df["raw_chp_P"] = df.get("chp_P_raw")
    # Paper target is building/grid consumption, not signed net export.
    # Keep raw signed grid P for audit, expose grid_P as non-negative import/consumption.
    df["grid_P"] = df["raw_grid_P"].clip(lower=0)
    df["pv_P"] = df["raw_pv_P"]
    df["chp_P"] = df["raw_chp_P"]

    for col in MODEL_VALUE_COLS:
        df[f"{col}_observed"] = df[col].notna()

    df = add_calendar_features(df)
    df = add_split_and_outage(df)

    missing_before_fill = {col: int(df[col].isna().sum()) for col in MODEL_VALUE_COLS}
    df[MODEL_VALUE_COLS] = df[MODEL_VALUE_COLS].fillna(0.0)
    df[FEATURE_COLS] = df[FEATURE_COLS].astype("float32")

    train_fit_mask = (df["split"] == "train") & (~df["is_gateway_outage"])
    scaler = MinMaxScaler(feature_range=(0, 1), clip=True)
    scaler.fit(df.loc[train_fit_mask, FEATURE_COLS])
    scaled = df.copy()
    scaled[FEATURE_COLS] = scaler.transform(df[FEATURE_COLS]).astype("float32")

    raw_path = OUTPUT_DIR / "team_1h_features_raw.parquet"
    scaled_path = OUTPUT_DIR / "team_1h_features_scaled.parquet"
    scaler_path = OUTPUT_DIR / "minmax_scaler.pkl"
    metadata_path = OUTPUT_DIR / "dataset_metadata.json"
    df.to_parquet(raw_path, index=False)
    scaled.to_parquet(scaled_path, index=False)
    joblib.dump(scaler, scaler_path)

    split_summary = (
        df.groupby("split", dropna=False)
        .agg(
            rows=("ts", "size"),
            gateway_outage_rows=("is_gateway_outage", "sum"),
            grid_P_observed=("grid_P_observed", "sum"),
            pv_P_observed=("pv_P_observed", "sum"),
            chp_P_observed=("chp_P_observed", "sum"),
            Ta_observed=("Ta_observed", "sum"),
            Igm_observed=("Igm_observed", "sum"),
        )
        .reset_index()
    )
    split_summary_path = OUTPUT_DIR / "split_summary.csv"
    split_summary.to_csv(split_summary_path, index=False)

    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_relation": "ems.reduced_measurement_1h (derived from ems.cr_measurement_1h)",
        "target": "grid_P",
        "feature_cols": FEATURE_COLS,
        "p_sign_convention": "positive = consumption / grid import; negative = generation / grid export",
        "target_definition": "grid_P is non-negative grid import/consumption active power, computed as max(raw signed grid transformer aggregate P, 0); target y is next-hour grid_P",
        "model_sign_convention": {"grid_P": "non-negative grid import/consumption; raw signed value preserved as raw_grid_P", "pv_P": "raw signed PV P; negative when generating", "chp_P": "raw signed CHP P; negative when generating"},
        "split": {"train": "2018-01-01 <= ts < 2022-01-01", "validation": "2022-01-01 <= ts < 2023-01-01", "test": "2023-01-01 <= ts < 2024-01-01"},
        "gateway_outages": GATEWAY_OUTAGES,
        "missing_policy": "fill model value columns with 0 after observed flags are stored",
        "scaler": "MinMaxScaler(feature_range=(0,1), clip=True, fit on train rows excluding gateway outages)",
        "missing_before_fill": missing_before_fill,
        "outputs": {"raw": str(raw_path), "scaled": str(scaled_path), "scaler": str(scaler_path), "split_summary": str(split_summary_path)},
        "scaler_data_min": dict(zip(FEATURE_COLS, map(float, scaler.data_min_))),
        "scaler_data_max": dict(zip(FEATURE_COLS, map(float, scaler.data_max_))),
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    print("dataset_built")
    print(f"raw_path={raw_path}")
    print(f"scaled_path={scaled_path}")
    print(f"scaler_path={scaler_path}")
    print(f"metadata_path={metadata_path}")
    print("missing_before_fill=", missing_before_fill)
    print(split_summary.to_string(index=False))


if __name__ == "__main__":
    main()
