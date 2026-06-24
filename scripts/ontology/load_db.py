#!/usr/bin/env python3
"""Read-only DB metadata source probes for CMS ontology generation.

The current deployed source is the DB `ontology` schema. The previous
`cms_metadata` registry shape is kept as a fallback so older local DB snapshots
can still regenerate the RDF artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv
import psycopg

ROOT = Path(__file__).resolve().parents[2]
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
    """Legacy metadata registry tables kept for local snapshot fallback."""
    return [
        "cms_metadata.meter_definition",
        "cms_metadata.meter_redundancy",
        "cms_metadata.meter_hardware_model",
        "cms_metadata.meter_hardware_assignment",
    ]


def ontology_table_names() -> list[str]:
    """Current DB ontology projection tables required for RDF regeneration."""
    return [
        "ontology.meter",
        "ontology.redundancy_pair",
        "ontology.hardware_model",
    ]


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


def build_table_existence_query(schema: str, table_names: list[str]) -> tuple[str, tuple[str, list[str]]]:
    tables = [name.split(".", 1)[1] for name in table_names]
    return (
        """
        SELECT table_schema || '.' || table_name AS table_name
        FROM information_schema.tables
        WHERE table_schema = %s
          AND table_name = ANY(%s)
        ORDER BY table_name
        """,
        (schema, tables),
    )


def check_metadata_tables() -> MetadataSourceStatus:
    load_dotenv(ROOT / ".env")
    config = read_db_config()
    if config.missing_keys:
        return MetadataSourceStatus(
            available=False,
            existing_tables=[],
            missing_tables=ontology_table_names(),
            safe_config=config.safe_summary,
        )

    sources = [
        ("ontology", ontology_table_names()),
        ("cms_metadata", metadata_table_names()),
    ]
    all_existing: list[str] = []
    first_missing: list[str] | None = None
    with psycopg.connect(**config.connect_kwargs) as conn:
        with conn.cursor() as cur:
            for schema, expected in sources:
                query, params = build_table_existence_query(schema, expected)
                cur.execute(query, params)
                existing = [row[0] for row in cur.fetchall()]
                all_existing.extend(existing)
                missing = [table for table in expected if table not in existing]
                if first_missing is None:
                    first_missing = missing
                if not missing:
                    return MetadataSourceStatus(
                        available=True,
                        existing_tables=existing,
                        missing_tables=[],
                        safe_config=config.safe_summary,
                    )

    return MetadataSourceStatus(
        available=False,
        existing_tables=sorted(set(all_existing)),
        missing_tables=first_missing or ontology_table_names(),
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


def connect_db():
    load_dotenv(ROOT / ".env")
    config = read_db_config()
    if config.missing_keys:
        raise SystemExit({"status": "missing DB config", "missing_keys": config.missing_keys})
    return psycopg.connect(**config.connect_kwargs)  # type: ignore[arg-type]


def _fetch_ontology_metadata() -> tuple[list[dict[str, object]], list[dict[str, str]], list[dict[str, str]]]:
    """Fetch ontology metadata from the current ``ontology`` schema load."""

    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '15s'")
            cur.execute(
                """
                SELECT
                    meter_urn,
                    meter_domain,
                    meter_role_code,
                    equipment_group_code,
                    COALESCE(equipment_name, '') AS equipment_name,
                    COALESCE(building_code, '') AS building_code,
                    COALESCE(sign_convention, '') AS sign_convention,
                    anomaly_priority,
                    CASE
                        WHEN lower(COALESCE(equipment_name, '')) LIKE 'feed emission lab%' THEN 'feed'
                        WHEN lower(COALESCE(equipment_name, '')) LIKE 'distribution emission lab%' THEN 'distribution'
                        ELSE ''
                    END AS equipment_layer
                FROM ontology.meter
                ORDER BY meter_urn
                """
            )
            meter_records = [
                {
                    "meter_urn": row[0],
                    "meter_domain": row[1],
                    "meter_role": row[2],
                    "equipment_group": row[3],
                    "equipment_name": row[4],
                    "building_code": row[5],
                    "sign_convention": row[6],
                    "anomaly_priority": row[7],
                    "equipment_layer": row[8],
                    "note_file": f"db:ontology.meter/{row[0]}",
                }
                for row in cur.fetchall()
            ]
            cur.execute(
                """
                SELECT primary_meter_urn, redundant_meter_urn, equipment_group_code, COALESCE(equipment_name, '')
                FROM ontology.redundancy_pair
                ORDER BY primary_meter_urn, redundant_meter_urn
                """
            )
            redundancy_records = [
                {
                    "primary_meter_urn": row[0],
                    "redundant_meter_urn": row[1],
                    "equipment_group": row[2],
                    "equipment_name": row[3],
                }
                for row in cur.fetchall()
            ]
            cur.execute(
                """
                SELECT
                    m.meter_urn,
                    m.hardware_model_code,
                    h.manufacturer,
                    h.model_name,
                    h.meter_category,
                    COALESCE(m.source_path, '') AS source_name,
                    '' AS source_doi,
                    'ontology.meter' AS source_table,
                    COALESCE(m.equipment_name, '') AS source_description
                FROM ontology.meter m
                JOIN ontology.hardware_model h USING (hardware_model_code)
                ORDER BY m.meter_urn
                """
            )
            hardware_records = [
                {
                    "meter_urn": row[0],
                    "hardware_model_code": row[1],
                    "manufacturer": row[2],
                    "model_name": row[3],
                    "meter_category": row[4],
                    "source_name": row[5],
                    "source_doi": row[6],
                    "source_table": row[7],
                    "source_description": row[8],
                }
                for row in cur.fetchall()
            ]
    return meter_records, redundancy_records, hardware_records


def fetch_db_metadata() -> tuple[list[dict[str, object]], list[dict[str, str]], list[dict[str, str]]]:
    """Fetch ontology metadata from the active approved DB metadata source."""

    status = ensure_db_metadata_available()
    if any(table.startswith("ontology.") for table in status.existing_tables):
        return _fetch_ontology_metadata()
    return _fetch_cms_metadata()


def _fetch_cms_metadata() -> tuple[list[dict[str, object]], list[dict[str, str]], list[dict[str, str]]]:
    """Fetch ontology metadata from approved DB metadata tables.

    Returns:
    - meter records compatible with generate.py
    - redundancy records compatible with generate.py
    - hardware assignment records for HardwareModel triples
    """
    ensure_db_metadata_available()
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '15s'")
            cur.execute(
                """
                SELECT
                    d.meter_urn,
                    d.meter_domain,
                    d.meter_role,
                    d.equipment_group,
                    COALESCE(d.equipment_name, '') AS equipment_name,
                    COALESCE(d.building_code, '') AS building_code,
                    COALESCE(d.sign_convention, '') AS sign_convention,
                    d.anomaly_priority,
                    CASE
                        WHEN lower(COALESCE(h.source_description, '')) LIKE 'feed emission lab%' THEN 'feed'
                        WHEN lower(COALESCE(h.source_description, '')) LIKE 'distribution emission lab%' THEN 'distribution'
                        ELSE ''
                    END AS equipment_layer
                FROM cms_metadata.meter_definition d
                LEFT JOIN cms_metadata.meter_hardware_assignment h USING (meter_urn)
                ORDER BY d.meter_urn
                """
            )
            meter_records = [
                {
                    "meter_urn": row[0],
                    "meter_domain": row[1],
                    "meter_role": row[2],
                    "equipment_group": row[3],
                    "equipment_name": row[4],
                    "building_code": row[5],
                    "sign_convention": row[6],
                    "anomaly_priority": row[7],
                    "equipment_layer": row[8],
                    "note_file": f"db:cms_metadata.meter_definition/{row[0]}",
                }
                for row in cur.fetchall()
            ]
            cur.execute(
                """
                SELECT primary_meter_urn, redundant_meter_urn, equipment_group, COALESCE(equipment_name, '')
                FROM cms_metadata.meter_redundancy
                ORDER BY primary_meter_urn, redundant_meter_urn
                """
            )
            redundancy_records = [
                {
                    "primary_meter_urn": row[0],
                    "redundant_meter_urn": row[1],
                    "equipment_group": row[2],
                    "equipment_name": row[3],
                }
                for row in cur.fetchall()
            ]
            cur.execute(
                """
                SELECT
                    a.meter_urn,
                    a.hardware_model_code,
                    h.manufacturer,
                    h.model_name,
                    h.meter_category,
                    COALESCE(a.source_name, '') AS source_name,
                    COALESCE(a.source_doi, '') AS source_doi,
                    COALESCE(a.source_table, '') AS source_table,
                    COALESCE(a.source_description, '') AS source_description
                FROM cms_metadata.meter_hardware_assignment a
                JOIN cms_metadata.meter_hardware_model h USING (hardware_model_code)
                ORDER BY a.meter_urn
                """
            )
            hardware_records = [
                {
                    "meter_urn": row[0],
                    "hardware_model_code": row[1],
                    "manufacturer": row[2],
                    "model_name": row[3],
                    "meter_category": row[4],
                    "source_name": row[5],
                    "source_doi": row[6],
                    "source_table": row[7],
                    "source_description": row[8],
                }
                for row in cur.fetchall()
            ]
    return meter_records, redundancy_records, hardware_records


if __name__ == "__main__":
    print(check_metadata_tables())
