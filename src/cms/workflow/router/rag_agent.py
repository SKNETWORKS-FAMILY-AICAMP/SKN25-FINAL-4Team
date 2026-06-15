"""
RAG Agent — 도메인 지식 + 문서 검색 → LLM 답변 생성.
LangGraph StateGraph의 노드로 호출되거나 단독으로 사용 가능.
"""

import os
import re
from dataclasses import dataclass, field

from cms.workflow.router.llm_client import chat as llm_chat
from dotenv import load_dotenv

load_dotenv()

from cms.knowledge.domain_knowledge import DOMAIN_KNOWLEDGE_PROMPT

TOP_K = 5  # 검색할 문서 수

# 미터 URN 패턴 (예: V.Z84, H1.Z16, H2.T.Z310) — 뒤에 한글이 붙어도 매칭되도록 \b 미사용
_METER_PAT      = re.compile(r"([A-Z]\d?\.(?:[A-Z]\.)?Z\d+)", re.IGNORECASE)
_MEASUREMENT_PAT = re.compile(r"\b(PF[123]?|P[123]|U[123]|I[123]|W_in|W_out|Igm|Ta)\b")


# 한글 measurement 표현 → 코드 (전류 I1~I3 중 대표값으로 I2 사용 등)
_KOR_MEASUREMENT = [
    (re.compile(r"역률|전력\s*팩터|파워\s*팩터"), ["PF1", "PF2", "PF3"]),
    (re.compile(r"전류"),                       ["I1", "I2", "I3"]),
    (re.compile(r"전압"),                       ["U1", "U2", "U3"]),
    (re.compile(r"유효\s*전력|순간\s*전력|소비\s*전력|전력\s*소비"), ["P"]),
    (re.compile(r"무효\s*전력"),                 ["Q"]),
    (re.compile(r"적산|누적\s*전력량|에너지\s*소비량"), ["W"]),
]

# measurement 코드 → 단위 (LLM이 올바른 단위로 답하도록)
_UNIT_MAP = {
    "PF1": "(역률, 무차원)", "PF2": "(역률, 무차원)", "PF3": "(역률, 무차원)", "PF": "(역률, 무차원)",
    "I1": "A", "I2": "A", "I3": "A",
    "U1": "V", "U2": "V", "U3": "V",
    "P": "kW", "P1": "kW", "P2": "kW", "P3": "kW",
    "Q": "kVAR", "W": "kWh", "W_in": "kWh", "W_out": "kWh",
    "Igm": "W/m²", "Ta": "°C",
}


def lookup_meter_measurements(question: str) -> list[str]:
    """질문에서 미터 URN과 measurement를 추출해 실제 DB 통계를 반환.

    예: 'V.Z84 계량기의 PF1 값' → ['V.Z84 PF1: 최신 -0.97, 평균 -0.95 ...']
    """
    meters = _METER_PAT.findall(question)
    if not meters:
        return []
    measurements = _MEASUREMENT_PAT.findall(question)
    # 영문 약자가 없으면 한글 표현에서 추론
    if not measurements:
        for pat, codes in _KOR_MEASUREMENT:
            if pat.search(question):
                measurements = codes
                break
    if not measurements:
        measurements = ["P"]
    try:
        loader = __import__("cms.service.legacy_data.loader", fromlist=["get_meter_measurement_stats"])
        get_meter_measurement_stats = loader.get_meter_measurement_stats
    except Exception:
        return []

    facts = []
    for meter in meters[:3]:           # 미터 최대 3개
        for meas in measurements[:3]:  # measurement 최대 3개
            try:
                stats = get_meter_measurement_stats(meter.upper(), meas)
                if stats:
                    unit = _UNIT_MAP.get(meas, "")
                    facts.append(
                        f"{stats['meter_urn']} {stats['measurement']} {unit}: "
                        f"최신값 {stats['latest_value']} ({stats['latest_ts'][:10]}), "
                        f"평균 {stats['avg']}, 범위 {stats['min']}~{stats['max']} "
                        f"(최근 {stats['count']}개 측정)"
                    )
            except Exception:
                pass
    return facts


@dataclass
class RAGState:
    question: str
    doc_context: list[str] = field(default_factory=list)
    meter_facts: list[str] = field(default_factory=list)
    answer: str = ""
    sources: list[str] = field(default_factory=list)



