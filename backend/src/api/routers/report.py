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


def _ensure_monthly_table(conn):
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


@router.post("/aggregate")
async def aggregate_monthly(
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


@router.get("")
async def get_report(months: int = Query(3, ge=1, le=84)):
    """최근 N개월 KPI 리포트 + 냉방-외기온 상관 데이터 조회."""
    try:
        with _db_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT period, total_consumption_kwh, self_sufficiency_pct,
                       avg_cop, anomaly_count, grid_dependency_pct, pv_kwh, chp_kwh
                FROM monthly_report
                ORDER BY period DESC LIMIT %s;
            """, (months,))
            rows = cur.fetchall()
    except Exception as e:
        return {"error": str(e), "items": [], "cooling_vs_temp": []}

    cols = ["period", "total_consumption_kwh", "self_sufficiency_pct",
            "avg_cop", "anomaly_count", "grid_dependency_pct", "pv_kwh", "chp_kwh"]
    items = [dict(zip(cols, r)) for r in rows]

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

    return {"items": items, "cooling_vs_temp": cooling_vs_temp}


# ══════════════════════════════════════════════════════════════════
#  일일 보고서 (Daily Report)
# ══════════════════════════════════════════════════════════════════

def _ensure_daily_table(conn):
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
            generated_by          TEXT DEFAULT 'manual',
            updated_at            TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    conn.commit()


def latest_data_date() -> str | None:
    """ems 데이터에 존재하는 가장 최근 '완전한' 날짜(YYYY-MM-DD)를 반환."""
    try:
        from data.loader import get_data_range
        _, end_dt = get_data_range()
        if end_dt is None:
            return None
        # 마지막 시각이 자정이면 그 전날이 마지막 완전한 하루
        d = end_dt.date()
        if end_dt.hour == 0 and end_dt.minute == 0:
            d = d - timedelta(days=1)
        return d.isoformat()
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


def _generate_daily_summary(kpi: dict) -> str:
    """일일 KPI를 바탕으로 짧은 AI 요약 생성."""
    try:
        from agents.llm_client import chat as llm_chat
    except Exception:
        return ""

    peak_str = (
        f"{kpi['peak_hour']}시 {kpi['peak_kw']:.0f}kW"
        if kpi.get("peak_hour") is not None else "N/A"
    )
    prompt = f"""당신은 에너지 관리 일일 보고서 작성자입니다.
시설: Honda R&D Europe GmbH, 독일 오펜바흐. 전력망: 독일 공공 전력망.
전력 용어는 "계통 전력"만 사용 (한전·수전량 등 한국 용어 금지).

## {kpi['date']} 일일 KPI
- 총 소비: {kpi['total_consumption_kwh']:,.0f} kWh
- 자급률: {kpi.get('self_sufficiency_pct')}%
- 평균 COP: {kpi.get('avg_cop')}
- 그리드 의존도: {kpi.get('grid_dependency_pct')}%
- PV 발전: {kpi['pv_kwh']:,.0f} kWh | CHP 발전: {kpi['chp_kwh']:,.0f} kWh
- 피크: {peak_str}
- 이상탐지: {kpi.get('anomaly_count', 0)}건

위 데이터로 3~4문장의 간결한 일일 요약을 작성하세요.
특이사항(피크 시간대, 자급률·COP의 평소 대비 높낮음, 이상탐지)을 짚어주세요.
참고 기준: 자급률 6년평균 39.6%, COP 중앙값 2.06."""
    try:
        return llm_chat([{"role": "user", "content": prompt}], max_tokens=400).strip()
    except Exception:
        return ""


def build_daily_report(date_str: str, generated_by: str = "manual") -> dict | None:
    """하루치 집계 + 이상탐지 건수 + AI 요약 → daily_report 테이블 upsert 후 반환."""
    kpi = _aggregate_day(date_str)
    if kpi is None:
        return None

    with _db_conn() as conn:
        _ensure_daily_table(conn)
        kpi["anomaly_count"] = _daily_anomaly_count(conn, date_str)
        kpi["ai_summary"]    = _generate_daily_summary(kpi)

        cur = conn.cursor()
        cur.execute("""
            INSERT INTO daily_report
                (date, total_consumption_kwh, self_sufficiency_pct, avg_cop,
                 anomaly_count, grid_dependency_pct, pv_kwh, chp_kwh,
                 peak_hour, peak_kw, hourly_profile, ai_summary, generated_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
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
                generated_by          = EXCLUDED.generated_by,
                updated_at            = NOW();
        """, (
            kpi["date"], kpi["total_consumption_kwh"], kpi["self_sufficiency_pct"],
            kpi["avg_cop"], kpi["anomaly_count"], kpi["grid_dependency_pct"],
            kpi["pv_kwh"], kpi["chp_kwh"], kpi["peak_hour"], kpi["peak_kw"],
            json.dumps(kpi["hourly_profile"]), kpi["ai_summary"], generated_by,
        ))
        conn.commit()

    return kpi


def _fetch_daily(conn, date_str: str) -> dict | None:
    cur = conn.cursor()
    cur.execute("""
        SELECT date, total_consumption_kwh, self_sufficiency_pct, avg_cop,
               anomaly_count, grid_dependency_pct, pv_kwh, chp_kwh,
               peak_hour, peak_kw, hourly_profile, ai_summary, generated_by, updated_at
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
        "ai_summary": r[11], "generated_by": r[12],
        "updated_at": r[13].isoformat() if r[13] else None,
    }


# ── 일일 보고서 엔드포인트 ────────────────────────────────────────

@router.get("/daily/latest-data-date")
async def get_latest_data_date():
    """ems 데이터에 존재하는 가장 최근 완전한 날짜 반환 (UI 기본값·스케줄러 기준)."""
    return {"date": latest_data_date()}


@router.get("/daily/list")
async def list_daily_reports(limit: int = Query(30, ge=1, le=365)):
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
async def get_daily_report(
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
async def aggregate_daily(date: str = Query(..., description="YYYY-MM-DD")):
    """특정 날짜 일일 보고서를 강제 재생성."""
    result = build_daily_report(date, generated_by="manual")
    if result is None:
        return {"error": f"{date} 데이터 없음", "date": date}
    return result


@router.get("/daily/download")
async def download_daily_report(
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
async def get_scheduler_status():
    """일일 보고서 스케줄러 상태 (활성 여부·다음 실행·마지막 실행)."""
    from api.scheduler import scheduler_status
    return scheduler_status()


@router.post("/daily/scheduler/run")
async def trigger_scheduler_now():
    """스케줄러 작업을 즉시 1회 실행 (최신 데이터 날짜 기준)."""
    target = latest_data_date()
    if not target:
        return {"error": "데이터 날짜 확인 불가"}
    result = build_daily_report(target, generated_by="scheduler")
    if result is None:
        return {"error": f"{target} 데이터 없음", "date": target}
    return result
