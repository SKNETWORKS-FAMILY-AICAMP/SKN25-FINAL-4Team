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
from typing import Any, Literal, cast

from cms.contracts.core import AgentRequest, AgentRoute, ChatRoute, RequestType, RouteDecision
from cms.contracts.qa import CheckSeverity

# Priority-rule keyword sets (policy §7.1). Bilingual; aligned with the team router benchmark
# while preserving the active AgentRequest(text=...) + RouteDecision contract.
APPROVAL_KEYWORDS = (
    "승인",
    "허가",
    "결재",
    "권한",
    "강제",
    "삭제",
    "수정",
    "변경",
    "덮어써",
    "위험",
    "approval",
    "approve",
    "approved",
    "side.effect",
    "side-effect",
    "dangerous",
    "destructive",
    "force",
    "mandatory",
    "write",
    "modify",
    "delete",
    "update",
    "insert",
    "truncate",
    "drop",
    "deploy",
    "schedule",
    "control",
    "promote",
    "쓰기",
    "배포",
    "스케줄",
    "제어",
    "승격",
)
JOB_KEYWORDS = (
    "report",
    "aggregate",
    "replay",
    "backfill",
    "batch",
    "generate",
    "long running",
    "ingestion",
    "queue",
    "full benchmark",
    "csv",
    "file",
    "html",
    "리포트",
    "보고서",
    "집계",
    "리플레이",
    "백필",
    "배치",
    "생성",
    "일괄",
    "계산",
    "통계",
    "등록",
    "다시 시작",
    "큐에",
    "전체 산출물",
    "산출물",
    "패키지",
)
JOB_ACTION_KEYWORDS = (
    "생성",
    "만들",
    "실행",
    "등록",
    "큐",
    "일괄",
    "배치",
    "백필",
    "리플레이",
    "예약",
    "내보내",
    "generate",
    "run",
    "execute",
    "register",
    "queue",
    "batch",
    "backfill",
    "replay",
    "schedule",
    "export",
)
EVIDENCE_KEYWORDS = (
    "meter",
    "measurement",
    "hour",
    "day",
    "date",
    "coverage",
    "gap",
    "anomaly",
    "value",
    "source_doc",
    "reference_context",
    "health score",
    "kwh",
    "cop",
    "ahu",
    "equipment",
    "maintenance",
    "work order",
    "usage",
    "consumption",
    "power",
    "energy",
    "trend",
    "average",
    "peak",
    "계량기",
    "시간",
    "값",
    "데이터",
    "이상",
    "탐지",
    "결측",
    "근거",
    "과징금",
    "위반",
    "기간",
    "작업지시서",
    "작업 지시",
    "자가소비율",
    "평균값",
    "분포",
    "계통의존도",
    "총소비전력",
    "소비전력",
    "냉방에너지",
    "난방에너지",
    "월간 리포트",
    "건강 점수",
    "설비",
    "유지보수",
    "예방 정비",
    "사용량",
    "전력",
    "소비",
    "평균",
    "추세",
    "피크",
    "커버리지",
)
EVIDENCE_INTENT_KEYWORDS = (
    "확인",
    "알려",
    "보여",
    "찾아",
    "설명",
    "몇 건",
    "반영",
    "근거",
    "분포",
    "원인",
    "사유",
    "비교",
    "분석",
    "추세",
    "평가",
    "진단",
    "결과",
    "어떻게",
    "무엇",
    "평균",
    "소요",
    "얼마",
    "몇",
    "현황",
    "상태",
    "check",
    "show",
    "find",
    "explain",
    "how many",
    "why",
    "compare",
    "trend",
)
EVIDENCE_CONTEXT_KEYS = (
    "time_window",
    "window",
    "meter_urns",
    "meters",
    "measurement",
    "metric",
    "source_doc",
    "reference_context",
)

