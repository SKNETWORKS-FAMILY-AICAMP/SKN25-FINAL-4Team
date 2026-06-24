"""Minimal in-memory CMS live equalization processor core.

This module is intentionally local-only: it accepts already harmonized live events
in memory and returns in-memory rows. It does not import database clients, open
network connections, or write MongoDB/PostgreSQL data. The implemented rules are
only a small checkpoint subset of the paper contract and must not be described as
production-ready or paper-complete.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

MAX_LINEAR_NEAREST_DISTANCE = timedelta(minutes=5)
MEAN_NON_CUMULATIVE_POLICY = "mean_non_cumulative"


@dataclass(frozen=True)
class LiveHarmonizedEvent:
    """One harmonized live event supplied by a caller, not read from a DB."""

    meter_urn: str
    measurement: str
    timestamp: datetime
    value: float
    is_weather: bool = False
    source_event_id: str | None = None
    native_interval_seconds: int | None = None
    cadence_policy_id: str | None = None
    timestamp_policy_id: str | None = None
    source_timezone: str | None = None
    source_ts_raw: str | None = None
    source_ts_column: str | None = None
    timestamp_quality_code: str | None = None
    timestamp_origin_rule: str | None = None

    @property
    def series_key(self) -> str:
        return f"{self.meter_urn}.{self.measurement}"


@dataclass(frozen=True)
class SeriesCadencePolicy:
    """Per meter/measurement native cadence and target observed grid policy."""

    native_interval_seconds: int = 60
    target_grain_minutes: int | None = None
    cadence_policy_id: str = "native_1min"
    aggregation_policy: str = MEAN_NON_CUMULATIVE_POLICY


@dataclass(frozen=True)
class EqualizedRow:
    """One in-memory observed bucket row with explicit claim discipline."""

    meter_urn: str
    measurement: str
    timestamp: datetime
    value: float
    quality: str
    mask_code: str | None = None
    evidence_level: str = "in_memory_observed"
    expected_points: int = 1
    observed_points: int = 0
    gap_points: int = 1
    coverage_ratio: float = 0.0
    source_event_ids: tuple[str, ...] = ()
    grain_minutes: int = 1
    source_native_interval_seconds: int = 60
    cadence_policy_id: str = "native_1min"
    expected_points_policy: str = "native_interval"
    aggregation_policy: str = MEAN_NON_CUMULATIVE_POLICY
    db_writes_executed: bool = False
    production_ready: bool = False
    paper_complete: bool = False
    timestamp_policy_ids: tuple[str, ...] = ()
    source_timezones: tuple[str, ...] = ()
    source_ts_columns: tuple[str, ...] = ()
    source_ts_raw_samples: tuple[str, ...] = ()
    timestamp_quality_summary: dict[str, int] = field(default_factory=dict)
    timestamp_origin_rules: tuple[str, ...] = ()

    @property
    def series_key(self) -> str:
        return f"{self.meter_urn}.{self.measurement}"


@dataclass(frozen=True)
class AggregatedRow:
    """One in-memory downsampled row for non-cumulative measurements only."""

    meter_urn: str
    measurement: str
    timestamp: datetime
    value: float
    grain_minutes: int
    expected_points: int
    observed_points: int
    gap_points: int
    coverage_ratio: float
    quality_summary: dict[str, int]
    source_event_ids: tuple[str, ...]
    mask_code: str | None
    evidence_level: str = "in_memory_observed"
    aggregation_policy: str = MEAN_NON_CUMULATIVE_POLICY
    source_native_interval_seconds: int | None = None
    cadence_policy_id: str | None = None
    expected_points_policy: str = "native_interval"
    cumulative_rules_implemented: bool = False
    db_writes_executed: bool = False
    production_ready: bool = False
    paper_complete: bool = False
    timestamp_policy_ids: tuple[str, ...] = ()
    source_timezones: tuple[str, ...] = ()
    source_ts_columns: tuple[str, ...] = ()
    source_ts_raw_samples: tuple[str, ...] = ()
    timestamp_quality_summary: dict[str, int] = field(default_factory=dict)
    timestamp_origin_rules: tuple[str, ...] = ()

    @property
    def series_key(self) -> str:
        return f"{self.meter_urn}.{self.measurement}"


@dataclass(frozen=True)
class LiveEqualizationResult:
    """In-memory processor output; not a DB-tested or production-ready artifact."""

    rows_1min: tuple[EqualizedRow, ...]
    rows_5min: tuple[AggregatedRow, ...]
    rows_15min: tuple[AggregatedRow, ...]
    rows_1h: tuple[AggregatedRow, ...]
    aggregation_policy: str = MEAN_NON_CUMULATIVE_POLICY
    cumulative_rules_implemented: bool = False
    db_writes_executed: bool = False
    production_ready: bool = False
    paper_complete: bool = False
    local_in_memory_only: bool = True

    @property
    def row_counts(self) -> dict[str, int]:
        return {
            "rows_out_1min": len(self.rows_1min),
            "rows_out_5min": len(self.rows_5min),
            "rows_out_15min": len(self.rows_15min),
            "rows_out_1h": len(self.rows_1h),
        }


@dataclass(frozen=True)
class LatencyMarkers:
    """Caller-supplied timestamps for latency calculation; no wall clock reads."""

    source_event_ts: datetime | None = None
    received_at: datetime | None = None
    fastapi_received_at: datetime | None = None
    kafka_ack_at: datetime | None = None
    kafka_event_visible_at: datetime | None = None
    processor_started_at: datetime | None = None
    eq_1min_done_at: datetime | None = None
    eq_5min_done_at: datetime | None = None
    pg_15min_committed_at: datetime | None = None
    pg_1h_committed_at: datetime | None = None
    qa_done_at: datetime | None = None


@dataclass(frozen=True)
class LatencySummary:
    """Latency seconds derived only from supplied marker timestamps."""

    kafka_to_1min_sec: float | None
    kafka_to_5min_sec: float | None
    kafka_to_15min_sec: float | None
    kafka_to_1h_sec: float | None
    end_to_end_sec: float | None
    wall_clock_used: bool = False
    db_writes_executed: bool = False
    production_ready: bool = False
    paper_complete: bool = False


def equalize_to_1min(
    events: Iterable[LiveHarmonizedEvent],
    *,
    start: datetime,
    end: datetime,
    cadence_policies: Mapping[str | tuple[str, str], SeriesCadencePolicy] | None = None,
) -> tuple[EqualizedRow, ...]:
    """Equalize harmonized series whose policy targets 1min rows in memory.

    The time window is ``[start, end)``. Missing expected 1min buckets remain
    gaps with NaN values and zero observed coverage. Low-frequency native series
    whose policy targets 15min or 1h are intentionally not materialized here, so
    non-native finer minutes are not mislabeled as data-loss gaps.
    """

    rows = _equalize_with_cadence(events, start=start, end=end, cadence_policies=cadence_policies)
    return tuple(row for row in rows if row.grain_minutes == 1)


def downsample_mean_non_cumulative(rows: Iterable[EqualizedRow], *, minutes: int) -> tuple[AggregatedRow, ...]:
    """Downsample observed rows by arithmetic mean for non-cumulative measurements.

    Cumulative-family rules are intentionally not implemented in this minimal
    core and are declared as such on every output row.
    """

    return _aggregate_observed_rows(rows, minutes=minutes)


def process_live_equalization(
    events: Iterable[LiveHarmonizedEvent],
    *,
    start: datetime,
    end: datetime,
    cadence_policies: Mapping[str | tuple[str, str], SeriesCadencePolicy] | None = None,
) -> LiveEqualizationResult:
    """Run the minimal local processor core using per-series cadence policies."""

    observed_rows = _equalize_with_cadence(events, start=start, end=end, cadence_policies=cadence_policies)
    rows_1min = tuple(row for row in observed_rows if row.grain_minutes == 1)
    rows_15min_direct = tuple(row for row in observed_rows if row.grain_minutes == 15)
    rows_1h_direct = tuple(row for row in observed_rows if row.grain_minutes == 60)

    rows_5min = downsample_mean_non_cumulative(rows_1min, minutes=5)
    rows_15min = _sort_aggregated(
        (*downsample_mean_non_cumulative(rows_1min, minutes=15), *_aggregate_observed_rows(rows_15min_direct, minutes=15))
    )
    rows_1h = _sort_aggregated(
        (
            *downsample_mean_non_cumulative(rows_1min, minutes=60),
            *_aggregate_observed_rows(rows_15min_direct, minutes=60),
            *_aggregate_observed_rows(rows_1h_direct, minutes=60),
        )
    )
    return LiveEqualizationResult(
        rows_1min=rows_1min,
        rows_5min=rows_5min,
        rows_15min=rows_15min,
        rows_1h=rows_1h,
    )


def summarize_latency(markers: LatencyMarkers) -> LatencySummary:
    """Compute latency seconds from supplied markers only."""

    return LatencySummary(
        kafka_to_1min_sec=_seconds_between(markers.kafka_event_visible_at, markers.eq_1min_done_at),
        kafka_to_5min_sec=_seconds_between(markers.kafka_event_visible_at, markers.eq_5min_done_at),
        kafka_to_15min_sec=_seconds_between(markers.kafka_event_visible_at, markers.pg_15min_committed_at),
        kafka_to_1h_sec=_seconds_between(markers.kafka_event_visible_at, markers.pg_1h_committed_at),
        end_to_end_sec=_seconds_between(markers.received_at, markers.qa_done_at),
    )


def _equalize_with_cadence(
    events: Iterable[LiveHarmonizedEvent],
    *,
    start: datetime,
    end: datetime,
    cadence_policies: Mapping[str | tuple[str, str], SeriesCadencePolicy] | None,
) -> tuple[EqualizedRow, ...]:
    if end <= start:
        raise ValueError("end must be after start")

    rows: list[EqualizedRow] = []
    for series_events in _group_events(events).values():
        policy = _resolve_cadence_policy(series_events[0], cadence_policies)
        rows.extend(_equalize_series(series_events, start=start, end=end, policy=policy))
    return tuple(sorted(rows, key=lambda row: (row.series_key, row.grain_minutes, row.timestamp)))


def _group_events(events: Iterable[LiveHarmonizedEvent]) -> dict[str, tuple[LiveHarmonizedEvent, ...]]:
    grouped: dict[str, list[LiveHarmonizedEvent]] = defaultdict(list)
    for event in events:
        grouped[event.series_key].append(event)
    return {key: tuple(sorted(value, key=lambda event: event.timestamp)) for key, value in grouped.items()}


def _equalize_series(events: tuple[LiveHarmonizedEvent, ...], *, start: datetime, end: datetime, policy: SeriesCadencePolicy) -> list[EqualizedRow]:
    if not events:
        return []

    target_grain_minutes = _target_grain_minutes(policy)
    expected_points = _expected_points_per_bucket(policy, target_grain_minutes=target_grain_minutes)
    events_by_bucket: dict[datetime, list[LiveHarmonizedEvent]] = defaultdict(list)
    for event in events:
        if start <= event.timestamp < end:
            events_by_bucket[_bucket_start(event.timestamp, target_grain_minutes)].append(event)

    output = []
    target = start
    while target < end:
        bucket_events = events_by_bucket.get(target, [])
        observed_points = len(bucket_events)
        gap_points = max(expected_points - observed_points, 0)
        coverage_ratio = min(observed_points / expected_points, 1.0) if expected_points else 0.0
        value = _mean_value(bucket_events)
        quality = _bucket_quality(observed_points=observed_points, expected_points=expected_points)
        source_event_ids = tuple(source_event_id for event in bucket_events if (source_event_id := _source_event_id(event)) is not None)
        timestamp_provenance_events = tuple(bucket_events) if bucket_events else (events[0],)
        output.append(
            EqualizedRow(
                meter_urn=events[0].meter_urn,
                measurement=events[0].measurement,
                timestamp=target,
                value=value,
                quality=quality,
                mask_code=_mask_code(quality),
                expected_points=expected_points,
                observed_points=observed_points,
                gap_points=gap_points,
                coverage_ratio=coverage_ratio,
                source_event_ids=source_event_ids,
                grain_minutes=target_grain_minutes,
                source_native_interval_seconds=policy.native_interval_seconds,
                cadence_policy_id=policy.cadence_policy_id,
                aggregation_policy=policy.aggregation_policy,
                timestamp_policy_ids=_unique_non_null(event.timestamp_policy_id for event in timestamp_provenance_events),
                source_timezones=_unique_non_null(event.source_timezone for event in timestamp_provenance_events),
                source_ts_columns=_unique_non_null(event.source_ts_column for event in timestamp_provenance_events),
                source_ts_raw_samples=_unique_non_null(event.source_ts_raw for event in bucket_events),
                timestamp_quality_summary=_count_non_null(event.timestamp_quality_code for event in bucket_events),
                timestamp_origin_rules=_unique_non_null(event.timestamp_origin_rule for event in timestamp_provenance_events),
            )
        )
        target += timedelta(minutes=target_grain_minutes)
    return output


def _aggregate_observed_rows(rows: Iterable[EqualizedRow], *, minutes: int) -> tuple[AggregatedRow, ...]:
    if minutes <= 0:
        raise ValueError("minutes must be positive")

    buckets: dict[tuple[str, str, datetime], list[EqualizedRow]] = defaultdict(list)
    for row in rows:
        bucket_start = _bucket_start(row.timestamp, minutes)
        buckets[(row.meter_urn, row.measurement, bucket_start)].append(row)

    aggregated = []
    for (meter_urn, measurement, bucket_start), bucket_rows in sorted(buckets.items(), key=lambda item: (item[0][0], item[0][1], item[0][2])):
        usable_values = [row.value for row in bucket_rows if not math.isnan(row.value)]
        mean_value = sum(usable_values) / len(usable_values) if usable_values else math.nan
        expected_points = sum(row.expected_points for row in bucket_rows)
        observed_points = sum(row.observed_points for row in bucket_rows)
        gap_points = sum(row.gap_points for row in bucket_rows)
        quality_summary: dict[str, int] = {}
        source_event_ids: list[str] = []
        native_intervals = {row.source_native_interval_seconds for row in bucket_rows}
        cadence_policy_ids = {row.cadence_policy_id for row in bucket_rows}
        timestamp_quality_summary: dict[str, int] = {}
        for row in bucket_rows:
            quality_summary[row.quality] = quality_summary.get(row.quality, 0) + 1
            source_event_ids.extend(row.source_event_ids)
            for code, count in row.timestamp_quality_summary.items():
                timestamp_quality_summary[code] = timestamp_quality_summary.get(code, 0) + count
        aggregated.append(
            AggregatedRow(
                meter_urn=meter_urn,
                measurement=measurement,
                timestamp=bucket_start,
                value=mean_value,
                grain_minutes=minutes,
                expected_points=expected_points,
                observed_points=observed_points,
                gap_points=gap_points,
                coverage_ratio=min(observed_points / expected_points, 1.0) if expected_points else 0.0,
                quality_summary=quality_summary,
                source_event_ids=tuple(source_event_ids),
                mask_code="gap" if gap_points else None,
                source_native_interval_seconds=next(iter(native_intervals)) if len(native_intervals) == 1 else None,
                cadence_policy_id=next(iter(cadence_policy_ids)) if len(cadence_policy_ids) == 1 else None,
                timestamp_policy_ids=_unique_from_rows(bucket_rows, "timestamp_policy_ids"),
                source_timezones=_unique_from_rows(bucket_rows, "source_timezones"),
                source_ts_columns=_unique_from_rows(bucket_rows, "source_ts_columns"),
                source_ts_raw_samples=_unique_from_rows(bucket_rows, "source_ts_raw_samples"),
                timestamp_quality_summary=timestamp_quality_summary,
                timestamp_origin_rules=_unique_from_rows(bucket_rows, "timestamp_origin_rules"),
            )
        )
    return tuple(aggregated)


def _sort_aggregated(rows: Iterable[AggregatedRow]) -> tuple[AggregatedRow, ...]:
    return tuple(sorted(rows, key=lambda row: (row.series_key, row.grain_minutes, row.timestamp)))


def _resolve_cadence_policy(
    event: LiveHarmonizedEvent,
    cadence_policies: Mapping[str | tuple[str, str], SeriesCadencePolicy] | None,
) -> SeriesCadencePolicy:
    if not cadence_policies:
        return SeriesCadencePolicy()
    return cadence_policies.get((event.meter_urn, event.measurement), cadence_policies.get(event.series_key, SeriesCadencePolicy()))


def _target_grain_minutes(policy: SeriesCadencePolicy) -> int:
    if policy.native_interval_seconds <= 0:
        raise ValueError("native_interval_seconds must be positive")
    if policy.target_grain_minutes is not None:
        if policy.target_grain_minutes <= 0:
            raise ValueError("target_grain_minutes must be positive")
        return policy.target_grain_minutes
    if policy.native_interval_seconds <= 60:
        return 1
    if policy.native_interval_seconds <= 900:
        return 15
    return 60


def _expected_points_per_bucket(policy: SeriesCadencePolicy, *, target_grain_minutes: int) -> int:
    target_seconds = target_grain_minutes * 60
    if target_seconds % policy.native_interval_seconds != 0:
        raise ValueError("target grain must be divisible by native interval")
    return target_seconds // policy.native_interval_seconds


def _mean_value(events: list[LiveHarmonizedEvent]) -> float:
    if not events:
        return math.nan
    return sum(event.value for event in events) / len(events)


def _bucket_quality(*, observed_points: int, expected_points: int) -> str:
    if observed_points <= 0:
        return "gap"
    if observed_points < expected_points:
        return "partial"
    return "observed"


def _mask_code(quality: str) -> str | None:
    if quality == "gap":
        return "gap"
    if quality == "partial":
        return "low_coverage"
    return None


def _unique_non_null(values: Iterable[str | None]) -> tuple[str, ...]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value is None or value in seen:
            continue
        seen.add(value)
        output.append(value)
    return tuple(output)


def _count_non_null(values: Iterable[str | None]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        if value is None:
            continue
        counts[value] = counts.get(value, 0) + 1
    return counts


def _unique_from_rows(rows: Iterable[EqualizedRow], attribute: str) -> tuple[str, ...]:
    values: list[str] = []
    for row in rows:
        values.extend(getattr(row, attribute))
    return _unique_non_null(values)


def _source_event_id(event: LiveHarmonizedEvent | None) -> str | None:
    if event is None:
        return None
    return event.source_event_id


def _bucket_start(timestamp: datetime, minutes: int) -> datetime:
    if minutes >= 60:
        hours = minutes // 60
        bucket_hour = (timestamp.hour // hours) * hours
        return timestamp.replace(hour=bucket_hour, minute=0, second=0, microsecond=0)
    bucket_minute = (timestamp.minute // minutes) * minutes
    return timestamp.replace(minute=bucket_minute, second=0, microsecond=0)


def _seconds_between(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return (end - start).total_seconds()
