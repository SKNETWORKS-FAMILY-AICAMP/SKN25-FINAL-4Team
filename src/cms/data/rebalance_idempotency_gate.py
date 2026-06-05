"""Pure rebalance/restart idempotency gate contracts."""

from __future__ import annotations

from cms.data.runtime_consumer_loop import ConsumerLoopStats
from cms.data.stream_consumer import ConsumerDecision

REBALANCE_IDEMPOTENCY_METRICS = ("consumer_duplicate", "consumer_reprocessed")

AGGREGATE_BUCKET_IDEMPOTENCY_POLICY = {
    "bucket_write_mode": "upsert_or_recompute",
    "bucket_identity": "meter_urn|measurement|resolution|bucket_start|policy_version",
    "enqueue_source": "insert_event_only",
    "duplicate_event": "skip_bucket_enqueue",
    "coverage_source": "accepted_measurement_event_rows",
}


def validate_rebalance_gate_stats(stats: ConsumerLoopStats) -> None:
    """Validate the successful rebalance idempotency accounting invariant."""

    if stats.processed != stats.inserted + stats.duplicate + stats.dlq:
        raise ValueError("processed must equal inserted + duplicate + dlq")
    if stats.committed != stats.processed:
        raise ValueError("committed must equal processed")
    if stats.retry != 0:
        raise ValueError("rebalance idempotency gate expects retry-free processed messages")


def build_rebalance_gate_metrics(stats: ConsumerLoopStats) -> dict[str, int]:
    """Build metric names consumed by the soak Grafana gate."""

    validate_rebalance_gate_stats(stats)
    return {
        "consumer_processed": stats.processed,
        "consumer_inserted": stats.inserted,
        "consumer_duplicate": stats.duplicate,
        "consumer_dlq": stats.dlq,
        "consumer_committed": stats.committed,
        "consumer_reprocessed": stats.duplicate,
    }


def should_enqueue_aggregate_bucket(decision: ConsumerDecision) -> bool:
    """Queue aggregate/coverage work only for committed inserts."""

    return decision.action == "insert_event" and decision.commit_offset


__all__ = [
    "AGGREGATE_BUCKET_IDEMPOTENCY_POLICY",
    "REBALANCE_IDEMPOTENCY_METRICS",
    "build_rebalance_gate_metrics",
    "should_enqueue_aggregate_bucket",
    "validate_rebalance_gate_stats",
]
