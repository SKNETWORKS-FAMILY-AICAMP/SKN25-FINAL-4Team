from api.errors import safe_err
import asyncio
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from langchain_core.messages import HumanMessage, AIMessage

from api.db import get_conn as _db_conn

router = APIRouter(prefix="/chat", tags=["chat"])
_executor = ThreadPoolExecutor(max_workers=4)


# ══════════════════════════════════════════════════════════════════
#  세션 저장소 — DB 스키마
# ══════════════════════════════════════════════════════════════════

def _ensure_chat_tables(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id            TEXT PRIMARY KEY,
            title         TEXT,
            created_at    TIMESTAMPTZ DEFAULT NOW(),
            updated_at    TIMESTAMPTZ DEFAULT NOW(),
            message_count INT DEFAULT 0,
            last_intent   TEXT
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id          BIGSERIAL PRIMARY KEY,
            session_id  TEXT REFERENCES chat_sessions(id) ON DELETE CASCADE,
            role        TEXT NOT NULL,
            content     TEXT NOT NULL,
            intent      TEXT,
            created_at  TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, id);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated ON chat_sessions(updated_at DESC);
    """)
    conn.commit()


def _make_title(question: str) -> str:
    """첫 질문에서 세션 제목 생성 (40자 컷)."""
    t = (question or "").strip().replace("\n", " ")
    return (t[:40] + "…") if len(t) > 40 else t


def _save_user_message(session_id: str, content: str, is_first: bool) -> None:
    """사용자 메시지 저장 + 세션 메타 업데이트."""
    try:
        with _db_conn() as conn:
            cur = conn.cursor()
            if is_first:
                cur.execute("""
                    INSERT INTO chat_sessions (id, title) VALUES (%s, %s)
                    ON CONFLICT (id) DO NOTHING;
                """, (session_id, _make_title(content)))
            cur.execute("""
                INSERT INTO chat_messages (session_id, role, content) VALUES (%s, 'user', %s);
            """, (session_id, content))
            cur.execute("""
                UPDATE chat_sessions
                SET message_count = message_count + 1, updated_at = NOW()
                WHERE id = %s;
            """, (session_id,))
            conn.commit()
    except Exception as e:
        print(f"[chat] save_user_message failed: {e}")


def _save_assistant_message(session_id: str, content: str, intent: str) -> None:
    """어시스턴트 메시지 저장 + 세션 last_intent 업데이트."""
    try:
        with _db_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO chat_messages (session_id, role, content, intent)
                VALUES (%s, 'assistant', %s, %s);
            """, (session_id, content, intent))
            cur.execute("""
                UPDATE chat_sessions
                SET message_count = message_count + 1, updated_at = NOW(), last_intent = %s
                WHERE id = %s;
            """, (intent, session_id))
            conn.commit()
    except Exception as e:
        print(f"[chat] save_assistant_message failed: {e}")


# ══════════════════════════════════════════════════════════════════
#  세션 API
# ══════════════════════════════════════════════════════════════════

