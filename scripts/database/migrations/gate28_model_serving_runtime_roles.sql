-- Gate 28 REVIEW-ONLY model-serving runtime DB role proposal.
--
-- Purpose:
--   Prepare a dedicated least-privilege role boundary for hybrid/reference-aware
--   model serving without touching canonical facts, ordinary DELETE privileges,
--   default privileges, passwords, or broad schema CREATE privileges.
--
-- Plan-only default:
--   Do not run blindly. This file performs DDL/GRANT only when an approved admin
--   transaction explicitly sets the local gate below. Repo verification should
--   use scripts/database/verify/gate28_model_serving_runtime_privilege_check.sql
--   first; no DB changes are executed by default in Gate 28.
--
-- Apply gate, after approval only:
--   BEGIN;
--   SET LOCAL cms.allow_gate28_model_serving_runtime_role_plan = '1';
--   \i scripts/database/migrations/gate28_model_serving_runtime_roles.sql
--   COMMIT;
--
-- Safety/trade-off:
--   - Group roles are NOLOGIN; login/password binding remains external to this file.
--   - Additive grants only; no REVOKE, DROP, ALTER DEFAULT PRIVILEGES, or secrets.
--   - Runtime write privileges are limited to approved mart/ops/qa serving outputs
--     and are intended to be attached only after the separate production write gate.
--   - Reference/backfill reads are isolated in cms_model_serving_reference_read and
--     are not bundled into cms_model_serving_runtime by this proposal.

\set ON_ERROR_STOP on

DO $$
BEGIN
    IF current_setting('cms.allow_gate28_model_serving_runtime_role_plan', true) IS DISTINCT FROM '1' THEN
        RAISE EXCEPTION 'gate28_model_serving_runtime_roles.sql is gated: SET LOCAL cms.allow_gate28_model_serving_runtime_role_plan = 1 in an approved admin transaction';
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cms_model_serving_runtime') THEN
        CREATE ROLE cms_model_serving_runtime NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cms_model_serving_reference_read') THEN
        CREATE ROLE cms_model_serving_reference_read NOLOGIN;
    END IF;
END
$$;

-- Production/hybrid model-serving runtime: approved feature inputs only.
GRANT USAGE ON SCHEMA mart TO cms_model_serving_runtime;
GRANT SELECT ON TABLE mart.peak_feature_15min TO cms_model_serving_runtime;
GRANT SELECT ON TABLE mart.anomaly_feature_1h TO cms_model_serving_runtime;

-- Production write-gated outputs only. No canonical writes, no ordinary DELETE.
GRANT USAGE ON SCHEMA ops TO cms_model_serving_runtime;
GRANT USAGE ON SCHEMA qa TO cms_model_serving_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE mart.pmax_forecast_15min TO cms_model_serving_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE mart.anomaly_warning_1h TO cms_model_serving_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE ops.pmax_forecast_inference_log TO cms_model_serving_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE ops.anomaly_warning_inference_log TO cms_model_serving_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE qa.pmax_forecast_evaluation TO cms_model_serving_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE qa.anomaly_warning_evaluation TO cms_model_serving_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE qa.model_serving_evidence_packet TO cms_model_serving_runtime;

-- Non-production/reference-backfill or explicitly approved hybrid reference reads.
-- Keep this role separate from cms_model_serving_runtime so production serving does
-- not silently fall back to reference.corrected_resampled_* inputs.
GRANT USAGE ON SCHEMA reference TO cms_model_serving_reference_read;
GRANT SELECT ON TABLE reference.corrected_resampled_15min TO cms_model_serving_reference_read;
GRANT SELECT ON TABLE reference.corrected_resampled_1h TO cms_model_serving_reference_read;

-- Intentional omissions:
--   - no GRANT on canonical.*
--   - no GRANT CREATE on any schema
--   - no GRANT DELETE/TRUNCATE/REFERENCES/TRIGGER on any table
--   - no ALTER DEFAULT PRIVILEGES; future objects must be approved explicitly
--   - no role membership grants to login users; bind outside this proposal
