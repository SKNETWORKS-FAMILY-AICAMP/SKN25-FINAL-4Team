#!/usr/bin/env python3
"""Generate reviewed CMS reference/canonical migration drafts.

This module is intentionally offline: it does not import DB clients, read .env,
or connect to PostgreSQL. It renders SQL/Markdown drafts from a small inventory
JSON produced by read-only inspection.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

Resolution = Literal["1min", "15min", "1h"]

RESOLUTIONS: tuple[Resolution, ...] = ("1min", "15min", "1h")
REFERENCE_TABLES: dict[Resolution, str] = {
    "1min": "corrected_resampled_1min",
    "15min": "corrected_15min",
    "1h": "corrected_1h",
}
CANONICAL_TABLES: dict[Resolution, str] = {
    "1min": "measurement_1min",
    "15min": "measurement_15min",
    "1h": "measurement_1h",
}
_ALLOWED_SCHEMAS = {"canonical", "reference", "staging", "ems"}
_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_DESTRUCTIVE_PATTERNS = (
    "DROP TABLE",
    "TRUNCATE",
    "DELETE FROM CANONICAL",
    "ALTER TABLE CANONICAL",
)


@dataclass(frozen=True)
class InventoryTable:
    table_schema: str
    table_name: str
    resolution: Resolution
    source_family: str
    classification: str

    @property
    def qualified_name(self) -> str:
        return f"{quote_ident(self.table_schema)}.{quote_ident(self.table_name)}"


def quote_ident(value: str) -> str:
    """Return a validated SQL identifier.

    The generator accepts only project-controlled snake_case identifiers. This is
    deliberately stricter than PostgreSQL so generated SQL stays reviewable.
    """

    if not _IDENT_RE.fullmatch(value):
        raise ValueError(f"unsafe SQL identifier: {value!r}")
    return value


def _resolution_from_name(table_name: str) -> Resolution | None:
    if table_name.endswith("_1min") or table_name == "measurement_1min":
        return "1min"
    if table_name.endswith("_15min") or table_name == "measurement_15min":
        return "15min"
    if table_name.endswith("_1h") or table_name == "measurement_1h":
        return "1h"
    return None


def _source_family(row: dict[str, Any]) -> str:
    explicit = str(row.get("source_family") or row.get("classification") or "").lower()
    haystack = " ".join(str(row.get(key, "")) for key in ("table_schema", "table_name", "notes", "source_family")).lower()
    repaired_markers = (
        "corrected_resampled",
        "corrected",
        "cr_measurement",
        "gap_filled",
        "gap filled",
        "gapfill",
        "gap_fill",
        "leap",
        "zero",
        "interpolation",
        "interpolated",
        "forward_fill",
        "backfill",
        "repaired",
        "resampled",
    )
    if any(marker in explicit or marker in haystack for marker in repaired_markers):
        return "corrected_resampled"
    if "observed" in explicit or "observed" in haystack:
        return "observed"
    return explicit or "unknown"


def classify_inventory(rows: Iterable[dict[str, Any]]) -> list[InventoryTable]:
    """Classify raw inventory rows for migration planning."""

    tables: list[InventoryTable] = []
    for row in rows:
        schema = quote_ident(str(row["table_schema"]))
        table = quote_ident(str(row["table_name"]))
        if schema not in _ALLOWED_SCHEMAS:
            continue
        resolution = _resolution_from_name(table)
        if resolution is None:
            continue
        family = _source_family(row)
        if family == "corrected_resampled":
            classification = "reference"
        elif family == "observed":
            classification = "canonical_observed"
        else:
            classification = "unknown_review"
        tables.append(InventoryTable(schema, table, resolution, family, classification))
    return tables


def inventory_sql() -> str:
    """Render read-only inventory SQL."""

    return """-- CMS migration read-only inventory. No DDL/DML.
SELECT current_database() AS current_database, current_user AS current_user;

