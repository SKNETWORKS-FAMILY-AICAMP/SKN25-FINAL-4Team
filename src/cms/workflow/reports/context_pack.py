"""Pure context-pack helpers for scheduled report generation.

Batch A scope: build deterministic report context, read-only/bounded SQL specs,
P-Max chart JSON, and prompt/test-lane guards. This module is intentionally
import-safe: no database, network, environment, server, or filesystem I/O.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from cms.workflow.reports.inspection_candidates import build_inspection_candidates
from cms.workflow.reports.observed_summary import build_observed_summary

PMAX_OBSERVED_FEATURE_TABLE = "mart.peak_feature_15min"
PMAX_REPORT_OUTPUT_TABLE = "mart.pmax_forecast_15min"
ANOMALY_LOG_TABLE = "ops.anomaly_log"
ANOMALY_WARNING_TABLE = "mart.anomaly_warning_1h"
ONTOLOGY_METER_CONTEXT_TABLE = "ontology.meter_context"
ONTOLOGY_METER_MEASUREMENT_CONTEXT_TABLE = "ontology.meter_measurement_context"
ONTOLOGY_METER_ROLE_TABLE = "ontology.meter_role"
WIKI_CONTEXT_TABLE = "ops.energy_doc"

UNKNOWN_LOCATION = "위치 미확인"
UNKNOWN_ROLE = "역할 미확인"
DEFAULT_CHART_UNIT = "kW"
MAX_ANOMALY_CONTEXT_ROWS = 100
MAX_WIKI_SNIPPET_CHARS = 360

Cadence = Literal["daily", "weekly", "monthly"]


@dataclass(frozen=True)
class ReportContextQuerySpec:
    """Parameterized read-only SQL contract used by report context builders."""

    name: str
    sql: str
    params: Mapping[str, Any]
    source_tables: tuple[str, ...]
    expected_columns: tuple[str, ...]
    bounded_by: tuple[str, ...]


@dataclass(frozen=True)
class PromptGuardStatus:
    """Static prompt guard verification result."""

    ok: bool
    missing_fragments: tuple[str, ...]


PROMPT_GUARD_FRAGMENTS = (
    "제공된 context pack에 없는 수치를 만들지 마세요",
    "ontology_context에 없는 위치/역할을 만들지 마세요",
    "Wiki 근거가 없으면 원인 가능성 표현을 금지",
    "이상치 원인을 단정하지 마세요",
    "P-Max 수치를 변경하지 마세요",
    "chart JSON의 값과 문장 값이 일치",
    "JSON object only",
    "JSON만 반환",
    "worker, queue, heartbeat, Airflow task, container, PC process를 사용자 보고서에 쓰지 마세요",
)


def build_ontology_context_query(meter_urns: Sequence[str]) -> ReportContextQuerySpec:
    """Build a bounded ontology lookup for explicit meter URNs only.

    The query uses a requested-meter CTE, so callers can keep unknown meters and
    apply fallback text without scanning ontology tables broadly.
    """

    meters = _dedupe_non_empty(meter_urns, "meter_urns")
    values = ", ".join(f"(%(meter_{idx})s)" for idx, _ in enumerate(meters))
    sql = f"""
WITH requested(meter_urn) AS (
  VALUES {values}
)
SELECT
  requested.meter_urn,
  mc.meter_domain,
  COALESCE(mc.meter_role_code, mmc.meter_role_code) AS meter_role_code,
  mr.role_label AS meter_role_label,
  mc.equipment_group_label,
  mc.building_code,
  mc.equipment_name,
  mc.location_label,
  mc.anomaly_priority,
  mc.sign_convention
FROM requested
LEFT JOIN {ONTOLOGY_METER_CONTEXT_TABLE} AS mc
  ON mc.meter_urn = requested.meter_urn
LEFT JOIN {ONTOLOGY_METER_MEASUREMENT_CONTEXT_TABLE} AS mmc
  ON mmc.meter_urn = requested.meter_urn
LEFT JOIN {ONTOLOGY_METER_ROLE_TABLE} AS mr
  ON mr.meter_role_code = COALESCE(mc.meter_role_code, mmc.meter_role_code)
