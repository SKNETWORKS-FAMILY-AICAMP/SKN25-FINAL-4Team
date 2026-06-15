"""Import-safe SQL contracts for CMS model-serving inputs.

The functions in this module build parameterized SQL query specs only. They do
not connect to PostgreSQL, read secrets, execute queries, or mutate state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from cms.contracts.anomaly_detection_1h import (
    ANOMALY_DETECTION_FEATURE_TABLE,
    ANOMALY_DETECTION_HISTORY_HOURS,
    ANOMALY_DETECTION_MODEL_METERS,
)
from cms.contracts.live_pipeline import SOURCE_MODE_HYBRID_WARM_START, SOURCE_MODE_LIVE_OBSERVED, SOURCE_MODE_REFERENCE_BACKFILL
from cms.contracts.pmax_forecast_15min import (
    PMAX_FORECAST_INPUT_TABLE,
    PMAX_FORECAST_LOGICAL_METER_SOURCES,
    PMAX_FORECAST_REQUIRED_HISTORY_WINDOWS,
    PMAX_FORECAST_REQUIRED_MEASUREMENTS,
    pmax_live_observed_source_meters,
)

PMAX_FEATURE_QUERY_COLUMNS = (
    "window_ts",
    "meter_urn",
    "measurement",
    "mean_value",
    "max_value",
    "min_value",
    "p95_value",
    "p99_value",
    "std_value",
    "last_value",
    "peak_ts",
    "peak_value",
    "observed_points",
    "expected_points",
    "coverage_ratio",
    "source_file",
    "source_layer",
    "source_mode",
    "provenance",
    "run_id",
    "created_at",
)

ANOMALY_FEATURE_QUERY_COLUMNS = (
    "bucket_ts",
    "meter_urn",
    "feature_set",
    "p_value",
    "u1_value",
    "pf_value",
    "qv_value",
    "tdiff_value",
    "derived_features",
    "input_quality",
    "source_refs",
    "created_at",
)

ANOMALY_REFERENCE_FEATURE_TABLE = "reference.corrected_resampled_1h"
ANOMALY_REFERENCE_FEATURE_QUERY_COLUMNS = (
    "ts",
    "meter_urn",
    "measurement",
    "value",
    "source_file",
    "run_id",
    "created_at",
)

SCHEMA_INVENTORY_TABLES = (
    "live.measurement_event",
    PMAX_FORECAST_INPUT_TABLE,
    "mart.peak_training_frame_15min",
    ANOMALY_DETECTION_FEATURE_TABLE,
    "mart.pmax_forecast_15min",
    "mart.anomaly_warning_1h",
    "ops.pmax_forecast_inference_log",
    "ops.anomaly_warning_inference_log",
    "qa.pmax_forecast_evaluation",
    "qa.anomaly_warning_evaluation",
    "qa.model_serving_evidence_packet",
)


@dataclass(frozen=True)
class SqlQuerySpec:
    """Parameterized SQL and its expected source contract."""

    name: str
    sql: str
    params: Mapping[str, Any]
    source_tables: tuple[str, ...]
    expected_columns: tuple[str, ...]
    source_contract: Mapping[str, Any] | None = None


def build_pmax_feature_query(
    *,
    base_ts: datetime,
    logical_meters: Sequence[str] = tuple(PMAX_FORECAST_LOGICAL_METER_SOURCES),
    input_table: str = PMAX_FORECAST_INPUT_TABLE,
    history_windows: int = PMAX_FORECAST_REQUIRED_HISTORY_WINDOWS,
    source_mode: str = SOURCE_MODE_LIVE_OBSERVED,
    allow_null_source_mode: bool = False,
) -> SqlQuerySpec:
    """Build the read query for P-Max v29 feature rows.

    The deployed feature table uses ``window_ts``. The query intentionally
    returns duplicate physical keys if present so local readiness logic can
    detect ambiguous latest rows instead of silently hiding schema drift.
    """

    _require_aware_datetime(base_ts, "base_ts")
    _require_table_name(input_table)
    if input_table != PMAX_FORECAST_INPUT_TABLE:
        raise ValueError(f"P-Max serving input must use {PMAX_FORECAST_INPUT_TABLE}")
    if history_windows < PMAX_FORECAST_REQUIRED_HISTORY_WINDOWS:
        raise ValueError(f"history_windows must be >= {PMAX_FORECAST_REQUIRED_HISTORY_WINDOWS}")
    allowed_query_modes = (SOURCE_MODE_LIVE_OBSERVED, SOURCE_MODE_REFERENCE_BACKFILL, SOURCE_MODE_HYBRID_WARM_START)
    if source_mode not in allowed_query_modes:
        raise ValueError("P-Max serving input source_mode must be one of: " + ",".join(allowed_query_modes))
    if allow_null_source_mode and source_mode != SOURCE_MODE_HYBRID_WARM_START:
        raise ValueError("allow_null_source_mode is only valid with explicit hybrid_warm_start source_mode")
    unsupported = tuple(meter for meter in logical_meters if meter not in PMAX_FORECAST_LOGICAL_METER_SOURCES)
    if unsupported:
        raise ValueError("unsupported logical_meters: " + ",".join(unsupported))

    input_end_ts = base_ts - timedelta(minutes=15)
    input_start_ts = input_end_ts - timedelta(minutes=15 * (history_windows - 1))
    if source_mode in {SOURCE_MODE_REFERENCE_BACKFILL, SOURCE_MODE_HYBRID_WARM_START}:
        source_meters = tuple(dict.fromkeys(source for logical in logical_meters for source in PMAX_FORECAST_LOGICAL_METER_SOURCES[logical]))
    else:
        source_meters = tuple(dict.fromkeys(source for logical in logical_meters for source in pmax_live_observed_source_meters(logical)))
    columns = ", ".join(PMAX_FEATURE_QUERY_COLUMNS)
    placeholders_sources = ", ".join(f"%(source_meter_{idx})s" for idx, _ in enumerate(source_meters))
    placeholders_measurements = ", ".join(f"%(measurement_{idx})s" for idx, _ in enumerate(PMAX_FORECAST_REQUIRED_MEASUREMENTS))
    source_mode_clause = "source_mode = %(source_mode)s"
    source_preference_order = (SOURCE_MODE_LIVE_OBSERVED,)
    if source_mode == SOURCE_MODE_REFERENCE_BACKFILL:
        source_mode_clause = "(source_mode = %(reference_source_mode)s OR source_mode IS NULL)"
        source_preference_order = (SOURCE_MODE_REFERENCE_BACKFILL, "null_source_mode")
    elif source_mode == SOURCE_MODE_HYBRID_WARM_START:
        source_mode_clause = "(source_mode = %(live_source_mode)s OR source_mode = %(reference_source_mode)s OR source_mode IS NULL)"
        source_preference_order = (SOURCE_MODE_LIVE_OBSERVED, SOURCE_MODE_REFERENCE_BACKFILL, "null_source_mode")
    order_terms = "created_at DESC NULLS LAST, run_id DESC NULLS LAST"
    if source_mode in {SOURCE_MODE_REFERENCE_BACKFILL, SOURCE_MODE_HYBRID_WARM_START}:
        source_rank = "CASE WHEN source_mode = %(live_source_mode)s THEN 0 WHEN source_mode = %(reference_source_mode)s THEN 1 WHEN source_mode IS NULL THEN 2 ELSE 3 END"
        order_terms = f"{source_rank}, {order_terms}"
    sql = f"""
