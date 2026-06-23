"""Export DB-backed P-Max training data to parquet archives for RunPod jobs."""
from __future__ import annotations

import json
import os
import re
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from . import operations

DEFAULT_TABLE = "mart.peak_feature_15min"
STEP_MINUTES = 15
MAX_IMPUTED_INPUT_ROWS = 4
TRAIN_START = pd.Timestamp("2018-01-01 00:00:00", tz="UTC")
END_TS = pd.Timestamp("2024-01-01 00:00:00", tz="UTC")
RAW_FEATURE_COLUMNS = ["P_mean", "P_max", "P_std", "U1_mean", "PF_mean"]
TARGET_COLUMN = "P_max"
INPUT_OBSERVED_COLUMN = "_input_observed"
TARGET_OBSERVED_COLUMN = "_target_observed"
INTERPOLATED_COLUMN = "_was_interpolated"
FORWARD_FILLED_COLUMN = "_was_forward_filled"
LOGICAL_METERS = {
    "V.Z81": [("V.Z81", 1)],
    "V.Z82": [("V.Z82", 1)],
    "H2.Z35x": [("H2.Z35", 1), ("H2.Z351", 2)],
    "H2.Z36x": [("H2.Z36", 1), ("H2.Z361", 2)],
}
TABLE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")


def validate_table_name(table_name: str) -> str:
    if not TABLE_NAME_PATTERN.fullmatch(table_name):
        raise ValueError(f"Invalid schema-qualified table name: {table_name!r}")
    return table_name


def build_engine():
    load_dotenv()
    db_host = os.getenv("DB_HOST", "localhost")
    db_port = os.getenv("DB_PORT", "5432")
    db_name = os.getenv("DB_NAME", "cms")
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
    return create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"options": "-c default_transaction_read_only=on"},
    )


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


def read_sql_with_retry(engine, sql: str, params: dict[str, Any]) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            return pd.read_sql(text(sql), engine, params=params)
        except Exception as exc:
            last_error = exc
            if attempt == 3:
                break
            time.sleep(5)
    assert last_error is not None
    raise last_error


def _limited_internal_interpolation(
    values: pd.Series,
    max_gap_rows: int,
) -> tuple[pd.Series, pd.Series]:
    filled = values.copy()
    interpolated = pd.Series(False, index=values.index)
    missing_group = values.isna().ne(values.isna().shift(fill_value=False)).cumsum()

    for _, gap in values[values.isna()].groupby(missing_group[values.isna()]):
        gap_index = gap.index
        if len(gap_index) > max_gap_rows:
            continue
        first_pos = values.index.get_loc(gap_index[0])
        last_pos = values.index.get_loc(gap_index[-1])
        if first_pos == 0 or last_pos == len(values) - 1:
            continue
        before = values.iloc[first_pos - 1]
        after = values.iloc[last_pos + 1]
        if pd.isna(before) or pd.isna(after):
            continue
        step = (after - before) / (len(gap_index) + 1)
        for offset, index in enumerate(gap_index, start=1):
            filled.loc[index] = before + step * offset
            interpolated.loc[index] = True
    return filled, interpolated


def impute_raw_feature_gaps(
    df: pd.DataFrame,
    meter_urn: str,
    *,
    end_ts: pd.Timestamp | None = None,
    allow_trailing_single_bucket: bool = False,
) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    frame = df.sort_values("ts").drop_duplicates("ts", keep="last").copy()
    range_end = pd.Timestamp(end_ts) if end_ts is not None else frame["ts"].iloc[-1]
    full_index = pd.date_range(
        start=frame["ts"].iloc[0],
        end=range_end,
        freq=f"{STEP_MINUTES}min",
    )
    frame = frame.set_index("ts").reindex(full_index)
    frame.index.name = "ts"
    frame["meter_urn"] = meter_urn

    frame[INPUT_OBSERVED_COLUMN] = frame[RAW_FEATURE_COLUMNS].notna().all(axis=1)
    frame[TARGET_OBSERVED_COLUMN] = frame[TARGET_COLUMN].notna()
    interpolated = pd.Series(False, index=frame.index)
    forward_filled = pd.Series(False, index=frame.index)

    for column in RAW_FEATURE_COLUMNS:
        values = frame[column]
        filled, column_interpolated = _limited_internal_interpolation(
            values,
            MAX_IMPUTED_INPUT_ROWS,
        )
        frame[column] = filled
        interpolated |= column_interpolated

    if allow_trailing_single_bucket and len(frame) >= 2:
        latest_index = frame.index[-1]
        previous_index = frame.index[-2]
        latest_missing = frame.loc[latest_index, RAW_FEATURE_COLUMNS].isna()
        previous_complete = frame.loc[previous_index, RAW_FEATURE_COLUMNS].notna().all()
        if latest_missing.any() and previous_complete:
            missing_columns = latest_missing[latest_missing].index.tolist()
            frame.loc[latest_index, missing_columns] = frame.loc[
                previous_index, missing_columns
            ].to_numpy()
            forward_filled.loc[latest_index] = True

    frame[INTERPOLATED_COLUMN] = interpolated
    frame[FORWARD_FILLED_COLUMN] = forward_filled
    return frame.reset_index()


