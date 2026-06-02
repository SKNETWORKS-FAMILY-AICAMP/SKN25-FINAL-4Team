"""In-memory async review job registry skeleton.

This module is the contract between the FastAPI submission interface (service plane) and the worker
that runs the LangGraph review graph (workflow plane). It is import-safe and side-effect-free: no DB,
no Mongo, no network, no background threads. A real deployment replaces the in-memory store with
``ops.api_job`` rows and runs :meth:`ReviewJobStore.process` inside an Airflow/worker context.

Submission is decoupled from execution (true async semantics):

    submit  -> classify; quick_answer answers inline, other routes register a queued ApiJob ticket
    process -> worker stub runs run_review and stores the AgentResponse (or stops awaiting approval)
    approve -> human approves an approval-gated job; the side-effecting action itself stays deferred
    snapshot-> status/result payload for GET /ops/jobs/{job_id}
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from cms.contracts.agent import AgentResponse, classify_route
from cms.contracts.core import AgentRequest, ChatRoute, to_plain_dict
from cms.contracts.job import ApiJob, JobType
from cms.workflow.langgraph_skeleton import GraphState, run_review

# quick_answer is the only route handled inline; every other route is an async review task.
INLINE_ROUTES: tuple[ChatRoute, ...] = ("quick_answer",)

_JOB_TYPE_BY_ROUTE: dict[ChatRoute, JobType] = {
    "evidence_answer": "qa_check",
    "needs_job": "build_report_packet",
    "report_shell": "render_report",
    "approval_required": "qa_check",  # review/gate before a deferred side-effecting action
}
_JOB_TYPES: frozenset[str] = frozenset(_JOB_TYPE_BY_ROUTE.values()) | {"refresh_cache", "replay_window", "render_report"}


def _job_type_for(route: ChatRoute, context: dict) -> JobType:
    override = context.get("job_type")
    if isinstance(override, str) and override in _JOB_TYPES:
        return override  # type: ignore[return-value]
    return _JOB_TYPE_BY_ROUTE.get(route, "build_report_packet")


@dataclass
class ReviewRecord:
    """Mutable in-memory record for one submitted review job."""

    job: ApiJob
    request: AgentRequest
    route: ChatRoute
    reason: str
    state: GraphState | None = None
    response: AgentResponse | None = None
    awaiting_approval: bool = False
    approved_by: str | None = None


class ReviewJobStore:
    """In-memory registry; not thread-safe and intentionally non-durable."""

    def __init__(self, *, id_prefix: str = "rev") -> None:
        self._records: dict[str, ReviewRecord] = {}
        self._id_prefix = id_prefix
        self._seq = 0

    def _next_id(self) -> str:
        self._seq += 1
        return f"{self._id_prefix}-{self._seq:04d}"

    def _record(self, job_id: str) -> ReviewRecord:
        if job_id not in self._records:
            raise KeyError(job_id)
        return self._records[job_id]

    def submit(self, request: AgentRequest) -> dict:
        """Classify the request; answer quick_answer inline, otherwise register a queued ticket."""

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

        job_id = self._next_id()
        context = dict(request.context or {})
        job = ApiJob(
            job_id=job_id,
            job_type=_job_type_for(decision.route, context),
            status="queued",
            requested_by=request.user_id,
            request_payload={"text": request.text, "route": decision.route},
        )
        self._records[job_id] = ReviewRecord(job=job, request=request, route=decision.route, reason=decision.reason)
        return {
            "mode": "job",
            "route": decision.route,
            "reason": decision.reason,
            "job_id": job_id,
            "status": job.status,
            "status_url": job.status_url,
            "writes_allowed": False,
            "side_effects_executed": False,
        }

    def process(self, job_id: str) -> dict:
        """Worker stub: run the deterministic review and store the result (no real side effects)."""

        record = self._record(job_id)
        record.job = replace(record.job, status="running")
        context = dict(record.request.context or {})
        context.setdefault("job_id", job_id)
        request = replace(record.request, context=context)
        state = run_review(GraphState(request=request))
        record.state = state
        record.response = state.response
        if record.response is not None and record.response.job_ref is None:
            record.response = replace(record.response, job_ref=record.job.status_url)

        if state.needs_human:
            record.awaiting_approval = True
            record.job = replace(record.job, status="running", progress={"stage": "awaiting_approval"})
        else:
            record.awaiting_approval = False
            record.job = replace(
                record.job,
                status="succeeded",
                progress={"stage": "completed"},
                result_ref=f"review:{job_id}",
            )
        return self.snapshot(job_id)

    def approve(self, job_id: str, *, approved_by: str | None = None) -> dict:
        """Record human approval. The side-effecting action stays deferred at this stage."""

        record = self._record(job_id)
        if not record.awaiting_approval:
            raise ValueError(f"job {job_id} is not awaiting approval")
        record.approved_by = approved_by or "unknown"
        record.awaiting_approval = False
        record.job = replace(
            record.job,
            status="succeeded",
            progress={
                "stage": "approved",
                "approved_by": record.approved_by,
                "approved_at": datetime.now(UTC).isoformat(),
                "execution": "deferred",
            },
            result_ref=f"review:{job_id}",
        )
        return self.snapshot(job_id)

    def snapshot(self, job_id: str) -> dict:
        """Status/result payload for GET /ops/jobs/{job_id}."""

        record = self._record(job_id)
        return {
            "job_id": job_id,
            "route": record.route,
            "reason": record.reason,
            "status": record.job.status,
            "status_url": record.job.status_url,
            "awaiting_approval": record.awaiting_approval,
            "approved_by": record.approved_by,
            "writes_allowed": False,
            "side_effects_executed": False,
            "job": to_plain_dict(record.job),
            "response": to_plain_dict(record.response) if record.response else None,
        }


__all__ = [
    "INLINE_ROUTES",
    "ReviewJobStore",
    "ReviewRecord",
]
