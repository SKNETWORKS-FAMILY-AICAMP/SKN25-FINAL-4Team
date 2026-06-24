from pathlib import Path
import os, psycopg
vals={}
for line in Path('/workspace/.env').read_text(errors='ignore').splitlines():
    s=line.strip()
    if s and not s.startswith('#') and '=' in s:
        k,v=s.split('=',1); vals[k.strip()]=v.strip().strip('"').strip("'")
conn=psycopg.connect(host=vals['DB_HOST'], port=int(vals.get('DB_PORT','5432')), dbname=vals['DB_NAME'], user=vals['DB_USER'], password=vals['DB_PASSWORD'], connect_timeout=10)
with conn, conn.cursor() as cur:
    for table in ['pmax_forecast_15min','anomaly_feature_1h','anomaly_warning_1h','peak_feature_15min']:
        print('##', table)
        cur.execute("""
          SELECT column_name, data_type
          FROM information_schema.columns
          WHERE table_schema='mart' AND table_name=%s
          ORDER BY ordinal_position
        """, (table,))
        for r in cur.fetchall(): print('|'.join(r))
