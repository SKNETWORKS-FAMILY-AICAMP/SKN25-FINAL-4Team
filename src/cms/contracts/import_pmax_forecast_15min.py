"""Import P-Max 15-minute forecast contracts.

This module reflects the attached Import P-Max champion-model DB integration
specification. It is import-safe and performs no database, network, Airflow,
Kafka, AWS, or filesystem I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

IMPORT_PMAX_INPUT_TABLE = "mart.peak_feature_15min"
IMPORT_PMAX_FORECAST_TABLE = "mart.import_pmax_forecast_15min"
IMPORT_PMAX_INFERENCE_LOG_TABLE = "ops.import_pmax_inference_log"
IMPORT_PMAX_EVALUATION_TABLE = "qa.import_pmax_forecast_evaluation"

IMPORT_PMAX_MODEL_GRAIN = "15min"
IMPORT_PMAX_WINDOW_POINTS = 96
IMPORT_PMAX_QUERY_HISTORY_DAYS = 14
IMPORT_PMAX_TARGET_MEASUREMENT = "P_max"
IMPORT_PMAX_HORIZON_MINUTES = (15, 30, 45, 60)
IMPORT_PMAX_REQUIRED_MEASUREMENTS = ("P", "U1", "PF")
IMPORT_PMAX_REQUIRED_AGGREGATES = ("P_mean", "P_max", "P_std", "U1_mean", "PF_mean")
IMPORT_PMAX_LOGICAL_METER_SOURCES: Mapping[str, tuple[str, ...]] = {
    "V.Z81": ("V.Z81",),
    "V.Z82": ("V.Z82",),
    "H2.Z35x": ("H2.Z35", "H2.Z351"),
    "H2.Z36x": ("H2.Z36", "H2.Z361"),
}

ImportPmaxRunStatus = Literal["success", "degraded", "failed"]


@dataclass(frozen=True)
class ImportPmaxForecastRow:
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
class ImportPmaxValidationIssue:
    """Structured validation issue for Import P-Max forecast rows."""

    issue: str
    field: str
    expected: str | int | float | None = None
    observed: str | int | float | None = None


@dataclass(frozen=True)
class ImportPmaxRunLogContract:
    """Ops-side run log contract for quality/runtime status.

    Forecast values belong in ``mart``. Runtime execution status, failure reason,
    missing-value repair counts, and degraded flags belong in ``ops`` so they do
    not become model prediction facts.
    """

    table_name: str
    allowed_statuses: tuple[ImportPmaxRunStatus, ...]
    max_replacement_rows: int
    max_internal_missing_segments: int
    max_internal_interpolation_minutes: int
    latest_missing_single_bucket_policy: str
    latest_missing_30min_policy: str
    external_alert_thresholds_in_model_scope: bool


IMPORT_PMAX_RUN_LOG_CONTRACT = ImportPmaxRunLogContract(
    table_name=IMPORT_PMAX_INFERENCE_LOG_TABLE,
    allowed_statuses=("success", "degraded", "failed"),
    max_replacement_rows=4,
    max_internal_missing_segments=1,
    max_internal_interpolation_minutes=60,
    latest_missing_single_bucket_policy="previous_observation_degraded",
    latest_missing_30min_policy="fail",
    external_alert_thresholds_in_model_scope=False,
)


def validate_import_pmax_forecast_row(row: ImportPmaxForecastRow) -> tuple[ImportPmaxValidationIssue, ...]:
    """Validate one Import P-Max mart forecast row.

    The checks are deliberately row-local and side-effect-free. Batch uniqueness
    remains the database/key contract: ``(logical_meter, base_ts, target_ts)``.
    """

    issues: list[ImportPmaxValidationIssue] = []
    if row.logical_meter not in IMPORT_PMAX_LOGICAL_METER_SOURCES:
        issues.append(
            ImportPmaxValidationIssue(
                issue="unsupported_logical_meter",
                field="logical_meter",
                expected="|".join(IMPORT_PMAX_LOGICAL_METER_SOURCES),
                observed=row.logical_meter,
            )
        )
    elif row.source_meter_urn not in IMPORT_PMAX_LOGICAL_METER_SOURCES[row.logical_meter]:
        issues.append(
            ImportPmaxValidationIssue(
                issue="source_meter_not_allowed_for_logical_meter",
                field="source_meter_urn",
                expected="|".join(IMPORT_PMAX_LOGICAL_METER_SOURCES[row.logical_meter]),
                observed=row.source_meter_urn,
            )
        )

    if not _is_15min_aligned(row.base_ts):
        issues.append(ImportPmaxValidationIssue("base_ts_not_15min_aligned", "base_ts", "15min boundary", row.base_ts.isoformat()))
    if not _is_15min_aligned(row.input_end_ts):
        issues.append(ImportPmaxValidationIssue("input_end_ts_not_15min_aligned", "input_end_ts", "15min boundary", row.input_end_ts.isoformat()))
    if not _is_15min_aligned(row.target_ts):
        issues.append(ImportPmaxValidationIssue("target_ts_not_15min_aligned", "target_ts", "15min boundary", row.target_ts.isoformat()))

    if row.horizon_minutes not in IMPORT_PMAX_HORIZON_MINUTES:
        issues.append(
            ImportPmaxValidationIssue(
                "unsupported_horizon_minutes",
                "horizon_minutes",
                ",".join(str(horizon) for horizon in IMPORT_PMAX_HORIZON_MINUTES),
                row.horizon_minutes,
            )
        )
    else:
        expected_target_ts = row.base_ts + timedelta(minutes=row.horizon_minutes)
        if row.target_ts != expected_target_ts:
            issues.append(
                ImportPmaxValidationIssue(
                    "target_ts_must_equal_base_ts_plus_horizon",
                    "target_ts",
                    expected_target_ts.isoformat(),
                    row.target_ts.isoformat(),
                )
            )

    expected_input_end_ts = row.base_ts - timedelta(minutes=15)
    if row.input_end_ts != expected_input_end_ts:
        issues.append(
            ImportPmaxValidationIssue(
                "input_end_ts_must_be_base_ts_minus_15min",
                "input_end_ts",
                expected_input_end_ts.isoformat(),
                row.input_end_ts.isoformat(),
            )
        )

    if row.predicted_p_max < 0:
        issues.append(ImportPmaxValidationIssue("predicted_p_max_must_be_nonnegative", "predicted_p_max", 0, row.predicted_p_max))

    return tuple(issues)


def actual_window_ts_for_forecast(row: ImportPmaxForecastRow) -> datetime:
    """Return the actual ``mart.peak_feature_15min.window_ts`` for evaluation."""

    return row.target_ts - timedelta(minutes=15)


def _is_15min_aligned(ts: datetime) -> bool:
    return ts.minute % 15 == 0 and ts.second == 0 and ts.microsecond == 0


__all__ = [
    "IMPORT_PMAX_EVALUATION_TABLE",
    "IMPORT_PMAX_FORECAST_TABLE",
    "IMPORT_PMAX_HORIZON_MINUTES",
    "IMPORT_PMAX_INFERENCE_LOG_TABLE",
    "IMPORT_PMAX_INPUT_TABLE",
    "IMPORT_PMAX_LOGICAL_METER_SOURCES",
    "IMPORT_PMAX_MODEL_GRAIN",
    "IMPORT_PMAX_QUERY_HISTORY_DAYS",
    "IMPORT_PMAX_REQUIRED_AGGREGATES",
    "IMPORT_PMAX_REQUIRED_MEASUREMENTS",
    "IMPORT_PMAX_RUN_LOG_CONTRACT",
    "IMPORT_PMAX_TARGET_MEASUREMENT",
    "IMPORT_PMAX_WINDOW_POINTS",
    "ImportPmaxForecastRow",
    "ImportPmaxRunLogContract",
    "ImportPmaxRunStatus",
    "ImportPmaxValidationIssue",
    "actual_window_ts_for_forecast",
    "validate_import_pmax_forecast_row",
]