def search_documents(question: str, top_k: int = TOP_K) -> list[str]:
    """일반 문서 지식베이스에서 관련 청크 검색 (energy_documents 테이블)."""
    try:
        from cms.knowledge.embedding import embed_query
        from cms.service.db import get_conn

        embedding = embed_query(question)
        vec_str = "[" + ",".join(str(x) for x in embedding) + "]"

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT content, source
                    FROM energy_documents
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s;
                    """,
                    (vec_str, top_k),
                )
                rows = cur.fetchall()
        return [row[0] for row in rows]
    except Exception:
        return []


def build_prompt(state: RAGState, history: list | None = None) -> str:
    doc_block = "\n".join(f"- {s}" for s in state.doc_context) or "없음"

    history_block = ""
    if history:
        lines = []
        for m in history:
            role = "사용자" if m.__class__.__name__ == "HumanMessage" else "AI"
            lines.append(f"{role}: {m.content}")
        history_block = "\n## 이전 대화\n" + "\n".join(lines) + "\n"

    meter_block = ""
    if state.meter_facts:
        meter_block = (
            "\n## 계량기 실측 데이터 (DB 조회 결과 — 이 수치를 근거로 답하세요)\n"
            + "\n".join(f"- {f}" for f in state.meter_facts) + "\n"
        )

    return f"""당신은 에너지 관리 전문 AI 분석가입니다.
Honda R&D 에너지 데이터 분석 시스템의 에이전트로서, 아래 지식을 바탕으로 정확하고 간결하게 답하세요.
질문 언어와 관계없이 항상 한국어로만 답변하세요.

{DOMAIN_KNOWLEDGE_PROMPT}
{meter_block}
## 참고 문서
{doc_block}
{history_block}
## 사용자 질문
{state.question}

답변 시 주의사항:
- 수치 언급 시 단위(W, kWh, °C 등)를 반드시 포함하세요.
- COP 계산 시 cool_elec=0인 경우를 고려하세요.
- 확실하지 않은 내용은 추측이라고 명시하세요.
- 한국 전력 관련 용어(한전, 수전량, 수전 전력 등) 절대 사용 금지.
- 에너지 관리·설비 모니터링·Honda 공장 데이터와 전혀 무관한 질문(주식, 요리, 날씨 예보, 연예, 스포츠, 정치, 의료, SNS 등)을 받으면 다음 한 문장만 답하고 더 이상 아무것도 추가하지 마세요: "저는 에너지·설비 관리 전문 AI입니다. 해당 주제는 업무 범위 밖이라 답변 드리기 어렵습니다. 설비 상태나 에너지 데이터에 대해 질문해 주세요."
"""


def run(question: str, history: list | None = None) -> RAGState:
    """RAG Agent 실행. LangGraph 노드에서 state dict로 호출 가능."""

    state = RAGState(question=question)

    # 1. 문서 검색
    state.doc_context = search_documents(question)

    # 1-b. 미터 실측값 조회 (V.Z84 PF1 같은 특정 계량기 질문)
    state.meter_facts = lookup_meter_measurements(question)

    # 2. LLM 답변 생성
    prompt = build_prompt(state, history=history)
    state.answer = llm_chat([{"role": "user", "content": prompt}], max_tokens=1024, thinking=False)

    return state


# ── LangGraph 노드 래퍼 ──────────────────────────────────────────

def langgraph_node(state: dict) -> dict:
    """LangGraph StateGraph 노드로 사용할 때의 진입점."""
    history = state.get("messages") or []
    result = run(state["question"], history=state.get("messages") or [])
    return {
        **state,
        "rag_answer": result.answer,
        "rag_sources": result.sources,
    }


if __name__ == "__main__":
    # 간단 테스트
    questions = [
        "COP가 갑자기 떨어졌는데 왜 그런 건가요?",
        "자급률이 낮아진 원인이 뭔가요?",
        "야간에 전력 소비가 감지됐는데 정상인가요?",
    ]
    for q in questions:
        print(f"\n질문: {q}")
        state = RAGState(question=q)
        prompt = build_prompt(state)
        print(f"답변: {llm_chat([{'role': 'user', 'content': prompt}], max_tokens=512)}")
