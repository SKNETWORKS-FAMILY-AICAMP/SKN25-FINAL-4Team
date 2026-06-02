"""Small stdlib-only contracts for the CMS application skeleton.

The canonical analytical source remains PostgreSQL-style tables under ``canonical``.
MongoDB is represented only as a recent live/replay cache; this module performs no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from typing import Any, Literal, cast

CANONICAL_MEASUREMENT_1MIN = "canonical.measurement_1min"
CANONICAL_MEASUREMENT_15MIN = "canonical.measurement_15min"
CANONICAL_MEASUREMENT_1H = "canonical.measurement_1h"
CANONICAL_SOURCE_TABLES = (CANONICAL_MEASUREMENT_1MIN, CANONICAL_MEASUREMENT_15MIN, CANONICAL_MEASUREMENT_1H)

SourceTable = Literal["canonical.measurement_1min", "canonical.measurement_15min", "canonical.measurement_1h"]
ReplayMode = Literal["live", "replay"]

# Legacy 3-route label kept for backward compatibility; superseded by ChatRoute below.
AgentRoute = Literal["query", "report", "approval"]
# Policy chat route decision table (docs/qa/qa_report_chat_policy.md §7). FastAPI router
# classifies into these five routes; the LangGraph review layer only handles the async branches.
ChatRoute = Literal["quick_answer", "evidence_answer", "needs_job", "approval_required", "report_shell"]


@dataclass(frozen=True)
class MeasurementWindow:
    """Half-open canonical measurement window selected by an API or agent request.

    Timestamp semantics are ``start_at <= ts < end_at`` when both bounds are present.
    """

    table: SourceTable
    start_at: datetime | None = None
    end_at: datetime | None = None
    meter_urns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.table not in CANONICAL_SOURCE_TABLES:
            raise ValueError(f"unsupported canonical table: {self.table}")
        if self.start_at is not None and self.end_at is not None and self.start_at >= self.end_at:
            raise ValueError("start_at must be before end_at")


@dataclass(frozen=True)
class LiveReplayRequest:
    """Request contract for recent live or replay reads.

    The source table records the canonical table semantics. Runtime implementations may use MongoDB
    as a recent cache, but should not treat MongoDB as the canonical mart or long-term source.
    """

    mode: ReplayMode
    window: MeasurementWindow
    limit: int = 1_000

    def __post_init__(self) -> None:
        if self.mode not in ("live", "replay"):
            raise ValueError(f"unsupported replay mode: {self.mode}")
        if self.limit <= 0:
            raise ValueError("limit must be positive")


@dataclass(frozen=True)
class LivePoint:
    """One normalized live/replay measurement point."""

    meter_urn: str
    ts: datetime
    value: float
    source_table: SourceTable
    unit: str | None = None
    quality: str | None = None
    tags: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CachePolicy:
    """Mongo cache policy marker; intentionally contains no connection details."""

    backend: str = "mongo_recent_live_replay_cache"
    canonical_tables: tuple[str, ...] = CANONICAL_SOURCE_TABLES
    read_only: bool = True
    max_age_minutes: int = 60 * 24 * 14


@dataclass(frozen=True)
class LiveReplayPlan:
    """Import-safe execution plan; adapters may use it later to perform real reads."""

    request: LiveReplayRequest
    cache_policy: CachePolicy = field(default_factory=CachePolicy)
    mongo_collection: str = "measurement_read_cache"
    writes_allowed: bool = False

    def mongo_filter(self) -> dict[str, Any]:
        query: dict[str, Any] = {"source_table": self.request.window.table}
        if self.request.window.meter_urns:
            query["meter_urn"] = {"$in": list(self.request.window.meter_urns)}
        ts_filter: dict[str, datetime] = {}
        if self.request.window.start_at is not None:
            ts_filter["$gte"] = self.request.window.start_at
        if self.request.window.end_at is not None:
            ts_filter["$lt"] = self.request.window.end_at
        if ts_filter:
            query["ts"] = ts_filter
        return query


@dataclass(frozen=True)
class LiveReplayResult:
    """Read-only live/replay result envelope."""

    plan: LiveReplayPlan
    points: tuple[LivePoint, ...] = ()
    note: str = "skeleton only; no Mongo/network I/O executed"


@dataclass(frozen=True)
class AgentRequest:
    """LangGraph-facing request contract for query/report/approval routing."""

    text: str
    route_hint: ChatRoute | None = None
    user_id: str | None = None
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RouteDecision:
    """LangGraph route decision without requiring LangGraph at import time."""

    route: ChatRoute
    reason: str
    needs_approval: bool = False


@dataclass(frozen=True)
class ReportRequest:
    """Report generation contract; mart generation remains intentionally deferred."""

    title: str
    window: MeasurementWindow
    sections: tuple[str, ...] = ("summary", "signals", "risks")
    mart_generation_deferred: bool = True


@dataclass(frozen=True)
class ApprovalRequest:
    """Human approval contract for side-effecting actions."""

    action: str
    reason: str
    approved: bool = False


def to_plain_dict(value: object) -> dict[str, Any]:
    """Convert dataclass contract objects to JSON-ish dictionaries."""

    if not is_dataclass(value) or isinstance(value, type):
        raise TypeError(f"expected dataclass instance, got {type(value).__name__}")
    return cast(dict[str, Any], _jsonable(asdict(value)))


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value
