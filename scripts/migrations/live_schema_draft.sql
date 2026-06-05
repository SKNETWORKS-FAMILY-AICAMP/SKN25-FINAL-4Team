-- Review-only PostgreSQL draft for the live measurement pipeline.
-- Do not run this file against production, AWS, or canonical schemas without explicit approval.
-- This draft does not grant canonical write permission.
-- Scope: live/qa/mart operational objects, common trigger boundary, and scratch-safe review.

CREATE SCHEMA IF NOT EXISTS live;
CREATE SCHEMA IF NOT EXISTS qa;
CREATE SCHEMA IF NOT EXISTS mart;
CREATE SCHEMA IF NOT EXISTS ops;

CREATE TABLE IF NOT EXISTS live.measurement_policy (
    policy_id BIGSERIAL PRIMARY KEY,
    meter_urn TEXT NOT NULL,
    measurement TEXT NOT NULL,
    effective_from TIMESTAMPTZ NOT NULL,
    effective_to TIMESTAMPTZ,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    source_update_mode TEXT NOT NULL,
    cadence_group TEXT NOT NULL,
    source_native_interval_seconds INTEGER,
    target_resolution_policy TEXT NOT NULL,
    value_policy TEXT NOT NULL,
    aggregation_policy TEXT NOT NULL,
    expected_points_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
    mean_rollup_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    peak_feature_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    coverage_threshold NUMERIC(6,5),
    max_state_hold_age_seconds INTEGER,
    canonical_eligible BOOLEAN NOT NULL DEFAULT FALSE,
    paper_policy_ref TEXT,
    policy_version INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT measurement_policy_effective_range_check
        CHECK (effective_to IS NULL OR effective_to > effective_from),
    CONSTRAINT measurement_policy_native_interval_check
        CHECK (source_native_interval_seconds IS NULL OR source_native_interval_seconds > 0),
    CONSTRAINT measurement_policy_coverage_threshold_check
        CHECK (coverage_threshold IS NULL OR coverage_threshold BETWEEN 0 AND 1),
    CONSTRAINT measurement_policy_version_check
        CHECK (policy_version > 0),
    CONSTRAINT measurement_policy_update_mode_check
        CHECK (source_update_mode IN ('periodic_sample', 'change_of_value_state', 'unknown')),
    CONSTRAINT measurement_policy_cadence_group_check
        CHECK (cadence_group IN ('native_1min', 'native_subminute', 'cov_state', 'native_5min_or_sparse', 'native_15min', 'native_1h', 'unknown_or_mismatch'))
);

CREATE UNIQUE INDEX IF NOT EXISTS measurement_policy_version_uq
    ON live.measurement_policy (meter_urn, measurement, effective_from, policy_version);

CREATE INDEX IF NOT EXISTS measurement_policy_lookup_idx
    ON live.measurement_policy (meter_urn, measurement, enabled, effective_from, effective_to);

CREATE TABLE IF NOT EXISTS live.measurement_event (
    event_id TEXT PRIMARY KEY,
    business_idempotency_key TEXT NOT NULL,
    source_event_id TEXT,
    meter_urn TEXT NOT NULL,
    measurement TEXT NOT NULL,
    event_ts TIMESTAMPTZ NOT NULL,
    value_text TEXT,
    value_numeric DOUBLE PRECISION,
    unit TEXT,
    source_layer TEXT NOT NULL DEFAULT 'kafka.measurement_raw_v1',
    source_ref TEXT,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    received_at TIMESTAMPTZ,
    raw_payload_hash TEXT,
    kafka_topic TEXT,
    kafka_partition INTEGER,
    kafka_offset BIGINT,
    kafka_key TEXT,
    consumer_group TEXT,
    consumed_at TIMESTAMPTZ,
    schema_version TEXT,
    policy_lookup_status TEXT NOT NULL DEFAULT 'pending',
    CONSTRAINT measurement_event_policy_status_check
        CHECK (policy_lookup_status IN ('pending', 'resolved', 'policy_miss', 'policy_ambiguous', 'policy_disabled', 'policy_block'))
);

CREATE UNIQUE INDEX IF NOT EXISTS measurement_event_business_idempotency_uq
    ON live.measurement_event (source_layer, source_event_id)
    WHERE source_event_id IS NOT NULL;

