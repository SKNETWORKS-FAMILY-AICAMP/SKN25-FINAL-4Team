from __future__ import annotations

import ast
import unittest
from pathlib import Path

from cms.data.db_scratch_guard import (
    ALLOWED_POSTGRES_TABLES,
    ScratchGuardError,
    mongo_scratch_collection_name,
    postgres_scratch_schema_name,
    validate_mongo_cleanup_target,
    validate_mongo_scratch_write,
    validate_postgres_cleanup_target,
    validate_postgres_scratch_write,
    validate_test_run_id,
    validate_write_allowed,
)


class DbScratchGuardTests(unittest.TestCase):
    def test_write_permission_is_default_deny_and_requires_env_plus_runtime_flag(self) -> None:
        with self.assertRaisesRegex(ScratchGuardError, "ALLOW_DB_SCRATCH_WRITE"):
            validate_write_allowed(allow_write=True, env={})
        with self.assertRaisesRegex(ScratchGuardError, "allow_write"):
            validate_write_allowed(allow_write=False, env={"ALLOW_DB_SCRATCH_WRITE": "1"})
        with self.assertRaisesRegex(ScratchGuardError, "ALLOW_DB_SCRATCH_WRITE"):
            validate_write_allowed(allow_write=True, env={"ALLOW_DB_SCRATCH_WRITE": "true"})

        self.assertTrue(validate_write_allowed(allow_write=True, env={"ALLOW_DB_SCRATCH_WRITE": "1"}))

    def test_test_run_id_is_strictly_safe_and_generates_exact_targets(self) -> None:
        self.assertEqual(validate_test_run_id("pilot_20260530"), "pilot_20260530")
        self.assertEqual(postgres_scratch_schema_name("pilot_20260530"), "cms_scratch_pilot_20260530")
        self.assertEqual(mongo_scratch_collection_name("pilot_20260530"), "test_measurement_raw_pilot_20260530")

        unsafe_ids = [
            "",
            "Pilot",
            "pilot-1",
            "pilot.1",
            "pilot/1",
            "pilot;drop",
            "1pilot",
            "pilot 1",
            "live",
            "live_20260530",
            "prod",
            "prod_20260530",
            "production",
            "production_20260530",
            "canonical",
            "canonical_20260530",
        ]
        for test_run_id in unsafe_ids:
            with self.subTest(test_run_id=test_run_id):
                with self.assertRaisesRegex(ScratchGuardError, "test_run_id"):
                    validate_test_run_id(test_run_id)

    def test_postgres_write_allows_only_cms_generated_schema_allowed_tables_and_matching_row_marker(self) -> None:
        env = {"ALLOW_DB_SCRATCH_WRITE": "1"}
        test_run_id = "pilot_20260530"
        schema = postgres_scratch_schema_name(test_run_id)

        for table in ALLOWED_POSTGRES_TABLES:
            with self.subTest(table=table):
                target = validate_postgres_scratch_write(
                    database="cms",
                    schema=schema,
                    table=table,
                    row={"test_run_id": test_run_id, "value": 1.0},
                    test_run_id=test_run_id,
                    allow_write=True,
                    env=env,
                )
                self.assertEqual(target.schema, schema)
                self.assertEqual(target.table, table)

        bad_cases = [
            {"database": "postgres", "schema": schema, "table": "measurement_1min", "row": {"test_run_id": test_run_id}, "message": "database"},
            {"database": "cms", "schema": "public", "table": "measurement_1min", "row": {"test_run_id": test_run_id}, "message": "schema"},
            {"database": "cms", "schema": "canonical", "table": "measurement_1min", "row": {"test_run_id": test_run_id}, "message": "schema"},
            {"database": "cms", "schema": "qa", "table": "measurement_1min", "row": {"test_run_id": test_run_id}, "message": "schema"},
            {"database": "cms", "schema": "ops", "table": "measurement_1min", "row": {"test_run_id": test_run_id}, "message": "schema"},
            {"database": "cms", "schema": "", "table": "measurement_1min", "row": {"test_run_id": test_run_id}, "message": "schema"},
            {"database": "cms", "schema": schema, "table": "measurement_raw", "row": {"test_run_id": test_run_id}, "message": "table"},
            {"database": "cms", "schema": schema, "table": "measurement_buffer", "row": {"test_run_id": test_run_id}, "message": "table"},
            {"database": "cms", "schema": schema, "table": "measurement_1min", "row": {"test_run_id": "other"}, "message": "test_run_id"},
            {"database": "cms", "schema": schema, "table": "measurement_1min", "row": {}, "message": "test_run_id"},
        ]
        for case in bad_cases:
            with self.subTest(case=case):
                with self.assertRaisesRegex(ScratchGuardError, case["message"]):
                    validate_postgres_scratch_write(
                        database=case["database"],
                        schema=case["schema"],
                        table=case["table"],
                        row=case["row"],
                        test_run_id=test_run_id,
                        allow_write=True,
                        env=env,
                    )

    def test_mongo_write_allows_only_generated_raw_collection_and_matching_document_marker(self) -> None:
        env = {"ALLOW_DB_SCRATCH_WRITE": "1"}
        test_run_id = "pilot_20260530"
        collection = mongo_scratch_collection_name(test_run_id)

        target = validate_mongo_scratch_write(
            collection=collection,
            document={"test_run_id": test_run_id, "value": 1.0},
            test_run_id=test_run_id,
            allow_write=True,
            env=env,
        )
        self.assertEqual(target.collection, collection)

        rejected_collections = [
            "measurement_buffer",
            "cursor",
            "read_cache",
            "reject",
            "measurement_raw",
            "test_measurement_raw_other",
            "public.test_measurement_raw_pilot_20260530",
            "",
        ]
        for rejected in rejected_collections:
            with self.subTest(collection=rejected):
                with self.assertRaisesRegex(ScratchGuardError, "collection"):
                    validate_mongo_scratch_write(
                        collection=rejected,
                        document={"test_run_id": test_run_id},
                        test_run_id=test_run_id,
                        allow_write=True,
                        env=env,
                    )

        for document in [{}, {"test_run_id": "other"}]:
            with self.subTest(document=document):
                with self.assertRaisesRegex(ScratchGuardError, "test_run_id"):
                    validate_mongo_scratch_write(
                        collection=collection,
                        document=document,
                        test_run_id=test_run_id,
                        allow_write=True,
                        env=env,
                    )

    def test_cleanup_targets_are_limited_to_exact_generated_schema_and_collection(self) -> None:
        env = {"ALLOW_DB_SCRATCH_WRITE": "1"}
        test_run_id = "pilot_20260530"
        schema = postgres_scratch_schema_name(test_run_id)
        collection = mongo_scratch_collection_name(test_run_id)

        self.assertEqual(
            validate_postgres_cleanup_target(database="cms", schema=schema, test_run_id=test_run_id, allow_write=True, env=env).schema,
            schema,
        )
        self.assertEqual(
            validate_mongo_cleanup_target(collection=collection, test_run_id=test_run_id, allow_write=True, env=env).collection,
            collection,
        )

        with self.assertRaisesRegex(ScratchGuardError, "schema"):
            validate_postgres_cleanup_target(database="cms", schema="public", test_run_id=test_run_id, allow_write=True, env=env)
        with self.assertRaisesRegex(ScratchGuardError, "database"):
            validate_postgres_cleanup_target(database="postgres", schema=schema, test_run_id=test_run_id, allow_write=True, env=env)
        with self.assertRaisesRegex(ScratchGuardError, "collection"):
            validate_mongo_cleanup_target(collection="measurement_raw", test_run_id=test_run_id, allow_write=True, env=env)

    def test_module_is_pure_and_does_not_import_db_clients(self) -> None:
        source_path = Path(__file__).resolve().parents[2] / "src" / "cms" / "data" / "db_scratch_guard.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])

        forbidden = {"psycopg", "psycopg2", "pymongo", "motor", "sqlalchemy", "asyncpg"}
        self.assertTrue(forbidden.isdisjoint(imported_roots), imported_roots)
        self.assertNotIn("socket", imported_roots)


if __name__ == "__main__":
    unittest.main()
