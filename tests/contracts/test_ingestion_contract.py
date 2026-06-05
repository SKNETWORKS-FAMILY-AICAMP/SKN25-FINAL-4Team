from __future__ import annotations

from cms.contracts.ingestion import (
    KAFKA_CONSUMER_GROUP,
    KAFKA_MESSAGE_KEY_FIELDS,
    MAX_RAW_EVENT_BYTES,
    MEASUREMENT_DLQ_TOPIC,
    MEASUREMENT_RAW_SCHEMA_VERSION,
    MEASUREMENT_RAW_TOPIC,
    MeasurementRawEvent,
    idempotency_key,
    kafka_message_key,
    measurement_raw_event_from_mapping,
    raw_payload_digest,
    should_send_to_dlq,
    validate_raw_event,
)


def _event(**overrides: object) -> MeasurementRawEvent:
    payload = {
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
        "raw_payload_hash": "a" * 64,
        "raw_payload_size_bytes": 512,
    }
    payload.update(overrides)
    return MeasurementRawEvent(**payload)


def test_kafka_topic_constants_are_phase1_contract() -> None:
    assert MEASUREMENT_RAW_TOPIC == "measurement_raw_v1"
    assert MEASUREMENT_DLQ_TOPIC == "measurement_dead_letter_v1"
    assert KAFKA_CONSUMER_GROUP == "postgres-live-ingest"
    assert KAFKA_MESSAGE_KEY_FIELDS == ("meter_urn", "measurement")


def test_message_key_and_idempotency_use_business_fields_not_offset() -> None:
    event = _event()

    assert kafka_message_key(event) == "meter:001|P"
    assert idempotency_key(event) == ("source_event", "sensor_gateway", "evt-001")


def test_idempotency_falls_back_to_payload_hash_when_source_event_id_missing() -> None:
    event = _event(source_event_id=None)

    assert idempotency_key(event) == ("payload_hash", "a" * 64, "meter:001", "P", "2026-06-04T00:00:00+00:00")


def test_validate_raw_event_accepts_valid_event() -> None:
    assert validate_raw_event(_event()) == ()


def test_validate_raw_event_marks_poison_message_for_dlq() -> None:
    errors = validate_raw_event(
        _event(
            schema_version="canonical_v1",
            meter_urn="",
            event_ts="not-a-time",
            value_text=None,
            value_numeric=None,
            raw_payload_size_bytes=MAX_RAW_EVENT_BYTES + 1,
        )
    )

    assert "invalid_schema_version" in errors
    assert "meter_urn_required" in errors
    assert "event_ts_invalid_iso_datetime" in errors
    assert "value_required" in errors
    assert "raw_payload_oversized" in errors
    assert should_send_to_dlq(errors) is True


def test_event_from_mapping_generates_hash_and_size() -> None:
    payload = {
        "source_system": "gateway",
        "source_event_id": "1",
        "meter_urn": "meter:001",
        "measurement": "P",
        "event_ts": "2026-06-04T00:00:00+00:00",
        "value_numeric": 0,
        "received_at": "2026-06-04T00:00:01+00:00",
    }

    event = measurement_raw_event_from_mapping(payload)

    assert event.schema_version == MEASUREMENT_RAW_SCHEMA_VERSION
    assert event.raw_payload_hash == raw_payload_digest(payload)
    assert event.raw_payload_size_bytes > 0
    assert validate_raw_event(event) == ()
