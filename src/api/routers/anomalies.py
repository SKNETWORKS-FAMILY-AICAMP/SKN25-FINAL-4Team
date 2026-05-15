import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import psycopg2
from dotenv import load_dotenv
from fastapi import APIRouter, BackgroundTasks, Query

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

load_dotenv()

router = APIRouter(prefix="/anomalies", tags=["anomalies"])
DB_URL = os.getenv("DATABASE_URL")


def _get_conn():
    return psycopg2.connect(DB_URL)


@router.get("")
async def list_anomalies(
    limit:    int           = Query(50, ge=1, le=500),
    offset:   int           = Query(0, ge=0),
    severity: Optional[str] = Query(None, description="HIGH / MEDIUM / LOW"),
    year:     Optional[int] = Query(None, description="연도 필터 예: 2022"),
    month:    Optional[int] = Query(None, description="월 필터 예: 7"),
):
    """이상탐지 결과 조회 (필터·페이지네이션 지원)."""
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        conds, params = [], []
        if severity:
            if severity.upper() == 'MEDIUM+':
                conds.append("severity IN ('HIGH', 'MEDIUM')")
            else:
                conds.append("severity = %s"); params.append(severity.upper())
        if year:
            conds.append("EXTRACT(YEAR  FROM timestamp) = %s"); params.append(year)
        if month:
            conds.append("EXTRACT(MONTH FROM timestamp) = %s"); params.append(month)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""

        # 전체 건수
        cur.execute(f"SELECT COUNT(*) FROM anomaly_results {where}", params)
        total = cur.fetchone()[0]

        cur.execute(f"""
            SELECT id, timestamp, meter_id, anomaly_type, severity, description,
                   score_stat, score_iso, score_lstm, vote_count, created_at
            FROM anomaly_results {where}
            ORDER BY timestamp DESC LIMIT %s OFFSET %s;
        """, params + [limit, offset])
        rows = cur.fetchall()
        conn.close()
    except Exception as e:
        return {"error": str(e), "items": [], "total": 0}

    cols = ["id", "timestamp", "meter_id", "anomaly_type", "severity",
            "description", "score_stat", "score_iso", "score_lstm",
            "vote_count", "created_at"]
    items = [dict(zip(cols, r)) for r in rows]
    for item in items:
        for k in ("timestamp", "created_at"):
            if item[k] and hasattr(item[k], "isoformat"):
                item[k] = item[k].isoformat()
    return {"total": total, "offset": offset, "limit": limit, "items": items}


@router.get("/summary")
async def anomaly_summary():
    """심각도별 건수 요약."""
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute("""
            SELECT severity, COUNT(*) as cnt
            FROM anomaly_results
            GROUP BY severity
            ORDER BY cnt DESC;
        """)
        rows = conn.cursor().fetchall() if False else cur.fetchall()
        conn.close()
        return {"summary": [{"severity": r[0], "count": r[1]} for r in rows]}
    except Exception as e:
        return {"error": str(e), "summary": []}


