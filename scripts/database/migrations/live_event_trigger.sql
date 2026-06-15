-- GATED live.measurement_event trigger activation for bounded runtime smoke.
-- Scope: enable future event inserts to resolve policy, materialize live.measurement_1min,
-- and enqueue live.bucket_queue rows. This does not backfill existing live.measurement_event rows
-- and does not write canonical, mart, or model-serving outputs.
-- Apply only inside an approved admin transaction:
--   BEGIN;
--   SET LOCAL cms.allow_live_trigger_activation = '1';
--   \i scripts/database/migrations/live_event_trigger.sql
--   COMMIT;

DO $$
BEGIN
    IF current_setting('cms.allow_live_trigger_activation', true) <> '1' THEN
        RAISE EXCEPTION 'activate_live_event_trigger.sql is gated: SET LOCAL cms.allow_live_trigger_activation = 1 in an approved admin transaction';
    END IF;
END
$$;

-- Gated live trigger activation. Do not apply without runtime smoke approval.
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
    -- Idempotency guard for direct live ingestion. PostgreSQL executes BEFORE
    -- INSERT triggers before ON CONFLICT handling, so duplicate/replayed
    -- messages must be skipped before any QA/1min/queue side effects.
    IF EXISTS (
        SELECT 1
        FROM live.measurement_event existing_live
        WHERE existing_live.event_id = NEW.event_id
           OR (
               NEW.source_event_id IS NOT NULL
               AND existing_live.source_layer = NEW.source_layer
               AND existing_live.source_event_id = NEW.source_event_id
           )
           OR (
               NEW.kafka_topic IS NOT NULL
               AND NEW.kafka_partition IS NOT NULL
               AND NEW.kafka_offset IS NOT NULL
               AND existing_live.kafka_topic = NEW.kafka_topic
               AND existing_live.kafka_partition = NEW.kafka_partition
               AND existing_live.kafka_offset = NEW.kafka_offset
           )
    ) THEN
        RETURN NULL;
    END IF;

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
