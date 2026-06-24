"""Injected-repository scratch DB adapter skeleton for CMS live equalization.

This module intentionally has no real database client imports and opens no network
connections. It reads raw harmonized documents from an injected source repository,
runs the in-memory live equalization processor, validates every PostgreSQL scratch
row through the guard, and hands rows to an injected sink repository.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from cms.data.db_scratch_guard import (
    POSTGRES_DATABASE,
    mongo_scratch_collection_name,
    postgres_scratch_schema_name,
    validate_postgres_scratch_write,
    validate_test_run_id,
    validate_write_allowed,
)
from cms.data.live_equalization_processor import (
    AggregatedRow,
    EqualizedRow,
    LiveHarmonizedEvent,
    SeriesCadencePolicy,
    process_live_equalization,
)
from cms.data.scratch_ddl import MEASUREMENT_TABLE_RESOLUTIONS
from cms.data.timestamp_qa import validate_timestamp_quality

POSTGRES_TARGET_TABLES = tuple(MEASUREMENT_TABLE_RESOLUTIONS)
_LANES_BY_RESOLUTION = {
    "1min": "eq_1min",
    "5min": "eq_5min",
    "15min": "downstream_15min",
    "1h": "downstream_1h",
}


class RawHarmonizedEventSource(Protocol):
    def iter_raw_harmonized_documents(self, *, test_run_id: str, start: datetime, end: datetime) -> Iterable[Mapping[str, Any]]:
        """Return raw Mongo-style harmonized event documents from an injected source."""
        ...


class PostgresScratchSink(Protocol):
    def write_rows(self, *, database: str, schema: str, table: str, rows: tuple[dict[str, Any], ...]) -> None:
        """Accept already-validated scratch rows. Implementations in tests are fakes/mocks."""
        ...


@dataclass(frozen=True)
class ScratchDbAdapterResult:
    row_counts: dict[str, int]
    target_names: dict[str, str]
    mongo_raw_source_collection: str
    db_writes_executed: bool
    timestamp_qa_report: dict[str, Any] | None = None
    production_ready: bool = False
    paper_complete: bool = False
    real_db_writes_executed: bool = False


class ScratchDbAdapter:
    def __init__(
        self,
        *,
        source_repository: RawHarmonizedEventSource,
        postgres_sink_repository: PostgresScratchSink,
        postgres_database: str = POSTGRES_DATABASE,
        postgres_schema: str | None = None,
    ) -> None:
        self.source_repository = source_repository
        self.postgres_sink_repository = postgres_sink_repository
        self.postgres_database = postgres_database
        self.postgres_schema = postgres_schema

    def run(
        self,
        *,
        test_run_id: str,
        start: datetime,
        end: datetime,
        allow_write: bool,
        env: Mapping[str, str] | None = None,
    ) -> ScratchDbAdapterResult:
        safe_test_run_id = validate_test_run_id(test_run_id)
        validate_write_allowed(allow_write=allow_write, env=env)

        schema = self.postgres_schema or postgres_scratch_schema_name(safe_test_run_id)
        start_utc = _coerce_aware_datetime(start, "start").astimezone(timezone.utc)
        end_utc = _coerce_aware_datetime(end, "end").astimezone(timezone.utc)
        if end_utc <= start_utc:
            raise ValueError("end must be after start")
        raw_documents = tuple(self.source_repository.iter_raw_harmonized_documents(test_run_id=safe_test_run_id, start=start_utc, end=end_utc))
        events = tuple(_live_event_from_raw_document(document, test_run_id=safe_test_run_id) for document in raw_documents)
        timestamp_qa_report = validate_timestamp_quality(events)
        if not timestamp_qa_report.passed:
            failure_codes = ",".join(str(failure["code"]) for failure in timestamp_qa_report.hard_failures)
            raise ValueError(f"timestamp QA failed: {failure_codes}")
        cadence_policies = _cadence_policies_from_raw_documents(raw_documents)
        equalization_result = process_live_equalization(events, start=start_utc, end=end_utc, cadence_policies=cadence_policies)

        rows_by_table = {
            "measurement_1min": tuple(_row_payload(row, test_run_id=safe_test_run_id, resolution="1min") for row in equalization_result.rows_1min),
            "measurement_5min": tuple(_row_payload(row, test_run_id=safe_test_run_id, resolution="5min") for row in equalization_result.rows_5min),
            "measurement_15min": tuple(_row_payload(row, test_run_id=safe_test_run_id, resolution="15min") for row in equalization_result.rows_15min),
            "measurement_1h": tuple(_row_payload(row, test_run_id=safe_test_run_id, resolution="1h") for row in equalization_result.rows_1h),
        }

        _validate_postgres_targets(
            database=self.postgres_database,
            schema=schema,
            test_run_id=safe_test_run_id,
            allow_write=allow_write,
            env=env,
        )

        write_calls = 0
        for table in POSTGRES_TARGET_TABLES:
            rows = rows_by_table[table]
            for row in rows:
                validate_postgres_scratch_write(
                    database=self.postgres_database,
                    schema=schema,
                    table=table,
                    row=row,
                    test_run_id=safe_test_run_id,
                    allow_write=allow_write,
                    env=env,
                )
            if rows:
                self.postgres_sink_repository.write_rows(database=self.postgres_database, schema=schema, table=table, rows=rows)
                write_calls += 1

        return ScratchDbAdapterResult(
            row_counts={table: len(rows_by_table[table]) for table in POSTGRES_TARGET_TABLES},
            target_names={table: _postgres_target_name(self.postgres_database, schema, table) for table in POSTGRES_TARGET_TABLES},
            mongo_raw_source_collection=mongo_scratch_collection_name(safe_test_run_id),
            db_writes_executed=write_calls > 0,
            timestamp_qa_report=timestamp_qa_report.to_dict(),
        )


def run_live_equalization_to_postgres_scratch(
    *,
    source_repository: RawHarmonizedEventSource,
    postgres_sink_repository: PostgresScratchSink,
    test_run_id: str,
    start: datetime,
    end: datetime,
    allow_write: bool,
    env: Mapping[str, str] | None = None,
) -> ScratchDbAdapterResult:
    adapter = ScratchDbAdapter(source_repository=source_repository, postgres_sink_repository=postgres_sink_repository)
    return adapter.run(test_run_id=test_run_id, start=start, end=end, allow_write=allow_write, env=env)


def _live_event_from_raw_document(document: Mapping[str, Any], *, test_run_id: str) -> LiveHarmonizedEvent:
    if not isinstance(document, Mapping):
        raise ValueError("raw harmonized document must be a mapping")
    document_test_run_id = document.get("test_run_id")
    if document_test_run_id is not None and document_test_run_id != test_run_id:
        raise ValueError("raw harmonized document test_run_id does not match adapter test_run_id")
    return LiveHarmonizedEvent(
        meter_urn=_required_str(document, "meter_urn"),
        measurement=_required_str(document, "measurement"),
        timestamp=_event_timestamp_from_document(document),
        value=float(document["value"]),
        is_weather=bool(document.get("is_weather", False)),
        source_event_id=_optional_source_event_id(document),
        native_interval_seconds=_optional_positive_int(document.get("native_interval_seconds"), "native_interval_seconds"),
        cadence_policy_id=_optional_str(document, "cadence_policy_id"),
        timestamp_policy_id=_optional_str(document, "timestamp_policy_id"),
        source_timezone=_optional_str(document, "source_timezone"),
        source_ts_raw=_optional_any_as_str(document, "source_ts_raw"),
        source_ts_column=_optional_str(document, "source_ts_column"),
        timestamp_quality_code=_optional_str(document, "timestamp_quality_code"),
        timestamp_origin_rule=_optional_str(document, "timestamp_origin_rule"),
    )


def _cadence_policies_from_raw_documents(documents: tuple[Mapping[str, Any], ...]) -> dict[str | tuple[str, str], SeriesCadencePolicy]:
    policies: dict[str | tuple[str, str], SeriesCadencePolicy] = {}
    for document in documents:
        if "native_interval_seconds" not in document:
            continue
        meter_urn = _required_str(document, "meter_urn")
        measurement = _required_str(document, "measurement")
        key = (meter_urn, measurement)
        policy = SeriesCadencePolicy(
            native_interval_seconds=_positive_int(document.get("native_interval_seconds"), "native_interval_seconds"),
            target_grain_minutes=_optional_positive_int(document.get("target_grain_minutes"), "target_grain_minutes"),
            cadence_policy_id=str(document.get("cadence_policy_id") or f"native_{document['native_interval_seconds']}s"),
            aggregation_policy=str(document.get("aggregation_policy") or "mean_non_cumulative"),
        )
        existing = policies.get(key)
        if existing is not None and existing != policy:
            raise ValueError("raw harmonized documents have conflicting cadence policies")
        policies[key] = policy
    return policies


def _positive_int(value: Any, key: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"raw harmonized document {key} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"raw harmonized document {key} must be a positive integer")
    return parsed


def _optional_positive_int(value: Any, key: str) -> int | None:
    if value is None:
        return None
    return _positive_int(value, key)


def _required_str(document: Mapping[str, Any], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"raw harmonized document {key} must be a non-empty string")
    return value


def _event_timestamp_from_document(document: Mapping[str, Any]) -> datetime:
    if "event_ts_utc" in document:
        event_ts_utc = _coerce_aware_datetime(document.get("event_ts_utc"), "event_ts_utc").astimezone(timezone.utc)
        if "timestamp" in document and document.get("timestamp") is not None:
            legacy_timestamp = _coerce_aware_datetime(document.get("timestamp"), "timestamp").astimezone(timezone.utc)
            if legacy_timestamp != event_ts_utc:
                raise ValueError("raw harmonized document event_ts_utc and timestamp must represent the same UTC instant")
        return event_ts_utc
    return _coerce_aware_datetime(document.get("timestamp"), "timestamp").astimezone(timezone.utc)


def _coerce_legacy_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise ValueError("raw harmonized document timestamp must be datetime or ISO string")


def _coerce_aware_datetime(value: Any, key: str) -> datetime:
    parsed = _coerce_legacy_datetime(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"raw harmonized document {key} must be timezone-aware")
    return parsed


def _optional_str(document: Mapping[str, Any], key: str) -> str | None:
    value = document.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"raw harmonized document {key} must be a non-empty string when provided")
    return value


def _optional_any_as_str(document: Mapping[str, Any], key: str) -> str | None:
    value = document.get(key)
    if value is None:
        return None
    return str(value)


def _optional_source_event_id(document: Mapping[str, Any]) -> str | None:
    value = document.get("source_event_id", document.get("event_id", document.get("_id")))
    if value is None:
        return None
    return str(value)


def _row_payload(row: EqualizedRow | AggregatedRow, *, test_run_id: str, resolution: str) -> dict[str, Any]:
    bucket_ts = row.timestamp
    lineage_key = f"{test_run_id}:{resolution}:{row.meter_urn}:{row.measurement}:{bucket_ts.isoformat()}"
    return {
        "test_run_id": test_run_id,
        "lane": _LANES_BY_RESOLUTION[resolution],
        "resolution": resolution,
        "bucket_ts": bucket_ts,
        "meter_urn": row.meter_urn,
        "measurement": row.measurement,
        "value": row.value,
        "quality_code": _quality_code(row),
        "mask_code": row.mask_code,
        "evidence_level": row.evidence_level,
        "expected_points": row.expected_points,
        "observed_points": row.observed_points,
        "gap_points": row.gap_points,
        "coverage_ratio": row.coverage_ratio,
        "source_native_interval_seconds": row.source_native_interval_seconds,
        "cadence_policy_id": row.cadence_policy_id,
        "target_resolution": resolution,
        "expected_points_policy": row.expected_points_policy,
        "aggregation_policy": row.aggregation_policy,
        "quality_summary": _quality_summary(row),
        "source_event_ids": row.source_event_ids,
        "timestamp_policy_ids": row.timestamp_policy_ids,
        "source_timezones": row.source_timezones,
        "source_ts_columns": row.source_ts_columns,
        "source_ts_raw_samples": row.source_ts_raw_samples,
        "timestamp_quality_summary": row.timestamp_quality_summary,
        "timestamp_origin_rules": row.timestamp_origin_rules,
        "lineage_key": lineage_key,
        "created_at": bucket_ts,
    }


def _quality_code(row: EqualizedRow | AggregatedRow) -> str:
    if isinstance(row, EqualizedRow):
        return row.quality
    return row.aggregation_policy or "ok_mean_non_cumulative"


def _quality_summary(row: EqualizedRow | AggregatedRow) -> dict[str, int]:
    if isinstance(row, EqualizedRow):
        return {row.quality: 1}
    return row.quality_summary


def _validate_postgres_targets(
    *,
    database: str,
    schema: str,
    test_run_id: str,
    allow_write: bool,
    env: Mapping[str, str] | None,
) -> None:
    guard_row = {"test_run_id": test_run_id}
    for table in POSTGRES_TARGET_TABLES:
        validate_postgres_scratch_write(
            database=database,
            schema=schema,
            table=table,
            row=guard_row,
            test_run_id=test_run_id,
            allow_write=allow_write,
            env=env,
        )


def _postgres_target_name(database: str, schema: str, table: str) -> str:
    return f"{database}.{schema}.{table}"
