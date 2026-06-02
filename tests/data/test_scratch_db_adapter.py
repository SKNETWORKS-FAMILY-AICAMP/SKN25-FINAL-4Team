from datetime import datetime, timedelta, timezone

import pytest

from cms.data.scratch_db_adapter import ScratchDbAdapter
from cms.data.scratch_ddl import REQUIRED_COMMON_COLUMNS, render_scratch_ddl


class FakeSource:
    def __init__(self, documents):
        self.documents = documents

    def iter_raw_harmonized_documents(self, *, test_run_id, start, end):
        return tuple(self.documents)


class FakeSink:
    def __init__(self):
        self.calls = []

    def write_rows(self, *, database, schema, table, rows):
        self.calls.append({"database": database, "schema": schema, "table": table, "rows": rows})


def test_adapter_payload_uses_actual_equalization_coverage_and_source_event_ids():
    start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    source = FakeSource(
        [
            {
                "test_run_id": "run_cov_001",
                "meter_urn": "urn:meter:1",
                "measurement": "active_power",
                "timestamp": start,
                "value": 10.0,
                "source_event_id": "mongo-a",
            },
            {
                "test_run_id": "run_cov_001",
                "meter_urn": "urn:meter:1",
                "measurement": "active_power",
                "timestamp": start + timedelta(minutes=2),
                "value": 20.0,
                "source_event_id": "mongo-b",
            },
        ]
    )
    sink = FakeSink()
    adapter = ScratchDbAdapter(source_repository=source, postgres_sink_repository=sink)

    adapter.run(
        test_run_id="run_cov_001",
        start=start,
        end=start + timedelta(minutes=5),
        allow_write=True,
        env={"ALLOW_DB_SCRATCH_WRITE": "1"},
    )

    rows_1min = next(call["rows"] for call in sink.calls if call["table"] == "measurement_1min")
    assert rows_1min[0]["coverage_ratio"] == 1.0
    assert rows_1min[0]["expected_points"] == 1
    assert rows_1min[0]["observed_points"] == 1
    assert rows_1min[0]["gap_points"] == 0
    assert rows_1min[0]["source_event_ids"] == ("mongo-a",)
    assert rows_1min[0]["source_native_interval_seconds"] == 60
    assert rows_1min[0]["cadence_policy_id"] == "native_1min"
    assert rows_1min[0]["target_resolution"] == "1min"
    assert rows_1min[0]["expected_points_policy"] == "native_interval"
    assert rows_1min[0]["aggregation_policy"] == "mean_non_cumulative"

    assert rows_1min[1]["quality_code"] == "gap"
    assert rows_1min[1]["mask_code"] == "gap"
    assert rows_1min[1]["evidence_level"] == "in_memory_observed"
    assert rows_1min[1]["coverage_ratio"] == 0.0
    assert rows_1min[1]["expected_points"] == 1
    assert rows_1min[1]["observed_points"] == 0
    assert rows_1min[1]["gap_points"] == 1
    assert rows_1min[1]["source_event_ids"] == ()

    rows_5min = next(call["rows"] for call in sink.calls if call["table"] == "measurement_5min")
    assert rows_5min[0]["mask_code"] == "gap"
    assert rows_5min[0]["evidence_level"] == "in_memory_observed"
    assert rows_5min[0]["coverage_ratio"] == 0.4
    assert rows_5min[0]["expected_points"] == 5
    assert rows_5min[0]["observed_points"] == 2
    assert rows_5min[0]["gap_points"] == 3
    assert rows_5min[0]["quality_summary"] == {"gap": 3, "observed": 2}
    assert rows_5min[0]["source_event_ids"] == ("mongo-a", "mongo-b")


