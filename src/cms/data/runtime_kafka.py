"""Lazy runtime Kafka adapters for AWS Phase 1.

No Kafka client is imported at module import time. Real deployments create the
adapters from environment only inside service entrypoints.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Protocol

from cms.contracts.ingestion import KAFKA_CONSUMER_GROUP, MEASUREMENT_RAW_TOPIC
from cms.data.kafka_adapter import KafkaProducerUnavailable


class RuntimeKafkaMessageConsumerLike(Protocol):
    def poll_message(self, *, timeout: float) -> tuple[object, dict[str, object]] | None: ...

    def commit(self, message: object) -> None: ...

    def close(self) -> None: ...


def make_kafka_producer_config(env: dict[str, str] | None = None) -> dict[str, object]:
    values = env or os.environ
    return {
        "bootstrap.servers": values.get("KAFKA_BOOTSTRAP_SERVERS", "cms-kafka:9092"),
        "client.id": values.get("KAFKA_CLIENT_ID", "cms-api-ingest"),
        "acks": values.get("KAFKA_ACKS", "all"),
    }


def make_kafka_consumer_config(env: dict[str, str] | None = None) -> dict[str, object]:
    values = env or os.environ
    return {
        "bootstrap.servers": values.get("KAFKA_BOOTSTRAP_SERVERS", "cms-kafka:9092"),
        "group.id": values.get("KAFKA_CONSUMER_GROUP", KAFKA_CONSUMER_GROUP),
        "enable.auto.commit": False,
        "auto.offset.reset": values.get("KAFKA_AUTO_OFFSET_RESET", "earliest"),
        "client.id": values.get("KAFKA_CONSUMER_CLIENT_ID", "kafka_to_postgres_consumer"),
    }


@dataclass
class ConfluentKafkaProducerAdapter:
    producer: Any
    flush_timeout: float = 5.0

    def produce(self, *, topic: str, key: str, value: dict[str, object]) -> dict[str, object]:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.producer.produce(topic=topic, key=key.encode("utf-8"), value=payload, on_delivery=None)
        remaining = self.producer.flush(self.flush_timeout)
        if remaining:
            raise KafkaProducerUnavailable("producer flush timed out")
        return {"acknowledged": True, "topic": topic, "key": key}


@dataclass
class ConfluentKafkaConsumerAdapter:
    consumer: Any
    topic: str
    consumer_group: str

    def poll_message(self, *, timeout: float) -> tuple[object, dict[str, object]] | None:
        message = self.consumer.poll(timeout)
        if message is None:
            return None
        error = message.error() if hasattr(message, "error") else None
        if error:
            raise RuntimeError(str(error))
        return message, _message_to_envelope(message)

    def commit(self, message: object) -> None:
        self.consumer.commit(message=message, asynchronous=False)

    def close(self) -> None:
        self.consumer.close()


def _decode_optional_bytes(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _message_to_envelope(message: Any) -> dict[str, object]:
    raw_value = message.value()
    if isinstance(raw_value, bytes):
        decoded = raw_value.decode("utf-8")
        try:
            value: object = json.loads(decoded)
        except json.JSONDecodeError:
            value = decoded
    else:
        value = raw_value
    return {
        "topic": str(message.topic()),
        "partition": int(message.partition()),
        "offset": int(message.offset()),
        "key": _decode_optional_bytes(message.key()),
        "value": value,
    }


def create_confluent_kafka_producer(env: dict[str, str] | None = None) -> ConfluentKafkaProducerAdapter:
    module = import_module("confluent_kafka")
    producer = module.Producer(make_kafka_producer_config(env))
    return ConfluentKafkaProducerAdapter(producer=producer)


def create_confluent_kafka_consumer(env: dict[str, str] | None = None) -> ConfluentKafkaConsumerAdapter:
    values = env or os.environ
    topic = values.get("MEASUREMENT_RAW_TOPIC", MEASUREMENT_RAW_TOPIC)
    consumer_group = values.get("KAFKA_CONSUMER_GROUP", KAFKA_CONSUMER_GROUP)
    module = import_module("confluent_kafka")
    consumer = module.Consumer(make_kafka_consumer_config(values))
    consumer.subscribe([topic])
    return ConfluentKafkaConsumerAdapter(consumer=consumer, topic=topic, consumer_group=consumer_group)


__all__ = [
    "ConfluentKafkaConsumerAdapter",
    "ConfluentKafkaProducerAdapter",
    "MEASUREMENT_RAW_TOPIC",
    "RuntimeKafkaMessageConsumerLike",
    "create_confluent_kafka_consumer",
    "create_confluent_kafka_producer",
    "make_kafka_consumer_config",
    "make_kafka_producer_config",
]
