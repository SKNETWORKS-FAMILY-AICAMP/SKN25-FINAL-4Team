from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cms.contracts.model_input_1h import ELECTRIC_REQUIRED_FEATURES, HISTORY_HOURS, ModelInput1HRow, ValidationIssue
from cms.modeling.fake_champion_adapter import FakeChampionAdapter, FakeChampionPrediction


def _valid_rows(*, latest_p: float = 321.5, meter_urn: str = "meter:electric:001") -> tuple[ModelInput1HRow, ...]:
    start = datetime(2026, 6, 1, tzinfo=UTC)
    rows: list[ModelInput1HRow] = []
    for offset in range(HISTORY_HOURS):
        p = latest_p if offset == HISTORY_HOURS - 1 else float(offset)
        features = {feature: 1.0 for feature in ELECTRIC_REQUIRED_FEATURES}
        features["P"] = p
        rows.append(ModelInput1HRow(meter_urn=meter_urn, meter_kind="electric", ts=start + timedelta(hours=offset), features=features))
    return tuple(rows)


def test_fake_champion_adapter_returns_three_horizon_predictions_from_latest_p_per_meter() -> None:
    rows = (*_valid_rows(latest_p=321.5, meter_urn="meter:electric:001"), *_valid_rows(latest_p=12.25, meter_urn="meter:electric:002"))
    base_ts = datetime(2026, 6, 7, 23, tzinfo=UTC)
    adapter = FakeChampionAdapter(model_version="test-v1")

    predictions = adapter.predict(rows, base_ts=base_ts)

    assert predictions == (
        FakeChampionPrediction(
            meter_urn="meter:electric:001",
            input_grain="1h",
            pred_t_plus_1=321.5,
            pred_t_plus_2=321.5,
            pred_t_plus_3=321.5,
            post_hoc={
                "adapter_name": "fake_champion_adapter",
                "model_version": "test-v1",
                "history_hours": 168,
                "base_ts": "2026-06-07T23:00:00+00:00",
                "pred_t_plus_1_ts": "2026-06-08T00:00:00+00:00",
                "pred_t_plus_2_ts": "2026-06-08T01:00:00+00:00",
                "pred_t_plus_3_ts": "2026-06-08T02:00:00+00:00",
            },
        ),
        FakeChampionPrediction(
            meter_urn="meter:electric:002",
            input_grain="1h",
            pred_t_plus_1=12.25,
            pred_t_plus_2=12.25,
            pred_t_plus_3=12.25,
            post_hoc={
                "adapter_name": "fake_champion_adapter",
                "model_version": "test-v1",
                "history_hours": 168,
                "base_ts": "2026-06-07T23:00:00+00:00",
                "pred_t_plus_1_ts": "2026-06-08T00:00:00+00:00",
                "pred_t_plus_2_ts": "2026-06-08T01:00:00+00:00",
                "pred_t_plus_3_ts": "2026-06-08T02:00:00+00:00",
            },
        ),
    )


def test_fake_champion_adapter_requires_explicit_base_ts_keyword() -> None:
    with pytest.raises(TypeError):
        FakeChampionAdapter().predict(_valid_rows())


def test_fake_champion_adapter_validates_model_input_before_predicting() -> None:
    rows = list(_valid_rows())
    rows[-1] = ModelInput1HRow(meter_urn="meter:electric:001", meter_kind="electric", ts=rows[-1].ts, features={"P": 1.0, "U1": 1.0})

    with pytest.raises(ValueError, match="model_input_1h validation failed") as exc_info:
        FakeChampionAdapter().predict(rows, base_ts=rows[-1].ts)

    assert exc_info.value.args[1].missing_features[0].feature == "PF"


def test_fake_champion_adapter_validates_against_explicit_base_ts() -> None:
    rows = _valid_rows()
    base_ts = rows[-1].ts + timedelta(hours=1)

    with pytest.raises(ValueError, match="model_input_1h validation failed") as exc_info:
        FakeChampionAdapter().predict(rows, base_ts=base_ts)

    assert ValidationIssue(
        meter_urn="meter:electric:001",
        ts=None,
        issue="insufficient_history_hours",
        expected=f">={HISTORY_HOURS}",
        observed=HISTORY_HOURS - 1,
    ) in exc_info.value.args[1].issues


def test_fake_champion_adapter_rejects_future_rows_after_base_ts() -> None:
    base_ts = datetime(2026, 6, 7, 23, tzinfo=UTC)
    future_ts = base_ts + timedelta(hours=1)
    rows = (
        *_valid_rows(),
        ModelInput1HRow(
            meter_urn="meter:electric:001",
            meter_kind="electric",
            ts=future_ts,
            features={"P": 1.0, "U1": 1.0, "PF": 1.0},
        ),
    )

    with pytest.raises(ValueError, match="model_input_1h validation failed") as exc_info:
        FakeChampionAdapter().predict(rows, base_ts=base_ts)

    assert ValidationIssue(
        meter_urn="meter:electric:001",
        ts=future_ts,
        issue="future_row_after_base_ts",
        expected=base_ts.isoformat(),
        observed=future_ts.isoformat(),
    ) in exc_info.value.args[1].issues
