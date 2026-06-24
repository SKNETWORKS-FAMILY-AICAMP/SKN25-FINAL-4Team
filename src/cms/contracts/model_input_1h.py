"""Pure 1-hour model input contract for champion-model adapters.

This module is import-safe: it defines only dataclasses and validation helpers.
It performs no database, network, AWS, or filesystem I/O.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

MeterKind = Literal["electric", "heat"]

INPUT_GRAIN_1H = "1h"
HISTORY_HOURS = 168
ELECTRIC_REQUIRED_FEATURES = ("P", "U1", "PF")
HEAT_REQUIRED_FEATURES = ("P", "qv", "Tdiff")
REQUIRED_FEATURES_BY_METER_KIND: Mapping[str, tuple[str, ...]] = {
    "electric": ELECTRIC_REQUIRED_FEATURES,
    "heat": HEAT_REQUIRED_FEATURES,
}


@dataclass(frozen=True)
class ModelInput1HRow:
    """One canonical 1-hour feature row for a single meter timestamp."""

    meter_urn: str
    meter_kind: MeterKind | str
    ts: datetime
    features: Mapping[str, float | int | None]


@dataclass(frozen=True)
class ValidationIssue:
    """Structured validation issue for a model input row or meter history."""

    meter_urn: str
    ts: datetime | None
    issue: str
    feature: str | None = None
    expected: int | str | None = None
    observed: int | str | None = None


@dataclass(frozen=True)
class ModelInput1HValidationResult:
    """Side-effect-free validation result for a batch of model input rows."""

    issues: tuple[ValidationIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def missing_features(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.issue == "missing_feature")


class ModelInput1HValidationError(ValueError):
    """Raised when a model adapter receives invalid 1-hour model input."""

    def __init__(self, result: ModelInput1HValidationResult) -> None:
        super().__init__("model_input_1h validation failed", result)
        self.result = result


def validate_model_input_1h(rows: Iterable[ModelInput1HRow], *, base_ts: datetime | None = None) -> ModelInput1HValidationResult:
    """Validate the 1-hour champion model input contract.

    Required checks:
    * timestamps are aligned exactly to the hour;
    * no duplicate ``meter_urn`` + ``ts`` rows;
    * electric meters contain exactly ``P``, ``U1``, and ``PF``;
    * heat meters contain exactly ``P``, ``qv``, and ``Tdiff``;
    * every meter has a contiguous recent 168-hour lookback ending at explicit
      ``base_ts`` or the meter's latest timestamp when omitted.
    """

    materialized_rows = tuple(rows)
    issues: list[ValidationIssue] = []
    seen_keys: set[tuple[str, datetime]] = set()
    unique_ts_by_meter: dict[str, set[datetime]] = defaultdict(set)

    for row in materialized_rows:
        if not _is_1h_aligned(row.ts):
            issues.append(ValidationIssue(meter_urn=row.meter_urn, ts=row.ts, issue="ts_not_1h_aligned"))

        key = (row.meter_urn, row.ts)
        if key in seen_keys:
            issues.append(ValidationIssue(meter_urn=row.meter_urn, ts=row.ts, issue="duplicate_meter_urn_ts"))
        else:
            seen_keys.add(key)
        unique_ts_by_meter[row.meter_urn].add(row.ts)

        required_features = REQUIRED_FEATURES_BY_METER_KIND.get(row.meter_kind)
        if required_features is None:
            issues.append(
                ValidationIssue(
                    meter_urn=row.meter_urn,
                    ts=row.ts,
                    issue="unsupported_meter_kind",
                    expected="electric|heat",
                    observed=str(row.meter_kind),
                )
            )
            continue

        unexpected_features = sorted(set(row.features) - set(required_features))
        for feature in unexpected_features:
            issues.append(
                ValidationIssue(
                    meter_urn=row.meter_urn,
                    ts=row.ts,
                    issue="unexpected_feature",
                    feature=feature,
                    expected=",".join(required_features),
                    observed=feature,
                )
            )

        for feature in required_features:
            if _is_missing_feature_value(row.features.get(feature)):
                issues.append(ValidationIssue(meter_urn=row.meter_urn, ts=row.ts, issue="missing_feature", feature=feature))

    for meter_urn in sorted(unique_ts_by_meter):
        meter_base_ts = base_ts if base_ts is not None else max(unique_ts_by_meter[meter_urn])
        window_start_ts = meter_base_ts - timedelta(hours=HISTORY_HOURS - 1)
        recent_timestamps = {ts for ts in unique_ts_by_meter[meter_urn] if window_start_ts <= ts <= meter_base_ts}
        observed = len(recent_timestamps)

        for ts in sorted(unique_ts_by_meter[meter_urn]):
            if ts > meter_base_ts:
                issues.append(
                    ValidationIssue(
                        meter_urn=meter_urn,
                        ts=ts,
                        issue="future_row_after_base_ts",
                        expected=meter_base_ts.isoformat(),
                        observed=ts.isoformat(),
                    )
                )

        if observed < HISTORY_HOURS:
            issues.append(
                ValidationIssue(
                    meter_urn=meter_urn,
                    ts=None,
                    issue="insufficient_history_hours",
                    expected=f">={HISTORY_HOURS}",
                    observed=observed,
                )
            )

        expected_timestamps = tuple(window_start_ts + timedelta(hours=offset) for offset in range(HISTORY_HOURS))
        for ts in expected_timestamps:
            if ts not in recent_timestamps:
                issues.append(
                    ValidationIssue(
                        meter_urn=meter_urn,
                        ts=ts,
                        issue="missing_timestamp",
                        expected="present",
                        observed="missing",
                    )
                )

        sorted_recent_timestamps = sorted(recent_timestamps)
        for previous_ts, next_ts in zip(sorted_recent_timestamps, sorted_recent_timestamps[1:], strict=False):
            gap_hours = int((next_ts - previous_ts).total_seconds() // 3600)
            if gap_hours != 1:
                issues.append(
                    ValidationIssue(
                        meter_urn=meter_urn,
                        ts=next_ts,
                        issue="gap",
                        expected="1h",
                        observed=f"{gap_hours}h",
                    )
                )

    return ModelInput1HValidationResult(issues=tuple(issues))


def assert_valid_model_input_1h(rows: Iterable[ModelInput1HRow], *, base_ts: datetime | None = None) -> ModelInput1HValidationResult:
    """Validate rows and raise a structured error when the contract fails."""

    result = validate_model_input_1h(rows, base_ts=base_ts)
    if not result.ok:
        raise ModelInput1HValidationError(result)
    return result


def _is_1h_aligned(ts: datetime) -> bool:
    return ts.minute == 0 and ts.second == 0 and ts.microsecond == 0


def _is_missing_feature_value(value: float | int | None) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return False


__all__ = [
    "ELECTRIC_REQUIRED_FEATURES",
    "HEAT_REQUIRED_FEATURES",
    "HISTORY_HOURS",
    "INPUT_GRAIN_1H",
    "MeterKind",
    "ModelInput1HRow",
    "ModelInput1HValidationError",
    "ModelInput1HValidationResult",
    "REQUIRED_FEATURES_BY_METER_KIND",
    "ValidationIssue",
    "assert_valid_model_input_1h",
    "validate_model_input_1h",
]
