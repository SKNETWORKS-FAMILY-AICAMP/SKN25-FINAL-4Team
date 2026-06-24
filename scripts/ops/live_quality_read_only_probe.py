#!/usr/bin/env python3
"""Local live-quality evidence probe with an explicit read-only connection gate.

The script never loads dotenv files and never prints connection material.  When
LIVE_QUALITY_READONLY_DSN is absent it emits a JSON no-execution packet so the
artifact can be exercised locally without touching AWS or a database.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

PROBE_VERSION = "live-quality-read-only-probe/v1"
CONNECTION_ENV_VAR = "LIVE_QUALITY_READONLY_DSN"
DEFAULT_STATEMENT_TIMEOUT_MS = 10_000

BOOTSTRAP_SQL = (
    "BEGIN READ ONLY",
    "SET default_transaction_read_only = on",
    "SET statement_timeout = '<runtime-ms>ms'",
)

PROBE_QUERIES: dict[str, str] = {
    "transaction_read_only_proof": """
        SELECT current_setting('transaction_read_only') AS transaction_read_only,
               current_setting('default_transaction_read_only') AS default_transaction_read_only,
               current_setting('statement_timeout') AS statement_timeout,
               current_database() AS database_name,
               current_user AS probe_user
    """,
    "schema_inventory": """
        SELECT table_schema,
               table_name,
               table_type
        FROM information_schema.tables
        WHERE table_schema IN ('live', 'qa', 'ops', 'mart')
        ORDER BY table_schema, table_name
    """,
    "column_inventory": """
        SELECT table_schema,
               table_name,
               column_name,
               ordinal_position,
               data_type,
               is_nullable
        FROM information_schema.columns
        WHERE table_schema IN ('live', 'qa', 'ops', 'mart')
        ORDER BY table_schema, table_name, ordinal_position
    """,
    "index_inventory": """
        SELECT schemaname,
               tablename,
               indexname,
               indexdef
        FROM pg_indexes
        WHERE schemaname IN ('live', 'qa', 'ops', 'mart')
        ORDER BY schemaname, tablename, indexname
    """,
}

OPTIONAL_TABLE_GUARD_SQL = "SELECT to_regclass(%s) IS NOT NULL AS relation_present"


@dataclass(frozen=True)
class OptionalQuery:
    relation_name: str
    reason: str
    sql: str
    required_columns: tuple[str, ...] = ()


OPTIONAL_TABLE_QUERIES: dict[str, OptionalQuery] = {
    "live_measurement_15min_count": OptionalQuery(
        relation_name="live.measurement_15min",
        reason="15-minute live measurement volume and freshness count",
        sql="""
            SELECT 'live.measurement_15min' AS relation_name,
                   count(*) AS row_count,
                   min(bucket_ts) AS min_bucket_ts,
                   max(bucket_ts) AS max_bucket_ts
            FROM live.measurement_15min
        """,
    ),
    "live_measurement_1h_count": OptionalQuery(
        relation_name="live.measurement_1h",
        reason="hourly live measurement volume and freshness count",
        sql="""
            SELECT 'live.measurement_1h' AS relation_name,
                   count(*) AS row_count,
                   min(bucket_ts) AS min_bucket_ts,
                   max(bucket_ts) AS max_bucket_ts
            FROM live.measurement_1h
        """,
    ),
    "qa_bad_row_reason_count_legacy": OptionalQuery(
        relation_name="qa.bad_row",
        reason="legacy bad-row reason distribution for quality triage",
        sql="""
            SELECT reason,
                   count(*) AS row_count,
                   min(raw_ts) AS min_raw_ts,
                   max(raw_ts) AS max_raw_ts
            FROM qa.bad_row
            GROUP BY reason
            ORDER BY row_count DESC, reason
            LIMIT 50
        """,
        required_columns=("reason", "raw_ts"),
    ),
    "qa_bad_row_reason_count_live_extension": OptionalQuery(
        relation_name="qa.bad_row",
        reason="live-extension bad-row reason distribution for quality triage",
        sql="""
            SELECT reason_code,
                   count(*) AS row_count,
                   min(observed_at) AS min_observed_at,
                   max(observed_at) AS max_observed_at
            FROM qa.bad_row
            GROUP BY reason_code
            ORDER BY row_count DESC, reason_code
            LIMIT 50
        """,
        required_columns=("reason_code", "observed_at"),
    ),
    "ops_quality_gate_reason_count": OptionalQuery(
        relation_name="ops.quality_gate_result",
        reason="quality gate result and reason distribution",
        sql="""
            SELECT gate_name,
                   status,
                   reason_code,
                   count(*) AS row_count,
                   max(checked_at) AS max_checked_at
            FROM ops.quality_gate_result
            GROUP BY gate_name, status, reason_code
            ORDER BY gate_name, status, row_count DESC
            LIMIT 100
        """,
    ),
}

FORBIDDEN_OUTPUT_KEYS = ("dsn", "url", "uri", "connection_string", "password", "token", "key")


def build_no_execution_packet(env: Mapping[str, str]) -> dict[str, Any]:
    """Return a local-only packet when explicit connection information is absent."""

    return {
        "ok": False,
        "executed": False,
        "probe_version": PROBE_VERSION,
        "generated_at": now_utc(),
        "reason": "missing explicit read-only connection information",
        "required_env": CONNECTION_ENV_VAR,
        "env_present": CONNECTION_ENV_VAR in env,
        "dotenv_loaded": False,
        "connection_material_printed": False,
        "next_step": "Provide LIVE_QUALITY_READONLY_DSN only after approval for a read-only evidence run.",
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local-gated read-only live quality evidence probes")
    parser.add_argument("--statement-timeout-ms", type=int, default=DEFAULT_STATEMENT_TIMEOUT_MS)
    parser.add_argument(
        "--require-approval-id",
        default="",
        help="Optional approval id to copy into the emitted evidence metadata.",
    )
    parser.add_argument(
        "--print-query-plan",
        action="store_true",
        help="Print query names and safety bootstrap SQL without connecting.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.print_query_plan:
        print(json.dumps(build_query_plan_packet(args), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    env = os.environ
    if CONNECTION_ENV_VAR not in env or not env[CONNECTION_ENV_VAR].strip():
        print(json.dumps(build_no_execution_packet(env), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    payload = run_probe(
        dsn=env[CONNECTION_ENV_VAR],
        statement_timeout_ms=max(1, args.statement_timeout_ms),
        approval_id=args.require_approval_id,
    )
    print(json.dumps(redact_for_output(payload), ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


def build_query_plan_packet(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "executed": False,
        "probe_version": PROBE_VERSION,
        "generated_at": now_utc(),
        "bootstrap_sql": list(BOOTSTRAP_SQL),
        "required_env": CONNECTION_ENV_VAR,
        "query_names": sorted(PROBE_QUERIES),
        "optional_query_names": sorted(OPTIONAL_TABLE_QUERIES),
        "optional_table_guard_sql": OPTIONAL_TABLE_GUARD_SQL,
        "statement_timeout_ms": max(1, args.statement_timeout_ms),
        "connection_material_printed": False,
    }


def run_probe(*, dsn: str, statement_timeout_ms: int, approval_id: str) -> dict[str, Any]:
    try:
        import psycopg  # type: ignore[import-not-found]
        from psycopg.rows import dict_row  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on operator runtime package selection.
        raise SystemExit("psycopg is required: run with `uv run --with psycopg[binary] ...`") from exc

    results: dict[str, Any] = {}
    skipped_optional_queries: list[dict[str, str]] = []
    with psycopg.connect(dsn, row_factory=dict_row) as conn:  # type: ignore[no-untyped-call]
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("BEGIN READ ONLY")
            cur.execute("SET default_transaction_read_only = on")
            cur.execute(f"SET statement_timeout = '{int(statement_timeout_ms)}ms'")
            for name, sql in PROBE_QUERIES.items():
                cur.execute(sql)
                results[name] = list(cur.fetchall())
            available_columns = _available_columns(results.get("column_inventory", []))
            for name, optional_query in OPTIONAL_TABLE_QUERIES.items():
                cur.execute(OPTIONAL_TABLE_GUARD_SQL, (optional_query.relation_name,))
                guard_row = dict(cur.fetchone() or {})
                if not guard_row.get("relation_present"):
                    skipped_optional_queries.append(
                        {
                            "query_name": name,
                            "relation_name": optional_query.relation_name,
                            "reason": "optional relation not present per to_regclass guard",
                        }
                    )
                    continue
                missing_columns = tuple(
                    column
                    for column in optional_query.required_columns
                    if column not in available_columns.get(optional_query.relation_name, frozenset())
                )
                if missing_columns:
                    skipped_optional_queries.append(
                        {
                            "query_name": name,
                            "relation_name": optional_query.relation_name,
                            "reason": "optional columns not present per information_schema guard: " + ",".join(missing_columns),
                        }
                    )
                    continue
                cur.execute(optional_query.sql)
                results[name] = list(cur.fetchall())
            conn.rollback()

    return {
        "ok": True,
        "executed": True,
        "probe_version": PROBE_VERSION,
        "generated_at": now_utc(),
        "approval_id": approval_id or "",
        "read_only_controls": list(BOOTSTRAP_SQL),
        "optional_table_guard_sql": OPTIONAL_TABLE_GUARD_SQL,
        "skipped_optional_queries": skipped_optional_queries,
        "attestation": {
            "dotenv_loaded": False,
            "connection_material_printed": False,
            "write_sql_in_probe": False,
            "secret_values_in_output": False,
        },
        "results": results,
    }


def redact_for_output(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in FORBIDDEN_OUTPUT_KEYS):
                redacted[str(key)] = "<redacted>"
            else:
                redacted[str(key)] = redact_for_output(item)
        return redacted
    if isinstance(value, list):
        return [redact_for_output(item) for item in value]
    if isinstance(value, tuple):
        return [redact_for_output(item) for item in value]
    return value


def _available_columns(rows: object) -> dict[str, frozenset[str]]:
    columns_by_relation: dict[str, set[str]] = {}
    if not isinstance(rows, list):
        return {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        schema = row.get("table_schema")
        table = row.get("table_name")
        column = row.get("column_name")
        if not schema or not table or not column:
            continue
        relation_name = f"{schema}.{table}"
        columns_by_relation.setdefault(relation_name, set()).add(str(column))
    return {relation: frozenset(columns) for relation, columns in columns_by_relation.items()}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
