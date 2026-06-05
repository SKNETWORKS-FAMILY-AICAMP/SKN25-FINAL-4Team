from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE_COMPOSE = ROOT / "docker" / "compose.aws.phase1.yml"
KAFKA3_OVERRIDE = ROOT / "docker" / "compose.aws.phase1.kafka3.override.yml"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_kafka3_override_defines_three_private_kraft_brokers() -> None:
    base = _text(BASE_COMPOSE)
    override = _text(KAFKA3_OVERRIDE)
    quorum = "1@cms-kafka:9093,2@cms-kafka-2:9093,3@cms-kafka-3:9093"

    assert 'KAFKA_NODE_ID: "1"' in base
    assert 'KAFKA_NODE_ID: "2"' in override
    assert 'KAFKA_NODE_ID: "3"' in override
    assert quorum in override
    assert 'KAFKA_ADVERTISED_LISTENERS: "PLAINTEXT://cms-kafka:9092"' in base
    assert 'KAFKA_ADVERTISED_LISTENERS: "PLAINTEXT://cms-kafka-2:9092"' in override
    assert 'KAFKA_ADVERTISED_LISTENERS: "PLAINTEXT://cms-kafka-3:9092"' in override
    assert override.count('CLUSTER_ID: "${KAFKA_CLUSTER_ID:-Q01TX1BIT1NFNF9DTFVTVEVS}"') == 3
    assert "cms_kafka_1_data:/var/lib/kafka/data" in override
    assert "cms_kafka_2_data:/var/lib/kafka/data" in override
    assert "cms_kafka_3_data:/var/lib/kafka/data" in override


def test_kafka3_override_sets_rf_minisr_and_clients_to_three_brokers() -> None:
    override = _text(KAFKA3_OVERRIDE)
    bootstrap = "cms-kafka:9092,cms-kafka-2:9092,cms-kafka-3:9092"

    assert '${KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR:-3}' in override
    assert '${KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR:-3}' in override
    assert '${KAFKA_TRANSACTION_STATE_LOG_MIN_ISR:-2}' in override
    assert '${KAFKA_DEFAULT_REPLICATION_FACTOR:-3}' in override
    assert '${KAFKA_MIN_INSYNC_REPLICAS:-2}' in override
    assert '--replication-factor ${KAFKA_RAW_REPLICATION_FACTOR:-3}' in override
    assert '--replication-factor ${KAFKA_DLQ_REPLICATION_FACTOR:-3}' in override
    assert bootstrap in override
    assert "--kafka.server=cms-kafka:9092" in override
    assert "--kafka.server=cms-kafka-2:9092" in override
    assert "--kafka.server=cms-kafka-3:9092" in override


def test_kafka3_override_keeps_no_public_kafka_ports_and_write_gates_closed() -> None:
    override = _text(KAFKA3_OVERRIDE)

    assert "ports:" not in override
    assert "9092:9092" not in override
    assert "9093:9093" not in override
    assert "0.0.0.0:9092" not in override
    assert "0.0.0.0:9093" not in override
    assert override.count('ALLOW_CANONICAL_WRITE: "0"') == 2
    assert override.count('ALLOW_PRODUCTION_DDL: "0"') == 2
