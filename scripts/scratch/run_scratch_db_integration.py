"""Run actual CMS scratch DB integration against configured MongoDB/PostgreSQL.

The script reads connection settings from the local `.env`, writes only isolated
scratch objects guarded by `test_run_id`, performs read-back verification, and
writes redacted evidence reports. It never prints secrets or connection strings.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
from cms.data.scratch_ddl import MEASUREMENT_TABLE_RESOLUTIONS, REQUIRED_COMMON_COLUMNS, render_scratch_cleanup_sql, render_scratch_ddl
from psycopg import sql
from psycopg.types.json import Jsonb
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"
REPORT_ROOT = ROOT / "reports"
ALLOW_ENV = {"ALLOW_DB_SCRATCH_WRITE": "1"}


class PymongoRawSource:
    def __init__(self, *, client: MongoClient, database: str, collection: str) -> None:
        self.client = client
        self.database = database
        self.collection = collection

    def iter_raw_harmonized_documents(self, *, test_run_id: str, start: datetime, end: datetime) -> tuple[dict[str, Any], ...]:
        del start, end
        coll = self.client[self.database][self.collection]
        documents: list[dict[str, Any]] = []
        for meter_urn in sorted(coll.distinct("meter_urn", {"test_run_id": test_run_id})):
            cursor = coll.find({"test_run_id": test_run_id, "meter_urn": meter_urn}, {"_id": 0}).sort("timestamp", 1).batch_size(100)
            documents.extend(dict(document) for document in cursor)
        return tuple(documents)


class PsycopgPostgresScratchSink:
    def __init__(self, *, connect_kwargs: dict[str, Any]) -> None:
        self.connect_kwargs = connect_kwargs

    def write_rows(self, *, database: str, schema: str, table: str, rows: tuple[dict[str, Any], ...]) -> None:
        if database != POSTGRES_DATABASE:
            raise ValueError("unexpected postgres database")
        if not rows:
            return
        columns = REQUIRED_COMMON_COLUMNS
        with psycopg.connect(**self.connect_kwargs) as conn:
            with conn.cursor() as cur:
                query = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
                    sql.Identifier(schema),
                    sql.Identifier(table),
                    sql.SQL(", ").join(sql.Identifier(column) for column in columns),
                    sql.SQL(", ").join(sql.Placeholder() for _ in columns),
                )
                values = [tuple(_pg_value(row[column]) for column in columns) for row in rows]
                cur.executemany(query, values)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-run-id", default=_default_run_id())
    parser.add_argument("--case", choices=("two_meter", "live81_1min_60m"), default="two_meter")
    parser.add_argument("--cleanup", action="store_true", help="drop the scratch schema and collection after verification")
    args = parser.parse_args()

    result = run_actual_scratch_db_integration(test_run_id=args.test_run_id, cleanup=args.cleanup, case=args.case)
    print(f"SCRATCH_DB_INTEGRATION_STATUS={result['status']}")
    print(f"TEST_RUN_ID={result['test_run_id']}")
    print(f"REPORT_DIR={result['report_dir']}")
    print(f"MONGO_RAW_COUNT={result['mongo']['raw_count']}")
    print(f"POSTGRES_COUNTS={json.dumps(result['postgres']['row_counts'], sort_keys=True)}")
    print(f"LATENCY_SECONDS={json.dumps(result['latency_seconds'], sort_keys=True)}")


def run_actual_scratch_db_integration(*, test_run_id: str, cleanup: bool, case: str = "two_meter") -> dict[str, Any]:
    env = _read_env(ENV_PATH)
    pg_kwargs = _postgres_connect_kwargs(env)
    mongo_uri = _required(env, "MONGODB_URI")
    mongo_db = env.get("MONGO_DB") or "cms"
    pg_schema = postgres_scratch_schema_name(test_run_id)
    mongo_collection = mongo_scratch_collection_name(test_run_id)
    report_dir = REPORT_ROOT / f"scratch_db_integration_{test_run_id}"
    report_dir.mkdir(parents=True, exist_ok=True)

    docs = _synthetic_harmonized_docs(test_run_id, case=case)
    timing: dict[str, float] = {}
    cleanup_report = {"postgres_schema_dropped": False, "mongo_collection_dropped": False}
    t_total_start = time.perf_counter()
    mongo_client: MongoClient | None = None

    try:
        mongo_client = MongoClient(mongo_uri, serverSelectionTimeoutMS=10_000)
        _verify_connections(pg_kwargs, mongo_client, mongo_db)

        t = time.perf_counter()
        _apply_postgres_ddl(pg_kwargs, test_run_id)
        timing["postgres_ddl_sec"] = time.perf_counter() - t

        t = time.perf_counter()
        _insert_mongo_docs(mongo_client, mongo_db, mongo_collection, test_run_id, docs)
        timing["mongo_insert_sec"] = time.perf_counter() - t

        t = time.perf_counter()
        mongo_readback = _mongo_readback(mongo_client, mongo_db, mongo_collection, test_run_id)
        timing["mongo_readback_sec"] = time.perf_counter() - t
        mongo_visible_at = time.perf_counter()

        t = time.perf_counter()
        adapter_result = run_live_equalization_to_postgres_scratch(
            source_repository=PymongoRawSource(client=mongo_client, database=mongo_db, collection=mongo_collection),
            postgres_sink_repository=PsycopgPostgresScratchSink(connect_kwargs=pg_kwargs),
            test_run_id=test_run_id,
            start=datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
            end=datetime(2024, 1, 1, 1, 0, tzinfo=UTC),
            allow_write=True,
            env=ALLOW_ENV,
        )
        timing["processor_to_postgres_sec"] = time.perf_counter() - t
        pg_outputs_visible_at = time.perf_counter()
        timing["mongo_visible_to_pg_outputs_sec"] = pg_outputs_visible_at - mongo_visible_at

        t = time.perf_counter()
        pg_readback = _postgres_readback(pg_kwargs, pg_schema)
        timing["postgres_readback_sec"] = time.perf_counter() - t
        timing["total_sec"] = time.perf_counter() - t_total_start

        result = {
            "status": "pass",
            "scope": "actual_aws_scratch_db_integration",
            "case": case,
            "expected_counts": _expected_counts(case),
            "test_run_id": test_run_id,
            "report_dir": str(report_dir.relative_to(ROOT)),
            "time_window": {"start": "2024-01-01T00:00:00Z", "end": "2024-01-01T01:00:00Z"},
            "objects": {
                "postgres_schema": pg_schema,
                "postgres_tables": tuple(MEASUREMENT_TABLE_RESOLUTIONS),
                "mongo_database": mongo_db,
                "mongo_collection": mongo_collection,
            },
            "mongo": mongo_readback,
            "postgres": pg_readback,
            "adapter_result": {**asdict(adapter_result), "real_db_writes_executed": True},
            "latency_seconds": {key: round(value, 6) for key, value in timing.items()},
            "cleanup": cleanup_report,
            "cleanup_commands": _cleanup_commands(test_run_id, mongo_db, mongo_collection),
            "claims": {
                "real_mongo_write_readback": True,
                "real_postgres_write_readback": True,
                "production_canonical_mutation": False,
                "paper_complete": False,
            },
        }
        _assert_expected_counts(result)
    finally:
        if cleanup:
            cleanup_report.update(_cleanup_postgres_schema(pg_kwargs, test_run_id))
            if mongo_client is not None:
                cleanup_report.update(_cleanup_mongo_collection(mongo_client, mongo_db, mongo_collection, test_run_id))
        if mongo_client is not None:
            mongo_client.close()

    result["cleanup"] = cleanup_report
    _write_reports(report_dir, result)
    return result


def _default_run_id() -> str:
    return "scratch_" + datetime.now(UTC).strftime("%Y%m%d%H%M%S")


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        values[key.strip()] = value
    return values


def _postgres_connect_kwargs(env: dict[str, str]) -> dict[str, Any]:
    return {
        "host": _required(env, "DB_HOST"),
        "port": int(env.get("DB_PORT", "5432")),
        "dbname": _required(env, "DB_NAME"),
        "user": _required(env, "DB_USER"),
        "password": _required(env, "DB_PASSWORD"),
        "connect_timeout": 10,
    }


def _required(env: dict[str, str], key: str) -> str:
    value = env.get(key)
    if not value:
        raise RuntimeError(f"missing required .env key: {key}")
    return value


def _verify_connections(pg_kwargs: dict[str, Any], mongo_client: MongoClient, mongo_db: str) -> None:
    with psycopg.connect(**pg_kwargs) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_user")
            db_name, user_name = cur.fetchone()
    if db_name != POSTGRES_DATABASE or user_name != POSTGRES_DATABASE:
        raise RuntimeError("postgres connection is not the cms scratch target")
    ping = mongo_client[mongo_db].command("ping")
    if int(ping.get("ok", 0)) != 1:
        raise RuntimeError("mongo ping failed")


def _synthetic_harmonized_docs(test_run_id: str, *, case: str = "two_meter") -> list[dict[str, Any]]:
    if case == "live81_1min_60m":
        return _live81_harmonized_docs(test_run_id)
    if case != "two_meter":
        raise ValueError(f"unsupported scratch integration case: {case}")
    start = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
    docs: list[dict[str, Any]] = []
    for minute in range(60):
        timestamp = start + timedelta(minutes=minute)
        docs.append(
            {
                "test_run_id": test_run_id,
                "meter_urn": "urn:meter:1min",
                "measurement": "active_power",
                "timestamp": timestamp.isoformat(),
                "value": float(minute),
                "source_event_id": f"one-{minute:02d}",
                "native_interval_seconds": 60,
                "cadence_policy_id": "native_1min",
                "aggregation_policy": "mean_non_cumulative",
            }
        )
    for minute in range(0, 60, 5):
        timestamp = start + timedelta(minutes=minute)
        docs.append(
            {
                "test_run_id": test_run_id,
                "meter_urn": "urn:meter:5min",
                "measurement": "active_power",
                "timestamp": timestamp.isoformat(),
                "value": float(minute),
                "source_event_id": f"five-{minute:02d}",
                "native_interval_seconds": 300,
                "cadence_policy_id": "native_5min_to_15min",
                "aggregation_policy": "mean_non_cumulative",
            }
        )
    return docs


def _live81_harmonized_docs(test_run_id: str) -> list[dict[str, Any]]:
    start = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
    docs: list[dict[str, Any]] = []
    for source_idx in range(1, 82):
        meter_urn = f"urn:meter:live81:{source_idx:03d}"
        for minute in range(60):
            timestamp = start + timedelta(minutes=minute)
            docs.append(
                {
                    "test_run_id": test_run_id,
                    "meter_urn": meter_urn,
                    "measurement": "active_power",
                    "timestamp": timestamp.isoformat(),
                    "value": float(source_idx * 1000 + minute),
                    "source_event_id": f"m{source_idx:03d}-{minute:02d}",
                    "native_interval_seconds": 60,
                    "cadence_policy_id": "native_1min",
                    "aggregation_policy": "mean_non_cumulative",
                }
            )
    return docs


def _insert_mongo_docs(client: MongoClient, database: str, collection: str, test_run_id: str, docs: list[dict[str, Any]]) -> None:
    for document in docs:
        validate_mongo_scratch_write(collection=collection, document=document, test_run_id=test_run_id, allow_write=True, env=ALLOW_ENV)
    coll = client[database][collection]
    coll.delete_many({"test_run_id": test_run_id})
    coll.insert_many(docs)
    count = coll.count_documents({"test_run_id": test_run_id})
    if count != len(docs):
        raise RuntimeError(f"mongo insert count mismatch: {count} != {len(docs)}")


def _mongo_readback(client: MongoClient, database: str, collection: str, test_run_id: str) -> dict[str, Any]:
    coll = client[database][collection]
    pipeline = [
        {"$match": {"test_run_id": test_run_id}},
        {
            "$group": {
                "_id": {"meter_urn": "$meter_urn", "measurement": "$measurement"},
                "count": {"$sum": 1},
                "min_ts": {"$min": "$timestamp"},
                "max_ts": {"$max": "$timestamp"},
                "native_interval_seconds": {"$first": "$native_interval_seconds"},
                "cadence_policy_id": {"$first": "$cadence_policy_id"},
            }
        },
        {"$sort": {"_id.meter_urn": 1, "_id.measurement": 1}},
    ]
    by_series = list(coll.aggregate(pipeline))
    return {
        "database": database,
        "collection": collection,
        "raw_count": coll.count_documents({"test_run_id": test_run_id}),
        "by_series": [_plain_mongo_group(row) for row in by_series],
    }


def _plain_mongo_group(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "meter_urn": row["_id"]["meter_urn"],
        "measurement": row["_id"]["measurement"],
        "count": row["count"],
        "min_ts": row["min_ts"],
        "max_ts": row["max_ts"],
        "native_interval_seconds": row.get("native_interval_seconds"),
        "cadence_policy_id": row.get("cadence_policy_id"),
    }


def _apply_postgres_ddl(connect_kwargs: dict[str, Any], test_run_id: str) -> None:
    with psycopg.connect(**connect_kwargs) as conn:
        with conn.cursor() as cur:
            cur.execute(render_scratch_ddl(test_run_id))


def _postgres_readback(connect_kwargs: dict[str, Any], schema: str) -> dict[str, Any]:
    row_counts: dict[str, int] = {}
    row_windows: dict[str, dict[str, str | None]] = {}
    provenance_samples: list[dict[str, Any]] = []
    with psycopg.connect(**connect_kwargs) as conn:
        with conn.cursor() as cur:
            for table in MEASUREMENT_TABLE_RESOLUTIONS:
                cur.execute(
                    sql.SQL("SELECT count(*), min(bucket_ts), max(bucket_ts) FROM {}.{}").format(
                        sql.Identifier(schema), sql.Identifier(table)
                    )
                )
                count, min_ts, max_ts = cur.fetchone()
                row_counts[table] = int(count)
                row_windows[table] = {
                    "min_bucket_ts": min_ts.isoformat() if min_ts else None,
                    "max_bucket_ts": max_ts.isoformat() if max_ts else None,
                }
            cur.execute(
                sql.SQL(
                    """
                    SELECT resolution, meter_urn, measurement, expected_points, observed_points,
                           coverage_ratio, source_native_interval_seconds, cadence_policy_id,
                           target_resolution, expected_points_policy, aggregation_policy
                    FROM {}.measurement_15min
                    ORDER BY source_native_interval_seconds DESC NULLS LAST, meter_urn, bucket_ts
                    LIMIT 8
                    """
                ).format(sql.Identifier(schema))
            )
            for row in cur.fetchall():
                provenance_samples.append(
                    {
                        "resolution": row[0],
                        "meter_urn": row[1],
                        "measurement": row[2],
                        "expected_points": row[3],
                        "observed_points": row[4],
                        "coverage_ratio": float(row[5]),
                        "source_native_interval_seconds": row[6],
                        "cadence_policy_id": row[7],
                        "target_resolution": row[8],
                        "expected_points_policy": row[9],
                        "aggregation_policy": row[10],
                    }
                )
    return {"schema": schema, "row_counts": row_counts, "row_windows": row_windows, "provenance_samples": provenance_samples}


def _pg_value(value: Any) -> Any:
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        return Jsonb(value)
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _assert_expected_counts(result: dict[str, Any]) -> None:
    expected = result["expected_counts"]
    expected_mongo_count = expected["mongo_raw"]
    expected_pg_counts = expected["postgres"]
    if result["mongo"]["raw_count"] != expected_mongo_count:
        raise RuntimeError(f"mongo raw count mismatch: {result['mongo']['raw_count']} != {expected_mongo_count}")
    if result["postgres"]["row_counts"] != expected_pg_counts:
        raise RuntimeError(f"postgres row counts mismatch: {result['postgres']['row_counts']} != {expected_pg_counts}")


def _expected_counts(case: str) -> dict[str, Any]:
    if case == "two_meter":
        return {
            "mongo_raw": 72,
            "postgres": {
                "measurement_1min": 60,
                "measurement_5min": 12,
                "measurement_15min": 8,
                "measurement_1h": 2,
            },
        }
    if case == "live81_1min_60m":
        return {
            "mongo_raw": 4860,
            "postgres": {
                "measurement_1min": 4860,
                "measurement_5min": 972,
                "measurement_15min": 324,
                "measurement_1h": 81,
            },
        }
    raise ValueError(f"unsupported scratch integration case: {case}")


def _cleanup_postgres_schema(connect_kwargs: dict[str, Any], test_run_id: str) -> dict[str, bool]:
    schema = postgres_scratch_schema_name(test_run_id)
    validate_postgres_cleanup_target(database=POSTGRES_DATABASE, schema=schema, test_run_id=test_run_id, allow_write=True, env=ALLOW_ENV)
    with psycopg.connect(**connect_kwargs) as conn:
        with conn.cursor() as cur:
            cur.execute(render_scratch_cleanup_sql(test_run_id))
    return {"postgres_schema_dropped": True}


def _cleanup_mongo_collection(client: MongoClient, database: str, collection: str, test_run_id: str) -> dict[str, bool]:
    validate_mongo_cleanup_target(collection=collection, test_run_id=test_run_id, allow_write=True, env=ALLOW_ENV)
    client[database][collection].drop()
    return {"mongo_collection_dropped": True}


def _cleanup_commands(test_run_id: str, mongo_db: str, mongo_collection: str) -> dict[str, str]:
    return {
        "postgres": render_scratch_cleanup_sql(test_run_id),
        "mongo": f"db.getSiblingDB('{mongo_db}').getCollection('{mongo_collection}').drop()",
    }


def _write_reports(report_dir: Path, result: dict[str, Any]) -> None:
    (report_dir / "scratch_db_integration_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (report_dir / "scratch_db_integration_report.md").write_text(_render_markdown(result), encoding="utf-8")


def _render_markdown(result: dict[str, Any]) -> str:
    latency = result["latency_seconds"]
    pg_counts = result["postgres"]["row_counts"]
    mongo = result["mongo"]
    objects = result["objects"]
    expected = result["expected_counts"]
    samples = result["postgres"]["provenance_samples"]
    sample_lines = "\n".join(
        f"| {row['meter_urn']} | {row['resolution']} | {row['expected_points']} | {row['observed_points']} | {row['source_native_interval_seconds']} | {row['cadence_policy_id']} |"
        for row in samples
    )
    return f"""# Actual Scratch DB Integration Report

