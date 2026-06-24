"""Pure expected-bucket generation for live missing-observation evidence.

This module is deliberately side-effect free: it does not read environment,
open database/client connections, or perform runtime writes.  It only turns an
aware time interval plus cadence policy into expected bucket descriptors.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

CADENCE_NATIVE_1MIN = "native_1min"
CADENCE_NATIVE_SUBMINUTE = "native_subminute"
CADENCE_NATIVE_5MIN = "native_5min"
CADENCE_SPARSE_EVENT = "sparse_event"
CADENCE_STATE_HOLD_LAST = "state_hold_last"
CADENCE_STRUCTURAL_NOT_EXPECTED = "structural_not_expected"

STRUCTURAL_NOT_EXPECTED_CADENCES = frozenset(
    {
        CADENCE_SPARSE_EVENT,
        CADENCE_STATE_HOLD_LAST,
        "state_hold",
        CADENCE_STRUCTURAL_NOT_EXPECTED,
    }
)


@dataclass(frozen=True)
class ExpectedBucket:
    """One bucket that is expected to have observations for a series."""

    meter_urn: str
    measurement: str
    bucket_ts: datetime
    expected_points: int
    cadence_class: str


def generate_expected_buckets(
    *,
    meter_urn: str,
    measurement: str,
    start_ts: datetime,
    end_ts: datetime,
    source_native_interval_seconds: int,
    target_grain_seconds: int,
    timezone_policy: str | None = None,
    cadence_class: str | None = None,
) -> tuple[ExpectedBucket, ...]:
    """Generate expected buckets in the half-open interval ``[start_ts, end_ts)``.

    Bucket timestamps are aligned to ``target_grain_seconds``.  If ``start_ts`` is
    not itself aligned, generation starts at the next aligned boundary inside the
    half-open interval.  Structural/non-periodic cadences such as sparse events
    and state-hold series intentionally return no buckets instead of inventing
    data-loss gaps.
    """

    _validate_series_identity(meter_urn=meter_urn, measurement=measurement)
    _validate_aware_interval(start_ts=start_ts, end_ts=end_ts)
    _validate_positive_seconds(source_native_interval_seconds, name="source_native_interval_seconds")
    _validate_positive_seconds(target_grain_seconds, name="target_grain_seconds")

    resolved_cadence = cadence_class or infer_cadence_class(source_native_interval_seconds)
    if resolved_cadence in STRUCTURAL_NOT_EXPECTED_CADENCES:
        return ()
    if resolved_cadence == CADENCE_NATIVE_5MIN and target_grain_seconds < source_native_interval_seconds:
        return ()

    expected_points = expected_points_for_bucket(
        source_native_interval_seconds=source_native_interval_seconds,
        target_grain_seconds=target_grain_seconds,
        cadence_class=resolved_cadence,
    )
    first_bucket_ts = align_start_to_grain(start_ts, target_grain_seconds)

    buckets: list[ExpectedBucket] = []
    bucket_ts = first_bucket_ts
    step = timedelta(seconds=target_grain_seconds)
    while bucket_ts < end_ts:
        buckets.append(
            ExpectedBucket(
                meter_urn=meter_urn,
                measurement=measurement,
                bucket_ts=bucket_ts,
                expected_points=expected_points,
                cadence_class=resolved_cadence,
            )
        )
        bucket_ts += step
    return tuple(buckets)


def infer_cadence_class(source_native_interval_seconds: int) -> str:
    """Infer the periodic cadence class from native interval seconds."""

    _validate_positive_seconds(source_native_interval_seconds, name="source_native_interval_seconds")
    if source_native_interval_seconds < 60:
        return CADENCE_NATIVE_SUBMINUTE
    if source_native_interval_seconds == 60:
        return CADENCE_NATIVE_1MIN
    if source_native_interval_seconds == 300:
        return CADENCE_NATIVE_5MIN
    return f"native_{source_native_interval_seconds}s"


def expected_points_for_bucket(
    *,
    source_native_interval_seconds: int,
    target_grain_seconds: int,
    cadence_class: str | None = None,
) -> int:
    """Return expected source points per target bucket.

    Frozen live-quality examples:
    * native_1min at 15min target -> 15
    * native_subminute 10s at 1min target -> 6
    * native_5min at 15min target -> 3
    """

    _validate_positive_seconds(source_native_interval_seconds, name="source_native_interval_seconds")
    _validate_positive_seconds(target_grain_seconds, name="target_grain_seconds")
    resolved_cadence = cadence_class or infer_cadence_class(source_native_interval_seconds)
    if resolved_cadence in STRUCTURAL_NOT_EXPECTED_CADENCES:
        raise ValueError("structural/not-expected cadence has no expected_points")
    if target_grain_seconds < source_native_interval_seconds:
        raise ValueError("target_grain_seconds must be >= source_native_interval_seconds for periodic cadences")
    if target_grain_seconds % source_native_interval_seconds != 0:
        raise ValueError("target_grain_seconds must be divisible by source_native_interval_seconds")
    return target_grain_seconds // source_native_interval_seconds


def align_start_to_grain(ts: datetime, target_grain_seconds: int) -> datetime:
    """Return ``ts`` if aligned, otherwise the next aligned aware timestamp."""

    _validate_aware_datetime(ts, name="ts")
    _validate_positive_seconds(target_grain_seconds, name="target_grain_seconds")
    utc_ts = ts.astimezone(UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = utc_ts - epoch
    total_microseconds = ((delta.days * 24 * 60 * 60 + delta.seconds) * 1_000_000) + delta.microseconds
    grain_microseconds = target_grain_seconds * 1_000_000
    remainder = total_microseconds % grain_microseconds
    if remainder == 0:
        return ts
    return ts + timedelta(microseconds=grain_microseconds - remainder)


def expected_bucket_key(bucket: ExpectedBucket) -> tuple[str, str, datetime]:
    """Return the idempotency key used to compare expected/existing buckets."""

    return (bucket.meter_urn, bucket.measurement, bucket.bucket_ts)


def _validate_series_identity(*, meter_urn: str, measurement: str) -> None:
    if not meter_urn:
        raise ValueError("meter_urn must be non-empty")
    if not measurement:
        raise ValueError("measurement must be non-empty")


def _validate_aware_interval(*, start_ts: datetime, end_ts: datetime) -> None:
    _validate_aware_datetime(start_ts, name="start_ts")
    _validate_aware_datetime(end_ts, name="end_ts")
    if end_ts <= start_ts:
        raise ValueError("end_ts must be after start_ts")


def _validate_aware_datetime(value: datetime, *, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _validate_positive_seconds(value: int, *, name: str) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive")


__all__ = [
    "CADENCE_NATIVE_1MIN",
    "CADENCE_NATIVE_5MIN",
    "CADENCE_NATIVE_SUBMINUTE",
    "CADENCE_SPARSE_EVENT",
    "CADENCE_STATE_HOLD_LAST",
    "CADENCE_STRUCTURAL_NOT_EXPECTED",
    "ExpectedBucket",
    "STRUCTURAL_NOT_EXPECTED_CADENCES",
    "align_start_to_grain",
    "expected_bucket_key",
    "expected_points_for_bucket",
    "generate_expected_buckets",
    "infer_cadence_class",
]
