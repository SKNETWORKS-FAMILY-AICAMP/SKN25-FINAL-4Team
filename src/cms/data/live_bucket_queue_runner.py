"""Import-safe SQL plan for the bounded ``live.bucket_queue`` worker.

This module deliberately builds a reviewable PostgreSQL command only. It does
not import database clients, open sockets, read environment variables, or execute
writes. The companion CLI defaults to dry-run and currently has no real database
adapter wired in.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from cms.contracts.live_pipeline import (
    ALLOWED_QUEUE_JOB_SPECS,
    CANONICAL_TABLES,
    JOB_KIND_MEAN_ROLLUP,
    JOB_KIND_PEAK_FEATURE,
    LIVE_BUCKET_QUEUE,
    LIVE_MEASUREMENT_1H,
    LIVE_MEASUREMENT_1MIN,
    LIVE_MEASUREMENT_15MIN,
    LIVE_PROMOTION_CHECK,
    MART_PEAK_FEATURE_15MIN,
    JobKind,
    Resolution,
)

LIVE_BUCKET_QUEUE_SOURCE_TABLE = LIVE_BUCKET_QUEUE
LIVE_BUCKET_WORKER_SOURCE_TABLES = (LIVE_BUCKET_QUEUE, LIVE_MEASUREMENT_1MIN)
LIVE_BUCKET_WORKER_ALLOWED_OUTPUT_TABLES = (
    LIVE_MEASUREMENT_15MIN,
    LIVE_MEASUREMENT_1H,
    MART_PEAK_FEATURE_15MIN,
    LIVE_PROMOTION_CHECK,
)
LIVE_BUCKET_WORKER_FORBIDDEN_OUTPUT_TABLES = CANONICAL_TABLES
LIVE_BUCKET_WORKER_RUNTIME_ADAPTER_STATUS = "psycopg_runtime_adapter"
LIVE_BUCKET_WORKER_COUNT_COLUMNS = (
    "claimed_count",
    "mean_rollup_count",
    "peak_feature_count",
    "promotion_check_count",
    "completed_count",
)

_OUTPUT_BY_JOB_SPEC: dict[tuple[JobKind, Resolution], str] = {
    (JOB_KIND_MEAN_ROLLUP, "15min"): LIVE_MEASUREMENT_15MIN,
    (JOB_KIND_MEAN_ROLLUP, "1h"): LIVE_MEASUREMENT_1H,
    (JOB_KIND_PEAK_FEATURE, "15min"): MART_PEAK_FEATURE_15MIN,
}


@dataclass(frozen=True)
class LiveBucketQueueWorkerCommand:
    """SQL command shape for one bounded worker pass over ``live.bucket_queue``."""

    source_table: str
    source_detail_table: str
    output_tables: tuple[str, ...]
    sql: str
    params: dict[str, object]
    job_specs: tuple[tuple[JobKind, Resolution], ...]
    count_columns: tuple[str, ...] = LIVE_BUCKET_WORKER_COUNT_COLUMNS
    forbidden_output_tables: tuple[str, ...] = LIVE_BUCKET_WORKER_FORBIDDEN_OUTPUT_TABLES
    runtime_adapter_status: str = LIVE_BUCKET_WORKER_RUNTIME_ADAPTER_STATUS

    def __post_init__(self) -> None:
        if self.source_table != LIVE_BUCKET_QUEUE:
            raise ValueError("live bucket worker source_table must be live.bucket_queue")
        if self.source_detail_table != LIVE_MEASUREMENT_1MIN:
            raise ValueError("live bucket worker source_detail_table must be live.measurement_1min")
        assert_allowed_worker_outputs(self.output_tables)
        if not self.job_specs:
            raise ValueError("at least one bucket queue job spec is required")


@dataclass(frozen=True)
class LiveBucketQueueWorkerResult:
    """Portable count-row contract for a future DB adapter."""

    claimed_count: int
    mean_rollup_count: int
    peak_feature_count: int
    promotion_check_count: int
    completed_count: int


def assert_allowed_worker_outputs(output_tables: Sequence[str]) -> None:
    """Allow only live rollups, peak-feature mart rows, and live QA eligibility checks."""

    forbidden = tuple(table for table in output_tables if table in LIVE_BUCKET_WORKER_FORBIDDEN_OUTPUT_TABLES)
    if forbidden:
        raise ValueError(f"live bucket worker must not write canonical tables: {forbidden}")
    unsupported = tuple(table for table in output_tables if table not in LIVE_BUCKET_WORKER_ALLOWED_OUTPUT_TABLES)
    if unsupported:
        raise ValueError(f"unsupported live bucket worker output tables: {unsupported}")


def make_live_bucket_queue_worker_command(
    *,
    batch_size: int = 100,
    worker_id: str = "live-bucket-queue-worker-dry-run",
    job_kinds: Sequence[str] | None = None,
    resolutions: Sequence[str] | None = None,
    min_coverage_ratio: float = 0.0,
    max_bucket_ts: datetime | None = None,
) -> LiveBucketQueueWorkerCommand:
    """Build a bounded SQL plan for mean rollup, peak feature, and QA eligibility work.

    The SQL is intentionally a plan/contract. It claims at most ``batch_size``
    pending queue rows, reads only ``live.measurement_1min`` for bucket source
    points, writes only the worker-owned output set, and returns count columns.
    No canonical table names are part of the allowed target contract.
    """

    _positive_int(batch_size, field_name="batch_size")
    if not worker_id.strip():
        raise ValueError("worker_id must be non-empty")
    if not 0 <= min_coverage_ratio <= 1:
        raise ValueError("min_coverage_ratio must be between 0 and 1")
    if max_bucket_ts is not None and (max_bucket_ts.tzinfo is None or max_bucket_ts.utcoffset() is None):
        raise ValueError("max_bucket_ts must be timezone-aware")

    job_specs = _normalize_job_specs(job_kinds=job_kinds, resolutions=resolutions)
    output_tables = _derive_output_tables(job_specs)
    assert_allowed_worker_outputs(output_tables)
    sql = _build_worker_sql(job_specs)
    return LiveBucketQueueWorkerCommand(
        source_table=LIVE_BUCKET_QUEUE,
        source_detail_table=LIVE_MEASUREMENT_1MIN,
        output_tables=output_tables,
        sql=sql,
        params={
            "batch_size": batch_size,
            "worker_id": worker_id,
            "min_coverage_ratio": min_coverage_ratio,
            "max_bucket_ts": max_bucket_ts,
        },
        job_specs=job_specs,
    )


def live_bucket_queue_result_from_count_row(row: Mapping[str, object]) -> LiveBucketQueueWorkerResult:
    """Normalize a future database count row into the import-safe result contract."""

    return LiveBucketQueueWorkerResult(
        claimed_count=_count_from_row(row, "claimed_count"),
        mean_rollup_count=_count_from_row(row, "mean_rollup_count"),
        peak_feature_count=_count_from_row(row, "peak_feature_count"),
        promotion_check_count=_count_from_row(row, "promotion_check_count"),
        completed_count=_count_from_row(row, "completed_count"),
    )


def _normalize_job_specs(
    *,
    job_kinds: Sequence[str] | None,
    resolutions: Sequence[str] | None,
) -> tuple[tuple[JobKind, Resolution], ...]:
    requested_job_kinds = _optional_text_set(job_kinds, field_name="job_kinds")
    requested_resolutions = _optional_text_set(resolutions, field_name="resolutions")
    specs = tuple(
        (job_kind, resolution)
        for job_kind, resolution in ALLOWED_QUEUE_JOB_SPECS
        if (requested_job_kinds is None or job_kind in requested_job_kinds)
        and (requested_resolutions is None or resolution in requested_resolutions)
    )
    if not specs:
        raise ValueError("requested job_kind/resolution filters do not match any allowed live.bucket_queue job spec")
    return cast(tuple[tuple[JobKind, Resolution], ...], specs)


def _derive_output_tables(job_specs: Sequence[tuple[JobKind, Resolution]]) -> tuple[str, ...]:
    # The SQL template contains all worker-owned INSERT branches, even when a
    # job filter makes some branches no-op. Keep the advertised output contract
    # equal to the full allowed set so static reviewers see every possible target.
    for spec in job_specs:
        _OUTPUT_BY_JOB_SPEC[spec]
    return LIVE_BUCKET_WORKER_ALLOWED_OUTPUT_TABLES


def _build_worker_sql(job_specs: Sequence[tuple[JobKind, Resolution]]) -> str:
    spec_predicate = " OR\n        ".join(
        f"(q.job_kind = '{job_kind}' AND q.resolution = '{resolution}')" for job_kind, resolution in job_specs
    )
    return f"""
