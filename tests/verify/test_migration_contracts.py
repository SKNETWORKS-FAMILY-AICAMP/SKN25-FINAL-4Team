from __future__ import annotations

import ast
import unittest
from pathlib import Path


class CmsMigrationVerifyScriptTests(unittest.TestCase):
    def test_verify_script_has_no_db_client_imports(self) -> None:
        script = Path("scripts/verify/verify_migration_contracts.py")
        tree = ast.parse(script.read_text())
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])

        forbidden = {"psycopg", "psycopg2", "pymongo", "motor", "sqlalchemy", "asyncpg", "dotenv"}
        self.assertTrue(forbidden.isdisjoint(imported_roots), imported_roots)

    def test_migration_generator_has_no_db_client_imports(self) -> None:
        script = Path("scripts/migrations/generate_reference_migration.py")
        tree = ast.parse(script.read_text())
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])

        forbidden = {"psycopg", "psycopg2", "pymongo", "motor", "sqlalchemy", "asyncpg", "dotenv"}
        self.assertTrue(forbidden.isdisjoint(imported_roots), imported_roots)


if __name__ == "__main__":
    unittest.main()
