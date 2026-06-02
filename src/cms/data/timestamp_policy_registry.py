"""CSV-backed timestamp policy registry."""

from __future__ import annotations

import csv
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cms.contracts.timestamp_policy import TimestampPolicy

REQUIRED_REGISTRY_FIELDS = (
    "meter_urn",
    "measurement",
    "timestamp_policy_id",
    "timestamp_column",
    "source_timezone",
    "source_timestamp_type",
    "native_interval_seconds",
    "timestamp_origin_rule",
    "cadence_policy_id",
)

REGISTRY_COLUMNS = (
    "meter_urn",
    "measurement",
    "source_id",
    "file_pattern",
    "timestamp_policy_id",
    "timestamp_column",
    "source_timezone",
    "source_timestamp_type",
    "native_interval_seconds",
    "target_grain_minutes",
    "timestamp_origin_rule",
    "cadence_policy_id",
    "aggregation_policy",
)


@dataclass(frozen=True)
class TimestampPolicyRegistry:
    policies: Mapping[tuple[str, str], TimestampPolicy]

    def get_policy(self, *, meter_urn: str, measurement: str) -> TimestampPolicy:
        key = (meter_urn, measurement)
        try:
            return self.policies[key]
        except KeyError as exc:
            raise KeyError(f"no timestamp policy for {meter_urn}.{measurement}") from exc

    @classmethod
    def from_csv(cls, path: str | Path) -> "TimestampPolicyRegistry":
        return load_timestamp_policy_registry(path)


def load_timestamp_policy_registry(path: str | Path) -> TimestampPolicyRegistry:
    policies: dict[tuple[str, str], TimestampPolicy] = {}
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        _validate_header(reader.fieldnames)
        for line_number, row in enumerate(reader, start=2):
            policy = _policy_from_row(row, line_number=line_number)
            key = (policy.meter_urn, policy.measurement)
            if key in policies:
                raise ValueError(f"duplicate timestamp policy for {key[0]}.{key[1]}")
            policies[key] = policy
    return TimestampPolicyRegistry(policies=policies)


def _validate_header(fieldnames: list[str] | None) -> None:
    if fieldnames is None:
        raise ValueError("timestamp policy registry is missing a header")
    missing = [field for field in REQUIRED_REGISTRY_FIELDS if field not in fieldnames]
    if missing:
        raise ValueError(f"timestamp policy registry missing required column {missing[0]}")


def _policy_from_row(row: Mapping[str, Any], *, line_number: int) -> TimestampPolicy:
    for field in REQUIRED_REGISTRY_FIELDS:
        if _blank_to_none(row.get(field)) is None:
            raise ValueError(f"line {line_number}: {field} is required")
    return TimestampPolicy(
        timestamp_policy_id=str(row["timestamp_policy_id"]).strip(),
        meter_urn=str(row["meter_urn"]).strip(),
        measurement=str(row["measurement"]).strip(),
        source_id=_blank_to_none(row.get("source_id")),
        file_pattern=_blank_to_none(row.get("file_pattern")),
        timestamp_column=str(row["timestamp_column"]).strip(),
        source_timezone=str(row["source_timezone"]).strip(),
        source_timestamp_type=str(row["source_timestamp_type"]).strip(),
        native_interval_seconds=_parse_int(row["native_interval_seconds"], "native_interval_seconds", line_number=line_number),
        target_grain_minutes=_parse_optional_int(row.get("target_grain_minutes"), "target_grain_minutes", line_number=line_number),
        timestamp_origin_rule=str(row["timestamp_origin_rule"]).strip(),
        cadence_policy_id=str(row["cadence_policy_id"]).strip(),
        aggregation_policy=str(row.get("aggregation_policy") or "mean_non_cumulative").strip(),
    )


def _blank_to_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_int(value: Any, field: str, *, line_number: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"line {line_number}: {field} must be an integer") from exc


def _parse_optional_int(value: Any, field: str, *, line_number: int) -> int | None:
    if _blank_to_none(value) is None:
        return None
    return _parse_int(value, field, line_number=line_number)


__all__ = [
    "REGISTRY_COLUMNS",
    "REQUIRED_REGISTRY_FIELDS",
    "TimestampPolicyRegistry",
    "load_timestamp_policy_registry",
]
