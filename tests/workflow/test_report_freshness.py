from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cms.workflow.report_freshness import build_model_result_freshness_queries, evaluate_model_result_freshness

REPORT_TS = datetime(2026, 6, 15, 9, 0, tzinfo=UTC)


def test_report_freshness_queries_target_exact_report_timestamp() -> None:
    query_set = build_model_result_freshness_queries(report_ts=REPORT_TS)

    assert query_set.params == {"report_ts": REPORT_TS}
    assert "FROM mart.pmax_forecast_15min" in query_set.queries["pmax_result_at_report_ts"]
    assert "WHERE base_ts = %(report_ts)s" in query_set.queries["pmax_result_at_report_ts"]
    assert "FROM mart.anomaly_warning_1h" in query_set.queries["anomaly_result_at_report_ts"]
    assert "WHERE forecast_origin_ts = %(report_ts)s" in query_set.queries["anomaly_result_at_report_ts"]
    assert "FROM qa.model_serving_evidence_packet" in query_set.queries["model_serving_evidence_at_report_ts"]
    assert "base_ts = %(report_ts)s" in query_set.queries["model_serving_evidence_at_report_ts"]
    assert "forecast_origin_ts = %(report_ts)s" in query_set.queries["model_serving_evidence_at_report_ts"]


def test_report_freshness_blocks_missing_model_lanes() -> None:
    status = evaluate_model_result_freshness(pmax_rows=0, anomaly_rows=0, evidence_rows=0)

    assert status.ok is False
    assert status.blocked is True
    assert status.block_reasons == (
        "missing_pmax_result_at_report_ts",
        "missing_anomaly_result_at_report_ts",
        "missing_model_serving_evidence_at_report_ts",
    )


def test_report_freshness_passes_when_both_model_lanes_and_evidence_exist() -> None:
    status = evaluate_model_result_freshness(pmax_rows=16, anomaly_rows=3, evidence_rows=1)

    assert status.ok is True
    assert status.blocked is False
    assert status.block_reasons == ()


def test_report_freshness_rejects_naive_timestamp_and_negative_counts() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        build_model_result_freshness_queries(report_ts=datetime(2026, 6, 15, 9, 0))
    with pytest.raises(ValueError, match="non-negative"):
        evaluate_model_result_freshness(pmax_rows=-1, anomaly_rows=1, evidence_rows=1)
