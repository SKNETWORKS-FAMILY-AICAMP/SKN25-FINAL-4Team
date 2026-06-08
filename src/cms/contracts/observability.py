"""Import-safe observability contracts for live pipeline monitoring.

This module defines Grafana/dashboard/alert contract constants only. It does not
connect to Grafana, PostgreSQL, Discord, Kafka, FastAPI, or any cloud service.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from cms.contracts.pmax_forecast_15min import (
    PMAX_FORECAST_EVALUATION_TABLE,
    PMAX_FORECAST_INFERENCE_LOG_TABLE,
    PMAX_FORECAST_TABLE,
)

EvidenceLevel = Literal["local_dry_run", "mocked_adapter", "scratch_db_integration", "aws_scratch_or_staging", "production_promotion"]
AlertSeverity = Literal["P0", "P1", "P2"]

GRAFANA_POSTGRES_DATASOURCE_UID = "postgres-cms-live"
GRAFANA_LIVE_PIPELINE_DASHBOARD_UID = "cms-runtime-operations"
GRAFANA_LIVE_PIPELINE_FOLDER = "CMS Operations"

LATENCY_METRICS = (
    "source_to_fastapi_sec",
    "fastapi_to_kafka_sec",
    "kafka_to_event_sec",
    "event_to_1min_sec",
    "event_to_queue_sec",
    "one_min_to_15min_sec",
    "one_min_to_1h_sec",
    "one_min_to_peak_feature_sec",
    "peak_feature_to_peak_input_sec",
    "qa_eligibility_sec",
    "promotion_ready_sec",
    "end_to_end_sec",
)
KAFKA_METRICS = (
    "kafka_consumer_lag",
    "kafka_dlq_count",
    "kafka_produce_error_count",
    "kafka_to_event_p95_sec",
)
FASTAPI_METRICS = (
    "fastapi_ingest_request_rate",
    "fastapi_ingest_4xx_count",
    "fastapi_ingest_5xx_count",
    "fastapi_ingest_p95_ms",
)

QUEUE_STATUS_VALUES = ("pending", "running", "done", "failed", "blocked")
ISSUE_KINDS = (
    "policy_miss",
    "policy_ambiguous",
    "policy_disabled",
    "coverage_below_threshold",
    "coverage_out_of_bounds",
    "lineage_missing",
    "peak_leakage_block",
    "timestamp_invalid",
    "duplicate_event",
    "kafka_validation_error",
    "kafka_dlq_publish_error",
)
WORKER_NAMES = (
    "kafka_to_postgres_consumer",
    "mean_rollup_worker",
    "peak_feature_worker",
    "qa_eligibility_worker",
    "promotion_worker",
    "report_worker",
)

LIVE_OPS_TABLES = (
    "ops.worker_heartbeat",
    "ops.pipeline_latency_event",
    "ops.pipeline_metric",
    "ops.kafka_consumer_lag",
    "ops.fastapi_ingest_metric",
    "live.measurement_event",
    "live.measurement_policy",
    "live.bucket_queue",
    "qa.live_measurement_issue",
    "live.promotion_check",
    "live.promotion_run",
)

CHAMPION_MODEL_DASHBOARD_UID = "cms-champion-model"
CHAMPION_MODEL_DASHBOARD_FOLDER = "CMS Champion Model"
CHAMPION_MODEL_EXTERNAL_ALERT_SENDING_ENABLED = False
CHAMPION_MODEL_SOURCE_TABLES = (
    "mart.champion_model_input_1h",
    "live.measurement_1h",
    "mart.champion_prediction_1h",
    "qa.champion_prediction_issue",
    "ops.champion_inference_metric",
    PMAX_FORECAST_TABLE,
    PMAX_FORECAST_INFERENCE_LOG_TABLE,
    PMAX_FORECAST_EVALUATION_TABLE,
)

GRAFANA_ALERT_RULES = (
    "no_live_events_5m",
    "oldest_pending_queue_age_10m",
    "queue_failed_rows_present",
    "worker_heartbeat_missing_2m",
    "end_to_end_p95_300s",
    "kafka_consumer_lag_10k",
    "kafka_dlq_count_nonzero",
    "kafka_produce_error_count_nonzero",
    "fastapi_ingest_5xx_spike_5m",
    "policy_miss_spike_10m",
    "peak_branch_lag_gt_mean_branch",
    "non_blocking_qa_warning_spike_10m",
)


@dataclass(frozen=True)
class AlertRuleContract:
    name: str
    severity: AlertSeverity
    metric: str
    threshold: float
    window: str
    cooldown: str
    description: str

    def __post_init__(self) -> None:
        if self.threshold < 0:
            raise ValueError("alert threshold must be non-negative")
        if self.cooldown == "":
            raise ValueError("alert cooldown is required")


ALERT_RULE_CONTRACTS = (
    AlertRuleContract("no_live_events_5m", "P0", "events_last_5min", 0, "5m", "5m", "No accepted live measurement events in the last 5 minutes."),
    AlertRuleContract("oldest_pending_queue_age_10m", "P0", "oldest_pending_age_sec", 600, "2m", "5m", "Oldest pending bucket_queue job is older than 10 minutes."),
    AlertRuleContract("queue_failed_rows_present", "P0", "queue_failed_count", 0, "3m", "5m", "One or more failed queue rows exist."),
    AlertRuleContract("worker_heartbeat_missing_2m", "P0", "worker_heartbeat_age_sec", 120, "2m", "5m", "A live pipeline worker has not heartbeated within 2 minutes."),
    AlertRuleContract("end_to_end_p95_300s", "P0", "end_to_end_p95_sec", 300, "10m", "10m", "End-to-end latency p95 exceeds 300 seconds."),
    AlertRuleContract("kafka_consumer_lag_10k", "P0", "kafka_consumer_lag", 10_000, "5m", "5m", "Kafka consumer lag exceeded the Phase 1 threshold."),
    AlertRuleContract("kafka_dlq_count_nonzero", "P0", "kafka_dlq_count", 0, "5m", "5m", "Measurement DLQ received one or more poison messages."),
    AlertRuleContract("kafka_produce_error_count_nonzero", "P0", "kafka_produce_error_count", 0, "5m", "5m", "FastAPI Kafka produce errors are present."),
    AlertRuleContract("fastapi_ingest_5xx_spike_5m", "P0", "fastapi_ingest_5xx_count", 0, "5m", "5m", "FastAPI ingest 5xx responses are present."),
    AlertRuleContract("policy_miss_spike_10m", "P1", "policy_miss_count_10m", 10, "10m", "15m", "Policy miss count exceeded the agreed live-run threshold."),
    AlertRuleContract("peak_branch_lag_gt_mean_branch", "P1", "peak_branch_lag_sec", 300, "10m", "15m", "Peak feature branch lags the mean rollup branch."),
    AlertRuleContract("non_blocking_qa_warning_spike_10m", "P2", "qa_warning_count_10m", 30, "10m", "30m", "Non-blocking QA warning count exceeded the live-run review threshold."),
)


@dataclass(frozen=True)
class DashboardPanelContract:
    title: str
    source_table: str
    query_purpose: str
    severity: AlertSeverity | None = None

    def __post_init__(self) -> None:
        if not _is_supported_dashboard_source_table(self.source_table):
            raise ValueError(f"unsupported dashboard source table: {self.source_table}")


DASHBOARD_SOURCE_SCHEMAS = ("live", "qa", "mart", "ops")


def _is_safe_unquoted_identifier(value: str) -> bool:
    return bool(value) and (value[0].isalpha() or value[0] == "_") and all(char.isalnum() or char == "_" for char in value)


def _is_supported_dashboard_source_table(source_table: str) -> bool:
    parts = source_table.split(".")
    if len(parts) != 2:
        return False
    schema, table = parts
    return schema in DASHBOARD_SOURCE_SCHEMAS and _is_safe_unquoted_identifier(schema) and _is_safe_unquoted_identifier(table)


DASHBOARD_PANEL_CONTRACTS = (
    DashboardPanelContract("Events last 5 minutes", "live.measurement_event", "live source freshness", "P0"),
    DashboardPanelContract("FastAPI ingest health", "ops.fastapi_ingest_metric", "ingest request and error rate", "P0"),
    DashboardPanelContract("Kafka consumer lag", "ops.kafka_consumer_lag", "consumer group lag", "P0"),
    DashboardPanelContract("Kafka DLQ and produce errors", "ops.pipeline_latency_event", "DLQ and produce error counters", "P0"),
    DashboardPanelContract("Queue by status/job/resolution", "live.bucket_queue", "queue backlog", "P0"),
    DashboardPanelContract("Oldest pending queue age", "live.bucket_queue", "queue stuck detection", "P0"),
    DashboardPanelContract("Recent blocking issues", "qa.live_measurement_issue", "issue triage", "P1"),
    DashboardPanelContract("Latency p95 by stage", "ops.pipeline_latency_event", "stage latency", "P0"),
    DashboardPanelContract("Worker heartbeat", "ops.worker_heartbeat", "worker liveness", "P0"),
    DashboardPanelContract("Promotion readiness", "live.promotion_check", "approval-gated promotion state", None),
    DashboardPanelContract("Active meters last 5 minutes", "live.measurement_event", "bounded active meter count", "P1"),
    DashboardPanelContract("Active series last 5 minutes", "live.measurement_event", "bounded active meter-measurement count", "P1"),
    DashboardPanelContract("Meter collection by series", "live.measurement_event", "per-series collection volume and freshness", "P1"),
    DashboardPanelContract("Stale meter series", "live.measurement_event", "stale series freshness triage", "P1"),
    DashboardPanelContract("Policy status by series", "live.measurement_policy", "policy lookup and active policy visibility", "P1"),
    DashboardPanelContract("Consumer invariant", "ops.pipeline_metric", "processed/inserted/duplicate/retry/DLQ reconciliation", "P1"),
)

CHAMPION_MODEL_DASHBOARD_PANEL_CONTRACTS = (
    DashboardPanelContract("Model input readiness", "mart.champion_model_input_1h", "champion model input materialization and validation state", "P0"),
    DashboardPanelContract("168h history coverage", "live.measurement_1h", "rolling 168h observed history coverage before inference", "P0"),
    DashboardPanelContract("Prediction freshness", "mart.champion_prediction_1h", "latest champion prediction write age", "P0"),
    DashboardPanelContract("Warning by horizon", "mart.champion_prediction_1h", "pre-warning count grouped by forecast horizon", "P1"),
    DashboardPanelContract("Post-hoc anomaly/error", "qa.champion_prediction_issue", "actual-vs-predicted anomaly and error triage", "P1"),
    DashboardPanelContract("Champion inference latency", "ops.champion_inference_metric", "inference adapter latency and runtime health", "P0"),
    DashboardPanelContract("P-Max prediction freshness", PMAX_FORECAST_TABLE, "latest 15min P-Max forecast write age", "P0"),
    DashboardPanelContract("P-Max quality status", PMAX_FORECAST_INFERENCE_LOG_TABLE, "success/degraded/failed run quality state", "P1"),
    DashboardPanelContract("P-Max evaluation error", PMAX_FORECAST_EVALUATION_TABLE, "post-hoc actual P_max forecast error", "P1"),
)


__all__ = [
    "ALERT_RULE_CONTRACTS",
    "CHAMPION_MODEL_DASHBOARD_FOLDER",
    "CHAMPION_MODEL_DASHBOARD_PANEL_CONTRACTS",
    "CHAMPION_MODEL_DASHBOARD_UID",
    "CHAMPION_MODEL_EXTERNAL_ALERT_SENDING_ENABLED",
    "CHAMPION_MODEL_SOURCE_TABLES",
    "DASHBOARD_PANEL_CONTRACTS",
    "DASHBOARD_SOURCE_SCHEMAS",
    "FASTAPI_METRICS",
    "GRAFANA_ALERT_RULES",
    "GRAFANA_LIVE_PIPELINE_DASHBOARD_UID",
    "GRAFANA_LIVE_PIPELINE_FOLDER",
    "GRAFANA_POSTGRES_DATASOURCE_UID",
    "ISSUE_KINDS",
    "KAFKA_METRICS",
    "LATENCY_METRICS",
    "LIVE_OPS_TABLES",
    "QUEUE_STATUS_VALUES",
    "WORKER_NAMES",
    "AlertRuleContract",
    "DashboardPanelContract",
]
