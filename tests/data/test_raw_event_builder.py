from datetime import datetime, timezone

import pytest

from cms.contracts.timestamp_policy import TimestampPolicy
from cms.data.raw_event_builder import build_raw_event


def _policy(**overrides):
    values = {
        "timestamp_policy_id": "tp_001",
        "meter_urn": "urn:meter:1",
        "measurement": "active_power",
        "source_id": "src-a",
        "file_pattern": "*.csv",
        "timestamp_column": "ts",
        "source_timezone": "UTC",
        "source_timestamp_type": "utc_instant",
        "native_interval_seconds": 300,
        "target_grain_minutes": 15,
        "timestamp_origin_rule": "exact_boundary",
        "cadence_policy_id": "native_5min_to_15min",
        "aggregation_policy": "mean_non_cumulative",
    }
    values.update(overrides)
    return TimestampPolicy(**values)


def test_build_raw_event_preserves_source_timestamp_and_policy_metadata():
    document = build_raw_event(
        {"ts": "2024-01-01T00:05:00Z", "value": "12.5", "source_event_id": "src-row-1"},
        _policy(),
    )

    assert document["meter_urn"] == "urn:meter:1"
    assert document["measurement"] == "active_power"
    assert document["source_ts_raw"] == "2024-01-01T00:05:00Z"
    assert document["source_ts_column"] == "ts"
    assert document["event_ts_utc"] == datetime(2024, 1, 1, 0, 5, tzinfo=timezone.utc)
    assert document["timestamp"] == document["event_ts_utc"]
    assert document["source_timezone"] == "UTC"
    assert document["timestamp_policy_id"] == "tp_001"
    assert document["timestamp_parse_status"] == "ok"
    assert document["timestamp_origin_rule"] == "exact_boundary"
    assert document["timestamp_quality_code"] == "timestamp_normalized"
    assert document["value"] == 12.5
    assert document["native_interval_seconds"] == 300
    assert document["target_grain_minutes"] == 15
    assert document["cadence_policy_id"] == "native_5min_to_15min"
    assert document["aggregation_policy"] == "mean_non_cumulative"
    assert document["source_event_id"] == "src-row-1"


def test_build_raw_event_rejects_value_casting_failure():
    with pytest.raises(ValueError, match="value"):
        build_raw_event({"ts": "2024-01-01T00:00:00Z", "value": "not-a-number"}, _policy())
