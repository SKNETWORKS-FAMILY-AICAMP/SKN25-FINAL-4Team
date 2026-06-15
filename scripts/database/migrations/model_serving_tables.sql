-- CMS model-serving table/view contract draft for the A-to-Z live/model-serving boundary.
-- REVIEW ONLY / DO NOT EXECUTE on AWS or production without an approved migration ticket,
-- backup plan, rollback plan, and an explicitly gated admin session.
-- Current AWS alignment (2026-06-11 audit): live.measurement_event, mart.peak_feature_15min,
-- mart.peak_input_15min legacy view/table, mart.pmax_forecast_15min,
-- ops.pmax_forecast_inference_log, and qa.pmax_forecast_evaluation are present.
-- Missing/target-only: mart.peak_training_frame_15min alias view, mart.anomaly_feature_1h,
-- mart.anomaly_warning_1h, ops.anomaly_warning_inference_log,
-- qa.anomaly_warning_evaluation, qa.model_serving_evidence_packet.
-- Canonical schemas are intentionally absent from this draft: no canonical writes and no
-- canonical read dependency are introduced by model-serving DDL.

CREATE SCHEMA IF NOT EXISTS mart;
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS qa;

-- Standard P-Max training/serving frame name.  The current AWS compatibility object is
-- mart.peak_input_15min; do not create a new *_input_* table.  The alias gives new code
-- a stable peak_training_frame name while legacy dashboards/scripts migrate.
CREATE OR REPLACE VIEW mart.peak_training_frame_15min AS
SELECT *
FROM mart.peak_input_15min;

-- Existing AWS mart.peak_feature_15min predates the live-observed provenance guard.
-- Add nullable provenance columns only; do not backfill old rows as live_observed.
-- Existing reference/corrected-origin rows must remain blocked from live serving until a
-- live/canonical-derived feature materialization worker writes explicit provenance.
ALTER TABLE IF EXISTS mart.peak_feature_15min
    ADD COLUMN IF NOT EXISTS source_layer TEXT,
    ADD COLUMN IF NOT EXISTS source_mode TEXT,
    ADD COLUMN IF NOT EXISTS provenance JSONB;

-- Current AWS-aligned P-Max output contract.  Values are serving forecasts, not canonical facts.
CREATE TABLE IF NOT EXISTS mart.pmax_forecast_15min (
    logical_meter TEXT NOT NULL,
    source_meter_urn TEXT NOT NULL,
    base_ts TIMESTAMPTZ NOT NULL,
    input_end_ts TIMESTAMPTZ NOT NULL,
    target_ts TIMESTAMPTZ NOT NULL,
    actual_window_ts TIMESTAMPTZ NOT NULL,
    horizon_minutes SMALLINT NOT NULL,
    predicted_p_max DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (logical_meter, base_ts, target_ts),
    CONSTRAINT pmax_forecast_horizon_check CHECK (horizon_minutes IN (15, 30, 45, 60)),
    CONSTRAINT pmax_forecast_prediction_nonnegative_check CHECK (predicted_p_max >= 0),
    CONSTRAINT forecast_input_end_ts_check CHECK (input_end_ts = base_ts - interval '15 minutes'),
    CONSTRAINT forecast_target_ts_check CHECK (target_ts = base_ts + make_interval(mins => horizon_minutes)),
    CONSTRAINT forecast_actual_window_ts_check CHECK (actual_window_ts = target_ts - interval '15 minutes'),
    CONSTRAINT pmax_forecast_logical_source_check CHECK (
        (logical_meter = 'V.Z81' AND source_meter_urn = 'V.Z81') OR
        (logical_meter = 'V.Z82' AND source_meter_urn = 'V.Z82') OR
        (logical_meter = 'H2.Z35x' AND source_meter_urn IN ('H2.Z35', 'H2.Z351')) OR
        (logical_meter = 'H2.Z36x' AND source_meter_urn IN ('H2.Z36', 'H2.Z361'))
    )
);

