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
from domain_knowledge import ANOMALY_DOMAIN_PROMPT, ANOMALY_RECOMMENDATION_PROMPT

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

    # N주 전 / N주일 동안
    m = re.search(r"(\d+)\s*주(일)?\s*(전|이내|동안)", q)
    if m:
        weeks = int(m.group(1))
        return (now - timedelta(weeks=weeks)).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")

    # N개월 전 / N달 동안
    m = re.search(r"(\d+)\s*(개월|달)\s*(전|이내|동안)", q)
    if m:
        months = int(m.group(1))
        return (now - timedelta(days=months * 30)).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")

    return None, None


def _run_ml_anomaly(start: str, end: str) -> list[dict]:
    """VMD-LSTM 잔차 + IF 실시간 이상탐지 (ML 팀 residual 방식)."""
    try:
        from models.anomaly.residual_model import predict_anomaly, is_available
        if not is_available():
            return []

        import pandas as pd
        from data.loader import load_range
        df = load_range(
            str((pd.Timestamp(start) - pd.Timedelta(hours=400)).date()),
            end,
        )
        if df.empty:
            return []

        an = predict_anomaly(df, start, end)
        if an.empty:
            return []

        an = an[an["anomaly_level"] != "NORMAL"]
        return [
            {
                "timestamp":   str(r["ts"])[:16],
                "type":        "residual+IF",
                "severity":    r["anomaly_level"],
                "description": (
                    f"잔차 {r['residual_w']/1000:.1f} kW "
                    f"(임계치 초과: {'예' if r['res_flag'] else '아니오'}, "
                    f"IF 플래그: {'예' if r['if_flag'] else '아니오'})"
                ),
                "votes": int(r["vote"]),
            }
            for _, r in an.iterrows()
        ]
    except Exception:
        return []


