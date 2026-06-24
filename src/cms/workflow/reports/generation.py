"""Minimum vertical slice for CMS ops report generation and storage.

This module is deterministic by default: it reads bounded report context rows,
builds a guarded context pack, creates observation-only report text, and stores
it in the cadence-specific ops report table. It does not call an LLM or network.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from cms.workflow.reports.renderer import render_deterministic_report, validate_user_report_contract
from cms.workflow.reports.context_pack import (
    ANOMALY_LOG_TABLE,
    ANOMALY_WARNING_TABLE,
    ONTOLOGY_METER_CONTEXT_TABLE,
    ONTOLOGY_METER_MEASUREMENT_CONTEXT_TABLE,
    ONTOLOGY_METER_ROLE_TABLE,
    PMAX_OBSERVED_FEATURE_TABLE,
    PMAX_REPORT_OUTPUT_TABLE,
    WIKI_CONTEXT_TABLE,
    build_anomaly_warning_context_query,
    build_latest_successful_anomaly_run_query,
    build_ontology_context_query,
    build_pmax_forecast_context_query,
    build_pmax_observed_feature_context_query,
    build_report_context_pack,
    build_wiki_context_query,
)

Cadence = Literal["daily", "weekly", "monthly"]
GENERATION_MODE = "deterministic_fallback"
DEFAULT_TIMEZONE = "Asia/Seoul"
UNKNOWN_METER_UI_PHRASE = "계량기 메타데이터 없음"
FORBIDDEN_CAUSE_TERMS_WITHOUT_WIKI = ("원인", "가능성", "추정", "때문", "영향", "likely", "cause", "caused", "because")
CADENCES: tuple[Cadence, ...] = ("daily", "weekly", "monthly")
REPORT_TABLES: dict[Cadence, str] = {
    "daily": "ops.daily_report",
    "weekly": "ops.weekly_report",
    "monthly": "ops.monthly_report",
}
LEGACY_REPORT_TABLES = REPORT_TABLES


@dataclass(frozen=True)
class ReportPeriod:
    cadence: Cadence
    period_start: datetime
    period_end: datetime
    period_key: str


class ReportReadinessBlocked(RuntimeError):
    """Raised before any write when the readiness gate does not pass."""


def validate_cadence(cadence: str) -> Cadence:
    if cadence not in CADENCES:
        raise ValueError("cadence must be daily, weekly, or monthly")
    return cadence  # type: ignore[return-value]


def compute_report_period(cadence: Cadence, *, now: datetime | None = None, timezone: str = DEFAULT_TIMEZONE) -> ReportPeriod:
    """Return approved report period boundaries: daily=previous day, weekly=previous Mon-Sun, monthly=previous month."""

    cadence = validate_cadence(cadence)
    tz = ZoneInfo(timezone)
    reference = now.astimezone(tz) if now else datetime.now(tz)
    today = reference.date()
    if cadence == "daily":
        start_date = today - timedelta(days=1)
        end_date = today
        key = start_date.isoformat()
    elif cadence == "weekly":
        current_monday = today - timedelta(days=today.weekday())
        start_date = current_monday - timedelta(days=7)
        end_date = current_monday
        key = f"{start_date.isoformat()}_{(end_date - timedelta(days=1)).isoformat()}"
    else:
        first_this_month = today.replace(day=1)
        last_prev_month = first_this_month - timedelta(days=1)
        start_date = last_prev_month.replace(day=1)
        end_date = first_this_month
        key = start_date.strftime("%Y-%m")
    return ReportPeriod(
        cadence=cadence,
        period_start=datetime.combine(start_date, time.min, tzinfo=tz),
        period_end=datetime.combine(end_date, time.min, tzinfo=tz),
        period_key=key,
    )


def readiness_allows_generation(readiness: Mapping[str, Any]) -> bool:
    """Return True only when the readiness snapshot has no blocking status."""

    if readiness.get("ok") is False or readiness.get("blocked") is True:
        return False
    probes = readiness.get("probes")
    if isinstance(probes, Mapping):
        for payload in probes.values():
            if not isinstance(payload, Mapping):
                continue
            if payload.get("status") == "error":
                return False
            if payload.get("status") == "partial" and payload.get("errors"):
                return False
    review = readiness.get("langgraph_review")
    if isinstance(review, Mapping) and (review.get("ok") is False or review.get("blocked") is True):
        return False
    return True


def assert_readiness_passed(readiness: Mapping[str, Any]) -> None:
    if not readiness_allows_generation(readiness):
        raise ReportReadinessBlocked("report generation blocked because readiness did not pass")


def generate_and_store_report(
    conn: Any,
    *,
    cadence: Cadence,
    readiness: Mapping[str, Any],
    now: datetime | None = None,
    timezone: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    """Build, guard, and upsert one ops report. Readiness is checked before any DB write."""

    assert_readiness_passed(readiness)
    period = compute_report_period(cadence, now=now, timezone=timezone)
    context_pack = fetch_context_pack(conn, period=period)
    record = build_report_record(period=period, context_pack=context_pack)
    stored = store_report(conn, record)
    return {"ok": True, "blocked": False, "report": stored, "record": record}


def fetch_context_pack(conn: Any, *, period: ReportPeriod) -> dict[str, Any]:
    observed_rows = _fetch_all(conn, build_pmax_observed_feature_context_query(period_start=period.period_start, period_end=period.period_end))
    forecast_rows = _fetch_all(conn, build_pmax_forecast_context_query(period_start=period.period_start, period_end=period.period_end))
    latest_runs = _fetch_all(conn, build_latest_successful_anomaly_run_query(period_start=period.period_start, period_end=period.period_end))
    run_id = str(latest_runs[0]["run_id"]) if latest_runs else ""
    anomaly_rows = []
    if run_id:
        anomaly_rows = _fetch_all(
            conn,
            build_anomaly_warning_context_query(run_id=run_id, period_start=period.period_start, period_end=period.period_end),
        )
    meter_urns = _meter_urns(observed_rows=observed_rows, forecast_rows=forecast_rows, anomaly_rows=anomaly_rows)
    ontology_rows = _fetch_optional_all(conn, build_ontology_context_query(meter_urns)) if meter_urns else []
    wiki_terms = _wiki_terms(anomaly_rows=anomaly_rows, ontology_rows=ontology_rows)
    wiki_rows = _fetch_optional_all(conn, build_wiki_context_query(search_terms=wiki_terms)) if wiki_terms else []
    context_pack = build_report_context_pack(
        cadence=period.cadence,
        period_start=period.period_start,
        period_end=period.period_end,
        pmax_rows=forecast_rows,
        pmax_observed_feature_rows=observed_rows,
        anomaly_rows=anomaly_rows,
        ontology_rows=ontology_rows,
        wiki_rows=wiki_rows,
        generation_mode=GENERATION_MODE,
    )
    context_pack["report_meta"]["period_key"] = period.period_key
    context_pack["context_provenance"]["latest_successful_anomaly_run_id"] = run_id
    return context_pack


def build_report_record(*, period: ReportPeriod, context_pack: Mapping[str, Any]) -> dict[str, Any]:
    sections = build_sllm_report_sections(context_pack)
    guard = enforce_hallucination_guard(sections=sections, context_pack=context_pack)
    if not guard["ok"]:
        raise ValueError("deterministic report failed hallucination guard: " + ", ".join(guard["violations"]))
    source_refs = _source_refs(context_pack)
    anomaly_rows = _row_sequence(context_pack.get("anomalies"))
    generation_mode = "sllm" if sections.get("report_json", {}).get("sllm_used") is True else GENERATION_MODE
    record = {
        "cadence": period.cadence,
        "period_key": period.period_key,
        "period_start": period.period_start.date().isoformat(),
        "period_end": period.period_end.date().isoformat(),
        "title": sections["title"],
        "executive_summary": sections["executive_summary"],
        "markdown": sections["markdown"],
        "report_json": sections["report_json"],
        "operator_actions": sections.get("operator_actions", []),
        "anomaly_count": _warning_count(anomaly_rows),
        "chart_json": context_pack.get("pmax", {}).get("chart", {}),
        "anomaly_rows": anomaly_rows,
        "summary": sections["summary"],
        "pmax_commentary": sections["pmax_commentary"],
        "anomaly_commentary": sections["anomaly_commentary"],
        "limitations": sections["limitations"],
        "context_pack": context_pack,
        "metadata": {"meter_context_ui": _meter_context_ui(context_pack)},
        "source_refs": source_refs,
        "guard_result": guard,
        "generation_mode": generation_mode,
    }
    record["idempotency_key"] = compute_idempotency_key(record)
    return record


def build_deterministic_sections(context_pack: Mapping[str, Any]) -> dict[str, Any]:
    """Render a user-facing report without model calls."""

    return render_deterministic_report(context_pack)



def api_report_generator(messages: Sequence[Mapping[str, str]]) -> str:
    """Small OpenAI-compatible API adapter for report rendering.

    The adapter delegates to the existing CMS LLM API client. It is intentionally
    thin: no local sLLM runtime, no prompt mutation, and no logging of secrets.
    Actual network use remains gated by `CMS_REPORT_SLLM_ENABLED=1` in
    `build_sllm_report_sections()`.
    """

    from cms.workflow.router.llm_client import chat

    model = os.getenv("CMS_REPORT_LLM_MODEL") or os.getenv("LLM_MODEL_FAST") or "gpt-4o-mini"
    return chat([dict(message) for message in messages], max_tokens=1200, thinking=False, model=model)


def build_sllm_report_sections(
    context_pack: Mapping[str, Any],
    *,
    generate: Any | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Render through an optional injected sLLM generator, otherwise fallback.

    This function never performs a network call by itself. Callers must pass an
    injected `generate(messages) -> dict|str` function and set the environment
    gate `CMS_REPORT_SLLM_ENABLED=1` or `enabled=True`. Any failure returns the
    deterministic renderer output with fallback metadata.
    """

    use_model = enabled if enabled is not None else os.getenv("CMS_REPORT_SLLM_ENABLED", "0") == "1"
    fallback = build_deterministic_sections(context_pack)
    if not use_model:
        fallback["report_json"] = {**fallback["report_json"], "sllm_used": False, "fallback_reason": "disabled"}
        return fallback
    if generate is None:
        generate = api_report_generator
    try:
        from cms.workflow.reports.context_pack import build_report_prompt_messages

        raw = generate(build_report_prompt_messages(context_pack))
        payload = json.loads(raw) if isinstance(raw, str) else raw
        report_json = validate_sllm_report_json(payload, context_pack=context_pack)
        markdown = str(report_json["markdown"])
        guard = validate_user_report_contract(report_json=report_json["report_json"], markdown=markdown, context_pack=context_pack)
        if not guard["ok"]:
            raise ValueError("sLLM output failed user report guard: " + ", ".join(guard["violations"]))
        return {
            "title": report_json["title"],
            "executive_summary": report_json["executive_summary"],
            "markdown": markdown,
            "report_json": {**report_json["report_json"], "sllm_used": True},
            "summary": report_json["executive_summary"],
            "pmax_commentary": _section_body(report_json["report_json"], "cost_peak"),
            "anomaly_commentary": _section_body(report_json["report_json"], "inspection_candidates"),
            "operator_actions": report_json["operator_actions"],
            "limitations": report_json["limitations"],
        }
    except Exception as exc:
        fallback["report_json"] = {**fallback["report_json"], "sllm_used": False, "fallback_reason": exc.__class__.__name__}
        return fallback


