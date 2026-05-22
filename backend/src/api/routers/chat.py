import asyncio
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter
from pydantic import BaseModel
from langchain_core.messages import HumanMessage, AIMessage

router = APIRouter(prefix="/chat", tags=["chat"])
_executor = ThreadPoolExecutor(max_workers=4)


class HistoryMessage(BaseModel):
    role: str   # "user" | "assistant"
    text: str


class ChatRequest(BaseModel):
    question: str
    history: list[HistoryMessage] = []


class ChatResponse(BaseModel):
    question: str
    intent: str
    answer: str
    pdf_path: str = ""


def _invoke_graph(question: str, lc_messages: list) -> dict:
    """동기 LangGraph 실행 — ThreadPoolExecutor에서 호출."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from agents.orchestrator import build_graph
    from agents.state import AgentState

    graph   = build_graph()
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
        "messages":         lc_messages,
    }
    return graph.invoke(initial)


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """사용자 자연어 질문을 오케스트레이터로 라우팅해 최종 답변 반환."""
    lc_messages = []
    for m in req.history[-10:]:
        if m.role == "user":
            lc_messages.append(HumanMessage(content=m.text))
        elif m.role == "assistant":
            lc_messages.append(AIMessage(content=m.text))

    # 동기 그래프를 스레드풀에서 실행해 이벤트 루프 블로킹 방지
    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        _executor, _invoke_graph, req.question, lc_messages
    )

    return ChatResponse(
        question=req.question,
        intent=result.get("intent", ""),
        answer=result.get("final_answer", "답변을 생성할 수 없습니다."),
        pdf_path=result.get("pdf_path", ""),
    )
