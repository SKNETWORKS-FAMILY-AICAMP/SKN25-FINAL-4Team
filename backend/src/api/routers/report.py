import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from fastapi import APIRouter, Query
from fastapi.responses import Response

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
load_dotenv()

router = APIRouter(prefix="/report", tags=["report"])

from api.db import get_conn as _db_conn  # noqa: E402


_monthly_table_ready = False

def _ensure_monthly_table(conn):
    global _monthly_table_ready
    if _monthly_table_ready:
        return
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS monthly_report (
            period               TEXT PRIMARY KEY,
            total_consumption_kwh FLOAT,
            self_sufficiency_pct  FLOAT,
            avg_cop               FLOAT,
            anomaly_count         INT,
            grid_dependency_pct   FLOAT,
            pv_kwh                FLOAT,
            chp_kwh               FLOAT,
            updated_at            TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    conn.commit()
    _monthly_table_ready = True


@router.post("/aggregate")
def aggregate_monthly(
    start: str = Query(..., description="예: 2022-01-01"),
    end:   str = Query(..., description="예: 2023-01-01"),
):
    """ems 데이터를 월별로 집계해 monthly_report 테이블에 저장."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from data.loader import load_range

    df = load_range(start, end)
    if df.empty:
        return {"error": "데이터 없음", "inserted": 0}

    df["ts"] = pd.to_datetime(df["ts"])
    df["period"] = df["ts"].dt.to_period("M").astype(str)

    # 월별 집계
    def _agg(g):
        hours = len(g)
        kwh   = lambda col: g[col].sum(skipna=True) / 1000  # W·h → kWh
        total = g["grid_P"].sum(skipna=True) + g["pv_P"].sum(skipna=True) + g["chp_P"].sum(skipna=True)
        local_total = total if total > 0 else float("nan")
        return pd.Series({
            "total_consumption_kwh": kwh("grid_P") + kwh("pv_P") + kwh("chp_P"),
            "self_sufficiency_pct":  (g["pv_P"].sum(skipna=True) + g["chp_P"].sum(skipna=True)) / local_total * 100,
            "avg_cop":               g["cop"].mean(skipna=True),
            "grid_dependency_pct":   g["grid_P"].sum(skipna=True) / local_total * 100,
            "pv_kwh":                kwh("pv_P"),
            "chp_kwh":               kwh("chp_P"),
        })

    monthly = df.groupby("period").apply(_agg).reset_index()

    # 커넥션 하나로 이상탐지 건수 조회 + 저장 통합
    inserted = 0
    try:
        with _db_conn() as conn:
            _ensure_monthly_table(conn)
            cur = conn.cursor()
            cur.execute("SELECT TO_CHAR(timestamp, 'YYYY-MM'), COUNT(*) FROM anomaly_results GROUP BY 1;")
            anomaly_counts = {r[0]: r[1] for r in cur.fetchall()}
            for _, row in monthly.iterrows():
                period = str(row["period"])
                cur.execute("""
                    INSERT INTO monthly_report
                        (period, total_consumption_kwh, self_sufficiency_pct, avg_cop,
                         anomaly_count, grid_dependency_pct, pv_kwh, chp_kwh)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (period) DO UPDATE SET
                        total_consumption_kwh = EXCLUDED.total_consumption_kwh,
                        self_sufficiency_pct  = EXCLUDED.self_sufficiency_pct,
                        avg_cop               = EXCLUDED.avg_cop,
                        anomaly_count         = EXCLUDED.anomaly_count,
                        grid_dependency_pct   = EXCLUDED.grid_dependency_pct,
                        pv_kwh                = EXCLUDED.pv_kwh,
                        chp_kwh               = EXCLUDED.chp_kwh,
                        updated_at            = NOW();
                """, (
                    period,
                    float(row["total_consumption_kwh"]) if pd.notna(row["total_consumption_kwh"]) else None,
                    float(row["self_sufficiency_pct"])  if pd.notna(row["self_sufficiency_pct"])  else None,
                    float(row["avg_cop"])               if pd.notna(row["avg_cop"])               else None,
                    anomaly_counts.get(period, 0),
                    float(row["grid_dependency_pct"])   if pd.notna(row["grid_dependency_pct"])   else None,
                    float(row["pv_kwh"])                if pd.notna(row["pv_kwh"])                else None,
                    float(row["chp_kwh"])               if pd.notna(row["chp_kwh"])               else None,
                ))
                inserted += cur.rowcount
            conn.commit()
    except Exception as e:
        return {"error": str(e), "months_inserted": inserted}

    return {"period": f"{start} ~ {end}", "months_inserted": inserted}


def _compute_yoy(items: list[dict]) -> list[dict]:
    """각 아이템에 전년동월 대비 변화율(yoy_*) 추가."""
    period_map = {it["period"]: it for it in items}
    for it in items:
        yr  = int(it["period"][:4])
        mon = it["period"][5:]           # "06"
        yoy = period_map.get(f"{yr-1}-{mon}")
        if yoy and yoy.get("total_consumption_kwh") and it.get("total_consumption_kwh"):
            it["yoy_consumption_pct"] = round(
                (it["total_consumption_kwh"] - yoy["total_consumption_kwh"])
                / yoy["total_consumption_kwh"] * 100, 1
            )
            it["yoy_self_pct"] = round(
                (it.get("self_sufficiency_pct") or 0) - (yoy.get("self_sufficiency_pct") or 0), 1
            )
        else:
            it["yoy_consumption_pct"] = None
            it["yoy_self_pct"]        = None
    return items


def _generate_trend_narrative(items: list[dict]) -> str:
    """최신 월의 전월/전년동월 비교 AI narrative 생성."""
    if len(items) < 2:
        return ""
    try:
        from agents.llm_client import chat as llm_chat
    except Exception:
        return ""

    latest = items[-1]
    prev   = items[-2]

    mom_pct = None
    if prev.get("total_consumption_kwh") and latest.get("total_consumption_kwh"):
        mom_pct = (latest["total_consumption_kwh"] - prev["total_consumption_kwh"]) \
                  / prev["total_consumption_kwh"] * 100

    yoy_period = f"{int(latest['period'][:4]) - 1}-{latest['period'][5:]}"
    yoy = next((it for it in items if it["period"] == yoy_period), None)
    yoy_lines = ""
    if yoy and yoy.get("total_consumption_kwh") and latest.get("total_consumption_kwh"):
        yoy_c = (latest["total_consumption_kwh"] - yoy["total_consumption_kwh"]) \
                / yoy["total_consumption_kwh"] * 100
        yoy_s = (latest.get("self_sufficiency_pct") or 0) - (yoy.get("self_sufficiency_pct") or 0)
        yoy_lines = (
            f"\n- 전년 동월({yoy_period}) 대비 소비량: {yoy_c:+.1f}%"
            f"  자급률: {yoy_s:+.1f}%p"
        )

    prompt = f"""당신은 EMS Agent — 에너지 월간 트렌드 분석 AI입니다.
시설: Honda R&D Europe GmbH, 독일 오펜바흐. 전력 용어는 "계통 전력"만 사용.
기준: 자급률 6년평균 39.6%, COP 중앙값 2.06.

## {latest["period"]} KPI
- 총 소비: {latest.get("total_consumption_kwh", 0):,.0f} kWh
- 자급률: {latest.get("self_sufficiency_pct") or 0:.1f}%
- 평균 COP: {latest.get("avg_cop") or 0:.2f}
- 그리드 의존도: {latest.get("grid_dependency_pct") or 0:.1f}%
- 이상탐지: {latest.get("anomaly_count", 0)}건

## 비교
- 전월({prev["period"]}) 대비 소비량: {f"{mom_pct:+.1f}%" if mom_pct is not None else "데이터 없음"}{yoy_lines}

3~4문장으로 월간 트렌드를 분석하세요.
전월 대비와 전년 동월 대비 변화를 모두 언급하고, 주목할 KPI 변화와 가능한 원인을 짚어주세요."""

    try:
        return llm_chat([{"role": "user", "content": prompt}], max_tokens=350).strip()
    except Exception:
        return ""


@router.get("")
def get_report(
    months:   int  = Query(3, ge=1, le=84),
    skip_ai:  bool = Query(True, description="True면 AI narrative 생성 안 함 (빠른 로드)"),
):
    """최근 N개월 KPI 리포트 + 냉방-외기온 상관 + YoY 비교 + (옵션) AI 트렌드 narrative."""
    # YoY 계산을 위해 요청 기간보다 12개월 더 많이 가져옴
    fetch_limit = months + 12

    # 시뮬 clock 기준 마지막 완전한 월만 표시 (latest_data_date와 동일 로직)
    sim_cutoff_period = None
    try:
        from api.routers.simulator import clock
        sim_now = clock.now
        cutoff_date = sim_now.date() - timedelta(days=1)
        sim_cutoff_period = cutoff_date.strftime("%Y-%m")
    except Exception:
        pass

    try:
        with _db_conn() as conn:
            cur = conn.cursor()
            if sim_cutoff_period:
                cur.execute("""
                    SELECT period, total_consumption_kwh, self_sufficiency_pct,
                           avg_cop, anomaly_count, grid_dependency_pct, pv_kwh, chp_kwh
                    FROM monthly_report
                    WHERE period <= %s
                    ORDER BY period DESC LIMIT %s;
                """, (sim_cutoff_period, fetch_limit))
            else:
                cur.execute("""
                    SELECT period, total_consumption_kwh, self_sufficiency_pct,
                           avg_cop, anomaly_count, grid_dependency_pct, pv_kwh, chp_kwh
                    FROM monthly_report
                    ORDER BY period DESC LIMIT %s;
                """, (fetch_limit,))
            rows = cur.fetchall()
    except Exception as e:
        return {"error": str(e), "items": [], "cooling_vs_temp": [], "trend_narrative": ""}

    cols = ["period", "total_consumption_kwh", "self_sufficiency_pct",
            "avg_cop", "anomaly_count", "grid_dependency_pct", "pv_kwh", "chp_kwh"]
    all_items = [dict(zip(cols, r)) for r in rows]
    all_items.reverse()   # 오래된 것 → 최신 순

    # YoY 계산 (전체 데이터 기준)
    all_items = _compute_yoy(all_items)

    # 응답에 포함할 아이템은 요청 months 개만 (최신 기준)
    items = all_items[-months:]

    # 최신 월 AI narrative — skip_ai=True면 생략 (빠른 로드용, 사용자가 명시적 요청 시 생성)
    trend_narrative = "" if skip_ai else _generate_trend_narrative(all_items)

    # 냉방-외기온 상관 데이터: ems DB에서 월별 avg(cool_output_P), avg(Ta) 조회
    cooling_vs_temp = []
    try:
        from data.loader import load_range
        if items:
            periods = sorted(r["period"] for r in items)
            start_str = periods[0] + "-01"
            # 마지막 period 다음 달까지
            last = periods[-1]
            yr, mo = int(last[:4]), int(last[5:7])
            mo += 1
            if mo > 12:
                mo, yr = 1, yr + 1
            end_str = f"{yr}-{mo:02d}-01"
            df = load_range(start_str, end_str)
            if not df.empty:
                df["ts"] = pd.to_datetime(df["ts"])
                df["period"] = df["ts"].dt.to_period("M").astype(str)
                grp = df.groupby("period").agg(
                    avg_ta=("Ta", "mean"),
                    avg_cool_kw=("cool_output_P", lambda x: x.mean(skipna=True) / 1000),
                ).reset_index()
                grp = grp[grp["period"].isin([r["period"] for r in items])]
                cooling_vs_temp = [
                    {
                        "period":       r["period"],
                        "avg_ta":       round(float(r["avg_ta"]), 1) if pd.notna(r["avg_ta"]) else None,
                        "avg_cool_kw":  round(float(r["avg_cool_kw"]), 1) if pd.notna(r["avg_cool_kw"]) else None,
                    }
                    for _, r in grp.iterrows()
                ]
    except Exception:
        pass  # 데이터 없을 시 빈 배열 반환

    return {"items": items, "cooling_vs_temp": cooling_vs_temp, "trend_narrative": trend_narrative}


# ══════════════════════════════════════════════════════════════════
#  일일 보고서 (Daily Report)
# ══════════════════════════════════════════════════════════════════

_daily_table_ready = False

def _ensure_daily_table(conn):
    global _daily_table_ready
    if _daily_table_ready:
        return
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_report (
            date                  DATE PRIMARY KEY,
            total_consumption_kwh FLOAT,
            self_sufficiency_pct  FLOAT,
            avg_cop               FLOAT,
            anomaly_count         INT,
            grid_dependency_pct   FLOAT,
            pv_kwh                FLOAT,
            chp_kwh               FLOAT,
            peak_hour             INT,
            peak_kw               FLOAT,
            hourly_profile        JSONB,
            ai_summary            TEXT,
            today_actions         TEXT,
            generated_by          TEXT DEFAULT 'manual',
            updated_at            TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    cur.execute("""
        ALTER TABLE daily_report
        ADD COLUMN IF NOT EXISTS today_actions TEXT;
    """)
    conn.commit()
    _daily_table_ready = True


def latest_data_date() -> str | None:
    """
    가장 최근 '완전한' 날짜(YYYY-MM-DD) 반환.
    시뮬레이터가 시작된 적이 있으면 sim_now의 어제 날짜를 반환 (실시간 흐름 모사).
    아니면 DB 마지막 날짜.
    """
    # 1. 시뮬레이터 clock 기준 — 항상 시뮬 현재 시각 기준으로 "어제"를 반환
    #    (미시작 상태 SIM_START_DEFAULT=2023-01-01이면 → 2022-12-31)
    try:
        from api.routers.simulator import clock
        sim_now = clock.now
        return (sim_now.date() - timedelta(days=1)).isoformat()
    except Exception:
        pass

    # 2. DB 마지막 날짜 폴백 — 마지막 행이 당일 일부 데이터일 수 있으므로 항상 하루 빼서 완전한 날짜 반환
    try:
        from data.loader import get_data_range
        _, end_dt = get_data_range()
        if end_dt is None:
            return None
        return (end_dt.date() - timedelta(days=1)).isoformat()
    except Exception:
        return None


def _aggregate_day(date_str: str) -> dict | None:
    """하루치(24시간) 데이터를 집계해 KPI + 시간대별 프로파일 dict 반환."""
    from data.loader import load_range

    day      = datetime.fromisoformat(date_str).date()
    next_day = (day + timedelta(days=1)).isoformat()
    df = load_range(day.isoformat(), next_day, freq="1h")
    if df.empty:
        return None

    df["ts"]   = pd.to_datetime(df["ts"])
    df["hour"] = df["ts"].dt.hour

    def _sum_kwh(col):  # 시간당 평균 W를 24시간 합산 → Wh → kWh
        return float(df[col].sum(skipna=True)) / 1000

    grid_kwh = _sum_kwh("grid_P")
    pv_kwh   = _sum_kwh("pv_P")
    chp_kwh  = _sum_kwh("chp_P")
    total    = grid_kwh + pv_kwh + chp_kwh
    local    = pv_kwh + chp_kwh

    # 시간대별 프로파일
    hourly = []
    for h in range(24):
        row = df[df["hour"] == h]
        if row.empty:
            hourly.append({"hour": h, "grid_kw": None, "pv_kw": None,
                           "chp_kw": None, "total_kw": None, "cop": None})
            continue
        g = float(row["grid_P"].mean(skipna=True)) / 1000
        p = float(row["pv_P"].mean(skipna=True)) / 1000
        c = float(row["chp_P"].mean(skipna=True)) / 1000
        cop_v = row["cop"].mean(skipna=True)
        hourly.append({
            "hour": h,
            "grid_kw":  round(g, 2) if pd.notna(row["grid_P"].mean(skipna=True)) else None,
            "pv_kw":    round(p, 2) if pd.notna(row["pv_P"].mean(skipna=True)) else None,
            "chp_kw":   round(c, 2) if pd.notna(row["chp_P"].mean(skipna=True)) else None,
            "total_kw": round(g + p + c, 2),
            "cop":      round(float(cop_v), 2) if pd.notna(cop_v) else None,
        })

    # 피크 시간
    valid_hours = [h for h in hourly if h["total_kw"] is not None]
    peak = max(valid_hours, key=lambda x: x["total_kw"]) if valid_hours else None

    return {
        "date":                  date_str,
        "total_consumption_kwh": round(total, 1),
        "self_sufficiency_pct":  round(local / total * 100, 1) if total > 0 else None,
        "avg_cop":               round(float(df["cop"].mean(skipna=True)), 2) if pd.notna(df["cop"].mean(skipna=True)) else None,
        "grid_dependency_pct":   round(grid_kwh / total * 100, 1) if total > 0 else None,
        "pv_kwh":                round(pv_kwh, 1),
        "chp_kwh":               round(chp_kwh, 1),
        "peak_hour":             peak["hour"] if peak else None,
        "peak_kw":               peak["total_kw"] if peak else None,
        "hourly_profile":        hourly,
    }


def _daily_anomaly_count(conn, date_str: str) -> int:
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM anomaly_results WHERE DATE(timestamp) = %s;",
            (date_str,),
        )
        return int(cur.fetchone()[0])
    except Exception:
        return 0


def _daily_anomaly_events(conn, date_str: str, limit: int = 50) -> list[dict]:
    """해당 날짜에 발생한 이상 이벤트 목록 (시간순)."""
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, timestamp, meter_id, anomaly_type, severity, description
            FROM anomaly_results
            WHERE DATE(timestamp) = %s
            ORDER BY timestamp ASC
            LIMIT %s;
        """, (date_str, limit))
        rows = cur.fetchall()
    except Exception:
        return []
    cols = ["id", "timestamp", "meter_id", "anomaly_type", "severity", "description"]
    return [
        {**dict(zip(cols, r)), "timestamp": r[1].isoformat() if r[1] else None}
        for r in rows
    ]


