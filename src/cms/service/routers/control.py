"""
Control & Optimization Recommendations Router.

예측 결과·이상탐지·에너지 효율을 종합해 운영자가 즉시 행동할 수 있는
권고 카드 목록을 생성한다. 실제 설비 제어는 모의 (승인 상태만 기록).
"""
from cms.service.errors import safe_err
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Query

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # backend/

router = APIRouter(prefix="/control", tags=["control"])

from cms.service.db import get_conn as _db_conn  # noqa: E402

_table_ready = False

# ── 비용 환산 (프로토타입 기준 단가) ──────────────────────────────
ENERGY_PRICE_EUR_KWH      = 0.20    # 에너지 단가 (billing 기본값과 동일)
DEMAND_PRICE_EUR_KW_MONTH = 12.0    # 데먼드 차지(월) ≈ €144/kW·년


def _eur(v: float) -> str:
    return f"€{v:,.0f}"


def _ensure_control_table(conn):
    global _table_ready
    if _table_ready:
        return
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS control_recommendations (
            id              TEXT PRIMARY KEY,
            generated_at    TIMESTAMPTZ DEFAULT NOW(),
            sim_now         TIMESTAMPTZ,
            category        TEXT,
            priority        TEXT,
            title           TEXT,
            description     TEXT,
            equipment       TEXT,
            expected_saving TEXT,
            status          TEXT DEFAULT 'pending',
            status_updated  TIMESTAMPTZ,
            outcome_evaluated_at TIMESTAMPTZ,
            outcome_value        FLOAT,
            outcome_unit         TEXT,
            outcome_label        TEXT,
            outcome_note         TEXT
        );
    """)
    for col, ddl in [
        ("sim_now",              "TIMESTAMPTZ"),
        ("outcome_evaluated_at", "TIMESTAMPTZ"),
        ("outcome_value",        "FLOAT"),
        ("outcome_unit",         "TEXT"),
        ("outcome_label",        "TEXT"),
        ("outcome_note",         "TEXT"),
    ]:
        cur.execute(f"ALTER TABLE control_recommendations ADD COLUMN IF NOT EXISTS {col} {ddl};")
    conn.commit()
    _table_ready = True


# ── 권고 생성 로직 ────────────────────────────────────────────────

def _gen_peak_shift(forecast: dict) -> dict | None:
    """피크 부하 시프트 권고."""
    data = forecast.get("v84") if isinstance(forecast.get("v84"), list) else None
    if not data or len(data) < 6:
        return None

    vals = [r["yhat"] for r in data]
    avg  = sum(vals) / len(vals)
    peak = max(vals)
    if avg <= 0:
        return None
    ratio = peak / avg
    if ratio < 1.4:
        return None

    peak_idx = vals.index(peak)
    peak_ts  = data[peak_idx].get("ts", "")
    saving_kw = (peak - avg) * 0.2   # 보수적으로 20% 분산 가정
    saving_eur = round(saving_kw * DEMAND_PRICE_EUR_KW_MONTH)

    return {
        "id":              f"peak-shift-{peak_ts}",
        "category":        "peak_shift",
        "priority":        "HIGH" if ratio >= 1.7 else "MEDIUM",
        "title":           f"피크 부하 시프트 — {peak_ts[:16]}",
        "description":     (
            f"예측 피크 {peak:.0f} kW (평균 {avg:.0f} kW의 {ratio:.2f}배). "
            f"피크 3시간 전부터 비핵심 부하(공조 프리쿨·ESS 충전)를 시프트하면 "
            f"약 {saving_kw:.0f} kW 분산 가능 → 월 데먼드 차지 약 {_eur(saving_eur)} 절감."
        ),
        "equipment":       "H1 공조 / ESS",
        "expected_saving": f"피크 -{saving_kw:.0f} kW · {_eur(saving_eur)}/월",
        "saving_eur":      saving_eur,
    }


def _gen_night_load(forecast: dict) -> dict | None:
    """야간 대기전력 점검 권고."""
    data = forecast.get("v84") if isinstance(forecast.get("v84"), list) else None
    if not data or len(data) < 12:
        return None

    night, day = [], []
    for r in data:
        ts = str(r.get("ts", ""))
        if len(ts) < 13:
            continue
        try:
            h = int(ts[11:13])
        except ValueError:
            continue
        if 0 <= h <= 5:
            night.append(r["yhat"])
        elif 8 <= h <= 18:
            day.append(r["yhat"])
    if not night or not day:
        return None

    n_avg = sum(night) / len(night)
    d_avg = sum(day)   / len(day)
    if d_avg <= 0:
        return None
    ratio = n_avg / d_avg
    if ratio < 0.7:
        return None

    reduce_kw  = max(n_avg - d_avg * 0.5, 0)
    # 야간 6시간(00~05) × 30일 × 에너지 단가
    saving_eur = round(reduce_kw * 6 * 30 * ENERGY_PRICE_EUR_KWH)

    return {
        "id":              "night-load-check",
        "category":        "night_check",
        "priority":        "MEDIUM",
        "title":           "야간 대기전력 점검",
        "description":     (
            f"야간(00~05시) 평균 {n_avg:.0f} kW가 주간(08~18시) 평균 {d_avg:.0f} kW의 "
            f"{ratio*100:.0f}%. 비가동 시간 설비 차단 시 월 약 {_eur(saving_eur)} 절감 여지."
        ),
        "equipment":       "전체 분기 차단기",
        "expected_saving": f"대기전력 -{reduce_kw:.0f} kW · {_eur(saving_eur)}/월",
        "saving_eur":      saving_eur,
    }


def _gen_anomaly_actions(conn) -> list[dict]:
    """최근 HIGH 이상탐지 → 점검 권고. 시뮬 활성 시 sim_now 이전만."""
    items = []

    # 재생 헤드 cutoff
    cutoff = None
    try:
        from cms.service.routers.simulator import clock
        cutoff = clock.now.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass

    try:
        cur = conn.cursor()
        if cutoff:
            cur.execute("""
                SELECT timestamp, anomaly_type, severity, description
                FROM anomaly_results
                WHERE severity IN ('HIGH', 'CRITICAL')
                  AND (gateway_failure IS NULL OR gateway_failure = FALSE)
                  AND timestamp <= %s
                ORDER BY timestamp DESC
                LIMIT 5;
            """, (cutoff,))
        else:
            cur.execute("""
                SELECT timestamp, anomaly_type, severity, description
                FROM anomaly_results
                WHERE severity IN ('HIGH', 'CRITICAL')
                  AND (gateway_failure IS NULL OR gateway_failure = FALSE)
                ORDER BY timestamp DESC
                LIMIT 5;
            """)
        rows = cur.fetchall()
    except Exception:
        return []

    REMEDY = {
        "COPDrop":           ("냉각기", "응축기 청소·냉매 압력 점검"),
        "CHPOutage":         ("CHP",   "가스 공급·과열 트립 점검"),
        "PowerSpike":        ("변압기", "피더별 전류·역률 콘덴서 점검"),
        "NightConsumption":  ("야간 부하", "분기 차단기 자동화 점검"),
        "PVNightNonZero":    ("PV 인버터", "인버터 재시작·센서 교정"),
    }
    for r in rows:
        ts, atype, sev, desc = r
        equipment, action = REMEDY.get(atype, ("해당 설비", "현장 확인 필요"))
        items.append({
            "id":              f"anomaly-{atype}-{ts.isoformat()}",
            "category":        "anomaly_response",
            "priority":        "HIGH",
            "title":           f"{atype} 후속 점검 — {ts.strftime('%m-%d %H:%M')}",
            "description":     f"{desc[:80]}... → {action}",
            "equipment":       equipment,
            "expected_saving": "재발 방지 (정성)",
            "saving_eur":      0,
        })
    return items


def _gen_efficiency(conn) -> dict | None:
    """최근 월 자급률·COP 악화 시 운영 검토 권고."""
    cutoff_period = None
    try:
        from cms.service.routers.simulator import clock
        cutoff_period = clock.now.strftime("%Y-%m")
    except Exception:
        pass

    try:
        cur = conn.cursor()
        if cutoff_period:
            cur.execute("""
                SELECT period, self_sufficiency_pct, avg_cop
                FROM monthly_report
                WHERE period <= %s
                ORDER BY period DESC LIMIT 3;
            """, (cutoff_period,))
        else:
            cur.execute("""
                SELECT period, self_sufficiency_pct, avg_cop
                FROM monthly_report
                ORDER BY period DESC LIMIT 3;
            """)
        rows = cur.fetchall()
    except Exception:
        return None
    if len(rows) < 2:
        return None

    latest, prev = rows[0], rows[1]
    if latest[1] is None or prev[1] is None:
        return None
    ss_drop  = (prev[1] or 0) - (latest[1] or 0)
    cop_drop = (prev[2] or 0) - (latest[2] or 0)

    issues = []
    if ss_drop > 5:
        issues.append(f"자급률 {prev[1]:.1f}% → {latest[1]:.1f}% (-{ss_drop:.1f}%p)")
    if cop_drop > 0.2:
        issues.append(f"COP {prev[2]:.2f} → {latest[2]:.2f} (-{cop_drop:.2f})")
    if not issues:
        return None

    return {
        "id":              f"efficiency-{latest[0]}",
        "category":        "efficiency_review",
        "priority":        "MEDIUM",
        "title":           f"효율 저하 검토 — {latest[0]}",
        "description":     " · ".join(issues) + " — 베이스라인 재학습 또는 설비 정밀 점검 권고.",
        "equipment":       "전체",
        "expected_saving": "추세 회복 (정성)",
        "saving_eur":      0,
    }


def _gen_peak_forecast_shift() -> dict | None:
    """Import P-Max 60분 피크 예측 → 선제 피크 시프트 권고 (데먼드 차지 절감)."""
    import os
    if not os.getenv("DB_PASS") and os.getenv("DB_PASSWORD"):
        os.environ["DB_PASS"] = os.getenv("DB_PASSWORD")
    try:
        import pandas as pd
        from cms.service.routers.simulator import effective_now
        from ml.forecasting.import_pmax import batch_inference
        model_root = Path(__file__).resolve().parents[3] / "ml" / "forecasting" / "artifacts" / "import_pmax_v29_60min"
        as_of = pd.Timestamp(effective_now()).floor("15min").strftime("%Y-%m-%dT%H:%M:%SZ")
        batch = batch_inference.run_batch(
            table_name="mart.peak_feature_15min",
            model_root=model_root,
            requested_as_of=as_of,
            lookback_days=14,
        )
    except Exception as e:
        print(f"[control] pmax peak rec skipped: {e}")
        return None

    total_peak = 0.0
    shiftable  = 0.0
    peak_at    = None
    for r in batch.get("results", []):
        try:
            peak_kw = float(r["next_60min_peak"]["predicted_import_p_max"]) / 1000
            last_kw = float(r["last_import_p_max"]) / 1000
        except (KeyError, TypeError, ValueError):
            continue
        total_peak += peak_kw
        if peak_kw > last_kw:
            shiftable += (peak_kw - last_kw)
        if peak_at is None:
            peak_at = r["next_60min_peak"].get("timestamp")

    if shiftable < 15:   # 시프트 여지 미미 → 권고 안 함
        return None

    saving_eur = round(shiftable * DEMAND_PRICE_EUR_KW_MONTH)
    return {
        "id":              f"peak-fc-{(peak_at or '')[:13]}",
        "category":        "peak_shift",
        "priority":        "HIGH" if shiftable >= 40 else "MEDIUM",
        "title":           f"계통 인입 피크 선제 시프트 — {(peak_at or '')[11:16]} 예상",
        "description":     (
            f"Import P-Max 예측: 향후 60분 계통 인입 피크 합계 약 {total_peak:.0f} kW "
            f"(상승분 {shiftable:.0f} kW). 피크 도달 전 비핵심 부하를 시프트하면 "
            f"월 데먼드 차지 약 {_eur(saving_eur)} 절감 여지."
        ),
        "equipment":       "계통 인입 (V/H2 주피더)",
        "expected_saving": f"피크 -{shiftable:.0f} kW · {_eur(saving_eur)}/월",
        "saving_eur":      saving_eur,
    }


# ── 엔드포인트 ────────────────────────────────────────────────────

@router.get("/recommendations")
def get_recommendations(hours: int = Query(24, ge=12, le=72)):
    """예측·이상·효율 데이터를 종합한 운영 권고 목록."""
    from cms.service.legacy_data.loader import load_range, get_data_range

    recs: list[dict] = []

    # 1. 예측 기반 권고 (v84 앙상블)
    try:
        import pandas as pd
        from ml.pipeline.inference import predict_meter, is_available
        from ml.pipeline.common.config import METER_SPECS_BY_URN
        from ml.pipeline.common.db import build_engine, fetch_meter_window

        _CANDIDATES = ["H2.Z66", "H2.Z64", "H1.Z10", "H1.Z12", "H3.Z43", "H4.Z50", "V.Z84"]
        avail = [m for m in _CANDIDATES if m in METER_SPECS_BY_URN and is_available(m, 1)]

        if avail:
            engine = build_engine()
            now    = pd.Timestamp.now(tz="UTC")
            combined: dict[str, float] = {}
            for urn in avail:
                spec   = METER_SPECS_BY_URN[urn]
                raw_df = fetch_meter_window(engine, spec, end_ts=now, window_hours=200)
                df     = predict_meter(urn, 1, raw_df, spec)
                for _, row in df.iterrows():
                    ts = str(row["ts"])[:16]
                    combined[ts] = combined.get(ts, 0.0) + float(row["pred_t_plus_1"]) / 1000

            forecast_data = [{"ts": ts, "yhat": kw} for ts, kw in sorted(combined.items())][:hours]
            forecast = {"v84": forecast_data}

            peak = _gen_peak_shift(forecast)
            if peak:
                recs.append(peak)
            night = _gen_night_load(forecast)
            if night:
                recs.append(night)
    except Exception as e:
        print(f"[control] v84 forecast-based rec failed: {e}")

    # 1-b. Import P-Max 60분 피크 예측 기반 선제 시프트 (v84 없이도 동작)
    peak_fc = _gen_peak_forecast_shift()
    if peak_fc:
        recs.append(peak_fc)

    # 2. DB 기반 권고
    try:
        with _db_conn() as conn:
            _ensure_control_table(conn)
            recs.extend(_gen_anomaly_actions(conn))
            eff = _gen_efficiency(conn)
            if eff:
                recs.append(eff)

            # 3. 저장된 상태 조회 (승인/거부 마킹)
            if recs:
                ids = [r["id"] for r in recs]
                cur = conn.cursor()
                cur.execute(
                    "SELECT id, status, status_updated FROM control_recommendations WHERE id = ANY(%s);",
                    (ids,)
                )
                status_map = {r[0]: {"status": r[1], "updated": r[2].isoformat() if r[2] else None}
                              for r in cur.fetchall()}
                for r in recs:
                    s = status_map.get(r["id"])
                    r["status"]         = s["status"] if s else "pending"
                    r["status_updated"] = s["updated"] if s else None

            # 4. Memory — 같은 카테고리의 최근 평가된 결과를 권고 본문에 첨부
            for r in recs:
                past = get_recent_outcomes(r["category"], limit=1)
                if past:
                    p = past[0]
                    emoji = "🎉" if p["label"] == "성공" else ("⚠️" if p["label"] == "실패" else "📚")
                    r["memory"] = {
                        "label":  p["label"],
                        "note":   p["note"],
                        "status": p["status"],
                    }
                    r["description"] = f"{emoji} [학습] {p['note']}\n\n{r['description']}"

            # 5. 적응형 학습 — 카테고리 누적 성공/실패로 신뢰도·재랭킹
            learning = _category_learning(conn)
            for r in recs:
                lr = learning.get(r["category"])
                if lr and lr["rate"] is not None and (lr["success"] + lr["failure"]) >= 1:
                    r["learned"] = lr
                    if lr["signal"] == "trusted":
                        r["description"] += f"\n\n🧠 [적응형 학습] 이 유형 과거 성공률 {lr['rate']}% ({lr['success']}/{lr['success']+lr['failure']}) — 신뢰도 높아 우선 추천."
                    elif lr["signal"] == "caution":
                        r["description"] += f"\n\n⚠️ [적응형 학습] 이 유형 과거 성공률 {lr['rate']}% ({lr['success']}/{lr['success']+lr['failure']}) — 반복 실패, 적용 전 재검토 권장."
    except Exception as e:
        print(f"[control] db-based rec failed: {e}")

    # 우선순위 정렬 + 적응형 재랭킹 (같은 우선순위 내 성공률 높은 유형 우선)
    pri_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    def _rank(x):
        lr = x.get("learned") or {}
        rate = lr.get("rate")
        return (pri_order.get(x["priority"], 9), -(rate if rate is not None else 50), x["id"])
    recs.sort(key=_rank)

    def _is_pending(r):
        return (r.get("status") or "pending") == "pending"
    pending_eur  = sum((r.get("saving_eur") or 0) for r in recs if _is_pending(r))
    approved_eur = sum((r.get("saving_eur") or 0) for r in recs if r.get("status") == "approved")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count":        len(recs),
        "items":        recs,
        "summary": {
            "pending_count":       sum(1 for r in recs if _is_pending(r)),
            "pending_saving_eur":  pending_eur,
            "approved_count":      sum(1 for r in recs if r.get("status") == "approved"),
            "approved_saving_eur": approved_eur,
        },
    }


@router.get("/learning-stats")
def learning_stats():
    """AI 학습 이력 요약 — 대시보드 미니 카드용."""
    try:
        with _db_conn() as conn:
            _ensure_control_table(conn)
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    COUNT(*)                                        FILTER (WHERE status_updated IS NOT NULL),
                    COUNT(*)                                        FILTER (WHERE status = 'approved'),
                    COUNT(*)                                        FILTER (WHERE outcome_label = '성공'),
                    COUNT(*)                                        FILTER (WHERE outcome_label = '실패'),
                    COALESCE(SUM(outcome_value) FILTER (WHERE outcome_label = '성공' AND outcome_unit = 'kW'), 0)
                FROM control_recommendations;
            """)
            total_decided, approved, success, failure, kw_saved = cur.fetchone()

            cur.execute("""
                SELECT title, outcome_label, outcome_note, outcome_evaluated_at
                FROM control_recommendations
                WHERE outcome_evaluated_at IS NOT NULL
                ORDER BY outcome_evaluated_at DESC LIMIT 3;
            """)
            recent = [
                {"title": r[0], "label": r[1], "note": r[2],
                 "evaluated_at": r[3].isoformat() if r[3] else None}
                for r in cur.fetchall()
            ]
        return {
            "total_decided":   int(total_decided or 0),
            "approved":        int(approved or 0),
            "success":         int(success or 0),
            "failure":         int(failure or 0),
            "kw_saved_total":  float(kw_saved or 0),
            "recent":          recent,
        }
    except Exception as e:
        return {"error": safe_err(e)}


