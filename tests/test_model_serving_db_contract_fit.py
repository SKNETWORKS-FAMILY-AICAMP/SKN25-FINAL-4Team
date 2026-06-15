from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cms.contracts.anomaly_detection_1h import (
    ANOMALY_DETECTION_FEATURE_TABLE,
    AnomalyDetectionLongRow,
    validate_anomaly_detection_batch,
)
from cms.contracts.live_pipeline import (
    SOURCE_MODE_HYBRID_WARM_START,
    SOURCE_MODE_LIVE_OBSERVED,
    SOURCE_MODE_REFERENCE_BACKFILL,
)
from cms.contracts.pmax_forecast_15min import (
    PMAX_FORECAST_INPUT_TABLE,
    PmaxFeatureReadinessRow,
    select_latest_pmax_feature_rows,
    validate_pmax_feature_readiness,
)
from cms.data.model_serving_queries import (
    ANOMALY_REFERENCE_FEATURE_TABLE,
    build_anomaly_feature_query,
    build_anomaly_reference_feature_query,
    build_pmax_feature_query,
)
from cms.modeling.anomaly_warning_adapter import AnomalyWarningAdapter
from cms.workflow.model_serving_airflow_skeleton import task_contracts
from cms.workflow.model_serving_pipeline import ModelServingArtifactMount

BASE_TS = datetime(2023, 6, 1, 0, 0, tzinfo=UTC)


def _pmax_feature_row(**overrides: object) -> PmaxFeatureReadinessRow:
    window_ts = overrides.get("window_ts", BASE_TS - timedelta(minutes=15))
    assert isinstance(window_ts, datetime)
    values = {
        "window_ts": window_ts,
        "meter_urn": "V.Z81",
        "measurement": "P",
        "mean_value": 1.0,
        "max_value": 2.0,
        "min_value": 0.0,
        "p95_value": 2.0,
        "p99_value": 2.0,
        "std_value": 0.1,
        "last_value": 1.0,
        "peak_ts": window_ts + timedelta(minutes=5),
        "peak_value": 2.0,
        "observed_points": 15,
        "expected_points": 15,
        "coverage_ratio": 1.0,
        "source_file": "V.Z81/V.Z81.P_harmonized.csv.gz",
        "source_layer": "mart.peak_feature_15min",
        "source_mode": SOURCE_MODE_LIVE_OBSERVED,
        "provenance": {"source": "unit-test"},
        "run_id": "run_live",
        "created_at": BASE_TS,
    }
    values.update(overrides)
    return PmaxFeatureReadinessRow(**values)  # type: ignore[arg-type]


class FakeAnomalyModel:
    def predict(self, rows: object) -> list[dict[str, object]]:
        assert rows
        return [
            {
                "meter_urn": "H1.K11",
                "forecast_origin_ts": BASE_TS,
                "pred_t_plus_1": 10.0,
                "pred_t_plus_2": 11.0,
                "pred_t_plus_3": 12.0,
                "threshold_lower_t_plus_1": 0.0,
                "threshold_lower_t_plus_2": 0.0,
                "threshold_lower_t_plus_3": 0.0,
                "threshold_upper_t_plus_1": 20.0,
                "threshold_upper_t_plus_2": 20.0,
                "threshold_upper_t_plus_3": 20.0,
            }
        ]


