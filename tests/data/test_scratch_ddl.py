from __future__ import annotations

import ast
import unittest
from pathlib import Path
from unittest.mock import patch

from cms.data.db_scratch_guard import ALLOWED_POSTGRES_TABLES, ScratchGuardError
from cms.data.scratch_ddl import (
    LATENCY_MARKERS,
    MEASUREMENT_TABLE_RESOLUTIONS,
    REQUIRED_COMMON_COLUMNS,
    SCRATCH_TABLES,
    render_scratch_cleanup_sql,
    render_scratch_ddl,
)


class ScratchDdlTests(unittest.TestCase):
    def test_rendered_ddl_uses_guard_generated_schema_and_declares_only_scratch_objects(self) -> None:
        ddl = render_scratch_ddl("pilot_20260530")

        self.assertIn("CREATE SCHEMA IF NOT EXISTS cms_scratch_pilot_20260530;", ddl)
        for table in SCRATCH_TABLES:
            with self.subTest(table=table):
                self.assertIn(f"CREATE TABLE IF NOT EXISTS cms_scratch_pilot_20260530.{table}", ddl)

        self.assertNotIn("public.", ddl)
        self.assertNotIn("canonical.", ddl)
        self.assertNotIn("scratch_live_db_", ddl)

    def test_schema_name_is_obtained_from_db_scratch_guard(self) -> None:
        with patch("cms.data.scratch_ddl.postgres_scratch_schema_name", return_value="cms_scratch_guarded") as schema_name:
            ddl = render_scratch_ddl("pilot_20260530")

        schema_name.assert_called_once_with("pilot_20260530")
        self.assertIn("CREATE SCHEMA IF NOT EXISTS cms_scratch_guarded;", ddl)

    def test_unsafe_test_run_ids_are_rejected_before_sql_is_returned(self) -> None:
        for test_run_id in ["", "Pilot", "pilot-1", "pilot;drop", "1pilot", "live", "prod_20260530", "canonical"]:
            with self.subTest(test_run_id=test_run_id):
                with self.assertRaises(ScratchGuardError):
                    render_scratch_ddl(test_run_id)
                with self.assertRaises(ScratchGuardError):
                    render_scratch_cleanup_sql(test_run_id)

    def test_contract_object_set_matches_write_guard(self) -> None:
        self.assertEqual(
            SCRATCH_TABLES,
            (
                "measurement_1min",
                "measurement_5min",
                "measurement_15min",
                "measurement_1h",
                "latency_events",
                "qa_metrics",
            ),
        )
        self.assertEqual(tuple(ALLOWED_POSTGRES_TABLES), SCRATCH_TABLES)

    def test_measurement_tables_include_required_columns_and_resolution_checks(self) -> None:
        ddl = render_scratch_ddl("pilot_20260530")
        for table, resolution in MEASUREMENT_TABLE_RESOLUTIONS.items():
            with self.subTest(table=table):
                start = ddl.index(f"CREATE TABLE IF NOT EXISTS cms_scratch_pilot_20260530.{table}")
                end = ddl.index("\n);", start)
                table_sql = ddl[start:end]
                for column in REQUIRED_COMMON_COLUMNS:
                    self.assertIn(column, table_sql)
                self.assertIn(f"CHECK (resolution = '{resolution}')", table_sql)
                self.assertIn("PRIMARY KEY (test_run_id, lane, resolution, bucket_ts, meter_urn, measurement, lineage_key)", table_sql)

    def test_latency_events_include_all_latency_markers(self) -> None:
        ddl = render_scratch_ddl("pilot_20260530")
        start = ddl.index("CREATE TABLE IF NOT EXISTS cms_scratch_pilot_20260530.latency_events")
        end = ddl.index("\n);", start)
        table_sql = ddl[start:end]

        for column in REQUIRED_COMMON_COLUMNS:
            self.assertIn(column, table_sql)
        for marker in LATENCY_MARKERS:
            self.assertIn(f"{marker} TIMESTAMPTZ", table_sql)
        for latency_column in ["mongo_to_1min_sec", "mongo_to_5min_sec", "mongo_to_15min_sec", "mongo_to_1h_sec", "end_to_end_sec"]:
            self.assertIn(f"{latency_column} DOUBLE PRECISION", table_sql)

    def test_qa_metrics_include_required_columns_and_metric_fields(self) -> None:
        ddl = render_scratch_ddl("pilot_20260530")
        start = ddl.index("CREATE TABLE IF NOT EXISTS cms_scratch_pilot_20260530.qa_metrics")
        end = ddl.index("\n);", start)
        table_sql = ddl[start:end]

        for column in REQUIRED_COMMON_COLUMNS:
            self.assertIn(column, table_sql)
        for column in ["metric_name TEXT NOT NULL", "metric_unit TEXT", "metric_scope TEXT NOT NULL", "details JSONB NOT NULL"]:
            self.assertIn(column, table_sql)

    def test_cleanup_sql_is_limited_to_guard_generated_schema(self) -> None:
        self.assertEqual(render_scratch_cleanup_sql("pilot_20260530"), "DROP SCHEMA IF EXISTS cms_scratch_pilot_20260530 CASCADE;")

    def test_module_is_pure_and_does_not_import_db_clients(self) -> None:
        source_path = Path(__file__).resolve().parents[2] / "src" / "cms" / "data" / "scratch_ddl.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])

        forbidden = {"psycopg", "psycopg2", "pymongo", "motor", "sqlalchemy", "asyncpg", "socket"}
        self.assertTrue(forbidden.isdisjoint(imported_roots), imported_roots)


if __name__ == "__main__":
    unittest.main()
