from pathlib import Path
import psycopg
vals={}
for line in Path('/workspace/.env').read_text(errors='ignore').splitlines():
    s=line.strip()
    if s and not s.startswith('#') and '=' in s:
        k,v=s.split('=',1); vals[k.strip()]=v.strip().strip('"').strip("'")

def q(name, sql):
    print('## ' + name)
    with psycopg.connect(host=vals['DB_HOST'], port=int(vals.get('DB_PORT','5432')), dbname=vals['DB_NAME'], user=vals['DB_USER'], password=vals['DB_PASSWORD'], connect_timeout=10, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout='90s'")
            cur.execute(sql)
            for row in cur.fetchall():
                print('|'.join('' if v is None else str(v) for v in row))

q('mart_table_ranges', """
SELECT 'anomaly_feature_1h', count(*)::text, min(bucket_ts)::text, max(bucket_ts)::text FROM mart.anomaly_feature_1h
UNION ALL
SELECT 'anomaly_warning_1h', count(*)::text, min(target_ts)::text, max(target_ts)::text FROM mart.anomaly_warning_1h
UNION ALL
SELECT 'pmax_forecast_15min', count(*)::text, min(target_ts)::text, max(target_ts)::text FROM mart.pmax_forecast_15min
ORDER BY 1
""")
q('anomaly_feature_year_counts', """
SELECT extract(year from bucket_ts)::int AS year, count(*) AS rows, count(DISTINCT meter_urn) AS meters
FROM mart.anomaly_feature_1h
GROUP BY 1 ORDER BY 1
""")
q('anomaly_warning_year_counts', """
SELECT extract(year from target_ts)::int AS year, count(*) AS rows, count(DISTINCT meter_urn) AS meters
FROM mart.anomaly_warning_1h
GROUP BY 1 ORDER BY 1
""")
q('pmax_forecast_year_counts', """
SELECT extract(year from target_ts)::int AS year, count(*) AS rows, count(DISTINCT logical_meter) AS logical_meters
FROM mart.pmax_forecast_15min
GROUP BY 1 ORDER BY 1
""")
q('reference_1h_sample_ranges', """
SELECT count(*)::text, min(bucket_ts)::text, max(bucket_ts)::text, count(DISTINCT meter_urn)::text, count(DISTINCT measurement)::text
FROM reference.corrected_resampled_1h
""")
q('live_policy_split_constants', """
SELECT 'anomaly_config', '2018-01-01', '2022-01-01', '2023-01-01', '2024-01-01'
""")
