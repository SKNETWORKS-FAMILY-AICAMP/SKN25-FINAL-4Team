from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cms.contracts.model_input_1h import ELECTRIC_REQUIRED_FEATURES, HISTORY_HOURS, ModelInput1HRow, ValidationIssue
from cms.workflow.champion_tasks import (
    ChampionRunConfig,
    ChampionTaskResult,
    airflow_task_entrypoint,
    check_live_1h_readiness,
    evaluate_posthoc_anomaly_thresholds,
    evaluate_pre_warning_thresholds,
    gate_champion_model_artifact,
    gate_kafka_t3b_t4_evidence,
    gate_manual_nonprod_run,
    join_posthoc_actuals_and_errors,
    load_run_config,
    publish_evidence_packet,
    record_pipeline_metrics,
    run_champion_1h_inference_adapter,
    validate_model_input_contract,
)

BASE_TS = datetime(2026, 6, 7, 23, tzinfo=UTC)


class FakeDagRun:
    def __init__(self, conf: dict[str, object] | None = None) -> None:
        self.conf = conf


def _valid_rows(*, latest_p: float = 321.5, meter_urn: str = "meter:electric:001", base_ts: datetime = BASE_TS) -> tuple[ModelInput1HRow, ...]:
    start = base_ts - timedelta(hours=HISTORY_HOURS - 1)
    rows: list[ModelInput1HRow] = []
    for offset in range(HISTORY_HOURS):
        p = latest_p if offset == HISTORY_HOURS - 1 else float(offset)
        features = {feature: 1.0 for feature in ELECTRIC_REQUIRED_FEATURES}
        features["P"] = p
        rows.append(ModelInput1HRow(meter_urn=meter_urn, meter_kind="electric", ts=start + timedelta(hours=offset), features=features))
    return tuple(rows)


def test_load_run_config_requires_and_parses_explicit_base_ts() -> None:
    config = load_run_config(
        {
            "base_ts": "2026-06-07T23:00:00Z",
            "environment": "dev",
            "manual_run": True,
            "dry_run": True,
            "writes_enabled": False,
        }
    )

    assert config == ChampionRunConfig(base_ts=BASE_TS, environment="dev", manual_run=True, dry_run=True, writes_enabled=False)

    with pytest.raises(ValueError, match="base_ts is required"):
        load_run_config({"environment": "dev"})

    with pytest.raises(ValueError, match="timezone-aware"):
        load_run_config({"base_ts": "2026-06-07T23:00:00"})


def test_airflow_task_entrypoint_load_run_config_reads_dag_run_conf_base_ts() -> None:
    result = airflow_task_entrypoint(
        "load_run_config",
        dag_run=FakeDagRun(
            {
                "base_ts": "2026-06-07T23:00:00Z",
                "environment": "dev",
                "manual_run": True,
                "dry_run": True,
                "writes_enabled": False,
            }
        ),
    )

    assert result == ChampionTaskResult(
        task_id="load_run_config",
        ok=True,
        data={"config": ChampionRunConfig(base_ts=BASE_TS, environment="dev", manual_run=True, dry_run=True, writes_enabled=False)},
    )


@pytest.mark.parametrize("task_id", ["load_run_config", "check_live_1h_readiness"])
def test_airflow_task_entrypoint_missing_context_returns_blocked_result_without_crashing(task_id: str) -> None:
    result = airflow_task_entrypoint(task_id)

    assert result.task_id == task_id
    assert result.ok is False
    assert result.blocked is True
    assert result.errors


def test_gate_manual_nonprod_run_blocks_production_and_writes() -> None:
    production = ChampionRunConfig(base_ts=BASE_TS, environment="production", writes_enabled=False)
    prod_result = gate_manual_nonprod_run(production)
    assert prod_result.ok is False
    assert prod_result.blocked is True
    assert "production" in prod_result.errors[0]

    writes = ChampionRunConfig(base_ts=BASE_TS, environment="dev", writes_enabled=True)
    writes_result = gate_manual_nonprod_run(writes)
    assert writes_result.ok is False
    assert writes_result.blocked is True
    assert any("writes_enabled" in error for error in writes_result.errors)

    nondry_run = ChampionRunConfig(base_ts=BASE_TS, environment="dev", dry_run=False, writes_enabled=False)
    nondry_result = gate_manual_nonprod_run(nondry_run)
    assert nondry_result.ok is False
    assert nondry_result.blocked is True
    assert any("dry_run" in error for error in nondry_result.errors)

    allowed = gate_manual_nonprod_run(ChampionRunConfig(base_ts=BASE_TS, environment="dev", writes_enabled=False))
    assert allowed == ChampionTaskResult(task_id="gate_manual_nonprod_run", ok=True, data={"environment": "dev", "writes_enabled": False})


def test_gate_kafka_t3b_t4_evidence_requires_zero_lag_dlq_retry_and_consumer_invariant() -> None:
    good = {
        "t3b": {"lag_after": 0, "dlq": 0, "retry": 0, "processed": 10, "inserted": 9, "duplicate": 1, "committed": 10},
        "t4": {"lag_after": 0, "dlq": 0, "retry": 0, "processed": 5, "inserted": 5, "duplicate": 0, "committed": 5},
    }
    assert gate_kafka_t3b_t4_evidence(good).ok is True

    nonzero = dict(good)
    nonzero["t4"] = {**good["t4"], "lag_after": 1, "dlq": 1, "retry": 1}
    failed = gate_kafka_t3b_t4_evidence(nonzero)
    assert failed.ok is False
    assert set(failed.errors) >= {"t4 lag_after must be 0", "t4 dlq must be 0", "t4 retry must be 0"}

    bad_invariant = dict(good)
    bad_invariant["t3b"] = {**good["t3b"], "processed": 10, "inserted": 8, "duplicate": 1, "committed": 9}
    invariant_failed = gate_kafka_t3b_t4_evidence(bad_invariant)
    assert invariant_failed.ok is False
    assert "t3b processed must equal inserted + duplicate + dlq" in invariant_failed.errors
    assert "t3b committed must equal processed" in invariant_failed.errors