REQUEST_TYPES: tuple[RequestType, ...] = ("query", "action_request", "approval_required", "off_topic")
AGENT_ROUTES: tuple[AgentRoute, ...] = ("anomaly", "cms", "forecast", "report", "rag")
REQUEST_TYPE_CONTEXT_KEY = "request_type"
AGENT_ROUTE_CONTEXT_KEY = "agent_route"
OFF_TOPIC_KEYWORDS = (
    "주식",
    "코인",
    "비트코인",
    "가상 화폐",
    "암호 화폐",
    "요리",
    "레시피",
    "맛집",
    "점심 메뉴",
    "저녁 메뉴",
    "연예인",
    "드라마",
    "영화",
    "스포츠",
    "야구",
    "축구",
    "농구",
    "정치",
    "선거",
    "오늘 날씨",
    "내일 날씨",
    "날씨 예보",
    "의료",
    "병원",
    "증상",
    "게임",
    "유튜브",
    "틱톡",
    "stock",
    "bitcoin",
    "crypto",
    "recipe",
    "restaurant",
    "weather forecast",
    "celebrity",
    "movie",
    "sports",
    "politics",
    "election",
    "medical",
    "hospital",
)
ACTION_REQUEST_KEYWORDS = (
    "작업 요청",
    "작업요청",
    "작업 지시",
    "작업지시",
    "작업 지시서",
    "정비 요청",
    "수리 요청",
    "점검 요청",
    "점검해줘",
    "점검 해줘",
    "조치 요청",
    "교체 요청",
    "예지보전",
    "repair",
    "work order",
    "maintenance request",
    "service request",
    "dispatch technician",
)
ANOMALY_ROUTE_KEYWORDS = (
    "이상 탐지",
    "이상탐지",
    "이상 발생",
    "이상 이력",
    "이상 건수",
    "이상 원인",
    "이상 분석",
    "이상 추이",
    "비정상",
    "스파이크",
    "급등",
    "급락",
    "급증",
    "경보",
    "알람",
    "잔차",
    "fault",
    "anomal",
    "powerspike",
    "copdrop",
    "chpoutage",
    "nightconsumption",
    "pvnightnonzero",
)
CMS_ROUTE_KEYWORDS = (
    "작업 지시",
    "작업지시",
    "정비",
    "수리",
    "예지보전",
    "상태 감시",
    "헬스",
    "진단",
    "설비 상태",
    "설비 점검",
    "설비 확인",
    "설비 문제",
    "냉방 설비",
    "수전 설비",
    "열병합",
    "시뮬",
    "work order",
    "maintenance",
    "equipment status",
    "equipment health",
)
FORECAST_ROUTE_KEYWORDS = (
    "예측",
    "전망",
    "앞으로",
    "내일",
    "다음 주",
    "장기",
    "예상",
    "미래",
    "계속될까",
    "계속 될까",
    "낮아질까",
    "높아질까",
    "늘어날까",
    "줄어들까",
    "떨어질까",
    "올라갈까",
    "forecast",
    "predict",
    "prediction",
    "tomorrow",
    "future",
)
REPORT_ROUTE_KEYWORDS = (
    "보고서",
    "리포트",
    "월간",
    "통계",
    "실적",
    "집계",
    "요금",
    "비용",
    "자급률",
    "자가소비율",
    "의존도",
    "kpi",
    "report",
    "monthly",
    "statistics",
    "cost",
)
METER_URN_MARKERS = (".z", "meter:")

Confidence = Literal["high", "medium", "low", "unavailable"]
QaPacketStatus = Literal["pass", "warn", "blocked"]
OutputStatus = Literal["draft", "final", "blocked", "needs_job", "approval_required"]


def _truthy(value: Any) -> bool:
    return bool(value)


def _match_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)


def _has_evidence_cue(text: str, context: Mapping[str, Any]) -> bool:
    if _match_any(text, EVIDENCE_KEYWORDS):
        return True
    return any(_truthy(context.get(key)) for key in EVIDENCE_CONTEXT_KEYS)