def test_pmax_query_uses_peak_feature_live_observed_and_current_h2_db_basis() -> None:
    spec = build_pmax_feature_query(base_ts=BASE_TS, logical_meters=("H2.Z35x", "H2.Z36x"))

    assert spec.source_tables == (PMAX_FORECAST_INPUT_TABLE,)
    assert f"FROM {PMAX_FORECAST_INPUT_TABLE}" in spec.sql
    assert "source_mode = %(source_mode)s" in spec.sql
    assert "source_mode IS NULL" not in spec.sql
    assert spec.params["source_mode"] == SOURCE_MODE_LIVE_OBSERVED
    assert spec.source_contract is not None
    assert spec.source_contract["allow_null_source_mode"] is False
    assert spec.source_contract["production_label_allowed"] is True
    assert spec.params["source_meter_0"] == "H2.Z351"
    assert spec.params["source_meter_1"] == "H2.Z361"
    assert "H2.Z35" not in spec.params.values()
    assert "H2.Z36" not in spec.params.values()

    with pytest.raises(ValueError, match="hybrid_warm_start"):
        build_pmax_feature_query(base_ts=BASE_TS, logical_meters=("H2.Z35x",), allow_null_source_mode=True)

    hybrid_spec = build_pmax_feature_query(base_ts=BASE_TS, logical_meters=("H2.Z35x",), source_mode=SOURCE_MODE_HYBRID_WARM_START, allow_null_source_mode=True)
    assert "source_mode = %(live_source_mode)s" in hybrid_spec.sql
    assert "source_mode = %(reference_source_mode)s" in hybrid_spec.sql
    assert "source_mode IS NULL" in hybrid_spec.sql
    assert hybrid_spec.params["source_mode"] == SOURCE_MODE_HYBRID_WARM_START
    assert hybrid_spec.source_contract is not None
    assert hybrid_spec.source_contract["live_rows_preferred"] is True
    assert hybrid_spec.source_contract["production_label_allowed"] is False
    assert "H2.Z35" in hybrid_spec.params.values()

    reference_spec = build_pmax_feature_query(base_ts=BASE_TS, logical_meters=("H2.Z35x",), source_mode=SOURCE_MODE_REFERENCE_BACKFILL)
    assert "(source_mode = %(reference_source_mode)s OR source_mode IS NULL)" in reference_spec.sql
    assert reference_spec.params["source_mode"] == SOURCE_MODE_REFERENCE_BACKFILL
    assert reference_spec.source_contract is not None
    assert reference_spec.source_contract["allow_null_source_mode"] is True

    with pytest.raises(ValueError, match="mart.peak_feature_15min"):
        build_pmax_feature_query(base_ts=BASE_TS, input_table="mart.active_peak_feature_15min")
    with pytest.raises(ValueError, match="source_mode"):
        build_pmax_feature_query(base_ts=BASE_TS, source_mode="unknown_mode")


def test_pmax_reference_rows_are_explicit_and_hybrid_selection_prefers_live_rows() -> None:
    live = _pmax_feature_row(created_at=BASE_TS)
    reference_newer = _pmax_feature_row(
        source_file="reference/V.Z81.P_corrected_resampled_1min.csv.gz",
        source_layer="reference.corrected_resampled_15min",
        source_mode=SOURCE_MODE_REFERENCE_BACKFILL,
        provenance={"source": "reference-backfill"},
        run_id="run_reference_newer",
        created_at=BASE_TS + timedelta(minutes=1),
    )

    strict_result = validate_pmax_feature_readiness((reference_newer,), base_ts=BASE_TS, logical_meters=("V.Z81",), window_points=1)
    assert "reference_source_for_live_serving" in {issue.issue for issue in strict_result.issues}
    assert "reference_source_file_for_live_serving" in {issue.issue for issue in strict_result.issues}

    hybrid_selected = select_latest_pmax_feature_rows((reference_newer, live), source_mode=SOURCE_MODE_HYBRID_WARM_START)
    assert hybrid_selected == (live,)
    hybrid_result = validate_pmax_feature_readiness((reference_newer, live), base_ts=BASE_TS, logical_meters=("V.Z81",), window_points=1, source_mode=SOURCE_MODE_HYBRID_WARM_START)
    assert hybrid_result.source_mode == SOURCE_MODE_HYBRID_WARM_START
    assert hybrid_result.source_composition == {SOURCE_MODE_LIVE_OBSERVED: 1}
    assert "reference_source_for_live_serving" not in {issue.issue for issue in hybrid_result.issues}

    reference_result = validate_pmax_feature_readiness((reference_newer,), base_ts=BASE_TS, logical_meters=("V.Z81",), window_points=1, source_mode=SOURCE_MODE_REFERENCE_BACKFILL)
    assert reference_result.source_composition == {SOURCE_MODE_REFERENCE_BACKFILL: 1}
    assert "reference_source_for_live_serving" not in {issue.issue for issue in reference_result.issues}


