-- Phase 1-A Grafana metric support table.
-- Safe support DDL: creates ops schema/table only; no canonical/reference writes.

CREATE SCHEMA IF NOT EXISTS ops;

CREATE TABLE IF NOT EXISTS ops.pipeline_metric (
    metric_id BIGSERIAL PRIMARY KEY,
    metric_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    run_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value DOUBLE PRECISION NOT NULL,
    unit TEXT NOT NULL DEFAULT 'count',
    labels JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pipeline_metric_value_nonnegative_check CHECK (metric_value >= 0)
);

CREATE INDEX IF NOT EXISTS idx_pipeline_metric_ts
    ON ops.pipeline_metric (metric_ts DESC);

CREATE INDEX IF NOT EXISTS idx_pipeline_metric_run_stage_name
    ON ops.pipeline_metric (run_id, stage, metric_name);

CREATE INDEX IF NOT EXISTS idx_pipeline_metric_name_ts
    ON ops.pipeline_metric (metric_name, metric_ts DESC);