ORDER BY requested.meter_urn
""".strip()
    return ReportContextQuerySpec(
        name="ontology_context_by_meter_urn",
        sql=sql,
        params={f"meter_{idx}": meter for idx, meter in enumerate(meters)},
        source_tables=(ONTOLOGY_METER_CONTEXT_TABLE, ONTOLOGY_METER_MEASUREMENT_CONTEXT_TABLE, ONTOLOGY_METER_ROLE_TABLE),
        expected_columns=(
            "meter_urn",
            "meter_domain",
            "meter_role_code",
            "meter_role_label",
            "equipment_group_label",
            "building_code",
            "equipment_name",
            "location_label",
            "anomaly_priority",
            "sign_convention",
        ),
        bounded_by=("meter_urns",),
    )


def build_pmax_forecast_context_query(
    *,
    period_start: datetime,
    period_end: datetime,
    logical_meters: Sequence[str] = (),
    limit: int = 5000,
) -> ReportContextQuerySpec:
    """Build a period-bounded P-Max report read query."""

    _require_period(period_start, period_end)
    if limit < 1 or limit > 20000:
        raise ValueError("limit must be between 1 and 20000")
    params: dict[str, Any] = {"period_start": period_start, "period_end": period_end, "limit": limit}
    meter_clause = ""
    bounded_by = ["target_ts_period", "limit"]
    meters = tuple(_dedupe_non_empty(logical_meters, "logical_meters", allow_empty=True))
    if meters:
        placeholders = ", ".join(f"%(logical_meter_{idx})s" for idx, _ in enumerate(meters))
        meter_clause = f"\n  AND logical_meter IN ({placeholders})"
        params.update({f"logical_meter_{idx}": meter for idx, meter in enumerate(meters)})
        bounded_by.append("logical_meters")
    sql = f"""
SELECT logical_meter, source_meter_urn, base_ts, input_end_ts, target_ts, horizon_minutes, predicted_p_max, created_at
FROM {PMAX_REPORT_OUTPUT_TABLE}
WHERE target_ts >= %(period_start)s
  AND target_ts < %(period_end)s{meter_clause}
ORDER BY logical_meter, target_ts, horizon_minutes
LIMIT %(limit)s
""".strip()
    return ReportContextQuerySpec(
        name="pmax_forecast_report_period",
        sql=sql,
        params=params,
        source_tables=(PMAX_REPORT_OUTPUT_TABLE,),
        expected_columns=("logical_meter", "source_meter_urn", "base_ts", "input_end_ts", "target_ts", "horizon_minutes", "predicted_p_max", "created_at"),
        bounded_by=tuple(bounded_by),
    )


def build_pmax_observed_feature_context_query(
    *,
    period_start: datetime,
    period_end: datetime,
    meter_urns: Sequence[str] = (),
    measurement: str = "P",
    limit: int = 5000,
) -> ReportContextQuerySpec:
    """Build a bounded observed/feature P-Max query for mart.peak_feature_15min."""

    _require_period(period_start, period_end)
    if limit < 1 or limit > 20000:
        raise ValueError("limit must be between 1 and 20000")
    measurement = measurement.strip()
    if not measurement:
        raise ValueError("measurement must be non-empty")
    params: dict[str, Any] = {"period_start": period_start, "period_end": period_end, "measurement": measurement, "limit": limit}
    meter_clause = ""
    bounded_by = ["window_ts_period", "measurement", "limit"]
    meters = tuple(_dedupe_non_empty(meter_urns, "meter_urns", allow_empty=True))
    if meters:
        placeholders = ", ".join(f"%(meter_{idx})s" for idx, _ in enumerate(meters))
        meter_clause = f"\n  AND meter_urn IN ({placeholders})"
        params.update({f"meter_{idx}": meter for idx, meter in enumerate(meters)})
        bounded_by.append("meter_urns")
    sql = f"""
SELECT window_ts, meter_urn, measurement, mean_value, max_value, min_value, p95_value, p99_value, last_value, peak_ts,
       peak_value, observed_points, expected_points, coverage_ratio, source_layer, source_mode, run_id, created_at
FROM {PMAX_OBSERVED_FEATURE_TABLE}
WHERE window_ts >= %(period_start)s
  AND window_ts < %(period_end)s
  AND measurement = %(measurement)s{meter_clause}
