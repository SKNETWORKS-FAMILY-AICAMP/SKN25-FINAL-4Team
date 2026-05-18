"""
Anomaly Detection Agent.
이상탐지 앙상블 결과를 해석하고 원인을 설명한다.
질문에서 날짜·기간을 파싱해 해당 구간 이상만 조회.
"""

import os
import re
import sys
from datetime import datetime, timedelta

import psycopg2
from llm_client import chat as llm_chat
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "knowledge"))
from ontology_indexer import embed_query

DB_URL    = os.getenv("DATABASE_URL")

# ── 시설 이벤트 컨텍스트 ─────────────────────────────────────────

_REGIME_EVENTS = """
Regime 경계 (설비 변경 이벤트):
- 2019-02-13: CHP Logic 변경 (On/Off → 연속 모듈레이션)
- 2019-06-01: PV Phase 1 설치
- 2020-03-01: COVID-19 (가동 패턴 급변 — 부하 감소)
- 2020-06-01: PV Phase 2 증설 (풀 용량)
- 2020-09-09: Meter Swap (H2.Z35/Z36 → H2.Z351/Z361 교체, 6일 결측)
- 2023-06-01: Heat Modernization (CHP 하절기 가동 패턴 변화)
"""

# 이 구간의 에너지 데이터는 실측값이 아닌 인공 보정값(과거 데이터 복사)임
_GATEWAY_FAILURES = """
게이트웨이 장애 구간 (인공 보정 데이터 — 이상으로 오판 금지):
- 2020-02-13 ~ 2020-03-06: Workshop Gateway Failure #1 (전체 시스템)
- 2020-08-20 ~ 2020-09-17: Emission Lab Gateway Failure (전체 시스템)
- 2021-11-15 ~ 2021-12-10: Distribution Gateway Failure (전체 시스템 + 기상 10% 결측)
- 2022-05-06 ~ 2022-07-14: Workshop Gateway Failure #2 (전체 시스템, 69일)
"""


# ── 날짜 파싱 ────────────────────────────────────────────────────

def _parse_date_range(question: str) -> tuple[str | None, str | None]:
    """
    질문에서 날짜·기간을 추출해 (start_iso, end_iso) 반환.
    파악 불가 시 (None, None) → 최근 30일 사용.
    """
    now = datetime.now()
    q   = question

    # 연도+월: "2022년 7월", "2022-07"
    m = re.search(r"(\d{4})[년\-]?\s*(\d{1,2})월?", q)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        start = datetime(y, mo, 1)
        end   = (start + timedelta(days=32)).replace(day=1)
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    # 연도만: "2022년", "2022년도"
    m = re.search(r"(\d{4})년", q)
    if m:
        y = int(m.group(1))
        return f"{y}-01-01", f"{y+1}-01-01"

    # 월만: "7월", "7월달" — 연도 없으면 가장 최근 해당 월 (DB 데이터 기준 2023년 이하)
    m = re.search(r"(?<!\d)(\d{1,2})월", q)
    if m:
        mo   = int(m.group(1))
        year = now.year if mo <= now.month else now.year - 1
        start = datetime(year, mo, 1)
        end   = (start + timedelta(days=32)).replace(day=1)
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    # 상대 표현
    if re.search(r"오늘|today", q):
        s = now.replace(hour=0, minute=0, second=0)
        return s.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")
    if re.search(r"어제|yesterday", q):
        s = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0)
        e = now.replace(hour=0, minute=0, second=0)
        return s.strftime("%Y-%m-%d"), e.strftime("%Y-%m-%d")
    if re.search(r"이번\s*주|this\s*week", q):
        s = now - timedelta(days=now.weekday())
        return s.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")
    if re.search(r"지난\s*주|last\s*week", q):
        s = now - timedelta(days=now.weekday() + 7)
        e = now - timedelta(days=now.weekday())
        return s.strftime("%Y-%m-%d"), e.strftime("%Y-%m-%d")
    if re.search(r"이번\s*달|이번\s*월|this\s*month", q):
        s = now.replace(day=1)
        return s.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")
    if re.search(r"지난\s*달|저번\s*달|last\s*month", q):
        first_this = now.replace(day=1)
        e = first_this
        s = (first_this - timedelta(days=1)).replace(day=1)
        return s.strftime("%Y-%m-%d"), e.strftime("%Y-%m-%d")

    # N일 전
    m = re.search(r"(\d+)\s*일\s*(전|이내|동안)", q)
    if m:
        days = int(m.group(1))
        return (now - timedelta(days=days)).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")

    return None, None


