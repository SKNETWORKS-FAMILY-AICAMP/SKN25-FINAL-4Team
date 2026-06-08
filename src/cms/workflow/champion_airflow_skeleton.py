"""Disabled Airflow DAG skeleton for the CMS champion 1h model pipeline.

Importing this module is intentionally Airflow-free and side-effect-free. The skeleton only describes
future workflow boundaries; callers must explicitly opt in via ``make_airflow_dag(enabled=True)`` to
build an Airflow DAG, and that DAG is still paused and unscheduled by default.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from typing import Any

from cms.contracts.core import CANONICAL_SOURCE_TABLES

DAG_ID = "cms_champion_1h_model_pipeline"
AIRFLOW_DAG_ENABLED = False
TASK_IDS = (
    "load_run_config",
    "gate_manual_nonprod_run",
    "gate_kafka_t3b_t4_evidence",
    "gate_champion_model_artifact",
    "check_live_1h_readiness",
    "materialize_champion_1h_model_input",
    "validate_model_input_contract",
    "run_champion_1h_inference_adapter",
    "write_champion_1h_predictions",
    "evaluate_pre_warning_thresholds",
    "route_pre_warning_alerts",
    "wait_for_posthoc_actuals",
    "join_posthoc_actuals_and_errors",
    "evaluate_posthoc_anomaly_thresholds",
    "route_posthoc_alerts",
    "record_pipeline_metrics",
    "publish_evidence_packet",
)
LIVE_MEASUREMENT_1H = "live.measurement_1h"


@dataclass(frozen=True)
class DisabledChampionAirflowDag:
    """Plain-Python stand-in that Airflow schedulers can safely ignore."""

    dag_id: str = DAG_ID
    enabled: bool = AIRFLOW_DAG_ENABLED
    schedule: None = None
    tasks: tuple[str, ...] = TASK_IDS
    writes_allowed: bool = False
    canonical_source_tables: tuple[str, ...] = CANONICAL_SOURCE_TABLES
    canonical_writes_allowed: bool = False
    deployment_status: str = "disabled import-safe skeleton only"


def describe_dag() -> DisabledChampionAirflowDag:
    """Return the disabled DAG contract without importing Airflow."""

    return DisabledChampionAirflowDag()


def _contract(*, reads: list[str], writes: list[str], purpose: str) -> dict[str, object]:
    """Build a task contract that explicitly forbids canonical writes."""

    canonical_writes = set(CANONICAL_SOURCE_TABLES).intersection(writes)
    if canonical_writes:
        raise ValueError(f"champion skeleton task cannot declare canonical writes: {sorted(canonical_writes)}")
    return {
        "reads": reads,
        "writes": writes,
        "canonical_writes_allowed": False,
        "writes_enabled": False,
        "purpose": purpose,
    }


def task_contracts() -> dict[str, dict[str, object]]:
    """Task boundaries for future implementation; this module executes no I/O."""

    return {
        "load_run_config": _contract(
            reads=["manual_run_config"],
            writes=[],
            purpose="load an explicit non-production/manual run configuration",
        ),
        "gate_manual_nonprod_run": _contract(
            reads=["manual_run_config", "environment_policy"],
            writes=[],
            purpose="block scheduler/production execution; allow only explicit non-production dry runs",
        ),
        "gate_kafka_t3b_t4_evidence": _contract(
            reads=["kafka.t3b", "kafka.t4", "qa.kafka_ingestion_evidence"],
            writes=[],
            purpose="confirm required live Kafka evidence exists before any model placeholder work",
        ),
        "gate_champion_model_artifact": _contract(
            reads=["model_registry.champion_1h_artifact", "model_registry.champion_1h_contract"],
            writes=[],
            purpose="confirm the champion model artifact and contract are available",
        ),
        "check_live_1h_readiness": _contract(
            reads=[LIVE_MEASUREMENT_1H, "qa.live_1h_readiness"],
            writes=[],
            purpose="check source 1h measurement readiness without mutating canonical data",
        ),
        "materialize_champion_1h_model_input": _contract(
            reads=[LIVE_MEASUREMENT_1H, "feature_contracts.champion_1h_input"],
            writes=["candidate.champion_1h_model_input"],
            purpose="declare the non-canonical candidate model input materialization boundary",
        ),
        "validate_model_input_contract": _contract(
            reads=["candidate.champion_1h_model_input", "feature_contracts.champion_1h_input"],
            writes=[],
            purpose="validate model input schema and coverage before inference",
        ),
        "run_champion_1h_inference_adapter": _contract(
            reads=["candidate.champion_1h_model_input", "model_registry.champion_1h_artifact"],
            writes=["candidate.champion_1h_inference_result"],
            purpose="declare the adapter boundary for future champion 1h inference",
        ),
        "write_champion_1h_predictions": _contract(
            reads=["candidate.champion_1h_inference_result"],
            writes=["candidate.champion_1h_predictions"],
            purpose="declare non-canonical prediction output; canonical writes remain forbidden",
        ),
        "evaluate_pre_warning_thresholds": _contract(
            reads=["candidate.champion_1h_predictions", "policy.pre_warning_thresholds"],
            writes=["ops.pre_warning_threshold_evaluations"],
            purpose="evaluate pre-warning thresholds against candidate predictions",
        ),
        "route_pre_warning_alerts": _contract(
            reads=["ops.pre_warning_threshold_evaluations", "policy.alert_routes"],
            writes=["ops.pre_warning_alert_routes"],
            purpose="declare alert routing decisions without sending external notifications",
        ),
        "wait_for_posthoc_actuals": _contract(
            reads=[LIVE_MEASUREMENT_1H, "candidate.champion_1h_predictions"],
            writes=[],
            purpose="wait boundary for actual 1h measurements needed for posthoc evaluation",
        ),
        "join_posthoc_actuals_and_errors": _contract(
            reads=[LIVE_MEASUREMENT_1H, "candidate.champion_1h_predictions"],
            writes=["qa.champion_1h_posthoc_errors"],
            purpose="declare non-canonical posthoc error calculation boundary",
        ),
        "evaluate_posthoc_anomaly_thresholds": _contract(
            reads=["qa.champion_1h_posthoc_errors", "policy.posthoc_anomaly_thresholds"],
            writes=["ops.posthoc_anomaly_evaluations"],
            purpose="evaluate anomaly thresholds after actuals are available",
        ),
        "route_posthoc_alerts": _contract(
            reads=["ops.posthoc_anomaly_evaluations", "policy.alert_routes"],
            writes=["ops.posthoc_alert_routes"],
            purpose="declare posthoc alert routing decisions without external side effects",
        ),
        "record_pipeline_metrics": _contract(
            reads=[
                "candidate.champion_1h_predictions",
                "ops.pre_warning_threshold_evaluations",
                "ops.posthoc_anomaly_evaluations",
            ],
            writes=["ops.champion_1h_pipeline_metrics"],
            purpose="declare non-canonical operational metrics for the disabled skeleton",
        ),
        "publish_evidence_packet": _contract(
            reads=[
                "qa.kafka_ingestion_evidence",
                "ops.champion_1h_pipeline_metrics",
                "qa.champion_1h_posthoc_errors",
            ],
            writes=["qa.champion_1h_evidence_packets"],
            purpose="declare evidence packet publication boundary; no scheduler deployment occurs here",
        ),
    }


def make_airflow_dag(*, enabled: bool = AIRFLOW_DAG_ENABLED) -> object:
    """Optionally build an Airflow DAG; disabled by default and safe without Airflow installed."""

    if not enabled:
        return describe_dag()

    airflow = import_module("airflow")
    empty_operator = import_module("airflow.operators.empty")
    python_operator = import_module("airflow.operators.python")
    champion_tasks = import_module("cms.workflow.champion_tasks")
    dag_class = airflow.DAG
    empty_operator_class = empty_operator.EmptyOperator
    python_operator_class = python_operator.PythonOperator

    dag = dag_class(
        dag_id=DAG_ID,
        schedule=None,
        start_date=datetime(2026, 1, 1, tzinfo=UTC),
        catchup=False,
        is_paused_upon_creation=True,
        tags=["cms", "champion-1h", "skeleton", "disabled-by-default"],
    )
    with dag:
        previous: Any | None = None
        for task_id in TASK_IDS:
            task_callable = getattr(champion_tasks, task_id, None)
            if task_callable is None:
                task = empty_operator_class(task_id=task_id)
            else:
                task = python_operator_class(
                    task_id=task_id,
                    python_callable=champion_tasks.airflow_task_entrypoint,
                    op_kwargs={"task_id": task_id},
                    do_xcom_push=False,
                )
            if previous is not None:
                previous >> task
            previous = task
    return dag


__all__ = [
    "AIRFLOW_DAG_ENABLED",
    "DAG_ID",
    "LIVE_MEASUREMENT_1H",
    "TASK_IDS",
    "DisabledChampionAirflowDag",
    "describe_dag",
    "make_airflow_dag",
    "task_contracts",
]
