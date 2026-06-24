from pathlib import Path
import os
import psycopg
from psycopg.types.json import Jsonb

LOAD_ID = 'ontology_meter_measurement_20260616'

def load_env(path: str) -> dict[str, str]:
    vals = {}
    for line in Path(path).read_text(errors='ignore').splitlines():
        s = line.strip()
        if s and not s.startswith('#') and '=' in s:
            k, v = s.split('=', 1)
            vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals

def connect():
    vals = load_env('/workspace/.env')
    return psycopg.connect(host=vals['DB_HOST'], port=int(vals.get('DB_PORT','5432')), dbname=vals['DB_NAME'], user=vals['DB_USER'], password=vals['DB_PASSWORD'], connect_timeout=10)

DDL = [
    '''
    CREATE TABLE IF NOT EXISTS ontology.meter_measurement (
        meter_urn text NOT NULL REFERENCES ontology.meter(meter_urn),
        measurement_code text NOT NULL REFERENCES ontology.measurement_code(measurement_code),
        source_table text NOT NULL DEFAULT 'live.measurement_policy',
        source_policy_id bigint NOT NULL,
        policy_version integer NOT NULL,
        enabled boolean NOT NULL,
        effective_from timestamptz NOT NULL,
        effective_to timestamptz,
        source_update_mode text NOT NULL,
        cadence_group text NOT NULL,
        source_native_interval_seconds integer,
        target_resolution_policy text NOT NULL,
        value_policy text NOT NULL,
        aggregation_policy text NOT NULL,
        expected_points_policy jsonb NOT NULL DEFAULT '{}'::jsonb,
        mean_rollup_enabled boolean NOT NULL,
        peak_feature_enabled boolean NOT NULL,
        coverage_threshold numeric(6,5),
        max_state_hold_age_seconds integer,
        canonical_eligible boolean NOT NULL,
        paper_policy_ref text,
        load_id text NOT NULL,
        loaded_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (meter_urn, measurement_code)
    )
    ''',
    '''
    CREATE OR REPLACE VIEW ontology.meter_measurement_context AS
    SELECT
        mm.meter_urn,
        mm.measurement_code,
        m.meter_domain,
        m.equipment_group_code,
        m.building_code,
        m.meter_role_code,
        mm.source_update_mode,
        mm.cadence_group,
        mm.source_native_interval_seconds,
        mm.target_resolution_policy,
        mm.aggregation_policy,
        mm.canonical_eligible,
        mc.family AS measurement_family,
        mc.description AS measurement_description
    FROM ontology.meter_measurement mm
    JOIN ontology.meter m USING (meter_urn)
    JOIN ontology.measurement_code mc USING (measurement_code)
    ''',
]


