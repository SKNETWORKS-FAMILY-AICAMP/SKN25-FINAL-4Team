"""
LangGraph 공유 상태 정의.
모든 에이전트가 이 State를 읽고 쓴다.
"""

from typing_extensions import TypedDict, NotRequired
from typing import Annotated
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    question: str                        # 사용자 원본 질문
    intent: str                          # 최종 의도: rag / anomaly / report / forecast / cms / off_topic
    rag_answer: str                      # RAG Agent 답변
    rag_sources: list[str]               # RAG Agent 참조 소스
    anomaly_result: dict                 # Anomaly Agent 결과
    report_result: str                   # Reporting Agent 결과
    forecast_result: dict                # Forecast Agent 결과
    critic_feedback: str                 # Critic Agent 피드백
    final_answer: str                    # 사용자에게 전달할 최종 답변
    pdf_path: str                        # 생성된 PDF 경로 (Reporting Agent)
    context: dict                        # 현재 화면/설비 컨텍스트 (예: {"equipment_id": "cooling"})
    messages: Annotated[list, add_messages]  # 대화 히스토리

    # 2-stage 라우터 확장 필드 (선택)
    request_type: NotRequired[str]            # query | action_request | approval_required | off_topic
    route: NotRequired[str]                  # anomaly | cms | report | forecast | rag
    request_type_method: NotRequired[str]     # rule | llm
    route_method: NotRequired[str]            # rule | llm
