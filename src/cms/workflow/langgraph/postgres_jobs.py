"""PostgreSQL-backed review job store for the LangGraph worker.

This module is optional-dependency safe at import time: psycopg is imported only when a
Postgres-backed store is instantiated. The store keeps the same dry-run/side-effect-free
contract as the in-memory ReviewJobStore while making queued work durable in
``ops.langgraph_jobs`` for a separate worker container to claim.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from cms.contracts.agent import classify_route
from cms.contracts.core import AgentRequest, ChatRoute, to_plain_dict
from cms.contracts.job import ApiJob, JobType
from cms.workflow.langgraph.skeleton import GraphState, run_review
from cms.workflow.review_jobs import INLINE_ROUTES

ASYNC_ROUTES: tuple[ChatRoute, ...] = ("evidence_answer", "needs_job", "approval_required", "report_shell")
_JOB_TYPE_BY_ROUTE: dict[ChatRoute, JobType] = {
    "evidence_answer": "qa_check",
    "needs_job": "build_report_packet",
    "report_shell": "render_report",
    "approval_required": "qa_check",
}
_ALLOWED_JOB_TYPES: frozenset[str] = frozenset(_JOB_TYPE_BY_ROUTE.values()) | {"refresh_cache", "replay_window", "render_report"}

DDL = """
CREATE SCHEMA IF NOT EXISTS ops;

CREATE TABLE IF NOT EXISTS ops.langgraph_jobs (
    job_id text PRIMARY KEY,
    route text NOT NULL,
    status text NOT NULL DEFAULT 'queued',
    priority integer NOT NULL DEFAULT 100,
    request_payload jsonb NOT NULL,
    context_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    result_payload jsonb,
    error_payload jsonb,
    worker_id text,
    attempts integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 3,
    awaiting_approval boolean NOT NULL DEFAULT false,
    approved_by text,
    side_effects_executed boolean NOT NULL DEFAULT false,
    dry_run boolean NOT NULL DEFAULT true,
    claimed_at timestamptz,
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    next_run_at timestamptz NOT NULL DEFAULT now(),
    CHECK (status IN ('queued','running','awaiting_approval','succeeded','failed','dead_letter','cancelled')),
    CHECK (route IN ('evidence_answer','needs_job','approval_required','report_shell')),
    CHECK (side_effects_executed = false)
);

CREATE INDEX IF NOT EXISTS idx_langgraph_jobs_queue
ON ops.langgraph_jobs (status, next_run_at, priority, created_at)
WHERE status = 'queued';

