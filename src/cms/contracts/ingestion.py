"""Import-safe Kafka ingestion contracts for CMS live measurements.

This module defines only pure dataclasses and helper functions. It does not
import Kafka, FastAPI, PostgreSQL clients, or execute any I/O.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from cms.contracts.live_pipeline import SOURCE_AUTHORITY_PC1_ARCHIVE, validate_live_injector_source_authority

MEASUREMENT_RAW_TOPIC = "measurement_raw_v1"
MEASUREMENT_DLQ_TOPIC = "measurement_dead_letter_v1"
KAFKA_CONSUMER_GROUP = "postgres-live-ingest"
KAFKA_MESSAGE_KEY_FIELDS = ("meter_urn", "measurement")
MEASUREMENT_RAW_SCHEMA_VERSION = "measurement_raw_v1"
MAX_RAW_EVENT_BYTES = 64_000

# PostgreSQL ingestion boundary: Kafka measurement_raw_v1 is the raw/staging
# buffer; the consumer writes validated events directly to the live ledger.
MEASUREMENT_LANDING_TABLE = "landing.measurement_raw_event"
MEASUREMENT_LIVE_TABLE = "live.measurement_event"
MEASUREMENT_INGESTION_TABLE_PATH = (
    MEASUREMENT_LANDING_TABLE,
    MEASUREMENT_LIVE_TABLE,
)
MEASUREMENT_EVENT_WRITE_TARGET_TABLE = MEASUREMENT_LIVE_TABLE


@dataclass(frozen=True)
class MeasurementRawEvent:
    """Kafka raw measurement payload for Phase 1 live ingestion.

    ``schema_version`` names the Kafka payload contract. It is not a canonical
    schema version. Business idempotency prefers ``source_system`` plus
    ``source_event_id``; when source event IDs are unavailable, the fallback uses
    raw payload hash with the measurement identity and timestamp.
    """

    schema_version: str
    source_system: str
    source_event_id: str | None
    meter_urn: str
    measurement: str
    event_ts: str
    value_text: str | None
    value_numeric: float | None
    unit: str | None
    received_at: str
    raw_payload_hash: str
    raw_payload_size_bytes: int = 0
    payload_errors: tuple[str, ...] = ()
    source_authority: str = SOURCE_AUTHORITY_PC1_ARCHIVE
    source_path: str | None = None


def _is_nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _parse_iso_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def raw_payload_digest(payload: dict[str, Any]) -> str:
    """Return a stable SHA-256 digest for a JSON-like raw payload."""

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def raw_payload_size_bytes(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))


def measurement_raw_event_from_mapping(payload: dict[str, Any]) -> MeasurementRawEvent:
    """Build a raw event from an API/Kafka mapping without external side effects."""

    payload_hash = str(payload.get("raw_payload_hash") or raw_payload_digest(payload))
    size = int(payload.get("raw_payload_size_bytes") or raw_payload_size_bytes(payload))
    payload_errors: list[str] = []
    value_numeric: float | None = None
    if payload.get("value_numeric") is not None:
        try:
            value_numeric = float(payload["value_numeric"])
        except (TypeError, ValueError):
            payload_errors.append("value_numeric_invalid")
    return MeasurementRawEvent(
        schema_version=str(payload.get("schema_version") or MEASUREMENT_RAW_SCHEMA_VERSION),
        source_system=str(payload.get("source_system") or ""),
        source_event_id=str(payload["source_event_id"]) if payload.get("source_event_id") is not None else None,
        meter_urn=str(payload.get("meter_urn") or ""),
        measurement=str(payload.get("measurement") or ""),
        event_ts=str(payload.get("event_ts") or ""),
        value_text=str(payload["value_text"]) if payload.get("value_text") is not None else None,
        value_numeric=value_numeric,
        unit=str(payload["unit"]) if payload.get("unit") is not None else None,
        received_at=str(payload.get("received_at") or ""),
        raw_payload_hash=payload_hash,
        raw_payload_size_bytes=size,
        payload_errors=tuple(payload_errors),
        source_authority=str(payload.get("source_authority") or SOURCE_AUTHORITY_PC1_ARCHIVE),
        source_path=str(payload["source_path"]) if payload.get("source_path") is not None else None,
    )


def kafka_message_key(event: MeasurementRawEvent) -> str:
    """Build the Kafka key from the stable meter/measurement routing fields."""

    return f"{event.meter_urn}|{event.measurement}"


def idempotency_key(event: MeasurementRawEvent) -> tuple[str, ...]:
    """Return the business idempotency key; never uses Kafka offset alone."""

    if event.source_system and event.source_event_id:
        return ("source_event", event.source_system, event.source_event_id)
    return ("payload_hash", event.raw_payload_hash, event.meter_urn, event.measurement, event.event_ts)


def validate_raw_event(event: MeasurementRawEvent) -> tuple[str, ...]:
    """Return validation errors for the Kafka raw payload contract."""

    errors: list[str] = list(event.payload_errors)
    if event.schema_version != MEASUREMENT_RAW_SCHEMA_VERSION:
        errors.append("invalid_schema_version")
    for field_name in ("source_system", "meter_urn", "measurement", "event_ts", "received_at", "raw_payload_hash"):
        if not _is_nonempty_text(getattr(event, field_name)):
            errors.append(f"{field_name}_required")
    for field_name in ("event_ts", "received_at"):
        value = getattr(event, field_name)
        if _is_nonempty_text(value):
            try:
                _parse_iso_datetime(value)
            except ValueError:
                errors.append(f"{field_name}_invalid_iso_datetime")
    if event.value_text is None and event.value_numeric is None:
        errors.append("value_required")
    if event.source_authority != SOURCE_AUTHORITY_PC1_ARCHIVE:
        errors.append("source_authority_pc1_archive_required")
    if event.source_path:
        try:
            validate_live_injector_source_authority(event.source_path, event.source_authority)
        except ValueError:
            errors.append("source_path_outside_pc1_archive_authority")
    if event.raw_payload_size_bytes > MAX_RAW_EVENT_BYTES:
        errors.append("raw_payload_oversized")
    if event.raw_payload_hash and len(event.raw_payload_hash) < 16:
        errors.append("raw_payload_hash_too_short")
    return tuple(errors)


def should_send_to_dlq(validation_errors: tuple[str, ...]) -> bool:
    """Validation failures are poison-message candidates for the DLQ."""

    return bool(validation_errors)


def raw_event_to_kafka_value(event: MeasurementRawEvent) -> dict[str, Any]:
    return asdict(event)


__all__ = [
    "KAFKA_CONSUMER_GROUP",
    "KAFKA_MESSAGE_KEY_FIELDS",
    "MAX_RAW_EVENT_BYTES",
    "MEASUREMENT_DLQ_TOPIC",
    "MEASUREMENT_EVENT_WRITE_TARGET_TABLE",
    "MEASUREMENT_INGESTION_TABLE_PATH",
    "MEASUREMENT_LANDING_TABLE",
    "MEASUREMENT_LIVE_TABLE",
    "MEASUREMENT_RAW_SCHEMA_VERSION",
    "MEASUREMENT_RAW_TOPIC",
    "MeasurementRawEvent",
    "SOURCE_AUTHORITY_PC1_ARCHIVE",
    "idempotency_key",
    "kafka_message_key",
    "measurement_raw_event_from_mapping",
    "raw_event_to_kafka_value",
    "raw_payload_digest",
    "raw_payload_size_bytes",
    "should_send_to_dlq",
    "validate_raw_event",
]