CREATE INDEX IF NOT EXISTS pmax_forecast_target_idx
    ON mart.pmax_forecast_15min (target_ts DESC, logical_meter);

CREATE TABLE IF NOT EXISTS ops.pmax_forecast_inference_log (
    run_id TEXT PRIMARY KEY,
    base_ts TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL,
    quality_status TEXT NOT NULL,
    logical_meter_count INTEGER NOT NULL,
    forecast_row_count INTEGER NOT NULL,
    replacement_row_count INTEGER NOT NULL DEFAULT 0,
    internal_missing_segment_count INTEGER NOT NULL DEFAULT 0,
    latest_missing_policy TEXT,
    error_reason TEXT,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT pmax_forecast_run_status_check CHECK (status IN ('success', 'degraded', 'failed')),
    CONSTRAINT pmax_forecast_quality_status_check CHECK (quality_status IN ('normal', 'degraded', 'failed')),
    CONSTRAINT pmax_forecast_counts_check CHECK (
        logical_meter_count >= 0
        AND forecast_row_count >= 0
        AND replacement_row_count >= 0
        AND internal_missing_segment_count >= 0
    ),
    CONSTRAINT pmax_forecast_log_time_check CHECK (completed_at IS NULL OR completed_at >= started_at)
);

CREATE TABLE IF NOT EXISTS qa.pmax_forecast_evaluation (
    evaluation_id BIGSERIAL PRIMARY KEY,
    logical_meter TEXT NOT NULL,
    source_meter_urn TEXT NOT NULL,
    base_ts TIMESTAMPTZ NOT NULL,
    target_ts TIMESTAMPTZ NOT NULL,
    actual_window_ts TIMESTAMPTZ NOT NULL,
    horizon_minutes SMALLINT NOT NULL,
    predicted_p_max DOUBLE PRECISION NOT NULL,
    actual_p_max DOUBLE PRECISION NOT NULL,
    absolute_error DOUBLE PRECISION NOT NULL,
    squared_error DOUBLE PRECISION NOT NULL,
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pmax_forecast_evaluation_horizon_check CHECK (horizon_minutes IN (15, 30, 45, 60)),
    CONSTRAINT pmax_forecast_evaluation_values_check CHECK (
        predicted_p_max >= 0 AND actual_p_max >= 0 AND absolute_error >= 0 AND squared_error >= 0
    ),
    CONSTRAINT pmax_forecast_evaluation_actual_window_ts_check CHECK (actual_window_ts = target_ts - interval '15 minutes')
);

-- Standard anomaly model-serving feature boundary.  Do not introduce new *_input_* DB objects.
CREATE TABLE IF NOT EXISTS mart.anomaly_feature_1h (
    bucket_ts TIMESTAMPTZ NOT NULL,
    meter_urn TEXT NOT NULL,
    feature_set TEXT NOT NULL CHECK (feature_set IN ('electric', 'heat')),
    p_value DOUBLE PRECISION,
    u1_value DOUBLE PRECISION,
    pf_value DOUBLE PRECISION,
    qv_value DOUBLE PRECISION,
    tdiff_value DOUBLE PRECISION,
    derived_features JSONB NOT NULL DEFAULT '{}'::jsonb,
    input_quality TEXT NOT NULL CHECK (input_quality IN ('good', 'warning', 'bad')),
    source_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (bucket_ts, meter_urn)
);

CREATE INDEX IF NOT EXISTS anomaly_feature_meter_window_idx
    ON mart.anomaly_feature_1h (meter_urn, bucket_ts);

