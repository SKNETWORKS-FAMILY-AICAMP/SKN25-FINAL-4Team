"""
LangGraph 공유 상태 정의.
모든 에이전트가 이 State를 읽고 쓴다.
"""

from typing import Annotated
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    question: str                        # 사용자 원본 질문
    intent: str                          # 의도 분류: rag / anomaly / report / forecast / general
    rag_answer: str                      # RAG Agent 답변
    rag_sources: list[str]               # RAG Agent 참조 소스
    ontology_context: list[str]          # 온톨로지 검색 결과
    anomaly_result: dict                 # Anomaly Agent 결과
    report_result: str                   # Reporting Agent 결과
    forecast_result: dict                # Forecast Agent 결과
    critic_feedback: str                 # Critic Agent 피드백
    final_answer: str                    # 사용자에게 전달할 최종 답변
    pdf_path: str                        # 생성된 PDF 경로 (Reporting Agent)
    messages: Annotated[list, add_messages]  # 대화 히스토리
