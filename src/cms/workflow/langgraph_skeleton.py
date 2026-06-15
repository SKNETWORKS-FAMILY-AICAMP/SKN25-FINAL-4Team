"""LangGraph-compatible async review workflow for CMS.

The real LangGraph dependency is optional and is never imported at module import time. This module
implements the policy chat-route review layer (``docs/qa/qa_report_chat_policy.md``) as a set of
deterministic, side-effect-free nodes over a frozen ``GraphState``:

    classify -> {approval | job | report | qa_gate -> {evidence | report}} -> finalize

Routing into this layer is performed upstream by the FastAPI lightweight router. The graph only owns
the async branches (evidence packet review, report draft, job handoff, human approval). It performs no
DB/Mongo/network I/O, generates no mart, and stops before any side effect on the approval branch.

LLM use is an optional hook: nodes accept ``llm=None`` and stay deterministic unless an explicit client
is injected. ``make_langgraph(enabled=True)`` is the only path that imports ``langgraph``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from importlib import import_module
from typing import Any

from cms.contracts.agent import (
    AgentResponse,
    EvidencePacket,
    QaSummary,
    QaWarning,
    classify_route,
)
from cms.contracts.core import (
    CANONICAL_MEASUREMENT_15MIN,
    AgentRequest,
    AgentRoute,
    ApprovalRequest,
    ChatRoute,
    MeasurementWindow,
    ReportRequest,
    RequestType,
    RouteDecision,
)
from cms.contracts.job import ApiJob, JobType

ROUTES: tuple[ChatRoute, ...] = ("quick_answer", "evidence_answer", "needs_job", "approval_required", "report_shell")
COVERAGE_MIN = 0.80


@dataclass(frozen=True)
class GraphState:
    """Frozen review state matching the intended LangGraph state shape.

    The ``side_effects_executed`` flag is part of the contract and must remain ``False``.
    """

    request: AgentRequest
    route: ChatRoute | None = None
    route_reason: str = ""
    request_type: RequestType = "query"
    agent_route: AgentRoute = "rag"
    qa_summary: QaSummary | None = None
    evidence_packet: EvidencePacket | None = None
    report_draft: ReportRequest | None = None
    approval: ApprovalRequest | None = None
    job: ApiJob | None = None
    messages: tuple[str, ...] = ()
    needs_human: bool = False
    response: AgentResponse | None = None
    side_effects_executed: bool = False


@dataclass(frozen=True)
class LangGraphSkeleton:
    """Fallback graph descriptor when LangGraph is absent or disabled."""

    routes: tuple[ChatRoute, ...] = ROUTES
    dependency: str = "langgraph optional"
    side_effects_executed: bool = False
    scope: str = "optional async evidence/report/job/approval review workflow only"


# ---------------------------------------------------------------------------
# Builders: deterministic, side-effect-free artifact construction.
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _truthy(value: Any) -> bool:
    return bool(value)


def assess_qa(request: AgentRequest) -> QaSummary:
    """Derive a QA summary from request context flags (no data I/O).

    Recognized context keys: ``qa_blocked`` (bool), ``qa_checks`` (name -> pass|warn|fail),
    ``coverage_ratio`` (float), ``quarantined_count`` (int).
    """

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


def build_evidence_packet(request: AgentRequest, qa_summary: QaSummary) -> EvidencePacket:
    """Assemble an evidence packet from QA results and any context-supplied metrics/sources."""

    context = request.context or {}
    request_id = str(context.get("request_id") or "req-unknown")
    metrics = tuple(context.get("metrics") or ())
    data_sources = tuple(context.get("data_sources") or ())
    assumptions = tuple(context.get("assumptions") or ())
    limitations = tuple(context.get("limitations") or ())
    output_status = "blocked" if qa_summary.is_blocked else "draft"
    return EvidencePacket(
        packet_id=f"pkt-{request_id}",
        request_id=request_id,
        created_at=_now(),
        qa_summary=qa_summary,
        data_sources=data_sources,
        metrics=metrics,
        assumptions=assumptions,
        limitations=limitations,
        approval_required=False,
        output_status=output_status,
    )


def build_report_draft(request: AgentRequest) -> ReportRequest:
    """Build a report shell (table of contents) with mart generation deferred."""

    context = request.context or {}
    window = context.get("window")
    if not isinstance(window, MeasurementWindow):
        window = MeasurementWindow(table=CANONICAL_MEASUREMENT_15MIN)
    title = str(context.get("title") or request.text[:80] or "CMS report shell")
    return ReportRequest(title=title, window=window)


def build_job(request: AgentRequest) -> ApiJob:
    """Build a queued background-job handoff; never executes the job."""

    context = request.context or {}
    job_type: JobType = context.get("job_type") or "build_report_packet"
    return ApiJob(
        job_id=str(context.get("job_id") or "job-pending"),
        job_type=job_type,
        status="queued",
        requested_by=request.user_id,
        request_payload={
            "text": request.text,
            "request_type": context.get("request_type"),
            "agent_route": context.get("agent_route"),
        },
        side_effects_executed=False,
    )


def build_approval(request: AgentRequest) -> ApprovalRequest:
    """Build a human-approval request; stays unapproved until a human acts."""

    context = request.context or {}
    action = str(context.get("action") or request.text[:80] or "side-effecting action")
    reason = str(context.get("reason") or "approval or side-effect keyword detected")
    return ApprovalRequest(action=action, reason=reason, approved=False)


def build_response(state: GraphState) -> AgentResponse:
    """Assemble the final response with the policy §10 minimum disclosure fields."""

    route: ChatRoute = state.route or "quick_answer"
    qa_status = state.qa_summary.status if state.qa_summary else None
    job_ref = state.job.status_url if state.job else None
    report_shell = state.report_draft is not None and route == "report_shell"
    limitations = state.evidence_packet.limitations if state.evidence_packet else ()
    message, next_action = _message_for(route, state)
    return AgentResponse(
        route=route,
        message=message,
        request_type=state.request_type,
        agent_route=state.agent_route,
        qa_status=qa_status,
        evidence_packet=state.evidence_packet,
        report_shell=report_shell,
        needs_human=state.needs_human,
        job_ref=job_ref,
        next_action=next_action,
        limitations=limitations,
        side_effects_executed=False,
    )


def _message_for(route: ChatRoute, state: GraphState) -> tuple[str, str | None]:
    if state.request_type == "off_topic":
        return (
            "저는 에너지·설비 관리 관련 질문만 도와드릴 수 있습니다.",
            "에너지 데이터, 설비 상태, 이상탐지, 예측 또는 보고서 질문으로 다시 요청하세요",
        )
    if route == "approval_required":
        return "human approval required before any side effect", "route to approver"
    if route == "needs_job":
        return "long-running work queued as a background job", state.job.status_url if state.job else None
    if route == "report_shell":
        return "data is blocked or insufficient; returning report shell only", "supply missing data / resolve QA block"
    if route == "evidence_answer":
        status = state.qa_summary.status if state.qa_summary else "unknown"
        return f"evidence-backed answer prepared (qa={status})", None
    return "quick answer; handled in the FastAPI fast path", None


# ---------------------------------------------------------------------------
# Nodes: GraphState -> GraphState (pure, deterministic, no side effects).
# ---------------------------------------------------------------------------


def classify_node(state: GraphState) -> GraphState:
    if state.route is not None:
        return state
    decision = classify_route(state.request)
    return replace(
        state,
        route=decision.route,
        route_reason=decision.reason,
        request_type=decision.request_type,
        agent_route=decision.agent_route,
    )


def qa_gate_node(state: GraphState) -> GraphState:
    qa = assess_qa(state.request)
    return replace(state, qa_summary=qa, messages=state.messages + (f"qa:{qa.status}",))


def evidence_node(state: GraphState) -> GraphState:
    qa = state.qa_summary or assess_qa(state.request)
    return replace(state, qa_summary=qa, evidence_packet=build_evidence_packet(state.request, qa))


def report_draft_node(state: GraphState) -> GraphState:
    return replace(state, report_draft=build_report_draft(state.request))


def job_node(state: GraphState) -> GraphState:
    context = dict(state.request.context or {})
    context.setdefault("request_type", state.request_type)
    context.setdefault("agent_route", state.agent_route)
    return replace(state, job=build_job(replace(state.request, context=context)))


def approval_node(state: GraphState) -> GraphState:
    return replace(state, approval=build_approval(state.request), needs_human=True)


def finalize_node(state: GraphState) -> GraphState:
    return replace(state, response=build_response(state))


def run_review(state: GraphState) -> GraphState:
    """Deterministic orchestrator mirroring the LangGraph topology (the tested path)."""

    state = classify_node(state)
    route = state.route
    if route == "approval_required":
        state = approval_node(state)
    elif route == "needs_job":
        state = job_node(state)
    elif route == "report_shell":
        state = report_draft_node(state)
    elif route == "evidence_answer":
        state = qa_gate_node(state)
        if state.qa_summary is not None and state.qa_summary.is_blocked:
            state = replace(state, route="report_shell", route_reason="qa blocked during evidence review")
            state = report_draft_node(state)
        else:
            state = evidence_node(state)
    return finalize_node(state)


def route_request(request: AgentRequest) -> RouteDecision:
    """Backward-compatible alias for :func:`cms.contracts.agent.classify_route`."""

    return classify_route(request)


def describe_graph() -> LangGraphSkeleton:
    """Return the routing graph contract without importing LangGraph."""

    return LangGraphSkeleton()


def make_langgraph(*, enabled: bool = False) -> object:
    """Optionally construct the LangGraph state graph; disabled by default.

    When enabled, callers should compile with ``interrupt_before=["approval"]`` (plus a checkpointer)
    so the approval branch halts before any side effect, matching the deterministic ``needs_human`` stop.
    """

    if not enabled:
        return describe_graph()

    try:
        graph_module = import_module("langgraph.graph")
    except (ImportError, ModuleNotFoundError):
        return describe_graph()
    state_graph_class = graph_module.StateGraph
    end_node = graph_module.END

    graph = state_graph_class(dict)
    graph.add_node("classify", _entry_node)
    graph.add_node("qa_gate", _wrap(qa_gate_node))
    graph.add_node("evidence", _wrap(evidence_node))
    graph.add_node("report", _wrap(report_draft_node))
    graph.add_node("job", _wrap(job_node))
    graph.add_node("approval", _wrap(approval_node))
    graph.add_node("finalize", _wrap(finalize_node))
    graph.set_entry_point("classify")
    graph.add_conditional_edges(
        "classify",
        lambda payload: payload["route"],
        {
            "approval_required": "approval",
            "needs_job": "job",
            "report_shell": "report",
            "evidence_answer": "qa_gate",
            "quick_answer": "finalize",
        },
    )
    graph.add_conditional_edges(
        "qa_gate",
        lambda payload: "report" if _state_blocked(payload) else "evidence",
        {"report": "report", "evidence": "evidence"},
    )
    for branch in ("approval", "job", "report", "evidence"):
        graph.add_edge(branch, "finalize")
    graph.add_edge("finalize", end_node)
    return graph


def _entry_node(payload: dict[str, Any]) -> dict[str, Any]:
    state = payload.get("state")
    if not isinstance(state, GraphState):
        route_hint = payload.get("route_hint")
        request = AgentRequest(
            text=str(payload.get("text", "")),
            route_hint=route_hint if route_hint in ROUTES else None,
            user_id=payload.get("user_id"),
            context=dict(payload.get("context") or {}),
        )
        state = GraphState(request=request)
    state = classify_node(state)
    return {"state": state, "route": state.route}


def _wrap(node: Any) -> Any:
    def _inner(payload: dict[str, Any]) -> dict[str, Any]:
        state: GraphState = payload["state"]
        new_state = node(state)
        return {"state": new_state, "route": new_state.route}

    return _inner


def _state_blocked(payload: dict[str, Any]) -> bool:
    state: GraphState = payload["state"]
    return state.qa_summary is not None and state.qa_summary.is_blocked


__all__ = [
    "COVERAGE_MIN",
    "ROUTES",
    "AgentResponse",
    "GraphState",
    "LangGraphSkeleton",
    "approval_node",
    "assess_qa",
    "build_approval",
    "build_evidence_packet",
    "build_job",
    "build_report_draft",
    "build_response",
    "classify_node",
    "describe_graph",
    "evidence_node",
    "finalize_node",
    "job_node",
    "make_langgraph",
    "qa_gate_node",
    "report_draft_node",
    "route_request",
    "run_review",
]
