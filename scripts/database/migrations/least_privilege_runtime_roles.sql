-- REVIEW-ONLY least-privilege runtime role plan for CMS live runtime.
--
-- Purpose:
--   Introduce additive least-privilege group roles that can be granted to login
--   roles after credentials are managed externally. This file intentionally does
--   not revoke, rotate, drop, or alter existing login credentials.
--
-- Apply gate:
--   BEGIN;
--   SET LOCAL cms.allow_least_privilege_role_plan = '1';
--   \i scripts/database/migrations/least_privilege_runtime_roles.sql
--   COMMIT;
--
-- Safety:
--   - Additive grants only; no REVOKE statements.
--   - No canonical writes.
--   - Group roles are NOLOGIN so this migration does not create usable auth.
--   - Existing broad roles such as cms are left untouched for rollback/fallback.

\set ON_ERROR_STOP on

DO $$
BEGIN
    IF current_setting('cms.allow_least_privilege_role_plan', true) <> '1' THEN
        RAISE EXCEPTION 'least_privilege_runtime_roles.sql is gated: SET LOCAL cms.allow_least_privilege_role_plan = 1 in an approved admin transaction';
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cms_runtime_ingest_role') THEN
        CREATE ROLE cms_runtime_ingest_role NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cms_runtime_backend_read_role') THEN
        CREATE ROLE cms_runtime_backend_read_role NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cms_runtime_observability_role') THEN
        CREATE ROLE cms_runtime_observability_role NOLOGIN;
    END IF;
END
$$;

-- Ingestion/consumer runtime: append/read live measurement events and write only
-- non-canonical operational QA evidence used by the stream runtime.
GRANT USAGE ON SCHEMA live TO cms_runtime_ingest_role;
GRANT USAGE ON SCHEMA ops TO cms_runtime_ingest_role;
GRANT USAGE ON SCHEMA qa TO cms_runtime_ingest_role;
GRANT SELECT, INSERT ON TABLE live.measurement_event TO cms_runtime_ingest_role;
GRANT SELECT, INSERT, UPDATE ON TABLE ops.metric TO cms_runtime_ingest_role;
GRANT SELECT, INSERT ON TABLE qa.bad_row TO cms_runtime_ingest_role;
GRANT SELECT, INSERT ON TABLE qa.live_issue TO cms_runtime_ingest_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA live TO cms_runtime_ingest_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA qa TO cms_runtime_ingest_role;

-- Backend API runtime: SELECT-only over non-canonical live/mart/ops/qa surfaces.
GRANT USAGE ON SCHEMA live TO cms_runtime_backend_read_role;
GRANT USAGE ON SCHEMA mart TO cms_runtime_backend_read_role;
GRANT USAGE ON SCHEMA ops TO cms_runtime_backend_read_role;
GRANT USAGE ON SCHEMA qa TO cms_runtime_backend_read_role;
GRANT SELECT ON ALL TABLES IN SCHEMA live TO cms_runtime_backend_read_role;
GRANT SELECT ON ALL TABLES IN SCHEMA mart TO cms_runtime_backend_read_role;
GRANT SELECT ON ALL TABLES IN SCHEMA ops TO cms_runtime_backend_read_role;
GRANT SELECT ON ALL TABLES IN SCHEMA qa TO cms_runtime_backend_read_role;

-- Grafana/Prometheus SQL exporter runtime: SELECT-only, no writes.
GRANT USAGE ON SCHEMA live TO cms_runtime_observability_role;
GRANT USAGE ON SCHEMA mart TO cms_runtime_observability_role;
GRANT USAGE ON SCHEMA ops TO cms_runtime_observability_role;
GRANT USAGE ON SCHEMA qa TO cms_runtime_observability_role;
GRANT SELECT ON ALL TABLES IN SCHEMA live TO cms_runtime_observability_role;
GRANT SELECT ON ALL TABLES IN SCHEMA mart TO cms_runtime_observability_role;
GRANT SELECT ON ALL TABLES IN SCHEMA ops TO cms_runtime_observability_role;
GRANT SELECT ON ALL TABLES IN SCHEMA qa TO cms_runtime_observability_role;

-- Explicitly no canonical schema grants are made here.
SELECT
    'least_privilege_runtime_roles_review_plan' AS check_name,
    has_schema_privilege('cms_runtime_ingest_role', 'live', 'USAGE') AS ingest_live_usage,
    has_table_privilege('cms_runtime_ingest_role', 'live.measurement_event', 'INSERT') AS ingest_event_insert,
    has_table_privilege('cms_runtime_backend_read_role', 'live.measurement_event', 'SELECT') AS backend_event_select,
    has_schema_privilege('cms_runtime_ingest_role', 'canonical', 'USAGE') AS ingest_canonical_usage,
    has_schema_privilege('cms_runtime_backend_read_role', 'canonical', 'USAGE') AS backend_canonical_usage;
