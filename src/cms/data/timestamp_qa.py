"""Timestamp QA checks for harmonized live events before equalization."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cms.data.live_equalization_processor import LiveHarmonizedEvent


@dataclass(frozen=True)
class TimestampQaReport:
    counts: dict[str, int]
    hard_failures: tuple[dict[str, Any], ...] = ()
    warnings: tuple[dict[str, Any], ...] = ()

    @property
    def passed(self) -> bool:
        return not self.hard_failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "counts": dict(self.counts),
            "hard_failures": tuple(dict(item) for item in self.hard_failures),
            "warnings": tuple(dict(item) for item in self.warnings),
        }


def validate_timestamp_quality(events: Iterable[LiveHarmonizedEvent]) -> TimestampQaReport:
    event_tuple = tuple(events)
    grouped: dict[tuple[str, str], list[tuple[int, LiveHarmonizedEvent]]] = defaultdict(list)
    for index, event in enumerate(event_tuple):
        grouped[(event.meter_urn, event.measurement)].append((index, event))

    hard_failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for (meter_urn, measurement), indexed_events in grouped.items():
        series = {"meter_urn": meter_urn, "measurement": measurement}
        _check_duplicates(indexed_events, series=series, hard_failures=hard_failures)
        _check_policy_conflicts(indexed_events, series=series, hard_failures=hard_failures)
        _check_order_and_intervals(indexed_events, series=series, hard_failures=hard_failures, warnings=warnings)

    return TimestampQaReport(
        counts={"events": len(event_tuple), "series": len(grouped)},
        hard_failures=tuple(hard_failures),
        warnings=tuple(warnings),
    )


def _check_duplicates(
    indexed_events: list[tuple[int, LiveHarmonizedEvent]],
    *,
    series: dict[str, str],
    hard_failures: list[dict[str, Any]],
) -> None:
    seen: dict[datetime, int] = {}
    for index, event in indexed_events:
        previous = seen.get(event.timestamp)
        if previous is not None:
            hard_failures.append(
                {
                    "code": "duplicate_event_ts_utc",
                    **series,
                    "event_ts_utc": _serialize_datetime(event.timestamp),
                    "first_index": previous,
                    "duplicate_index": index,
                }
            )
        else:
            seen[event.timestamp] = index


def _check_policy_conflicts(
    indexed_events: list[tuple[int, LiveHarmonizedEvent]],
    *,
    series: dict[str, str],
    hard_failures: list[dict[str, Any]],
) -> None:
    policy_ids = _non_null_values(event.timestamp_policy_id for _, event in indexed_events)
    if len(policy_ids) > 1:
        hard_failures.append({"code": "timestamp_policy_conflict", **series, "timestamp_policy_ids": tuple(sorted(policy_ids))})
    native_intervals = _non_null_values(event.native_interval_seconds for _, event in indexed_events)
    if len(native_intervals) > 1:
        hard_failures.append({"code": "native_interval_policy_conflict", **series, "native_interval_seconds": tuple(sorted(native_intervals))})


def _check_order_and_intervals(
    indexed_events: list[tuple[int, LiveHarmonizedEvent]],
    *,
    series: dict[str, str],
    hard_failures: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> None:
    if len(indexed_events) < 2:
        _check_boundary(indexed_events, series=series, hard_failures=hard_failures)
        return
    input_timestamps = [event.timestamp for _, event in indexed_events]
    if input_timestamps != sorted(input_timestamps):
        warnings.append({"code": "out_of_order_input", **series, "count": 1})

    sorted_events = sorted(indexed_events, key=lambda item: item[1].timestamp)
    _check_boundary(sorted_events, series=series, hard_failures=hard_failures)
    native_interval = sorted_events[0][1].native_interval_seconds
    if native_interval is None:
        return
    for (_, previous), (_, current) in zip(sorted_events, sorted_events[1:]):
        delta_seconds = (current.timestamp - previous.timestamp).total_seconds()
        if delta_seconds <= 0:
            continue
        if delta_seconds % native_interval != 0:
            hard_failures.append(
                {
                    "code": "native_interval_mismatch",
                    **series,
                    "previous_event_ts_utc": _serialize_datetime(previous.timestamp),
                    "event_ts_utc": _serialize_datetime(current.timestamp),
                    "delta_seconds": delta_seconds,
                    "native_interval_seconds": native_interval,
                }
            )
        elif delta_seconds > native_interval:
            warnings.append(
                {
                    "code": "unexpected_gap",
                    **series,
                    "previous_event_ts_utc": _serialize_datetime(previous.timestamp),
                    "event_ts_utc": _serialize_datetime(current.timestamp),
                    "delta_seconds": delta_seconds,
                    "native_interval_seconds": native_interval,
                }
            )


def _check_boundary(
    indexed_events: list[tuple[int, LiveHarmonizedEvent]],
    *,
    series: dict[str, str],
    hard_failures: list[dict[str, Any]],
) -> None:
    for index, event in indexed_events:
        native_interval = event.native_interval_seconds
        if native_interval is None:
            continue
        if event.timestamp.microsecond != 0 or event.timestamp.timestamp() % native_interval != 0:
            hard_failures.append(
                {
                    "code": "unexpected_off_boundary_timestamp",
                    **series,
                    "event_ts_utc": _serialize_datetime(event.timestamp),
                    "index": index,
                    "native_interval_seconds": native_interval,
                }
            )


def _non_null_values(values: Iterable[Any]) -> set[Any]:
    return {value for value in values if value is not None}


def _serialize_datetime(value: datetime) -> str:
    return value.isoformat()


__all__ = ["TimestampQaReport", "validate_timestamp_quality"]