def validate_sllm_report_json(payload: Any, *, context_pack: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("sLLM output must be a JSON object")
    required = ("title", "executive_summary", "markdown", "report_json", "operator_actions", "limitations")
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError("sLLM output missing keys: " + ", ".join(missing))
    if not isinstance(payload["report_json"], Mapping):
        raise ValueError("report_json must be an object")
    if not isinstance(payload["operator_actions"], Sequence) or isinstance(payload["operator_actions"], (str, bytes, bytearray)):
        raise ValueError("operator_actions must be an array")
    if not isinstance(payload["limitations"], Sequence) or isinstance(payload["limitations"], (str, bytes, bytearray)):
        raise ValueError("limitations must be an array")
    normalized = dict(payload)
    normalized_report_json = dict(payload["report_json"])
    normalized_report_json.setdefault("operator_actions", list(payload["operator_actions"]))
    normalized_report_json.setdefault("limitations", list(payload["limitations"]))
    normalized["report_json"] = normalized_report_json
    return normalized


def _section_body(report_json: Mapping[str, Any], key: str) -> str:
    sections = report_json.get("sections")
    if isinstance(sections, Sequence) and not isinstance(sections, (str, bytes, bytearray)):
        for section in sections:
            if isinstance(section, Mapping) and section.get("key") == key:
                return str(section.get("body") or "")
    return ""


def enforce_hallucination_guard(*, sections: Mapping[str, Any], context_pack: Mapping[str, Any]) -> dict[str, Any]:
    violations: list[str] = []
    combined = "\n".join(str(sections.get(key) or "") for key in ("summary", "pmax_commentary", "anomaly_commentary"))
    if not context_pack.get("wiki_context"):
        found = [term for term in FORBIDDEN_CAUSE_TERMS_WITHOUT_WIKI if term.lower() in combined.lower()]
        if found:
            violations.append("cause_terms_without_wiki:" + ",".join(found))
    meter_ui = _meter_context_ui(context_pack)
    if meter_ui == UNKNOWN_METER_UI_PHRASE and UNKNOWN_METER_UI_PHRASE not in meter_ui:
        violations.append("missing_unknown_meter_ui_phrase")
    return {"ok": not violations, "violations": violations, "wiki_context_present": bool(context_pack.get("wiki_context"))}


def compute_idempotency_key(record: Mapping[str, Any]) -> str:
    payload = {
        "cadence": record.get("cadence"),
        "period_key": record.get("period_key"),
        "period_start": record.get("period_start"),
        "period_end": record.get("period_end"),
        "source_refs": record.get("source_refs"),
        "generation_mode": record.get("generation_mode"),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _period_column(cadence: Cadence) -> str:
    return "date" if cadence == "daily" else "period"


def build_report_upsert_statement(record: Mapping[str, Any]) -> tuple[str, tuple[Any, ...]]:
    cadence = validate_cadence(str(record.get("cadence") or ""))
    table = REPORT_TABLES[cadence]
    period_column = _period_column(cadence)
    base_columns = [period_column]
    base_values = [record["period_key"]]
    if cadence == "weekly":
        base_columns.extend(["period_start", "period_end"])
        base_values.extend([record["period_start"], record["period_end"]])
    doc_columns = [
        "title",
        "executive_summary",
        "markdown",
        "report_json",
        "operator_actions",
        "chart_json",
        "anomaly_rows",
        "summary",
        "pmax_commentary",
        "anomaly_commentary",
        "limitations",
        "context_pack",
        "metadata",
        "source_refs",
        "guard_result",
        "generation_mode",
        "idempotency_key",
        "generated_at",
        "updated_at",
    ]
    columns = base_columns + doc_columns
    placeholders = ["%s" for _ in base_columns] + [
        "%s",
        "%s",
        "%s",
        "%s::jsonb",
        "%s::jsonb",
        "%s::jsonb",
        "%s::jsonb",
        "%s",
        "%s",
        "%s",
        "%s::jsonb",
        "%s::jsonb",
        "%s::jsonb",
        "%s::jsonb",
        "%s::jsonb",
        "%s",
        "%s",
        "NOW()",
        "NOW()",
    ]
    update_columns = [column for column in doc_columns if column != "generated_at"]
    set_clause = ",\n    ".join(f"{column} = EXCLUDED.{column}" for column in update_columns)
    sql = f"""
INSERT INTO {table}
    ({', '.join(columns)})
VALUES ({', '.join(placeholders)})
ON CONFLICT ({period_column}) DO UPDATE SET
    {set_clause}
RETURNING {period_column} AS period, generation_mode, idempotency_key, generated_at, updated_at
""".strip()
    params = tuple(base_values) + (
        record["title"],
        record["executive_summary"],
        record["markdown"],
        json.dumps(record["report_json"], ensure_ascii=False, default=str),
        json.dumps(record.get("operator_actions", []), ensure_ascii=False, default=str),
        json.dumps(record["chart_json"], ensure_ascii=False, default=str),
        json.dumps(record["anomaly_rows"], ensure_ascii=False, default=str),
        record["summary"],
        record["pmax_commentary"],
        record["anomaly_commentary"],
        json.dumps(record["limitations"], ensure_ascii=False, default=str),
        json.dumps(record["context_pack"], ensure_ascii=False, default=str),
        json.dumps(record["metadata"], ensure_ascii=False, default=str),
        json.dumps(record["source_refs"], ensure_ascii=False, default=str),
        json.dumps(record["guard_result"], ensure_ascii=False, default=str),
        record["generation_mode"],
        record["idempotency_key"],
    )
    return sql, params


def build_latest_report_query(cadence: Cadence) -> str:
    cadence = validate_cadence(cadence)
    table = REPORT_TABLES[cadence]
    period_column = _period_column(cadence)
    return f"""
SELECT {period_column} AS period, title, executive_summary, markdown, report_json, operator_actions,
       chart_json, anomaly_rows, summary, pmax_commentary, anomaly_commentary, limitations,
       context_pack, metadata, source_refs, guard_result, generation_mode, idempotency_key,
       generated_at, updated_at
FROM {table}
WHERE idempotency_key IS NOT NULL
ORDER BY {period_column} DESC, generated_at DESC NULLS LAST, updated_at DESC NULLS LAST
LIMIT 1
""".strip()


def build_report_period_query(cadence: Cadence, period_key: str) -> tuple[str, tuple[Any, ...]]:
    cadence = validate_cadence(cadence)
    table = REPORT_TABLES[cadence]
    period_column = _period_column(cadence)
    return f"""
SELECT {period_column} AS period, title, executive_summary, markdown, report_json, operator_actions,
       chart_json, anomaly_rows, summary, pmax_commentary, anomaly_commentary, limitations,
       context_pack, metadata, source_refs, guard_result, generation_mode, idempotency_key,
       generated_at, updated_at
FROM {table}
WHERE {period_column} = %s
LIMIT 1
""".strip(), (period_key,)


def build_report_periods_query(cadence: Cadence) -> str:
    cadence = validate_cadence(cadence)
    table = REPORT_TABLES[cadence]
    period_column = _period_column(cadence)
    return f"""
SELECT {period_column} AS period_key, title, generated_at, updated_at
FROM {table}
WHERE idempotency_key IS NOT NULL
ORDER BY {period_column} DESC, generated_at DESC NULLS LAST, updated_at DESC NULLS LAST
LIMIT %s
""".strip()


def build_legacy_latest_report_query(cadence: Cadence) -> str:
    return build_latest_report_query(cadence)


def store_report(conn: Any, record: Mapping[str, Any]) -> dict[str, Any]:
    sql, params = build_report_upsert_statement(record)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    conn.commit()
    return _row_to_dict(row, ("period", "generation_mode", "idempotency_key", "generated_at", "updated_at"))


def fetch_latest_report(conn: Any, cadence: Cadence) -> dict[str, Any] | None:
    return _fetch_report_row(conn, build_latest_report_query(cadence), ())


def fetch_report_period(conn: Any, cadence: Cadence, period_key: str) -> dict[str, Any] | None:
    sql, params = build_report_period_query(cadence, period_key)
    return _fetch_report_row(conn, sql, params)


def _fetch_latest_legacy_report(conn: Any, cadence: Cadence) -> dict[str, Any] | None:
    return fetch_latest_report(conn, cadence)


def _fetch_report_row(conn: Any, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    if not row:
        return None
    return _row_to_dict(
        row,
        (
            "period",
            "title",
            "executive_summary",
            "markdown",
            "report_json",
            "operator_actions",
            "chart_json",
            "anomaly_rows",
            "summary",
            "pmax_commentary",
            "anomaly_commentary",
            "limitations",
            "context_pack",
            "metadata",
            "source_refs",
            "guard_result",
            "generation_mode",
            "idempotency_key",
            "generated_at",
            "updated_at",
        ),
    )


def _fetch_all(conn: Any, spec: Any) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(spec.sql, spec.params)
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description]
    return [_row_to_dict(row, columns) for row in rows]


def _fetch_optional_all(conn: Any, spec: Any) -> list[dict[str, Any]]:
    try:
        return _fetch_all(conn, spec)
    except Exception:
        conn.rollback()
        return []


def _row_to_dict(row: Any, columns: Sequence[str]) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, Mapping):
        return {str(key): _json_safe(value) for key, value in row.items()}
    return {columns[idx]: _json_safe(value) for idx, value in enumerate(row)}


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _meter_urns(*, observed_rows: Sequence[Mapping[str, Any]], forecast_rows: Sequence[Mapping[str, Any]], anomaly_rows: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    meters: list[str] = []
    for row in observed_rows:
        _append_text(meters, row.get("meter_urn"))
    for row in forecast_rows:
        _append_text(meters, row.get("logical_meter"))
        _append_text(meters, row.get("source_meter_urn"))
    for row in anomaly_rows:
        _append_text(meters, row.get("meter_urn"))
    return tuple(dict.fromkeys(meters))


def _wiki_terms(*, anomaly_rows: Sequence[Mapping[str, Any]], ontology_rows: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    terms: list[str] = []
    for row in anomaly_rows[:10]:
        _append_text(terms, row.get("warning_reason_code"))
        _append_text(terms, row.get("warning_type"))
    for row in ontology_rows[:10]:
        _append_text(terms, row.get("equipment_group_label"))
        _append_text(terms, row.get("meter_role_label"))
    return tuple(dict.fromkeys(terms))


def _append_text(values: list[str], value: object) -> None:
    text = str(value or "").strip()
    if text:
        values.append(text)


def _row_sequence(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def _warning_count(rows: Sequence[Mapping[str, Any]]) -> int:
    return sum(1 for row in rows if row.get("warning_flag") is True)


def _quality_issue_count(rows: Sequence[Mapping[str, Any]]) -> int:
    return sum(
        1
        for row in rows
        if row.get("status") == "insufficient_data" or row.get("warning_reason_code") == "INPUT_QUALITY_ISSUE"
    )


def _format_kw(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "–"
    return f"{number:,.1f} kW"


def _source_refs(context_pack: Mapping[str, Any]) -> dict[str, Any]:
    provenance = _mapping(context_pack.get("context_provenance"))
    return {
        "pmax_observed_or_feature_source": provenance.get("pmax_observed_or_feature_source", PMAX_OBSERVED_FEATURE_TABLE),
        "pmax_forecast_source": provenance.get("pmax_forecast_source", PMAX_REPORT_OUTPUT_TABLE),
        "anomaly_source": [ANOMALY_LOG_TABLE, ANOMALY_WARNING_TABLE],
        "latest_successful_anomaly_run_id": provenance.get("latest_successful_anomaly_run_id"),
        "ontology_source": [ONTOLOGY_METER_CONTEXT_TABLE, ONTOLOGY_METER_MEASUREMENT_CONTEXT_TABLE, ONTOLOGY_METER_ROLE_TABLE],
        "wiki_source": WIKI_CONTEXT_TABLE,
    }


def _meter_context_ui(context_pack: Mapping[str, Any]) -> str:
    meter_context = context_pack.get("meter_context")
    if not isinstance(meter_context, Mapping) or not meter_context:
        return UNKNOWN_METER_UI_PHRASE
    known = [row for row in meter_context.values() if isinstance(row, Mapping) and row.get("context_status") == "known"]
    if not known:
        return UNKNOWN_METER_UI_PHRASE
    labels = []
    for row in known[:5]:
        location = row.get("location_label") or UNKNOWN_METER_UI_PHRASE
        role = row.get("meter_role_label") or UNKNOWN_METER_UI_PHRASE
        labels.append(f"{location} / {role}")
    return "; ".join(labels)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _cadence_label(cadence: str) -> str:
    return {"daily": "일간", "weekly": "주간", "monthly": "월간"}.get(cadence, "운영")


__all__ = [
    "CADENCES",
    "DEFAULT_TIMEZONE",
    "GENERATION_MODE",
    "LEGACY_REPORT_TABLES",
    "REPORT_TABLES",
    "UNKNOWN_METER_UI_PHRASE",
    "ReportPeriod",
    "ReportReadinessBlocked",
    "assert_readiness_passed",
    "build_deterministic_sections",
    "build_legacy_latest_report_query",
    "build_latest_report_query",
    "build_report_period_query",
    "build_report_periods_query",
    "fetch_report_period",
    "api_report_generator",
    "build_sllm_report_sections",
    "build_report_record",
    "build_report_upsert_statement",
    "compute_idempotency_key",
    "compute_report_period",
    "enforce_hallucination_guard",
    "validate_sllm_report_json",
    "fetch_context_pack",
    "fetch_latest_report",
    "generate_and_store_report",
    "readiness_allows_generation",
    "store_report",
    "validate_cadence",
]
