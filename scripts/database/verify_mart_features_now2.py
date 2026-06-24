from pathlib import Path
import psycopg
vals={}
for line in Path('/workspace/.env').read_text(errors='ignore').splitlines():
    s=line.strip()
    if s and not s.startswith('#') and '=' in s:
        k,v=s.split('=',1); vals[k.strip()]=v.strip().strip('"').strip("'")

def run(name, sql):
    print('##', name)
    with psycopg.connect(host=vals['DB_HOST'], port=int(vals.get('DB_PORT','5432')), dbname=vals['DB_NAME'], user=vals['DB_USER'], password=vals['DB_PASSWORD'], connect_timeout=10, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '120s'")
            cur.execute(sql)
            for row in cur.fetchall(): print('|'.join('' if v is None else str(v) for v in row))

run('pmax_summary', """
    SELECT count(*), count(DISTINCT logical_meter), count(DISTINCT source_meter_urn),
           min(target_ts), max(target_ts),
           count(*) FILTER (WHERE target_ts >= TIMESTAMPTZ '2023-12-01 00:00:00+09'),
           count(*) FILTER (WHERE base_ts >= TIMESTAMPTZ '2023-12-01 00:00:00+09'),
           count(*) FILTER (WHERE actual_window_ts >= TIMESTAMPTZ '2023-12-01 00:00:00+09'),
           count(*) FILTER (WHERE actual_window_ts <> target_ts - interval '15 minutes'),
           count(*) FILTER (WHERE predicted_p_max < 0)
    FROM mart.pmax_forecast_15min
    WHERE target_ts >= TIMESTAMPTZ '2023-01-01 00:00:00+09' AND target_ts < TIMESTAMPTZ '2023-12-01 00:00:00+09'
""")
run('pmax_horizon_counts', """
    SELECT horizon_minutes, count(*) FROM mart.pmax_forecast_15min
    WHERE target_ts >= TIMESTAMPTZ '2023-01-01 00:00:00+09' AND target_ts < TIMESTAMPTZ '2023-12-01 00:00:00+09'
    GROUP BY 1 ORDER BY 1
""")
run('pmax_duplicate_full_key', """
    SELECT count(*) FROM (
      SELECT logical_meter, source_meter_urn, base_ts, target_ts, actual_window_ts, horizon_minutes, count(*)
      FROM mart.pmax_forecast_15min
      WHERE target_ts >= TIMESTAMPTZ '2023-01-01 00:00:00+09' AND target_ts < TIMESTAMPTZ '2023-12-01 00:00:00+09'
      GROUP BY 1,2,3,4,5,6 HAVING count(*) > 1
    ) d
""")
run('anomaly_feature_summary', """
    SELECT count(*), count(DISTINCT meter_urn), count(DISTINCT feature_set), min(bucket_ts), max(bucket_ts),
           count(*) FILTER (WHERE bucket_ts >= TIMESTAMPTZ '2023-12-01 00:00:00+09'),
           count(*) FILTER (WHERE feature_set IS NULL OR feature_set = ''),
           count(*) FILTER (WHERE input_quality IS NULL OR input_quality = '')
    FROM mart.anomaly_feature_1h
    WHERE bucket_ts >= TIMESTAMPTZ '2023-01-01 00:00:00+09' AND bucket_ts < TIMESTAMPTZ '2023-12-01 00:00:00+09'
""")
run('anomaly_feature_duplicate_key', """
    SELECT count(*) FROM (
      SELECT meter_urn, bucket_ts, feature_set, count(*)
      FROM mart.anomaly_feature_1h
      WHERE bucket_ts >= TIMESTAMPTZ '2023-01-01 00:00:00+09' AND bucket_ts < TIMESTAMPTZ '2023-12-01 00:00:00+09'
      GROUP BY 1,2,3 HAVING count(*) > 1
    ) d
""")
run('anomaly_warning_summary', """
    SELECT count(*), count(DISTINCT meter_urn), min(target_ts), max(target_ts),
           count(*) FILTER (WHERE target_ts >= TIMESTAMPTZ '2023-12-01 00:00:00+09'),
           sum(CASE WHEN warning_flag THEN 1 ELSE 0 END),
           count(*) FILTER (WHERE status IS NULL OR status = '')
    FROM mart.anomaly_warning_1h
    WHERE target_ts >= TIMESTAMPTZ '2023-01-01 00:00:00+09' AND target_ts < TIMESTAMPTZ '2023-12-01 00:00:00+09'
""")
run('anomaly_warning_duplicate_key', """
    SELECT count(*) FROM (
      SELECT meter_urn, forecast_origin_ts, lead_step, count(*)
      FROM mart.anomaly_warning_1h
      WHERE target_ts >= TIMESTAMPTZ '2023-01-01 00:00:00+09' AND target_ts < TIMESTAMPTZ '2023-12-01 00:00:00+09'
      GROUP BY 1,2,3 HAVING count(*) > 1
    ) d
""")
run('peak_feature_summary', """
    SELECT count(*), count(DISTINCT meter_urn), count(DISTINCT measurement), min(window_ts), max(window_ts),
           count(*) FILTER (WHERE window_ts >= TIMESTAMPTZ '2023-12-01 00:00:00+09'),
           count(*) FILTER (WHERE coverage_ratio < 0 OR coverage_ratio > 1),
           count(*) FILTER (WHERE observed_points < 0 OR expected_points < 0)
    FROM mart.peak_feature_15min
    WHERE window_ts >= TIMESTAMPTZ '2023-01-01 00:00:00+09' AND window_ts < TIMESTAMPTZ '2023-12-01 00:00:00+09'
""")
run('ops_logs', """
    SELECT 'pmax', run_id, status, quality_status, forecast_row_count::text, logical_meter_count::text
    FROM ops.pmax_log WHERE run_id='preload_2023_jan_nov_20260616_pmax'
    UNION ALL
    SELECT 'anomaly', run_id, status, quality_status, warning_row_count::text, logical_meter_count::text
    FROM ops.anomaly_log WHERE run_id='preload_2023_jan_nov_20260616_anomaly'
    ORDER BY 1
""")
