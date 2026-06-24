"""Read-only live/replay skeleton for recent EMS measurements.

This file intentionally avoids PyMongo imports and any network or database I/O. MongoDB is modeled
only as a recent cache in front of the canonical measurement tables.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, cast

from cms.contracts.core import (
    CANONICAL_MEASUREMENT_1H,
    CANONICAL_MEASUREMENT_15MIN,
    CANONICAL_SOURCE_TABLES,
    CachePolicy,
    LivePoint,
    LiveReplayPlan,
    LiveReplayRequest,
    LiveReplayResult,
    MeasurementWindow,
    ReplayMode,
    SourceTable,
)

DEFAULT_CACHE_POLICY = CachePolicy()


class RecentCache(Protocol):
    """Minimal in-process cache protocol for tests and later adapters."""

    def read(self, plan: LiveReplayPlan) -> Iterable[LivePoint]:
        """Return points for a plan without mutating external systems."""
        ...

@dataclass
class InMemoryRecentCache:
    """Tiny read-only-ish cache useful for API tests; not a Mongo replacement."""

    points: tuple[LivePoint, ...] = ()

    def read(self, plan: LiveReplayPlan) -> tuple[LivePoint, ...]:
        window = plan.request.window
        rows: list[LivePoint] = []
        wanted = set(window.meter_urns)
        for point in self.points:
            if point.source_table != window.table:
                continue
            if wanted and point.meter_urn not in wanted:
                continue
            if window.start_at is not None and point.ts < window.start_at:
                continue
            if window.end_at is not None and point.ts >= window.end_at:
                continue
            rows.append(point)
            if len(rows) >= plan.request.limit:
                break
        return tuple(rows)


@dataclass(frozen=True)
class CacheReadSummary:
    """Human-readable summary of what a real adapter would do."""

    source_of_truth: tuple[str, str] = CANONICAL_SOURCE_TABLES
    cache_backend: str = DEFAULT_CACHE_POLICY.backend
    cache_role: str = "recent live/replay acceleration only"
    writes_allowed: bool = False
    airflow_enabled: bool = False
    mart_generation_deferred: bool = True


@dataclass(frozen=True)
class MongoReadSkeleton:
    """Safe description of a future Mongo read; no client or connection string is stored."""

    collection: str
    filter: dict[str, object]
    limit: int
    sort: tuple[tuple[str, int], ...] = field(default_factory=lambda: (("ts", 1),))
    projection: dict[str, int] = field(default_factory=lambda: {"_id": 0})


def make_live_replay_plan(request: LiveReplayRequest) -> LiveReplayPlan:
    """Build a read-only plan for live/replay access."""

    return LiveReplayPlan(request=request, cache_policy=DEFAULT_CACHE_POLICY)


def describe_mongo_read(plan: LiveReplayPlan) -> MongoReadSkeleton:
    """Return the Mongo query shape a later adapter could execute."""

    return MongoReadSkeleton(collection=plan.mongo_collection, filter=cast(dict[str, object], plan.mongo_filter()), limit=plan.request.limit)


def read_live_replay(request: LiveReplayRequest, cache: RecentCache | None = None) -> LiveReplayResult:
    """Read from an injected in-memory/test cache, or return an empty skeleton result.

    A production adapter may later pass a cache object backed by Mongo reads. This function itself
    does not import Mongo libraries, create clients, write documents, or contact the network.
    """

    plan = make_live_replay_plan(request)
    points = tuple(cache.read(plan)) if cache is not None else ()
    return LiveReplayResult(plan=plan, points=points)


def build_request(
    *,
    mode: ReplayMode = "live",
    table: SourceTable = CANONICAL_MEASUREMENT_15MIN,
    start_at: datetime | str | None = None,
    end_at: datetime | str | None = None,
    meter_urns: Iterable[str] = (),
    limit: int = 1_000,
) -> LiveReplayRequest:
    """Convenience factory for CLI/API code without Pydantic."""

    window = MeasurementWindow(
        table=table,
        start_at=_parse_dt(start_at),
        end_at=_parse_dt(end_at),
        meter_urns=tuple(meter_urns),
    )
    return LiveReplayRequest(mode=mode, window=window, limit=limit)


def default_1h_request(*, limit: int = 24) -> LiveReplayRequest:
    """Default replay contract for the hourly canonical table."""

    return build_request(mode="replay", table=CANONICAL_MEASUREMENT_1H, limit=limit)


def default_15min_request(*, limit: int = 96) -> LiveReplayRequest:
    """Default live contract for the 15-minute canonical table."""

    return build_request(mode="live", table=CANONICAL_MEASUREMENT_15MIN, limit=limit)


def _parse_dt(value: datetime | str | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
