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
import threading
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
_KW_ANOMALY  = re.compile(r"이상\s*탐지|이상\s*발생|이상\s*이력|이상\s*건수|이상\s*원인"
                          r"|이상\s*분석|이상\s*추이|이상\s*현황|이상\s*분포|이상\s*비교"
                          r"|비정상|스파이크|급등|급락|급증|오류|탐지|경보|알람|fault|anomal"
                          r"|chpoutage|powerspike|copdrop|nightconsumption|pvnightnonzero"
                          r"|냉매\s*누설|진동\s*과다|cop\s*저하|COP\s*저하|압력\s*변동"
                          r"|전압\s*불균형|유량\s*감소|전력\s*급증|소음\s*증가"
                          r"|사건|빈도|심각도|발생\s*건수|몇\s*건|잔차|급등\s*이벤트|게이트웨이\s*장애")
_KW_REPORT   = re.compile(r"보고서|리포트|report|kpi|월간|통계|실적|집계|월별\s*현황"
                          r"|요금|비용|cost|전력\s*비용|얼마나\s*나"
                          r"|의존도|자급률|출력\s*얼마|사용량\s*어때|사용량\s*얼마"
                          r"|그리드\s*의존|외부\s*전력\s*의존|계통\s*의존"
                          r"|보고서.*요약|요약.*보고서|월간.*요약|요약.*월간|실적.*요약|요약.*실적")
_KW_FORECAST = re.compile(r"예측|전망|앞으로|내일|다음\s*주|장기|예상|forecast|미래|될\s*것"
                          r"|계속\s*될까|계속될까|낮아질까|높아질까|늘어날까|줄어들까"
                          r"|떨어질까|올라갈까|늘\s*까|줄\s*까|추세|앞으로.*될")
_KW_CMS      = re.compile(r"작업\s*지시|정비|수리|예지보전|상태\s*감시|헬스|진단"
                          r"|설비\s*상태|설비\s*이상|설비\s*점검|설비\s*확인|설비\s*문제"
                          r"|설비.*요약|요약.*설비|설비\s*상태\s*요약|설비\s*요약"
                          r"|냉방\s*설비|계통.*설비|수전.*설비"
                          r"|열병합|시뮬|시뮬레이터|시연"
                          r"|설비.*이상\s*건수|설비.*최근\s*이상|태양광.*이상\s*건수|태양광.*최근\s*이상")

# 에너지·설비 관리와 명백히 무관한 주제 → 즉시 off_topic 거절
_KW_OFFTOPIC = re.compile(
    r"주식|코인|비트코인|가상\s*화폐|암호\s*화폐"
    r"|요리|레시피|음식|맛집|식당|배달|점심\s*메뉴|저녁\s*메뉴|아침\s*메뉴|구내식당"
    r"|연예인|드라마|영화|음악|스포츠|야구|축구|농구|골프|올림픽"
    r"|정치|선거|국회|대통령|국정|법안"
    r"|오늘\s*날씨|내일\s*날씨|날씨\s*예보|기상\s*예보"
    r"|의료|병원|질병|증상|처방전"
    r"|연애|결혼|이혼|육아|임신"
    r"|게임|유튜브|틱톡|sns|인스타|트위터"
    r"|운영\s*테이블.*변경|테이블.*변경|테이블.*삭제|테이블.*drop|테이블.*truncate"
    r"|서버\s*파일.*덮어쓰기|파일.*덮어쓰기|원격.*서버.*파일"
    r"|승인\s*요청|결재\s*올려|허가\s*받아|force\s*처리|강제.*진행"
    r"|db.*insert|db.*update|db.*delete|db.*drop"
)

# 미래 시제 강제 override — anomaly/cms 키워드와 동시에 있어도 forecast로
_KW_FUTURE   = re.compile(r"계속\s*될까|계속될까|낮아질까|높아질까|늘어날까|줄어들까"
                          r"|떨어질까|올라갈까|계속\s*낮아|계속\s*떨어|계속\s*높아"
                          r"|앞으로.*될|~할\s*것|추세.*앞|앞.*추세")

# 설비명 + 이상건수 → cms 강제 (compute_equipment_status가 더 정확한 건수 제공)
_KW_EQ_ANOMALY_COUNT = re.compile(
    r"(태양광|계통|수전|냉방|chp|열병합).*(이상.*건수|최근.*이상|이상.*몇\s*건)"
)