def test_anomaly_query_uses_bucket_ts_feature_table_and_rejects_direct_canonical_or_alias_sources() -> None:
    spec = build_anomaly_feature_query(forecast_origin_ts=BASE_TS, meter_urns=("H1.K11",))

    assert spec.source_tables == (ANOMALY_DETECTION_FEATURE_TABLE,)
    assert f"FROM {ANOMALY_DETECTION_FEATURE_TABLE}" in spec.sql
    assert "bucket_ts BETWEEN" in spec.sql
    assert spec.expected_columns[0] == "bucket_ts"

    with pytest.raises(ValueError, match="canonical"):
        build_anomaly_feature_query(forecast_origin_ts=BASE_TS, feature_table="canonical.measurement_1h")
    with pytest.raises(ValueError, match="mart.anomaly_feature_1h"):
        build_anomaly_feature_query(forecast_origin_ts=BASE_TS, feature_table="mart.anomaly_input_1h")


def test_anomaly_reference_query_is_nonprod_backfill_and_kept_separate_from_live_feature_table() -> None:
    spec = build_anomaly_reference_feature_query(forecast_origin_ts=BASE_TS, meter_urns=("H1.K11",))

    assert spec.name == "anomaly_reference_1h_feature"
    assert spec.source_tables == (ANOMALY_REFERENCE_FEATURE_TABLE,)
    assert f"FROM {ANOMALY_REFERENCE_FEATURE_TABLE}" in spec.sql
    assert "ts >= %(input_start_ts)s" in spec.sql
    assert "bucket_ts" not in spec.sql
    assert spec.params["source_mode"] == SOURCE_MODE_REFERENCE_BACKFILL
    assert spec.expected_columns == ("ts", "meter_urn", "measurement", "value", "source_file", "run_id", "created_at")

    with pytest.raises(ValueError, match="canonical"):
        build_anomaly_reference_feature_query(forecast_origin_ts=BASE_TS, reference_table="canonical.measurement_1h")
    with pytest.raises(ValueError, match="reference.corrected_resampled_1h"):
        build_anomaly_reference_feature_query(forecast_origin_ts=BASE_TS, reference_table="live.measurement_1h")


def test_anomaly_adapter_derives_feature_table_source_refs_from_bucket_ts_and_blocks_canonical_refs() -> None:
    adapter = AnomalyWarningAdapter(model=FakeAnomalyModel())
    result = adapter.predict(
        (
            {
                "bucket_ts": BASE_TS - timedelta(hours=1),
                "meter_urn": "H1.K11",
                "p_value": 1.0,
                "u1_value": 2.0,
                "pf_value": 0.9,
            },
        )
    )

    assert result.ok is True
    assert result.long_rows[0].source_input_refs == (f"{ANOMALY_DETECTION_FEATURE_TABLE}:H1.K11:2023-05-31T23:00:00+00:00",)

    bad_row = AnomalyDetectionLongRow(
        meter_urn="H1.K11",
        model_urn="H1.K11",
        forecast_origin_ts=BASE_TS,
        target_ts=BASE_TS + timedelta(hours=1),
        lead_step=1,
        horizon_hours=3,
        predicted_p=1.0,
        threshold_lower=0.0,
        threshold_upper=2.0,
        warning_flag=False,
        warning_type="none",
        status="success",
        physical_flag=False,
        input_quality="good",
        warning_reason_code="NONE",
        created_at=BASE_TS,
        source_input_refs=("canonical.measurement_1h:H1.K11:2023-05-31T23:00:00+00:00",),
    )
    assert "canonical_source_ref_forbidden" in {issue.issue for issue in validate_anomaly_detection_batch((bad_row,))}


def test_model_serving_artifact_mount_and_airflow_contracts_include_local_pmax_anomaly_paths_and_no_canonical() -> None:
    mount = ModelServingArtifactMount()
    assert mount.pmax_uri == "artifacts/pmax/import_pmax_v29_60min"
    assert mount.anomaly_uri == "artifacts/anomaly/test6_residual_v84_3h_share_20260609"

    contracts = task_contracts()
    query_reads = contracts["build_model_serving_input_queries"]["reads"]
    dry_run_writes = contracts["run_model_serving_dry_run"]["writes"]
    assert isinstance(query_reads, list)
    assert isinstance(dry_run_writes, list)
    assert PMAX_FORECAST_INPUT_TABLE in query_reads
    assert ANOMALY_DETECTION_FEATURE_TABLE in query_reads
    assert "mart.anomaly_warning_1h" in dry_run_writes
    for contract in contracts.values():
        reads = contract["reads"]
        writes = contract["writes"]
        assert isinstance(reads, list)
        assert isinstance(writes, list)
        assert all(not table.startswith("canonical.") for table in reads + writes)
