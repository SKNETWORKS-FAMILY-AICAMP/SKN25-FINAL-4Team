"""Import P-Max 15-minute forecast schema-boundary contract tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cms.contracts.import_pmax_forecast_15min import (
    IMPORT_PMAX_EVALUATION_TABLE,
    IMPORT_PMAX_FORECAST_TABLE,
    IMPORT_PMAX_INFERENCE_LOG_TABLE,
    IMPORT_PMAX_INPUT_TABLE,
    IMPORT_PMAX_LOGICAL_METER_SOURCES,
    IMPORT_PMAX_MODEL_GRAIN,
    IMPORT_PMAX_QUERY_HISTORY_DAYS,
    IMPORT_PMAX_TARGET_MEASUREMENT,
    IMPORT_PMAX_WINDOW_POINTS,
    ImportPmaxForecastRow,
    ImportPmaxRunLogContract,
    actual_window_ts_for_forecast,
    validate_import_pmax_forecast_row,
)

BASE_TS = datetime(2026, 6, 6, 23, 45, tzinfo=UTC)


def _forecast_row(**overrides: object) -> ImportPmaxForecastRow:
    values = {
        "logical_meter": "V.Z81",
        "source_meter_urn": "V.Z81",
        "base_ts": BASE_TS,
        "input_end_ts": BASE_TS - timedelta(minutes=15),
        "target_ts": BASE_TS + timedelta(minutes=15),
        "horizon_minutes": 15,
        "predicted_p_max": 12345.67,
        "created_at": BASE_TS + timedelta(seconds=5),
    }
    values.update(overrides)
    return ImportPmaxForecastRow(**values)  # type: ignore[arg-type]


def test_import_pmax_schema_boundary_uses_mart_for_predictions_ops_for_logs_and_qa_for_evaluation() -> None:
    assert IMPORT_PMAX_INPUT_TABLE == "mart.peak_feature_15min"
    assert IMPORT_PMAX_FORECAST_TABLE == "mart.import_pmax_forecast_15min"
    assert IMPORT_PMAX_INFERENCE_LOG_TABLE == "ops.import_pmax_inference_log"
    assert IMPORT_PMAX_EVALUATION_TABLE == "qa.import_pmax_forecast_evaluation"

    assert not IMPORT_PMAX_FORECAST_TABLE.startswith("analysis.")
    assert not IMPORT_PMAX_INFERENCE_LOG_TABLE.startswith("analysis.")


def test_import_pmax_contract_reflects_attached_model_spec() -> None:
    assert IMPORT_PMAX_MODEL_GRAIN == "15min"
    assert IMPORT_PMAX_WINDOW_POINTS == 96
    assert IMPORT_PMAX_QUERY_HISTORY_DAYS == 14
    assert IMPORT_PMAX_TARGET_MEASUREMENT == "P_max"
    assert IMPORT_PMAX_LOGICAL_METER_SOURCES == {
        "V.Z81": ("V.Z81",),
        "V.Z82": ("V.Z82",),
        "H2.Z35x": ("H2.Z35", "H2.Z351"),
        "H2.Z36x": ("H2.Z36", "H2.Z361"),
    }


def test_import_pmax_forecast_row_validates_target_end_timestamp_and_allowed_source_meter() -> None:
    row = _forecast_row()
    result = validate_import_pmax_forecast_row(row)

    assert result == ()
    assert actual_window_ts_for_forecast(row) == BASE_TS

    assert validate_import_pmax_forecast_row(_forecast_row(input_end_ts=BASE_TS))[0].issue == "input_end_ts_must_be_base_ts_minus_15min"
    assert validate_import_pmax_forecast_row(_forecast_row(target_ts=BASE_TS))[0].issue == "target_ts_must_equal_base_ts_plus_horizon"
    assert validate_import_pmax_forecast_row(_forecast_row(source_meter_urn="V.Z82"))[0].issue == "source_meter_not_allowed_for_logical_meter"


def test_import_pmax_forecast_row_blocks_negative_prediction_bad_horizon_and_unaligned_timestamps() -> None:
    assert validate_import_pmax_forecast_row(_forecast_row(predicted_p_max=-0.1))[0].issue == "predicted_p_max_must_be_nonnegative"
    assert validate_import_pmax_forecast_row(_forecast_row(horizon_minutes=75))[0].issue == "unsupported_horizon_minutes"
    assert validate_import_pmax_forecast_row(_forecast_row(base_ts=BASE_TS + timedelta(minutes=1)))[0].issue == "base_ts_not_15min_aligned"


def test_import_pmax_run_log_contract_keeps_quality_and_runtime_status_in_ops() -> None:
    log = ImportPmaxRunLogContract(
        table_name=IMPORT_PMAX_INFERENCE_LOG_TABLE,
        allowed_statuses=("success", "degraded", "failed"),
        max_replacement_rows=4,
        max_internal_missing_segments=1,
        max_internal_interpolation_minutes=60,
        latest_missing_single_bucket_policy="previous_observation_degraded",
        latest_missing_30min_policy="fail",
        external_alert_thresholds_in_model_scope=False,
    )

    assert log.table_name.startswith("ops.")
    assert "degraded" in log.allowed_statuses
    assert log.max_replacement_rows == 4
    assert log.external_alert_thresholds_in_model_scope is False


@pytest.mark.parametrize("logical_meter,source_meter", [("H2.Z35x", "H2.Z35"), ("H2.Z35x", "H2.Z351"), ("H2.Z36x", "H2.Z36"), ("H2.Z36x", "H2.Z361")])
def test_import_pmax_logical_replacement_meters_allow_single_source_but_not_mixed(logical_meter: str, source_meter: str) -> None:
    row = _forecast_row(logical_meter=logical_meter, source_meter_urn=source_meter)

    assert validate_import_pmax_forecast_row(row) == ()
