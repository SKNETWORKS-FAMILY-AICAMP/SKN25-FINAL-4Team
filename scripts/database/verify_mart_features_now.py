from pathlib import Path
import os
import psycopg

vals = {}
for line in Path('/workspace/.env').read_text(errors='ignore').splitlines():
    s = line.strip()
    if s and not s.startswith('#') and '=' in s:
        k, v = s.split('=', 1)
        vals[k.strip()] = v.strip().strip('"').strip("'")

def connect():
    return psycopg.connect(host=vals['DB_HOST'], port=int(vals.get('DB_PORT','5432')), dbname=vals['DB_NAME'], user=vals['DB_USER'], password=vals['DB_PASSWORD'], connect_timeout=10)
queries = [
    ("table_exists", """
        SELECT table_schema || '.' || table_name AS table_name
        FROM information_schema.tables
        WHERE table_schema='mart'
          AND table_name IN ('pmax_forecast_15min','anomaly_feature_1h','anomaly_warning_1h','peak_feature_15min')
        ORDER BY 1
    """),
    ("pmax_summary", """
        SELECT count(*) AS rows,
               count(DISTINCT logical_meter) AS logical_meters,
               count(DISTINCT source_meter_urn) AS source_meters,
               min(target_ts) AS min_target_ts,
               max(target_ts) AS max_target_ts,
               count(*) FILTER (WHERE target_ts >= TIMESTAMPTZ '2023-12-01 00:00:00+09') AS dec_target_rows,
               count(*) FILTER (WHERE base_ts >= TIMESTAMPTZ '2023-12-01 00:00:00+09') AS dec_base_rows,
               count(*) FILTER (WHERE actual_window_ts >= TIMESTAMPTZ '2023-12-01 00:00:00+09') AS dec_actual_window_rows,
               count(*) FILTER (WHERE actual_window_ts <> target_ts - interval '15 minutes') AS actual_window_contract_violations,
               count(*) FILTER (WHERE predicted_p_max < 0) AS negative_predictions
        FROM mart.pmax_forecast_15min
        WHERE target_ts >= TIMESTAMPTZ '2023-01-01 00:00:00+09'
          AND target_ts <  TIMESTAMPTZ '2023-12-01 00:00:00+09'
    """),
    ("pmax_duplicate_keys", """
        SELECT count(*) FROM (
            SELECT logical_meter, source_meter_urn, target_ts, count(*)
            FROM mart.pmax_forecast_15min
            WHERE target_ts >= TIMESTAMPTZ '2023-01-01 00:00:00+09'
              AND target_ts <  TIMESTAMPTZ '2023-12-01 00:00:00+09'
            GROUP BY 1,2,3 HAVING count(*) > 1
        ) d
    """),
    ("anomaly_feature_summary", """
        SELECT count(*) AS rows,
               count(DISTINCT meter_urn) AS meters,
               count(DISTINCT feature_set) AS feature_sets,
               min(bucket_ts) AS min_bucket_ts,
               max(bucket_ts) AS max_bucket_ts,
               count(*) FILTER (WHERE bucket_ts >= TIMESTAMPTZ '2023-12-01 00:00:00+09') AS dec_rows,
               count(*) FILTER (WHERE feature_set IS NULL OR feature_set = '') AS missing_feature_set_rows,
               count(*) FILTER (WHERE input_quality IS NULL OR input_quality = '') AS missing_input_quality_rows,
               count(*) FILTER (WHERE source_refs IS NULL OR source_refs = '{}'::jsonb) AS missing_source_refs_rows
        FROM mart.anomaly_feature_1h
        WHERE bucket_ts >= TIMESTAMPTZ '2023-01-01 00:00:00+09'
          AND bucket_ts <  TIMESTAMPTZ '2023-12-01 00:00:00+09'
    """),
    ("anomaly_feature_duplicate_keys", """
        SELECT count(*) FROM (
            SELECT meter_urn, bucket_ts, feature_set, count(*)
            FROM mart.anomaly_feature_1h
            WHERE bucket_ts >= TIMESTAMPTZ '2023-01-01 00:00:00+09'
              AND bucket_ts <  TIMESTAMPTZ '2023-12-01 00:00:00+09'
            GROUP BY 1,2,3 HAVING count(*) > 1
        ) d
    """),
    ("anomaly_warning_summary", """
        SELECT count(*) AS rows,
               count(DISTINCT meter_urn) AS meters,
               min(target_ts) AS min_target_ts,
               max(target_ts) AS max_target_ts,
               count(*) FILTER (WHERE target_ts >= TIMESTAMPTZ '2023-12-01 00:00:00+09') AS dec_target_rows,
               sum(CASE WHEN warning_flag THEN 1 ELSE 0 END) AS warning_true_rows,
               count(*) FILTER (WHERE status IS NULL OR status = '') AS missing_status_rows,
               count(*) FILTER (WHERE source_input_refs IS NULL OR source_input_refs = '{}'::jsonb) AS missing_source_input_refs_rows
        FROM mart.anomaly_warning_1h
        WHERE target_ts >= TIMESTAMPTZ '2023-01-01 00:00:00+09'
          AND target_ts <  TIMESTAMPTZ '2023-12-01 00:00:00+09'
    """),
    ("anomaly_warning_duplicate_keys", """
        SELECT count(*) FROM (
            SELECT meter_urn, forecast_origin_ts, lead_step, count(*)
            FROM mart.anomaly_warning_1h
            WHERE target_ts >= TIMESTAMPTZ '2023-01-01 00:00:00+09'
              AND target_ts <  TIMESTAMPTZ '2023-12-01 00:00:00+09'
            GROUP BY 1,2,3 HAVING count(*) > 1
        ) d
    """),
    ("peak_feature_summary", """
        SELECT count(*) AS rows,
               count(DISTINCT meter_urn) AS meters,
               count(DISTINCT measurement) AS measurements,
               min(window_ts) AS min_window_ts,
               max(window_ts) AS max_window_ts,
               count(*) FILTER (WHERE window_ts >= TIMESTAMPTZ '2023-12-01 00:00:00+09') AS dec_rows,
               count(*) FILTER (WHERE coverage_ratio < 0 OR coverage_ratio > 1) AS invalid_coverage_rows,
               count(*) FILTER (WHERE observed_points < 0 OR expected_points < 0) AS invalid_points_rows
        FROM mart.peak_feature_15min
        WHERE window_ts >= TIMESTAMPTZ '2023-01-01 00:00:00+09'
          AND window_ts <  TIMESTAMPTZ '2023-12-01 00:00:00+09'
    """),
    ("peak_feature_duplicate_keys", """
        SELECT count(*) FROM (
            SELECT meter_urn, measurement, window_ts, count(*)
            FROM mart.peak_feature_15min
            WHERE window_ts >= TIMESTAMPTZ '2023-01-01 00:00:00+09'
              AND window_ts <  TIMESTAMPTZ '2023-12-01 00:00:00+09'
            GROUP BY 1,2,3 HAVING count(*) > 1
        ) d
    """),
    ("ops_logs", """
        SELECT 'pmax' AS kind, run_id, status, quality_status, forecast_row_count::text, logical_meter_count::text
        FROM ops.pmax_log
        WHERE run_id='preload_2023_jan_nov_20260616_pmax'
        UNION ALL
        SELECT 'anomaly' AS kind, run_id, status, quality_status, warning_row_count::text, logical_meter_count::text
        FROM ops.anomaly_log
        WHERE run_id='preload_2023_jan_nov_20260616_anomaly'
        ORDER BY kind
    """),
]
with connect() as conn:
    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = '120s'")
        for name, sql in queries:
            print(f'## {name}')
            cur.execute(sql)
            for row in cur.fetchall():
                print('|'.join('' if v is None else str(v) for v in row))
