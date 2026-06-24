"""
Orchestrator Agent — LangGraph StateGraph.
사용자 질문의 의도를 분류하고 하위 에이전트로 라우팅한다.

2-Stage Flow (요청유형 → 라우팅축):

  사용자 질문
      ↓
  classify_request_type  (query | action_request | approval_required | off_topic)
      ↓
  ┌──────────── query ────────────┐
  │          classify_route       │ (anomaly | cms | forecast | report | domain)
  └───────────────┬──────────────┘
                  
             action/approval
            /approval_required
                 ┌─────
                 ↓
              cms (요청/승인 계열은 cms에서 처리)

      ↓
  critic  (품질 이슈 있을 때만 검토)
      ↓
  최종 답변
"""

import os
import re
import sys
import threading
import time
from typing import Any, cast
from dotenv import load_dotenv
from langgraph.graph import StateGraph, END

load_dotenv()

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from state import AgentState
from llm_client import chat as llm_chat
import rag_agent
import anomaly_agent
import reporting_agent
import forecast_agent
import cms_agent


def _trace_start(state: AgentState, name: str) -> tuple[AgentState, float]:
    """Return a shallow-copied state and a perf counter for latency tracing."""
    traced: dict[str, Any] = dict(state)
    traced.setdefault("timing_trace", {})
    return cast(AgentState, traced), time.perf_counter()


def _trace_end(state: AgentState, name: str, t0: float, **extra) -> AgentState:
    """Attach node latency in milliseconds to state['timing_trace']."""
    traced: dict[str, Any] = dict(state)
    trace: dict[str, Any] = dict(traced.get("timing_trace") or {})
    trace[name] = {
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        **{k: v for k, v in extra.items() if v is not None},
    }
    traced["timing_trace"] = trace
    return cast(AgentState, traced)


# ── 노드 1: 의도 분류 (2-stage 룰 + LLM 폴백) ───────────────────

# q.lower()로 매칭하므로 이상 유형명도 소문자로 작성
_KW_ANOMALY = re.compile(
    r"이상\s*탐지|이상\s*발생|이상\s*이력|이상\s*건수|이상\s*원인"
    r"|이상\s*분석|이상\s*추이|이상\s*현황|이상\s*분포|이상\s*비교"
    r"|비정상|스파이크|급등|급락|급증|오류|탐지|경보|알람|fault|anomal"
    r"|chpoutage|powerspike|copdrop|nightconsumption|pvnightnonzero"
    r"|냉매\s*누설|진동\s*과다|cop\s*저하|cop\s*저하|cop\s*drop"
    r"|전압\s*불균형|유량\s*감소|전력\s*급증|소음\s*증가"
    r"|사건|빈도|심각도|발생\s*건수|몇\s*건|잔차|급등\s*이벤트|게이트웨이\s*장애"
)
_KW_REPORT = re.compile(
    r"보고서|리포트|report|kpi|월간|통계|실적|집계|월별\s*현황"
    r"|요금|비용|cost|전력\s*비용|얼마나\s*나"
    r"|의존도|자급률|출력\s*얼마|사용량\s*어때|사용량\s*얼마"
    r"|그리드\s*의존|외부\s*전력\s*의존|계통\s*의존"
    r"|보고서.*요약|요약.*보고서|월간.*요약|요약.*월간|실적.*요약|요약.*실적"
)
_KW_FORECAST = re.compile(
    r"예측|전망|앞으로|내일|다음\s*주|장기|예상|forecast|미래|될\s*것"
    r"|계속\s*될까|계속될까|낮아질까|높아질까|늘어날까|줄어들까"
    r"|떨어질까|올라갈까|늘\s*까|줄\s*까|추세|앞으로.*될"
)
_KW_CMS = re.compile(
    r"작업\s*지시|작업\s*요청|정비|수리|예지보전|상태\s*감시|헬스|진단"
    r"|설비\s*상태|설비\s*이상|설비\s*점검|설비\s*확인|설비\s*문제"
    r"|설비.*요약|요약.*설비|설비\s*상태\s*요약|설비\s*요약"
    r"|냉방\s*설비|계통.*설비|수전.*설비"
    r"|열병합|시뮬|시뮬레이터|시연"
    r"|설비.*이상\s*건수|설비.*최근\s*이상|태양광.*이상\s*건수|태양광.*최근\s*이상"
)