@router.get("/timeline")
async def anomaly_timeline():
    """월별 이상탐지 건수 (차트용)."""
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute("""
            SELECT TO_CHAR(timestamp, 'YYYY-MM') AS month,
                   severity, COUNT(*) AS cnt
            FROM anomaly_results
            GROUP BY 1, 2
            ORDER BY 1;
        """)
        rows = cur.fetchall()
        conn.close()
        result = {}
        for month, severity, cnt in rows:
            if month not in result:
                result[month] = {"month": month, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
            result[month][severity] = cnt
        return {"timeline": list(result.values())}
    except Exception as e:
        return {"error": str(e), "timeline": []}


@router.get("/types")
async def anomaly_types():
    """이상 유형별 건수 요약 (파이차트용)."""
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute("""
            SELECT anomaly_type, COUNT(*) AS cnt
            FROM anomaly_results
            GROUP BY anomaly_type
            ORDER BY cnt DESC;
        """)
        rows = cur.fetchall()
        conn.close()
        return {"types": [{"type": r[0], "count": r[1]} for r in rows]}
    except Exception as e:
        return {"error": str(e), "types": []}


@router.get("/{anomaly_id}/context")
async def anomaly_context(anomaly_id: int, hours: int = Query(24, ge=6, le=72)):
    """이상 항목 전후 시계열 + 메타 반환 (차트·AI 분석용)."""
    # 1. 이상 레코드 조회
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute("""
            SELECT id, timestamp, meter_id, anomaly_type, severity, description,
                   score_stat, score_iso, score_lstm, vote_count
            FROM anomaly_results WHERE id = %s;
        """, (anomaly_id,))
        row = cur.fetchone()
        conn.close()
    except Exception as e:
        return {"error": str(e)}

    if not row:
        return {"error": "not found"}

    cols = ["id","timestamp","meter_id","anomaly_type","severity","description",
            "score_stat","score_iso","score_lstm","vote_count"]
    anomaly = dict(zip(cols, row))
    ts: datetime = anomaly["timestamp"]
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    anomaly["timestamp"] = ts.isoformat()

    # 2. 전후 시계열 로드
    try:
        from data.loader import load_reduced
        start = ts - timedelta(hours=hours)
        end   = ts + timedelta(hours=hours)
        df = load_reduced(start, end)
        if df.empty:
            return {"anomaly": anomaly, "timeseries": []}
        df = df[["ts","grid_P","pv_P","chp_P","cop","cool_output_P","cool_elec_P"]].copy()
        df["ts"] = df["ts"].astype(str)
        # W → kW, 소수점 1자리
        for col in ["grid_P","pv_P","chp_P","cool_output_P","cool_elec_P"]:
            df[col] = (df[col] / 1000).round(1)
        df["cop"] = df["cop"].round(3)
        df = df.where(df.notna(), None)
        import pytz
        berlin    = pytz.timezone("Europe/Berlin")
        ts_berlin = ts.astimezone(berlin)
        return {
            "anomaly":    anomaly,
            "timeseries": df.to_dict(orient="records"),
            "anomaly_ts": ts_berlin.strftime("%Y-%m-%d %H:%M"),
        }
    except Exception as e:
        return {"anomaly": anomaly, "timeseries": [], "error": str(e)}


_run_status: dict[str, dict] = {}   # job_id → {status, total, counts, error}


def _do_detection(job_id: str, start: str, end: str) -> None:
    """백그라운드 이상탐지 실행."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    try:
        from data.loader import load_range
        from models.anomaly.ensemble import run

        _run_status[job_id]["status"] = "running"
        df        = load_range(start, end)
        anomalies = run(df, save_to_db=True)
        counts    = anomalies["severity"].value_counts().to_dict()
        _run_status[job_id].update({"status": "done", "total": len(anomalies), "counts": counts})
    except Exception as e:
        _run_status[job_id].update({"status": "error", "error": str(e)})


@router.post("/run")
async def run_detection(
    background_tasks: BackgroundTasks,
    start: str = Query(..., description="ISO 날짜 예: 2022-07-01"),
    end:   str = Query(..., description="ISO 날짜 예: 2022-08-01"),
):
    """지정 기간 앙상블 이상탐지 백그라운드 실행. /run/status/{job_id}로 완료 확인."""
    job_id = f"{start}_{end}_{datetime.now().strftime('%H%M%S')}"
    _run_status[job_id] = {"status": "queued", "period": f"{start} ~ {end}"}
    background_tasks.add_task(_do_detection, job_id, start, end)
    return {"job_id": job_id, "status": "queued", "period": f"{start} ~ {end}"}


@router.get("/run/status/{job_id}")
async def run_status(job_id: str):
    """이상탐지 실행 상태 조회."""
    return _run_status.get(job_id, {"status": "not_found"})
