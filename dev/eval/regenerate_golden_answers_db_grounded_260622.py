#!/usr/bin/env python3
"""Regenerate 260622 golden answers with DB/DW/DM evidence.

Grounding policy follows docs/experiment_metrics_260619.html:
- anomaly: mart.anomaly_warning_1h + ontology.meter_context
- cms/actionable facility status: ops.work_order + mart.anomaly_warning_1h + ontology.meter_context
- forecast: mart.pmax_forecast_15min / mart.peak_feature_15min + ops reports when available
- report: ops.daily_report / weekly_report / monthly_report
- rag/domain QA: ontology.meter_context / ontology.measurement_code + reference.corrected_resampled_1h numeric facts

No target LLM is called by this script. The output still includes the key
`llm_model_call_latency_ms` in `generation_metrics`; it is null when no LLM call
was used, and `llm_call_used=false` makes that explicit.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT.parents[1]
ENV_PATH = PROJECT / ".env"
INPUT = ROOT / "dev/eval/data/router_two_stage_eval_300_with_qa60_260622.json"
OUTPUT = ROOT / "dev/eval/data/router_two_stage_eval_300_with_qa60_golden_answers_260622.json"

ROUTE1_LABELS = {"query", "multi_intent", "action_request", "approval_required", "off_topic"}
ROUTE2_LABELS = {"anomaly", "cms", "forecast", "report", "domain"}

SEASONS = {
    "봄": ("2023-03-01", "2023-06-01"),
    "여름": ("2023-06-01", "2023-09-01"),
    "가을": ("2023-09-01", "2023-12-01"),
    "겨울": ("2023-12-01", "2024-03-01"),
}

REASON_KO = {
    "LOW_LOAD_VS_USUAL_HOUR": "평소 시간대 대비 저부하",
    "HIGH_LOAD_VS_USUAL_HOUR": "평소 시간대 대비 고부하",
    "INPUT_QUALITY_ISSUE": "입력 품질 이슈",
    "KNOWN_METER_ISSUE": "알려진 계량기 이슈",
    "NO_PREDICTION": "예측값 없음",
    "NONE": "정상 범위",
}


def load_env(path: Path) -> dict[str, str]:
    env = {}
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env

ENV = load_env(ENV_PATH)


def psql_json(sql: str) -> tuple[list[dict[str, Any]], float]:
    wrapper = "select coalesce(json_agg(t), '[]'::json) from (\n" + sql.strip().rstrip(";") + "\n) t;"
    cmd = [
        "psql",
        "-h", ENV["DB_HOST"], "-p", ENV.get("DB_PORT", "5432"),
        "-U", ENV["DB_USER"], "-d", ENV["DB_NAME"], "-X", "-q", "-At",
        "-v", "ON_ERROR_STOP=1",
    ]
    run_env = os.environ.copy()
    run_env["PGPASSWORD"] = ENV["DB_PASSWORD"]
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, input=wrapper, text=True, capture_output=True, env=run_env, timeout=180)
    ms = round((time.perf_counter() - t0) * 1000, 2)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    out = proc.stdout.strip() or "[]"
    return json.loads(out), ms


def sql_lit(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def clean_msg(msg: str) -> str:
    msg = re.sub(r"\s+", " ", msg).strip()
    msg = re.sub(r" — (?:rag|cms|forecast|report) 평가 케이스 \d+ 기준으로 답해줘\.?$", "", msg)
    msg = re.sub(r" — 요청번호 \d+로 처리해줘\.?$", "", msg)
    return msg.replace("필요성가", "필요성이").replace("필요성를", "필요성을").replace("자급률가", "자급률이").replace("역률가", "역률이").replace("사용량가", "사용량이").replace("전력가", "전력이")


def fmt_int(n: Any) -> str:
    try:
        return f"{int(n):,}"
    except Exception:
        return "0"


def fmt_num(v: Any, nd: int = 1) -> str:
    if v is None:
        return "없음"
    try:
        return f"{float(v):,.{nd}f}"
    except Exception:
        return str(v)


def top_counts(rows: list[dict[str, Any]], key: str, val: str = "count", n: int = 3) -> str:
    parts = []
    for r in rows[:n]:
        label = str(r.get(key) or "미분류")
        if key == "warning_reason_code":
            label = REASON_KO.get(label, label)
        parts.append(f"{label} {fmt_int(r.get(val))}건")
    return ", ".join(parts) if parts else "집계 없음"


def build_static_evidence() -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    evidence["meter_context"], evidence["lat_meter_context_ms"] = psql_json(
        "select meter_urn, meter_domain, equipment_group_label, building_code, equipment_name, anomaly_priority from ontology.meter_context order by meter_urn"
    )
    evidence["measurement_code"], evidence["lat_measurement_code_ms"] = psql_json(
        "select measurement_code, description, family, aggregate_policy, missing_policy from ontology.measurement_code order by measurement_code"
    )
    evidence["work_order_summary"], evidence["lat_work_order_ms"] = psql_json(
        "select coalesce(equipment_id,'unknown') equipment_id, coalesce(equipment_name,'미지정') equipment_name, coalesce(priority,'UNKNOWN') priority, coalesce(status,'unknown') status, count(*)::int count from ops.work_order group by 1,2,3,4 order by count desc, equipment_id"
    )
    evidence["daily_latest"], evidence["lat_daily_ms"] = psql_json(
        "select date, total_consumption_kwh, self_sufficiency_pct, avg_cop, anomaly_count, grid_dependency_pct, pv_kwh, chp_kwh, peak_hour, peak_kw, summary, pmax_commentary, anomaly_commentary from ops.daily_report order by date desc limit 10"
    )
    evidence["monthly_latest"], evidence["lat_monthly_ms"] = psql_json(
        "select period, total_consumption_kwh, self_sufficiency_pct, avg_cop, anomaly_count, grid_dependency_pct, pv_kwh, chp_kwh, summary, pmax_commentary, anomaly_commentary from ops.monthly_report order by period desc limit 18"
    )
    evidence["forecast_latest"], evidence["lat_forecast_ms"] = psql_json(
        "select distinct on (logical_meter) logical_meter, source_meter_urn, target_ts, horizon_minutes, predicted_p_max from mart.pmax_forecast_15min where horizon_minutes=60 order by logical_meter, target_ts desc"
    )
    evidence["anomaly_overall"], evidence["lat_anom_overall_ms"] = psql_json(
        "select count(*)::int total_rows, count(*) filter (where warning_flag)::int warning_count, min(target_ts) min_ts, max(target_ts) max_ts from mart.anomaly_warning_1h"
    )
    return evidence


def meter_rows(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    return evidence["meter_context"]


def meter_by_urn(evidence: dict[str, Any], urn: str) -> dict[str, Any] | None:
    for m in meter_rows(evidence):
        if m["meter_urn"] == urn:
            return m
    return None


def measurement_desc(mc: dict[str, Any] | None) -> str:
    if not mc:
        return "계측값"
    code = mc.get("measurement_code")
    mapping = {
        "P": "전력 또는 열량 계열의 순간 출력값",
        "P1": "L1 상 전력값",
        "PF": "전체 역률",
        "PF1": "L1 상 역률",
        "U1": "L1 상 전압값",
        "I1": "L1 상 전류값",
    }
    return mapping.get(code, str(mc.get("description") or "계측값"))


def measurement_by_code(evidence: dict[str, Any], code: str) -> dict[str, Any] | None:
    for m in evidence["measurement_code"]:
        if m["measurement_code"] == code:
            return m
    return None


def concept_filter(msg: str) -> tuple[str, str]:
    msg = clean_msg(msg)
    # return human label, SQL condition using alias mc and aw
    if "CHP" in msg or "열병합" in msg:
        return "CHP 관련 계량기", "(mc.equipment_group_label ilike '%열병합%' or mc.equipment_name ilike '%CHP%')"
    if "PV" in msg or "태양광" in msg:
        return "PV 발전 전력 계량기", "(mc.equipment_group_label ilike '%태양광%' or mc.equipment_name ilike '%PV%')"
    if "서버실 냉방" in msg:
        return "서버실 냉방 계량기", "(mc.equipment_group_label ilike '%서버%' and (mc.equipment_name ilike '%cooling%' or mc.equipment_group_label ilike '%냉방%'))"
    if "서버 전력" in msg:
        return "서버 전력 계량기", "(mc.equipment_group_label ilike '%서버%')"
    if "배출가스 시험실 냉방" in msg or "HVAC" in msg:
        return "배출가스 시험실 냉방/HVAC 계량기", "(mc.building_code='H1' and (mc.equipment_group_label ilike '%공조%' or mc.equipment_group_label ilike '%냉방%' or mc.equipment_name ilike '%HVAC%'))"
    if "배출가스 시험실 시험장비" in msg:
        return "배출가스 시험실 시험장비 전력 계량기", "(mc.building_code='H1' and mc.equipment_group_label ilike '%배출%')"
    if "냉방 열량" in msg:
        return "냉방 열량 계량기", "(mc.equipment_group_label ilike '%냉방 열%')"
    if "냉방 장치" in msg:
        return "냉방 장치 관련 계량기", "(mc.equipment_group_label ilike '%냉방%' or mc.equipment_name ilike '%cooling%' or mc.equipment_name ilike '%HVAC%')"
    if "열 계량기" in msg:
        return "열 계량기", "(mc.meter_domain='thermal')"
    # explicit meter
    m = re.search(r"([A-Z][A-Z0-9]*(?:\.[A-Z0-9]+)+x?|PF1)", msg)
    if m:
        urn = m.group(1)
        if urn.endswith("x"):
            prefix = urn[:-1]
            return urn, f"(aw.meter_urn = {sql_lit(prefix)} or aw.meter_urn like {sql_lit(prefix+'%')})"
        return urn, f"aw.meter_urn = {sql_lit(urn)}"
    return "전체 계량기", "true"


def season_filter(msg: str) -> tuple[str | None, str]:
    for s, (start, end) in SEASONS.items():
        if s in msg:
            return s, f"aw.target_ts >= {sql_lit(start)}::timestamptz and aw.target_ts < {sql_lit(end)}::timestamptz"
    if "2023년" in msg:
        return "2023년", "aw.target_ts >= '2023-01-01'::timestamptz and aw.target_ts < '2024-01-01'::timestamptz"
    return None, "true"


def anomaly_answer(row: dict[str, Any]) -> tuple[str, dict[str, Any], float]:
    msg = row["message"]
    label, cfilter = concept_filter(msg)
    season, sfilter = season_filter(msg)
    base = f"""
        from mart.anomaly_warning_1h aw
        left join ontology.meter_context mc on mc.meter_urn = aw.meter_urn
        where aw.warning_flag = true and {cfilter} and {sfilter}
    """
    summary_sql = f"select count(*)::int total, count(distinct aw.meter_urn)::int meters, min(aw.target_ts) min_ts, max(aw.target_ts) max_ts {base}"
    by_month_sql = f"select to_char(date_trunc('month', aw.target_ts), 'YYYY-MM') as month_label, count(*)::int count {base} group by 1 order by 1"
    by_reason_sql = f"select aw.warning_reason_code, count(*)::int count {base} group by 1 order by count desc limit 3"
    by_meter_sql = f"select aw.meter_urn, coalesce(mc.equipment_group_label,'미분류') equipment_group_label, count(*)::int count {base} group by 1,2 order by count desc limit 3"
    t0 = time.perf_counter()
    summary, l1 = psql_json(summary_sql)
    by_month, l2 = psql_json(by_month_sql)
    by_reason, l3 = psql_json(by_reason_sql)
    by_meter, l4 = psql_json(by_meter_sql)
    lat = round((time.perf_counter() - t0) * 1000, 2)
    s = summary[0] if summary else {"total": 0, "meters": 0}
    period = season or "전체 기간"
    total = int(s.get("total") or 0)
    if total == 0:
        ans = f"{period} 기준 {label}에서 확인된 이상 경보는 0건입니다. 동일 조건에서 경보가 발생한 계량기도 없어 유형별 분포는 없습니다. 운영 화면에서는 기간과 계량기 범위를 바꿔 다시 확인하는 것이 좋습니다."
    else:
        months = top_counts(by_month, "month_label")
        reasons = top_counts(by_reason, "warning_reason_code")
        meters = top_counts(by_meter, "meter_urn")
        ans = f"{period} 기준 {label}의 이상 경보는 총 {fmt_int(total)}건이며, 관련 계량기는 {fmt_int(s.get('meters'))}개입니다. 월별 분포는 {months} 순이고, 주요 원인은 {reasons}입니다. 계량기별로는 {meters} 순입니다."
    ev = {"sources": ["mart.anomaly_warning_1h", "ontology.meter_context"], "concept": label, "period": period, "summary": s, "by_month": by_month, "by_reason": by_reason, "by_meter": by_meter, "db_query_latency_ms": round(l1+l2+l3+l4,2)}
    return ans, ev, lat


def cms_answer(row: dict[str, Any]) -> tuple[str, dict[str, Any], float]:
    msg = clean_msg(row["message"])
    concept = "전체 설비"
    cond = "true"
    if "태양광" in msg:
        concept, cond = "태양광 설비", "mc.equipment_group_label ilike '%태양광%'"
        eq_id = "pv"
    elif "냉동기" in msg or "냉방" in msg:
        concept, cond = "냉방 설비", "(mc.equipment_group_label ilike '%냉방%' or mc.equipment_name ilike '%cooling%')"
        eq_id = "cooling"
    elif "변압기" in msg or "수전" in msg:
        concept, cond = "수전/변압기 설비", "mc.equipment_group_label ilike '%수전%'"
        eq_id = "grid"
    elif "보일러" in msg or "CHP" in msg:
        concept, cond = "열병합/열 설비", "(mc.equipment_group_label ilike '%열%' or mc.equipment_name ilike '%CHP%')"
        eq_id = "chp"
    else:
        eq_id = None
    anom_sql = f"""
        select count(*)::int warning_count, count(distinct aw.meter_urn)::int affected_meters
        from mart.anomaly_warning_1h aw left join ontology.meter_context mc on mc.meter_urn=aw.meter_urn
        where aw.warning_flag=true and aw.target_ts >= (select max(target_ts) - interval '7 days' from mart.anomaly_warning_1h) and {cond}
    """
    wo_sql = "select coalesce(priority,'UNKNOWN') priority, coalesce(status,'unknown') status, count(*)::int count from ops.work_order"
    if eq_id:
        wo_sql += f" where equipment_id={sql_lit(eq_id)}"
    wo_sql += " group by 1,2 order by count desc"
    t0 = time.perf_counter(); anom,l1=psql_json(anom_sql); wo,l2=psql_json(wo_sql); lat=round((time.perf_counter()-t0)*1000,2)
    a=anom[0] if anom else {"warning_count":0,"affected_meters":0}
    wcnt=sum(int(x.get('count') or 0) for x in wo)
    topwo=top_counts(wo,"priority") if wo else "등록 작업 없음"
    topwo=topwo.replace('LOW','낮음').replace('MEDIUM','보통').replace('HIGH','높음')
    risk = "주의" if int(a.get('warning_count') or 0)>0 or wcnt>0 else "정상 관찰"
    ans=f"{concept}의 최근 기준 상태는 {risk}입니다. 최근 7일 이상 경보는 {fmt_int(a.get('warning_count'))}건, 관련 계량 지점은 {fmt_int(a.get('affected_meters'))}개이고, 진행 중인 점검 작업은 {fmt_int(wcnt)}건({topwo})입니다."
    ev={"sources":["mart.anomaly_warning_1h","ops.work_order","ontology.meter_context"],"concept":concept,"recent_7d_anomaly":a,"work_orders":wo,"db_query_latency_ms":round(l1+l2,2)}
    return ans, ev, lat


def forecast_answer(row: dict[str, Any], static: dict[str, Any]) -> tuple[str, dict[str, Any], float]:
    msg=clean_msg(row['message'])
    logical=None
    if 'V.Z82' in msg: logical='V.Z82'
    elif 'H2.Z35' in msg: logical='H2.Z35x'
    elif 'H2.Z36' in msg: logical='H2.Z36x'
    rows=static['forecast_latest']
    latest=[x for x in rows if x.get('logical_meter')==logical] if logical else []
    ev={"sources":["mart.pmax_forecast_15min","ops.daily_report","ops.monthly_report"],"forecast_latest":rows,"db_query_latency_ms":round(float(static.get('lat_forecast_ms') or 0)+float(static.get('lat_daily_ms') or 0)+float(static.get('lat_monthly_ms') or 0),2)}
    # Long-horizon or unsupported metrics should not reuse the same short-term pmax answer.
    unsupported = []
    if '자급률' in msg: unsupported.append('자급률')
    if 'COP' in msg: unsupported.append('COP')
    if '역률' in msg: unsupported.append('역률')
    long_horizon = any(t in msg for t in ['다음 달','다음 주','월간','주간'])
    if unsupported:
        metric = '·'.join(unsupported)
        ans=f"현재 확인 가능한 예측값은 60분 단기 피크 전력 전망입니다. {metric} 예측값은 현재 확인되지 않습니다."
        return ans, ev, float(ev['db_query_latency_ms'])
    if long_horizon and '60분' not in msg and '다음 구간' not in msg:
        ans="현재 확인 가능한 예측값은 60분 단기 피크 전력 전망입니다. 월간·주간 예측값은 현재 확인되지 않습니다."
        return ans, ev, float(ev['db_query_latency_ms'])
    if latest:
        f=latest[0]
        ans=f"{f['logical_meter']}의 60분 뒤 피크 전력 예측값은 {fmt_num(f.get('predicted_p_max'),1)}입니다. 예측 대상 시각은 {f.get('target_ts')}입니다."
        ev={"sources":["mart.pmax_forecast_15min"],"forecast":f,"db_query_latency_ms":static.get('lat_forecast_ms', 0)}
        return ans, ev, float(static.get('lat_forecast_ms') or 0)
    if rows:
        top=max(rows, key=lambda x: float(x.get('predicted_p_max') or 0))
        avg=sum(float(x.get('predicted_p_max') or 0) for x in rows)/len(rows)
        ans=f"현재 확인 가능한 60분 단기 피크 전력 전망은 {len(rows)}개 예측 대상 계량 지점 기준입니다. 평균 예측 피크는 {fmt_num(avg,1)}이고, 가장 높은 전망은 {top.get('logical_meter')}의 {fmt_num(top.get('predicted_p_max'),1)}입니다."
    else:
        ans="현재 확인 가능한 단기 예측값이 없습니다."
    return ans, ev, float(ev['db_query_latency_ms'])


def report_answer(row: dict[str, Any], static: dict[str, Any]) -> tuple[str, dict[str, Any], float]:
    msg=clean_msg(row['message'])
    m=re.search(r"(20\d{2})년\s*(\d{1,2})월", msg)
    ev={"sources":["ops.daily_report","ops.monthly_report"]}
    if m:
        period=f"{m.group(1)}-{int(m.group(2)):02d}"
        rows,lat=psql_json(f"select period,total_consumption_kwh,self_sufficiency_pct,avg_cop,anomaly_count,grid_dependency_pct,pv_kwh,chp_kwh,summary,pmax_commentary,anomaly_commentary from ops.monthly_report where period={sql_lit(period)}")
        ev.update({"period":period,"monthly_report":rows,"db_query_latency_ms":lat})
        if rows:
            r=rows[0]
            ans=f"{period} 월간 보고서 기준 총 사용량은 {fmt_num(r.get('total_consumption_kwh'),1)}kWh, 자급률은 {fmt_num(r.get('self_sufficiency_pct'),1)}%, 평균 COP는 {fmt_num(r.get('avg_cop'),2)}입니다. 이상 건수는 {fmt_int(r.get('anomaly_count'))}건, 외부 전력 의존도는 {fmt_num(r.get('grid_dependency_pct'),1)}%로 확인됩니다."
        else:
            ans=f"{period} 월간 보고서 데이터는 현재 확인되지 않습니다."
        return ans, ev, float(ev.get('db_query_latency_ms') or 0)
    latest=static['daily_latest'][0] if static['daily_latest'] else None
    recent=static['daily_latest'][:7]
    ev.update({"daily_latest":latest,"recent_daily":recent,"monthly_latest":static['monthly_latest'][:3],"db_query_latency_ms":round(float(static.get('lat_daily_ms') or 0)+float(static.get('lat_monthly_ms') or 0),2)})
    if not latest:
        return "현재 일간 보고서 데이터가 확인되지 않습니다.", ev, float(ev['db_query_latency_ms'])
    if '지난달' in msg and static['monthly_latest']:
        monthly_valid=[x for x in static['monthly_latest'] if x.get('total_consumption_kwh') is not None]
        mr=monthly_valid[0] if monthly_valid else static['monthly_latest'][0]
        if mr.get('total_consumption_kwh') is None:
            ans="최근 월간 보고서 데이터는 현재 확인되지 않습니다."
        else:
            ans=f"최근 월간 보고서 기준 기간은 {mr.get('period')}이며 총 사용량은 {fmt_num(mr.get('total_consumption_kwh'),1)}kWh, 자급률은 {fmt_num(mr.get('self_sufficiency_pct'),1)}%, 평균 COP는 {fmt_num(mr.get('avg_cop'),2)}입니다. 이상 건수는 {fmt_int(mr.get('anomaly_count'))}건입니다."
    elif '3일' in msg:
        recent3=static['daily_latest'][:3]
        total=sum(float(r.get('total_consumption_kwh') or 0) for r in recent3)
        anomalies=sum(int(r.get('anomaly_count') or 0) for r in recent3)
        peak=max(recent3, key=lambda r: float(r.get('peak_kw') or 0)) if recent3 else latest
        ans=f"최근 3일 기준 총 사용량은 {fmt_num(total,1)}kWh, 이상 건수는 총 {fmt_int(anomalies)}건입니다. 가장 높은 피크는 {peak.get('date')} {peak.get('peak_hour')}시 {fmt_num(peak.get('peak_kw'),1)}kW입니다."
    elif '7일' in msg or '최근 7' in msg:
        total=sum(float(r.get('total_consumption_kwh') or 0) for r in recent)
        anomalies=sum(int(r.get('anomaly_count') or 0) for r in recent)
        avg_cop=sum(float(r.get('avg_cop') or 0) for r in recent)/len(recent) if recent else 0
        ans=f"최근 7일 기준 총 사용량은 {fmt_num(total,1)}kWh, 평균 COP는 {fmt_num(avg_cop,2)}입니다. 이상 건수는 총 {fmt_int(anomalies)}건입니다."
    elif '리스크' in msg:
        ans=f"최근 일간 보고서 기준 운영 리스크 지표는 이상 건수 {fmt_int(latest.get('anomaly_count'))}건, 피크 {latest.get('peak_hour')}시 {fmt_num(latest.get('peak_kw'),1)}kW입니다. 외부 전력 의존도는 {fmt_num(latest.get('grid_dependency_pct'),1)}%입니다."
    elif '개선' in msg:
        ans=f"최근 일간 보고서 기준 개선 검토 지표는 자급률 {fmt_num(latest.get('self_sufficiency_pct'),1)}%, 평균 COP {fmt_num(latest.get('avg_cop'),2)}, 외부 전력 의존도 {fmt_num(latest.get('grid_dependency_pct'),1)}%입니다."
    else:
        ans=f"최근 일간 보고서 기준일은 {latest.get('date')}이며 총 사용량은 {fmt_num(latest.get('total_consumption_kwh'),1)}kWh, 자급률은 {fmt_num(latest.get('self_sufficiency_pct'),1)}%, 평균 COP는 {fmt_num(latest.get('avg_cop'),2)}입니다. 이상 건수는 {fmt_int(latest.get('anomaly_count'))}건, 피크는 {latest.get('peak_hour')}시에 {fmt_num(latest.get('peak_kw'),1)}kW로 확인됩니다."
    return ans, ev, float(ev['db_query_latency_ms'])


def rag_answer(row: dict[str, Any], static: dict[str, Any]) -> tuple[str, dict[str, Any], float]:
    msg=clean_msg(row['message'])
    m=re.search(r"([A-Z][A-Z0-9]*(?:\.[A-Z0-9]+)+x?|PF1)", msg)
    ev={"sources":["ontology.meter_context","ontology.measurement_code","reference.corrected_resampled_1h"]}
    if 'COP' in msg:
        ans="COP는 냉방이나 열 생산 설비가 투입 에너지 대비 얼마나 효율적으로 열을 처리했는지를 보는 효율 지표입니다. 값이 높을수록 같은 에너지로 더 많은 냉방·열 처리를 한 것으로 해석합니다. 보고서 KPI에서는 COP를 전력·열량 계측값과 함께 관리합니다."
        ev.update({"concept":"COP","db_query_latency_ms":round(float(static.get('lat_measurement_code_ms') or 0)+float(static.get('lat_daily_ms') or 0),2)})
        return ans, ev, float(ev['db_query_latency_ms'])
    if '역률' in msg or 'PF1' in msg:
        mc=measurement_by_code(static,'PF1') or measurement_by_code(static,'PF')
        ans="역률은 전력이 얼마나 유효하게 사용되는지를 나타내는 지표이며, 낮으면 같은 일을 하는 데 더 큰 전류가 필요해 손실과 설비 부담이 커질 수 있습니다. PF/PF1은 평균과 범위 품질검사를 함께 적용하는 전력 품질 계측값입니다. 낮은 역률이 반복되면 부하 특성, 보상 설비, 특정 시간대의 전력 사용 패턴을 함께 확인합니다."
        ev.update({"measurement_code":mc,"db_query_latency_ms":float(static.get('lat_measurement_code_ms') or 0)})
        return ans, ev, float(ev['db_query_latency_ms'])
    if m:
        urn=m.group(1)
        if urn.endswith('x'):
            candidates=[x for x in static['meter_context'] if x['meter_urn'].startswith(urn[:-1])]
            ctx=candidates[0] if candidates else None
        else:
            ctx=meter_by_urn(static,urn)
        if ctx:
            meas,lat=psql_json(f"select measurement, count(*)::int points, min(ts) min_ts, max(ts) max_ts, avg(value) avg_value from reference.corrected_resampled_1h where meter_urn={sql_lit(ctx['meter_urn'])} group by measurement order by points desc limit 3")
            ev.update({"meter_context":ctx,"measurement_stats":meas,"db_query_latency_ms":lat})
            stats=top_counts(meas,'measurement','points')
            domain_label=({'electricity':'전력','thermal':'열','weather':'기상'}.get(ctx.get('meter_domain'), ctx.get('meter_domain')))
            equipment = f", 설비명은 {ctx.get('equipment_name')}" if ctx.get('equipment_name') else ""
            ans=f"{urn}은 {ctx.get('equipment_group_label')} 계열의 {domain_label} 계량기입니다{equipment}. 1시간 단위 운영 데이터에서는 주요 측정값이 {stats} 순으로 확인됩니다."
        else:
            ans=f"{urn}은 현재 계량기 정보에서 직접 확인되지 않습니다. 현재 확인 가능한 정보만으로는 계량기 용도와 설비명을 확정할 수 없습니다."
            ev.update({"meter":urn,"db_query_latency_ms":float(static.get('lat_meter_context_ms') or 0)})
        return ans, ev, float(ev.get('db_query_latency_ms') or 0)
    # generic measurement value meaning
    code='U1' if '전압' in msg else 'I1' if '전류' in msg else 'PF' if '역률' in msg else 'P'
    mc=measurement_by_code(static,code)
    ans=f"{code} 값은 {measurement_desc(mc)}을 의미합니다. 운영 데이터의 값은 보정·리샘플링된 운영 계측값으로, 결측은 값 없음으로 구분하고 0은 설비 정지나 저부하 상태일 수 있습니다. 최근 평균, 피크, 반복 여부는 이상 판단에 함께 사용됩니다."
    ev.update({"measurement_code":mc,"db_query_latency_ms":float(static.get('lat_measurement_code_ms') or 0)})
    return ans, ev, float(ev['db_query_latency_ms'])


def answer_non_query(row: dict[str, Any]) -> str:
    r1=row.get('expected_route1')
    if r1=='action_request':
        return "요청하신 작업은 실제 등록, 배정, 상태 변경 전에 대상, 기한, 담당자 확인이 필요합니다. 제가 임의로 실행 완료 처리하지는 않겠습니다."
    if r1=='approval_required':
        return "요청하신 변경 또는 삭제 작업은 운영 데이터나 설비 제어에 영향을 줄 수 있어 승인 없이 바로 실행할 수 없습니다."
    if r1=='off_topic':
        return "이 요청은 공장 에너지 관리, 설비 상태, 이상탐지, 예측, 보고서 작성 범위를 벗어난 내용입니다. 저는 EMS 운영 지원 챗봇이므로 해당 주제에는 답변하지 않겠습니다. 전력 사용량, 계량기 이상, 설비 점검, 에너지 리포트와 관련된 질문으로 다시 요청해 주세요."
    if r1=='multi_intent':
        return "요청 안에 분석, 보고서 작성, 작업 등록 또는 승인 성격의 일이 함께 들어 있어 한 번에 실행하면 처리 기준이 불명확합니다. 먼저 수행할 항목을 하나로 정해 주시면 그 단계부터 안전하게 진행하겠습니다."
    return "요청 의도가 명확하지 않아 처리 목적과 대상 범위를 먼저 확인하는 것이 적절합니다."


def validate_payload(payload: dict[str, Any]) -> list[str]:
    issues=[]; rows=payload['rows']
    ids=[r['id'] for r in rows]
    if len(ids)!=len(set(ids)): issues.append('duplicate ids')
    for r in rows:
        if not r.get('reference_answer'): issues.append(f"empty answer {r.get('id')}")
        if r.get('expected_route1')=='query':
            if r.get('expected_route2') not in ROUTE2_LABELS: issues.append(f"bad route2 {r.get('id')}")
            if not r.get('answer_evidence'): issues.append(f"missing evidence {r.get('id')}")
        else:
            if r.get('expected_route2') is not None: issues.append(f"nonquery route2 {r.get('id')}")
        gm=r.get('generation_metrics') or {}
        if 'llm_model_call_latency_ms' not in gm: issues.append(f"missing llm latency {r.get('id')}")
    banned=['DB/DW/DM','BERTScore','source_url','Nature','논문','raw enum','KNOWN_','LOW_LOAD','HIGH_LOAD','route:','expected_','JSON','데이터셋']
    text='\n'.join(r.get('reference_answer','') for r in rows)
    for b in banned:
        if re.search(re.escape(b), text, re.I): issues.append(f'banned term {b}')
    return issues



def uniquify_user_message(row: dict[str, Any], seen: set[str]) -> None:
    """Avoid duplicate user-facing messages after removing eval suffixes."""
    msg = str(row.get('message', ''))
    if msg not in seen:
        seen.add(msg)
        return
    rid = str(row.get('id', ''))
    label = row.get('expected_route2')
    if label == 'domain':
        if 'H2.Z35x는 무엇을 측정하는 계량기야?' == msg:
            msg = 'H2.Z35x의 주요 측정값은 뭐야?'
        elif 'P1.K03는 무엇을 측정하는 계량기야?' == msg:
            msg = 'P1.K03 계량기 정보를 확인해줘.'
        elif 'V.Z84는 무엇을 측정하는 계량기야?' == msg:
            msg = 'V.Z84 계량기 용도를 알려줘.'
        elif msg == 'COP 계산 방식 설명해줘':
            variants = ['COP가 무엇을 의미하는지 설명해줘.', 'COP 값이 높다는 건 무슨 뜻이야?', 'COP 값이 낮으면 어떤 의미야?', 'COP를 에너지 효율 지표로 보는 이유가 뭐야?', '냉방 설비에서 COP는 어떻게 해석해?', '보고서에서 COP는 어떤 KPI야?']
            idx = int(re.sub(r'\D', '', rid)[-2:] or '0') % len(variants)
            msg = variants[idx]
        elif msg == '역률이 낮다는 건 설비 관점에서 무슨 뜻이야?':
            variants = ['역률이 낮으면 전력 설비에 어떤 부담이 생겨?', 'PF1 값은 어떤 의미야?', '역률은 전력 품질에서 어떤 지표야?', '역률 저하는 어떤 상태를 의미해?', '전력 품질에서 PF/PF1을 어떻게 보면 돼?', '낮은 역률이 반복되면 무엇을 확인해?']
            idx = int(re.sub(r'\D', '', rid)[-2:] or '0') % len(variants)
            msg = variants[idx]
        elif '전압 값' in msg:
            msg = 'H2.Z35x의 전압 계측값 의미를 알려줘.'
    elif label == 'forecast':
        msg = msg.replace('다음 주 보일러 부하가 증가할 가능성이 있어?', '다음 주 보일러 부하 전망을 확인해줘.')
        msg = msg.replace('태양광 구역 에너지 사용량 60분 뒤 전망 알려줘', '태양광 구역 60분 뒤 피크 전력 전망 알려줘')
        msg = msg.replace('forecast: P1.K03 다음 구간 사용량 예측해줘', 'P1.K03 다음 구간 피크 전력 예측해줘')
    elif label == 'report':
        msg = msg.replace('지난 3일 개선 포인트와 운영 리스크를 각각 3개 도출해줘', '지난 3일 운영 리스크를 요약해줘')
    # If still duplicated, add a natural object hint rather than an eval suffix.
    if msg in seen:
        msg = f"{msg} ({rid[-3:]})"
    row['message'] = msg
    seen.add(msg)

def main() -> None:
    src=json.loads(INPUT.read_text(encoding='utf-8'))
    rows=src['rows']
    static=build_static_evidence()
    out=[]; query_count=0; db_latency_total=0.0
    seen_messages: set[str] = set()
    for r in rows:
        nr=dict(r)
        nr['message'] = clean_msg(str(nr.get('message', '')))
        if nr.get('expected_route2') == 'rag':
            nr['expected_route2'] = 'domain'
        if isinstance(nr.get('expected_final_action'), str) and nr['expected_final_action'] == 'route:rag':
            nr['expected_final_action'] = 'route:domain'
        if isinstance(nr.get('id'), str):
            nr['id'] = nr['id'].replace('R2S2-Q-RAG-', 'R2S2-Q-DOMAIN-')
        uniquify_user_message(nr, seen_messages)
        if nr.get('expected_route1')=='query':
            query_count+=1
            route2=nr.get('expected_route2')
            if route2=='anomaly': ans,ev,lat=anomaly_answer(nr)
            elif route2=='cms': ans,ev,lat=cms_answer(nr)
            elif route2=='forecast': ans,ev,lat=forecast_answer(nr,static)
            elif route2=='report': ans,ev,lat=report_answer(nr,static)
            elif route2=='domain': ans,ev,lat=rag_answer(nr,static)
            else: ans,ev,lat=("요청을 처리할 서비스 라벨이 명확하지 않습니다.",{},0.0)
            nr['reference_answer']=ans
            nr['answer_evidence']=ev
            db_latency_total += float((ev or {}).get('db_query_latency_ms') or lat or 0)
        else:
            nr['reference_answer']=answer_non_query(nr)
        nr.pop('reference_answer_gpt55', None); nr.pop('gpt55_generation', None); nr.pop('reference_answer_golden', None); nr.pop('golden_generation', None)
        nr['generation_metrics']={
            'method':'db_grounded_deterministic' if nr.get('expected_route1')=='query' else 'policy_template',
            'llm_call_used': False,
            'llm_model': None,
            'llm_model_call_latency_ms': None,
            'db_query_latency_ms': round(float((nr.get('answer_evidence') or {}).get('db_query_latency_ms') or 0),2),
        }
        out.append(nr)
    payload={
        'schema_version':'router-two-stage-eval.v6_qa60_contained_db_grounded_golden_answers_260622',
        'generated_at':datetime.now(timezone.utc).isoformat(),
        'source_dataset':str(INPUT.relative_to(ROOT)),
        'reference_policy':'reference_answer is the canonical golden-standard answer. Query answers are grounded in PostgreSQL runtime DB/DW/DM sources defined in docs/experiment_metrics_260619.html; non-query answers use service gate policy templates. LLM latency key is present under generation_metrics; it is null when no LLM call was used.',
        'evidence_policy':{'runtime_sources':['ops','mart','reference','ontology','ops.energy_doc'], 'html_spec':'docs/experiment_metrics_260619.html'},
        'summary':{
            'row_count':len(out),
            'query_answer_db_grounded_count':query_count,
            'route1_distribution':dict(Counter(r.get('expected_route1') for r in out)),
            'route2_distribution_on_query':dict(Counter(r.get('expected_route2') for r in out if r.get('expected_route1')=='query')),
            'qa_subset_count':sum(1 for r in out if r.get('qa_subset')),
            'reference_answer_nonempty_count':sum(1 for r in out if r.get('reference_answer')),
            'llm_call_used_count':sum(1 for r in out if (r.get('generation_metrics') or {}).get('llm_call_used')),
            'db_query_latency_ms_total_recorded':round(db_latency_total,2),
        },
        'rows':out,
    }
    issues=validate_payload(payload)
    payload['summary']['validation_issue_count']=len(issues)
    if issues: payload['validation_issues']=issues[:100]
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'output':str(OUTPUT),'rows':len(out),'issues':len(issues),'query':query_count,'size':OUTPUT.stat().st_size}, ensure_ascii=False, indent=2))

if __name__=='__main__':
    main()
