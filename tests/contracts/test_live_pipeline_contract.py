from datetime import UTC, datetime

import pytest

from cms.contracts.live_pipeline import (
    ALLOWED_QUEUE_JOB_SPECS,
    CANONICAL_MEASUREMENT_15MIN,
    CANONICAL_TABLES,
    FORBIDDEN_TRIGGER_TARGETS,
    JOB_KIND_MEAN_ROLLUP,
    JOB_KIND_PEAK_FEATURE,
    LIVE_BUCKET_QUEUE,
    LIVE_MEASUREMENT_1H,
    LIVE_MEASUREMENT_1MIN,
    LIVE_MEASUREMENT_15MIN,
    LIVE_MEASUREMENT_POLICY,
    LIVE_ROLLUP_VALUE_SEMANTIC,
    MART_PEAK_FEATURE_15MIN,
    MART_PEAK_INPUT_15MIN,
    QA_LIVE_MEASUREMENT_ISSUE,
    QUEUE_IDEMPOTENCY_FIELDS,
    RESOLUTION_1H,
    RESOLUTION_15MIN,
    BucketQueueKey,
    ExpectedPointsPolicy,
    LiveMeasurementEvent,
    LiveMeasurementPolicy,
    PolicyLookupResult,
    assert_trigger_contract,
    compute_coverage,
    count_observed_points,
    decide_trigger_actions,
    derive_expected_points,
    floor_to_resolution,
    guard_peak_feature_promotion,
    is_observed_value,
    live_mean_rollup_output_contract,
    trigger_policy_block_reasons,
)


def _event() -> LiveMeasurementEvent:
    return LiveMeasurementEvent(
        event_id="evt-1",
        meter_urn="H1.K11",
        measurement="P",
        source_ts=datetime(2024, 1, 1, 0, 29, 37, tzinfo=UTC),
        value=0.0,
    )


def _policy() -> LiveMeasurementPolicy:
    return LiveMeasurementPolicy(meter_urn="H1.K11", measurement="P", policy_version=7)


def test_trigger_jobs_are_exactly_three_allowed_worker_jobs():
    result = decide_trigger_actions(_event(), _policy())

    assert result.upsert_1min is True
    assert [(job.key.job_kind, job.key.resolution) for job in result.queue_jobs] == list(ALLOWED_QUEUE_JOB_SPECS)
    assert [(job.key.job_kind, job.key.resolution) for job in result.queue_jobs] == [
        (JOB_KIND_MEAN_ROLLUP, RESOLUTION_15MIN),
        (JOB_KIND_MEAN_ROLLUP, RESOLUTION_1H),
        (JOB_KIND_PEAK_FEATURE, RESOLUTION_15MIN),
    ]
    assert [job.key.bucket_ts for job in result.queue_jobs] == [
        datetime(2024, 1, 1, 0, 15, tzinfo=UTC),
        datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 0, 15, tzinfo=UTC),
    ]


def test_trigger_skeleton_only_models_allowed_operations():
    result = decide_trigger_actions(_event(), _policy())

    assert_trigger_contract(result)
    assert [operation.target for operation in result.operations] == [
        LIVE_MEASUREMENT_POLICY,
        LIVE_MEASUREMENT_1MIN,
        LIVE_BUCKET_QUEUE,
    ]
    assert all(operation.target not in FORBIDDEN_TRIGGER_TARGETS for operation in result.operations)
    assert all(operation.target not in CANONICAL_TABLES for operation in result.operations)


def test_policy_miss_logs_issue_without_1min_or_queue_jobs():
    result = decide_trigger_actions(_event(), None)

    assert result.upsert_1min is False
    assert result.queue_jobs == ()
    assert [operation.target for operation in result.operations] == [LIVE_MEASUREMENT_POLICY, QA_LIVE_MEASUREMENT_ISSUE]
    assert result.issues[0].issue_kind == "policy_miss"


