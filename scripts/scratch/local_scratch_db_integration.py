from __future__ import annotations

import json
import subprocess
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from cms.data.db_scratch_guard import (
    POSTGRES_DATABASE,
    mongo_scratch_collection_name,
    postgres_scratch_schema_name,
    validate_mongo_cleanup_target,
    validate_mongo_scratch_write,
    validate_postgres_cleanup_target,
)
from cms.data.scratch_db_adapter import run_live_equalization_to_postgres_scratch
from cms.data.scratch_ddl import MEASUREMENT_TABLE_RESOLUTIONS, render_scratch_cleanup_sql, render_scratch_ddl
from psycopg import sql
from psycopg.types.json import Jsonb

POSTGRES_IMAGE = "postgres:16-alpine"
MONGO_IMAGE = "mongo:7"
POSTGRES_PASSWORD = "cms"
POSTGRES_USER = "cms"


class MongoShellRawSource:
    def __init__(self, *, container: str, collection: str) -> None:
        self.container = container
        self.collection = collection

    def iter_raw_harmonized_documents(self, *, test_run_id: str, start: datetime, end: datetime) -> tuple[dict[str, Any], ...]:
        del start, end
        js = (
            "const docs = db.getCollection(" + json.dumps(self.collection) + ")"
            ".find({test_run_id: " + json.dumps(test_run_id) + "}, {_id: 0})"
            ".sort({timestamp: 1}).toArray();"
            "print(JSON.stringify(docs));"
        )
        output = _docker_exec_mongosh(self.container, js)
        return tuple(json.loads(output or "[]"))


class PsycopgPostgresScratchSink:
    def __init__(self, *, dsn: str) -> None:
        self.dsn = dsn

    def write_rows(self, *, database: str, schema: str, table: str, rows: tuple[dict[str, Any], ...]) -> None:
        if database != POSTGRES_DATABASE:
            raise ValueError("unexpected postgres database")
        if not rows:
            return
        columns = (
            "test_run_id",
            "lane",
            "resolution",
            "bucket_ts",
            "meter_urn",
            "measurement",
            "value",
            "quality_code",
            "mask_code",
            "evidence_level",
            "expected_points",
            "observed_points",
            "gap_points",
            "coverage_ratio",
            "source_native_interval_seconds",
            "cadence_policy_id",
            "target_resolution",
            "expected_points_policy",
            "aggregation_policy",
            "quality_summary",
            "source_event_ids",
            "lineage_key",
            "created_at",
        )
        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                query = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
                    sql.Identifier(schema),
                    sql.Identifier(table),
                    sql.SQL(", ").join(sql.Identifier(column) for column in columns),
                    sql.SQL(", ").join(sql.Placeholder() for _ in columns),
                )
                values = [tuple(_pg_value(row[column]) for column in columns) for row in rows]
                cur.executemany(query, values)


