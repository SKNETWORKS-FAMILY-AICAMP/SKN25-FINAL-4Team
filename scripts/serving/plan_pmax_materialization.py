#!/usr/bin/env python3
"""Emit a read-only P-Max strict materialization approval packet."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from cms.data.pmax_materialization_plan import PmaxMaterializationScope, build_inventory_queries, build_packet, build_scope


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ts", required=True, help="Timezone-aware P-Max inference base timestamp")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--history-windows", type=int, default=288)
    parser.add_argument("--json", action="store_true", help="Emit compact JSON")
    args = parser.parse_args()

    scope = build_scope(base_ts=_parse_ts(args.base_ts), history_windows=args.history_windows)
    env = _load_env(args.env_file)
    results = _run_read_only_queries(env, scope)
    packet = build_packet(scope, results)
    print(json.dumps(packet, ensure_ascii=False, indent=None if args.json else 2, default=str, sort_keys=True))
    return 0


def _parse_ts(raw: str) -> datetime:
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--base-ts must be timezone-aware")
    return parsed


def _load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        raise FileNotFoundError(f"env file not found: {path}")
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _run_read_only_queries(env: dict[str, str], scope: PmaxMaterializationScope) -> dict[str, Any]:
    psycopg = __import__("psycopg")
    conn = psycopg.connect(
        host=env.get("POSTGRES_HOST") or env.get("DB_HOST"),
        port=int(env.get("POSTGRES_PORT") or env.get("DB_PORT") or "5432"),
        dbname=env.get("POSTGRES_DB") or env.get("DB_NAME"),
        user=env.get("POSTGRES_USER") or env.get("DB_USER"),
        password=env.get("POSTGRES_PASSWORD") or env.get("DB_PASSWORD"),
        connect_timeout=5,
    )
    output: dict[str, Any] = {}
    try:
        with conn.cursor() as cur:
            cur.execute("BEGIN READ ONLY")
            cur.execute("SET LOCAL statement_timeout = '10000ms'")
            for query in build_inventory_queries(scope):
                cur.execute(query.sql, query.params)
                columns = [desc.name for desc in cur.description]
                rows = [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
                output[query.name] = rows[0] if len(rows) == 1 and query.name == "write_scope_estimate" else rows
            conn.rollback()
    finally:
        conn.close()
    return output


if __name__ == "__main__":
    raise SystemExit(main())