def _enrich_events(report: dict) -> dict:
    """보고서 dict에 당일 이상 이벤트 목록을 추가 (조회 시점 기준 최신)."""
    if not report or not report.get("date"):
        return report
    try:
        with _db_conn() as conn:
            report["anomaly_events"] = _daily_anomaly_events(conn, report["date"])
    except Exception:
        report["anomaly_events"] = []
    return report


def _generate_daily_summary(kpi: dict) -> tuple[str, str]:
    """일일 KPI → (ai_summary, today_actions) 두 텍스트 반환."""
    try:
        from agents.llm_client import chat as llm_chat
    except Exception:
        return "", ""

    peak_str = (
        f"{kpi['peak_hour']}시 {kpi['peak_kw']:.0f} kW"
        if kpi.get("peak_hour") is not None else "N/A"
    )
    anomaly_count = kpi.get("anomaly_count", 0)
    anomaly_events = kpi.get("anomaly_events") or []
    anomaly_lines = "\n".join(
        f"  - [{e.get('severity','?')}] {str(e.get('timestamp',''))[:16]} {e.get('anomaly_type','')}: {e.get('description','')}"
        for e in anomaly_events[:5]
    ) if anomaly_events else "  없음"

    prompt = f"""당신은 EMS Agent — 공장 에너지 일일 운영 브리핑을 작성하는 AI입니다.
시설: Honda R&D Europe GmbH, 독일 오펜바흐. 전력망: 독일 공공 전력망.
전력 용어는 "계통 전력"만 사용 (한전·수전량 등 한국 용어 금지).
기준: 자급률 6년평균 39.6%, COP 중앙값 2.06.

## {kpi['date']} 일일 KPI
- 총 소비: {kpi['total_consumption_kwh']:,.0f} kWh
- 자급률: {kpi.get('self_sufficiency_pct')}%  (기준: 39.6%)
- 평균 COP: {kpi.get('avg_cop')}  (기준: 2.06)
- 그리드 의존도: {kpi.get('grid_dependency_pct')}%
- PV 발전: {kpi['pv_kwh']:,.0f} kWh | CHP 발전: {kpi['chp_kwh']:,.0f} kWh
- 피크: {peak_str}
- 이상탐지: {anomaly_count}건
{anomaly_lines}

다음 두 섹션을 정확히 아래 형식으로 출력하세요. 다른 텍스트 없이.

---SUMMARY---
(3~4문장. 당일 운영 상태를 총평. 자급률·COP·이상탐지 중 평소와 다른 점을 짚는다.)

---ACTIONS---
(오늘 운영자가 실행할 체크리스트. 이상탐지 건이 있으면 해당 설비 점검을 포함.
 없으면 "이상 없음 — 정상 운영 유지" 한 줄.
 최대 3개 항목, 각 항목은 "· "(중간점 1개+공백) 으로만 시작. "··"처럼 두 번 쓰지 마세요.)"""

    few_shot_user = (
        "당신은 EMS Agent — 공장 에너지 일일 운영 브리핑을 작성하는 AI입니다.\n"
        "시설: Honda R&D Europe GmbH, 독일 오펜바흐. 전력 용어는 '계통 전력'만 사용.\n\n"
        "## 2024-01-10 일일 KPI\n"
        "- 총 소비: 38,500 kWh\n- 자급률: 42.1%  (기준: 39.6%)\n"
        "- 평균 COP: 2.31  (기준: 2.06)\n- 그리드 의존도: 57.9%\n"
        "- PV 발전: 9,100 kWh | CHP 발전: 7,100 kWh\n"
        "- 피크: 10시 1,520 kW\n- 이상탐지: 0건\n  없음\n\n"
        "다음 두 섹션을 정확히 아래 형식으로 출력하세요. 다른 텍스트 없이.\n\n"
        "---SUMMARY---\n(3~4문장)\n\n---ACTIONS---\n(체크리스트)"
    )
    few_shot_assistant = (
        "---SUMMARY---\n"
        "2024-01-10 일일 총 소비량은 38,500 kWh이며, 자급률 42.1%로 6년 평균(39.6%)을 상회했습니다. "
        "평균 COP 2.31은 중앙값 2.06을 크게 웃돌아 냉방 효율이 양호합니다. "
        "이상탐지 건수는 0건으로 정상 운영 상태입니다.\n\n"
        "---ACTIONS---\n"
        "이상 없음 — 정상 운영 유지"
    )
    try:
        raw = llm_chat(
            [
                {"role": "user",      "content": few_shot_user},
                {"role": "assistant", "content": few_shot_assistant},
                {"role": "user",      "content": prompt},
            ],
            max_tokens=500,
        ).strip()
    except Exception:
        return "", ""

    summary, actions = "", ""
    if "---SUMMARY---" in raw and "---ACTIONS---" in raw:
        parts   = raw.split("---ACTIONS---", 1)
        summary = parts[0].replace("---SUMMARY---", "").strip()
        actions = parts[1].strip()
    else:
        summary = raw
    return summary, actions