@router.delete("/recommendations")
def clear_recommendations():
    """전체 권고 처리·학습 이력 초기화 (승인/거부/outcome 모두 삭제). 데모 초기화용."""
    try:
        with _db_conn() as conn:
            cur = conn.cursor()
            _ensure_control_table(conn)
            cur.execute("DELETE FROM control_recommendations;")
            deleted = cur.rowcount
            conn.commit()
        return {"deleted": True, "count": deleted}
    except Exception as e:
        return {"error": safe_err(e)}


@router.post("/recommendations/{rec_id}/approve")
def approve(rec_id: str, body: dict | None = None):
    """권고 승인 — 모의 (실제 설비 제어 없음, 상태만 기록)."""
    _save_status(rec_id, "approved", body)
    return {"ok": True, "id": rec_id, "status": "approved"}


@router.post("/recommendations/{rec_id}/reject")
def reject(rec_id: str, body: dict | None = None):
    _save_status(rec_id, "rejected", body)
    return {"ok": True, "id": rec_id, "status": "rejected"}


def _save_status(rec_id: str, status: str, body: dict | None) -> None:
    title = (body or {}).get("title", "")
    desc  = (body or {}).get("description", "")
    cat   = (body or {}).get("category", "")
    pri   = (body or {}).get("priority", "")

    # 재생 헤드 시각 같이 저장 — 24h 후 outcome 평가에 사용
    sim_now = None
    try:
        from cms.service.routers.simulator import clock
        sim_now = clock.now
    except Exception:
        pass

    try:
        with _db_conn() as conn:
            _ensure_control_table(conn)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO control_recommendations
                    (id, category, priority, title, description, status, status_updated, sim_now)
                VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s)
                ON CONFLICT (id) DO UPDATE SET
                    status         = EXCLUDED.status,
                    status_updated = NOW(),
                    sim_now        = COALESCE(control_recommendations.sim_now, EXCLUDED.sim_now);
            """, (rec_id, cat, pri, title, desc, status, sim_now))
            conn.commit()
    except Exception as e:
        print(f"[control] save_status failed: {e}")


# ══════════════════════════════════════════════════════════════════
#  Outcome 평가 — 승인 24h 후 권고가 정말 효과 있었는지 측정
# ══════════════════════════════════════════════════════════════════

def evaluate_pending_outcomes(sim_now=None) -> list[dict]:
    """
    승인된 권고 중 outcome 미평가 항목을 sim_now 기준 24h 지났으면 평가.
    시뮬 워커에서 호출됨.
    반환: 새로 평가된 outcome 목록 (회고 알림용).
    """
    from datetime import datetime, timedelta
    if sim_now is None:
        try:
            from cms.service.routers.simulator import clock
            sim_now = clock.now
        except Exception:
            sim_now = datetime.now(timezone.utc)

    cutoff = sim_now - timedelta(hours=24)
    evaluated = []

    try:
        with _db_conn() as conn:
            _ensure_control_table(conn)
            cur = conn.cursor()
            cur.execute("""
                SELECT id, category, title, status, sim_now, equipment
                FROM control_recommendations
                WHERE status IN ('approved', 'rejected')
                  AND sim_now IS NOT NULL
                  AND sim_now <= %s
                  AND outcome_evaluated_at IS NULL;
            """, (cutoff,))
            rows = cur.fetchall()

            for rec_id, cat, title, status, rec_sim_now, equipment in rows:
                outcome = _compute_outcome(conn, cat, rec_sim_now, status)
                cur.execute("""
                    UPDATE control_recommendations
                    SET outcome_evaluated_at = NOW(),
                        outcome_value        = %s,
                        outcome_unit         = %s,
                        outcome_label        = %s,
                        outcome_note         = %s
                    WHERE id = %s;
                """, (
                    outcome["value"], outcome["unit"],
                    outcome["label"], outcome["note"],
                    rec_id,
                ))
                evaluated.append({
                    "id":       rec_id,
                    "title":    title,
                    "category": cat,
                    "status":   status,
                    "outcome":  outcome,
                })
            conn.commit()
    except Exception as e:
        print(f"[control] evaluate_outcomes failed: {e}")
    return evaluated


def _compute_outcome(conn, category: str, rec_sim_now, status: str) -> dict:
    """카테고리별 outcome 계산 — 권고 시점 후 24h 데이터 기반."""
    from datetime import timedelta
    try:
        from cms.service.legacy_data.loader import load_range
        import pandas as pd

        win_start = rec_sim_now
        win_end   = rec_sim_now + timedelta(hours=24)
        prev_start = rec_sim_now - timedelta(hours=24)

        if category == "peak_shift":
            # 권고 24h 후 피크 vs 직전 24h 피크 비교
            df_after  = load_range(str(win_start.date()), str(win_end.date()))
            df_before = load_range(str(prev_start.date()), str(win_start.date()))
            if df_after.empty or df_before.empty:
                return {"value": 0, "unit": "kW", "label": "중립", "note": "데이터 부족"}
            peak_after  = float(df_after["grid_P"].max()  or 0) / 1000
            peak_before = float(df_before["grid_P"].max() or 0) / 1000
            delta = peak_before - peak_after   # 줄어들면 양수
            if status == "approved":
                if delta > 30:
                    return {"value": round(delta, 1), "unit": "kW",
                            "label": "성공",
                            "note": f"권고 적용 후 피크 -{delta:.0f} kW 분산 확인 (사전 24h {peak_before:.0f} → 사후 {peak_after:.0f})"}
                elif delta < -30:
                    return {"value": round(delta, 1), "unit": "kW",
                            "label": "실패",
                            "note": f"피크가 오히려 {-delta:.0f} kW 상승 (외부 요인 추정)"}
                else:
                    return {"value": round(delta, 1), "unit": "kW",
                            "label": "중립", "note": "유의미한 변화 없음"}
            else:   # rejected
                if peak_after > peak_before + 30:
                    return {"value": round(peak_after - peak_before, 1), "unit": "kW",
                            "label": "실패",
                            "note": f"권고 거부 → 피크 {peak_after - peak_before:.0f} kW 추가 상승 (조치했더라면 회피 가능)"}
                else:
                    return {"value": 0, "unit": "kW",
                            "label": "중립", "note": "거부했으나 피크 변동 없음"}

        elif category == "anomaly_response":
            # 같은 유형 이상이 24h 안에 재발했는지
            cur = conn.cursor()
            cur.execute("""
                SELECT COUNT(*) FROM anomaly_results
                WHERE timestamp > %s AND timestamp <= %s
                  AND severity IN ('HIGH','CRITICAL');
            """, (win_start, win_end))
            recurrence = int(cur.fetchone()[0])
            if status == "approved":
                if recurrence == 0:
                    return {"value": 0, "unit": "건", "label": "성공",
                            "note": "조치 후 24h 동안 재발 없음"}
                else:
                    return {"value": recurrence, "unit": "건", "label": "실패",
                            "note": f"조치했으나 24h 안에 {recurrence}건 재발"}
            else:
                if recurrence > 0:
                    return {"value": recurrence, "unit": "건", "label": "실패",
                            "note": f"권고 거부 → 24h 안에 {recurrence}건 재발 (조치했더라면 예방 가능)"}
                else:
                    return {"value": 0, "unit": "건", "label": "중립",
                            "note": "거부했으나 재발 없음"}

        elif category == "night_check":
            # 야간 부하 변화
            df = load_range(str(win_start.date()), str(win_end.date()))
            if df.empty:
                return {"value": 0, "unit": "kW", "label": "중립", "note": "데이터 부족"}
            df["ts"]   = pd.to_datetime(df["ts"])
            df["hour"] = df["ts"].dt.hour
            night = df[df["hour"].between(0, 5)]
            n_avg = float(night["grid_P"].mean() or 0) / 1000
            label = "성공" if status == "approved" and n_avg < 100 else "중립"
            return {"value": round(n_avg, 1), "unit": "kW", "label": label,
                    "note": f"24h 후 야간 평균 부하 {n_avg:.0f} kW"}

        elif category == "efficiency_review":
            return {"value": 0, "unit": "%", "label": "중립", "note": "월별 평가 필요 (장기 추세)"}

    except Exception as e:
        return {"value": 0, "unit": "", "label": "중립", "note": "평가 중 오류가 발생했습니다."}

    return {"value": 0, "unit": "", "label": "중립", "note": "평가 불가"}


def _category_learning(conn) -> dict:
    """카테고리별 누적 outcome 집계 → 적응형 재랭킹/신뢰도 산출."""
    cur = conn.cursor()
    cur.execute("""
        SELECT category,
          COUNT(*) FILTER (WHERE outcome_label = '성공'),
          COUNT(*) FILTER (WHERE outcome_label = '실패'),
          COUNT(*) FILTER (WHERE outcome_label = '중립')
        FROM control_recommendations
        WHERE outcome_evaluated_at IS NOT NULL
        GROUP BY category;
    """)
    out = {}
    for cat, s, f, n in cur.fetchall():
        s, f, n = int(s or 0), int(f or 0), int(n or 0)
        decided = s + f
        rate = round(s / decided * 100) if decided else None
        signal = None
        if decided >= 2 and rate is not None:
            signal = "trusted" if rate >= 67 else ("caution" if rate <= 34 else None)
        out[cat] = {"success": s, "failure": f, "neutral": n, "rate": rate, "signal": signal}
    return out


def get_recent_outcomes(category: str, limit: int = 2) -> list[dict]:
    """같은 카테고리의 최근 평가된 outcome — 새 권고 생성 시 Memory로 활용."""
    try:
        with _db_conn() as conn:
            _ensure_control_table(conn)
            cur = conn.cursor()
            cur.execute("""
                SELECT title, status, outcome_label, outcome_note, outcome_evaluated_at
                FROM control_recommendations
                WHERE category = %s
                  AND outcome_evaluated_at IS NOT NULL
                ORDER BY outcome_evaluated_at DESC
                LIMIT %s;
            """, (category, limit))
            rows = cur.fetchall()
        return [
            {"title": r[0], "status": r[1], "label": r[2], "note": r[3],
             "evaluated_at": r[4].isoformat() if r[4] else None}
            for r in rows
        ]
    except Exception:
        return []
