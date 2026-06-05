from __future__ import annotations

import sys

from cms.contracts.ingestion import MEASUREMENT_RAW_TOPIC, measurement_raw_event_from_mapping
from cms.data.kafka_adapter import (
    InMemoryKafkaProducer,
    KafkaProducerUnavailable,
    build_kafka_publish_request,
    create_runtime_kafka_producer,
)


def _payload(**overrides: object) -> dict[str, object]:
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


def test_kafka_adapter_module_does_not_import_real_kafka_at_import_time() -> None:
    assert "kafka" not in sys.modules
    assert "confluent_kafka" not in sys.modules


def test_build_kafka_publish_request_uses_phase1_topic_key_and_raw_value() -> None:
    event = measurement_raw_event_from_mapping(_payload())

    request = build_kafka_publish_request(event)

    assert request.topic == MEASUREMENT_RAW_TOPIC
    assert request.key == "meter:001|P"
    assert request.value["schema_version"] == "measurement_raw_v1"
    assert request.value["raw_payload_hash"] == event.raw_payload_hash


def test_in_memory_kafka_producer_records_publish_and_exposes_envelope() -> None:
    producer = InMemoryKafkaProducer()
    event = measurement_raw_event_from_mapping(_payload())
    request = build_kafka_publish_request(event)

    ack = producer.produce(topic=request.topic, key=request.key, value=request.value)
    envelope = producer.to_kafka_message(0)

    assert ack == {"acknowledged": True, "topic": MEASUREMENT_RAW_TOPIC, "partition": 0, "offset": 0}
    assert envelope["topic"] == MEASUREMENT_RAW_TOPIC
    assert envelope["key"] == "meter:001|P"
    assert envelope["value"]["source_event_id"] == "evt-001"


def test_in_memory_kafka_producer_can_model_publish_failure() -> None:
    producer = InMemoryKafkaProducer(available=False)

    try:
        producer.produce(topic=MEASUREMENT_RAW_TOPIC, key="meter:001|P", value={})
    except KafkaProducerUnavailable as exc:
        assert "producer unavailable" in str(exc)
    else:  # pragma: no cover - explicit failure branch for contract clarity
        raise AssertionError("expected KafkaProducerUnavailable")


def test_runtime_kafka_adapter_uses_injected_client_factory_without_importing_real_client() -> None:
    class FakeRuntimeClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def produce(self, topic: str, key: str, value: dict[str, object]) -> dict[str, object]:
            self.calls.append({"topic": topic, "key": key, "value": value})
            return {"acknowledged": True, "partition": 3, "offset": 9}

    producer = create_runtime_kafka_producer(client_factory=FakeRuntimeClient)

    ack = producer.produce(topic=MEASUREMENT_RAW_TOPIC, key="meter:001|P", value={"ok": True})

    assert ack == {"acknowledged": True, "topic": MEASUREMENT_RAW_TOPIC, "partition": 3, "offset": 9}
    assert "kafka" not in sys.modules
    assert "confluent_kafka" not in sys.modules
