"""Peak-oriented 15-minute feature aggregation from 1-minute samples.

This module is pure and import-safe. It does not open files, import database
clients, or write to external systems. Historical loaders can use it to build
`mart.peak_feature_15min` rows from archived 1-minute corrected/resampled CSVs.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PeakSample:
    """One 1-minute value for a single meter/measurement series."""

    timestamp: datetime
    value: float | None


@dataclass(frozen=True)
class PeakFeatureRow:
    """One 15-minute peak feature row for `mart.peak_feature_15min`."""

    window_ts: datetime
    meter_urn: str
    measurement: str
    mean_value: float
    max_value: float
    min_value: float
    p95_value: float
    p99_value: float
    std_value: float
    last_value: float
    peak_ts: datetime
    peak_value: float
    observed_points: int
    expected_points: int
    coverage_ratio: float
    source_file: str
    run_id: str


def floor_to_window(timestamp: datetime, *, minutes: int = 15) -> datetime:
    """Floor a timestamp to a minute-aligned fixed-width window."""

    floored_minute = (timestamp.minute // minutes) * minutes
    return timestamp.replace(minute=floored_minute, second=0, microsecond=0)


def is_observed(value: float | None) -> bool:
    return value is not None and not math.isnan(value)


def nearest_rank_percentile(values: list[float], percentile: float) -> float:
    """Return nearest-rank percentile for a non-empty value list."""

    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    rank = math.ceil((percentile / 100.0) * len(ordered))
    index = min(max(rank - 1, 0), len(ordered) - 1)
    return ordered[index]


def aggregate_peak_features(
    samples: Iterable[PeakSample],
    *,
    meter_urn: str,
    measurement: str,
    source_file: str,
    run_id: str,
    window_minutes: int = 15,
    expected_points: int = 15,
) -> list[PeakFeatureRow]:
    """Aggregate 1-minute samples into 15-minute peak feature rows.

    Null and NaN values are ignored for statistics and coverage counts.
    `last_value` is the last observed value by timestamp within the window.
    Ties for `peak_ts` keep the first timestamp with the maximum value.
    """

    buckets: dict[datetime, list[PeakSample]] = defaultdict(list)
    for sample in samples:
        if is_observed(sample.value):
            buckets[floor_to_window(sample.timestamp, minutes=window_minutes)].append(sample)

    rows: list[PeakFeatureRow] = []
    for window_ts in sorted(buckets):
        window_samples = sorted(buckets[window_ts], key=lambda sample: sample.timestamp)
        values = [float(sample.value) for sample in window_samples if sample.value is not None]
        max_value = max(values)
        peak_sample = next(sample for sample in window_samples if sample.value == max_value)
        observed_points = len(values)
        rows.append(
            PeakFeatureRow(
                window_ts=window_ts,
                meter_urn=meter_urn,
                measurement=measurement,
                mean_value=statistics.fmean(values),
                max_value=max_value,
                min_value=min(values),
                p95_value=nearest_rank_percentile(values, 95.0),
                p99_value=nearest_rank_percentile(values, 99.0),
                std_value=statistics.pstdev(values) if len(values) > 1 else 0.0,
                last_value=values[-1],
                peak_ts=peak_sample.timestamp,
                peak_value=max_value,
                observed_points=observed_points,
                expected_points=expected_points,
                coverage_ratio=observed_points / expected_points,
                source_file=source_file,
                run_id=run_id,
            )
        )
    return rows
