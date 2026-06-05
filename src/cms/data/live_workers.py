"""Pure worker skeletons for the CMS live measurement pipeline.

The functions in this module are import-safe and side-effect-free. They do not
open database connections, read files, call cloud APIs, execute DDL/DML, or write
canonical data. They model worker contracts for local/scratch tests.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, cast

from cms.contracts.live_pipeline import (
    CANONICAL_TABLES,
    LIVE_MEASUREMENT_1H,
    LIVE_MEASUREMENT_15MIN,
    LIVE_ROLLUP_VALUE_SEMANTIC,
    MART_PEAK_FEATURE_15MIN,
    MART_PEAK_INPUT_15MIN,
    RESOLUTION_15MIN,
    Coverage,
    ExpectedPointsPolicy,
    LiveMeasurementEvent,
    PromotionDecision,
    Resolution,
    compute_coverage,
    count_observed_points,
    derive_expected_points,
    floor_to_resolution,
    guard_peak_feature_promotion,
    is_observed_value,
    live_mean_rollup_output_contract,
)

StageName = Literal[
    "kafka_to_event",
    "event_to_1min",
    "1min_to_15min",
    "1min_to_1h",
    "1min_to_peak_feature",
    "peak_feature_to_peak_input",
    "qa_eligibility",
    "promotion_ready",
    "end_to_end",
]


@dataclass(frozen=True)
class BufferedEventRecord:
    """Kafka raw-event record after parsing only the fields needed for local contracts."""

    event_id: str
    meter_urn: str
    measurement: str
    source_ts: datetime
    value: float | None
    source_system: str = "kafka.measurement_raw_v1"
    source_event_id: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MeanRollupRecord:
    """Worker-owned mean observed rollup record for live 15min/1h tables."""

    table: str
    bucket_ts: datetime
    resolution: Resolution
    meter_urn: str
    measurement: str
    value: float | None
    expected_points: int
    observed_points: int
    gap_points: int
    coverage_ratio: float
    aggregation_policy: str = LIVE_ROLLUP_VALUE_SEMANTIC
    source_event_ids: tuple[str, ...] = ()
    quality_code: str = "observed_mean"

    def __post_init__(self) -> None:
        live_mean_rollup_output_contract(self.resolution)
        if self.table not in {LIVE_MEASUREMENT_15MIN, LIVE_MEASUREMENT_1H}:
            raise ValueError(f"mean rollup must target live rollup table, got {self.table}")
        if self.aggregation_policy != LIVE_ROLLUP_VALUE_SEMANTIC:
            raise ValueError("mean rollup value must be mean_observed_only")


@dataclass(frozen=True)
class PeakFeatureRecord:
    """Mart-only 15min peak feature record."""

    table: str
    bucket_ts: datetime
    meter_urn: str
    measurement: str
    peak_value: float | None
    peak_ts: datetime | None
    max_value: float | None
    min_value: float | None
    mean_value: float | None
    std_value: float | None
    coverage_ratio: float
    valid_peak_window: bool
    source_event_ids: tuple[str, ...] = ()
    feature_version: str = "draft"

    def __post_init__(self) -> None:
        if self.table != MART_PEAK_FEATURE_15MIN:
            raise ValueError("peak feature records must stay in mart.peak_feature_15min")


@dataclass(frozen=True)
class PeakInputRecord:
    """Mart-only 15min model input with rolling 1h peak fields."""

    table: str
    bucket_ts: datetime
    meter_urn: str
    measurement: str
    rolling_1h_peak_value: float | None
    rolling_1h_peak_ts: datetime | None
    rolling_1h_mean_value: float | None
    rolling_1h_valid_bucket_count: int
    rolling_1h_coverage_ratio: float
    feature_version: str = "draft"

    def __post_init__(self) -> None:
        if self.table != MART_PEAK_INPUT_15MIN:
            raise ValueError("peak input records must stay in mart.peak_input_15min")
        if self.rolling_1h_valid_bucket_count < 0:
            raise ValueError("rolling valid bucket count must be non-negative")


@dataclass(frozen=True)
class EligibilityDecision:
    allowed: bool
    block_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class PromotionReadyResult:
    ready: bool
    target_table: str
    source_table: str
    approval_id: str | None = None
    promotion_id: str | None = None
    block_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class LatencyRecord:
    stage: StageName
    duration_sec: float
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.duration_sec < 0:
            raise ValueError("duration_sec must be non-negative")


class StageTimer:
    """Small context manager for later latency instrumentation."""

    def __init__(self, stage: StageName, **metadata: Any) -> None:
        self.stage = stage
        self.metadata = metadata
        self._start: float | None = None
        self.record: LatencyRecord | None = None

    def __enter__(self) -> StageTimer:
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._start is None:
            raise RuntimeError("StageTimer exited before start")
        self.record = LatencyRecord(cast(StageName, self.stage), time.perf_counter() - self._start, self.metadata)


def _parse_source_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError("source_ts must be datetime or ISO datetime string")


def _parse_numeric(value: Any) -> float | None:
    if value is None:
        return None
    parsed = float(value)
    if math.isnan(parsed):
        return None
    return parsed


def transform_buffered_event(raw: Mapping[str, Any]) -> LiveMeasurementEvent:
    """Map a raw-buffer record into the import-safe live event contract."""

    event_id = str(raw.get("event_id") or raw.get("source_event_id") or raw.get("kafka_key") or "")
    meter_urn = str(raw.get("meter_urn") or "")
    measurement = str(raw.get("measurement") or "")
    if not event_id or not meter_urn or not measurement:
        raise ValueError("event_id, meter_urn, and measurement are required")

    source_ts = _parse_source_ts(raw.get("source_ts") or raw.get("event_ts"))
    value = _parse_numeric(raw.get("value_numeric", raw.get("value")))
    return LiveMeasurementEvent(
        event_id=event_id,
        meter_urn=meter_urn,
        measurement=measurement,
        source_ts=source_ts,
        value=value,
        source_system=str(raw.get("source_system") or "kafka.measurement_raw_v1"),
        source_event_id=raw.get("source_event_id"),
        payload=dict(raw),
    )


def _observed_floats(values: Iterable[float | None]) -> list[float]:
    return [float(value) for value in values if value is not None and is_observed_value(value)]


def build_mean_rollup(
    *,
    bucket_ts: datetime,
    resolution: Resolution,
    meter_urn: str,
    measurement: str,
    values: Sequence[float | None],
    expected_policy: ExpectedPointsPolicy,
    source_event_ids: Sequence[str] = (),
) -> MeanRollupRecord:
    """Build a live mean observed rollup without peak fields."""

    contract = live_mean_rollup_output_contract(resolution)
    expected_points = derive_expected_points(expected_policy, resolution)
    observed_points = count_observed_points(values)
    coverage = compute_coverage(observed_points, expected_points)
    observed_values = _observed_floats(values)
    mean_value = sum(observed_values) / len(observed_values) if observed_values else None
    gap_points = max(expected_points - observed_points, 0)
    quality_code = "observed_mean" if observed_values else "null_observation"
    return MeanRollupRecord(
        table=contract.table,
        bucket_ts=floor_to_resolution(bucket_ts, resolution),
        resolution=resolution,
        meter_urn=meter_urn,
        measurement=measurement,
        value=mean_value,
        expected_points=expected_points,
        observed_points=observed_points,
        gap_points=gap_points,
        coverage_ratio=coverage.coverage_ratio,
        source_event_ids=tuple(source_event_ids),
        quality_code=quality_code,
    )


def build_peak_feature(
    *,
    bucket_ts: datetime,
    meter_urn: str,
    measurement: str,
    observations: Sequence[tuple[datetime, float | None, str]],
    expected_points: int,
    min_coverage_ratio: float = 0.0,
    feature_version: str = "draft",
) -> PeakFeatureRecord:
    """Build a mart-only 15min peak feature from observed values."""

    observed = [(ts, float(value), event_id) for ts, value, event_id in observations if value is not None and is_observed_value(value)]
    coverage = compute_coverage(len(observed), expected_points)
    if not observed:
        return PeakFeatureRecord(
            table=MART_PEAK_FEATURE_15MIN,
            bucket_ts=floor_to_resolution(bucket_ts, RESOLUTION_15MIN),
            meter_urn=meter_urn,
            measurement=measurement,
            peak_value=None,
            peak_ts=None,
            max_value=None,
            min_value=None,
            mean_value=None,
            std_value=None,
            coverage_ratio=coverage.coverage_ratio,
            valid_peak_window=False,
            feature_version=feature_version,
        )

    values = [value for _, value, _ in observed]
    peak_ts, peak_value, _ = max(observed, key=lambda item: item[1])
    mean_value = sum(values) / len(values)
    variance = sum((value - mean_value) ** 2 for value in values) / len(values)
    return PeakFeatureRecord(
        table=MART_PEAK_FEATURE_15MIN,
        bucket_ts=floor_to_resolution(bucket_ts, RESOLUTION_15MIN),
        meter_urn=meter_urn,
        measurement=measurement,
        peak_value=peak_value,
        peak_ts=peak_ts,
        max_value=max(values),
        min_value=min(values),
        mean_value=mean_value,
        std_value=math.sqrt(variance),
        coverage_ratio=coverage.coverage_ratio,
        valid_peak_window=coverage.coverage_ratio >= min_coverage_ratio,
        source_event_ids=tuple(event_id for _, _, event_id in observed),
        feature_version=feature_version,
    )


def build_peak_input(features: Sequence[PeakFeatureRecord], *, feature_version: str = "draft") -> PeakInputRecord:
    """Build a mart-only rolling 1h peak input from up to four 15min features."""

    if not features:
        raise ValueError("at least one peak feature is required")
    window = tuple(features[-4:])
    first = window[-1]
    valid = [feature for feature in window if feature.valid_peak_window and feature.peak_value is not None]
    peak = max(valid, key=lambda feature: cast(float, feature.peak_value)) if valid else None
    mean_values = [feature.mean_value for feature in valid if feature.mean_value is not None]
    return PeakInputRecord(
        table=MART_PEAK_INPUT_15MIN,
        bucket_ts=first.bucket_ts,
        meter_urn=first.meter_urn,
        measurement=first.measurement,
        rolling_1h_peak_value=peak.peak_value if peak else None,
        rolling_1h_peak_ts=peak.peak_ts if peak else None,
        rolling_1h_mean_value=sum(mean_values) / len(mean_values) if mean_values else None,
        rolling_1h_valid_bucket_count=len(valid),
        rolling_1h_coverage_ratio=sum(feature.coverage_ratio for feature in window) / len(window),
        feature_version=feature_version,
    )


def evaluate_qa_eligibility(
    *,
    source_table: str,
    target_table: str,
    coverage: Coverage,
    coverage_threshold: float | None = None,
    policy_block_reasons: Iterable[str] = (),
    issue_kinds: Iterable[str] = (),
    lineage_present: bool = True,
) -> EligibilityDecision:
    """Evaluate row-level eligibility without writing promotion state."""

    block_reasons: list[str] = []
    block_reasons.extend(policy_block_reasons)
    block_reasons.extend(f"issue:{issue_kind}" for issue_kind in issue_kinds)
    if not lineage_present:
        block_reasons.append("lineage_missing")
    if not 0 <= coverage.coverage_ratio <= 1:
        block_reasons.append("coverage_out_of_bounds")
    if coverage_threshold is not None and coverage.coverage_ratio < coverage_threshold:
        block_reasons.append("coverage_below_threshold")
    peak_guard = guard_peak_feature_promotion(source_table, target_table)
    block_reasons.extend(peak_guard.block_reasons)
    return EligibilityDecision(allowed=not block_reasons, block_reasons=tuple(block_reasons))


def prepare_promotion(
    *,
    source_table: str,
    target_table: str,
    approval_id: str | None,
    promotion_id: str | None,
) -> PromotionReadyResult:
    """Return a promotion-ready marker only when approval and target guards pass."""

    block_reasons: list[str] = []
    if not approval_id:
        block_reasons.append("approval_required")
    if not promotion_id:
        block_reasons.append("promotion_id_required")
    if target_table not in CANONICAL_TABLES:
        block_reasons.append("canonical_target_required")
    peak_guard: PromotionDecision = guard_peak_feature_promotion(source_table, target_table)
    block_reasons.extend(peak_guard.block_reasons)
    return PromotionReadyResult(
        ready=not block_reasons,
        target_table=target_table,
        source_table=source_table,
        approval_id=approval_id,
        promotion_id=promotion_id,
        block_reasons=tuple(block_reasons),
    )


__all__ = [
    "BufferedEventRecord",
    "EligibilityDecision",
    "LatencyRecord",
    "MeanRollupRecord",
    "PeakFeatureRecord",
    "PeakInputRecord",
    "PromotionReadyResult",
    "StageName",
    "StageTimer",
    "build_mean_rollup",
    "build_peak_feature",
    "build_peak_input",
    "evaluate_qa_eligibility",
    "prepare_promotion",
    "transform_buffered_event",
]
