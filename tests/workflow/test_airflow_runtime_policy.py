"""Tests for service-grade CMS Airflow runtime contracts."""

from __future__ import annotations

import sys

from cms.contracts.anomaly_detection_1h import ANOMALY_DETECTION_FEATURE_TABLE, ANOMALY_DETECTION_FORECAST_TABLE
from cms.contracts.pmax_forecast_15min import PMAX_FORECAST_INPUT_TABLE, PMAX_FORECAST_TABLE
from cms.workflow import airflow_skeleton, champion_airflow_skeleton, daily_report_airflow, model_serving_airflow_skeleton, monthly_report_airflow, weekly_report_airflow
from cms.workflow.airflow_runtime_policy import (
    AIRFLOW_CONNECTION_IDS,
    AIRFLOW_DAILY_REPORT_SCHEDULE,
    AIRFLOW_DEFAULT_RETRIES,
    AIRFLOW_ENV_VARS,
    AIRFLOW_MAX_ACTIVE_RUNS,
    AIRFLOW_MONTHLY_REPORT_SCHEDULE,
    AIRFLOW_OBSERVABILITY_SIGNALS,
    AIRFLOW_REGISTRATION_MODE,
    AIRFLOW_REPORT_READINESS_REGISTRATION_MODE,
    AIRFLOW_REPORT_READINESS_TRIGGER_POLICY,
    AIRFLOW_SCHEDULED_REPORT_DAG_IDS,
    AIRFLOW_SCHEDULED_REPORT_SCHEDULES,
    AIRFLOW_TASK_TRIGGER_RULE,
    AIRFLOW_TRIGGER_POLICY,
    AIRFLOW_WEEKLY_REPORT_SCHEDULE,
    CANONICAL_APPROVAL_BOUNDARY,
    WRITE_GATE_POLICY,
    describe_all_runtime_decisions,
    describe_service_runtime_contract,
    validate_runtime_decision,
)


def test_runtime_decisions_register_daily_weekly_monthly_reports_as_scheduled() -> None:
    decisions = describe_all_runtime_decisions()

    assert {decision.dag_id for decision in decisions} == {
        "daily_report",
        "weekly_report",
        "monthly_report",
        "cms_live_replay",
        "cms_champion_1h_model_pipeline",
        "model_serving_pipeline",
    }
    scheduled = [decision for decision in decisions if decision.schedule is not None]
    assert [decision.dag_id for decision in scheduled] == list(AIRFLOW_SCHEDULED_REPORT_DAG_IDS)
    assert {decision.dag_id: decision.schedule for decision in scheduled} == dict(AIRFLOW_SCHEDULED_REPORT_SCHEDULES)
    assert AIRFLOW_DAILY_REPORT_SCHEDULE == "0 9 * * *"
    assert AIRFLOW_WEEKLY_REPORT_SCHEDULE == "0 9 * * 1"
    assert AIRFLOW_MONTHLY_REPORT_SCHEDULE == "0 9 1 * *"
    for decision in scheduled:
        assert decision.enabled is True
        assert decision.runtime_deployed is True
        assert decision.registration_mode == AIRFLOW_REPORT_READINESS_REGISTRATION_MODE
        assert decision.trigger_policy == AIRFLOW_REPORT_READINESS_TRIGGER_POLICY
        assert decision.catchup is False
        assert decision.is_paused_upon_creation is False

    for decision in decisions:
        assert validate_runtime_decision(decision) == ()
        assert decision.catchup is False
        assert decision.max_active_runs == AIRFLOW_MAX_ACTIVE_RUNS
        assert decision.default_retries == AIRFLOW_DEFAULT_RETRIES
        assert set(AIRFLOW_ENV_VARS).issubset(decision.required_env_vars)
        assert set(AIRFLOW_OBSERVABILITY_SIGNALS).issubset(decision.observability_signals)
        assert decision.write_gate_policy == WRITE_GATE_POLICY
        assert decision.canonical_approval_boundary == CANONICAL_APPROVAL_BOUNDARY
        assert decision.writes_enabled is False
        assert decision.canonical_writes_allowed is False

    manual = {decision.dag_id: decision for decision in decisions if decision.dag_id not in AIRFLOW_SCHEDULED_REPORT_DAG_IDS}
    assert set(manual) == {"cms_live_replay", "cms_champion_1h_model_pipeline", "model_serving_pipeline"}
    for decision in manual.values():
        assert decision.enabled is False
        assert decision.runtime_deployed is True
        assert decision.registration_mode == AIRFLOW_REGISTRATION_MODE
        assert decision.trigger_policy == AIRFLOW_TRIGGER_POLICY
        assert decision.schedule is None
        assert decision.is_paused_upon_creation is True


