from __future__ import annotations

from cms.data.kafka_adapter import InMemoryKafkaProducer
from cms.data.postgres_event_writer import InMemoryPostgresEventWriter
from cms.data.stream_consumer_runner import process_kafka_message
from cms.service.api import make_ingest_measurement_payload


def _api_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "source_system": "sensor_gateway",
        "source_event_id": "evt-001",
        "meter_urn": "meter:001",
        "measurement": "P",
        "event_ts": "2026-06-04T00:00:00+00:00",
        "value_text": "10.5",
        "value_numeric": 10.5,
        "unit": "W",
        "received_at": "2026-06-04T00:00:01+00:00",
    }
    payload.update(overrides)
    return payload


def test_fake_ingestion_e2e_fastapi_to_kafka_to_postgres_commit() -> None:
    producer = InMemoryKafkaProducer()
    writer = InMemoryPostgresEventWriter()
    dlq = InMemoryKafkaProducer()

    accepted = make_ingest_measurement_payload(_api_payload(), producer=producer)
    processed = process_kafka_message(producer.to_kafka_message(0), writer=writer, dlq_producer=dlq)

    assert accepted["status_code"] == 202
    assert accepted["postgres_write_attempted"] is False
    assert processed.decision.action == "insert_event"
    assert processed.decision.commit_offset is True
    assert list(writer.rows) == ["source_event|sensor_gateway|evt-001"]
    assert dlq.published_records == []


def test_fake_ingestion_e2e_duplicate_replay_is_idempotent_noop() -> None:
    producer = InMemoryKafkaProducer()
    writer = InMemoryPostgresEventWriter()
    dlq = InMemoryKafkaProducer()

    make_ingest_measurement_payload(_api_payload(), producer=producer)
    make_ingest_measurement_payload(_api_payload(), producer=producer)

    first = process_kafka_message(producer.to_kafka_message(0), writer=writer, dlq_producer=dlq)
    second = process_kafka_message(producer.to_kafka_message(1), writer=writer, dlq_producer=dlq)

    assert first.decision.action == "insert_event"
    assert second.decision.action == "idempotent_noop"
    assert second.decision.commit_offset is True
    assert len(writer.rows) == 1


def test_fake_ingestion_e2e_api_validation_failure_stops_before_kafka() -> None:
    producer = InMemoryKafkaProducer()

    rejected = make_ingest_measurement_payload(_api_payload(meter_urn="", value_numeric=None, value_text=None), producer=producer)

    assert rejected["status_code"] == 422
    assert producer.published_records == []


def test_fake_ingestion_e2e_poison_kafka_message_goes_to_dlq_not_postgres() -> None:
    writer = InMemoryPostgresEventWriter()
    dlq = InMemoryKafkaProducer()
    poison_message = {
        "topic": "measurement_raw_v1",
        "partition": 0,
        "offset": 9,
        "key": "meter:001|P",
        "value": _api_payload(value_text=None, value_numeric="not-a-number"),
    }

    processed = process_kafka_message(poison_message, writer=writer, dlq_producer=dlq)

    assert processed.decision.action == "send_to_dlq"
    assert processed.decision.commit_offset is True
    assert writer.rows == {}
    assert dlq.to_kafka_message(0)["topic"] == "measurement_dead_letter_v1"
