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
DASHBOARD_DIR = ROOT / "docker/grafana/provisioning/dashboards/json"
ARCHIVE_DASHBOARD_DIR = ROOT / "docker/grafana/provisioning/dashboards/archive"
RUNTIME_DASHBOARD_PATH = DASHBOARD_DIR / "cms_runtime_operations.json"
TEST_GATES_DASHBOARD_PATH = DASHBOARD_DIR / "cms_test_gates.json"
DATASOURCE_PATH = ROOT / "docker/grafana/provisioning/datasources/postgres_cms_live.yaml"
PROMETHEUS_DATASOURCE_PATH = ROOT / "docker/grafana/provisioning/datasources/prometheus_cms_stream.yaml"
PROMETHEUS_CONFIG_PATH = ROOT / "docker/prometheus/phase1.yml"
ALERT_PATH = ROOT / "docker/grafana/provisioning/alerting/live_pipeline_alerts.yaml"
CONTACT_POINT_PATH = ROOT / "docker/grafana/provisioning/alerting/contact_points.yaml"
QUERY_DOC_PATH = ROOT / "docs/qa/grafana_ops_query_contract.md"
PLAN_DOC_PATH = ROOT / "docs/qa/grafana_observability_plan.md"
DDL_PATH = ROOT / "scripts/migrations/live_schema_draft.sql"
COMPOSE_PATH = ROOT / "docker-compose.yml"


def load_dashboard(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def assert_panel_queries_are_read_only(dashboard: dict) -> None:
    for panel in dashboard["panels"]:
        assert panel["datasource"]["uid"] in {GRAFANA_POSTGRES_DATASOURCE_UID, "prometheus-cms-stream"}
        for target in panel["targets"]:
            if panel["datasource"]["uid"] == GRAFANA_POSTGRES_DATASOURCE_UID:
                sql = target["rawSql"].strip().upper()
                assert sql.startswith(("SELECT", "WITH"))
                assert not sql.startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP"))
            else:
                assert "expr" in target
                assert not target["expr"].strip().upper().startswith(("SELECT", "INSERT", "UPDATE", "DELETE"))


def test_observability_contract_constants_cover_runtime_monitoring():
    assert GRAFANA_POSTGRES_DATASOURCE_UID == "postgres-cms-live"
    assert GRAFANA_LIVE_PIPELINE_DASHBOARD_UID == "cms-runtime-operations"
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


def test_only_runtime_and_test_gate_dashboards_are_active() -> None:
    active = {path.name for path in DASHBOARD_DIR.glob("*.json")}
    archived = {path.name for path in ARCHIVE_DASHBOARD_DIR.glob("*.json")}

    assert active == {"cms_runtime_operations.json", "cms_test_gates.json"}
    assert {
        "cms_live_pipeline_overview.json",
        "cms_live_soak_gates.json",
        "cms_phase1_status.json",
        "cms_phase1b_kafka_exporter.json",
        "cms_phase1c_system_postgres.json",
        "cms_pmax_forecast.json",
    } <= archived


def test_runtime_operations_dashboard_focuses_on_pipeline_and_server_health() -> None:
    dashboard = load_dashboard(RUNTIME_DASHBOARD_PATH)

    assert dashboard["uid"] == GRAFANA_LIVE_PIPELINE_DASHBOARD_UID
    assert dashboard["title"] == "CMS Runtime Operations"
    assert dashboard["refresh"] == "30s"
    titles = {panel["title"] for panel in dashboard["panels"]}
    assert {
        "Events last 5m",
        "Events last 1h",
        "Latest consumed age",
        "Active series last 15m",
        "Kafka consumer lag",
        "DLQ offset",
        "Kafka brokers",
        "PostgreSQL exporter up",
        "Latest retry",
        "Latest DLQ metric",
        "Latest duplicate",
        "Latest invariant delta",
        "Recent blocking issues",
        "Recent pipeline metrics",
        "CPU busy by node",
        "Memory available by node",
        "Filesystem free by node",
        "PostgreSQL deadlocks last 5m",
        "PostgreSQL active connections",
    } <= titles
    assert_panel_queries_are_read_only(dashboard)
    raw = json.dumps(dashboard)
    for token in [
        "live.measurement_event",
        "consumed_at",
        "ops.pipeline_metric",
        "qa.live_measurement_issue",
        "consumer_retry",
        "consumer_dlq",
        "consumer_duplicate",
        "invariant_delta",
        "kafka_consumergroup_lag_sum",
        "measurement_raw_v1",
        "measurement_dead_letter_v1",
        "node_cpu_seconds_total",
        "node_memory_MemAvailable_bytes",
        "node_filesystem_avail_bytes",
        "pg_up",
        "pg_stat_activity_count",
        "pg_stat_database_deadlocks",
    ]:
        assert token in raw


def test_test_gates_dashboard_focuses_on_runtime_evidence() -> None:
    dashboard = load_dashboard(TEST_GATES_DASHBOARD_PATH)

    assert dashboard["uid"] == "cms-test-gates"
    assert dashboard["title"] == "CMS Test Gates / Evidence"
    assert dashboard["refresh"] == "30s"
    titles = {panel["title"] for panel in dashboard["panels"]}
    assert {
        "Latest gate status",
        "Latest lag after",
        "Latest retry",
        "Latest DLQ",
        "Gate run summary",
        "Processed / inserted / duplicate / retry / DLQ invariant",
        "Recent gate metrics",
    } <= titles
    assert_panel_queries_are_read_only(dashboard)
    raw = json.dumps(dashboard)
    for token in [
        "ops.pipeline_metric",
        "live_soak_%",
        "t4%",
        "producer_accepted",
        "consumer_processed",
        "consumer_inserted",
        "consumer_duplicate",
        "consumer_retry",
        "consumer_dlq",
        "consumer_lag_after",
        "invariant_delta",
    ]:
        assert token in raw


def test_model_and_phase_dashboards_are_archived_not_active() -> None:
    active_text = "\n".join(path.read_text(encoding="utf-8") for path in DASHBOARD_DIR.glob("*.json"))

    assert "CMS P-Max Forecast" not in active_text
    assert "mart.pmax_forecast_15min" not in active_text
    assert "CMS Phase 1-A Status" not in active_text
    assert "CMS Phase 1-B Kafka Exporter" not in active_text
    assert "CMS Phase 1-C System and PostgreSQL" not in active_text


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


def test_prometheus_config_covers_stream_and_db_exporters() -> None:
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
