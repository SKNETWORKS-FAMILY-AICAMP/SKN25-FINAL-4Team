"""edge stream Kafka-to-PostgreSQL consumer service entrypoint.

This script is intentionally safe as a deployment entrypoint. The default compose
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
    runtime_profile: str
    kafka_bootstrap_servers: str
    measurement_raw_topic: str
    kafka_topic_identity: str | None
    measurement_dlq_topic: str
    kafka_consumer_group: str
    kafka_auto_offset_reset: str
    kafka_consumer_client_id: str | None
    kafka_consumer_instance_id: str | None
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
        runtime_profile=os.getenv("CMS_RUNTIME_PROFILE", "edge"),
        kafka_bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", ""),
        measurement_raw_topic=os.getenv("MEASUREMENT_RAW_TOPIC", MEASUREMENT_RAW_TOPIC),
        kafka_topic_identity=os.getenv("KAFKA_TOPIC_IDENTITY") or None,
        measurement_dlq_topic=os.getenv("MEASUREMENT_DLQ_TOPIC", MEASUREMENT_DLQ_TOPIC),
        kafka_consumer_group=os.getenv("KAFKA_CONSUMER_GROUP", KAFKA_CONSUMER_GROUP),
        kafka_auto_offset_reset=os.getenv("KAFKA_AUTO_OFFSET_RESET", "earliest"),
        kafka_consumer_client_id=os.getenv("KAFKA_CONSUMER_CLIENT_ID") or None,
        kafka_consumer_instance_id=os.getenv("KAFKA_CONSUMER_INSTANCE_ID") or os.getenv("KAFKA_GROUP_INSTANCE_ID") or None,
        postgres_host=os.getenv("POSTGRES_HOST", ""),
        postgres_port=int(os.getenv("POSTGRES_PORT", "5432")),
        postgres_db=os.getenv("POSTGRES_DB", "cms"),
        postgres_user=os.getenv("POSTGRES_USER", "cms"),
        postgres_password_configured=bool(os.getenv("POSTGRES_PASSWORD")),
        allow_canonical_write=os.getenv("ALLOW_CANONICAL_WRITE", "0"),
        allow_production_ddl=os.getenv("ALLOW_PRODUCTION_DDL", "0"),
    )


def build_dry_run_report(config: ConsumerServiceConfig) -> dict[str, object]:
    """Return a redacted readiness report for the consumer service entrypoint."""

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


def validate_edge_runtime_config(config: ConsumerServiceConfig) -> None:
    """Fail before client imports when edge runtime still points at legacy AWS stream wiring."""

    if config.runtime_profile != "edge":
        return
    from cms.data.runtime_kafka import validate_kafka_cluster_bootstrap_servers
    from cms.data.runtime_postgres import validate_edge_postgres_host

    validate_kafka_cluster_bootstrap_servers(config.kafka_bootstrap_servers)
    validate_edge_postgres_host(config.postgres_host)


def run_runtime_report(*, max_messages: int | None = None, poll_timeout: float = 1.0, max_idle_polls: int | None = 60) -> dict[str, object]:
    """Run the runtime consumer loop with lazy Kafka/PostgreSQL adapters."""

    from cms.data.runtime_consumer_loop import run_consumer_loop
    from cms.data.runtime_kafka import create_confluent_kafka_consumer, create_confluent_kafka_producer
    from cms.data.runtime_postgres import create_psycopg_event_writer

    config = load_config_from_env()
    consumer = create_confluent_kafka_consumer()
    writer = create_psycopg_event_writer()
    dlq_producer = create_confluent_kafka_producer()
    stats = run_consumer_loop(
        consumer=consumer,
        writer=writer,
        dlq_producer=dlq_producer,
        max_messages=max_messages,
        poll_timeout=poll_timeout,
        max_idle_polls=max_idle_polls,
        kafka_topic_identity=config.kafka_topic_identity,
    )
    return {
        "service": "kafka_to_postgres_consumer",
        "mode": "runtime",
        "stats": asdict(stats),
        "secrets_reported": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CMS edge stream Kafka-to-PostgreSQL consumer service")
    parser.add_argument("--dry-run", action="store_true", help="validate config and avoid external Kafka/PostgreSQL clients")
    parser.add_argument("--runtime", action="store_true", help="run real Kafka/PostgreSQL adapters; requires explicit approval gate")
    parser.add_argument("--idle-seconds", type=int, default=0, help="optional idle duration after dry-run report for container smoke")
    parser.add_argument("--max-messages", type=int, default=None, help="optional bounded message count for smoke runs")
    parser.add_argument("--poll-timeout", type=float, default=1.0, help="Kafka poll timeout in seconds")
    parser.add_argument(
        "--max-idle-polls",
        type=int,
        default=60,
        help="stop after this many consecutive empty polls in runtime mode; 0 keeps a standing consumer alive",
    )
    args = parser.parse_args()
    if args.max_idle_polls < 0:
        raise SystemExit("--max-idle-polls must be non-negative")
    return args


def main() -> int:
    args = parse_args()
    config = load_config_from_env()

    runtime_flag = os.getenv("CMS_ENABLE_RUNTIME_CONSUMER") == "1"
    if args.runtime and not args.dry_run:
        if not runtime_flag:
            raise SystemExit("runtime consumer requires CMS_ENABLE_RUNTIME_CONSUMER=1")
        validate_edge_runtime_config(config)
        max_idle_polls = None if args.max_idle_polls == 0 else args.max_idle_polls
        print(json.dumps(run_runtime_report(max_messages=args.max_messages, poll_timeout=args.poll_timeout, max_idle_polls=max_idle_polls), ensure_ascii=False, sort_keys=True), flush=True)
        return 0

    print(json.dumps(build_dry_run_report(config), ensure_ascii=False, sort_keys=True), flush=True)
    if args.idle_seconds > 0:
        time.sleep(args.idle_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
