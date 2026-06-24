"""Import-safe anomaly warning adapter.

The production artifact can contain several model families. This adapter defines a
small boundary: callers pass an already-loaded predictor or lazy loader, and the
adapter normalizes artifact-style wide predictions into validated contract rows.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast

from cms.contracts.anomaly_detection_1h import (
    ANOMALY_DETECTION_FEATURE_TABLE,
    ANOMALY_DETECTION_HORIZON_HOURS,
    AnomalyDetectionLongRow,
    AnomalyDetectionValidationIssue,
    AnomalyDetectionWideRow,
    anomaly_model_urn_for_meter,
    anomaly_wide_to_long_rows,
    validate_anomaly_detection_batch,
    validate_anomaly_detection_wide_row,
)


class SupportsAnomalyPredict(Protocol):
    def predict(self, rows: Any) -> Any: ...


class SupportsLoad(Protocol):
    def load(self) -> SupportsAnomalyPredict: ...


class AnomalyWarningPredictionError(ValueError):
    """Raised when anomaly warning model output cannot be normalized."""


@dataclass(frozen=True)
class AnomalyWarningAdapterResult:
    """Structured anomaly adapter result."""

    wide_rows: tuple[AnomalyDetectionWideRow, ...]
    long_rows: tuple[AnomalyDetectionLongRow, ...]
    validation_issues: tuple[AnomalyDetectionValidationIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.validation_issues


@dataclass(frozen=True)
class AnomalyWarningAdapter:
    """Normalize anomaly model predictions into validated warning rows."""

    model: SupportsAnomalyPredict | SupportsLoad
    strict_validation: bool = True

    def predict(self, rows: Iterable[Mapping[str, Any]]) -> AnomalyWarningAdapterResult:
        materialized_rows = tuple(dict(row) for row in rows)
        if not materialized_rows:
            raise AnomalyWarningPredictionError("at least one anomaly feature row is required")
        raw_predictions = _ensure_model(self.model).predict(materialized_rows)
        refs_by_meter = _source_refs_by_meter(materialized_rows)
        wide_rows = tuple(
            _wide_row_from_mapping(item, default_source_input_refs=_default_source_refs(item, refs_by_meter))
            for item in _as_prediction_sequence(raw_predictions)
        )
        wide_issues = tuple(issue for row in wide_rows for issue in validate_anomaly_detection_wide_row(row))
        if self.strict_validation and wide_issues:
            issue_names = ",".join(issue.issue for issue in wide_issues)
            raise AnomalyWarningPredictionError(f"invalid anomaly warning rows: {issue_names}")
        try:
            long_rows = tuple(long for row in wide_rows for long in anomaly_wide_to_long_rows(row))
        except ValueError as exc:
            raise AnomalyWarningPredictionError(str(exc)) from exc
        issues = wide_issues + validate_anomaly_detection_batch(long_rows)
        if self.strict_validation and issues:
            issue_names = ",".join(issue.issue for issue in issues)
            raise AnomalyWarningPredictionError(f"invalid anomaly warning rows: {issue_names}")
        return AnomalyWarningAdapterResult(wide_rows=wide_rows, long_rows=long_rows, validation_issues=issues)


def _ensure_model(model_or_loader: SupportsAnomalyPredict | SupportsLoad) -> SupportsAnomalyPredict:
    predict = getattr(model_or_loader, "predict", None)
    if callable(predict):
        return cast(SupportsAnomalyPredict, model_or_loader)
    load = getattr(model_or_loader, "load", None)
    if callable(load):
        loaded = load()
        if callable(getattr(loaded, "predict", None)):
            return cast(SupportsAnomalyPredict, loaded)
    raise AnomalyWarningPredictionError("model must provide predict(), or loader must provide load() returning a predict() model")


def _as_prediction_sequence(raw_predictions: Any) -> tuple[Mapping[str, Any], ...]:
    normalized = raw_predictions.to_dict(orient="records") if hasattr(raw_predictions, "to_dict") else raw_predictions
    if isinstance(normalized, Mapping):
        return (normalized,)
    if isinstance(normalized, Sequence) and not isinstance(normalized, str | bytes | bytearray):
        if not all(isinstance(item, Mapping) for item in normalized):
            raise AnomalyWarningPredictionError("anomaly predictions must be mappings")
        return tuple(cast(Mapping[str, Any], item) for item in normalized)
    raise AnomalyWarningPredictionError("anomaly predictions must be a mapping or sequence of mappings")


def _wide_row_from_mapping(value: Mapping[str, Any], *, default_source_input_refs: tuple[str, ...] = ()) -> AnomalyDetectionWideRow:
    try:
        meter_urn = str(value["meter_urn"])
        return AnomalyDetectionWideRow(
            meter_urn=meter_urn,
            model_urn=str(value.get("model_urn", anomaly_model_urn_for_meter(meter_urn))),
            forecast_origin_ts=_datetime_any(value, "forecast_origin_ts", "timestamp", "input_end_ts"),
            horizon_hours=int(value.get("horizon", ANOMALY_DETECTION_HORIZON_HOURS)),
            status=cast(Any, value.get("status", "success")),
            pred_t_plus_1=_optional_float(value.get("pred_t_plus_1")),
            pred_t_plus_2=_optional_float(value.get("pred_t_plus_2")),
            pred_t_plus_3=_optional_float(value.get("pred_t_plus_3")),
            threshold_lower_t_plus_1=_optional_float(value.get("threshold_lower_t_plus_1")),
            threshold_lower_t_plus_2=_optional_float(value.get("threshold_lower_t_plus_2")),
            threshold_lower_t_plus_3=_optional_float(value.get("threshold_lower_t_plus_3")),
            threshold_upper_t_plus_1=_optional_float(value.get("threshold_upper_t_plus_1")),
            threshold_upper_t_plus_2=_optional_float(value.get("threshold_upper_t_plus_2")),
            threshold_upper_t_plus_3=_optional_float(value.get("threshold_upper_t_plus_3")),
            warning_t_plus_1=bool(value.get("warning_t_plus_1", False)),
            warning_t_plus_2=bool(value.get("warning_t_plus_2", False)),
            warning_t_plus_3=bool(value.get("warning_t_plus_3", False)),
            warning_type_t_plus_1=cast(Any, value.get("warning_type_t_plus_1", "none")),
            warning_type_t_plus_2=cast(Any, value.get("warning_type_t_plus_2", "none")),
            warning_type_t_plus_3=cast(Any, value.get("warning_type_t_plus_3", "none")),
            warning_flag=bool(value.get("warning_flag", False)),
            physical_flag=bool(value.get("physical_flag", False)),
            physical_issue_types=cast(str | None, value.get("physical_issue_types")),
            physical_issue_count=int(value.get("physical_issue_count", 0)),
            physical_issue_recent_count=int(value.get("physical_issue_recent_count", 0)),
            physical_issue_pattern=str(value.get("physical_issue_pattern", "none")),
            input_quality=cast(Any, value.get("input_quality", "good")),
            input_missing_count=int(value.get("input_missing_count", 0)),
            input_physical_count=int(value.get("input_physical_count", 0)),
            input_imputed_count=int(value.get("input_imputed_count", 0)),
            warning_reason_code=cast(Any, value.get("warning_reason_code", "NONE")),
            warning_reason_detail=cast(str | None, value.get("warning_reason_detail")),
            created_at=_datetime_value(value, "created_at", fallback_key="forecast_origin_ts"),
            physical_issue_detail=cast(str | None, value.get("physical_issue_detail")),
            meter_issue_types=cast(str | None, value.get("meter_issue_types")),
            meter_issue_detail=cast(str | None, value.get("meter_issue_detail")),
            meter_issue_severity=cast(str | None, value.get("meter_issue_severity")),
            low_sample_t_plus_1=bool(value.get("low_sample_t_plus_1", False)),
            low_sample_t_plus_2=bool(value.get("low_sample_t_plus_2", False)),
            low_sample_t_plus_3=bool(value.get("low_sample_t_plus_3", False)),
            source_input_refs=_source_refs_from_mapping(value, default_source_input_refs),
        )
    except AnomalyWarningPredictionError:
        raise
    except KeyError as exc:
        raise AnomalyWarningPredictionError(f"anomaly prediction missing required field: {exc}") from exc
    except (TypeError, ValueError) as exc:
        raise AnomalyWarningPredictionError(f"invalid anomaly prediction field: {exc}") from exc


def _source_refs_by_meter(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, tuple[str, ...]]:
    refs_by_meter: dict[str, list[str]] = {}
    for row in rows:
        meter_urn = row.get("meter_urn")
        if meter_urn is None:
            continue
        refs = _normalize_source_refs(row.get("source_input_refs", row.get("source_refs")))
        if not refs:
            refs = _feature_row_source_ref(row)
        if refs:
            refs_by_meter.setdefault(str(meter_urn), []).extend(refs)
    return {meter_urn: tuple(dict.fromkeys(refs)) for meter_urn, refs in refs_by_meter.items()}


def _feature_row_source_ref(row: Mapping[str, Any]) -> tuple[str, ...]:
    meter_urn = row.get("meter_urn")
    bucket_ts = row.get("bucket_ts", row.get("ts"))
    if meter_urn is None or bucket_ts is None:
        return ()
    if isinstance(bucket_ts, datetime):
        ts_value = bucket_ts.isoformat()
    else:
        ts_value = str(bucket_ts)
    return (f"{ANOMALY_DETECTION_FEATURE_TABLE}:{meter_urn}:{ts_value}",)


def _default_source_refs(prediction: Mapping[str, Any], refs_by_meter: Mapping[str, tuple[str, ...]]) -> tuple[str, ...]:
    meter_urn = prediction.get("meter_urn")
    if meter_urn is None:
        return ()
    return refs_by_meter.get(str(meter_urn), ())


def _source_refs_from_mapping(value: Mapping[str, Any], default_source_input_refs: tuple[str, ...]) -> tuple[str, ...]:
    refs = _normalize_source_refs(value.get("source_input_refs", value.get("source_refs")))
    return refs or default_source_input_refs


def _normalize_source_refs(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return ()
        if stripped.startswith("["):
            try:
                return _normalize_source_refs(json.loads(stripped))
            except json.JSONDecodeError:
                pass
        if "," in stripped:
            return tuple(dict.fromkeys(part.strip() for part in stripped.split(",") if part.strip()))
        return (stripped,)
    if isinstance(value, Mapping):
        return (json.dumps(dict(value), sort_keys=True, default=str),)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        refs: list[str] = []
        for item in value:
            refs.extend(_normalize_source_refs(item))
        return tuple(dict.fromkeys(refs))
    return (str(value),)


def _datetime_any(value: Mapping[str, Any], *keys: str) -> datetime:
    for key in keys:
        if value.get(key) is not None:
            return _datetime_value(value, key)
    joined = "|".join(keys)
    raise AnomalyWarningPredictionError(f"one of {joined} is required")


def _datetime_value(value: Mapping[str, Any], key: str, *, fallback_key: str | None = None) -> datetime:
    observed = value.get(key)
    if observed is None and fallback_key is not None:
        try:
            observed = value[fallback_key]
        except KeyError as exc:
            raise AnomalyWarningPredictionError(f"{key} is required") from exc
    if isinstance(observed, datetime):
        return observed
    if isinstance(observed, str):
        raw = observed.replace("Z", "+00:00") if observed.endswith("Z") else observed
        try:
            return datetime.fromisoformat(raw)
        except ValueError as exc:
            raise AnomalyWarningPredictionError(f"{key} must be ISO-8601 parseable") from exc
    raise AnomalyWarningPredictionError(f"{key} must be datetime or ISO-8601 string")


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise AnomalyWarningPredictionError("numeric anomaly prediction fields must be parseable floats") from exc


__all__ = [
    "AnomalyWarningAdapter",
    "AnomalyWarningAdapterResult",
    "AnomalyWarningPredictionError",
]
