"""Disabled Airflow DAG skeleton for CMS live/replay boundaries.

Importing this module never imports Airflow and never registers a scheduled DAG. If Airflow is later
installed, callers must explicitly opt in by calling ``make_airflow_dag(enabled=True)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import import_module
from typing import Any

from cms.contracts.core import CANONICAL_SOURCE_TABLES
from cms.workflow.airflow_runtime_policy import (
    AIRFLOW_DEFAULT_RETRIES,
    AIRFLOW_MAX_ACTIVE_RUNS,
    AIRFLOW_RETRY_DELAY_SECONDS,
    AIRFLOW_TASK_TRIGGER_RULE,
)

DAG_ID = "cms_live_replay"
AIRFLOW_DAG_ENABLED = False
AIRFLOW_RUNTIME_DEPLOYED = True
AIRFLOW_DEPLOYMENT_STATUS = "registered as manual paused no-write DAG only"
AIRFLOW_INTEGRATION_LANES = ("live_replay", "canonical_read_gate", "approval_routing")
TASK_IDS = (
    "validate_canonical_window",
    "describe_recent_cache_read",
    "route_report_or_approval",
)


@dataclass(frozen=True)
class DisabledAirflowDag:
    """Plain-Python stand-in that Airflow schedulers can safely ignore."""

    dag_id: str = DAG_ID
    enabled: bool = AIRFLOW_DAG_ENABLED
    schedule: None = None
    tasks: tuple[str, ...] = TASK_IDS
    canonical_source_tables: tuple[str, str] = CANONICAL_SOURCE_TABLES
    writes_allowed: bool = False
    mongo_role: str = "recent live/replay cache only"
    mart_generation_deferred: bool = True
    max_active_runs: int = AIRFLOW_MAX_ACTIVE_RUNS
    retries: int = AIRFLOW_DEFAULT_RETRIES
    retry_delay_seconds: int = AIRFLOW_RETRY_DELAY_SECONDS
    trigger_rule: str = AIRFLOW_TASK_TRIGGER_RULE


def describe_dag() -> DisabledAirflowDag:
    """Return the disabled DAG contract without importing Airflow."""

    return DisabledAirflowDag()


def task_contracts() -> dict[str, dict[str, object]]:
    """Task boundaries for future implementation; all are side-effect-free placeholders."""

    return {
        "validate_canonical_window": {
            "reads": list(CANONICAL_SOURCE_TABLES),
            "writes": [],
            "purpose": "validate request windows for 1h/15min canonical measurements",
        },
        "describe_recent_cache_read": {
            "reads": ["mongo_recent_live_replay_cache"],
            "writes": [],
            "purpose": "describe recent cache read shape only",
        },
        "route_report_or_approval": {
            "reads": [],
            "writes": [],
            "purpose": "hand off to query/report/approval routing skeleton",
        },
    }


def make_airflow_dag(*, enabled: bool = AIRFLOW_DAG_ENABLED) -> object:
    """Optionally build an Airflow DAG; disabled by default and safe without Airflow installed."""

    if not enabled:
        return describe_dag()

    airflow = import_module("airflow")
    empty_operator = import_module("airflow.operators.empty")
    dag_class = airflow.DAG
    empty_operator_class = empty_operator.EmptyOperator

    dag = dag_class(
        dag_id=DAG_ID,
        schedule=None,
        start_date=datetime(2026, 1, 1, tzinfo=UTC),
        catchup=False,
        is_paused_upon_creation=True,
        max_active_runs=AIRFLOW_MAX_ACTIVE_RUNS,
        default_args={"owner": "cms", "retries": AIRFLOW_DEFAULT_RETRIES, "retry_delay": timedelta(seconds=AIRFLOW_RETRY_DELAY_SECONDS)},
        tags=["cms", "runtime-contract", "disabled-by-default"],
    )
    with dag:
        previous: Any | None = None
        for task_id in TASK_IDS:
            task = empty_operator_class(task_id=task_id, trigger_rule=AIRFLOW_TASK_TRIGGER_RULE)
            if previous is not None:
                previous >> task
            previous = task
    return dag


__all__ = [
    "AIRFLOW_DAG_ENABLED",
    "DAG_ID",
    "TASK_IDS",
    "DisabledAirflowDag",
    "describe_dag",
    "make_airflow_dag",
    "task_contracts",
]
