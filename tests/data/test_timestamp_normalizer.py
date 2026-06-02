from datetime import datetime, timezone

import pytest

from cms.contracts.timestamp_policy import TimestampPolicy
from cms.data.timestamp_normalizer import normalize_timestamp


def _policy(**overrides):
    values = {
        "timestamp_policy_id": "tp_001",
        "meter_urn": "urn:meter:1",
        "measurement": "active_power",
        "source_id": None,
        "file_pattern": None,
        "timestamp_column": "ts",
        "source_timezone": "UTC",
        "source_timestamp_type": "utc_instant",
        "native_interval_seconds": 60,
        "target_grain_minutes": 1,
        "timestamp_origin_rule": "exact_boundary",
        "cadence_policy_id": "native_1min",
    }
    values.update(overrides)
    return TimestampPolicy(**values)


def test_normalize_timestamp_converts_utc_iso_string_to_utc_aware_datetime():
    normalized = normalize_timestamp({"ts": "2024-01-01T00:00:00Z"}, _policy())

    assert normalized.event_ts_utc == datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    assert normalized.source_ts_raw == "2024-01-01T00:00:00Z"
    assert normalized.source_ts_column == "ts"
    assert normalized.timestamp_parse_status == "ok"
    assert normalized.timestamp_quality_code == "timestamp_normalized"


def test_normalize_timestamp_converts_asia_seoul_wall_time_to_utc():
    policy = _policy(
        source_timezone="Asia/Seoul",
        source_timestamp_type="local_wall_time",
        timestamp_column="local_ts",
    )

    normalized = normalize_timestamp({"local_ts": "2024-01-01 09:00:00"}, policy)

    assert normalized.event_ts_utc == datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)


def test_normalize_timestamp_rejects_aware_value_for_local_wall_time():
    policy = _policy(
        source_timezone="Asia/Seoul",
        source_timestamp_type="local_wall_time",
        timestamp_column="local_ts",
    )

    with pytest.raises(ValueError, match="local_wall_time"):
        normalize_timestamp({"local_ts": "2024-01-01T09:00:00+00:00"}, policy)


def test_normalize_timestamp_rejects_missing_timestamp_column():
    with pytest.raises(ValueError, match="timestamp column"):
        normalize_timestamp({}, _policy())


def test_normalize_timestamp_rejects_parse_failure():
    with pytest.raises(ValueError, match="parse"):
        normalize_timestamp({"ts": "not a timestamp"}, _policy())


def test_normalize_timestamp_floors_to_native_interval_boundary():
    policy = _policy(native_interval_seconds=300, timestamp_origin_rule="floor_to_native_interval")

    normalized = normalize_timestamp({"ts": "2024-01-01T00:04:30Z"}, policy)

    assert normalized.event_ts_utc == datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    assert normalized.timestamp_quality_code == "timestamp_floored_to_native_interval"


def test_normalize_timestamp_rejects_off_boundary_when_policy_requires_reject():
    policy = _policy(native_interval_seconds=300, timestamp_origin_rule="reject_off_boundary")

    with pytest.raises(ValueError, match="boundary"):
        normalize_timestamp({"ts": "2024-01-01T00:04:30Z"}, policy)


def test_normalize_timestamp_exact_boundary_rejects_unaligned_seconds():
    with pytest.raises(ValueError, match="boundary"):
        normalize_timestamp({"ts": "2024-01-01T00:00:30Z"}, _policy())
