from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cms.contracts.pmax_forecast_15min import PMAX_FORECAST_MODEL_VERSION, PmaxFeatureReadinessRow
from cms.workflow.pmax_forecast_tasks import (
    PmaxForecastRunConfig,
    PmaxForecastTaskResult,
    airflow_task_entrypoint,
    build_pmax_features,
    check_pmax_feature_readiness,
    gate_manual_nonprod_run,
    gate_pmax_model_artifact,
    load_run_config,
    publish_pmax_evidence_packet,
    record_pmax_pipeline_metrics,
    run_pmax_forecast_adapter,
    validate_pmax_forecast_output,
)

BASE_TS = datetime(2026, 6, 8, 0, 0, tzinfo=UTC)


class FakeDagRun:
    def __init__(self, conf: dict[str, object] | None = None) -> None:
        self.conf = conf


class FakePmaxModel:
    def predict(self, rows: object) -> list[list[float]]:
        assert rows
        return [[10.0, 11.0, 12.0, 13.0]]


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


def test_pmax_load_run_config_and_airflow_entrypoint_are_import_safe() -> None:
    config = load_run_config({"base_ts": "2026-06-08T00:00:00Z", "environment": "dev", "logical_meters": "V.Z81"})
    assert config == PmaxForecastRunConfig(base_ts=BASE_TS, environment="dev", logical_meters=("V.Z81",))

    result = airflow_task_entrypoint("load_run_config", dag_run=FakeDagRun({"base_ts": "2026-06-08T00:00:00Z", "logical_meters": ["V.Z81"]}))
    assert result.ok is True
    assert result.data["config"].base_ts == BASE_TS

    blocked = airflow_task_entrypoint("run_pmax_forecast_adapter")
    assert blocked.ok is False
    assert blocked.blocked is True

    with pytest.raises(ValueError, match="15min"):
        load_run_config({"base_ts": "2026-06-08T00:01:00Z"})


def test_pmax_gates_block_writes_production_and_missing_artifact() -> None:
    assert gate_manual_nonprod_run(PmaxForecastRunConfig(base_ts=BASE_TS, environment="dev", logical_meters=("V.Z81",))).ok is True
    blocked = gate_manual_nonprod_run(PmaxForecastRunConfig(base_ts=BASE_TS, environment="production", writes_enabled=True, logical_meters=("V.Z81",)))
    assert blocked.ok is False
    assert any("production" in error for error in blocked.errors)
    assert any("writes_enabled" in error for error in blocked.errors)

    ok_artifact = gate_pmax_model_artifact({"model_version": PMAX_FORECAST_MODEL_VERSION, "adapter_name": "fake", "available": True, "artifact_uri": "in-memory://pmax-artifact"})
    assert ok_artifact.ok is True
    bad_artifact = gate_pmax_model_artifact({"model_version": "v0", "available": False})
    assert bad_artifact.ok is False
    assert "artifact must be available" in bad_artifact.errors

    missing_uri = gate_pmax_model_artifact({"model_version": PMAX_FORECAST_MODEL_VERSION, "available": True})
    assert missing_uri.ok is False
    assert "artifact must be available" in missing_uri.errors


def test_pmax_tasks_build_features_run_adapter_validate_metrics_and_packet() -> None:
    config = PmaxForecastRunConfig(base_ts=BASE_TS, environment="dev", logical_meters=("V.Z81",))
    rows = _history_rows()

    readiness = check_pmax_feature_readiness(rows=rows, config=config)
    assert readiness.ok is True

    build = build_pmax_features(rows=rows, config=config)
    assert build.ok is True
    features = build.data["features"]
    assert len(features) == 1

    inference = run_pmax_forecast_adapter(features=features, config=config, model=FakePmaxModel())
    assert inference.ok is True
    forecast_rows = inference.data["forecast_rows"]
    assert len(forecast_rows) == 4

    validation = validate_pmax_forecast_output(rows=forecast_rows)
    assert validation.ok is True

    metrics = record_pmax_pipeline_metrics(rows=forecast_rows, readiness_result=readiness.data["readiness_result"])
    assert metrics == PmaxForecastTaskResult(
        task_id="record_pmax_pipeline_metrics",
        ok=True,
        data={"prediction_count": 4, "logical_meter_count": 1, "logical_meters": ("V.Z81",), "readiness_ok": True},
    )

    packet = publish_pmax_evidence_packet(config=config, metrics_result=metrics, evidence={"source": "unit-test"})
    assert packet.ok is True
    assert packet.data["packet"]["base_ts"] == "2026-06-08T00:00:00+00:00"
    assert packet.data["packet"]["metrics"] == metrics.data
