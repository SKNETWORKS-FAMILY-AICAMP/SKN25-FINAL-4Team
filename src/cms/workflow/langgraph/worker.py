"""CLI worker for durable LangGraph review jobs.

The worker polls ``ops.langgraph_jobs`` from PostgreSQL, claims one queued job with
``FOR UPDATE SKIP LOCKED``, runs the deterministic ``run_review`` dry-run workflow, and
writes the result/status back to PostgreSQL. It never performs side effects.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

from cms.workflow.langgraph.postgres_jobs import PostgresReviewJobStore


def load_settings(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CMS LangGraph durable review worker")
    parser.add_argument("--once", action="store_true", help="claim/process at most one queued job and exit")
    parser.add_argument("--loop", action="store_true", help="poll forever")
    parser.add_argument("--healthcheck", action="store_true", help="verify worker configuration/imports and exit")
    parser.add_argument("--migrate", action="store_true", help="create/verify ops.langgraph_jobs schema and exit")
    parser.add_argument("--no-auto-migrate", action="store_true", help="do not auto-create schema on startup")
    parser.add_argument("--worker-id", default=os.environ.get("LANGGRAPH_WORKER_ID", "langgraph-worker"))
    parser.add_argument("--poll-interval", type=float, default=float(os.environ.get("LANGGRAPH_POLL_INTERVAL_SECONDS", "3")))
    parser.add_argument("--max-jobs", type=int, default=int(os.environ.get("LANGGRAPH_MAX_JOBS_PER_TICK", "1")))
    return parser.parse_args(argv)


def healthcheck() -> dict[str, Any]:
    # Instantiate without connecting unless explicit DB health is requested. Container health should
    # stay cheap; schema/DB checks are covered by --migrate and worker startup logs.
    required = [os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_DSN") or os.environ.get("DB_HOST") or os.environ.get("POSTGRES_HOST")]
    ok = all(required)
    return {
        "status": "ok" if ok else "degraded",
        "worker_id": os.environ.get("LANGGRAPH_WORKER_ID", "langgraph-worker"),
        "job_store": os.environ.get("LANGGRAPH_JOB_STORE") or os.environ.get("REVIEW_JOB_STORE") or "postgres",
        "dry_run": os.environ.get("LANGGRAPH_DRY_RUN", "true"),
        "db_config_present": ok,
    }


def run_once(store: PostgresReviewJobStore, *, worker_id: str) -> dict[str, Any] | None:
    row = store.claim_next_job(worker_id=worker_id)
    if row is None:
        return None
    return store.process_claimed(row, worker_id=worker_id)


def main_loop(store: PostgresReviewJobStore, *, worker_id: str, poll_interval: float, max_jobs: int) -> None:
    while True:
        processed = 0
        for _ in range(max_jobs):
            result = run_once(store, worker_id=worker_id)
            if result is None:
                break
            processed += 1
            print(json.dumps({"event": "processed", "job_id": result["job_id"], "status": result["status"], "worker_id": worker_id}, ensure_ascii=False), flush=True)
        if processed == 0:
            time.sleep(poll_interval)


def main(argv: list[str] | None = None) -> int:
    settings = load_settings(argv)
    if settings.healthcheck:
        payload = healthcheck()
        print(json.dumps(payload, ensure_ascii=False), flush=True)
        return 0 if payload["status"] == "ok" else 1

    auto_migrate = not settings.no_auto_migrate
    store = PostgresReviewJobStore.from_env(worker_id=settings.worker_id, auto_migrate=auto_migrate)
    if settings.migrate:
        store.ensure_schema()
        print(json.dumps({"event": "migrated", "schema": "ops.langgraph_jobs"}, ensure_ascii=False), flush=True)
        return 0

    if settings.once or not settings.loop:
        result = run_once(store, worker_id=settings.worker_id)
        print(json.dumps({"event": "idle", "worker_id": settings.worker_id}, ensure_ascii=False) if result is None else json.dumps(result, ensure_ascii=False), flush=True)
        return 0

    main_loop(store, worker_id=settings.worker_id, poll_interval=settings.poll_interval, max_jobs=settings.max_jobs)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
