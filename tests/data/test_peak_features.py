from datetime import UTC, datetime, timedelta

from cms.data.peak_features import PeakSample, aggregate_peak_features, floor_to_window


def test_floor_to_window_uses_15min_utc_bucket():
    ts = datetime(2024, 1, 1, 0, 29, 30, tzinfo=UTC)

    assert floor_to_window(ts, minutes=15) == datetime(2024, 1, 1, 0, 15, tzinfo=UTC)


def test_aggregate_peak_features_preserves_peak_and_coverage():
    start = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
    samples = [
        PeakSample(start + timedelta(minutes=0), 10.0),
        PeakSample(start + timedelta(minutes=1), 20.0),
        PeakSample(start + timedelta(minutes=2), 100.0),
        PeakSample(start + timedelta(minutes=3), 30.0),
    ]

    rows = aggregate_peak_features(
        samples,
        meter_urn="H1.K11",
        measurement="P",
        source_file="H1.K11/H1.K11.P_corrected_resampled_1min.csv.gz",
        run_id="peak15_pilot_test",
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.window_ts == start
    assert row.meter_urn == "H1.K11"
    assert row.measurement == "P"
    assert row.mean_value == 40.0
    assert row.max_value == 100.0
    assert row.min_value == 10.0
    assert row.p95_value == 100.0
    assert row.p99_value == 100.0
    assert round(row.std_value, 6) == 35.355339
    assert row.last_value == 30.0
    assert row.peak_ts == start + timedelta(minutes=2)
    assert row.peak_value == 100.0
    assert row.observed_points == 4
    assert row.expected_points == 15
    assert row.coverage_ratio == 4 / 15


def test_aggregate_peak_features_splits_windows_and_ignores_nan():
    start = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
    samples = [
        PeakSample(start + timedelta(minutes=14), 1.0),
        PeakSample(start + timedelta(minutes=15), 5.0),
        PeakSample(start + timedelta(minutes=16), None),
        PeakSample(start + timedelta(minutes=17), float("nan")),
        PeakSample(start + timedelta(minutes=18), 7.0),
    ]

    rows = aggregate_peak_features(
        samples,
        meter_urn="H1.K11",
        measurement="P",
        source_file="H1.K11/H1.K11.P_corrected_resampled_1min.csv.gz",
        run_id="peak15_pilot_test",
    )

    assert [row.window_ts for row in rows] == [start, start + timedelta(minutes=15)]
    assert [row.max_value for row in rows] == [1.0, 7.0]
    assert [row.observed_points for row in rows] == [1, 2]