WITH claimable AS (
    SELECT q.queue_id
    FROM {LIVE_BUCKET_QUEUE} AS q
    WHERE q.status = 'pending'
      AND (
        {spec_predicate}
      )
      AND (
        %(max_bucket_ts)s::timestamptz IS NULL OR
        (q.resolution = '15min' AND q.bucket_ts + interval '15 minutes' <= %(max_bucket_ts)s::timestamptz) OR
        (q.resolution = '1h' AND q.bucket_ts + interval '1 hour' <= %(max_bucket_ts)s::timestamptz)
      )
    ORDER BY q.bucket_ts, q.queue_id
    LIMIT %(batch_size)s
    FOR UPDATE SKIP LOCKED
), locked AS (
    SELECT q.queue_id, q.meter_urn, q.measurement, q.resolution, q.bucket_ts, q.job_kind, q.policy_id, q.policy_version
    FROM {LIVE_BUCKET_QUEUE} AS q
    JOIN claimable AS c ON q.queue_id = c.queue_id
), source_1min AS (
    SELECT
        l.queue_id,
        l.meter_urn,
        l.measurement,
        l.resolution,
        l.bucket_ts AS target_bucket_ts,
        l.job_kind,
        l.policy_id,
        l.policy_version,
        m.bucket_ts AS source_bucket_ts,
        m.value,
        m.expected_points,
        m.observed_points,
        m.gap_points,
        m.source_event_ids,
        m.source_run_id,
        m.provenance AS source_provenance
    FROM locked AS l
    JOIN {LIVE_MEASUREMENT_1MIN} AS m
      ON m.meter_urn = l.meter_urn
     AND m.measurement = l.measurement
     AND m.policy_version = l.policy_version
     AND m.resolution = '1min'
     AND m.bucket_ts >= l.bucket_ts
     AND m.bucket_ts < CASE
        WHEN l.resolution = '15min' THEN l.bucket_ts + interval '15 minutes'
        WHEN l.resolution = '1h' THEN l.bucket_ts + interval '1 hour'
        ELSE l.bucket_ts
     END
), bucket_stats AS (
    SELECT
        l.queue_id,
        l.meter_urn,
        l.measurement,
        l.resolution,
        l.bucket_ts,
        l.job_kind,
        l.policy_id,
        l.policy_version,
        COALESCE(sum(s.expected_points), 0)::integer AS expected_points,
        COALESCE(sum(s.observed_points), 0)::integer AS observed_points,
        COALESCE(sum(s.gap_points), 0)::integer AS gap_points,
        CASE WHEN COALESCE(sum(s.expected_points), 0) > 0
            THEN LEAST(1.0, COALESCE(sum(s.observed_points), 0)::numeric / sum(s.expected_points))
            ELSE 0
        END AS coverage_ratio,
        avg(s.value) FILTER (WHERE s.value IS NOT NULL) AS mean_value,
        max(s.value) FILTER (WHERE s.value IS NOT NULL) AS peak_value,
        min(s.value) FILTER (WHERE s.value IS NOT NULL) AS min_value,
        stddev_pop(s.value) FILTER (WHERE s.value IS NOT NULL) AS std_value,
        (array_agg(s.source_bucket_ts::text ORDER BY s.source_bucket_ts) FILTER (WHERE s.source_bucket_ts IS NOT NULL))::text[] AS source_bucket_refs,
        (array_agg(DISTINCT source_event_id.event_id) FILTER (WHERE source_event_id.event_id IS NOT NULL))::text[] AS source_event_ids,
        min(s.source_run_id) FILTER (WHERE s.source_run_id IS NOT NULL) AS source_run_id,
        jsonb_agg(DISTINCT s.source_provenance) FILTER (WHERE s.source_provenance IS NOT NULL AND s.source_provenance <> '{{}}'::jsonb) AS source_provenance_refs,
        (array_agg(s.value ORDER BY s.source_bucket_ts DESC) FILTER (WHERE s.value IS NOT NULL))[1] AS last_value
    FROM locked AS l
    LEFT JOIN source_1min AS s ON s.queue_id = l.queue_id
    LEFT JOIN LATERAL unnest(s.source_event_ids) AS source_event_id(event_id) ON TRUE
    GROUP BY l.queue_id, l.meter_urn, l.measurement, l.resolution, l.bucket_ts, l.job_kind, l.policy_id, l.policy_version
), mean_15min AS (
    INSERT INTO {LIVE_MEASUREMENT_15MIN} (
        bucket_ts, resolution, meter_urn, measurement, value, aggregation_policy,
        expected_points, observed_points, gap_points, coverage_ratio, quality_code,
        provenance, source_event_ids, source_bucket_refs, policy_id, policy_version, lineage_key
    )
    SELECT
        bucket_ts, '15min', meter_urn, measurement, mean_value, 'mean_observed_only',
        expected_points, observed_points, gap_points, coverage_ratio,
        CASE WHEN observed_points = 0 THEN 'null_observation' ELSE 'observed_mean' END,
        jsonb_build_object('source_table', '{LIVE_MEASUREMENT_1MIN}', 'queue_source', '{LIVE_BUCKET_QUEUE}', 'job_kind', job_kind),
        COALESCE(source_event_ids, ARRAY[]::text[]), COALESCE(source_bucket_refs, ARRAY[]::text[]), policy_id, policy_version,
        queue_id::text
    FROM bucket_stats
    WHERE job_kind = 'mean_rollup' AND resolution = '15min'
    ON CONFLICT (meter_urn, measurement, resolution, bucket_ts, policy_version)
    DO UPDATE SET
        value = EXCLUDED.value,
        expected_points = EXCLUDED.expected_points,
        observed_points = EXCLUDED.observed_points,
        gap_points = EXCLUDED.gap_points,
        coverage_ratio = EXCLUDED.coverage_ratio,
        quality_code = EXCLUDED.quality_code,
        provenance = EXCLUDED.provenance,
        source_event_ids = EXCLUDED.source_event_ids,
        source_bucket_refs = EXCLUDED.source_bucket_refs,
        updated_at = now()
    RETURNING bucket_ts, meter_urn, measurement, resolution, policy_version
), mean_1h AS (
    INSERT INTO {LIVE_MEASUREMENT_1H} (
        bucket_ts, resolution, meter_urn, measurement, value, aggregation_policy,
        expected_points, observed_points, gap_points, coverage_ratio, quality_code,
        provenance, source_event_ids, source_bucket_refs, policy_id, policy_version, lineage_key
    )
    SELECT
        bucket_ts, '1h', meter_urn, measurement, mean_value, 'mean_observed_only',
        expected_points, observed_points, gap_points, coverage_ratio,
        CASE WHEN observed_points = 0 THEN 'null_observation' ELSE 'observed_mean' END,
        jsonb_build_object('source_table', '{LIVE_MEASUREMENT_1MIN}', 'queue_source', '{LIVE_BUCKET_QUEUE}', 'job_kind', job_kind),
        COALESCE(source_event_ids, ARRAY[]::text[]), COALESCE(source_bucket_refs, ARRAY[]::text[]), policy_id, policy_version,
        queue_id::text
    FROM bucket_stats
    WHERE job_kind = 'mean_rollup' AND resolution = '1h'
    ON CONFLICT (meter_urn, measurement, resolution, bucket_ts, policy_version)
    DO UPDATE SET
        value = EXCLUDED.value,
        expected_points = EXCLUDED.expected_points,
        observed_points = EXCLUDED.observed_points,
        gap_points = EXCLUDED.gap_points,
        coverage_ratio = EXCLUDED.coverage_ratio,
        quality_code = EXCLUDED.quality_code,
        provenance = EXCLUDED.provenance,
        source_event_ids = EXCLUDED.source_event_ids,
        source_bucket_refs = EXCLUDED.source_bucket_refs,
        updated_at = now()
    RETURNING bucket_ts, meter_urn, measurement, resolution, policy_version
), peak_15min AS (
    INSERT INTO {MART_PEAK_FEATURE_15MIN} (
        window_ts, meter_urn, measurement, mean_value, max_value, min_value,
        p95_value, p99_value, std_value, last_value, peak_ts, peak_value,
        observed_points, expected_points, coverage_ratio, source_file, run_id,
        source_layer, source_mode, provenance
    )
    SELECT
        stats.bucket_ts,
        stats.meter_urn,
        stats.measurement,
        stats.mean_value,
        stats.peak_value,
        stats.min_value,
        percentile_disc(0.95) WITHIN GROUP (ORDER BY s.value) FILTER (WHERE s.value IS NOT NULL),
        percentile_disc(0.99) WITHIN GROUP (ORDER BY s.value) FILTER (WHERE s.value IS NOT NULL),
        stats.std_value,
        stats.last_value,
        peak_source.source_bucket_ts,
        stats.peak_value,
        stats.observed_points,
        stats.expected_points,
        stats.coverage_ratio,
        concat('live.measurement_1min/', stats.meter_urn, '/', stats.measurement, '/', stats.bucket_ts::text),
        COALESCE(stats.source_run_id, concat('live_bucket_queue_worker:', stats.policy_version::text)),
        '{LIVE_MEASUREMENT_1MIN}',
        'live_observed',
        jsonb_build_object(
            'source_layer', '{LIVE_MEASUREMENT_1MIN}',
            'source_mode', 'live_observed',
            'queue_source', '{LIVE_BUCKET_QUEUE}',
            'job_kind', stats.job_kind,
            'policy_id', stats.policy_id,
            'policy_version', stats.policy_version,
            'source_bucket_refs', COALESCE(stats.source_bucket_refs, ARRAY[]::text[]),
            'source_event_ids', COALESCE(stats.source_event_ids, ARRAY[]::text[]),
            'source_provenance_refs', COALESCE(stats.source_provenance_refs, '[]'::jsonb)
        )
    FROM bucket_stats AS stats
    LEFT JOIN source_1min AS s ON s.queue_id = stats.queue_id
    LEFT JOIN LATERAL (
        SELECT source_1min.source_bucket_ts
        FROM source_1min
        WHERE source_1min.queue_id = stats.queue_id AND source_1min.value = stats.peak_value
        ORDER BY source_1min.source_bucket_ts
        LIMIT 1
    ) AS peak_source ON TRUE
    WHERE stats.job_kind = 'peak_feature' AND stats.resolution = '15min'
    GROUP BY
        stats.queue_id, stats.bucket_ts, stats.meter_urn, stats.measurement,
        stats.mean_value, stats.peak_value, stats.min_value, stats.std_value,
        stats.last_value, peak_source.source_bucket_ts, stats.observed_points,
        stats.expected_points, stats.coverage_ratio, stats.source_run_id,
        stats.policy_id, stats.policy_version, stats.job_kind,
        stats.source_bucket_refs, stats.source_event_ids, stats.source_provenance_refs
    ON CONFLICT (window_ts, meter_urn, measurement, run_id)
    DO UPDATE SET
        mean_value = EXCLUDED.mean_value,
        max_value = EXCLUDED.max_value,
        min_value = EXCLUDED.min_value,
        p95_value = EXCLUDED.p95_value,
        p99_value = EXCLUDED.p99_value,
        std_value = EXCLUDED.std_value,
        last_value = EXCLUDED.last_value,
        peak_ts = EXCLUDED.peak_ts,
        peak_value = EXCLUDED.peak_value,
        observed_points = EXCLUDED.observed_points,
        expected_points = EXCLUDED.expected_points,
        coverage_ratio = EXCLUDED.coverage_ratio,
        source_file = EXCLUDED.source_file,
        source_layer = EXCLUDED.source_layer,
        source_mode = EXCLUDED.source_mode,
        provenance = EXCLUDED.provenance
    RETURNING window_ts AS bucket_ts, meter_urn, measurement, '15min'::text AS resolution, 0::integer AS policy_version
), promotion_checks AS (
    INSERT INTO {LIVE_PROMOTION_CHECK} (
        source_table, meter_urn, measurement, resolution, bucket_ts, policy_id, policy_version,
        eligibility_status, block_reasons, evidence
    )
    SELECT
        CASE
            WHEN stats.job_kind = 'peak_feature' THEN '{MART_PEAK_FEATURE_15MIN}'
            WHEN stats.resolution = '1h' THEN '{LIVE_MEASUREMENT_1H}'
            ELSE '{LIVE_MEASUREMENT_15MIN}'
        END AS source_table,
        stats.meter_urn,
        stats.measurement,
        stats.resolution,
        stats.bucket_ts,
        stats.policy_id,
        stats.policy_version,
        CASE WHEN stats.coverage_ratio >= %(min_coverage_ratio)s THEN 'pass' ELSE 'block' END AS eligibility_status,
        CASE WHEN stats.coverage_ratio >= %(min_coverage_ratio)s THEN ARRAY[]::text[] ELSE ARRAY['coverage_below_threshold']::text[] END,
        jsonb_build_object(
            'queue_source', '{LIVE_BUCKET_QUEUE}',
            'source_table', '{LIVE_MEASUREMENT_1MIN}',
            'job_kind', stats.job_kind,
            'coverage_ratio', stats.coverage_ratio,
            'canonical_write', false
        )
    FROM bucket_stats AS stats
    RETURNING check_id
), completed AS (
    UPDATE {LIVE_BUCKET_QUEUE} AS q
    SET status = 'done',
        locked_by = NULL,
        locked_at = NULL,
        updated_at = now(),
        last_error = NULL
    FROM locked AS l
    WHERE q.queue_id = l.queue_id
    RETURNING q.queue_id
)
SELECT
    (SELECT count(*) FROM locked)::integer AS claimed_count,
    ((SELECT count(*) FROM mean_15min) + (SELECT count(*) FROM mean_1h))::integer AS mean_rollup_count,
    (SELECT count(*) FROM peak_15min)::integer AS peak_feature_count,
    (SELECT count(*) FROM promotion_checks)::integer AS promotion_check_count,
    (SELECT count(*) FROM completed)::integer AS completed_count
