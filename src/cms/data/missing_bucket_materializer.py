"""Pure missing-observation row materializer.

The functions in this module only compare expected bucket descriptors with
caller-supplied existing keys.  They do not connect to databases, read runtime
configuration, or perform writes.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cms.data.expected_bucket import ExpectedBucket, expected_bucket_key

MISSING_OBSERVATION_MASK_CODE = "missing_observation"
NULL_OBSERVATION_QUALITY_CODE = "null_observation"
EXPECTED_BUCKET_PROVENANCE = "expected_bucket_materializer"

BucketKey = tuple[str, str, datetime]


@dataclass(frozen=True)
class MissingBucketRow:
    """Materialized row for an absent expected observation bucket."""

    meter_urn: str
    measurement: str
    bucket_ts: datetime
    expected_points: int
    cadence_class: str
    value: None = None
    observed_points: int = 0
    missing_points: int = 0
    coverage_ratio: float = 0.0
    mask_code: str = MISSING_OBSERVATION_MASK_CODE
    quality_code: str = NULL_OBSERVATION_QUALITY_CODE
    provenance: str = EXPECTED_BUCKET_PROVENANCE
    bad_row: bool = False

    @property
    def coverage(self) -> float:
        """Compatibility alias for contracts that name coverage without suffix."""

        return self.coverage_ratio

    @property
    def is_bad_row(self) -> bool:
        """Missing observations are explicitly not qa.bad_row records."""

        return self.bad_row

    def __post_init__(self) -> None:
        if self.expected_points <= 0:
            raise ValueError("expected_points must be positive")
        missing_points = self.expected_points if self.missing_points == 0 else self.missing_points
        object.__setattr__(self, "missing_points", missing_points)
        if self.observed_points != 0:
            raise ValueError("missing observation rows must have observed_points=0")
        if self.missing_points != self.expected_points:
            raise ValueError("missing_points must equal expected_points")
        if self.coverage_ratio != 0:
            raise ValueError("missing observation rows must have coverage_ratio=0")
        if self.value is not None:
            raise ValueError("missing observation rows must have value=None")
        if self.mask_code != MISSING_OBSERVATION_MASK_CODE:
            raise ValueError("missing observation rows must use missing_observation mask_code")
        if self.quality_code != NULL_OBSERVATION_QUALITY_CODE:
            raise ValueError("missing observation rows must use null_observation quality_code")
        if self.provenance != EXPECTED_BUCKET_PROVENANCE:
            raise ValueError("missing observation rows must use expected_bucket_materializer provenance")
        if self.bad_row is not False:
            raise ValueError("missing observations are not bad rows")


def materialize_missing_buckets(
    expected_buckets: Iterable[ExpectedBucket],
    existing_bucket_keys: Iterable[BucketKey | ExpectedBucket | Mapping[str, Any]],
) -> tuple[MissingBucketRow, ...]:
    """Create rows only for expected buckets absent from ``existing_bucket_keys``."""

    existing_keys = {_normalize_bucket_key(key) for key in existing_bucket_keys}
    rows: list[MissingBucketRow] = []
    for bucket in expected_buckets:
        if expected_bucket_key(bucket) in existing_keys:
            continue
        rows.append(missing_row_from_expected_bucket(bucket))
    return tuple(rows)


def materialize_missing_bucket_rows(
    expected_buckets: Iterable[ExpectedBucket],
    existing_bucket_keys: Iterable[BucketKey | ExpectedBucket | Mapping[str, Any]],
) -> tuple[MissingBucketRow, ...]:
    """Backward-compatible explicit alias for missing row materialization."""

    return materialize_missing_buckets(expected_buckets, existing_bucket_keys)


def missing_row_from_expected_bucket(bucket: ExpectedBucket) -> MissingBucketRow:
    """Convert one absent expected bucket into a missing-observation row."""

    return MissingBucketRow(
        meter_urn=bucket.meter_urn,
        measurement=bucket.measurement,
        bucket_ts=bucket.bucket_ts,
        expected_points=bucket.expected_points,
        cadence_class=bucket.cadence_class,
        value=None,
        observed_points=0,
        missing_points=bucket.expected_points,
        coverage_ratio=0.0,
        mask_code=MISSING_OBSERVATION_MASK_CODE,
        quality_code=NULL_OBSERVATION_QUALITY_CODE,
        provenance=EXPECTED_BUCKET_PROVENANCE,
        bad_row=False,
    )


def bucket_key(meter_urn: str, measurement: str, bucket_ts: datetime) -> BucketKey:
    """Build a normalized bucket key for existing-row comparisons."""

    return (meter_urn, measurement, bucket_ts)


def _normalize_bucket_key(key: BucketKey | ExpectedBucket | Mapping[str, Any]) -> BucketKey:
    if isinstance(key, ExpectedBucket):
        return expected_bucket_key(key)
    if isinstance(key, Mapping):
        bucket_ts = key.get("bucket_ts", key.get("timestamp", key.get("window_ts")))
        if bucket_ts is None:
            raise ValueError("mapping bucket key must include bucket_ts, timestamp, or window_ts")
        return (str(key["meter_urn"]), str(key["measurement"]), bucket_ts)
    meter_urn, measurement, bucket_ts = key
    return (meter_urn, measurement, bucket_ts)


__all__ = [
    "EXPECTED_BUCKET_PROVENANCE",
    "MISSING_OBSERVATION_MASK_CODE",
    "NULL_OBSERVATION_QUALITY_CODE",
    "BucketKey",
    "MissingBucketRow",
    "bucket_key",
    "materialize_missing_bucket_rows",
    "materialize_missing_buckets",
    "missing_row_from_expected_bucket",
]