def build_daily_report(date_str: str, generated_by: str = "manual", skip_ai: bool = False) -> dict | None:
    """하루치 집계 + 이상탐지 건수 + (옵션) AI 요약 → daily_report 테이블 upsert 후 반환.

    skip_ai=True면 LLM 호출 없이 KPI/프로파일만 저장 (시뮬 워커용 — 비용 절감).
    """
    kpi = _aggregate_day(date_str)
    if kpi is None:
        return None

    with _db_conn() as conn:
        _ensure_daily_table(conn)
        kpi["anomaly_count"]  = _daily_anomaly_count(conn, date_str)
        kpi["anomaly_events"] = _daily_anomaly_events(conn, date_str, limit=5)
        if skip_ai:
            kpi["ai_summary"]    = ""
            kpi["today_actions"] = ""
        else:
            summary, actions     = _generate_daily_summary(kpi)
            kpi["ai_summary"]    = summary
            kpi["today_actions"] = actions

        cur = conn.cursor()
        cur.execute("""
            INSERT INTO daily_report
                (date, total_consumption_kwh, self_sufficiency_pct, avg_cop,
                 anomaly_count, grid_dependency_pct, pv_kwh, chp_kwh,
                 peak_hour, peak_kw, hourly_profile, ai_summary, today_actions, generated_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
            ON CONFLICT (date) DO UPDATE SET
                total_consumption_kwh = EXCLUDED.total_consumption_kwh,
                self_sufficiency_pct  = EXCLUDED.self_sufficiency_pct,
                avg_cop               = EXCLUDED.avg_cop,
                anomaly_count         = EXCLUDED.anomaly_count,
                grid_dependency_pct   = EXCLUDED.grid_dependency_pct,
                pv_kwh                = EXCLUDED.pv_kwh,
                chp_kwh               = EXCLUDED.chp_kwh,
                peak_hour             = EXCLUDED.peak_hour,
                peak_kw               = EXCLUDED.peak_kw,
                hourly_profile        = EXCLUDED.hourly_profile,
                ai_summary            = EXCLUDED.ai_summary,
                today_actions         = EXCLUDED.today_actions,
                generated_by          = EXCLUDED.generated_by,
                updated_at            = NOW();
        """, (
            kpi["date"], kpi["total_consumption_kwh"], kpi["self_sufficiency_pct"],
            kpi["avg_cop"], kpi["anomaly_count"], kpi["grid_dependency_pct"],
            kpi["pv_kwh"], kpi["chp_kwh"], kpi["peak_hour"], kpi["peak_kw"],
            json.dumps(kpi["hourly_profile"]), kpi["ai_summary"], kpi["today_actions"],
            generated_by,
        ))
        conn.commit()

    return kpi


