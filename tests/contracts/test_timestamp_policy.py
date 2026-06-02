import pytest

from cms.contracts.timestamp_policy import TimestampPolicy


def _policy(**overrides):
    values = {
        "timestamp_policy_id": "tp_001",
        "meter_urn": "urn:meter:1",
        "measurement": "active_power",
        "source_id": "source-a",
        "file_pattern": "*.csv",
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


def test_timestamp_policy_accepts_required_fields_and_default_aggregation_policy():
    policy = _policy()

    assert policy.timestamp_policy_id == "tp_001"
    assert policy.meter_urn == "urn:meter:1"
    assert policy.measurement == "active_power"
    assert policy.source_id == "source-a"
    assert policy.file_pattern == "*.csv"
    assert policy.timestamp_column == "ts"
    assert policy.source_timezone == "UTC"
    assert policy.source_timestamp_type == "utc_instant"
    assert policy.native_interval_seconds == 60
    assert policy.target_grain_minutes == 1
    assert policy.timestamp_origin_rule == "exact_boundary"
    assert policy.cadence_policy_id == "native_1min"
    assert policy.aggregation_policy == "mean_non_cumulative"


@pytest.mark.parametrize("native_interval_seconds", [0, -1])
def test_timestamp_policy_rejects_non_positive_native_interval(native_interval_seconds):
    with pytest.raises(ValueError, match="native_interval_seconds"):
        _policy(native_interval_seconds=native_interval_seconds)


@pytest.mark.parametrize("target_grain_minutes", [0, -1])
def test_timestamp_policy_rejects_non_positive_target_grain(target_grain_minutes):
    with pytest.raises(ValueError, match="target_grain_minutes"):
        _policy(target_grain_minutes=target_grain_minutes)


@pytest.mark.parametrize("source_timestamp_type", ["local_time", "epoch"])
def test_timestamp_policy_rejects_unsupported_source_timestamp_type(source_timestamp_type):
    with pytest.raises(ValueError, match="source_timestamp_type"):
        _policy(source_timestamp_type=source_timestamp_type)


@pytest.mark.parametrize("timestamp_origin_rule", ["ceil", "round"])
def test_timestamp_policy_rejects_unsupported_origin_rule(timestamp_origin_rule):
    with pytest.raises(ValueError, match="timestamp_origin_rule"):
        _policy(timestamp_origin_rule=timestamp_origin_rule)
