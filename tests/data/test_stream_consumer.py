from __future__ import annotations

from cms.contracts.ingestion import MEASUREMENT_DLQ_TOPIC, MEASUREMENT_RAW_SCHEMA_VERSION
from cms.data.stream_consumer import (
    build_postgres_insert_payload,
    decide_consumer_action,
    decide_offset_commit,
    parse_kafka_envelope,
)


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
    return {"topic": "measurement_raw_v1", "partition": 2, "offset": 42, "key": "meter:001|P", "value": value}


def test_parse_kafka_envelope_and_build_postgres_payload_shape() -> None:
    envelope = parse_kafka_envelope(_message())
    event = decide_consumer_action(envelope, db_transaction_succeeded=True).event
    assert event is not None

    payload = build_postgres_insert_payload(event, envelope, consumed_at="2026-06-04T00:00:02+00:00")

    assert payload["target_table"] == "live.measurement_event"
    assert payload["source_layer"] == "kafka.measurement_raw_v1"
    assert payload["kafka_topic"] == "measurement_raw_v1"
    assert payload["kafka_partition"] == 2
    assert payload["kafka_offset"] == 42
    assert payload["consumer_group"] == "postgres-live-ingest"
    assert payload["business_idempotency_key"] == ("source_event", "sensor_gateway", "evt-001")


def test_db_transaction_success_commits_offset() -> None:
    decision = decide_consumer_action(parse_kafka_envelope(_message()), db_transaction_succeeded=True)

    assert decision.action == "insert_event"
    assert decision.commit_offset is True
    assert decision.failure_stage == "none"


def test_db_transaction_failure_does_not_commit_offset() -> None:
    decision = decide_consumer_action(parse_kafka_envelope(_message()), db_transaction_succeeded=False)

    assert decision.action == "retry"
    assert decision.commit_offset is False
    assert decision.failure_stage == "db_transaction"


def test_validation_failure_goes_to_dlq_and_commits_only_after_dlq_success() -> None:
    envelope = parse_kafka_envelope(_message(schema_version="canonical_v1", meter_urn="", value_text=None, value_numeric=None))

    failed_dlq = decide_consumer_action(envelope, dlq_publish_succeeded=False)
    sent_dlq = decide_consumer_action(envelope, dlq_publish_succeeded=True)

    assert failed_dlq.action == "send_to_dlq"
    assert failed_dlq.commit_offset is False
    assert failed_dlq.failure_stage == "dlq_publish"
    assert sent_dlq.commit_offset is True
    assert sent_dlq.dlq_payload is not None
    assert sent_dlq.dlq_payload["topic"] == MEASUREMENT_DLQ_TOPIC
    assert "invalid_schema_version" in sent_dlq.validation_errors


def test_malformed_numeric_payload_goes_to_dlq_without_throwing() -> None:
    envelope = parse_kafka_envelope(_message(value_numeric="not-a-number", value_text=None))

    decision = decide_consumer_action(envelope, dlq_publish_succeeded=True)

    assert decision.action == "send_to_dlq"
    assert decision.commit_offset is True
    assert "value_numeric_invalid" in decision.validation_errors
    assert decision.dlq_payload is not None
    assert decision.dlq_payload["raw_value"]["value_numeric"] == "not-a-number"


def test_duplicate_business_key_is_idempotent_noop_after_transaction() -> None:
    decision = decide_consumer_action(parse_kafka_envelope(_message()), db_transaction_succeeded=True, duplicate_event=True)

    assert decision.action == "idempotent_noop"
    assert decision.commit_offset is True
    assert decision.idempotency_key == ("source_event", "sensor_gateway", "evt-001")
    assert decision.reason == "duplicate business idempotency key"


def test_unexpected_error_never_commits_offset() -> None:
    decision = decide_consumer_action(parse_kafka_envelope(_message()), unexpected_error="boom")

    assert decision.action == "retry"
    assert decision.commit_offset is False
    assert decision.failure_stage == "unexpected"


def test_offset_commit_helper_requires_successful_db_or_dlq() -> None:
    assert decide_offset_commit(db_transaction_succeeded=True) is True
    assert decide_offset_commit(db_transaction_succeeded=False) is False
    assert decide_offset_commit(validation_failed=True, dlq_publish_succeeded=True) is True
    assert decide_offset_commit(validation_failed=True, dlq_publish_succeeded=False) is False
