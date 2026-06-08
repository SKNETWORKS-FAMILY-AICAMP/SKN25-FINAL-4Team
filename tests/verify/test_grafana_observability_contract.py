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
PROMETHEUS_DATASOURCE_PATH = ROOT / "docker/grafana/provisioning/datasources/prometheus_cms_stream.yaml"
PROMETHEUS_CONFIG_PATH = ROOT / "docker/prometheus/phase1.yml"
KAFKA_EXPORTER_DASHBOARD_PATH = ROOT / "docker/grafana/provisioning/dashboards/json/cms_phase1b_kafka_exporter.json"
SYSTEM_POSTGRES_DASHBOARD_PATH = ROOT / "docker/grafana/provisioning/dashboards/json/cms_phase1c_system_postgres.json"
SOAK_GATES_DASHBOARD_PATH = ROOT / "docker/grafana/provisioning/dashboards/json/cms_live_soak_gates.json"
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
    assert "ops.pipeline_metric" in LIVE_OPS_TABLES
    assert "live.measurement_policy" in LIVE_OPS_TABLES
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
    assert {panel.source_table for panel in DASHBOARD_PANEL_CONTRACTS} >= {
        "live.bucket_queue",
        "live.measurement_event",
        "live.measurement_policy",
        "ops.pipeline_latency_event",
        "ops.pipeline_metric",
        "qa.live_measurement_issue",
    }


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
        "Active meters last 5m",
        "Active series last 5m",
        "Meter collection by series",
        "Stale meter series",
        "Policy status by series",
        "Processed / inserted / duplicate / retry / DLQ invariant",
    } <= titles
    for panel in dashboard["panels"]:
        assert panel["datasource"]["uid"] == GRAFANA_POSTGRES_DATASOURCE_UID
        for target in panel["targets"]:
            assert target["rawSql"].strip().upper().startswith(("SELECT", "WITH"))


def test_live_pipeline_meter_coverage_panels_are_bounded_and_contract_aligned():
    dashboard = json.loads(DASHBOARD_PATH.read_text(encoding="utf-8"))
    panels = {panel["title"]: panel for panel in dashboard["panels"]}

    raw = json.dumps(dashboard)
    for token in [
        "live.measurement_event",
        "live.measurement_policy",
        "ops.pipeline_metric",
        "meter_urn",
        "measurement",
        "event_age_sec",
        "active",
        "policy_lookup_status",
        "consumer_processed",
        "consumer_inserted",
        "consumer_duplicate",
        "consumer_retry",
        "consumer_dlq",
        "invariant_delta",
        "invariant_status",
    ]:
        assert token in raw

    for title in [
        "Meter collection by series",
        "Stale meter series",
        "Policy status by series",
        "Processed / inserted / duplicate / retry / DLQ invariant",
    ]:
        sql = panels[title]["targets"][0]["rawSql"]
        assert "LIMIT" in sql.upper()
        assert "GROUP BY" in sql.upper()

    for title in ["Active meters last 5m", "Active series last 5m"]:
        sql = panels[title]["targets"][0]["rawSql"]
        assert "count(DISTINCT" in sql
        assert "GROUP BY" not in sql.upper()


def test_grafana_provisioning_uses_placeholders_not_secrets():
    datasource = DATASOURCE_PATH.read_text(encoding="utf-8")
    prometheus_datasource = PROMETHEUS_DATASOURCE_PATH.read_text(encoding="utf-8")
    contact_point = CONTACT_POINT_PATH.read_text(encoding="utf-8")
    compose = COMPOSE_PATH.read_text(encoding="utf-8")

    assert "uid: postgres-cms-live" in datasource
    assert "${GRAFANA_POSTGRES_PASSWORD}" in datasource
    assert "uid: prometheus-cms-stream" in prometheus_datasource
    assert "${GRAFANA_PROMETHEUS_URL}" in prometheus_datasource
    assert "${GRAFANA_DISCORD_WEBHOOK_URL}" in contact_point
    assert "profiles:" in compose
    assert "observability" in compose
    assert "grafana/grafana" in compose
    assert "GRAFANA_PROMETHEUS_URL" in compose
    for text in (datasource, contact_point):
        assert "discord.com/api/webhooks/" not in text
        assert "password:" not in text.lower() or "${GRAFANA_POSTGRES_PASSWORD}" in text


def test_kafka_exporter_dashboard_uses_prometheus_datasource_and_promql() -> None:
    dashboard = json.loads(KAFKA_EXPORTER_DASHBOARD_PATH.read_text(encoding="utf-8"))

    assert dashboard["uid"] == "cms-phase1b-kafka-exporter"
    assert dashboard["refresh"] == "15s"
    titles = {panel["title"] for panel in dashboard["panels"]}
    assert {
        "Kafka brokers",
        "measurement_raw_v1 log end offset",
        "postgres-live-ingest consumer lag",
        "DLQ log end offset",
    } <= titles
    for panel in dashboard["panels"]:
        assert panel["datasource"]["uid"] == "prometheus-cms-stream"
        for target in panel["targets"]:
            assert "expr" in target
            assert not target["expr"].strip().upper().startswith(("SELECT", "INSERT", "UPDATE", "DELETE"))
    raw = json.dumps(dashboard)
    assert "kafka_consumergroup_lag" in raw
    assert "kafka_topic_partition_current_offset" in raw
    assert "measurement_raw_v1" in raw
    assert "measurement_dead_letter_v1" in raw