def _context_request_type(context: Mapping[str, Any]) -> RequestType | None:
    value = context.get(REQUEST_TYPE_CONTEXT_KEY)
    if isinstance(value, str) and value in REQUEST_TYPES:
        return cast(RequestType, value)
    return None


def _context_agent_route(context: Mapping[str, Any]) -> AgentRoute | None:
    value = context.get(AGENT_ROUTE_CONTEXT_KEY)
    if isinstance(value, str) and value in AGENT_ROUTES:
        return cast(AgentRoute, value)
    return None


def _has_meter_marker(text: str, context: Mapping[str, Any]) -> bool:
    if any(marker in text for marker in METER_URN_MARKERS):
        return True
    return _truthy(context.get("meter_urns")) or _truthy(context.get("meters"))


def classify_request_type(request: AgentRequest) -> tuple[RequestType, str]:
    """Stage 1 of the uy-style router: query/action/approval/off-topic.

    This is deliberately rule-only and import-safe. Approval/side-effect cues win
    before off-topic so destructive DB/API requests keep the approval gate.
    """

    context = request.context or {}
    explicit = _context_request_type(context)
    if explicit is not None:
        return explicit, "context.request_type"

    text = request.text.lower()
    if _match_any(text, APPROVAL_KEYWORDS) or _truthy(context.get("requires_approval")):
        return "approval_required", "security/approval/side-effect keyword"
    if _match_any(text, ACTION_REQUEST_KEYWORDS) or _truthy(context.get("action_request")):
        return "action_request", "maintenance/work-order action keyword"
    if _match_any(text, OFF_TOPIC_KEYWORDS) or _truthy(context.get("off_topic")):
        return "off_topic", "off-topic keyword"
    return "query", "default query"


def classify_agent_route(request: AgentRequest, request_type: RequestType | None = None) -> tuple[AgentRoute, str]:
    """Stage 2 of the uy-style router: domain agent metadata.

    Action and approval requests are forced to ``cms`` like the uy/workspace
    orchestrator, but public ChatRoute values are still computed separately.
    """

    context = request.context or {}
    explicit = _context_agent_route(context)
    if explicit is not None:
        return explicit, "context.agent_route"

    rt = request_type or classify_request_type(request)[0]
    if rt in {"action_request", "approval_required"}:
        return "cms", "request_type forces cms"
    if rt == "off_topic":
        return "rag", "off-topic rejection metadata"

    text = request.text.lower()
    # Future/forecast expressions intentionally win over anomaly/cms terms.
    if _match_any(text, FORECAST_ROUTE_KEYWORDS):
        return "forecast", "forecast keyword"
    if _match_any(text, CMS_ROUTE_KEYWORDS):
        return "cms", "cms/equipment keyword"
    if _match_any(text, ANOMALY_ROUTE_KEYWORDS):
        return "anomaly", "anomaly keyword"
    if _match_any(text, REPORT_ROUTE_KEYWORDS):
        return "report", "report/kpi/cost keyword"
    if _has_meter_marker(text, context):
        return "rag", "meter lookup defaults to rag metadata"
    return "rag", "default rag metadata"


def _decision(
    *,
    route: ChatRoute,
    reason: str,
    request_type: RequestType,
    agent_route: AgentRoute,
    request_type_method: str,
    agent_route_method: str,
    needs_approval: bool = False,
) -> RouteDecision:
    return RouteDecision(
        route=route,
        reason=reason,
        needs_approval=needs_approval,
        request_type=request_type,
        agent_route=agent_route,
        request_type_method=request_type_method,
        agent_route_method=agent_route_method,
    )


