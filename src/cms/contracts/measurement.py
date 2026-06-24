"""Side-effect-free CMS measurement pipeline contracts.

These contracts describe the pre-model data path only. They do not create database
clients, open network connections, write files, or register schedulers.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

POSTGRES_DB_NAME = "cms"
MONGO_DB_NAME = "cms"

CANONICAL_MEASUREMENT_1MIN = "canonical.measurement_1min"
CANONICAL_MEASUREMENT_15MIN = "canonical.measurement_15min"
CANONICAL_MEASUREMENT_1H = "canonical.measurement_1h"
CANONICAL_MEASUREMENT_TABLES = (CANONICAL_MEASUREMENT_1MIN, CANONICAL_MEASUREMENT_15MIN, CANONICAL_MEASUREMENT_1H)

MONGO_MEASUREMENT_RAW = "measurement_raw"
MONGO_MEASUREMENT_BUFFER = "measurement_buffer"
MONGO_MEASUREMENT_REJECT = "measurement_reject"
MONGO_MEASUREMENT_CURSOR = "measurement_cursor"
MONGO_MEASUREMENT_READ_CACHE = "measurement_read_cache"
MONGO_COLLECTIONS = (
    MONGO_MEASUREMENT_RAW,
    MONGO_MEASUREMENT_BUFFER,
    MONGO_MEASUREMENT_REJECT,
    MONGO_MEASUREMENT_CURSOR,
    MONGO_MEASUREMENT_READ_CACHE,
)

SourceKind = Literal["batch", "live", "replay", "archive"]
ResolutionCode = Literal["1min", "15min", "1h"]
CanonicalTable = Literal["canonical.measurement_1min", "canonical.measurement_15min", "canonical.measurement_1h"]
AggregationPolicy = Literal["avg", "last", "delta", "mode", "worst"]


@dataclass(frozen=True)
class MeasurementWindow:
    """Half-open canonical measurement query window: ``start_at <= ts < end_at``."""

    table: CanonicalTable
    start_at: datetime
    end_at: datetime
    meter_urns: tuple[str, ...] = ()
    measurements: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.table not in CANONICAL_MEASUREMENT_TABLES:
            raise ValueError(f"unsupported canonical table: {self.table}")
        if self.start_at >= self.end_at:
            raise ValueError("start_at must be before end_at")

    def contains(self, ts: datetime) -> bool:
        return self.start_at <= ts < self.end_at


@dataclass(frozen=True)
class MeasurementEvent:
    """Raw live/replay measurement event before equal-interval normalization."""

    event_id: str
    source_kind: SourceKind
    source_ts: datetime
    ingest_ts: datetime
    meter_urn: str
    measurement: str
    value: float
    run_id: str
    unit: str | None = None
    quality_code: str = "raw"
    source_seq: int | None = None
    source_ref: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    mongo_collection: str = MONGO_MEASUREMENT_RAW

    @property
    def lineage_key(self) -> str:
        return f"{self.run_id}:{self.event_id}"


@dataclass(frozen=True)
class MeasurementBucketCandidate:
    """Equal-interval measurement candidate before controlled promotion review."""

    bucket_id: str
    bucket_ts: datetime
    resolution_code: ResolutionCode
    meter_urn: str
    measurement: str
    value: float
    aggregation_policy: AggregationPolicy
    run_id: str
    source_event_ids: tuple[str, ...] = ()
    coverage_ratio: float | None = None
    quality_code: str = "ok"
    promote_status: Literal["pending", "promoted", "rejected"] = "pending"
    mongo_collection: str = MONGO_MEASUREMENT_BUFFER


@dataclass(frozen=True)
class MeasurementCursor:
    """Live/replay cursor and watermark contract for MongoDB ``measurement_cursor``."""

    cursor_id: str
    mode: Literal["live", "replay"]
    window_start: datetime
    window_end: datetime
    watermark_ts: datetime
    status: Literal["running", "paused", "done", "failed"] = "running"
    last_event_id: str | None = None
    mongo_collection: str = MONGO_MEASUREMENT_CURSOR


@dataclass(frozen=True)
class LatencyBudget:
    """Target p95 latency budgets in milliseconds for the pre-model skeleton."""

    event_receive_p95_ms: int
    mongo_write_p95_ms: int
    normalize_p95_ms: int
    qa_light_p95_ms: int
    canonical_promote_p95_ms: int
    dashboard_recent_p95_ms: int
    chat_quick_p95_ms: int
    chat_evidence_p95_ms: int
    background_handoff_p95_ms: int
    report_pipeline_p95_ms: int


def default_latency_budget() -> LatencyBudget:
    """Return conservative p95 budgets for skeleton design and documentation."""

    return LatencyBudget(
        event_receive_p95_ms=200,
        mongo_write_p95_ms=200,
        normalize_p95_ms=2_000,
        qa_light_p95_ms=1_000,
        canonical_promote_p95_ms=5_000,
        dashboard_recent_p95_ms=2_000,
        chat_quick_p95_ms=3_000,
        chat_evidence_p95_ms=8_000,
        background_handoff_p95_ms=1_500,
        report_pipeline_p95_ms=600_000,
    )


__all__ = [
    "AggregationPolicy",
    "CANONICAL_MEASUREMENT_1MIN",
    "CANONICAL_MEASUREMENT_15MIN",
    "CANONICAL_MEASUREMENT_1H",
    "CANONICAL_MEASUREMENT_TABLES",
    "CanonicalTable",
    "LatencyBudget",
    "MONGO_COLLECTIONS",
    "MONGO_DB_NAME",
    "MONGO_MEASUREMENT_BUFFER",
    "MONGO_MEASUREMENT_CURSOR",
    "MONGO_MEASUREMENT_RAW",
    "MONGO_MEASUREMENT_READ_CACHE",
    "MONGO_MEASUREMENT_REJECT",
    "MeasurementBucketCandidate",
    "MeasurementCursor",
    "MeasurementEvent",
    "MeasurementWindow",
    "POSTGRES_DB_NAME",
    "ResolutionCode",
    "SourceKind",
    "default_latency_budget",
]
