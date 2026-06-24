"""Mocked scratch adapter for the live measurement pipeline.

This module is intentionally import-safe and side-effect-free. It owns no database
client, network connection, DDL execution, or canonical write. It validates every
planned scratch target through ``db_scratch_guard`` and writes only to injected
repositories used by tests or future scratch DB adapters.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from cms.contracts.live_pipeline import (
    CANONICAL_MEASUREMENT_15MIN,
    LIVE_BUCKET_QUEUE,
    LIVE_MEASUREMENT_EVENT,
    MART_PEAK_FEATURE_15MIN,
    RESOLUTION_1H,
    RESOLUTION_15MIN,
    Coverage,
    ExpectedPointsPolicy,
    LiveMeasurementEvent,
    LiveMeasurementPolicy,
    assert_trigger_contract,
    decide_trigger_actions,
    floor_to_resolution,
)
from cms.data.db_scratch_guard import (
    POSTGRES_DATABASE,
    ScratchGuardError,
    postgres_scratch_schema_name,
    validate_postgres_scratch_write,
    validate_test_run_id,
    validate_write_allowed,
)
from cms.data.live_workers import (
    MeanRollupRecord,
    PeakFeatureRecord,
    build_mean_rollup,
    build_peak_feature,
    evaluate_qa_eligibility,
    prepare_promotion,
    transform_buffered_event,
)
from cms.data.scratch_ddl import SCRATCH_TABLES


class BufferedEventSource(Protocol):
    def iter_buffered_events(self, *, test_run_id: str, start: datetime, end: datetime) -> Iterable[Mapping[str, Any]]:
        """Return Kafka-envelope-style event mappings from an injected source."""
        ...


class ScratchRowSink(Protocol):
    def write_rows(self, *, database: str, schema: str, table: str, rows: tuple[dict[str, Any], ...]) -> None:
        """Accept already-guarded scratch rows."""
        ...


@dataclass(frozen=True)
class LiveScratchAdapterResult:
    row_counts: dict[str, int]
    target_names: dict[str, str]
    kafka_raw_topic: str
    evidence_level: str = "mocked_adapter"
    production_ready: bool = False
    canonical_writes_executed: bool = False
    real_db_writes_executed: bool = False


class LiveScratchPipelineAdapter:
    def __init__(
        self,
        *,
        source_repository: BufferedEventSource,
        postgres_sink_repository: ScratchRowSink,
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
        policy: LiveMeasurementPolicy,
        allow_write: bool,
        env: Mapping[str, str] | None = None,
    ) -> LiveScratchAdapterResult:
        safe_test_run_id = validate_test_run_id(test_run_id)
        validate_write_allowed(allow_write=allow_write, env=env)
        if end <= start:
            raise ValueError("end must be after start")

        schema = self.postgres_schema or postgres_scratch_schema_name(safe_test_run_id)
        kafka_topic = "measurement_raw_v1"
        raw_records = tuple(self.source_repository.iter_buffered_events(test_run_id=safe_test_run_id, start=start, end=end))
        events = tuple(_event_from_raw_record(record, test_run_id=safe_test_run_id) for record in raw_records)

        rows_by_table = _empty_rows_by_table()
        rows_by_table["measurement_event"] = tuple(_event_row(event, safe_test_run_id) for event in events)

        queue_rows: list[dict[str, Any]] = []
        one_min_rows: list[dict[str, Any]] = []
        issue_rows: list[dict[str, Any]] = []
        for event in events:
            trigger_result = decide_trigger_actions(event, policy)
            assert_trigger_contract(trigger_result)
            queue_rows.extend(_queue_row(job, safe_test_run_id) for job in trigger_result.queue_jobs)
            if trigger_result.upsert_1min:
                one_min_rows.append(_one_min_row(event, policy, safe_test_run_id))
            issue_rows.extend(_issue_metric_row(issue, event, safe_test_run_id) for issue in trigger_result.issues)
        rows_by_table["bucket_queue"] = tuple(queue_rows)
        rows_by_table["measurement_1min"] = tuple(one_min_rows)
        rows_by_table["qa_metrics"] = tuple(issue_rows)

        mean_15min = _build_mean_rollup_if_possible(events, policy, RESOLUTION_15MIN)
        mean_1h = _build_mean_rollup_if_possible(events, policy, RESOLUTION_1H)
        peak_feature = _build_peak_feature_if_possible(events, policy)
        if mean_15min is not None:
            rows_by_table["measurement_15min"] = (_mean_rollup_row(mean_15min, safe_test_run_id),)
            rows_by_table["promotion_check"] = (_promotion_check_row(mean_15min, safe_test_run_id),)
        if mean_1h is not None:
            rows_by_table["measurement_1h"] = (_mean_rollup_row(mean_1h, safe_test_run_id),)
        if peak_feature is not None:
            rows_by_table["peak_feature_15min"] = (_peak_feature_row(peak_feature, safe_test_run_id),)
        rows_by_table["latency_events"] = (_latency_row(safe_test_run_id, start, events),)

        _validate_postgres_targets(database=self.postgres_database, schema=schema, test_run_id=safe_test_run_id, allow_write=allow_write, env=env)
        for table, rows in rows_by_table.items():
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

        return LiveScratchAdapterResult(
            row_counts={table: len(rows) for table, rows in rows_by_table.items()},
            target_names={table: f"{self.postgres_database}.{schema}.{table}" for table in SCRATCH_TABLES},
            kafka_raw_topic=kafka_topic,
        )


def _empty_rows_by_table() -> dict[str, tuple[dict[str, Any], ...]]:
    return {table: () for table in SCRATCH_TABLES}


def _event_from_raw_record(record: Mapping[str, Any], *, test_run_id: str) -> LiveMeasurementEvent:
    if record.get("test_run_id") not in {None, test_run_id}:
        raise ScratchGuardError("raw Kafka fixture test_run_id mismatch")
    return transform_buffered_event(record)


def _event_row(event: LiveMeasurementEvent, test_run_id: str) -> dict[str, Any]:
    return _live_table_row(
        test_run_id,
        event.source_ts,
        event.meter_urn,
        event.measurement,
        {
            "table": LIVE_MEASUREMENT_EVENT,
            "event_id": event.event_id,
            "source_event_id": event.source_event_id,
            "value": event.value,
            "source_system": event.source_system,
        },
    )


def _queue_row(job: Any, test_run_id: str) -> dict[str, Any]:
    key = job.key
    return _live_table_row(
        test_run_id,
        key.bucket_ts,
        key.meter_urn,
        key.measurement,
        {"table": LIVE_BUCKET_QUEUE, "resolution": key.resolution, "job_kind": key.job_kind, "policy_version": key.policy_version, "status": job.status},
    )


def _one_min_row(event: LiveMeasurementEvent, policy: LiveMeasurementPolicy, test_run_id: str) -> dict[str, Any]:
    observed = 1 if event.value is not None else 0
    return _common_row(
        test_run_id=test_run_id,
        lane="live_1min",
        resolution="1min",
        bucket_ts=floor_to_resolution(event.source_ts, "1min"),
        meter_urn=event.meter_urn,
        measurement=event.measurement,
        value=event.value,
        expected_points=1,
        observed_points=observed,
        source_event_ids=(event.event_id,),
        source_native_interval_seconds=policy.native_cadence_seconds,
        target_resolution="1min",
        quality_code="observed" if observed else "null_observation",
        mask_code=None if observed else "missing_observation",
    )


def _build_mean_rollup_if_possible(events: Sequence[LiveMeasurementEvent], policy: LiveMeasurementPolicy, resolution: str) -> MeanRollupRecord | None:
    if not events:
        return None
    first = events[0]
    expected_policy = ExpectedPointsPolicy(
        native_cadence_seconds=policy.native_cadence_seconds or 60,
        expected_points_15min=policy.expected_points_15min,
        expected_points_1h=policy.expected_points_1h,
    )
    return build_mean_rollup(
        bucket_ts=first.source_ts,
        resolution=resolution,  # type: ignore[arg-type]
        meter_urn=first.meter_urn,
        measurement=first.measurement,
        values=tuple(event.value for event in events),
        expected_policy=expected_policy,
        source_event_ids=tuple(event.event_id for event in events),
    )


def _build_peak_feature_if_possible(events: Sequence[LiveMeasurementEvent], policy: LiveMeasurementPolicy) -> PeakFeatureRecord | None:
    if not events:
        return None
    first = events[0]
    return build_peak_feature(
        bucket_ts=first.source_ts,
        meter_urn=first.meter_urn,
        measurement=first.measurement,
        observations=tuple((event.source_ts, event.value, event.event_id) for event in events),
        expected_policy=ExpectedPointsPolicy(
            native_cadence_seconds=policy.native_cadence_seconds or 60,
            expected_points_15min=policy.expected_points_15min,
            expected_points_1h=policy.expected_points_1h,
        ),
        min_coverage_ratio=0.0,
    )


def _mean_rollup_row(record: MeanRollupRecord, test_run_id: str) -> dict[str, Any]:
    return _common_row(
        test_run_id=test_run_id,
        lane="mean_rollup",
        resolution=record.resolution,
        bucket_ts=record.bucket_ts,
        meter_urn=record.meter_urn,
        measurement=record.measurement,
        value=record.value,
        expected_points=record.expected_points,
        observed_points=record.observed_points,
        source_event_ids=record.source_event_ids,
        target_resolution=record.resolution,
        quality_code=record.quality_code,
        mask_code=None if record.observed_points else "missing_observation",
        coverage_ratio=record.coverage_ratio,
    )


def _peak_feature_row(record: PeakFeatureRecord, test_run_id: str) -> dict[str, Any]:
    return _live_table_row(test_run_id, record.bucket_ts, record.meter_urn, record.measurement, {"table": MART_PEAK_FEATURE_15MIN, **record.__dict__})


def _promotion_check_row(record: MeanRollupRecord, test_run_id: str) -> dict[str, Any]:
    eligibility = evaluate_qa_eligibility(
        source_table=record.table,
        target_table=CANONICAL_MEASUREMENT_15MIN,
        coverage=Coverage(record.observed_points, record.expected_points, record.coverage_ratio),
        lineage_present=bool(record.source_event_ids),
    )
    promotion = prepare_promotion(source_table=record.table, target_table=CANONICAL_MEASUREMENT_15MIN, approval_id=None, promotion_id=None)
    return _live_table_row(
        test_run_id,
        record.bucket_ts,
        record.meter_urn,
        record.measurement,
        {
            "source_table": record.table,
            "target_table": CANONICAL_MEASUREMENT_15MIN,
            "eligible": eligibility.allowed,
            "ready": promotion.ready,
            "block_reasons": (*eligibility.block_reasons, *promotion.block_reasons),
        },
    )


def _issue_metric_row(issue: Any, event: LiveMeasurementEvent, test_run_id: str) -> dict[str, Any]:
    return _common_row(
        test_run_id=test_run_id,
        lane="qa_issue",
        resolution="1min",
        bucket_ts=floor_to_resolution(event.source_ts, "1min"),
        meter_urn=event.meter_urn,
        measurement=event.measurement,
        value=None,
        expected_points=1,
        observed_points=0,
        source_event_ids=(event.event_id,),
        target_resolution="1min",
        quality_code="failed",
        mask_code="issue",
        coverage_ratio=0.0,
        extra={"metric_name": issue.issue_kind, "details": {"reason": issue.reason}},
    )


def _latency_row(test_run_id: str, start: datetime, events: Sequence[LiveMeasurementEvent]) -> dict[str, Any]:
    first = events[0] if events else None
    return _common_row(
        test_run_id=test_run_id,
        lane="latency",
        resolution="1min",
        bucket_ts=start,
        meter_urn=first.meter_urn if first else "none",
        measurement=first.measurement if first else "none",
        value=None,
        expected_points=1,
        observed_points=1 if first else 0,
        source_event_ids=(first.event_id,) if first else (),
        target_resolution="1min",
        quality_code="observed",
        mask_code=None,
        coverage_ratio=1.0 if first else 0.0,
        extra={"stage": "ingest", "kafka_to_event_sec": 0.0, "event_to_1min_sec": 0.0, "end_to_end_sec": 0.0},
    )


def _live_table_row(test_run_id: str, bucket_ts: datetime, meter_urn: str, measurement: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {"test_run_id": test_run_id, "bucket_ts": bucket_ts, "meter_urn": meter_urn, "measurement": measurement, "payload": dict(payload)}


def _common_row(
    *,
    test_run_id: str,
    lane: str,
    resolution: str,
    bucket_ts: datetime,
    meter_urn: str,
    measurement: str,
    value: float | None,
    expected_points: int,
    observed_points: int,
    source_event_ids: Sequence[str],
    target_resolution: str,
    quality_code: str,
    mask_code: str | None,
    source_native_interval_seconds: int | None = 60,
    coverage_ratio: float | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ratio = observed_points / expected_points if coverage_ratio is None and expected_points else (coverage_ratio or 0.0)
    row: dict[str, Any] = {
        "test_run_id": test_run_id,
        "lane": lane,
        "resolution": resolution,
        "bucket_ts": bucket_ts,
        "meter_urn": meter_urn,
        "measurement": measurement,
        "value": value,
        "quality_code": quality_code,
        "mask_code": mask_code,
        "evidence_level": "mocked_adapter",
        "expected_points": expected_points,
        "observed_points": observed_points,
        "gap_points": max(expected_points - observed_points, 0),
        "missing_points": max(expected_points - observed_points, 0),
        "coverage_ratio": ratio,
        "source_native_interval_seconds": source_native_interval_seconds,
        "cadence_policy_id": f"native_{source_native_interval_seconds}s" if source_native_interval_seconds else None,
        "target_resolution": target_resolution,
        "expected_points_policy": "policy_expected_points",
        "aggregation_policy": "mean_observed_only",
        "quality_summary": {quality_code: 1},
        "source_event_ids": tuple(source_event_ids),
        "timestamp_policy_ids": (),
        "source_timezones": (),
        "source_ts_columns": (),
        "source_ts_raw_samples": (),
        "timestamp_quality_summary": {},
        "timestamp_origin_rules": (),
        "lineage_key": f"{test_run_id}:{lane}:{resolution}:{meter_urn}:{measurement}:{bucket_ts.isoformat()}",
        "created_at": bucket_ts,
    }
    if extra:
        row.update(extra)
    return row


def _validate_postgres_targets(
    *,
    database: str,
    schema: str,
    test_run_id: str,
    allow_write: bool,
    env: Mapping[str, str] | None,
) -> None:
    for table in SCRATCH_TABLES:
        validate_postgres_scratch_write(
            database=database,
            schema=schema,
            table=table,
            row={"test_run_id": test_run_id},
            test_run_id=test_run_id,
            allow_write=allow_write,
            env=env,
        )


__all__ = ["BufferedEventSource", "LiveScratchAdapterResult", "LiveScratchPipelineAdapter", "ScratchRowSink"]
