"""
RAG Agent — 온톨로지 지식 + 문서 검색 → LLM 답변 생성.
LangGraph StateGraph의 노드로 호출되거나 단독으로 사용 가능.
"""

import os
import sys
from dataclasses import dataclass, field
from typing import Optional

import psycopg2
from llm_client import chat as llm_chat
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "knowledge"))
from ontology_indexer import embed_query  # 로컬 임베딩 모델 재사용

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/energy_db")
TOP_K = 5  # 검색할 문서 수


@dataclass
class RAGState:
    question: str
    ontology_context: list[str] = field(default_factory=list)
    doc_context: list[str] = field(default_factory=list)
    answer: str = ""
    sources: list[str] = field(default_factory=list)



def search_ontology(question: str, top_k: int = TOP_K) -> list[str]:
    """온톨로지 지식베이스에서 관련 문장 검색."""
    embedding = embed_query(question)
    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"

    conn = psycopg2.connect(DB_URL)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT content, 1 - (embedding <=> %s::vector) AS similarity
            FROM ontology_knowledge
            ORDER BY embedding <=> %s::vector
            LIMIT %s;
            """,
            (vec_str, vec_str, top_k),
        )
        rows = cur.fetchall()
    conn.close()

    return [row[0] for row in rows if row[1] > 0.4]  # 유사도 0.4 이하 제거


def search_documents(question: str, top_k: int = TOP_K) -> list[str]:
    """일반 문서 지식베이스에서 관련 청크 검색 (energy_documents 테이블)."""
    try:
        embedding = embed_query(question)
        vec_str = "[" + ",".join(str(x) for x in embedding) + "]"

        conn = psycopg2.connect(DB_URL)
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
        conn.close()
        return [row[0] for row in rows]
    except Exception:
        return []


def build_prompt(state: RAGState, history: list | None = None) -> str:
    ontology_block = "\n".join(f"- {s}" for s in state.ontology_context) or "없음"
    doc_block = "\n".join(f"- {s}" for s in state.doc_context) or "없음"

    history_block = ""
    if history:
        lines = []
        for m in history:
            role = "사용자" if m.__class__.__name__ == "HumanMessage" else "AI"
            lines.append(f"{role}: {m.content}")
        history_block = "\n## 이전 대화\n" + "\n".join(lines) + "\n"

    return f"""당신은 에너지 관리 전문 AI 분석가입니다.
Honda R&D 에너지 데이터 분석 시스템의 에이전트로서, 아래 지식을 바탕으로 정확하고 간결하게 답하세요.

## 시설 컨텍스트
- 시설: Honda R&D Europe GmbH, 독일 오펜바흐 (Offenbach, Germany)
- 전력망: 독일 공공 전력망 (한국 한전과 무관)
- 전력 용어: "계통 전력" 또는 "외부 계통 전력" 사용 (한전·수전량·한국 전력 용어 사용 금지)
- 데이터 기간: 2017~2024년

## 에너지 도메인 지식 (온톨로지)
{ontology_block}

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
"""


def run(question: str, history: list | None = None) -> RAGState:
    """RAG Agent 실행. LangGraph 노드에서 state dict로 호출 가능."""

    state = RAGState(question=question)

    # 1. 온톨로지 검색
    state.ontology_context = search_ontology(question)

    # 2. 문서 검색
    state.doc_context = search_documents(question)

    # 3. LLM 답변 생성
    prompt = build_prompt(state, history=history)
    state.answer = llm_chat([{"role": "user", "content": prompt}], max_tokens=1024)

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
        "ontology_context": result.ontology_context,
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
        state.ontology_context = ["COP = 냉방출력 / 냉방전력. 중앙값 2.06. cool_elec=0 방어 필수.",
                                   "CHP 시스템은 전기와 열을 동시에 생산한다.",
                                   "자급률 = (PV + CHP) / total_consumption. 6년평균 39.6%(2022년 46.9%)."]
        prompt = build_prompt(state)
        print(f"답변: {llm_chat([{'role': 'user', 'content': prompt}], max_tokens=512)}")
