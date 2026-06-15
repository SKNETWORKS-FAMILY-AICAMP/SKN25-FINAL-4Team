-- R2 live bucket worker runtime grants.
--
-- STATUS: APPLY PACKET. Non-canonical runtime grants only.
-- Purpose:
--   Allow cms_ingest, the current edge stream consumer/worker role, to process
--   live.bucket_queue peak_feature jobs into mart.peak_feature_15min and write
--   live.promotion_check evidence during R2 harmonized live replay.
--
-- Safety:
--   - No canonical schema usage or grants.
--   - No DDL on data tables.
--   - No data row writes in this packet.

\set ON_ERROR_STOP on

DO $$
BEGIN
    IF current_setting('cms.allow_r2_worker_grants', true) <> '1' THEN
        RAISE EXCEPTION 'r2_live_bucket_worker_grants.sql is gated: SET LOCAL cms.allow_r2_worker_grants = 1 in an approved admin transaction';
    END IF;
END
$$;

GRANT USAGE ON SCHEMA mart TO cms_ingest;
GRANT SELECT, INSERT, UPDATE ON TABLE mart.peak_feature_15min TO cms_ingest;
GRANT SELECT ON TABLE mart.active_peak_feature_15min TO cms_ingest;
GRANT SELECT ON TABLE ops.active_data_exclusion_window TO cms_ingest;
GRANT SELECT, INSERT, UPDATE ON TABLE live.measurement_15min TO cms_ingest;
GRANT SELECT, INSERT, UPDATE ON TABLE live.measurement_1h TO cms_ingest;
GRANT SELECT, INSERT, UPDATE ON TABLE live.promotion_check TO cms_ingest;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA live TO cms_ingest;

SELECT
    'r2_worker_grants_applied' AS check_name,
    has_schema_privilege('cms_ingest', 'mart', 'USAGE') AS mart_usage,
    has_table_privilege('cms_ingest', 'mart.peak_feature_15min', 'INSERT') AS peak_insert,
    has_table_privilege('cms_ingest', 'mart.peak_feature_15min', 'UPDATE') AS peak_update,
    has_table_privilege('cms_ingest', 'mart.active_peak_feature_15min', 'SELECT') AS active_peak_select,
    has_table_privilege('cms_ingest', 'live.promotion_check', 'INSERT') AS promotion_insert,
    has_schema_privilege('cms_ingest', 'canonical', 'USAGE') AS canonical_usage;
