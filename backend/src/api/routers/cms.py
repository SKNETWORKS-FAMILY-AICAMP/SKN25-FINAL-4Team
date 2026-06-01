"""
CMS (Condition Monitoring System) 설비 상태 라우터.

기능별 설비 단위로 헬스 스코어와 상태를 산출한다.
설비 ↔ 이상 유형 매핑으로 최근 이상 이력을 가중 집계하고,
로더에서 현재 핵심 지표를 끌어와 카드에 함께 노출한다.

설비 마스터는 손으로 시드하지 않고 로더의 기능별 미터 그룹(계통/PV/CHP/냉방)에서 도출한다.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Query

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from api.db import get_conn as _db_conn  # noqa: E402

router = APIRouter(prefix="/cms", tags=["cms"])

# ── 설비 마스터 (기능별 시스템) ───────────────────────────────────
# types: 이 설비로 귀속되는 anomaly_type (anomaly_results.anomaly_type 기준)
# metric: loader 컬럼명 / scale: 표시 변환 (W→kW는 0.001)
EQUIPMENT = [
    {"id": "grid",    "name": "계통/수전",   "icon": "⚡", "types": ["PowerSpike", "NightConsumption"],
     "metric": "grid_P", "metric_label": "수전 전력", "unit": "kW", "scale": 0.001},
    {"id": "cooling", "name": "냉방설비",     "icon": "❄️", "types": ["COPDrop"],
     "metric": "cop", "metric_label": "COP", "unit": "", "scale": 1.0},
    {"id": "chp",     "name": "열병합발전",   "icon": "🔥", "types": ["CHPOutage"],
     "metric": "chp_P", "metric_label": "발전 출력", "unit": "kW", "scale": 0.001},
    {"id": "pv",      "name": "태양광",       "icon": "☀️", "types": ["PVNightNonZero"],
     "metric": "pv_P", "metric_label": "발전 출력", "unit": "kW", "scale": 0.001},
]

# 노출 시간(시간당 발생률) 대비 가중 — 절대 건수가 아니라 비율로 평가해야 변별됨.
# 데이터가 시간 단위라 window_days*24 시간을 분모로 사용.
_SEV_RATE_WEIGHT = {"CRITICAL": 8.0, "HIGH": 6.0, "MEDIUM": 0.6, "LOW": 0.2}
_WINDOW_DAYS = 30


def _status(score: float) -> str:
    if score >= 85:
        return "정상"
    if score >= 60:
        return "주의"
    return "경고"


def _anchor_now(conn) -> datetime:
    """
    상태 평가의 기준 시각.
    시뮬 시각(effective_now)을 쓰되, 데이터 최신 이상 시각을 넘지 않도록 클램프.
    시뮬 미시작 시 effective_now가 실시각(미래)이라 최신 데이터로 떨어진다.
    """
    try:
        from api.routers.simulator import effective_now
        eff = effective_now().replace(tzinfo=timezone.utc)
    except Exception:
        eff = datetime.now(timezone.utc)
    cur = conn.cursor()
    cur.execute("SELECT MAX(timestamp) FROM anomaly_results;")
    latest = cur.fetchone()[0]
    if latest is None:
        return eff
    return min(eff, latest)


def _latest_metrics(anchor: datetime) -> dict:
    """anchor 직전 며칠 구간에서 설비별 핵심 지표 최신값을 한 번에 추출."""
    out: dict = {}
    try:
        from data.loader import load_range
        start = (anchor - timedelta(days=3)).strftime("%Y-%m-%d")
        end   = (anchor + timedelta(days=1)).strftime("%Y-%m-%d")
        df = load_range(start, end)
        if df is None or df.empty:
            return out
        for eq in EQUIPMENT:
            col = eq["metric"]
            if col not in df.columns:
                continue
            series = df[col].dropna()
            if series.empty:
                continue
            out[eq["id"]] = float(series.iloc[-1]) * eq["scale"]
    except Exception as e:
        print(f"[cms] metric load failed: {e}")
    return out


def _equipment_by_id(eq_id: str) -> dict | None:
    return next((e for e in EQUIPMENT if e["id"] == eq_id), None)


# 진단 캐시 — (eq_id, window, anchor날짜)별 1회만 LLM 호출
_diag_cache: dict = {}


def _gather_anomalies(conn, types, window_start, anchor):
    """설비 유형에 귀속되는 최근 이상의 집계 + 대표 사례."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT anomaly_type, severity, COUNT(*)
        FROM anomaly_results
        WHERE timestamp > %s AND timestamp <= %s
          AND anomaly_type = ANY(%s)
          AND COALESCE(gateway_failure, FALSE) = FALSE
        GROUP BY anomaly_type, severity;
        """,
        (window_start, anchor, types),
    )
    by_type: dict = {}
    total = 0
    for atype, sev, cnt in cur.fetchall():
        total += cnt
        d = by_type.setdefault(atype, {"HIGH": 0, "MEDIUM": 0, "LOW": 0})
        key = "HIGH" if sev in ("HIGH", "CRITICAL") else sev
        d[key] = d.get(key, 0) + cnt

    cur.execute(
        """
        SELECT timestamp, anomaly_type, severity, description, residual_w, actual_w, predicted_w
        FROM anomaly_results
        WHERE timestamp > %s AND timestamp <= %s
          AND anomaly_type = ANY(%s)
          AND COALESCE(gateway_failure, FALSE) = FALSE
        ORDER BY CASE severity WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1
                               WHEN 'MEDIUM' THEN 2 ELSE 3 END,
                 ABS(COALESCE(residual_w, 0)) DESC
        LIMIT 8;
        """,
        (window_start, anchor, types),
    )
    examples = []
    for ts, atype, sev, desc, res, act, pred in cur.fetchall():
        examples.append({
            "ts": ts.isoformat() if ts else None,
            "type": atype, "severity": sev, "description": desc,
            "residual_kw":  round(res / 1000, 1)  if res  is not None else None,
            "actual_kw":    round(act / 1000, 1)  if act  is not None else None,
            "predicted_kw": round(pred / 1000, 1) if pred is not None else None,
        })
    return total, by_type, examples


# 설비 → 전기 계측 대상 미터 (loader 기능 그룹 재사용)
def _elec_meters(eq_id: str) -> list[str]:
    from data.loader import (
        _GRID_METERS, _PV_METERS, _CHP_ELEC_PRIMARY, _CHP_ELEC_FALLBACK, _COOL_ELEC,
    )
    return {
        "grid":    _GRID_METERS,
        "pv":      _PV_METERS,
        "chp":     _CHP_ELEC_PRIMARY + _CHP_ELEC_FALLBACK,
        "cooling": _COOL_ELEC,
    }.get(eq_id, [])


def _electrical_signature(eq_id: str, anchor, window_days: int) -> dict | None:
    """설비 미터의 전기 시그니처: 3상 불평형(%)·평균 역률·평균 주파수."""
    meters = _elec_meters(eq_id)
    if not meters:
        return None
    start = anchor - timedelta(days=window_days)
    try:
        with _db_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT meter_urn, measurement, AVG(value)
                FROM ems.cr_measurement_1h
                WHERE meter_urn = ANY(%s)
                  AND measurement IN ('U1','U2','U3','I1','I2','I3','PF','f')
                  AND ts > %s AND ts <= %s
                GROUP BY meter_urn, measurement;
                """,
                (meters, start, anchor),
            )
            # 미터별로 묶기 — 불평형은 같은 미터의 3상끼리만 계산해야 의미 있음
            per_meter: dict = {}
            for urn, meas, val in cur.fetchall():
                if val is not None:
                    per_meter.setdefault(urn, {})[meas] = float(val)
    except Exception as e:
        print(f"[cms] electrical signature failed: {e}")
        return None

    def _meter_imbalance(m, a, b, c):
        vals = [m.get(a), m.get(b), m.get(c)]
        vals = [v for v in vals if v is not None]
        if len(vals) < 3:
            return None
        mean = sum(vals) / 3
        return None if mean == 0 else (max(vals) - min(vals)) / abs(mean) * 100

    def _avg(key_fn):
        xs = [v for v in (key_fn(m) for m in per_meter.values()) if v is not None]
        return sum(xs) / len(xs) if xs else None

    return {
        "v_imbalance_pct": _avg(lambda m: _meter_imbalance(m, "U1", "U2", "U3")),
        "i_imbalance_pct": _avg(lambda m: _meter_imbalance(m, "I1", "I2", "I3")),
        "pf_avg":          _avg(lambda m: m.get("PF")),
        "freq_avg":        _avg(lambda m: m.get("f")),
        "meter_count":     len(per_meter),
    }


