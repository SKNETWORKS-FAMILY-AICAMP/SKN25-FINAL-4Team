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


# ── 노드 1: 의도 분류 ────────────────────────────────────────────

INTENT_PROMPT = """사용자 질문을 읽고 아래 중 하나로만 답하세요. 다른 말은 하지 마세요.

- anomaly  : 이상탐지, 이상, 비정상, 스파이크, 급등, 급락, 오류, 센서 관련
- report   : 보고서, 리포트, KPI, 월간, 요약, 통계, 실적 관련
- forecast : 예측, 전망, 앞으로, 내일, 다음주, 장기, ~할 것 같아, 예상
- rag      : 개념 설명, 원인 분석, 방법, 권장사항, 그 외 모든 질문

질문: {question}"""


def classify_intent(state: AgentState) -> AgentState:
    raw = llm_chat(
        [{"role": "user", "content": INTENT_PROMPT.format(question=state["question"])}],
        max_tokens=10,
    ).strip().lower()
    intent = raw if raw in ("anomaly", "report", "rag", "forecast") else "rag"
    print(f"[Orchestrator] 의도 분류: '{state['question']}' → {intent}")
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


# ── 노드 6: Critic Agent (품질 이슈가 있을 때만 LLM 호출) ────────

# 검토 없이 통과시킬 기준: 이 키워드가 없으면 도메인 오류 가능성 낮음
_BAD_TERMS = re.compile(r"한전|수전량|수전\s*전력|kWh당|㎾h|전기요금|전력요금")
_DOMAIN_KW = re.compile(r"kW|kWh|°C|COP|자급률|계통|이상탐지|예측|보고서")

CRITIC_PROMPT = """당신은 에너지 분석 품질 검토자입니다.
시설: Honda R&D Europe GmbH, 독일 오펜바흐. 전력망: 독일 공공 전력망.
아래 답변을 검토하고 문제가 있으면 수정된 최종 답변을 제시하세요.
문제가 없으면 원본 답변을 그대로 반환하세요.

검토 기준:
- 한전·수전량·수전 전력 등 한국 전력 용어 → "계통 전력"으로 교체
- 단위(W, kWh, °C) 누락
- COP 계산 시 0나누기 미언급 (COP 관련 질문일 때)
- PV 야간 NaN을 결측으로 오해
- 수치가 맥락에 맞지 않음 (자급률 6년평균 39.6%·2022년 46.9%, COP 중앙값 2.06)
- 과도한 추측

원본 질문: {question}
원본 답변: {answer}

최종 답변:"""


def critic_node(state: AgentState) -> AgentState:
    anomaly_exp = (state.get("anomaly_result") or {}).get("explanation", "")
    answer = anomaly_exp or state.get("rag_answer") or state.get("report_result") or ""
    if not answer:
        return {**state, "final_answer": "답변을 생성할 수 없습니다."}

    # 빠른 통과: 한국 전력 용어가 없고 도메인 키워드도 적으면 그대로 반환
    has_bad   = bool(_BAD_TERMS.search(answer))
    has_domain = bool(_DOMAIN_KW.search(answer))
    if not has_bad and not has_domain:
        print("[Critic] 통과 (검토 불필요)")
        return {**state, "final_answer": answer}

    # 한국 용어가 없고 짧은 답변이면 그대로 반환
    if not has_bad and len(answer) < 300:
        print("[Critic] 통과 (짧은 도메인 답변)")
        return {**state, "final_answer": answer}

    print("[Critic Agent] 검토 중...")
    final = llm_chat(
        [{"role": "user", "content": CRITIC_PROMPT.format(
            question=state["question"],
            answer=answer,
        )}],
        max_tokens=1500,
    ).strip()
    return {**state, "final_answer": final, "critic_feedback": final}


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
    g.add_node("critic",    critic_node)

    g.set_entry_point("classify")

    g.add_conditional_edges("classify", route, {
        "rag":      "rag",
        "anomaly":  "anomaly",
        "report":   "report",
        "forecast": "forecast",
    })

    g.add_edge("rag",      "critic")
    g.add_edge("anomaly",  "critic")
    g.add_edge("report",   "critic")
    g.add_edge("forecast", "critic")
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
        "ontology_context": [],
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
