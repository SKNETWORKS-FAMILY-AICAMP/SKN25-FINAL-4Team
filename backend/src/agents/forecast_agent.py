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
sys.path.insert(0, str(Path(__file__).parent.parent / "knowledge"))

from domain_knowledge import KFEMS_STANDARD_TERMS, FORECAST_RECOMMENDATION_PROMPT


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
    """XGBoost 폴백 예측."""
    try:
        from models.forecasting.xgboost_model import predict
        fc = predict(df, hours=hours)
        records = [
            {"ts": str(r["ts"])[:16], "yhat_kw": round(float(r["yhat"]) / 1000, 1)}
            for _, r in fc.iterrows()
        ]
        return {"error": None, "model": "xgboost", "records": records}
    except FileNotFoundError:
        return {"error": "XGBoost 모델 파일 없음 (POST /forecast/train/xgboost 먼저 실행)", "model": None, "records": []}
    except Exception as e:
        return {"error": str(e), "model": None, "records": []}


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


def _compute_future_hints(records: list[dict]) -> str:
    """미래 예측 시계열에서 결정론적 운영 힌트를 추출."""
    if not records:
        return "(힌트 없음)"
    vals = [r["yhat_kw"] for r in records]
    avg, peak, low = sum(vals) / len(vals), max(vals), min(vals)
    peak_idx = vals.index(peak)
    peak_ts = records[peak_idx]["ts"]
    peak_ratio = peak / avg if avg > 0 else 0

    lines = [f"- 피크/평균 비율: {peak_ratio:.2f}배 (피크 시각: {peak_ts})"]

    # 피크 직전 램프(상승) 구간 — 피크 3시간 전부터 증가량
    if peak_idx >= 3:
        ramp_start = records[peak_idx - 3]
        ramp_delta = peak - ramp_start["yhat_kw"]
        if ramp_delta > 0:
            lines.append(
                f"- 피크 직전 램프: {ramp_start['ts']} → {peak_ts}, +{ramp_delta:.1f} kW 상승"
            )

    # 야간 시간대(00~05시) 평균 vs 주간(08~18시) 평균
    night, day = [], []
    for r in records:
        try:
            hour = int(str(r["ts"])[11:13])
        except (ValueError, IndexError):
            continue
        if 0 <= hour <= 5:
            night.append(r["yhat_kw"])
        elif 8 <= hour <= 18:
            day.append(r["yhat_kw"])
    if night and day:
        ratio = sum(night) / len(night) / (sum(day) / len(day))
        if ratio > 0.7:
            lines.append(
                f"- 야간/주간 비율: {ratio:.2f} (야간 부하가 주간 대비 70% 이상 → 대기전력 점검 권고)"
            )

    # 최저 시간대 — ESS 충전 / 부하 시프트 후보
    low_ts = records[vals.index(low)]["ts"]
    lines.append(f"- 최저 부하 시각: {low_ts} ({low:.1f} kW) — 부하 시프트·ESS 충전 후보 구간")

    return "\n".join(lines)


def _compute_historical_hints(records: list[dict]) -> str:
    """과거 예측 vs 실측 비교에서 운영 힌트를 추출."""
    if not records:
        return "(힌트 없음)"
    errors_abs = [(abs(r["error_kw"]), r) for r in records]
    errors_abs.sort(key=lambda x: -x[0])
    top_err = errors_abs[:3]

    lines = ["오차 상위 3구간 (모델 재학습 또는 미터 점검 후보):"]
    for err, r in top_err:
        sign = "과소예측" if r["error_kw"] > 0 else "과대예측"
        lines.append(
            f"- {r['ts']}: 실측 {r['actual_kw']:.1f} / 예측 {r['predicted_kw']:.1f} kW "
            f"({sign} {err:.1f} kW)"
        )

    # MAPE 비슷한 척도 (실측 평균 대비 MAE 비율)
    actual_avg = sum(r["actual_kw"] for r in records) / len(records)
    mae = sum(abs(r["error_kw"]) for r in records) / len(records)
    if actual_avg > 0:
        lines.append(
            f"- 평균 오차율: {mae/actual_avg*100:.1f}% (실측 평균 {actual_avg:.1f} kW 대비 MAE {mae:.1f} kW)"
        )
    return "\n".join(lines)


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
    # 시뮬레이터가 활성이면 sim_now를 '지금'으로 사용 (data leakage 방지)
    try:
        from api.routers.simulator import effective_now
        now_dt = pd.Timestamp(effective_now()).tz_localize("UTC")
    except Exception:
        now_dt = pd.Timestamp.now(tz="UTC")
    end_dt   = now_dt.normalize()
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
    records = result.get("records") or []

    if mode == "historical":
        hints_block = _compute_historical_hints(records)
        scenario_note = (
            "과거 기간 예측-실측 비교입니다. 오차 분석을 통해 모델 신뢰도와 "
            "설비 운영 패턴 변화를 진단하세요."
        )
    else:
        hints_block = _compute_future_hints(records) if records else "(힌트 없음)"
        scenario_note = (
            "미래 예측 시계열입니다. 운영자가 오늘/내일 바로 행동할 수 있는 "
            "에너지 최적화 운전 관점의 권고를 작성하세요."
        )

    prompt = f"""당신은 EMS Agent — 공장 에너지 운영을 돕는 AI 코파일럿입니다.
시설: Honda R&D Europe GmbH, 독일 오펜바흐. 전력망: 독일 공공 전력망.
용어: "계통 전력" 사용 (한전·수전량 등 한국 용어 사용 금지).

{KFEMS_STANDARD_TERMS}
{history_block}

## 예측 결과 요약
{forecast_block}

## 운영 힌트 (결정론적 계산값 — 답변에 반드시 인용)
{hints_block}

## 사용자 질문
{question}

## 시나리오
{scenario_note}

{FORECAST_RECOMMENDATION_PROMPT}

마지막 줄에는 모델이 없다는 결과가 들어오면 학습 API(POST /forecast/train/xgboost)를 안내하세요."""

    rag_ans = llm_chat([{"role": "user", "content": prompt}], max_tokens=1200)
    
    # 시연용: 프론트엔드 동적 렌더링을 위한 마크다운 태그 주입
    if not result.get("error"):
        rag_ans += "\n\n[CHART:FORECAST]"

    return {
        **state,
        "rag_answer":      rag_ans,
        "forecast_result": result,
    }


def langgraph_node(state: dict) -> dict:
    return run(state)
