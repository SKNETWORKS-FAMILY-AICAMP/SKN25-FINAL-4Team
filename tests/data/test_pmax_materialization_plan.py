from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cms.data.pmax_materialization_plan import build_inventory_queries, build_packet, build_scope

BASE_TS = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)


def test_pmax_materialization_scope_is_bounded_to_current_serving_meters() -> None:
    scope = build_scope(base_ts=BASE_TS)

    assert scope.target_table == "mart.peak_feature_15min"
    assert scope.history_windows == 288
    assert scope.input_end_ts.isoformat() == "2023-12-31T23:45:00+00:00"
    assert scope.input_start_ts.isoformat() == "2023-12-29T00:00:00+00:00"
    assert set(scope.logical_meters) == {"H2.Z35x", "H2.Z36x", "V.Z81", "V.Z82"}
    assert set(scope.source_meters) == {"H2.Z351", "H2.Z361", "V.Z81", "V.Z82"}
    assert scope.measurements == ("P", "U1", "PF")


def test_pmax_materialization_inventory_queries_are_read_only() -> None:
    scope = build_scope(base_ts=BASE_TS)
    queries = build_inventory_queries(scope)

    assert {query.name for query in queries} == {
        "strict_live_observed_coverage",
        "null_lineage_coverage",
        "write_scope_estimate",
        "sample_null_lineage_rows",
    }
    for query in queries:
        normalized = query.sql.lower()
        assert normalized.startswith("select")
        assert "update " not in normalized
        assert "insert " not in normalized
        assert "delete " not in normalized
        assert "drop " not in normalized
        assert query.params["source_mode"] == "live_observed"


def test_pmax_materialization_packet_requires_separate_mart_write_gate() -> None:
    scope = build_scope(base_ts=BASE_TS)
    packet = build_packet(scope, {"write_scope_estimate": {"candidate_rows": 6912}})

    assert packet["packet_type"] == "pmax_strict_live_observed_materialization_approval"
    assert packet["target_table"] == "mart.peak_feature_15min"
    assert packet["current_verdict"] == "strict_blocked_until_live_observed_lineage_exists_for_all_required_windows"
    assert packet["forbidden_shortcut"] == "do_not_relabel_corrected_resampled_or_null_lineage_rows_as_live_observed_without_source_approval"
    gate = packet["write_gate"]
    assert isinstance(gate, dict)
    assert gate["requires_separate_production_mart_write_approval"] is True
    assert gate["canonical_write_allowed"] is False
    assert gate["destructive_cleanup_allowed"] is False
    assert gate["target_only"] == "mart.peak_feature_15min"


def test_pmax_materialization_scope_rejects_unsupported_meter() -> None:
    with pytest.raises(ValueError, match="unsupported logical_meters"):
        build_scope(base_ts=BASE_TS, logical_meters=("H2.Z35",))
