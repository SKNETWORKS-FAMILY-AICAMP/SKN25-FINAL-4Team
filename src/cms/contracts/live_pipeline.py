"""Import-safe contracts for the CMS live measurement pipeline.

This module is intentionally pure Python: no database clients, no AWS clients, no
file I/O, and no production/canonical writes. It models the live trigger and
worker contracts so implementation experiments can be tested without executing
DDL/DML.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

LIVE_MEASUREMENT_EVENT = "live.measurement_event"
LIVE_MEASUREMENT_POLICY = "live.measurement_policy"
LIVE_MEASUREMENT_1MIN = "live.measurement_1min"
LIVE_MEASUREMENT_15MIN = "live.measurement_15min"
LIVE_MEASUREMENT_1H = "live.measurement_1h"
LIVE_BUCKET_QUEUE = "live.bucket_queue"
LIVE_PROMOTION_CHECK = "live.promotion_check"
QA_LIVE_MEASUREMENT_ISSUE = "qa.live_issue"
MART_PEAK_FEATURE_15MIN = "mart.peak_feature_15min"
MART_PEAK_INPUT_15MIN = "mart.peak_feature_15min"
CANONICAL_MEASUREMENT_1MIN = "canonical.measurement_1min"
CANONICAL_MEASUREMENT_15MIN = "canonical.measurement_15min"
CANONICAL_MEASUREMENT_1H = "canonical.measurement_1h"

SOURCE_MODE_REFERENCE_BACKFILL = "reference_backfill"
SOURCE_MODE_HYBRID_WARM_START = "hybrid_warm_start"
SOURCE_MODE_LIVE_OBSERVED = "live_observed"
SOURCE_LAYER_REFERENCE_CORRECTED_RESAMPLED = "reference.corrected_resampled"
SOURCE_LAYER_KAFKA_RAW = "kafka.measurement_raw_v1"
SOURCE_AUTHORITY_PC1_ARCHIVE = "pc1_archive"

OBSERVED_SOURCE_LAYERS = (
    SOURCE_LAYER_KAFKA_RAW,
    LIVE_MEASUREMENT_EVENT,
    LIVE_MEASUREMENT_1MIN,
    LIVE_MEASUREMENT_15MIN,
    LIVE_MEASUREMENT_1H,
    MART_PEAK_FEATURE_15MIN,
    MART_PEAK_INPUT_15MIN,
    CANONICAL_MEASUREMENT_1MIN,
    CANONICAL_MEASUREMENT_15MIN,
    CANONICAL_MEASUREMENT_1H,
)
REFERENCE_SOURCE_LAYERS = (SOURCE_LAYER_REFERENCE_CORRECTED_RESAMPLED, "corrected_resampled")

CANONICAL_TABLES = (
    CANONICAL_MEASUREMENT_1MIN,
    CANONICAL_MEASUREMENT_15MIN,
    CANONICAL_MEASUREMENT_1H,
)
MART_PEAK_TABLES = (MART_PEAK_FEATURE_15MIN, MART_PEAK_INPUT_15MIN)
LIVE_INJECTOR_SOURCE_AUTHORITIES = (SOURCE_AUTHORITY_PC1_ARCHIVE,)
LIVE_INJECTOR_FORBIDDEN_SOURCE_ROOT_MARKERS = (
    "pc2",
    "pc3",
    "reference",
    "corrected_resampled",
    "canonical",
    "mart",
    "backfill",
)

SourceMode = Literal["reference_backfill", "hybrid_warm_start", "live_observed"]
TriggerOperationKind = Literal["policy_lookup", "measurement_1min_upsert", "bucket_queue_enqueue", "issue_log"]
JobKind = Literal["mean_rollup", "peak_feature"]
Resolution = Literal["1min", "15min", "1h"]
PolicyLookupStatus = Literal["found", "missing", "ambiguous"]
PromotionBlockReason = Literal[
    "coverage_out_of_bounds",
    "cumulative_policy_blocked",
    "unknown_policy_blocked",
    "circular_policy_blocked",
    "heterogeneous_native_cadence",
    "state_hold_last_without_evidence",
    "peak_feature_never_canonical",
    "peak_leakage_block",
]

JOB_KIND_MEAN_ROLLUP: JobKind = "mean_rollup"
JOB_KIND_PEAK_FEATURE: JobKind = "peak_feature"
RESOLUTION_1MIN: Resolution = "1min"
RESOLUTION_15MIN: Resolution = "15min"
RESOLUTION_1H: Resolution = "1h"

ALLOWED_TRIGGER_TARGETS = (
    LIVE_MEASUREMENT_POLICY,
    LIVE_MEASUREMENT_1MIN,
    LIVE_BUCKET_QUEUE,
    QA_LIVE_MEASUREMENT_ISSUE,
)
FORBIDDEN_TRIGGER_TARGETS = CANONICAL_TABLES + MART_PEAK_TABLES + (LIVE_MEASUREMENT_15MIN, LIVE_MEASUREMENT_1H)
ALLOWED_QUEUE_JOB_SPECS: tuple[tuple[JobKind, Resolution], ...] = (
    (JOB_KIND_MEAN_ROLLUP, RESOLUTION_15MIN),
    (JOB_KIND_MEAN_ROLLUP, RESOLUTION_1H),
    (JOB_KIND_PEAK_FEATURE, RESOLUTION_15MIN),
)
QUEUE_IDEMPOTENCY_FIELDS = ("meter_urn", "measurement", "resolution", "bucket_ts", "job_kind", "policy_version")
LIVE_ROLLUP_VALUE_SEMANTIC = "mean_observed_only"
PEAK_BRANCH_TABLES = MART_PEAK_TABLES

# The final production unique source is intentionally not encoded as DDL here.
# The live ledger is modeled as immutable; corrections should be additional
# events/issues/promotion exclusions, not in-place mutation of prior events.
EVENT_LEDGER_MUTABILITY = "immutable_append_only"
EVENT_IDEMPOTENCY_CANDIDATE_FIELDS = ("source_system", "source_event_id", "source_ts", "meter_urn", "measurement")


@dataclass(frozen=True)
class LiveMeasurementEvent:
    """One observed live event after Kafka-to-PostgreSQL ingest, before trigger effects."""

    event_id: str
    meter_urn: str
    measurement: str
    source_ts: datetime
    value: float | None
    source_system: str = SOURCE_LAYER_KAFKA_RAW
    source_event_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LiveMeasurementPolicy:
    """Minimal policy needed by the trigger skeleton.

    ``policy_kind`` values that need domain-specific rules are blocked until a
    finalized policy exists. ``state_hold_last`` requires explicit evidence.
    """

    meter_urn: str
    measurement: str
    policy_version: int
    expected_points_15min: int | None = None
    expected_points_1h: int | None = None
    enabled: bool = True
    native_cadence_seconds: int | None = 60
    heterogeneous_native_cadence: bool = False
    policy_kind: Literal["mean_observed", "state_hold_last", "cumulative", "unknown", "circular"] = "mean_observed"
    state_hold_last_evidence: str | None = None


@dataclass(frozen=True)
class ExpectedPointsPolicy:
    """Expected live bucket points derived from native measurement cadence.

    Sub-minute cadences are intentionally gated: they must either supply explicit
    expected points for the requested bucket or be explicitly approved for cadence
    derivation, so accidental high-frequency streams do not silently inflate QA
    expectations.
    """

    native_cadence_seconds: int = 60
    expected_points_15min: int | None = None
    expected_points_1h: int | None = None
    sub_minute_policy_approved: bool = False


@dataclass(frozen=True)
class PolicyLookupResult:
    """Pure trigger policy lookup outcome, including effective-time context."""

    status: PolicyLookupStatus
    policy: LiveMeasurementPolicy | None = None
    effective_ts: datetime | None = None
    matched_policy_versions: tuple[int, ...] = ()
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.status == "found" and self.policy is None:
            raise ValueError("found policy lookup requires a policy")
        if self.status in {"missing", "ambiguous"} and self.policy is not None:
            raise ValueError(f"{self.status} policy lookup must not include a policy")


@dataclass(frozen=True)
class LiveRollupOutputContract:
    """Output contract for live mean rollup worker tables.

    Live 15-minute and 1-hour rollups carry observed-only mean values. Peak
    predictions/peak inputs remain in the mart branch and are not fields on these
    live rollup outputs.
    """

    table: str
    resolution: Resolution
    value_semantic: str = LIVE_ROLLUP_VALUE_SEMANTIC
    peak_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        expected_table_by_resolution = {
            RESOLUTION_15MIN: LIVE_MEASUREMENT_15MIN,
            RESOLUTION_1H: LIVE_MEASUREMENT_1H,
        }
        if self.resolution not in expected_table_by_resolution:
            raise ValueError(f"live rollup output is unsupported for resolution: {self.resolution}")
        expected_table = expected_table_by_resolution[self.resolution]
        if self.table != expected_table:
            raise ValueError(f"live rollup output table mismatch: {self.table} != {expected_table}")
        if self.value_semantic != LIVE_ROLLUP_VALUE_SEMANTIC:
            raise ValueError(f"unsupported live rollup value semantic: {self.value_semantic}")
        if self.peak_fields:
            raise ValueError("live mean rollup outputs must not expose peak fields")


@dataclass(frozen=True)
class BucketQueueKey:
    """Idempotency key for ``live.bucket_queue``."""

    meter_urn: str
    measurement: str
    resolution: Resolution
    bucket_ts: datetime
    job_kind: JobKind
    policy_version: int

    def __post_init__(self) -> None:
        validate_queue_key(self)

    def as_tuple(self) -> tuple[str, str, Resolution, datetime, JobKind, int]:
        return (self.meter_urn, self.measurement, self.resolution, self.bucket_ts, self.job_kind, self.policy_version)


@dataclass(frozen=True)
class BucketQueueJob:
    """Pure representation of a dirty bucket job to enqueue."""

    key: BucketQueueKey
    status: Literal["pending"] = "pending"


@dataclass(frozen=True)
class IssueRecord:
    """Skeleton issue record; exact ``qa.live_issue`` DDL is not finalized."""

    issue_kind: str
    meter_urn: str
    measurement: str
    event_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class TriggerOperation:
    """Side-effect label for contract tests; not executable SQL or DML."""

    kind: TriggerOperationKind
    target: str

    def __post_init__(self) -> None:
        if self.target not in ALLOWED_TRIGGER_TARGETS:
            raise ValueError(f"trigger operation target is forbidden: {self.target}")


@dataclass(frozen=True)
class TriggerDecisionResult:
    """Result of the trigger skeleton for an inserted live event."""

    operations: tuple[TriggerOperation, ...]
    queue_jobs: tuple[BucketQueueJob, ...] = ()
    issues: tuple[IssueRecord, ...] = ()
    upsert_1min: bool = False


@dataclass(frozen=True)
class Coverage:
    observed_points: int
    expected_points: int
    coverage_ratio: float

    @property
    def missing_points(self) -> int:
        return max(self.expected_points - self.observed_points, 0)


@dataclass(frozen=True)
class PromotionDecision:
    allowed: bool
    block_reasons: tuple[PromotionBlockReason, ...] = ()


@dataclass(frozen=True)
class GzipCleanupPlan:
    """Side-effect-free cleanup gate for local gzip archive candidates.

    The plan never deletes files. It only states which candidate paths would be
    eligible for a later approved cleanup executor. Dry-run mode and missing
    approval must both produce an empty executable target set.
    """

    candidate_paths: tuple[str, ...]
    executable_delete_targets: tuple[str, ...]
    dry_run: bool
    approval_id: str | None = None
    source_authority: str = SOURCE_AUTHORITY_PC1_ARCHIVE
    block_reasons: tuple[str, ...] = ()


def is_observed_value(value: float | None) -> bool:
    """Return True for observed values; NULL/NaN are missing and 0 is observed."""

    return value is not None and not (isinstance(value, float) and math.isnan(value))


def classify_source_mode(source_layer: str) -> SourceMode:
    """Classify a row source into reference/backfill or live/canonical observed mode."""

    normalized = source_layer.strip().lower()
    if not normalized:
        raise ValueError("source_layer is required")
    if any(marker in normalized for marker in REFERENCE_SOURCE_LAYERS):
        return SOURCE_MODE_REFERENCE_BACKFILL
    observed_layers = {layer.lower() for layer in OBSERVED_SOURCE_LAYERS}
    if normalized in observed_layers or normalized.startswith("live.") or normalized.startswith("canonical."):
        return SOURCE_MODE_LIVE_OBSERVED
    raise ValueError(f"unknown source_layer: {source_layer}")


def is_live_observed_source(source_layer: str, source_mode: str | None = None) -> bool:
    """Return True only for live/canonical observed rows, never reference/backfill rows."""

    try:
        inferred = classify_source_mode(source_layer)
    except ValueError:
        return False
    if source_mode is not None and source_mode != inferred:
        return False
    return inferred == SOURCE_MODE_LIVE_OBSERVED


def require_live_observed_source(source_layer: str, source_mode: str | None = None) -> None:
    """Raise when a live-serving/canonical candidate row comes from reference data."""

    if not is_live_observed_source(source_layer, source_mode):
        observed_mode = source_mode if source_mode is not None else classify_source_mode(source_layer)
        raise ValueError(f"live serving requires live_observed source_layer, got {source_layer}/{observed_mode}")


def validate_live_injector_source_authority(source_root: str | Path, source_authority: str = SOURCE_AUTHORITY_PC1_ARCHIVE) -> None:
    """Require the live injector to use the PC1 gzip archive authority only.

    The local live pipeline may run on PC1~3 hosts, but its source-of-truth gzip
    root remains the PC1 archive lane. Reference/backfill/canonical/mart paths
    are explicitly rejected as injector roots so comparison data cannot masquerade
    as live-observed input.
    """

    if source_authority != SOURCE_AUTHORITY_PC1_ARCHIVE:
        raise ValueError(f"live injector source authority must be {SOURCE_AUTHORITY_PC1_ARCHIVE}")
    normalized_parts = tuple(part.lower() for part in Path(source_root).expanduser().parts)
    for part in normalized_parts:
        if any(marker == part or marker in part for marker in LIVE_INJECTOR_FORBIDDEN_SOURCE_ROOT_MARKERS):
            raise ValueError(f"live injector source root is outside the PC1 archive authority: {source_root}")


def plan_gzip_cleanup(
    candidate_paths: Iterable[str | Path],
    *,
    dry_run: bool = True,
    approval_id: str | None = None,
    source_authority: str = SOURCE_AUTHORITY_PC1_ARCHIVE,
) -> GzipCleanupPlan:
    """Build a non-destructive gzip cleanup plan behind dry-run and approval gates."""

    candidates = tuple(Path(path).as_posix() for path in candidate_paths)
    block_reasons: list[str] = []
    if source_authority != SOURCE_AUTHORITY_PC1_ARCHIVE:
        block_reasons.append("pc1_source_authority_required")
    if dry_run:
        block_reasons.append("dry_run_only")
    if not approval_id:
        block_reasons.append("approval_required")
    executable_targets = candidates if not block_reasons else ()
    return GzipCleanupPlan(
        candidate_paths=candidates,
        executable_delete_targets=executable_targets,
        dry_run=dry_run,
        approval_id=approval_id,
        source_authority=source_authority,
        block_reasons=tuple(block_reasons),
    )


def count_observed_points(values: Iterable[float | None]) -> int:
    return sum(1 for value in values if is_observed_value(value))


def compute_coverage(observed_points: int, expected_points: int) -> Coverage:
    """Compute bounded coverage_ratio = observed_points / expected_points.

    Negative observed counts and zero/negative expected counts are invalid because
    they hide bad QA arithmetic. Ratios above 1 are clamped to 1 for contract
    safety while preserving the observed count for issue logging.
    """

    if observed_points < 0:
        raise ValueError("observed_points must be non-negative")
    if expected_points <= 0:
        raise ValueError("expected_points must be positive")
    ratio = max(0.0, min(1.0, observed_points / expected_points))
    return Coverage(observed_points=observed_points, expected_points=expected_points, coverage_ratio=ratio)


def _explicit_expected_points(policy: ExpectedPointsPolicy, resolution: Resolution) -> int | None:
    if resolution == RESOLUTION_15MIN:
        return policy.expected_points_15min
    if resolution == RESOLUTION_1H:
        return policy.expected_points_1h
    raise ValueError(f"expected-points policy is unsupported for resolution: {resolution}")


def derive_expected_points(policy: ExpectedPointsPolicy, resolution: Resolution) -> int:
    """Derive expected observed points for a live 15min/1h mean rollup bucket."""

    explicit_expected_points = _explicit_expected_points(policy, resolution)
    if explicit_expected_points is not None:
        if explicit_expected_points <= 0:
            raise ValueError("explicit expected points must be positive")
        return explicit_expected_points

    if policy.native_cadence_seconds <= 0:
        raise ValueError("native cadence seconds must be positive")
    if policy.native_cadence_seconds < 60 and not policy.sub_minute_policy_approved:
        raise ValueError("sub-minute cadence requires explicit expected points or approved policy")

    bucket_seconds_by_resolution = {
        RESOLUTION_15MIN: 15 * 60,
        RESOLUTION_1H: 60 * 60,
    }
    bucket_seconds = bucket_seconds_by_resolution[resolution]
    return max(1, bucket_seconds // policy.native_cadence_seconds)


def live_mean_rollup_output_contract(resolution: Resolution) -> LiveRollupOutputContract:
    """Return the live rollup output contract for worker-owned mean rollups."""

    table_by_resolution = {
        RESOLUTION_15MIN: LIVE_MEASUREMENT_15MIN,
        RESOLUTION_1H: LIVE_MEASUREMENT_1H,
    }
    if resolution not in table_by_resolution:
        raise ValueError(f"live rollup output is unsupported for resolution: {resolution}")
    return LiveRollupOutputContract(table=table_by_resolution[resolution], resolution=resolution)


def _policy_lookup_issue(event: LiveMeasurementEvent, lookup: PolicyLookupResult | None) -> IssueRecord:
    if lookup is not None and lookup.status == "ambiguous":
        reason = lookup.reason or f"ambiguous effective live.measurement_policy versions: {lookup.matched_policy_versions}"
        return IssueRecord("policy_ambiguous", event.meter_urn, event.measurement, event.event_id, reason)
    reason = lookup.reason if lookup is not None and lookup.reason else "missing live.measurement_policy"
    return IssueRecord("policy_miss", event.meter_urn, event.measurement, event.event_id, reason)


def resolve_policy_lookup(
    event: LiveMeasurementEvent,
    policy_lookup: LiveMeasurementPolicy | PolicyLookupResult | None,
) -> tuple[LiveMeasurementPolicy | None, IssueRecord | None]:
    """Normalize trigger policy input into either a policy or a lookup issue."""

    if policy_lookup is None:
        return None, _policy_lookup_issue(event, None)
    if isinstance(policy_lookup, LiveMeasurementPolicy):
        return policy_lookup, None
    if policy_lookup.status == "found":
        if policy_lookup.policy is None:
            raise ValueError("found policy lookup requires a policy")
        return policy_lookup.policy, None
    return None, _policy_lookup_issue(event, policy_lookup)


def validate_queue_key(key: BucketQueueKey) -> None:
    if not key.meter_urn:
        raise ValueError("meter_urn is required")
    if not key.measurement:
        raise ValueError("measurement is required")
    if key.policy_version < 1:
        raise ValueError("policy_version must be positive")
    if (key.job_kind, key.resolution) not in ALLOWED_QUEUE_JOB_SPECS:
        raise ValueError(f"unsupported bucket queue job: {key.job_kind}/{key.resolution}")


def floor_to_resolution(timestamp: datetime, resolution: Resolution) -> datetime:
    """Floor an event timestamp to the worker bucket timestamp."""

    if resolution == RESOLUTION_1MIN:
        return timestamp.replace(second=0, microsecond=0)
    if resolution == RESOLUTION_15MIN:
        minute = (timestamp.minute // 15) * 15
        return timestamp.replace(minute=minute, second=0, microsecond=0)
    if resolution == RESOLUTION_1H:
        return timestamp.replace(minute=0, second=0, microsecond=0)
    raise ValueError(f"unsupported resolution: {resolution}")


def trigger_policy_block_reasons(policy: LiveMeasurementPolicy) -> tuple[PromotionBlockReason, ...]:
    reasons: list[PromotionBlockReason] = []
    if policy.heterogeneous_native_cadence:
        reasons.append("heterogeneous_native_cadence")
    if policy.policy_kind == "state_hold_last" and not policy.state_hold_last_evidence:
        reasons.append("state_hold_last_without_evidence")
    if policy.policy_kind == "cumulative":
        reasons.append("cumulative_policy_blocked")
    if policy.policy_kind == "unknown":
        reasons.append("unknown_policy_blocked")
    if policy.policy_kind == "circular":
        reasons.append("circular_policy_blocked")
    return tuple(reasons)


def derive_trigger_queue_jobs(event: LiveMeasurementEvent, policy: LiveMeasurementPolicy) -> tuple[BucketQueueJob, ...]:
    """Return the exact dirty-bucket jobs the trigger may enqueue for one event."""

    return tuple(
        BucketQueueJob(
            BucketQueueKey(
                meter_urn=event.meter_urn,
                measurement=event.measurement,
                resolution=resolution,
                bucket_ts=floor_to_resolution(event.source_ts, resolution),
                job_kind=job_kind,
                policy_version=policy.policy_version,
            )
        )
        for job_kind, resolution in ALLOWED_QUEUE_JOB_SPECS
    )


def decide_trigger_actions(event: LiveMeasurementEvent, policy_lookup: LiveMeasurementPolicy | PolicyLookupResult | None) -> TriggerDecisionResult:
    """Pure trigger skeleton for an inserted ``live.measurement_event`` row.

    Allowed trigger work is limited to policy lookup, 1-minute upsert, bucket
    queue enqueue, and issue logging. Workers handle 15min/1h rollups, peak
    features, QA eligibility, and controlled promotion.
    """

    operations: list[TriggerOperation] = [TriggerOperation("policy_lookup", LIVE_MEASUREMENT_POLICY)]
    policy, lookup_issue = resolve_policy_lookup(event, policy_lookup)
    if lookup_issue is not None:
        operations.append(TriggerOperation("issue_log", QA_LIVE_MEASUREMENT_ISSUE))
        return TriggerDecisionResult(operations=tuple(operations), issues=(lookup_issue,))

    if policy is None:
        raise ValueError("policy lookup resolved without policy or issue")

    block_reasons = trigger_policy_block_reasons(policy)
    if not policy.enabled or block_reasons:
        reason = "policy_disabled" if not policy.enabled else ",".join(block_reasons)
        issue = IssueRecord("policy_block", event.meter_urn, event.measurement, event.event_id, reason)
        operations.append(TriggerOperation("issue_log", QA_LIVE_MEASUREMENT_ISSUE))
        return TriggerDecisionResult(operations=tuple(operations), issues=(issue,))

    queue_jobs = derive_trigger_queue_jobs(event, policy)
    operations.extend(
        (
            TriggerOperation("measurement_1min_upsert", LIVE_MEASUREMENT_1MIN),
            TriggerOperation("bucket_queue_enqueue", LIVE_BUCKET_QUEUE),
        )
    )
    return TriggerDecisionResult(operations=tuple(operations), queue_jobs=queue_jobs, upsert_1min=True)


def assert_trigger_contract(result: TriggerDecisionResult) -> None:
    """Raise if a trigger result attempts worker, mart, or canonical work."""

    forbidden_targets = [operation.target for operation in result.operations if operation.target in FORBIDDEN_TRIGGER_TARGETS]
    if forbidden_targets:
        raise ValueError(f"trigger produced forbidden targets: {forbidden_targets}")


def guard_peak_feature_promotion(source_table: str, target_table: str) -> PromotionDecision:
    """Block peak prediction rows from canonical promotion.

    Peak rows are mart-only features (including rolling 1h peak inputs) and must
    never become canonical ``measurement_15min``/``measurement_1h`` values.
    """

    reasons: list[PromotionBlockReason] = []
    if source_table in MART_PEAK_TABLES:
        reasons.append("peak_feature_never_canonical")
    if target_table in CANONICAL_TABLES and source_table in MART_PEAK_TABLES:
        reasons.append("peak_leakage_block")
    return PromotionDecision(allowed=not reasons, block_reasons=tuple(reasons))


__all__ = [
    "ALLOWED_QUEUE_JOB_SPECS",
    "ALLOWED_TRIGGER_TARGETS",
    "BucketQueueJob",
    "BucketQueueKey",
    "CANONICAL_TABLES",
    "Coverage",
    "EVENT_IDEMPOTENCY_CANDIDATE_FIELDS",
    "EVENT_LEDGER_MUTABILITY",
    "ExpectedPointsPolicy",
    "FORBIDDEN_TRIGGER_TARGETS",
    "GzipCleanupPlan",
    "IssueRecord",
    "JOB_KIND_MEAN_ROLLUP",
    "JOB_KIND_PEAK_FEATURE",
    "LIVE_BUCKET_QUEUE",
    "LIVE_MEASUREMENT_1H",
    "LIVE_MEASUREMENT_1MIN",
    "LIVE_MEASUREMENT_15MIN",
    "LIVE_MEASUREMENT_EVENT",
    "LIVE_MEASUREMENT_POLICY",
    "LIVE_PROMOTION_CHECK",
    "LIVE_ROLLUP_VALUE_SEMANTIC",
    "LiveMeasurementEvent",
    "LiveMeasurementPolicy",
    "LiveRollupOutputContract",
    "MART_PEAK_FEATURE_15MIN",
    "MART_PEAK_INPUT_15MIN",
    "PEAK_BRANCH_TABLES",
    "PolicyLookupResult",
    "PolicyLookupStatus",
    "QA_LIVE_MEASUREMENT_ISSUE",
    "QUEUE_IDEMPOTENCY_FIELDS",
    "RESOLUTION_15MIN",
    "RESOLUTION_1H",
    "RESOLUTION_1MIN",
    "PromotionDecision",
    "SOURCE_AUTHORITY_PC1_ARCHIVE",
    "SOURCE_LAYER_KAFKA_RAW",
    "SOURCE_LAYER_REFERENCE_CORRECTED_RESAMPLED",
    "SOURCE_MODE_HYBRID_WARM_START",
    "SOURCE_MODE_LIVE_OBSERVED",
    "SOURCE_MODE_REFERENCE_BACKFILL",
    "TriggerDecisionResult",
    "TriggerOperation",
    "assert_trigger_contract",
    "compute_coverage",
    "count_observed_points",
    "decide_trigger_actions",
    "derive_expected_points",
    "derive_trigger_queue_jobs",
    "floor_to_resolution",
    "guard_peak_feature_promotion",
    "is_observed_value",
    "live_mean_rollup_output_contract",
    "plan_gzip_cleanup",
    "resolve_policy_lookup",
    "trigger_policy_block_reasons",
    "validate_live_injector_source_authority",
    "validate_queue_key",
]
