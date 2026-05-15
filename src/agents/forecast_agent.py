"""
Forecast Agent — 전력 소비 예측 결과를 불러와 자연어로 설명.
orchestrator에서 intent='forecast'일 때 호출.
"""

import os
import re
import sys
from pathlib import Path

import pandas as pd
from llm_client import chat as llm_chat
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent))



# ── 질문에서 예측 시간 파싱 ───────────────────────────────────────

def _parse_horizon(question: str) -> int:
    """질문에서 예측 시간(시간 단위)을 추출. 기본 24h."""
    q = question
    if re.search(r"1\s*달|한\s*달|월간|1\s*month", q):
        return 168  # 7일 (Prophet 최대)
    if re.search(r"1\s*주|일주일|주간|7\s*일", q):
        return 168
    if re.search(r"3\s*일|72\s*시간", q):
        return 72
    if re.search(r"2\s*일|48\s*시간", q):
        return 48
    m = re.search(r"(\d+)\s*시간", q)
    if m:
        return min(int(m.group(1)), 168)
    return 24  # 기본값 24시간


def _run_forecast(hours: int) -> dict:
    """Prophet → XGBoost 순으로 예측 시도. 둘 다 실패 시 오류 반환."""
    from data.loader import load_range

    # 최근 3개월 데이터를 컨텍스트로 사용 (DB 쿼리 속도 고려)
    end   = pd.Timestamp.now().normalize()
    start = end - pd.DateOffset(months=3)
    try:
        df = load_range(str(start.date()), str(end.date()))
    except Exception as e:
        return {"error": str(e), "model": None, "records": []}

    for model_name in ("prophet", "xgboost"):
        try:
            if model_name == "prophet":
                from models.forecasting.prophet_model import predict
                fc = predict(df, hours=hours)
                records = [
                    {"ts": str(r["ts"])[:16], "yhat_kw": round(float(r["yhat"]), 1)}
                    for _, r in fc.iterrows()
                ]
            else:
                from models.forecasting.xgboost_model import predict
                fc = predict(df, hours=hours)
                records = [
                    {"ts": str(r["ts"])[:16], "yhat_kw": round(float(r["yhat"]) / 1000, 1)}
                    for _, r in fc.iterrows()
                ]
            return {"error": None, "model": model_name, "records": records}
        except FileNotFoundError:
            continue
        except Exception:
            continue

    return {"error": "저장된 예측 모델 없음 (POST /forecast/train 먼저 실행)", "model": None, "records": []}


def _summarize(records: list[dict], hours: int, model: str) -> str:
    """예측 결과 수치 요약 문자열 생성."""
    if not records:
        return "예측 데이터 없음"
    vals = [r["yhat_kw"] for r in records]
    avg  = sum(vals) / len(vals)
    peak = max(vals)
    low  = min(vals)
    peak_ts = records[vals.index(peak)]["ts"]
    return (
        f"모델: {model} | 예측 기간: {hours}시간\n"
        f"- 평균 계통 전력: {avg:.1f} kW\n"
        f"- 피크: {peak:.1f} kW ({peak_ts})\n"
        f"- 최저: {low:.1f} kW\n"
        f"- 예측 시작: {records[0]['ts']} / 종료: {records[-1]['ts']}"
    )


# ── LangGraph 노드 ────────────────────────────────────────────────

def run(state: dict) -> dict:

    question = state.get("question", "")
    hours    = _parse_horizon(question)
    result   = _run_forecast(hours)

    # 히스토리 포함
    history_lines = []
    for m in (state.get("messages") or [])[-6:]:
        role = "사용자" if m.__class__.__name__ == "HumanMessage" else "AI"
        history_lines.append(f"{role}: {m.content}")
    history_block = ("\n## 이전 대화\n" + "\n".join(history_lines)) if history_lines else ""

    if result["error"]:
        forecast_block = f"예측 실패: {result['error']}"
    else:
        forecast_block = _summarize(result["records"], hours, result["model"])

    prompt = f"""당신은 에너지 소비 예측 전문 AI입니다.
시설: Honda R&D Europe GmbH, 독일 오펜바흐. 전력망: 독일 공공 전력망.
용어: "계통 전력" 사용 (한전·수전량 등 한국 용어 사용 금지).
{history_block}

## 예측 결과
{forecast_block}

## 사용자 질문
{question}

예측 수치를 기반으로 다음을 설명하세요:
1. 예측 기간 평균·피크 소비량 요약
2. 피크 시간대 및 주의사항
3. 실제 운영에 유용한 권고사항 (설비 가동 일정 조정 등)
모델이 없으면 학습 방법을 안내하세요."""

    return {
        **state,
        "rag_answer": llm_chat([{"role": "user", "content": prompt}], max_tokens=1024),
        "forecast_result": result,
    }


def langgraph_node(state: dict) -> dict:
    return run(state)