_KW_DOMAIN = re.compile(
    r"무엇|뭐야|의미|뜻|설명|정의|측정|주요\s*측정값|계량기|meter|measurement"
    r"|cop|역률|power\s*factor|전압|전류|유효\s*전력|무효\s*전력|단위"
)

# 에너지·설비 관리와 명백히 무관한 주제 → 즉시 off_topic 거절
_KW_OFFTOPIC = re.compile(
    r"주식|코인|비트코인|가상\s*화폐|암호\s*화폐"
    r"|요리|레시피|음식|맛집|식당|배달|점심|저녁|아침|점심\s*메뉴|저녁\s*메뉴|아침\s*메뉴|구내식당"
    r"|연예인|드라마|영화|음악|스포츠|야구|축구|농구|골프|올림픽"
    r"|정치|선거|국회|대통령|국정|법안"
    r"|오늘\s*날씨|내일\s*날씨|날씨\s*예보|기상\s*예보"
    r"|의료|병원|질병|증상|처방전|감기약|약\s*추천"
    r"|연애|결혼|이혼|육아|임신"
    r"|게임|유튜브|틱톡|sns|인스타|트위터|여행지|여행\s*추천|관광지"
    r"|운영\s*테이블.*변경|테이블.*변경|테이블.*삭제|테이블.*drop|테이블.*truncate"
    r"|서버\s*파일.*덮어쓰기|파일.*덮어쓰기|원격.*서버.*파일"
    r"|db.*insert|db.*update|db.*delete|db.*drop"
)

# 미래 시제 강제 override — anomaly/cms 키워드와 동시에 있어도 forecast로
_KW_FUTURE = re.compile(
    r"계속\s*될까|계속될까|낮아질까|높아질까|늘어날까|줄어들까"
    r"|떨어질까|올라갈까|계속\s*낮아|계속\s*떨어|계속\s*높아"
    r"|앞으로.*될|~할\s*것|추세.*앞|앞.*추세"
)

# 설비명 + 이상건수 → cms 강제 (compute_equipment_status가 더 정확한 건수 제공)
_KW_EQ_ANOMALY_COUNT = re.compile(r"(태양광|계통|수전|냉방|chp|열병합).*(이상.*건수|최근.*이상|이상.*몇\s*건)")

# Stage 1: 요청 유형
_KW_APPROVAL_REQUIRED = re.compile(
    r"승인\s*요청|결재\s*요청|결재\s*올려|결재\s*해줘|승인해줘|승인\s*바래|허가\s*받아|허가\s*요청|safety\s*approval|force\s*처리|강제\s*진행"
)
_KW_ACTION_REQUEST = re.compile(
    r"작업\s*요청|작업\s*지시|작업\s*지시서|정비\s*요청|수리\s*요청|점검\s*요청|점검\s*해\s*줘|작동\s*중단|조치\s*요청|예지보전|교체\s*요청|repair|work\s*order"
)

# 복합 의도: 분석/조회 + 보고서/예측/작업/승인 등 둘 이상의 작업을 한 번에 요구
_KW_MULTI_INTENT = re.compile(
    r"(분석|확인|조회|점검|요약|보고서|리포트|예측|전망|작업|티켓|등록|배정|승인|처리|배포)"
    r".*(하고|해서|한 뒤|다음|그리고|및|와|과|까지|동시에)"
    r".*(분석|확인|조회|점검|요약|보고서|리포트|예측|전망|작업|티켓|등록|배정|승인|처리|배포)"
)

