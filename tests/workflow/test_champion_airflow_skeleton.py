"""Tests for the disabled/import-safe champion 1h Airflow skeleton."""

from __future__ import annotations

import subprocess
import sys
import types
from dataclasses import is_dataclass

from cms.contracts.core import CANONICAL_SOURCE_TABLES
from cms.workflow import champion_airflow_skeleton as champion

EXPECTED_TASK_IDS = (
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


def test_import_does_not_require_or_load_airflow() -> None:
    code = (
        "import sys;"
        "import cms.workflow.champion_airflow_skeleton as champion;"
        "dag=champion.describe_dag();"
        "assert dag.dag_id == 'cms_champion_1h_model_pipeline';"
        "assert 'airflow' not in sys.modules;"
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": "src", "PYTHONDONTWRITEBYTECODE": "1", "PATH": ""},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_describe_dag_returns_disabled_dataclass_contract() -> None:
    dag = champion.describe_dag()

    assert is_dataclass(dag)
    assert dag.dag_id == "cms_champion_1h_model_pipeline"
    assert dag.enabled is False
    assert dag.schedule is None
    assert dag.writes_allowed is False
    assert dag.tasks == EXPECTED_TASK_IDS
    assert champion.TASK_IDS == EXPECTED_TASK_IDS


def test_task_contracts_state_reads_writes_and_no_canonical_writes() -> None:
    contracts = champion.task_contracts()

    assert tuple(contracts) == EXPECTED_TASK_IDS
    canonical_tables = set(CANONICAL_SOURCE_TABLES)
    assert canonical_tables
    assert any(contracts[task_id]["reads"] for task_id in EXPECTED_TASK_IDS)

    for task_id, contract in contracts.items():
        assert "reads" in contract, task_id
        assert "writes" in contract, task_id
        assert isinstance(contract["reads"], list), task_id
        assert isinstance(contract["writes"], list), task_id
        assert contract["canonical_writes_allowed"] is False, task_id
        assert canonical_tables.isdisjoint(contract["writes"]), task_id


def test_live_1h_tasks_read_live_measurement_1h_not_canonical_1h() -> None:
    contracts = champion.task_contracts()
    live_1h_task_ids = (
        "check_live_1h_readiness",
        "materialize_champion_1h_model_input",
        "wait_for_posthoc_actuals",
        "join_posthoc_actuals_and_errors",
    )

    for task_id in live_1h_task_ids:
        reads = contracts[task_id]["reads"]
        writes = contracts[task_id]["writes"]
        assert isinstance(reads, list), task_id
        assert isinstance(writes, list), task_id
        assert "live.measurement_1h" in reads, task_id
        assert "canonical.measurement_1h" not in reads, task_id
        assert set(CANONICAL_SOURCE_TABLES).isdisjoint(writes), task_id


def test_make_airflow_dag_disabled_returns_plain_contract_without_airflow_import() -> None:
    before = set(sys.modules)

    dag = champion.make_airflow_dag(enabled=False)

    assert dag == champion.describe_dag()
    assert "airflow" not in (set(sys.modules) - before)


def test_make_airflow_dag_enabled_lazy_imports_and_builds_paused_dag(monkeypatch) -> None:
    from cms.workflow import champion_tasks

    created_tasks: list[FakeBaseOperator] = []
    created_python_tasks: list[FakePythonOperator] = []
    active_dags: list[FakeDAG] = []

    class FakeDAG:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs
            self.task_ids: list[str] = []
            self.dependencies: list[tuple[str, str]] = []

        def __enter__(self) -> FakeDAG:
            active_dags.append(self)
            return self

        def __exit__(self, *exc_info: object) -> None:
            active_dags.pop()

    class FakeBaseOperator:
        def __init__(self, *, task_id: str) -> None:
            self.task_id = task_id
            created_tasks.append(self)
            active_dags[-1].task_ids.append(task_id)

        def __rshift__(self, other: FakeBaseOperator) -> FakeBaseOperator:
            active_dags[-1].dependencies.append((self.task_id, other.task_id))
            return other

    class FakeEmptyOperator(FakeBaseOperator):
        pass

    class FakePythonOperator(FakeBaseOperator):
        def __init__(self, *, task_id: str, python_callable: object, op_kwargs: dict[str, object] | None = None, do_xcom_push: bool = True) -> None:
            self.python_callable = python_callable
            self.op_kwargs = op_kwargs or {}
            self.do_xcom_push = do_xcom_push
            super().__init__(task_id=task_id)
            created_python_tasks.append(self)

    airflow_module = types.ModuleType("airflow")
    airflow_module.DAG = FakeDAG  # type: ignore[attr-defined]
    operators_module = types.ModuleType("airflow.operators")
    empty_operator_module = types.ModuleType("airflow.operators.empty")
    empty_operator_module.EmptyOperator = FakeEmptyOperator  # type: ignore[attr-defined]
    python_operator_module = types.ModuleType("airflow.operators.python")
    python_operator_module.PythonOperator = FakePythonOperator  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "airflow", airflow_module)
    monkeypatch.setitem(sys.modules, "airflow.operators", operators_module)
    monkeypatch.setitem(sys.modules, "airflow.operators.empty", empty_operator_module)
    monkeypatch.setitem(sys.modules, "airflow.operators.python", python_operator_module)

    dag = champion.make_airflow_dag(enabled=True)

    expected_python_task_ids = tuple(task_id for task_id in EXPECTED_TASK_IDS if hasattr(champion_tasks, task_id))
    assert isinstance(dag, FakeDAG)
    assert dag.kwargs["dag_id"] == "cms_champion_1h_model_pipeline"
    assert dag.kwargs["schedule"] is None
    assert dag.kwargs["catchup"] is False
    assert dag.kwargs["is_paused_upon_creation"] is True
    assert dag.task_ids == list(EXPECTED_TASK_IDS)
    assert [task.task_id for task in created_tasks] == list(EXPECTED_TASK_IDS)
    assert [task.task_id for task in created_python_tasks] == list(expected_python_task_ids)
    assert [task.python_callable for task in created_python_tasks] == [champion_tasks.airflow_task_entrypoint] * len(expected_python_task_ids)
    assert [task.op_kwargs for task in created_python_tasks] == [{"task_id": task_id} for task_id in expected_python_task_ids]
    assert [task.do_xcom_push for task in created_python_tasks] == [False] * len(expected_python_task_ids)
    assert dag.dependencies == list(zip(EXPECTED_TASK_IDS[:-1], EXPECTED_TASK_IDS[1:], strict=True))