ORDER BY window_ts, meter_urn
LIMIT %(limit)s
""".strip()
    return ReportContextQuerySpec(
        name="pmax_observed_feature_period",
        sql=sql,
        params=params,
        source_tables=(PMAX_OBSERVED_FEATURE_TABLE,),
        expected_columns=(
            "window_ts",
            "meter_urn",
            "measurement",
            "mean_value",
            "max_value",
            "min_value",
            "p95_value",
            "p99_value",
            "last_value",
            "peak_ts",
            "peak_value",
            "observed_points",
            "expected_points",
            "coverage_ratio",
            "source_layer",
            "source_mode",
            "run_id",
            "created_at",
        ),
        bounded_by=tuple(bounded_by),
    )


def build_latest_successful_anomaly_run_query(*, period_start: datetime, period_end: datetime) -> ReportContextQuerySpec:
    """Build the first bounded anomaly query: latest successful run in period."""

    _require_period(period_start, period_end)
    sql = f"""
SELECT run_id, max(COALESCE(finished_at, started_at)) AS latest_created_at, count(*) AS log_rows
FROM {ANOMALY_LOG_TABLE}
WHERE status = %(success_status)s
  AND forecast_origin_ts >= %(period_start)s
  AND forecast_origin_ts < %(period_end)s
GROUP BY run_id
ORDER BY latest_created_at DESC NULLS LAST, run_id DESC
LIMIT 1
""".strip()
    return ReportContextQuerySpec(
        name="latest_successful_anomaly_run_for_period",
        sql=sql,
        params={"period_start": period_start, "period_end": period_end, "success_status": "success"},
        source_tables=(ANOMALY_LOG_TABLE,),
        expected_columns=("run_id", "latest_created_at", "log_rows"),
        bounded_by=("forecast_origin_ts_period", "status", "limit_1"),
    )


def build_anomaly_warning_context_query(
    *,
    run_id: str,
    period_start: datetime,
    period_end: datetime,
    meter_urns: Sequence[str] = (),
    limit: int = MAX_ANOMALY_CONTEXT_ROWS,
) -> ReportContextQuerySpec:
    """Build the second bounded anomaly query: warnings for one run_id."""

    _require_period(period_start, period_end)
    run_id = run_id.strip()
    if not run_id:
        raise ValueError("run_id must be non-empty")
    if limit < 1 or limit > 5000:
        raise ValueError("limit must be between 1 and 5000")
    params: dict[str, Any] = {"run_id": run_id, "period_start": period_start, "period_end": period_end, "limit": limit}
    meter_clause = ""
    bounded_by = ["run_id", "target_ts_period", "limit"]
    meters = tuple(_dedupe_non_empty(meter_urns, "meter_urns", allow_empty=True))
    if meters:
        placeholders = ", ".join(f"%(meter_{idx})s" for idx, _ in enumerate(meters))
        meter_clause = f"\n  AND meter_urn IN ({placeholders})"
        params.update({f"meter_{idx}": meter for idx, meter in enumerate(meters)})
        bounded_by.append("meter_urns")
    sql = f"""
SELECT run_id, meter_urn, forecast_origin_ts, target_ts, warning_flag, warning_type, status, warning_reason_code, warning_reason_detail, created_at
FROM {ANOMALY_WARNING_TABLE}
WHERE run_id = %(run_id)s
  AND target_ts >= %(period_start)s
  AND target_ts < %(period_end)s{meter_clause}
ORDER BY target_ts DESC, meter_urn
LIMIT %(limit)s
""".strip()
    return ReportContextQuerySpec(
        name="anomaly_warning_context_by_run",
        sql=sql,
        params=params,
        source_tables=(ANOMALY_WARNING_TABLE,),
        expected_columns=(
            "run_id",
            "meter_urn",
            "forecast_origin_ts",
            "target_ts",
            "warning_flag",
            "warning_type",
            "status",
            "warning_reason_code",
            "warning_reason_detail",
            "created_at",
        ),
        bounded_by=tuple(bounded_by),
    )


def build_wiki_context_query(*, search_terms: Sequence[str], limit: int = 5) -> ReportContextQuerySpec:
    """Build a bounded Wiki/RAG lookup against ops.energy_doc as evidence only."""

    terms = _dedupe_non_empty(search_terms, "search_terms")
    if limit < 1 or limit > 20:
        raise ValueError("limit must be between 1 and 20")
    predicates = [f"(content ILIKE %(term_{idx})s OR source ILIKE %(term_{idx})s)" for idx, _ in enumerate(terms)]
    sql = f"""
