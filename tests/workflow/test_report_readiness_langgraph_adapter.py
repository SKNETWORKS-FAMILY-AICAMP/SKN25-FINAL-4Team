from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any, cast

from cms.workflow import daily_report_airflow, report_readiness_airflow


def _mapping(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value)


def _iterable(value: object) -> Iterable[object]:
    return cast(Iterable[object], value)


def test_report_generation_uses_langgraph_service_path_by_default(monkeypatch) -> None:
    monkeypatch.delenv(report_readiness_airflow.PROBE_ENV_FLAG, raising=False)
    monkeypatch.delenv(report_readiness_airflow.LANGGRAPH_REPORT_BRANCH_ENV, raising=False)

    result = _mapping(daily_report_airflow.airflow_task_entrypoint("build_readiness_report_status"))

    assert result["ok"] is True
    assert result["blocked"] is False
    assert result["read_only"] is True
    assert result["writes_enabled"] is False
    assert result["canonical_writes_allowed"] is False
    review = _mapping(result["langgraph_review"])
    assert review["ok"] is True
    assert review["blocked"] is False
    assert review["engine"] == "cms.workflow.langgraph_review.run_review"
    assert review["route"] == "evidence_answer"
    assert review["agent_route"] == "report"
    assert review["qa_status"] == "pass"
    assert review["side_effects_executed"] is False
    assert review["writes_enabled"] is False
    assert review["canonical_writes_allowed"] is False
    evidence_packet = _mapping(review["evidence_packet"])
    qa_summary = _mapping(evidence_packet["qa_summary"])
    assert qa_summary["status"] == "pass"
    report_contract = _mapping(report_readiness_airflow.task_contracts()["build_readiness_report_status"])
    assert evidence_packet["data_sources"] == list(_iterable(report_contract["reads"]))
    json.dumps(result)


def test_report_status_builder_includes_langgraph_service_path_by_default(monkeypatch) -> None:
    monkeypatch.delenv(report_readiness_airflow.PROBE_ENV_FLAG, raising=False)
    monkeypatch.delenv(report_readiness_airflow.LANGGRAPH_REPORT_BRANCH_ENV, raising=False)

    result = report_readiness_airflow.build_readiness_report_status(scope="all")

    review = _mapping(result["langgraph_review"])
    assert review["engine"] == "cms.workflow.langgraph_review.run_review"
    assert review["route"] in {"evidence_answer", "report_shell"}
    assert review["side_effects_executed"] is False
    assert review["writes_enabled"] is False
    assert review["canonical_writes_allowed"] is False
    json.dumps(result)


def test_report_langgraph_branch_can_be_disabled_for_break_glass(monkeypatch) -> None:
    monkeypatch.setenv(report_readiness_airflow.LANGGRAPH_REPORT_BRANCH_ENV, "0")

    result = _mapping(daily_report_airflow.airflow_task_entrypoint("build_readiness_report_status"))

    assert "langgraph_review" not in result
    assert result["ok"] is True
    assert result["writes_enabled"] is False
    assert result["canonical_writes_allowed"] is False


def test_report_langgraph_context_preserves_probe_skipped_as_limitation(monkeypatch) -> None:
    monkeypatch.delenv(report_readiness_airflow.PROBE_ENV_FLAG, raising=False)
    snapshot = report_readiness_airflow.build_readiness_report_status(scope="all")

    context = report_readiness_airflow.build_report_langgraph_context(
        snapshot=snapshot,
        task_id="build_readiness_report_status",
    )

    assert context["qa_blocked"] is False
    qa_checks = cast(Mapping[str, object], context["qa_checks"])
    limitations = _iterable(context["limitations"])
    assert qa_checks["readiness_probes"] == "skipped"
    assert any("probe_skipped:" in str(limitation) for limitation in limitations)
    assert context["agent_route"] == "report"
    assert context["request_type"] == "query"


def test_report_langgraph_branch_turns_required_fail_into_report_shell(monkeypatch) -> None:
    monkeypatch.delenv(report_readiness_airflow.LANGGRAPH_REPORT_BRANCH_ENV, raising=False)
    snapshot = report_readiness_airflow.build_readiness_report_status(scope="all")
    snapshot["probes"] = {
        "postgres": {
            "status": "partial",
            "errors": {"model_input_freshness": "required relation missing: mart.anomaly_feature_1h"},
        },
        "grafana": {"status": "ok"},
        "prometheus": {"status": "ok"},
    }

    review = report_readiness_airflow.run_report_langgraph_branch(
        snapshot=snapshot,
        task_id="build_readiness_report_status",
    )

    assert review["ok"] is True
    assert review["route"] == "report_shell"
    assert review["qa_status"] == "blocked"
    assert review["report_shell"] is True
    assert review["side_effects_executed"] is False
    assert review["writes_enabled"] is False
    assert review["canonical_writes_allowed"] is False
    json.dumps(review)
