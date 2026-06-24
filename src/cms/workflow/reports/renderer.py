"""Deterministic user-facing report renderer.

The renderer consumes `context_pack.v2` and produces the same JSON/Markdown
contract whether or not an sLLM path is enabled elsewhere.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

FORBIDDEN_CAUSE_TERMS = ("원인", "때문", "likely", "cause", "caused", "because")
DEVELOPER_REPORT_TERMS = (
    "worker",
    "queue",
    "heartbeat",
    "Airflow task",
    "container",
    "PC process",
    "warning_flag=true",
    "deterministic_fallback",
    "generation_mode",
    "Guard",
    "Wiki/RAG",
)


def render_deterministic_report(context_pack: Mapping[str, Any]) -> dict[str, Any]:
    meta = _mapping(context_pack.get("report_meta"))
    observed = _mapping(context_pack.get("observed_summary"))
    pmax = _mapping(context_pack.get("pmax"))
    inspection = _sequence(context_pack.get("inspection_candidates"))
    raw_actions = context_pack.get("operator_actions")
    actions = (
        [str(item) for item in raw_actions if str(item).strip()]
        if isinstance(raw_actions, Sequence) and not isinstance(raw_actions, (str, bytes, bytearray))
        else []
    )
    title = _title(meta)
    executive_summary = _executive_summary(meta, observed, inspection)
    sections = [
        {
            "key": "usage_patterns",
            "title": "사용 패턴",
            "body": _usage_pattern_text(observed),
        },
        {
            "key": "key_meters",
            "title": "주요 계량기",
            "body": _key_meter_text(observed),
            "items": _sequence(observed.get("top_meters"))[:10],
        },
        {
            "key": "inspection_candidates",
            "title": "점검 후보",
            "body": _inspection_text(inspection),
            "items": inspection[:10],
        },
        {
            "key": "cost_peak",
            "title": "비용·피크 관리",
            "body": _peak_text(observed, pmax),
        },
        {
            "key": "forecast_reference",
            "title": "참고: 단기 예측 상태",
            "body": _forecast_reference_text(pmax),
            "display": "collapsed_reference",
        },
    ]
    limitations = _limitations(context_pack)
    markdown = _markdown(title, executive_summary, sections, actions, limitations)
    report_json = {
        "schema_version": "report_payload.v2",
        "title": title,
        "executive_summary": executive_summary,
        "sections": sections,
        "operator_actions": actions,
        "limitations": limitations,
    }
    guard_result = validate_user_report_contract(report_json=report_json, markdown=markdown, context_pack=context_pack)
    if not guard_result["ok"]:
        raise ValueError("user report contract failed: " + ", ".join(guard_result["violations"]))
    return {
        "title": title,
        "executive_summary": executive_summary,
        "markdown": markdown,
        "report_json": report_json,
        "summary": executive_summary,
        "pmax_commentary": _peak_text(observed, pmax),
        "anomaly_commentary": _inspection_text(inspection),
        "operator_actions": actions,
        "limitations": limitations,
        "guard_result": guard_result,
    }


def validate_user_report_contract(*, report_json: Mapping[str, Any], markdown: str, context_pack: Mapping[str, Any]) -> dict[str, Any]:
    violations: list[str] = []
    text = markdown + "\n" + str(report_json)
    for term in DEVELOPER_REPORT_TERMS:
        if term.lower() in text.lower():
            violations.append(f"developer_term:{term}")
    wiki_context = context_pack.get("wiki_context")
    if not wiki_context:
        cause_hits = [term for term in FORBIDDEN_CAUSE_TERMS if term.lower() in text.lower()]
        if cause_hits:
            violations.append("cause_claim_without_wiki:" + ",".join(cause_hits))
    sections = _sequence(report_json.get("sections"))
    required = {"usage_patterns", "key_meters", "inspection_candidates", "cost_peak"}
    found = {str(section.get("key")) for section in sections if isinstance(section, Mapping)}
    missing = sorted(required - found)
    if missing:
        violations.append("missing_sections:" + ",".join(missing))
    if not report_json.get("operator_actions"):
        violations.append("missing_operator_actions")
    return {"ok": not violations, "violations": violations}


def _title(meta: Mapping[str, Any]) -> str:
    cadence = {"daily": "일간", "weekly": "주간", "monthly": "월간"}.get(str(meta.get("cadence")), "운영")
    start = meta.get("period_start") or "기간 시작 미확인"
    end = meta.get("period_end") or "기간 종료 미확인"
    return f"{cadence} 운영 보고서 {start} ~ {end}"


def _executive_summary(meta: Mapping[str, Any], observed: Mapping[str, Any], inspection: Sequence[Mapping[str, Any]]) -> str:
    peak = _mapping(observed.get("period_peak"))
    count = observed.get("meter_count", 0)
    if peak:
        peak_text = f"실측 기준 최고 피크는 {peak.get('meter_urn', '계량기 미확인')}의 {_fmt(peak.get('peak_value'))} kW입니다."
    else:
        peak_text = "실측 피크 데이터가 아직 없습니다."
    return f"이 보고서는 실측/피처 데이터를 우선해 {count}개 계량기의 사용 패턴을 요약합니다. {peak_text} 점검 후보는 {len(inspection)}건입니다."


def _usage_pattern_text(observed: Mapping[str, Any]) -> str:
    patterns = _sequence(observed.get("usage_patterns"))
    if not patterns:
        return "반복적으로 두드러진 시간대 패턴은 확인되지 않았습니다."
    labels = [f"{item.get('hour')}시 평균 피크 {_fmt(item.get('avg_peak_value'))} kW" for item in patterns[:3]]
    return "피크가 큰 시간대는 " + ", ".join(labels) + " 순입니다."


def _key_meter_text(observed: Mapping[str, Any]) -> str:
    top = _sequence(observed.get("top_meters"))
    if not top:
        return "주요 계량기 피크를 계산할 데이터가 없습니다."
    labels = [f"{item.get('meter_urn')} {_fmt(item.get('peak_value'))} kW" for item in top[:5]]
    return "주요 계량기 피크: " + ", ".join(labels)


def _inspection_text(rows: Sequence[Mapping[str, Any]]) -> str:
    if not rows:
        return "실제 이상 경고로 분류된 점검 후보가 없습니다."
    first = rows[0]
    return f"우선 확인 후보는 {first.get('meter_urn')}이며, 분류는 {first.get('reason_class')}입니다. 전체 후보는 {len(rows)}건입니다."


def _peak_text(observed: Mapping[str, Any], pmax: Mapping[str, Any]) -> str:
    peak = _mapping(observed.get("period_peak"))
    if not peak:
        return "피크 관리를 위한 실측 기준값이 없습니다."
    return f"비용·피크 관리는 {peak.get('target_ts') or peak.get('peak_ts') or peak.get('window_ts')} 구간의 {_fmt(peak.get('peak_value'))} kW를 우선 확인합니다."


def _forecast_reference_text(pmax: Mapping[str, Any]) -> str:
    forecast_series = _sequence(pmax.get("forecast_series"))
    if not forecast_series:
        return "단기 예측 참고 데이터가 없습니다."
    return f"단기 예측은 참고용으로만 표시합니다. 예측 계열 수: {len(forecast_series)}."


def _limitations(context_pack: Mapping[str, Any]) -> list[str]:
    notes = ["제공된 실측 자료와 점검 후보를 기준으로 작성했습니다."]
    observed = _mapping(context_pack.get("observed_summary"))
    notes.extend(str(item) for item in _sequence(observed.get("data_quality_notes"))[:3])
    if not context_pack.get("wiki_context"):
        notes.append("추가 문서 근거가 없어 관측 사실과 점검 후보만 표시합니다.")
    return notes


def _markdown(title: str, summary: str, sections: Sequence[Mapping[str, Any]], actions: Sequence[str], limitations: Sequence[str]) -> str:
    parts = [f"# {title}", "## 핵심 요약\n" + summary]
    for section in sections:
        if section.get("key") == "forecast_reference":
            parts.append("## 참고: 단기 예측 상태\n" + str(section.get("body", "")))
        else:
            parts.append(f"## {section.get('title')}\n{section.get('body', '')}")
    parts.append("## 운영자 조치\n" + "\n".join(f"- {item}" for item in actions))
    parts.append("## 한계\n" + "\n".join(f"- {item}" for item in limitations))
    return "\n\n".join(parts)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _fmt(value: Any) -> str:
    try:
        return f"{float(value):,.1f}"
    except (TypeError, ValueError):
        return "–"


__all__ = ["render_deterministic_report", "validate_user_report_contract"]
