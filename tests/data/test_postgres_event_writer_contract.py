from __future__ import annotations

from pathlib import Path
import re

from cms.data.postgres_event_writer import (
    InMemoryPostgresEventWriter,
    PostgresWriteResult,
    make_measurement_event_insert_command,
)


ROOT = Path(__file__).resolve().parents[2]


def _postgres_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "target_table": "live.measurement_event",
        "event_id": "source_event|sensor_gateway|evt-001",
        "source_event_id": "evt-001",
        "meter_urn": "meter:001",
        "measurement": "P",
        "event_ts": "2026-06-04T00:00:00+00:00",
        "value_text": "10.5",
        "value_numeric": 10.5,
        "unit": "W",
        "source_layer": "kafka.measurement_raw_v1",
        "source_ref": "measurement_raw_v1:0:0",
        "ingested_at": "2026-06-04T00:00:02+00:00",
        "received_at": "2026-06-04T00:00:01+00:00",
        "raw_payload_hash": "a" * 64,
        "policy_lookup_status": "pending",
        "kafka_topic": "measurement_raw_v1",
        "kafka_partition": 0,
        "kafka_offset": 0,
        "kafka_key": "meter:001|P",
        "consumer_group": "postgres-live-ingest",
        "consumed_at": "2026-06-04T00:00:02+00:00",
        "schema_version": "measurement_raw_v1",
        "business_idempotency_key": "source_event|sensor_gateway|evt-001",
    }
    payload.update(overrides)
    return payload


def test_measurement_event_insert_command_is_live_only_and_idempotent() -> None:
    command = make_measurement_event_insert_command(_postgres_payload())

    assert command.target_table == "live.measurement_event"
    assert "INSERT INTO live.measurement_event" in command.sql
    assert "ON CONFLICT (event_id) DO NOTHING" in command.sql
    assert "canonical." not in command.sql
    assert command.params["event_id"] == "source_event|sensor_gateway|evt-001"
    assert command.params["business_idempotency_key"] == "source_event|sensor_gateway|evt-001"


def test_measurement_event_insert_columns_exist_in_live_schema_draft() -> None:
    command = make_measurement_event_insert_command(_postgres_payload())
    ddl = (ROOT / "scripts/migrations/live_schema_draft.sql").read_text(encoding="utf-8")
    match = re.search(r"CREATE TABLE IF NOT EXISTS live\.measurement_event \((.*?)\n\);", ddl, re.S)
    assert match is not None
    ddl_columns = {
        line.strip().split()[0]
        for line in match.group(1).splitlines()
        if line.strip() and not line.strip().startswith("CONSTRAINT")
    }

    assert set(command.params) <= ddl_columns


def test_measurement_event_insert_command_rejects_non_live_target() -> None:
    try:
        make_measurement_event_insert_command(_postgres_payload(target_table="canonical.measurement_1min"))
    except ValueError as exc:
        assert "live.measurement_event" in str(exc)
    else:  # pragma: no cover - explicit failure branch for contract clarity
        raise AssertionError("expected ValueError")


def test_in_memory_postgres_writer_models_insert_then_duplicate_noop() -> None:
    writer = InMemoryPostgresEventWriter()
    payload = _postgres_payload()

    first = writer.insert_measurement_event(payload)
    second = writer.insert_measurement_event(payload)

    assert first == PostgresWriteResult(succeeded=True, duplicate_event=False, rows_affected=1)
    assert second == PostgresWriteResult(succeeded=True, duplicate_event=True, rows_affected=0)
    assert writer.rows["source_event|sensor_gateway|evt-001"]["kafka_offset"] == 0


def test_in_memory_postgres_writer_models_transaction_failure_without_row() -> None:
    writer = InMemoryPostgresEventWriter(fail=True)

    result = writer.insert_measurement_event(_postgres_payload())

    assert result.succeeded is False
    assert result.duplicate_event is False
    assert result.rows_affected == 0
    assert result.error == "postgres transaction failed"
    assert writer.rows == {}