SELECT id, content, source, hash, created_at
FROM {WIKI_CONTEXT_TABLE}
WHERE {" OR ".join(predicates)}
ORDER BY created_at DESC NULLS LAST, id DESC
LIMIT %(limit)s
""".strip()
    params: dict[str, Any] = {f"term_{idx}": f"%{term}%" for idx, term in enumerate(terms)}
    params["limit"] = limit
    return ReportContextQuerySpec(
        name="wiki_context_evidence_search",
        sql=sql,
        params=params,
        source_tables=(WIKI_CONTEXT_TABLE,),
        expected_columns=("id", "content", "source", "hash", "created_at"),
        bounded_by=("search_terms", "limit"),
    )


def build_pmax_chart_json(
    pmax_rows: Sequence[Mapping[str, Any]],
    *,
    observed_feature_rows: Sequence[Mapping[str, Any]] = (),
    unit: str = DEFAULT_CHART_UNIT,
) -> dict[str, Any]:
    """Convert P-Max forecast plus observed/feature rows to frontend-safe chart JSON."""

    rows = [_normalize_pmax_row(row) for row in pmax_rows]
    observed_rows = [_normalize_pmax_observed_feature_row(row) for row in observed_feature_rows]
    series_by_meter: dict[str, list[dict[str, Any]]] = {}
    observed_by_meter: dict[str, list[dict[str, Any]]] = {}
    peak_point: dict[str, Any] | None = None
    recharts_points: dict[str, dict[str, Any]] = {}
    for row in sorted(rows, key=lambda item: (item["logical_meter"], item["target_ts"], item["horizon_minutes"])):
        point = {
            "target_ts": row["target_ts"],
            "predicted_p_max": row["predicted_p_max"],
            "horizon_minutes": row["horizon_minutes"],
            "base_ts": row["base_ts"],
            "source_meter_urn": row["source_meter_urn"],
        }
        series_by_meter.setdefault(row["logical_meter"], []).append(point)
        recharts_points.setdefault(row["target_ts"], {"ts": row["target_ts"], "source_tables": [PMAX_REPORT_OUTPUT_TABLE]})[
            "forecast_pmax"
        ] = row["predicted_p_max"]
        candidate = {"logical_meter": row["logical_meter"], **point}
        if peak_point is None or candidate["predicted_p_max"] > peak_point["predicted_p_max"]:
            peak_point = candidate
    for row in sorted(observed_rows, key=lambda item: (item["meter_urn"], item["window_ts"])):
        point = {
            "window_ts": row["window_ts"],
            "measurement": row["measurement"],
            "observed_or_feature_peak": row["peak_value"],
            "max_value": row["max_value"],
            "coverage_ratio": row["coverage_ratio"],
            "source_mode": row["source_mode"],
        }
        observed_by_meter.setdefault(row["meter_urn"], []).append(point)
        recharts_point = recharts_points.setdefault(row["window_ts"], {"ts": row["window_ts"], "source_tables": []})
        recharts_point["observed_or_feature_peak"] = row["peak_value"]
        sources = recharts_point.setdefault("source_tables", [])
        if PMAX_OBSERVED_FEATURE_TABLE not in sources:
            sources.append(PMAX_OBSERVED_FEATURE_TABLE)
    return {
        "schema_version": "report_pmax_chart.v1",
        "type": "line",
        "chart_type": "pmax_observed_forecast",
        "library": "recharts",
        "source_table": PMAX_REPORT_OUTPUT_TABLE,
        "source_tables": [PMAX_OBSERVED_FEATURE_TABLE, PMAX_REPORT_OUTPUT_TABLE],
        "x_axis": {"field": "target_ts", "type": "datetime"},
        "y_axis": {"field": "predicted_p_max", "unit": unit},
        "lines": [
            {"dataKey": "observed_or_feature_peak", "name": "실측/피처 피크"},
            {"dataKey": "forecast_pmax", "name": "P-Max 예측선"},
        ],
        "data": [recharts_points[key] for key in sorted(recharts_points)],
        "series": [{"logical_meter": meter, "points": points} for meter, points in series_by_meter.items()],
        "forecast_series": [{"logical_meter": meter, "points": points} for meter, points in series_by_meter.items()],
        "observed_feature_series": [{"meter_urn": meter, "points": points} for meter, points in observed_by_meter.items()],
        "peak_point": peak_point or {},
    }


def build_report_context_pack(
    *,
    cadence: Cadence,
    period_start: datetime,
    period_end: datetime,
    pmax_rows: Sequence[Mapping[str, Any]],
    anomaly_rows: Sequence[Mapping[str, Any]],
    pmax_observed_feature_rows: Sequence[Mapping[str, Any]] = (),
    ontology_rows: Sequence[Mapping[str, Any]] = (),
    wiki_rows: Sequence[Mapping[str, Any]] = (),
    generation_mode: str = "test_api_model",
) -> dict[str, Any]:
    """Assemble deterministic LLM context from fixture or DB result rows."""

    if cadence not in {"daily", "weekly", "monthly"}:
        raise ValueError("cadence must be daily, weekly, or monthly")
    _require_period(period_start, period_end)
    pmax_chart = build_pmax_chart_json(pmax_rows, observed_feature_rows=pmax_observed_feature_rows)
    anomalies = [_normalize_anomaly_row(row) for row in anomaly_rows][:MAX_ANOMALY_CONTEXT_ROWS]
    meter_urns = _context_meter_urns(pmax_chart=pmax_chart, anomalies=anomalies)
    meter_context = build_meter_context_map(meter_urns=meter_urns, ontology_rows=ontology_rows)
    wiki_context = [_normalize_wiki_row(row) for row in wiki_rows]
    observed_summary = build_observed_summary(pmax_observed_feature_rows)
    inspection_candidates = build_inspection_candidates(anomalies)
    operator_actions = [candidate["operator_action"] for candidate in inspection_candidates[:5]]
    if not operator_actions:
        operator_actions = ["점검 후보가 없으므로 주요 계량기 피크와 표본 부족 구간만 확인합니다."]
    return {
        "schema_version": "report_context_pack.v2",
        "report_meta": {
            "cadence": cadence,
            "period_start": _isoformat(period_start),
            "period_end": _isoformat(period_end),
            "generation_mode": generation_mode,
        },
        "observed_summary": observed_summary,
        "inspection_candidates": inspection_candidates,
        "operator_actions": operator_actions,
        "pmax": {
            "chart": pmax_chart,
            "series": pmax_chart["series"],
            "forecast_series": pmax_chart["forecast_series"],
            "observed_feature_series": pmax_chart["observed_feature_series"],
            "peak_point": pmax_chart["peak_point"],
            "summary": _pmax_summary(pmax_chart["peak_point"]),
            "display_policy": "supplementary_reference_only",
        },
        "anomalies": anomalies,
        "meter_context": meter_context,
        "wiki_context": wiki_context,
        "generation_rules": {
            "do_not_invent_numbers": True,
            "observed_first": True,
            "forecast_policy": "supplementary_reference_only",
            "unknown_meter_context_policy": "write_unknown",
            "cause_statement_policy": "only_with_wiki_evidence",
            "do_not_use_wiki_as_report_storage": True,
            "hide_developer_terms_from_user_report": True,
        },
        "context_provenance": {
            "observed_summary_source": PMAX_OBSERVED_FEATURE_TABLE,
            "inspection_candidate_source": ANOMALY_WARNING_TABLE,
            "pmax_observed_or_feature_source": PMAX_OBSERVED_FEATURE_TABLE,
            "pmax_forecast_source": PMAX_REPORT_OUTPUT_TABLE,
            "pmax_source_table": PMAX_REPORT_OUTPUT_TABLE,
            "anomaly_source_tables": [ANOMALY_LOG_TABLE, ANOMALY_WARNING_TABLE],
            "ontology_source_tables": [ONTOLOGY_METER_CONTEXT_TABLE, ONTOLOGY_METER_MEASUREMENT_CONTEXT_TABLE, ONTOLOGY_METER_ROLE_TABLE],
            "wiki_source_table": WIKI_CONTEXT_TABLE,
        },
    }


def build_meter_context_map(*, meter_urns: Sequence[str], ontology_rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Normalize ontology rows and apply explicit unknown fallbacks."""

    requested = _dedupe_non_empty(meter_urns, "meter_urns", allow_empty=True)
    ontology_by_meter = {str(row.get("meter_urn", "")).strip(): row for row in ontology_rows if str(row.get("meter_urn", "")).strip()}
    result: dict[str, dict[str, Any]] = {}
    for meter in requested:
        row = ontology_by_meter.get(meter, {})
        role_code = _clean_optional(row.get("meter_role_code"))
        role_label = _clean_optional(row.get("meter_role_label") or row.get("role_label"))
        location_label = _clean_optional(row.get("location_label") or row.get("location"))
        result[meter] = {
            "meter_urn": meter,
            "meter_domain": _clean_optional(row.get("meter_domain")) or "unknown",
            "meter_role_code": role_code or "unknown",
            "meter_role_label": role_label or UNKNOWN_ROLE,
            "equipment_group_label": _clean_optional(row.get("equipment_group_label")) or UNKNOWN_ROLE,
            "building_code": _clean_optional(row.get("building_code")) or "unknown",
            "equipment_name": _clean_optional(row.get("equipment_name")) or UNKNOWN_ROLE,
            "location_label": location_label or UNKNOWN_LOCATION,
            "anomaly_priority": row.get("anomaly_priority"),
            "sign_convention": _clean_optional(row.get("sign_convention")) or "unknown",
            "context_status": "known" if row else "missing_fallback",
        }
    return result


