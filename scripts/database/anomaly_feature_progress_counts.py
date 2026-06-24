from pathlib import Path
import psycopg
vals={}
for line in Path('/workspace/.env').read_text(errors='ignore').splitlines():
    s=line.strip()
    if s and not s.startswith('#') and '=' in s:
        k,v=s.split('=',1); vals[k.strip()]=v.strip().strip('"').strip("'")
with psycopg.connect(host=vals['DB_HOST'], port=int(vals.get('DB_PORT','5432')), dbname=vals['DB_NAME'], user=vals['DB_USER'], password=vals['DB_PASSWORD'], connect_timeout=10, autocommit=True) as conn:
    with conn.cursor() as cur:
        cur.execute("SET statement_timeout='60s'")
        cur.execute("""
            SELECT extract(year from bucket_ts)::int AS year, count(*) AS rows, count(DISTINCT meter_urn) AS meters,
                   min(bucket_ts)::text, max(bucket_ts)::text
            FROM mart.anomaly_feature_1h
            WHERE bucket_ts >= TIMESTAMPTZ '2018-01-01 00:00:00+09'
              AND bucket_ts <  TIMESTAMPTZ '2024-01-01 00:00:00+09'
            GROUP BY 1 ORDER BY 1
        """)
        for row in cur.fetchall(): print('|'.join(map(str,row)))
