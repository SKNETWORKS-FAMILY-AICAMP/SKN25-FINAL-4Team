"""P-Max forecast adapter around model artifacts.

The adapter is import-safe and can be exercised with fake model objects in tests.
It performs no artifact lookup; callers pass an already-loaded model or a lazy
loader object whose ``load()`` method is called only when inference runs.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol, cast

from cms.contracts.pmax_forecast_15min import (
    PMAX_FORECAST_FEATURE_COLUMNS,
    PMAX_FORECAST_HORIZON_MINUTES,
    PMAX_FORECAST_MODEL_VERSION,
    PMAX_FORECAST_WINDOW_POINTS,
    PmaxForecastRow,
    PmaxForecastValidationIssue,
    validate_pmax_forecast_row,
)
from cms.modeling.pmax_feature_builder import PmaxFeatureVector, build_model_matrix

ModelInputFormat = Literal["matrix", "dicts", "pandas"]


class SupportsPredict(Protocol):
    def predict(self, rows: Any) -> Any: ...


class SupportsPredictFeatures(Protocol):
    def predict_features(self, features: Any, rows: Any) -> Any: ...


class SupportsLoad(Protocol):
    def load(self) -> SupportsPredict | SupportsPredictFeatures: ...


class PmaxForecastPredictionError(ValueError):
    """Raised when model output cannot be converted to forecast rows."""


@dataclass(frozen=True)
class PmaxForecastAdapterResult:
    """Structured result for a P-Max inference adapter run."""

    rows: tuple[PmaxForecastRow, ...]
    validation_issues: tuple[PmaxForecastValidationIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.validation_issues


@dataclass(frozen=True)
class PmaxForecastAdapter:
    """Convert feature vectors into validated P-Max forecast rows."""

    model: SupportsPredict | SupportsPredictFeatures | SupportsLoad
    adapter_name: str = "pmax_forecast_adapter"
    model_version: str = PMAX_FORECAST_MODEL_VERSION
    input_format: ModelInputFormat = "matrix"
    created_at_factory: Callable[[datetime], datetime] | None = None
    strict_validation: bool = True

    def predict(self, features: Iterable[PmaxFeatureVector]) -> PmaxForecastAdapterResult:
        materialized_features = tuple(features)
        if not materialized_features:
            raise PmaxForecastPredictionError("at least one P-Max feature vector is required")

        model_input = self._model_input(materialized_features)
        model = _ensure_model(self.model)
        predict_features = getattr(model, "predict_features", None)
        if callable(predict_features):
            raw_predictions = predict_features(materialized_features, model_input)
        else:
            raw_predictions = cast(SupportsPredict, model).predict(model_input)
        prediction_rows = _normalize_batch_predictions(raw_predictions, expected_rows=len(materialized_features))

        rows: list[PmaxForecastRow] = []
        issues: list[PmaxForecastValidationIssue] = []
        for feature, horizon_values in zip(materialized_features, prediction_rows, strict=True):
            created_at = self._created_at(feature.base_ts)
            for horizon_minutes, predicted_value in zip(PMAX_FORECAST_HORIZON_MINUTES, horizon_values, strict=True):
                row = PmaxForecastRow(
                    logical_meter=feature.logical_meter,
                    source_meter_urn=feature.source_meter_urn,
                    base_ts=feature.base_ts,
                    input_end_ts=feature.input_end_ts,
                    target_ts=feature.base_ts + timedelta(minutes=horizon_minutes),
                    horizon_minutes=horizon_minutes,
                    predicted_p_max=float(predicted_value),
                    created_at=created_at,
                )
                row_issues = validate_pmax_forecast_row(row)
                rows.append(row)
                issues.extend(row_issues)

        if self.strict_validation and issues:
            issue_names = ",".join(issue.issue for issue in issues)
            raise PmaxForecastPredictionError(f"invalid P-Max forecast rows: {issue_names}")
        return PmaxForecastAdapterResult(rows=tuple(rows), validation_issues=tuple(issues))

    def _created_at(self, base_ts: datetime) -> datetime:
        if self.created_at_factory is not None:
            return self.created_at_factory(base_ts)
        return datetime.now(tz=base_ts.tzinfo)

    def _model_input(self, features: Sequence[PmaxFeatureVector]) -> Any:
        columns, matrix = build_model_matrix(features, columns=PMAX_FORECAST_FEATURE_COLUMNS)
        expected_width = PMAX_FORECAST_WINDOW_POINTS * len(PMAX_FORECAST_FEATURE_COLUMNS)
        if any(len(row) != expected_width for row in matrix):
            raise PmaxForecastPredictionError(f"P-Max model input must be flattened 96x22 features ({expected_width} values per row)")
        if self.input_format == "matrix":
            return [list(row) for row in matrix]
        if self.input_format == "dicts":
            flattened_columns = _flattened_feature_columns(columns)
            return [dict(zip(flattened_columns, row, strict=True)) for row in matrix]
        if self.input_format == "pandas":
            flattened_columns = _flattened_feature_columns(columns)
            try:
                import pandas as pd  # type: ignore[import-not-found]
            except ModuleNotFoundError as exc:
                raise PmaxForecastPredictionError("pandas is required for input_format='pandas'") from exc
            return pd.DataFrame([dict(zip(flattened_columns, row, strict=True)) for row in matrix], columns=list(flattened_columns))
        raise PmaxForecastPredictionError(f"unsupported input_format: {self.input_format}")


def _flattened_feature_columns(columns: Sequence[str]) -> tuple[str, ...]:
    return tuple(f"t{step:02d}_{column}" for step in range(PMAX_FORECAST_WINDOW_POINTS) for column in columns)


def _ensure_model(model_or_loader: SupportsPredict | SupportsPredictFeatures | SupportsLoad) -> SupportsPredict | SupportsPredictFeatures:
    predict = getattr(model_or_loader, "predict", None)
    predict_features = getattr(model_or_loader, "predict_features", None)
    if callable(predict) or callable(predict_features):
        return cast(SupportsPredict, model_or_loader)
    load = getattr(model_or_loader, "load", None)
    if callable(load):
        loaded = load()
        loaded_predict = getattr(loaded, "predict", None)
        loaded_predict_features = getattr(loaded, "predict_features", None)
        if callable(loaded_predict) or callable(loaded_predict_features):
            return cast(SupportsPredict, loaded)
    raise PmaxForecastPredictionError("model must provide predict(), or loader must provide load() returning a predict() model")


def _normalize_batch_predictions(raw_predictions: Any, *, expected_rows: int) -> tuple[tuple[float, ...], ...]:
    if expected_rows == 1:
        try:
            return (_normalize_one_prediction(raw_predictions),)
        except PmaxForecastPredictionError:
            pass

    normalized = raw_predictions.tolist() if hasattr(raw_predictions, "tolist") else raw_predictions
    if not _is_sequence(normalized):
        raise PmaxForecastPredictionError("model predictions must be a sequence")
    predictions = tuple(normalized)
    if len(predictions) != expected_rows:
        raise PmaxForecastPredictionError(f"model returned {len(predictions)} prediction rows for {expected_rows} feature rows")
    return tuple(_normalize_one_prediction(prediction) for prediction in predictions)


def _normalize_one_prediction(prediction: Any) -> tuple[float, ...]:
    normalized = prediction.tolist() if hasattr(prediction, "tolist") else prediction
    if isinstance(normalized, Mapping):
        values = [_mapping_horizon_value(normalized, horizon) for horizon in PMAX_FORECAST_HORIZON_MINUTES]
        return tuple(_finite_prediction(value) for value in values)

    if _is_sequence(normalized):
        values = tuple(normalized)
        if len(values) != len(PMAX_FORECAST_HORIZON_MINUTES):
            raise PmaxForecastPredictionError(f"one prediction row must contain {len(PMAX_FORECAST_HORIZON_MINUTES)} horizons")
        return tuple(_finite_prediction(value) for value in values)

    attr_values: list[Any] = []
    for horizon in PMAX_FORECAST_HORIZON_MINUTES:
        attr_name = f"pred_t_plus_{horizon // 15}"
        if not hasattr(prediction, attr_name):
            raise PmaxForecastPredictionError("prediction object must expose horizon attributes or be a mapping/sequence")
        attr_values.append(getattr(prediction, attr_name))
    return tuple(_finite_prediction(value) for value in attr_values)


def _mapping_horizon_value(prediction: Mapping[Any, Any], horizon: int) -> Any:
    candidates = (horizon, str(horizon), f"horizon_{horizon}", f"t+{horizon}", f"pred_t_plus_{horizon // 15}")
    for candidate in candidates:
        if candidate in prediction:
            return prediction[candidate]
    raise PmaxForecastPredictionError(f"prediction mapping missing horizon {horizon}")


def _finite_prediction(value: Any) -> float:
    numeric = float(value)
    if math.isnan(numeric) or math.isinf(numeric):
        raise PmaxForecastPredictionError("prediction values must be finite")
    return numeric


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray)


__all__ = [
    "ModelInputFormat",
    "PmaxForecastAdapter",
    "PmaxForecastAdapterResult",
    "PmaxForecastPredictionError",
]