def test_gate_champion_model_artifact_requires_available_champion_artifact() -> None:
    ok = gate_champion_model_artifact({"model_version": "fake-v1", "adapter_name": "fake_champion_adapter", "available": True})
    assert ok.ok is True
    assert ok.data["model_version"] == "fake-v1"

    failed = gate_champion_model_artifact({"model_version": "", "available": False})
    assert failed.ok is False
    assert "model_version is required" in failed.errors
    assert "artifact must be available" in failed.errors


def test_readiness_and_contract_validation_use_explicit_base_ts() -> None:
    rows = _valid_rows(base_ts=BASE_TS - timedelta(hours=1))

    readiness = check_live_1h_readiness(rows=rows, config=ChampionRunConfig(base_ts=BASE_TS))
    contract = validate_model_input_contract(rows=rows, config=ChampionRunConfig(base_ts=BASE_TS))

    expected = ValidationIssue(
        meter_urn="meter:electric:001",
        ts=None,
        issue="insufficient_history_hours",
        expected=f">={HISTORY_HOURS}",
        observed=HISTORY_HOURS - 1,
    )
    assert readiness.ok is False
    assert contract.ok is False
    assert expected in readiness.data["validation_result"].issues
    assert expected in contract.data["validation_result"].issues


def test_run_champion_1h_inference_adapter_uses_fake_adapter_with_base_ts() -> None:
    result = run_champion_1h_inference_adapter(rows=_valid_rows(latest_p=42.0), config=ChampionRunConfig(base_ts=BASE_TS))

    assert result.ok is True
    predictions = result.data["predictions"]
    assert len(predictions) == 1
    assert predictions[0].pred_t_plus_1 == 42.0
    assert predictions[0].post_hoc["adapter_name"] == "fake_champion_adapter"
    assert predictions[0].post_hoc["base_ts"] == "2026-06-07T23:00:00+00:00"


def test_pre_warning_thresholds_are_separate_from_posthoc_anomaly_thresholds() -> None:
    inference = run_champion_1h_inference_adapter(rows=_valid_rows(latest_p=42.0), config=ChampionRunConfig(base_ts=BASE_TS))
    predictions = inference.data["predictions"]

    pre = evaluate_pre_warning_thresholds(predictions=predictions, thresholds={"max_prediction": 40.0})
    assert pre.ok is False
    assert pre.data["pre_warnings"] == (
        {"meter_urn": "meter:electric:001", "horizon": "pred_t_plus_1", "prediction": 42.0, "threshold": 40.0},
        {"meter_urn": "meter:electric:001", "horizon": "pred_t_plus_2", "prediction": 42.0, "threshold": 40.0},
        {"meter_urn": "meter:electric:001", "horizon": "pred_t_plus_3", "prediction": 42.0, "threshold": 40.0},
    )
    assert "posthoc_anomalies" not in pre.data

    joined = join_posthoc_actuals_and_errors(
        predictions=predictions,
        actuals={"meter:electric:001": {"pred_t_plus_1": 44.0, "pred_t_plus_2": 41.0, "pred_t_plus_3": 39.0}},
    )
    assert joined.ok is True
    assert joined.data["posthoc_errors"] == (
        {"meter_urn": "meter:electric:001", "horizon": "pred_t_plus_1", "prediction": 42.0, "actual": 44.0, "error": 2.0, "abs_error": 2.0},
        {"meter_urn": "meter:electric:001", "horizon": "pred_t_plus_2", "prediction": 42.0, "actual": 41.0, "error": -1.0, "abs_error": 1.0},
        {"meter_urn": "meter:electric:001", "horizon": "pred_t_plus_3", "prediction": 42.0, "actual": 39.0, "error": -3.0, "abs_error": 3.0},
    )

    posthoc = evaluate_posthoc_anomaly_thresholds(posthoc_errors=joined.data["posthoc_errors"], thresholds={"max_abs_error": 1.5})
    assert posthoc.ok is False
    assert posthoc.data["posthoc_anomalies"] == (
        {"meter_urn": "meter:electric:001", "horizon": "pred_t_plus_1", "abs_error": 2.0, "threshold": 1.5},
        {"meter_urn": "meter:electric:001", "horizon": "pred_t_plus_3", "abs_error": 3.0, "threshold": 1.5},
    )
    assert "pre_warnings" not in posthoc.data


def test_record_pipeline_metrics_and_publish_evidence_packet_are_in_memory_only() -> None:
    metrics = record_pipeline_metrics(
        predictions=(object(), object()),
        pre_warning_result=ChampionTaskResult(task_id="evaluate_pre_warning_thresholds", ok=False, data={"pre_warnings": ({"a": 1},)}),
        posthoc_result=ChampionTaskResult(task_id="evaluate_posthoc_anomaly_thresholds", ok=True, data={"posthoc_anomalies": ()}),
    )

    assert metrics.ok is True
    assert metrics.data == {"prediction_count": 2, "pre_warning_count": 1, "posthoc_anomaly_count": 0}

    packet = publish_evidence_packet(config=ChampionRunConfig(base_ts=BASE_TS), metrics_result=metrics, evidence={"kafka": "ok"})
    assert packet.ok is True
    assert packet.data["packet"]["base_ts"] == "2026-06-07T23:00:00+00:00"
    assert packet.data["packet"]["metrics"] == metrics.data
    assert packet.data["packet"]["evidence"] == {"kafka": "ok"}
