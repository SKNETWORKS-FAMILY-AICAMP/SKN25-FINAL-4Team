"""Airflow runtime registration and service-grade contract policy for CMS DAGs.

This module is import-safe and intentionally does not import Airflow. It defines
CMS's scheduler target set: scheduled, readiness-first daily/weekly/monthly
report generation DAGs plus manual paused no-write registrations for model-serving,
champion, and live/replay DAGs.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

AIRFLOW_RUNTIME_CONTRACT_VERSION = "cms-airflow-service-contract-v1"
AIRFLOW_REGISTRATION_MODE = "disabled_by_default_service_contract"
AIRFLOW_TRIGGER_POLICY = "manual_dag_run_conf_required"
AIRFLOW_SCHEDULED_REPORT_DAG_IDS = ("daily_report", "weekly_report", "monthly_report")
AIRFLOW_DAILY_REPORT_SCHEDULE = "0 9 * * *"
AIRFLOW_WEEKLY_REPORT_SCHEDULE = "0 9 * * 1"
AIRFLOW_MONTHLY_REPORT_SCHEDULE = "0 9 1 * *"
AIRFLOW_SCHEDULED_REPORT_SCHEDULES = MappingProxyType(
    {
        "daily_report": AIRFLOW_DAILY_REPORT_SCHEDULE,
        "weekly_report": AIRFLOW_WEEKLY_REPORT_SCHEDULE,
        "monthly_report": AIRFLOW_MONTHLY_REPORT_SCHEDULE,
    }
)
AIRFLOW_REPORT_READINESS_DAG_ID = "daily_report"
AIRFLOW_REPORT_READINESS_REGISTRATION_MODE = "scheduled_readiness_first_report_generation"
AIRFLOW_REPORT_READINESS_SCHEDULE = AIRFLOW_DAILY_REPORT_SCHEDULE
AIRFLOW_REPORT_READINESS_TIMEZONE = "Asia/Seoul"
AIRFLOW_REPORT_READINESS_TRIGGER_POLICY = "scheduled_report_readiness_first_generation"
AIRFLOW_DEFAULT_RETRIES = 2
AIRFLOW_RETRY_DELAY_SECONDS = 300
AIRFLOW_MAX_ACTIVE_RUNS = 1
AIRFLOW_TASK_TRIGGER_RULE = "all_success"
AIRFLOW_ENV_VARS = (
    "CMS_RUNTIME_ENV",
    "CMS_AIRFLOW_DAG_ENABLED",
    "CMS_AIRFLOW_WRITE_GATE",
    "CMS_AIRFLOW_CANONICAL_APPROVAL_ID",
)
AIRFLOW_CONNECTION_IDS = MappingProxyType(
    {
        "postgres": "cms_postgres",
        "mongo": "cms_mongo",
        "kafka": "cms_kafka",
        "artifacts": "cms_artifacts",
        "observability": "cms_observability",
    }
)
AIRFLOW_OBSERVABILITY_SIGNALS = (
    "airflow.task.duration",
    "airflow.task.retries",
    "airflow.task.failure",
    "cms.workflow.task_contract",
)
CANONICAL_APPROVAL_BOUNDARY = (
    "canonical source tables are read-only for Airflow DAGs; any future canonical write requires "
    "CMS_AIRFLOW_CANONICAL_APPROVAL_ID plus a separate reviewed writer outside these DAG contracts"
)
WRITE_GATE_POLICY = "dry_run_or_noncanonical_only; canonical_writes_forbidden_without_external_approval"


@dataclass(frozen=True)
class AirflowTaskRuntimeContract:
    """Service-grade runtime contract for one Airflow task boundary."""

    task_id: str
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    required_connection_ids: tuple[str, ...]
    retries: int
    retry_delay_seconds: int
    trigger_rule: str
    xcom_policy: str
    observability_signals: tuple[str, ...]
    writes_enabled: bool
    canonical_writes_allowed: bool
    write_gate: str
    purpose: str


@dataclass(frozen=True)
class AirflowServiceRuntimeContract:
    """Import-safe runtime contract an Airflow deployment must satisfy."""

    dag_id: str
    version: str
    enabled_by_default: bool
    registration_mode: str
    schedule: str | None
    trigger_policy: str
    catchup: bool
    is_paused_upon_creation: bool
    max_active_runs: int
    default_retries: int
    retry_delay_seconds: int
    required_env_vars: tuple[str, ...]
    required_connection_ids: tuple[str, ...]
    observability_signals: tuple[str, ...]
    write_gate_policy: str
    canonical_approval_boundary: str
    integration_lanes: tuple[str, ...]
    tasks: tuple[AirflowTaskRuntimeContract, ...]


@dataclass(frozen=True)
class AirflowRuntimeDecision:
    """Verified runtime policy for one CMS Airflow DAG module."""

    dag_id: str
    enabled: bool
    runtime_deployed: bool
    registration_mode: str
    schedule: str | None
    catchup: bool
    is_paused_upon_creation: bool
    task_count: int
    writes_enabled: bool
    canonical_writes_allowed: bool
    xcom_policy: str
    deployment_status: str
    trigger_policy: str
    max_active_runs: int
    default_retries: int
    retry_delay_seconds: int
    required_env_vars: tuple[str, ...]
    required_connection_ids: tuple[str, ...]
    observability_signals: tuple[str, ...]
    write_gate_policy: str
    canonical_approval_boundary: str
    integration_lanes: tuple[str, ...]


def describe_service_runtime_contract(module: Any) -> AirflowServiceRuntimeContract:
    """Return the service-grade runtime contract for a CMS Airflow module."""

    dag = module.describe_dag()
    raw_contracts = module.task_contracts()
    task_contracts = tuple(
        _task_runtime_contract(task_id, raw_contracts[task_id], module=module)
        for task_id in dag.tasks
    )
    connection_ids = _unique(
        connection_id
        for task in task_contracts
        for connection_id in task.required_connection_ids
    )
    return AirflowServiceRuntimeContract(
        dag_id=module.DAG_ID,
        version=AIRFLOW_RUNTIME_CONTRACT_VERSION,
        enabled_by_default=bool(module.AIRFLOW_DAG_ENABLED),
        registration_mode=str(getattr(module, "AIRFLOW_REGISTRATION_MODE", AIRFLOW_REGISTRATION_MODE)),
        schedule=dag.schedule,
        trigger_policy=str(getattr(module, "AIRFLOW_TRIGGER_POLICY", AIRFLOW_TRIGGER_POLICY)),
        catchup=bool(getattr(module, "AIRFLOW_CATCHUP", False)),
        is_paused_upon_creation=bool(getattr(module, "AIRFLOW_IS_PAUSED_UPON_CREATION", True)),
        max_active_runs=AIRFLOW_MAX_ACTIVE_RUNS,
        default_retries=AIRFLOW_DEFAULT_RETRIES,
        retry_delay_seconds=AIRFLOW_RETRY_DELAY_SECONDS,
        required_env_vars=AIRFLOW_ENV_VARS,
        required_connection_ids=connection_ids,
        observability_signals=AIRFLOW_OBSERVABILITY_SIGNALS,
        write_gate_policy=WRITE_GATE_POLICY,
        canonical_approval_boundary=CANONICAL_APPROVAL_BOUNDARY,
        integration_lanes=tuple(getattr(module, "AIRFLOW_INTEGRATION_LANES", (module.DAG_ID,))),
        tasks=task_contracts,
    )


def describe_runtime_decision(module: Any) -> AirflowRuntimeDecision:
    """Return the current Airflow deployment policy for a CMS DAG module."""

    service_contract = describe_service_runtime_contract(module)
    writes_enabled = any(task.writes_enabled for task in service_contract.tasks)
    canonical_writes_allowed = any(task.canonical_writes_allowed for task in service_contract.tasks)
    xcom_policy = str(
        getattr(
            module,
            "AIRFLOW_XCOM_POLICY",
            "xcom_payloads_allowed" if module.DAG_ID == "model_serving_pipeline" else "xcom_disabled_or_not_required",
        )
    )
    return AirflowRuntimeDecision(
        dag_id=module.DAG_ID,
        enabled=service_contract.enabled_by_default,
        runtime_deployed=bool(getattr(module, "AIRFLOW_RUNTIME_DEPLOYED", False)),
        registration_mode=service_contract.registration_mode,
        schedule=service_contract.schedule,
        catchup=service_contract.catchup,
        is_paused_upon_creation=service_contract.is_paused_upon_creation,
        task_count=len(service_contract.tasks),
        writes_enabled=writes_enabled,
        canonical_writes_allowed=canonical_writes_allowed,
        xcom_policy=xcom_policy,
        deployment_status=str(getattr(module, "AIRFLOW_DEPLOYMENT_STATUS", "manual paused no-write DAG registration only")),
        trigger_policy=service_contract.trigger_policy,
        max_active_runs=service_contract.max_active_runs,
        default_retries=service_contract.default_retries,
        retry_delay_seconds=service_contract.retry_delay_seconds,
        required_env_vars=service_contract.required_env_vars,
        required_connection_ids=service_contract.required_connection_ids,
        observability_signals=service_contract.observability_signals,
        write_gate_policy=service_contract.write_gate_policy,
        canonical_approval_boundary=service_contract.canonical_approval_boundary,
        integration_lanes=service_contract.integration_lanes,
    )


def describe_all_runtime_decisions() -> tuple[AirflowRuntimeDecision, ...]:
    """Describe active CMS Airflow DAG deployment policies."""

    from cms.workflow import (
        daily_report_airflow,
        monthly_report_airflow,
        weekly_report_airflow,
    )

    return (
        describe_runtime_decision(daily_report_airflow),
        describe_runtime_decision(weekly_report_airflow),
        describe_runtime_decision(monthly_report_airflow),
    )


def validate_runtime_decision(decision: AirflowRuntimeDecision) -> tuple[str, ...]:
    """Return blocking policy issues for a runtime decision."""

    issues: list[str] = []
    if decision.dag_id in AIRFLOW_SCHEDULED_REPORT_SCHEDULES:
        expected_schedule = AIRFLOW_SCHEDULED_REPORT_SCHEDULES[decision.dag_id]
        if not decision.enabled:
            issues.append("scheduled_report_not_enabled")
        if not decision.runtime_deployed:
            issues.append("scheduled_report_not_runtime_deployed")
        if decision.registration_mode != AIRFLOW_REPORT_READINESS_REGISTRATION_MODE:
            issues.append("scheduled_report_registration_mode_mismatch")
        if decision.schedule != expected_schedule:
            issues.append("scheduled_report_schedule_mismatch")
        if decision.trigger_policy != AIRFLOW_REPORT_READINESS_TRIGGER_POLICY:
            issues.append("scheduled_report_trigger_policy_mismatch")
        if decision.is_paused_upon_creation:
            issues.append("scheduled_report_paused_on_creation")
    else:
        if decision.enabled:
            issues.append("airflow_manual_dag_enabled_by_default")
        if decision.registration_mode != AIRFLOW_REGISTRATION_MODE:
            issues.append("airflow_registration_mode_not_manual_contract")
        if decision.schedule is not None:
            issues.append("airflow_manual_schedule_enabled")
        if decision.trigger_policy != AIRFLOW_TRIGGER_POLICY:
            issues.append("airflow_trigger_policy_not_manual_conf")
        if not decision.is_paused_upon_creation:
            issues.append("airflow_manual_not_paused_on_creation")

    if decision.catchup:
        issues.append("airflow_catchup_enabled")
    if decision.max_active_runs != AIRFLOW_MAX_ACTIVE_RUNS:
        issues.append("airflow_max_active_runs_not_one")
    if decision.default_retries < AIRFLOW_DEFAULT_RETRIES:
        issues.append("airflow_retries_below_service_contract")
    if decision.retry_delay_seconds <= 0:
        issues.append("airflow_retry_delay_missing")
    if decision.writes_enabled and not (
        decision.dag_id in AIRFLOW_SCHEDULED_REPORT_SCHEDULES
        and not decision.canonical_writes_allowed
    ):
        issues.append("airflow_task_writes_enabled")
    if decision.canonical_writes_allowed:
        issues.append("airflow_canonical_writes_allowed")
    if decision.task_count <= 0:
        issues.append("airflow_task_contract_empty")
    if not set(AIRFLOW_ENV_VARS).issubset(decision.required_env_vars):
        issues.append("airflow_required_env_contract_incomplete")
    if not decision.required_connection_ids:
        issues.append("airflow_connection_contract_empty")
    if not set(AIRFLOW_OBSERVABILITY_SIGNALS).issubset(decision.observability_signals):
        issues.append("airflow_observability_contract_incomplete")
    if decision.write_gate_policy != WRITE_GATE_POLICY:
        issues.append("airflow_write_gate_policy_mismatch")
    if decision.canonical_approval_boundary != CANONICAL_APPROVAL_BOUNDARY:
        issues.append("airflow_canonical_approval_boundary_mismatch")
    return tuple(issues)


def _task_runtime_contract(task_id: str, contract: Mapping[str, object], *, module: Any) -> AirflowTaskRuntimeContract:
    reads = _strings(contract.get("reads", ()))
    writes = _strings(contract.get("writes", ()))
    canonical_writes_allowed = bool(contract.get("canonical_writes_allowed", False))
    writes_enabled = bool(contract.get("writes_enabled", False))
    xcom_policy = str(
        getattr(
            module,
            "AIRFLOW_TASK_XCOM_POLICY",
            "xcom_payload_required" if module.DAG_ID == "model_serving_pipeline" else "xcom_disabled_or_not_required",
        )
    )
    return AirflowTaskRuntimeContract(
        task_id=task_id,
        reads=reads,
        writes=writes,
        required_connection_ids=_infer_connection_ids(reads + writes, task_id=task_id),
        retries=AIRFLOW_DEFAULT_RETRIES,
        retry_delay_seconds=AIRFLOW_RETRY_DELAY_SECONDS,
        trigger_rule=AIRFLOW_TASK_TRIGGER_RULE,
        xcom_policy=xcom_policy,
        observability_signals=AIRFLOW_OBSERVABILITY_SIGNALS,
        writes_enabled=writes_enabled,
        canonical_writes_allowed=canonical_writes_allowed,
        write_gate=_write_gate(writes, canonical_writes_allowed=canonical_writes_allowed, writes_enabled=writes_enabled),
        purpose=str(contract.get("purpose", "")),
    )


def _infer_connection_ids(resources: tuple[str, ...], *, task_id: str) -> tuple[str, ...]:
    connection_ids: list[str] = []
    for resource in resources:
        if resource.startswith(("live.", "mart.", "ops.", "qa.", "candidate.")):
            connection_ids.append(AIRFLOW_CONNECTION_IDS["postgres"])
        if resource.startswith("mongo_"):
            connection_ids.append(AIRFLOW_CONNECTION_IDS["mongo"])
        if "kafka" in resource or resource.startswith("kafka."):
            connection_ids.append(AIRFLOW_CONNECTION_IDS["kafka"])
        if "artifact" in resource or resource.startswith("model_registry."):
            connection_ids.append(AIRFLOW_CONNECTION_IDS["artifacts"])
    if task_id.startswith(("record_", "publish_", "route_", "evaluate_")):
        connection_ids.append(AIRFLOW_CONNECTION_IDS["observability"])
    return _unique(connection_ids)


def _write_gate(writes: tuple[str, ...], *, canonical_writes_allowed: bool, writes_enabled: bool) -> str:
    if any(write.startswith("canonical.") for write in writes) or canonical_writes_allowed:
        return "blocked_until_canonical_approval_id_and_external_writer_review"
    if writes_enabled and writes and all(write in {"ops.daily_report", "ops.weekly_report", "ops.monthly_report"} for write in writes):
        return "readiness_gated_noncanonical_ops_report_write"
    if writes_enabled:
        return "blocked_by_default_write_gate"
    if writes:
        return "noncanonical_write_contract_declared_but_runtime_writes_disabled"
    return "read_only"


def _strings(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)  # type: ignore[operator]


def _unique(values: Any) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if value))


__all__ = [
    "AIRFLOW_CONNECTION_IDS",
    "AIRFLOW_DAILY_REPORT_SCHEDULE",
    "AIRFLOW_DEFAULT_RETRIES",
    "AIRFLOW_ENV_VARS",
    "AIRFLOW_MAX_ACTIVE_RUNS",
    "AIRFLOW_MONTHLY_REPORT_SCHEDULE",
    "AIRFLOW_OBSERVABILITY_SIGNALS",
    "AIRFLOW_REGISTRATION_MODE",
    "AIRFLOW_REPORT_READINESS_DAG_ID",
    "AIRFLOW_REPORT_READINESS_REGISTRATION_MODE",
    "AIRFLOW_REPORT_READINESS_SCHEDULE",
    "AIRFLOW_REPORT_READINESS_TIMEZONE",
    "AIRFLOW_REPORT_READINESS_TRIGGER_POLICY",
    "AIRFLOW_RETRY_DELAY_SECONDS",
    "AIRFLOW_RUNTIME_CONTRACT_VERSION",
    "AIRFLOW_SCHEDULED_REPORT_DAG_IDS",
    "AIRFLOW_SCHEDULED_REPORT_SCHEDULES",
    "AIRFLOW_TASK_TRIGGER_RULE",
    "AIRFLOW_TRIGGER_POLICY",
    "AIRFLOW_WEEKLY_REPORT_SCHEDULE",
    "CANONICAL_APPROVAL_BOUNDARY",
    "WRITE_GATE_POLICY",
    "AirflowRuntimeDecision",
    "AirflowServiceRuntimeContract",
    "AirflowTaskRuntimeContract",
    "describe_all_runtime_decisions",
    "describe_runtime_decision",
    "describe_service_runtime_contract",
    "validate_runtime_decision",
]