**Status:** {result['status']}
**Scope:** {result['scope']}
**Case:** `{result['case']}`
**Test run ID:** `{result['test_run_id']}`
**Time window:** `{result['time_window']['start']}` to `{result['time_window']['end']}`
**Production/canonical mutation:** none

## Scratch objects

| Store | Object |
|---|---|
| MongoDB | `{objects['mongo_database']}.{objects['mongo_collection']}` |
| PostgreSQL schema | `{objects['postgres_schema']}` |
| PostgreSQL tables | `{', '.join(objects['postgres_tables'])}` |

## Read-back counts

| Store/Table | Count |
|---|---:|
| MongoDB raw docs | {mongo['raw_count']} |
| PostgreSQL measurement_1min | {pg_counts['measurement_1min']} |
| PostgreSQL measurement_5min | {pg_counts['measurement_5min']} |
| PostgreSQL measurement_15min | {pg_counts['measurement_15min']} |
| PostgreSQL measurement_1h | {pg_counts['measurement_1h']} |

Expected counts passed: MongoDB `{expected['mongo_raw']}`, PostgreSQL `{expected['postgres']['measurement_1min']}/{expected['postgres']['measurement_5min']}/{expected['postgres']['measurement_15min']}/{expected['postgres']['measurement_1h']}`.