SELECT schema_name
FROM information_schema.schemata
WHERE schema_name IN ('canonical', 'reference', 'staging', 'qa', 'ops', 'mart', 'ems')
ORDER BY schema_name;

SELECT table_schema, table_name, table_type
FROM information_schema.tables
WHERE table_schema IN ('canonical', 'reference', 'staging', 'qa', 'ops', 'mart', 'ems')
ORDER BY table_schema, table_name;

SELECT table_schema, table_name, column_name, data_type, is_nullable, ordinal_position
FROM information_schema.columns
WHERE table_schema IN ('canonical', 'reference', 'staging', 'ems')
ORDER BY table_schema, table_name, ordinal_position;

SELECT
    n.nspname || '.' || c.relname AS table_name,
    c.reltuples::bigint AS estimated_rows,
    c.relkind
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'canonical'
  AND c.relname IN ('measurement_1min', 'measurement_15min', 'measurement_1h')
ORDER BY c.relname;
"""


def _db_guard_sql(expected_db: str) -> str:
    safe_db = _sql_literal(expected_db)
    return f"DO $$ BEGIN IF current_database() <> {safe_db} THEN RAISE EXCEPTION 'wrong database: %', current_database(); END IF; END $$;"


def render_reference_copy_sql(inventory: Iterable[InventoryTable], *, run_id: str, expected_db: str = "cms") -> str:
    """Render Track R copy-first SQL draft.

    Reference targets use a generic JSONB payload so source tables with different
    column layouts cannot be misinserted by positional ``SELECT src.*`` columns.
    Re-runs with the same migration_run_id/source_table/row_hash are idempotent.
    """

    safe_run_id = _sql_literal(run_id)
    lines = [
        "-- Track R: corrected/resampled -> reference copy-first draft.",
        "-- Review before production execution. Do not run without Viowlet approval.",
        _db_guard_sql(expected_db),
        "SET statement_timeout = '15min';",
        "CREATE SCHEMA IF NOT EXISTS reference;",
        "",
    ]
    selected = [table for table in inventory if table.classification == "reference"]
    emitted_targets: set[str] = set()
    for table in selected:
        target = f"reference.{REFERENCE_TABLES[table.resolution]}"
        source = table.qualified_name
        if target not in emitted_targets:
            emitted_targets.add(target)
            lines.extend(
                [
                    f"CREATE TABLE IF NOT EXISTS {target} (",
                    "    row_data jsonb NOT NULL,",
                    "    migration_run_id text NOT NULL,",
                    "    source_table text NOT NULL,",
                    "    source_family text NOT NULL CHECK (source_family = 'corrected_resampled'),",
                    "    copied_at timestamptz NOT NULL DEFAULT now()",
                    ");",
                    f"CREATE UNIQUE INDEX IF NOT EXISTS {REFERENCE_TABLES[table.resolution]}_migration_source_row_hash_uq",
                    f"    ON {target} (migration_run_id, source_table, md5(row_data::text));",
                    "",
                ]
            )
        lines.extend(
            [
                f"-- Source: {source} -> Target: {target}",
                f"INSERT INTO {target} (row_data, migration_run_id, source_table, source_family, copied_at)",
                "SELECT",
                "    to_jsonb(src) AS row_data,",
                f"    {safe_run_id}::text AS migration_run_id,",
                f"    '{source}'::text AS source_table,",
                "    'corrected_resampled'::text AS source_family,",
                "    now() AS copied_at",
                f"FROM {source} AS src",
                "ON CONFLICT DO NOTHING;",
                "",
            ]
        )
    if not selected:
        lines.append("-- No corrected/resampled source tables were classified for Track R.")
    sql = "\n".join(lines).rstrip() + "\n"
    assert_no_unapproved_destructive_sql(sql)
    return sql


def render_reference_rollback_sql(inventory: Iterable[InventoryTable], *, run_id: str, expected_db: str = "cms") -> str:
    """Render Track R rollback draft scoped to this migration run."""

    safe_run_id = _sql_literal(run_id)
    lines = [
        "-- Track R rollback draft: remove rows copied by one migration_run_id.",
        "-- Review before execution. Source tables are untouched by Track R.",
        _db_guard_sql(expected_db),
        "SET statement_timeout = '15min';",
        "",
    ]
    selected = [table for table in inventory if table.classification == "reference"]
    for table in selected:
        target = f"reference.{REFERENCE_TABLES[table.resolution]}"
        lines.extend(
            [
                f"-- Roll back copied rows for {target}",
                f"DELETE FROM {target}",
                f"WHERE migration_run_id = {safe_run_id};",
                "",
            ]
        )
    if not selected:
        lines.append("-- No reference-classified tables to roll back.")
    return "\n".join(lines).rstrip() + "\n"


def render_canonical_observed_contract_rollback_sql() -> str:
    """Render Track C rollback note. Track C is approval-gated."""

    return """-- Track C rollback draft.
