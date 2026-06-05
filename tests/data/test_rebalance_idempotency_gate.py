from __future__ import annotations

import json
from pathlib import Path

from tests.data.test_runtime_consumer_loop_contract import FakeConsumer
from tests.data.test_stream_consumer_runner_contract import _message

from cms.data.kafka_adapter import InMemoryKafkaProducer
from cms.data.postgres_event_writer import InMemoryPostgresEventWriter
from cms.data.rebalance_idempotency_gate import (
    AGGREGATE_BUCKET_IDEMPOTENCY_POLICY,
    REBALANCE_IDEMPOTENCY_METRICS,
    build_rebalance_gate_metrics,
    should_enqueue_aggregate_bucket,
    validate_rebalance_gate_stats,
)
from cms.data.runtime_consumer_loop import ConsumerLoopStats, run_consumer_loop
from cms.data.stream_consumer_runner import process_kafka_message

ROOT = Path(__file__).resolve().parents[2]
SOAK_GATES_DASHBOARD_PATH = ROOT / "docker/grafana/provisioning/dashboards/json/cms_live_soak_gates.json"


def _message_at_offset(offset: int, **value_overrides: object) -> dict[str, object]:
    message = _message(**value_overrides)
    message["offset"] = offset
    return message


def test_fake_rebalance_reprocesses_same_offset_as_duplicate_and_preserves_invariants() -> None:
    message = _message_at_offset(5)
    stats = run_consumer_loop(
        consumer=FakeConsumer([message, dict(message)]),
        writer=InMemoryPostgresEventWriter(),
        dlq_producer=InMemoryKafkaProducer(),
        max_messages=2,
    )

    validate_rebalance_gate_stats(stats)

    assert stats == ConsumerLoopStats(polled=2, processed=2, committed=2, inserted=1, duplicate=1, dlq=0, retry=0)
    assert build_rebalance_gate_metrics(stats) == {
        "consumer_processed": 2,
        "consumer_inserted": 1,
        "consumer_duplicate": 1,
        "consumer_dlq": 0,
        "consumer_committed": 2,
        "consumer_reprocessed": 1,
    }


def test_fake_restart_same_source_event_id_different_offset_is_duplicate_run() -> None:
    writer = InMemoryPostgresEventWriter()
    first_consumer = FakeConsumer([_message_at_offset(5)])
    restarted_consumer = FakeConsumer([_message_at_offset(42)])

    first_stats = run_consumer_loop(consumer=first_consumer, writer=writer, dlq_producer=InMemoryKafkaProducer(), max_messages=1)
    restarted_stats = run_consumer_loop(consumer=restarted_consumer, writer=writer, dlq_producer=InMemoryKafkaProducer(), max_messages=1)

    validate_rebalance_gate_stats(first_stats)
    validate_rebalance_gate_stats(restarted_stats)

    assert first_stats == ConsumerLoopStats(polled=1, processed=1, committed=1, inserted=1, duplicate=0, dlq=0, retry=0)
    assert restarted_stats == ConsumerLoopStats(polled=1, processed=1, committed=1, inserted=0, duplicate=1, dlq=0, retry=0)
    assert writer.rows is not None
    assert len(writer.rows) == 1
    assert writer.rows["source_event|sensor_gateway|evt-001"]["source_ref"] == "measurement_raw_v1:0:5"


def test_aggregate_bucket_enqueue_gate_uses_insert_only_policy() -> None:
    writer = InMemoryPostgresEventWriter()
    dlq = InMemoryKafkaProducer()

    first = process_kafka_message(_message_at_offset(5), writer=writer, dlq_producer=dlq)
    duplicate = process_kafka_message(_message_at_offset(42), writer=writer, dlq_producer=dlq)

    assert should_enqueue_aggregate_bucket(first.decision) is True
    assert should_enqueue_aggregate_bucket(duplicate.decision) is False
    assert AGGREGATE_BUCKET_IDEMPOTENCY_POLICY == {
        "bucket_write_mode": "upsert_or_recompute",
        "bucket_identity": "meter_urn|measurement|resolution|bucket_start|policy_version",
        "enqueue_source": "insert_event_only",
        "duplicate_event": "skip_bucket_enqueue",
        "coverage_source": "accepted_measurement_event_rows",
    }


def test_grafana_soak_dashboard_links_duplicate_and_reprocessed_metrics() -> None:
    dashboard = json.loads(SOAK_GATES_DASHBOARD_PATH.read_text(encoding="utf-8"))
    raw = json.dumps(dashboard)

    assert REBALANCE_IDEMPOTENCY_METRICS == ("consumer_duplicate", "consumer_reprocessed")
    assert "Duplicate / reprocessed events" in {panel["title"] for panel in dashboard["panels"]}
    for metric in REBALANCE_IDEMPOTENCY_METRICS:
        assert metric in raw
