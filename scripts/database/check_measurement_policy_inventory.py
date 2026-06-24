from pathlib import Path
import os
import psycopg

vals = {}
for line in Path('/workspace/.env').read_text(errors='ignore').splitlines():
    s = line.strip()
    if s and not s.startswith('#') and '=' in s:
        k, v = s.split('=', 1)
        vals[k.strip()] = v.strip().strip('"').strip("'")
conn = psycopg.connect(host=vals['DB_HOST'], port=int(vals.get('DB_PORT','5432')), dbname=vals['DB_NAME'], user=vals['DB_USER'], password=vals['DB_PASSWORD'], connect_timeout=10)
with conn, conn.cursor() as cur:
    cur.execute("SELECT count(*), count(DISTINCT (meter_urn, measurement)) FROM live.measurement_policy")
    print('policy_rows|distinct_pairs|' + '|'.join(map(str, cur.fetchone())))
    cur.execute("SELECT count(DISTINCT meter_urn), count(DISTINCT measurement) FROM live.measurement_policy")
    print('policy_meters|measurements|' + '|'.join(map(str, cur.fetchone())))
    cur.execute("""
      SELECT count(*) FROM (
        SELECT meter_urn, measurement FROM live.measurement_policy
        EXCEPT
        SELECT meter_urn, measurement_code FROM ontology.meter CROSS JOIN ontology.measurement_code
      ) x
    """)
    print('outside_ontology_meter_x_measurement|' + str(cur.fetchone()[0]))
    cur.execute("""
      SELECT count(*) FROM live.measurement_policy p
      LEFT JOIN ontology.meter m ON m.meter_urn = p.meter_urn
      WHERE m.meter_urn IS NULL
    """)
    print('unknown_meter_rows|' + str(cur.fetchone()[0]))
    cur.execute("""
      SELECT count(*) FROM live.measurement_policy p
      LEFT JOIN ontology.measurement_code mc ON mc.measurement_code = p.measurement
      WHERE mc.measurement_code IS NULL
    """)
    print('unknown_measurement_rows|' + str(cur.fetchone()[0]))
    cur.execute("SELECT enabled, count(*) FROM live.measurement_policy GROUP BY enabled ORDER BY enabled")
    for r in cur.fetchall(): print('enabled_count|' + '|'.join(map(str, r)))
    cur.execute("SELECT source_update_mode, cadence_group, count(*) FROM live.measurement_policy GROUP BY 1,2 ORDER BY 1,2")
    for r in cur.fetchall(): print('mode_cadence_count|' + '|'.join(map(str, r)))
    cur.execute("SELECT meter_urn, measurement, source_update_mode, cadence_group, target_resolution_policy, canonical_eligible FROM live.measurement_policy ORDER BY meter_urn, measurement LIMIT 12")
    for r in cur.fetchall(): print('sample|' + '|'.join(map(str, r)))