-- Transport/progress dedup only; business idempotency remains event_id/business_idempotency_key based.
CREATE UNIQUE INDEX IF NOT EXISTS measurement_event_kafka_offset_uq
    ON live.measurement_event (kafka_topic, kafka_partition, kafka_offset)
    WHERE kafka_topic IS NOT NULL AND kafka_partition IS NOT NULL AND kafka_offset IS NOT NULL;

CREATE INDEX IF NOT EXISTS measurement_event_lookup_idx
    ON live.measurement_event (meter_urn, measurement, event_ts);

CREATE INDEX IF NOT EXISTS measurement_event_policy_status_idx
    ON live.measurement_event (policy_lookup_status, event_ts);

CREATE TABLE IF NOT EXISTS live.measurement_1min (
    bucket_ts TIMESTAMPTZ NOT NULL,
    resolution TEXT NOT NULL DEFAULT '1min',
    meter_urn TEXT NOT NULL,
    measurement TEXT NOT NULL,
    value DOUBLE PRECISION,
    unit TEXT,
    aggregation_policy TEXT NOT NULL,
    expected_points INTEGER NOT NULL,
    observed_points INTEGER NOT NULL,
    gap_points INTEGER NOT NULL,
    coverage_ratio NUMERIC(8,6) NOT NULL,
    mask_code TEXT,
    quality_code TEXT,
    quality_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_event_ids TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    source_bucket_refs TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    source_run_id TEXT,
    policy_id BIGINT REFERENCES live.measurement_policy(policy_id),
    policy_version INTEGER NOT NULL,
    lineage_key TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (meter_urn, measurement, resolution, bucket_ts, policy_version),
    CONSTRAINT measurement_1min_resolution_check CHECK (resolution = '1min'),
    CONSTRAINT measurement_1min_counts_check CHECK (expected_points >= 0 AND observed_points >= 0 AND gap_points >= 0),
    CONSTRAINT measurement_1min_coverage_check CHECK (coverage_ratio BETWEEN 0 AND 1)
);

CREATE TABLE IF NOT EXISTS live.measurement_15min (
    LIKE live.measurement_1min EXCLUDING DEFAULTS EXCLUDING CONSTRAINTS EXCLUDING INDEXES,
    PRIMARY KEY (meter_urn, measurement, resolution, bucket_ts, policy_version),
    CONSTRAINT measurement_15min_resolution_check CHECK (resolution = '15min'),
    CONSTRAINT measurement_15min_counts_check CHECK (expected_points >= 0 AND observed_points >= 0 AND gap_points >= 0),
    CONSTRAINT measurement_15min_coverage_check CHECK (coverage_ratio BETWEEN 0 AND 1)
);

CREATE TABLE IF NOT EXISTS live.measurement_1h (
    LIKE live.measurement_1min EXCLUDING DEFAULTS EXCLUDING CONSTRAINTS EXCLUDING INDEXES,
    PRIMARY KEY (meter_urn, measurement, resolution, bucket_ts, policy_version),
    CONSTRAINT measurement_1h_resolution_check CHECK (resolution = '1h'),
    CONSTRAINT measurement_1h_counts_check CHECK (expected_points >= 0 AND observed_points >= 0 AND gap_points >= 0),
    CONSTRAINT measurement_1h_coverage_check CHECK (coverage_ratio BETWEEN 0 AND 1)
);

CREATE INDEX IF NOT EXISTS measurement_15min_window_idx
    ON live.measurement_15min (meter_urn, measurement, bucket_ts);

CREATE INDEX IF NOT EXISTS measurement_1h_window_idx
    ON live.measurement_1h (meter_urn, measurement, bucket_ts);

