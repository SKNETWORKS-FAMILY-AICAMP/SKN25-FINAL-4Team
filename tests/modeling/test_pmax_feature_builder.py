from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cms.contracts.pmax_forecast_15min import PMAX_FORECAST_FEATURE_COLUMNS, PmaxFeatureReadinessRow
from cms.modeling.pmax_feature_builder import PmaxFeatureBuildError, build_model_matrix, build_pmax_feature_vectors

BASE_TS = datetime(2026, 6, 8, 0, 0, tzinfo=UTC)


def _feature_row(**overrides: object) -> PmaxFeatureReadinessRow:
    window_ts = overrides.get("window_ts", BASE_TS - timedelta(minutes=15))
    assert isinstance(window_ts, datetime)
    measurement = str(overrides.get("measurement", "P"))
    values = {
        "window_ts": window_ts,
        "meter_urn": "V.Z81",
        "measurement": measurement,
        "mean_value": 1.0,
        "max_value": 2.0 if measurement == "P" else 1.0,
        "min_value": 0.0,
        "p95_value": 2.0,
        "p99_value": 2.0,
        "std_value": 0.1 if measurement == "P" else 0.0,
        "last_value": 1.0,
        "peak_ts": window_ts + timedelta(minutes=5),
        "peak_value": 2.0,
        "observed_points": 15,
        "expected_points": 15,
        "coverage_ratio": 1.0,
        "source_file": f"V.Z81/V.Z81.{measurement}_harmonized.csv.gz",
        "source_layer": "mart.peak_feature_15min",
        "source_mode": "live_observed",
        "provenance": {"source": "unit-test-live-observed"},
        "run_id": "run_a",
        "created_at": BASE_TS,
    }
    values.update(overrides)
    return PmaxFeatureReadinessRow(**values)  # type: ignore[arg-type]


def _history_rows(history_windows: int = 288) -> tuple[PmaxFeatureReadinessRow, ...]:
    input_end_ts = BASE_TS - timedelta(minutes=15)
    start = input_end_ts - timedelta(minutes=15 * (history_windows - 1))
    rows: list[PmaxFeatureReadinessRow] = []
    for offset in range(history_windows):
        window_ts = start + timedelta(minutes=15 * offset)
        rows.append(_feature_row(window_ts=window_ts, measurement="P", mean_value=100.0 + offset, max_value=200.0 + offset, std_value=0.5))
        rows.append(_feature_row(window_ts=window_ts, measurement="U1", mean_value=10.0 + offset, max_value=10.0, std_value=0.0))
        rows.append(_feature_row(window_ts=window_ts, measurement="PF", mean_value=0.9 + offset / 1000.0, max_value=1.0, std_value=0.0))
    return tuple(rows)


def test_build_pmax_feature_vectors_populates_declared_metadata_columns_in_order() -> None:
    result = build_pmax_feature_vectors(_history_rows(), base_ts=BASE_TS, logical_meters=("V.Z81",))

    assert result.ok is True
    assert len(result.features) == 1
    feature = result.features[0]
    assert tuple(feature.values) == PMAX_FORECAST_FEATURE_COLUMNS
    assert feature.logical_meter == "V.Z81"
    assert feature.source_meter_urn == "V.Z81"
    assert feature.history_window_count == 288
    assert len(feature.step_values) == 96
    assert feature.metadata["flattened_feature_count"] == 96 * len(PMAX_FORECAST_FEATURE_COLUMNS)
    assert feature.values["P_max"] == 487.0
    assert feature.values["P_max_lag_1"] == 486.0
    assert feature.values["P_max_lag_96"] == 391.0
    assert feature.values["P_max_lag_192"] == 295.0
    assert feature.step_values[0]["P_max"] == 392.0
    assert feature.step_values[0]["P_max_lag_192"] == 200.0
    assert feature.values["P_max_roll_1h_mean"] == pytest.approx(485.5)
    assert feature.values["P_max_roll_1h_max"] == 487.0
    assert feature.values["P_max_diff_4"] == 4.0
    assert feature.values["P_mean_diff_1"] == 1.0

    columns, matrix = build_model_matrix(result.features)
    assert columns == PMAX_FORECAST_FEATURE_COLUMNS
    assert len(matrix) == 1
    assert len(matrix[0]) == 96 * len(PMAX_FORECAST_FEATURE_COLUMNS)
    assert matrix[0][columns.index("P_max")] == 392.0
    latest_offset = 95 * len(columns)
    assert matrix[0][latest_offset + columns.index("P_max")] == 487.0


def test_build_pmax_feature_vectors_blocks_short_history_and_missing_rows() -> None:
    with pytest.raises(PmaxFeatureBuildError, match="history_windows"):
        build_pmax_feature_vectors(_history_rows(96), base_ts=BASE_TS, logical_meters=("V.Z81",), history_windows=96)

    rows = list(_history_rows())
    rows = [row for row in rows if not (row.window_ts == BASE_TS - timedelta(minutes=15) and row.measurement == "PF")]
    result = build_pmax_feature_vectors(rows, base_ts=BASE_TS, logical_meters=("V.Z81",))
    assert result.ok is False
    assert any("missing" in error for error in result.errors)
