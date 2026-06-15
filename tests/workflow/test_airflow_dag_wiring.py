"""Tests for concrete Airflow DAG wrapper wiring."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from cms.workflow.model_serving_pipeline import airflow_xcom_task_entrypoint

REPO_ROOT = Path(__file__).resolve().parents[2]
DAGS_DIR = REPO_ROOT / "dags"


class FakeDAG:
    active: list["FakeDAG"] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = dict(kwargs)
        self.task_ids: list[str] = []
        self.dependencies: list[tuple[str, str]] = []

    def __enter__(self) -> "FakeDAG":
        self.active.append(self)
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.active.pop()


class FakeBaseOperator:
    def __init__(self, *, task_id: str, trigger_rule: str = "all_success") -> None:
        self.task_id = task_id
        self.trigger_rule = trigger_rule
        FakeDAG.active[-1].task_ids.append(task_id)

    def __rshift__(self, other: "FakeBaseOperator") -> "FakeBaseOperator":
        FakeDAG.active[-1].dependencies.append((self.task_id, other.task_id))
        return other


class FakeEmptyOperator(FakeBaseOperator):
    pass


class FakePythonOperator(FakeBaseOperator):
    constructed: list["FakePythonOperator"] = []

    def __init__(
        self,
        *,
        task_id: str,
        python_callable: object,
        op_kwargs: dict[str, object] | None = None,
        do_xcom_push: bool = True,
        trigger_rule: str = "all_success",
    ) -> None:
        self.python_callable = python_callable
        self.op_kwargs = op_kwargs or {}
        self.do_xcom_push = do_xcom_push
        super().__init__(task_id=task_id, trigger_rule=trigger_rule)
        self.constructed.append(self)


def _install_fake_airflow(monkeypatch: Any) -> None:
    FakeDAG.active.clear()
    FakePythonOperator.constructed.clear()
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


def _import_wrapper(filename: str) -> types.ModuleType:
    path = DAGS_DIR / filename
    module_name = f"_test_airflow_wrapper_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_airflow_dag_wrappers_import_without_running_task_callables(monkeypatch: Any) -> None:
    _install_fake_airflow(monkeypatch)

    expected = {
        "daily_report.py": ("daily_report", "0 9 * * *", False),
        "weekly_report.py": ("weekly_report", "0 9 * * 1", False),
        "monthly_report.py": ("monthly_report", "0 9 1 * *", False),
        "cms_live_replay.py": ("cms_live_replay", None, True),
        "cms_champion_1h_model_pipeline.py": ("cms_champion_1h_model_pipeline", None, True),
        "model_serving_pipeline.py": ("model_serving_pipeline", None, True),
    }

    for filename, (dag_id, schedule, paused) in expected.items():
        before_python_operator_count = len(FakePythonOperator.constructed)
        module = _import_wrapper(filename)
        dag = module.dag

        assert isinstance(dag, FakeDAG)
        assert dag.kwargs["dag_id"] == dag_id
        assert dag.kwargs["schedule"] == schedule
        assert dag.kwargs["catchup"] is False
        assert dag.kwargs["is_paused_upon_creation"] is paused
        assert dag.kwargs["max_active_runs"] == 1
        assert dag.task_ids
        # Constructing PythonOperator wrappers must not call the task callables.
        for operator in FakePythonOperator.constructed[before_python_operator_count:]:
            assert callable(operator.python_callable)


def test_model_serving_compose_mounts_actual_airflow_dags_folder() -> None:
    compose_text = (REPO_ROOT / "docker" / "compose.model_serving.yml").read_text(encoding="utf-8")

    assert DAGS_DIR.is_dir()
    assert "AIRFLOW__CORE__DAGS_FOLDER: \"/opt/airflow/dags\"" in compose_text
    assert "- ../dags:/opt/airflow/dags:ro" in compose_text
    assert "daily_report,weekly_report,monthly_report,cms_live_replay,cms_champion_1h_model_pipeline,model_serving_pipeline" in compose_text
    assert "cms.runtime.scheduled_dags: \"daily_report,weekly_report,monthly_report\"" in compose_text


def test_model_serving_airflow_fixture_dry_run_executes_in_memory_no_write_path() -> None:
    result = airflow_xcom_task_entrypoint(
        "run_model_serving_dry_run",
        dag_run=SimpleNamespace(conf=_model_serving_fixture_conf()),
    )

    assert result["ok"] is True
    assert result["blocked"] is False
    assert result["data"]["write_attempted"] is False
    assert result["data"]["canonical_write_attempted"] is False
    assert result["data"]["write_batch"]["writes_enabled"] is False
    assert result["data"]["write_batch"]["canonical_writes_enabled"] is False
    assert result["data"]["packet"]["writes_enabled"] is False
    assert result["data"]["packet"]["canonical_writes_enabled"] is False
    assert result["data"]["pmax_forecast_rows"]


def test_model_serving_airflow_fixture_cross_validation_and_publish_execute() -> None:
    conf = _model_serving_fixture_conf()

    cross = airflow_xcom_task_entrypoint("validate_cross_lane_consistency", dag_run=SimpleNamespace(conf=conf))
    publish = airflow_xcom_task_entrypoint("publish_model_serving_evidence_packet", dag_run=SimpleNamespace(conf=conf))

    assert cross["ok"] is True
    assert cross["blocked"] is False
    assert publish["ok"] is True
    assert publish["blocked"] is False
    assert publish["data"]["packet"]["writes_enabled"] is False
    assert publish["data"]["packet"]["canonical_writes_enabled"] is False


def test_model_serving_airflow_dry_run_remains_blocked_without_fixture_mode() -> None:
    result = airflow_xcom_task_entrypoint(
        "run_model_serving_dry_run",
        dag_run=SimpleNamespace(conf={"base_ts": "2023-06-01T00:00:00+00:00"}),
    )

    assert result["ok"] is False
    assert result["blocked"] is True
    assert "runtime materialized P-Max rows" in result["errors"][0]


def _model_serving_fixture_conf() -> dict[str, object]:
    return {
        "base_ts": "2023-06-01T00:00:00+00:00",
        "runtime_fixture_enabled": True,
        "pmax_logical_meters": ["H2.Z35x", "H2.Z36x"],
    }
