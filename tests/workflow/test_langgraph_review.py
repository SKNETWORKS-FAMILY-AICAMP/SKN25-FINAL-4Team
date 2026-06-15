"""Unit tests for the LangGraph review workflow and shared chat-route classifier.

These tests cover the deterministic path only. The optional ``langgraph`` dependency is exercised
just enough to confirm it is not imported at module import time.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from importlib.util import find_spec

import pytest

from cms.contracts.agent import (
    EvidencePacket,
    MetricEvidence,
    QaSummary,
    classify_route,
)
from cms.contracts.core import AgentRequest
from cms.workflow import langgraph_skeleton as lg
from cms.workflow.review_jobs import ReviewJobStore


def _state(text: str, **context: object) -> lg.GraphState:
    return lg.GraphState(request=AgentRequest(text=text, context=context))


# --- classify_route priority (policy §7.1) -------------------------------------------------


@pytest.mark.parametrize(
    ("text", "context", "expected"),
    [
        ("delete cache and schedule deploy", {}, "approval_required"),
        ("show power usage", {"qa_blocked": True}, "report_shell"),
        ("monthly report summary", {}, "needs_job"),
        ("show latest power usage", {}, "evidence_answer"),
        ("how does the cms work", {}, "quick_answer"),
    ],
)
def test_classify_route_priority(text: str, context: dict, expected: str) -> None:
    decision = classify_route(AgentRequest(text=text, context=context))
    assert decision.route == expected
    assert decision.needs_approval is (expected == "approval_required")


def test_classify_route_hint_wins() -> None:
    decision = classify_route(AgentRequest(text="anything at all", route_hint="needs_job"))
    assert decision.route == "needs_job"
    assert decision.needs_approval is False


def test_classify_evidence_cue_from_context_only() -> None:
    # No keyword in text, but a meter scope in context -> evidence_answer.
    decision = classify_route(AgentRequest(text="tell me about this", context={"meter_urns": ("meter:a",)}))
    assert decision.route == "evidence_answer"


# --- qa_gate / evidence ---------------------------------------------------------------------


def test_assess_qa_low_coverage_warns() -> None:
    qa = lg.assess_qa(AgentRequest(text="x", context={"coverage_ratio": 0.5}))
    assert qa.status == "warn"
    assert any(w.code == "coverage_gap" for w in qa.warnings)


def test_assess_qa_failed_check_blocks() -> None:
    qa = lg.assess_qa(AgentRequest(text="x", context={"qa_checks": {"schema": "fail"}}))
    assert qa.status == "blocked"
    assert qa.is_blocked


def test_run_review_evidence_pass_builds_packet() -> None:
    state = lg.run_review(_state("show power usage", request_id="r-1", coverage_ratio=0.95))
    assert state.route == "evidence_answer"
    assert state.evidence_packet is not None
    assert state.evidence_packet.qa_summary.status == "pass"
    assert state.evidence_packet.output_status == "draft"
    assert state.response.side_effects_executed is False


def test_run_review_qa_blocked_reroutes_to_report_shell() -> None:
    # Classifies as evidence_answer (no qa_blocked flag), but qa_gate finds a failed check and
    # the review falls back to report_shell before producing any evidence packet.
    state = lg.run_review(_state("show power usage", qa_checks={"value": "fail"}))
    assert state.route == "report_shell"
    assert state.report_draft is not None
    assert state.evidence_packet is None
    assert state.response.report_shell is True


# --- approval / job -------------------------------------------------------------------------


def test_run_review_approval_stops_before_side_effects() -> None:
    state = lg.run_review(_state("approve and write to canonical"))
    assert state.route == "approval_required"
    assert state.needs_human is True
    assert state.approval is not None
    assert state.approval.approved is False
    assert state.response.needs_human is True
    assert state.response.side_effects_executed is False


def test_run_review_job_handoff_is_queued() -> None:
    state = lg.run_review(_state("monthly report summary", job_id="job-7"))
    assert state.route == "needs_job"
    assert state.job is not None
    assert state.job.status == "queued"
    assert state.job.side_effects_executed is False
    assert state.response.job_ref == "/ops/jobs/job-7"


def test_review_job_store_registers_evidence_work_until_worker_runs() -> None:
    store = ReviewJobStore(id_prefix="test")
    submitted = store.submit(AgentRequest(text="show power usage", context={"request_id": "r-1", "coverage_ratio": 0.95}))

    assert submitted["mode"] == "job"
    assert submitted["route"] == "evidence_answer"
    assert submitted["status"] == "queued"
    snapshot = store.snapshot(submitted["job_id"])
    assert snapshot["status"] == "queued"
    assert snapshot["response"] is None

    processed = store.process(submitted["job_id"])
    assert processed["status"] == "succeeded"
    assert processed["awaiting_approval"] is False
    assert processed["response"]["route"] == "evidence_answer"
    assert processed["response"]["job_ref"] == f"/ops/jobs/{submitted['job_id']}"
    assert processed["response"]["qa_status"] == "pass"
    assert processed["side_effects_executed"] is False


def test_review_job_store_needs_job_response_refs_registered_ticket() -> None:
    store = ReviewJobStore(id_prefix="test")
    submitted = store.submit(AgentRequest(text="monthly report summary"))

    processed = store.process(submitted["job_id"])

    assert processed["status"] == "succeeded"
    assert processed["response"]["job_ref"] == f"/ops/jobs/{submitted['job_id']}"
    assert processed["job"]["result_ref"] == f"review:{submitted['job_id']}"


def test_review_job_store_approval_waits_for_human_and_defers_execution() -> None:
    store = ReviewJobStore(id_prefix="test")
    submitted = store.submit(AgentRequest(text="approve and write to canonical", user_id="viowlet"))

    processed = store.process(submitted["job_id"])
    assert processed["status"] == "running"
    assert processed["awaiting_approval"] is True
    assert processed["response"]["needs_human"] is True
    assert processed["response"]["job_ref"] == f"/ops/jobs/{submitted['job_id']}"
    assert processed["side_effects_executed"] is False

    approved = store.approve(submitted["job_id"], approved_by="viowlet")
    assert approved["status"] == "succeeded"
    assert approved["awaiting_approval"] is False
    assert approved["approved_by"] == "viowlet"
    assert approved["job"]["progress"]["execution"] == "deferred"
    assert approved["side_effects_executed"] is False


# --- evidence packet contract ---------------------------------------------------------------


def test_low_confidence_metric_is_not_assertable() -> None:
    metric = MetricEvidence(name="cooling_kwh", value=12.0, confidence="low")
    assert metric.is_assertable is False


def test_evidence_packet_blocked_requires_blocked_output() -> None:
    blocked = QaSummary(status="blocked")
    with pytest.raises(ValueError):
        EvidencePacket(packet_id="p", request_id="r", created_at=datetime.now(UTC), qa_summary=blocked, output_status="draft")
    # blocked output status is accepted
    EvidencePacket(packet_id="p", request_id="r", created_at=datetime.now(UTC), qa_summary=blocked, output_status="blocked")


# --- import-safety & optional langgraph -----------------------------------------------------


def test_descriptor_default_and_routes() -> None:
    descriptor = lg.make_langgraph()
    assert descriptor == lg.describe_graph()
    assert set(descriptor.routes) == {"quick_answer", "evidence_answer", "needs_job", "approval_required", "report_shell"}
    assert descriptor.side_effects_executed is False


def test_import_does_not_pull_optional_deps() -> None:
    code = (
        "import sys;"
        "import cms.contracts.agent, cms.workflow.langgraph_skeleton;"
        "blocked=[m for m in ('langgraph','langchain','openai','anthropic') if m in sys.modules];"
        "assert not blocked, blocked;"
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": "src", "PYTHONDONTWRITEBYTECODE": "1", "PATH": ""},
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


@pytest.mark.skipif(find_spec("langgraph") is None, reason="langgraph not installed")
def test_make_langgraph_enabled_builds_state_graph() -> None:
    graph = lg.make_langgraph(enabled=True)
    # A langgraph StateGraph exposes node-registration APIs.
    assert hasattr(graph, "add_node")


def test_make_langgraph_enabled_without_dep_returns_descriptor() -> None:
    if find_spec("langgraph") is not None:
        pytest.skip("langgraph is installed; cannot assert the missing-dependency path")
    descriptor = lg.make_langgraph(enabled=True)
    assert descriptor == lg.describe_graph()
    assert getattr(descriptor, "side_effects_executed") is False
