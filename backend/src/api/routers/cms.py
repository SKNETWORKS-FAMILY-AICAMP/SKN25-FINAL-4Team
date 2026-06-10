"""
CMS (Condition Monitoring System) 설비 상태 라우터.

기능별 설비 단위로 헬스 스코어와 상태를 산출한다.
설비 ↔ 이상 유형 매핑으로 최근 이상 이력을 가중 집계하고,
로더에서 현재 핵심 지표를 끌어와 카드에 함께 노출한다.

설비 마스터는 손으로 시드하지 않고 로더의 기능별 미터 그룹(계통/PV/CHP/냉방)에서 도출한다.
"""
import re
import sys
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Query

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from api.db import get_conn as _db_conn  # noqa: E402

router = APIRouter(prefix="/cms", tags=["cms"])

# 설비 마스터 데이터는 api.config 모듈의 get_equipment_list()를 사용합니다.

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
        from api.config import get_equipment_list
        for eq in get_equipment_list():
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
    from api.config import get_equipment_list
    return next((e for e in get_equipment_list() if e["id"] == eq_id), None)


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
                FROM reference.corrected_resampled_1h
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
        lines.append(f"- {atype}: 심각(HIGH) {c['HIGH']}건 / 주의(MEDIUM) {c['MEDIUM']}건 / 경미(LOW) {c['LOW']}건")
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

이상이 0건이고 전기 시그니처도 정상 범위면 "현재 정상 — 특이 이상 없음" 한 줄로만 답하세요.

## 📝 모범 진단 예시
### 🩺 진단 요약
최근 30일간 PowerSpike 3건이 발생했으며, 3상 전류 불평형이 12.5%로 주의 수준입니다. 변압기 부하 편중으로 인한 효율 저하가 우려됩니다.

### 🔍 추정 원인
- 특정 상(Phase)에 단상 부하가 집중되어 전류 불평형 12.5% 발생 (10% 정상 범주 초과).
- 2024-07-18 10:00 실측 1,250kW로 일시적 피크(PowerSpike) 동반 발생.

