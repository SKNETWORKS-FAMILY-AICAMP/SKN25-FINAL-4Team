-- CMS cadence report table storage contract candidate.
-- Gate scope: keep existing ops.daily_report / ops.weekly_report / ops.monthly_report as the report store.
-- Approval boundary: candidate DDL only. Do not execute on AWS or any deployed database before Viowlet approval.
-- Runtime boundary: no Airflow trigger, container restart, or server file modification is implied by this file.

-- Rationale:
-- ops.report_document is not required for the current CMS report flow. The simpler
-- contract is to preserve the existing cadence-specific tables and add the fields
-- needed by the user-facing JSON/Markdown report.

CREATE TABLE IF NOT EXISTS ops.weekly_report (
    period TEXT PRIMARY KEY,
    period_start DATE,
    period_end DATE,
    total_consumption_kwh DOUBLE PRECISION,
    self_sufficiency_pct DOUBLE PRECISION,
    avg_cop DOUBLE PRECISION,
    anomaly_count INTEGER,
    grid_dependency_pct DOUBLE PRECISION,
    pv_kwh DOUBLE PRECISION,
    chp_kwh DOUBLE PRECISION,
    peak_kw DOUBLE PRECISION,
    title TEXT,
    executive_summary TEXT,
    markdown TEXT,
    report_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    operator_actions JSONB NOT NULL DEFAULT '[]'::jsonb,
    chart_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    anomaly_rows JSONB NOT NULL DEFAULT '[]'::jsonb,
    summary TEXT,
    pmax_commentary TEXT,
    anomaly_commentary TEXT,
    limitations JSONB NOT NULL DEFAULT '[]'::jsonb,
    context_pack JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_refs JSONB NOT NULL DEFAULT '{}'::jsonb,
    guard_result JSONB NOT NULL DEFAULT '{}'::jsonb,
    generation_mode TEXT NOT NULL DEFAULT 'deterministic_fallback',
    idempotency_key TEXT,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT weekly_report_period_bounds_check CHECK (period_start IS NULL OR period_end IS NULL OR period_start < period_end),
    CONSTRAINT weekly_report_generation_mode_check CHECK (
        generation_mode IN ('sllm', 'api_fallback', 'deterministic_fallback')
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS weekly_report_idempotency_key_idx
    ON ops.weekly_report (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

ALTER TABLE IF EXISTS ops.weekly_report
    ADD COLUMN IF NOT EXISTS title TEXT,
    ADD COLUMN IF NOT EXISTS executive_summary TEXT,
    ADD COLUMN IF NOT EXISTS markdown TEXT,
    ADD COLUMN IF NOT EXISTS report_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS operator_actions JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS chart_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS anomaly_rows JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS summary TEXT,
    ADD COLUMN IF NOT EXISTS pmax_commentary TEXT,
    ADD COLUMN IF NOT EXISTS anomaly_commentary TEXT,
    ADD COLUMN IF NOT EXISTS limitations JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS context_pack JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS source_refs JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS guard_result JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS generation_mode TEXT NOT NULL DEFAULT 'deterministic_fallback',
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT,
    ADD COLUMN IF NOT EXISTS generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

ALTER TABLE IF EXISTS ops.daily_report
    ADD COLUMN IF NOT EXISTS title TEXT,
    ADD COLUMN IF NOT EXISTS executive_summary TEXT,
    ADD COLUMN IF NOT EXISTS markdown TEXT,
    ADD COLUMN IF NOT EXISTS report_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS operator_actions JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS chart_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS anomaly_rows JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS summary TEXT,
    ADD COLUMN IF NOT EXISTS pmax_commentary TEXT,
    ADD COLUMN IF NOT EXISTS anomaly_commentary TEXT,
    ADD COLUMN IF NOT EXISTS limitations JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS context_pack JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS source_refs JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS guard_result JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS generation_mode TEXT NOT NULL DEFAULT 'deterministic_fallback',
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT,
    ADD COLUMN IF NOT EXISTS generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE UNIQUE INDEX IF NOT EXISTS daily_report_idempotency_key_idx
    ON ops.daily_report (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

ALTER TABLE IF EXISTS ops.monthly_report
    ADD COLUMN IF NOT EXISTS title TEXT,
    ADD COLUMN IF NOT EXISTS executive_summary TEXT,
    ADD COLUMN IF NOT EXISTS markdown TEXT,
    ADD COLUMN IF NOT EXISTS report_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS operator_actions JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS chart_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS anomaly_rows JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS summary TEXT,
    ADD COLUMN IF NOT EXISTS pmax_commentary TEXT,
    ADD COLUMN IF NOT EXISTS anomaly_commentary TEXT,
    ADD COLUMN IF NOT EXISTS limitations JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS context_pack JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS source_refs JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS guard_result JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS generation_mode TEXT NOT NULL DEFAULT 'deterministic_fallback',
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT,
    ADD COLUMN IF NOT EXISTS generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE UNIQUE INDEX IF NOT EXISTS monthly_report_idempotency_key_idx
    ON ops.monthly_report (idempotency_key)
    WHERE idempotency_key IS NOT NULL;