CREATE TABLE IF NOT EXISTS live.bucket_queue (
    queue_id BIGSERIAL PRIMARY KEY,
    meter_urn TEXT NOT NULL,
    measurement TEXT NOT NULL,
    resolution TEXT NOT NULL,
    bucket_ts TIMESTAMPTZ NOT NULL,
    job_kind TEXT NOT NULL,
    policy_id BIGINT REFERENCES live.measurement_policy(policy_id),
    policy_version INTEGER NOT NULL,
    source_min_ts TIMESTAMPTZ,
    source_max_ts TIMESTAMPTZ,
    watermark_ts TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    locked_by TEXT,
    locked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT bucket_queue_resolution_check CHECK (resolution IN ('15min', '1h')),
    CONSTRAINT bucket_queue_job_kind_check CHECK (job_kind IN ('mean_rollup', 'peak_feature')),
    CONSTRAINT bucket_queue_status_check CHECK (status IN ('pending', 'running', 'done', 'failed', 'blocked')),
    CONSTRAINT bucket_queue_attempt_count_check CHECK (attempt_count >= 0),
    CONSTRAINT bucket_queue_source_range_check CHECK (source_max_ts IS NULL OR source_min_ts IS NULL OR source_max_ts >= source_min_ts),
    CONSTRAINT bucket_queue_job_resolution_check CHECK (
        (job_kind = 'mean_rollup' AND resolution IN ('15min', '1h')) OR
        (job_kind = 'peak_feature' AND resolution = '15min')
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS bucket_queue_idempotency_uq
    ON live.bucket_queue (meter_urn, measurement, resolution, bucket_ts, job_kind, policy_version);

CREATE INDEX IF NOT EXISTS bucket_queue_claim_idx
    ON live.bucket_queue (status, job_kind, bucket_ts, locked_at);

CREATE TABLE IF NOT EXISTS qa.live_measurement_issue (
    issue_id BIGSERIAL PRIMARY KEY,
    issue_kind TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'medium',
    meter_urn TEXT NOT NULL,
    measurement TEXT NOT NULL,
    event_id TEXT,
    bucket_ts TIMESTAMPTZ,
    resolution TEXT,
    policy_id BIGINT,
    policy_version INTEGER,
    reason TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT live_measurement_issue_severity_check CHECK (severity IN ('low', 'medium', 'high', 'block'))
);

CREATE INDEX IF NOT EXISTS live_measurement_issue_lookup_idx
    ON qa.live_measurement_issue (meter_urn, measurement, created_at);

CREATE TABLE IF NOT EXISTS live.promotion_check (
    check_id BIGSERIAL PRIMARY KEY,
    source_table TEXT NOT NULL,
    meter_urn TEXT NOT NULL,
    measurement TEXT NOT NULL,
    resolution TEXT NOT NULL,
    bucket_ts TIMESTAMPTZ NOT NULL,
    policy_id BIGINT,
    policy_version INTEGER NOT NULL,
    eligibility_status TEXT NOT NULL,
    block_reasons TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT promotion_check_status_check CHECK (eligibility_status IN ('pass', 'warn', 'block'))
);

CREATE INDEX IF NOT EXISTS promotion_check_lookup_idx
    ON live.promotion_check (eligibility_status, resolution, bucket_ts);

CREATE TABLE IF NOT EXISTS ops.worker_heartbeat (
    worker_name TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'unknown',
    heartbeat_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error TEXT,
    restart_count INTEGER NOT NULL DEFAULT 0,
    processed_count BIGINT NOT NULL DEFAULT 0,
    failed_count BIGINT NOT NULL DEFAULT 0,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT worker_heartbeat_status_check CHECK (status IN ('starting', 'running', 'degraded', 'stopped', 'failed', 'unknown')),
    CONSTRAINT worker_heartbeat_counts_check CHECK (restart_count >= 0 AND processed_count >= 0 AND failed_count >= 0)
);

CREATE TABLE IF NOT EXISTS ops.pipeline_latency_event (
    latency_id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    evidence_level TEXT NOT NULL,
    stage TEXT NOT NULL,
    event_id TEXT,
    meter_urn TEXT,
    measurement TEXT,
    bucket_ts TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    duration_sec DOUBLE PRECISION,
    source_to_fastapi_sec DOUBLE PRECISION,
    fastapi_to_kafka_sec DOUBLE PRECISION,
    kafka_to_event_sec DOUBLE PRECISION,
    event_to_1min_sec DOUBLE PRECISION,
    event_to_queue_sec DOUBLE PRECISION,
    one_min_to_15min_sec DOUBLE PRECISION,
    one_min_to_1h_sec DOUBLE PRECISION,
    one_min_to_peak_feature_sec DOUBLE PRECISION,
    peak_feature_to_peak_input_sec DOUBLE PRECISION,
    qa_eligibility_sec DOUBLE PRECISION,
    promotion_ready_sec DOUBLE PRECISION,
    end_to_end_sec DOUBLE PRECISION,
    kafka_consumer_lag BIGINT,
    kafka_dlq_count BIGINT,
    kafka_produce_error_count BIGINT,
    fastapi_ingest_request_rate DOUBLE PRECISION,
    fastapi_ingest_4xx_count BIGINT,
    fastapi_ingest_5xx_count BIGINT,
    fastapi_ingest_p95_ms DOUBLE PRECISION,
    blocked_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT pipeline_latency_nonnegative_check CHECK (
        COALESCE(duration_sec, 0) >= 0
        AND COALESCE(source_to_fastapi_sec, 0) >= 0
        AND COALESCE(fastapi_to_kafka_sec, 0) >= 0
        AND COALESCE(kafka_to_event_sec, 0) >= 0
        AND COALESCE(event_to_1min_sec, 0) >= 0
        AND COALESCE(event_to_queue_sec, 0) >= 0
        AND COALESCE(one_min_to_15min_sec, 0) >= 0
        AND COALESCE(one_min_to_1h_sec, 0) >= 0
        AND COALESCE(one_min_to_peak_feature_sec, 0) >= 0
        AND COALESCE(peak_feature_to_peak_input_sec, 0) >= 0
        AND COALESCE(qa_eligibility_sec, 0) >= 0
        AND COALESCE(promotion_ready_sec, 0) >= 0
        AND COALESCE(end_to_end_sec, 0) >= 0
        AND COALESCE(kafka_consumer_lag, 0) >= 0
        AND COALESCE(kafka_dlq_count, 0) >= 0
        AND COALESCE(kafka_produce_error_count, 0) >= 0
        AND COALESCE(fastapi_ingest_request_rate, 0) >= 0
        AND COALESCE(fastapi_ingest_4xx_count, 0) >= 0
        AND COALESCE(fastapi_ingest_5xx_count, 0) >= 0
        AND COALESCE(fastapi_ingest_p95_ms, 0) >= 0
    ),
    CONSTRAINT pipeline_latency_counts_check CHECK (blocked_count >= 0 AND failed_count >= 0 AND retry_count >= 0)
);



CREATE TABLE IF NOT EXISTS ops.kafka_consumer_lag (
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    topic TEXT NOT NULL,
    consumer_group TEXT NOT NULL,
    partition INTEGER,
    current_offset BIGINT,
    high_watermark BIGINT,
    lag BIGINT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT kafka_consumer_lag_nonnegative_check CHECK (lag >= 0)
);

CREATE TABLE IF NOT EXISTS ops.fastapi_ingest_metric (
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metric_name TEXT NOT NULL,
    metric_value DOUBLE PRECISION NOT NULL,
    route TEXT NOT NULL DEFAULT '/ingest/measurements',
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT fastapi_ingest_metric_name_check CHECK (metric_name IN ('fastapi_ingest_request_rate', 'fastapi_ingest_4xx_count', 'fastapi_ingest_5xx_count', 'fastapi_ingest_p95_ms')),
    CONSTRAINT fastapi_ingest_metric_nonnegative_check CHECK (metric_value >= 0)
);

CREATE INDEX IF NOT EXISTS worker_heartbeat_status_idx
    ON ops.worker_heartbeat (status, heartbeat_at);

CREATE INDEX IF NOT EXISTS pipeline_latency_stage_idx
    ON ops.pipeline_latency_event (stage, created_at);

CREATE INDEX IF NOT EXISTS pipeline_latency_bucket_idx
    ON ops.pipeline_latency_event (bucket_ts, meter_urn, measurement);

CREATE INDEX IF NOT EXISTS kafka_consumer_lag_lookup_idx
    ON ops.kafka_consumer_lag (consumer_group, topic, observed_at);

CREATE INDEX IF NOT EXISTS fastapi_ingest_metric_lookup_idx
    ON ops.fastapi_ingest_metric (metric_name, created_at);

CREATE TABLE IF NOT EXISTS live.promotion_run (
    promotion_id TEXT PRIMARY KEY,
    approval_id TEXT NOT NULL,
    target_tables TEXT[] NOT NULL,
    source_window TSTZRANGE,
    row_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'requested',
    requested_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT promotion_run_status_check CHECK (status IN ('requested', 'approved', 'running', 'done', 'failed', 'rolled_back'))
);

CREATE TABLE IF NOT EXISTS mart.peak_feature_15min (
    bucket_ts TIMESTAMPTZ NOT NULL,
    meter_urn TEXT NOT NULL,
    measurement TEXT NOT NULL,
    peak_value DOUBLE PRECISION,
    peak_ts TIMESTAMPTZ,
    max_value DOUBLE PRECISION,
    min_value DOUBLE PRECISION,
    mean_value DOUBLE PRECISION,
    std_value DOUBLE PRECISION,
    coverage_ratio NUMERIC(8,6) NOT NULL,
    valid_peak_window BOOLEAN NOT NULL DEFAULT FALSE,
    source_bucket_refs TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    policy_id BIGINT,
    policy_version INTEGER NOT NULL,
    feature_version TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (meter_urn, measurement, bucket_ts, policy_version, feature_version),
    CONSTRAINT peak_feature_coverage_check CHECK (coverage_ratio BETWEEN 0 AND 1)
);

CREATE INDEX IF NOT EXISTS peak_feature_window_idx
    ON mart.peak_feature_15min (meter_urn, measurement, bucket_ts);

CREATE TABLE IF NOT EXISTS mart.peak_input_15min (
    bucket_ts TIMESTAMPTZ NOT NULL,
    meter_urn TEXT NOT NULL,
    measurement TEXT NOT NULL,
    rolling_1h_peak_value DOUBLE PRECISION,
    rolling_1h_peak_ts TIMESTAMPTZ,
    rolling_1h_mean_value DOUBLE PRECISION,
    rolling_1h_valid_bucket_count INTEGER NOT NULL,
    rolling_1h_coverage_ratio NUMERIC(8,6) NOT NULL,
    policy_id BIGINT,
    policy_version INTEGER NOT NULL,
    feature_version TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (meter_urn, measurement, bucket_ts, policy_version, feature_version),
    CONSTRAINT peak_input_valid_count_check CHECK (rolling_1h_valid_bucket_count >= 0),
    CONSTRAINT peak_input_coverage_check CHECK (rolling_1h_coverage_ratio BETWEEN 0 AND 1)
);

-- Common trigger draft. Review-only: not enabled unless the DDL is approved.
CREATE OR REPLACE FUNCTION live.handle_measurement_event_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    matched_policy live.measurement_policy%ROWTYPE;
    policy_count INTEGER;
    disabled_policy_count INTEGER;
    bucket_1min TIMESTAMPTZ;
    bucket_15min TIMESTAMPTZ;
    bucket_1h TIMESTAMPTZ;
BEGIN
    SELECT count(*)
    INTO policy_count
    FROM live.measurement_policy p
    WHERE p.meter_urn = NEW.meter_urn
      AND p.measurement = NEW.measurement
      AND p.enabled = TRUE
      AND p.effective_from <= NEW.event_ts
      AND (p.effective_to IS NULL OR p.effective_to > NEW.event_ts);

    IF policy_count = 0 THEN
        SELECT count(*)
        INTO disabled_policy_count
        FROM live.measurement_policy p
        WHERE p.meter_urn = NEW.meter_urn
          AND p.measurement = NEW.measurement
          AND p.enabled = FALSE
          AND p.effective_from <= NEW.event_ts
          AND (p.effective_to IS NULL OR p.effective_to > NEW.event_ts);

        IF disabled_policy_count > 0 THEN
            INSERT INTO qa.live_measurement_issue (issue_kind, severity, meter_urn, measurement, event_id, reason)
            VALUES ('policy_disabled', 'block', NEW.meter_urn, NEW.measurement, NEW.event_id, 'effective live.measurement_policy is disabled');
            NEW.policy_lookup_status := 'policy_disabled';
            RETURN NEW;
        END IF;

        INSERT INTO qa.live_measurement_issue (issue_kind, severity, meter_urn, measurement, event_id, reason)
        VALUES ('policy_miss', 'block', NEW.meter_urn, NEW.measurement, NEW.event_id, 'no effective live.measurement_policy');
        NEW.policy_lookup_status := 'policy_miss';
        RETURN NEW;
    END IF;

    IF policy_count > 1 THEN
        INSERT INTO qa.live_measurement_issue (issue_kind, severity, meter_urn, measurement, event_id, reason)
        VALUES ('policy_ambiguous', 'block', NEW.meter_urn, NEW.measurement, NEW.event_id, 'multiple effective live.measurement_policy rows');
        NEW.policy_lookup_status := 'policy_ambiguous';
        RETURN NEW;
    END IF;

    SELECT *
    INTO matched_policy
    FROM live.measurement_policy p
    WHERE p.meter_urn = NEW.meter_urn
      AND p.measurement = NEW.measurement
      AND p.enabled = TRUE
      AND p.effective_from <= NEW.event_ts
      AND (p.effective_to IS NULL OR p.effective_to > NEW.event_ts)
    LIMIT 1;

    bucket_1min := date_trunc('minute', NEW.event_ts);
    bucket_15min := date_trunc('hour', NEW.event_ts) + floor(extract(minute FROM NEW.event_ts) / 15) * interval '15 minutes';
    bucket_1h := date_trunc('hour', NEW.event_ts);

    INSERT INTO live.measurement_1min (
        bucket_ts, resolution, meter_urn, measurement, value, unit, aggregation_policy,
        expected_points, observed_points, gap_points, coverage_ratio, mask_code, quality_code,
        provenance, source_event_ids, policy_id, policy_version, lineage_key
    )
    VALUES (
        bucket_1min, '1min', NEW.meter_urn, NEW.measurement, NEW.value_numeric, NEW.unit,
        matched_policy.aggregation_policy,
        1,
        CASE WHEN NEW.value_numeric IS NULL THEN 0 ELSE 1 END,
        CASE WHEN NEW.value_numeric IS NULL THEN 1 ELSE 0 END,
        CASE WHEN NEW.value_numeric IS NULL THEN 0 ELSE 1 END,
        CASE WHEN NEW.value_numeric IS NULL THEN 'missing_observation' ELSE NULL END,
        CASE WHEN NEW.value_numeric IS NULL THEN 'null_observation' ELSE 'observed' END,
        jsonb_build_object('source_table', 'live.measurement_event', 'policy_version', matched_policy.policy_version),
        ARRAY[NEW.event_id],
        matched_policy.policy_id,
        matched_policy.policy_version,
        NEW.event_id
    )
    ON CONFLICT (meter_urn, measurement, resolution, bucket_ts, policy_version)
    DO UPDATE SET
        value = EXCLUDED.value,
        observed_points = EXCLUDED.observed_points,
        gap_points = EXCLUDED.gap_points,
        coverage_ratio = EXCLUDED.coverage_ratio,
        quality_code = EXCLUDED.quality_code,
        source_event_ids = live.measurement_1min.source_event_ids || EXCLUDED.source_event_ids,
        updated_at = now();

    IF matched_policy.mean_rollup_enabled THEN
        INSERT INTO live.bucket_queue (meter_urn, measurement, resolution, bucket_ts, job_kind, policy_id, policy_version, source_min_ts, source_max_ts, watermark_ts)
        VALUES
            (NEW.meter_urn, NEW.measurement, '15min', bucket_15min, 'mean_rollup', matched_policy.policy_id, matched_policy.policy_version, NEW.event_ts, NEW.event_ts, NEW.event_ts),
            (NEW.meter_urn, NEW.measurement, '1h', bucket_1h, 'mean_rollup', matched_policy.policy_id, matched_policy.policy_version, NEW.event_ts, NEW.event_ts, NEW.event_ts)
        ON CONFLICT (meter_urn, measurement, resolution, bucket_ts, job_kind, policy_version)
        DO UPDATE SET status = 'pending', source_min_ts = LEAST(live.bucket_queue.source_min_ts, EXCLUDED.source_min_ts), source_max_ts = GREATEST(live.bucket_queue.source_max_ts, EXCLUDED.source_max_ts), watermark_ts = GREATEST(live.bucket_queue.watermark_ts, EXCLUDED.watermark_ts), updated_at = now();
    END IF;

    IF matched_policy.peak_feature_enabled THEN
        INSERT INTO live.bucket_queue (meter_urn, measurement, resolution, bucket_ts, job_kind, policy_id, policy_version, source_min_ts, source_max_ts, watermark_ts)
        VALUES (NEW.meter_urn, NEW.measurement, '15min', bucket_15min, 'peak_feature', matched_policy.policy_id, matched_policy.policy_version, NEW.event_ts, NEW.event_ts, NEW.event_ts)
        ON CONFLICT (meter_urn, measurement, resolution, bucket_ts, job_kind, policy_version)
        DO UPDATE SET status = 'pending', source_min_ts = LEAST(live.bucket_queue.source_min_ts, EXCLUDED.source_min_ts), source_max_ts = GREATEST(live.bucket_queue.source_max_ts, EXCLUDED.source_max_ts), watermark_ts = GREATEST(live.bucket_queue.watermark_ts, EXCLUDED.watermark_ts), updated_at = now();
    END IF;

    NEW.policy_lookup_status := 'resolved';
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS measurement_event_insert_live_trigger ON live.measurement_event;
CREATE TRIGGER measurement_event_insert_live_trigger
BEFORE INSERT ON live.measurement_event
FOR EACH ROW
EXECUTE FUNCTION live.handle_measurement_event_insert();

-- End of review-only draft.