def fetch_source_meter(engine, table_name: str, meter_urn: str) -> pd.DataFrame:
    meter = read_sql_with_retry(
        engine,
        feature_sql(table_name, meter_urn),
        {"meter_urn": meter_urn, "start_ts": TRAIN_START, "end_ts": END_TS},
    )
    meter["window_ts"] = pd.to_datetime(meter["window_ts"], utc=True)
    for column in RAW_FEATURE_COLUMNS:
        meter[column] = pd.to_numeric(meter[column], errors="coerce")
    meter["P_mean"] = meter["P_mean"].clip(lower=0)
    meter["P_max"] = meter["P_max"].clip(lower=0)
    meter.loc[meter["U1_mean"] <= 0, "U1_mean"] = np.nan
    meter.loc[meter["U1_mean"] > 1000, "U1_mean"] = np.nan
    meter.loc[meter["PF_mean"].abs() > 1.5, "PF_mean"] = np.nan
    meter = meter.rename(columns={"window_ts": "ts"}).sort_values("ts").reset_index(drop=True)
    return impute_raw_feature_gaps(meter, meter_urn)


def _selected_logical_meters(meters: list[str] | None = None) -> list[str]:
    if not meters:
        return list(LOGICAL_METERS)
    invalid = sorted(set(meters) - set(LOGICAL_METERS))
    if invalid:
        raise ValueError(f"unknown P-Max logical meters: {invalid}")
    return list(dict.fromkeys(meters))


def _source_meters_for(logical_meters: list[str]) -> list[str]:
    sources: list[str] = []
    for logical_meter in logical_meters:
        for source_meter, _segment_id in LOGICAL_METERS[logical_meter]:
            if source_meter not in sources:
                sources.append(source_meter)
    return sources


def export_training_data_archive(
    destination_root: str | Path,
    run_id: str,
    *,
    meters: list[str] | None = None,
    table_name: str | None = None,
    overwrite: bool = False,
) -> dict:
    """Export selected P-Max source-meter frames into ``run_id.tar.gz``.

    The tarball contains ``{run_id}/frames/{source_meter}.parquet`` and a
    manifest. Frames are saved after the same DB query and gap-imputation path
    used by normal P-Max training, so RunPod can train without direct DB access.
    """
    operations.validate_run_id(run_id)
    table = validate_table_name(table_name or DEFAULT_TABLE)
    logical_meters = _selected_logical_meters(meters)
    source_meters = _source_meters_for(logical_meters)
    if not source_meters:
        raise ValueError("no P-Max source meters selected for export")

    destination = Path(destination_root)
    destination.mkdir(parents=True, exist_ok=True)
    archive_path = destination / f"{run_id}.tar.gz"
    if archive_path.exists() and not overwrite:
        return {
            "status": "exists",
            "run_id": run_id,
            "archive_path": str(archive_path),
        }

    engine = build_engine()
    started_at = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory(prefix=f"pmax_training_data_{run_id}_", dir=str(destination)) as tmp:
        tmp_root = Path(tmp)
        package_root = tmp_root / run_id
        frames_dir = package_root / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        source_rows: list[dict] = []
        for source_meter in source_meters:
            frame = fetch_source_meter(engine, table, source_meter)
            out_path = frames_dir / f"{source_meter}.parquet"
            frame.to_parquet(out_path, index=False, compression="snappy")
            source_rows.append(
                {
                    "source_meter": source_meter,
                    "rows": int(len(frame)),
                    "file": f"frames/{source_meter}.parquet",
                }
            )

        manifest = {
            "run_id": run_id,
            "model_kind": "pmax",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": started_at.isoformat(),
            "table": table,
            "logical_meters": logical_meters,
            "logical_meter_mapping": {
                key: [{"source_meter": source, "segment_id": segment_id} for source, segment_id in value]
                for key, value in LOGICAL_METERS.items()
                if key in logical_meters
            },
            "source_meter_count": len(source_rows),
            "source_meters": source_rows,
        }
        (package_root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        tmp_archive = destination / f".{run_id}.tar.gz.tmp"
        if tmp_archive.exists():
            tmp_archive.unlink()
        with tarfile.open(tmp_archive, "w:gz") as tar:
            tar.add(package_root, arcname=run_id)
        tmp_archive.replace(archive_path)

    return {
        "status": "exported",
        "run_id": run_id,
        "archive_path": str(archive_path),
        "size_bytes": archive_path.stat().st_size,
        "logical_meter_count": len(logical_meters),
        "source_meter_count": len(source_meters),
    }
