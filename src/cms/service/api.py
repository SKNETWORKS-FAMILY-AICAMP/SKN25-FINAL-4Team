"""Import-safe FastAPI application skeleton for CMS.

FastAPI is optional: importing this module never imports FastAPI. Calling ``create_app`` returns a
small dataclass skeleton if FastAPI is not installed.
"""

from __future__ import annotations

import os
from collections.abc import Callable
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
    kafka_message_key,
    measurement_raw_event_from_mapping,
    raw_event_to_kafka_value,
    validate_raw_event,
)
from cms.data.live_replay import build_request, describe_mongo_read, read_live_replay
from cms.service.query_planner import QueryPlanningError, make_query_plan
from cms.workflow import review_jobs
from cms.workflow.langgraph_skeleton import ROUTES as CHAT_ROUTES

API_TITLE = "CMS Live/Replay Skeleton"
API_VERSION = "0.1.0"
ROUTES = (
    ("GET", "/", "service index and route discovery"),
    ("GET", "/health", "import-safe health contract"),
    ("GET", "/contracts", "canonical source/cache contract"),
    ("POST", "/ingest/measurements", "validate measurement payload and publish to Kafka; no DB write"),
    ("POST", "/live-replay/plan", "read-only live/replay plan; no DB I/O"),
    ("POST", "/latency/probe", "dry-run request handling latency around live/replay plan"),
    ("POST", "/query/plan", "read-only parameterized SQL plan for evidence_answer requests"),
    ("POST", "/reports/email/dry-run", "validate report email payload without sending"),
    ("POST", "/chat/route", "classify a request; answer quick_answer inline or register a review job"),
    ("GET", "/ops/jobs/{job_id}", "review job status/result"),
    ("POST", "/ops/jobs/{job_id}/run", "worker stub: run the deferred review (dry-run, no side effects)"),
    ("POST", "/ops/approvals/{job_id}", "approve an approval-gated review job; execution stays deferred"),
)

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

    def route_paths(self) -> tuple[str, ...]:
        return tuple(path for _, path, _ in self.routes)


def health() -> dict[str, object]:
    """Side-effect-free health payload."""

    return {
        "status": "ok",
        "service": "cms-api-skeleton",
        "canonical_tables": list(CANONICAL_SOURCE_TABLES),
        "mongo_role": "recent live/replay cache only",
        "writes_allowed": False,
    }


def index() -> dict[str, object]:
    """Human-friendly root payload for browser/API smoke checks."""

    return {
        "service": API_TITLE,
        "version": API_VERSION,
        "status": "ok",
        "docs": "/docs",
        "health": "/health",
        "routes": [{"method": method, "path": path, "description": description} for method, path, description in ROUTES],
        "writes_allowed": False,
    }


def contracts() -> dict[str, object]:
    """Expose the key implementation boundaries without importing optional libraries."""

    return {
        "canonical_source_tables": list(CANONICAL_SOURCE_TABLES),
        "reference_tables": ["reference.corrected_resampled_1min", "reference.corrected_resampled_15min", "reference.corrected_resampled_1h"],
        "anomaly_source": "canonical observed measurements or approved mart.anomaly_input; reference.corrected_resampled_* is audit/reference-only",
        "mongo": "recent live/replay cache only; not canonical storage",
        "airflow": "disabled skeleton; no scheduling from this module",
        "langgraph": "optional async evidence/report/job/approval review layer; FastAPI router does primary routing",
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
        "source_boundary": "canonical observed or mart.anomaly_input only; reference.corrected_resampled is not service truth",
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
    """Create a FastAPI app when available; otherwise return an ``ApiSkeleton``."""

    try:
        fastapi = import_module("fastapi")
    except ModuleNotFoundError as exc:
        if exc.name == "fastapi":
            return ApiSkeleton()
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

    app = fastapi.FastAPI(title=API_TITLE, version=API_VERSION)
    body = fastapi.Body(...)

    @app.get("/")
    def _index() -> dict[str, object]:
        return index()

    @app.get("/health")
    def _health() -> dict[str, object]:
        return health()

    @app.get("/contracts")
    def _contracts() -> dict[str, object]:
        return contracts()

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

    return app


__all__ = [
    "API_TITLE",
    "API_VERSION",
    "ROUTES",
    "ApiSkeleton",
    "approve_review_job",
    "build_ingest_producer_from_env",
    "contracts",
    "create_app",
    "get_review_job",
    "health",
    "index",
    "KafkaProducerLike",
    "make_ingest_measurement_payload",
    "make_latency_probe_payload",
    "make_plan_payload",
    "make_query_plan_payload",
    "make_report_email_dry_run_payload",
    "run_review_job",
    "submit_chat_route",
]