REQUEST_TYPE_PROMPT = """사용자 질문을 읽고 아래 중 하나로만 답하세요. 다른 말은 하지 마세요.

- query          : 분석 조회/설명/설비 상태/이상·설비 상태/보고서 조회/예측 설명 같은 질의
- action_request : 점검, 조치, 실행, 수리, 작업 지시, 작업 요청에 해당하는 요청형 질의
- approval_required : 승인/결재/허가/통제 동의가 요구되는 요청형 질의
- off_topic      : 에너지/설비/이상탐지와 무관한 질문(주식·요리·날씨·연예·스포츠·정치·의료 등)
- multi_intent   : 분석+보고서+작업 등록처럼 둘 이상의 독립 작업을 한 요청에 동시에 요구

질문: {question}"""

ROUTE_PROMPT = """사용자 질문을 읽고 아래 중 하나로만 답하세요. 다른 말은 하지 마세요.

- anomaly   : 이상탐지 결과·건수·원인 분석. 이상 유형명(CHPOutage/PowerSpike/COPDrop/NightConsumption/PVNightNonZero)이 나오면 무조건 anomaly.
- cms       : 설비 상태 점검, 작업지시, 정비, 예지보전. "CHP 괜찮아?", "설비 점검해야 돼?".
- report    : 실적·현황 조회. 보고서, KPI, 월간, 요약, 통계, 자급률, 의존도, 사용량, 비용.
- forecast  : 미래 예측·전망. "앞으로", "내일", "~될까?", "~낮아질까?", "추세".
- domain    : 개념 설명, 계량기 값 의미, 실시간 센서값 조회, 그 외 에너지·설비 관련 질문.

핵심 구분:
- "이상 있어?" / "이상 발생했어?" → anomaly
- "COP 떨어질까?" / "이상 계속될까?" → forecast
- "설비 상태 어때?" / "태양광 이상 확인해봐" → cms
- "자급률 왜 떨어졌어?" / "이번 달 비용?" → report
- "오늘 주식 뭐 살까?" / "점심 뭐 먹을까?" / "날씨 예보 알려줘" → off_topic

질문: {question}"""


# 특정 계량기 URN 패턴 (V.Z84, H1.Z16 등) → domain으로 직행
_METER_URN_PAT = re.compile(r"[A-Z]\d?\.(?:[A-Z]\.)?Z\d+", re.IGNORECASE)


# ── Stage 1: 요청유형 분류 ────────────────────────────────────

def _rule_classify_request_type(question: str) -> str | None:
    """요청 유형을 키워드 룰로 분류한다. 명확한 오프토픽만 빠르게 걸러낸다."""
    if not question:
        return "query"

    q = question.lower()

    # 에너지·설비와 무관한 주제는 즉시 off_topic
    if _KW_OFFTOPIC.search(q):
        return "off_topic"

    if _KW_MULTI_INTENT.search(q):
        return "multi_intent"

    # 특정 계량기 URN은 기본적으로 조회성 query로 처리
    if _METER_URN_PAT.search(q):
        return "query"

    if _KW_APPROVAL_REQUIRED.search(q):
        return "approval_required"

    if _KW_ACTION_REQUEST.search(q):
        return "action_request"

    # 설비명 + 이상건수 질의는 query로 라우팅(쿼리 분기에서 cms로 수렴)
    if _KW_EQ_ANOMALY_COUNT.search(q):
        return "query"

    return "query"


def classify_request_type(state: AgentState) -> AgentState:
    state, t0 = _trace_start(state, "stage1_request_type")
    question = state["question"]

    request_type = _rule_classify_request_type(question)
    if request_type:
        print(f"[Orchestrator] 요청유형 분류 (룰): '{question}' → {request_type}")
        return _trace_end({
            **state,
            "request_type": request_type,
            "request_type_method": "rule",
        }, "stage1_request_type", t0, method="rule", label=request_type)

    # 룰 분류가 어려운 경우 LLM 폴백 (문맥 기반 분기)
    raw = llm_chat(
        [{"role": "user", "content": REQUEST_TYPE_PROMPT.format(question=question)}],
        max_tokens=10,
        fast=True,
    ).strip().lower()
    valid = ("query", "action_request", "approval_required", "off_topic", "multi_intent")
    request_type = raw if raw in valid else "query"
    print(f"[Orchestrator] 요청유형 분류 (LLM): '{question}' → {request_type}")
    return _trace_end({
        **state,
        "request_type": request_type,
        "request_type_method": "llm",
    }, "stage1_request_type", t0, method="llm", label=request_type)