-- Canonical observed population/switch requires separate approval and should
-- produce a run-specific rollback based on the exact promotion_id/source_run_id.
-- This generic contract rollback intentionally performs no DDL/DML.
SELECT 'Track C rollback must be generated from the approved production execution packet' AS rollback_notice;
"""


def render_canonical_observed_contract_sql(*, expected_db: str = "cms") -> str:
    """Render Track C canonical observed DDL contract draft."""

    pieces = [
        "-- Track C: canonical observed contract draft.",
        "-- Execute only after observed source is validated and Viowlet approves Track C.",
        _db_guard_sql(expected_db),
        "SET statement_timeout = '15min';",
        "CREATE SCHEMA IF NOT EXISTS canonical;",
        "",
    ]
    for resolution, table in CANONICAL_TABLES.items():
        expected = {"1min": 1, "15min": 15, "1h": 60}[resolution]
        pieces.append(
            f"""CREATE TABLE IF NOT EXISTS canonical.{table} (
    bucket_ts timestamptz NOT NULL,
    resolution text NOT NULL CHECK (resolution = '{resolution}'),
    meter_urn text NOT NULL,
    measurement text NOT NULL,
    value double precision NULL,
    unit text NULL,
    aggregation_policy text NOT NULL,
    expected_points integer NOT NULL DEFAULT {expected} CHECK (expected_points > 0),
    observed_points integer NOT NULL CHECK (observed_points >= 0),
    gap_points integer NOT NULL CHECK (gap_points >= 0),
    coverage_ratio double precision NOT NULL CHECK (coverage_ratio >= 0.0 AND coverage_ratio <= 1.0),
    mask_code text NULL,
    quality_code text NOT NULL,
    quality_summary jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    provenance jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    source_event_ids text[] NOT NULL DEFAULT '{{}}'::text[],
    source_run_id text NOT NULL,
    promotion_id text NOT NULL,
    lineage_key text NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT {table}_points_check CHECK (observed_points + gap_points <= expected_points),
    CONSTRAINT {table}_coverage_formula_check CHECK (
        abs(coverage_ratio - (observed_points::double precision / expected_points::double precision)) < 0.000001
    ),
    CONSTRAINT {table}_gap_null_check CHECK (
        NOT (resolution = '1min' AND observed_points = 0)
        OR (value IS NULL AND gap_points = 1 AND coverage_ratio = 0.0 AND mask_code IS NOT NULL AND mask_code LIKE '%gap%')
    ),
    CONSTRAINT {table}_uq UNIQUE (resolution, bucket_ts, meter_urn, measurement, promotion_id)
);
CREATE INDEX IF NOT EXISTS {table}_bucket_meter_idx ON canonical.{table} (bucket_ts, meter_urn, measurement);
CREATE INDEX IF NOT EXISTS {table}_source_run_idx ON canonical.{table} (source_run_id);
CREATE INDEX IF NOT EXISTS {table}_promotion_idx ON canonical.{table} (promotion_id);
"""
        )
    sql = "\n".join(pieces).rstrip() + "\n"
    assert_no_unapproved_destructive_sql(sql)
    return sql


def render_reconciliation_sql(inventory: Iterable[InventoryTable]) -> str:
    """Render read-only reconciliation SQL for Track R targets."""

    lines = [
        "-- CMS migration reconciliation SQL. SELECT-only.",
        "SELECT current_database() AS current_database, current_user AS current_user;",
        "",
    ]
    selected = [table for table in inventory if table.classification == "reference"]
    for table in selected:
        target = f"reference.{REFERENCE_TABLES[table.resolution]}"
        source = table.qualified_name
        lines.extend(
            [
                f"-- Reconcile {source} -> {target}",
                "WITH",
                f"source_rows AS (SELECT to_jsonb(src) AS row_json FROM {source} AS src),",
                f"target_rows AS (SELECT row_data AS row_json FROM {target} AS tgt WHERE source_table = '{source}'),",
                "source_norm AS (SELECT md5(row_json::text) AS row_hash, row_json FROM source_rows),",
                "target_norm AS (SELECT md5(row_json::text) AS row_hash, row_json FROM target_rows)",
                "SELECT",
                f"    '{source}' AS source_table,",
                f"    '{target}' AS target_table,",
                "    (SELECT COUNT(*) FROM source_norm) AS source_rows,",
                "    (SELECT COUNT(*) FROM target_norm) AS target_rows,",
                "    (SELECT md5(COALESCE(string_agg(row_hash, '' ORDER BY row_hash), '')) FROM source_norm) AS source_hash,",
                "    (SELECT md5(COALESCE(string_agg(row_hash, '' ORDER BY row_hash), '')) FROM target_norm) AS target_hash,",
                "    (SELECT COUNT(DISTINCT row_json->>'meter_urn') FROM source_norm) AS source_meter_urns,",
                "    (SELECT COUNT(DISTINCT row_json->>'meter_urn') FROM target_norm) AS target_meter_urns,",
                "    (SELECT COUNT(DISTINCT row_json->>'measurement') FROM source_norm) AS source_measurements,",
                "    (SELECT COUNT(DISTINCT row_json->>'measurement') FROM target_norm) AS target_measurements,",
                "    (SELECT COUNT(*) FROM source_norm WHERE row_json ? 'value' AND row_json->>'value' IS NULL) AS source_null_values,",
                "    (SELECT COUNT(*) FROM target_norm WHERE row_json ? 'value' AND row_json->>'value' IS NULL) AS target_null_values,",
                "    (SELECT COUNT(*) FROM source_norm WHERE COALESCE(row_json->>'mask_code', '') LIKE '%gap%') AS source_gap_rows,",
                "    (SELECT COUNT(*) FROM target_norm WHERE COALESCE(row_json->>'mask_code', '') LIKE '%gap%') AS target_gap_rows,",
                f"    (SELECT COUNT(*) FROM {target} AS tgt WHERE source_table = '{source}' AND source_family = 'corrected_resampled') AS target_corrected_family_rows;",
                "",
                "WITH",
                f"source_sample AS (SELECT to_jsonb(src) AS row_json FROM {source} AS src ORDER BY md5(to_jsonb(src)::text) LIMIT 20),",
                f"target_sample AS (SELECT row_data AS row_json FROM {target} AS tgt WHERE source_table = '{source}' ORDER BY md5(row_data::text) LIMIT 20)",
                "SELECT",
                f"    '{source}' AS source_table,",
                "    (SELECT jsonb_agg(row_json ORDER BY md5(row_json::text)) FROM source_sample) AS source_sample,",
                "    (SELECT jsonb_agg(row_json ORDER BY md5(row_json::text)) FROM target_sample) AS target_sample;",
                "",
            ]
        )
    if not selected:
        lines.append("-- No reference-classified tables to reconcile.")
    sql = "\n".join(lines).rstrip() + "\n"
    assert_select_only_reconciliation(sql)
    return sql


def render_approval_packet(*, run_id: str, classified: list[InventoryTable], expected_db: str = "cms") -> str:
    reference_sources = [table.qualified_name for table in classified if table.classification == "reference"]
    unknown = [table.qualified_name for table in classified if table.classification == "unknown_review"]
    observed = [table.qualified_name for table in classified if table.classification == "canonical_observed"]
    return f"""# CMS Migration Approval Packet Draft

