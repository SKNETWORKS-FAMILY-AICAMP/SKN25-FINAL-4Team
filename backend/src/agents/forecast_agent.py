"""
Forecast Agent — 전력 소비 예측 결과를 불러와 자연어로 설명.
orchestrator에서 intent='forecast'일 때 호출.

모델 우선순위:
  과거 기간 질문 → VMD-LSTM (ML 팀 학습 모델)
  미래 예측 질문 → VMD-LSTM rolling forecast → XGBoost → Prophet
"""

import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from llm_client import chat as llm_chat
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── 날짜 파싱 ─────────────────────────────────────────────────────

def _parse_date_range(question: str) -> tuple[str | None, str | None]:
    """질문에서 과거 날짜 범위를 추출. 파악 불가 시 (None, None)."""
    from datetime import timedelta
    now = datetime.now()
    q   = question

    # 연도+월: "2022년 7월", "2022-07"
    m = re.search(r"(\d{4})[년\-]?\s*(\d{1,2})월?", q)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        start = datetime(y, mo, 1)
        end   = (start + pd.DateOffset(months=1))
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    # 연도만: "2022년", "2023년도"
    m = re.search(r"(\d{4})년", q)
    if m:
        y = int(m.group(1))
        return f"{y}-01-01", f"{y+1}-01-01"

    # 월만: "7월" → 가장 최근 해당 월
    m = re.search(r"(?<!\d)(\d{1,2})월", q)
    if m:
        mo   = int(m.group(1))
        year = now.year if mo <= now.month else now.year - 1
        start = datetime(year, mo, 1)
        end   = (start + pd.DateOffset(months=1))
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    # 지난/이번
    if re.search(r"지난\s*달|저번\s*달|last\s*month", q):
        first_this = now.replace(day=1)
        e = first_this
        s = (first_this - pd.Timedelta(days=1)).replace(day=1)
        return s.strftime("%Y-%m-%d"), e.strftime("%Y-%m-%d")
    if re.search(r"지난\s*주|last\s*week", q):
        s = now - pd.Timedelta(days=now.weekday() + 7)
        e = now - pd.Timedelta(days=now.weekday())
        return s.strftime("%Y-%m-%d"), e.strftime("%Y-%m-%d")

    return None, None


def _parse_horizon(question: str) -> int:
    """질문에서 미래 예측 시간(시간 단위) 추출. 기본 24h."""
    q = question
    if re.search(r"1\s*달|한\s*달|월간|1\s*month", q):
        return 168
    if re.search(r"1\s*주|일주일|주간|7\s*일", q):
        return 168
    if re.search(r"3\s*일|72\s*시간", q):
        return 72
    if re.search(r"2\s*일|48\s*시간", q):
        return 48
    m = re.search(r"(\d+)\s*시간", q)
    if m:
        return min(int(m.group(1)), 168)
    return 24


def _is_future_question(question: str) -> bool:
    """질문이 미래 예측인지 여부."""
    return bool(re.search(
        r"앞으로|내일|다음|미래|예측해|예측 해|향후|이번 주|이번\s*주|this\s*week|next|forecast",
        question,
    ))


# ── VMD-LSTM 예측 ────────────────────────────────────────────────

def _run_vmd_lstm_historical(df: pd.DataFrame, start: str, end: str) -> dict:
    try:
        from models.forecasting.vmd_lstm_model import predict_historical, is_available
        if not is_available():
            return {"error": "VMD-LSTM 모델 파일 없음", "model": None, "records": [], "mode": "historical"}
        fc = predict_historical(df, start, end)
        if fc.empty:
            return {"error": "해당 기간 데이터 부족", "model": "vmd-lstm", "records": [], "mode": "historical"}
        records = [
            {
                "ts":           str(r["ts"])[:16],
                "actual_kw":   round(float(r["actual_w"]) / 1000, 1),
                "predicted_kw":round(float(r["predicted_w"]) / 1000, 1),
                "error_kw":    round(float(r["error_w"]) / 1000, 1),
            }
            for _, r in fc.iterrows()
        ]
        return {"error": None, "model": "vmd-lstm", "records": records, "mode": "historical"}
    except Exception as e:
        return {"error": str(e), "model": None, "records": [], "mode": "historical"}


def _run_vmd_lstm_future(df: pd.DataFrame, hours: int) -> dict:
    try:
        from models.forecasting.vmd_lstm_model import predict_future, is_available
        if not is_available():
            return {"error": "VMD-LSTM 모델 파일 없음", "model": None, "records": []}
        fc = predict_future(df, hours=hours)
        records = [
            {"ts": str(r["ts"])[:16], "yhat_kw": float(r["predicted_kw"])}
            for _, r in fc.iterrows()
        ]
        return {"error": None, "model": "vmd-lstm", "records": records}
    except Exception as e:
        return {"error": str(e), "model": None, "records": []}


def _run_future_fallback(df: pd.DataFrame, hours: int) -> dict:
    """XGBoost → Prophet 순으로 미래 예측 시도."""
    for model_name in ("xgboost", "prophet"):
        try:
            if model_name == "xgboost":
                from models.forecasting.xgboost_model import predict
                fc = predict(df, hours=hours)
                records = [
                    {"ts": str(r["ts"])[:16], "yhat_kw": round(float(r["yhat"]) / 1000, 1)}
                    for _, r in fc.iterrows()
                ]
            else:
                from models.forecasting.prophet_model import predict
                fc = predict(df, hours=hours)
                records = [
                    {"ts": str(r["ts"])[:16], "yhat_kw": round(float(r["yhat"]), 1)}
                    for _, r in fc.iterrows()
                ]
            return {"error": None, "model": model_name, "records": records}
        except FileNotFoundError:
            continue
        except Exception:
            continue
    return {"error": "저장된 예측 모델 없음 (POST /forecast/train 먼저 실행)", "model": None, "records": []}


