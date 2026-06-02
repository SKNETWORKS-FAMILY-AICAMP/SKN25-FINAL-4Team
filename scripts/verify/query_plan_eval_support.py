"""Mock evaluation support for FastAPI query-plan SQL contracts.

This module is intentionally DB-free. It evaluates the current planner contract
against in-memory canonical-like rows so API SQL generation can be regression
checked without writing mock rows into production or scratch databases.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class QueryEvalCase:
    case_id: str
    description: str
    payload: dict[str, Any]
    expected_status: int = 200
    expected_route: str | None = "evidence_answer"
    expected_table: str | None = None
    expected_aggregation: str | None = None
    expected_params: dict[str, Any] | None = None
    expected_rows: list[dict[str, Any]] | None = None
    expected_error: str | None = None


MOCK_CANONICAL_ROWS: tuple[dict[str, Any], ...] = (
    # 15min rows for Korean average and raw-point cases.
    {
        "table": "canonical.measurement_15min",
        "ts": datetime.fromisoformat("2023-08-01T00:00:00"),
        "meter_urn": "H2.Z64",
        "measurement": "W",
        "value": Decimal("10.0"),
        "coverage_ratio": Decimal("0.95"),
    },
    {
        "table": "canonical.measurement_15min",
        "ts": datetime.fromisoformat("2023-08-01T00:15:00"),
        "meter_urn": "H2.Z64",
        "measurement": "W",
        "value": Decimal("20.0"),
        "coverage_ratio": Decimal("0.95"),
    },
    {
        "table": "canonical.measurement_15min",
        "ts": datetime.fromisoformat("2023-08-01T00:30:00"),
        "meter_urn": "H2.Z64",
        "measurement": "W",
        "value": Decimal("30.0"),
        "coverage_ratio": Decimal("0.95"),
    },
    # Distractors for measurement, boundary, and meter filters.
    {
        "table": "canonical.measurement_15min",
        "ts": datetime.fromisoformat("2023-08-01T00:00:00"),
        "meter_urn": "H2.Z64",
        "measurement": "P",
        "value": Decimal("999.0"),
        "coverage_ratio": Decimal("1.0"),
    },
    {
        "table": "canonical.measurement_15min",
        "ts": datetime.fromisoformat("2023-09-01T00:00:00"),
        "meter_urn": "H2.Z64",
        "measurement": "W",
        "value": Decimal("999.0"),
        "coverage_ratio": Decimal("1.0"),
    },
    {
        "table": "canonical.measurement_15min",
        "ts": datetime.fromisoformat("2023-08-01T00:00:00"),
        "meter_urn": "H1.K11",
        "measurement": "W",
        "value": Decimal("5.0"),
        "coverage_ratio": Decimal("0.8"),
    },
    {
        "table": "canonical.measurement_15min",
        "ts": datetime.fromisoformat("2023-08-01T00:15:00"),
        "meter_urn": "H1.K11",
        "measurement": "W",
        "value": Decimal("6.0"),
        "coverage_ratio": Decimal("0.8"),
    },
    # 1h rows for explicit context and max aggregation.
    {
        "table": "canonical.measurement_1h",
        "ts": datetime.fromisoformat("2023-08-01T00:00:00"),
        "meter_urn": "V.Z84",
        "measurement": "P",
        "value": Decimal("11.0"),
        "coverage_ratio": Decimal("0.75"),
    },
    {
        "table": "canonical.measurement_1h",
        "ts": datetime.fromisoformat("2023-08-01T01:00:00"),
        "meter_urn": "V.Z84",
        "measurement": "P",
        "value": Decimal("19.0"),
        "coverage_ratio": Decimal("0.90"),
    },
    {
        "table": "canonical.measurement_1h",
        "ts": datetime.fromisoformat("2023-08-01T00:00:00"),
        "meter_urn": "V.Z84",
        "measurement": "W",
        "value": Decimal("999.0"),
        "coverage_ratio": Decimal("1.0"),
    },
    # 1min rows for explicit 1min resolution checks.
    {
        "table": "canonical.measurement_1min",
        "ts": datetime.fromisoformat("2023-08-01T00:00:00"),
        "meter_urn": "H2.Z64",
        "measurement": "W",
        "value": Decimal("2.0"),
        "coverage_ratio": Decimal("0.9"),
    },
    {
        "table": "canonical.measurement_1min",
        "ts": datetime.fromisoformat("2023-08-01T00:01:00"),
        "meter_urn": "H2.Z64",
        "measurement": "W",
        "value": Decimal("4.0"),
        "coverage_ratio": Decimal("0.9"),
    },
    {
        "table": "canonical.measurement_1min",
        "ts": datetime.fromisoformat("2023-08-01T00:02:00"),
        "meter_urn": "H2.Z64",
        "measurement": "W",
        "value": Decimal("6.0"),
        "coverage_ratio": Decimal("0.9"),
    },
)


QUERY_EVAL_CASES: tuple[QueryEvalCase, ...] = (
    QueryEvalCase(
        case_id="QP-AVG-001",
        description="Korean month average power query uses 15min canonical data and parameterized meter/measurement filters.",
        payload={"text": "H2.Z64의 2023년 8월 평균 전력 사용량", "limit": 10},
        expected_table="canonical.measurement_15min",
        expected_aggregation="avg",
        expected_params={
            "start_at": "2023-08-01T00:00:00",
            "end_at": "2023-09-01T00:00:00",
            "meter_urns": ["H2.Z64"],
            "measurement": "W",
            "limit": 10,
        },
        expected_rows=[{"meter_urn": "H2.Z64", "measurement": "W", "value": "20.0", "coverage_ratio": "0.95", "bucket_count": 3}],
    ),
    QueryEvalCase(
        case_id="QP-MAX-002",
        description="Explicit context can select 1h table and pressure measurement while the prompt selects max/peak aggregation.",
        payload={
            "text": "V.Z84의 2023-08-01 최대 피크 확인",
            "context": {"measurement": "P", "resolution": "1h"},
            "limit": 10,
        },
        expected_table="canonical.measurement_1h",
        expected_aggregation="max",
        expected_params={
            "start_at": "2023-08-01T00:00:00",
            "end_at": "2023-08-02T00:00:00",
            "meter_urns": ["V.Z84"],
            "measurement": "P",
            "limit": 10,
        },
        expected_rows=[{"meter_urn": "V.Z84", "measurement": "P", "value": "19.0", "coverage_ratio": "0.825", "bucket_count": 2}],
    ),
    QueryEvalCase(
        case_id="QP-SUM-003",
        description="Korean monthly total energy wording maps to SUM(value) on W measurement with meter filtering.",
        payload={"text": "H1.K11의 2023-08 합계 에너지 사용량", "limit": 10},
        expected_table="canonical.measurement_15min",
        expected_aggregation="sum",
        expected_params={
            "start_at": "2023-08-01T00:00:00",
            "end_at": "2023-09-01T00:00:00",
            "meter_urns": ["H1.K11"],
            "measurement": "W",
            "limit": 10,
        },
        expected_rows=[{"meter_urn": "H1.K11", "measurement": "W", "value": "11.0", "coverage_ratio": "0.8", "bucket_count": 2}],
    ),
    QueryEvalCase(
        case_id="QP-RAW-004",
        description="Raw evidence request returns ordered raw rows rather than aggregate rows.",
        payload={"text": "H2.Z64 2023-08-01 전력 사용량 원자료", "limit": 10},
        expected_table="canonical.measurement_15min",
        expected_aggregation="raw_points",
        expected_params={
            "start_at": "2023-08-01T00:00:00",
            "end_at": "2023-08-02T00:00:00",
            "meter_urns": ["H2.Z64"],
            "measurement": "W",
            "limit": 10,
        },
        expected_rows=[
            {"ts": "2023-08-01T00:00:00", "meter_urn": "H2.Z64", "measurement": "W", "value": "10.0", "coverage_ratio": "0.95"},
            {"ts": "2023-08-01T00:15:00", "meter_urn": "H2.Z64", "measurement": "W", "value": "20.0", "coverage_ratio": "0.95"},
            {"ts": "2023-08-01T00:30:00", "meter_urn": "H2.Z64", "measurement": "W", "value": "30.0", "coverage_ratio": "0.95"},
        ],
    ),
    QueryEvalCase(
        case_id="QP-1MIN-005",
        description="Explicit 1min wording selects canonical.measurement_1min and evaluates aggregate semantics on 1min mock rows.",
        payload={"text": "H2.Z64 2023-08-01 1분 평균 전력", "limit": 10},
        expected_table="canonical.measurement_1min",
        expected_aggregation="avg",
        expected_params={
            "start_at": "2023-08-01T00:00:00",
            "end_at": "2023-08-02T00:00:00",
            "meter_urns": ["H2.Z64"],
            "measurement": "W",
            "limit": 10,
        },
        expected_rows=[{"meter_urn": "H2.Z64", "measurement": "W", "value": "4.0", "coverage_ratio": "0.9", "bucket_count": 3}],
    ),
    QueryEvalCase(
        case_id="QP-REJECT-006",
        description="Destructive wording is rejected by the FastAPI boundary and does not produce SQL.",
        payload={"text": "delete canonical rows for H2.Z64 in 2023"},
        expected_status=400,
        expected_route=None,
        expected_error="approval_required",
    ),
    QueryEvalCase(
        case_id="QP-REJECT-007",
        description="Reference corrected/resampled table cannot be selected as service SQL truth.",
        payload={
            "text": "show H2.Z64 2023-08 평균 전력 사용량",
            "context": {"table": "reference.corrected_resampled_15min"},
        },
        expected_status=400,
        expected_route=None,
        expected_error="unsupported canonical table",
    ),
)


def run_eval_case(client: Any, case: QueryEvalCase) -> dict[str, Any]:
    response = client.post("/query/plan", json=case.payload)
    record: dict[str, Any] = {
        "case_id": case.case_id,
        "description": case.description,
        "prompt": case.payload.get("text"),
        "expected_status": case.expected_status,
        "actual_status": response.status_code,
        "status": "pass",
        "failures": [],
    }

    if response.status_code != case.expected_status:
        record["failures"].append(f"expected HTTP {case.expected_status}, got {response.status_code}")

    body = response.json()
    record["response"] = body

    if case.expected_status != 200:
        detail = str(body.get("detail", body))
        record["error_detail"] = detail
        if case.expected_error and case.expected_error not in detail:
            record["failures"].append(f"expected error containing {case.expected_error!r}, got {detail!r}")
        return _finalize(record)

    sql = body.get("sql", "")
    params = body.get("params", {})
    record.update(
        {
            "route": body.get("route"),
            "table": body.get("table"),
            "aggregation": body.get("aggregation"),
            "params": params,
            "sql_shape": _sql_shape(sql),
        }
    )

    _expect_equal(record, "route", body.get("route"), case.expected_route)
    _expect_equal(record, "table", body.get("table"), case.expected_table)
    _expect_equal(record, "aggregation", body.get("aggregation"), case.expected_aggregation)
    _expect_equal(record, "writes_allowed", body.get("writes_allowed"), False)
    _expect_equal(record, "side_effects_executed", body.get("side_effects_executed"), False)

    if not str(sql).strip().lower().startswith("select "):
        record["failures"].append("SQL does not start with SELECT")
    if ";" in str(sql):
        record["failures"].append("SQL contains semicolon")
    if "FROM canonical.measurement_" not in str(sql):
        record["failures"].append("SQL does not read from canonical measurement table")

    for key, expected in (case.expected_params or {}).items():
        _expect_equal(record, f"params.{key}", params.get(key), expected)

    for meter in params.get("meter_urns", []):
        if meter in str(sql):
            record["failures"].append(f"meter literal {meter!r} leaked into SQL")
    if "measurement" in params and f"'{params['measurement']}'" in str(sql):
        record["failures"].append("measurement value literal leaked into SQL")

    actual_rows = execute_mock_plan(sql, params, MOCK_CANONICAL_ROWS)
    record["actual_rows"] = actual_rows
    record["expected_rows"] = case.expected_rows or []
    if actual_rows != (case.expected_rows or []):
        record["failures"].append("mock result rows differ from expected rows")

    return _finalize(record)


def execute_mock_plan(sql: str, params: dict[str, Any], rows: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    """Evaluate the current read-only SQL contract against in-memory rows.

    This intentionally is not a general SQL parser. It checks and evaluates the
    exact plan shape produced by ``cms.service.query_planner``.
    """

    table = _extract_table(sql)
    start_at = _parse_dt(params["start_at"])
    end_at = _parse_dt(params["end_at"])
    meter_urns = set(params.get("meter_urns", []))
    measurement = params.get("measurement")
    limit = int(params["limit"])

    filtered = [
        row
        for row in rows
        if row["table"] == table
        and start_at <= row["ts"] < end_at
        and (not meter_urns or row["meter_urn"] in meter_urns)
        and (measurement is None or row["measurement"] == measurement)
    ]

    if "GROUP BY meter_urn, measurement" not in sql:
        return [
            {
                "ts": row["ts"].isoformat(),
                "meter_urn": row["meter_urn"],
                "measurement": row["measurement"],
                "value": str(row["value"]),
                "coverage_ratio": str(row["coverage_ratio"]),
            }
            for row in sorted(filtered, key=lambda item: (item["ts"], item["meter_urn"], item["measurement"]))[:limit]
        ]

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in filtered:
        grouped.setdefault((row["meter_urn"], row["measurement"]), []).append(row)

    output: list[dict[str, Any]] = []
    for (meter_urn, measurement_name), group in sorted(grouped.items()):
        values = [row["value"] for row in group]
        if "AVG(value)" in sql:
            value = sum(values, Decimal("0")) / Decimal(len(values))
        elif "MAX(value)" in sql:
            value = max(values)
        elif "SUM(value)" in sql:
            value = sum(values, Decimal("0"))
        else:
            raise AssertionError(f"unsupported aggregate SQL: {sql}")
        coverage = sum((row["coverage_ratio"] for row in group), Decimal("0")) / Decimal(len(group))
        output.append(
            {
                "meter_urn": meter_urn,
                "measurement": measurement_name,
                "value": str(value),
                "coverage_ratio": str(coverage),
                "bucket_count": len(group),
            }
        )
    return output[:limit]


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(1 for record in records if record["status"] == "pass")
    failed = len(records) - passed
    return {"total": len(records), "passed": passed, "failed": failed, "status": "pass" if failed == 0 else "fail"}


def render_markdown_report(records: list[dict[str, Any]]) -> str:
    summary = summarize_records(records)
    lines = [
        "# Query Plan Evaluation Results",
        "",
        "**Date:** 2026-06-01",
        "**Scope:** FastAPI `/query/plan` deterministic SQL planner, mock canonical data only",
        "**DB writes:** none",
        "",
        "## Summary",
        "",
        f"- Total cases: {summary['total']}",
        f"- Passed: {summary['passed']}",
        f"- Failed: {summary['failed']}",
        f"- Overall status: `{summary['status']}`",
        "",
        "## Case Records",
        "",
        "| Case | Status | Prompt | Table | Aggregation | Result |",
        "|---|---|---|---|---|---|",
    ]
    for record in records:
        response = record.get("response", {})
        result = record.get("actual_rows") or record.get("error_detail") or ""
        lines.append(
            "| "
            + " | ".join(
                [
                    str(record["case_id"]),
                    str(record["status"]),
                    _md(str(record.get("prompt", ""))),
                    _md(str(record.get("table") or response.get("table") or "")),
                    _md(str(record.get("aggregation") or response.get("aggregation") or "")),
                    _md(str(result)),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- SQL is generated and validated as read-only, but not executed against PostgreSQL by the API.",
            "- Mock execution validates planner semantics without inserting test rows into AWS/production databases.",
            "- RAGAS is not used here because this layer is deterministic SQL planning, not RAG answer generation.",
        ]
    )
    return "\n".join(lines) + "\n"


def _extract_table(sql: str) -> str:
    marker = "FROM "
    if marker not in sql:
        raise AssertionError("SQL has no FROM clause")
    return sql.split(marker, 1)[1].split("\n", 1)[0].strip()


def _parse_dt(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    assert isinstance(value, str)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _expect_equal(record: dict[str, Any], label: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        record["failures"].append(f"{label}: expected {expected!r}, got {actual!r}")


def _sql_shape(sql: str) -> dict[str, bool]:
    return {
        "select_only": str(sql).strip().lower().startswith("select "),
        "has_semicolon": ";" in str(sql),
        "uses_named_params": "%(start_at)s" in str(sql) and "%(end_at)s" in str(sql),
        "uses_canonical_measurement": "FROM canonical.measurement_" in str(sql),
    }


def _finalize(record: dict[str, Any]) -> dict[str, Any]:
    record["status"] = "fail" if record["failures"] else "pass"
    return record


def _md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")
