"""CMS LangGraph async review workflow.

Team `uy/workspace` LangGraph topology is integrated here while preserving the
active repository DB/schema contracts. The module is import-safe: LangGraph is
imported only inside `make_langgraph(enabled=True)`.

Topology:
    review_input -> state_contract -> classify
      quick_answer -> finalize
      evidence/report/job/approval -> retrieve_specs -> qa_gate
        evidence + QA pass/warn -> evidence -> finalize
        evidence + QA blocked -> report_draft -> finalize
        report_shell -> report_draft -> finalize
        needs_job -> review_note -> review_artifact/caveat -> job/api_register -> finalize
        approval_required -> approval -> finalize
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cms.contracts.core import ChatRoute
from cms.workflow.nodes import (
    api_register_node,
    approval_node,
    caveat_node,
    classify_node,
    evidence_node,
    finalize_node,
    job_node,
    ops_approval_node,
    qa_gate_node,
    report_draft_node,
    retrieve_specs_node,
    review_artifact_node,
    review_input_node,
    review_note_node,
    state_contract_node,
)
from cms.workflow.state import GraphState

ROUTES: tuple[ChatRoute, ...] = ("quick_answer", "evidence_answer", "needs_job", "approval_required", "report_shell")

_NODE_REGISTRY: dict[str, Callable[[GraphState], GraphState]] = {
    "review_input": review_input_node,
    "state_contract": state_contract_node,
    "classify": classify_node,
    "retrieve_specs": retrieve_specs_node,
    "qa_gate": qa_gate_node,
    "evidence": evidence_node,
    "report_draft": report_draft_node,
    "review_note": review_note_node,
    "review_artifact": review_artifact_node,
    "approval": approval_node,
    "ops_approval": ops_approval_node,
    "caveat": caveat_node,
    "job": job_node,
    "api_register": api_register_node,
    "finalize": finalize_node,
}


def _route(state: GraphState) -> str:
    route = state.route
    if route == "quick_answer":
        return "finalize"
    if route in {"evidence_answer", "report_shell", "needs_job", "approval_required"}:
        return "retrieve_specs"
    return "finalize"


def _qa_route(state: GraphState) -> str:
    route = state.route
    if route == "evidence_answer":
        if state.qa_summary and state.qa_summary.is_blocked:
            return "report_draft"
        return "evidence"
    if route == "report_shell":
        return "report_draft"
    if route in {"needs_job", "approval_required"}:
        return "review_note"
    return "finalize"


def _draft_route(state: GraphState) -> str:
    if state.route == "approval_required":
        return "approval"
    if state.qa_summary and state.qa_summary.is_blocked:
        return "caveat"
    return "review_artifact"


def _run_nodes(state: GraphState, *node_names: str) -> GraphState:
    for name in node_names:
        state = _NODE_REGISTRY[name](state)
    return state


def run_review(state: GraphState) -> GraphState:
    """Run the deterministic review workflow without importing LangGraph."""

    state = _run_nodes(state, "review_input", "state_contract", "classify")

    stage_one = _route(state)
    if stage_one == "finalize":
        return _run_nodes(state, "finalize")

    state = _run_nodes(state, "retrieve_specs", "qa_gate")
    phase2 = _qa_route(state)

    if phase2 == "evidence":
        return _run_nodes(state, "evidence", "finalize")
    if phase2 == "report_draft":
        if state.route == "evidence_answer" and state.qa_summary and state.qa_summary.is_blocked:
            state.route = "report_shell"
            state.route_reason = "qa blocked during evidence review"
        return _run_nodes(state, "report_draft", "finalize")
    if phase2 == "review_note":
        state = _run_nodes(state, "review_note")
        phase3 = _draft_route(state)
        if phase3 == "approval":
            return _run_nodes(state, "approval", "finalize")
        if phase3 == "caveat":
            return _run_nodes(state, "caveat", "api_register", "finalize")
        return _run_nodes(state, "review_artifact", "job", "api_register", "finalize")

    if state.route == "approval_required":
        state = _run_nodes(state, "approval")
        if state.needs_human:
            return _run_nodes(state, "finalize")
        return _run_nodes(state, "ops_approval", "finalize")

    return _run_nodes(state, "finalize")


@dataclass(frozen=True)
class LangGraphReviewDescriptor:
    """Descriptor returned when LangGraph is disabled or unavailable."""

    routes: tuple[ChatRoute, ...] = ROUTES
    dependency: str = "langgraph optional"
    side_effects_executed: bool = False
    scope: str = "optional async evidence/report/job/approval review workflow only"
    nodes: tuple[str, ...] = tuple(_NODE_REGISTRY)


def describe_graph() -> LangGraphReviewDescriptor:
    return LangGraphReviewDescriptor()


def make_langgraph(*, enabled: bool = False, checkpointer: object | None = None) -> object:
    """Optionally build a LangGraph StateGraph.

    `enabled=False` returns the import-safe descriptor. `enabled=True` imports
    LangGraph lazily; if the optional dependency is absent, the descriptor is
    returned rather than importing any LLM/DB dependency.
    """

    if not enabled:
        return describe_graph()
    try:
        from langgraph.graph import END, StateGraph  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        return describe_graph()

    builder = StateGraph(dict)
    builder.add_node("review_input", _wrap("review_input"))
    builder.add_node("state_contract", _wrap("state_contract"))
    builder.add_node("classify", _wrap("classify"))
    builder.add_node("retrieve_specs", _wrap("retrieve_specs"))
    builder.add_node("qa_gate", _wrap("qa_gate"))
    builder.add_node("evidence", _wrap("evidence"))
    builder.add_node("report_draft", _wrap("report_draft"))
    builder.add_node("review_note", _wrap("review_note"))
    builder.add_node("review_artifact", _wrap("review_artifact"))
    builder.add_node("approval", _wrap("approval"))
    builder.add_node("caveat", _wrap("caveat"))
    builder.add_node("job", _wrap("job"))
    builder.add_node("api_register", _wrap("api_register"))
    builder.add_node("finalize", _wrap("finalize"))

    builder.set_entry_point("review_input")
    builder.add_edge("review_input", "state_contract")
    builder.add_edge("state_contract", "classify")
    builder.add_conditional_edges(
        "classify",
        lambda payload: _route(payload["state"]),
        {"finalize": "finalize", "retrieve_specs": "retrieve_specs"},
    )
    builder.add_edge("retrieve_specs", "qa_gate")
    builder.add_conditional_edges(
        "qa_gate",
        lambda payload: _qa_route(payload["state"]),
        {"evidence": "evidence", "report_draft": "report_draft", "review_note": "review_note", "finalize": "finalize"},
    )
    builder.add_conditional_edges(
        "review_note",
        lambda payload: _draft_route(payload["state"]),
        {"approval": "approval", "caveat": "caveat", "review_artifact": "review_artifact"},
    )
    builder.add_edge("review_artifact", "job")
    builder.add_edge("caveat", "api_register")
    builder.add_edge("job", "api_register")
    for branch in ("approval", "api_register", "evidence", "report_draft"):
        builder.add_edge(branch, "finalize")
    builder.add_edge("finalize", END)
    if checkpointer is not None:
        return builder.compile(checkpointer=checkpointer)
    return builder


def _wrap(node_name: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def _inner(payload: dict[str, Any]) -> dict[str, Any]:
        state = payload["state"]
        if not isinstance(state, GraphState):
            raise TypeError("payload['state'] must be cms.workflow.state.GraphState")
        return {"state": _NODE_REGISTRY[node_name](state)}

    return _inner


__all__ = [
    "GraphState",
    "LangGraphReviewDescriptor",
    "ROUTES",
    "describe_graph",
    "make_langgraph",
    "run_review",
]
