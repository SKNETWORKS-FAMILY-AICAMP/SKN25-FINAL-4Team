#!/usr/bin/env python3
"""Read-only DB metadata source probes for EMS ontology generation."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping

from dotenv import load_dotenv
import psycopg

REQUIRED_ENV_KEYS = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"]


@dataclass(frozen=True)
class DBConfig:
    connect_kwargs: dict[str, str]
    missing_keys: list[str]
    safe_summary: dict[str, str]


@dataclass(frozen=True)
class MetadataSourceStatus:
    available: bool
    existing_tables: list[str]
    missing_tables: list[str]
    safe_config: dict[str, str]


def metadata_table_names() -> list[str]:
    return ["ems.meter_definition", "ems.meter_redundancy"]


def read_db_config(env: Mapping[str, str] | None = None) -> DBConfig:
    values = dict(os.environ if env is None else env)
    missing = [key for key in REQUIRED_ENV_KEYS if not values.get(key)]
    connect_kwargs = {
        "host": values.get("DB_HOST", ""),
        "port": values.get("DB_PORT", ""),
        "dbname": values.get("DB_NAME", ""),
        "user": values.get("DB_USER", ""),
        "password": values.get("DB_PASSWORD", ""),
    }
    safe_summary = dict(connect_kwargs)
    safe_summary["password"] = "***" if connect_kwargs["password"] else ""
    return DBConfig(connect_kwargs=connect_kwargs, missing_keys=missing, safe_summary=safe_summary)


def build_table_existence_query() -> tuple[str, tuple[str, list[str]]]:
    tables = [name.split(".", 1)[1] for name in metadata_table_names()]
    return (
        """
        SELECT table_schema || '.' || table_name AS table_name
        FROM information_schema.tables
        WHERE table_schema = %s
          AND table_name = ANY(%s)
        ORDER BY table_name
        """,
        ("ems", tables),
    )


def check_metadata_tables() -> MetadataSourceStatus:
    load_dotenv()
    config = read_db_config()
    if config.missing_keys:
        return MetadataSourceStatus(
            available=False,
            existing_tables=[],
            missing_tables=metadata_table_names(),
            safe_config=config.safe_summary,
        )

    query, params = build_table_existence_query()
    with psycopg.connect(**config.connect_kwargs) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            existing = [row[0] for row in cur.fetchall()]

    expected = metadata_table_names()
    missing = [table for table in expected if table not in existing]
    return MetadataSourceStatus(
        available=not missing,
        existing_tables=existing,
        missing_tables=missing,
        safe_config=config.safe_summary,
    )


def ensure_db_metadata_available() -> MetadataSourceStatus:
    status = check_metadata_tables()
    if not status.available:
        raise SystemExit(
            {
                "status": "metadata tables not available",
                "existing_tables": status.existing_tables,
                "missing_tables": status.missing_tables,
                "safe_config": status.safe_config,
            }
        )
    return status


if __name__ == "__main__":
    print(check_metadata_tables())