def _fetch_daily(conn, date_str: str) -> dict | None:
    cur = conn.cursor()
    cur.execute("""
        SELECT date, total_consumption_kwh, self_sufficiency_pct, avg_cop,
               anomaly_count, grid_dependency_pct, pv_kwh, chp_kwh,
               peak_hour, peak_kw, hourly_profile, ai_summary, today_actions,
               generated_by, updated_at
        FROM daily_report WHERE date = %s;
    """, (date_str,))
    r = cur.fetchone()
    if not r:
        return None
    return {
        "date": r[0].isoformat(), "total_consumption_kwh": r[1],
        "self_sufficiency_pct": r[2], "avg_cop": r[3], "anomaly_count": r[4],
        "grid_dependency_pct": r[5], "pv_kwh": r[6], "chp_kwh": r[7],
        "peak_hour": r[8], "peak_kw": r[9], "hourly_profile": r[10],
        "ai_summary": r[11], "today_actions": r[12],
        "generated_by": r[13],
        "updated_at": r[14].isoformat() if r[14] else None,
    }


# ── 일일 보고서 엔드포인트 ────────────────────────────────────────

@router.get("/daily/latest-data-date")
def get_latest_data_date():
    """ems 데이터에 존재하는 가장 최근 완전한 날짜 반환 (UI 기본값·스케줄러 기준)."""
    return {"date": latest_data_date()}