def build_report_prompt_messages(context_pack: Mapping[str, Any]) -> list[dict[str, str]]:
    """Build guarded messages for the report LLM provider."""

    context_json = json.dumps(context_pack, ensure_ascii=False, sort_keys=True, indent=2)
    system = """
당신은 에너지 운영 리포트 작성 보조 모델입니다.
제공된 context pack에 없는 수치를 만들지 마세요.
제공되지 않은 수치는 '데이터 없음' 또는 '미확인'으로 표기하세요.
ontology_context에 없는 위치/역할을 만들지 마세요.
Wiki 근거가 없으면 원인 가능성 표현을 금지합니다.
이상치 원인을 단정하지 마세요.
P-Max 수치를 변경하지 마세요.
chart JSON의 값과 문장 값이 일치해야 합니다.
JSON만 반환하세요. 코드블록, 설명문, 주석을 붙이지 마세요.
최상위 키는 title, executive_summary, markdown, report_json, operator_actions, limitations만 사용하세요.
operator_actions와 limitations는 반드시 문자열 배열이어야 합니다.
report_json은 schema_version='report_payload.v2'와 sections 배열을 포함해야 합니다.
sections에는 usage_patterns, key_meters, inspection_candidates, cost_peak 키가 반드시 있어야 합니다.
worker, queue, heartbeat, Airflow task, container, PC process를 사용자 보고서에 쓰지 마세요.
""".strip()
    user = f"""
REPORT_META / PMAX_CONTEXT / ANOMALY_CONTEXT / METER_CONTEXT / WIKI_CONTEXT가 포함된 context pack입니다.
ops.energy_doc는 Wiki/RAG 보조 근거이며 report 저장소가 아닙니다.

CONTEXT_PACK_JSON:
{context_json}

OUTPUT_FORMAT:
JSON object only:
{{
  "title": "문자열",
  "executive_summary": "문자열",
  "markdown": "# Markdown 문자열",
  "report_json": {{
    "schema_version": "report_payload.v2",
    "sections": [
      {{"key": "usage_patterns", "title": "사용 패턴", "body": "문자열"}},
      {{"key": "key_meters", "title": "주요 계량기", "body": "문자열"}},
      {{"key": "inspection_candidates", "title": "점검 후보", "body": "문자열"}},
      {{"key": "cost_peak", "title": "비용·피크 관리", "body": "문자열"}}
    ]
  }},
  "operator_actions": ["문자열"],
  "limitations": ["문자열"]
}}
""".strip()
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def inspect_prompt_guard(messages: Sequence[Mapping[str, str]] | str) -> PromptGuardStatus:
    """Verify that prompt guard fragments are present before model invocation."""

    if isinstance(messages, str):
        text = messages
    else:
        text = "\n".join(message.get("content", "") for message in messages)
    missing = tuple(fragment for fragment in PROMPT_GUARD_FRAGMENTS if fragment not in text)
    return PromptGuardStatus(ok=not missing, missing_fragments=missing)


