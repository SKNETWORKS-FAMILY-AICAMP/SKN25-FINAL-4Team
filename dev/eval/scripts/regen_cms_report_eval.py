"""
CMS·Report eval 기준 데이터를 현재 DB 값으로 갱신.

CMS  : compute_equipment_status()에서 실시간 헬스 스코어·이상 건수 조회
Report: monthly_report 테이블에서 최신 KPI 값 조회

실행:
  backend/.venv/bin/python dev/eval/scripts/regen_cms_report_eval.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend" / "src"))

from dotenv import load_dotenv
load_dotenv(str(Path(__file__).resolve().parents[3] / ".env"))

import psycopg2

DB_URL    = os.getenv("DATABASE_URL")
DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "ems_eval_evidence_97.json"


# ── Report: monthly_report 테이블 캐시 ──────────────────────────────

def _load_monthly_report() -> dict:
    """period → row dict 매핑."""
    conn = psycopg2.connect(DB_URL)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT period, total_consumption_kwh, self_sufficiency_pct,
                   chp_kwh, pv_kwh, grid_dependency_pct, avg_cop
            FROM monthly_report ORDER BY period;
        """)
        cols = ["period","total_consumption_kwh","self_sufficiency_pct",
                "chp_kwh","pv_kwh","grid_dependency_pct","avg_cop"]
        return {str(r[0])[:7]: dict(zip(cols, r)) for r in cur.fetchall()}
    finally:
        conn.close()


_METRIC_MAP = {
    "self_sufficiency_pct":    ("self_sufficiency_pct", "%"),
    "total_consumption_kwh":   ("total_consumption_kwh", "kWh"),
    "chp_kwh":                 ("chp_kwh", "kWh"),
    "pv_kwh":                  ("pv_kwh", "kWh"),
    "grid_dependency_pct":     ("grid_dependency_pct", "%"),
    "avg_cop":                 ("avg_cop", ""),
}

_METRIC_LABEL_KO = {
    "self_sufficiency_pct":    "자급률",
    "total_consumption_kwh":   "총 전력 소비량",
    "chp_kwh":                 "CHP 전력 생산량",
    "pv_kwh":                  "태양광 발전량",
    "grid_dependency_pct":     "계통 의존도",
    "avg_cop":                 "COP 평균",
}


def update_report_item(item: dict, monthly: dict) -> bool:
    ctx = item["reference_context"]
    period_m = re.search(r"period=(\d{4}년 \d{1,2}월)", ctx)
    metric_m  = re.search(r"metric=(\S+)", ctx)
    if not period_m or not metric_m:
        return False

    period_str = period_m.group(1)  # "2023년 12월"
    metric     = metric_m.group(1)

    # "2023년 12월" → "2023-12"
    ym = re.search(r"(\d{4})년 (\d{1,2})월", period_str)
    if not ym:
        return False
    key = f"{ym.group(1)}-{int(ym.group(2)):02d}"

    row = monthly.get(key)
    if row is None:
        return False

    col, unit = _METRIC_MAP.get(metric, (None, None))
    if col is None:
        return False

    val = row.get(col)
    if val is None:
        return False

    # reference_context 수치 교체
    new_ctx = re.sub(r"value=[\d.]+", f"value={val}", ctx)
    item["reference_context"] = new_ctx

    # reference_answer 생성
    label = _METRIC_LABEL_KO.get(metric, metric)
    if unit == "%":
        val_str = f"{val:.2f}%"
    elif unit == "kWh":
        val_str = f"{val:,.2f} kWh"
    else:
        val_str = f"{val:.3f}"

    item["reference_answer"] = f"{period_str}의 {label}은 {val_str}입니다."
    return True


# ── CMS: compute_equipment_status 실시간 조회 ──────────────────────

def _load_equipment_status() -> dict:
    """equipment_id → {health_score, anomaly_total, status} 매핑."""
    try:
        from api.routers.cms import compute_equipment_status
        data = compute_equipment_status()
        return {it["id"]: it for it in data.get("items", [])}
    except Exception as e:
        print(f"  [CMS 상태 조회 실패] {e}")
        return {}


def update_cms_item(item: dict, eq_status: dict) -> bool:
    ctx = item["reference_context"]
    eq_m     = re.search(r"equipment_id=(\S+)", ctx)
    score_m  = re.search(r"health_score=(\d+)", ctx)
    anomaly_m = re.search(r"recent_anomaly_count=(\d+)", ctx)

    if not eq_m:
        return False

    eq_id = eq_m.group(1)
    it = eq_status.get(eq_id)
    if it is None:
        return False

    new_score   = it["health_score"]
    new_anomaly = it["anomaly_total"]
    new_status  = it["status"]

    changed = False
    if score_m and int(score_m.group(1)) != new_score:
        ctx = ctx.replace(f"health_score={score_m.group(1)}", f"health_score={new_score}")
        changed = True
    if anomaly_m and int(anomaly_m.group(1)) != new_anomaly:
        ctx = ctx.replace(f"recent_anomaly_count={anomaly_m.group(1)}", f"recent_anomaly_count={new_anomaly}")
        changed = True

    item["reference_context"] = ctx

    # reference_answer 재생성 (헬스 스코어 포함)
    old_ans = item["reference_answer"]
    # 헬스 스코어 수치 교체
    new_ans = re.sub(r"헬스 스코어는 \d+", f"헬스 스코어는 {new_score}", old_ans)
    new_ans = re.sub(r"최근 이상은 \d+건", f"최근 이상은 {new_anomaly}건", new_ans)
    item["reference_answer"] = new_ans

    return changed


def main():
    data: list[dict] = json.load(open(DATA_PATH, encoding="utf-8"))
    monthly = _load_monthly_report()
    print(f"monthly_report 로드: {len(monthly)}개월")

    eq_status = _load_equipment_status()
    print(f"equipment status 로드: {len(eq_status)}개 설비")

    report_changed = cms_changed = 0

    for item in data:
        cat = item["category"]
        if cat == "report":
            if update_report_item(item, monthly):
                report_changed += 1
        elif cat == "cms":
            if update_cms_item(item, eq_status):
                cms_changed += 1
                print(f"  [CMS] {item['id']}: {item['query'][:50]}")

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n완료: report {report_changed}건 수치 변경, CMS {cms_changed}건 수치 변경")
    print(f"저장: {DATA_PATH.name}")


if __name__ == "__main__":
    main()