def test_adapter_payload_preserves_native_cadence_policy_for_5min_source():
    start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    source = FakeSource(
        [
            {
                "test_run_id": "run_cov_005",
                "meter_urn": "urn:meter:5min",
                "measurement": "active_power",
                "timestamp": start,
                "value": 10.0,
                "source_event_id": "five-a",
                "native_interval_seconds": 300,
                "cadence_policy_id": "native_5min_to_15min",
                "aggregation_policy": "mean_non_cumulative",
            },
            {
                "test_run_id": "run_cov_005",
                "meter_urn": "urn:meter:5min",
                "measurement": "active_power",
                "timestamp": start + timedelta(minutes=5),
                "value": 20.0,
                "source_event_id": "five-b",
                "native_interval_seconds": 300,
                "cadence_policy_id": "native_5min_to_15min",
                "aggregation_policy": "mean_non_cumulative",
            },
            {
                "test_run_id": "run_cov_005",
                "meter_urn": "urn:meter:5min",
                "measurement": "active_power",
                "timestamp": start + timedelta(minutes=10),
                "value": 30.0,
                "source_event_id": "five-c",
                "native_interval_seconds": 300,
                "cadence_policy_id": "native_5min_to_15min",
                "aggregation_policy": "mean_non_cumulative",
            },
        ]
    )
    sink = FakeSink()
    adapter = ScratchDbAdapter(source_repository=source, postgres_sink_repository=sink)

    result = adapter.run(
        test_run_id="run_cov_005",
        start=start,
        end=start + timedelta(minutes=15),
        allow_write=True,
        env={"ALLOW_DB_SCRATCH_WRITE": "1"},
    )

    assert result.row_counts["measurement_1min"] == 0
    assert result.row_counts["measurement_5min"] == 0
    assert result.row_counts["measurement_15min"] == 1
    rows_15min = next(call["rows"] for call in sink.calls if call["table"] == "measurement_15min")
    assert rows_15min[0]["expected_points"] == 3
    assert rows_15min[0]["observed_points"] == 3
    assert rows_15min[0]["source_native_interval_seconds"] == 300
    assert rows_15min[0]["cadence_policy_id"] == "native_5min_to_15min"
    assert rows_15min[0]["target_resolution"] == "15min"
    assert rows_15min[0]["expected_points_policy"] == "native_interval"
    assert rows_15min[0]["aggregation_policy"] == "mean_non_cumulative"


def test_scratch_common_columns_include_coverage_counts_and_quality_summary():
    for column in (
        "mask_code",
        "evidence_level",
        "expected_points",
        "observed_points",
        "gap_points",
        "source_native_interval_seconds",
        "cadence_policy_id",
        "target_resolution",
        "expected_points_policy",
        "aggregation_policy",
        "quality_summary",
        "timestamp_policy_ids",
        "source_timezones",
        "source_ts_columns",
        "source_ts_raw_samples",
        "timestamp_quality_summary",
        "timestamp_origin_rules",
    ):
        assert column in REQUIRED_COMMON_COLUMNS
        assert column in render_scratch_ddl("run_cov_001")


def test_adapter_rejects_mismatched_event_ts_utc_and_legacy_timestamp():
    start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    source = FakeSource(
        [
            {
                "test_run_id": "run_ts_001",
                "meter_urn": "urn:meter:1",
                "measurement": "active_power",
                "event_ts_utc": start,
                "timestamp": start + timedelta(minutes=1),
                "value": 10.0,
                "source_event_id": "event-ts-wins",
            }
        ]
    )
    sink = FakeSink()
    adapter = ScratchDbAdapter(source_repository=source, postgres_sink_repository=sink)

    with pytest.raises(ValueError, match="event_ts_utc.*timestamp"):
        adapter.run(
            test_run_id="run_ts_001",
            start=start,
            end=start + timedelta(minutes=1),
            allow_write=True,
            env={"ALLOW_DB_SCRATCH_WRITE": "1"},
        )