def build_low_cost_api_model_test_request(
    context_pack: Mapping[str, Any],
    *,
    model: str = "gpt-4o-mini",
    max_tokens: int = 700,
    temperature: float = 0.1,
) -> dict[str, Any]:
    """Return a no-network request spec for the one-sample low-cost test lane."""

    if max_tokens < 1 or max_tokens > 1200:
        raise ValueError("max_tokens must be between 1 and 1200 for the low-cost test lane")
    if temperature < 0 or temperature > 0.3:
        raise ValueError("temperature must be between 0 and 0.3 for the low-cost test lane")
    messages = build_report_prompt_messages(context_pack)
    guard = inspect_prompt_guard(messages)
    if not guard.ok:
        raise ValueError("prompt guard missing required fragments: " + ", ".join(guard.missing_fragments))
    return {
        "lane": "low_cost_api_model_fixture_smoke",
        "provider": "openai_compatible",
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": messages,
        "secret_policy": "api_key_env_only_do_not_log",
        "network_call": False,
    }


def run_low_cost_api_model_fixture_smoke(
    context_pack: Mapping[str, Any],
    *,
    generate: Callable[[Mapping[str, Any]], str],
    model: str = "gpt-4o-mini",
    max_tokens: int = 700,
    temperature: float = 0.1,
) -> dict[str, Any]:
    """Run the one-sample generation lane through an injected/mock generator."""

    request = build_low_cost_api_model_test_request(context_pack, model=model, max_tokens=max_tokens, temperature=temperature)
    output = generate(request)
    if not isinstance(output, str):
        raise ValueError("fixture smoke generator must return text")
    return {
        "ok": bool(output.strip()),
        "lane": request["lane"],
        "model": request["model"],
        "max_tokens": request["max_tokens"],
        "temperature": request["temperature"],
        "network_call": request["network_call"],
        "output_text": output,
    }