CREATE TABLE IF NOT EXISTS mart.anomaly_warning_1h (
    warning_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    model_name TEXT NOT NULL DEFAULT 'anomaly_warning',
    model_version TEXT NOT NULL,
    release_version TEXT NOT NULL,
    meter_urn TEXT NOT NULL,
    model_urn TEXT NOT NULL,
    forecast_origin_ts TIMESTAMPTZ NOT NULL,
    target_ts TIMESTAMPTZ NOT NULL,
    lead_step INTEGER NOT NULL CHECK (lead_step IN (1, 2, 3)),
    horizon_hours INTEGER NOT NULL CHECK (horizon_hours = 3),
    predicted_p DOUBLE PRECISION,
    threshold_lower DOUBLE PRECISION,
    threshold_upper DOUBLE PRECISION,
    warning_flag BOOLEAN NOT NULL,
    warning_type TEXT NOT NULL CHECK (warning_type IN ('high', 'low', 'none')),
    status TEXT NOT NULL CHECK (status IN ('success', 'insufficient_data', 'no_artifact', 'error')),
    physical_flag BOOLEAN NOT NULL,
    input_quality TEXT NOT NULL CHECK (input_quality IN ('good', 'warning', 'bad')),
    warning_reason_code TEXT NOT NULL CHECK (
        warning_reason_code IN (
            'NO_PREDICTION',
            'KNOWN_METER_ISSUE',
            'INPUT_QUALITY_ISSUE',
            'HIGH_LOAD_VS_USUAL_HOUR',
            'LOW_LOAD_VS_USUAL_HOUR',
            'NONE'
        )
    ),
    source_input_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (target_ts = forecast_origin_ts + make_interval(hours => lead_step)),
    CHECK (threshold_lower IS NULL OR threshold_upper IS NULL OR threshold_lower <= threshold_upper),
    UNIQUE (meter_urn, forecast_origin_ts, lead_step)
);

CREATE INDEX IF NOT EXISTS anomaly_warning_target_idx
    ON mart.anomaly_warning_1h (target_ts DESC, meter_urn);

CREATE TABLE IF NOT EXISTS ops.anomaly_warning_inference_log (
    run_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    model_name TEXT NOT NULL DEFAULT 'anomaly_warning',
    model_version TEXT NOT NULL,
    release_version TEXT NOT NULL,
    forecast_origin_ts TIMESTAMPTZ NOT NULL,
    artifact_ref TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('success', 'degraded', 'failed')),
    meter_count INTEGER NOT NULL DEFAULT 0 CHECK (meter_count >= 0),
    prediction_count INTEGER NOT NULL DEFAULT 0 CHECK (prediction_count >= 0),
    warning_count INTEGER NOT NULL DEFAULT 0 CHECK (warning_count >= 0),
    blocked_reason TEXT,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);

CREATE TABLE IF NOT EXISTS qa.anomaly_warning_evaluation (
    evaluation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    warning_id TEXT NOT NULL,
    meter_urn TEXT NOT NULL,
    forecast_origin_ts TIMESTAMPTZ NOT NULL,
    target_ts TIMESTAMPTZ NOT NULL,
    lead_step INTEGER NOT NULL CHECK (lead_step IN (1, 2, 3)),
    metric_name TEXT NOT NULL,
    metric_value DOUBLE PRECISION NOT NULL,
    quality_status TEXT NOT NULL CHECK (quality_status IN ('pass', 'warn', 'fail')),
    evidence_ref TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (warning_id, metric_name)
);

CREATE TABLE IF NOT EXISTS qa.model_serving_evidence_packet (
    packet_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    base_ts TIMESTAMPTZ NOT NULL,
    forecast_origin_ts TIMESTAMPTZ NOT NULL,
    dry_run BOOLEAN NOT NULL DEFAULT true,
    writes_enabled BOOLEAN NOT NULL DEFAULT false,
    pmax_prediction_count INTEGER NOT NULL DEFAULT 0 CHECK (pmax_prediction_count >= 0),
    anomaly_prediction_count INTEGER NOT NULL DEFAULT 0 CHECK (anomaly_prediction_count >= 0),
    evidence JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
