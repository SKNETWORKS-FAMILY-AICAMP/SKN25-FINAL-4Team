-- REVIEW-ONLY runtime access plan for the CMS A-to-Z live/model-serving boundary.
-- Do not run blindly. Apply only from an approved migration/admin session.
-- Gate convention for psql/manual application:
--   BEGIN;
--   SET LOCAL cms.allow_access_ddl = '1';
--   \i scripts/database/migrations/model_serving_runtime_access.sql
--   COMMIT;
-- The guard below prevents accidental direct execution without the local gate.

DO $$
BEGIN
    IF current_setting('cms.allow_access_ddl', true) <> '1' THEN
        RAISE EXCEPTION 'model_serving_runtime_access.sql is gated: SET LOCAL cms.allow_access_ddl = 1 in an approved admin transaction';
    END IF;
END
$$;

-- Role separation. Passwords/login attributes are managed outside this file by AWS/RDS admin tooling.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cms_migration_admin') THEN
        CREATE ROLE cms_migration_admin NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cms_ingest') THEN
        CREATE ROLE cms_ingest LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cms_model_serving') THEN
        CREATE ROLE cms_model_serving LOGIN;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA live TO cms_ingest, cms_model_serving;
GRANT USAGE ON SCHEMA mart TO cms_model_serving;
GRANT USAGE ON SCHEMA ops TO cms_ingest, cms_model_serving;
GRANT USAGE ON SCHEMA qa TO cms_ingest, cms_model_serving;

-- cms_ingest: live event append + operational/QA issue reporting only.
GRANT SELECT, INSERT ON TABLE live.measurement_event TO cms_ingest;
GRANT SELECT, INSERT, UPDATE ON TABLE ops.pipeline_metric TO cms_ingest;
GRANT SELECT, INSERT ON TABLE qa.bad_row TO cms_ingest;
GRANT SELECT, INSERT ON TABLE qa.meter_tag TO cms_ingest;
-- Target-only live lane grants; valid after the approved live schema migration creates these objects.
GRANT SELECT ON TABLE live.measurement_policy TO cms_ingest;
GRANT SELECT, INSERT, UPDATE ON TABLE live.measurement_1min TO cms_ingest;
GRANT SELECT, INSERT, UPDATE ON TABLE live.bucket_queue TO cms_ingest;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA live TO cms_ingest;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA qa TO cms_ingest;
GRANT SELECT, INSERT ON TABLE qa.live_measurement_issue TO cms_ingest;

-- cms_model_serving: read live/mart features, write non-canonical serving outputs only.
GRANT SELECT ON TABLE live.measurement_event TO cms_model_serving;
GRANT SELECT ON TABLE mart.peak_feature_15min TO cms_model_serving;
GRANT SELECT ON TABLE mart.active_peak_feature_15min TO cms_model_serving;
GRANT SELECT ON TABLE ops.active_data_exclusion_window TO cms_model_serving;
GRANT SELECT ON TABLE mart.peak_training_frame_15min TO cms_model_serving;
-- Current AWS legacy compatibility object; keep SELECT-only while migrating callers to peak_training_frame.
GRANT SELECT ON TABLE mart.peak_feature_15min TO cms_model_serving;
GRANT SELECT ON TABLE mart.anomaly_feature_1h TO cms_model_serving;

GRANT SELECT, INSERT, UPDATE ON TABLE mart.pmax_forecast_15min TO cms_model_serving;
GRANT SELECT, INSERT, UPDATE ON TABLE ops.pmax_forecast_inference_log TO cms_model_serving;
GRANT SELECT, INSERT, UPDATE ON TABLE qa.pmax_forecast_evaluation TO cms_model_serving;
GRANT SELECT, INSERT, UPDATE ON TABLE mart.anomaly_warning_1h TO cms_model_serving;
GRANT SELECT, INSERT, UPDATE ON TABLE ops.anomaly_warning_inference_log TO cms_model_serving;
GRANT SELECT, INSERT, UPDATE ON TABLE qa.anomaly_warning_evaluation TO cms_model_serving;
GRANT SELECT, INSERT, UPDATE ON TABLE qa.model_serving_evidence_packet TO cms_model_serving;

-- migration/admin owns migration boundary. Runtime roles do not receive DDL privileges.
GRANT USAGE, CREATE ON SCHEMA live TO cms_migration_admin;
GRANT USAGE, CREATE ON SCHEMA mart TO cms_migration_admin;
GRANT USAGE, CREATE ON SCHEMA ops TO cms_migration_admin;
GRANT USAGE, CREATE ON SCHEMA qa TO cms_migration_admin;

-- Canonical boundary: intentionally no GRANT on canonical.* appears in this access plan.
-- Canonical promotion remains a separate approval path and is not part of default live/model-serving runtime.
