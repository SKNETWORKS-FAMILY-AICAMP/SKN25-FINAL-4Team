"""Build raw harmonized event documents from source rows and timestamp policies."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cms.contracts.timestamp_policy import TimestampPolicy
from cms.data.timestamp_normalizer import normalize_timestamp


def build_raw_event(
    row: Mapping[str, object],
    policy: TimestampPolicy,
    *,
    value_column: str = "value",
    source_event_id_column: str | None = "source_event_id",
) -> dict[str, Any]:
    if value_column not in row:
        raise ValueError(f"value column {value_column!r} is missing")
    try:
        value = float(row[value_column])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"value column {value_column!r} must be numeric") from exc

    normalized = normalize_timestamp(row, policy)
    document: dict[str, Any] = {
        "meter_urn": policy.meter_urn,
        "measurement": policy.measurement,
        "source_ts_raw": normalized.source_ts_raw,
        "source_ts_column": normalized.source_ts_column,
        "event_ts_utc": normalized.event_ts_utc,
        "timestamp": normalized.event_ts_utc,
        "source_timezone": normalized.source_timezone,
        "timestamp_policy_id": normalized.timestamp_policy_id,
        "timestamp_parse_status": normalized.timestamp_parse_status,
        "timestamp_origin_rule": normalized.timestamp_origin_rule,
        "timestamp_quality_code": normalized.timestamp_quality_code,
        "value": value,
        "native_interval_seconds": policy.native_interval_seconds,
        "target_grain_minutes": policy.target_grain_minutes,
        "cadence_policy_id": policy.cadence_policy_id,
        "aggregation_policy": policy.aggregation_policy,
        "source_event_id": _source_event_id(row, source_event_id_column),
    }
    return document


def _source_event_id(row: Mapping[str, object], source_event_id_column: str | None) -> str | None:
    candidates = []
    if source_event_id_column is not None:
        candidates.append(source_event_id_column)
    candidates.extend(("event_id", "_id"))
    for key in candidates:
        value = row.get(key)
        if value is not None:
            return str(value)
    return None


__all__ = ["build_raw_event"]