def _count_anomalies_by_type(start: str | None, end: str | None) -> dict:
    """기간 내 anomaly_type별 전체 건수 집계 (severity 무관)."""
    try:
        conn = psycopg2.connect(DB_URL)
        try:
            cur = conn.cursor()
            conds, params = [], []
            if start:
                conds.append("timestamp >= %s"); params.append(start)
            if end:
                conds.append("timestamp <  %s"); params.append(end)
            where = ("WHERE " + " AND ".join(conds)) if conds else ""
            cur.execute(f"""
                SELECT anomaly_type, severity, COUNT(*)
                FROM anomaly_results {where}
                GROUP BY anomaly_type, severity
                ORDER BY COUNT(*) DESC;
            """, params)
            result: dict = {}
            for atype, sev, cnt in cur.fetchall():
                if atype not in result:
                    result[atype] = {"total": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
                result[atype]["total"] += cnt
                result[atype][sev] = result[atype].get(sev, 0) + cnt
            return result
        finally:
            conn.close()
    except Exception:
        return {}


def _fetch_anomalies(limit: int = 20,
                     start: str | None = None,
                     end:   str | None = None,
                     exclude_gateway: bool = True) -> list[dict]:
    """anomaly_results 테이블 조회. 게이트웨이 장애 구간은 기본 제외."""
    try:
        conn = psycopg2.connect(DB_URL)
        try:
            cur  = conn.cursor()

            conds, params = ["severity IN ('HIGH','MEDIUM')"], []
            if start:
                conds.append("timestamp >= %s"); params.append(start)
            if end:
                conds.append("timestamp <  %s"); params.append(end)
            if exclude_gateway:
                conds.append("(gateway_failure IS NULL OR gateway_failure = FALSE)")
            # 시뮬 활성 시: sim_now 이후 데이터는 챗봇에서도 안 보이게
            try:
                from api.routers.simulator import clock, SIM_START_DEFAULT
                sim_now = clock.now
                if sim_now > SIM_START_DEFAULT:
                    conds.append("timestamp <= %s")
                    params.append(sim_now.strftime("%Y-%m-%d %H:%M:%S"))
            except Exception:
                pass

            where = "WHERE " + " AND ".join(conds)
            cur.execute(f"""
                SELECT timestamp, meter_id, anomaly_type, severity, description,
                       vote_count, actual_w, predicted_w, residual_w
                FROM anomaly_results
                {where}
                ORDER BY timestamp DESC
                LIMIT %s;
            """, params + [limit])
            rows = cur.fetchall()
            return [
                {"timestamp": r[0], "meter_id": r[1], "type": r[2],
                 "severity": r[3], "description": r[4], "votes": r[5],
                 "actual_w": r[6], "predicted_w": r[7], "residual_w": r[8]}
                for r in rows
            ]
        finally:
            conn.close()
    except Exception as e:
        print(f"[AnomalyAgent] DB 조회 실패: {e}")
        return []


def _enrich_with_sensors(anomalies: list[dict], df) -> list[dict]:
    """이상 목록에 해당 시점의 실제 센서값을 추가한다."""
    import pandas as pd
    if df is None or df.empty:
        return anomalies
    try:
        d = df.copy()
        d["_ts"] = pd.to_datetime(d["ts"]).dt.tz_localize(None).dt.floor("h")
        d = d.set_index("_ts")
        for a in anomalies:
            try:
                ts = pd.to_datetime(str(a["timestamp"])[:16]).floor("h")
                if ts in d.index:
                    row = d.loc[ts]
                    # 중복 인덱스면 첫 행 사용
                    if hasattr(row, "iloc"):
                        row = row.iloc[0]
                    a["sensors"] = {
                        "grid_kw":  round(float(row.get("grid_P", 0) or 0) / 1000, 1),
                        "pv_kw":    round(float(row.get("pv_P",   0) or 0) / 1000, 1),
                        "chp_kw":   round(float(row.get("chp_P",  0) or 0) / 1000, 1),
                        "cop":      round(float(row.get("cop",     0) or 0), 2),
                        "temp_c":   round(float(row.get("Ta",      0) or 0), 1),
                    }
            except Exception:
                pass
    except Exception:
        pass
    return anomalies


def _load_sensor_df_for_anomalies(anomalies: list[dict]):
    """DB 경로 이상 목록의 시간 범위에 해당하는 센서 데이터 로드."""
    if not anomalies:
        return None
    try:
        import pandas as pd
        from data.loader import load_range
        timestamps = [str(a["timestamp"])[:10] for a in anomalies]
        t_min = min(timestamps)
        t_max = max(timestamps)
        # 범위가 너무 크면 스킵 (>90일)
        if (pd.Timestamp(t_max) - pd.Timestamp(t_min)).days > 90:
            return None
        return load_range(t_min, t_max + "T23:59:59")
    except Exception:
        return None





# ── 메인 실행 ────────────────────────────────────────────────────

def run(state: dict) -> dict:

    question = state.get("question", "")

    # 날짜 파싱
    start, end = _parse_date_range(question)

    # ── 이상탐지: DB 타입별 이상 우선, ML residual+IF 보완 ─────────
    # DB에 PowerSpike/ResidualSpike 등 타입별 이상이 저장되므로 항상 먼저 조회
    ml_source = False
    sensor_df = None
    if start and end:
        db_anomalies = _fetch_anomalies(limit=200, start=start, end=end)
        if db_anomalies:
            recent = db_anomalies
            sensor_df = _load_sensor_df_for_anomalies(recent)
        else:
            # DB 결과 없을 때만 ML 사용
            recent = _run_ml_anomaly(start, end)
            if recent:
                ml_source = True
                try:
                    import pandas as pd
                    from data.loader import load_range
                    sensor_df = load_range(
                        str((pd.Timestamp(start) - pd.Timedelta(hours=2)).date()), end
                    )
                except Exception:
                    pass
            else:
                recent = []
    else:
        # 날짜 미지정: DB 최신 20건 (ML은 기간 필수)
        recent = _fetch_anomalies(limit=20)
        sensor_df = _load_sensor_df_for_anomalies(recent)

    # 센서값 주입
    if sensor_df is not None:
        recent = _enrich_with_sensors(recent, sensor_df)

    # 날짜 표시
    period_str = f"{start} ~ {end}" if start else "최근 20건"
    source_str = "VMD-LSTM 잔차+IF (실시간)" if ml_source else "anomaly_results DB"

    # 전체 건수 집계 (severity 무관 — 카운트 질문 대응, ML 경로에도 항상 DB에서 집계)
    type_counts = _count_anomalies_by_type(start, end) if start else {}
    if type_counts:
        count_lines = []
        grand_total = sum(v["total"] for v in type_counts.values())
        for atype, info in sorted(type_counts.items(), key=lambda x: -x[1]["total"]):
            count_lines.append(
                f"  {atype}: 총 {info['total']}건 "
                f"(HIGH {info.get('HIGH',0)} / MEDIUM {info.get('MEDIUM',0)} / LOW {info.get('LOW',0)})"
            )
        count_block = f"## 이상 유형별 전체 건수 (severity 전체 포함, 합계 {grand_total}건)\n" + "\n".join(count_lines)
    else:
        count_block = ""

    def _fmt_anomaly(r: dict) -> str:
        ts  = str(r["timestamp"])[:16]
        typ = r.get("type", "Unknown")
        sev = r["severity"]
        votes = r.get("votes", "?")
        desc = r.get("description", "")
        line = f"- [{sev}|{votes}표] {ts} | {typ}: {desc}"
        s = r.get("sensors")
        if s:
            line += (
                f"\n    센서: 계통 {s['grid_kw']}kW / PV {s['pv_kw']}kW / "
                f"CHP {s['chp_kw']}kW / COP {s['cop']} / 기온 {s['temp_c']}°C"
            )
        return line

    anomaly_block = (
        "\n".join(_fmt_anomaly(r) for r in recent)
        if recent else "해당 기간 탐지된 이상 없음"
    )

    # 대화 히스토리
    history_lines = []
    for m in (state.get("messages") or [])[-6:]:
        role = "사용자" if m.__class__.__name__ == "HumanMessage" else "AI"
        history_lines.append(f"{role}: {m.content}")
    history_block = ("\n## 이전 대화\n" + "\n".join(history_lines)) if history_lines else ""

    prompt = f"""당신은 EMS Agent — 에너지 시설 이상을 감지하고 운영자에게 조치를 안내하는 AI입니다.
시설: Honda R&D Europe GmbH, 독일 오펜바흐. 전력망: 독일 공공 전력망.
질문 언어와 관계없이 항상 한국어로만 답변하세요.
전력 용어: "계통 전력" 또는 "외부 계통 전력"만 사용 (한전·수전량 등 한국 용어 사용 금지).
{history_block}

## 조회 기간: {period_str}  (출처: {source_str})
{count_block}
【중요】건수를 답할 때는 반드시 위 "총 N건"의 숫자를 사용하세요. 아래 상세 목록은 표본이므로 건수 계산에 사용하지 마세요.

## 이상탐지 상세 샘플 ({len(recent)}건, HIGH/MEDIUM 우선 표시)
각 항목 아래 "센서:" 줄이 있으면 해당 시각의 실측값입니다.
{anomaly_block}

## 시설 이벤트 참조
{_REGIME_EVENTS}
{_GATEWAY_FAILURES}
※ 게이트웨이 장애 구간의 이상은 인공 보정 데이터이므로 반드시 명시하세요.

{ANOMALY_DOMAIN_PROMPT}

## 사용자 질문
{question}

{ANOMALY_RECOMMENDATION_PROMPT}"""

    # ── few-shot: 이상적인 이상탐지 분석 형식 예시 ──
    fs_user = (
        "## 이상탐지 결과 (4건)\n"
        "- 2023-11-12 10:00 MEDIUM PowerSpike\n"
        "- 2023-11-15 14:00 MEDIUM PowerSpike\n"
        "- 2023-11-20 09:00 MEDIUM PowerSpike\n"
        "- 2023-11-22 11:00 MEDIUM PowerSpike\n\n"
        "## 사용자 질문\n2023년 11월의 PowerSpike 발생 건수를 분석하고 관리 방안을 추천해 주세요."
    )
    fs_assistant = (
        "### 🚨 핵심 요약\n"
        "2023년 11월에 총 4건의 PowerSpike가 발생했으며, 모두 MEDIUM 등급입니다. "
        "대형 설비의 동시 기동이 주원인으로 판단됩니다.\n\n"
        "### 🔍 유형별 분석\n"
        "**[PowerSpike] 4건 (MEDIUM: 4)**\n"
        "- 대표 시각: 2023-11-12 10:00\n"
        "- 추정 원인: 대형 설비 동시 기동에 의한 부하 급증 가능성\n"
        "- ⬆ 부하 분산 절차 개선 필요\n\n"
        "### ✅ 즉시 조치 목록\n"
        "1. 설비 스케줄 확인 후 동시 기동 설비를 순차 분산\n"
        "2. 역률 개선 콘덴서 작동 상태 점검\n\n"
        "### 📋 배경 참고\n게이트웨이 장애 구간과 무관한 정상 측정 구간입니다."
    )

    answer = llm_chat(
        [
            {"role": "user",      "content": fs_user},
            {"role": "assistant", "content": fs_assistant},
            {"role": "user",      "content": prompt},
        ],
        max_tokens=1024,
    )

    if recent:
        answer += "\n\n[CHART:ANOMALY]"

    return {
        **state,
        "anomaly_result": {"raw": recent, "explanation": answer},
        "rag_answer":     answer,
    }


def langgraph_node(state: dict) -> dict:
    return run(state)
