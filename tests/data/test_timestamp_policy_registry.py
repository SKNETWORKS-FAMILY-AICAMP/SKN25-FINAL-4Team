import pytest

from cms.data.timestamp_policy_registry import TimestampPolicyRegistry, load_timestamp_policy_registry


def test_load_timestamp_policy_registry_converts_csv_rows_to_policies(tmp_path):
    path = tmp_path / "policies.csv"
    path.write_text(
        "meter_urn,measurement,source_id,file_pattern,timestamp_policy_id,timestamp_column,source_timezone,source_timestamp_type,native_interval_seconds,target_grain_minutes,timestamp_origin_rule,cadence_policy_id,aggregation_policy\n"
        "urn:meter:1,active_power,src-a,*.csv,tp_001,ts,UTC,utc_instant,60,1,exact_boundary,native_1min,mean_non_cumulative\n"
    )

    registry = load_timestamp_policy_registry(path)
    policy = registry.get_policy(meter_urn="urn:meter:1", measurement="active_power")

    assert isinstance(registry, TimestampPolicyRegistry)
    assert policy.timestamp_policy_id == "tp_001"
    assert policy.native_interval_seconds == 60
    assert policy.target_grain_minutes == 1
    assert policy.timestamp_column == "ts"


def test_load_timestamp_policy_registry_rejects_duplicate_meter_measurement(tmp_path):
    path = tmp_path / "policies.csv"
    path.write_text(
        "meter_urn,measurement,source_id,file_pattern,timestamp_policy_id,timestamp_column,source_timezone,source_timestamp_type,native_interval_seconds,target_grain_minutes,timestamp_origin_rule,cadence_policy_id,aggregation_policy\n"
        "urn:meter:1,active_power,src-a,*.csv,tp_001,ts,UTC,utc_instant,60,1,exact_boundary,native_1min,mean_non_cumulative\n"
        "urn:meter:1,active_power,src-b,*.csv,tp_002,ts,UTC,utc_instant,60,1,exact_boundary,native_1min,mean_non_cumulative\n"
    )

    with pytest.raises(ValueError, match="duplicate"):
        load_timestamp_policy_registry(path)


def test_load_timestamp_policy_registry_rejects_missing_required_field(tmp_path):
    path = tmp_path / "policies.csv"
    path.write_text(
        "meter_urn,measurement,source_id,file_pattern,timestamp_policy_id,timestamp_column,source_timezone,source_timestamp_type,native_interval_seconds,target_grain_minutes,timestamp_origin_rule,cadence_policy_id,aggregation_policy\n"
        "urn:meter:1,active_power,src-a,*.csv,,ts,UTC,utc_instant,60,1,exact_boundary,native_1min,mean_non_cumulative\n"
    )

    with pytest.raises(ValueError, match="timestamp_policy_id"):
        load_timestamp_policy_registry(path)
