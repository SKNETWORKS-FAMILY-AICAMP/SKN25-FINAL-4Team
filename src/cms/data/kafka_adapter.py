"""Import-safe Kafka adapter skeletons for CMS Phase 1 ingestion.

This module intentionally does not import Kafka client packages at module import
or create network connections. Runtime code can inject a client factory, while
local tests use the in-memory producer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from cms.contracts.ingestion import MEASUREMENT_RAW_TOPIC, MeasurementRawEvent, kafka_message_key, raw_event_to_kafka_value


class KafkaProducerLike(Protocol):
    """Minimal producer protocol shared by API and runner tests."""

    def produce(self, *, topic: str, key: str, value: dict[str, object]) -> dict[str, object]: ...


@dataclass(frozen=True)
class KafkaPublishRequest:
    topic: str
    key: str
    value: dict[str, object]


class KafkaProducerUnavailable(RuntimeError):
    """Raised when a producer cannot acknowledge a local/runtime publish."""


@dataclass
class InMemoryKafkaProducer:
    """Local fake producer for import-safe tests and dry-run wiring."""

    available: bool = True
    partition: int = 0
    published_records: list[dict[str, object]] | None = None

    def __post_init__(self) -> None:
        if self.published_records is None:
            self.published_records = []

    def produce(self, *, topic: str, key: str, value: dict[str, object]) -> dict[str, object]:
        if not self.available:
            raise KafkaProducerUnavailable("producer unavailable")
        assert self.published_records is not None
        offset = len(self.published_records)
        record = {"topic": topic, "partition": self.partition, "offset": offset, "key": key, "value": value}
        self.published_records.append(record)
        return {"acknowledged": True, "topic": topic, "partition": self.partition, "offset": offset}

    def to_kafka_message(self, index: int) -> dict[str, object]:
        assert self.published_records is not None
        return dict(self.published_records[index])


@dataclass(frozen=True)
class RuntimeKafkaProducer:
    """Thin wrapper around an injected runtime Kafka client.

    The wrapped client is expected to expose ``produce(topic, key, value)``. This
    class keeps adapter construction import-safe because the caller owns client
    creation.
    """

    client: Any

    def produce(self, *, topic: str, key: str, value: dict[str, object]) -> dict[str, object]:
        ack = self.client.produce(topic, key, value)
        if ack is None:
            return {"acknowledged": True, "topic": topic}
        if not isinstance(ack, dict):
            raise KafkaProducerUnavailable("producer returned a non-dict ack")
        return {"acknowledged": bool(ack.get("acknowledged", True)), "topic": topic, **ack}


def build_kafka_publish_request(event: MeasurementRawEvent) -> KafkaPublishRequest:
    """Build the Phase 1 raw-topic publish request for a validated event."""

    return KafkaPublishRequest(topic=MEASUREMENT_RAW_TOPIC, key=kafka_message_key(event), value=raw_event_to_kafka_value(event))


def create_runtime_kafka_producer(*, client_factory: Any | None = None) -> RuntimeKafkaProducer:
    """Create a runtime producer from an injected factory without importing Kafka.

    Real deployments should pass a configured factory or client constructor. The
    no-factory branch is intentionally unavailable so local/import-safe tests do
    not silently depend on an external broker package.
    """

    if client_factory is None:
        raise KafkaProducerUnavailable("runtime Kafka client factory is required")
    return RuntimeKafkaProducer(client=client_factory())


__all__ = [
    "InMemoryKafkaProducer",
    "KafkaProducerLike",
    "KafkaProducerUnavailable",
    "KafkaPublishRequest",
    "RuntimeKafkaProducer",
    "build_kafka_publish_request",
    "create_runtime_kafka_producer",
]