## Provenance samples from PostgreSQL 15min rows

| meter_urn | resolution | expected_points | observed_points | native_interval_seconds | cadence_policy_id |
|---|---:|---:|---:|---:|---|
{sample_lines}

## Latency seconds

| Stage | Seconds |
|---|---:|
| PostgreSQL DDL | {latency['postgres_ddl_sec']} |
| MongoDB insert | {latency['mongo_insert_sec']} |
| MongoDB read-back | {latency['mongo_readback_sec']} |
| Processor to PostgreSQL writes | {latency['processor_to_postgres_sec']} |
| Mongo visible to PostgreSQL outputs | {latency['mongo_visible_to_pg_outputs_sec']} |
| PostgreSQL read-back | {latency['postgres_readback_sec']} |
| Total | {latency['total_sec']} |

This is a scratch replay integration latency measurement. It proves real DB wiring, row-contract behavior, and the stated count gate, not production throughput or paper-complete correction logic.

## Cleanup commands

```sql
{result['cleanup_commands']['postgres']}
```

```javascript
{result['cleanup_commands']['mongo']}
```

Cleanup executed in this run: PostgreSQL `{result['cleanup']['postgres_schema_dropped']}`, MongoDB `{result['cleanup']['mongo_collection_dropped']}`.
"""


if __name__ == "__main__":
    main()
