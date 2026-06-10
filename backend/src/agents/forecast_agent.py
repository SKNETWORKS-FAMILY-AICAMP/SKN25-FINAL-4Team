"""
Forecast Agent — v84 앙상블 모델로 계량기별 전력 예측 후 자연어로 설명.
orchestrator에서 intent='forecast'일 때 호출.
"""

from api.errors import safe_err
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from llm_client import chat as llm_chat
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent))
# /app (도커) 또는 프로젝트 루트 (로컬) — ml/ 패키지 접근용
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from domain_knowledge import KFEMS_STANDARD_TERMS, FORECAST_RECOMMENDATION_PROMPT


# 기본 예측 대상 계량기 (대표 전력 계량기)
DEFAULT_METER_URN = "H2.Z66"
DEFAULT_HORIZON   = 1  # 1h 예측


# ── 날짜 파싱 ─────────────────────────────────────────────────────

def _parse_horizon(question: str) -> int:
    """질문에서 예측 horizon(1 또는 3) 추출. 기본 1."""
    if re.search(r"3\s*시간|three\s*hour", question):
        return 3
    return 1


def _is_future_question(question: str) -> bool:
    return bool(re.search(
        r"앞으로|내일|다음|미래|예측해|예측 해|향후|이번 주|이번\s*주|this\s*week|next|forecast",
        question,
    ))


def _parse_meter_urn(question: str) -> str:
    """질문에서 계량기 URN 추출. 없으면 DEFAULT_METER_URN."""
    from ml.pipeline.common.config import METER_SPECS_BY_URN
    for urn in METER_SPECS_BY_URN:
        if urn in question:
            return urn
    return DEFAULT_METER_URN


# ── 추론 실행 ─────────────────────────────────────────────────────

def _run_v84_forecast(meter_urn: str, horizon: int, raw_df: pd.DataFrame) -> dict:
    """v84 앙상블 추론. 반환: {error, model, records, meter_urn, horizon}"""
    try:
        from ml.pipeline.inference import predict_meter, is_available
        from ml.pipeline.common.config import METER_SPECS_BY_URN

        if meter_urn not in METER_SPECS_BY_URN:
            return {"error": f"알 수 없는 계량기: {meter_urn}", "model": None, "records": []}

        if not is_available(meter_urn, horizon):
            return {
                "error": (
                    f"{meter_urn} 학습 artifacts 없음. "
                    f"먼저 학습을 실행하세요:\n"
                    f"  .venv-train/bin/python -m ml.pipeline.train --horizon {horizon}"
                ),
                "model": None,
                "records": [],
            }

        spec = METER_SPECS_BY_URN[meter_urn]
        df = predict_meter(meter_urn, horizon, raw_df, spec)

        records = []
        for _, row in df.iterrows():
            records.append({
                "ts":     str(row["ts"])[:16],
                "yhat_W": round(float(row[f"pred_t_plus_1"]), 1),
                "yhat_kw": round(float(row[f"pred_t_plus_1"]) / 1000, 2),
            })
        return {"error": None, "model": "v84-ensemble", "records": records,
                "meter_urn": meter_urn, "horizon": horizon}

    except FileNotFoundError as e:
        return {"error": safe_err(e), "model": None, "records": []}
    except Exception as e:
        return {"error": safe_err(e), "model": None, "records": []}


# ── 요약 ──────────────────────────────────────────────────────────

def _summarize(records: list[dict], meter_urn: str, horizon: int, model: str) -> str:
    if not records:
        return "예측 데이터 없음"
    vals = [r["yhat_kw"] for r in records]
    avg, peak, low = sum(vals) / len(vals), max(vals), min(vals)
    peak_ts = records[vals.index(peak)]["ts"]
    return (
        f"모델: {model} | 계량기: {meter_urn} | horizon: {horizon}h\n"
        f"- 예측 평균: {avg:.2f} kW\n"
        f"- 피크: {peak:.2f} kW ({peak_ts})\n"
        f"- 최저: {low:.2f} kW\n"
        f"- 예측 시작: {records[0]['ts']}"
    )


def _compute_hints(records: list[dict]) -> str:
    if not records:
        return "(힌트 없음)"
    vals = [r["yhat_kw"] for r in records]
    avg = sum(vals) / len(vals)
    peak = max(vals)
    peak_ts = records[vals.index(peak)]["ts"]
    low = min(vals)
    low_ts = records[vals.index(low)]["ts"]
    lines = [
        f"- 피크: {peak:.2f} kW ({peak_ts})",
        f"- 최저: {low:.2f} kW ({low_ts}) — 부하 시프트 후보",
        f"- 피크/평균 비율: {peak/avg:.2f}배" if avg > 0 else "",
    ]
    return "\n".join(l for l in lines if l)


# ── LangGraph 노드 ────────────────────────────────────────────────

def run(state: dict) -> dict:
    question = state.get("question", "")
    history_lines = []
    for m in (state.get("messages") or [])[-6:]:
        role = "사용자" if m.__class__.__name__ == "HumanMessage" else "AI"
        history_lines.append(f"{role}: {m.content}")
    history_block = ("\n## 이전 대화\n" + "\n".join(history_lines)) if history_lines else ""

    meter_urn = _parse_meter_urn(question)
    horizon   = _parse_horizon(question)

    # 최근 데이터 로드 (입력 윈도우 168h + 파생변수 여유분)
    try:
        from api.routers.simulator import effective_now
        now_dt = pd.Timestamp(effective_now()).tz_localize("UTC")
    except Exception:
        now_dt = pd.Timestamp.now(tz="UTC")

    end_dt   = now_dt.normalize()
    start_dt = end_dt - pd.Timedelta(hours=200)

    try:
        from data.loader import load_range
        # 계량기 원시 데이터는 inference가 직접 DB 조회하므로 여기선 연결 테스트만
        raw_df = None
    except Exception as e:
        raw_df = None

    # inference는 DB에서 직접 조회
    try:
        from ml.pipeline.common.db import build_engine, fetch_meter_window
        from ml.pipeline.common.config import METER_SPECS_BY_URN
        engine = build_engine()
        spec   = METER_SPECS_BY_URN.get(meter_urn)
        if spec:
            raw_df = fetch_meter_window(engine, spec, end_ts=now_dt, window_hours=200)
    except Exception as e:
        result = {"error": f"DB 조회 실패: {e}", "model": None, "records": []}
        return _make_response(state, question, history_block, "(데이터 조회 실패)", result)

    result = _run_v84_forecast(meter_urn, horizon, raw_df)

    if result["error"]:
        forecast_block = f"예측 실패: {result['error']}"
    else:
        forecast_block = _summarize(result["records"], meter_urn, horizon, result["model"])

    return _make_response(state, question, history_block, forecast_block, result)


def _make_response(state, question, history_block, forecast_block, result):
    records = result.get("records") or []
    hints_block = _compute_hints(records) if records else "(힌트 없음)"

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

{FORECAST_RECOMMENDATION_PROMPT}

학습 artifacts가 없다는 오류가 오면 학습 실행 명령어를 안내하세요:
  .venv-train/bin/python -m ml.pipeline.train --horizon 1"""

    rag_ans = llm_chat([{"role": "user", "content": prompt}], max_tokens=1200)

    if not result.get("error"):
        rag_ans += "\n\n[CHART:FORECAST]"

    return {
        **state,
        "rag_answer":      rag_ans,
        "forecast_result": result,
    }


def langgraph_node(state: dict) -> dict:
    return run(state)
