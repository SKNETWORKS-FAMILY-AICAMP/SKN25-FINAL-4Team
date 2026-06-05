from __future__ import annotations

import json
import sys
from types import ModuleType

import pytest

from cms.contracts.ingestion import MEASUREMENT_DLQ_TOPIC, MEASUREMENT_RAW_TOPIC


def test_runtime_kafka_module_does_not_import_confluent_at_module_import_time() -> None:
    sys.modules.pop("confluent_kafka", None)

    import cms.data.runtime_kafka as runtime_kafka

    assert runtime_kafka.MEASUREMENT_RAW_TOPIC == MEASUREMENT_RAW_TOPIC
    assert "confluent_kafka" not in sys.modules


def test_make_producer_config_uses_phase1_defaults_and_no_public_listener() -> None:
    from cms.data.runtime_kafka import make_kafka_producer_config

    config = make_kafka_producer_config({"KAFKA_BOOTSTRAP_SERVERS": "cms-kafka:9092"})

    assert config["bootstrap.servers"] == "cms-kafka:9092"
    assert "0.0.0.0" not in json.dumps(config)


def test_create_confluent_producer_lazily_wraps_client_and_json_serializes_value(monkeypatch: pytest.MonkeyPatch) -> None:
    published: list[dict[str, object]] = []

    class FakeProducer:
        def __init__(self, config: dict[str, object]) -> None:
            self.config = config

        def produce(self, *, topic: str, key: bytes, value: bytes, on_delivery: object | None = None) -> None:
            published.append({"topic": topic, "key": key, "value": value, "on_delivery": on_delivery})

        def flush(self, timeout: float) -> int:
            return 0

    module = ModuleType("confluent_kafka")
    module.Producer = FakeProducer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "confluent_kafka", module)

    from cms.data.runtime_kafka import create_confluent_kafka_producer

    producer = create_confluent_kafka_producer({"KAFKA_BOOTSTRAP_SERVERS": "cms-kafka:9092"})
    ack = producer.produce(topic=MEASUREMENT_RAW_TOPIC, key="meter:001|P", value={"a": 1})

    assert ack["acknowledged"] is True
    assert ack["topic"] == MEASUREMENT_RAW_TOPIC
    assert published[0]["key"] == b"meter:001|P"
    assert json.loads(published[0]["value"].decode("utf-8")) == {"a": 1}


def test_create_confluent_consumer_uses_raw_topic_and_group_without_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    subscribed: list[list[str]] = []

    class FakeConsumer:
        def __init__(self, config: dict[str, object]) -> None:
            self.config = config

        def subscribe(self, topics: list[str]) -> None:
            subscribed.append(topics)

    module = ModuleType("confluent_kafka")
    module.Consumer = FakeConsumer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "confluent_kafka", module)

    from cms.data.runtime_kafka import create_confluent_kafka_consumer

    consumer = create_confluent_kafka_consumer(
        {
            "KAFKA_BOOTSTRAP_SERVERS": "cms-kafka:9092",
            "KAFKA_CONSUMER_GROUP": "postgres-live-ingest",
            "MEASUREMENT_RAW_TOPIC": MEASUREMENT_RAW_TOPIC,
        }
    )

    assert consumer.topic == MEASUREMENT_RAW_TOPIC
    assert consumer.consumer_group == "postgres-live-ingest"
    assert subscribed == [[MEASUREMENT_RAW_TOPIC]]


def test_confluent_consumer_message_mapping_parses_json_value_and_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    committed: list[object] = []

    class FakeMessage:
        def topic(self) -> str:
            return MEASUREMENT_RAW_TOPIC

        def partition(self) -> int:
            return 1

        def offset(self) -> int:
            return 7

        def key(self) -> bytes:
            return b"meter:001|P"

        def value(self) -> bytes:
            return b'{"meter_urn":"meter:001","measurement":"P"}'

        def error(self) -> None:
            return None

    class FakeConsumer:
        def __init__(self, config: dict[str, object]) -> None:
            self.config = config

        def subscribe(self, topics: list[str]) -> None:
            self.topics = topics

        def poll(self, timeout: float) -> FakeMessage:
            return FakeMessage()

        def commit(self, message: object, asynchronous: bool = False) -> None:
            committed.append(message)

        def close(self) -> None:
            pass

    module = ModuleType("confluent_kafka")
    module.Consumer = FakeConsumer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "confluent_kafka", module)

    from cms.data.runtime_kafka import create_confluent_kafka_consumer

    consumer = create_confluent_kafka_consumer({"KAFKA_BOOTSTRAP_SERVERS": "cms-kafka:9092"})
    polled = consumer.poll_message(timeout=0.1)
    assert polled is not None
    message, envelope = polled

    assert envelope["topic"] == MEASUREMENT_RAW_TOPIC
    assert envelope["partition"] == 1
    assert envelope["offset"] == 7
    assert envelope["key"] == "meter:001|P"
    assert envelope["value"] == {"meter_urn": "meter:001", "measurement": "P"}

    consumer.commit(message)
    assert committed == [message]


def test_dlq_publish_request_uses_dead_letter_topic(monkeypatch: pytest.MonkeyPatch) -> None:
    published: list[str] = []

    class FakeProducer:
        def __init__(self, config: dict[str, object]) -> None:
            pass

        def produce(self, *, topic: str, key: bytes, value: bytes, on_delivery: object | None = None) -> None:
            published.append(topic)

        def flush(self, timeout: float) -> int:
            return 0

    module = ModuleType("confluent_kafka")
    module.Producer = FakeProducer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "confluent_kafka", module)

    from cms.data.runtime_kafka import create_confluent_kafka_producer

    producer = create_confluent_kafka_producer({"KAFKA_BOOTSTRAP_SERVERS": "cms-kafka:9092"})
    producer.produce(topic=MEASUREMENT_DLQ_TOPIC, key="bad", value={"validation_errors": ["x"]})

    assert published == [MEASUREMENT_DLQ_TOPIC]