def test_service_runtime_contracts_cover_task_retries_observability_connections_and_write_gates() -> None:
    for module in (
        daily_report_airflow,
        weekly_report_airflow,
        monthly_report_airflow,
        airflow_skeleton,
        champion_airflow_skeleton,
        model_serving_airflow_skeleton,
    ):
        contract = describe_service_runtime_contract(module)

        assert contract.tasks
        assert tuple(task.task_id for task in contract.tasks) == module.describe_dag().tasks
        assert contract.max_active_runs == AIRFLOW_MAX_ACTIVE_RUNS
        assert contract.default_retries == AIRFLOW_DEFAULT_RETRIES
        for task in contract.tasks:
            assert task.retries == AIRFLOW_DEFAULT_RETRIES
            assert task.retry_delay_seconds > 0
            assert task.trigger_rule == AIRFLOW_TASK_TRIGGER_RULE
            assert set(AIRFLOW_OBSERVABILITY_SIGNALS).issubset(task.observability_signals)
            assert task.writes_enabled is False
            assert task.canonical_writes_allowed is False
            assert not any(write.startswith("canonical.") for write in task.writes)
            if task.writes:
                assert task.write_gate == "noncanonical_write_contract_declared_but_runtime_writes_disabled"
            else:
                assert task.write_gate == "read_only"


def test_model_serving_airflow_contract_integrates_pmax_and_anomaly_lanes_without_canonical_writes() -> None:
    contract = describe_service_runtime_contract(model_serving_airflow_skeleton)
    task_contracts = model_serving_airflow_skeleton.task_contracts()

    assert contract.integration_lanes == ("pmax_forecast_15min", "anomaly_warning_1h", "combined_model_serving")
    build_reads = task_contracts["build_model_serving_input_queries"]["reads"]
    run_reads = task_contracts["run_model_serving_dry_run"]["reads"]
    run_writes = task_contracts["run_model_serving_dry_run"]["writes"]
    assert isinstance(build_reads, list)
    assert isinstance(run_reads, list)
    assert isinstance(run_writes, list)
    assert PMAX_FORECAST_INPUT_TABLE in build_reads
    assert ANOMALY_DETECTION_FEATURE_TABLE in build_reads
    assert PMAX_FORECAST_INPUT_TABLE in run_reads
    assert ANOMALY_DETECTION_FEATURE_TABLE in run_reads
    assert PMAX_FORECAST_TABLE in run_writes
    assert ANOMALY_DETECTION_FORECAST_TABLE in run_writes
    assert set(contract.required_connection_ids) >= {
        AIRFLOW_CONNECTION_IDS["postgres"],
        AIRFLOW_CONNECTION_IDS["model_artifacts"],
        AIRFLOW_CONNECTION_IDS["observability"],
    }
    assert all(not write.startswith("canonical.") for task in contract.tasks for write in task.writes)


def test_report_task_entrypoint_is_read_only_and_skips_external_probes_by_default(monkeypatch) -> None:
    monkeypatch.delenv(daily_report_airflow.PROBE_ENV_FLAG, raising=False)

    result = daily_report_airflow.airflow_task_entrypoint("build_readiness_report_status")

    assert result["ok"] is True
    assert result["blocked"] is False
    assert result["read_only"] is True
    assert result["writes_enabled"] is False
    assert result["canonical_writes_allowed"] is False
    assert result["probes"] == {
        "status": "skipped",
        "reason": "CMS_AIRFLOW_ENABLE_READINESS_PROBES=1 is required for external read-only probes",
    }


def test_report_external_probes_are_optional_and_secret_safe(monkeypatch) -> None:
    monkeypatch.setenv(daily_report_airflow.PROBE_ENV_FLAG, "1")
    for key in (
        "POSTGRES_HOST",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "DB_USER",
        "DB_PASS",
        "DB_PASSWORD",
        "GRAFANA_URL",
        "PROMETHEUS_URL",
    ):
        monkeypatch.delenv(key, raising=False)

    result = daily_report_airflow.airflow_task_entrypoint("collect_job_metadata_readiness")

    assert result["ok"] is True
    probes = result["probes"]
    assert isinstance(probes, dict)
    assert probes["postgres"] == {"status": "skipped", "reason": "postgres connection environment is missing or placeholder"}
    assert probes["grafana"] == {"status": "skipped", "reason": "GRAFANA_URL is not configured"}
    assert probes["prometheus"] == {"status": "skipped", "reason": "PROMETHEUS_URL is not configured"}


def test_airflow_runtime_policy_import_does_not_load_airflow() -> None:
    assert "airflow" not in sys.modules
