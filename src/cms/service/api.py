"""Import-safe FastAPI application factory for CMS.

FastAPI is optional: importing this module never imports FastAPI. Calling ``create_app`` returns a
small dataclass descriptor if FastAPI is not installed.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib import import_module
from time import perf_counter
from typing import Any, Protocol, cast

from cms.contracts.core import (
    CANONICAL_SOURCE_TABLES,
    AgentRequest,
    SourceTable,
    to_plain_dict,
)
from cms.contracts.ingestion import (
    MEASUREMENT_RAW_TOPIC,
    SOURCE_AUTHORITY_PC1_ARCHIVE,
    kafka_message_key,
    measurement_raw_event_from_mapping,
    raw_event_to_kafka_value,
    validate_raw_event,
)
from cms.data.live_replay import build_request, describe_mongo_read, read_live_replay
from cms.service.query_planner import QueryPlanningError, make_query_plan
from cms.workflow import review_jobs
from cms.workflow.langgraph_review import ROUTES as CHAT_ROUTES

API_TITLE = "CMS Live/Replay API"
INGESTION_API_TITLE = "CMS Ingestion API"
BACKEND_API_TITLE = "CMS Backend API"
API_VERSION = "0.1.0"
API_ROLE_COMBINED = "combined"
API_ROLE_INGESTION = "ingestion"
API_ROLE_BACKEND = "backend"
IMPORTED_BACKEND_ROUTER_ENV = "CMS_ENABLE_IMPORTED_BACKEND_ROUTERS"
IMPORTED_BACKEND_ROUTER_MODULES = (
    "cms.service.routers.auth",
    "cms.service.routers.chat",
    "cms.service.routers.notifications",
    "cms.service.routers.forecast",
    "cms.service.routers.cms",
    "cms.service.routers.anomalies",
    "cms.service.routers.control",
    "cms.service.routers.report",
    "cms.service.routers.simulator",
    "cms.service.routers.settings",
    "cms.service.routers.users",
)
COMMON_ROUTES = (
    ("GET", "/", "service index and route discovery"),
    ("GET", "/health", "import-safe health contract"),
    ("GET", "/contracts", "canonical source/cache contract"),
)
INGESTION_ROUTES = (
    ("POST", "/ingest/measurements", "validate measurement payload and publish to Kafka; no DB write"),
)
BACKEND_ROUTES = (
    ("POST", "/auth/login", "frontend demo auth facade; issues a local dry-run token only"),
    ("GET", "/auth/me", "frontend demo auth facade; no account lookup or writes"),
    ("POST", "/auth/logout", "frontend demo auth facade; no session mutation"),
    ("POST", "/live-replay/plan", "read-only live/replay plan; no DB I/O"),
    ("POST", "/latency/probe", "dry-run request handling latency around live/replay plan"),
    ("POST", "/query/plan", "read-only parameterized SQL plan for evidence_answer requests"),
    ("POST", "/reports/email/dry-run", "validate report email payload without sending"),
    ("POST", "/chat/route", "classify a request; answer quick_answer inline or register a review job"),
    ("POST", "/chat/stream", "SSE-compatible frontend chat facade over deterministic dry-run route semantics"),
    ("GET", "/notifications/stream", "SSE heartbeat for frontend boot; no notification worker"),
    ("GET", "/simulator/status", "frontend simulator clock status facade; no simulator control"),
    ("GET", "/forecast/models", "frontend forecast model catalogue facade; no artifact load"),
    ("GET", "/forecast/train/status", "frontend forecast training status facade; no trainer execution"),
    ("GET", "/forecast/predict/{model}", "frontend forecast prediction facade; deterministic dry-run series"),
    ("GET", "/forecast/peak", "frontend import P-Max forecast facade; deterministic dry-run series"),
    ("GET", "/ops/jobs/{job_id}", "review job status/result"),
    ("POST", "/ops/jobs/{job_id}/run", "worker stub: run the deferred review (dry-run, no side effects)"),
    ("POST", "/ops/approvals/{job_id}", "approve an approval-gated review job; execution stays deferred"),
    ("GET", "/model/results/summary", "read-only model result table contract; no DB execution"),
    ("POST", "/model-ops/{model_kind}/training/start", "RunPod retraining submission; disabled unless explicit RunPod/state env gates are set"),
    ("GET", "/model-ops/{model_kind}/training/latest", "latest RunPod retraining job record/status"),
    ("GET", "/model-ops/{model_kind}/training/{job_id}/status", "RunPod retraining job status"),
    ("POST", "/model-ops/{model_kind}/artifacts/upload", "candidate artifact upload; disabled unless artifact-write env gate is set"),
    ("POST", "/model-ops/{model_kind}/artifacts/probe", "small authenticated upload probe"),
    ("GET", "/model-ops/{model_kind}/runs/{run_id}", "candidate run summary"),
    ("POST", "/model-ops/{model_kind}/runs/{run_id}/validate", "candidate validation; marker writes require artifact-write env gate"),
    ("POST", "/model-ops/{model_kind}/runs/{run_id}/promote", "promotion; disabled unless promotion-write env gate and confirm=true"),
    ("POST", "/model-ops/{model_kind}/rollback", "rollback; disabled unless promotion-write env gate and confirm=true"),
)
ROUTES = COMMON_ROUTES + INGESTION_ROUTES + BACKEND_ROUTES

# Module-level in-memory review registry. A real deployment swaps this for ops.api_job + a worker.
_REVIEW_STORE = review_jobs.ReviewJobStore()


class KafkaProducerLike(Protocol):
    """Minimal producer protocol for injection; no Kafka client import required."""

    def produce(self, *, topic: str, key: str, value: dict[str, object]) -> dict[str, object]: ...


class UnavailableKafkaProducer:
    """Fallback producer used unless runtime Kafka is explicitly enabled."""

    def produce(self, *, topic: str, key: str, value: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("Kafka producer is not configured in import-safe skeleton")


def build_ingest_producer_from_env(env: dict[str, str] | None = None) -> KafkaProducerLike:
    """Build the ingest producer behind an explicit runtime env gate.

    The disabled branch does not import Kafka client packages. The enabled branch
    lazily imports the runtime adapter, which then lazily imports ``confluent_kafka``.
    """

    values = env or os.environ
    if values.get("CMS_ENABLE_RUNTIME_KAFKA_PRODUCER") != "1":
        return UnavailableKafkaProducer()
    from cms.data.runtime_kafka import create_confluent_kafka_producer

    return create_confluent_kafka_producer(values)


@dataclass(frozen=True)
class ApiSkeleton:

    """Fallback object returned when FastAPI is unavailable."""

    title: str = API_TITLE
    version: str = API_VERSION
    routes: tuple[tuple[str, str, str], ...] = ROUTES
    fastapi_available: bool = False
    role: str = API_ROLE_COMBINED

    def route_paths(self) -> tuple[str, ...]:
        return tuple(path for _, path, _ in self.routes)


def health(*, role: str = API_ROLE_COMBINED, title: str | None = None) -> dict[str, object]:
    """Side-effect-free health payload."""

    return {
        "status": "ok",
        "service": title or api_title_for_role(role),
        "role": role,
        "canonical_tables": list(CANONICAL_SOURCE_TABLES),
        "mongo_role": "recent live/replay cache only",
        "writes_allowed": False,
    }


def routes_for_role(role: str = API_ROLE_COMBINED) -> tuple[tuple[str, str, str], ...]:
    """Return the route contract for one FastAPI deployment role."""

    if role == API_ROLE_COMBINED:
        return ROUTES
    if role == API_ROLE_INGESTION:
        return COMMON_ROUTES + INGESTION_ROUTES
    if role == API_ROLE_BACKEND:
        return COMMON_ROUTES + BACKEND_ROUTES
    raise ValueError(f"unsupported api role: {role}")


def api_title_for_role(role: str) -> str:
    """Return a human-readable title for one FastAPI deployment role."""

    if role == API_ROLE_INGESTION:
        return INGESTION_API_TITLE
    if role == API_ROLE_BACKEND:
        return BACKEND_API_TITLE
    if role == API_ROLE_COMBINED:
        return API_TITLE
    raise ValueError(f"unsupported api role: {role}")


def index(
    *,
    routes: tuple[tuple[str, str, str], ...] = ROUTES,
    role: str = API_ROLE_COMBINED,
    title: str = API_TITLE,
) -> dict[str, object]:
    """Human-friendly root payload for browser/API smoke checks."""

    return {
        "service": title,
        "version": API_VERSION,
        "role": role,
        "status": "ok",
        "docs": "/docs",
        "health": "/health",
        "routes": [{"method": method, "path": path, "description": description} for method, path, description in routes],
        "writes_allowed": False,
    }


def contracts() -> dict[str, object]:
    """Expose the key implementation boundaries without importing optional libraries."""

    return {
        "canonical_source_tables": list(CANONICAL_SOURCE_TABLES),
        "reference_tables": ["reference.corrected_resampled_1min", "reference.corrected_15min", "reference.corrected_1h"],
        "anomaly_source": "canonical observed measurements or approved mart.anomaly_feature; reference.corrected_resampled_* is audit/reference-only",
        "mongo": "recent live/replay cache only; not canonical storage",
        "airflow": "disabled skeleton; no scheduling from this module",
        "langgraph": "optional async evidence/report/job/approval review layer; FastAPI router does primary routing",
        "chat_router_metadata": "two-stage metadata request_type=query/action_request/approval_required/off_topic and agent_route=anomaly/cms/forecast/report/rag; public ChatRoute unchanged",
        "query_planner": "read-only parameterized SELECT plans for evidence_answer; no DB execution",
        "mart_generation": "deferred",
    }


def make_plan_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Build a live/replay plan payload from plain JSON-like input."""

    request = build_request(
        mode=payload.get("mode", "live"),
        table=cast(SourceTable, payload.get("table", "canonical.measurement_15min")),
        start_at=payload.get("start_at"),
        end_at=payload.get("end_at"),
        meter_urns=payload.get("meter_urns", ()),
        limit=int(payload.get("limit", 1_000)),
    )
    result = read_live_replay(request)
    mongo_read = describe_mongo_read(result.plan)
    return {"result": to_plain_dict(result), "mongo_read_skeleton": to_plain_dict(mongo_read)}


