"""P-Max 15-minute forecast contracts.

This module reflects the attached P-Max champion-model DB integration
specification. It is import-safe and performs no database, network, Airflow,
Kafka, AWS, or filesystem I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

PMAX_FORECAST_INPUT_TABLE = "mart.peak_feature_15min"
PMAX_FORECAST_TABLE = "mart.pmax_forecast_15min"
PMAX_FORECAST_INFERENCE_LOG_TABLE = "ops.pmax_forecast_inference_log"
PMAX_FORECAST_EVALUATION_TABLE = "qa.pmax_forecast_evaluation"

PMAX_FORECAST_MODEL_GRAIN = "15min"
PMAX_FORECAST_WINDOW_POINTS = 96
PMAX_FORECAST_QUERY_HISTORY_DAYS = 14
PMAX_FORECAST_TARGET_MEASUREMENT = "P_max"
PMAX_FORECAST_HORIZON_MINUTES = (15, 30, 45, 60)
PMAX_FORECAST_REQUIRED_MEASUREMENTS = ("P", "U1", "PF")
PMAX_FORECAST_REQUIRED_AGGREGATES = ("P_mean", "P_max", "P_std", "U1_mean", "PF_mean")
PMAX_FORECAST_LOGICAL_METER_SOURCES: Mapping[str, tuple[str, ...]] = {
    "V.Z81": ("V.Z81",),
    "V.Z82": ("V.Z82",),
    "H2.Z35x": ("H2.Z35", "H2.Z351"),
    "H2.Z36x": ("H2.Z36", "H2.Z361"),
}

PmaxForecastRunStatus = Literal["success", "degraded", "failed"]


@dataclass(frozen=True)
class PmaxForecastRow:
    """One mart forecast row for one logical meter and one 15-minute horizon.

    ``target_ts`` is the predicted bucket end timestamp. The actual bucket start
    for post-hoc joins is ``target_ts - 15 minutes``.
    """

    logical_meter: str
    source_meter_urn: str
    base_ts: datetime
    input_end_ts: datetime
    target_ts: datetime
    horizon_minutes: int
    predicted_p_max: float
    created_at: datetime


@dataclass(frozen=True)
class PmaxForecastValidationIssue:
    """Structured validation issue for P-Max forecast rows."""

    issue: str
    field: str
    expected: str | int | float | None = None
    observed: str | int | float | None = None


@dataclass(frozen=True)
class PmaxForecastRunLogContract:
    """Ops-side run log contract for quality/runtime status.

    Forecast values belong in ``mart``. Runtime execution status, failure reason,
    missing-value repair counts, and degraded flags belong in ``ops`` so they do
    not become model prediction facts.
    """

    table_name: str
    allowed_statuses: tuple[PmaxForecastRunStatus, ...]
    max_replacement_rows: int
    max_internal_missing_segments: int
    max_internal_interpolation_minutes: int
    latest_missing_single_bucket_policy: str
    latest_missing_30min_policy: str
    external_alert_thresholds_in_model_scope: bool


PMAX_FORECAST_RUN_LOG_CONTRACT = PmaxForecastRunLogContract(
    table_name=PMAX_FORECAST_INFERENCE_LOG_TABLE,
    allowed_statuses=("success", "degraded", "failed"),
    max_replacement_rows=4,
    max_internal_missing_segments=1,
    max_internal_interpolation_minutes=60,
    latest_missing_single_bucket_policy="previous_observation_degraded",
    latest_missing_30min_policy="fail",
    external_alert_thresholds_in_model_scope=False,
)


def validate_pmax_forecast_row(row: PmaxForecastRow) -> tuple[PmaxForecastValidationIssue, ...]:
    """Validate one P-Max mart forecast row.

    The checks are deliberately row-local and side-effect-free. Batch uniqueness
    remains the database/key contract: ``(logical_meter, base_ts, target_ts)``.
    """

    issues: list[PmaxForecastValidationIssue] = []
    if row.logical_meter not in PMAX_FORECAST_LOGICAL_METER_SOURCES:
        issues.append(
            PmaxForecastValidationIssue(
                issue="unsupported_logical_meter",
                field="logical_meter",
                expected="|".join(PMAX_FORECAST_LOGICAL_METER_SOURCES),
                observed=row.logical_meter,
            )
        )
    elif row.source_meter_urn not in PMAX_FORECAST_LOGICAL_METER_SOURCES[row.logical_meter]:
        issues.append(
            PmaxForecastValidationIssue(
                issue="source_meter_not_allowed_for_logical_meter",
                field="source_meter_urn",
                expected="|".join(PMAX_FORECAST_LOGICAL_METER_SOURCES[row.logical_meter]),
                observed=row.source_meter_urn,
            )
        )

    if not _is_15min_aligned(row.base_ts):
        issues.append(PmaxForecastValidationIssue("base_ts_not_15min_aligned", "base_ts", "15min boundary", row.base_ts.isoformat()))
    if not _is_15min_aligned(row.input_end_ts):
        issues.append(PmaxForecastValidationIssue("input_end_ts_not_15min_aligned", "input_end_ts", "15min boundary", row.input_end_ts.isoformat()))
    if not _is_15min_aligned(row.target_ts):
        issues.append(PmaxForecastValidationIssue("target_ts_not_15min_aligned", "target_ts", "15min boundary", row.target_ts.isoformat()))

    if row.horizon_minutes not in PMAX_FORECAST_HORIZON_MINUTES:
        issues.append(
            PmaxForecastValidationIssue(
                "unsupported_horizon_minutes",
                "horizon_minutes",
                ",".join(str(horizon) for horizon in PMAX_FORECAST_HORIZON_MINUTES),
                row.horizon_minutes,
            )
        )
    else:
        expected_target_ts = row.base_ts + timedelta(minutes=row.horizon_minutes)
        if row.target_ts != expected_target_ts:
            issues.append(
                PmaxForecastValidationIssue(
                    "target_ts_must_equal_base_ts_plus_horizon",
                    "target_ts",
                    expected_target_ts.isoformat(),
                    row.target_ts.isoformat(),
                )
            )

    expected_input_end_ts = row.base_ts - timedelta(minutes=15)
    if row.input_end_ts != expected_input_end_ts:
        issues.append(
            PmaxForecastValidationIssue(
                "input_end_ts_must_be_base_ts_minus_15min",
                "input_end_ts",
                expected_input_end_ts.isoformat(),
                row.input_end_ts.isoformat(),
            )
        )

    if row.predicted_p_max < 0:
        issues.append(PmaxForecastValidationIssue("predicted_p_max_must_be_nonnegative", "predicted_p_max", 0, row.predicted_p_max))

    return tuple(issues)


def actual_window_ts_for_forecast(row: PmaxForecastRow) -> datetime:
    """Return the actual ``mart.peak_feature_15min.window_ts`` for evaluation."""

    return row.target_ts - timedelta(minutes=15)


def _is_15min_aligned(ts: datetime) -> bool:
    return ts.minute % 15 == 0 and ts.second == 0 and ts.microsecond == 0


__all__ = [
    "PMAX_FORECAST_EVALUATION_TABLE",
    "PMAX_FORECAST_TABLE",
    "PMAX_FORECAST_HORIZON_MINUTES",
    "PMAX_FORECAST_INFERENCE_LOG_TABLE",
    "PMAX_FORECAST_INPUT_TABLE",
    "PMAX_FORECAST_LOGICAL_METER_SOURCES",
    "PMAX_FORECAST_MODEL_GRAIN",
    "PMAX_FORECAST_QUERY_HISTORY_DAYS",
    "PMAX_FORECAST_REQUIRED_AGGREGATES",
    "PMAX_FORECAST_REQUIRED_MEASUREMENTS",
    "PMAX_FORECAST_RUN_LOG_CONTRACT",
    "PMAX_FORECAST_TARGET_MEASUREMENT",
    "PMAX_FORECAST_WINDOW_POINTS",
    "PmaxForecastRow",
    "PmaxForecastRunLogContract",
    "PmaxForecastRunStatus",
    "PmaxForecastValidationIssue",
    "actual_window_ts_for_forecast",
    "validate_pmax_forecast_row",
]
