"""Pure Kafka-to-PostgreSQL consumer decision contract.

No Kafka or PostgreSQL client is imported here. The module models parse,
validation, DLQ, idempotent insert payload, and offset commit decisions for
local tests before any real broker/DB integration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from cms.contracts.ingestion import (
    KAFKA_CONSUMER_GROUP,
    MEASUREMENT_DLQ_TOPIC,
    MEASUREMENT_RAW_TOPIC,
    MeasurementRawEvent,
    idempotency_key,
    kafka_message_key,
    measurement_raw_event_from_mapping,
    raw_event_to_kafka_value,
    should_send_to_dlq,
    validate_raw_event,
)

ConsumerAction = Literal["insert_event", "idempotent_noop", "send_to_dlq", "retry"]
FailureStage = Literal["none", "validation", "dlq_publish", "db_transaction", "unexpected"]


@dataclass(frozen=True)
class KafkaEnvelope:
    topic: str
    partition: int
    offset: int
    key: str | None
    value: dict[str, Any]
    timestamp: str | None = None
    headers: tuple[tuple[str, str], ...] = ()
    parse_errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConsumerDecision:
    action: ConsumerAction
    commit_offset: bool
    failure_stage: FailureStage
    event: MeasurementRawEvent | None = None
    postgres_payload: dict[str, Any] | None = None
    dlq_payload: dict[str, Any] | None = None
    validation_errors: tuple[str, ...] = ()
    idempotency_key: tuple[str, ...] = ()
    reason: str | None = None


def parse_kafka_envelope(message: dict[str, Any]) -> KafkaEnvelope:
    """Parse a plain Kafka-like message mapping into an envelope."""

    value = message.get("value")
    parse_errors: tuple[str, ...] = ()
    if not isinstance(value, dict):
        value = {"_invalid_kafka_value": value}
        parse_errors = ("kafka_value_not_object",)
    return KafkaEnvelope(
        topic=str(message.get("topic") or MEASUREMENT_RAW_TOPIC),
        partition=int(message.get("partition", 0)),
        offset=int(message.get("offset", 0)),
        key=str(message["key"]) if message.get("key") is not None else None,
        value=value,
        timestamp=str(message["timestamp"]) if message.get("timestamp") is not None else None,
        headers=tuple((str(k), str(v)) for k, v in message.get("headers", ())),
        parse_errors=parse_errors,
    )


def build_postgres_insert_payload(
    event: MeasurementRawEvent,
    envelope: KafkaEnvelope,
    *,
    consumed_at: str | None = None,
    consumer_group: str = KAFKA_CONSUMER_GROUP,
) -> dict[str, Any]:
    """Build the idempotent live.measurement_event insert shape."""

    consumed = consumed_at or datetime.now(UTC).isoformat()
    business_key = "|".join(idempotency_key(event))
    return {
        "target_table": "live.measurement_event",
        "event_id": business_key,
        "source_event_id": event.source_event_id,
        "meter_urn": event.meter_urn,
        "measurement": event.measurement,
        "event_ts": event.event_ts,
        "value_text": event.value_text,
        "value_numeric": event.value_numeric,
        "unit": event.unit,
        "source_layer": "kafka.measurement_raw_v1",
        "source_ref": f"{envelope.topic}:{envelope.partition}:{envelope.offset}",
        "ingested_at": consumed,
        "received_at": event.received_at,
        "raw_payload_hash": event.raw_payload_hash,
        "policy_lookup_status": "pending",
        "kafka_topic": envelope.topic,
        "kafka_partition": envelope.partition,
        "kafka_offset": envelope.offset,
        "kafka_key": envelope.key or kafka_message_key(event),
        "consumer_group": consumer_group,
        "consumed_at": consumed,
        "schema_version": event.schema_version,
        "business_idempotency_key": business_key,
    }


def build_dlq_payload(event: MeasurementRawEvent | None, envelope: KafkaEnvelope, validation_errors: tuple[str, ...]) -> dict[str, Any]:
    """Build a DLQ payload that separates poison messages from normal flow."""

    return {
        "topic": MEASUREMENT_DLQ_TOPIC,
        "source_topic": envelope.topic,
        "source_partition": envelope.partition,
        "source_offset": envelope.offset,
        "source_key": envelope.key,
        "validation_errors": validation_errors,
        "raw_value": envelope.value,
        "event": raw_event_to_kafka_value(event) if event else None,
    }


def decide_offset_commit(*, db_transaction_succeeded: bool = False, dlq_publish_succeeded: bool = False, validation_failed: bool = False) -> bool:
    """Commit only after DB transaction success or successful DLQ publish."""

    if validation_failed:
        return dlq_publish_succeeded
    return db_transaction_succeeded


def decide_consumer_action(
    envelope: KafkaEnvelope,
    *,
    db_transaction_succeeded: bool = False,
    dlq_publish_succeeded: bool = False,
    duplicate_event: bool = False,
    unexpected_error: str | None = None,
    consumed_at: str | None = None,
) -> ConsumerDecision:
    """Return the consumer decision for one Kafka envelope."""

    if unexpected_error:
        return ConsumerDecision(action="retry", commit_offset=False, failure_stage="unexpected", reason=unexpected_error)

    event = measurement_raw_event_from_mapping(envelope.value)
    validation_errors = (*envelope.parse_errors, *validate_raw_event(event))
    if should_send_to_dlq(validation_errors):
        return ConsumerDecision(
            action="send_to_dlq",
            commit_offset=decide_offset_commit(validation_failed=True, dlq_publish_succeeded=dlq_publish_succeeded),
            failure_stage="validation" if dlq_publish_succeeded else "dlq_publish",
            event=event,
            dlq_payload=build_dlq_payload(event, envelope, validation_errors),
            validation_errors=validation_errors,
            idempotency_key=idempotency_key(event),
        )

    postgres_payload = build_postgres_insert_payload(event, envelope, consumed_at=consumed_at)
    if duplicate_event:
        return ConsumerDecision(
            action="idempotent_noop",
            commit_offset=db_transaction_succeeded,
            failure_stage="none" if db_transaction_succeeded else "db_transaction",
            event=event,
            postgres_payload=postgres_payload,
            idempotency_key=idempotency_key(event),
            reason="duplicate business idempotency key",
        )
    return ConsumerDecision(
        action="insert_event" if db_transaction_succeeded else "retry",
        commit_offset=db_transaction_succeeded,
        failure_stage="none" if db_transaction_succeeded else "db_transaction",
        event=event,
        postgres_payload=postgres_payload,
        idempotency_key=idempotency_key(event),
    )


__all__ = [
    "ConsumerAction",
    "ConsumerDecision",
    "FailureStage",
    "KafkaEnvelope",
    "build_dlq_payload",
    "build_postgres_insert_payload",
    "decide_consumer_action",
    "decide_offset_commit",
    "parse_kafka_envelope",
]