def make_latency_probe_payload(payload: dict[str, Any], *, monotonic: Callable[[], float] = perf_counter) -> dict[str, Any]:
    """Measure app-level handling around the read-only live/replay plan contract."""

    started = monotonic()
    plan_payload = make_plan_payload(payload)
    elapsed_ms = (monotonic() - started) * 1_000
    return {
        "route": "/latency/probe",
        "dry_run": True,
        "side_effects_executed": False,
        "writes_allowed": False,
        "evidence_level": "api_dry_run",
        "source_boundary": "canonical observed or mart.anomaly_feature only; reference.corrected_resampled is not service truth",
        "latency_ms": elapsed_ms,
        "plan": plan_payload,
    }


def make_query_plan_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a read-only SQL plan for an evidence-backed question without executing it."""

    plan = make_query_plan(payload)
    return to_plain_dict(plan)


def make_ingest_measurement_payload(payload: dict[str, Any], *, producer: KafkaProducerLike) -> dict[str, Any]:
    """Validate an ingest payload and publish to Kafka through an injected producer only.

    This function never writes PostgreSQL, runs workers, or imports a real Kafka
    client. It returns HTTP-style contract payloads so tests can verify route
    semantics without FastAPI being installed.
    """

    event = measurement_raw_event_from_mapping(payload)
    errors = validate_raw_event(event)
    if errors:
        return {
            "route": "/ingest/measurements",
            "status_code": 422,
            "accepted": False,
            "errors": list(errors),
            "writes_allowed": False,
            "postgres_write_attempted": False,
            "rollup_qa_promotion_attempted": False,
        }

    key = kafka_message_key(event)
    value = raw_event_to_kafka_value(event)
    try:
        ack = producer.produce(topic=MEASUREMENT_RAW_TOPIC, key=key, value=value)
    except Exception as exc:  # noqa: BLE001 - contract maps producer failures to 503-style payloads.
        return {
            "route": "/ingest/measurements",
            "status_code": 503,
            "accepted": False,
            "producer_error": str(exc),
            "writes_allowed": False,
            "postgres_write_attempted": False,
            "rollup_qa_promotion_attempted": False,
        }

    acknowledged = bool(ack.get("acknowledged", False))
    if not acknowledged:
        return {
            "route": "/ingest/measurements",
            "status_code": 503,
            "accepted": False,
            "producer_acknowledged": False,
            "writes_allowed": False,
            "postgres_write_attempted": False,
            "rollup_qa_promotion_attempted": False,
        }
    return {
        "route": "/ingest/measurements",
        "status_code": 202,
        "accepted": True,
        "topic": MEASUREMENT_RAW_TOPIC,
        "key": key,
        "producer_acknowledged": True,
        "producer_ack": ack,
        "raw_payload_hash": event.raw_payload_hash,
        "source_authority": event.source_authority,
        "source_authority_required": SOURCE_AUTHORITY_PC1_ARCHIVE,
        "writes_allowed": False,
        "postgres_write_attempted": False,
        "rollup_qa_promotion_attempted": False,
    }


def make_report_email_dry_run_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a report email payload and return local dry-run queue metadata."""

    recipients = _parse_recipients(payload.get("recipients"))
    subject = _required_text(payload.get("subject"), "subject")
    body = _required_text(payload.get("body"), "body")
    return {
        "route": "/reports/email/dry-run",
        "status": "queued",
        "dry_run": True,
        "side_effects_executed": False,
        "send_attempted": False,
        "writes_allowed": False,
        "evidence_level": "api_dry_run",
        "recipients": recipients,
        "recipient_count": len(recipients),
        "subject": subject,
        "body_bytes": len(body.encode("utf-8")),
        "queue": "local-dry-run",
    }


