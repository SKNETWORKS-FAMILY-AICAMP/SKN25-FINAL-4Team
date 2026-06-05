"""Import-safe PostgreSQL live.measurement_event writer contract.

No PostgreSQL client is imported here. The module defines the idempotent insert
shape and local in-memory writer used by runner/dry-run tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

LIVE_MEASUREMENT_EVENT_TABLE = "live.measurement_event"

_INSERT_COLUMNS = (
    "event_id",
    "source_event_id",
    "meter_urn",
    "measurement",
    "event_ts",
    "value_text",
    "value_numeric",
    "unit",
    "source_layer",
    "source_ref",
    "ingested_at",
    "received_at",
    "raw_payload_hash",
    "policy_lookup_status",
    "kafka_topic",
    "kafka_partition",
    "kafka_offset",
    "kafka_key",
    "consumer_group",
    "consumed_at",
    "schema_version",
    "business_idempotency_key",
)


@dataclass(frozen=True)
class PostgresInsertCommand:
    target_table: str
    sql: str
    params: dict[str, object]


@dataclass(frozen=True)
class PostgresWriteResult:
    succeeded: bool
    duplicate_event: bool
    rows_affected: int
    error: str | None = None


class PostgresEventWriterLike(Protocol):
    """Minimal writer protocol for the Kafka consumer runner shell."""

    def insert_measurement_event(self, payload: dict[str, object]) -> PostgresWriteResult: ...


def _require_live_measurement_event(payload: dict[str, object]) -> None:
    target = payload.get("target_table")
    if target != LIVE_MEASUREMENT_EVENT_TABLE:
        raise ValueError(f"target_table must be {LIVE_MEASUREMENT_EVENT_TABLE}")


def make_measurement_event_insert_command(payload: dict[str, object]) -> PostgresInsertCommand:
    """Build the idempotent live.measurement_event insert command.

    ``event_id`` is the business idempotency key. Kafka topic/partition/offset
    remains transport metadata and is not used as the business identity.
    """

    _require_live_measurement_event(payload)
    values = ", ".join(f"%({column})s" for column in _INSERT_COLUMNS)
    columns = ", ".join(_INSERT_COLUMNS)
    sql = (
        f"INSERT INTO {LIVE_MEASUREMENT_EVENT_TABLE} ({columns}) "
        f"VALUES ({values}) "
        "ON CONFLICT (event_id) DO NOTHING"
    )
    params = {column: payload.get(column) for column in _INSERT_COLUMNS}
    return PostgresInsertCommand(target_table=LIVE_MEASUREMENT_EVENT_TABLE, sql=sql, params=params)


@dataclass
class InMemoryPostgresEventWriter:
    """Mocked PostgreSQL writer with idempotent business-key semantics."""

    fail: bool = False
    rows: dict[str, dict[str, object]] | None = None

    def __post_init__(self) -> None:
        if self.rows is None:
            self.rows = {}

    def insert_measurement_event(self, payload: dict[str, object]) -> PostgresWriteResult:
        if self.fail:
            return PostgresWriteResult(succeeded=False, duplicate_event=False, rows_affected=0, error="postgres transaction failed")
        command = make_measurement_event_insert_command(payload)
        event_id = command.params.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            return PostgresWriteResult(succeeded=False, duplicate_event=False, rows_affected=0, error="event_id required")
        assert self.rows is not None
        if event_id in self.rows:
            return PostgresWriteResult(succeeded=True, duplicate_event=True, rows_affected=0)
        self.rows[event_id] = dict(payload)
        return PostgresWriteResult(succeeded=True, duplicate_event=False, rows_affected=1)


__all__ = [
    "InMemoryPostgresEventWriter",
    "LIVE_MEASUREMENT_EVENT_TABLE",
    "PostgresEventWriterLike",
    "PostgresInsertCommand",
    "PostgresWriteResult",
    "make_measurement_event_insert_command",
]
