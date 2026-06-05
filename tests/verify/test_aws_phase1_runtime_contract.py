from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker" / "compose.aws.phase1.yml"
CONSUMER_SCRIPT = ROOT / "scripts" / "live" / "run_consumer_service.py"
ENV_EXAMPLE = ROOT / "docker" / "aws_phase1.env.example"


def test_aws_phase1_compose_keeps_kafka_private_and_uses_phase1_topics() -> None:
    text = COMPOSE.read_text(encoding="utf-8")

    assert "cms-kafka:" in text
    assert "apache/kafka:3.7.1" in text
    assert "bitnami/kafka" not in text
    assert "KAFKA_LISTENERS" in text
    assert "KAFKA_ADVERTISED_LISTENERS" in text
    assert "MEASUREMENT_RAW_TOPIC" in text
    assert "measurement_raw_v1" in text
    assert "MEASUREMENT_DLQ_TOPIC" in text
    assert "measurement_dead_letter_v1" in text
    assert "KAFKA_CONSUMER_GROUP" in text
    assert "postgres-live-ingest" in text
    assert "9092:9092" not in text
    assert "0.0.0.0:9092" not in text
    assert "${CMS_API_BIND:-127.0.0.1}:${CMS_API_PORT:-8000}:8000" in text


def test_aws_phase1_compose_uses_lean_phase1_image_not_full_ml_image() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    dockerfile = ROOT / "docker" / "Dockerfile.phase1"
    requirements = ROOT / "docker" / "requirements.phase1.txt"

    assert dockerfile.exists()
    assert requirements.exists()
    assert "dockerfile: docker/Dockerfile.phase1" in text
    assert text.count("dockerfile: docker/Dockerfile.phase1") == 1
    assert "dockerfile: docker/Dockerfile\n" not in text
    requirements_text = requirements.read_text(encoding="utf-8")
    for heavy_dependency in ("torch", "transformers", "mlflow", "jupyterlab", "xgboost"):
        assert heavy_dependency not in requirements_text
    for phase1_dependency in ("fastapi", "uvicorn", "psycopg", "pydantic", "confluent-kafka"):
        assert phase1_dependency in requirements_text


def test_aws_phase1_consumer_entrypoint_exists_and_is_used_by_compose() -> None:
    text = COMPOSE.read_text(encoding="utf-8")

    assert CONSUMER_SCRIPT.exists()
    assert "scripts/live/run_consumer_service.py" in text
    assert "cms-kafka-init" in text
    assert "/opt/kafka/bin/kafka-topics.sh" in text
    assert "--dry-run" in text
    assert "--runtime" not in text
    assert "--idle-seconds" not in text


def test_aws_phase1_consumer_entrypoint_default_is_dry_run_safe() -> None:
    completed = subprocess.run(
        [sys.executable, str(CONSUMER_SCRIPT)],
        check=True,
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src"), "POSTGRES_PASSWORD": "secret-value-that-must-not-print"},
        capture_output=True,
        text=True,
    )
    report = json.loads(completed.stdout)

    assert report["mode"] == "dry_run"
    assert report["external_clients_started"] is False
    assert report["postgres_write_attempted"] is False
    assert "secret-value-that-must-not-print" not in completed.stdout


def test_aws_phase1_consumer_entrypoint_dry_run_is_redacted_and_side_effect_free() -> None:
    completed = subprocess.run(
        [sys.executable, str(CONSUMER_SCRIPT), "--dry-run"],
        check=True,
        cwd=ROOT,
        env={
            "PYTHONPATH": str(ROOT / "src"),
            "KAFKA_BOOTSTRAP_SERVERS": "cms-kafka:9092",
            "POSTGRES_PASSWORD": "secret-value-that-must-not-print",
        },
        capture_output=True,
        text=True,
    )
    report = json.loads(completed.stdout)

    assert report["service"] == "kafka_to_postgres_consumer"
    assert report["external_clients_started"] is False
    assert report["offset_commit_attempted"] is False
    assert report["postgres_write_attempted"] is False
    assert report["config"]["measurement_raw_topic"] == "measurement_raw_v1"
    assert report["config"]["measurement_dlq_topic"] == "measurement_dead_letter_v1"
    assert report["config"]["postgres_password_configured"] is True
    assert "secret-value-that-must-not-print" not in completed.stdout


def test_aws_phase1_consumer_entrypoint_runtime_mode_uses_lazy_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[bool] = []
    subscribed: list[list[str]] = []

    class FakeProducer:
        def __init__(self, config: dict[str, object]) -> None:
            self.config = config

        def produce(self, *, topic: str, key: bytes, value: bytes, on_delivery: object | None = None) -> None:
            pass

        def flush(self, timeout: float) -> int:
            return 0

    class FakeConsumer:
        def __init__(self, config: dict[str, object]) -> None:
            self.config = config

        def subscribe(self, topics: list[str]) -> None:
            subscribed.append(topics)

        def poll(self, timeout: float) -> None:
            return None

        def close(self) -> None:
            closed.append(True)

    class FakeConnection:
        def __enter__(self) -> FakeConnection:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            pass

    confluent = ModuleType("confluent_kafka")
    confluent.Producer = FakeProducer  # type: ignore[attr-defined]
    confluent.Consumer = FakeConsumer  # type: ignore[attr-defined]
    psycopg = ModuleType("psycopg")
    psycopg.connect = lambda **kwargs: FakeConnection()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "confluent_kafka", confluent)
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "cms-kafka:9092")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret-value-that-must-not-print")

    from scripts.live import run_consumer_service

    report = run_consumer_service.run_runtime_report(max_messages=0, poll_timeout=0.01)

    assert report["service"] == "kafka_to_postgres_consumer"
    assert report["mode"] == "runtime"
    assert report["stats"] == {"polled": 0, "processed": 0, "committed": 0, "inserted": 0, "duplicate": 0, "dlq": 0, "retry": 0}
    assert subscribed == [["measurement_raw_v1"]]
    assert closed == [True]
    assert "secret-value-that-must-not-print" not in json.dumps(report)


def test_aws_phase1_env_example_documents_required_non_secret_shape() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")

    assert "KAFKA_BOOTSTRAP_SERVERS=cms-kafka:9092" in text
    assert "MEASUREMENT_RAW_TOPIC=measurement_raw_v1" in text
    assert "MEASUREMENT_DLQ_TOPIC=measurement_dead_letter_v1" in text
    assert "KAFKA_CONSUMER_GROUP=postgres-live-ingest" in text
    assert "CMS_ENABLE_RUNTIME_KAFKA_PRODUCER=1" in text
    assert "CMS_API_BIND=127.0.0.1" in text
    assert "POSTGRES_HOST=172.31.47.236" in text
    assert "POSTGRES_DB=cms" in text
    assert "POSTGRES_USER=cms" in text
    assert "POSTGRES_PASSWORD=" in text
    assert "REPLACE_ME" not in text
    assert "postgresql://" not in text