def request_type_router(state: AgentState) -> str:
    rt = state.get("request_type")
    if rt in ("off_topic", "action_request", "approval_required", "multi_intent", "query"):
        return rt
    return "query"


# ── Stage 2: 라우팅 축 분류 ──────────────────────────────────

def _rule_classify_route(question: str) -> str | None:
    """Stage2: routing 축(5-route) 분류. 명확하면 반환, 애매하면 None."""
    q = question.lower()

    # 미래 시제 표현이 있으면 다른 키워드보다 forecast 우선
    if _KW_FUTURE.search(q):
        return "forecast"

    # 설비명 + 이상건수 조합 → cms 강제
    if _KW_EQ_ANOMALY_COUNT.search(q):
        return "cms"

    scores = {
        "anomaly": len(_KW_ANOMALY.findall(q)),
        "report": len(_KW_REPORT.findall(q)),
        "forecast": len(_KW_FORECAST.findall(q)),
        "cms": len(_KW_CMS.findall(q)),
        "domain": len(_KW_DOMAIN.findall(q)),
    }
    best, count = max(scores.items(), key=lambda x: x[1])
    if count >= 1:
        # 두 카테고리가 동점이면 LLM에 위임
        second = sorted(scores.values(), reverse=True)[1]
        if count > second:
            return best
    return None


def classify_route(state: AgentState) -> AgentState:
    state, t0 = _trace_start(state, "stage2_route")
    question = state["question"]

    route = _rule_classify_route(question)
    if route:
        print(f"[Orchestrator] 라우팅 축 분류 (룰): '{question}' → {route}")
        return _trace_end({
            **state,
            "route": route,
            "intent": route,
            "route_method": "rule",
        }, "stage2_route", t0, method="rule", label=route)

    # 룰로 판단 불가 → LLM 폴백
    raw = llm_chat(
        [{"role": "user", "content": ROUTE_PROMPT.format(question=question)}],
        max_tokens=10,
        fast=True,
    ).strip().lower()
    valid = ("anomaly", "report", "domain", "forecast", "cms")
    route = raw if raw in valid else "domain"
    print(f"[Orchestrator] 라우팅 축 분류 (LLM): '{question}' → {route}")
    return _trace_end({
        **state,
        "route": route,
        "intent": route,
        "route_method": "llm",
    }, "stage2_route", t0, method="llm", label=route)


def resolve_request_to_route(state: AgentState) -> AgentState:
    """요청유형을 최종 intent로 정규화한다."""
    state, t0 = _trace_start(state, "resolve_request_to_route")
    request_type = state.get("request_type", "query")
    route = state.get("route", "domain")

    if request_type == "action_request":
        print("[Orchestrator] 요청유형 액션/작업: cms로 강제 라우팅")
        return _trace_end({**state, "intent": "cms", "route": "cms"}, "resolve_request_to_route", t0, request_type=request_type, route="cms")

    if request_type == "approval_required":
        print("[Orchestrator] 요청유형 승인/결재: cms로 강제 라우팅")
        return _trace_end({**state, "intent": "cms", "route": "cms"}, "resolve_request_to_route", t0, request_type=request_type, route="cms")

    if request_type == "off_topic":
        print("[Orchestrator] 요청유형 off_topic: rejection 경로")
        return _trace_end({**state, "intent": "off_topic", "route": "domain"}, "resolve_request_to_route", t0, request_type=request_type, route="domain")

    if route in ("anomaly", "report", "forecast", "cms", "domain"):
        return _trace_end({**state, "intent": route}, "resolve_request_to_route", t0, request_type=request_type, route=route)

    return _trace_end({**state, "intent": "domain"}, "resolve_request_to_route", t0, request_type=request_type, route="domain")