### ✅ 권장 조치
- 현장 정비팀: 분전반 단상 부하 결선 상태 확인 후 상별 균등 분배.
- 시설 관리팀: 역률 보상용 콘덴서 뱅크 정상 동작 여부 점검.
""")
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
            "실행 가능한 진단을 작성합니다. 질문 언어와 관계없이 항상 한국어로만 답변하세요.\n\n" + ANOMALY_DOMAIN_PROMPT
        )
        # 발전 설비(PV·CHP)는 역률 음수/저값이 정상 — 오진단 방지
        if eq.get("id") in ("pv", "chp"):
            system += (
                "\n\n⚠️ 현재 진단 대상은 발전 설비(태양광/열병합)입니다. "
                "역률이 음수이거나 낮은 값이면 전력을 역송(생산)하고 있는 정상 상태입니다. "
                "반드시 '정상(역송)' 으로 명시하고, 역률 저하 문제로 기술하지 마세요."
            )
        prompt = _build_diag_prompt(eq, window_days, total, by_type, examples, sig_str)

        # ── few-shot: 설비 유형별 예시 선택 ──────────────────────────
        # 계절 정상 케이스: 이상 0건 + 외기온 영향으로 COP 기준 미달이지만 정상
        fs_seasonal_user = (
            "다음은 '냉방 시스템' 설비의 최근 30일 이상 탐지 결과입니다.\n"
            "[이상 요약] 총 0건\n"
            "[전기 시그니처] 전압 398V, 전류 36A, 역률 0.88, COP 1.85 (연간 중앙값 2.06)\n"
            "[환경 정보] 외기온 35.2°C (평년 8월 평균 28°C 대비 +7.2°C)\n"
            "위 데이터와 도메인 지식을 근거로 이 설비의 상태를 진단하세요."
        )
        fs_seasonal_assistant = (
            "### 🩺 진단 요약\n"
            "이상탐지 0건 — 현 시점 계절 정상 범위입니다. "
            "COP 1.85는 연간 중앙값(2.06) 대비 낮지만, 외기온 35.2°C(평년 대비 +7.2°C)인 한여름 조건을 고려하면 정상 허용 범위(1.7~2.0)에 해당합니다.\n\n"
            "### 🔍 추정 원인\n"
            "- COP 1.85: 외기온 35.2°C → 응축기 열방출 부하 증가로 인한 계절적 효율 저하 (이상 아님)\n"
            "- 전기 시그니처(전압 398V, 역률 0.88) 모두 정상 범위\n\n"
            "### ✅ 권장 조치\n"
            "- 즉각 조치 불필요 — 정상 운영 유지\n"
            "- 외기온 정상화(30°C 이하) 후에도 COP 2.0 미만이면 냉매 충전량 점검 예약"
        )
        if eq.get("id") in ("pv", "chp"):
            # 발전 설비: 역률 음수 = 정상(역송) 예시
            fs_user = (
                "다음은 '태양광(PV)' 설비의 최근 30일 이상 탐지 결과입니다.\n"
                "[이상 요약] 총 1건\n"
                "- PVNightNonZero: 심각(HIGH) 0건 / 주의(MEDIUM) 0건 / 경미(LOW) 1건\n"
                "[전기 시그니처] 전압 401V, 역률 -0.94 (역송 상태), 발전량 8,800kWh/월\n"
                "위 데이터와 도메인 지식을 근거로 이 설비의 상태를 진단하세요."
            )
            fs_assistant = (
                "### 🩺 진단 요약\n"
                "역률 -0.94는 태양광 인버터가 전력을 계통으로 역송(발전) 중인 정상 상태입니다. "
                "경미한 야간 발전 감지 1건을 제외하면 전반적으로 정상 운영 중입니다.\n\n"
                "### 🔍 추정 원인\n"
                "- 역률 -0.94: 발전 설비 정상 상태 — 인버터가 전력을 계통으로 공급 중 (이상 아님)\n"
                "- PVNightNonZero 경미(LOW) 1건: 야간 미세 발전 감지 — 인버터 절전 모드 점검 권고\n\n"
                "### ✅ 권장 조치\n"
                "- 역률 관련 조치 불필요 (정상 역송 상태)\n"
                "- 야간 인버터 출력 로그 확인 (경미 우선순위, 즉각 조치 불필요)"
            )
        else:
            # 소비 설비: 역률 저하·전류 과부하 예시
            fs_user = (
                "다음은 '냉방 시스템' 설비의 최근 30일 이상 탐지 결과입니다.\n"
                "[이상 요약] 총 8건\n"
                "- COPDrop: 심각(HIGH) 2건 / 주의(MEDIUM) 3건 / 경미(LOW) 3건\n"
                "[전기 시그니처] 전압 381V(-4.8%), 전류 42A(+10.5%), 역률 0.72\n"
                "위 데이터와 도메인 지식을 근거로 이 설비의 상태를 진단하세요."
            )
            fs_assistant = (
                "### 🩺 진단 요약\n"
                "최근 30일간 총 8건의 이상이 감지됐으며, 이 중 심각(HIGH) 2건이 발생했습니다. "
                "역률 0.72 저하와 전류 +10.5% 과부하가 동시 발생 중으로, "
                "역률개선 콘덴서 열화 또는 과부하 운전이 의심됩니다.\n\n"
                "### 🔍 추정 원인\n"
                "- 역률 0.72 (기준 0.85↑): 역률개선 콘덴서 열화 또는 탈락 — 무효전력 과다\n"
                "- 전류 42A (+10.5%): 권선 부분 단락 또는 과부하 운전 가능성\n"
                "- 전압 381V (-4.8%): 계통 전압 저하로 인한 전류 상승 연쇄 추정\n\n"
                "### ✅ 권장 조치\n"
                "- 역률개선 콘덴서 커패시턴스 측정 및 교체 여부 확인\n"
                "- 절연 저항 측정으로 권선 단락 여부 점검\n"
                "- 부하 감소 후 전류 재측정하여 정격 복귀 확인"
            )

        diagnosis = llm_chat(
            [
                {"role": "system",    "content": system},
                {"role": "user",      "content": fs_seasonal_user},
                {"role": "assistant", "content": fs_seasonal_assistant},
                {"role": "user",      "content": fs_user},
                {"role": "assistant", "content": fs_assistant},
                {"role": "user",      "content": prompt},
            ],
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


def _ensure_anomaly_columns(conn) -> None:
    """anomaly_results에 v84 컬럼이 없으면 추가 (최초 1회)."""
    cur = conn.cursor()
    for col, dtype in [("actual_w", "FLOAT"), ("predicted_w", "FLOAT"),
                       ("residual_w", "FLOAT"), ("source", "TEXT"), ("gateway_failure", "BOOLEAN")]:
        cur.execute(f"ALTER TABLE anomaly_results ADD COLUMN IF NOT EXISTS {col} {dtype};")
    conn.commit()


def compute_equipment_status(window_days: int = _WINDOW_DAYS) -> dict:
    """설비별 헬스 스코어 + 상태 + 최근 이상 요약 + 현재 핵심 지표. (라우트·에이전트 공용)"""
    from api.config import get_equipment_list
    with _db_conn() as conn:
        anchor = _anchor_now(conn)
        window_start = anchor - timedelta(days=window_days)
        cur = conn.cursor()

        items = []
        for eq in get_equipment_list():
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
def get_equipment(window_days: int = Query(_WINDOW_DAYS, ge=1, le=180)):
    """프론트엔드 노출용 설비 목록 반환."""
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


def _linreg_fit(values: list[float]) -> tuple[float, float]:
    """x=[0,1,...]에 대해 y=slope*x + intercept 피팅."""
    n = len(values)
    if n < 2:
        return 0.0, values[0] if values else 0.0
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(values) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return 0.0, my
    slope = sum((xs[i] - mx) * (values[i] - my) for i in range(n)) / den
    intercept = my - slope * mx
    return slope, intercept


def _exponential_decay_months_until(values: list[float], limit: float) -> float | None:
    """
    y = a * e^(-b * x) 피팅 후 y가 limit에 도달하기까지 남은 step 수 반환.
    ln(y) = ln(a) - b * x
    """
    pos_values = [max(v, 1e-5) for v in values]
    log_values = [math.log(v) for v in pos_values]
    slope, intercept = _linreg_fit(log_values)
    if slope >= 0:
        return None
    a = math.exp(intercept)
    b = -slope
    if a <= limit:
        return 0.0
    x_crit = (math.log(a) - math.log(limit)) / b
    x_curr = len(values) - 1
    return max(x_crit - x_curr, 0.0)


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
        from api.config import get_equipment_list as _get_equipment_list
        _equipment_list = _get_equipment_list()
        anomaly_series = {}
        for eq in _equipment_list:
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

    for eq in _equipment_list:
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
            if current > _COP_CRITICAL:
                # 1. 선형 외삽 개월 수
                m_linear = (current - _COP_CRITICAL) / (-slope) if slope < -0.005 else None
                # 2. 지수 감쇠 외삽 개월 수
                m_decay = _exponential_decay_months_until(vals, _COP_CRITICAL)
                
                # 둘 중 더 보수적인(작은) 남은 수명 선택
                candidates = [c for c in [m_linear, m_decay] if c is not None and c > 0]
                if candidates:
                    m = min(candidates)
                    if 0 < m <= 36:
                        # 3. 계절 보수 보정: 예측 범위 내에 여름철(7, 8월)이 낄 때마다 RUL 10%씩 단축 (최대 25%)
                        try:
                            curr_month = anchor.month
                            summer_months = 0
                            for step in range(1, int(m) + 1):
                                future_m = (curr_month + step - 1) % 12 + 1
                                if future_m in (7, 8):
                                    summer_months += 1
                            m_adjusted = m * (1.0 - min(summer_months * 0.1, 0.25))
                            m = round(m_adjusted, 1)
                        except Exception:
                            m = round(m, 1)
                        projection = f"현 추세 지속 시 약 {m}개월 후 COP {_COP_CRITICAL} 하회 예상 (지수감쇠 및 계절보정 적용)"
            
            risk = "높음" if (current < 1.7 or (projection and direction == "악화" and ("개월" in projection) and current > _COP_CRITICAL and float(re.search(r"약\s*([\d\.]+)\s*개월", projection).group(1) if re.search(r"약\s*([\d\.]+)\s*개월", projection) else 99) <= 6)) \
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
        cur = conn.cursor()
        cur.execute("SELECT status, COUNT(*) FROM work_orders GROUP BY status;")
        by = {s: 0 for s in _WO_STATUS}
        for s, c in cur.fetchall():
            by[s] = c
    return {"open": by["open"], "in_progress": by["in_progress"], "done": by["done"],
            "total": sum(by.values())}