def _model_result_tables() -> list[str]:
    from cms.data.model_serving_queries import SCHEMA_INVENTORY_TABLES

    return [
        table
        for table in SCHEMA_INVENTORY_TABLES
        if table.startswith(("mart.pmax_", "mart.anomaly_", "ops.pmax_", "ops.anomaly_", "qa."))
    ]


def _model_results_db_config(env: Mapping[str, str]) -> dict[str, str]:
    postgres_password = env.get("POSTGRES_PASSWORD") or ""
    db_password = env.get("DB_PASSWORD") or ""
    host = env.get("POSTGRES_HOST") or env.get("DB_HOST") or ""
    port = env.get("POSTGRES_PORT") or env.get("DB_PORT") or "5432"
    dbname = env.get("POSTGRES_DB") or env.get("DB_NAME") or "cms"
    if postgres_password:
        user = env.get("POSTGRES_USER") or env.get("DB_USER") or ""
        password = postgres_password
    else:
        user = env.get("DB_USER") or env.get("POSTGRES_USER") or ""
        password = db_password
    sslmode = env.get("POSTGRES_SSLMODE") or env.get("DB_SSLMODE") or "prefer"
    return {
        "host": host,
        "port": port,
        "dbname": dbname,
        "user": user,
        "password": password,
        "sslmode": sslmode,
    }


def _iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    return str(value)


