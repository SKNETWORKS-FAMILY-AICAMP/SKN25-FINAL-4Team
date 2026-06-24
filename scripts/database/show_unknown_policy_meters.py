from pathlib import Path
import os, psycopg
vals={}
for line in Path('/workspace/.env').read_text(errors='ignore').splitlines():
    s=line.strip()
    if s and not s.startswith('#') and '=' in s:
        k,v=s.split('=',1); vals[k.strip()]=v.strip().strip('"').strip("'")
conn=psycopg.connect(host=vals['DB_HOST'], port=int(vals.get('DB_PORT','5432')), dbname=vals['DB_NAME'], user=vals['DB_USER'], password=vals['DB_PASSWORD'], connect_timeout=10)
with conn, conn.cursor() as cur:
    cur.execute("""
      SELECT p.meter_urn, p.measurement, p.source_update_mode, p.cadence_group, p.target_resolution_policy
      FROM live.measurement_policy p
      LEFT JOIN ontology.meter m ON m.meter_urn=p.meter_urn
      WHERE m.meter_urn IS NULL
      ORDER BY p.meter_urn, p.measurement
    """)
    for r in cur.fetchall(): print('|'.join(map(str,r)))
