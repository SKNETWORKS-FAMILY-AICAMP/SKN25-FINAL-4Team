from datetime import datetime, timedelta, timezone

from cms.data.live_equalization_processor import LiveHarmonizedEvent
from cms.data.timestamp_qa import validate_timestamp_quality


def _event(offset_minutes, **overrides):
    values = {
        "meter_urn": "urn:meter:1",
        "measurement": "active_power",
        "timestamp": datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=offset_minutes),
        "value": 10.0,
        "source_event_id": f"e-{offset_minutes}",
        "native_interval_seconds": 300,
        "timestamp_policy_id": "tp_001",
        "cadence_policy_id": "native_5min_to_15min",
    }
    values.update(overrides)
    return LiveHarmonizedEvent(**values)


def test_timestamp_qa_returns_json_serializable_pass_report_without_mutation():
    events = (_event(0), _event(5), _event(10))

    report = validate_timestamp_quality(events)

    assert report.passed is True
    assert report.counts["events"] == 3
    assert report.counts["series"] == 1
    assert report.hard_failures == ()
    assert report.to_dict()["passed"] is True
    assert events[0].timestamp_policy_id == "tp_001"


def test_timestamp_qa_hard_fails_duplicate_timestamps():
    report = validate_timestamp_quality((_event(0), _event(0, source_event_id="dup")))

    assert report.passed is False
    assert any(failure["code"] == "duplicate_event_ts_utc" for failure in report.hard_failures)


def test_timestamp_qa_hard_fails_conflicting_policy_in_same_series():
    report = validate_timestamp_quality((_event(0), _event(5, timestamp_policy_id="tp_002")))

    assert report.passed is False
    assert any(failure["code"] == "timestamp_policy_conflict" for failure in report.hard_failures)


def test_timestamp_qa_hard_fails_native_interval_mismatch():
    report = validate_timestamp_quality((_event(0), _event(7)))

    assert report.passed is False
    assert any(failure["code"] == "native_interval_mismatch" for failure in report.hard_failures)


def test_timestamp_qa_warns_for_out_of_order_and_unexpected_gap():
    report = validate_timestamp_quality((_event(10), _event(0)))

    assert report.passed is True
    assert any(warning["code"] == "out_of_order_input" for warning in report.warnings)
    assert any(warning["code"] == "unexpected_gap" for warning in report.warnings)
