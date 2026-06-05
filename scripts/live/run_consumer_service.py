"""AWS Phase 1 Kafka-to-PostgreSQL consumer service entrypoint.

This script is intentionally safe as a deployment skeleton. The default compose
command runs it in dry-run mode to validate environment wiring without opening
Kafka/PostgreSQL clients or committing offsets. Runtime client wiring should be
added behind explicit adapter gates.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass

from cms.contracts.ingestion import KAFKA_CONSUMER_GROUP, MEASUREMENT_DLQ_TOPIC, MEASUREMENT_RAW_TOPIC


@dataclass(frozen=True)
class ConsumerServiceConfig:
    kafka_bootstrap_servers: str
    measurement_raw_topic: str
    measurement_dlq_topic: str
    kafka_consumer_group: str
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password_configured: bool
    allow_canonical_write: str
    allow_production_ddl: str


def load_config_from_env() -> ConsumerServiceConfig:
    """Load non-secret service configuration from environment.

    Passwords are represented as booleans only so dry-run output cannot leak a
    secret value.
    """

    return ConsumerServiceConfig(
        kafka_bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "cms-kafka:9092"),
        measurement_raw_topic=os.getenv("MEASUREMENT_RAW_TOPIC", MEASUREMENT_RAW_TOPIC),
        measurement_dlq_topic=os.getenv("MEASUREMENT_DLQ_TOPIC", MEASUREMENT_DLQ_TOPIC),
        kafka_consumer_group=os.getenv("KAFKA_CONSUMER_GROUP", KAFKA_CONSUMER_GROUP),
        postgres_host=os.getenv("POSTGRES_HOST", "172.31.47.236"),
        postgres_port=int(os.getenv("POSTGRES_PORT", "5432")),
        postgres_db=os.getenv("POSTGRES_DB", "cms"),
        postgres_user=os.getenv("POSTGRES_USER", "cms"),
        postgres_password_configured=bool(os.getenv("POSTGRES_PASSWORD")),
        allow_canonical_write=os.getenv("ALLOW_CANONICAL_WRITE", "0"),
        allow_production_ddl=os.getenv("ALLOW_PRODUCTION_DDL", "0"),
    )


def build_dry_run_report(config: ConsumerServiceConfig) -> dict[str, object]:
    """Return a redacted readiness report for the consumer service skeleton."""

    return {
        "service": "kafka_to_postgres_consumer",
        "mode": "dry_run",
        "external_clients_started": False,
        "offset_commit_attempted": False,
        "postgres_write_attempted": False,
        "canonical_write_allowed": config.allow_canonical_write == "1",
        "production_ddl_allowed": config.allow_production_ddl == "1",
        "config": asdict(config),
    }


def run_runtime_report(*, max_messages: int | None = None, poll_timeout: float = 1.0) -> dict[str, object]:
    """Run the runtime consumer loop with lazy Kafka/PostgreSQL adapters."""

    from cms.data.runtime_consumer_loop import run_consumer_loop
    from cms.data.runtime_kafka import create_confluent_kafka_consumer, create_confluent_kafka_producer
    from cms.data.runtime_postgres import create_psycopg_event_writer

    consumer = create_confluent_kafka_consumer()
    writer = create_psycopg_event_writer()
    dlq_producer = create_confluent_kafka_producer()
    stats = run_consumer_loop(
        consumer=consumer,
        writer=writer,
        dlq_producer=dlq_producer,
        max_messages=max_messages,
        poll_timeout=poll_timeout,
    )
    return {
        "service": "kafka_to_postgres_consumer",
        "mode": "runtime",
        "stats": asdict(stats),
        "secrets_reported": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CMS AWS Phase 1 Kafka-to-PostgreSQL consumer service")
    parser.add_argument("--dry-run", action="store_true", help="validate config and avoid external Kafka/PostgreSQL clients")
    parser.add_argument("--runtime", action="store_true", help="run real Kafka/PostgreSQL adapters; requires explicit approval gate")
    parser.add_argument("--idle-seconds", type=int, default=0, help="optional idle duration after dry-run report for container smoke")
    parser.add_argument("--max-messages", type=int, default=None, help="optional bounded message count for smoke runs")
    parser.add_argument("--poll-timeout", type=float, default=1.0, help="Kafka poll timeout in seconds")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config_from_env()

    runtime_enabled = args.runtime or os.getenv("CMS_ENABLE_RUNTIME_CONSUMER") == "1"
    if runtime_enabled and not args.dry_run:
        print(json.dumps(run_runtime_report(max_messages=args.max_messages, poll_timeout=args.poll_timeout), ensure_ascii=False, sort_keys=True), flush=True)
        return 0

    print(json.dumps(build_dry_run_report(config), ensure_ascii=False, sort_keys=True), flush=True)
    if args.idle_seconds > 0:
        time.sleep(args.idle_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