def _fmt_signature(sig: dict | None) -> str:
    if not sig:
        return ""
    parts = []
    if sig.get("v_imbalance_pct") is not None:
        parts.append(f"3상 전압 불평형 {sig['v_imbalance_pct']:.1f}%")
    if sig.get("i_imbalance_pct") is not None:
        parts.append(f"3상 전류 불평형 {sig['i_imbalance_pct']:.1f}%")
    if sig.get("pf_avg") is not None:
        parts.append(f"평균 역률 {sig['pf_avg']:.2f}")
    if sig.get("freq_avg") is not None:
        parts.append(f"평균 주파수 {sig['freq_avg']:.2f}Hz")
    return ", ".join(parts)


def _build_diag_prompt(eq, window_days, total, by_type, examples, sig_str="") -> str:
    lines = [
        f"다음은 '{eq['name']}' 설비의 최근 {window_days}일 이상 탐지 결과입니다.",
        f"이 설비에 귀속되는 이상 유형: {', '.join(eq['types'])}",
        "",
        f"[이상 요약] 총 {total}건",
    ]
    for atype, c in by_type.items():
        lines.append(f"- {atype}: HIGH {c['HIGH']} / MEDIUM {c['MEDIUM']} / LOW {c['LOW']}")
    if sig_str:
        lines.append(
            f"\n[전기 시그니처 (최근 {window_days}일, 미터별 평균)] {sig_str}\n"
            "  해석 기준:\n"
            "  - 3상 불평형 10% 초과 → 결상·부하 불균형·권선 이상 의심 (단, 냉방 다단 압축기는 부분부하 시 자연 발생 가능).\n"
            "  - 역률: 소비 설비(계통·냉방)에서 0.85 미만이면 무효전력 과다·효율 저하. "
            "발전 설비(열병합·태양광)는 역률이 음수/낮아도 전력 생산(역송)이라 정상이니 효율 저하로 단정하지 말 것.\n"
            "  - 주파수 50Hz ±0.5 이탈 → 계통 안정성 문제."
        )
    if examples:
        lines.append("\n[대표 이상 사례]")
        for e in examples:
            ts = (e["ts"] or "")[:16].replace("T", " ")
            parts = [f"{ts} {e['severity']} {e['type']}"]
            if e["actual_kw"] is not None and e["predicted_kw"] is not None:
                parts.append(f"실측 {e['actual_kw']}kW / 예측 {e['predicted_kw']}kW")
            if e["residual_kw"] is not None:
                parts.append(f"잔차 {e['residual_kw']}kW")
            lines.append("- " + ", ".join(parts))
    lines.append("""
위 데이터와 도메인 지식을 근거로 이 설비의 상태를 진단하세요. 형식:

### 🩺 진단 요약
2~3문장. 현재 상태와 가장 주의할 점.

### 🔍 추정 원인
- 근거가 되는 센서값/패턴을 직접 인용. 불확실하면 '추정'이라고 명시.

### ✅ 권장 조치
- 운영자가 실행할 체크리스트 3개 이내. "어디서 무엇을" 형식.

이상이 0건이고 전기 시그니처도 정상 범위면 "현재 정상 — 특이 이상 없음" 한 줄로만 답하세요.""")
    return "\n".join(lines)