def route(state: AgentState) -> str:
    return state.get("request_type", "query")


def route_to_agent(state: AgentState) -> str:
    return state.get("route", "domain")


# ── 거절 응답 (off_topic) — LLM 호출 없이 템플릿으로 반환 ──────────

_REJECTION_MSG = (
    "저는 Honda R&D 에너지·설비 관리 전문 AI 코파일럿입니다.\n"
    "에너지 데이터 분석, 설비 상태 모니터링, 이상탐지, 예지보전 등 "
    "설비 운영 관련 질문을 도와드릴 수 있습니다."
)


def rejection_node(state: AgentState) -> AgentState:
    state, t0 = _trace_start(state, "gate_rejection")
    print("[Orchestrator] off_topic → 거절 응답")
    return _trace_end({**state, "final_answer": _REJECTION_MSG, "intent": "off_topic", "route": "domain"}, "gate_rejection", t0)


_MULTI_INTENT_MSG = (
    "요청에 분석, 보고서 작성, 작업 등록/승인처럼 여러 작업이 함께 포함되어 있습니다.\n"
    "정확하고 안전하게 처리하려면 먼저 하나의 작업으로 나눠서 요청해 주세요. 예: 이상 분석만 먼저 요청하거나, 보고서 작성/작업 등록을 별도 요청으로 진행할 수 있습니다."
)

def multi_intent_node(state: AgentState) -> AgentState:
    state, t0 = _trace_start(state, "gate_multi_intent")
    print("[Orchestrator] multi_intent → clarification 응답")
    return _trace_end({**state, "final_answer": _MULTI_INTENT_MSG, "intent": "multi_intent", "route": "domain"}, "gate_multi_intent", t0)


# ── 노드 2~5: 하위 에이전트 래퍼 ────────────────────────────────

def rag_node(state: AgentState) -> AgentState:
    state, t0 = _trace_start(state, "agent_domain")
    print("[RAG Agent] 실행 중...")
    return _trace_end(rag_agent.langgraph_node(state), "agent_domain", t0)


def anomaly_node(state: AgentState) -> AgentState:
    state, t0 = _trace_start(state, "agent_anomaly")
    print("[Anomaly Agent] 실행 중...")
    return _trace_end(anomaly_agent.langgraph_node(state), "agent_anomaly", t0)


def report_node(state: AgentState) -> AgentState:
    state, t0 = _trace_start(state, "agent_report")
    print("[Reporting Agent] 실행 중...")
    return _trace_end(reporting_agent.langgraph_node(state), "agent_report", t0)


def forecast_node(state: AgentState) -> AgentState:
    state, t0 = _trace_start(state, "agent_forecast")
    print("[Forecast Agent] 실행 중...")
    return _trace_end(forecast_agent.langgraph_node(state), "agent_forecast", t0)


def cms_node(state: AgentState) -> AgentState:
    state, t0 = _trace_start(state, "agent_cms")
    print("[CMS Agent] 실행 중...")
    return _trace_end(cms_agent.langgraph_node(state), "agent_cms", t0)


# ── 노드 6: Critic (LLM 제거 — 문자열 치환으로 대체) ─────────────

_BAD_TERMS = re.compile(r"한전|수전량|수전\s*전력|kWh당|㎾h|전기요금|전력요금")

_REPLACEMENTS = [
    (re.compile(r"한전"), "독일 공공 전력망"),
    (re.compile(r"수전량"), "계통 인입 전력량"),
    (re.compile(r"수전\s*전력"), "계통 전력"),
    (re.compile(r"전기요금"), "전력 비용"),
    (re.compile(r"전력요금"), "전력 비용"),
    (re.compile(r"㎾h"), "kWh"),
    (re.compile(r"kWh당"), "kWh 단가"),
]


def _fix_terms(text: str) -> str:
    for pattern, replacement in _REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text


