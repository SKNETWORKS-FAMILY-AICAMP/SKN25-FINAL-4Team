from datetime import UTC, datetime

from cms.contracts.live_pipeline import (
    CANONICAL_MEASUREMENT_15MIN,
    RESOLUTION_1H,
    RESOLUTION_15MIN,
    ExpectedPointsPolicy,
    compute_coverage,
)
from cms.data.live_workers import (
    StageTimer,
    build_mean_rollup,
    build_peak_feature,
    build_peak_input,
    evaluate_qa_eligibility,
    prepare_promotion,
    transform_buffered_event,
)


def _ts(minute: int) -> datetime:
    return datetime(2024, 1, 1, 0, minute, tzinfo=UTC)


def test_transform_buffered_event_is_import_safe_mapping_only():
    event = transform_buffered_event(
        {
            "kafka_key": "meter:001|P",
            "meter_urn": "H1.K11",
            "measurement": "P",
            "event_ts": "2024-01-01T00:01:00+00:00",
            "value_numeric": "0",
        }
    )

    assert event.event_id == "meter:001|P"
    assert event.value == 0.0
    assert event.source_system == "kafka.measurement_raw_v1"


def test_mean_rollup_preserves_zero_null_and_excludes_peak_fields():
    rollup = build_mean_rollup(
        bucket_ts=_ts(17),
        resolution=RESOLUTION_15MIN,
        meter_urn="H1.K11",
        measurement="P",
        values=[0.0, None, 2.0, float("nan")],
        expected_policy=ExpectedPointsPolicy(native_cadence_seconds=5 * 60),
        source_event_ids=["e1", "e3"],
    )

    assert rollup.table == "live.measurement_15min"
    assert rollup.bucket_ts == _ts(15)
    assert rollup.value == 1.0
    assert rollup.expected_points == 3
    assert rollup.observed_points == 2
    assert rollup.gap_points == 1
    assert rollup.coverage_ratio == 2 / 3
    assert not hasattr(rollup, "peak_value")
    assert not hasattr(rollup, "peak_ts")


def test_one_hour_mean_rollup_uses_native_15min_expected_points():
    rollup = build_mean_rollup(
        bucket_ts=_ts(45),
        resolution=RESOLUTION_1H,
        meter_urn="H1.K11",
        measurement="P",
        values=[1.0, 2.0, None, 4.0],
        expected_policy=ExpectedPointsPolicy(native_cadence_seconds=15 * 60),
    )

    assert rollup.table == "live.measurement_1h"
    assert rollup.bucket_ts == _ts(0)
    assert rollup.expected_points == 4
    assert rollup.observed_points == 3
    assert rollup.value == 7 / 3


def test_peak_feature_and_rolling_input_are_mart_only():
    feature_1 = build_peak_feature(
        bucket_ts=_ts(17),
        meter_urn="H1.K11",
        measurement="P",
        observations=[(_ts(15), 1.0, "e1"), (_ts(16), 5.0, "e2"), (_ts(17), None, "e3")],
        expected_points=3,
        min_coverage_ratio=0.5,
    )
    feature_2 = build_peak_feature(
        bucket_ts=_ts(32),
        meter_urn="H1.K11",
        measurement="P",
        observations=[(_ts(30), 2.0, "e4"), (_ts(31), 4.0, "e5")],
        expected_points=2,
    )
    peak_input = build_peak_input([feature_1, feature_2])

    assert feature_1.table == "mart.peak_feature_15min"
    assert feature_1.peak_value == 5.0
    assert feature_1.peak_ts == _ts(16)
    assert peak_input.table == "mart.peak_input_15min"
    assert peak_input.rolling_1h_peak_value == 5.0
    assert peak_input.rolling_1h_valid_bucket_count == 2


def test_qa_eligibility_blocks_issues_coverage_lineage_and_peak_leakage():
    decision = evaluate_qa_eligibility(
        source_table="mart.peak_feature_15min",
        target_table=CANONICAL_MEASUREMENT_15MIN,
        coverage=compute_coverage(1, 4),
        coverage_threshold=0.8,
        policy_block_reasons=["cumulative_policy_blocked"],
        issue_kinds=["policy_miss"],
        lineage_present=False,
    )

    assert decision.allowed is False
    assert "coverage_below_threshold" in decision.block_reasons
    assert "lineage_missing" in decision.block_reasons
    assert "peak_leakage_block" in decision.block_reasons
    assert "issue:policy_miss" in decision.block_reasons
    assert "cumulative_policy_blocked" in decision.block_reasons


def test_promotion_stub_requires_approval_and_blocks_peak_rows():
    missing_approval = prepare_promotion(
        source_table="live.measurement_15min",
        target_table=CANONICAL_MEASUREMENT_15MIN,
        approval_id=None,
        promotion_id="promo-1",
        promotion_check_id="check-1",
    )
    peak_leakage = prepare_promotion(
        source_table="mart.peak_feature_15min",
        target_table=CANONICAL_MEASUREMENT_15MIN,
        approval_id="approval-1",
        promotion_id="promo-1",
        promotion_check_id="check-1",
    )
    ready = prepare_promotion(
        source_table="live.measurement_15min",
        target_table=CANONICAL_MEASUREMENT_15MIN,
        approval_id="approval-1",
        promotion_id="promo-1",
        promotion_check_id="check-1",
    )

    assert missing_approval.ready is False
    assert missing_approval.block_reasons == ("approval_required",)
    assert peak_leakage.ready is False
    assert "peak_leakage_block" in peak_leakage.block_reasons
    assert ready.ready is True
    assert ready.block_reasons == ()


def test_stage_timer_records_latency_without_running_pipeline():
    with StageTimer("kafka_to_event", run_id="scratch_20260604") as timer:
        pass

    assert timer.record is not None
    assert timer.record.stage == "kafka_to_event"
    assert timer.record.duration_sec >= 0
    assert timer.record.metadata["run_id"] == "scratch_20260604"
