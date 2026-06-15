"""CMS LangGraph review workflow nodes.

Adapted from the team `uy/workspace` LangGraph review topology, but wired to the
active repository contracts (`AgentRequest.text`, string-literal `ChatRoute`).
The nodes are deterministic, import-safe, and perform no DB/vector/LLM I/O.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cms.contracts.agent import AgentResponse, EvidencePacket, MetricEvidence, QaSummary, QaWarning, classify_route
from cms.contracts.core import (
    CANONICAL_MEASUREMENT_15MIN,
    AgentRequest,
    ApprovalRequest,
    MeasurementWindow,
    ReportRequest,
)
from cms.contracts.job import ApiJob
from cms.workflow.state import GraphState

COVERAGE_MIN = 0.80


def _truthy(value: object) -> bool:
    return bool(value)


def assess_qa(request: AgentRequest) -> QaSummary:
    """Derive a QA summary from request context flags without data I/O."""

    context = request.context or {}
    checks: dict[str, str] = dict(context.get("qa_checks") or {})
    warnings: list[QaWarning] = []
    status = "blocked" if _truthy(context.get("qa_blocked")) else "pass"

    coverage = context.get("coverage_ratio")
    if isinstance(coverage, (int, float)):
        checks.setdefault("coverage", "pass" if coverage >= COVERAGE_MIN else "warn")
        if coverage < COVERAGE_MIN and status != "blocked":
            status = "warn"
            warnings.append(QaWarning("coverage_gap", f"coverage_ratio {coverage:.2f} below {COVERAGE_MIN}", "warning"))

    for name, check_status in checks.items():
        if check_status == "fail":
            warnings.append(QaWarning(f"{name}_error", f"{name} check failed", "error"))
            status = "blocked"

    quarantined = int(context.get("quarantined_count") or 0)
    return QaSummary(status=status, checks=checks, quarantined_count=quarantined, warnings=tuple(warnings))


def review_input_node(state: GraphState) -> GraphState:
    """Validate and normalize the review request shell."""

    state.messages.append(f"[review_input] text_len={len(state.request.text)}")
    return state


def state_contract_node(state: GraphState) -> GraphState:
    """Verify the no-write invariant for the review workflow."""

    if state.side_effects_executed:
        raise AssertionError("LangGraph review workflow must not execute side effects")
    state.messages.append("[state_contract] no-write contract verified")
    return state


def retrieve_specs_node(state: GraphState) -> GraphState:
    """Attach lightweight contract context without DB/vector/LLM access."""

    state.specs_context = "\n".join(
        [
            "## Active CMS review contracts",
            f"canonical_read_default={CANONICAL_MEASUREMENT_15MIN}",
            "workflow_scope=async evidence/report/job/approval review only",
            "side_effects=disabled",
        ]
    )
    state.messages.append("[retrieve_specs] active contract context attached")
    return state


def classify_node(state: GraphState) -> GraphState:
    """Classify the request into the five active chat routes."""

    decision = classify_route(state.request)
    state.route = decision.route
    state.route_reason = decision.reason
    state.request_type = decision.request_type
    state.agent_route = decision.agent_route
    state.messages.append(
        f"[classify] route={decision.route} request_type={decision.request_type} "
        f"agent_route={decision.agent_route} reason={decision.reason}"
    )
    return state


def qa_gate_node(state: GraphState) -> GraphState:
    """Run the deterministic QA gate used by the existing skeleton."""

    qa_summary = assess_qa(state.request)
    state.qa_summary = qa_summary
    state.messages.append(
        f"[qa_gate] status={qa_summary.status} checks={len(qa_summary.checks)} "
        f"warnings={len(qa_summary.warnings)}"
    )
    return state


def evidence_node(state: GraphState) -> GraphState:
    """Build an evidence packet from request context and QA results."""

    qa_summary = state.qa_summary or assess_qa(state.request)
    state.qa_summary = qa_summary
    context = state.request.context or {}
    request_id = str(context.get("request_id") or "req-unknown")
    metrics = tuple(context.get("metrics") or ())
    if not metrics and "coverage_ratio" in context:
        metrics = (
            MetricEvidence(
                name="coverage_ratio",
                value=context.get("coverage_ratio"),
                unit="ratio",
                confidence="high" if not qa_summary.is_blocked else "low",
                source_refs=("qa_gate",),
            ),
        )
    packet = EvidencePacket(
        packet_id=f"pkt-{request_id}",
        request_id=request_id,
        created_at=datetime.now(UTC),
        qa_summary=qa_summary,
        data_sources=tuple(context.get("data_sources") or ()),
        metrics=metrics,
        assumptions=tuple(context.get("assumptions") or ()),
        limitations=tuple(context.get("limitations") or ()),
        approval_required=False,
        output_status="blocked" if qa_summary.is_blocked else "draft",
    )
    state.evidence_packet = packet
    state.messages.append(f"[evidence] packet_id={packet.packet_id} status={packet.output_status}")
    return state


def report_draft_node(state: GraphState) -> GraphState:
    """Build a report shell; heavy report generation remains deferred."""

    context = state.request.context or {}
    window = context.get("window")
    if not isinstance(window, MeasurementWindow):
        window = MeasurementWindow(table=CANONICAL_MEASUREMENT_15MIN)
    title = str(context.get("title") or state.request.text[:80] or "CMS report shell")
    state.report_draft = ReportRequest(title=title, window=window)
    state.messages.append(f"[report_draft] title={title}")
    return state


def review_note_node(state: GraphState) -> GraphState:
    """Create a deterministic review note for async paths."""

    state.review_note_text = (
        f"route={state.route}; request_type={state.request_type}; agent_route={state.agent_route}; "
        f"qa={state.qa_summary.status if state.qa_summary else 'not_run'}; side_effects=disabled"
    )
    state.messages.append("[review_note] note prepared")
    return state


def review_artifact_node(state: GraphState) -> GraphState:
    """Create an in-memory artifact reference; no file is written here."""

    route = state.route or "quick_answer"
    state.artifact_id = f"review-{route}"
    state.artifact_path = f"memory://{state.artifact_id}"
    state.messages.append(f"[review_artifact] artifact_id={state.artifact_id}")
    return state


def approval_node(state: GraphState) -> GraphState:
    """Create a human approval request and stop before side effects."""

    context = state.request.context or {}
    action = str(context.get("action") or state.request.text[:80] or "side-effecting action")
    reason = str(context.get("reason") or state.route_reason or "approval required")
    state.approval = ApprovalRequest(action=action, reason=reason, approved=False)
    state.needs_human = True
    state.messages.append("[approval] human approval required")
    return state


def ops_approval_node(state: GraphState) -> GraphState:
    """Record that ops approval would be checked outside the graph."""

    state.messages.append("[ops_approval] deferred to operations gate")
    return state


def caveat_node(state: GraphState) -> GraphState:
    """Capture QA caveats for blocked or limited outputs."""

    if state.qa_summary and state.qa_summary.warnings:
        state.caveats.extend(w.message for w in state.qa_summary.warnings)
    if not state.caveats:
        state.caveats.append("QA or source evidence is insufficient for a final answer")
    state.messages.append(f"[caveat] count={len(state.caveats)}")
    return state


def job_node(state: GraphState) -> GraphState:
    """Create a queued API job contract without executing it."""

    context = state.request.context or {}
    job_id = str(context.get("job_id") or "job-pending")
    state.job = ApiJob(
        job_id=job_id,
        job_type="build_report_packet",
        status="queued",
        requested_by=state.request.user_id,
        request_payload={
            "text": state.request.text,
            "route": state.route,
            "request_type": state.request_type,
            "agent_route": state.agent_route,
        },
        progress={"review": state.review_note_text} if state.review_note_text else {},
        result_ref=state.artifact_id,
        side_effects_executed=False,
    )
    state.messages.append(f"[job] queued={state.job.job_id}")
    return state


def api_register_node(state: GraphState) -> GraphState:
    """Register the in-memory handoff in graph state only; no API/DB call."""

    if state.job is None and state.route in {"needs_job", "report_shell"}:
        state = job_node(state)
    state.messages.append("[api_register] deferred/no side effect")
    return state


def finalize_node(state: GraphState) -> GraphState:
    """Build the final AgentResponse with explicit side-effect disclosure."""

    route = state.route or "quick_answer"
    qa_status = state.qa_summary.status if state.qa_summary else None
    limitations = tuple(state.caveats) or (state.evidence_packet.limitations if state.evidence_packet else ())
    if state.request_type == "off_topic":
        message = "저는 에너지·설비 관리 관련 질문만 도와드릴 수 있습니다."
        next_action = "에너지 데이터, 설비 상태, 이상탐지, 예측 또는 보고서 질문으로 다시 요청하세요"
    elif route == "approval_required":
        message = "human approval required before any side effect"
        next_action = "route to approver"
    elif route == "needs_job":
        message = "long-running work queued as a background job"
        next_action = state.job.status_url if state.job else None
    elif route == "report_shell":
        message = "data is blocked or insufficient; returning report shell only"
        next_action = "supply missing data or resolve QA block"
    elif route == "evidence_answer":
        message = f"evidence-backed answer prepared (qa={qa_status or 'unknown'})"
        next_action = None
    else:
        message = "quick answer; handled in the FastAPI fast path"
        next_action = None
    state.response = AgentResponse(
        route=route,
        message=message,
        request_type=state.request_type,
        agent_route=state.agent_route,
        qa_status=qa_status,
        evidence_packet=state.evidence_packet,
        report_shell=state.report_draft is not None and route == "report_shell",
        needs_human=state.needs_human,
        job_ref=state.job.status_url if state.job else None,
        next_action=next_action,
        limitations=limitations,
        side_effects_executed=False,
    )
    state.messages.append(f"[finalize] route={route}")
    return state


__all__ = [
    "review_input_node",
    "state_contract_node",
    "retrieve_specs_node",
    "classify_node",
    "qa_gate_node",
    "evidence_node",
    "report_draft_node",
    "review_note_node",
    "review_artifact_node",
    "approval_node",
    "ops_approval_node",
    "caveat_node",
    "job_node",
    "api_register_node",
    "finalize_node",
]
