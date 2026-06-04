"""
CMS Agent — 설비 상태/진단/정비 작업지시 질문에 답한다.

오케스트레이터의 'cms' 의도로 라우팅되며, api.routers.cms의 공용 헬퍼
(compute_equipment_status / run_diagnosis)와 work_orders 테이블을 재사용한다.
답변은 rag_answer에 실어 critic이 집어가도록 한다(forecast 에이전트와 동일 패턴).
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))   # src 경로 (api.* 임포트용)

# 설비 키워드 → id
_EQ_KEYWORDS = [
    ("grid",    re.compile(r"계통|수전|변압기|grid")),
    ("cooling", re.compile(r"냉방|냉각|칠러|cop|cooling")),
    ("chp",     re.compile(r"열병합|chp")),
    ("pv",      re.compile(r"태양|pv|솔라|solar")),
]
_WO_KW   = re.compile(r"작업\s*지시.*(목록|현황|조회|확인|있어|보여|알려)|미해결|조치\s*내역|점검\s*이력|work\s*order")
_DIAG_KW = re.compile(r"진단|원인|왜|이유|점검|조치|분석|해석|평가|검토|어때|어떤가|살펴|어떻게\s*됐")
_PRED_KW = re.compile(r"예지보전|언제.*고장|잔여\s*수명|수명|추세|악화|예측.*위험|위험.*예측")

# ── 행동(액션) 트리거 — 코파일럿이 실제로 실행 ───────────────────
_ACT_WO    = re.compile(r"작업\s*지시.*(생성|만들|등록|발행|추가|올려|작성)|(생성|만들|등록|발행|작성)\s*해.*작업\s*지시")
_ACT_SIM   = re.compile(r"시뮬|시뮬레이터|시연")
_SIM_START = re.compile(r"시작|재생|돌려|run|play|진행")
_SIM_PAUSE = re.compile(r"정지|멈춰|일시정지|pause|stop|중단")
_DATE_ISO  = re.compile(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})")
_DATE_KOR  = re.compile(r"(?:(20\d{2})\s*년\s*)?(\d{1,2})\s*월\s*(\d{1,2})\s*일")


def _extract_date(q: str) -> str | None:
    m = _DATE_ISO.search(q)
    if m:
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        return f"{y}-{mo:02d}-{d:02d}"
    m = _DATE_KOR.search(q)
    if m:
        y = m.group(1) or "2023"
        return f"{y}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


def _detect_equipment(q: str) -> str | None:
    ql = q.lower()
    for eq_id, pat in _EQ_KEYWORDS:
        if pat.search(ql):
            return eq_id
    return None


def _fmt_work_orders() -> str:
    from api.db import get_conn
    from api.routers.cms import _ensure_wo_table, _wo_row, _WO_COLS
    with get_conn() as conn:
        _ensure_wo_table(conn)
        cur = conn.cursor()
        cur.execute("SELECT status, COUNT(*) FROM work_orders GROUP BY status;")
        by = {"open": 0, "in_progress": 0, "done": 0}
        for s, c in cur.fetchall():
            by[s] = c
        cur.execute(
            f"""SELECT {_WO_COLS} FROM work_orders
                ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END,
                         created_at DESC LIMIT 6;""")
        rows = [_wo_row(r) for r in cur.fetchall()]
    open_n = by["open"] + by["in_progress"]
    lines = [
        "## 🔧 정비 작업지시 현황",
        f"- **미해결 {open_n}건** (열림 {by['open']} · 진행중 {by['in_progress']}) · 완료 {by['done']} · 전체 {sum(by.values())}",
    ]
    if rows:
        label = {"open": "열림", "in_progress": "진행중", "done": "완료"}
        lines.append("\n**최근 작업지시**")
        for w in rows:
            lines.append(f"- [{label.get(w['status'], w['status'])}] {w['equipment_name']} · {w['title']}")
    else:
        lines.append("\n등록된 작업지시가 없습니다. 설비 진단 후 작업지시를 생성할 수 있습니다.")
    return "\n".join(lines)


def _fmt_status_overall() -> str:
    from api.routers.cms import compute_equipment_status
    data = compute_equipment_status()
    items = data.get("items", [])
    lines = [f"## 🏭 설비 상태 요약 (기준 {data.get('anchor','')[:10]})"]
    for it in items:   # 이미 헬스 낮은 순 정렬
        lines.append(
            f"- {it['icon']} **{it['name']}**: 헬스 {it['health_score']} ({it['status']}) "
            f"· 최근 30일 이상 {it['anomaly_total']}건"
        )
    worst = items[0] if items else None
    if worst and worst["status"] != "정상":
        lines.append(f"\n가장 주의가 필요한 설비는 **{worst['name']}**입니다. \"{worst['name']} 진단해줘\"라고 물어보세요.")
    return "\n".join(lines)


def _fmt_status_one(eq_id: str) -> str:
    from api.routers.cms import compute_equipment_status
    data = compute_equipment_status()
    it = next((x for x in data.get("items", []) if x["id"] == eq_id), None)
    if not it:
        return _fmt_status_overall()
    c = it["counts"]
    mv = f"{it['metric_value']}{it['unit']}" if it.get("metric_value") is not None else "–"
    return (
        f"## {it['icon']} {it['name']} 상태\n"
        f"- 헬스 스코어: **{it['health_score']} ({it['status']})**\n"
        f"- 최근 30일 이상: {it['anomaly_total']}건 (심각 {c['HIGH']} / 주의 {c['MEDIUM']} / 경미 {c['LOW']})\n"
        f"- 현재 {it['metric_label']}: {mv}\n\n"
        f"원인·조치가 궁금하면 \"{it['name']} 진단해줘\"라고 물어보세요."
    )


def _fmt_predictive() -> str:
    from api.routers.cms import compute_predictive
    data = compute_predictive()
    lines = ["## 🔮 예지보전 — 추세 기반 위험 예측", "_(현 추세 외삽 · 참고용)_\n"]
    for it in data.get("items", []):   # 위험 높은 순 정렬
        mark = {"악화": "▲", "개선": "▼", "안정": "▬"}.get(it["direction"], "")
        lines.append(f"- {it['icon']} **{it['name']}** · 위험 {it['risk']} {mark} {it['direction']} — {it['note']}")
    return "\n".join(lines)


def _fmt_diagnosis(eq_id: str) -> str:
    from api.routers.cms import run_diagnosis
    res = run_diagnosis(eq_id)
    if res.get("error"):
        return res["error"]
    name = res.get("equipment", {}).get("name", "설비")
    icon = res.get("equipment", {}).get("icon", "🔧")
    return f"## {icon} {name} AI 진단\n\n{res.get('diagnosis','')}"


def _do_create_work_order(eq_id: str | None) -> str:
    """진단 기반 작업지시를 실제로 생성 (상태 변경 액션)."""
    from api.routers.cms import (
        compute_equipment_status, run_diagnosis, insert_work_order, _equipment_by_id,
    )
    items = compute_equipment_status().get("items", [])
    if not eq_id:
        # 설비 미지정 → 가장 취약한 설비 (이미 헬스 낮은 순 정렬)
        if not items:
            return "설비 정보를 찾지 못해 작업지시를 만들 수 없습니다."
        eq_id = items[0]["id"]
    eq = _equipment_by_id(eq_id)
    if not eq:
        return f"알 수 없는 설비입니다: {eq_id}"
    it = next((x for x in items if x["id"] == eq_id), None)
    status = it["status"] if it else "주의"
    priority = {"경고": "HIGH", "주의": "MEDIUM", "정상": "LOW"}.get(status, "MEDIUM")
    diag = run_diagnosis(eq_id)
    wo = insert_work_order(
        equipment_id=eq_id, equipment_name=eq["name"],
        title=f"{eq['name']} 정비 작업",
        cause=f"헬스 {it['health_score'] if it else '-'} ({status}) · AI 코파일럿 생성",
        action=diag.get("diagnosis", ""), priority=priority,
    )
    return (
        f"✅ **{eq['name']} 작업지시**를 생성했습니다 (#{wo['id']}, 우선순위 {priority}).\n"
        f"AI 진단 결과를 조치 내용으로 첨부했습니다. **정비 작업지시** 탭에서 진행·완료할 수 있습니다."
    )


def _do_simulator(q: str) -> str:
    """시뮬레이터 제어 (상태 변경 액션) — 시크/시작/정지."""
    from datetime import datetime
    from api.routers.simulator import clock
    date = _extract_date(q)
    acted = []
    if date:
        try:
            clock.seek(datetime.fromisoformat(f"{date}T00:00:00"))
            acted.append(f"{date}로 이동")
        except Exception:
            return f"날짜를 해석하지 못했습니다: {date}"
    if _SIM_PAUSE.search(q):
        clock.pause(); acted.append("일시정지")
    elif _SIM_START.search(q) or date:   # 날짜로 이동하면 보통 재생도 원함
        clock.start(); acted.append("재생 시작")
    if not acted:
        return '시뮬레이터 명령을 이해하지 못했습니다. 예: "시뮬레이터 2023-02-08로 가서 시작해줘"'
    return f"✅ 시뮬레이터: {', '.join(acted)}. 현재 시각 **{clock.now.strftime('%Y-%m-%d %H:%M')}**"


def run(state: dict) -> dict:
    q = state.get("question", "")
    ctx = state.get("context") or {}
    # 질문에서 설비를 못 찾으면 현재 보던 화면의 설비를 컨텍스트로 사용
    eq_id = _detect_equipment(q) or ctx.get("equipment_id")

    # ── 행동(액션) 우선 ──
    if _ACT_WO.search(q):
        answer = _do_create_work_order(eq_id)
    elif _ACT_SIM.search(q) or _extract_date(q):
        answer = _do_simulator(q)
    # ── 조회 ──
    elif _WO_KW.search(q):
        answer = _fmt_work_orders()
    elif _PRED_KW.search(q):
        answer = _fmt_predictive()
    elif _DIAG_KW.search(q):
        if eq_id:
            answer = _fmt_diagnosis(eq_id)
        else:
            # 설비 미지정 진단 요청 → 가장 취약한 설비 진단
            from api.routers.cms import compute_equipment_status
            items = compute_equipment_status().get("items", [])
            if items and items[0]["status"] != "정상":
                answer = _fmt_diagnosis(items[0]["id"])
            else:
                answer = _fmt_status_overall()
    elif eq_id:
        answer = _fmt_status_one(eq_id)
    else:
        answer = _fmt_status_overall()

    return {**state, "rag_answer": answer}


def langgraph_node(state: dict) -> dict:
    return run(state)