def test_adapter_accepts_matching_event_ts_utc_and_aware_timestamp_same_utc_instant():
    start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    source = FakeSource(
        [
            {
                "test_run_id": "run_ts_002",
                "meter_urn": "urn:meter:1",
                "measurement": "active_power",
                "event_ts_utc": "2024-01-01T00:00:00Z",
                "timestamp": "2024-01-01T09:00:00+09:00",
                "value": 10.0,
                "source_event_id": "event-ts-match",
            }
        ]
    )
    sink = FakeSink()
    adapter = ScratchDbAdapter(source_repository=source, postgres_sink_repository=sink)

    adapter.run(
        test_run_id="run_ts_002",
        start=start,
        end=start + timedelta(minutes=1),
        allow_write=True,
        env={"ALLOW_DB_SCRATCH_WRITE": "1"},
    )

    rows_1min = next(call["rows"] for call in sink.calls if call["table"] == "measurement_1min")
    assert rows_1min[0]["bucket_ts"] == start


def test_adapter_payload_preserves_timestamp_policy_provenance():
    start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    source = FakeSource(
        [
            {
                "test_run_id": "run_ts_007",
                "meter_urn": "urn:meter:1",
                "measurement": "active_power",
                "event_ts_utc": start,
                "timestamp": start,
                "value": 10.0,
                "source_event_id": "event-a",
                "native_interval_seconds": 60,
                "cadence_policy_id": "native_1min",
                "timestamp_policy_id": "tp_001",
                "source_timezone": "UTC",
                "source_ts_raw": "2024-01-01T00:00:00Z",
                "source_ts_column": "measured_at",
                "timestamp_quality_code": "timestamp_normalized",
                "timestamp_origin_rule": "exact_boundary",
            }
        ]
    )
    sink = FakeSink()
    adapter = ScratchDbAdapter(source_repository=source, postgres_sink_repository=sink)

    adapter.run(
        test_run_id="run_ts_007",
        start=start,
        end=start + timedelta(minutes=1),
        allow_write=True,
        env={"ALLOW_DB_SCRATCH_WRITE": "1"},
    )

    rows_1min = next(call["rows"] for call in sink.calls if call["table"] == "measurement_1min")
    assert rows_1min[0]["timestamp_policy_ids"] == ("tp_001",)
    assert rows_1min[0]["source_timezones"] == ("UTC",)
    assert rows_1min[0]["source_ts_columns"] == ("measured_at",)
    assert rows_1min[0]["source_ts_raw_samples"] == ("2024-01-01T00:00:00Z",)
    assert rows_1min[0]["timestamp_quality_summary"] == {"timestamp_normalized": 1}
    assert rows_1min[0]["timestamp_origin_rules"] == ("exact_boundary",)


def test_adapter_rejects_naive_event_ts_utc():
    start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    source = FakeSource(
        [
            {
                "test_run_id": "run_ts_003",
                "meter_urn": "urn:meter:1",
                "measurement": "active_power",
                "event_ts_utc": datetime(2024, 1, 1, 0, 0),
                "value": 10.0,
            }
        ]
    )
    sink = FakeSink()
    adapter = ScratchDbAdapter(source_repository=source, postgres_sink_repository=sink)

    with pytest.raises(ValueError, match="timezone-aware"):
        adapter.run(
            test_run_id="run_ts_003",
            start=start,
            end=start + timedelta(minutes=1),
            allow_write=True,
            env={"ALLOW_DB_SCRATCH_WRITE": "1"},
        )


def test_adapter_rejects_naive_legacy_timestamp():
    start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    source = FakeSource(
        [
            {
                "test_run_id": "run_ts_008",
                "meter_urn": "urn:meter:1",
                "measurement": "active_power",
                "timestamp": datetime(2024, 1, 1, 0, 0),
                "value": 10.0,
            }
        ]
    )
    sink = FakeSink()
    adapter = ScratchDbAdapter(source_repository=source, postgres_sink_repository=sink)

    with pytest.raises(ValueError, match="timezone-aware"):
        adapter.run(
            test_run_id="run_ts_008",
            start=start,
            end=start + timedelta(minutes=1),
            allow_write=True,
            env={"ALLOW_DB_SCRATCH_WRITE": "1"},
        )


