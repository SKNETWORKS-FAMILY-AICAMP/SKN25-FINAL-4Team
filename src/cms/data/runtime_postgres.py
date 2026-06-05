"""Lazy runtime PostgreSQL writer for live.measurement_event.

The module does not import psycopg at import time. Runtime service code creates
this writer only after the AWS smoke gate is ready.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib import import_module

from cms.data.postgres_event_writer import (
    LIVE_MEASUREMENT_EVENT_TABLE,
    PostgresWriteResult,
    make_measurement_event_insert_command,
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


@dataclass(frozen=True)
class PsycopgPostgresEventWriter:
    config: PsycopgConnectionConfig

    def insert_measurement_event(self, payload: dict[str, object]) -> PostgresWriteResult:
        command = make_measurement_event_insert_command(payload)
        try:
            psycopg = import_module("psycopg")
            with psycopg.connect(**self.config.connect_kwargs()) as conn:
                with conn.cursor() as cur:
                    cur.execute(command.sql, command.params)
                    rows_affected = int(cur.rowcount or 0)
                conn.commit()
        except Exception as exc:  # noqa: BLE001 - writer maps runtime DB errors to retry/no-commit result.
            return PostgresWriteResult(
                succeeded=False,
                duplicate_event=False,
                rows_affected=0,
                error=_redact(str(exc), self.config.password),
            )
        return PostgresWriteResult(succeeded=True, duplicate_event=rows_affected == 0, rows_affected=rows_affected)


def load_postgres_config_from_env(env: dict[str, str] | None = None) -> PsycopgConnectionConfig:
    values = env or os.environ
    return PsycopgConnectionConfig(
        host=values.get("POSTGRES_HOST", "172.31.47.236"),
        port=int(values.get("POSTGRES_PORT", "5432")),
        dbname=values.get("POSTGRES_DB", "cms"),
        user=values.get("POSTGRES_USER", "cms"),
        password=values.get("POSTGRES_PASSWORD") or None,
        sslmode=values.get("POSTGRES_SSLMODE", "disable"),
    )


def create_psycopg_event_writer(env: dict[str, str] | None = None) -> PsycopgPostgresEventWriter:
    return PsycopgPostgresEventWriter(config=load_postgres_config_from_env(env))


def _redact(message: str, secret: str | None) -> str:
    cleaned = message
    if secret:
        cleaned = cleaned.replace(secret, "[REDACTED]")
    return cleaned


__all__ = [
    "LIVE_MEASUREMENT_EVENT_TABLE",
    "PsycopgConnectionConfig",
    "PsycopgPostgresEventWriter",
    "create_psycopg_event_writer",
    "load_postgres_config_from_env",
]
