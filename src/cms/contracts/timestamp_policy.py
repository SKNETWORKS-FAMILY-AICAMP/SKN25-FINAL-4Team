"""Timestamp interpretation policy contract for raw CMS measurements."""

from __future__ import annotations

from dataclasses import dataclass

SOURCE_TIMESTAMP_TYPES = frozenset({"utc_instant", "local_wall_time"})
TIMESTAMP_ORIGIN_RULES = frozenset({"exact_boundary", "floor_to_native_interval", "reject_off_boundary"})
DEFAULT_AGGREGATION_POLICY = "mean_non_cumulative"


@dataclass(frozen=True)
class TimestampPolicy:
    timestamp_policy_id: str
    meter_urn: str
    measurement: str
    source_id: str | None
    file_pattern: str | None
    timestamp_column: str
    source_timezone: str
    source_timestamp_type: str
    native_interval_seconds: int
    target_grain_minutes: int | None
    timestamp_origin_rule: str
    cadence_policy_id: str
    aggregation_policy: str = DEFAULT_AGGREGATION_POLICY

    def __post_init__(self) -> None:
        for field_name in (
            "timestamp_policy_id",
            "meter_urn",
            "measurement",
            "timestamp_column",
            "source_timezone",
            "source_timestamp_type",
            "timestamp_origin_rule",
            "cadence_policy_id",
            "aggregation_policy",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field_name} must be a non-empty string")
        if self.source_timestamp_type not in SOURCE_TIMESTAMP_TYPES:
            raise ValueError(f"source_timestamp_type must be one of {sorted(SOURCE_TIMESTAMP_TYPES)}")
        if self.timestamp_origin_rule not in TIMESTAMP_ORIGIN_RULES:
            raise ValueError(f"timestamp_origin_rule must be one of {sorted(TIMESTAMP_ORIGIN_RULES)}")
        if not isinstance(self.native_interval_seconds, int) or self.native_interval_seconds <= 0:
            raise ValueError("native_interval_seconds must be a positive integer")
        if self.target_grain_minutes is not None:
            if not isinstance(self.target_grain_minutes, int) or self.target_grain_minutes <= 0:
                raise ValueError("target_grain_minutes must be a positive integer when provided")


__all__ = [
    "DEFAULT_AGGREGATION_POLICY",
    "SOURCE_TIMESTAMP_TYPES",
    "TIMESTAMP_ORIGIN_RULES",
    "TimestampPolicy",
]