def classify_route(request: AgentRequest) -> RouteDecision:
    """Classify a request into public ChatRoute plus two-stage router metadata.

    Stage 1 computes ``request_type`` (query/action_request/approval_required/off_topic).
    Stage 2 computes ``agent_route`` (anomaly/cms/forecast/report/rag). The returned
    ``route`` remains one of the five public ChatRoute values.
    """

    request_type, request_type_method = classify_request_type(request)
    agent_route, agent_route_method = classify_agent_route(request, request_type)

    if request.route_hint is not None:
        return _decision(
            route=request.route_hint,
            reason="explicit route_hint",
            request_type=request_type,
            agent_route=agent_route,
            request_type_method=request_type_method,
            agent_route_method=agent_route_method,
            needs_approval=request.route_hint == "approval_required",
        )

    text = request.text.lower()
    context = request.context or {}

    if request_type == "approval_required":
        return _decision(
            route="approval_required",
            reason=request_type_method,
            request_type=request_type,
            agent_route=agent_route,
            request_type_method=request_type_method,
            agent_route_method=agent_route_method,
            needs_approval=True,
        )
    if _truthy(context.get("qa_blocked")):
        return _decision(
            route="report_shell",
            reason="context.qa_blocked=True",
            request_type=request_type,
            agent_route=agent_route,
            request_type_method=request_type_method,
            agent_route_method=agent_route_method,
        )
    if request_type == "off_topic":
        return _decision(
            route="quick_answer",
            reason="off-topic request rejected by lightweight router",
            request_type=request_type,
            agent_route=agent_route,
            request_type_method=request_type_method,
            agent_route_method=agent_route_method,
        )
    if request_type == "action_request":
        return _decision(
            route="needs_job",
            reason=request_type_method,
            request_type=request_type,
            agent_route=agent_route,
            request_type_method=request_type_method,
            agent_route_method=agent_route_method,
        )

    has_job_keyword = _match_any(text, JOB_KEYWORDS)
    has_job_action = _match_any(text, JOB_ACTION_KEYWORDS)
    has_evidence_keyword = _has_evidence_cue(text, context) or agent_route in {"anomaly", "cms", "forecast"}
    has_evidence_intent = _match_any(text, EVIDENCE_INTENT_KEYWORDS) or agent_route in {"anomaly", "cms", "forecast"}

    if has_evidence_keyword and (has_evidence_intent or not has_job_action):
        return _decision(
            route="evidence_answer",
            reason="meter/measurement/domain evidence keyword",
            request_type=request_type,
            agent_route=agent_route,
            request_type_method=request_type_method,
            agent_route_method=agent_route_method,
        )
    if has_job_keyword or _truthy(context.get("needs_job")):
        return _decision(
            route="needs_job",
            reason="async/long-running keyword (report/aggregate/replay/backfill)",
            request_type=request_type,
            agent_route=agent_route,
            request_type_method=request_type_method,
            agent_route_method=agent_route_method,
        )
    return _decision(
        route="quick_answer",
        reason="default (no specific route indicator)",
        request_type=request_type,
        agent_route=agent_route,
        request_type_method=request_type_method,
        agent_route_method=agent_route_method,
    )


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
    request_type: RequestType = "query"
    agent_route: AgentRoute = "rag"
    qa_status: QaPacketStatus | None = None
    evidence_packet: EvidencePacket | None = None
    report_shell: bool = False
    needs_human: bool = False
    job_ref: str | None = None
    next_action: str | None = None
    limitations: tuple[str, ...] = ()
    side_effects_executed: bool = False


__all__ = [
    "ACTION_REQUEST_KEYWORDS",
    "AGENT_ROUTES",
    "ANOMALY_ROUTE_KEYWORDS",
    "APPROVAL_KEYWORDS",
    "CMS_ROUTE_KEYWORDS",
    "EVIDENCE_CONTEXT_KEYS",
    "EVIDENCE_KEYWORDS",
    "JOB_KEYWORDS",
    "FORECAST_ROUTE_KEYWORDS",
    "AgentResponse",
    "Confidence",
    "EvidencePacket",
    "MetricEvidence",
    "OFF_TOPIC_KEYWORDS",
    "OutputStatus",
    "QaPacketStatus",
    "QaSummary",
    "QaWarning",
    "REPORT_ROUTE_KEYWORDS",
    "REQUEST_TYPES",
    "classify_agent_route",
    "classify_request_type",
    "classify_route",
]