@router.get("/daily/list")
def list_daily_reports(limit: int = Query(30, ge=1, le=365)):
    """저장된 일일 보고서 목록 (최근순, 요약 KPI만)."""
    try:
        with _db_conn() as conn:
            _ensure_daily_table(conn)
            cur = conn.cursor()
            cur.execute("""
                SELECT date, total_consumption_kwh, self_sufficiency_pct, avg_cop,
                       anomaly_count, peak_hour, peak_kw, generated_by
                FROM daily_report ORDER BY date DESC LIMIT %s;
            """, (limit,))
            rows = cur.fetchall()
    except Exception as e:
        return {"error": str(e), "items": []}

    cols = ["date", "total_consumption_kwh", "self_sufficiency_pct", "avg_cop",
            "anomaly_count", "peak_hour", "peak_kw", "generated_by"]
    items = [
        {**dict(zip(cols, r)), "date": r[0].isoformat()}
        for r in rows
    ]
    return {"items": items}


@router.get("/daily")
def get_daily_report(
    date: str = Query(..., description="YYYY-MM-DD"),
    regenerate: bool = Query(False, description="true면 저장본 무시하고 재생성"),
):
    """일일 보고서 조회. 저장본이 없으면 즉시 집계·생성."""
    if not regenerate:
        try:
            with _db_conn() as conn:
                _ensure_daily_table(conn)
                cached = _fetch_daily(conn, date)
            if cached:
                return _enrich_events(cached)
        except Exception as e:
            return {"error": str(e)}

    result = build_daily_report(date, generated_by="manual")
    if result is None:
        return {"error": f"{date} 데이터 없음", "date": date}
    return _enrich_events(result)


@router.post("/daily/aggregate")
def aggregate_daily(date: str = Query(..., description="YYYY-MM-DD")):
    """특정 날짜 일일 보고서를 강제 재생성."""
    result = build_daily_report(date, generated_by="manual")
    if result is None:
        return {"error": f"{date} 데이터 없음", "date": date}
    return result


@router.get("/daily/download")
def download_daily_report(
    date: str = Query(..., description="YYYY-MM-DD"),
    format: str = Query("pdf", description="pdf | docx | hwpx"),
):
    """일일 보고서를 문서 파일(PDF/DOCX/HWPX)로 다운로드."""
    from urllib.parse import quote
    from api import report_export

    # 저장본 우선, 없으면 즉시 생성
    report = None
    try:
        with _db_conn() as conn:
            _ensure_daily_table(conn)
            report = _fetch_daily(conn, date)
    except Exception:
        pass
    if report is None:
        report = build_daily_report(date, generated_by="manual")
    if report is None:
        return {"error": f"{date} 데이터 없음", "date": date}
    report = _enrich_events(report)

    try:
        data, media, filename = report_export.render(report, format)
    except ValueError as e:
        return {"error": str(e)}

    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/daily/scheduler")
def get_scheduler_status():
    """일일 보고서 스케줄러 상태 (활성 여부·다음 실행·마지막 실행)."""
    from api.scheduler import scheduler_status
    return scheduler_status()


@router.post("/daily/scheduler/run")
def trigger_scheduler_now():
    """스케줄러 작업을 즉시 1회 실행 (최신 데이터 날짜 기준)."""
    target = latest_data_date()
    if not target:
        return {"error": "데이터 날짜 확인 불가"}
    result = build_daily_report(target, generated_by="scheduler")
    if result is None:
        return {"error": f"{target} 데이터 없음", "date": target}
    return result


# ══════════════════════════════════════════════════════════════════
#  에너지 밸런스 검증 (Energy Balance Verification)
# ══════════════════════════════════════════════════════════════════

# 알려진 게이트웨이 장애 구간 (데이터 신뢰도 하락 기간)
_GATEWAY_FAILURES = [
    ("2020-02", "2020-03"),
    ("2020-08", "2020-09"),
    ("2021-11", "2021-12"),
    ("2022-05", "2022-07"),
]


def _is_gateway_failure(period: str) -> bool:
    """해당 월이 게이트웨이 장애 구간에 포함되는지 확인."""
    for start, end in _GATEWAY_FAILURES:
        if start <= period <= end:
            return True
    return False