@router.get("/sessions")
def list_sessions(
    limit: int = Query(50, ge=1, le=200),
    search: str = Query("", description="제목·메시지 부분 일치"),
):
    """세션 목록 (최근 갱신 순)."""
    try:
        with _db_conn() as conn:
            cur = conn.cursor()
            if search:
                pat = f"%{search}%"
                cur.execute("""
                    SELECT DISTINCT s.id, s.title, s.created_at, s.updated_at,
                                    s.message_count, s.last_intent
                    FROM chat_sessions s
                    LEFT JOIN chat_messages m ON m.session_id = s.id
                    WHERE s.title ILIKE %s OR m.content ILIKE %s
                    ORDER BY s.updated_at DESC LIMIT %s;
                """, (pat, pat, limit))
            else:
                cur.execute("""
                    SELECT id, title, created_at, updated_at, message_count, last_intent
                    FROM chat_sessions
                    ORDER BY updated_at DESC LIMIT %s;
                """, (limit,))
            rows = cur.fetchall()
    except Exception as e:
        return {"error": safe_err(e), "items": []}

    items = [
        {
            "id":            r[0],
            "title":         r[1] or "(제목 없음)",
            "created_at":    r[2].isoformat() if r[2] else None,
            "updated_at":    r[3].isoformat() if r[3] else None,
            "message_count": r[4],
            "last_intent":   r[5],
        }
        for r in rows
    ]
    return {"items": items}


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    """세션 상세 — 모든 메시지 반환."""
    try:
        with _db_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, title, created_at, updated_at, message_count, last_intent
                FROM chat_sessions WHERE id = %s;
            """, (session_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="세션 없음")

            cur.execute("""
                SELECT role, content, intent, created_at
                FROM chat_messages
                WHERE session_id = %s
                ORDER BY id ASC;
            """, (session_id,))
            msgs = cur.fetchall()
    except HTTPException:
        raise
    except Exception as e:
        return {"error": safe_err(e)}

    return {
        "session": {
            "id":            row[0],
            "title":         row[1],
            "created_at":    row[2].isoformat() if row[2] else None,
            "updated_at":    row[3].isoformat() if row[3] else None,
            "message_count": row[4],
            "last_intent":   row[5],
        },
        "messages": [
            {
                "role":       m[0],
                "content":    m[1],
                "intent":     m[2],
                "created_at": m[3].isoformat() if m[3] else None,
            }
            for m in msgs
        ],
    }


@router.delete("/sessions")
def delete_all_sessions():
    """전체 대화 세션 + 메시지 삭제 (CASCADE). 데모 초기화용."""
    try:
        with _db_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM chat_sessions;")
            deleted = cur.rowcount
            conn.commit()
        return {"deleted": True, "count": deleted}
    except Exception as e:
        return {"error": safe_err(e)}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    """세션 + 메시지 삭제 (CASCADE)."""
    try:
        with _db_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM chat_sessions WHERE id = %s;", (session_id,))
            conn.commit()
        return {"deleted": True}
    except Exception as e:
        return {"error": safe_err(e)}


# ══════════════════════════════════════════════════════════════════
#  요청·응답 모델
# ══════════════════════════════════════════════════════════════════

class HistoryMessage(BaseModel):
    role: str
    text: str


class ChatRequest(BaseModel):
    question: str
    history: list[HistoryMessage] = []
    session_id: str | None = None
    is_first:   bool = False
    context:    dict | None = None   # 현재 보던 화면/설비 컨텍스트 (예: {"equipment_id": "cooling"})


class ChatResponse(BaseModel):
    question: str
    intent: str
    answer: str
    pdf_path: str = ""
    session_id: str = ""
    timing_trace: dict | None = None


def _invoke_graph(question: str, lc_messages: list, context: dict | None = None) -> dict:
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
        "context":          context or {},
    }
    return graph.invoke(initial)


# ══════════════════════════════════════════════════════════════════
#  스트리밍·일반 채팅
# ══════════════════════════════════════════════════════════════════

@router.post("/stream")
async def chat_stream(req: ChatRequest):
    """SSE 스트리밍 채팅 — final_answer를 단어 단위로 실시간 전송, 세션에 저장."""
    lc_messages = []
    for m in req.history[-10:]:
        if m.role == "user":
            lc_messages.append(HumanMessage(content=m.text))
        elif m.role == "assistant":
            lc_messages.append(AIMessage(content=m.text))

    question   = req.question
    session_id = req.session_id or str(uuid.uuid4())
    is_first   = req.is_first or not req.session_id

    # 사용자 메시지 즉시 저장 (백그라운드 스레드)
    await asyncio.get_running_loop().run_in_executor(
        _executor, _save_user_message, session_id, question, is_first
    )

    # 진행 단계 메시지 (시간이 흐르면서 순차적으로 보여줌)
    PROGRESS_STEPS = [
        "🔍 질문의 의도를 파악하고 있어요...",
        "📚 관련 데이터를 조회하고 있어요...",
        "🤖 AI 에이전트가 분석 중이에요...",
        "✍️  답변을 정리하고 있어요...",
        "⏳ 거의 다 됐어요...",
    ]

    async def generate():
        # session_id를 먼저 전송 (프론트가 신규 세션 id 받아갈 수 있게)
        yield f"data: {json.dumps({'type': 'session', 'session_id': session_id})}\n\n"
        yield f"data: {json.dumps({'type': 'status', 'content': PROGRESS_STEPS[0]})}\n\n"

        # graph 실행을 백그라운드 태스크로
        loop = asyncio.get_running_loop()
        graph_task = loop.run_in_executor(_executor, _invoke_graph, question, lc_messages, req.context)

        # 1.8초마다 다음 진행 메시지 전송
        step_idx = 1
        try:
            while True:
                try:
                    result = await asyncio.wait_for(asyncio.shield(graph_task), timeout=1.8)
                    break   # 완료
                except asyncio.TimeoutError:
                    if step_idx < len(PROGRESS_STEPS):
                        yield f"data: {json.dumps({'type': 'status', 'content': PROGRESS_STEPS[step_idx]})}\n\n"
                        step_idx += 1
                    # 마지막 메시지 이후엔 그대로 유지하며 계속 대기
            answer   = result.get("final_answer") or "답변을 생성할 수 없습니다."
            intent   = result.get("intent", "")
            pdf_path = result.get("pdf_path", "")
        except Exception as e:
            try:
                from agents.llm_client import LLMClientError
            except Exception:  # pragma: no cover
                LLMClientError = ()
            is_llm_error = isinstance(e, LLMClientError) or e.__class__.__name__ == "LLMClientError"
            if is_llm_error:
                payload = {
                    'type': 'error',
                    'error_type': 'llm_unavailable',
                    'content': 'LLM endpoint/model is unavailable',
                    'provider': getattr(e, 'provider', ''),
                    'model': getattr(e, 'model', ''),
                    'status_code': getattr(e, 'status_code', None),
                }
                yield f"data: {json.dumps(payload)}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'error', 'content': safe_err(e)})}\n\n"
            return

        yield f"data: {json.dumps({'type': 'intent', 'content': intent})}\n\n"

        words  = answer.split(" ")
        CHUNK  = 3
        for i in range(0, len(words), CHUNK):
            chunk = " ".join(words[i : i + CHUNK])
            if i + CHUNK < len(words):
                chunk += " "
            yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"
            await asyncio.sleep(0.015)

        # 어시스턴트 메시지 저장 (스트리밍 종료 직전)
        await asyncio.get_running_loop().run_in_executor(
            _executor, _save_assistant_message, session_id, answer, intent
        )

        yield f"data: {json.dumps({'type': 'done', 'pdf_path': pdf_path})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":        "keep-alive",
        },
    )


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """비스트리밍 채팅 — 세션에 자동 저장."""
    t_total = time.perf_counter()
    lc_messages = []
    for m in req.history[-10:]:
        if m.role == "user":
            lc_messages.append(HumanMessage(content=m.text))
        elif m.role == "assistant":
            lc_messages.append(AIMessage(content=m.text))

    session_id = req.session_id or str(uuid.uuid4())
    is_first   = req.is_first or not req.session_id

    loop = asyncio.get_running_loop()
    t_save_user = time.perf_counter()
    await loop.run_in_executor(_executor, _save_user_message, session_id, req.question, is_first)
    save_user_ms = round((time.perf_counter() - t_save_user) * 1000, 2)

    try:
        t_graph = time.perf_counter()
        result = await loop.run_in_executor(_executor, _invoke_graph, req.question, lc_messages, req.context)
        graph_ms = round((time.perf_counter() - t_graph) * 1000, 2)
    except Exception as e:
        # LLM endpoint/model errors should be diagnosable by the QA/E2E pipeline,
        # not collapsed into an opaque HTTP 500.
        try:
            from agents.llm_client import LLMClientError
        except Exception:  # pragma: no cover
            LLMClientError = ()
        is_llm_error = isinstance(e, LLMClientError) or e.__class__.__name__ == "LLMClientError"
        if is_llm_error:
            raise HTTPException(
                status_code=503,
                detail={
                    "error_type": "llm_unavailable",
                    "message": "LLM endpoint/model is unavailable",
                    "provider": getattr(e, "provider", ""),
                    "model": getattr(e, "model", ""),
                    "status_code": getattr(e, "status_code", None),
                },
            )
        raise

    answer = result.get("final_answer", "답변을 생성할 수 없습니다.")
    intent = result.get("intent", "")
    t_save_assistant = time.perf_counter()
    await loop.run_in_executor(_executor, _save_assistant_message, session_id, answer, intent)
    save_assistant_ms = round((time.perf_counter() - t_save_assistant) * 1000, 2)
    timing_trace = dict(result.get("timing_trace") or {})
    timing_trace["backend_save_user"] = {"latency_ms": save_user_ms}
    timing_trace["langgraph_total"] = {"latency_ms": graph_ms}
    timing_trace["backend_save_assistant"] = {"latency_ms": save_assistant_ms}
    timing_trace["backend_total_until_response_build"] = {"latency_ms": round((time.perf_counter() - t_total) * 1000, 2)}

    return ChatResponse(
        question=req.question,
        intent=intent,
        answer=answer,
        pdf_path=result.get("pdf_path", ""),
        session_id=session_id,
        timing_trace=timing_trace,
    )


@router.get("/download-pdf")
def download_chat_pdf(filename: str):
    """채팅에서 생성된 보고서 PDF 파일을 다운로드"""
    from agents.reporting_agent import PDF_DIR
    
    # 경로 보안 검증 (Path Traversal 차단)
    safe_path = (PDF_DIR / filename).resolve()
    if not str(safe_path).startswith(str(PDF_DIR.resolve())):
        raise HTTPException(status_code=400, detail="허용되지 않는 파일 이름입니다.")
        
    if not safe_path.exists():
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
        
    return FileResponse(
        path=safe_path,
        media_type="application/pdf",
        filename=filename
    )