SELECT {columns}
FROM {input_table}
WHERE window_ts BETWEEN %(input_start_ts)s AND %(input_end_ts)s
  AND meter_urn IN ({placeholders_sources})
  AND measurement IN ({placeholders_measurements})
  AND {source_mode_clause}
ORDER BY window_ts, meter_urn, measurement, {order_terms}
""".strip()
    params: dict[str, Any] = {
        "input_start_ts": input_start_ts,
        "input_end_ts": input_end_ts,
        "base_ts": base_ts,
        "history_windows": history_windows,
        "source_mode": source_mode,
        "live_source_mode": SOURCE_MODE_LIVE_OBSERVED,
        "reference_source_mode": SOURCE_MODE_REFERENCE_BACKFILL,
    }
    params.update({f"source_meter_{idx}": meter for idx, meter in enumerate(source_meters)})
    params.update({f"measurement_{idx}": measurement for idx, measurement in enumerate(PMAX_FORECAST_REQUIRED_MEASUREMENTS)})
    return SqlQuerySpec(
        name="pmax_feature_input",
        sql=sql,
        params=params,
        source_tables=(input_table,),
        expected_columns=PMAX_FEATURE_QUERY_COLUMNS,
        source_contract={
            "source_mode": source_mode,
            "allowed_source_modes": source_preference_order,
            "allow_null_source_mode": source_mode in {SOURCE_MODE_REFERENCE_BACKFILL, SOURCE_MODE_HYBRID_WARM_START},
            "live_rows_preferred": source_mode == SOURCE_MODE_HYBRID_WARM_START,
            "production_label_allowed": source_mode == SOURCE_MODE_LIVE_OBSERVED,
        },
    )


def build_anomaly_feature_query(
    *,
    forecast_origin_ts: datetime,
    meter_urns: Sequence[str] = tuple(ANOMALY_DETECTION_MODEL_METERS),
    feature_table: str = ANOMALY_DETECTION_FEATURE_TABLE,
    history_hours: int = ANOMALY_DETECTION_HISTORY_HOURS,
) -> SqlQuerySpec:
    """Build the read query for anomaly v84 1h feature rows."""

    _require_aware_datetime(forecast_origin_ts, "forecast_origin_ts")
    _require_table_name(feature_table)
    if feature_table != ANOMALY_DETECTION_FEATURE_TABLE:
        raise ValueError(f"anomaly serving input must use {ANOMALY_DETECTION_FEATURE_TABLE}")
    if history_hours < ANOMALY_DETECTION_HISTORY_HOURS:
        raise ValueError(f"history_hours must be >= {ANOMALY_DETECTION_HISTORY_HOURS}")
    unsupported = tuple(meter for meter in meter_urns if meter not in ANOMALY_DETECTION_MODEL_METERS)
    if unsupported:
        raise ValueError("unsupported anomaly meter_urns: " + ",".join(unsupported))

    input_end_ts = forecast_origin_ts - timedelta(hours=1)
    input_start_ts = input_end_ts - timedelta(hours=history_hours - 1)
    columns = ", ".join(ANOMALY_FEATURE_QUERY_COLUMNS)
    placeholders = ", ".join(f"%(meter_{idx})s" for idx, _ in enumerate(meter_urns))
    sql = f"""
