import pytest
from unittest.mock import patch

def mock_llm_chat(messages, max_tokens=1024):
    """LLM 실제 API 호출을 차단하고 텍스트를 검사하여 모킹된 결과를 반환"""
    prompt = messages[0]["content"]
    
    # 1. Orchestrator Intent 분류
    if "사용자 질문을 읽고 아래 중 하나로만 답하세요" in prompt:
        if "예측" in prompt:
            return "forecast"
        elif "보고서" in prompt:
            return "report"
        elif "이상" in prompt:
            return "anomaly"
        return "rag"
    
    # 2. Critic Node 응답
    if "당신은 에너지 분석 품질 검토자입니다" in prompt:
        return "수정된 Mocked Answer"
    
    return "Mocked RAG/Anomaly Answer"

@patch("agents.llm_client.chat", side_effect=mock_llm_chat)
def test_orchestrator_classify_intent(mock_chat):
    """사용자 질문에 따라 의도가 올바르게 분류되는지 확인"""
    from agents.orchestrator import classify_intent
    
    state1 = classify_intent({"question": "최근 한 달 동안의 이상탐지 결과 보여줘"})
    assert state1["intent"] == "anomaly"
    
    state2 = classify_intent({"question": "내일 소비량이 어떻게 될까 (예측)"})
    assert state2["intent"] == "forecast"
    
    state3 = classify_intent({"question": "에너지 자급률이 뭐야?"})
    assert state3["intent"] == "rag"

@patch("agents.llm_client.chat", side_effect=mock_llm_chat)
def test_critic_node_filtering(mock_chat):
    """Critic 노드가 특정 금지어가 포함될 때 LLM을 호출해 수정하는지 확인"""
    from agents.orchestrator import critic_node
    
    # 한국 전력 용어('한전') 포함 -> LLM 교정 호출 대상
    state_bad = critic_node({
        "question": "어디서 전기를 가져오나요?",
        "rag_answer": "이 시설은 한전에서 전기를 가져옵니다.",
        "anomaly_result": {},
        "report_result": ""
    })
    assert state_bad["final_answer"] == "수정된 Mocked Answer"
    
    # 정상 용어 및 짧은 답변 -> LLM 호출 없이 바로 통과해야 함
    state_good = critic_node({
        "question": "상태가 어떤가요?",
        "rag_answer": "현재 시스템은 정상적으로 운영 중입니다.",
        "anomaly_result": {},
        "report_result": ""
    })
    assert state_good["final_answer"] == "현재 시스템은 정상적으로 운영 중입니다."