def _read_model_results_summary_from_db(run_id: str | None, env: Mapping[str, str]) -> dict[str, Any]:
    config = _model_results_db_config(env)
    missing = [key for key in ("host", "user", "password") if not config[key]]
    if missing:
        return {
            "status": "db_config_missing",
            "db_read_attempted": False,
            "missing_config": missing,
        }

    import psycopg

    query_run_id = run_id
    with psycopg.connect(
        host=config["host"],
        port=config["port"],
        dbname=config["dbname"],
        user=config["user"],
        password=config["password"],
        sslmode=config["sslmode"],
        connect_timeout=5,
    ) as conn, conn.cursor() as cur:
        if query_run_id is None:
            cur.execute(
                """
                SELECT run_id
                FROM qa.serving_evidence
                ORDER BY created_at DESC
                LIMIT 1
                """
            )
            row = cur.fetchone()
            query_run_id = row[0] if row else None

        if query_run_id is None:
            return {
                "status": "empty",
                "db_read_attempted": True,
                "run_id": None,
                "counts": {},
            }

        cur.execute(
            """
            SELECT run_id, base_ts, forecast_origin_ts, dry_run, writes_enabled,
                   pmax_prediction_count, anomaly_prediction_count, evidence, created_at
            FROM qa.serving_evidence
            WHERE run_id = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (query_run_id,),
        )
        evidence_row = cur.fetchone()
        if evidence_row is None:
            return {
                "status": "not_found",
                "db_read_attempted": True,
                "run_id": query_run_id,
                "counts": {},
            }

        _, base_ts, forecast_origin_ts, dry_run, writes_enabled, pmax_count, anomaly_count, evidence, created_at = evidence_row
        evidence = evidence or {}

        cur.execute("SELECT count(*) FROM qa.serving_evidence WHERE run_id = %s", (query_run_id,))
        qa_rows = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM ops.pmax_log WHERE run_id = %s", (query_run_id,))
        pmax_log_rows = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM ops.anomaly_log WHERE run_id = %s", (query_run_id,))
        anomaly_log_rows = cur.fetchone()[0]
        cur.execute(
            """
            SELECT count(*), count(DISTINCT warning_id),
                   count(*) FILTER (
                       WHERE warning_id IS NULL OR meter_urn IS NULL OR target_ts IS NULL OR predicted_p IS NULL
                   )
            FROM mart.anomaly_warning_1h
            WHERE run_id = %s
            """,
            (query_run_id,),
        )
        anomaly_rows, anomaly_distinct_keys, anomaly_null_critical = cur.fetchone()

        pmax_rows = pmax_distinct_keys = pmax_null_critical = 0
        if base_ts is not None:
            cur.execute(
                """
                SELECT count(*), count(DISTINCT logical_meter || '|' || target_ts::text),
                       count(*) FILTER (
                           WHERE logical_meter IS NULL OR target_ts IS NULL OR predicted_p_max IS NULL
                       )
                FROM mart.pmax_forecast_15min
                WHERE base_ts = %s
                """,
                (base_ts,),
            )
            pmax_rows, pmax_distinct_keys, pmax_null_critical = cur.fetchone()

    return {
        "status": "ok",
        "db_read_attempted": True,
        "run_id": query_run_id,
        "base_ts": _iso_or_none(base_ts),
        "forecast_origin_ts": _iso_or_none(forecast_origin_ts),
        "created_at": _iso_or_none(created_at),
        "dry_run": dry_run,
        "writes_enabled": writes_enabled,
        "source_modes": {
            "pmax": evidence.get("pmax_source_mode"),
            "anomaly": evidence.get("anomaly_source_mode"),
        },
        "prediction_counts": {
            "pmax": pmax_count,
            "anomaly": anomaly_count,
        },
        "counts": {
            "mart.pmax_forecast_15min": {
                "rows_by_base_ts": pmax_rows,
                "distinct_keys": pmax_distinct_keys,
                "critical_nulls": pmax_null_critical,
            },
            "mart.anomaly_warning_1h": {
                "rows_by_run_id": anomaly_rows,
                "distinct_keys": anomaly_distinct_keys,
                "critical_nulls": anomaly_null_critical,
            },
            "ops.pmax_log": {"rows_by_run_id": pmax_log_rows},
            "ops.anomaly_log": {"rows_by_run_id": anomaly_log_rows},
            "qa.serving_evidence": {"rows_by_run_id": qa_rows},
        },
    }


def make_model_results_summary_payload(run_id: str | None = None, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Return a read-only model-result summary, using DB read-back when configured."""

    result = {
        "route": "/model/results/summary",
        "role": API_ROLE_BACKEND,
        "dry_run": True,
        "side_effects_executed": False,
        "writes_allowed": False,
        "model_result_tables": _model_result_tables(),
    }
    try:
        result.update(_read_model_results_summary_from_db(run_id, env or os.environ))
    except Exception as exc:
        result.update(
            {
                "status": "db_unavailable",
                "db_read_attempted": True,
                "error": type(exc).__name__,
            }
        )
    return result


def make_auth_login_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a frontend-compatible demo auth payload without account lookup or session writes."""

    email = _required_text(payload.get("email"), "email")
    password = _required_text(payload.get("password"), "password")
    if "@" not in email:
        raise ValueError("valid email is required")
    if not password:
        raise ValueError("password is required")
    user = _facade_user(email)
    return {
        "route": "/auth/login",
        "token": os.getenv("CMS_FRONTEND_FACADE_TOKEN", "change_me"),
        "token_type": "bearer",
        "user": user,
        "dry_run": True,
        "writes_allowed": False,
        "side_effects_executed": False,
        "session_write_attempted": False,
        "credential_store_checked": False,
    }


def make_auth_me_payload(authorization: str | None = None) -> dict[str, Any]:
    """Return the demo user accepted by the imported frontend without persistent auth state."""

    user = _facade_user("admin@honda-rd.eu")
    user.update(
        {
            "route": "/auth/me",
            "authenticated": bool(authorization),
            "dry_run": True,
            "writes_allowed": False,
            "side_effects_executed": False,
            "account_lookup_attempted": False,
        }
    )
    return user


def make_auth_logout_payload() -> dict[str, Any]:
    """Acknowledge logout without revoking or mutating server-side session state."""

    return {
        "route": "/auth/logout",
        "ok": True,
        "dry_run": True,
        "writes_allowed": False,
        "side_effects_executed": False,
        "session_revoked": False,
    }


def make_chat_stream_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Build SSE events for the frontend chat stream from deterministic chat-route semantics."""

    text = payload.get("question", payload.get("text"))
    context = payload.get("context") or {}
    route_hint = payload.get("route_hint")
    user_id = payload.get("user_id")
    if not isinstance(context, dict):
        raise ValueError("context must be an object")
    request = _build_agent_request({"text": text, "context": context, "route_hint": route_hint, "user_id": user_id})

    from cms.workflow.langgraph_review import GraphState, run_review

    state = run_review(GraphState(request=request))
    response = state.response
    route = state.route or "quick_answer"
    message = response.message if response else "dry-run chat facade response prepared"
    session_id = str(payload.get("session_id") or "facade-session")
    return [
        {
            "type": "session",
            "session_id": session_id,
            "dry_run": True,
            "writes_allowed": False,
            "side_effects_executed": False,
        },
        {
            "type": "status",
            "content": "deterministic dry-run route prepared",
            "route": route,
            "dry_run": True,
            "writes_allowed": False,
            "side_effects_executed": False,
        },
        {
            "type": "intent",
            "content": route,
            "dry_run": True,
            "writes_allowed": False,
            "side_effects_executed": False,
        },
        {
            "type": "token",
            "content": message,
            "dry_run": True,
            "writes_allowed": False,
            "side_effects_executed": False,
        },
        {
            "type": "done",
            "route": route,
            "response": to_plain_dict(response) if response else None,
            "dry_run": True,
            "writes_allowed": False,
            "side_effects_executed": False,
        },
    ]


def make_notifications_heartbeat_payload() -> dict[str, Any]:
    """Return a single EventSource-compatible heartbeat; no alert worker or queue read."""

    return {
        "route": "/notifications/stream",
        "type": "heartbeat",
        "message": "frontend facade heartbeat",
        "dry_run": True,
        "writes_allowed": False,
        "side_effects_executed": False,
        "notification_worker_enabled": False,
    }


def make_simulator_status_payload() -> dict[str, Any]:
    """Return simulator clock shape required by the frontend without starting a simulator."""

    return {
        "route": "/simulator/status",
        "running": False,
        "now": "2023-01-01T00:00:00",
        "speed": 3600,
        "worker": {"checks": 0, "anomalies_found": 0, "last_check_at": None},
        "dry_run": True,
        "writes_allowed": False,
        "side_effects_executed": False,
        "simulator_control_enabled": False,
    }


def make_forecast_models_payload() -> dict[str, Any]:
    """Return the imported frontend's forecast model catalogue without loading artifacts."""

    return {
        "route": "/forecast/models",
        "models": [
            {
                "name": "v84-ensemble",
                "label": "v84 ensemble dry-run facade",
                "available": True,
                "horizons": [1, 3],
                "meters": 45,
                "badges": [
                    {"text": "read-only", "color": "#3fb950", "bg": "#3fb95022"},
                    {"text": "dry-run", "color": "#2563eb", "bg": "#2563eb22"},
                ],
            }
        ],
        "dry_run": True,
        "writes_allowed": False,
        "side_effects_executed": False,
        "artifact_load_attempted": False,
    }


def make_forecast_train_status_payload() -> dict[str, Any]:
    """Return done statuses so the frontend can enable prediction without trainer writes."""

    return {
        "route": "/forecast/train/status",
        "status": {"v84-1h": "done", "v84-3h": "done"},
        "dry_run": True,
        "writes_allowed": False,
        "side_effects_executed": False,
        "trainer_started": False,
    }


def make_forecast_predict_payload(model: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a deterministic frontend-compatible forecast series without model execution."""

    params = payload or {}
    meter_urn = str(params.get("meter_urn") or "H2.Z66")
    horizon = int(params.get("horizon") or 1)
    horizon = max(1, min(horizon, 3))
    forecast = [
        {"ts": f"2023-01-01T{hour:02d}:00:00", "yhat_kw": round(42.0 + idx * 1.25, 3)}
        for idx, hour in enumerate(range(1, horizon + 1), start=0)
    ]
    return {
        "route": "/forecast/predict/{model}",
        "model": model,
        "meter_urn": meter_urn,
        "horizon": horizon,
        "forecast": forecast,
        "dry_run": True,
        "writes_allowed": False,
        "side_effects_executed": False,
        "model_execution_attempted": False,
    }


def make_forecast_peak_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return deterministic Import P-Max facade data for the billing panel."""

    params = payload or {}
    requested_as_of = str(params.get("as_of") or "2023-01-01T00:00:00")
    meters = []
    for idx, logical_meter in enumerate(("IMPORT-P1", "IMPORT-P2", "IMPORT-P3", "IMPORT-P4"), start=1):
        base = 120.0 + idx * 10
        predictions = [
            {"horizon_minutes": minutes, "predicted_kw": round(base + step * 2.5, 1)}
            for step, minutes in enumerate((15, 30, 45, 60), start=1)
        ]
        peak = max(predictions, key=lambda row: row["predicted_kw"])
        meters.append(
            {
                "logical_meter": logical_meter,
                "peak_kw": peak["predicted_kw"],
                "peak_at": "2023-01-01T01:00:00",
                "last_import_p_max_kw": round(base + 1.0, 1),
                "data_quality": "dry_run",
                "predictions": predictions,
            }
        )
    return {
        "route": "/forecast/peak",
        "requested_as_of": requested_as_of,
        "lookback_days": int(params.get("lookback_days") or 14),
        "meters": meters,
        "dry_run": True,
        "writes_allowed": False,
        "side_effects_executed": False,
        "model_execution_attempted": False,
    }


def _facade_user(email: str) -> dict[str, Any]:
    return {
        "id": "frontend-facade-user",
        "email": email,
        "name": "Frontend Facade User",
        "role": "demo",
    }


def _sse_line(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


def _parse_recipients(value: object) -> list[str]:
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, list):
        candidates = value
    else:
        raise ValueError("at least one valid recipient is required")
    recipients = [_required_text(candidate, "recipient") for candidate in candidates]
    if not recipients or any(not _looks_like_email(recipient) for recipient in recipients):
        raise ValueError("at least one valid recipient is required")
    return recipients


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    cleaned = value.strip()
    if field_name in {"recipient", "subject"} and any(char in cleaned for char in "\r\n"):
        raise ValueError(f"{field_name} must not contain header control characters")
    return cleaned


def _looks_like_email(value: str) -> bool:
    local, separator, domain = value.partition("@")
    return bool(local and separator and "." in domain and not domain.startswith(".") and not domain.endswith("."))


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _include_imported_backend_routers(app: Any) -> list[str]:
    """Mount team backend routers only when explicitly enabled.

    Several imported src/frontend/backend routes perform DB writes or in-memory mutations.
    The default backend surface remains the CMS no-write facade; full team routers are
    an explicit runtime choice via ``CMS_ENABLE_IMPORTED_BACKEND_ROUTERS=1``.
    """

    mounted: list[str] = []
    for module_name in IMPORTED_BACKEND_ROUTER_MODULES:
        module = import_module(module_name)
        router = module.router
        app.include_router(router)
        mounted.append(module_name)
    return mounted


def _include_model_ops_router(app: Any) -> str:
    """Mount guarded model-ops routes on the backend surface.

    The router itself is no-write/no-network by default; mutating operations require
    explicit environment gates such as ``CMS_MODEL_OPS_ENABLE_RUNPOD`` or
    ``CMS_MODEL_OPS_ENABLE_PROMOTION_WRITES``.
    """

    module_name = "cms.service.routers.model_ops"
    module = import_module(module_name)
    app.include_router(module.router)
    return module_name


def _build_agent_request(payload: dict[str, Any]) -> AgentRequest:
    """Validate a chat payload into an ``AgentRequest`` (no LLM/network)."""

    text = _required_text(payload.get("text"), "text")
    route_hint = payload.get("route_hint")
    if route_hint is not None and route_hint not in CHAT_ROUTES:
        raise ValueError(f"unsupported route_hint: {route_hint}")
    context = payload.get("context") or {}
    if not isinstance(context, dict):
        raise ValueError("context must be an object")
    user_id = payload.get("user_id")
    return AgentRequest(text=text, route_hint=route_hint, user_id=user_id, context=context)


def submit_chat_route(payload: dict[str, Any]) -> dict[str, Any]:
    """Classify a request; answer quick_answer inline or register an async review job."""

    request = _build_agent_request(payload)
    return _REVIEW_STORE.submit(request)


def get_review_job(job_id: str) -> dict[str, Any]:
    """Return a review job status/result snapshot. Raises ``KeyError`` if unknown."""

    return _REVIEW_STORE.snapshot(job_id)


def run_review_job(job_id: str) -> dict[str, Any]:
    """Worker-stub trigger that runs the deferred review for a job (dry-run, no side effects)."""

    snapshot = _REVIEW_STORE.process(job_id)
    snapshot["dry_run"] = True
    snapshot["worker"] = "stub"
    return snapshot


def approve_review_job(payload: dict[str, Any], job_id: str) -> dict[str, Any]:
    """Record human approval for an approval-gated job. Side-effecting execution stays deferred."""

    return _REVIEW_STORE.approve(job_id, approved_by=payload.get("approved_by"))


def _model_payload(value: object) -> dict[str, Any]:
    """Convert Pydantic v1/v2 models or plain dicts to request payloads."""

    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return cast(dict[str, Any], value.model_dump(exclude_none=True))
    if hasattr(value, "dict"):
        return cast(dict[str, Any], value.dict(exclude_none=True))
    raise TypeError(f"unsupported request model: {type(value).__name__}")


def create_app() -> object:
    """Create the combined FastAPI app when available; otherwise return an ``ApiSkeleton``."""

    return _create_app(title=API_TITLE, routes=ROUTES, role=API_ROLE_COMBINED, include_ingest=True, include_backend=True)


def create_ingestion_app() -> object:
    """Create the ingestion-only FastAPI app: health/contracts plus Kafka ingest only."""

    return _create_app(title=INGESTION_API_TITLE, routes=COMMON_ROUTES + INGESTION_ROUTES, role=API_ROLE_INGESTION, include_ingest=True, include_backend=False)


def create_backend_app() -> object:
    """Create the backend-only FastAPI app: read/status/job paths, no Kafka producer route."""

    return _create_app(title=BACKEND_API_TITLE, routes=COMMON_ROUTES + BACKEND_ROUTES, role=API_ROLE_BACKEND, include_ingest=False, include_backend=True)


def _create_app(
    *,
    title: str,
    routes: tuple[tuple[str, str, str], ...],
    role: str,
    include_ingest: bool,
    include_backend: bool,
) -> object:
    try:
        fastapi = import_module("fastapi")
    except ModuleNotFoundError as exc:
        if exc.name == "fastapi":
            return ApiSkeleton(title=title, routes=routes, role=role)
        raise

    pydantic = import_module("pydantic")
    BaseModel = pydantic.BaseModel
    Field = pydantic.Field

    class LiveReplayPlanRequest(BaseModel):
        mode: str = "live"
        table: str = "canonical.measurement_15min"
        start_at: str | None = None
        end_at: str | None = None
        meter_urns: list[str] = Field(default_factory=list)
        limit: int = 1_000

    class IngestMeasurementRequest(BaseModel):
        schema_version: str | None = None
        source_system: str
        source_authority: str | None = None
        source_path: str | None = None
        source_event_id: str | None = None
        meter_urn: str
        measurement: str
        event_ts: str
        value_text: str | None = None
        value_numeric: float | None = None
        unit: str | None = None
        received_at: str
        raw_payload_hash: str | None = None

    class QueryPlanRequest(BaseModel):
        text: str
        context: dict[str, Any] = Field(default_factory=dict)
        route_hint: str | None = None
        user_id: str | None = None
        limit: int | None = None

    class ReportEmailDryRunRequest(BaseModel):
        recipients: str | list[str]
        subject: str
        body: str

    class ChatRouteRequest(BaseModel):
        text: str
        context: dict[str, Any] = Field(default_factory=dict)
        route_hint: str | None = None
        user_id: str | None = None

    class ApprovalRequestPayload(BaseModel):
        approved_by: str | None = None

    app = fastapi.FastAPI(title=title, version=API_VERSION)
    try:
        CORSMiddleware = import_module("fastapi.middleware.cors").CORSMiddleware
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000", "http://127.0.0.1:3000"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    except ModuleNotFoundError:
        pass
    StreamingResponse = import_module("fastapi.responses").StreamingResponse
    body = fastapi.Body(...)

    @app.get("/")
    def _index() -> dict[str, object]:
        return index(routes=routes, role=role, title=title)

    @app.get("/health")
    def _health() -> dict[str, object]:
        return health(role=role, title=title)

    @app.get("/contracts")
    def _contracts() -> dict[str, object]:
        return contracts()

    if include_ingest:
        ingest_producer = build_ingest_producer_from_env()

        @app.post("/ingest/measurements")
        def _ingest_measurements(payload: Any = body) -> dict[str, Any]:
            try:
                request = IngestMeasurementRequest.model_validate(payload)
                result = make_ingest_measurement_payload(_model_payload(request), producer=ingest_producer)
            except ValueError as exc:
                raise fastapi.HTTPException(status_code=422, detail=str(exc)) from exc
            if result["status_code"] == 422:
                raise fastapi.HTTPException(status_code=422, detail=result)
            if result["status_code"] == 503:
                raise fastapi.HTTPException(status_code=503, detail=result)
            return result

    if include_backend:
        @app.post("/auth/login")
        def _auth_login(payload: Any = body) -> dict[str, Any]:
            try:
                return make_auth_login_payload(dict(payload))
            except (TypeError, ValueError) as exc:
                raise fastapi.HTTPException(status_code=400, detail=str(exc)) from exc

        @app.get("/auth/me")
        def _auth_me(authorization: str | None = fastapi.Header(default=None)) -> dict[str, Any]:
            return make_auth_me_payload(authorization)

        @app.post("/auth/logout")
        def _auth_logout() -> dict[str, Any]:
            return make_auth_logout_payload()

        @app.post("/chat/stream")
        def _chat_stream(payload: Any = body) -> Any:
            try:
                events = make_chat_stream_events(dict(payload))
            except (TypeError, ValueError) as exc:
                raise fastapi.HTTPException(status_code=400, detail=str(exc)) from exc
            return StreamingResponse((_sse_line(event) for event in events), media_type="text/event-stream")

        @app.get("/notifications/stream")
        def _notifications_stream() -> Any:
            return StreamingResponse((_sse_line(make_notifications_heartbeat_payload()) for _ in range(1)), media_type="text/event-stream")

        @app.get("/simulator/status")
        def _simulator_status() -> dict[str, Any]:
            return make_simulator_status_payload()

        @app.get("/forecast/models")
        def _forecast_models() -> dict[str, Any]:
            return make_forecast_models_payload()

        @app.get("/forecast/train/status")
        def _forecast_train_status() -> dict[str, Any]:
            return make_forecast_train_status_payload()

        @app.get("/forecast/predict/{model}")
        def _forecast_predict(model: str, meter_urn: str | None = None, horizon: int = 1) -> dict[str, Any]:
            return make_forecast_predict_payload(model, {"meter_urn": meter_urn, "horizon": horizon})

        @app.get("/forecast/peak")
        def _forecast_peak(as_of: str | None = None, lookback_days: int = 14) -> dict[str, Any]:
            return make_forecast_peak_payload({"as_of": as_of, "lookback_days": lookback_days})

        @app.post("/live-replay/plan")
        def _live_replay_plan(payload: Any = body) -> dict[str, Any]:
            try:
                request = LiveReplayPlanRequest.model_validate(payload)
                return make_plan_payload(_model_payload(request))
            except (TypeError, ValueError) as exc:
                raise fastapi.HTTPException(status_code=400, detail=str(exc)) from exc

        @app.post("/latency/probe")
        def _latency_probe(payload: Any = body) -> dict[str, Any]:
            try:
                request = LiveReplayPlanRequest.model_validate(payload)
                return make_latency_probe_payload(_model_payload(request))
            except (TypeError, ValueError) as exc:
                raise fastapi.HTTPException(status_code=400, detail=str(exc)) from exc

        @app.post("/query/plan")
        def _query_plan(payload: Any = body) -> dict[str, Any]:
            try:
                request = QueryPlanRequest.model_validate(payload)
                return make_query_plan_payload(_model_payload(request))
            except (QueryPlanningError, ValueError) as exc:
                raise fastapi.HTTPException(status_code=400, detail=str(exc)) from exc

        @app.post("/reports/email/dry-run")
        def _report_email_dry_run(payload: Any = body) -> dict[str, Any]:
            try:
                request = ReportEmailDryRunRequest.model_validate(payload)
                return make_report_email_dry_run_payload(_model_payload(request))
            except ValueError as exc:
                raise fastapi.HTTPException(status_code=400, detail=str(exc)) from exc

        @app.post("/chat/route")
        def _chat_route(payload: Any = body) -> dict[str, Any]:
            try:
                request = ChatRouteRequest.model_validate(payload)
                return submit_chat_route(_model_payload(request))
            except ValueError as exc:
                raise fastapi.HTTPException(status_code=400, detail=str(exc)) from exc

        @app.get("/ops/jobs/{job_id}")
        def _get_job(job_id: str) -> dict[str, Any]:
            try:
                return get_review_job(job_id)
            except KeyError as exc:
                raise fastapi.HTTPException(status_code=404, detail=f"unknown job: {job_id}") from exc

        @app.post("/ops/jobs/{job_id}/run")
        def _run_job(job_id: str) -> dict[str, Any]:
            try:
                return run_review_job(job_id)
            except KeyError as exc:
                raise fastapi.HTTPException(status_code=404, detail=f"unknown job: {job_id}") from exc

        @app.post("/ops/approvals/{job_id}")
        def _approve_job(job_id: str, payload: Any = None) -> dict[str, Any]:
            try:
                request = ApprovalRequestPayload.model_validate(payload or {})
                return approve_review_job(_model_payload(request), job_id)
            except KeyError as exc:
                raise fastapi.HTTPException(status_code=404, detail=f"unknown job: {job_id}") from exc
            except ValueError as exc:
                raise fastapi.HTTPException(status_code=409, detail=str(exc)) from exc

        @app.get("/model/results/summary")
        def _model_results_summary(run_id: str | None = None) -> dict[str, Any]:
            return make_model_results_summary_payload(run_id=run_id)

        app.state.model_ops_router = _include_model_ops_router(app)

        if _truthy_env(IMPORTED_BACKEND_ROUTER_ENV):
            app.state.imported_backend_routers = _include_imported_backend_routers(app)
        else:
            app.state.imported_backend_routers = []

    return app


__all__ = [
    "API_ROLE_BACKEND",
    "API_ROLE_COMBINED",
    "API_ROLE_INGESTION",
    "API_TITLE",
    "API_VERSION",
    "BACKEND_API_TITLE",
    "BACKEND_ROUTES",
    "COMMON_ROUTES",
    "INGESTION_API_TITLE",
    "INGESTION_ROUTES",
    "ROUTES",
    "ApiSkeleton",
    "api_title_for_role",
    "approve_review_job",
    "build_ingest_producer_from_env",
    "contracts",
    "create_app",
    "create_backend_app",
    "create_ingestion_app",
    "get_review_job",
    "health",
    "index",
    "KafkaProducerLike",
    "make_auth_login_payload",
    "make_auth_logout_payload",
    "make_auth_me_payload",
    "make_chat_stream_events",
    "make_forecast_models_payload",
    "make_forecast_peak_payload",
    "make_forecast_predict_payload",
    "make_forecast_train_status_payload",
    "make_ingest_measurement_payload",
    "make_latency_probe_payload",
    "make_model_results_summary_payload",
    "make_plan_payload",
    "make_query_plan_payload",
    "make_report_email_dry_run_payload",
    "routes_for_role",
    "run_review_job",
    "submit_chat_route",
]