""".strip()


def _optional_text_set(values: Sequence[str] | None, *, field_name: str) -> frozenset[str] | None:
    if values is None:
        return None
    normalized = frozenset(values)
    if any(not isinstance(value, str) or not value for value in normalized):
        raise ValueError(f"{field_name} must contain only non-empty strings")
    return normalized or None


def _positive_int(value: int, *, field_name: str) -> int:
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _count_from_row(row: Mapping[str, object], field_name: str) -> int:
    value = row.get(field_name, 0)
    if not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


__all__ = [
    "LIVE_BUCKET_QUEUE_SOURCE_TABLE",
    "LIVE_BUCKET_WORKER_ALLOWED_OUTPUT_TABLES",
    "LIVE_BUCKET_WORKER_COUNT_COLUMNS",
    "LIVE_BUCKET_WORKER_FORBIDDEN_OUTPUT_TABLES",
    "LIVE_BUCKET_WORKER_RUNTIME_ADAPTER_STATUS",
    "LIVE_BUCKET_WORKER_SOURCE_TABLES",
    "LiveBucketQueueWorkerCommand",
    "LiveBucketQueueWorkerResult",
    "assert_allowed_worker_outputs",
    "live_bucket_queue_result_from_count_row",
    "make_live_bucket_queue_worker_command",
]
