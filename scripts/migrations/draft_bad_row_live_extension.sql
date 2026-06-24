-- DRAFT ONLY: bad-row live quality extension sketch.
-- NOT AN EXECUTABLE PRODUCTION MIGRATION.
-- Do not run this file through an automated migration runner, deploy job, psql
-- session, or production console.  It is intentionally parked as a draft for
-- review of a possible future change.
--
-- APPROVAL GATE:
--   * Requires a separate written production DDL approval id before any adapted
--     statement is moved into an executable migration.
--   * Requires live evidence packet review, rollback planning, and maintenance
--     window/lock-impact assessment.
--   * Requires least-privilege operator role validation; no broad owner/admin
--     role should be used for the final change unless explicitly approved.
--
-- REDACTION / DATA HANDLING:
--   * Do not embed credentials, connection strings, AWS identifiers, raw error
--     payloads, or user/customer data in migration comments or evidence output.
--   * Reason values copied into tickets must be aggregated or redacted.
--
-- LEAST-PRIVILEGE NOTES:
--   * Final migration role should have only the minimum schema privileges needed
--     for the approved ALTER/CREATE INDEX statements.
--   * Any index build must be reviewed for lock, disk, and timeout impact.
--
-- ROLLBACK NOTE:
--   * Rollback SQL must be authored and approved in the real migration packet;
--     this draft deliberately does not provide executable rollback commands.

-- DRAFT SHAPE ONLY - adapt names after evidence review and approval.
ALTER TABLE qa.bad_row
    ADD COLUMN IF NOT EXISTS evidence_packet_id text,
    ADD COLUMN IF NOT EXISTS live_quality_reason text,
    ADD COLUMN IF NOT EXISTS first_observed_at timestamptz,
    ADD COLUMN IF NOT EXISTS last_observed_at timestamptz;

-- DRAFT SHAPE ONLY - requires approval id, lock review, and redacted evidence.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bad_row_live_quality_reason_observed_at
    ON qa.bad_row (live_quality_reason, last_observed_at DESC);

-- DRAFT SHAPE ONLY - optional linkage for evidence packet lookup after approval.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bad_row_evidence_packet_id
    ON qa.bad_row (evidence_packet_id);