def run_local_scratch_db_integration(
    *,
    test_run_id: str,
    postgres_port: int,
    mongo_port: int,
    cleanup: bool = True,
    cleanup_containers: bool = True,
) -> dict[str, Any]:
    postgres_schema = postgres_scratch_schema_name(test_run_id)
    mongo_collection = mongo_scratch_collection_name(test_run_id)
    postgres_container = f"cms-scratch-{test_run_id}-postgres"
    mongo_container = f"cms-scratch-{test_run_id}-mongo"
    env = {"ALLOW_DB_SCRATCH_WRITE": "1"}
    cleanup_report = {
        "postgres_schema_dropped": False,
        "mongo_collection_dropped": False,
        "postgres_container_removed": False,
        "mongo_container_removed": False,
    }
    dsn = _postgres_dsn(postgres_port)

    _remove_container_if_exists(postgres_container)
    _remove_container_if_exists(mongo_container)
    try:
        _run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                postgres_container,
                "-e",
                f"POSTGRES_USER={POSTGRES_USER}",
                "-e",
                f"POSTGRES_PASSWORD={POSTGRES_PASSWORD}",
                "-e",
                f"POSTGRES_DB={POSTGRES_DATABASE}",
                "-p",
                f"127.0.0.1:{postgres_port}:5432",
                POSTGRES_IMAGE,
            ]
        )
        _run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                mongo_container,
                "-p",
                f"127.0.0.1:{mongo_port}:27017",
                MONGO_IMAGE,
            ]
        )
        _wait_for_postgres(dsn)
        _wait_for_mongo(mongo_container)
        _apply_postgres_ddl(dsn, test_run_id)
        docs = _synthetic_harmonized_docs(test_run_id)
        _insert_mongo_docs(mongo_container, mongo_collection, test_run_id, docs, env)

        adapter_result = run_live_equalization_to_postgres_scratch(
            source_repository=MongoShellRawSource(container=mongo_container, collection=mongo_collection),
            postgres_sink_repository=PsycopgPostgresScratchSink(dsn=dsn),
            test_run_id=test_run_id,
            start=datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
            end=datetime(2024, 1, 1, 1, 0, tzinfo=UTC),
            allow_write=True,
            env=env,
        )
        postgres_row_counts = _postgres_row_counts(dsn, postgres_schema)
        mongo_raw_count = _mongo_count(mongo_container, mongo_collection, test_run_id)
        public_canonical_tables = _public_canonical_measurement_tables(dsn)
        report = {
            "claims": {
                "scratch_db_integration": "local only",
                "production_ready": False,
                "paper_complete": False,
                "aws_untouched": True,
            },
            "test_run_id": test_run_id,
            "postgres_container": postgres_container,
            "mongo_container": mongo_container,
            "postgres_schema": postgres_schema,
            "mongo_collection": mongo_collection,
            "postgres_row_counts": postgres_row_counts,
            "mongo_raw_count": mongo_raw_count,
            "postgres_public_canonical_tables": public_canonical_tables,
            "adapter_result": {**asdict(adapter_result), "real_db_writes_executed": True},
            "cleanup": cleanup_report,
        }
    finally:
        if cleanup:
            cleanup_report.update(_cleanup_postgres_schema(dsn, test_run_id, env))
            cleanup_report.update(_cleanup_mongo_collection(mongo_container, mongo_collection, test_run_id, env))
        if cleanup_containers:
            cleanup_report["postgres_container_removed"] = _remove_container_if_exists(postgres_container)
            cleanup_report["mongo_container_removed"] = _remove_container_if_exists(mongo_container)

    report["cleanup"] = cleanup_report
    return report


def _postgres_dsn(port: int) -> str:
    return f"host=127.0.0.1 port={port} dbname={POSTGRES_DATABASE} user={POSTGRES_USER} password={POSTGRES_PASSWORD}"


def _pg_value(value: Any) -> Any:
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        return Jsonb(value)
    return value


def _synthetic_harmonized_docs(test_run_id: str) -> list[dict[str, Any]]:
    start = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
    docs = []
    for minute in range(60):
        timestamp = start + timedelta(minutes=minute)
        docs.append(
            {
                "test_run_id": test_run_id,
                "meter_urn": "V.Z84",
                "measurement": "W",
                "timestamp": timestamp.isoformat(),
                "value": float(minute),
            }
        )
    return docs


def _insert_mongo_docs(container: str, collection: str, test_run_id: str, docs: list[dict[str, Any]], env: dict[str, str]) -> None:
    for document in docs:
        validate_mongo_scratch_write(collection=collection, document=document, test_run_id=test_run_id, allow_write=True, env=env)
    js = (
        "const collection = db.getCollection(" + json.dumps(collection) + ");"
        "collection.deleteMany({test_run_id: " + json.dumps(test_run_id) + "});"
        "const docs = JSON.parse(" + json.dumps(json.dumps(docs)) + ");"
        "collection.insertMany(docs);"
        "print(JSON.stringify({count: collection.countDocuments({test_run_id: " + json.dumps(test_run_id) + "})}));"
    )
    result = json.loads(_docker_exec_mongosh(container, js))
    if result["count"] != len(docs):
        raise RuntimeError(f"mongo insert count mismatch: {result}")


