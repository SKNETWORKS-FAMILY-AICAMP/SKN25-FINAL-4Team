"""Lazy runtime PostgreSQL writer for live.measurement_event.

The module does not import psycopg at import time. Runtime service code creates
this writer only after the AWS smoke gate is ready.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import import_module

from cms.data.live_bucket_queue_runner import (
    LiveBucketQueueWorkerCommand,
    LiveBucketQueueWorkerResult,
    live_bucket_queue_result_from_count_row,
    make_live_bucket_queue_worker_command,
)
from cms.data.postgres_event_writer import (
    LIVE_MEASUREMENT_EVENT_TABLE,
    MEASUREMENT_EVENT_WRITE_TARGET_TABLE,
    MEASUREMENT_INGESTION_TABLE_PATH,
    PostgresWriteResult,
    make_measurement_event_insert_command,
)

EDGE_RUNTIME_PROFILE_ENV = "CMS_RUNTIME_PROFILE"
EDGE_RUNTIME_PROFILE = "edge"
AWS_PRIVATE_POSTGRES_HOST_PREFIX = "172.31."
AWS_STREAM_HOST_MARKERS = frozenset({"cms-stream", "db-stream", "43.202.114.249", "172.31.26.245"})

_DUPLICATE_CONSTRAINTS = frozenset(
    {
        "measurement_event_business_idempotency_uq",
        "measurement_event_kafka_offset_uq",
    }
)


@dataclass(frozen=True)
class PsycopgConnectionConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str | None = None
    sslmode: str = "disable"
    connect_timeout: int = 5

    def connect_kwargs(self) -> dict[str, object]:
        kwargs: dict[str, object] = {
            "host": self.host,
            "port": self.port,
            "dbname": self.dbname,
            "user": self.user,
            "sslmode": self.sslmode,
            "connect_timeout": self.connect_timeout,
        }
        if self.password:
            kwargs["password"] = self.password
        return kwargs


@dataclass
class PsycopgPostgresEventWriter:
    config: PsycopgConnectionConfig
    _conn: object | None = field(default=None, init=False, repr=False)

    def _connection(self) -> object:
        if self._conn is None or bool(getattr(self._conn, "closed", False)):
            psycopg = import_module("psycopg")
            self._conn = psycopg.connect(**self.config.connect_kwargs())
        return self._conn

    def insert_measurement_event(self, payload: dict[str, object]) -> PostgresWriteResult:
        command = make_measurement_event_insert_command(payload)
        try:
            conn = self._connection()
            with conn.cursor() as cur:  # type: ignore[attr-defined]
                cur.execute(command.sql, command.params)
                rows_affected = int(cur.rowcount or 0)
            conn.commit()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - writer maps runtime DB errors to retry/no-commit result.
            self._rollback_or_close()
            if _is_duplicate_conflict(exc):
                return PostgresWriteResult(succeeded=True, duplicate_event=True, rows_affected=0)
            return PostgresWriteResult(
                succeeded=False,
                duplicate_event=False,
                rows_affected=0,
                error=_redact(str(exc), self.config.password),
            )
        return PostgresWriteResult(succeeded=True, duplicate_event=rows_affected == 0, rows_affected=rows_affected)

    def _rollback_or_close(self) -> None:
        if self._conn is None:
            return
        rollback = getattr(self._conn, "rollback", None)
        if not callable(rollback):
            self.close()
            return
        try:
            rollback()
        except Exception:  # noqa: BLE001 - broken runtime connections should be discarded.
            self.close()

    def close(self) -> None:
        if self._conn is None:
            return
        try:
            close = getattr(self._conn, "close", None)
            if callable(close):
                close()
        finally:
            self._conn = None


@dataclass(frozen=True)
class PsycopgLiveBucketQueueWorker:
    """Runtime executor for one bounded ``live.bucket_queue`` worker pass.

    Psycopg is imported lazily only when ``run_once`` is called by a gated
    runtime path. The SQL itself remains owned by the import-safe
    ``live_bucket_queue_runner`` contract.
    """

    config: PsycopgConnectionConfig

    def run_once(
        self,
        command: LiveBucketQueueWorkerCommand | None = None,
        *,
        batch_size: int = 100,
        worker_id: str = "live-bucket-queue-worker-runtime",
        job_kinds: tuple[str, ...] | list[str] | None = None,
        resolutions: tuple[str, ...] | list[str] | None = None,
        min_coverage_ratio: float = 0.0,
    ) -> LiveBucketQueueWorkerResult:
        worker_command = command or make_live_bucket_queue_worker_command(
            batch_size=batch_size,
            worker_id=worker_id,
            job_kinds=job_kinds,
            resolutions=resolutions,
            min_coverage_ratio=min_coverage_ratio,
        )
        try:
            psycopg = import_module("psycopg")
            rows = import_module("psycopg.rows")
            with psycopg.connect(**self.config.connect_kwargs(), row_factory=rows.dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute(worker_command.sql, worker_command.params)
                    row = cur.fetchone()
                conn.commit()
        except Exception as exc:  # noqa: BLE001 - operational runner must surface redacted DB/client errors.
            raise RuntimeError(_redact(str(exc), self.config.password)) from exc
        if row is None:
            raise RuntimeError("live.bucket_queue worker returned no count row")
        return live_bucket_queue_result_from_count_row(row)


def load_postgres_config_from_env(env: dict[str, str] | None = None) -> PsycopgConnectionConfig:
    values = env or os.environ
    host = resolve_postgres_host(values)
    return PsycopgConnectionConfig(
        host=host,
        port=int(values.get("POSTGRES_PORT") or values.get("DB_PORT", "5432")),
        dbname=values.get("POSTGRES_DB") or values.get("DB_NAME", "cms"),
        user=values.get("POSTGRES_USER") or values.get("DB_USER", "cms"),
        password=values.get("POSTGRES_PASSWORD") or values.get("DB_PASSWORD") or None,
        sslmode=values.get("POSTGRES_SSLMODE") or values.get("DB_SSLMODE", "disable"),
    )


def create_psycopg_event_writer(env: dict[str, str] | None = None) -> PsycopgPostgresEventWriter:
    return PsycopgPostgresEventWriter(config=load_postgres_config_from_env(env))


def create_psycopg_live_bucket_queue_worker(env: dict[str, str] | None = None) -> PsycopgLiveBucketQueueWorker:
    return PsycopgLiveBucketQueueWorker(config=load_postgres_config_from_env(env))


def resolve_postgres_host(env: Mapping[str, str] | None = None) -> str:
    values = env or os.environ
    host = (values.get("POSTGRES_HOST") or values.get("DB_HOST") or "").strip()
    if is_edge_runtime(values):
        validate_edge_postgres_host(host)
        return host
    if not host:
        raise RuntimeError("POSTGRES_HOST must be set explicitly for PostgreSQL runtime")
    return host


def is_edge_runtime(env: Mapping[str, str] | None = None) -> bool:
    values = env or os.environ
    return values.get(EDGE_RUNTIME_PROFILE_ENV) == EDGE_RUNTIME_PROFILE


def validate_edge_postgres_host(host: str | None) -> None:
    cleaned = (host or "").strip()
    if not cleaned:
        raise RuntimeError("POSTGRES_HOST must be set explicitly for edge runtime")
    if cleaned.startswith(AWS_PRIVATE_POSTGRES_HOST_PREFIX):
        raise RuntimeError("edge runtime must not point POSTGRES_HOST at AWS private VPC hosts")
    if any(marker in cleaned for marker in AWS_STREAM_HOST_MARKERS):
        raise RuntimeError("edge runtime must not point POSTGRES_HOST at AWS cms-stream hosts")


def _is_duplicate_conflict(exc: Exception) -> bool:
    constraint_name = getattr(getattr(exc, "diag", None), "constraint_name", None)
    if constraint_name in _DUPLICATE_CONSTRAINTS:
        return True
    message = str(exc)
    return any(name in message for name in _DUPLICATE_CONSTRAINTS)


def _redact(message: str, secret: str | None) -> str:
    cleaned = message
    if secret:
        cleaned = cleaned.replace(secret, "[REDACTED]")
    return cleaned


__all__ = [
    "LIVE_MEASUREMENT_EVENT_TABLE",
    "MEASUREMENT_EVENT_WRITE_TARGET_TABLE",
    "MEASUREMENT_INGESTION_TABLE_PATH",
    "PsycopgConnectionConfig",
    "PsycopgLiveBucketQueueWorker",
    "PsycopgPostgresEventWriter",
    "create_psycopg_event_writer",
    "create_psycopg_live_bucket_queue_worker",
    "is_edge_runtime",
    "load_postgres_config_from_env",
    "resolve_postgres_host",
    "validate_edge_postgres_host",
]
