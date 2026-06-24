"""Import-safe SQL helper for ``ops.worker_heartbeat`` updates.

The helper builds one bounded PostgreSQL upsert command only. It does not import
runtime database clients, read environment variables, or execute writes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

WORKER_HEARTBEAT_TABLE = "ops.worker_heartbeat"
WORKER_EVENT_LOG_TABLE = "ops.worker_event_log"
WORKER_HEARTBEAT_STATUSES = frozenset({"starting", "running", "degraded", "stopped", "failed", "unknown"})
WORKER_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")

WorkerHeartbeatStatus = Literal["starting", "running", "degraded", "stopped", "failed", "unknown"]


@dataclass(frozen=True)
class WorkerHeartbeatCommand:
    """Parameterized SQL command for one worker heartbeat upsert."""

    target_table: str
    sql: str
    params: dict[str, object]

    def __post_init__(self) -> None:
        if self.target_table != WORKER_HEARTBEAT_TABLE:
            raise ValueError("worker heartbeat target_table must be ops.worker_heartbeat")


@dataclass(frozen=True)
class WorkerEventLogCommand:
    """Parameterized SQL command for one append-only worker event log row."""

    target_table: str
    sql: str
    params: dict[str, object]

    def __post_init__(self) -> None:
        if self.target_table != WORKER_EVENT_LOG_TABLE:
            raise ValueError("worker event log target_table must be ops.worker_event_log")


def make_worker_heartbeat_upsert_command(
    *,
    worker_name: str,
    status: WorkerHeartbeatStatus,
    processed_count: int = 0,
    failed_count: int = 0,
    restart_count: int = 0,
    last_error: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> WorkerHeartbeatCommand:
    """Build a safe upsert command for ``ops.worker_heartbeat``.

    Count fields are deltas for the current bounded run. ``heartbeat_at`` and
    ``updated_at`` are set by PostgreSQL at execution time.
    """

    _validate_worker_name(worker_name)
    if status not in WORKER_HEARTBEAT_STATUSES:
        raise ValueError(f"unsupported worker heartbeat status: {status}")
    _nonnegative_int(processed_count, field_name="processed_count")
    _nonnegative_int(failed_count, field_name="failed_count")
    _nonnegative_int(restart_count, field_name="restart_count")
    details_json = json.dumps(dict(details or {}), ensure_ascii=False, sort_keys=True)
    return WorkerHeartbeatCommand(
        target_table=WORKER_HEARTBEAT_TABLE,
        sql="""
INSERT INTO ops.worker_heartbeat (
    worker_name,
    status,
    heartbeat_at,
    updated_at,
    last_error,
    restart_count,
    processed_count,
    failed_count,
    details
)
VALUES (
    %(worker_name)s,
    %(status)s,
    now(),
    now(),
    %(last_error)s,
    %(restart_count)s,
    %(processed_count)s,
    %(failed_count)s,
    %(details_json)s::jsonb
)
ON CONFLICT (worker_name) DO UPDATE SET
    status = EXCLUDED.status,
    heartbeat_at = EXCLUDED.heartbeat_at,
    updated_at = now(),
    last_error = EXCLUDED.last_error,
    restart_count = ops.worker_heartbeat.restart_count + EXCLUDED.restart_count,
    processed_count = ops.worker_heartbeat.processed_count + EXCLUDED.processed_count,
    failed_count = ops.worker_heartbeat.failed_count + EXCLUDED.failed_count,
    details = EXCLUDED.details
""".strip(),
        params={
            "worker_name": worker_name,
            "status": status,
            "last_error": last_error,
            "restart_count": restart_count,
            "processed_count": processed_count,
            "failed_count": failed_count,
            "details_json": details_json,
        },
    )


def make_worker_event_log_insert_command(
    *,
    worker_name: str,
    status: WorkerHeartbeatStatus,
    processed_count: int = 0,
    failed_count: int = 0,
    restart_count: int = 0,
    last_error: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> WorkerEventLogCommand:
    """Build a safe append-only event row for one bounded worker pass.

    This complements ``ops.worker_heartbeat``: heartbeat stores the latest state,
    while this command preserves start/success/failure history for later audit.
    Count fields are deltas for the current bounded run.
    """

    _validate_worker_name(worker_name)
    if status not in WORKER_HEARTBEAT_STATUSES:
        raise ValueError(f"unsupported worker event status: {status}")
    _nonnegative_int(processed_count, field_name="processed_count")
    _nonnegative_int(failed_count, field_name="failed_count")
    _nonnegative_int(restart_count, field_name="restart_count")
    details_json = json.dumps(dict(details or {}), ensure_ascii=False, sort_keys=True)
    return WorkerEventLogCommand(
        target_table=WORKER_EVENT_LOG_TABLE,
        sql="""
INSERT INTO ops.worker_event_log (
    worker_name,
    event_at,
    status,
    processed_delta,
    failed_delta,
    restart_delta,
    error_message,
    details
)
VALUES (
    %(worker_name)s,
    now(),
    %(status)s,
    %(processed_count)s,
    %(failed_count)s,
    %(restart_count)s,
    %(last_error)s,
    %(details_json)s::jsonb
)
""".strip(),
        params={
            "worker_name": worker_name,
            "status": status,
            "last_error": last_error,
            "restart_count": restart_count,
            "processed_count": processed_count,
            "failed_count": failed_count,
            "details_json": details_json,
        },
    )


def _validate_worker_name(worker_name: str) -> None:
    if not WORKER_NAME_PATTERN.fullmatch(worker_name):
        raise ValueError("worker_name must be short snake_case text")


def _nonnegative_int(value: int, *, field_name: str) -> None:
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


__all__ = [
    "WORKER_HEARTBEAT_STATUSES",
    "WORKER_HEARTBEAT_TABLE",
    "WORKER_EVENT_LOG_TABLE",
    "WorkerEventLogCommand",
    "WorkerHeartbeatCommand",
    "WorkerHeartbeatStatus",
    "make_worker_event_log_insert_command",
    "make_worker_heartbeat_upsert_command",
]
