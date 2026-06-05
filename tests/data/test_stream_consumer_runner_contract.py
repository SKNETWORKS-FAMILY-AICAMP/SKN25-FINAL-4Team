from __future__ import annotations

from cms.contracts.ingestion import MEASUREMENT_DLQ_TOPIC, MEASUREMENT_RAW_SCHEMA_VERSION
from cms.data.kafka_adapter import InMemoryKafkaProducer
from cms.data.postgres_event_writer import InMemoryPostgresEventWriter
from cms.data.stream_consumer_runner import process_kafka_message


def _message(**value_overrides: object) -> dict[str, object]:
    value = {
        "schema_version": MEASUREMENT_RAW_SCHEMA_VERSION,
        "source_system": "sensor_gateway",
        "source_event_id": "evt-001",
        "meter_urn": "meter:001",
        "measurement": "P",
        "event_ts": "2026-06-04T00:00:00+00:00",
        "value_text": "10.5",
        "value_numeric": 10.5,
        "unit": "W",
        "received_at": "2026-06-04T00:00:01+00:00",
        "raw_payload_hash": "b" * 64,
    }
    value.update(value_overrides)
    return {"topic": "measurement_raw_v1", "partition": 0, "offset": 5, "key": "meter:001|P", "value": value}


def test_process_kafka_message_writes_postgres_then_commits_offset() -> None:
    writer = InMemoryPostgresEventWriter()
    dlq = InMemoryKafkaProducer()

    result = process_kafka_message(_message(), writer=writer, dlq_producer=dlq, consumed_at="2026-06-04T00:00:02+00:00")

    assert result.decision.action == "insert_event"
    assert result.decision.commit_offset is True
    assert result.write_result is not None
    assert result.write_result.succeeded is True
    assert result.dlq_ack is None
    assert writer.rows["source_event|sensor_gateway|evt-001"]["target_table"] == "live.measurement_event"


def test_process_kafka_message_duplicate_business_key_is_noop_and_commits() -> None:
    writer = InMemoryPostgresEventWriter()
    dlq = InMemoryKafkaProducer()

    first = process_kafka_message(_message(offset=5), writer=writer, dlq_producer=dlq)
    second = process_kafka_message(_message(offset=6), writer=writer, dlq_producer=dlq)

    assert first.decision.action == "insert_event"
    assert second.decision.action == "idempotent_noop"
    assert second.decision.commit_offset is True
    assert len(writer.rows) == 1


def test_process_kafka_message_db_failure_retries_without_commit() -> None:
    writer = InMemoryPostgresEventWriter(fail=True)

    result = process_kafka_message(_message(), writer=writer, dlq_producer=InMemoryKafkaProducer())

    assert result.decision.action == "retry"
    assert result.decision.commit_offset is False
    assert result.decision.failure_stage == "db_transaction"


def test_process_kafka_message_writer_exception_retries_without_commit() -> None:
    class RaisingWriter:
        def insert_measurement_event(self, payload: dict[str, object]):  # noqa: ANN201
            raise RuntimeError("db connection unavailable")

    result = process_kafka_message(_message(), writer=RaisingWriter(), dlq_producer=InMemoryKafkaProducer())

    assert result.decision.action == "retry"
    assert result.decision.commit_offset is False
    assert result.decision.failure_stage == "db_transaction"
    assert result.decision.reason == "db connection unavailable"


def test_process_kafka_message_non_object_value_goes_to_dlq_without_throwing() -> None:
    dlq = InMemoryKafkaProducer()
    message = {"topic": "measurement_raw_v1", "partition": 0, "offset": 8, "key": "meter:001|P", "value": "not an object"}

    result = process_kafka_message(message, writer=InMemoryPostgresEventWriter(), dlq_producer=dlq)

    assert result.decision.action == "send_to_dlq"
    assert result.decision.commit_offset is True
    assert "kafka_value_not_object" in result.decision.validation_errors
    assert dlq.to_kafka_message(0)["value"]["raw_value"] == {"_invalid_kafka_value": "not an object"}


def test_process_kafka_message_validation_failure_sends_dlq_then_commits() -> None:
    dlq = InMemoryKafkaProducer()

    result = process_kafka_message(_message(value_numeric="bad", value_text=None), writer=InMemoryPostgresEventWriter(), dlq_producer=dlq)

    assert result.decision.action == "send_to_dlq"
    assert result.decision.commit_offset is True
    assert result.dlq_ack is not None
    assert result.dlq_ack["topic"] == MEASUREMENT_DLQ_TOPIC
    assert dlq.to_kafka_message(0)["value"]["validation_errors"] == ("value_numeric_invalid", "value_required")


def test_process_kafka_message_dlq_failure_does_not_commit_offset() -> None:
    result = process_kafka_message(
        _message(value_numeric="bad", value_text=None),
        writer=InMemoryPostgresEventWriter(),
        dlq_producer=InMemoryKafkaProducer(available=False),
    )

    assert result.decision.action == "send_to_dlq"
    assert result.decision.commit_offset is False
    assert result.decision.failure_stage == "dlq_publish"
    assert result.dlq_ack is None
