"""1-hour anomaly warning model contracts.

This module reflects the shared ``test6_residual`` v84 3-hour anomaly-warning
artifact. It is import-safe and performs no database, network, Airflow, Kafka,
AWS, or filesystem I/O.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

ANOMALY_DETECTION_RELEASE = "test6_residual_v84_3h_share_20260609"
ANOMALY_DETECTION_RELEASE_SHA256 = "42ab201786939448e060bf2b544bda241b84a993ef55e81d75fe19894de7ce98"
ANOMALY_DETECTION_MODEL_VERSION = "v84"
ANOMALY_DETECTION_HORIZON_HOURS = 3
ANOMALY_DETECTION_LEAD_STEPS = (1, 2, 3)
ANOMALY_DETECTION_HISTORY_HOURS = 343
ANOMALY_DETECTION_TARGET = "P"
ANOMALY_DETECTION_BUCKET_GRAIN = "1h"
ANOMALY_DETECTION_FEATURE_TABLE = "mart.anomaly_feature_1h"
ANOMALY_DETECTION_OBSERVED_SOURCE_TABLE = "live.measurement_1h"
ANOMALY_DETECTION_FORECAST_TABLE = "mart.anomaly_warning_1h"
ANOMALY_DETECTION_INFERENCE_LOG_TABLE = "ops.anomaly_warning_inference_log"
ANOMALY_DETECTION_EVALUATION_TABLE = "qa.anomaly_warning_evaluation"
ANOMALY_DETECTION_ALLOWED_EVIDENCE_TABLES = (
    ANOMALY_DETECTION_FEATURE_TABLE,
    ANOMALY_DETECTION_FORECAST_TABLE,
    ANOMALY_DETECTION_INFERENCE_LOG_TABLE,
    ANOMALY_DETECTION_EVALUATION_TABLE,
)
ANOMALY_DETECTION_ARTIFACT_ADAPTER_STUB = "anomaly_detection_artifact_stub"

ANOMALY_DETECTION_ELECTRIC_FEATURE_COLUMNS = ("P", "U1", "PF")
ANOMALY_DETECTION_ELECTRIC_NO_PF_FEATURE_COLUMNS = ("P", "U1")
ANOMALY_DETECTION_HEAT_FEATURE_COLUMNS = ("P", "qv", "Tdiff")
ANOMALY_DETECTION_DERIVED_FEATURE_COLUMNS = (
    "hour_sin",
    "hour_cos",
    "day_of_week_sin",
    "day_of_week_cos",
    "month_sin",
    "month_cos",
    "diff_lag24",
    "diff_lag168",
    "is_workday",
    "rolling_mean_24h",
)
ANOMALY_DETECTION_ALLOWED_FEATURE_SETS = (
    ANOMALY_DETECTION_ELECTRIC_FEATURE_COLUMNS + ANOMALY_DETECTION_DERIVED_FEATURE_COLUMNS,
    ANOMALY_DETECTION_ELECTRIC_NO_PF_FEATURE_COLUMNS + ANOMALY_DETECTION_DERIVED_FEATURE_COLUMNS,
    ANOMALY_DETECTION_HEAT_FEATURE_COLUMNS + ANOMALY_DETECTION_DERIVED_FEATURE_COLUMNS,
)
ANOMALY_DETECTION_PREDICT_METERS = (
    "H1.K11",
    "H1.K12",
    "H1.K14",
    "H1.K15",
    "H1.K16",
    "H1.W11",
    "H1.W12",
    "H1.Z10",
    "H1.Z11",
    "H1.Z12",
    "H1.Z13",
    "H1.Z14",
    "H1.Z16",
    "H1.Z18",
    "H1.Z19",
    "H1.Z20",
    "H1.Z21",
    "H1.Z22",
    "H1.Z23",
    "H1.Z24",
    "H1.Z25",
    "H1.Z26",
    "H1.Z27",
    "H1.Z310",
    "H1.ZE20",
    "H2.K21",
    "H2.T.Z31",
    "H2.T.Z32",
    "H2.Z311",
    "H2.Z61",
    "H2.Z62",
    "H2.Z63",
    "H2.Z64",
    "H2.Z65",
    "H2.Z66",
    "H2.Z67",
    "H2.Z68",
    "H2.Z69",
    "H2.Z70",
    "H2.ZE64",
    "H2.ZE65",
    "H2.ZE66",
    "H2.ZE67",
    "H2.ZE74",
    "H3.Z312",
    "H3.Z42",
    "H3.Z43",
    "H3.Z44",
    "H3.Z45",
    "H3.Z46",
    "H3.Z47",
    "H3.Z48",
    "H3.Z49",
    "H3.Z71",
    "H3.ZE43",
    "H3.ZE44",
    "H4.Z50",
    "H4.Z51",
    "H4.ZE50",
    "H4.ZE51",
    "V.K21",
    "V.Z84",
    "V.ZE84",
)
ANOMALY_DETECTION_METER_MODEL_URN_MAP: Mapping[str, str] = {
    "H2.Z66": "H2.Z66",
    "H2.ZE66": "H2.ZE66",
    "H1.Z12": "H1.Z12",
    "H4.Z51": "H4.Z51",
    "H2.T.Z31": "H2.T.Z31",
    "H1.Z13": "H1.Z13",
    "H1.Z21": "H1.Z21",
    "H1.Z24": "H1.Z24",
    "H2.Z64": "H2.Z64",
    "H3.Z43": "H3.Z43",
    "H3.Z44": "H3.Z44",
    "H3.Z48": "H3.Z48",
    "H4.Z50": "H4.Z50",
    "V.Z84": "V.Z84",
    "H1.Z20": "H1.Z20",
    "H1.Z10": "H1.Z10",
    "H1.Z16": "H1.Z16",
    "H1.Z18": "H1.Z18",
    "H1.Z19": "H1.Z19",
    "H1.Z23": "H1.Z23",
    "H1.Z26": "H1.Z26",
    "H1.Z27": "H1.Z27",
    "H2.Z61": "H2.Z61",
    "H2.Z62": "H2.Z62",
    "H2.Z63": "H2.Z63",
    "H2.Z65": "H2.Z65",
    "H2.Z68": "H2.Z68",
    "H2.Z69": "H2.Z69",
    "H2.ZE65": "H2.ZE65",
    "H2.ZE74": "H2.ZE74",
    "H3.Z42": "H3.Z42",
    "H3.Z45": "H3.Z45",
    "H3.Z46": "H3.Z46",
    "H3.Z47": "H3.Z47",
    "H3.Z71": "H3.Z71",
    "H2.Z311": "H2.Z311",
    "H2.ZE67": "H2.ZE67",
    "H2.T.Z32": "H2.T.Z32",
    "H2.Z70": "H2.Z70",
    "H3.ZE44": "H3.ZE44",
    "H3.Z49": "H3.Z49",
    "V.ZE84": "V.ZE84",
    "V.K21": "V.K21",
    "H1.K11": "H1.K11",
    "H1.K12": "H1.K12",
    "H1.K14": "H1.K14",
    "H1.K15": "H1.K15",
    "H1.K16": "H1.K16",
    "H2.K21": "H2.K21",
    "H1.W11": "H1.W11",
    "H1.W12": "H1.W12",
    "H2.Z67": "H2.Z66",
    "H1.Z11": "H1.Z12",
    "H4.ZE51": "H4.Z51",
    "H1.Z14": "H1.Z13",
    "H1.Z22": "H1.Z21",
    "H1.Z25": "H1.Z24",
    "H2.ZE64": "H2.Z64",
    "H3.ZE43": "H3.Z43",
    "H4.ZE50": "H4.Z50",
    "H1.Z310": "V.Z84",
    "H3.Z312": "V.Z84",
    "H1.ZE20": "H1.Z20",
}
ANOMALY_DETECTION_MODEL_METERS = ANOMALY_DETECTION_PREDICT_METERS
ANOMALY_DETECTION_ARTIFACT_MODEL_URNS = tuple(dict.fromkeys(ANOMALY_DETECTION_METER_MODEL_URN_MAP.values()))
ANOMALY_DETECTION_DIRECT_MODEL_METERS = tuple(meter for meter, model_urn in ANOMALY_DETECTION_METER_MODEL_URN_MAP.items() if meter == model_urn)
ANOMALY_DETECTION_TRANSFER_MEMBER_MODEL_URN_MAP = {
    meter: model_urn for meter, model_urn in ANOMALY_DETECTION_METER_MODEL_URN_MAP.items() if meter != model_urn
}

AnomalyRunStatus = Literal["success", "insufficient_data", "no_artifact", "error"]
AnomalyWarningType = Literal["high", "low", "none"]
AnomalyInputQuality = Literal["good", "warning", "bad"]
AnomalyReasonCode = Literal[
    "NO_PREDICTION",
    "KNOWN_METER_ISSUE",
    "INPUT_QUALITY_ISSUE",
    "HIGH_LOAD_VS_USUAL_HOUR",
    "LOW_LOAD_VS_USUAL_HOUR",
    "NONE",
]
ANOMALY_DETECTION_ALLOWED_STATUSES = ("success", "insufficient_data", "no_artifact", "error")
ANOMALY_DETECTION_ALLOWED_WARNING_TYPES = ("high", "low", "none")
ANOMALY_DETECTION_ALLOWED_INPUT_QUALITIES = ("good", "warning", "bad")
ANOMALY_DETECTION_ALLOWED_REASON_CODES = (
    "NO_PREDICTION",
    "KNOWN_METER_ISSUE",
    "INPUT_QUALITY_ISSUE",
    "HIGH_LOAD_VS_USUAL_HOUR",
    "LOW_LOAD_VS_USUAL_HOUR",
    "NONE",
)


@dataclass(frozen=True)
class AnomalyDetectionValidationIssue:
    """Structured validation issue for anomaly warning rows."""

    issue: str
    field: str
    expected: str | int | float | None = None
    observed: str | int | float | None = None


@dataclass(frozen=True)
class AnomalyDetectionWideRow:
    """One artifact-style wide anomaly warning row for one forecast origin."""

    meter_urn: str
    model_urn: str
    forecast_origin_ts: datetime
    horizon_hours: int
    status: AnomalyRunStatus
    pred_t_plus_1: float | None
    pred_t_plus_2: float | None
    pred_t_plus_3: float | None
    threshold_lower_t_plus_1: float | None
    threshold_lower_t_plus_2: float | None
    threshold_lower_t_plus_3: float | None
    threshold_upper_t_plus_1: float | None
    threshold_upper_t_plus_2: float | None
    threshold_upper_t_plus_3: float | None
    warning_t_plus_1: bool
    warning_t_plus_2: bool
    warning_t_plus_3: bool
    warning_type_t_plus_1: AnomalyWarningType
    warning_type_t_plus_2: AnomalyWarningType
    warning_type_t_plus_3: AnomalyWarningType
    warning_flag: bool
    physical_flag: bool
    physical_issue_types: str | None
    physical_issue_count: int
    physical_issue_recent_count: int
    physical_issue_pattern: str
    input_quality: AnomalyInputQuality
    input_missing_count: int
    input_physical_count: int
    input_imputed_count: int
    warning_reason_code: AnomalyReasonCode
    warning_reason_detail: str | None
    created_at: datetime
    source_input_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnomalyDetectionLongRow:
    """DB-friendly long warning row for one meter, origin, and lead step."""

    meter_urn: str
    model_urn: str
    forecast_origin_ts: datetime
    target_ts: datetime
    lead_step: int
    horizon_hours: int
    predicted_p: float | None
    threshold_lower: float | None
    threshold_upper: float | None
    warning_flag: bool
    warning_type: AnomalyWarningType
    status: AnomalyRunStatus
    physical_flag: bool
    input_quality: AnomalyInputQuality
    warning_reason_code: AnomalyReasonCode
    created_at: datetime
    source_input_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnomalyDetectionArtifactBoundary:
    """Repo-local boundary for the external anomaly detection artifact."""

    adapter_name: str = ANOMALY_DETECTION_ARTIFACT_ADAPTER_STUB
    release_name: str | None = ANOMALY_DETECTION_RELEASE
    model_version: str | None = ANOMALY_DETECTION_MODEL_VERSION
    drive_artifact_verified: bool = False
    external_io_enabled: bool = False
    artifact_uri: str | None = None

    @property
    def available(self) -> bool:
        return bool(self.drive_artifact_verified and self.artifact_uri and self.model_version and not self.external_io_enabled)


def validate_anomaly_detection_wide_row(row: AnomalyDetectionWideRow) -> tuple[AnomalyDetectionValidationIssue, ...]:
    """Validate one artifact-style anomaly warning row."""

    issues: list[AnomalyDetectionValidationIssue] = []
    if row.horizon_hours != ANOMALY_DETECTION_HORIZON_HOURS:
        issues.append(AnomalyDetectionValidationIssue("unsupported_horizon_hours", "horizon_hours", ANOMALY_DETECTION_HORIZON_HOURS, row.horizon_hours))
    if row.meter_urn not in ANOMALY_DETECTION_MODEL_METERS:
        issues.append(AnomalyDetectionValidationIssue("unsupported_meter_urn", "meter_urn", "known v84 artifact meter", row.meter_urn))
    else:
        expected_model_urn = anomaly_model_urn_for_meter(row.meter_urn)
        if row.model_urn != expected_model_urn:
            issues.append(AnomalyDetectionValidationIssue("model_urn_routing_mismatch", "model_urn", expected_model_urn, row.model_urn))
    if row.status not in ANOMALY_DETECTION_ALLOWED_STATUSES:
        issues.append(AnomalyDetectionValidationIssue("unsupported_status", "status", "|".join(ANOMALY_DETECTION_ALLOWED_STATUSES), row.status))
    if row.input_quality not in ANOMALY_DETECTION_ALLOWED_INPUT_QUALITIES:
        issues.append(
            AnomalyDetectionValidationIssue("unsupported_input_quality", "input_quality", "|".join(ANOMALY_DETECTION_ALLOWED_INPUT_QUALITIES), row.input_quality)
        )
    if row.warning_reason_code not in ANOMALY_DETECTION_ALLOWED_REASON_CODES:
        issues.append(
            AnomalyDetectionValidationIssue(
                "unsupported_warning_reason_code",
                "warning_reason_code",
                "|".join(ANOMALY_DETECTION_ALLOWED_REASON_CODES),
                row.warning_reason_code,
            )
        )
    for step in ANOMALY_DETECTION_LEAD_STEPS:
        warning_type = _step_value(row, "warning_type", step)
        if warning_type not in ANOMALY_DETECTION_ALLOWED_WARNING_TYPES:
            issues.append(
                AnomalyDetectionValidationIssue("unsupported_warning_type", f"warning_type_t_plus_{step}", "|".join(ANOMALY_DETECTION_ALLOWED_WARNING_TYPES), warning_type)
            )
    if not _is_1h_aligned(row.forecast_origin_ts):
        issues.append(AnomalyDetectionValidationIssue("forecast_origin_ts_not_1h_aligned", "forecast_origin_ts", "1h boundary", row.forecast_origin_ts.isoformat()))
    if row.status == "success":
        for step in ANOMALY_DETECTION_LEAD_STEPS:
            value = _step_value(row, "pred", step)
            if value is None or not _is_finite(value):
                issues.append(AnomalyDetectionValidationIssue("missing_or_invalid_prediction", f"pred_t_plus_{step}", "finite float", value))
            lower = _step_value(row, "threshold_lower", step)
            upper = _step_value(row, "threshold_upper", step)
            if lower is None or upper is None or not _is_finite(lower) or not _is_finite(upper):
                issues.append(AnomalyDetectionValidationIssue("missing_or_invalid_threshold", f"threshold_t_plus_{step}", "finite lower/upper", None))
            elif float(lower) > float(upper):
                issues.append(AnomalyDetectionValidationIssue("threshold_lower_gt_upper", f"threshold_t_plus_{step}", "lower <= upper", f"{lower}>{upper}"))
    if row.warning_flag != any(_step_value(row, "warning", step) for step in ANOMALY_DETECTION_LEAD_STEPS):
        issues.append(AnomalyDetectionValidationIssue("warning_flag_not_step_or", "warning_flag", "OR(step warnings)", str(row.warning_flag)))
    for count_field in ("physical_issue_count", "physical_issue_recent_count", "input_missing_count", "input_physical_count", "input_imputed_count"):
        if getattr(row, count_field) < 0:
            issues.append(AnomalyDetectionValidationIssue("negative_count", count_field, 0, getattr(row, count_field)))
    return tuple(issues)


def anomaly_wide_to_long_rows(row: AnomalyDetectionWideRow) -> tuple[AnomalyDetectionLongRow, ...]:
    """Convert one artifact-style wide row into one row per lead step."""

    issues = validate_anomaly_detection_wide_row(row)
    if issues:
        names = ",".join(issue.issue for issue in issues)
        raise ValueError(f"invalid anomaly detection wide row: {names}")
    return tuple(
        AnomalyDetectionLongRow(
            meter_urn=row.meter_urn,
            model_urn=row.model_urn,
            forecast_origin_ts=row.forecast_origin_ts,
            target_ts=row.forecast_origin_ts + timedelta(hours=step),
            lead_step=step,
            horizon_hours=row.horizon_hours,
            predicted_p=_step_value(row, "pred", step),
            threshold_lower=_step_value(row, "threshold_lower", step),
            threshold_upper=_step_value(row, "threshold_upper", step),
            warning_flag=bool(_step_value(row, "warning", step)),
            warning_type=_step_value(row, "warning_type", step),
            status=row.status,
            physical_flag=row.physical_flag,
            input_quality=row.input_quality,
            warning_reason_code=row.warning_reason_code,
            created_at=row.created_at,
            source_input_refs=row.source_input_refs,
        )
        for step in ANOMALY_DETECTION_LEAD_STEPS
    )


def validate_anomaly_detection_batch(rows: Iterable[AnomalyDetectionLongRow]) -> tuple[AnomalyDetectionValidationIssue, ...]:
    """Validate long-row batch uniqueness and timestamp semantics."""

    issues: list[AnomalyDetectionValidationIssue] = []
    seen: set[tuple[str, datetime, int]] = set()
    for row in rows:
        key = (row.meter_urn, row.forecast_origin_ts, row.lead_step)
        if key in seen:
            issues.append(AnomalyDetectionValidationIssue("duplicate_long_row_key", "meter_urn,forecast_origin_ts,lead_step", None, str(key)))
        seen.add(key)
        if row.lead_step not in ANOMALY_DETECTION_LEAD_STEPS:
            issues.append(AnomalyDetectionValidationIssue("unsupported_lead_step", "lead_step", "1|2|3", row.lead_step))
        if row.target_ts != row.forecast_origin_ts + timedelta(hours=row.lead_step):
            issues.append(AnomalyDetectionValidationIssue("target_ts_mismatch", "target_ts", "origin + lead_step hours", row.target_ts.isoformat()))
        for source_ref in row.source_input_refs:
            if source_ref.strip().lower().startswith("canonical."):
                issues.append(AnomalyDetectionValidationIssue("canonical_source_ref_forbidden", "source_input_refs", ANOMALY_DETECTION_FEATURE_TABLE, source_ref))
    return tuple(issues)


def anomaly_model_urn_for_meter(meter_urn: str) -> str:
    """Return the v84 artifact ``model_urn`` used to predict ``meter_urn``."""

    try:
        return ANOMALY_DETECTION_METER_MODEL_URN_MAP[meter_urn]
    except KeyError as exc:
        raise ValueError(f"unsupported anomaly meter_urn: {meter_urn}") from exc


def anomaly_meters_for_model_urn(model_urn: str) -> tuple[str, ...]:
    """Return all prediction meter URNs routed to a v84 artifact model URN."""

    if model_urn not in ANOMALY_DETECTION_ARTIFACT_MODEL_URNS:
        raise ValueError(f"unsupported anomaly model_urn: {model_urn}")
    return tuple(meter for meter, mapped_model in ANOMALY_DETECTION_METER_MODEL_URN_MAP.items() if mapped_model == model_urn)


def _step_value(row: AnomalyDetectionWideRow, prefix: str, step: int):
    return getattr(row, f"{prefix}_t_plus_{step}")


def _is_1h_aligned(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None and value.minute == 0 and value.second == 0 and value.microsecond == 0


def _is_finite(value: float | int) -> bool:
    numeric = float(value)
    return not math.isnan(numeric) and not math.isinf(numeric)


__all__ = [
    "ANOMALY_DETECTION_ALLOWED_EVIDENCE_TABLES",
    "ANOMALY_DETECTION_ALLOWED_FEATURE_SETS",
    "ANOMALY_DETECTION_ARTIFACT_ADAPTER_STUB",
    "ANOMALY_DETECTION_ARTIFACT_MODEL_URNS",
    "ANOMALY_DETECTION_DERIVED_FEATURE_COLUMNS",
    "ANOMALY_DETECTION_DIRECT_MODEL_METERS",
    "ANOMALY_DETECTION_ELECTRIC_FEATURE_COLUMNS",
    "ANOMALY_DETECTION_ELECTRIC_NO_PF_FEATURE_COLUMNS",
    "ANOMALY_DETECTION_EVALUATION_TABLE",
    "ANOMALY_DETECTION_FORECAST_TABLE",
    "ANOMALY_DETECTION_HISTORY_HOURS",
    "ANOMALY_DETECTION_FEATURE_TABLE",
    "ANOMALY_DETECTION_OBSERVED_SOURCE_TABLE",
    "ANOMALY_DETECTION_INFERENCE_LOG_TABLE",
    "ANOMALY_DETECTION_LEAD_STEPS",
    "ANOMALY_DETECTION_METER_MODEL_URN_MAP",
    "ANOMALY_DETECTION_MODEL_METERS",
    "ANOMALY_DETECTION_MODEL_VERSION",
    "ANOMALY_DETECTION_PREDICT_METERS",
    "ANOMALY_DETECTION_RELEASE",
    "ANOMALY_DETECTION_RELEASE_SHA256",
    "ANOMALY_DETECTION_TRANSFER_MEMBER_MODEL_URN_MAP",
    "AnomalyDetectionArtifactBoundary",
    "AnomalyDetectionLongRow",
    "AnomalyDetectionValidationIssue",
    "AnomalyDetectionWideRow",
    "anomaly_meters_for_model_urn",
    "anomaly_model_urn_for_meter",
    "anomaly_wide_to_long_rows",
    "validate_anomaly_detection_batch",
    "validate_anomaly_detection_wide_row",
]
