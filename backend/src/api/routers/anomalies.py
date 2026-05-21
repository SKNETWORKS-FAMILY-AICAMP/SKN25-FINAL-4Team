import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import APIRouter, BackgroundTasks, Query

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

load_dotenv()

router = APIRouter(prefix="/anomalies", tags=["anomalies"])

from api.db import get_conn as _db_conn  # noqa: E402


@router.get("")
async def list_anomalies(
    limit:       int           = Query(50, ge=1, le=500),
    offset:      int           = Query(0, ge=0),
    severity:    Optional[str] = Query(None, description="HIGH / MEDIUM / LOW"),
    year:        Optional[int] = Query(None, description="연도 필터 예: 2022"),
    month:       Optional[int] = Query(None, description="월 필터 예: 7"),
    exclude_gf:  bool          = Query(True, description="게이트웨이 장애 구간 제외"),
):
    """이상탐지 결과 조회 (필터·페이지네이션 지원)."""
    try:
        with _db_conn() as conn:
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
            if exclude_gf:
                conds.append("(gateway_failure IS NULL OR gateway_failure = FALSE)")
            where = ("WHERE " + " AND ".join(conds)) if conds else ""

            cur.execute(f"SELECT COUNT(*) FROM anomaly_results {where}", params)
            total = cur.fetchone()[0]

            cur.execute(f"""
                SELECT id, timestamp, meter_id, anomaly_type, severity, description,
                       score_stat, score_iso, score_lstm, vote_count, created_at,
                       actual_w, predicted_w, residual_w, source, gateway_failure
                FROM anomaly_results {where}
                ORDER BY timestamp DESC LIMIT %s OFFSET %s;
            """, params + [limit, offset])
            rows = cur.fetchall()
    except Exception as e:
        return {"error": str(e), "items": [], "total": 0}

    cols = ["id", "timestamp", "meter_id", "anomaly_type", "severity",
            "description", "score_stat", "score_iso", "score_lstm",
            "vote_count", "created_at",
            "actual_w", "predicted_w", "residual_w", "source", "gateway_failure"]
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
        with _db_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT severity, COUNT(*) as cnt
                FROM anomaly_results
                GROUP BY severity
                ORDER BY cnt DESC;
            """)
            rows = cur.fetchall()
        return {"summary": [{"severity": r[0], "count": r[1]} for r in rows]}
    except Exception as e:
        return {"error": str(e), "summary": []}


@router.get("/timeline")
async def anomaly_timeline():
    """월별 이상탐지 건수 (차트용)."""
    try:
        with _db_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT TO_CHAR(timestamp, 'YYYY-MM') AS month,
                       severity, COUNT(*) AS cnt
                FROM anomaly_results
                GROUP BY 1, 2
                ORDER BY 1;
            """)
            rows = cur.fetchall()
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
        with _db_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT anomaly_type, COUNT(*) AS cnt
                FROM anomaly_results
                GROUP BY anomaly_type
                ORDER BY cnt DESC;
            """)
            rows = cur.fetchall()
        return {"types": [{"type": r[0], "count": r[1]} for r in rows]}
    except Exception as e:
        return {"error": str(e), "types": []}


@router.get("/{anomaly_id}/context")
async def anomaly_context(anomaly_id: int, hours: int = Query(24, ge=6, le=72)):
    """이상 항목 전후 시계열 + 메타 반환 (차트·AI 분석용)."""
    try:
        with _db_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, timestamp, meter_id, anomaly_type, severity, description,
                       score_stat, score_iso, score_lstm, vote_count
                FROM anomaly_results WHERE id = %s;
            """, (anomaly_id,))
            row = cur.fetchone()
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

# 게이트웨이 장애 구간 — 인공 보정 데이터이므로 탐지 결과에 source 표시
_GATEWAY_FAILURES = [
    ("2020-02-13", "2020-03-06"),
    ("2020-08-20", "2020-09-17"),
    ("2021-11-15", "2021-12-10"),
    ("2022-05-06", "2022-07-14"),
]


def _is_gateway_failure(ts) -> bool:
    import pandas as pd
    t = pd.Timestamp(ts).tz_localize(None) if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts)
    for s, e in _GATEWAY_FAILURES:
        if pd.Timestamp(s) <= t <= pd.Timestamp(e):
            return True
    return False


def _classify_type_residual(row) -> str:
    """센서값 + VMD-LSTM 잔차 기반 이상 유형 분류."""
    import pandas as pd
    ts        = row.get("ts")
    actual    = float(row.get("actual_w",    0) or 0)
    predicted = float(row.get("predicted_w", 0) or 0)
    residual  = float(row.get("residual_w",  0) or 0)
    cop       = float(row.get("cop",    99)  or 99)
    pv        = float(row.get("pv_P",    0)  or 0)
    chp       = float(row.get("chp_P",  -1)  or -1)

    hour = pd.to_datetime(ts).hour if ts is not None else 12
    night = (hour < 6 or hour >= 22)

    # COP 급락 — 냉동기 효율 이상
    if cop < 1.0:
        return "COPDrop"

    # PV 야간 출력 — 센서/데이터 오류
    if night and pv > 500:
        return "PVNightNonZero"

    # CHP 정지 — 공급 측 급감 + 계통 의존 급등
    if chp >= 0 and chp < 500 and predicted > actual * 1.2 and residual > 8000:
        return "CHPOutage"

    # 야간 소비 급등
    if night and actual > predicted * 1.25 and residual > 5000:
        return "NightConsumption"

    # 계통 전력 소비 급등
    if actual > predicted * 1.2 and residual > 10000:
        return "PowerSpike"

    # 예측 대비 실제 급감 (CHP 출력 보정 가능성)
    if predicted > actual * 1.3 and residual > 10000:
        return "CHPOutage"

    return "Unknown"


