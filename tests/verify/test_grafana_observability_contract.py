import json
from pathlib import Path

from cms.contracts.observability import (
    ALERT_RULE_CONTRACTS,
    DASHBOARD_PANEL_CONTRACTS,
    FASTAPI_METRICS,
    GRAFANA_ALERT_RULES,
    GRAFANA_LIVE_PIPELINE_DASHBOARD_UID,
    GRAFANA_POSTGRES_DATASOURCE_UID,
    KAFKA_METRICS,
    LATENCY_METRICS,
    LIVE_OPS_TABLES,
    WORKER_NAMES,
)

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_PATH = ROOT / "docker/grafana/provisioning/dashboards/json/cms_live_pipeline_overview.json"
DATASOURCE_PATH = ROOT / "docker/grafana/provisioning/datasources/postgres_cms_live.yaml"
ALERT_PATH = ROOT / "docker/grafana/provisioning/alerting/live_pipeline_alerts.yaml"
CONTACT_POINT_PATH = ROOT / "docker/grafana/provisioning/alerting/contact_points.yaml"
QUERY_DOC_PATH = ROOT / "docs/qa/grafana_ops_query_contract.md"
PLAN_DOC_PATH = ROOT / "docs/qa/grafana_observability_plan.md"
DDL_PATH = ROOT / "scripts/migrations/live_schema_draft.sql"
COMPOSE_PATH = ROOT / "docker-compose.yml"


def test_observability_contract_constants_cover_live_pipeline_monitoring():
    assert GRAFANA_POSTGRES_DATASOURCE_UID == "postgres-cms-live"
    assert GRAFANA_LIVE_PIPELINE_DASHBOARD_UID == "cms-live-pipeline"
    assert "ops.worker_heartbeat" in LIVE_OPS_TABLES
    assert "ops.pipeline_latency_event" in LIVE_OPS_TABLES
    assert "end_to_end_sec" in LATENCY_METRICS
    assert "source_to_mongo_sec" not in LATENCY_METRICS
    assert "mongo_to_event_sec" not in LATENCY_METRICS
    assert "source_to_fastapi_sec" in LATENCY_METRICS
    assert "fastapi_to_kafka_sec" in LATENCY_METRICS
    assert "kafka_to_event_sec" in LATENCY_METRICS
    assert "kafka_to_postgres_consumer" in WORKER_NAMES
    assert "mongo_to_postgres_ingest" not in WORKER_NAMES
    assert "kafka_consumer_lag" in KAFKA_METRICS
    assert "kafka_dlq_count" in KAFKA_METRICS
    assert "kafka_produce_error_count" in KAFKA_METRICS
    assert "fastapi_ingest_5xx_count" in FASTAPI_METRICS
    assert {rule.name for rule in ALERT_RULE_CONTRACTS} == set(GRAFANA_ALERT_RULES)
    assert {panel.source_table for panel in DASHBOARD_PANEL_CONTRACTS} >= {"live.bucket_queue", "qa.live_measurement_issue", "ops.pipeline_latency_event"}


def test_grafana_dashboard_json_is_valid_and_uses_postgres_datasource():
    dashboard = json.loads(DASHBOARD_PATH.read_text(encoding="utf-8"))

    assert dashboard["uid"] == GRAFANA_LIVE_PIPELINE_DASHBOARD_UID
    assert dashboard["refresh"] == "30s"
    assert len(dashboard["panels"]) >= 8
    titles = {panel["title"] for panel in dashboard["panels"]}
    assert {
        "Events last 5m",
        "Queue by status / job / resolution",
        "Recent blocking issues",
        "End-to-end latency p95",
        "Stage latency p95",
        "Worker heartbeat",
        "Promotion readiness",
        "FastAPI ingest health",
        "Kafka consumer lag",
        "Kafka DLQ and produce errors",
    } <= titles
    for panel in dashboard["panels"]:
        assert panel["datasource"]["uid"] == GRAFANA_POSTGRES_DATASOURCE_UID
        for target in panel["targets"]:
            assert target["rawSql"].strip().upper().startswith("SELECT")


