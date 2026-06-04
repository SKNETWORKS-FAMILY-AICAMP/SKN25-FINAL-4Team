"""
Orchestrator Agent — LangGraph StateGraph.
사용자 질문의 의도를 분류하고 하위 에이전트로 라우팅한다.

흐름:
  사용자 질문
      ↓
  classify_intent  (의도 분류)
      ↓
  ┌─────────────────────────────┐
  │ rag / anomaly / report /    │
  │ forecast                    │
  └─────────────────────────────┘
      ↓
  critic  (품질 이슈 있을 때만 검토)
      ↓
  최종 답변
"""

import os
import re
import sys
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


# ── 노드 1: 의도 분류 (키워드 룰 → LLM 폴백) ───────────────────

# q.lower()로 매칭하므로 이상 유형명도 소문자로 작성
_KW_ANOMALY  = re.compile(r"이상|비정상|스파이크|급등|급락|오류|센서|탐지|경보|알람|fault|anomal"
                          r"|chpoutage|powerspike|copdrop|nightconsumption|pvnightnonzero"
                          r"|사건|빈도|심각도|발생\s*건수|몇\s*건|이상\s*탐지|이상\s*발생|이상\s*이력"
                          r"|잔차|급등\s*이벤트|게이트웨이\s*장애")
_KW_REPORT   = re.compile(r"보고서|리포트|report|kpi|월간|요약|통계|실적|집계|월별\s*현황|요금|비용|cost|전력\s*비용|얼마나\s*나")
_KW_FORECAST = re.compile(r"예측|전망|앞으로|내일|다음\s*주|장기|예상|forecast|미래|될\s*것")
_KW_CMS      = re.compile(r"설비|헬스|진단|작업\s*지시|정비|수리|예지보전|상태\s*감시|계통|열병합|냉방|태양광|시뮬|시뮬레이터|시연")

INTENT_PROMPT = """사용자 질문을 읽고 아래 중 하나로만 답하세요. 다른 말은 하지 마세요.

- cms      : 설비 상태, 설비 헬스, 설비 진단, 작업지시, 정비, 예지보전, 특정 설비(계통/열병합/냉방/태양광) 상태
- anomaly  : 이상탐지 결과·건수·원인 분석. 이상 유형명(CHPOutage/PowerSpike/COPDrop/NightConsumption/PVNightNonZero)이 나오면 무조건 anomaly.
- report   : 보고서, 리포트, KPI, 월간, 요약, 통계, 실적, 요금, 비용, 전력 비용 관련
- forecast : 예측, 전망, 앞으로, 내일, 다음주, 장기, ~할 것 같아, 예상
- rag      : 개념 설명, 방법, 특정 계량기(V.Z84 등) 값 의미, 그 외 모든 질문

규칙: 특정 이상 유형명이 보이면 anomaly. "건수/발생/빈도/원인"이 이상과 함께 나오면 anomaly.

질문: {question}"""


# 특정 계량기 URN 패턴 (V.Z84, H1.Z16 등) → rag로 직행
_METER_URN_PAT = re.compile(r"[A-Z]\d?\.(?:[A-Z]\.)?Z\d+", re.IGNORECASE)


def _rule_classify(question: str) -> str | None:
    """키워드 룰로 명확히 분류 가능하면 반환, 애매하면 None."""
    # 특정 계량기 URN이 보이면 무조건 rag (미터 실측값 조회)
    if _METER_URN_PAT.search(question):
        return "rag"

    q = question.lower()
    scores = {
        "anomaly":  len(_KW_ANOMALY.findall(q)),
        "report":   len(_KW_REPORT.findall(q)),
        "forecast": len(_KW_FORECAST.findall(q)),
        "cms":      len(_KW_CMS.findall(q)),
    }
    best, count = max(scores.items(), key=lambda x: x[1])
    if count >= 1:
        # 두 카테고리가 동점이면 LLM에 위임
        second = sorted(scores.values(), reverse=True)[1]
        if count > second:
            return best
    return None


def classify_intent(state: AgentState) -> AgentState:
    question = state["question"]

    intent = _rule_classify(question)
    if intent:
        print(f"[Orchestrator] 의도 분류 (룰): '{question}' → {intent}")
        return {**state, "intent": intent}

    # 룰로 판단 불가 → LLM 폴백
    raw = llm_chat(
        [{"role": "user", "content": INTENT_PROMPT.format(question=question)}],
        max_tokens=10,
    ).strip().lower()
    intent = raw if raw in ("anomaly", "report", "rag", "forecast", "cms") else "rag"
    print(f"[Orchestrator] 의도 분류 (LLM): '{question}' → {intent}")
    return {**state, "intent": intent}