def _apply_postgres_ddl(dsn: str, test_run_id: str) -> None:
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(render_scratch_ddl(test_run_id))


def _postgres_row_counts(dsn: str, schema: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for table in MEASUREMENT_TABLE_RESOLUTIONS:
                cur.execute(sql.SQL("SELECT count(*) FROM {}.{}").format(sql.Identifier(schema), sql.Identifier(table)))
                counts[table] = int(cur.fetchone()[0])
    return counts


def _mongo_count(container: str, collection: str, test_run_id: str) -> int:
    js = (
        "print(JSON.stringify({count: db.getCollection(" + json.dumps(collection) + ")"
        ".countDocuments({test_run_id: " + json.dumps(test_run_id) + "})}));"
    )
    return int(json.loads(_docker_exec_mongosh(container, js))["count"])


def _public_canonical_measurement_tables(dsn: str) -> list[str]:
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_schema || '.' || table_name
                FROM information_schema.tables
                WHERE table_schema IN ('public', 'canonical')
                  AND table_name LIKE 'measurement_%'
                ORDER BY 1
                """
            )
            return [row[0] for row in cur.fetchall()]


def _cleanup_postgres_schema(dsn: str, test_run_id: str, env: dict[str, str]) -> dict[str, bool]:
    schema = postgres_scratch_schema_name(test_run_id)
    validate_postgres_cleanup_target(database=POSTGRES_DATABASE, schema=schema, test_run_id=test_run_id, allow_write=True, env=env)
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(render_scratch_cleanup_sql(test_run_id))
            cur.execute("SELECT to_regnamespace(%s)", (schema,))
            dropped = cur.fetchone()[0] is None
    return {"postgres_schema_dropped": dropped}


def _cleanup_mongo_collection(container: str, collection: str, test_run_id: str, env: dict[str, str]) -> dict[str, bool]:
    validate_mongo_cleanup_target(collection=collection, test_run_id=test_run_id, allow_write=True, env=env)
    js = (
        "db.getCollection(" + json.dumps(collection) + ").drop();"
        "print(JSON.stringify({exists: db.getCollectionNames().includes(" + json.dumps(collection) + ")}));"
    )
    result = json.loads(_docker_exec_mongosh(container, js))
    return {"mongo_collection_dropped": not bool(result["exists"])}


def _wait_for_postgres(dsn: str) -> None:
    deadline = time.monotonic() + 60
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(dsn, connect_timeout=2) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    return
        except Exception as exc:  # pragma: no cover - diagnostics only
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"postgres did not become ready: {last_error}")


def _wait_for_mongo(container: str) -> None:
    deadline = time.monotonic() + 60
    last_error = ""
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["docker", "exec", container, "mongosh", "cms", "--quiet", "--eval", "JSON.stringify(db.adminCommand({ping: 1}))"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and '"ok":1' in result.stdout.replace(" ", ""):
            return
        last_error = result.stderr or result.stdout
        time.sleep(1)
    raise RuntimeError(f"mongo did not become ready: {last_error}")


def _docker_exec_mongosh(container: str, js: str) -> str:
    result = _run(["docker", "exec", container, "mongosh", "cms", "--quiet", "--eval", js])
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _remove_container_if_exists(name: str) -> bool:
    result = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=30)
    if name not in result.stdout.splitlines():
        return True
    _run(["docker", "rm", "-f", name])
    return name not in subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=30).stdout.splitlines()


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(command)}\nstdout={result.stdout}\nstderr={result.stderr}")
    return result


if __name__ == "__main__":
    report = run_local_scratch_db_integration(
        test_run_id="localdb_tdd_20260531",
        postgres_port=55432,
        mongo_port=27028,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
