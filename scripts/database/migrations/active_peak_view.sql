-- R2 active exclusion metadata + serving view.
--
-- STATUS: REVIEW/APPLY PACKET. Non-destructive DDL only; no row move, no row delete,
-- no canonical write, no promotion.
--
-- Purpose:
--   Keep historical corrected/reference-derived rows in place, but prevent active
--   model-serving queries from reading overlap rows while the 2023 holdout year
--   is replayed from PC1 harmonized observed events through the live path.
--
-- Corrected P-Max live holdout meter basis:
--   V.Z81, V.Z82, H2.Z351, H2.Z361 with P/U1/PF.
--   H2.Z35/H2.Z36 are older replacement predecessors and must not define the
--   2023 live holdout window.
--
-- Window convention:
--   2023 KST holdout as half-open [2023-01-01 00:00:00+09, 2024-01-01 00:00:00+09).
--
-- Safety:
--   - Creates ops.exclusion_window metadata.
--   - Creates mart.peak_feature_15min view over mart.peak_feature_15min.
--   - Inserts/updates one active exclusion rule for corrected_resampled 2023 peak rows.
--   - Does not update/delete/copy existing data rows.
--   - Runtime model-serving query must still require source_mode='live_observed'.

\set ON_ERROR_STOP on

DO $$
BEGIN
    IF current_setting('cms.allow_r2_active_exclusion_ddl', true) <> '1' THEN
        RAISE EXCEPTION 'r2_active_exclusion_view.sql is gated: SET LOCAL cms.allow_r2_active_exclusion_ddl = 1 in an approved admin transaction';
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS ops.exclusion_window (
    exclusion_id TEXT PRIMARY KEY,
    table_name TEXT NOT NULL,
    time_column TEXT NOT NULL,
    start_ts TIMESTAMPTZ NOT NULL,
    end_ts TIMESTAMPTZ NOT NULL,
    source_file_pattern TEXT,
    run_id_pattern TEXT,
    reason TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (end_ts > start_ts),
    CHECK (table_name ~ '^[a-z_]+\.[a-z0-9_]+$'),
    CHECK (time_column ~ '^[a-z_][a-z0-9_]*$')
);

CREATE INDEX IF NOT EXISTS active_data_exclusion_window_table_time_idx
    ON ops.exclusion_window (table_name, active, start_ts, end_ts);

INSERT INTO ops.exclusion_window (
    exclusion_id,
    table_name,
    time_column,
    start_ts,
    end_ts,
    source_file_pattern,
    run_id_pattern,
    reason,
    active
) VALUES (
    'r2_2023_peak_feature_corrected_resampled_exclusion',
    'mart.peak_feature_15min',
    'window_ts',
    timestamptz '2023-01-01 00:00:00+09',
    timestamptz '2024-01-01 00:00:00+09',
    '%corrected_resampled%',
    NULL,
    'R2 2023 harmonized live replay overlap: keep corrected/reference rows physically, exclude from active serving view',
    true
)
ON CONFLICT (exclusion_id) DO UPDATE SET
    table_name = EXCLUDED.table_name,
    time_column = EXCLUDED.time_column,
    start_ts = EXCLUDED.start_ts,
    end_ts = EXCLUDED.end_ts,
    source_file_pattern = EXCLUDED.source_file_pattern,
    run_id_pattern = EXCLUDED.run_id_pattern,
    reason = EXCLUDED.reason,
    active = EXCLUDED.active,
    updated_at = now();

-- Deprecated: no active_peak/peak_input/training_frame alias is created.
-- P-Max callers must read mart.peak_feature_15min directly.