def test_policy_lookup_missing_result_logs_issue_without_1min_or_queue_jobs():
    lookup = PolicyLookupResult(status="missing", effective_ts=_event().source_ts, reason="no effective policy at event time")

    result = decide_trigger_actions(_event(), lookup)

    assert result.upsert_1min is False
    assert result.queue_jobs == ()
    assert [operation.target for operation in result.operations] == [LIVE_MEASUREMENT_POLICY, QA_LIVE_MEASUREMENT_ISSUE]
    assert result.issues[0].issue_kind == "policy_miss"
    assert result.issues[0].reason == "no effective policy at event time"


def test_policy_ambiguous_logs_issue_without_1min_or_queue_jobs():
    lookup = PolicyLookupResult(status="ambiguous", effective_ts=_event().source_ts, matched_policy_versions=(7, 8))

    result = decide_trigger_actions(_event(), lookup)

    assert result.upsert_1min is False
    assert result.queue_jobs == ()
    assert [operation.target for operation in result.operations] == [LIVE_MEASUREMENT_POLICY, QA_LIVE_MEASUREMENT_ISSUE]
    assert result.issues[0].issue_kind == "policy_ambiguous"
    assert result.issues[0].reason is not None
    assert "7" in result.issues[0].reason


def test_policy_lookup_found_result_uses_effective_policy():
    lookup = PolicyLookupResult(status="found", policy=_policy(), effective_ts=_event().source_ts, matched_policy_versions=(7,))

    result = decide_trigger_actions(_event(), lookup)

    assert result.upsert_1min is True
    assert len(result.queue_jobs) == 3
    assert result.issues == ()


@pytest.mark.parametrize(
    ("policy", "resolution", "expected_points"),
    [
        (ExpectedPointsPolicy(native_cadence_seconds=5 * 60), RESOLUTION_15MIN, 3),
        (ExpectedPointsPolicy(native_cadence_seconds=15 * 60), RESOLUTION_15MIN, 1),
        (ExpectedPointsPolicy(native_cadence_seconds=15 * 60), RESOLUTION_1H, 4),
        (ExpectedPointsPolicy(native_cadence_seconds=60 * 60), RESOLUTION_1H, 1),
        (ExpectedPointsPolicy(), RESOLUTION_15MIN, 15),
        (ExpectedPointsPolicy(), RESOLUTION_1H, 60),
    ],
)
def test_expected_points_policy_derives_from_native_cadence(policy, resolution, expected_points):
    assert derive_expected_points(policy, resolution) == expected_points


def test_sub_minute_expected_points_require_explicit_or_approved_policy():
    with pytest.raises(ValueError, match="sub-minute cadence"):
        derive_expected_points(ExpectedPointsPolicy(native_cadence_seconds=30), RESOLUTION_15MIN)

    assert derive_expected_points(ExpectedPointsPolicy(native_cadence_seconds=30, expected_points_15min=30), RESOLUTION_15MIN) == 30
    assert derive_expected_points(ExpectedPointsPolicy(native_cadence_seconds=30, sub_minute_policy_approved=True), RESOLUTION_1H) == 120


def test_queue_key_idempotency_separates_job_kind_and_resolution():
    ts = datetime(2024, 1, 1, 0, 15, tzinfo=UTC)
    mean_15min = BucketQueueKey("H1.K11", "P", RESOLUTION_15MIN, ts, JOB_KIND_MEAN_ROLLUP, 7)
    peak_15min = BucketQueueKey("H1.K11", "P", RESOLUTION_15MIN, ts, JOB_KIND_PEAK_FEATURE, 7)
    mean_1h = BucketQueueKey("H1.K11", "P", RESOLUTION_1H, floor_to_resolution(ts, RESOLUTION_1H), JOB_KIND_MEAN_ROLLUP, 7)

    assert QUEUE_IDEMPOTENCY_FIELDS == ("meter_urn", "measurement", "resolution", "bucket_ts", "job_kind", "policy_version")
    assert mean_15min.as_tuple() != peak_15min.as_tuple()
    assert mean_15min.as_tuple() != mean_1h.as_tuple()
    with pytest.raises(ValueError, match="unsupported bucket queue job"):
        BucketQueueKey("H1.K11", "P", RESOLUTION_1H, ts, JOB_KIND_PEAK_FEATURE, 7)


