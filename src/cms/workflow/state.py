"""Import-safe LangGraph review state contract.

This state mirrors the team `uy/workspace` LangGraph workflow shape while using
the active repository contracts. It is workflow-internal and does not change DB,
schema, or shared contract table names.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cms.contracts.agent import AgentResponse, EvidencePacket, QaSummary
from cms.contracts.core import AgentRequest, AgentRoute, ApprovalRequest, ChatRoute, ReportRequest, RequestType
from cms.contracts.job import ApiJob


@dataclass
class GraphState:
    """Mutable state for the async review workflow.

    The graph is import-safe and side-effect-free; `side_effects_executed` must
    stay False for every deterministic path.
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
    caveats: list[str] = field(default_factory=list)
    artifact_id: str | None = None
    artifact_path: str | None = None
    review_note_text: str = ""
    specs_context: str = ""
    messages: list[str] = field(default_factory=list)
    needs_human: bool = False
    response: AgentResponse | None = None
    side_effects_executed: bool = False


__all__ = ["GraphState"]