def main() -> int:
    with connect() as conn:
        try:
            with conn.cursor() as cur:
                for stmt in DDL:
                    cur.execute(stmt)
                cur.execute('SELECT count(*), count(DISTINCT (meter_urn, measurement)) FROM live.measurement_policy')
                source_rows, source_distinct_pairs = cur.fetchone()
                cur.execute("""
                    SELECT count(*)
                    FROM live.measurement_policy p
                    JOIN ontology.meter m ON m.meter_urn = p.meter_urn
                    JOIN ontology.measurement_code mc ON mc.measurement_code = p.measurement
                """)
                eligible_rows = cur.fetchone()[0]
                cur.execute("""
                    SELECT jsonb_agg(jsonb_build_object('meter_urn', p.meter_urn, 'measurement', p.measurement) ORDER BY p.meter_urn, p.measurement)
                    FROM live.measurement_policy p
                    LEFT JOIN ontology.meter m ON m.meter_urn = p.meter_urn
                    LEFT JOIN ontology.measurement_code mc ON mc.measurement_code = p.measurement
                    WHERE m.meter_urn IS NULL OR mc.measurement_code IS NULL
                """)
                excluded = cur.fetchone()[0] or []
                cur.execute("""
                    INSERT INTO ontology.meter_measurement (
                        meter_urn, measurement_code, source_table, source_policy_id, policy_version,
                        enabled, effective_from, effective_to, source_update_mode, cadence_group,
                        source_native_interval_seconds, target_resolution_policy, value_policy,
                        aggregation_policy, expected_points_policy, mean_rollup_enabled,
                        peak_feature_enabled, coverage_threshold, max_state_hold_age_seconds,
                        canonical_eligible, paper_policy_ref, load_id, loaded_at
                    )
                    SELECT
                        p.meter_urn,
                        p.measurement AS measurement_code,
                        'live.measurement_policy' AS source_table,
                        p.policy_id AS source_policy_id,
                        p.policy_version,
                        p.enabled,
                        p.effective_from,
                        p.effective_to,
                        p.source_update_mode,
                        p.cadence_group,
                        p.source_native_interval_seconds,
                        p.target_resolution_policy,
                        p.value_policy,
                        p.aggregation_policy,
                        p.expected_points_policy,
                        p.mean_rollup_enabled,
                        p.peak_feature_enabled,
                        p.coverage_threshold,
                        p.max_state_hold_age_seconds,
                        p.canonical_eligible,
                        p.paper_policy_ref,
                        %(load_id)s,
                        now()
                    FROM live.measurement_policy p
                    JOIN ontology.meter m ON m.meter_urn = p.meter_urn
                    JOIN ontology.measurement_code mc ON mc.measurement_code = p.measurement
                    ON CONFLICT (meter_urn, measurement_code) DO UPDATE SET
                        source_policy_id = EXCLUDED.source_policy_id,
                        policy_version = EXCLUDED.policy_version,
                        enabled = EXCLUDED.enabled,
                        effective_from = EXCLUDED.effective_from,
                        effective_to = EXCLUDED.effective_to,
                        source_update_mode = EXCLUDED.source_update_mode,
                        cadence_group = EXCLUDED.cadence_group,
                        source_native_interval_seconds = EXCLUDED.source_native_interval_seconds,
                        target_resolution_policy = EXCLUDED.target_resolution_policy,
                        value_policy = EXCLUDED.value_policy,
                        aggregation_policy = EXCLUDED.aggregation_policy,
                        expected_points_policy = EXCLUDED.expected_points_policy,
                        mean_rollup_enabled = EXCLUDED.mean_rollup_enabled,
                        peak_feature_enabled = EXCLUDED.peak_feature_enabled,
                        coverage_threshold = EXCLUDED.coverage_threshold,
                        max_state_hold_age_seconds = EXCLUDED.max_state_hold_age_seconds,
                        canonical_eligible = EXCLUDED.canonical_eligible,
                        paper_policy_ref = EXCLUDED.paper_policy_ref,
                        load_id = EXCLUDED.load_id,
                        loaded_at = EXCLUDED.loaded_at
                """, {'load_id': LOAD_ID})
                cur.execute('SELECT count(*), count(DISTINCT meter_urn), count(DISTINCT measurement_code) FROM ontology.meter_measurement')
                final_rows, final_meters, final_measurements = cur.fetchone()
                counts = {
                    'source_table': 'live.measurement_policy',
                    'source_rows': int(source_rows),
                    'source_distinct_pairs': int(source_distinct_pairs),
                    'eligible_rows': int(eligible_rows),
                    'excluded_rows': len(excluded),
                    'final_rows': int(final_rows),
                    'final_meters': int(final_meters),
                    'final_measurements': int(final_measurements),
                    'excluded': excluded,
                }
                cur.execute(
                    """
                    INSERT INTO ontology.load_log (load_id, status, source_paths, source_hashes, counts, loaded_at)
                    VALUES (%s, 'success', %s, %s, %s, now())
                    ON CONFLICT (load_id) DO UPDATE SET
                        status='success', source_paths=EXCLUDED.source_paths,
                        source_hashes=EXCLUDED.source_hashes, counts=EXCLUDED.counts,
                        loaded_at=EXCLUDED.loaded_at
                    """,
                    (LOAD_ID, Jsonb({'source_table': 'live.measurement_policy'}), Jsonb({}), Jsonb(counts)),
                )
                cur.execute("SELECT 1 FROM pg_roles WHERE rolname='cms_backend_model_results_read'")
                if cur.fetchone():
                    cur.execute('GRANT SELECT ON ontology.meter_measurement TO cms_backend_model_results_read')
                    cur.execute('GRANT SELECT ON ontology.meter_measurement_context TO cms_backend_model_results_read')
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    print(counts)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