def _fallback_diagnosis(eq, total, by_type) -> str:
    if total == 0:
        return "### 🩺 진단 요약\n현재 정상 — 특이 이상 없음."
    top = max(by_type.items(), key=lambda kv: kv[1]["HIGH"] * 3 + kv[1]["MEDIUM"], default=None)
    head = f"### 🩺 진단 요약\n최근 이상 {total}건이 감지되었습니다."
    if top:
        head += f" 가장 주의가 필요한 유형은 **{top[0]}**입니다."
    return head + "\n\n_(AI 요약 미사용 — LLM 미설정 또는 호출 실패. 도메인 점검 권고를 참고하세요.)_"


def run_diagnosis(eq_id: str, window_days: int = _WINDOW_DAYS, regenerate: bool = False) -> dict:
    """설비별 LLM 고장 원인 진단 — 원인 추정 + 권장 조치. (라우트·에이전트 공용)"""
    eq = _equipment_by_id(eq_id)
    if eq is None:
        return {"error": f"알 수 없는 설비: {eq_id}"}

    with _db_conn() as conn:
        anchor = _anchor_now(conn)
        window_start = anchor - timedelta(days=window_days)
        total, by_type, examples = _gather_anomalies(conn, eq["types"], window_start, anchor)

    cache_key = f"{eq_id}|{window_days}|{anchor.date().isoformat()}"
    if not regenerate and cache_key in _diag_cache:
        return _diag_cache[cache_key]

    sig     = _electrical_signature(eq_id, anchor, window_days)
    sig_str = _fmt_signature(sig)

    llm_used = False
    try:
        from agents.llm_client import chat as llm_chat
        from knowledge.domain_knowledge import ANOMALY_DOMAIN_PROMPT
        system = (
            "당신은 에너지 설비 고장 진단 전문가입니다. 데이터에 근거해 간결하고 "
            "실행 가능한 진단을 한국어로 작성합니다.\n\n" + ANOMALY_DOMAIN_PROMPT
        )
        prompt = _build_diag_prompt(eq, window_days, total, by_type, examples, sig_str)
        diagnosis = llm_chat(
            [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            max_tokens=600,
        ).strip()
        llm_used = True
    except Exception as e:
        print(f"[cms] diagnose LLM failed: {e}")
        diagnosis = _fallback_diagnosis(eq, total, by_type)

    result = {
        "equipment":     {"id": eq["id"], "name": eq["name"], "icon": eq["icon"]},
        "window_days":   window_days,
        "anchor":        anchor.isoformat(),
        "anomaly_total": total,
        "by_type":       by_type,
        "examples":      examples,
        "electrical":    sig,
        "electrical_str": sig_str,
        "diagnosis":     diagnosis,
        "llm_used":      llm_used,
        "generated_at":  datetime.now(timezone.utc).isoformat(),
    }
    _diag_cache[cache_key] = result
    return result


@router.get("/equipment/{eq_id}/diagnose")
def diagnose_equipment(
    eq_id: str,
    window_days: int = Query(_WINDOW_DAYS, ge=1, le=180),
    regenerate: bool = Query(False),
):
    return run_diagnosis(eq_id, window_days, regenerate)


def compute_equipment_status(window_days: int = _WINDOW_DAYS) -> dict:
    """설비별 헬스 스코어 + 상태 + 최근 이상 요약 + 현재 핵심 지표. (라우트·에이전트 공용)"""
    with _db_conn() as conn:
        anchor = _anchor_now(conn)
        window_start = anchor - timedelta(days=window_days)
        cur = conn.cursor()

        items = []
        for eq in EQUIPMENT:
            cur.execute(
                """
                SELECT severity, COUNT(*), MAX(timestamp)
                FROM anomaly_results
                WHERE timestamp > %s AND timestamp <= %s
                  AND anomaly_type = ANY(%s)
                  AND COALESCE(gateway_failure, FALSE) = FALSE
                GROUP BY severity;
                """,
                (window_start, anchor, eq["types"]),
            )
            counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
            penalty_frac = 0.0
            last_at = None
            total = 0
            hours = max(window_days * 24, 1)
            for sev, cnt, mx in cur.fetchall():
                total += cnt
                key = "HIGH" if sev in ("HIGH", "CRITICAL") else sev
                counts[key] = counts.get(key, 0) + cnt
                penalty_frac += (cnt / hours) * _SEV_RATE_WEIGHT.get(sev, 0.3)
                if mx and (last_at is None or mx > last_at):
                    last_at = mx
            score = round(100 * (1 - min(penalty_frac, 1.0)))
            items.append({
                "id":              eq["id"],
                "name":            eq["name"],
                "icon":            eq["icon"],
                "types":           eq["types"],
                "health_score":    score,
                "status":          _status(score),
                "anomaly_total":   total,
                "counts":          counts,
                "last_anomaly_at": last_at.isoformat() if last_at else None,
                "metric_label":    eq["metric_label"],
                "unit":            eq["unit"],
                "metric_value":    None,   # 아래에서 채움
            })

    # 핵심 지표 부착 (DB 커넥션 밖에서 로더 호출)
    metrics = _latest_metrics(anchor)
    for it in items:
        if it["id"] in metrics:
            it["metric_value"] = round(metrics[it["id"]], 2)

    # 헬스 낮은 순으로 — 문제 설비가 위로
    items.sort(key=lambda x: x["health_score"])

    return {
        "anchor":      anchor.isoformat(),
        "window_days": window_days,
        "count":       len(items),
        "items":       items,
    }


@router.get("/equipment")
def equipment_status(window_days: int = Query(_WINDOW_DAYS, ge=1, le=180)):
    return compute_equipment_status(window_days)


# ══════════════════════════════════════════════════════════════════
#  예지보전 — 추세 기반 위험 예측 (가짜 RUL 아님, 명시적 추세 외삽)
# ══════════════════════════════════════════════════════════════════

_COP_CRITICAL = 1.5   # 도메인: COP 1.5 이하 심각


def _linreg_slope(values: list[float]) -> float:
    """단순 최소제곱 기울기 (x=0,1,2,... 월 단위 step)."""
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(values) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return 0.0
    return sum((xs[i] - mx) * (values[i] - my) for i in range(n)) / den


def _months_until(anchor) -> str:
    return anchor.strftime("%Y-%m")


def compute_predictive(months: int = 8) -> dict:
    """설비별 추세 기반 위험 예측. 냉방=COP 추세, 그 외=월 이상 발생률 추세."""
    items = []
    with _db_conn() as conn:
        anchor = _anchor_now(conn)
        anchor_m = _months_until(anchor)
        cur = conn.cursor()

        # 냉방: 월평균 COP 추세
        cur.execute(
            "SELECT period, avg_cop FROM monthly_report "
            "WHERE avg_cop IS NOT NULL AND period <= %s ORDER BY period;",
            (anchor_m,),
        )
        cop_rows = [(p, float(v)) for p, v in cur.fetchall()][-months:]

        # 전 설비: 월별 이상 건수 추세
        anomaly_series = {}
        for eq in EQUIPMENT:
            cur.execute(
                """
                SELECT TO_CHAR(timestamp, 'YYYY-MM') AS m, COUNT(*)
                FROM anomaly_results
                WHERE anomaly_type = ANY(%s) AND timestamp <= %s
                  AND COALESCE(gateway_failure, FALSE) = FALSE
                GROUP BY 1 ORDER BY 1;
                """,
                (eq["types"], anchor),
            )
            anomaly_series[eq["id"]] = [(p, int(c)) for p, c in cur.fetchall()][-months:]

    for eq in EQUIPMENT:
        if eq["id"] == "cooling" and len(cop_rows) >= 3:
            series = [{"period": p, "value": round(v, 2)} for p, v in cop_rows]
            vals = [v for _, v in cop_rows]
            slope = _linreg_slope(vals)          # COP / 월
            current = vals[-1]
            if slope < -0.01:
                direction = "악화"
            elif slope > 0.01:
                direction = "개선"
            else:
                direction = "안정"
            projection = None
            if slope < -0.005 and current > _COP_CRITICAL:
                m = (current - _COP_CRITICAL) / (-slope)
                if 0 < m <= 36:
                    projection = f"현 추세 지속 시 약 {round(m)}개월 후 COP {_COP_CRITICAL} 하회 예상"
            risk = "높음" if (current < 1.7 or (projection and "개월" in projection and direction == "악화" and (current - _COP_CRITICAL) / (-slope) <= 6)) \
                   else ("보통" if direction == "악화" else "낮음")
            note = projection or f"월평균 COP {current:.2f}, 추세 {direction}"
            items.append({
                "id": eq["id"], "name": eq["name"], "icon": eq["icon"],
                "indicator_label": "월평균 COP", "series": series,
                "direction": direction, "risk": risk,
                "projection": projection, "note": note,
            })
            continue

        # 이상 발생률 추세
        rows = anomaly_series.get(eq["id"], [])
        if len(rows) >= 3:
            series = [{"period": p, "value": v} for p, v in rows]
            vals = [v for _, v in rows]
            slope = _linreg_slope(vals)
            mean = sum(vals) / len(vals) if vals else 0
            if mean > 0 and slope > 0.15 * mean:
                direction, risk = "악화", ("높음" if vals[-1] >= mean else "보통")
            elif mean > 0 and slope < -0.15 * mean:
                direction, risk = "개선", "낮음"
            else:
                direction, risk = "안정", "낮음"
            note = f"월 이상 {vals[-1]}건 (평균 {mean:.0f}), 추세 {direction}"
            items.append({
                "id": eq["id"], "name": eq["name"], "icon": eq["icon"],
                "indicator_label": "월 이상 건수", "series": series,
                "direction": direction, "risk": risk,
                "projection": None, "note": note,
            })
        else:
            items.append({
                "id": eq["id"], "name": eq["name"], "icon": eq["icon"],
                "indicator_label": "월 이상 건수", "series": [],
                "direction": "데이터 부족", "risk": "낮음",
                "projection": None, "note": "추세 판단을 위한 데이터가 부족합니다.",
            })

    risk_order = {"높음": 0, "보통": 1, "낮음": 2}
    items.sort(key=lambda x: risk_order.get(x["risk"], 9))
    return {"anchor": anchor.isoformat(), "months": months, "items": items}


@router.get("/predictive")
def predictive_maintenance(months: int = Query(8, ge=3, le=24)):
    """설비별 추세 기반 예지보전 위험 예측."""
    return compute_predictive(months)


# ══════════════════════════════════════════════════════════════════
#  정비 작업지시 (Work Orders) — 진단 → 조치 → 이력
# ══════════════════════════════════════════════════════════════════

from fastapi import Body  # noqa: E402

_wo_ready = False
_WO_STATUS = ("open", "in_progress", "done")


def _ensure_wo_table(conn):
    global _wo_ready
    if _wo_ready:
        return
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS work_orders (
            id             SERIAL PRIMARY KEY,
            equipment_id   TEXT,
            equipment_name TEXT,
            title          TEXT,
            cause          TEXT,
            action         TEXT,
            priority       TEXT DEFAULT 'MEDIUM',
            status         TEXT DEFAULT 'open',
            assignee       TEXT,
            outcome_note   TEXT,
            created_at     TIMESTAMPTZ DEFAULT NOW(),
            updated_at     TIMESTAMPTZ DEFAULT NOW(),
            resolved_at    TIMESTAMPTZ
        );
    """)
    conn.commit()
    _wo_ready = True


def _wo_row(r) -> dict:
    return {
        "id": r[0], "equipment_id": r[1], "equipment_name": r[2], "title": r[3],
        "cause": r[4], "action": r[5], "priority": r[6], "status": r[7],
        "assignee": r[8], "outcome_note": r[9],
        "created_at":  r[10].isoformat() if r[10] else None,
        "updated_at":  r[11].isoformat() if r[11] else None,
        "resolved_at": r[12].isoformat() if r[12] else None,
    }


_WO_COLS = ("id, equipment_id, equipment_name, title, cause, action, priority, "
            "status, assignee, outcome_note, created_at, updated_at, resolved_at")


def insert_work_order(equipment_id=None, equipment_name=None, title=None,
                      cause=None, action=None, priority="MEDIUM") -> dict:
    """작업지시 1건 생성 (라우트·챗 에이전트 공용)."""
    with _db_conn() as conn:
        _ensure_wo_table(conn)
        cur = conn.cursor()
        cur.execute(
            f"""
            INSERT INTO work_orders (equipment_id, equipment_name, title, cause, action, priority)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING {_WO_COLS};
            """,
            (equipment_id, equipment_name, title or "정비 작업지시",
             cause, action, (priority or "MEDIUM").upper()),
        )
        row = cur.fetchone()
        conn.commit()
    return _wo_row(row)


@router.post("/work-orders")
def create_work_order(body: dict = Body(...)):
    """진단 결과로부터 정비 작업지시 생성."""
    return insert_work_order(
        equipment_id=body.get("equipment_id"),
        equipment_name=body.get("equipment_name"),
        title=body.get("title"),
        cause=body.get("cause"),
        action=body.get("action"),
        priority=body.get("priority", "MEDIUM"),
    )


@router.get("/work-orders")
def list_work_orders(
    status: str | None = Query(None),
    equipment_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    """작업지시 목록."""
    with _db_conn() as conn:
        _ensure_wo_table(conn)
        cur = conn.cursor()
        clauses, params = [], []
        if status:
            clauses.append("status = %s"); params.append(status)
        if equipment_id:
            clauses.append("equipment_id = %s"); params.append(equipment_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        cur.execute(
            f"""
            SELECT {_WO_COLS} FROM work_orders
            {where}
            ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END,
                     CASE priority WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END,
                     created_at DESC
            LIMIT %s;
            """,
            tuple(params),
        )
        items = [_wo_row(r) for r in cur.fetchall()]
    return {"count": len(items), "items": items}


@router.post("/work-orders/{wo_id}/status")
def update_work_order(wo_id: int, body: dict = Body(...)):
    """작업지시 상태 전환 (open→in_progress→done). done이면 완료 메모·시각 기록."""
    new_status = (body.get("status") or "").lower()
    if new_status not in _WO_STATUS:
        return {"error": f"잘못된 상태: {new_status}"}
    resolved = "NOW()" if new_status == "done" else "NULL"
    with _db_conn() as conn:
        _ensure_wo_table(conn)
        cur = conn.cursor()
        cur.execute(
            f"""
            UPDATE work_orders
            SET status = %s,
                outcome_note = COALESCE(%s, outcome_note),
                assignee = COALESCE(%s, assignee),
                updated_at = NOW(),
                resolved_at = {resolved}
            WHERE id = %s
            RETURNING {_WO_COLS};
            """,
            (new_status, body.get("outcome_note"), body.get("assignee"), wo_id),
        )
        row = cur.fetchone()
        conn.commit()
    if row is None:
        return {"error": "작업지시를 찾을 수 없습니다."}
    return _wo_row(row)


@router.get("/work-orders/stats")
def work_order_stats():
    """상태별 작업지시 건수 요약."""
    with _db_conn() as conn:
        _ensure_wo_table(conn)
        cur = conn.cursor()
        cur.execute("SELECT status, COUNT(*) FROM work_orders GROUP BY status;")
        by = {s: 0 for s in _WO_STATUS}
        for s, c in cur.fetchall():
            by[s] = c
    return {"open": by["open"], "in_progress": by["in_progress"], "done": by["done"],
            "total": sum(by.values())}