def _compute_balance_flags(item: dict) -> dict:
    """단일 월 KPI에서 밸런스 이상 플래그 계산."""
    flags = []
    score = 100  # 0~100, 낮을수록 데이터 품질 문제

    total = item.get("total_consumption_kwh") or 0
    pv    = item.get("pv_kwh") or 0
    chp   = item.get("chp_kwh") or 0
    ss    = item.get("self_sufficiency_pct")
    gd    = item.get("grid_dependency_pct")

    # 1. 게이트웨이 장애 구간
    if _is_gateway_failure(item["period"]):
        flags.append("gateway_failure")
        score -= 40

    # 2. 자급률 + 그리드 의존도 합 검증 (100%에 가까워야)
    if ss is not None and gd is not None:
        balance_sum = ss + gd
        if abs(balance_sum - 100) > 5:
            flags.append(f"ss_gd_mismatch:{balance_sum:.1f}%")
            score -= 20

    # 3. 자급률 재계산 vs 저장값 검증
    if total > 0 and ss is not None:
        computed_ss = (pv + chp) / total * 100
        if abs(computed_ss - ss) > 3:
            flags.append(f"ss_recalc_diff:{computed_ss - ss:+.1f}%p")
            score -= 15

    # 4. 음수 또는 0 소비 이상
    if total <= 0:
        flags.append("zero_consumption")
        score -= 50

    # 5. 비현실적 자급률 (100% 초과)
    if ss is not None and ss > 100:
        flags.append(f"ss_over_100:{ss:.1f}%")
        score -= 30

    # 6. PV 비율 계절 역전 검증 (여름 > 겨울 정상 — 극단적 역전 탐지)
    if total > 0:
        pv_ratio = pv / total * 100
        month = int(item["period"][5:7])
        if month in (12, 1, 2) and pv_ratio > 30:
            flags.append(f"pv_high_in_winter:{pv_ratio:.1f}%")
            score -= 10

    score = max(0, score)
    return {
        "flags":         flags,
        "quality_score": score,
        "quality_label": "정상" if score >= 80 else ("주의" if score >= 50 else "불량"),
    }


def _generate_balance_narrative(items_with_balance: list[dict]) -> str:
    """데이터 품질 AI 요약 생성."""
    try:
        from agents.llm_client import chat as llm_chat
    except Exception:
        return ""

    problem_months = [
        f"  - {it['period']}: {', '.join(it['balance_flags'])} (점수 {it['quality_score']})"
        for it in items_with_balance
        if it.get("quality_score", 100) < 80
    ]
    ok_count      = sum(1 for it in items_with_balance if it.get("quality_score", 100) >= 80)
    total_count   = len(items_with_balance)
    problem_text  = "\n".join(problem_months) if problem_months else "  없음"

    prompt = f"""당신은 EMS Agent — 에너지 데이터 품질 분석 AI입니다.
시설: Honda R&D Europe GmbH, 독일 오펜바흐.

## 분석 대상: {total_count}개월 데이터
- 정상 월: {ok_count}개월
- 문제 월: {total_count - ok_count}개월
{problem_text}

## 알려진 게이트웨이 장애 구간
- 2020-02~03, 2020-08~09, 2021-11~12, 2022-05~07 (인공 보정 데이터)

2~3문장으로 데이터 품질 현황을 요약하세요.
문제 월이 있다면 원인과 분석 시 주의사항을 간략히 짚어주세요."""

    try:
        return llm_chat([{"role": "user", "content": prompt}], max_tokens=250).strip()
    except Exception:
        return ""


@router.get("/balance")
def get_balance_report(
    months:  int  = Query(24, ge=3, le=84),
    skip_ai: bool = Query(True, description="True면 AI narrative 생성 안 함"),
):
    """에너지 밸런스 검증 — 월별 데이터 품질 점수 + (옵션) AI 요약."""
    try:
        with _db_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT period, total_consumption_kwh, self_sufficiency_pct,
                       grid_dependency_pct, pv_kwh, chp_kwh
                FROM monthly_report
                ORDER BY period DESC LIMIT %s;
            """, (months,))
            rows = cur.fetchall()
    except Exception as e:
        return {"error": str(e), "items": [], "narrative": ""}

    cols = ["period", "total_consumption_kwh", "self_sufficiency_pct",
            "grid_dependency_pct", "pv_kwh", "chp_kwh"]
    items = [dict(zip(cols, r)) for r in rows]
    items.reverse()   # 오래된 것 → 최신 순

    # 밸런스 플래그 계산
    for it in items:
        bal = _compute_balance_flags(it)
        it["balance_flags"]  = bal["flags"]
        it["quality_score"]  = bal["quality_score"]
        it["quality_label"]  = bal["quality_label"]

    narrative = "" if skip_ai else _generate_balance_narrative(items)

    return {
        "items":         items,
        "narrative":     narrative,
        "ok_count":      sum(1 for it in items if it["quality_score"] >= 80),
        "warn_count":    sum(1 for it in items if 50 <= it["quality_score"] < 80),
        "bad_count":     sum(1 for it in items if it["quality_score"] < 50),
        "total_months":  len(items),
    }


# ══════════════════════════════════════════════════════════════════
#  외기온 정규화 에너지 원단위 (Energy Intensity — Weather-Normalized)
# ══════════════════════════════════════════════════════════════════

_HDD_BASE = 18.0   # 난방 기준온도 °C
_CDD_BASE = 22.0   # 냉방 기준온도 °C


def _generate_ei_narrative(items: list[dict], ei_avg: float | None) -> str:
    """EI 추이 AI 요약."""
    if not items or ei_avg is None:
        return ""
    try:
        from agents.llm_client import chat as llm_chat
    except Exception:
        return ""

    valid = [it for it in items if it.get("ei_total") is not None]
    if len(valid) < 3:
        return ""

    # 최근 3개월 vs 전체 평균
    recent   = valid[-3:]
    recent_avg = sum(it["ei_total"] for it in recent) / len(recent)
    trend_pct  = (recent_avg - ei_avg) / ei_avg * 100 if ei_avg else 0

    worst  = max(valid, key=lambda x: x["ei_total"])
    best   = min(valid, key=lambda x: x["ei_total"])

    prompt = f"""당신은 EMS Agent — 외기온 정규화 에너지 원단위(EI) 분석 AI입니다.
시설: Honda R&D Europe GmbH, 독일 오펜바흐.

## 분석 결과 (단위: kWh/DD, 낮을수록 에너지 효율 좋음)
- 전체 평균 EI: {ei_avg:.1f} kWh/DD
- 최근 3개월 평균: {recent_avg:.1f} kWh/DD ({trend_pct:+.1f}% vs 전체평균)
- 최고 효율 월: {best['period']} ({best['ei_total']:.1f} kWh/DD, 외기온 {best.get('avg_ta','?')}°C)
- 최저 효율 월: {worst['period']} ({worst['ei_total']:.1f} kWh/DD, 외기온 {worst.get('avg_ta','?')}°C)

