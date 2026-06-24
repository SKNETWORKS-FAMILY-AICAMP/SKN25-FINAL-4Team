"""Deterministic read-only SQL planner for evidence-backed chat/API queries.

The planner is intentionally small and import-safe. It does not connect to a
database, inspect schemas, call an LLM, or execute SQL. Its job is to turn a
bounded evidence request into a parameterized SELECT plan against approved
canonical measurement tables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, cast

from cms.contracts.agent import classify_route
from cms.contracts.core import (
    CANONICAL_MEASUREMENT_1H,
    CANONICAL_MEASUREMENT_1MIN,
    CANONICAL_MEASUREMENT_15MIN,
    CANONICAL_SOURCE_TABLES,
    AgentRequest,
    SourceTable,
)

Aggregation = Literal["raw_points", "avg", "max", "sum"]

DEFAULT_LIMIT = 1_000
MAX_LIMIT = 10_000
READ_COLUMNS = (
    "ts",
    "meter_urn",
    "measurement",
    "value",
    "unit",
    "coverage_ratio",
    "quality_flag",
    "source_run_id",
    "promotion_id",
)
FORBIDDEN_SQL_WORDS = frozenset(
    {
        "alter",
        "call",
        "copy",
        "create",
        "delete",
        "drop",
        "execute",
        "grant",
        "insert",
        "merge",
        "revoke",
        "truncate",
        "update",
    }
)

_METER_RE = re.compile(r"(?<![A-Za-z0-9_.])(?:H\d|V|WeatherStation)(?:\.[A-Za-z0-9]+)+(?![A-Za-z0-9_.])")
_ISO_DAY_RE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_ISO_MONTH_RE = re.compile(r"(?<!\d)(\d{4})-(\d{2})(?!-\d)")
_KOREAN_MONTH_RE = re.compile(r"(?<!\d)(\d{4})\s*년\s*(\d{1,2})\s*월")
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")


class QueryPlanningError(ValueError):
    """Raised when a user request cannot be converted into a safe read plan."""


@dataclass(frozen=True)
class QueryPlan:
    """A parameterized, read-only evidence query plan."""

    route: str
    question: str
    table: SourceTable
    sql: str
    params: dict[str, object]
    aggregation: Aggregation = "raw_points"
    selected_columns: tuple[str, ...] = READ_COLUMNS
    evidence_level: str = "query_plan_only"
    qa_required: bool = True
    dry_run: bool = True
    side_effects_executed: bool = False
    writes_allowed: bool = False
    assumptions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = field(default_factory=lambda: ("SQL is planned but not executed by this endpoint.",))


def make_query_plan(payload: dict[str, Any]) -> QueryPlan:
    """Build a safe read-only SQL plan from request text plus optional context."""

    text = _required_text(payload.get("text"), "text")
    context = payload.get("context") or {}
    if not isinstance(context, dict):
        raise QueryPlanningError("context must be an object")

    request = AgentRequest(text=text, route_hint=payload.get("route_hint"), user_id=payload.get("user_id"), context=context)
    decision = classify_route(request)
    if decision.route == "approval_required":
        raise QueryPlanningError("approval_required requests cannot produce executable SQL")
    if decision.route in {"quick_answer", "report_shell", "needs_job"}:
        raise QueryPlanningError(f"{decision.route} is not a direct evidence SQL route")

    start_at, end_at, time_assumption = _resolve_window(text, context)
    meter_urns = _resolve_meter_urns(text, context)
    measurement, measurement_assumption = _resolve_measurement(text, context)
    table = _resolve_table(text, context)
    aggregation = _resolve_aggregation(text, context)
    limit = _resolve_limit(payload.get("limit", context.get("limit", DEFAULT_LIMIT)))

    sql = render_select_sql(table=table, aggregation=aggregation, has_meter_filter=bool(meter_urns), has_measurement_filter=measurement is not None)
    assert_read_only_sql(sql)
    params: dict[str, object] = {
        "start_at": start_at,
        "end_at": end_at,
        "limit": limit,
    }
    if meter_urns:
        params["meter_urns"] = meter_urns
    if measurement is not None:
        params["measurement"] = measurement

    assumptions = tuple(item for item in (time_assumption, measurement_assumption) if item)
    return QueryPlan(
        route=decision.route,
        question=text,
        table=table,
        sql=sql,
        params=params,
        aggregation=aggregation,
        assumptions=assumptions,
    )


def render_select_sql(*, table: SourceTable, aggregation: Aggregation, has_meter_filter: bool, has_measurement_filter: bool) -> str:
    """Render SQL from whitelisted identifiers and named parameters only."""

    if table not in CANONICAL_SOURCE_TABLES:
        raise QueryPlanningError(f"unsupported canonical table: {table}")
    where_parts = ["ts >= %(start_at)s", "ts < %(end_at)s"]
    if has_meter_filter:
        where_parts.append("meter_urn = ANY(%(meter_urns)s)")
    if has_measurement_filter:
        where_parts.append("measurement = %(measurement)s")
    where_sql = "\n  AND ".join(where_parts)

    if aggregation == "raw_points":
        return (
            "SELECT ts, meter_urn, measurement, value, unit, coverage_ratio, quality_flag, source_run_id, promotion_id\n"
            f"FROM {table}\n"
            f"WHERE {where_sql}\n"
            "ORDER BY ts ASC, meter_urn ASC, measurement ASC\n"
            "LIMIT %(limit)s"
        )

    value_expr = {
        "avg": "AVG(value)",
        "max": "MAX(value)",
        "sum": "SUM(value)",
    }[aggregation]
    return (
        f"SELECT meter_urn, measurement, {value_expr} AS value, AVG(coverage_ratio) AS coverage_ratio, COUNT(*) AS bucket_count\n"
        f"FROM {table}\n"
        f"WHERE {where_sql}\n"
        "GROUP BY meter_urn, measurement\n"
        "ORDER BY meter_urn ASC, measurement ASC\n"
        "LIMIT %(limit)s"
    )


def assert_read_only_sql(sql: str) -> None:
    """Reject SQL outside the supported SELECT-only contract."""

    normalized = sql.strip().lower()
    if not normalized.startswith("select "):
        raise QueryPlanningError("only SELECT statements are allowed")
    if ";" in normalized:
        raise QueryPlanningError("multiple SQL statements are not allowed")
    words = set(re.findall(r"[a-z_]+", normalized))
    forbidden = words & FORBIDDEN_SQL_WORDS
    if forbidden:
        raise QueryPlanningError(f"forbidden SQL operation: {sorted(forbidden)[0]}")
    if not any(f"from {table}" in normalized for table in CANONICAL_SOURCE_TABLES):
        raise QueryPlanningError("query must read from an approved canonical measurement table")


def _resolve_window(text: str, context: dict[str, Any]) -> tuple[datetime, datetime, str | None]:
    start_raw = context.get("start_at")
    end_raw = context.get("end_at")
    if start_raw is not None and end_raw is not None:
        start_at = _parse_datetime(start_raw, "start_at")
        end_at = _parse_datetime(end_raw, "end_at")
        if start_at >= end_at:
            raise QueryPlanningError("start_at must be before end_at")
        return start_at, end_at, None

    days = _ISO_DAY_RE.findall(text)
    if len(days) >= 2:
        start_at = _date_from_parts(days[0])
        end_at = _date_from_parts(days[1])
        if start_at >= end_at:
            raise QueryPlanningError("first date must be before second date")
        return start_at, end_at, "interpreted two dates as a half-open [start, end) window"
    if len(days) == 1:
        start_at = _date_from_parts(days[0])
        return start_at, _add_month_or_day(start_at, days=1), "interpreted one date as a single-day window"

    korean_month = _KOREAN_MONTH_RE.search(text)
    if korean_month:
        start_at = datetime(int(korean_month.group(1)), int(korean_month.group(2)), 1)
        return start_at, _add_month_or_day(start_at, months=1), "interpreted Korean year-month as a calendar-month window"

    iso_month = _ISO_MONTH_RE.search(text)
    if iso_month:
        start_at = datetime(int(iso_month.group(1)), int(iso_month.group(2)), 1)
        return start_at, _add_month_or_day(start_at, months=1), "interpreted year-month as a calendar-month window"

    year = _YEAR_RE.search(text)
    if year:
        start_at = datetime(int(year.group(1)), 1, 1)
        return start_at, datetime(start_at.year + 1, 1, 1), "interpreted year as a calendar-year window"

    raise QueryPlanningError("bounded time window is required for evidence SQL")


def _resolve_meter_urns(text: str, context: dict[str, Any]) -> tuple[str, ...]:
    raw = context.get("meter_urns", context.get("meters"))
    if raw:
        if isinstance(raw, str):
            candidates = (raw,)
        elif isinstance(raw, (list, tuple)):
            candidates = tuple(str(item) for item in raw)
        else:
            raise QueryPlanningError("meter_urns must be a string or list")
    else:
        candidates = tuple(_METER_RE.findall(text))
    return tuple(dict.fromkeys(_validate_meter_urn(item) for item in candidates))


def _resolve_measurement(text: str, context: dict[str, Any]) -> tuple[str | None, str | None]:
    raw = context.get("measurement", context.get("metric"))
    if raw is not None:
        return _validate_measurement(str(raw)), None
    lowered = text.lower()
    if any(token in lowered for token in ("temperature", "기온", "온도")):
        return "Ta", "inferred measurement=Ta from temperature wording"
    if any(token in lowered for token in ("power", "energy", "usage", "consumption", "전력", "사용량", "소비")):
        return "W", "inferred measurement=W from energy/power wording"
    return None, "no measurement filter inferred; query will return all measurements in scope"


def _resolve_table(text: str, context: dict[str, Any]) -> SourceTable:
    table = context.get("table")
    if table is not None:
        if table not in CANONICAL_SOURCE_TABLES:
            raise QueryPlanningError(f"unsupported canonical table: {table}")
        return cast(SourceTable, table)
    resolution = str(context.get("resolution", "")).lower()
    lowered = text.lower()
    if resolution in {"1min", "1m"} or "1min" in lowered or "1분" in lowered:
        return CANONICAL_MEASUREMENT_1MIN
    if resolution in {"1h", "hour", "hourly"} or "1h" in lowered or "1시간" in lowered or "시간별" in lowered:
        return CANONICAL_MEASUREMENT_1H
    return CANONICAL_MEASUREMENT_15MIN


def _resolve_aggregation(text: str, context: dict[str, Any]) -> Aggregation:
    raw = context.get("aggregation")
    if raw:
        if raw not in {"raw_points", "avg", "max", "sum"}:
            raise QueryPlanningError(f"unsupported aggregation: {raw}")
        return cast(Aggregation, raw)
    lowered = text.lower()
    if any(token in lowered for token in ("average", "avg", "평균")):
        return "avg"
    if any(token in lowered for token in ("peak", "max", "최대", "피크")):
        return "max"
    if any(token in lowered for token in ("total", "sum", "합계", "총")):
        return "sum"
    return "raw_points"


def _resolve_limit(value: object) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise QueryPlanningError("limit must be an integer") from exc
    if limit <= 0:
        raise QueryPlanningError("limit must be positive")
    return min(limit, MAX_LIMIT)


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QueryPlanningError(f"{field_name} is required")
    return value.strip()


def _parse_datetime(value: object, field_name: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise QueryPlanningError(f"{field_name} must be an ISO datetime string")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise QueryPlanningError(f"{field_name} must be an ISO datetime string") from exc


def _date_from_parts(parts: tuple[str, str, str]) -> datetime:
    return datetime(int(parts[0]), int(parts[1]), int(parts[2]))


def _add_month_or_day(value: datetime, *, months: int = 0, days: int = 0) -> datetime:
    if months:
        month = value.month + months
        year = value.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        return datetime(year, month, 1, tzinfo=value.tzinfo)
    if days:
        from datetime import timedelta

        return value + timedelta(days=days)
    return value


def _validate_meter_urn(value: str) -> str:
    if not _METER_RE.fullmatch(value):
        raise QueryPlanningError(f"unsafe meter_urn: {value!r}")
    return value


def _validate_measurement(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", value):
        raise QueryPlanningError(f"unsafe measurement: {value!r}")
    return value


__all__ = [
    "Aggregation",
    "QueryPlan",
    "QueryPlanningError",
    "assert_read_only_sql",
    "make_query_plan",
    "render_select_sql",
]
