"""CMS measurement QA and quarantine contracts.

This module describes data-quality gates before analysis/model layers exist. It is
separate from operational anomaly detection: quarantine rows are data-quality issues,
not model-derived equipment anomaly candidates.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from cms.contracts.measurement import CanonicalTable, SourceKind

CheckScope = Literal["load", "window", "replay", "api_read", "promote"]
CheckStatus = Literal["running", "passed", "warning", "failed"]
CheckSeverity = Literal["info", "warning", "error", "fatal"]
CheckResultStatus = Literal["passed", "failed"]
QaStage = Literal["parse", "normalize", "promote", "post_load"]
QuarantineReason = Literal[
    "invalid_ts",
    "invalid_value",
    "duplicate",
    "coverage_fail",
    "late_event",
    "schema_error",
    "unknown_meter",
]
ReviewStatus = Literal["new", "reviewed", "accepted", "discarded", "retried"]


@dataclass(frozen=True)
class MeasurementCheckRun:
    """One QA check run over a source run or canonical measurement window."""

    check_run_id: str
    source_run_id: str
    target_table: CanonicalTable
    check_scope: CheckScope
    window_start: datetime | None = None
    window_end: datetime | None = None
    status: CheckStatus = "running"
    summary: Mapping[str, Any] = field(default_factory=dict)
    table_name: str = "qa.measurement_check_run"


@dataclass(frozen=True)
class MeasurementCheckResult:
    """One named QA result such as coverage, duplicate, timestamp, or value range."""

    check_result_id: str
    check_run_id: str
    check_name: str
    severity: CheckSeverity
    status: CheckResultStatus
    metric_value: float | None = None
    threshold_value: float | None = None
    sample_ref: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)
    table_name: str = "qa.measurement_check_result"

    @property
    def blocks_promote(self) -> bool:
        return self.status == "failed" and self.severity in {"error", "fatal"}


@dataclass(frozen=True)
class MeasurementQuarantineEvent:
    """Data-quality quarantine event for invalid or non-promotable measurements."""

    quarantine_id: str
    source_run_id: str
    source_kind: SourceKind
    reason_code: QuarantineReason
    qa_stage: QaStage
    source_ref: str | None = None
    meter_urn: str | None = None
    measurement: str | None = None
    source_ts: datetime | None = None
    bucket_ts: datetime | None = None
    raw_value: str | None = None
    retryable: bool = False
    review_status: ReviewStatus = "new"
    lineage_ref: str | None = None
    table_name: str = "qa.measurement_quarantine"

    @property
    def is_data_quality_issue(self) -> bool:
        return True


@dataclass(frozen=True)
class MeasurementCoverage:
    """Coverage summary for one meter/measurement/window."""

    coverage_id: str
    source_run_id: str
    target_table: CanonicalTable
    meter_urn: str
    measurement: str
    window_start: datetime
    window_end: datetime
    expected_points: int
    actual_points: int
    gap_count: int = 0
    duplicate_count: int = 0
    late_count: int = 0
    status: Literal["ok", "warning", "failed"] = "ok"
    table_name: str = "qa.measurement_coverage"

    @property
    def coverage_ratio(self) -> float:
        if self.expected_points <= 0:
            return 0.0
        return self.actual_points / self.expected_points


__all__ = [
    "CheckResultStatus",
    "CheckScope",
    "CheckSeverity",
    "CheckStatus",
    "MeasurementCheckResult",
    "MeasurementCheckRun",
    "MeasurementCoverage",
    "MeasurementQuarantineEvent",
    "QaStage",
    "QuarantineReason",
    "ReviewStatus",
]
