"""Feature builder for the P-Max 15-minute forecast adapter.

The builder consumes already-fetched ``mart.peak_feature_15min`` contract rows
and performs all transformations in memory. It does not read from or write to a
DB, filesystem, Airflow, Kafka, Drive, or model artifact.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from cms.contracts.pmax_forecast_15min import (
    PMAX_FORECAST_FEATURE_COLUMNS,
    PMAX_FORECAST_LOGICAL_METER_SOURCES,
    PMAX_FORECAST_REQUIRED_MEASUREMENTS,
    PMAX_FORECAST_WINDOW_POINTS,
    PmaxFeatureReadinessResult,
    PmaxFeatureReadinessRow,
    select_latest_pmax_feature_rows,
    validate_pmax_feature_readiness,
)

_INTERVAL = timedelta(minutes=15)
_REQUIRED_PMAX_LAGS = (1, 4, 96, 192)
_DEFAULT_HISTORY_WINDOWS = PMAX_FORECAST_WINDOW_POINTS + max(_REQUIRED_PMAX_LAGS)


class PmaxFeatureBuildError(ValueError):
    """Raised when model features cannot be built from the provided rows."""


@dataclass(frozen=True)
class PmaxFeatureVector:
    """One ordered feature vector for one logical meter and base timestamp."""

    logical_meter: str
    source_meter_urn: str
    base_ts: datetime
    input_end_ts: datetime
    values: Mapping[str, float]
    history_window_count: int
    step_values: tuple[Mapping[str, float], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def ordered_values(self, columns: Sequence[str] = PMAX_FORECAST_FEATURE_COLUMNS) -> tuple[float, ...]:
        rows = self.step_values or (self.values,)
        return tuple(float(row[column]) for row in rows for column in columns)


@dataclass(frozen=True)
class PmaxFeatureBuildResult:
    """Structured in-memory feature build result."""

    base_ts: datetime
    input_end_ts: datetime
    features: tuple[PmaxFeatureVector, ...]
    readiness_result: PmaxFeatureReadinessResult
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors and self.readiness_result.ok


@dataclass(frozen=True)
class _WindowAggregates:
    window_ts: datetime
    source_meter_urn: str
    p_mean: float
    p_max: float
    p_std: float
    u1_mean: float
    pf_mean: float


def build_pmax_feature_vectors(
    rows: Iterable[PmaxFeatureReadinessRow],
    *,
    base_ts: datetime,
    logical_meters: Sequence[str] = tuple(PMAX_FORECAST_LOGICAL_METER_SOURCES),
    history_windows: int = _DEFAULT_HISTORY_WINDOWS,
    strict_readiness: bool = True,
) -> PmaxFeatureBuildResult:
    """Build P-Max adapter feature vectors from in-memory feature rows.

    ``history_windows`` defaults to 288 so every row in the 96-step model input
    can populate lag-192 features. The contract readiness gate is still
    evaluated on the latest 96 windows.
    """

    if history_windows < _DEFAULT_HISTORY_WINDOWS:
        raise PmaxFeatureBuildError(f"history_windows must be >= {_DEFAULT_HISTORY_WINDOWS} to populate P-Max lag features")

    materialized_rows = tuple(rows)
    readiness_result = validate_pmax_feature_readiness(materialized_rows, base_ts=base_ts, logical_meters=logical_meters)
    errors: list[str] = []
    if strict_readiness and not readiness_result.ok:
        errors.extend(issue.issue for issue in readiness_result.issues)

    input_end_ts = base_ts - _INTERVAL
    expected_windows = tuple(input_end_ts - _INTERVAL * offset for offset in reversed(range(history_windows)))
    selected_rows = select_latest_pmax_feature_rows(materialized_rows)
    features: list[PmaxFeatureVector] = []

    for logical_meter in logical_meters:
        try:
            history = _build_logical_history(selected_rows, logical_meter=logical_meter, expected_windows=expected_windows)
            features.append(_feature_vector_from_history(logical_meter=logical_meter, base_ts=base_ts, input_end_ts=input_end_ts, history=history))
        except PmaxFeatureBuildError as exc:
            errors.append(str(exc))

    return PmaxFeatureBuildResult(
        base_ts=base_ts,
        input_end_ts=input_end_ts,
        features=tuple(features) if not errors else tuple(features),
        readiness_result=readiness_result,
        errors=tuple(errors),
    )


def build_model_matrix(features: Iterable[PmaxFeatureVector], *, columns: Sequence[str] = PMAX_FORECAST_FEATURE_COLUMNS) -> tuple[tuple[str, ...], tuple[tuple[float, ...], ...]]:
    """Return an ordered numeric matrix suitable for most sklearn-like models."""

    materialized_features = tuple(features)
    return tuple(columns), tuple(feature.ordered_values(columns) for feature in materialized_features)


def _build_logical_history(
    rows: Sequence[PmaxFeatureReadinessRow], *, logical_meter: str, expected_windows: Sequence[datetime]
) -> tuple[_WindowAggregates, ...]:
    allowed_sources = PMAX_FORECAST_LOGICAL_METER_SOURCES.get(logical_meter)
    if allowed_sources is None:
        raise PmaxFeatureBuildError(f"unsupported logical meter: {logical_meter}")

    source_meter = _choose_single_source(rows, allowed_sources=allowed_sources, expected_windows=expected_windows)
    by_key = {(row.window_ts, row.measurement): row for row in rows if row.meter_urn == source_meter and row.measurement in PMAX_FORECAST_REQUIRED_MEASUREMENTS}

    history: list[_WindowAggregates] = []
    for window_ts in expected_windows:
        missing = [measurement for measurement in PMAX_FORECAST_REQUIRED_MEASUREMENTS if (window_ts, measurement) not in by_key]
        if missing:
            raise PmaxFeatureBuildError(f"{logical_meter} missing {','.join(missing)} at {window_ts.isoformat()} for {source_meter}")
        p_row = by_key[(window_ts, "P")]
        u1_row = by_key[(window_ts, "U1")]
        pf_row = by_key[(window_ts, "PF")]
        history.append(
            _WindowAggregates(
                window_ts=window_ts,
                source_meter_urn=source_meter,
                p_mean=_finite_float(p_row.mean_value, field="P.mean_value", logical_meter=logical_meter, window_ts=window_ts),
                p_max=_finite_float(p_row.max_value, field="P.max_value", logical_meter=logical_meter, window_ts=window_ts),
                p_std=_finite_float(p_row.std_value, field="P.std_value", logical_meter=logical_meter, window_ts=window_ts),
                u1_mean=_finite_float(u1_row.mean_value, field="U1.mean_value", logical_meter=logical_meter, window_ts=window_ts),
                pf_mean=_finite_float(pf_row.mean_value, field="PF.mean_value", logical_meter=logical_meter, window_ts=window_ts),
            )
        )
    return tuple(history)


def _choose_single_source(rows: Sequence[PmaxFeatureReadinessRow], *, allowed_sources: Sequence[str], expected_windows: Sequence[datetime]) -> str:
    expected_set = set(expected_windows)
    coverage: dict[str, int] = {source: 0 for source in allowed_sources}
    for row in rows:
        if row.meter_urn in coverage and row.window_ts in expected_set and row.measurement in PMAX_FORECAST_REQUIRED_MEASUREMENTS:
            coverage[row.meter_urn] += 1
    best_source, best_count = max(coverage.items(), key=lambda item: (item[1], item[0]))
    if best_count == 0:
        raise PmaxFeatureBuildError(f"no rows for allowed sources: {'|'.join(allowed_sources)}")
    return best_source


def _feature_vector_from_history(
    *, logical_meter: str, base_ts: datetime, input_end_ts: datetime, history: Sequence[_WindowAggregates]
) -> PmaxFeatureVector:
    input_history = tuple(history[-PMAX_FORECAST_WINDOW_POINTS:])
    step_values = tuple(_feature_values_at(history, index) for index in range(len(history) - PMAX_FORECAST_WINDOW_POINTS, len(history)))
    if len(step_values) != PMAX_FORECAST_WINDOW_POINTS:
        raise PmaxFeatureBuildError(f"expected {PMAX_FORECAST_WINDOW_POINTS} model input steps, got {len(step_values)}")
    latest = input_history[-1]
    return PmaxFeatureVector(
        logical_meter=logical_meter,
        source_meter_urn=latest.source_meter_urn,
        base_ts=base_ts,
        input_end_ts=input_end_ts,
        values=step_values[-1],
        history_window_count=len(history),
        step_values=step_values,
        metadata={
            "latest_window_ts": latest.window_ts.isoformat(),
            "input_start_ts": input_history[0].window_ts.isoformat(),
            "flattened_feature_count": len(step_values) * len(PMAX_FORECAST_FEATURE_COLUMNS),
        },
    )


def _feature_values_at(history: Sequence[_WindowAggregates], index: int) -> Mapping[str, float]:
    row = history[index]
    prefix = history[: index + 1]
    p_max_values = tuple(item.p_max for item in prefix)
    p_mean_values = tuple(item.p_mean for item in prefix)
    u1_mean_values = tuple(item.u1_mean for item in prefix)
    pf_mean_values = tuple(item.pf_mean for item in prefix)
    values = {
        "P_mean": row.p_mean,
        "P_max": row.p_max,
        "P_std": row.p_std,
        "U1_mean": row.u1_mean,
        "PF_mean": row.pf_mean,
        "hour_sin": _hour_sin(row.window_ts),
        "hour_cos": _hour_cos(row.window_ts),
        "dayofweek_sin": _dayofweek_sin(row.window_ts),
        "P_max_lag_1": _lag(p_max_values, 1),
        "P_max_lag_96": _lag(p_max_values, 96),
        "P_max_lag_192": _lag(p_max_values, 192),
        "P_max_roll_1h_mean": _roll_mean(p_max_values, 4),
        "P_max_roll_1h_max": _roll_max(p_max_values, 4),
        "P_max_roll_1h_std": _roll_std(p_max_values, 4),
        "P_max_roll_3h_mean": _roll_mean(p_max_values, 12),
        "P_max_roll_3h_max": _roll_max(p_max_values, 12),
        "P_max_roll_6h_mean": _roll_mean(p_max_values, 24),
        "P_max_diff_1": _diff(p_max_values, 1),
        "P_max_diff_4": _diff(p_max_values, 4),
        "P_mean_diff_1": _diff(p_mean_values, 1),
        "U1_mean_diff_1": _diff(u1_mean_values, 1),
        "PF_mean_diff_1": _diff(pf_mean_values, 1),
    }
    missing_columns = tuple(column for column in PMAX_FORECAST_FEATURE_COLUMNS if column not in values)
    if missing_columns:
        raise PmaxFeatureBuildError(f"feature builder did not populate columns: {missing_columns}")
    return {column: values[column] for column in PMAX_FORECAST_FEATURE_COLUMNS}


def _finite_float(value: float | int | None, *, field: str, logical_meter: str, window_ts: datetime) -> float:
    if value is None:
        raise PmaxFeatureBuildError(f"{logical_meter} {field} is missing at {window_ts.isoformat()}")
    float_value = float(value)
    if math.isnan(float_value) or math.isinf(float_value):
        raise PmaxFeatureBuildError(f"{logical_meter} {field} must be finite at {window_ts.isoformat()}")
    return float_value


def _lag(values: Sequence[float], periods: int) -> float:
    return values[-1 - periods]


def _diff(values: Sequence[float], periods: int) -> float:
    return values[-1] - values[-1 - periods]


def _roll_mean(values: Sequence[float], periods: int) -> float:
    return statistics.fmean(values[-periods:])


def _roll_max(values: Sequence[float], periods: int) -> float:
    return max(values[-periods:])


def _roll_std(values: Sequence[float], periods: int) -> float:
    window = values[-periods:]
    if len(window) <= 1:
        return 0.0
    return statistics.stdev(window)


def _hour_angle(ts: datetime) -> float:
    return ((ts.hour * 60 + ts.minute) / (24 * 60)) * 2 * math.pi


def _hour_sin(ts: datetime) -> float:
    return math.sin(_hour_angle(ts))


def _hour_cos(ts: datetime) -> float:
    return math.cos(_hour_angle(ts))


def _dayofweek_sin(ts: datetime) -> float:
    return math.sin((ts.weekday() / 7) * 2 * math.pi)


__all__ = [
    "PmaxFeatureBuildError",
    "PmaxFeatureBuildResult",
    "PmaxFeatureVector",
    "build_model_matrix",
    "build_pmax_feature_vectors",
]
