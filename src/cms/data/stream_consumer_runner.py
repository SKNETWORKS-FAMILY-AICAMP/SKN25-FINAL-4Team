"""Import-safe Kafka consumer runner shell.

The runner coordinates pure decision logic with injected PostgreSQL and DLQ
adapters. It does not import real Kafka/PostgreSQL clients or commit offsets by
itself; it returns the commit decision for the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cms.contracts.ingestion import MEASUREMENT_DLQ_TOPIC
from cms.data.kafka_adapter import KafkaProducerLike
from cms.data.postgres_event_writer import PostgresEventWriterLike, PostgresWriteResult
from cms.data.stream_consumer import ConsumerDecision, decide_consumer_action, parse_kafka_envelope


@dataclass(frozen=True)
class StreamConsumerProcessResult:
    decision: ConsumerDecision
    write_result: PostgresWriteResult | None = None
    dlq_ack: dict[str, object] | None = None


def process_kafka_message(
    message: dict[str, Any],
    *,
    writer: PostgresEventWriterLike,
    dlq_producer: KafkaProducerLike,
    consumed_at: str | None = None,
    kafka_topic_identity: str | None = None,
) -> StreamConsumerProcessResult:
    """Process one Kafka-like message with injected adapters.

    Valid events are written to ``live.measurement_event`` through the writer.
    Invalid/poison messages are published to DLQ. Offset commit remains a return
    value only and is true only after DB transaction success or DLQ publish
    success.
    """

    envelope = parse_kafka_envelope(message)
    first_decision = decide_consumer_action(envelope, consumed_at=consumed_at, kafka_topic_identity=kafka_topic_identity)

    if first_decision.action == "send_to_dlq":
        dlq_ack: dict[str, object] | None = None
        dlq_succeeded = False
        if first_decision.dlq_payload is not None:
            try:
                dlq_ack = dlq_producer.produce(
                    topic=MEASUREMENT_DLQ_TOPIC,
                    key=envelope.key or "",
                    value=first_decision.dlq_payload,
                )
                dlq_succeeded = bool(dlq_ack.get("acknowledged", False))
            except Exception:  # noqa: BLE001 - runner maps DLQ adapter failure to no-commit decision.
                dlq_succeeded = False
                dlq_ack = None
        final_decision = decide_consumer_action(
            envelope,
            dlq_publish_succeeded=dlq_succeeded,
            consumed_at=consumed_at,
            kafka_topic_identity=kafka_topic_identity,
        )
        return StreamConsumerProcessResult(decision=final_decision, dlq_ack=dlq_ack)

    if first_decision.postgres_payload is None:
        return StreamConsumerProcessResult(decision=first_decision)

    try:
        write_result = writer.insert_measurement_event(first_decision.postgres_payload)
    except Exception as exc:  # noqa: BLE001 - runner returns no-commit retry for adapter failures.
        return StreamConsumerProcessResult(
            decision=ConsumerDecision(
                action="retry",
                commit_offset=False,
                failure_stage="db_transaction",
                event=first_decision.event,
                postgres_payload=first_decision.postgres_payload,
                idempotency_key=first_decision.idempotency_key,
                reason=str(exc),
            )
        )
    final_decision = decide_consumer_action(
        envelope,
        db_transaction_succeeded=write_result.succeeded,
        duplicate_event=write_result.duplicate_event,
        consumed_at=consumed_at,
        kafka_topic_identity=kafka_topic_identity,
    )
    return StreamConsumerProcessResult(decision=final_decision, write_result=write_result)


__all__ = ["StreamConsumerProcessResult", "process_kafka_message"]
