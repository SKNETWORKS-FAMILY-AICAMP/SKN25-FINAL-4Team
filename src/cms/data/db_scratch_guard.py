from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

ALLOW_DB_SCRATCH_WRITE_ENV = "ALLOW_DB_SCRATCH_WRITE"
POSTGRES_DATABASE = "cms"
POSTGRES_SCHEMA_PREFIX = "cms_scratch_"
MONGO_COLLECTION_PREFIX = "test_measurement_raw_"
ALLOWED_POSTGRES_TABLES = (
    "measurement_1min",
    "measurement_5min",
    "measurement_15min",
    "measurement_1h",
    "latency_events",
    "qa_metrics",
)

_TEST_RUN_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,47}\Z")
_FORBIDDEN_TEST_RUN_IDS = {"live", "prod", "production", "canonical"}
_FORBIDDEN_TEST_RUN_ID_PREFIXES = ("live_", "prod_", "production_", "canonical_")


class ScratchGuardError(ValueError):
    """Raised when a scratch DB target is not explicitly safe."""


@dataclass(frozen=True)
class PostgresScratchTarget:
    database: str
    schema: str
    table: str | None
    test_run_id: str


@dataclass(frozen=True)
class MongoScratchTarget:
    collection: str
    test_run_id: str


def validate_write_allowed(*, allow_write: bool, env: Mapping[str, str] | None = None) -> bool:
    """Return True only when both runtime and environment write gates are enabled."""
    current_env = os.environ if env is None else env
    if current_env.get(ALLOW_DB_SCRATCH_WRITE_ENV) != "1":
        raise ScratchGuardError(f"{ALLOW_DB_SCRATCH_WRITE_ENV} must be exactly '1' for scratch writes")
    if allow_write is not True:
        raise ScratchGuardError("runtime allow_write must be True for scratch writes")
    return True


def validate_test_run_id(test_run_id: str) -> str:
    """Validate and return a safe test run identifier for generated DB names."""
    if not isinstance(test_run_id, str) or _TEST_RUN_ID_RE.fullmatch(test_run_id) is None:
        raise ScratchGuardError("test_run_id must match [a-z][a-z0-9_]{0,47}")
    if test_run_id in _FORBIDDEN_TEST_RUN_IDS or test_run_id.startswith(_FORBIDDEN_TEST_RUN_ID_PREFIXES):
        raise ScratchGuardError("test_run_id must be scratch/test only, not live/prod/canonical")
    return test_run_id


def postgres_scratch_schema_name(test_run_id: str) -> str:
    return f"{POSTGRES_SCHEMA_PREFIX}{validate_test_run_id(test_run_id)}"


def mongo_scratch_collection_name(test_run_id: str) -> str:
    return f"{MONGO_COLLECTION_PREFIX}{validate_test_run_id(test_run_id)}"


def validate_postgres_scratch_write(
    *,
    database: str,
    schema: str,
    table: str,
    row: Mapping[str, Any],
    test_run_id: str,
    allow_write: bool,
    env: Mapping[str, str] | None = None,
) -> PostgresScratchTarget:
    validate_write_allowed(allow_write=allow_write, env=env)
    safe_test_run_id = validate_test_run_id(test_run_id)
    _validate_postgres_database(database)
    _validate_postgres_schema(schema, safe_test_run_id)
    _validate_postgres_table(table)
    _validate_payload_test_run_id(row, safe_test_run_id, "row")
    return PostgresScratchTarget(database=database, schema=schema, table=table, test_run_id=safe_test_run_id)


def validate_mongo_scratch_write(
    *,
    collection: str,
    document: Mapping[str, Any],
    test_run_id: str,
    allow_write: bool,
    env: Mapping[str, str] | None = None,
) -> MongoScratchTarget:
    validate_write_allowed(allow_write=allow_write, env=env)
    safe_test_run_id = validate_test_run_id(test_run_id)
    _validate_mongo_collection(collection, safe_test_run_id)
    _validate_payload_test_run_id(document, safe_test_run_id, "document")
    return MongoScratchTarget(collection=collection, test_run_id=safe_test_run_id)


def validate_postgres_cleanup_target(
    *,
    database: str,
    schema: str,
    test_run_id: str,
    allow_write: bool,
    env: Mapping[str, str] | None = None,
) -> PostgresScratchTarget:
    validate_write_allowed(allow_write=allow_write, env=env)
    safe_test_run_id = validate_test_run_id(test_run_id)
    _validate_postgres_database(database)
    _validate_postgres_schema(schema, safe_test_run_id)
    return PostgresScratchTarget(database=database, schema=schema, table=None, test_run_id=safe_test_run_id)


def validate_mongo_cleanup_target(
    *,
    collection: str,
    test_run_id: str,
    allow_write: bool,
    env: Mapping[str, str] | None = None,
) -> MongoScratchTarget:
    validate_write_allowed(allow_write=allow_write, env=env)
    safe_test_run_id = validate_test_run_id(test_run_id)
    _validate_mongo_collection(collection, safe_test_run_id)
    return MongoScratchTarget(collection=collection, test_run_id=safe_test_run_id)


def _validate_postgres_database(database: str) -> None:
    if database != POSTGRES_DATABASE:
        raise ScratchGuardError("postgres database must be cms")


def _validate_postgres_schema(schema: str, test_run_id: str) -> None:
    expected = postgres_scratch_schema_name(test_run_id)
    if schema != expected:
        raise ScratchGuardError(f"postgres schema must be exactly {expected}")


def _validate_postgres_table(table: str) -> None:
    if table not in ALLOWED_POSTGRES_TABLES:
        allowed = ", ".join(ALLOWED_POSTGRES_TABLES)
        raise ScratchGuardError(f"postgres table must be one of: {allowed}")


def _validate_mongo_collection(collection: str, test_run_id: str) -> None:
    expected = mongo_scratch_collection_name(test_run_id)
    if collection != expected:
        raise ScratchGuardError(f"mongo collection must be exactly {expected}")


def _validate_payload_test_run_id(payload: Mapping[str, Any], test_run_id: str, payload_name: str) -> None:
    if not isinstance(payload, Mapping) or payload.get("test_run_id") != test_run_id:
        raise ScratchGuardError(f"{payload_name} test_run_id must match {test_run_id}")
