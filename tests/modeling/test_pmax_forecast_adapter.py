from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cms.contracts.pmax_forecast_15min import PMAX_FORECAST_FEATURE_COLUMNS, PMAX_FORECAST_HORIZON_MINUTES
from cms.modeling.pmax_feature_builder import PmaxFeatureVector
from cms.modeling.pmax_forecast_adapter import PmaxForecastAdapter, PmaxForecastPredictionError

BASE_TS = datetime(2026, 6, 8, 0, 0, tzinfo=UTC)


class FakePmaxModel:
    def __init__(self, predictions: object) -> None:
        self.predictions = predictions
        self.seen_rows: object | None = None

    def predict(self, rows: object) -> object:
        self.seen_rows = rows
        return self.predictions


class FakeLoader:
    def __init__(self, model: FakePmaxModel) -> None:
        self.model = model
        self.load_calls = 0

    def load(self) -> FakePmaxModel:
        self.load_calls += 1
        return self.model


def _feature(logical_meter: str = "V.Z81") -> PmaxFeatureVector:
    values = {column: float(index + 1) for index, column in enumerate(PMAX_FORECAST_FEATURE_COLUMNS)}
    step_values = tuple(
        {column: float((step * len(PMAX_FORECAST_FEATURE_COLUMNS)) + index + 1) for index, column in enumerate(PMAX_FORECAST_FEATURE_COLUMNS)}
        for step in range(96)
    )
    return PmaxFeatureVector(
        logical_meter=logical_meter,
        source_meter_urn=logical_meter,
        base_ts=BASE_TS,
        input_end_ts=BASE_TS - timedelta(minutes=15),
        values=values,
        history_window_count=288,
        step_values=step_values,
    )


def test_pmax_forecast_adapter_converts_fake_model_multioutput_to_valid_forecast_rows() -> None:
    model = FakePmaxModel([[10.0, 11.0, 12.0, 13.0]])
    adapter = PmaxForecastAdapter(model=model, created_at_factory=lambda base_ts: base_ts)

    result = adapter.predict((_feature(),))

    assert result.ok is True
    assert len(result.rows) == 4
    assert [row.horizon_minutes for row in result.rows] == list(PMAX_FORECAST_HORIZON_MINUTES)
    assert [row.predicted_p_max for row in result.rows] == [10.0, 11.0, 12.0, 13.0]
    assert result.rows[0].target_ts == BASE_TS + timedelta(minutes=15)
    assert result.rows[0].created_at == BASE_TS
    assert isinstance(model.seen_rows, list)
    assert len(model.seen_rows) == 1
    assert len(model.seen_rows[0]) == 96 * len(PMAX_FORECAST_FEATURE_COLUMNS)


def test_pmax_forecast_adapter_accepts_lazy_loader_and_dict_predictions() -> None:
    model = FakePmaxModel([{15: 1.0, 30: 2.0, 45: 3.0, 60: 4.0}])
    loader = FakeLoader(model)
    adapter = PmaxForecastAdapter(model=loader, input_format="dicts", created_at_factory=lambda base_ts: base_ts)

    result = adapter.predict((_feature(),))

    assert loader.load_calls == 1
    assert result.ok is True
    assert [row.predicted_p_max for row in result.rows] == [1.0, 2.0, 3.0, 4.0]
    assert isinstance(model.seen_rows, list)
    assert isinstance(model.seen_rows[0], dict)
    assert len(model.seen_rows[0]) == 96 * len(PMAX_FORECAST_FEATURE_COLUMNS)
    assert "t00_P_mean" in model.seen_rows[0]
    assert "t95_PF_mean" in model.seen_rows[0]


class FakeFeatureAwareModel:
    def __init__(self) -> None:
        self.seen_features: object | None = None
        self.seen_rows: object | None = None

    def predict_features(self, features: object, rows: object) -> list[list[float]]:
        self.seen_features = features
        self.seen_rows = rows
        return [[21.0, 22.0, 23.0, 24.0]]


def test_pmax_forecast_adapter_uses_release_style_predict_features_hook() -> None:
    model = FakeFeatureAwareModel()
    adapter = PmaxForecastAdapter(model=model, created_at_factory=lambda base_ts: base_ts)

    result = adapter.predict((_feature(),))

    assert result.ok is True
    assert [row.predicted_p_max for row in result.rows] == [21.0, 22.0, 23.0, 24.0]
    assert model.seen_features is not None
    assert model.seen_rows is not None


class FakeArray:
    def __init__(self, values: object) -> None:
        self.values = values

    def tolist(self) -> object:
        return self.values


class FakeArrayModel:
    def predict(self, rows: object) -> FakeArray:
        assert rows
        return FakeArray([[5.0, 6.0, 7.0, 8.0]])


def test_pmax_forecast_adapter_accepts_numpy_like_prediction_outputs() -> None:
    adapter = PmaxForecastAdapter(model=FakeArrayModel(), created_at_factory=lambda base_ts: base_ts)

    result = adapter.predict((_feature(),))

    assert result.ok is True
    assert [row.predicted_p_max for row in result.rows] == [5.0, 6.0, 7.0, 8.0]


def test_pmax_forecast_adapter_blocks_invalid_negative_prediction_in_strict_mode() -> None:
    adapter = PmaxForecastAdapter(model=FakePmaxModel([[-1.0, 2.0, 3.0, 4.0]]), created_at_factory=lambda base_ts: base_ts)

    with pytest.raises(PmaxForecastPredictionError, match="predicted_p_max_must_be_nonnegative"):
        adapter.predict((_feature(),))


def test_pmax_forecast_adapter_can_clip_negative_predictions_for_operational_serving() -> None:
    adapter = PmaxForecastAdapter(
        model=FakePmaxModel([[-1.0, 2.0, 3.0, 4.0]]),
        created_at_factory=lambda base_ts: base_ts,
        negative_prediction_policy="clip_zero",
    )

    result = adapter.predict((_feature(),))

    assert result.ok is True
    assert [row.predicted_p_max for row in result.rows] == [0.0, 2.0, 3.0, 4.0]