날씨 영향을 제거한 실질 에너지 효율 추이를 2~3문장으로 분석하세요.
최근 추세가 개선/악화 중인지, 특이 시기가 있다면 그 원인을 짚어주세요."""

    try:
        return llm_chat([{"role": "user", "content": prompt}], max_tokens=250).strip()
    except Exception:
        return ""


@router.get("/energy-intensity")
def get_energy_intensity(
    months:  int  = Query(24, ge=6, le=84),
    skip_ai: bool = Query(True, description="True면 AI narrative 생성 안 함"),
):
    """외기온 정규화 에너지 원단위 — 날씨 영향 제거 후 실질 효율 추이 + (옵션) AI."""
    # 1. monthly_report KPI 조회
    try:
        with _db_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT period, total_consumption_kwh, pv_kwh, chp_kwh
                FROM monthly_report ORDER BY period DESC LIMIT %s;
            """, (months,))
            rows = cur.fetchall()
    except Exception as e:
        return {"error": str(e), "items": [], "narrative": ""}

    if not rows:
        return {"items": [], "narrative": "", "ei_avg": None}

    monthly_kpi = {
        r[0]: {"total": r[1] or 0.0, "pv": r[2] or 0.0, "chp": r[3] or 0.0}
        for r in rows
    }
    periods = sorted(monthly_kpi.keys())

    # 2. 온도·냉방전력 원시 데이터 로드
    try:
        from data.loader import load_range
        start_str = periods[0] + "-01"
        last      = periods[-1]
        yr, mo    = int(last[:4]), int(last[5:7])
        mo += 1
        if mo > 12:
            mo, yr = 1, yr + 1
        end_str = f"{yr}-{mo:02d}-01"

        df = load_range(start_str, end_str)
        if df.empty:
            return {"items": [], "narrative": "", "ei_avg": None}

        df["ts"]     = pd.to_datetime(df["ts"])
        df["period"] = df["ts"].dt.to_period("M").astype(str)

        def _dd(grp):
            ta = grp["Ta"].dropna()
            if ta.empty:
                return pd.Series({"hdd": None, "cdd": None, "avg_ta": None, "cool_kwh": 0.0})
            hdd      = float(ta.apply(lambda t: max(0.0, _HDD_BASE - t)).sum() / 24)
            cdd      = float(ta.apply(lambda t: max(0.0, t - _CDD_BASE)).sum() / 24)
            avg_ta   = float(ta.mean())
            cool_kwh = float(grp["cool_elec_P"].dropna().sum() / 1000) \
                       if "cool_elec_P" in grp.columns else 0.0
            return pd.Series({"hdd": hdd, "cdd": cdd, "avg_ta": avg_ta, "cool_kwh": cool_kwh})

        dd_monthly = df.groupby("period").apply(_dd).reset_index()

    except Exception as e:
        return {"error": str(e), "items": [], "narrative": "", "ei_avg": None}

    # 3. EI 계산
    items = []
    for _, row in dd_monthly.iterrows():
        period = row["period"]
        if period not in monthly_kpi:
            continue
        kpi      = monthly_kpi[period]
        total    = kpi["total"]
        grid_kwh = max(0.0, total - kpi["pv"] - kpi["chp"])
        hdd, cdd = (row["hdd"] or 0.0), (row["cdd"] or 0.0)
        cool_kwh = row.get("cool_kwh") or 0.0
        avg_ta   = row["avg_ta"]
        dd_total = hdd + cdd

        ei_total         = round(total    / max(dd_total, 1), 1) if dd_total > 5  else None
        ei_grid_per_hdd  = round(grid_kwh / max(hdd, 1),      1) if hdd      > 5  else None
        ei_cool_per_cdd  = round(cool_kwh / max(cdd, 1),      1) if cdd      > 5  else None

        items.append({
            "period":          period,
            "avg_ta":          round(avg_ta, 1)   if avg_ta is not None else None,
            "hdd":             round(hdd, 1),
            "cdd":             round(cdd, 1),
            "dd_total":        round(dd_total, 1),
            "total_kwh":       round(total),
            "ei_total":        ei_total,
            "ei_grid_per_hdd": ei_grid_per_hdd,
            "ei_cool_per_cdd": ei_cool_per_cdd,
        })

    # 4. 전체 평균 + 평균 대비 편차
    ei_values = [it["ei_total"] for it in items if it["ei_total"] is not None]
    ei_avg    = round(sum(ei_values) / len(ei_values), 1) if ei_values else None
    if ei_avg:
        for it in items:
            if it["ei_total"] is not None:
                it["ei_vs_avg"] = round(it["ei_total"] - ei_avg, 1)

    narrative = "" if skip_ai else _generate_ei_narrative(items, ei_avg)

    return {"items": items, "ei_avg": ei_avg, "narrative": narrative}


# ══════════════════════════════════════════════════════════════════
#  요금 관리 (Billing — Cost Target Monitoring)
# ══════════════════════════════════════════════════════════════════

# 독일 산업용 전력 단가 (기본값) — 시연 시 SettingsPanel에서 조정
_DEFAULT_UNIT_PRICE_EUR = 0.20      # EUR / kWh
_DEFAULT_PEAK_PRICE_EUR = 12.0      # EUR / kW (월 계약전력 기본요금)
_DEFAULT_TARGET_EUR     = 50000.0   # 월 목표 비용
_EUR_KRW                = 1450.0    # 표시용 환율 (참고치)


def _generate_billing_narrative(billing: dict) -> str:
    """비용 분석 AI 요약 생성."""
    try:
        from agents.llm_client import chat as llm_chat
    except Exception:
        return ""

    over_pct  = (billing["projected_eur"] - billing["target_eur"]) / billing["target_eur"] * 100 \
                if billing["target_eur"] else 0
    peak_risk = billing.get("peak_risk_pct") or 0
    status    = billing["status"]

    prompt = f"""당신은 EMS Agent — 공장 에너지 비용 분석 AI입니다.
시설: Honda R&D Europe GmbH, 독일 오펜바흐.

## {billing['period']} 비용 현황
- 누적 비용: €{billing['actual_eur']:,.0f}
- 월말 예상: €{billing['projected_eur']:,.0f}
- 목표: €{billing['target_eur']:,.0f}
- 목표 대비: {over_pct:+.1f}%
- 상태: {status}
- 진행률: {billing['days_elapsed']}/{billing['days_in_month']}일
- 피크 위험: {peak_risk:.0f}%
- 최대 피크: {billing.get('max_peak_kw', 0):.0f} kW

2~3문장으로 비용 현황을 분석하세요. 목표 초과 위험이 있으면 구체적 조치(피크 시프트, 야간 부하 분산 등)를 1개 권고하세요."""

    try:
        return llm_chat([{"role": "user", "content": prompt}], max_tokens=300).strip()
    except Exception:
        return ""


