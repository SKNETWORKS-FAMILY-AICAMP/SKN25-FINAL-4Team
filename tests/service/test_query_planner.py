from __future__ import annotations

import pytest

from cms.service import api
from cms.service.query_planner import QueryPlanningError, assert_read_only_sql, make_query_plan


def test_korean_evidence_question_plans_parameterized_read_only_sql() -> None:
    plan = make_query_plan(
        {
            "text": "H2.Z64의 2023년 8월 평균 전력 사용량 보여줘",
            "limit": 500,
        }
    )

    assert plan.route == "evidence_answer"
    assert plan.table == "canonical.measurement_15min"
    assert plan.aggregation == "avg"
    assert plan.params["start_at"].isoformat() == "2023-08-01T00:00:00"
    assert plan.params["end_at"].isoformat() == "2023-09-01T00:00:00"
    assert plan.params["meter_urns"] == ("H2.Z64",)
    assert plan.params["measurement"] == "W"
    assert "%(meter_urns)s" in plan.sql
    assert "H2.Z64" not in plan.sql
    assert plan.writes_allowed is False
    assert plan.side_effects_executed is False


def test_context_can_select_1h_table_and_explicit_measurement() -> None:
    result = api.make_query_plan_payload(
        {
            "text": "show peak usage",
            "context": {
                "meter_urns": ["V.Z84"],
                "measurement": "P",
                "resolution": "1h",
                "start_at": "2023-01-01T00:00:00",
                "end_at": "2023-02-01T00:00:00",
            },
        }
    )

    assert result["route"] == "evidence_answer"
    assert result["table"] == "canonical.measurement_1h"
    assert result["aggregation"] == "max"
    assert result["params"]["measurement"] == "P"
    assert result["params"]["meter_urns"] == ["V.Z84"]
    assert result["dry_run"] is True
    assert result["qa_required"] is True


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"text": "delete canonical rows for H2.Z64 in 2023"}, "approval_required"),
        ({"text": "how does the cms work"}, "quick_answer"),
        ({"text": "show H2.Z64 power usage"}, "time window"),
        ({"text": "show power usage in 2023", "context": {"table": "reference.corrected_resampled_15min"}}, "unsupported canonical table"),
        ({"text": "show usage", "context": {"meter_urns": ["H2.Z64;drop"], "start_at": "2023-01-01", "end_at": "2023-01-02"}}, "unsafe meter_urn"),
    ],
)
def test_query_planner_rejects_unsafe_or_unbounded_requests(payload: dict, message: str) -> None:
    with pytest.raises(QueryPlanningError, match=message):
        make_query_plan(payload)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE canonical.measurement_15min SET value = 0",
        "SELECT * FROM canonical.measurement_15min; DROP TABLE canonical.measurement_15min",
        "SELECT * FROM reference.corrected_resampled_15min",
    ],
)
def test_read_only_sql_guard_rejects_forbidden_sql(sql: str) -> None:
    with pytest.raises(QueryPlanningError):
        assert_read_only_sql(sql)
