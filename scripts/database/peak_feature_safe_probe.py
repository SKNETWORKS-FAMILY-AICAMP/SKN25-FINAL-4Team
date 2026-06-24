from pathlib import Path
import psycopg
vals={}
for line in Path('/workspace/.env').read_text(errors='ignore').splitlines():
    s=line.strip()
    if s and not s.startswith('#') and '=' in s:
        k,v=s.split('=',1); vals[k.strip()]=v.strip().strip('"').strip("'")
conn=psycopg.connect(host=vals['DB_HOST'], port=int(vals.get('DB_PORT','5432')), dbname=vals['DB_NAME'], user=vals['DB_USER'], password=vals['DB_PASSWORD'], connect_timeout=10, autocommit=True)
with conn.cursor() as cur:
    cur.execute("""
      SELECT c.reltuples::bigint AS estimated_rows,
             pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size,
             COALESCE(s.n_live_tup,0) AS n_live_tup,
             COALESCE(s.n_dead_tup,0) AS n_dead_tup
      FROM pg_class c
      JOIN pg_namespace n ON n.oid=c.relnamespace
      LEFT JOIN pg_stat_user_tables s ON s.relid=c.oid
      WHERE n.nspname='mart' AND c.relname='peak_feature_15min'
    """)
    print('peak_feature_catalog|' + '|'.join(map(str, cur.fetchone())))
    cur.execute("SELECT window_ts, meter_urn, measurement, observed_points, expected_points, coverage_ratio, source_layer, source_mode FROM mart.peak_feature_15min LIMIT 5")
    for row in cur.fetchall(): print('peak_feature_sample|' + '|'.join('' if v is None else str(v) for v in row))
