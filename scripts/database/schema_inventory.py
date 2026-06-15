#!/usr/bin/env python3
"""Read-only model-serving schema inventory helper.

Default mode prints the parameterized information_schema query. Use --execute
only in an approved environment with explicit PostgreSQL environment variables.
No .env file is read by this script.
"""

from __future__ import annotations

import argparse
import json
import os
from importlib import import_module
from typing import Any

from cms.data.model_serving_queries import build_model_serving_schema_inventory_query

_REQUIRED_EXEC_ENV = ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_USER")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="execute read-only inventory against PostgreSQL using explicit POSTGRES_* env")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    spec = build_model_serving_schema_inventory_query()
    if not args.execute:
        payload = {"name": spec.name, "sql": spec.sql, "params": dict(spec.params), "source_tables": spec.source_tables}
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else _format_query(payload))
        return 0

    missing = [key for key in _REQUIRED_EXEC_ENV if not os.environ.get(key)]
    if missing:
        print(json.dumps({"ok": False, "error": "missing required env", "missing": missing}, ensure_ascii=False))
        return 2

    rows = _execute_inventory(spec.sql, dict(spec.params))
    summary = summarize_inventory(rows)
    payload = {"ok": True, "summary": summary, "rows": rows}
    print(json.dumps(payload, ensure_ascii=False, default=str, indent=2) if args.json else _format_summary(summary))
    return 0


def summarize_inventory(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tables: dict[str, dict[str, Any]] = {}
    for row in rows:
        table_name = f"{row['table_schema']}.{row['table_name']}"
        table = tables.setdefault(table_name, {"exists": False, "columns": []})
        if row.get("column_name") is not None:
            table["exists"] = True
            table["columns"].append(row["column_name"])
    missing_tables = tuple(table for table, meta in tables.items() if not meta["exists"])
    return {"table_count": len(tables), "missing_tables": missing_tables, "tables": tables}


def _execute_inventory(sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    psycopg = import_module("psycopg")
    kwargs = {
        "host": os.environ["POSTGRES_HOST"],
        "port": int(os.environ.get("POSTGRES_PORT", "5432")),
        "dbname": os.environ["POSTGRES_DB"],
        "user": os.environ["POSTGRES_USER"],
        "sslmode": os.environ.get("POSTGRES_SSLMODE", "disable"),
        "connect_timeout": int(os.environ.get("POSTGRES_CONNECT_TIMEOUT", "5")),
    }
    password = os.environ.get("POSTGRES_PASSWORD")
    if password:
        kwargs["password"] = password
    with psycopg.connect(**kwargs) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(sql, params)
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


def _format_query(payload: dict[str, Any]) -> str:
    return "\n".join(("-- model-serving schema inventory", payload["sql"], "", "-- params", json.dumps(payload["params"], ensure_ascii=False, default=str, indent=2)))


def _format_summary(summary: dict[str, Any]) -> str:
    lines = [f"table_count={summary['table_count']}", "missing_tables=" + ",".join(summary["missing_tables"])]
    for table, meta in summary["tables"].items():
        lines.append(f"{table}: {'present' if meta['exists'] else 'missing'} columns={len(meta['columns'])}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
