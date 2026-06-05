from __future__ import annotations

from cms.data.db_scratch_guard import postgres_scratch_schema_name

MEASUREMENT_TABLE_RESOLUTIONS = {
    "measurement_1min": "1min",
    "measurement_5min": "5min",
    "measurement_15min": "15min",
    "measurement_1h": "1h",
}
LIVE_PIPELINE_TABLES = (
    "measurement_event",
    "bucket_queue",
    "peak_feature_15min",
    "peak_input_15min",
    "promotion_check",
)
SCRATCH_TABLES = ("measurement_event", *MEASUREMENT_TABLE_RESOLUTIONS.keys(), "bucket_queue", "peak_feature_15min", "peak_input_15min", "promotion_check", "latency_events", "qa_metrics")

REQUIRED_COMMON_COLUMNS = (
    "test_run_id",
    "lane",
    "resolution",
    "bucket_ts",
    "meter_urn",
    "measurement",
    "value",
    "quality_code",
    "mask_code",
    "evidence_level",
    "expected_points",
    "observed_points",
    "gap_points",
    "coverage_ratio",
    "source_native_interval_seconds",
    "cadence_policy_id",
    "target_resolution",
    "expected_points_policy",
    "aggregation_policy",
    "quality_summary",
    "source_event_ids",
    "timestamp_policy_ids",
    "source_timezones",
    "source_ts_columns",
    "source_ts_raw_samples",
    "timestamp_quality_summary",
    "timestamp_origin_rules",
    "lineage_key",
    "created_at",
)

LATENCY_MARKERS = (
    "source_event_ts",
    "fastapi_received_at",
    "kafka_ack_at",
    "kafka_consumed_at",
    "event_committed_at",
    "eq_1min_done_at",
    "queue_enqueued_at",
    "pg_15min_committed_at",
    "pg_1h_committed_at",
    "peak_feature_done_at",
    "peak_input_done_at",
    "qa_done_at",
)

