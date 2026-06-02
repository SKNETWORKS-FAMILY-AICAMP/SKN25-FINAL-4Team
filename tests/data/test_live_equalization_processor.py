import math
from datetime import datetime, timedelta

from cms.data.live_equalization_processor import (
    LiveHarmonizedEvent,
    SeriesCadencePolicy,
    downsample_mean_non_cumulative,
    equalize_to_1min,
    process_live_equalization,
)


def test_equalize_to_1min_keeps_missing_bucket_as_gap_with_zero_coverage():
    start = datetime(2024, 1, 1, 0, 0)
    rows = equalize_to_1min(
        (
            LiveHarmonizedEvent(
                meter_urn="urn:meter:1",
                measurement="active_power",
                timestamp=start,
                value=10.0,
                source_event_id="mongo-a",
            ),
            LiveHarmonizedEvent(
                meter_urn="urn:meter:1",
                measurement="active_power",
                timestamp=start + timedelta(minutes=2),
                value=20.0,
                source_event_id="mongo-b",
            ),
        ),
        start=start,
        end=start + timedelta(minutes=3),
    )

    assert len(rows) == 3
    assert rows[0].value == 10.0
    assert rows[0].quality == "observed"
    assert rows[0].expected_points == 1
    assert rows[0].observed_points == 1
    assert rows[0].gap_points == 0
    assert rows[0].coverage_ratio == 1.0
    assert rows[0].source_event_ids == ("mongo-a",)

    assert math.isnan(rows[1].value)
    assert rows[1].quality == "gap"
    assert rows[1].mask_code == "gap"
    assert rows[1].evidence_level == "in_memory_observed"
    assert rows[1].expected_points == 1
    assert rows[1].observed_points == 0
    assert rows[1].gap_points == 1
    assert rows[1].coverage_ratio == 0.0
    assert rows[1].source_event_ids == ()


def test_downsample_mean_non_cumulative_carries_coverage_and_quality_summary():
    start = datetime(2024, 1, 1, 0, 0)
    rows = equalize_to_1min(
        (
            LiveHarmonizedEvent(
                meter_urn="urn:meter:1",
                measurement="active_power",
                timestamp=start,
                value=10.0,
                source_event_id="mongo-a",
            ),
            LiveHarmonizedEvent(
                meter_urn="urn:meter:1",
                measurement="active_power",
                timestamp=start + timedelta(minutes=2),
                value=20.0,
                source_event_id="mongo-b",
            ),
        ),
        start=start,
        end=start + timedelta(minutes=5),
    )

    aggregated = downsample_mean_non_cumulative(rows, minutes=5)

    assert len(aggregated) == 1
    assert aggregated[0].value == 15.0
    assert aggregated[0].expected_points == 5
    assert aggregated[0].observed_points == 2
    assert aggregated[0].gap_points == 3
    assert aggregated[0].coverage_ratio == 0.4
    assert aggregated[0].mask_code == "gap"
    assert aggregated[0].evidence_level == "in_memory_observed"
    assert aggregated[0].quality_summary == {"gap": 3, "observed": 2}
    assert aggregated[0].source_event_ids == ("mongo-a", "mongo-b")


def test_subminute_native_cadence_uses_native_expected_ticks_per_1min_bucket():
    start = datetime(2024, 1, 1, 0, 0)
    rows = equalize_to_1min(
        (
            LiveHarmonizedEvent(
                meter_urn="urn:meter:fast",
                measurement="active_power",
                timestamp=start,
                value=10.0,
                source_event_id="tick-a",
            ),
            LiveHarmonizedEvent(
                meter_urn="urn:meter:fast",
                measurement="active_power",
                timestamp=start + timedelta(seconds=1),
                value=14.0,
                source_event_id="tick-b",
            ),
        ),
        start=start,
        end=start + timedelta(minutes=1),
        cadence_policies={
            ("urn:meter:fast", "active_power"): SeriesCadencePolicy(
                native_interval_seconds=1,
                cadence_policy_id="native_1s_to_1min",
            )
        },
    )

    assert len(rows) == 1
    assert rows[0].grain_minutes == 1
    assert rows[0].expected_points == 60
    assert rows[0].observed_points == 2
    assert rows[0].gap_points == 58
    assert rows[0].coverage_ratio == 2 / 60
    assert rows[0].quality == "partial"
    assert rows[0].mask_code == "low_coverage"
    assert rows[0].value == 12.0
    assert rows[0].source_event_ids == ("tick-a", "tick-b")
    assert rows[0].source_native_interval_seconds == 1


def test_native_5min_series_targets_15min_without_fake_1min_gaps():
    start = datetime(2024, 1, 1, 0, 0)
    result = process_live_equalization(
        (
            LiveHarmonizedEvent(
                meter_urn="urn:meter:slow",
                measurement="active_power",
                timestamp=start,
                value=10.0,
                source_event_id="five-a",
            ),
            LiveHarmonizedEvent(
                meter_urn="urn:meter:slow",
                measurement="active_power",
                timestamp=start + timedelta(minutes=5),
                value=20.0,
                source_event_id="five-b",
            ),
            LiveHarmonizedEvent(
                meter_urn="urn:meter:slow",
                measurement="active_power",
                timestamp=start + timedelta(minutes=10),
                value=30.0,
                source_event_id="five-c",
            ),
        ),
        start=start,
        end=start + timedelta(minutes=15),
        cadence_policies={
            "urn:meter:slow.active_power": SeriesCadencePolicy(
                native_interval_seconds=300,
                cadence_policy_id="native_5min_to_15min",
            )
        },
    )

    assert result.rows_1min == ()
    assert result.rows_5min == ()
    assert len(result.rows_15min) == 1
    assert result.rows_15min[0].grain_minutes == 15
    assert result.rows_15min[0].expected_points == 3
    assert result.rows_15min[0].observed_points == 3
    assert result.rows_15min[0].gap_points == 0
    assert result.rows_15min[0].coverage_ratio == 1.0
    assert result.rows_15min[0].value == 20.0
    assert result.rows_15min[0].source_event_ids == ("five-a", "five-b", "five-c")
    assert len(result.rows_1h) == 1
    assert result.rows_1h[0].expected_points == 3
