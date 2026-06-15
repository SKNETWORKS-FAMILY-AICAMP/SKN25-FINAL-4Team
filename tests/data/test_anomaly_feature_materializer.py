from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cms.data.anomaly_feature_materializer import (
    ANOMALY_FEATURE_STRICT_SOURCE_TABLE,
    ANOMALY_FEATURE_WRITE_ENV_FLAG,
    execute_anomaly_feature_materialization_command,
    make_anomaly_feature_materialization_command,
)

START_TS = datetime(2026, 6, 15, 0, 0, tzinfo=UTC)
END_TS = START_TS + timedelta(hours=6)


def test_anomaly_feature_materializer_uses_existing_model_serving_boundary() -> None:
    command = make_anomaly_feature_materialization_command(
        start_ts=START_TS,
        end_ts=END_TS,
        meter_urns=("H1.K11",),
        batch_size=50,
    )

    assert command.source_table == "live.measurement_1h"
    assert command.source_table == ANOMALY_FEATURE_STRICT_SOURCE_TABLE
    assert command.target_table == "mart.anomaly_feature_1h"
    assert command.source_mode == "live_observed"
    assert "FROM live.measurement_1h" in command.sql
    assert "INSERT INTO mart.anomaly_feature_1h" in command.sql
    assert "max(src.value) FILTER (WHERE src.measurement = 'P') AS p_value" in command.sql
    assert "max(src.value) FILTER (WHERE src.measurement = 'U1') AS u1_value" in command.sql
    assert "max(src.value) FILTER (WHERE src.measurement = 'PF') AS pf_value" in command.sql
    assert "'policy_id', src.policy_id" in command.sql
    assert "source_mode" in command.sql
    assert command.params["start_ts"] == START_TS
    assert command.params["end_ts"] == END_TS
    assert command.params["meter_0"] == "H1.K11"


def test_anomaly_feature_materializer_allows_canonical_approved_observed_source_with_promotion_ref() -> None:
    command = make_anomaly_feature_materialization_command(
        start_ts=START_TS,
        end_ts=END_TS,
        source_table="canonical.measurement_1h",
        source_mode="live_observed",
    )

    assert "FROM canonical.measurement_1h" in command.sql
    assert "'promotion_id', NULLIF(src.promotion_id, '')" in command.sql
    assert "'policy_id', src.policy_id" not in command.sql


def test_anomaly_feature_materializer_rejects_reference_mart_and_bad_time_windows() -> None:
    with pytest.raises(ValueError, match="reference"):
        make_anomaly_feature_materialization_command(start_ts=START_TS, end_ts=END_TS, source_table="reference.corrected_resampled_1h")
    with pytest.raises(ValueError, match="mart"):
        make_anomaly_feature_materialization_command(start_ts=START_TS, end_ts=END_TS, source_table="mart.anomaly_feature_1h")
    with pytest.raises(ValueError, match="end_ts"):
        make_anomaly_feature_materialization_command(start_ts=END_TS, end_ts=START_TS)


def test_anomaly_feature_materializer_runtime_requires_double_gate() -> None:
    command = make_anomaly_feature_materialization_command(start_ts=START_TS, end_ts=END_TS)

    blocked = execute_anomaly_feature_materialization_command(command, allow_write=False, env={})
    assert blocked.ok is False
    assert blocked.attempted is False
    assert blocked.blocked is True
    assert blocked.errors == ("allow_write_required", f"{ANOMALY_FEATURE_WRITE_ENV_FLAG}=1_required")

    blocked_env = execute_anomaly_feature_materialization_command(command, allow_write=True, env={})
    assert blocked_env.errors == (f"{ANOMALY_FEATURE_WRITE_ENV_FLAG}=1_required",)