def _migrate_schema(conn) -> None:
    """anomaly_results 테이블에 신 모델용 컬럼 추가 (없으면)."""
    cur = conn.cursor()
    for col, dtype in [("actual_w", "FLOAT"), ("predicted_w", "FLOAT"),
                       ("residual_w", "FLOAT"), ("source", "TEXT"), ("gateway_failure", "BOOLEAN")]:
        cur.execute(f"""
            ALTER TABLE anomaly_results
            ADD COLUMN IF NOT EXISTS {col} {dtype};
        """)
    conn.commit()


def _save_residual_results(anomalies, job_id: str) -> int:
    """VMD-LSTM 잔차+IF 탐지 결과를 anomaly_results에 저장."""
    import pandas as pd
    try:
        with _db_conn() as conn:
            _migrate_schema(conn)
            cur = conn.cursor()
            inserted = 0
            for _, row in anomalies.iterrows():
                ts = pd.Timestamp(row["ts"])
                if ts.tzinfo is None:
                    ts = ts.tz_localize("UTC")
                severity = {"HIGH": "HIGH", "MEDIUM": "MEDIUM"}.get(row["anomaly_level"], "LOW")
                anomaly_type = _classify_type_residual(row)
                gf = _is_gateway_failure(row["ts"])
                description = (
                    f"잔차 {float(row['residual_w'])/1000:.1f} kW "
                    f"(임계치초과={'Y' if row['res_flag'] else 'N'}, "
                    f"IF플래그={'Y' if row['if_flag'] else 'N'})"
                    + (" [게이트웨이 장애 구간]" if gf else "")
                )
                cur.execute("""
                    INSERT INTO anomaly_results
                        (timestamp, meter_id, anomaly_type, severity, description,
                         score_stat, score_iso, vote_count,
                         actual_w, predicted_w, residual_w, source, gateway_failure)
                    VALUES (%s,'residual+IF',%s,%s,%s,%s,%s,%s,%s,%s,%s,'vmd-lstm-residual',%s)
                    ON CONFLICT DO NOTHING;
                """, (
                    ts.isoformat(), anomaly_type, severity, description,
                    int(row["res_flag"]), int(row["if_flag"]), int(row["vote"]),
                    float(row["actual_w"]), float(row["predicted_w"]), float(row["residual_w"]),
                    gf,
                ))
                inserted += cur.rowcount
            conn.commit()
        return inserted
    except Exception as e:
        _run_status[job_id]["error"] = str(e)
        return 0


def _do_detection(job_id: str, start: str, end: str) -> None:
    """VMD-LSTM 잔차+IF 백그라운드 이상탐지."""
    import pandas as pd
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    try:
        from models.anomaly.residual_model import predict_anomaly, is_available
        from data.loader import load_range

        _run_status[job_id]["status"] = "running"

        if not is_available():
            # 모델 파일 없으면 구 앙상블 fallback
            from models.anomaly.ensemble import run
            df = load_range(start, end)
            anomalies = run(df, save_to_db=True)
            counts = anomalies["severity"].value_counts().to_dict()
            _run_status[job_id].update({
                "status": "done", "total": len(anomalies),
                "counts": counts, "model": "legacy-ensemble",
            })
            return

        # 잔차 계산을 위해 충분한 컨텍스트 데이터 로드 (400h 여유)
        ctx_start = str((pd.Timestamp(start) - pd.Timedelta(hours=400)).date())
        df = load_range(ctx_start, end)
        an = predict_anomaly(df, start, end)

        if an.empty:
            _run_status[job_id].update({"status": "done", "total": 0, "counts": {}})
            return

        flagged = an[an["anomaly_level"] != "NORMAL"].copy()

        # 유형 분류 품질 향상: 센서값(COP, PV, CHP) 병합
        try:
            sensor_cols = [c for c in ["cop", "pv_P", "chp_P"] if c in df.columns]
            if sensor_cols:
                s = df[["ts"] + sensor_cols].copy()
                s["_mts"] = pd.to_datetime(s["ts"]).dt.tz_localize(None).dt.floor("h")
                s = s.drop("ts", axis=1).drop_duplicates("_mts").set_index("_mts")
                flagged["_mts"] = pd.to_datetime(flagged["ts"]).dt.tz_localize(None).dt.floor("h")
                flagged = flagged.merge(s, on="_mts", how="left").drop(columns=["_mts"])
        except Exception:
            pass

        counts = flagged["anomaly_level"].value_counts().to_dict()
        inserted = _save_residual_results(flagged, job_id)
        _run_status[job_id].update({
            "status": "done", "total": inserted,
            "counts": counts, "model": "vmd-lstm-residual",
        })
    except Exception as e:
        _run_status[job_id].update({"status": "error", "error": str(e)})


@router.post("/run")
async def run_detection(
    background_tasks: BackgroundTasks,
    start: str = Query(..., description="ISO 날짜 예: 2022-07-01"),
    end:   str = Query(..., description="ISO 날짜 예: 2022-08-01"),
):
    """VMD-LSTM 잔차+IF 이상탐지 백그라운드 실행. /run/status/{job_id}로 완료 확인."""
    job_id = f"{start}_{end}_{datetime.now().strftime('%H%M%S')}"
    _run_status[job_id] = {"status": "queued", "period": f"{start} ~ {end}"}
    background_tasks.add_task(_do_detection, job_id, start, end)
    return {"job_id": job_id, "status": "queued", "period": f"{start} ~ {end}"}


@router.get("/run/status/{job_id}")
async def run_status(job_id: str):
    """이상탐지 실행 상태 조회."""
    return _run_status.get(job_id, {"status": "not_found"})