@router.get("/billing")
def get_billing(
    month:        str   = Query(None, description="YYYY-MM, 기본은 최신 데이터 월"),
    unit_price:   float = Query(_DEFAULT_UNIT_PRICE_EUR, description="EUR/kWh"),
    peak_price:   float = Query(_DEFAULT_PEAK_PRICE_EUR, description="EUR/kW (월 기본요금)"),
    target_eur:   float = Query(_DEFAULT_TARGET_EUR,    description="월 목표 비용 EUR"),
):
    """월 요금 분석 — 누적/예상 비용 + 피크 위험 + AI 권고."""
    # 1. 분석 대상 월 결정
    if not month:
        latest = latest_data_date()
        if not latest:
            return {"error": "데이터 없음"}
        month = latest[:7]

    # 2. 해당 월 일일 보고서 조회
    try:
        with _db_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT date, total_consumption_kwh, peak_kw, anomaly_count
                FROM daily_report
                WHERE TO_CHAR(date, 'YYYY-MM') = %s
                ORDER BY date;
            """, (month,))
            rows = cur.fetchall()
    except Exception as e:
        return {"error": str(e)}

    # daily_report 부족 시 raw 데이터에서 즉시 계산 (시뮬 진행 속도 따라잡기)
    if not rows or len(rows) < 3:
        try:
            from data.loader import load_range
            yr, mo = int(month[:4]), int(month[5:7])
            start_str = f"{yr}-{mo:02d}-01"
            if mo == 12:
                end_str = f"{yr + 1}-01-01"
            else:
                end_str = f"{yr}-{mo + 1:02d}-01"

            df = load_range(start_str, end_str)
            if df.empty:
                return {"error": f"{month} 데이터 없음", "period": month}

            df["ts"]   = pd.to_datetime(df["ts"])
            df["date"] = df["ts"].dt.date
            daily_agg = df.groupby("date").agg(
                consumption_kwh=("grid_P",
                                 lambda x: (x.sum(skipna=True)
                                            + df.loc[x.index, "pv_P"].sum(skipna=True)
                                            + df.loc[x.index, "chp_P"].sum(skipna=True)) / 1000),
                peak_kw=("grid_P", lambda x: x.max(skipna=True) / 1000),
            ).reset_index()
            daily = [
                {
                    "date":            r["date"].isoformat(),
                    "consumption_kwh": float(r["consumption_kwh"]) if pd.notna(r["consumption_kwh"]) else 0.0,
                    "peak_kw":         float(r["peak_kw"]) if pd.notna(r["peak_kw"]) else 0.0,
                    "cost_eur":        (float(r["consumption_kwh"]) if pd.notna(r["consumption_kwh"]) else 0.0) * unit_price,
                    "anomaly_count":   0,
                }
                for _, r in daily_agg.iterrows()
            ]
        except Exception as e:
            return {"error": f"raw 계산 실패: {e}", "period": month}
    else:
        daily = [
            {
                "date":            r[0].isoformat(),
                "consumption_kwh": float(r[1] or 0),
                "peak_kw":         float(r[2] or 0),
                "cost_eur":        float(r[1] or 0) * unit_price,
                "anomaly_count":   int(r[3] or 0),
            }
            for r in rows
        ]

    # 3. 누적·평균
    actual_kwh    = sum(d["consumption_kwh"] for d in daily)
    actual_eur    = actual_kwh * unit_price
    days_elapsed  = len(daily)
    max_peak_kw   = max((d["peak_kw"] for d in daily), default=0)
    peak_cost_eur = max_peak_kw * peak_price

    # 4. 월말 추정
    from datetime import date as _date, timedelta
    yr, mo = int(month[:4]), int(month[5:7])
    if mo == 12:
        next_first = _date(yr + 1, 1, 1)
    else:
        next_first = _date(yr, mo + 1, 1)
    days_in_month = (next_first - _date(yr, mo, 1)).days

    if days_elapsed > 0:
        avg_daily_kwh   = actual_kwh / days_elapsed
        projected_kwh   = avg_daily_kwh * days_in_month
        projected_eur   = projected_kwh * unit_price + peak_cost_eur
    else:
        projected_kwh = projected_eur = 0

    # 5. 상태 판정
    if projected_eur > target_eur * 1.05:
        status = "초과 위험"
    elif projected_eur > target_eur * 0.95:
        status = "주의"
    else:
        status = "정상"

    # 6. 피크 위험 (최근 7일 평균 vs 전체 평균)
    peak_risk_pct = 0
    if len(daily) >= 7:
        recent_avg_peak = sum(d["peak_kw"] for d in daily[-7:]) / 7
        all_avg_peak    = sum(d["peak_kw"] for d in daily) / days_elapsed
        if all_avg_peak > 0:
            ratio = recent_avg_peak / all_avg_peak
            peak_risk_pct = min(100, max(0, (ratio - 1) * 100 + 50))   # 1.0배=50%, 2.0배=100%

    billing = {
        "period":         month,
        "unit_price_eur": unit_price,
        "peak_price_eur": peak_price,
        "target_eur":     target_eur,
        "actual_kwh":     round(actual_kwh, 1),
        "actual_eur":     round(actual_eur, 0),
        "projected_kwh":  round(projected_kwh, 1),
        "projected_eur":  round(projected_eur, 0),
        "peak_cost_eur":  round(peak_cost_eur, 0),
        "max_peak_kw":    round(max_peak_kw, 1),
        "days_elapsed":   days_elapsed,
        "days_in_month":  days_in_month,
        "status":         status,
        "peak_risk_pct":  round(peak_risk_pct, 0),
        "daily":          daily,
        "krw_rate":       _EUR_KRW,
    }
    billing["narrative"] = _generate_billing_narrative(billing)

    return billing
