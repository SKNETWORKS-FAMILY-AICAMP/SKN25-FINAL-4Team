from pathlib import Path
import psycopg
vals={}
for line in Path('/workspace/.env').read_text(errors='ignore').splitlines():
    s=line.strip()
    if s and not s.startswith('#') and '=' in s:
        k,v=s.split('=',1)
        vals[k.strip()]=v.strip().strip('"').strip("'")
conn=psycopg.connect(host=vals['DB_HOST'], port=int(vals.get('DB_PORT','5432')), dbname=vals['DB_NAME'], user=vals['DB_USER'], password=vals['DB_PASSWORD'])
with conn, conn.cursor() as cur:
    cur.execute("""
      SELECT count(*), count(DISTINCT meter_urn)
      FROM mart.anomaly_warning_1h
      WHERE target_ts >= '2023-01-01 00:00:00+09'::timestamptz
        AND target_ts < '2023-12-01 00:00:00+09'::timestamptz
    """)
    rows, meters = cur.fetchone()
    print(f'anomaly_warning_rows_jan_nov|{rows}|meters|{meters}')