def _fetch_anomalies(limit: int = 20,
                     start: str | None = None,
                     end:   str | None = None) -> list[dict]:
    """anomaly_results 테이블 조회. 날짜 범위 있으면 필터링."""
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()

        conds, params = ["severity IN ('HIGH','MEDIUM')"], []
        if start:
            conds.append("timestamp >= %s"); params.append(start)
        if end:
            conds.append("timestamp <  %s"); params.append(end)

        where = "WHERE " + " AND ".join(conds)
        cur.execute(f"""
            SELECT timestamp, meter_id, anomaly_type, severity, description, vote_count
            FROM anomaly_results
            {where}
            ORDER BY timestamp DESC
            LIMIT %s;
        """, params + [limit])
        rows = cur.fetchall()
        conn.close()
        return [
            {"timestamp": r[0], "meter_id": r[1], "type": r[2],
             "severity": r[3], "description": r[4], "votes": r[5]}
            for r in rows
        ]
    except Exception:
        return []


def _search_ontology(question: str) -> list[str]:
    """이상 관련 온톨로지 지식 검색."""
    try:
        vec     = embed_query(question)
        vec_str = "[" + ",".join(str(x) for x in vec) + "]"
        conn    = psycopg2.connect(DB_URL)
        cur     = conn.cursor()
        cur.execute("""
            SELECT content FROM ontology_knowledge
            ORDER BY embedding <=> %s::vector
            LIMIT 5;
        """, (vec_str,))
        rows = cur.fetchall()
        conn.close()
        return [r[0] for r in rows]
    except Exception:
        return []


# ── 메인 실행 ────────────────────────────────────────────────────

def run(state: dict) -> dict:

    question = state.get("question", "")

    # 날짜 파싱
    start, end = _parse_date_range(question)
    if start is None:
        # 기본: 최근 30일 (데이터 기간 고려 → DB 기준 최신)
        recent = _fetch_anomalies(limit=20)
    else:
        recent = _fetch_anomalies(limit=50, start=start, end=end)

    ontology = _search_ontology(question)

    # 날짜 표시
    period_str = f"{start} ~ {end}" if start else "최근 20건"

    anomaly_block = (
        "\n".join(
            f"- [{r['severity']}|{r['votes']}표] {str(r['timestamp'])[:16]} | "
            f"{r['type']}: {r['description']}"
            for r in recent
        )
        if recent else "해당 기간 탐지된 이상 없음 (또는 anomaly_results 테이블 미생성)"
    )
    ontology_block = "\n".join(f"- {s}" for s in ontology) or "없음"

    # 대화 히스토리
    history_lines = []
    for m in (state.get("messages") or [])[-6:]:
        role = "사용자" if m.__class__.__name__ == "HumanMessage" else "AI"
        history_lines.append(f"{role}: {m.content}")
    history_block = ("\n## 이전 대화\n" + "\n".join(history_lines)) if history_lines else ""

    prompt = f"""당신은 에너지 시설 이상탐지 전문 분석가입니다.
시설: Honda R&D Europe GmbH, 독일 오펜바흐. 전력망: 독일 공공 전력망.
전력 용어: "계통 전력" 또는 "외부 계통 전력"만 사용 (한전·수전량 등 한국 용어 사용 금지).
{history_block}

## 시설 이벤트 참조
{_REGIME_EVENTS}
{_GATEWAY_FAILURES}
※ 게이트웨이 장애 구간의 이상은 실제 설비 문제가 아닌 인공 보정 데이터 특성일 수 있으므로 반드시 명시하세요.

## 조회 기간: {period_str}
## 이상탐지 결과 ({len(recent)}건, MEDIUM 이상만)
{anomaly_block}

## 관련 도메인 지식
{ontology_block}

## 사용자 질문
{question}

이상의 원인, 영향 범위, 권장 조치를 구체적으로 설명하세요.
수치 단위(W, kWh, °C)를 명시하고, 확실하지 않은 내용은 추측임을 밝히세요.
이상이 없으면 "해당 기간에 주요 이상 탐지 없음"을 먼저 명시하세요."""

    answer = llm_chat([{"role": "user", "content": prompt}], max_tokens=1024)

    return {
        **state,
        "anomaly_result": {"raw": recent, "explanation": answer},
        "rag_answer":     answer,
    }


def langgraph_node(state: dict) -> dict:
    return run(state)
