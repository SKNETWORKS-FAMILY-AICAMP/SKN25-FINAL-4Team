from datetime import UTC, datetime, timedelta

import pytest

from cms.contracts.live_pipeline import LiveMeasurementPolicy
from cms.data.db_scratch_guard import ScratchGuardError, postgres_scratch_schema_name
from cms.data.live_scratch_adapter import LiveScratchPipelineAdapter


class FakeBufferedSource:
    def __init__(self, records):
        self.records = tuple(records)
        self.calls = []

    def iter_buffered_events(self, *, test_run_id, start, end):
        self.calls.append({"test_run_id": test_run_id, "start": start, "end": end})
        return self.records


class FakeScratchSink:
    def __init__(self):
        self.calls = []

    def write_rows(self, *, database, schema, table, rows):
        self.calls.append({"database": database, "schema": schema, "table": table, "rows": rows})


def _records(start):
    return [
        {
            "test_run_id": "run_live_001",
            "event_id": "event-a",
            "source_event_id": "mongo-a",
            "meter_urn": "urn:meter:1",
            "measurement": "active_power",
            "source_ts": start,
            "value": 10.0,
        },
        {
            "test_run_id": "run_live_001",
            "event_id": "event-b",
            "source_event_id": "mongo-b",
            "meter_urn": "urn:meter:1",
            "measurement": "active_power",
            "source_ts": start + timedelta(minutes=1),
            "value": 20.0,
        },
    ]


def test_live_scratch_adapter_writes_mocked_pipeline_rows_to_guarded_targets():
    start = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
    source = FakeBufferedSource(_records(start))
    sink = FakeScratchSink()
    adapter = LiveScratchPipelineAdapter(source_repository=source, postgres_sink_repository=sink)

    result = adapter.run(
        test_run_id="run_live_001",
        start=start,
        end=start + timedelta(minutes=15),
        policy=LiveMeasurementPolicy(meter_urn="urn:meter:1", measurement="active_power", policy_version=7),
        allow_write=True,
        env={"ALLOW_DB_SCRATCH_WRITE": "1"},
    )

    assert result.evidence_level == "mocked_adapter"
    assert result.production_ready is False
    assert result.canonical_writes_executed is False
    assert result.real_db_writes_executed is False
    assert result.kafka_raw_topic == "measurement_raw_v1"
    assert result.target_names["measurement_event"] == "cms.cms_scratch_run_live_001.measurement_event"
    assert set(result.row_counts) >= {
        "measurement_event",
        "measurement_1min",
        "bucket_queue",
        "measurement_15min",
        "measurement_1h",
        "peak_feature_15min",
        "peak_input_15min",
        "promotion_check",
        "latency_events",
    }

    calls_by_table = {call["table"]: call for call in sink.calls}
    assert calls_by_table["measurement_event"]["schema"] == postgres_scratch_schema_name("run_live_001")
    assert len(calls_by_table["measurement_event"]["rows"]) == 2
    assert len(calls_by_table["measurement_1min"]["rows"]) == 2
    assert result.row_counts["bucket_queue"] == 6

    queue_payloads = [row["payload"] for row in calls_by_table["bucket_queue"]["rows"]]
    assert {payload["job_kind"] for payload in queue_payloads} == {"mean_rollup", "peak_feature"}
    assert {payload["resolution"] for payload in queue_payloads} == {"15min", "1h"}

    rollup = calls_by_table["measurement_15min"]["rows"][0]
    assert rollup["value"] == 15.0
    assert rollup["aggregation_policy"] == "mean_observed_only"
    assert rollup["coverage_ratio"] == 2 / 15

    peak_feature = calls_by_table["peak_feature_15min"]["rows"][0]
    assert peak_feature["payload"]["table"] == "mart.peak_feature_15min"
    assert peak_feature["payload"]["peak_value"] == 20.0

    promotion_check = calls_by_table["promotion_check"]["rows"][0]
    assert promotion_check["payload"]["target_table"] == "canonical.measurement_15min"
    assert promotion_check["payload"]["ready"] is False
    assert "approval_required" in promotion_check["payload"]["block_reasons"]


def test_live_scratch_adapter_is_default_deny_before_sink_write():
    start = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
    source = FakeBufferedSource(_records(start))
    sink = FakeScratchSink()
    adapter = LiveScratchPipelineAdapter(source_repository=source, postgres_sink_repository=sink)

    with pytest.raises(ScratchGuardError, match="ALLOW_DB_SCRATCH_WRITE"):
        adapter.run(
            test_run_id="run_live_001",
            start=start,
            end=start + timedelta(minutes=15),
            policy=LiveMeasurementPolicy(meter_urn="urn:meter:1", measurement="active_power", policy_version=7),
            allow_write=True,
            env={},
        )
    assert sink.calls == []


def test_live_scratch_adapter_rejects_unsafe_run_id_and_mismatched_raw_marker():
    start = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
    sink = FakeScratchSink()

    adapter = LiveScratchPipelineAdapter(source_repository=FakeBufferedSource(_records(start)), postgres_sink_repository=sink)
    with pytest.raises(ScratchGuardError, match="test_run_id"):
        adapter.run(
            test_run_id="prod_001",
            start=start,
            end=start + timedelta(minutes=15),
            policy=LiveMeasurementPolicy(meter_urn="urn:meter:1", measurement="active_power", policy_version=7),
            allow_write=True,
            env={"ALLOW_DB_SCRATCH_WRITE": "1"},
        )
    assert sink.calls == []

    bad_source = FakeBufferedSource([{**_records(start)[0], "test_run_id": "other"}])
    adapter = LiveScratchPipelineAdapter(source_repository=bad_source, postgres_sink_repository=sink)
    with pytest.raises(ScratchGuardError, match="test_run_id"):
        adapter.run(
            test_run_id="run_live_001",
            start=start,
            end=start + timedelta(minutes=15),
            policy=LiveMeasurementPolicy(meter_urn="urn:meter:1", measurement="active_power", policy_version=7),
            allow_write=True,
            env={"ALLOW_DB_SCRATCH_WRITE": "1"},
        )
    assert sink.calls == []