def test_coverage_counts_zero_observed_but_null_and_nan_missing():
    values = [None, 0.0, 1.5, float("nan")]

    assert [is_observed_value(value) for value in values] == [False, True, True, False]
    coverage = compute_coverage(count_observed_points(values), expected_points=4)
    assert coverage.observed_points == 2
    assert coverage.expected_points == 4
    assert coverage.coverage_ratio == 0.5
    assert compute_coverage(5, expected_points=4).coverage_ratio == 1.0


def test_peak_feature_promotion_is_blocked_from_canonical():
    decision = guard_peak_feature_promotion(MART_PEAK_FEATURE_15MIN, CANONICAL_MEASUREMENT_15MIN)
    rolling_decision = guard_peak_feature_promotion(MART_PEAK_INPUT_15MIN, CANONICAL_MEASUREMENT_15MIN)

    assert decision.allowed is False
    assert decision.block_reasons == ("peak_feature_never_canonical", "peak_leakage_block")
    assert rolling_decision.allowed is False
    assert "peak_leakage_block" in rolling_decision.block_reasons


def test_live_mean_rollup_outputs_are_observed_mean_only_without_peak_fields():
    rollup_15min = live_mean_rollup_output_contract(RESOLUTION_15MIN)
    rollup_1h = live_mean_rollup_output_contract(RESOLUTION_1H)

    assert (rollup_15min.table, rollup_15min.resolution) == (LIVE_MEASUREMENT_15MIN, RESOLUTION_15MIN)
    assert (rollup_1h.table, rollup_1h.resolution) == (LIVE_MEASUREMENT_1H, RESOLUTION_1H)
    assert rollup_15min.value_semantic == LIVE_ROLLUP_VALUE_SEMANTIC
    assert rollup_1h.value_semantic == LIVE_ROLLUP_VALUE_SEMANTIC
    assert rollup_15min.peak_fields == ()
    assert rollup_1h.peak_fields == ()


@pytest.mark.parametrize(
    ("policy", "expected_reason"),
    [
        (LiveMeasurementPolicy("H1.K11", "P", 1, policy_kind="cumulative"), "cumulative_policy_blocked"),
        (LiveMeasurementPolicy("H1.K11", "P", 1, policy_kind="unknown"), "unknown_policy_blocked"),
        (LiveMeasurementPolicy("H1.K11", "P", 1, policy_kind="circular"), "circular_policy_blocked"),
        (LiveMeasurementPolicy("H1.K11", "P", 1, heterogeneous_native_cadence=True), "heterogeneous_native_cadence"),
        (LiveMeasurementPolicy("H1.K11", "P", 1, policy_kind="state_hold_last"), "state_hold_last_without_evidence"),
    ],
)
def test_blocked_policies_log_issue_without_1min_upsert_or_queue_jobs(policy, expected_reason):
    result = decide_trigger_actions(_event(), policy)

    assert result.upsert_1min is False
    assert result.queue_jobs == ()
    assert [operation.target for operation in result.operations] == [LIVE_MEASUREMENT_POLICY, QA_LIVE_MEASUREMENT_ISSUE]
    assert result.issues[0].issue_kind == "policy_block"
    assert result.issues[0].reason == expected_reason


def test_policy_qa_blocks_unfinalized_or_unsupported_policy_modes():
    assert trigger_policy_block_reasons(LiveMeasurementPolicy("H1.K11", "P", 1, policy_kind="state_hold_last")) == (
        "state_hold_last_without_evidence",
    )
    assert trigger_policy_block_reasons(LiveMeasurementPolicy("H1.K11", "P", 1, policy_kind="state_hold_last", state_hold_last_evidence="meter spec")) == ()
    assert trigger_policy_block_reasons(LiveMeasurementPolicy("H1.K11", "P", 1, policy_kind="cumulative")) == (
        "cumulative_policy_blocked",
    )
    assert trigger_policy_block_reasons(LiveMeasurementPolicy("H1.K11", "P", 1, heterogeneous_native_cadence=True)) == (
        "heterogeneous_native_cadence",
    )