SELECT {columns}
FROM {feature_table}
WHERE bucket_ts BETWEEN %(input_start_ts)s AND %(input_end_ts)s
  AND meter_urn IN ({placeholders})
ORDER BY bucket_ts, meter_urn
""".strip()
    params: dict[str, Any] = {
        "input_start_ts": input_start_ts,
        "input_end_ts": input_end_ts,
        "forecast_origin_ts": forecast_origin_ts,
        "history_hours": history_hours,
    }
    params.update({f"meter_{idx}": meter for idx, meter in enumerate(meter_urns)})
    return SqlQuerySpec(
        name="anomaly_1h_feature",
        sql=sql,
        params=params,
        source_tables=(feature_table,),
        expected_columns=ANOMALY_FEATURE_QUERY_COLUMNS,
    )


def build_anomaly_reference_feature_query(
    *,
    forecast_origin_ts: datetime,
    meter_urns: Sequence[str] = tuple(ANOMALY_DETECTION_MODEL_METERS),
    reference_table: str = ANOMALY_REFERENCE_FEATURE_TABLE,
    history_hours: int = ANOMALY_DETECTION_HISTORY_HOURS,
) -> SqlQuerySpec:
    """Build the non-production reference/backfill read query for anomaly v84.

    This is deliberately separate from :func:`build_anomaly_feature_query` so
    reference rows cannot silently satisfy the approved live-serving source. The
    returned rows are raw long-form reference measurements; callers must pivot and
    label the run as ``reference_backfill`` evidence.
    """

    _require_aware_datetime(forecast_origin_ts, "forecast_origin_ts")
    _require_table_name(reference_table)
    if reference_table != ANOMALY_REFERENCE_FEATURE_TABLE:
        raise ValueError(f"anomaly reference dry-run input must use {ANOMALY_REFERENCE_FEATURE_TABLE}")
    if history_hours < ANOMALY_DETECTION_HISTORY_HOURS:
        raise ValueError(f"history_hours must be >= {ANOMALY_DETECTION_HISTORY_HOURS}")
    unsupported = tuple(meter for meter in meter_urns if meter not in ANOMALY_DETECTION_MODEL_METERS)
    if unsupported:
        raise ValueError("unsupported anomaly meter_urns: " + ",".join(unsupported))

    input_end_ts = forecast_origin_ts
    input_start_ts = input_end_ts - timedelta(hours=history_hours)
    columns = ", ".join(ANOMALY_REFERENCE_FEATURE_QUERY_COLUMNS)
    placeholders = ", ".join(f"%(meter_{idx})s" for idx, _ in enumerate(meter_urns))
    sql = f"""