INTENT_PROMPT = """사용자 질문을 읽고 아래 중 하나로만 답하세요. 다른 말은 하지 마세요.

- anomaly   : 이상탐지 결과·건수·원인 분석. 이상 유형명(CHPOutage/PowerSpike/COPDrop/NightConsumption/PVNightNonZero)이 나오면 무조건 anomaly.
- cms       : 설비 상태 점검, 작업지시, 정비, 예지보전. "CHP 괜찮아?", "설비 점검해야 돼?".
- report    : 실적·현황 조회. 보고서, KPI, 월간, 요약, 통계, 자급률, 의존도, 사용량, 비용.
- forecast  : 미래 예측·전망. "앞으로", "내일", "~될까?", "~낮아질까?", "추세".
- rag       : 개념 설명, 계량기 값 의미, 실시간 센서값 조회, 그 외 에너지·설비 관련 질문.
- off_topic : 에너지 관리, 설비 모니터링, Honda 공장과 전혀 무관한 질문. 주식, 요리, 날씨 예보, 연예, 스포츠, 정치, 의료, SNS 등.

핵심 구분:
- "이상 있어?" / "이상 발생했어?" → anomaly
- "COP 떨어질까?" / "이상 계속될까?" → forecast
- "설비 상태 어때?" / "태양광 이상 확인해봐" → cms
- "자급률 왜 떨어졌어?" / "이번 달 비용?" → report
- "오늘 주식 뭐 살까?" / "점심 뭐 먹을까?" / "날씨 예보 알려줘" → off_topic

질문: {question}"""


# 특정 계량기 URN 패턴 (V.Z84, H1.Z16 등) → rag로 직행
_METER_URN_PAT = re.compile(r"[A-Z]\d?\.(?:[A-Z]\.)?Z\d+", re.IGNORECASE)


def _rule_classify(question: str) -> str | None:
    """키워드 룰로 명확히 분류 가능하면 반환, 애매하면 None."""
    # 특정 계량기 URN이 보이면 무조건 rag
    if _METER_URN_PAT.search(question):
        return "rag"

    q = question.lower()

    # 에너지·설비와 무관한 명백 off-topic — LLM 호출 불필요
    if _KW_OFFTOPIC.search(q):
        return "off_topic"

    # 미래 시제 표현이 있으면 다른 키워드보다 forecast 우선
    if _KW_FUTURE.search(q):
        return "forecast"

    # 설비명 + 이상건수 조합 → cms 강제 (compute_equipment_status가 정확한 건수 반환)
    if _KW_EQ_ANOMALY_COUNT.search(q):
        return "cms"

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
        fast=True,
    ).strip().lower()
    valid = ("anomaly", "report", "rag", "forecast", "cms", "off_topic")
    intent = raw if raw in valid else "rag"
    print(f"[Orchestrator] 의도 분류 (LLM): '{question}' → {intent}")
    return {**state, "intent": intent}


def route(state: AgentState) -> str:
    return state.get("intent", "rag")


# ── 거절 응답 (off_topic) — LLM 호출 없이 템플릿으로 반환 ──────────

_REJECTION_MSG = (
    "저는 Honda R&D 에너지·설비 관리 전문 AI 코파일럿입니다.\n"
    "에너지 데이터 분석, 설비 상태 모니터링, 이상탐지, 예지보전 등 "
    "설비 운영 관련 질문을 도와드릴 수 있습니다."
)


def rejection_node(state: AgentState) -> AgentState:
    print("[Orchestrator] off_topic → 거절 응답")
    return {**state, "final_answer": _REJECTION_MSG}


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
_graph_lock = threading.Lock()


def build_graph():
    global _graph
    if _graph is not None:
        return _graph
    with _graph_lock:
        if _graph is not None:
            return _graph

        g = StateGraph(AgentState)

        g.add_node("classify",   classify_intent)
        g.add_node("rag",        rag_node)
        g.add_node("anomaly",    anomaly_node)
        g.add_node("report",     report_node)
        g.add_node("forecast",   forecast_node)
        g.add_node("cms",        cms_node)
        g.add_node("critic",     critic_node)
        g.add_node("rejection",  rejection_node)

        g.set_entry_point("classify")

        g.add_conditional_edges("classify", route, {
            "rag":       "rag",
            "anomaly":   "anomaly",
            "report":    "report",
            "forecast":  "forecast",
            "cms":       "cms",
            "off_topic": "rejection",
        })

        g.add_edge("rag",       "critic")
        g.add_edge("anomaly",   "critic")
        g.add_edge("report",    "critic")
        g.add_edge("forecast",  "critic")
        g.add_edge("cms",       "critic")
        g.add_edge("critic",    END)
        g.add_edge("rejection", END)   # 거절은 critic 통과 불필요

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
