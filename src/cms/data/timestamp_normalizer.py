"""Normalize source timestamps according to TimestampPolicy."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cms.contracts.timestamp_policy import TimestampPolicy


@dataclass(frozen=True)
class NormalizedTimestamp:
    source_ts_raw: Any
    source_ts_column: str
    event_ts_utc: datetime
    source_timezone: str
    timestamp_policy_id: str
    timestamp_parse_status: str
    timestamp_origin_rule: str
    timestamp_quality_code: str


def normalize_timestamp(row: Mapping[str, object], policy: TimestampPolicy) -> NormalizedTimestamp:
    if policy.timestamp_column not in row:
        raise ValueError(f"timestamp column {policy.timestamp_column!r} is missing")
    raw_value = row[policy.timestamp_column]
    event_ts_utc = _parse_to_utc(raw_value, policy)
    event_ts_utc, quality_code = _apply_origin_rule(event_ts_utc, policy)
    return NormalizedTimestamp(
        source_ts_raw=raw_value,
        source_ts_column=policy.timestamp_column,
        event_ts_utc=event_ts_utc,
        source_timezone=policy.source_timezone,
        timestamp_policy_id=policy.timestamp_policy_id,
        timestamp_parse_status="ok",
        timestamp_origin_rule=policy.timestamp_origin_rule,
        timestamp_quality_code=quality_code,
    )


def _parse_to_utc(value: object, policy: TimestampPolicy) -> datetime:
    try:
        zone = ZoneInfo(policy.source_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown source_timezone {policy.source_timezone!r}") from exc

    try:
        parsed = _parse_datetime_value(value)
    except ValueError as exc:
        raise ValueError(f"failed to parse timestamp from column {policy.timestamp_column!r}") from exc

    if policy.source_timestamp_type == "utc_instant":
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("utc_instant timestamps must include timezone information")
        return parsed.astimezone(timezone.utc)
    if policy.source_timestamp_type == "local_wall_time":
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            raise ValueError("local_wall_time timestamps must not include timezone information")
        parsed = parsed.replace(tzinfo=zone)
        return parsed.astimezone(timezone.utc)
    raise ValueError(f"unsupported source_timestamp_type {policy.source_timestamp_type!r}")


def _parse_datetime_value(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("empty timestamp")
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    raise ValueError("timestamp must be datetime or ISO string")


def _apply_origin_rule(event_ts_utc: datetime, policy: TimestampPolicy) -> tuple[datetime, str]:
    if policy.timestamp_origin_rule == "floor_to_native_interval":
        floored = _floor_to_interval(event_ts_utc, policy.native_interval_seconds)
        quality_code = "timestamp_normalized" if floored == event_ts_utc else "timestamp_floored_to_native_interval"
        return floored, quality_code
    if not _is_on_interval_boundary(event_ts_utc, policy.native_interval_seconds):
        raise ValueError("timestamp is not aligned to the native interval boundary")
    if policy.timestamp_origin_rule in {"exact_boundary", "reject_off_boundary"}:
        return event_ts_utc, "timestamp_normalized"
    raise ValueError(f"unsupported timestamp_origin_rule {policy.timestamp_origin_rule!r}")


def _is_on_interval_boundary(value: datetime, interval_seconds: int) -> bool:
    if value.microsecond != 0:
        return False
    return math.isclose(value.timestamp() % interval_seconds, 0.0, abs_tol=1e-9)


def _floor_to_interval(value: datetime, interval_seconds: int) -> datetime:
    floored_epoch = math.floor(value.timestamp() / interval_seconds) * interval_seconds
    return datetime.fromtimestamp(floored_epoch, tz=timezone.utc)


__all__ = ["NormalizedTimestamp", "normalize_timestamp"]
