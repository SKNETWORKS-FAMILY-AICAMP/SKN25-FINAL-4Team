from __future__ import annotations

import pytest
from datetime import datetime, timezone

from cms.data.canonical_promotion_runner import (
    CANONICAL_PROMOTION_WRITE_ENV_FLAG,
    execute_canonical_promotion_command,
    make_canonical_promotion_command,
)


def test_canonical_promotion_command_links_checks_live_policy_live_rows_to_canonical() -> None:
    command = make_canonical_promotion_command(
        promotion_id="promo_20260615_0855",
        approval_id="approval_1",
        batch_size=25,
        min_coverage_ratio=0.8,
    )

    assert command.source_tables == ("live.measurement_15min", "live.measurement_1h")
    assert command.target_tables == ("canonical.measurement_15min", "canonical.measurement_1h")
    assert "FROM live.promotion_check AS pc" in command.sql
    assert "JOIN live.measurement_policy AS policy" in command.sql
    assert "policy.canonical_eligible = true" in command.sql
    assert "policy.cadence_group = 'native_1min'" in command.sql
    assert "policy.source_native_interval_seconds = 60" in command.sql
    assert "src.expected_points = 15" in command.sql
    assert "src.expected_points = 60" in command.sql
    assert "src.observed_points + src.gap_points" in command.sql
    assert "SELECT DISTINCT ON (src.bucket_ts, src.meter_urn, src.measurement)" in command.sql
    assert "ORDER BY src.bucket_ts, src.meter_urn, src.measurement, checks.check_id DESC" in command.sql
    assert "JOIN live.measurement_15min AS src" in command.sql
    assert "JOIN live.measurement_1h AS src" in command.sql
    assert "INSERT INTO canonical.measurement_15min" in command.sql
    assert "INSERT INTO canonical.measurement_1h" in command.sql
    assert "FOR UPDATE SKIP LOCKED" in command.sql
    assert "UPDATE live.promotion_check AS pc" in command.sql
    assert "'canonical_write', true" in command.sql
    assert "marked_promotion_check_count" in command.sql
    assert command.params["promotion_id"] == "promo_20260615_0855"
    assert command.params["approval_id"] == "approval_1"
    assert command.params["batch_size"] == 25
    assert command.params["min_coverage_ratio"] == 0.8
    assert command.params["max_bucket_ts"] is None


def test_canonical_promotion_can_be_capped_to_replay_clock_closed_buckets() -> None:
    max_bucket_ts = datetime(2023, 1, 1, 1, 38, tzinfo=timezone.utc)
    command = make_canonical_promotion_command(
        promotion_id="promo_replay_clock",
        approval_id="approval_1",
        max_bucket_ts=max_bucket_ts,
    )

    assert "pc.bucket_ts + interval '15 minutes' <= %(max_bucket_ts)s::timestamptz" in command.sql
    assert "pc.bucket_ts + interval '1 hour' <= %(max_bucket_ts)s::timestamptz" in command.sql
    assert command.params["max_bucket_ts"] == max_bucket_ts


def test_canonical_promotion_blocks_mart_reference_and_unknown_sources() -> None:
    for source_table in ("mart.peak_feature_15min", "mart.peak_input_15min", "reference.corrected_resampled_1h", "live.measurement_event"):
        with pytest.raises(ValueError):
            make_canonical_promotion_command(
                promotion_id="promo_1",
                approval_id="approval_1",
                source_tables=(source_table,),
            )


def test_canonical_promotion_runtime_requires_double_gate() -> None:
    command = make_canonical_promotion_command(promotion_id="promo_1", approval_id="approval_1")

    blocked = execute_canonical_promotion_command(command, allow_write=False, env={})
    assert blocked.ok is False
    assert blocked.attempted is False
    assert blocked.blocked is True
    assert blocked.errors == ("allow_write_required", f"{CANONICAL_PROMOTION_WRITE_ENV_FLAG}=1_required")

    blocked_env = execute_canonical_promotion_command(command, allow_write=True, env={})
    assert blocked_env.errors == (f"{CANONICAL_PROMOTION_WRITE_ENV_FLAG}=1_required",)