def _normalize_pmax_row(row: Mapping[str, Any]) -> dict[str, Any]:
    predicted = row.get("predicted_p_max")
    if predicted is None:
        raise ValueError("pmax row missing predicted_p_max")
    target_ts = row.get("target_ts")
    logical_meter = _required_text(row, "logical_meter")
    return {
        "logical_meter": logical_meter,
        "source_meter_urn": _clean_optional(row.get("source_meter_urn")) or logical_meter,
        "base_ts": _isoformat(row.get("base_ts")),
        "input_end_ts": _isoformat(row.get("input_end_ts")),
        "target_ts": _isoformat(target_ts),
        "horizon_minutes": int(row.get("horizon_minutes", 0)),
        "predicted_p_max": float(predicted),
        "created_at": _isoformat(row.get("created_at")),
    }


def _normalize_pmax_observed_feature_row(row: Mapping[str, Any]) -> dict[str, Any]:
    peak_value = row.get("peak_value", row.get("max_value"))
    max_value = row.get("max_value", peak_value)
    if peak_value is None:
        raise ValueError("observed/feature pmax row missing peak_value or max_value")
    return {
        "window_ts": _isoformat(row.get("window_ts")),
        "meter_urn": _required_text(row, "meter_urn"),
        "measurement": _clean_optional(row.get("measurement")) or "P",
        "max_value": float(max_value) if max_value is not None else None,
        "peak_ts": _isoformat(row.get("peak_ts")),
        "peak_value": float(peak_value),
        "observed_points": int(row.get("observed_points", 0) or 0),
        "expected_points": int(row.get("expected_points", 0) or 0),
        "coverage_ratio": float(row.get("coverage_ratio", 0.0) or 0.0),
        "source_layer": _clean_optional(row.get("source_layer")) or "unknown",
        "source_mode": _clean_optional(row.get("source_mode")) or "unknown",
        "run_id": _clean_optional(row.get("run_id")) or "unknown",
        "created_at": _isoformat(row.get("created_at")),
    }