def critic_node(state: AgentState) -> AgentState:
    state, t0 = _trace_start(state, "critic")
    anomaly_exp = (state.get("anomaly_result") or {}).get("explanation", "")
    answer = anomaly_exp or state.get("rag_answer") or state.get("report_result") or ""
    if not answer:
        return _trace_end({**state, "final_answer": "답변을 생성할 수 없습니다."}, "critic", t0)

    if _BAD_TERMS.search(answer):
        answer = _fix_terms(answer)
        print("[Critic] 용어 교정 완료")
    else:
        print("[Critic] 통과")

    return _trace_end({**state, "final_answer": answer}, "critic", t0)


# ── 그래프 조립 ──────────────────────────────────────────────────

_graph = None
_graph_lock = threading.Lock()


def build_graph():
    global _graph
    if _graph is not None:
        return _graph
    with _graph_lock:
        if _graph is not None:
            return _graph

        g = StateGraph(AgentState)

        g.add_node("classify_request_type", classify_request_type)
        g.add_node("classify_route", classify_route)
        g.add_node("resolve_request_to_route", resolve_request_to_route)
        g.add_node("domain", rag_node)
        g.add_node("anomaly", anomaly_node)
        g.add_node("report", report_node)
        g.add_node("forecast", forecast_node)
        g.add_node("cms", cms_node)
        g.add_node("critic", critic_node)
        g.add_node("rejection", rejection_node)
        g.add_node("multi_intent", multi_intent_node)

        g.set_entry_point("classify_request_type")

        g.add_conditional_edges(
            "classify_request_type",
            request_type_router,
            {
                "off_topic": "rejection",
                "multi_intent": "multi_intent",
                "action_request": "resolve_request_to_route",
                "approval_required": "resolve_request_to_route",
                "query": "classify_route",
            },
        )

        g.add_conditional_edges(
            "classify_route",
            route_to_agent,
            {
                "anomaly": "anomaly",
                "cms": "cms",
                "report": "report",
                "forecast": "forecast",
                "domain": "domain",
            },
        )

        g.add_edge("resolve_request_to_route", "critic")

        g.add_edge("domain", "critic")
        g.add_edge("anomaly", "critic")
        g.add_edge("report", "critic")
        g.add_edge("forecast", "critic")
        g.add_edge("cms", "critic")
        g.add_edge("critic", END)
        g.add_edge("rejection", END)
        g.add_edge("multi_intent", END)

        _graph = g.compile()
    return _graph


def classify_intent(state: AgentState) -> AgentState:
    """단독 실행 테스트 호환용 래퍼: 기존 인터페이스(최종 intent 반환)."""
    state = classify_request_type(state)
    if state.get("request_type") == "off_topic":
        return {**state, "intent": "off_topic"}

    if state.get("request_type") == "multi_intent":
        return {**state, "intent": "multi_intent"}

    if state.get("request_type") in {"action_request", "approval_required"}:
        return {**state, "intent": "cms"}

    state = classify_route(state)
    return resolve_request_to_route(state)


def run(question: str) -> str:
    graph = build_graph()
    initial: AgentState = {
        "question": question,
        "intent": "",
        "rag_answer": "",
        "rag_sources": [],
        "anomaly_result": {},
        "report_result": "",
        "forecast_result": {},
        "critic_feedback": "",
        "final_answer": "",
        "pdf_path": "",
        "messages": [],
        "context": {},
        "request_type": "query",
        "route": "domain",
        "request_type_method": "",
        "route_method": "",
    }
    result = graph.invoke(initial)
    return result["final_answer"]


if __name__ == "__main__":
    tests = [
        "내일 전력 소비 예측해줘",
        "COP가 갑자기 떨어졌는데 왜 그런 건가요?",
        "이번 달 이상탐지 결과 요약해줘",
        "자급률이 낮아진 원인이 뭔가요?",
        "설비 정비 작업 지시 내려줘",
        "승인 요청: 냉각 펌프 점검 시작해도 될까요?",
    ]
    for q in tests:
        print(f"\n{'='*60}\n질문: {q}\n{'='*60}")
        print(run(q))
