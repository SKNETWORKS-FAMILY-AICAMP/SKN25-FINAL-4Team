from pathlib import Path
import psycopg
vals={}
for line in Path('/workspace/.env').read_text(errors='ignore').splitlines():
    s=line.strip()
    if s and not s.startswith('#') and '=' in s:
        k,v=s.split('=',1); vals[k.strip()]=v.strip().strip('"').strip("'")
with psycopg.connect(host=vals['DB_HOST'], port=int(vals.get('DB_PORT','5432')), dbname=vals['DB_NAME'], user=vals['DB_USER'], password=vals['DB_PASSWORD'], connect_timeout=10, autocommit=True) as conn:
  with conn.cursor() as cur:
    cur.execute("SET statement_timeout='90s'")
    print('## warning_columns')
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='mart' AND table_name='anomaly_warning_1h' ORDER BY ordinal_position")
    print('|'.join(r[0] for r in cur.fetchall()))
    print('## warning_rows')
    cur.execute("SELECT count(*), count(DISTINCT meter_urn), min(target_ts), max(target_ts) FROM mart.anomaly_warning_1h")
    for row in cur.fetchall(): print('|'.join('' if v is None else str(v) for v in row))
    print('## warning_status_counts')
    cur.execute("SELECT warning_status, count(*) FROM mart.anomaly_warning_1h GROUP BY 1 ORDER BY 1")
    for row in cur.fetchall(): print('|'.join('' if v is None else str(v) for v in row))
    print('## warning_dec_any')
    cur.execute("SELECT EXISTS(SELECT 1 FROM mart.anomaly_warning_1h WHERE target_ts >= TIMESTAMPTZ '2023-12-01 00:00:00+09')")
    print(cur.fetchone()[0])
    print('## warning_dup_key')
    cur.execute("SELECT count(*) FROM (SELECT meter_urn, forecast_origin_ts, lead_step, count(*) c FROM mart.anomaly_warning_1h GROUP BY 1,2,3 HAVING count(*)>1) d")
    print(cur.fetchone()[0])
    print('## ontology_counts')
    cur.execute("SELECT 'meter', count(*) FROM ontology.meter UNION ALL SELECT 'meter_measurement', count(*) FROM ontology.meter_measurement UNION ALL SELECT 'measurement_code', count(*) FROM ontology.measurement_code UNION ALL SELECT 'triple', count(*) FROM ontology.triple ORDER BY 1")
    for row in cur.fetchall(): print('|'.join(map(str,row)))
    print('## ontology_log')
    cur.execute("SELECT load_id, status FROM ontology.load_log ORDER BY loaded_at DESC LIMIT 3")
    for row in cur.fetchall(): print('|'.join(map(str,row)))
