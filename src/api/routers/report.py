import os
from datetime import timezone

import pandas as pd
import psycopg2
from dotenv import load_dotenv
from fastapi import APIRouter, Query

load_dotenv()

router = APIRouter(prefix="/report", tags=["report"])
DB_URL = os.getenv("DATABASE_URL")


def _get_conn():
    return psycopg2.connect(DB_URL)


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

    # 이상탐지 건수 조회
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute("SELECT TO_CHAR(timestamp, 'YYYY-MM'), COUNT(*) FROM anomaly_results GROUP BY 1;")
        anomaly_counts = {r[0]: r[1] for r in cur.fetchall()}
        conn.close()
    except Exception:
        anomaly_counts = {}

    # DB 저장
    conn = _get_conn()
    _ensure_monthly_table(conn)
    cur  = conn.cursor()
    inserted = 0
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
    conn.close()

    return {"period": f"{start} ~ {end}", "months_inserted": inserted}


@router.get("")
async def get_report(months: int = Query(3, ge=1, le=84)):
    """최근 N개월 KPI 리포트 조회."""
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute("""
            SELECT period, total_consumption_kwh, self_sufficiency_pct,
                   avg_cop, anomaly_count, grid_dependency_pct, pv_kwh, chp_kwh
            FROM monthly_report
            ORDER BY period DESC LIMIT %s;
        """, (months,))
        rows = conn.cursor().fetchall() if False else cur.fetchall()
        conn.close()
    except Exception as e:
        return {"error": str(e), "items": []}

    cols = ["period", "total_consumption_kwh", "self_sufficiency_pct",
            "avg_cop", "anomaly_count", "grid_dependency_pct", "pv_kwh", "chp_kwh"]
    return {"items": [dict(zip(cols, r)) for r in rows]}
