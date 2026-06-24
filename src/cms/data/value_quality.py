"""Pure live measurement value-quality decisions.

This module is intentionally import-safe: it has no DB/Kafka clients and no I/O.
It classifies arrived measurement values before they are allowed to become
``live.measurement_event`` observed values.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

_NUMERIC_POLICY_MEASUREMENTS = frozenset({"P", "I", "V", "PF"})
# Active power can be signed depending on meter direction/export semantics.
_IMPOSSIBLE_NEGATIVE_MEASUREMENTS = frozenset({"I", "V"})
_MISSING_TEXT = frozenset({"", "none", "null"})
_UNSET = object()


@dataclass(frozen=True)
class ValueQualityDecision:
    """Result of a pure value-quality check for one arrived measurement row."""

    accepted: bool
    quarantine: bool
    reason_code: str | None = None
    warnings: tuple[str, ...] = ()
    normalized_value: float | None = None
    measurement: str = ""
    qa_stage: str = "value_quality"


def _field(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _normalize_measurement(measurement: object) -> str:
    return str(measurement or "").strip().upper()


def _parse_numeric(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("boolean is not a numeric measurement value")
    if isinstance(value, str) and value.strip().lower() in _MISSING_TEXT:
        return None
    return float(value)  # type: ignore[arg-type]


def decide_value_quality(
    event_or_measurement: object = None,
    value_numeric: object = _UNSET,
    value_text: object = _UNSET,
    *,
    missing_warning_code: str = "missing_null_observation",
) -> ValueQualityDecision:
    """Classify one live measurement value without side effects.

    ``event_or_measurement`` may be a ``MeasurementRawEvent``-like object, a
    mapping, or a measurement name.  When the value is missing/null, the decision
    is accepted with a warning by default: missing observations are not
    ``qa.bad_row`` rows.  Non-finite values, non-numeric values for known numeric
    measurements, and simple impossible physical ranges are quarantined.
    """

    if isinstance(event_or_measurement, str):
        measurement = _normalize_measurement(event_or_measurement)
        numeric_candidate = None if value_numeric is _UNSET else value_numeric
        text_candidate = None if value_text is _UNSET else value_text
    else:
        measurement = _normalize_measurement(_field(event_or_measurement, "measurement", ""))
        numeric_candidate = _field(event_or_measurement, "value_numeric", None) if value_numeric is _UNSET else value_numeric
        text_candidate = _field(event_or_measurement, "value_text", None) if value_text is _UNSET else value_text

    raw_candidate = numeric_candidate if numeric_candidate is not None else text_candidate
    try:
        normalized_value = _parse_numeric(raw_candidate)
    except (TypeError, ValueError):
        if measurement in _NUMERIC_POLICY_MEASUREMENTS:
            return ValueQualityDecision(
                accepted=False,
                quarantine=True,
                reason_code="value_non_numeric",
                measurement=measurement,
            )
        return ValueQualityDecision(
            accepted=True,
            quarantine=False,
            warnings=("unknown_measurement_range_policy", "value_not_numeric_for_unknown_policy"),
            measurement=measurement,
        )

    if normalized_value is None:
        return ValueQualityDecision(
            accepted=True,
            quarantine=False,
            warnings=(missing_warning_code,),
            normalized_value=None,
            measurement=measurement,
        )

    if not math.isfinite(normalized_value):
        return ValueQualityDecision(
            accepted=False,
            quarantine=True,
            reason_code="value_not_finite",
            normalized_value=normalized_value,
            measurement=measurement,
        )

    if measurement in _IMPOSSIBLE_NEGATIVE_MEASUREMENTS and normalized_value < 0.0:
        return ValueQualityDecision(
            accepted=False,
            quarantine=True,
            reason_code="value_negative_impossible",
            normalized_value=normalized_value,
            measurement=measurement,
        )

    if measurement == "PF" and not -1.0 <= normalized_value <= 1.0:
        return ValueQualityDecision(
            accepted=False,
            quarantine=True,
            reason_code="power_factor_out_of_range",
            normalized_value=normalized_value,
            measurement=measurement,
        )

    warnings: tuple[str, ...] = () if measurement in _NUMERIC_POLICY_MEASUREMENTS else ("unknown_measurement_range_policy",)
    return ValueQualityDecision(
        accepted=True,
        quarantine=False,
        warnings=warnings,
        normalized_value=normalized_value,
        measurement=measurement,
    )


def decide_numeric_value_quality(
    measurement: str,
    value: object,
    *,
    value_text: object = _UNSET,
) -> ValueQualityDecision:
    """Convenience wrapper for callers that have measurement/value primitives."""

    return decide_value_quality(measurement, value, value_text)


def decide_measurement_value_quality(event: object) -> ValueQualityDecision:
    """Compatibility alias for event-shaped callers."""

    return decide_value_quality(event)


__all__ = [
    "ValueQualityDecision",
    "decide_measurement_value_quality",
    "decide_numeric_value_quality",
    "decide_value_quality",
]
