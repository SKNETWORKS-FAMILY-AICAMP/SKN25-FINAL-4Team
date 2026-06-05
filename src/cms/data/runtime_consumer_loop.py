"""Runtime consumer loop with injected Kafka/PostgreSQL adapters."""

from __future__ import annotations

from dataclasses import dataclass

from cms.data.kafka_adapter import KafkaProducerLike
from cms.data.postgres_event_writer import PostgresEventWriterLike
from cms.data.runtime_kafka import RuntimeKafkaMessageConsumerLike
from cms.data.stream_consumer_runner import process_kafka_message


@dataclass(frozen=True)
class ConsumerLoopStats:
    polled: int = 0
    processed: int = 0
    committed: int = 0
    inserted: int = 0
    duplicate: int = 0
    dlq: int = 0
    retry: int = 0

    def bump(self, **updates: int) -> ConsumerLoopStats:
        values = self.__dict__.copy()
        for key, value in updates.items():
            values[key] += value
        return ConsumerLoopStats(**values)


def run_consumer_loop(
    *,
    consumer: RuntimeKafkaMessageConsumerLike,
    writer: PostgresEventWriterLike,
    dlq_producer: KafkaProducerLike,
    max_messages: int | None = None,
    poll_timeout: float = 1.0,
) -> ConsumerLoopStats:
    """Poll messages and commit offsets only after runner-approved success."""

    stats = ConsumerLoopStats()
    try:
        while max_messages is None or stats.processed < max_messages:
            polled = consumer.poll_message(timeout=poll_timeout)
            if polled is None:
                break
            raw_message, envelope = polled
            stats = stats.bump(polled=1)
            result = process_kafka_message(envelope, writer=writer, dlq_producer=dlq_producer)
            increments = {"processed": 1}
            if result.decision.action == "insert_event":
                increments["inserted"] = 1
            elif result.decision.action == "idempotent_noop":
                increments["duplicate"] = 1
            elif result.decision.action == "send_to_dlq":
                increments["dlq"] = 1
            elif result.decision.action == "retry":
                increments["retry"] = 1
            if result.decision.commit_offset:
                consumer.commit(raw_message)
                increments["committed"] = 1
            stats = stats.bump(**increments)
    finally:
        consumer.close()
    return stats


__all__ = ["ConsumerLoopStats", "run_consumer_loop"]