SELECT {columns}
FROM {reference_table}
WHERE ts >= %(input_start_ts)s
  AND ts < %(input_end_ts)s
  AND meter_urn IN ({placeholders})
ORDER BY ts, meter_urn, measurement
""".strip()
    params: dict[str, Any] = {
        "input_start_ts": input_start_ts,
        "input_end_ts": input_end_ts,
        "forecast_origin_ts": forecast_origin_ts,
        "history_hours": history_hours,
        "source_mode": SOURCE_MODE_REFERENCE_BACKFILL,
    }
    params.update({f"meter_{idx}": meter for idx, meter in enumerate(meter_urns)})
    return SqlQuerySpec(
        name="anomaly_reference_1h_feature",
        sql=sql,
        params=params,
        source_tables=(reference_table,),
        expected_columns=ANOMALY_REFERENCE_FEATURE_QUERY_COLUMNS,
    )


def build_model_serving_schema_inventory_query(tables: Sequence[str] = SCHEMA_INVENTORY_TABLES) -> SqlQuerySpec:
    """Build a read-only information_schema query for serving readiness."""

    for table in tables:
        _require_table_name(table)
    values = ", ".join(f"(%(schema_{idx})s, %(table_{idx})s)" for idx, _ in enumerate(tables))
    sql = f"""
WITH expected(table_schema, table_name) AS (
  VALUES {values}
)
SELECT
  e.table_schema,
  e.table_name,
  c.column_name,
  c.data_type,
  c.is_nullable,
  c.ordinal_position
FROM expected AS e
LEFT JOIN information_schema.columns AS c
  ON c.table_schema = e.table_schema
 AND c.table_name = e.table_name
ORDER BY e.table_schema, e.table_name, c.ordinal_position
""".strip()
    params: dict[str, Any] = {}
    for idx, table in enumerate(tables):
        schema, name = table.split(".", 1)
        params[f"schema_{idx}"] = schema
        params[f"table_{idx}"] = name
    return SqlQuerySpec(
        name="model_serving_schema_inventory",
        sql=sql,
        params=params,
        source_tables=("information_schema.columns",),
        expected_columns=("table_schema", "table_name", "column_name", "data_type", "is_nullable", "ordinal_position"),
    )


def _require_aware_datetime(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _require_table_name(table_name: str) -> None:
    parts = table_name.split(".")
    if len(parts) != 2 or not all(part.isidentifier() for part in parts):
        raise ValueError(f"invalid qualified table name: {table_name}")
    if table_name.startswith("canonical."):
        raise ValueError("model-serving queries must not target canonical tables")


__all__ = [
    "ANOMALY_FEATURE_QUERY_COLUMNS",
    "ANOMALY_REFERENCE_FEATURE_QUERY_COLUMNS",
    "ANOMALY_REFERENCE_FEATURE_TABLE",
    "PMAX_FEATURE_QUERY_COLUMNS",
    "SCHEMA_INVENTORY_TABLES",
    "SqlQuerySpec",
    "build_anomaly_feature_query",
    "build_anomaly_reference_feature_query",
    "build_model_serving_schema_inventory_query",
    "build_pmax_feature_query",
]