# ── 요약 ────────────────────────────────────────────────────────

def _summarize_future(records: list[dict], hours: int, model: str) -> str:
    if not records:
        return "예측 데이터 없음"
    vals     = [r["yhat_kw"] for r in records]
    avg, peak, low = sum(vals) / len(vals), max(vals), min(vals)
    peak_ts  = records[vals.index(peak)]["ts"]
    return (
        f"모델: {model} | 예측 기간: {hours}시간\n"
        f"- 평균 계통 전력: {avg:.1f} kW\n"
        f"- 피크: {peak:.1f} kW ({peak_ts})\n"
        f"- 최저: {low:.1f} kW\n"
        f"- 예측 시작: {records[0]['ts']} / 종료: {records[-1]['ts']}"
    )


def _summarize_historical(records: list[dict], start: str, end: str, model: str) -> str:
    if not records:
        return "예측 데이터 없음"
    preds  = [r["predicted_kw"] for r in records]
    actuals = [r["actual_kw"] for r in records]
    errors  = [abs(r["error_kw"]) for r in records]
    mae_kw  = sum(errors) / len(errors)
    peak_p  = max(preds)
    peak_ts = records[preds.index(peak_p)]["ts"]
    # 실제 피크
    peak_a  = max(actuals)
    peak_a_ts = records[actuals.index(peak_a)]["ts"]
    return (
        f"모델: {model} | 분석 기간: {start} ~ {end} ({len(records)}시간)\n"
        f"- 예측 평균: {sum(preds)/len(preds):.1f} kW  /  실측 평균: {sum(actuals)/len(actuals):.1f} kW\n"
        f"- 예측 피크: {peak_p:.1f} kW ({peak_ts})\n"
        f"- 실측 피크: {peak_a:.1f} kW ({peak_a_ts})\n"
        f"- MAE: {mae_kw:.1f} kW"
    )


# ── LangGraph 노드 ────────────────────────────────────────────────

def run(state: dict) -> dict:
    question = state.get("question", "")
    history_lines = []
    for m in (state.get("messages") or [])[-6:]:
        role = "사용자" if m.__class__.__name__ == "HumanMessage" else "AI"
        history_lines.append(f"{role}: {m.content}")
    history_block = ("\n## 이전 대화\n" + "\n".join(history_lines)) if history_lines else ""

    # 데이터 로드 (최근 3개월 + 336h lag 여유분)
    end_dt   = pd.Timestamp.now(tz="UTC").normalize()
    start_dt = end_dt - pd.DateOffset(months=3) - pd.Timedelta(hours=336)
    try:
        from data.loader import load_range
        df = load_range(str(start_dt.date()), str(end_dt.date()))
    except Exception as e:
        result        = {"error": str(e), "model": None, "records": [], "mode": "error"}
        forecast_block = f"데이터 로드 실패: {e}"
        return _make_response(state, question, history_block, forecast_block, result)

    # ── 과거 기간 질문 ──────────────────────────────────────────
    start_str, end_str = _parse_date_range(question)
    if start_str and not _is_future_question(question):
        # 해당 기간까지 데이터 다시 로드
        try:
            df_hist = load_range(
                str((pd.Timestamp(start_str) - pd.Timedelta(hours=400)).date()),
                end_str,
            )
        except Exception:
            df_hist = df

        result = _run_vmd_lstm_historical(df_hist, start_str, end_str)
        if result["error"]:
            forecast_block = f"예측 실패: {result['error']}"
        else:
            forecast_block = _summarize_historical(
                result["records"], start_str, end_str, result["model"]
            )
        return _make_response(state, question, history_block, forecast_block, result)

    # ── 미래 예측 질문 ──────────────────────────────────────────
    hours  = _parse_horizon(question)
    result = _run_vmd_lstm_future(df, hours)
    if result["error"]:
        result = _run_future_fallback(df, hours)

    if result["error"]:
        forecast_block = f"예측 실패: {result['error']}"
    else:
        forecast_block = _summarize_future(result["records"], hours, result["model"])

    return _make_response(state, question, history_block, forecast_block, result)


def _make_response(state, question, history_block, forecast_block, result):
    mode = result.get("mode", "future")
    if mode == "historical":
        task_instruction = """예측 수치와 실측값을 비교하여 다음을 설명하세요:
1. 예측 정확도 (MAE, 주요 오차 구간)
2. 실측 피크와 예측 피크 비교
3. 오차가 큰 시간대의 특징 및 가능한 원인
4. 실제 운영에 유용한 시사점"""
    else:
        task_instruction = """예측 수치를 기반으로 다음을 설명하세요:
1. 예측 기간 평균·피크 소비량 요약
2. 피크 시간대 및 주의사항
3. 실제 운영에 유용한 권고사항 (설비 가동 일정 조정 등)
모델이 없으면 학습 방법을 안내하세요."""

    prompt = f"""당신은 에너지 소비 예측 전문 AI입니다.
시설: Honda R&D Europe GmbH, 독일 오펜바흐. 전력망: 독일 공공 전력망.
용어: "계통 전력" 사용 (한전·수전량 등 한국 용어 사용 금지).
{history_block}

## 예측 결과
{forecast_block}

## 사용자 질문
{question}

{task_instruction}"""

    return {
        **state,
        "rag_answer":      llm_chat([{"role": "user", "content": prompt}], max_tokens=1024),
        "forecast_result": result,
    }


def langgraph_node(state: dict) -> dict:
    return run(state)
