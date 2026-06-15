"""Disabled Airflow DAG skeleton for combined CMS model serving.

Importing this module never imports Airflow and never registers a scheduled DAG.
The DAG only connects the P-Max and anomaly serving lanes as an explicit manual
non-production dry-run contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import import_module
from typing import Any

from cms.contracts.anomaly_detection_1h import (
    ANOMALY_DETECTION_EVALUATION_TABLE,
    ANOMALY_DETECTION_FEATURE_TABLE,
    ANOMALY_DETECTION_FORECAST_TABLE,
    ANOMALY_DETECTION_INFERENCE_LOG_TABLE,
)
from cms.contracts.pmax_forecast_15min import (
    PMAX_FORECAST_EVALUATION_TABLE,
    PMAX_FORECAST_INFERENCE_LOG_TABLE,
    PMAX_FORECAST_INPUT_TABLE,
    PMAX_FORECAST_TABLE,
)
from cms.workflow.airflow_runtime_policy import (
    AIRFLOW_DEFAULT_RETRIES,
    AIRFLOW_MAX_ACTIVE_RUNS,
    AIRFLOW_RETRY_DELAY_SECONDS,
    AIRFLOW_TASK_TRIGGER_RULE,
)
from cms.workflow.model_serving_pipeline import TASK_IDS

DAG_ID = "model_serving_pipeline"
AIRFLOW_DAG_ENABLED = False
AIRFLOW_RUNTIME_DEPLOYED = True
AIRFLOW_DEPLOYMENT_STATUS = "registered as manual paused no-write DAG only"
AIRFLOW_INTEGRATION_LANES = ("pmax_forecast_15min", "anomaly_warning_1h", "combined_model_serving")


@dataclass(frozen=True)
class DisabledModelServingAirflowDag:
    """Plain-Python stand-in that Airflow schedulers can safely ignore."""

    dag_id: str = DAG_ID
    enabled: bool = AIRFLOW_DAG_ENABLED
    schedule: None = None
    tasks: tuple[str, ...] = TASK_IDS
    writes_allowed: bool = False
    canonical_writes_allowed: bool = False
    deployment_status: str = "disabled import-safe skeleton only"
    max_active_runs: int = AIRFLOW_MAX_ACTIVE_RUNS
    retries: int = AIRFLOW_DEFAULT_RETRIES
    retry_delay_seconds: int = AIRFLOW_RETRY_DELAY_SECONDS
    trigger_rule: str = AIRFLOW_TASK_TRIGGER_RULE


def describe_dag() -> DisabledModelServingAirflowDag:
    """Return the disabled DAG contract without importing Airflow."""

    return DisabledModelServingAirflowDag()


def _contract(*, reads: list[str], writes: list[str], purpose: str) -> dict[str, object]:
    if any(table.startswith("canonical.") for table in writes):
        raise ValueError("model-serving Airflow skeleton cannot declare canonical writes")
    return {
        "reads": reads,
        "writes": writes,
        "writes_enabled": False,
        "canonical_writes_allowed": False,
        "purpose": purpose,
    }


def task_contracts() -> dict[str, dict[str, object]]:
    """Task boundaries for future Airflow implementation; no I/O is executed."""

    return {
        "load_model_serving_run_config": _contract(
            reads=["manual_run_config"],
            writes=[],
            purpose="load one explicit manual non-production model-serving dry-run config",
        ),
        "gate_model_serving_manual_nonprod_run": _contract(
            reads=["manual_run_config", "environment_policy"],
            writes=[],
            purpose="block production, scheduler, non-dry-run, and write-enabled execution",
        ),
        "gate_model_serving_artifacts": _contract(
            reads=["runtime_artifact_mount", "pmax_release_descriptor", "anomaly_release_descriptor"],
            writes=[],
            purpose="verify artifact descriptors and mount policy without loading binaries",
        ),
        "build_model_serving_input_queries": _contract(
            reads=[PMAX_FORECAST_INPUT_TABLE, ANOMALY_DETECTION_FEATURE_TABLE],
            writes=[],
            purpose="build side-effect-free P-Max and anomaly SQL input query specs; anomaly lane remains config-gated until AWS tables exist",
        ),
        "run_model_serving_dry_run": _contract(
            reads=[PMAX_FORECAST_INPUT_TABLE, ANOMALY_DETECTION_FEATURE_TABLE, "model_artifact_mount"],
            writes=[PMAX_FORECAST_TABLE, ANOMALY_DETECTION_FORECAST_TABLE],
            purpose="declare P-Max and anomaly dry-run adapter execution boundary; repo callable keeps writes disabled",
        ),
        "validate_cross_lane_consistency": _contract(
            reads=[PMAX_FORECAST_TABLE, ANOMALY_DETECTION_FORECAST_TABLE],
            writes=[],
            purpose="verify timestamp, output-table, and disabled-branch consistency",
        ),
        "publish_model_serving_evidence_packet": _contract(
            reads=[
                PMAX_FORECAST_TABLE,
                PMAX_FORECAST_INFERENCE_LOG_TABLE,
                PMAX_FORECAST_EVALUATION_TABLE,
                ANOMALY_DETECTION_FORECAST_TABLE,
                ANOMALY_DETECTION_INFERENCE_LOG_TABLE,
                ANOMALY_DETECTION_EVALUATION_TABLE,
            ],
            writes=[],
            purpose="build combined evidence packet in memory; persistence remains write-gated",
        ),
    }


def make_airflow_dag(*, enabled: bool = AIRFLOW_DAG_ENABLED) -> object:
    """Optionally build an Airflow DAG; disabled by default and safe without Airflow installed."""

    if not enabled:
        return describe_dag()

    airflow = import_module("airflow")
    python_operator = import_module("airflow.operators.python")
    pipeline_tasks = import_module("cms.workflow.model_serving_pipeline")
    dag_class = airflow.DAG
    python_operator_class = python_operator.PythonOperator

    dag = dag_class(
        dag_id=DAG_ID,
        schedule=None,
        start_date=datetime(2026, 1, 1, tzinfo=UTC),
        catchup=False,
        is_paused_upon_creation=True,
        max_active_runs=AIRFLOW_MAX_ACTIVE_RUNS,
        default_args={"owner": "cms", "retries": AIRFLOW_DEFAULT_RETRIES, "retry_delay": timedelta(seconds=AIRFLOW_RETRY_DELAY_SECONDS)},
        tags=["cms", "model-serving", "pmax", "anomaly", "runtime-contract", "disabled-by-default"],
    )
    with dag:
        previous: Any | None = None
        for task_id in TASK_IDS:
            task = python_operator_class(
                task_id=task_id,
                python_callable=pipeline_tasks.airflow_xcom_task_entrypoint,
                op_kwargs={"task_id": task_id},
                do_xcom_push=True,
                trigger_rule=AIRFLOW_TASK_TRIGGER_RULE,
            )
            if previous is not None:
                previous >> task
            previous = task
    return dag


__all__ = [
    "AIRFLOW_DAG_ENABLED",
    "DAG_ID",
    "DisabledModelServingAirflowDag",
    "TASK_IDS",
    "describe_dag",
    "make_airflow_dag",
    "task_contracts",
]
