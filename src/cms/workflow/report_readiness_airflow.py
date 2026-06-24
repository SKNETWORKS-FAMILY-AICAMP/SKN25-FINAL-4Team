"""Scheduled readiness-first Airflow DAG contract for CMS report generation.

The DAG keeps readiness tasks first. Only after readiness passes may the final
generation task write cadence-specific rows to ops.daily_report,
ops.weekly_report, or ops.monthly_report. No canonical writes, credential files,
filesystem writes, external notifications, or destructive actions happen on
import.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime, timedelta
from importlib import import_module
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from cms.workflow.airflow_runtime_policy import (
    AIRFLOW_DEFAULT_RETRIES,
    AIRFLOW_MAX_ACTIVE_RUNS,
    AIRFLOW_REPORT_READINESS_REGISTRATION_MODE,
    AIRFLOW_REPORT_READINESS_SCHEDULE,
    AIRFLOW_REPORT_READINESS_TIMEZONE,
    AIRFLOW_RETRY_DELAY_SECONDS,
    AIRFLOW_REPORT_READINESS_TRIGGER_POLICY,
    AIRFLOW_TASK_TRIGGER_RULE,
)

DAG_ID = "daily_report"
AIRFLOW_DAG_ENABLED = True
AIRFLOW_RUNTIME_DEPLOYED = True
AIRFLOW_REGISTRATION_MODE = AIRFLOW_REPORT_READINESS_REGISTRATION_MODE
AIRFLOW_TRIGGER_POLICY = AIRFLOW_REPORT_READINESS_TRIGGER_POLICY
AIRFLOW_CATCHUP = False
AIRFLOW_IS_PAUSED_UPON_CREATION = False
AIRFLOW_XCOM_POLICY = "xcom_disabled_or_not_required"
AIRFLOW_DEPLOYMENT_STATUS = "scheduled readiness-first report generation DAG registered for the Airflow scheduler"
AIRFLOW_INTEGRATION_LANES = ("scheduled_report", "qa_evidence", "promotion_check", "job_metadata")
TASK_IDS = (
    "collect_qa_evidence_readiness",
    "collect_promotion_check_readiness",
    "collect_job_metadata_readiness",
    "build_readiness_report_status",
    "generate_ops_report",
)
PROBE_ENV_FLAG = "CMS_AIRFLOW_ENABLE_READINESS_PROBES"
LANGGRAPH_REPORT_BRANCH_ENV = "CMS_AIRFLOW_ENABLE_LANGGRAPH_REPORT_BRANCH"
LANGGRAPH_REPORT_BRANCH_ALLOWED_ROUTES = ("evidence_answer", "report_shell")
PLACEHOLDER_VALUES = {"", "***", "placeholder", "changeme", "set-me", "set_me"}
SERVICE_START_REPORT_TABLES = (
    "live.measurement_event",
    "ops.metric",
    "qa.meter_tag",
    "qa.bad_row",
    "mart.peak_feature_15min",
    "mart.pmax_forecast_15min",
    "ops.pmax_log",
    "mart.anomaly_feature_1h",
    "mart.anomaly_warning_1h",
    "ops.anomaly_log",
    "qa.serving_evidence",
)


@dataclass(frozen=True)
class ReportReadinessAirflowDag:
    """Plain-Python description of the scheduled readiness-first report DAG."""

    dag_id: str = DAG_ID
    enabled: bool = AIRFLOW_DAG_ENABLED
    schedule: str = AIRFLOW_REPORT_READINESS_SCHEDULE
    timezone: str = AIRFLOW_REPORT_READINESS_TIMEZONE
    tasks: tuple[str, ...] = TASK_IDS
    writes_allowed: bool = True
    canonical_writes_allowed: bool = False
    deployment_status: str = AIRFLOW_DEPLOYMENT_STATUS
    max_active_runs: int = AIRFLOW_MAX_ACTIVE_RUNS
    retries: int = AIRFLOW_DEFAULT_RETRIES
    retry_delay_seconds: int = AIRFLOW_RETRY_DELAY_SECONDS
    trigger_rule: str = AIRFLOW_TASK_TRIGGER_RULE


def describe_dag(
    *,
    dag_id: str = DAG_ID,
    schedule: str = AIRFLOW_REPORT_READINESS_SCHEDULE,
    report_kind: str = "daily",
) -> ReportReadinessAirflowDag:
    """Return the scheduled DAG contract without importing Airflow."""

    return ReportReadinessAirflowDag(dag_id=dag_id, schedule=schedule)


def _contract(*, reads: list[str], purpose: str, writes: list[str] | None = None) -> dict[str, object]:
    """Build a task contract; generation writes are explicit and readiness-gated."""

    write_tables = writes or []
    return {
        "reads": reads,
        "writes": write_tables,
        "writes_enabled": bool(write_tables),
        "canonical_writes_allowed": False,
        "purpose": purpose,
    }


def task_contracts() -> dict[str, dict[str, object]]:
    """Task boundaries for the scheduled report; only the final task writes ops report rows."""

    return {
        "collect_qa_evidence_readiness": _contract(
            reads=[
                "live.measurement_event",
                "ops.metric",
                "qa.meter_tag",
                "qa.bad_row",
            ],
            purpose="summarize service-start live/QA evidence availability from active tables without mutating storage",
        ),
        "collect_promotion_check_readiness": _contract(
            reads=[
                "mart.peak_feature_15min",
                "mart.pmax_forecast_15min",
                "ops.pmax_log",
                "mart.anomaly_feature_1h",
                "mart.anomaly_warning_1h",
                "ops.anomaly_log",
            ],
            purpose="summarize service-start P-Max/anomaly feature/forecast/inference readiness without writes",
        ),
        "collect_job_metadata_readiness": _contract(
            reads=[
                "ops.metric",
                "ops.pmax_log",
                "ops.anomaly_log",
                "qa.serving_evidence",
                "grafana.health",
                "prometheus.health",
            ],
            purpose="summarize long-running workflow, Kafka, and observability metadata readiness without writes",
        ),
        "build_readiness_report_status": _contract(
            reads=list(SERVICE_START_REPORT_TABLES),
            purpose="build an in-memory service-start readiness report/status summary; report persistence remains disabled in this task",
        ),
        "generate_ops_report": _contract(
            reads=[
                "mart.peak_feature_15min",
                "mart.pmax_forecast_15min",
                "ops.anomaly_log",
                "mart.anomaly_warning_1h",
                "ontology.meter_context",
                "ontology.meter_measurement_context",
                "ontology.meter_role",
                "ops.energy_doc",
            ],
            writes=["ops.daily_report", "ops.weekly_report", "ops.monthly_report"],
            purpose="after readiness passes, generate and store one deterministic user-facing report row for the DAG cadence",
        ),
    }


def build_readiness_report_status(
    *,
    scope: str = "all",
    report_kind: str = "daily",
    dag_id: str = DAG_ID,
    schedule: str = AIRFLOW_REPORT_READINESS_SCHEDULE,
    include_langgraph_review: bool | None = None,
) -> dict[str, object]:
    """Return a read-only report descriptor and optional live probes."""

    snapshot = {
        "dag_id": dag_id,
        "report_kind": report_kind,
        "scope": scope,
        "schedule": schedule,
        "timezone": AIRFLOW_REPORT_READINESS_TIMEZONE,
        "generated_at": datetime.now(UTC).isoformat(),
        "read_only": True,
        "writes_enabled": False,
        "canonical_writes_allowed": False,
        "tasks": TASK_IDS,
    }
    snapshot["probes"] = _collect_readiness_probes(scope=scope)
    should_run_review = scope == "all" if include_langgraph_review is None else include_langgraph_review
    if should_run_review and _langgraph_report_branch_enabled():
        snapshot["langgraph_review"] = run_report_langgraph_branch(snapshot=snapshot, task_id="build_readiness_report_status")
    return snapshot


def airflow_task_entrypoint(
    task_id: str,
    *,
    report_kind: str = "daily",
    dag_id: str = DAG_ID,
    schedule: str = AIRFLOW_REPORT_READINESS_SCHEDULE,
    **_context: Any,
) -> dict[str, object]:
    """Airflow PythonOperator entrypoint. Readiness tasks run first; generation writes only after readiness passes."""

    if task_id not in TASK_IDS:
        return {"task_id": task_id or "unknown", "ok": False, "blocked": True, "reason": "unknown report/readiness task_id"}
    if task_id == "generate_ops_report":
        readiness = build_readiness_report_status(
            scope="all",
            report_kind=report_kind,
            dag_id=dag_id,
            schedule=schedule,
            include_langgraph_review=False,
        )
        from cms.workflow.reports.generation import ReportReadinessBlocked, generate_and_store_report, readiness_allows_generation

        if not readiness_allows_generation(readiness):
            return _xcom_safe(
                {
                    "task_id": task_id,
                    "ok": False,
                    "blocked": True,
                    "reason": "readiness check did not pass; generation/write skipped",
                    "readiness": readiness,
                    "writes_executed": False,
                    "canonical_writes_allowed": False,
                }
            )  # type: ignore[return-value]
        try:
            from cms.service.db import get_conn

            with get_conn() as conn:
                now = _ops_reference_ts_for_cadence(conn, report_kind)
                result = generate_and_store_report(conn, cadence=report_kind, readiness=readiness, now=now)  # type: ignore[arg-type]
            result.update({"task_id": task_id, "writes_executed": True, "canonical_writes_allowed": False})
            return _xcom_safe(result)  # type: ignore[return-value]
        except ReportReadinessBlocked as exc:
            return {"task_id": task_id, "ok": False, "blocked": True, "reason": str(exc), "writes_executed": False}
    scope = {
        "collect_qa_evidence_readiness": "qa",
        "collect_promotion_check_readiness": "promotion",
        "collect_job_metadata_readiness": "ops",
        "build_readiness_report_status": "all",
    }[task_id]
    payload = build_readiness_report_status(
        scope=scope,
        report_kind=report_kind,
        dag_id=dag_id,
        schedule=schedule,
        include_langgraph_review=task_id == "build_readiness_report_status",
    )
    payload.update({"task_id": task_id, "ok": True, "blocked": False})
    return _xcom_safe(payload)  # type: ignore[return-value]



def resolve_report_period_from_watermark(*, cadence: str, watermark: datetime | None, timezone: str = AIRFLOW_REPORT_READINESS_TIMEZONE) -> dict[str, object]:
    """Resolve the report period from the latest data watermark without DB writes."""

    from cms.workflow.reports.generation import compute_report_period, validate_cadence

    report_cadence = validate_cadence(cadence)
    if watermark is None:
        return {"cadence": report_cadence, "watermark": None, "period": None, "blocked": True, "reason": "watermark_unavailable"}
    if watermark.tzinfo is None or watermark.utcoffset() is None:
        watermark = watermark.replace(tzinfo=UTC)
    reference = _reference_ts_from_watermark(report_cadence, watermark)
    period = compute_report_period(report_cadence, now=reference, timezone=timezone)
    return {
        "cadence": report_cadence,
        "watermark": watermark.isoformat(),
        "reference_ts": reference.isoformat(),
        "period": {
            "period_start": period.period_start.isoformat(),
            "period_end": period.period_end.isoformat(),
            "period_key": period.period_key,
        },
        "blocked": False,
    }


def _reference_ts_from_watermark(cadence: str, watermark: datetime) -> datetime:
    if cadence == "monthly":
        first_this_month = watermark.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if first_this_month.month == 12:
            return first_this_month.replace(year=first_this_month.year + 1, month=1)
        return first_this_month.replace(month=first_this_month.month + 1)
    if cadence == "weekly":
        days_until_next_monday = (7 - watermark.weekday()) % 7
        return watermark + timedelta(days=days_until_next_monday or 7)
    return watermark + timedelta(days=1)


def _latest_ops_source_ts(conn: Any) -> datetime | None:
    cur = conn.cursor()
    candidates = []
    for table, column in (
        ("mart.pmax_forecast_15min", "target_ts"),
        ("mart.peak_feature_15min", "window_ts"),
        ("mart.anomaly_warning_1h", "target_ts"),
        ("live.measurement_15min", "bucket_ts"),
    ):
        try:
            cur.execute(f"SELECT MAX({column}) FROM {table};")
            value = cur.fetchone()[0]
            if value is not None:
                candidates.append(value)
        except Exception:
            conn.rollback()
    if not candidates:
        return None
    latest = max(candidates)
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=UTC)
    return latest


def _ops_reference_ts_for_cadence(conn: Any, cadence: str) -> datetime | None:
    latest = _latest_ops_source_ts(conn)
    if latest is None:
        return None
    return _reference_ts_from_watermark(cadence, latest)


def _langgraph_report_branch_enabled() -> bool:
    return os.getenv(LANGGRAPH_REPORT_BRANCH_ENV, "1").strip().lower() not in {"0", "false", "no", "off"}


def build_report_langgraph_context(*, snapshot: Mapping[str, object], task_id: str) -> dict[str, object]:
    """Map a read-only report snapshot into LangGraph review context."""

    contracts = task_contracts()
    contract = contracts.get(task_id, {})
    raw_reads = contract.get("reads", ()) if isinstance(contract, Mapping) else ()
    reads = tuple(str(item) for item in raw_reads) if isinstance(raw_reads, (tuple, list)) else ()
    probes = snapshot.get("probes")
    qa_checks, limitations = _qa_context_from_probes(probes)
    request_id = ":".join(
        str(snapshot.get(key) or "unknown")
        for key in ("dag_id", "report_kind", "scope", "generated_at")
    )
    return {
        "request_id": request_id,
        "dag_id": str(snapshot.get("dag_id") or DAG_ID),
        "task_id": task_id,
        "report_kind": str(snapshot.get("report_kind") or "daily"),
        "scope": str(snapshot.get("scope") or "all"),
        "schedule": str(snapshot.get("schedule") or AIRFLOW_REPORT_READINESS_SCHEDULE),
        "timezone": str(snapshot.get("timezone") or AIRFLOW_REPORT_READINESS_TIMEZONE),
        "request_type": "query",
        "agent_route": "report",
        "qa_blocked": any(status == "fail" for status in qa_checks.values()),
        "qa_checks": qa_checks,
        "data_sources": reads,
        "assumptions": (
            "scheduled report generation uses the LangGraph review service path",
            "read-only readiness snapshot; no production or canonical writes are executed",
        ),
        "limitations": tuple(limitations),
        "title": f"{str(snapshot.get('report_kind') or 'daily')} CMS readiness report",
    }


def report_readiness_to_agent_request(*, snapshot: Mapping[str, object], task_id: str, route_hint: str = "evidence_answer") -> Any:
    """Convert a report readiness snapshot into a LangGraph AgentRequest lazily."""

    from cms.contracts.core import AgentRequest

    context = build_report_langgraph_context(snapshot=snapshot, task_id=task_id)
    return AgentRequest(
        text=f"CMS {context['report_kind']} readiness evidence for {context['scope']} scope",
        route_hint=route_hint,  # type: ignore[arg-type]
        user_id="airflow:report_dag",
        context=context,
    )


def run_report_langgraph_branch(*, snapshot: Mapping[str, object], task_id: str) -> dict[str, object]:
    """Run the service LangGraph review path for scheduled report generation."""

    try:
        from cms.contracts.core import to_plain_dict
        from cms.workflow.langgraph.review import GraphState, run_review
    except ModuleNotFoundError as exc:
        return {
            "ok": True,
            "blocked": False,
            "engine": "cms.workflow.langgraph.review.run_review",
            "skipped": True,
            "reason": f"optional LangGraph review dependency unavailable: {exc.name}",
            "writes_enabled": False,
            "canonical_writes_allowed": False,
        }

    request = report_readiness_to_agent_request(snapshot=snapshot, task_id=task_id, route_hint="evidence_answer")
    state = run_review(GraphState(request=request))
    if state.route not in LANGGRAPH_REPORT_BRANCH_ALLOWED_ROUTES:
        return {
            "ok": False,
            "blocked": True,
            "engine": "cms.workflow.langgraph.review.run_review",
            "reason": f"unexpected report route: {state.route}",
            "route": state.route,
            "side_effects_executed": state.side_effects_executed,
            "writes_enabled": False,
            "canonical_writes_allowed": False,
        }
    if state.side_effects_executed or state.needs_human:
        return {
            "ok": False,
            "blocked": True,
            "engine": "cms.workflow.langgraph.review.run_review",
            "reason": "LangGraph report branch violated no-side-effect or human-gate contract",
            "route": state.route,
            "needs_human": state.needs_human,
            "side_effects_executed": state.side_effects_executed,
            "writes_enabled": False,
            "canonical_writes_allowed": False,
        }
    return _xcom_safe(
        {
            "ok": True,
            "blocked": False,
            "engine": "cms.workflow.langgraph.review.run_review",
            "route": state.route,
            "route_reason": state.route_reason,
            "request_type": state.request_type,
            "agent_route": state.agent_route,
            "qa_status": state.qa_summary.status if state.qa_summary else None,
            "evidence_packet": to_plain_dict(state.evidence_packet) if state.evidence_packet else None,
            "report_shell": state.report_draft is not None,
            "report_draft": to_plain_dict(state.report_draft) if state.report_draft else None,
            "response": to_plain_dict(state.response) if state.response else None,
            "messages": tuple(state.messages),
            "needs_human": state.needs_human,
            "side_effects_executed": state.side_effects_executed,
            "writes_enabled": False,
            "canonical_writes_allowed": False,
        }
    )  # type: ignore[return-value]


def _qa_context_from_probes(probes: object) -> tuple[dict[str, str], list[str]]:
    qa_checks = {
        "read_only_contract": "pass",
        "production_write_gate": "pass",
        "canonical_write_gate": "pass",
    }
    limitations: list[str] = []
    if not isinstance(probes, Mapping):
        qa_checks["readiness_probes"] = "skipped"
        limitations.append("probe_skipped: readiness probe payload unavailable")
        return qa_checks, limitations
    if probes.get("status") == "skipped":
        qa_checks["readiness_probes"] = "skipped"
        reason = probes.get("reason")
        limitations.append(f"probe_skipped: {reason}" if reason else "probe_skipped: external readiness probes disabled")
        return qa_checks, limitations
    for name, payload in probes.items():
        if not isinstance(payload, Mapping):
            qa_checks[f"probe.{name}"] = "warn"
            limitations.append(f"probe_warning: {name} payload is unavailable")
            continue
        status = str(payload.get("status") or "unknown")
        if status == "ok":
            qa_checks[f"probe.{name}"] = "pass"
        elif status == "skipped":
            qa_checks[f"probe.{name}"] = "skipped"
            limitations.append(f"probe_skipped: {name}: {payload.get('reason') or 'not configured'}")
        elif status == "partial":
            errors = payload.get("errors")
            error_text = str(errors or "partial results")
            if "required" in error_text or "mart.anomaly" in error_text or "mart.peak_feature" in error_text:
                qa_checks[f"probe.{name}"] = "fail"
                limitations.append(f"probe_blocked: {name}: {error_text}")
            else:
                qa_checks[f"probe.{name}"] = "warn"
                limitations.append(f"probe_partial: {name}: {error_text}")
        else:
            qa_checks[f"probe.{name}"] = "warn"
            limitations.append(f"probe_warning: {name}: {payload.get('reason') or status}")
    return qa_checks, limitations


def _collect_readiness_probes(*, scope: str) -> dict[str, object]:
    probes_enabled = os.getenv(PROBE_ENV_FLAG, "0") == "1"
    if not probes_enabled:
        return {"status": "skipped", "reason": f"{PROBE_ENV_FLAG}=1 is required for external read-only probes"}

    return {
        "postgres": _postgres_readiness(scope=scope),
        "grafana": _http_health_probe("GRAFANA_URL", default_path="/api/health"),
        "prometheus": _http_health_probe("PROMETHEUS_URL", default_path="/-/ready"),
    }


def _postgres_readiness(*, scope: str) -> dict[str, object]:
    config = _postgres_config_from_env()
    if config is None:
        return {"status": "skipped", "reason": "postgres connection environment is missing or placeholder"}

    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError as exc:
        return {"status": "skipped", "reason": f"psycopg2 unavailable: {exc.__class__.__name__}"}

    queries = _postgres_queries(scope)
    results: dict[str, object] = {}
    errors: dict[str, str] = {}
    try:
        conn = psycopg2.connect(
            host=str(config["host"]),
            port=int(config["port"]),
            dbname=str(config["dbname"]),
            user=str(config["user"]),
            password=str(config["password"]),
            sslmode=str(config["sslmode"]),
            connect_timeout=5,
        )
        try:
            for name, sql in queries.items():
                try:
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute("BEGIN READ ONLY")
                        cur.execute("SET LOCAL statement_timeout = '5000ms'")
                        cur.execute(sql)
                        rows = cur.fetchall()
                    conn.rollback()
                    results[name] = [_json_safe_mapping(row) for row in rows]
                except Exception as exc:  # pragma: no cover - depends on live DB catalog drift
                    conn.rollback()
                    errors[name] = f"{exc.__class__.__name__}: {str(exc).splitlines()[0][:240]}"
        finally:
            conn.close()
    except Exception as exc:  # pragma: no cover - depends on runtime network/credentials
        return {"status": "error", "reason": f"{exc.__class__.__name__}: {str(exc).splitlines()[0][:240]}"}

    return {
        "status": "ok" if not errors else "partial",
        "scope": scope,
        "query_count": len(queries),
        "results": results,
        "errors": errors,
    }


def _postgres_config_from_env() -> dict[str, str | int] | None:
    host = os.getenv("POSTGRES_HOST") or os.getenv("DB_HOST")
    dbname = os.getenv("POSTGRES_DB") or os.getenv("DB_NAME")
    user = os.getenv("POSTGRES_USER") or os.getenv("DB_USER")
    password = os.getenv("POSTGRES_PASSWORD") or os.getenv("DB_PASS") or os.getenv("DB_PASSWORD")
    if any(_is_placeholder(value) for value in (host, dbname, user, password)):
        return None
    assert host is not None
    assert dbname is not None
    assert user is not None
    assert password is not None
    return {
        "host": host,
        "port": int(os.getenv("POSTGRES_PORT") or os.getenv("DB_PORT") or "5432"),
        "dbname": dbname,
        "user": user,
        "password": password,
        "sslmode": os.getenv("POSTGRES_SSLMODE", "disable"),
    }


def _postgres_queries(scope: str) -> dict[str, str]:
    active_values = ",\n                ".join(f"('{name}')" for name in SERVICE_START_REPORT_TABLES)
    common = {
        "db_clock": "SELECT now() AS db_now",
        "service_start_relation_presence": f"""
            SELECT name, to_regclass(name) IS NOT NULL AS present
            FROM (VALUES
                {active_values}
            ) AS t(name)
            ORDER BY name
        """,
        "service_start_relation_estimates": f"""
            WITH active_tables(name) AS (
                VALUES
                {active_values}
            ), presence AS (
                SELECT name, to_regclass(name) AS oid FROM active_tables
            )
            SELECT p.name, COALESCE(s.n_live_tup, c.reltuples)::bigint AS estimated_rows
            FROM presence p
            LEFT JOIN pg_class c ON c.oid = p.oid
            LEFT JOIN pg_stat_all_tables s ON s.relid = p.oid
            ORDER BY p.name
        """,
    }
    qa = {
        "live_event_freshness": """
            SELECT max(event_ts) AS max_event_ts, max(consumed_at) AS max_consumed_at
            FROM live.measurement_event
        """,
        "recent_qa_summary": """
            SELECT 'qa.meter_tag' AS source, count(*) AS rows, max(created_at) AS latest_created_at
            FROM qa.meter_tag
            UNION ALL
            SELECT 'qa.bad_row' AS source, count(*) AS rows, max(created_at) AS latest_created_at
            FROM qa.bad_row
        """,
        "pipeline_metric_latest": """
            SELECT run_id, max(metric_ts) AS latest_metric_ts, count(*) AS metric_rows
            FROM ops.metric
            GROUP BY run_id
            ORDER BY latest_metric_ts DESC
            LIMIT 10
        """,
    }
    promotion = {
        "pmax_forecast_summary": """
            SELECT count(*) AS rows, max(target_ts) AS latest_target_ts, max(created_at) AS latest_created_at
            FROM mart.pmax_forecast_15min
        """,
        "pmax_inference_summary": """
            SELECT status, quality_status, count(*) AS rows, max(started_at) AS latest_started_at
            FROM ops.pmax_log
            GROUP BY status, quality_status
            ORDER BY latest_started_at DESC NULLS LAST
        """,
        "anomaly_feature_summary": """
            SELECT count(*) AS rows, max(bucket_ts) AS latest_bucket_ts, max(created_at) AS latest_created_at
            FROM mart.anomaly_feature_1h
        """,
        "anomaly_warning_summary": """
            SELECT count(*) AS rows, max(target_ts) AS latest_target_ts, max(created_at) AS latest_created_at
            FROM mart.anomaly_warning_1h
        """,
        "anomaly_inference_summary": """
            SELECT status, count(*) AS rows, max(started_at) AS latest_started_at
            FROM ops.anomaly_log
            GROUP BY status
            ORDER BY latest_started_at DESC NULLS LAST
        """,
    }
    ops = {
        "model_serving_evidence_packet_summary": """
            SELECT dry_run, writes_enabled, count(*) AS rows, max(created_at) AS latest_created_at
            FROM qa.serving_evidence
            GROUP BY dry_run, writes_enabled
            ORDER BY latest_created_at DESC NULLS LAST
        """,
    }
    if scope == "qa":
        return {**common, **qa}
    if scope == "promotion":
        return {**common, **promotion}
    if scope == "ops":
        return {**common, **ops}
    return {**common, **qa, **promotion, **ops}


def _http_health_probe(env_name: str, *, default_path: str) -> dict[str, object]:
    base_url = os.getenv(env_name)
    if _is_placeholder(base_url):
        return {"status": "skipped", "reason": f"{env_name} is not configured"}
    url = str(base_url).rstrip("/") + default_path
    try:
        request = Request(url, method="GET")
        with urlopen(request, timeout=5) as response:  # noqa: S310 - operator-provided internal URL, read-only health probe
            return {"status": "ok", "http_status": response.status, "url_env": env_name}
    except (OSError, URLError) as exc:
        return {"status": "error", "reason": f"{exc.__class__.__name__}: {str(exc)[:240]}", "url_env": env_name}


def _is_placeholder(value: object) -> bool:
    if value is None:
        return True
    return str(value).strip().lower() in PLACEHOLDER_VALUES


def _json_safe_mapping(row: Mapping[str, object]) -> dict[str, object]:
    return {str(key): _json_safe_value(value) for key, value in row.items()}


def _json_safe_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _xcom_safe(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return _xcom_safe(asdict(value))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _xcom_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_xcom_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def make_airflow_dag(
    *,
    enabled: bool = AIRFLOW_DAG_ENABLED,
    dag_id: str = DAG_ID,
    schedule: str = AIRFLOW_REPORT_READINESS_SCHEDULE,
    report_kind: str = "daily",
) -> object:
    """Build the scheduled read-only Airflow DAG when Airflow imports this wrapper."""

    if not enabled:
        return describe_dag(dag_id=dag_id, schedule=schedule, report_kind=report_kind)

    airflow = import_module("airflow")
    python_operator = import_module("airflow.operators.python")
    dag_class = airflow.DAG
    python_operator_class = python_operator.PythonOperator

    dag = dag_class(
        dag_id=dag_id,
        schedule=schedule,
        start_date=datetime(2026, 1, 1, tzinfo=ZoneInfo(AIRFLOW_REPORT_READINESS_TIMEZONE)),
        catchup=AIRFLOW_CATCHUP,
        is_paused_upon_creation=AIRFLOW_IS_PAUSED_UPON_CREATION,
        max_active_runs=AIRFLOW_MAX_ACTIVE_RUNS,
        default_args={"owner": "cms", "retries": AIRFLOW_DEFAULT_RETRIES, "retry_delay": timedelta(seconds=AIRFLOW_RETRY_DELAY_SECONDS)},
        tags=["cms", report_kind, "report", "readiness-first", "scheduled"],
    )
    with dag:
        previous: Any | None = None
        for task_id in TASK_IDS:
            task = python_operator_class(
                task_id=task_id,
                python_callable=airflow_task_entrypoint,
                op_kwargs={"task_id": task_id, "report_kind": report_kind, "dag_id": dag_id, "schedule": schedule},
                do_xcom_push=False,
                trigger_rule=AIRFLOW_TASK_TRIGGER_RULE,
            )
            if previous is not None:
                previous >> task
            previous = task
    return dag


__all__ = [
    "AIRFLOW_DAG_ENABLED",
    "AIRFLOW_RUNTIME_DEPLOYED",
    "DAG_ID",
    "LANGGRAPH_REPORT_BRANCH_ENV",
    "PROBE_ENV_FLAG",
    "TASK_IDS",
    "ReportReadinessAirflowDag",
    "airflow_task_entrypoint",
    "build_report_langgraph_context",
    "build_readiness_report_status",
    "describe_dag",
    "make_airflow_dag",
    "report_readiness_to_agent_request",
    "resolve_report_period_from_watermark",
    "run_report_langgraph_branch",
    "task_contracts",
]
