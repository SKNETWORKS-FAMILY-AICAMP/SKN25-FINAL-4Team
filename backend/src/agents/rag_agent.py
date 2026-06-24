"""
RAG Agent — 도메인 지식 + 문서 검색 → LLM 답변 생성.
LangGraph StateGraph의 노드로 호출되거나 단독으로 사용 가능.
"""

import os
import re
import sys
from dataclasses import dataclass, field
from typing import Optional

from llm_client import chat as llm_chat
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "knowledge"))
from embedding import embed_query
from domain_knowledge import DOMAIN_KNOWLEDGE_PROMPT

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "api"))
from db import get_conn

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
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "data"))
        from loader import get_meter_measurement_stats
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


def _domain_fallback_answer(question: str) -> str:
    q = (question or "").lower()
    if "pf1" in q or "역률" in q or "power factor" in q:
        return (
            "PF1은 1번 상의 역률(power factor)을 의미합니다. "
            "역률은 유효전력이 피상전력 중 얼마나 효율적으로 사용되는지를 나타내는 무차원 값이며, "
            "1에 가까울수록 전력이 효율적으로 사용된다는 뜻입니다."
        )
    if "pf2" in q:
        return "PF2는 2번 상의 역률(power factor)을 의미합니다. 1에 가까울수록 전력 사용 효율이 높습니다."
    if "pf3" in q:
        return "PF3는 3번 상의 역률(power factor)을 의미합니다. 1에 가까울수록 전력 사용 효율이 높습니다."
    if "cop" in q:
        return (
            "COP는 냉방·열공급 설비의 성능계수입니다. "
            "같은 전력으로 더 많은 냉열 또는 열을 만들수록 COP가 높고, 설비 효율이 좋다고 해석합니다."
        )
    if "전압" in q:
        return "전압은 전기 회로에서 전류를 흐르게 하는 전위차입니다. EMS에서는 상별 전압을 확인해 전원 품질과 불균형 여부를 점검합니다."
    if "전류" in q:
        return "전류는 전기 부하에 실제로 흐르는 전하의 양입니다. EMS에서는 상별 전류를 통해 부하 크기와 불균형을 확인합니다."
    return "해당 도메인 용어는 계량기 정보와 운영 데이터를 함께 확인해 해석해야 합니다. 구체적인 계량기 ID나 측정 항목을 알려주시면 더 정확히 설명할 수 있습니다."


def run(question: str, history: list | None = None) -> RAGState:
    """RAG Agent 실행. LangGraph 노드에서 state dict로 호출 가능."""

    state = RAGState(question=question)

    # 0. 핵심 도메인 용어는 LLM이 부정확하게 회피하지 않도록 결정론적 정의를 우선 사용
    if re.search(r"pf[123]?|역률|power\s*factor|cop|전압|전류", question, re.IGNORECASE):
        state.answer = _domain_fallback_answer(question)
        return state

    # 1. 문서 검색
    state.doc_context = search_documents(question)

    # 1-b. 미터 실측값 조회 (V.Z84 PF1 같은 특정 계량기 질문)
    state.meter_facts = lookup_meter_measurements(question)

    # 2. LLM 답변 생성. Endpoint/model이 빈 content를 반환하면 domain fallback으로 보강한다.
    prompt = build_prompt(state, history=history)
    try:
        state.answer = llm_chat([{"role": "user", "content": prompt}], max_tokens=1024, thinking=False)
    except Exception:
        state.answer = ""
    if not (state.answer or "").strip():
        state.answer = _domain_fallback_answer(question)

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
