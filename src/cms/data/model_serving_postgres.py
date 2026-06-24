"""Import-safe PostgreSQL command builder for model-serving batches.

This module turns already validated model-serving table batches into
parameterized PostgreSQL upsert commands. It does not import psycopg at import
time and does not connect to a database unless the optional runtime sink is
instantiated and called by approved service code.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from importlib import import_module
from typing import Any, Protocol

from cms.data.model_serving_queries import SqlQuerySpec
from cms.data.model_serving_sink import (
    MODEL_SERVING_ALLOWED_TABLES,
    ModelServingSink,
    ModelServingTableBatch,
    ModelServingWriteBatch,
)
from cms.data.runtime_postgres import PsycopgConnectionConfig

_CONFLICT_KEYS: Mapping[str, tuple[str, ...]] = {
    "mart.pmax_forecast_15min": ("logical_meter", "base_ts", "target_ts"),
    "ops.pmax_log": ("run_id",),
    "qa.pmax_eval": ("evaluation_id",),
    "mart.anomaly_warning_1h": ("meter_urn", "forecast_origin_ts", "lead_step"),
    "ops.anomaly_log": ("run_id",),
    "qa.anomaly_eval": ("evaluation_id",),
    "qa.serving_evidence": ("packet_id",),
}

MODEL_SERVING_PRESENT_AWS_TABLE_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "mart.pmax_forecast_15min": (
        "logical_meter",
        "source_meter_urn",
        "base_ts",
        "input_end_ts",
        "target_ts",
        "actual_window_ts",
        "horizon_minutes",
        "predicted_p_max",
        "created_at",
    ),
    "ops.pmax_log": (
        "run_id",
        "base_ts",
        "status",
        "quality_status",
        "logical_meter_count",
        "forecast_row_count",
        "replacement_row_count",
        "internal_missing_segment_count",
        "latest_missing_policy",
        "error_reason",
        "details",
        "started_at",
        "completed_at",
    ),
    "qa.pmax_eval": (
        "evaluation_id",
        "logical_meter",
        "source_meter_urn",
        "base_ts",
        "target_ts",
        "actual_window_ts",
        "horizon_minutes",
        "predicted_p_max",
        "actual_p_max",
        "absolute_error",
        "squared_error",
        "evaluated_at",
    ),
    "mart.anomaly_warning_1h": (
        "warning_id",
        "run_id",
        "model_name",
        "model_version",
        "release_version",
        "meter_urn",
        "model_urn",
        "forecast_origin_ts",
        "target_ts",
        "lead_step",
        "horizon_hours",
        "predicted_p",
        "threshold_lower",
        "threshold_upper",
        "warning_flag",
        "warning_type",
        "status",
        "physical_flag",
        "input_quality",
        "warning_reason_code",
        "input_missing_count",
        "input_physical_count",
        "physical_issue_types",
        "physical_issue_recent_count",
        "physical_issue_pattern",
        "physical_issue_detail",
        "meter_issue_types",
        "meter_issue_detail",
        "meter_issue_severity",
        "warning_reason_detail",
        "low_sample",
        "source_input_refs",
        "created_at",
    ),
    "ops.anomaly_log": (
        "run_id",
        "job_id",
        "model_name",
        "model_version",
        "release_version",
        "forecast_origin_ts",
        "artifact_ref",
        "status",
        "meter_count",
        "prediction_count",
        "warning_count",
        "blocked_reason",
        "details",
        "started_at",
        "finished_at",
    ),
    "qa.anomaly_eval": (
        "evaluation_id",
        "run_id",
        "warning_id",
        "meter_urn",
        "forecast_origin_ts",
        "target_ts",
        "lead_step",
        "metric_name",
        "metric_value",
        "quality_status",
        "evidence_ref",
        "created_at",
    ),
    "qa.serving_evidence": (
        "packet_id",
        "run_id",
        "forecast_origin_ts",
        "dry_run",
        "writes_enabled",
        "pmax_prediction_count",
        "anomaly_prediction_count",
        "evidence",
        "created_at",
    ),
}


@dataclass(frozen=True)
class ModelServingPostgresCommand:
    """One parameterized PostgreSQL statement for a serving table."""

    target_table: str
    sql: str
    params: Mapping[str, Any]
    row_count: int


@dataclass(frozen=True)
class ModelServingReadResult:
    """Materialized read-only result for a model-serving input query."""

    query_name: str
    source_tables: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]

    @property
    def row_count(self) -> int:
        return len(self.rows)


class ModelServingCursor(Protocol):
    """Small cursor protocol used by the read-only materialization boundary."""

    def execute(self, sql: str, params: Mapping[str, Any]) -> object: ...

    def fetchall(self) -> Sequence[Any]: ...


@dataclass(frozen=True)
class PsycopgModelServingReader:
    """Runtime reader for approved read-only model-serving input materialization."""

    config: PsycopgConnectionConfig

    def fetch(self, query: SqlQuerySpec) -> ModelServingReadResult:
        _require_read_only_query(query)
        psycopg = import_module("psycopg")
        rows = import_module("psycopg.rows")
        with psycopg.connect(**self.config.connect_kwargs(), row_factory=rows.dict_row) as conn:
            with conn.cursor() as cur:
                return materialize_model_serving_read_query(cur, query)


@dataclass(frozen=True)
class PsycopgModelServingSink(ModelServingSink):
    """Runtime sink for approved model-serving writes.

    The class is transaction-per-table because each upstream batch is already
    grouped by table. Higher-level service code should call it only after the
    model-serving write gate has passed.
    """

    config: PsycopgConnectionConfig

    def write_rows(self, *, schema: str, table: str, rows: tuple[dict[str, Any], ...]) -> None:
        table_batch = ModelServingTableBatch(f"{schema}.{table}", rows)
        command = make_model_serving_table_upsert_command(table_batch)
        if command is None:
            return
        psycopg = import_module("psycopg")
        with psycopg.connect(**self.config.connect_kwargs()) as conn:
            with conn.cursor() as cur:
                cur.execute(command.sql, _adapt_jsonb_params(command.params))
            conn.commit()


def _adapt_jsonb_params(params: Mapping[str, Any]) -> Mapping[str, Any]:
    json_mod = import_module("psycopg.types.json")
    jsonb = json_mod.Jsonb
    return {key: jsonb(_json_safe(value)) if isinstance(value, (dict, list)) else value for key, value in params.items()}


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def materialize_model_serving_read_query(cursor: ModelServingCursor, query: SqlQuerySpec) -> ModelServingReadResult:
    """Execute one approved read-only input query and materialize rows as dicts.

    The function is intentionally cursor-based so tests can verify the boundary
    without importing psycopg or opening a DB connection.
    """

    _require_read_only_query(query)
    cursor.execute(query.sql, query.params)
    rows = tuple(_row_to_dict(row, query.expected_columns) for row in cursor.fetchall())
    return ModelServingReadResult(query_name=query.name, source_tables=query.source_tables, rows=rows)


def make_model_serving_upsert_commands(batch: ModelServingWriteBatch) -> tuple[ModelServingPostgresCommand, ...]:
    """Build PostgreSQL upsert commands for every non-empty table batch."""

    return tuple(command for table_batch in batch.batches if (command := make_model_serving_table_upsert_command(table_batch)) is not None)


def make_model_serving_table_upsert_command(table_batch: ModelServingTableBatch) -> ModelServingPostgresCommand | None:
    """Build a parameterized upsert for one allowed serving table."""

    if not table_batch.rows:
        return None
    _require_allowed_table(table_batch.table_name)
    conflict_keys = _CONFLICT_KEYS.get(table_batch.table_name)
    if conflict_keys is None:
        raise ValueError(f"missing conflict key contract for {table_batch.table_name}")
    columns = _ordered_columns(table_batch.rows)
    _require_columns(table_batch.table_name, columns, conflict_keys)
    quoted_columns = ", ".join(_quote_identifier(column) for column in columns)
    values_sql: list[str] = []
    params: dict[str, Any] = {}
    for row_idx, row in enumerate(table_batch.rows):
        placeholders: list[str] = []
        for column in columns:
            param_name = f"r{row_idx}_{column}"
            placeholders.append(f"%({param_name})s")
            params[param_name] = row.get(column)
        values_sql.append("(" + ", ".join(placeholders) + ")")
    conflict_sql = ", ".join(_quote_identifier(column) for column in conflict_keys)
    update_columns = tuple(column for column in columns if column not in conflict_keys)
    if update_columns:
        update_sql = ", ".join(f"{_quote_identifier(column)} = EXCLUDED.{_quote_identifier(column)}" for column in update_columns)
        conflict_action = f"DO UPDATE SET {update_sql}"
    else:
        conflict_action = "DO NOTHING"
    sql = (
        f"INSERT INTO {table_batch.table_name} ({quoted_columns}) "
        f"VALUES {', '.join(values_sql)} "
        f"ON CONFLICT ({conflict_sql}) {conflict_action}"
    )
    return ModelServingPostgresCommand(target_table=table_batch.table_name, sql=sql, params=params, row_count=len(table_batch.rows))


def _require_read_only_query(query: SqlQuerySpec) -> None:
    sql = " ".join(query.sql.strip().split()).lower()
    if not (sql.startswith("select ") or sql.startswith("with ")):
        raise ValueError(f"model-serving read query must start with SELECT or WITH: {query.name}")
    forbidden = (" insert ", " update ", " delete ", " truncate ", " drop ", " alter ", " create ", " grant ", " revoke ", " copy ")
    padded = f" {sql} "
    matched = tuple(token.strip() for token in forbidden if token in padded)
    if matched:
        raise ValueError(f"model-serving read query contains forbidden SQL token(s): {','.join(matched)}")
    canonical_sources = tuple(table for table in query.source_tables if table.startswith("canonical."))
    if canonical_sources:
        raise ValueError("model-serving read queries must not source canonical tables: " + ",".join(canonical_sources))


def _row_to_dict(row: Any, expected_columns: Sequence[str]) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    if hasattr(row, "_asdict"):
        return dict(row._asdict())
    if isinstance(row, Sequence) and not isinstance(row, str | bytes):
        if len(row) != len(expected_columns):
            raise ValueError(f"row length {len(row)} does not match expected columns {len(expected_columns)}")
        return dict(zip(expected_columns, row, strict=True))
    raise ValueError(f"unsupported model-serving row type: {type(row).__name__}")


def _ordered_columns(rows: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    columns: list[str] = []
    for row in rows:
        for column in row:
            if column not in columns:
                columns.append(column)
    return tuple(columns)


def _require_allowed_table(table_name: str) -> None:
    if table_name.startswith("canonical."):
        raise ValueError("model-serving writes must not target canonical tables")
    if table_name not in MODEL_SERVING_ALLOWED_TABLES:
        raise ValueError(f"table is not in the model-serving allowlist: {table_name}")


def _require_columns(table_name: str, columns: Sequence[str], conflict_keys: Sequence[str]) -> None:
    if not columns:
        raise ValueError(f"{table_name} requires at least one column")
    missing = tuple(column for column in conflict_keys if column not in columns)
    if missing:
        raise ValueError(f"{table_name} missing conflict key columns: {','.join(missing)}")
    expected_columns = MODEL_SERVING_PRESENT_AWS_TABLE_COLUMNS.get(table_name, ())
    unknown = tuple(column for column in columns if column not in expected_columns)
    if unknown:
        raise ValueError(f"{table_name} contains columns absent from AWS catalog: {','.join(unknown)}")
    for column in columns:
        if not column.isidentifier():
            raise ValueError(f"invalid column name for {table_name}: {column}")


def _quote_identifier(identifier: str) -> str:
    if not identifier.isidentifier():
        raise ValueError(f"invalid SQL identifier: {identifier}")
    return '"' + identifier + '"'


__all__ = [
    "MODEL_SERVING_PRESENT_AWS_TABLE_COLUMNS",
    "ModelServingPostgresCommand",
    "ModelServingReadResult",
    "PsycopgModelServingReader",
    "PsycopgModelServingSink",
    "make_model_serving_table_upsert_command",
    "make_model_serving_upsert_commands",
    "materialize_model_serving_read_query",
]