def test_system_postgres_dashboard_uses_prometheus_datasource_and_promql() -> None:
    dashboard = json.loads(SYSTEM_POSTGRES_DASHBOARD_PATH.read_text(encoding="utf-8"))

    assert dashboard["uid"] == "cms-phase1c-system-postgres"
    assert dashboard["refresh"] == "30s"
    titles = {panel["title"] for panel in dashboard["panels"]}
    assert {
        "Node exporter targets",
        "CPU busy by node",
        "Memory available % by node",
        "Filesystem free % by node",
        "Disk read/write bytes by node",
        "Network rx/tx bytes by node",
        "PostgreSQL exporter up",
        "PostgreSQL active connections",
        "PostgreSQL cache hit ratio",
        "PostgreSQL temp bytes/files",
        "PostgreSQL locks by mode",
        "PostgreSQL deadlocks last 5m",
        "Kafka lag current",
        "Kafka consumer lag vs CPU busy",
    } <= titles
    for panel in dashboard["panels"]:
        assert panel["datasource"]["uid"] == "prometheus-cms-stream"
        for target in panel["targets"]:
            assert "expr" in target
            assert not target["expr"].strip().upper().startswith(("SELECT", "INSERT", "UPDATE", "DELETE"))
    raw = json.dumps(dashboard)
    for token in [
        "node_cpu_seconds_total",
        "node_memory_MemAvailable_bytes",
        "node_memory_MemTotal_bytes",
        "node_filesystem_avail_bytes",
        "node_filesystem_size_bytes",
        "node_disk_read_bytes_total",
        "node_disk_written_bytes_total",
        "node_network_receive_bytes_total",
        "node_network_transmit_bytes_total",
        "pg_up",
        "pg_stat_activity_count",
        "pg_stat_database_blks_hit",
        "pg_stat_database_blks_read",
        "pg_stat_database_temp_bytes",
        "pg_stat_database_temp_files",
        "pg_locks_count",
        "pg_stat_database_deadlocks",
        "kafka_consumergroup_lag_sum",
        "postgres-live-ingest",
        "measurement_raw_v1",
    ]:
        assert token in raw

    prometheus_config = PROMETHEUS_CONFIG_PATH.read_text(encoding="utf-8")
    for token in [
        "job_name: node-exporter-stream",
        "job_name: node-exporter-db",
        "job_name: postgres-exporter",
        "job_name: kafka-exporter",
        "node: cms-stream",
        "node: cms-db",
    ]:
        assert token in prometheus_config


def test_live_soak_gates_dashboard_uses_postgres_datasource_and_ops_metrics() -> None:
    dashboard = json.loads(SOAK_GATES_DASHBOARD_PATH.read_text(encoding="utf-8"))

    assert dashboard["uid"] == "cms-live-soak-gates"
    assert dashboard["title"] == "CMS Live Soak Gates"
    assert dashboard["refresh"] == "15s"
    titles = {panel["title"] for panel in dashboard["panels"]}
    assert {
        "Latest gate status",
        "Latest consumer lag",
        "Latest DLQ",
        "Latest retry",
        "Soak run summary",
        "Producer/consumer events by run",
        "Retry / DLQ / lag after",
        "Recent soak metrics",
    } <= titles
    for panel in dashboard["panels"]:
        assert panel["datasource"]["uid"] == GRAFANA_POSTGRES_DATASOURCE_UID
        for target in panel["targets"]:
            sql = target["rawSql"].strip().upper()
            assert sql.startswith(("SELECT", "WITH"))
            assert not sql.startswith(("INSERT", "UPDATE", "DELETE"))
    raw = json.dumps(dashboard)
    for token in [
        "ops.pipeline_metric",
        "live_soak_%",
        "producer_accepted",
        "consumer_processed",
        "consumer_inserted",
        "consumer_committed",
        "consumer_retry",
        "consumer_dlq",
        "consumer_lag_after",
        "gate_status_warning",
    ]:
        assert token in raw


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
        "CREATE TABLE IF NOT EXISTS mart.pmax_forecast_15min",
        "CREATE TABLE IF NOT EXISTS ops.pmax_forecast_inference_log",
        "CREATE TABLE IF NOT EXISTS qa.pmax_forecast_evaluation",
        "forecast_target_ts_check",
        "pmax_forecast_run_status_check",
        "actual_window_ts TIMESTAMPTZ NOT NULL",
        "forecast_actual_window_ts_check CHECK (actual_window_ts = target_ts - interval '15 minutes')",
        "pmax_forecast_logical_source_check",
        "pmax_forecast_evaluation_actual_window_ts_check CHECK (actual_window_ts = target_ts - interval '15 minutes')",
    ]:
        assert token in ddl
