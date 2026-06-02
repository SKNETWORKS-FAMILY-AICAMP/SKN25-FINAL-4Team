"""Review-layer agent contracts and the shared chat-route classifier.

This module is import-safe: it never imports LangGraph, langchain, LLM SDKs, or any DB/network
client. It defines the policy chat-route decision (``classify_route``) plus the minimal evidence
packet / response dataclasses described in ``docs/qa/qa_report_chat_policy.md`` (§6, §7, §10).

``classify_route`` is shared by the FastAPI lightweight router (primary routing) and the LangGraph
review graph entry node, so it lives in the plane-neutral contracts package to avoid a workflow ->
service back-dependency.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from cms.contracts.core import AgentRequest, ChatRoute, RouteDecision
from cms.contracts.qa import CheckSeverity

# Priority-rule keyword sets (policy §7.1). Bilingual; continues the original skeleton lists.
APPROVAL_KEYWORDS = (
    "approve",
    "approval",
    "delete",
    "write",
    "deploy",
    "schedule",
    "control",
    "promote",
    "승인",
    "삭제",
    "쓰기",
    "배포",
    "스케줄",
    "제어",
    "승격",
    "권한",
)
JOB_KEYWORDS = (
    "report",
    "summary",
    "pdf",
    "docx",
    "backfill",
    "replay",
    "recompute",
    "aggregate",
    "batch",
    "리포트",
    "보고서",
    "요약",
    "집계",
    "재처리",
    "백필",
    "배치",
)
EVIDENCE_KEYWORDS = (
    "usage",
    "consumption",
    "power",
    "energy",
    "trend",
    "average",
    "peak",
    "coverage",
    "사용량",
    "전력",
    "소비",
    "평균",
    "추세",
    "피크",
    "커버리지",
)
EVIDENCE_CONTEXT_KEYS = ("time_window", "window", "meter_urns", "meters", "measurement", "metric")

Confidence = Literal["high", "medium", "low", "unavailable"]
QaPacketStatus = Literal["pass", "warn", "blocked"]
OutputStatus = Literal["draft", "final", "blocked", "needs_job", "approval_required"]


def _truthy(value: Any) -> bool:
    return bool(value)


def _has_evidence_cue(text: str, context: Mapping[str, Any]) -> bool:
    if any(keyword in text for keyword in EVIDENCE_KEYWORDS):
        return True
    return any(_truthy(context.get(key)) for key in EVIDENCE_CONTEXT_KEYS)


def classify_route(request: AgentRequest) -> RouteDecision:
    """Classify a request into one of the five policy chat routes (deterministic, no LLM/network).

    Priority order follows policy §7.1: approval/security > QA-blocked > long-running job >
    evidence-backed answer > general quick answer.
    """

    if request.route_hint is not None:
        return RouteDecision(
            route=request.route_hint,
            reason="explicit route_hint",
            needs_approval=request.route_hint == "approval_required",
        )

    text = request.text.lower()
    context = request.context or {}

    if any(keyword in text for keyword in APPROVAL_KEYWORDS) or _truthy(context.get("requires_approval")):
        return RouteDecision(route="approval_required", reason="approval or side-effect keyword detected", needs_approval=True)
    if _truthy(context.get("qa_blocked")):
        return RouteDecision(route="report_shell", reason="qa blocked; report shell only")
    if any(keyword in text for keyword in JOB_KEYWORDS) or _truthy(context.get("needs_job")):
        return RouteDecision(route="needs_job", reason="long-running aggregation/report job required")
    if _has_evidence_cue(text, context):
        return RouteDecision(route="evidence_answer", reason="time/meter/metric scoped question")
    return RouteDecision(route="quick_answer", reason="general question; no data scope required")


@dataclass(frozen=True)
class MetricEvidence:
    """One reported metric with its source references and confidence (policy §6)."""

    name: str
    value: float | str | None = None
    unit: str | None = None
    aggregation: str | None = None
    confidence: Confidence = "unavailable"
    source_refs: tuple[str, ...] = ()

    @property
    def is_assertable(self) -> bool:
        """Low/unavailable confidence metrics must not be stated as definitive figures."""

        return self.confidence in {"high", "medium"} and self.value is not None


@dataclass(frozen=True)
class QaWarning:
    """One disclosed QA warning attached to an evidence packet."""

    code: str
    message: str
    severity: CheckSeverity = "warning"


@dataclass(frozen=True)
class QaSummary:
    """Pre-model QA summary for a request window (policy §6 ``qa_summary``)."""

    status: QaPacketStatus
    checks: Mapping[str, str] = field(default_factory=dict)
    quarantined_count: int = 0
    warnings: tuple[QaWarning, ...] = ()

    @property
    def is_blocked(self) -> bool:
        return self.status == "blocked"


@dataclass(frozen=True)
class EvidencePacket:
    """Minimal evidence packet (policy §6). Required: packet/request id, created_at, qa status."""

    packet_id: str
    request_id: str
    created_at: datetime
    qa_summary: QaSummary
    data_sources: tuple[str, ...] = ()
    metrics: tuple[MetricEvidence, ...] = ()
    assumptions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    approval_required: bool = False
    output_status: OutputStatus = "draft"

    def __post_init__(self) -> None:
        # Policy §6.1: a blocked QA status must surface as blocked/approval_required output.
        if self.qa_summary.is_blocked and self.output_status not in {"blocked", "approval_required"}:
            raise ValueError("blocked qa_summary requires output_status in {blocked, approval_required}")


@dataclass(frozen=True)
class AgentResponse:
    """Final review-layer response with the policy §10 minimum disclosure fields."""

    route: ChatRoute
    message: str
    qa_status: QaPacketStatus | None = None
    evidence_packet: EvidencePacket | None = None
    report_shell: bool = False
    needs_human: bool = False
    job_ref: str | None = None
    next_action: str | None = None
    limitations: tuple[str, ...] = ()
    side_effects_executed: bool = False


__all__ = [
    "APPROVAL_KEYWORDS",
    "EVIDENCE_CONTEXT_KEYS",
    "EVIDENCE_KEYWORDS",
    "JOB_KEYWORDS",
    "AgentResponse",
    "Confidence",
    "EvidencePacket",
    "MetricEvidence",
    "OutputStatus",
    "QaPacketStatus",
    "QaSummary",
    "QaWarning",
    "classify_route",
]