**Run ID:** `{run_id}`
**Target DB:** `{expected_db}`

## Scope

Track R copies corrected/resampled sources into `reference.corrected_resampled_*`.
Track C canonical observed population/switch is separate and requires observed source validation.

## Classified sources

- Reference sources: {reference_sources or 'none'}
- Observed-compliant candidates: {observed or 'none'}
- Unknown review: {unknown or 'none'}

## Required approval wording for Track R

```text
승인: cms reference corrected_resampled copy production 실행 허용
대상 DB: {expected_db}
대상 SQL: <exact Track R SQL file paths>
```

## Required approval wording for Track C

```text
승인: cms canonical observed population/switch production 실행 허용
대상 DB: {expected_db}
대상 observed source: <exact source table/job/run>
대상 SQL: <exact Track C SQL file paths>
```

Track R approval does not imply Track C approval.
"""


def assert_no_unapproved_destructive_sql(sql: str) -> None:
    upper = sql.upper()
    for pattern in _DESTRUCTIVE_PATTERNS:
        if pattern in upper:
            raise ValueError(f"unapproved destructive SQL found: {pattern}")


def assert_select_only_reconciliation(sql: str) -> None:
    upper = sql.upper()
    forbidden = ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "TRUNCATE ", "ALTER ", "CREATE ")
    for token in forbidden:
        if token in upper:
            raise ValueError(f"reconciliation SQL must be SELECT-only; found {token.strip()}")


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _load_inventory(path: Path) -> list[InventoryTable]:
    rows = json.loads(path.read_text())
    if not isinstance(rows, list):
        raise ValueError("inventory JSON must be a list of table rows")
    return classify_inventory(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory-json", type=Path, help="Read-only inventory JSON list.")
    parser.add_argument("--run-id", default="mig_20260601")
    parser.add_argument("--out-dir", type=Path, help="Write SQL/Markdown drafts to this directory.")
    parser.add_argument("--expected-db", default=os.environ.get("DB_NAME", "cms"), help="Expected target database for SQL guard.")
    parser.add_argument("--print-inventory-sql", action="store_true")
    args = parser.parse_args()

    if args.print_inventory_sql:
        print(inventory_sql())
        return

    if not args.inventory_json:
        raise SystemExit("--inventory-json is required unless --print-inventory-sql is used")

    classified = _load_inventory(args.inventory_json)
    outputs = {
        "20260601_cms_reference_copy.sql": render_reference_copy_sql(classified, run_id=args.run_id, expected_db=args.expected_db),
        "20260601_cms_reference_copy_rollback.sql": render_reference_rollback_sql(classified, run_id=args.run_id, expected_db=args.expected_db),
        "20260601_cms_reference_reconciliation.sql": render_reconciliation_sql(classified),
        "20260601_cms_canonical_observed_contract.sql": render_canonical_observed_contract_sql(expected_db=args.expected_db),
        "20260601_cms_canonical_observed_contract_rollback.sql": render_canonical_observed_contract_rollback_sql(),
        "approval_packet.md": render_approval_packet(run_id=args.run_id, classified=classified, expected_db=args.expected_db),
    }
    if args.out_dir:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        for name, content in outputs.items():
            (args.out_dir / name).write_text(content)
    else:
        for name, content in outputs.items():
            print(f"-- FILE: {name}")
            print(content)


if __name__ == "__main__":
    main()