def test_adapter_rejects_naive_window_with_aware_events():
    event_ts = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    source = FakeSource(
        [
            {
                "test_run_id": "run_ts_009",
                "meter_urn": "urn:meter:1",
                "measurement": "active_power",
                "event_ts_utc": event_ts,
                "value": 10.0,
            }
        ]
    )
    sink = FakeSink()
    adapter = ScratchDbAdapter(source_repository=source, postgres_sink_repository=sink)

    with pytest.raises(ValueError, match="start.*timezone-aware"):
        adapter.run(
            test_run_id="run_ts_009",
            start=datetime(2024, 1, 1, 0, 0),
            end=datetime(2024, 1, 1, 0, 1),
            allow_write=True,
            env={"ALLOW_DB_SCRATCH_WRITE": "1"},
        )


def test_adapter_timestamp_qa_rejects_duplicate_before_sink_write():
    start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    source = FakeSource(
        [
            {
                "test_run_id": "run_ts_004",
                "meter_urn": "urn:meter:1",
                "measurement": "active_power",
                "event_ts_utc": start,
                "value": 10.0,
                "native_interval_seconds": 60,
                "timestamp_policy_id": "tp_001",
            },
            {
                "test_run_id": "run_ts_004",
                "meter_urn": "urn:meter:1",
                "measurement": "active_power",
                "event_ts_utc": start,
                "value": 20.0,
                "native_interval_seconds": 60,
                "timestamp_policy_id": "tp_001",
            },
        ]
    )
    sink = FakeSink()
    adapter = ScratchDbAdapter(source_repository=source, postgres_sink_repository=sink)

    with pytest.raises(ValueError, match="duplicate_event_ts_utc"):
        adapter.run(
            test_run_id="run_ts_004",
            start=start,
            end=start + timedelta(minutes=1),
            allow_write=True,
            env={"ALLOW_DB_SCRATCH_WRITE": "1"},
        )
    assert sink.calls == []


def test_adapter_timestamp_qa_rejects_policy_conflict_before_sink_write():
    start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    source = FakeSource(
        [
            {
                "test_run_id": "run_ts_005",
                "meter_urn": "urn:meter:1",
                "measurement": "active_power",
                "event_ts_utc": start,
                "value": 10.0,
                "native_interval_seconds": 60,
                "timestamp_policy_id": "tp_001",
            },
            {
                "test_run_id": "run_ts_005",
                "meter_urn": "urn:meter:1",
                "measurement": "active_power",
                "event_ts_utc": start + timedelta(minutes=1),
                "value": 20.0,
                "native_interval_seconds": 60,
                "timestamp_policy_id": "tp_002",
            },
        ]
    )
    sink = FakeSink()
    adapter = ScratchDbAdapter(source_repository=source, postgres_sink_repository=sink)

    with pytest.raises(ValueError, match="timestamp_policy_conflict"):
        adapter.run(
            test_run_id="run_ts_005",
            start=start,
            end=start + timedelta(minutes=2),
            allow_write=True,
            env={"ALLOW_DB_SCRATCH_WRITE": "1"},
        )
    assert sink.calls == []


def test_adapter_timestamp_qa_rejects_native_interval_mismatch_before_sink_write():
    start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    source = FakeSource(
        [
            {
                "test_run_id": "run_ts_006",
                "meter_urn": "urn:meter:1",
                "measurement": "active_power",
                "event_ts_utc": start,
                "value": 10.0,
                "native_interval_seconds": 300,
                "timestamp_policy_id": "tp_001",
            },
            {
                "test_run_id": "run_ts_006",
                "meter_urn": "urn:meter:1",
                "measurement": "active_power",
                "event_ts_utc": start + timedelta(minutes=7),
                "value": 20.0,
                "native_interval_seconds": 300,
                "timestamp_policy_id": "tp_001",
            },
        ]
    )
    sink = FakeSink()
    adapter = ScratchDbAdapter(source_repository=source, postgres_sink_repository=sink)

    with pytest.raises(ValueError, match="native_interval_mismatch"):
        adapter.run(
            test_run_id="run_ts_006",
            start=start,
            end=start + timedelta(minutes=15),
            allow_write=True,
            env={"ALLOW_DB_SCRATCH_WRITE": "1"},
        )
    assert sink.calls == []
