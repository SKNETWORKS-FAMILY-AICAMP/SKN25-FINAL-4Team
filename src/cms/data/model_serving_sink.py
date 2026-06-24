"""Write-gated model-serving sink payload builder.

This module converts validated P-Max and anomaly workflow rows into table-shaped
payloads for the serving ``mart``/``ops``/``qa`` boundary. It does not connect to
PostgreSQL and does not execute writes by itself.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from cms.contracts.anomaly_detection_1h import (
    ANOMALY_DETECTION_EVALUATION_TABLE,
    ANOMALY_DETECTION_FORECAST_TABLE,
    ANOMALY_DETECTION_INFERENCE_LOG_TABLE,
    ANOMALY_DETECTION_MODEL_VERSION,
    ANOMALY_DETECTION_RELEASE,
    AnomalyDetectionLongRow,
)
from cms.contracts.pmax_forecast_15min import (
    PMAX_FORECAST_EVALUATION_TABLE,
    PMAX_FORECAST_INFERENCE_LOG_TABLE,
    PMAX_FORECAST_MODEL_VERSION,
    PMAX_FORECAST_PRODUCTION_RELEASE,
    PMAX_FORECAST_TABLE,
    PmaxForecastRow,
    actual_window_ts_for_forecast,
)

MODEL_SERVING_EVIDENCE_TABLE = "qa.serving_evidence"
MODEL_SERVING_ALLOWED_TABLES = (
    PMAX_FORECAST_TABLE,
    PMAX_FORECAST_INFERENCE_LOG_TABLE,
    PMAX_FORECAST_EVALUATION_TABLE,
    ANOMALY_DETECTION_FORECAST_TABLE,
    ANOMALY_DETECTION_INFERENCE_LOG_TABLE,
    ANOMALY_DETECTION_EVALUATION_TABLE,
    MODEL_SERVING_EVIDENCE_TABLE,
)
MODEL_SERVING_WRITE_ENV_FLAG = "ALLOW_MODEL_SERVING_WRITE"
MODEL_SERVING_ABSENT_AWS_TABLES: tuple[str, ...] = ()
MODEL_SERVING_ABSENT_AWS_WARNING_PREFIX = "absent_aws_table"
_ANOMALY_OUTPUT_TABLES = (
    ANOMALY_DETECTION_FORECAST_TABLE,
    ANOMALY_DETECTION_INFERENCE_LOG_TABLE,
    ANOMALY_DETECTION_EVALUATION_TABLE,
)


class ModelServingSink(Protocol):
    """Minimal row sink protocol for future PostgreSQL adapters."""

    def write_rows(self, *, schema: str, table: str, rows: tuple[dict[str, Any], ...]) -> None: ...


@dataclass(frozen=True)
class ModelServingTableBatch:
    """Rows prepared for one fully qualified serving table."""

    table_name: str
    rows: tuple[dict[str, Any], ...]

    @property
    def schema(self) -> str:
        return self.table_name.split(".", 1)[0]

    @property
    def table(self) -> str:
        return self.table_name.split(".", 1)[1]


@dataclass(frozen=True)
class ModelServingWriteBatch:
    """Prepared model-serving rows grouped by table."""

    run_id: str
    batches: tuple[ModelServingTableBatch, ...]
    writes_enabled: bool = False
    canonical_writes_enabled: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def row_count(self) -> int:
        return sum(len(batch.rows) for batch in self.batches)

    def rows_for(self, table_name: str) -> tuple[dict[str, Any], ...]:
        for batch in self.batches:
            if batch.table_name == table_name:
                return batch.rows
        return ()


@dataclass(frozen=True)
class ModelServingWriteResult:
    """Result of a write-gated sink call."""

    ok: bool
    attempted: bool
    written_tables: tuple[str, ...] = ()
    written_rows: int = 0
    blocked: bool = False
    errors: tuple[str, ...] = ()


def build_model_serving_write_batch(
    *,
    run_id: str,
    job_id: str,
    started_at: datetime,
    finished_at: datetime,
    artifact_refs: Mapping[str, str],
    pmax_rows: Sequence[PmaxForecastRow],
    anomaly_rows: Sequence[AnomalyDetectionLongRow],
    evidence_packet: Mapping[str, Any],
    writes_enabled: bool = False,
    canonical_writes_enabled: bool = False,
) -> ModelServingWriteBatch:
    """Build target-table payloads for a completed combined dry-run."""

    forecast_rows = tuple(pmax_rows)
    warning_rows = tuple(anomaly_rows)
    pmax_ref = str(artifact_refs.get("pmax", ""))
    anomaly_ref = str(artifact_refs.get("anomaly", ""))
    batches = (
        ModelServingTableBatch(PMAX_FORECAST_TABLE, tuple(_pmax_forecast_payload(run_id, row) for row in forecast_rows)),
        ModelServingTableBatch(
            PMAX_FORECAST_INFERENCE_LOG_TABLE,
            (_pmax_log_payload(run_id, job_id, _pmax_base_ts(forecast_rows, started_at), started_at, finished_at, pmax_ref, forecast_rows),) if forecast_rows else (),
        ),
        ModelServingTableBatch(PMAX_FORECAST_EVALUATION_TABLE, ()),
        ModelServingTableBatch(ANOMALY_DETECTION_FORECAST_TABLE, tuple(_anomaly_warning_payload(run_id, row) for row in warning_rows)),
        ModelServingTableBatch(
            ANOMALY_DETECTION_INFERENCE_LOG_TABLE,
            (_anomaly_log_payload(run_id, job_id, started_at, finished_at, anomaly_ref, warning_rows),) if warning_rows else (),
        ),
        ModelServingTableBatch(ANOMALY_DETECTION_EVALUATION_TABLE, ()),
        ModelServingTableBatch(MODEL_SERVING_EVIDENCE_TABLE, (_evidence_payload(run_id, evidence_packet, forecast_rows, warning_rows),)),
    )
    warnings = _batch_warnings(batches)
    return ModelServingWriteBatch(
        run_id=run_id,
        batches=batches,
        writes_enabled=writes_enabled,
        canonical_writes_enabled=canonical_writes_enabled,
        warnings=warnings,
    )


def write_model_serving_batch(
    *, batch: ModelServingWriteBatch, sink: ModelServingSink, allow_write: bool = False, env: Mapping[str, str] | None = None
) -> ModelServingWriteResult:
    """Write a prepared batch through an injected sink only when all gates pass."""

    errors = _write_gate_errors(batch=batch, allow_write=allow_write, env=env)
    if errors:
        return ModelServingWriteResult(ok=False, attempted=False, blocked=True, errors=errors)

    written_tables: list[str] = []
    written_rows = 0
    for table_batch in batch.batches:
        if not table_batch.rows:
            continue
        sink.write_rows(schema=table_batch.schema, table=table_batch.table, rows=table_batch.rows)
        written_tables.append(table_batch.table_name)
        written_rows += len(table_batch.rows)
    return ModelServingWriteResult(ok=True, attempted=True, written_tables=tuple(written_tables), written_rows=written_rows)


def _pmax_forecast_payload(run_id: str, row: PmaxForecastRow) -> dict[str, Any]:
    return {
        "logical_meter": row.logical_meter,
        "source_meter_urn": row.source_meter_urn,
        "base_ts": row.base_ts,
        "input_end_ts": row.input_end_ts,
        "target_ts": row.target_ts,
        "actual_window_ts": actual_window_ts_for_forecast(row),
        "horizon_minutes": row.horizon_minutes,
        "predicted_p_max": row.predicted_p_max,
        "created_at": row.created_at,
    }


def _pmax_log_payload(
    run_id: str, job_id: str, base_ts: datetime, started_at: datetime, finished_at: datetime, artifact_ref: str, rows: Sequence[PmaxForecastRow]
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "base_ts": base_ts,
        "status": "success",
        "quality_status": "normal" if rows else "degraded",
        "logical_meter_count": len({row.logical_meter for row in rows}),
        "forecast_row_count": len(rows),
        "replacement_row_count": 0,
        "internal_missing_segment_count": 0,
        "latest_missing_policy": "previous_observation_degraded",
        "error_reason": None,
        "details": {
            "job_id": job_id,
            "artifact_ref": artifact_ref,
            "model_name": "pmax_forecast",
            "model_version": PMAX_FORECAST_MODEL_VERSION,
            "release_version": PMAX_FORECAST_PRODUCTION_RELEASE,
        },
        "started_at": started_at,
        "completed_at": finished_at,
    }


def _anomaly_warning_payload(run_id: str, row: AnomalyDetectionLongRow) -> dict[str, Any]:
    return {
        "warning_id": _stable_id("anomaly", run_id, row.meter_urn, row.forecast_origin_ts.isoformat(), str(row.lead_step)),
        "run_id": run_id,
        "model_name": "anomaly_warning",
        "model_version": ANOMALY_DETECTION_MODEL_VERSION,
        "release_version": ANOMALY_DETECTION_RELEASE,
        "meter_urn": row.meter_urn,
        "model_urn": row.model_urn,
        "forecast_origin_ts": row.forecast_origin_ts,
        "target_ts": row.target_ts,
        "lead_step": row.lead_step,
        "horizon_hours": row.horizon_hours,
        "predicted_p": row.predicted_p,
        "threshold_lower": row.threshold_lower,
        "threshold_upper": row.threshold_upper,
        "warning_flag": row.warning_flag,
        "warning_type": row.warning_type,
        "status": row.status,
        "physical_flag": row.physical_flag,
        "input_quality": row.input_quality,
        "warning_reason_code": row.warning_reason_code,
        "input_missing_count": row.input_missing_count,
        "input_physical_count": row.input_physical_count,
        "physical_issue_types": row.physical_issue_types,
        "physical_issue_recent_count": row.physical_issue_recent_count,
        "physical_issue_pattern": row.physical_issue_pattern,
        "physical_issue_detail": row.physical_issue_detail,
        "meter_issue_types": row.meter_issue_types,
        "meter_issue_detail": row.meter_issue_detail,
        "meter_issue_severity": row.meter_issue_severity,
        "warning_reason_detail": row.warning_reason_detail,
        "low_sample": row.low_sample,
        "source_input_refs": list(row.source_input_refs),
        "created_at": row.created_at,
    }


def _anomaly_log_payload(
    run_id: str, job_id: str, started_at: datetime, finished_at: datetime, artifact_ref: str, rows: Sequence[AnomalyDetectionLongRow]
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "job_id": job_id,
        "model_name": "anomaly_warning",
        "model_version": ANOMALY_DETECTION_MODEL_VERSION,
        "release_version": ANOMALY_DETECTION_RELEASE,
        "forecast_origin_ts": rows[0].forecast_origin_ts if rows else started_at,
        "artifact_ref": artifact_ref,
        "status": "success",
        "meter_count": len({row.meter_urn for row in rows}),
        "prediction_count": len(rows),
        "warning_count": sum(1 for row in rows if row.warning_flag),
        "blocked_reason": None,
        "details": {},
        "started_at": started_at,
        "finished_at": finished_at,
    }


def _evidence_payload(
    run_id: str, evidence_packet: Mapping[str, Any], pmax_rows: Sequence[PmaxForecastRow], anomaly_rows: Sequence[AnomalyDetectionLongRow]
) -> dict[str, Any]:
    forecast_origin_ts = _datetime_from_packet(evidence_packet, "forecast_origin_ts", fallback_key="base_ts")
    return {
        "packet_id": _stable_id("evidence", run_id, forecast_origin_ts.isoformat()),
        "run_id": run_id,
        "forecast_origin_ts": forecast_origin_ts,
        "dry_run": bool(evidence_packet.get("dry_run", True)),
        "writes_enabled": bool(evidence_packet.get("writes_enabled", False)),
        "pmax_prediction_count": len(pmax_rows),
        "anomaly_prediction_count": len(anomaly_rows),
        "evidence": dict(evidence_packet),
        "created_at": forecast_origin_ts,
    }


def _datetime_from_packet(packet: Mapping[str, Any], key: str, *, fallback_key: str | None = None) -> datetime:
    active_key = key if key in packet else fallback_key
    if active_key is None or active_key not in packet:
        raise KeyError(key)
    value = packet[active_key]
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise TypeError(f"{active_key} must be datetime or ISO string")


def _pmax_base_ts(rows: Sequence[PmaxForecastRow], fallback: datetime) -> datetime:
    return rows[0].base_ts if rows else fallback


def _batch_warnings(batches: Sequence[ModelServingTableBatch]) -> tuple[str, ...]:
    warnings: list[str] = []
    for batch in batches:
        if batch.table_name not in MODEL_SERVING_ALLOWED_TABLES:
            warnings.append(f"unexpected_table:{batch.table_name}")
        if batch.table_name.startswith("canonical."):
            warnings.append(f"canonical_table_forbidden:{batch.table_name}")
    return tuple(warnings)


def _absent_aws_table_warning(table_name: str) -> str:
    return f"{MODEL_SERVING_ABSENT_AWS_WARNING_PREFIX}:{table_name}"


def _write_gate_errors(*, batch: ModelServingWriteBatch, allow_write: bool, env: Mapping[str, str] | None) -> tuple[str, ...]:
    errors: list[str] = []
    active_env = env or {}
    if not allow_write:
        errors.append("allow_write must be true")
    if not batch.writes_enabled:
        errors.append("batch.writes_enabled must be true")
    if batch.canonical_writes_enabled:
        errors.append("canonical_writes_enabled must be false")
    if active_env.get(MODEL_SERVING_WRITE_ENV_FLAG) != "1":
        errors.append(f"{MODEL_SERVING_WRITE_ENV_FLAG} must be 1")
    for warning in _batch_warnings(batch.batches) + batch.warnings:
        if warning not in errors:
            errors.append(warning)
    return tuple(errors)


def _stable_id(*parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return digest


__all__ = [
    "MODEL_SERVING_ABSENT_AWS_TABLES",
    "MODEL_SERVING_ABSENT_AWS_WARNING_PREFIX",
    "MODEL_SERVING_ALLOWED_TABLES",
    "MODEL_SERVING_EVIDENCE_TABLE",
    "MODEL_SERVING_WRITE_ENV_FLAG",
    "ModelServingSink",
    "ModelServingTableBatch",
    "ModelServingWriteBatch",
    "ModelServingWriteResult",
    "build_model_serving_write_batch",
    "write_model_serving_batch",
]