_LATENCY_SECONDS_COLUMNS = (
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


def render_scratch_ddl(test_run_id: str) -> str:
    """Return side-effect-free PostgreSQL scratch DDL for a validated test run.

    The schema name is delegated to db_scratch_guard so unsafe test_run_id values
    are rejected before any SQL text is returned.
    """
    schema = postgres_scratch_schema_name(test_run_id)
    parts = [
        "-- CMS live equalization scratch DDL contract.",
        "-- Review and execute manually only after scratch DB write approval.",
        "-- This generator does not connect to a database or perform writes.",
        f"CREATE SCHEMA IF NOT EXISTS {schema};",
    ]
    parts.extend(_render_measurement_table(schema, table, resolution, test_run_id) for table, resolution in MEASUREMENT_TABLE_RESOLUTIONS.items())
    parts.extend(_render_live_pipeline_table(schema, table, test_run_id) for table in LIVE_PIPELINE_TABLES)
    parts.append(_render_latency_events_table(schema, test_run_id))
    parts.append(_render_qa_metrics_table(schema, test_run_id))
    return "\n\n".join(parts) + "\n"


def render_scratch_cleanup_sql(test_run_id: str) -> str:
    """Return scratch-only cleanup SQL for the guard-generated schema."""
    schema = postgres_scratch_schema_name(test_run_id)
    return f"DROP SCHEMA IF EXISTS {schema} CASCADE;"


def _render_measurement_table(schema: str, table: str, resolution: str, test_run_id: str) -> str:
    return f"""CREATE TABLE IF NOT EXISTS {schema}.{table} (
    {_common_columns_sql(test_run_id)},
    CONSTRAINT ck_{table}_test_run_id CHECK (test_run_id = '{test_run_id}'),
    CONSTRAINT ck_{table}_resolution CHECK (resolution = '{resolution}'),
    CONSTRAINT ck_{table}_coverage_ratio CHECK (coverage_ratio >= 0.0 AND coverage_ratio <= 1.0),
    PRIMARY KEY (test_run_id, lane, resolution, bucket_ts, meter_urn, measurement, lineage_key)
);

CREATE INDEX IF NOT EXISTS idx_{table}_bucket
ON {schema}.{table} (bucket_ts, lane, meter_urn, measurement);

CREATE INDEX IF NOT EXISTS idx_{table}_lineage
ON {schema}.{table} (lineage_key);"""


def _render_live_pipeline_table(schema: str, table: str, test_run_id: str) -> str:
    return f"""CREATE TABLE IF NOT EXISTS {schema}.{table} (
    scratch_row_id BIGSERIAL PRIMARY KEY,
    test_run_id TEXT NOT NULL DEFAULT '{test_run_id}',
    bucket_ts TIMESTAMPTZ,
    meter_urn TEXT,
    measurement TEXT,
    payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_{table}_test_run_id CHECK (test_run_id = '{test_run_id}')
);

CREATE INDEX IF NOT EXISTS idx_{table}_scratch_lookup
ON {schema}.{table} (test_run_id, bucket_ts, meter_urn, measurement);"""


def _render_latency_events_table(schema: str, test_run_id: str) -> str:
    marker_columns = ",\n    ".join(f"{marker} TIMESTAMPTZ" for marker in LATENCY_MARKERS)
    latency_columns = ",\n    ".join(f"{column} DOUBLE PRECISION" for column in _LATENCY_SECONDS_COLUMNS)
    return f"""CREATE TABLE IF NOT EXISTS {schema}.latency_events (
    latency_event_id BIGSERIAL PRIMARY KEY,
    {_common_columns_sql(test_run_id)},
    stage TEXT NOT NULL CHECK (stage IN ('ingest', 'kafka_ack', 'kafka_to_event', 'eq_1min', 'eq_5min', 'pg_15min', 'pg_1h', 'peak_feature', 'peak_input', 'qa')),
    {marker_columns},
    {latency_columns},
    CONSTRAINT ck_latency_events_test_run_id CHECK (test_run_id = '{test_run_id}'),
    CONSTRAINT ck_latency_events_coverage_ratio CHECK (coverage_ratio >= 0.0 AND coverage_ratio <= 1.0),
    UNIQUE (test_run_id, lane, resolution, bucket_ts, meter_urn, measurement, lineage_key, stage)
);

CREATE INDEX IF NOT EXISTS idx_latency_events_bucket
ON {schema}.latency_events (bucket_ts, lane, meter_urn, measurement);

CREATE INDEX IF NOT EXISTS idx_latency_events_stage
ON {schema}.latency_events (stage, created_at);"""


def _render_qa_metrics_table(schema: str, test_run_id: str) -> str:
    return f"""CREATE TABLE IF NOT EXISTS {schema}.qa_metrics (
    qa_metric_id BIGSERIAL PRIMARY KEY,
    {_common_columns_sql(test_run_id)},
    metric_name TEXT NOT NULL,
    metric_unit TEXT,
    metric_scope TEXT NOT NULL DEFAULT 'bucket',
    status TEXT NOT NULL DEFAULT 'observed' CHECK (status IN ('observed', 'passed', 'warning', 'failed')),
    details JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    CONSTRAINT ck_qa_metrics_test_run_id CHECK (test_run_id = '{test_run_id}'),
    CONSTRAINT ck_qa_metrics_coverage_ratio CHECK (coverage_ratio >= 0.0 AND coverage_ratio <= 1.0),
    UNIQUE (test_run_id, lane, resolution, bucket_ts, meter_urn, measurement, metric_name, lineage_key)
);

CREATE INDEX IF NOT EXISTS idx_qa_metrics_bucket
ON {schema}.qa_metrics (bucket_ts, lane, meter_urn, measurement);

CREATE INDEX IF NOT EXISTS idx_qa_metrics_name
ON {schema}.qa_metrics (metric_name, status, created_at);"""


def _common_columns_sql(test_run_id: str) -> str:
    return f"""test_run_id TEXT NOT NULL DEFAULT '{test_run_id}',
    lane TEXT NOT NULL,
    resolution TEXT NOT NULL,
    bucket_ts TIMESTAMPTZ NOT NULL,
    meter_urn TEXT NOT NULL,
    measurement TEXT NOT NULL,
    value DOUBLE PRECISION,
    quality_code TEXT NOT NULL,
    mask_code TEXT,
    evidence_level TEXT NOT NULL,
    expected_points INTEGER NOT NULL,
    observed_points INTEGER NOT NULL,
    gap_points INTEGER NOT NULL,
    coverage_ratio DOUBLE PRECISION NOT NULL,
    source_native_interval_seconds INTEGER,
    cadence_policy_id TEXT,
    target_resolution TEXT NOT NULL,
    expected_points_policy TEXT NOT NULL,
    aggregation_policy TEXT NOT NULL,
    quality_summary JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    source_event_ids TEXT[] NOT NULL DEFAULT '{{}}'::text[],
    timestamp_policy_ids TEXT[] NOT NULL DEFAULT '{{}}'::text[],
    source_timezones TEXT[] NOT NULL DEFAULT '{{}}'::text[],
    source_ts_columns TEXT[] NOT NULL DEFAULT '{{}}'::text[],
    source_ts_raw_samples TEXT[] NOT NULL DEFAULT '{{}}'::text[],
    timestamp_quality_summary JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    timestamp_origin_rules TEXT[] NOT NULL DEFAULT '{{}}'::text[],
    lineage_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()"""


__all__ = [
    "LATENCY_MARKERS",
    "LIVE_PIPELINE_TABLES",
    "MEASUREMENT_TABLE_RESOLUTIONS",
    "REQUIRED_COMMON_COLUMNS",
    "SCRATCH_TABLES",
    "render_scratch_cleanup_sql",
    "render_scratch_ddl",
]