def _normalize_anomaly_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "run_id": _clean_optional(row.get("run_id")) or "unknown",
        "meter_urn": _required_text(row, "meter_urn"),
        "forecast_origin_ts": _isoformat(row.get("forecast_origin_ts")),
        "target_ts": _isoformat(row.get("target_ts")),
        "warning_flag": bool(row.get("warning_flag", False)),
        "warning_type": _clean_optional(row.get("warning_type")) or "none",
        "status": _clean_optional(row.get("status")) or "unknown",
        "warning_reason_code": _clean_optional(row.get("warning_reason_code")) or "NONE",
        "warning_reason_detail": _clean_optional(row.get("warning_reason_detail")) or "",
        "created_at": _isoformat(row.get("created_at")),
    }


def _normalize_wiki_row(row: Mapping[str, Any]) -> dict[str, Any]:
    content = _clean_optional(row.get("content")) or ""
    return {
        "id": row.get("id"),
        "source": _clean_optional(row.get("source")) or "unknown",
        "hash": _clean_optional(row.get("hash")) or "",
        "snippet": content[:MAX_WIKI_SNIPPET_CHARS],
        "created_at": _isoformat(row.get("created_at")),
    }


def _context_meter_urns(*, pmax_chart: Mapping[str, Any], anomalies: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    meters: list[str] = []
    for series in pmax_chart.get("series", []):
        if isinstance(series, Mapping):
            meter = _clean_optional(series.get("logical_meter"))
            if meter:
                meters.append(meter)
            for point in series.get("points", []):
                if isinstance(point, Mapping):
                    source_meter = _clean_optional(point.get("source_meter_urn"))
                    if source_meter:
                        meters.append(source_meter)
    for series in pmax_chart.get("observed_feature_series", []):
        if isinstance(series, Mapping):
            meter = _clean_optional(series.get("meter_urn"))
            if meter:
                meters.append(meter)
    for row in anomalies:
        meter = _clean_optional(row.get("meter_urn"))
        if meter:
            meters.append(meter)
    return tuple(dict.fromkeys(meters))


def _pmax_summary(peak_point: Mapping[str, Any]) -> str:
    if not peak_point:
        return "P-Max 데이터 없음"
    return (
        f"P-Max peak {peak_point['predicted_p_max']:g} {DEFAULT_CHART_UNIT} "
        f"at {peak_point['target_ts']} for {peak_point['logical_meter']}"
    )


def _required_text(row: Mapping[str, Any], field_name: str) -> str:
    value = _clean_optional(row.get(field_name))
    if not value:
        raise ValueError(f"{field_name} must be non-empty")
    return value


def _clean_optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dedupe_non_empty(values: Sequence[str], field_name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    cleaned = tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
    if not cleaned and not allow_empty:
        raise ValueError(f"{field_name} must contain at least one non-empty value")
    return cleaned


def _isoformat(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return ""
    return str(value)


def _require_period(period_start: datetime, period_end: datetime) -> None:
    _require_aware_datetime(period_start, "period_start")
    _require_aware_datetime(period_end, "period_end")
    if period_start >= period_end:
        raise ValueError("period_start must be earlier than period_end")


def _require_aware_datetime(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


__all__ = [
    "ANOMALY_LOG_TABLE",
    "ANOMALY_WARNING_TABLE",
    "DEFAULT_CHART_UNIT",
    "MAX_ANOMALY_CONTEXT_ROWS",
    "MAX_WIKI_SNIPPET_CHARS",
    "ONTOLOGY_METER_CONTEXT_TABLE",
    "ONTOLOGY_METER_MEASUREMENT_CONTEXT_TABLE",
    "ONTOLOGY_METER_ROLE_TABLE",
    "PMAX_OBSERVED_FEATURE_TABLE",
    "PMAX_REPORT_OUTPUT_TABLE",
    "PROMPT_GUARD_FRAGMENTS",
    "PromptGuardStatus",
    "ReportContextQuerySpec",
    "UNKNOWN_LOCATION",
    "UNKNOWN_ROLE",
    "WIKI_CONTEXT_TABLE",
    "build_anomaly_warning_context_query",
    "build_latest_successful_anomaly_run_query",
    "build_low_cost_api_model_test_request",
    "build_meter_context_map",
    "build_ontology_context_query",
    "build_pmax_chart_json",
    "build_pmax_forecast_context_query",
    "build_pmax_observed_feature_context_query",
    "build_report_context_pack",
    "build_report_prompt_messages",
    "build_wiki_context_query",
    "inspect_prompt_guard",
    "run_low_cost_api_model_fixture_smoke",
]
