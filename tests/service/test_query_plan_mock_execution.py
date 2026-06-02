from __future__ import annotations

import importlib.util
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest


MOCK_CANONICAL_ROWS = (
    {
        "ts": datetime.fromisoformat("2023-08-01T00:00:00"),
        "meter_urn": "H2.Z64",
        "measurement": "W",
        "value": Decimal("10.0"),
        "coverage_ratio": Decimal("0.95"),
    },
    {
        "ts": datetime.fromisoformat("2023-08-01T00:15:00"),
        "meter_urn": "H2.Z64",
        "measurement": "W",
        "value": Decimal("20.0"),
        "coverage_ratio": Decimal("0.95"),
    },
    {
        "ts": datetime.fromisoformat("2023-08-01T00:30:00"),
        "meter_urn": "H2.Z64",
        "measurement": "W",
        "value": Decimal("30.0"),
        "coverage_ratio": Decimal("0.95"),
    },
    {
        "ts": datetime.fromisoformat("2023-08-01T00:00:00"),
        "meter_urn": "H2.Z64",
        "measurement": "P",
        "value": Decimal("999.0"),
        "coverage_ratio": Decimal("1.00"),
    },
    {
        "ts": datetime.fromisoformat("2023-09-01T00:00:00"),
        "meter_urn": "H2.Z64",
        "measurement": "W",
        "value": Decimal("999.0"),
        "coverage_ratio": Decimal("1.00"),
    },
    {
        "ts": datetime.fromisoformat("2023-08-01T00:00:00"),
        "meter_urn": "H1.K11",
        "measurement": "W",
        "value": Decimal("999.0"),
        "coverage_ratio": Decimal("1.00"),
    },
)


def test_fastapi_query_plan_matches_mock_canonical_rows() -> None:
    if importlib.util.find_spec("fastapi") is None:
        pytest.skip("fastapi is required for FastAPI query-plan mock execution smoke")
    if importlib.util.find_spec("httpx") is None:
        pytest.skip("httpx is required for FastAPI TestClient")

    from fastapi.testclient import TestClient

    from cms.service import api

    client = TestClient(api.create_app())
    response = client.post("/query/plan", json={"text": "H2.Z64의 2023년 8월 평균 전력 사용량", "limit": 10})

    assert response.status_code == 200
    payload = response.json()
    assert payload["route"] == "evidence_answer"
    assert payload["table"] == "canonical.measurement_15min"
    assert payload["aggregation"] == "avg"
    assert payload["writes_allowed"] is False
    assert payload["side_effects_executed"] is False
    assert "H2.Z64" not in payload["sql"]
    assert "%(meter_urns)s" in payload["sql"]
    assert "FROM canonical.measurement_15min" in payload["sql"]
    assert "AVG(value)" in payload["sql"]

    rows = _execute_mock_plan(payload["sql"], payload["params"], MOCK_CANONICAL_ROWS)

    assert rows == [("H2.Z64", "W", Decimal("20.0"), Decimal("0.95"), 3)]


def _execute_mock_plan(sql: str, params: dict[str, Any], rows: tuple[dict[str, Any], ...]) -> list[tuple[str, str, Decimal, Decimal, int]]:
    """Evaluate the current read-only aggregate plan against in-memory mock rows.

    This is not a SQL parser. It is a narrow smoke for the planner contract:
    whitelisted canonical table, parameterized time/meter/measurement filters,
    and grouped AVG output shape.
    """

    assert sql.startswith("SELECT ")
    assert ";" not in sql
    assert "FROM canonical.measurement_15min" in sql
    assert "WHERE ts >= %(start_at)s" in sql
    assert "ts < %(end_at)s" in sql
    assert "meter_urn = ANY(%(meter_urns)s)" in sql
    assert "measurement = %(measurement)s" in sql
    assert "GROUP BY meter_urn, measurement" in sql

    start_at = _parse_dt(params["start_at"])
    end_at = _parse_dt(params["end_at"])
    meter_urns = set(params["meter_urns"])
    measurement = params["measurement"]

    filtered = [
        row
        for row in rows
        if start_at <= row["ts"] < end_at and row["meter_urn"] in meter_urns and row["measurement"] == measurement
    ]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in filtered:
        grouped.setdefault((row["meter_urn"], row["measurement"]), []).append(row)

    output: list[tuple[str, str, Decimal, Decimal, int]] = []
    for (meter_urn, metric), group in sorted(grouped.items()):
        value = sum((row["value"] for row in group), Decimal("0")) / Decimal(len(group))
        coverage = sum((row["coverage_ratio"] for row in group), Decimal("0")) / Decimal(len(group))
        output.append((meter_urn, metric, value, coverage, len(group)))
    return output[: int(params["limit"])]


def _parse_dt(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    assert isinstance(value, str)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
