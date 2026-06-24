"""Daily readiness-first CMS report generation Airflow DAG contract."""

from __future__ import annotations

from cms.workflow import report_readiness_airflow as base
from cms.workflow.airflow_runtime_policy import AIRFLOW_DAILY_REPORT_SCHEDULE

DAG_ID = "daily_report"
REPORT_KIND = "daily"
AIRFLOW_DAG_ENABLED = base.AIRFLOW_DAG_ENABLED
AIRFLOW_RUNTIME_DEPLOYED = base.AIRFLOW_RUNTIME_DEPLOYED
AIRFLOW_REGISTRATION_MODE = base.AIRFLOW_REGISTRATION_MODE
AIRFLOW_TRIGGER_POLICY = base.AIRFLOW_TRIGGER_POLICY
AIRFLOW_CATCHUP = base.AIRFLOW_CATCHUP
AIRFLOW_IS_PAUSED_UPON_CREATION = base.AIRFLOW_IS_PAUSED_UPON_CREATION
AIRFLOW_XCOM_POLICY = base.AIRFLOW_XCOM_POLICY
AIRFLOW_DEPLOYMENT_STATUS = base.AIRFLOW_DEPLOYMENT_STATUS
AIRFLOW_INTEGRATION_LANES = ("daily_report", "qa_evidence", "promotion_check", "job_metadata")
TASK_IDS = base.TASK_IDS
PROBE_ENV_FLAG = base.PROBE_ENV_FLAG

def describe_dag() -> base.ReportReadinessAirflowDag:
    return base.describe_dag(dag_id=DAG_ID, schedule=AIRFLOW_DAILY_REPORT_SCHEDULE, report_kind=REPORT_KIND)

def task_contracts() -> dict[str, dict[str, object]]:
    return base.task_contracts()

def airflow_task_entrypoint(task_id: str, **context: object) -> dict[str, object]:
    return base.airflow_task_entrypoint(task_id, report_kind=REPORT_KIND, dag_id=DAG_ID, schedule=AIRFLOW_DAILY_REPORT_SCHEDULE, **context)

def make_airflow_dag(*, enabled: bool = AIRFLOW_DAG_ENABLED) -> object:
    return base.make_airflow_dag(enabled=enabled, dag_id=DAG_ID, schedule=AIRFLOW_DAILY_REPORT_SCHEDULE, report_kind=REPORT_KIND)