def route(state: AgentState) -> str:
    return state.get("intent", "rag")


# ── 노드 2~5: 하위 에이전트 래퍼 ────────────────────────────────

def rag_node(state: AgentState) -> AgentState:
    print("[RAG Agent] 실행 중...")
    return rag_agent.langgraph_node(state)


def anomaly_node(state: AgentState) -> AgentState:
    print("[Anomaly Agent] 실행 중...")
    return anomaly_agent.langgraph_node(state)


def report_node(state: AgentState) -> AgentState:
    print("[Reporting Agent] 실행 중...")
    return reporting_agent.langgraph_node(state)


def forecast_node(state: AgentState) -> AgentState:
    print("[Forecast Agent] 실행 중...")
    return forecast_agent.langgraph_node(state)


def cms_node(state: AgentState) -> AgentState:
    print("[CMS Agent] 실행 중...")
    return cms_agent.langgraph_node(state)


# ── 노드 6: Critic (LLM 제거 — 문자열 치환으로 대체) ─────────────

_BAD_TERMS = re.compile(r"한전|수전량|수전\s*전력|kWh당|㎾h|전기요금|전력요금")

_REPLACEMENTS = [
    (re.compile(r"한전"),          "독일 공공 전력망"),
    (re.compile(r"수전량"),        "계통 인입 전력량"),
    (re.compile(r"수전\s*전력"),   "계통 전력"),
    (re.compile(r"전기요금"),      "전력 비용"),
    (re.compile(r"전력요금"),      "전력 비용"),
    (re.compile(r"㎾h"),           "kWh"),
    (re.compile(r"kWh당"),         "kWh 단가"),
]


def _fix_terms(text: str) -> str:
    for pattern, replacement in _REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text


def critic_node(state: AgentState) -> AgentState:
    anomaly_exp = (state.get("anomaly_result") or {}).get("explanation", "")
    answer = anomaly_exp or state.get("rag_answer") or state.get("report_result") or ""
    if not answer:
        return {**state, "final_answer": "답변을 생성할 수 없습니다."}

    if _BAD_TERMS.search(answer):
        answer = _fix_terms(answer)
        print("[Critic] 용어 교정 완료")
    else:
        print("[Critic] 통과")

    return {**state, "final_answer": answer}


# ── 그래프 조립 ──────────────────────────────────────────────────

_graph = None


def build_graph():
    global _graph
    if _graph is not None:
        return _graph

    g = StateGraph(AgentState)

    g.add_node("classify",  classify_intent)
    g.add_node("rag",       rag_node)
    g.add_node("anomaly",   anomaly_node)
    g.add_node("report",    report_node)
    g.add_node("forecast",  forecast_node)
    g.add_node("cms",       cms_node)
    g.add_node("critic",    critic_node)

    g.set_entry_point("classify")

    g.add_conditional_edges("classify", route, {
        "rag":      "rag",
        "anomaly":  "anomaly",
        "report":   "report",
        "forecast": "forecast",
        "cms":      "cms",
    })

    g.add_edge("rag",      "critic")
    g.add_edge("anomaly",  "critic")
    g.add_edge("report",   "critic")
    g.add_edge("forecast", "critic")
    g.add_edge("cms",      "critic")
    g.add_edge("critic",   END)

    _graph = g.compile()
    return _graph


def run(question: str) -> str:
    graph = build_graph()
    initial: AgentState = {
        "question":         question,
        "intent":           "",
        "rag_answer":       "",
        "rag_sources":      [],
        "anomaly_result":   {},
        "report_result":    "",
        "forecast_result":  {},
        "critic_feedback":  "",
        "final_answer":     "",
        "pdf_path":         "",
        "messages":         [],
    }
    result = graph.invoke(initial)
    return result["final_answer"]


if __name__ == "__main__":
    tests = [
        "내일 전력 소비 예측해줘",
        "COP가 갑자기 떨어졌는데 왜 그런 건가요?",
        "이번 달 이상탐지 결과 요약해줘",
        "자급률이 낮아진 원인이 뭔가요?",
    ]
    for q in tests:
        print(f"\n{'='*60}\n질문: {q}\n{'='*60}")
        print(run(q))
