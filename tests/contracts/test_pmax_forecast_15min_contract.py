"""P-Max 15-minute forecast schema-boundary contract tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cms.contracts.pmax_forecast_15min import (
    PMAX_FEATURE_LATEST_SELECTION_SQL,
    PMAX_FORECAST_ARTIFACT_ADAPTER_STUB,
    PMAX_FORECAST_CANDIDATE_VERSIONS,
    PMAX_FORECAST_EVALUATION_TABLE,
    PMAX_FORECAST_FEATURE_COLUMNS,
    PMAX_FORECAST_INFERENCE_LOG_TABLE,
    PMAX_FORECAST_INPUT_TABLE,
    PMAX_FORECAST_LOGICAL_METER_SOURCES,
    PMAX_FORECAST_MODEL_GRAIN,
    PMAX_FORECAST_MODEL_VERSION,
    PMAX_FORECAST_PRODUCTION_RELEASE,
    PMAX_FORECAST_PRODUCTION_RELEASE_SHA256,
    PMAX_FORECAST_QUERY_HISTORY_DAYS,
    PMAX_FORECAST_TABLE,
    PMAX_FORECAST_TARGET_MEASUREMENT,
    PMAX_FORECAST_WINDOW_POINTS,
    PmaxFeatureReadinessRow,
    PmaxForecastArtifactBoundary,
    PmaxForecastRow,
    PmaxForecastRunLogContract,
    actual_window_ts_for_forecast,
    select_latest_pmax_feature_rows,
    validate_pmax_feature_readiness,
    validate_pmax_forecast_row,
)

BASE_TS = datetime(2026, 6, 6, 23, 45, tzinfo=UTC)


def _feature_row(**overrides: object) -> PmaxFeatureReadinessRow:
    values = {
        "window_ts": BASE_TS - timedelta(minutes=15),
        "meter_urn": "V.Z81",
        "measurement": "P",
        "mean_value": 10.0,
        "max_value": 12.0,
        "min_value": 8.0,
        "p95_value": 12.0,
        "p99_value": 12.0,
        "std_value": 0.5,
        "last_value": 11.0,
        "peak_ts": BASE_TS - timedelta(minutes=10),
        "peak_value": 12.0,
        "observed_points": 15,
        "expected_points": 15,
        "coverage_ratio": 1.0,
        "source_file": "V.Z81/V.Z81.P_corrected_resampled_1min.csv.gz",
        "run_id": "run_a",
        "created_at": BASE_TS,
    }
    values.update(overrides)
    return PmaxFeatureReadinessRow(**values)  # type: ignore[arg-type]


def _ready_feature_rows(*, logical_meter: str = "V.Z81", source_meter: str = "V.Z81", base_ts: datetime = BASE_TS) -> tuple[PmaxFeatureReadinessRow, ...]:
    input_end_ts = base_ts - timedelta(minutes=15)
    start = input_end_ts - timedelta(minutes=15 * (PMAX_FORECAST_WINDOW_POINTS - 1))
    rows: list[PmaxFeatureReadinessRow] = []
    for offset in range(PMAX_FORECAST_WINDOW_POINTS):
        window_ts = start + timedelta(minutes=15 * offset)
        for measurement in ("P", "U1", "PF"):
            rows.append(
                _feature_row(
                    window_ts=window_ts,
                    meter_urn=source_meter,
                    measurement=measurement,
                    source_file=f"{source_meter}/{source_meter}.{measurement}_corrected_resampled_1min.csv.gz",
                    created_at=base_ts + timedelta(seconds=offset),
                    mean_value=10.0 if measurement != "PF" else 0.95,
                    max_value=12.0 if measurement == "P" else 10.0,
                    std_value=0.5 if measurement == "P" else 0.0,
                    peak_ts=window_ts + timedelta(minutes=5),
                )
            )
    assert logical_meter in PMAX_FORECAST_LOGICAL_METER_SOURCES
    assert source_meter in PMAX_FORECAST_LOGICAL_METER_SOURCES[logical_meter]
    return tuple(rows)


def _forecast_row(**overrides: object) -> PmaxForecastRow:
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
    return PmaxForecastRow(**values)  # type: ignore[arg-type]


def test_pmax_forecast_schema_boundary_uses_mart_for_predictions_ops_for_logs_and_qa_for_evaluation() -> None:
    assert PMAX_FORECAST_INPUT_TABLE == "mart.peak_feature_15min"
    assert PMAX_FORECAST_TABLE == "mart.pmax_forecast_15min"
    assert PMAX_FORECAST_INFERENCE_LOG_TABLE == "ops.pmax_forecast_inference_log"
    assert PMAX_FORECAST_EVALUATION_TABLE == "qa.pmax_forecast_evaluation"

    assert not PMAX_FORECAST_TABLE.startswith("analysis.")
    assert not PMAX_FORECAST_INFERENCE_LOG_TABLE.startswith("analysis.")


def test_pmax_forecast_contract_reflects_attached_model_spec() -> None:
    assert PMAX_FORECAST_MODEL_GRAIN == "15min"
    assert PMAX_FORECAST_WINDOW_POINTS == 96
    assert PMAX_FORECAST_QUERY_HISTORY_DAYS == 14
    assert PMAX_FORECAST_TARGET_MEASUREMENT == "P_max"
    assert PMAX_FORECAST_LOGICAL_METER_SOURCES == {
        "V.Z81": ("V.Z81",),
        "V.Z82": ("V.Z82",),
        "H2.Z35x": ("H2.Z35", "H2.Z351"),
        "H2.Z36x": ("H2.Z36", "H2.Z361"),
    }


def test_pmax_artifact_boundary_matches_verified_v29_drive_release_metadata() -> None:
    boundary = PmaxForecastArtifactBoundary()
    verified = PmaxForecastArtifactBoundary(
        drive_artifact_verified=True,
        external_io_enabled=False,
        artifact_uri="drive://1X_ZScb17QV1pPp8KqwEQvqlMn5WmfG8Q",
        model_version="v29",
    )
    blocked_io = PmaxForecastArtifactBoundary(
        drive_artifact_verified=True,
        external_io_enabled=True,
        artifact_uri="drive://1X_ZScb17QV1pPp8KqwEQvqlMn5WmfG8Q",
        model_version="v29",
    )

    assert boundary.adapter_name == PMAX_FORECAST_ARTIFACT_ADAPTER_STUB
    assert boundary.available is False
    assert verified.available is True
    assert blocked_io.available is False
    assert PMAX_FORECAST_PRODUCTION_RELEASE == "import_pmax_production_release_20260608"
    assert PMAX_FORECAST_PRODUCTION_RELEASE_SHA256 == "fc3848ea0bb76afd75252d8fc32f189709b5f323629bfd069efaf86ddc58bd80"
    assert PMAX_FORECAST_MODEL_VERSION == "v29"
    assert PMAX_FORECAST_CANDIDATE_VERSIONS == ("v20", "v23", "v25", "v27")
    assert len(PMAX_FORECAST_FEATURE_COLUMNS) == 22
    assert PMAX_FORECAST_FEATURE_COLUMNS[:5] == ("P_mean", "P_max", "P_std", "U1_mean", "PF_mean")


def test_pmax_forecast_row_validates_target_end_timestamp_and_allowed_source_meter() -> None:
    row = _forecast_row()
    result = validate_pmax_forecast_row(row)

    assert result == ()
    assert actual_window_ts_for_forecast(row) == BASE_TS

    assert validate_pmax_forecast_row(_forecast_row(input_end_ts=BASE_TS))[0].issue == "input_end_ts_must_be_base_ts_minus_15min"
    assert validate_pmax_forecast_row(_forecast_row(target_ts=BASE_TS))[0].issue == "target_ts_must_equal_base_ts_plus_horizon"
    assert validate_pmax_forecast_row(_forecast_row(source_meter_urn="V.Z82"))[0].issue == "source_meter_not_allowed_for_logical_meter"


def test_pmax_forecast_row_blocks_negative_prediction_bad_horizon_and_unaligned_timestamps() -> None:
    assert validate_pmax_forecast_row(_forecast_row(predicted_p_max=-0.1))[0].issue == "predicted_p_max_must_be_nonnegative"
    assert validate_pmax_forecast_row(_forecast_row(horizon_minutes=75))[0].issue == "unsupported_horizon_minutes"
    assert validate_pmax_forecast_row(_forecast_row(base_ts=BASE_TS + timedelta(minutes=1)))[0].issue == "base_ts_not_15min_aligned"


def test_pmax_forecast_run_log_contract_keeps_quality_and_runtime_status_in_ops() -> None:
    log = PmaxForecastRunLogContract(
        table_name=PMAX_FORECAST_INFERENCE_LOG_TABLE,
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
def test_pmax_forecast_logical_replacement_meters_allow_single_source_but_not_mixed(logical_meter: str, source_meter: str) -> None:
    row = _forecast_row(logical_meter=logical_meter, source_meter_urn=source_meter)

    assert validate_pmax_forecast_row(row) == ()


def test_pmax_feature_latest_selection_contract_uses_created_at_then_run_id() -> None:
    stale = _feature_row(mean_value=1.0, run_id="run_z", created_at=BASE_TS - timedelta(seconds=1))
    newer = _feature_row(mean_value=2.0, run_id="run_a", created_at=BASE_TS)
    tie_break = _feature_row(mean_value=3.0, run_id="run_b", created_at=BASE_TS)

    selected = select_latest_pmax_feature_rows((stale, newer, tie_break))

    assert selected == (tie_break,)
    assert "row_number() OVER" in PMAX_FEATURE_LATEST_SELECTION_SQL
    assert "PARTITION BY window_ts, meter_urn, measurement" in PMAX_FEATURE_LATEST_SELECTION_SQL
    assert "ORDER BY created_at DESC NULLS LAST, run_id DESC NULLS LAST" in PMAX_FEATURE_LATEST_SELECTION_SQL


def test_pmax_feature_readiness_blocks_ambiguous_duplicate_latest_rows() -> None:
    rows = list(_ready_feature_rows())
    tied_duplicate = _feature_row(
        window_ts=rows[0].window_ts,
        meter_urn=rows[0].meter_urn,
        measurement=rows[0].measurement,
        created_at=rows[0].created_at,
        run_id=rows[0].run_id,
        peak_ts=rows[0].peak_ts,
        mean_value=99.0,
    )
    rows.append(tied_duplicate)

    result = validate_pmax_feature_readiness(rows, base_ts=BASE_TS, logical_meters=("V.Z81",))

    assert "duplicate_latest_ambiguous" in {issue.issue for issue in result.issues}


def test_pmax_feature_readiness_accepts_complete_latest_96_point_feature_set() -> None:
    rows = _ready_feature_rows()

    result = validate_pmax_feature_readiness(rows, base_ts=BASE_TS, logical_meters=("V.Z81",))

    assert result.ok is True
    assert result.input_end_ts == BASE_TS - timedelta(minutes=15)
    assert result.selected_row_count == PMAX_FORECAST_WINDOW_POINTS * 3


def test_pmax_feature_readiness_blocks_missing_windows_low_coverage_and_missing_aggregates() -> None:
    rows = list(_ready_feature_rows())
    removed = rows.pop(0)
    rows[0] = _feature_row(
        window_ts=rows[0].window_ts,
        meter_urn=rows[0].meter_urn,
        measurement=rows[0].measurement,
        peak_ts=rows[0].peak_ts,
        coverage_ratio=14 / 15,
        observed_points=14,
        mean_value=None,
        created_at=rows[0].created_at,
    )

    result = validate_pmax_feature_readiness(rows, base_ts=BASE_TS, logical_meters=("V.Z81",))

    issue_names = {issue.issue for issue in result.issues}
    assert result.ok is False
    assert "missing_feature_window" in issue_names
    assert "coverage_ratio_below_threshold" in issue_names
    assert "missing_required_aggregate" in issue_names
    assert any(issue.window_ts == removed.window_ts and issue.measurement == removed.measurement for issue in result.issues)


def test_pmax_feature_readiness_blocks_mixed_replacement_sources() -> None:
    rows = list(_ready_feature_rows(logical_meter="H2.Z35x", source_meter="H2.Z35"))
    rows.extend(_ready_feature_rows(logical_meter="H2.Z35x", source_meter="H2.Z351")[:3])

    result = validate_pmax_feature_readiness(rows, base_ts=BASE_TS, logical_meters=("H2.Z35x",))

    assert "mixed_source_meters_for_logical_meter" in {issue.issue for issue in result.issues}