CREATE INDEX IF NOT EXISTS idx_langgraph_jobs_worker
ON ops.langgraph_jobs (worker_id, updated_at DESC);
"""


def _load_psycopg() -> Any:
    try:
        import psycopg  # type: ignore
        from psycopg.rows import dict_row  # type: ignore
        from psycopg.types.json import Jsonb  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on runtime image
        raise RuntimeError("psycopg[binary] is required for REVIEW_JOB_STORE=postgres") from exc
    return psycopg, dict_row, Jsonb


def _job_type_for(route: ChatRoute, context: dict[str, Any]) -> JobType:
    override = context.get("job_type")
    if isinstance(override, str) and override in _ALLOWED_JOB_TYPES:
        return override  # type: ignore[return-value]
    return _JOB_TYPE_BY_ROUTE.get(route, "build_report_packet")


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _as_jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=_json_default))


class PostgresReviewJobStore:
    """Durable ReviewJobStore implementation backed by ``ops.langgraph_jobs``."""

    def __init__(
        self,
        *,
        worker_id: str | None = None,
        id_prefix: str = "rev",
        auto_migrate: bool | None = None,
        connect_timeout: int = 5,
    ) -> None:
        self.worker_id = worker_id or os.environ.get("LANGGRAPH_WORKER_ID") or "langgraph-worker"
        self.id_prefix = id_prefix
        self.connect_timeout = connect_timeout
        self._psycopg, self._dict_row, self._Jsonb = _load_psycopg()
        if auto_migrate if auto_migrate is not None else _truthy_env("LANGGRAPH_AUTO_MIGRATE", default=True):
            self.ensure_schema()

    @classmethod
    def from_env(cls, *, worker_id: str | None = None, auto_migrate: bool | None = None) -> "PostgresReviewJobStore":
        return cls(worker_id=worker_id, auto_migrate=auto_migrate)

    def _connect(self) -> Any:
        dsn = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_DSN")
        kwargs: dict[str, Any] = {"row_factory": self._dict_row, "connect_timeout": self.connect_timeout}
        if dsn:
            return self._psycopg.connect(dsn, **kwargs)
        host = os.environ.get("POSTGRES_HOST") or os.environ.get("DB_HOST")
        dbname = os.environ.get("POSTGRES_DB") or os.environ.get("DB_NAME")
        user = os.environ.get("POSTGRES_USER") or os.environ.get("DB_USER")
        password = os.environ.get("POSTGRES_PASSWORD") or os.environ.get("DB_PASSWORD")
        port = int(os.environ.get("POSTGRES_PORT") or os.environ.get("DB_PORT") or "5432")
        sslmode = os.environ.get("POSTGRES_SSLMODE")
        params: dict[str, Any] = {"host": host, "port": port, "dbname": dbname, "user": user, "password": password, **kwargs}
        if sslmode:
            params["sslmode"] = sslmode
        return self._psycopg.connect(**params)

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(DDL)
            conn.commit()

    def submit(self, request: AgentRequest) -> dict[str, Any]:
        decision = classify_route(request)
        if decision.route in INLINE_ROUTES:
            state = run_review(GraphState(request=request))
            return {
                "mode": "inline",
                "route": decision.route,
                "reason": decision.reason,
                "response": to_plain_dict(state.response) if state.response else None,
                "writes_allowed": False,
                "side_effects_executed": False,
            }

        job_id = f"{self.id_prefix}-{uuid.uuid4().hex[:12]}"
        context = dict(request.context or {})
        request_payload = {
            "text": request.text,
            "route_hint": request.route_hint,
            "user_id": request.user_id,
            "route": decision.route,
            "reason": decision.reason,
            "job_type": _job_type_for(decision.route, context),
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ops.langgraph_jobs
                    (job_id, route, status, request_payload, context_payload, dry_run, side_effects_executed)
                VALUES
                    (%s, %s, 'queued', %s, %s, true, false)
                """,
                (job_id, decision.route, self._Jsonb(_as_jsonable(request_payload)), self._Jsonb(_as_jsonable(context))),
            )
            conn.commit()
        return {
            "mode": "job",
            "route": decision.route,
            "reason": decision.reason,
            "job_id": job_id,
            "status": "queued",
            "status_url": f"/ops/jobs/{job_id}",
            "writes_allowed": False,
            "side_effects_executed": False,
            "store": "postgres",
        }

    def snapshot(self, job_id: str) -> dict[str, Any]:
        row = self._fetch_job(job_id)
        if row is None:
            raise KeyError(job_id)
        return self._snapshot_from_row(row)

    def approve(self, job_id: str, *, approved_by: str | None = None) -> dict[str, Any]:
        row = self._fetch_job(job_id)
        if row is None:
            raise KeyError(job_id)
        if not row.get("awaiting_approval"):
            raise ValueError(f"job {job_id} is not awaiting approval")
        progress = {"stage": "approved", "approved_by": approved_by or "unknown", "approved_at": datetime.now(UTC).isoformat(), "execution": "deferred"}
        result = dict(row.get("result_payload") or {})
        result.setdefault("job", {})["progress"] = progress
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE ops.langgraph_jobs
                SET status='succeeded', awaiting_approval=false, approved_by=%s,
                    result_payload=%s, finished_at=now(), updated_at=now()
                WHERE job_id=%s
                """,
                (approved_by or "unknown", self._Jsonb(_as_jsonable(result)), job_id),
            )
            conn.commit()
        return self.snapshot(job_id)

    def process(self, job_id: str, *, worker_id: str | None = None) -> dict[str, Any]:
        row = self.claim_job(job_id=job_id, worker_id=worker_id or self.worker_id)
        if row is None:
            existing = self._fetch_job(job_id)
            if existing is None:
                raise KeyError(job_id)
            if existing["status"] not in {"queued", "running"}:
                return self._snapshot_from_row(existing)
            raise RuntimeError(f"could not claim job {job_id} with status {existing['status']}")
        return self.process_claimed(row, worker_id=worker_id or self.worker_id)

    def claim_next_job(self, *, worker_id: str | None = None) -> dict[str, Any] | None:
        return self.claim_job(job_id=None, worker_id=worker_id or self.worker_id)

    def claim_job(self, *, job_id: str | None = None, worker_id: str | None = None) -> dict[str, Any] | None:
        worker = worker_id or self.worker_id
        if job_id:
            sql = """
            WITH next_job AS (
                SELECT job_id FROM ops.langgraph_jobs
                WHERE job_id=%s AND status='queued' AND attempts < max_attempts
                FOR UPDATE SKIP LOCKED
            )
            UPDATE ops.langgraph_jobs j
            SET status='running', worker_id=%s, attempts=attempts+1,
                claimed_at=now(), started_at=COALESCE(started_at, now()), updated_at=now()
            FROM next_job
            WHERE j.job_id=next_job.job_id
            RETURNING j.*
            """
            params = (job_id, worker)
        else:
            sql = """
            WITH next_job AS (
                SELECT job_id FROM ops.langgraph_jobs
                WHERE status='queued' AND next_run_at <= now() AND attempts < max_attempts
                ORDER BY priority ASC, created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE ops.langgraph_jobs j
            SET status='running', worker_id=%s, attempts=attempts+1,
                claimed_at=now(), started_at=COALESCE(started_at, now()), updated_at=now()
            FROM next_job
            WHERE j.job_id=next_job.job_id
            RETURNING j.*
            """
            params = (worker,)
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
            conn.commit()
            return dict(row) if row else None

    def process_claimed(self, row: dict[str, Any], *, worker_id: str | None = None) -> dict[str, Any]:
        worker = worker_id or row.get("worker_id") or self.worker_id
        job_id = row["job_id"]
        try:
            request = payload_to_request(row.get("request_payload") or {}, row.get("context_payload") or {}, job_id=job_id)
            state = run_review(GraphState(request=request))
            status = "awaiting_approval" if state.needs_human else "succeeded"
            result_payload = build_result_payload(row, state)
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE ops.langgraph_jobs
                    SET status=%s, result_payload=%s, awaiting_approval=%s,
                        finished_at=CASE WHEN %s IN ('succeeded','failed','dead_letter') THEN now() ELSE finished_at END,
                        updated_at=now()
                    WHERE job_id=%s AND worker_id=%s
                    """,
                    (status, self._Jsonb(_as_jsonable(result_payload)), state.needs_human, status, job_id, worker),
                )
                conn.commit()
            return self.snapshot(job_id)
        except Exception as exc:
            self.mark_failed(row, exc, worker_id=worker)
            raise

    def mark_failed(self, row: dict[str, Any], exc: Exception, *, worker_id: str | None = None) -> None:
        worker = worker_id or row.get("worker_id") or self.worker_id
        job_id = row["job_id"]
        attempts = int(row.get("attempts") or 0)
        max_attempts = int(row.get("max_attempts") or 3)
        status = "dead_letter" if attempts >= max_attempts else "queued"
        error_payload = {"error_type": type(exc).__name__, "message": str(exc), "worker_id": worker, "attempts": attempts}
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE ops.langgraph_jobs
                SET status=%s, error_payload=%s,
                    next_run_at=CASE WHEN %s='queued' THEN now() + (%s * interval '30 seconds') ELSE next_run_at END,
                    finished_at=CASE WHEN %s='dead_letter' THEN now() ELSE finished_at END,
                    updated_at=now()
                WHERE job_id=%s AND worker_id=%s
                """,
                (status, self._Jsonb(error_payload), status, max(1, attempts), status, job_id, worker),
            )
            conn.commit()

    def _fetch_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM ops.langgraph_jobs WHERE job_id=%s", (job_id,)).fetchone()
            return dict(row) if row else None

    def _snapshot_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        result = row.get("result_payload") or {}
        request_payload = row.get("request_payload") or {}
        job_type = request_payload.get("job_type") or _JOB_TYPE_BY_ROUTE.get(row["route"], "qa_check")
        job = ApiJob(
            job_id=row["job_id"],
            job_type=job_type,
            status="running" if row["status"] == "awaiting_approval" else row["status"] if row["status"] in {"queued", "running", "succeeded", "failed", "cancelled"} else "failed",
            requested_by=request_payload.get("user_id"),
            request_payload=request_payload,
            progress=result.get("job", {}).get("progress", {}),
            result_ref=f"review:{row['job_id']}" if row["status"] in {"succeeded", "awaiting_approval"} else None,
            error_summary=(row.get("error_payload") or {}).get("message"),
            side_effects_executed=False,
        )
        return {
            "job_id": row["job_id"],
            "route": row["route"],
            "reason": request_payload.get("reason", ""),
            "status": row["status"],
            "status_url": job.status_url,
            "awaiting_approval": bool(row.get("awaiting_approval")),
            "approved_by": row.get("approved_by"),
            "writes_allowed": False,
            "dry_run": bool(row.get("dry_run", True)),
            "side_effects_executed": False,
            "worker_id": row.get("worker_id"),
            "attempts": row.get("attempts"),
            "job": to_plain_dict(job),
            "response": result.get("response"),
            "error": row.get("error_payload"),
            "store": "postgres",
        }


def payload_to_request(request_payload: dict[str, Any], context_payload: dict[str, Any], *, job_id: str) -> AgentRequest:
    context = dict(context_payload or {})
    context.setdefault("job_id", job_id)
    return AgentRequest(
        text=str(request_payload.get("text") or ""),
        route_hint=request_payload.get("route_hint"),
        user_id=request_payload.get("user_id"),
        context=context,
    )


def build_result_payload(row: dict[str, Any], state: GraphState) -> dict[str, Any]:
    response = state.response
    if response is not None and response.job_ref is None:
        response = replace(response, job_ref=f"/ops/jobs/{row['job_id']}")
    job_payload = None
    if state.job is not None:
        job_payload = to_plain_dict(state.job)
    return {
        "response": to_plain_dict(response) if response else None,
        "route": state.route,
        "needs_human": state.needs_human,
        "job": job_payload or {"progress": {"stage": "awaiting_approval" if state.needs_human else "completed"}},
        "side_effects_executed": False,
        "dry_run": True,
        "processed_at": datetime.now(UTC).isoformat(),
    }


def _truthy_env(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


__all__ = ["ASYNC_ROUTES", "DDL", "PostgresReviewJobStore", "build_result_payload", "payload_to_request"]
