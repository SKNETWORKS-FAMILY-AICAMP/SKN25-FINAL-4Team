from __future__ import annotations

import sys
from types import ModuleType

import pytest

from cms.contracts.ingestion import MEASUREMENT_RAW_TOPIC
from cms.service import api


class FakeProducer:
    def __init__(self, *, fail: bool = False, ack: bool = True) -> None:
        self.fail = fail
        self.ack = ack
        self.calls: list[dict[str, object]] = []

    def produce(self, *, topic: str, key: str, value: dict[str, object]) -> dict[str, object]:
        if self.fail:
            raise RuntimeError("producer unavailable")
        self.calls.append({"topic": topic, "key": key, "value": value})
        return {"acknowledged": self.ack, "partition": 0, "offset": 7}


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


def test_runtime_ingest_producer_env_gate_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    sys.modules.pop("confluent_kafka", None)
    monkeypatch.delenv("CMS_ENABLE_RUNTIME_KAFKA_PRODUCER", raising=False)

    producer = api.build_ingest_producer_from_env()

    with pytest.raises(RuntimeError, match="Kafka producer is not configured"):
        producer.produce(topic=MEASUREMENT_RAW_TOPIC, key="k", value={})
    assert "confluent_kafka" not in sys.modules


def test_runtime_ingest_producer_env_gate_lazily_creates_confluent_producer(monkeypatch: pytest.MonkeyPatch) -> None:
    published: list[str] = []

    class FakeProducerClient:
        def __init__(self, config: dict[str, object]) -> None:
            self.config = config

        def produce(self, *, topic: str, key: bytes, value: bytes, on_delivery: object | None = None) -> None:
            published.append(topic)

        def flush(self, timeout: float) -> int:
            return 0

    module = ModuleType("confluent_kafka")
    module.Producer = FakeProducerClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "confluent_kafka", module)
    monkeypatch.setenv("CMS_ENABLE_RUNTIME_KAFKA_PRODUCER", "1")
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "cms-kafka:9092")

    producer = api.build_ingest_producer_from_env()
    ack = producer.produce(topic=MEASUREMENT_RAW_TOPIC, key="meter:001|P", value={"ok": True})

    assert ack["acknowledged"] is True
    assert published == [MEASUREMENT_RAW_TOPIC]


def test_ingest_route_is_in_import_safe_fallback_routes() -> None:
    assert "/ingest/measurements" in {path for _, path, _ in api.ROUTES}
    assert "/ingest/measurements" in set(api.ApiSkeleton().route_paths())


def test_ingest_measurement_valid_payload_publishes_to_kafka_and_returns_202_style_payload() -> None:
    producer = FakeProducer()

    result = api.make_ingest_measurement_payload(_payload(), producer=producer)

    assert result["status_code"] == 202
    assert result["accepted"] is True
    assert result["topic"] == MEASUREMENT_RAW_TOPIC
    assert result["key"] == "meter:001|P"
    assert result["writes_allowed"] is False
    assert result["postgres_write_attempted"] is False
    assert result["rollup_qa_promotion_attempted"] is False
    assert producer.calls[0]["topic"] == MEASUREMENT_RAW_TOPIC


def test_ingest_measurement_validation_failure_returns_422_style_payload_without_produce() -> None:
    producer = FakeProducer()

    result = api.make_ingest_measurement_payload(_payload(meter_urn="", value_text=None, value_numeric=None), producer=producer)

    assert result["status_code"] == 422
    assert result["accepted"] is False
    assert "meter_urn_required" in result["errors"]
    assert "value_required" in result["errors"]
    assert producer.calls == []


def test_ingest_measurement_producer_failure_returns_503_style_payload() -> None:
    result = api.make_ingest_measurement_payload(_payload(), producer=FakeProducer(fail=True))

    assert result["status_code"] == 503
    assert result["accepted"] is False
    assert result["producer_error"] == "producer unavailable"
    assert result["writes_allowed"] is False


def test_ingest_measurement_negative_ack_returns_503_style_payload() -> None:
    result = api.make_ingest_measurement_payload(_payload(), producer=FakeProducer(ack=False))

    assert result["status_code"] == 503
    assert result["accepted"] is False
    assert result["producer_acknowledged"] is False