def test_grafana_provisioning_uses_placeholders_not_secrets():
    datasource = DATASOURCE_PATH.read_text(encoding="utf-8")
    contact_point = CONTACT_POINT_PATH.read_text(encoding="utf-8")
    compose = COMPOSE_PATH.read_text(encoding="utf-8")

    assert "uid: postgres-cms-live" in datasource
    assert "${GRAFANA_POSTGRES_PASSWORD}" in datasource
    assert "${GRAFANA_DISCORD_WEBHOOK_URL}" in contact_point
    assert "profiles:" in compose
    assert "observability" in compose
    assert "grafana/grafana" in compose
    for text in (datasource, contact_point):
        assert "discord.com/api/webhooks/" not in text
        assert "password:" not in text.lower() or "${GRAFANA_POSTGRES_PASSWORD}" in text


def test_alert_rules_and_docs_cover_p0_operational_cases():
    alert_text = ALERT_PATH.read_text(encoding="utf-8")
    query_doc = QUERY_DOC_PATH.read_text(encoding="utf-8")
    plan_doc = PLAN_DOC_PATH.read_text(encoding="utf-8")

    for token in [
        "no-live-events-5m",
        "oldest-pending-queue-age-10m",
        "queue-failed-rows-present",
        "worker-heartbeat-missing-2m",
        "report_worker",
        "kafka_to_postgres_consumer",
        "WHEN h.worker_name IS NULL THEN 999999",
        "end-to-end-p95-300s",
        "kafka-consumer-lag-10k",
        "kafka-dlq-count-nonzero",
        "kafka-produce-error-count-nonzero",
        "fastapi-ingest-5xx-spike-5m",
        "kafka_consumer_lag",
        "kafka_dlq_count",
        "kafka_produce_error_count",
        "fastapi_ingest_5xx_count",
        "policy-miss-spike-10m",
        "peak-branch-lag-gt-mean-branch",
        "non-blocking-qa-warning-spike-10m",
        "severity: P0",
        "severity: P1",
        "severity: P2",
        "severity IN ('low', 'medium', 'high')",
        "datasourceUid: postgres-cms-live",
    ]:
        assert token in alert_text
    for metric in LATENCY_METRICS:
        assert metric in query_doc
    for metric in KAFKA_METRICS + FASTAPI_METRICS:
        assert metric in query_doc
    assert "mongo_to_postgres_ingest" not in query_doc
    assert "source_to_mongo_sec" not in query_doc
    assert "mongo_to_event_sec" not in query_doc
    for token in ["Alert runbook", "Monitoring/UI = Grafana", "Operational assistant = Hermes", "Control plane = later FastAPI or CLI", "Kafka consumer lag", "FastAPI ingest 5xx"]:
        assert token in plan_doc


def test_live_schema_draft_includes_ops_observability_tables():
    ddl = DDL_PATH.read_text(encoding="utf-8")

    assert "source_to_mongo_sec" not in ddl
    assert "mongo_to_event_sec" not in ddl

    for token in [
        "CREATE SCHEMA IF NOT EXISTS ops;",
        "CREATE TABLE IF NOT EXISTS ops.worker_heartbeat",
        "CREATE TABLE IF NOT EXISTS ops.pipeline_latency_event",
        "CREATE TABLE IF NOT EXISTS ops.kafka_consumer_lag",
        "CREATE TABLE IF NOT EXISTS ops.fastapi_ingest_metric",
        "business_idempotency_key TEXT NOT NULL",
        "worker_heartbeat_status_check",
        "pipeline_latency_nonnegative_check",
        "kafka_consumer_lag_nonnegative_check",
        "fastapi_ingest_metric_name_check",
        "end_to_end_sec DOUBLE PRECISION",
        "source_to_fastapi_sec DOUBLE PRECISION",
        "fastapi_to_kafka_sec DOUBLE PRECISION",
        "kafka_to_event_sec DOUBLE PRECISION",
    ]:
        assert token in ddl
