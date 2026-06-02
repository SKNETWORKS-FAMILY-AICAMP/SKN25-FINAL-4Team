"""CMS operation, split, and background-job contracts.

These dataclasses describe durable state rows for the pre-model skeleton. They do not
execute jobs or touch PostgreSQL/MongoDB directly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from cms.contracts.measurement import CANONICAL_MEASUREMENT_15MIN, CanonicalTable, ResolutionCode, SourceKind

RunStatus = Literal["queued", "running", "ok", "failed", "cancelled"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled", "expired"]
JobType = Literal["refresh_cache", "build_report_packet", "qa_check", "replay_window", "render_report"]


@dataclass(frozen=True)
class MeasurementLoadRun:
    """Durable run state for batch/live/replay measurement loads."""

    run_id: str
    target_table: CanonicalTable
    run_type: Literal["batch", "live", "replay", "backfill"] = "batch"
    source_kind: SourceKind = "archive"
    resolution_code: ResolutionCode = "15min"
    status: RunStatus = "queued"
    file_count: int = 0
    event_count_in: int = 0
    row_count_out: int = 0
    reject_count: int = 0
    config_version: str | None = None
    code_version: str | None = None
    notes: str | None = None
    table_name: str = "ops.measurement_load_run"


@dataclass(frozen=True)
class MeasurementFileState:
    """Per-source-file progress state for archive measurement loads."""

    run_id: str
    source_file: str
    resolution_code: ResolutionCode
    status: RunStatus = "queued"
    rows_in: int = 0
    rows_out: int = 0
    reject_count: int = 0
    sha256: str | None = None
    gzip_ok: bool | None = None
    error: str | None = None
    table_name: str = "ops.measurement_file_state"


@dataclass(frozen=True)
class DataSplit:
    """Half-open time split for historical backfill, holdout, and live replay."""

    split_id: str
    name: str
    start_at: datetime
    end_at: datetime | None
    purpose: str
    timezone_name: str = "Asia/Seoul"
    is_active: bool = True
    notes: str | None = None
    table_name: str = "ops.data_split"

    @classmethod
    def live_replay_default(cls) -> DataSplit:
        return cls(
            split_id="live_replay_2023",
            name="live_replay",
            start_at=datetime(2023, 1, 1, 9, 0, tzinfo=UTC),
            end_at=None,
            purpose="holdout_replay",
            notes="Default CMS holdout/live replay split discovered after canonical load.",
        )


@dataclass(frozen=True)
class MeasurementReplayRun:
    """Replay execution state from canonical source into MongoDB measurement buffers."""

    replay_run_id: str
    source_split_id: str
    mode: Literal["live", "replay"]
    window_start: datetime
    window_end: datetime
    status: RunStatus = "queued"
    watermark_ts: datetime | None = None
    cursor_ref: str | None = None
    events_read: int = 0
    events_written: int = 0
    reject_count: int = 0
    table_name: str = "ops.measurement_replay_run"


@dataclass(frozen=True)
class ApiJob:
    """FastAPI background handoff contract for work that cannot answer immediately."""

    job_id: str
    job_type: JobType
    status: JobStatus
    requested_by: str | None = None
    request_payload: Mapping[str, Any] = field(default_factory=dict)
    progress: Mapping[str, Any] = field(default_factory=dict)
    result_ref: str | None = None
    error_summary: str | None = None
    side_effects_executed: bool = False
    table_name: str = "ops.api_job"

    @property
    def status_url(self) -> str:
        return f"/ops/jobs/{self.job_id}"


def default_load_run(run_id: str, target_table: CanonicalTable = CANONICAL_MEASUREMENT_15MIN) -> MeasurementLoadRun:
    return MeasurementLoadRun(run_id=run_id, target_table=target_table)


__all__ = [
    "ApiJob",
    "DataSplit",
    "JobStatus",
    "JobType",
    "MeasurementFileState",
    "MeasurementLoadRun",
    "MeasurementReplayRun",
    "RunStatus",
    "default_load_run",
]
