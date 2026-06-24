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
    print('objects')
    cur.execute("""
      SELECT table_type, table_name
      FROM information_schema.tables
      WHERE table_schema='ontology'
      ORDER BY table_type, table_name
    """)
    for row in cur.fetchall(): print('|'.join(map(str,row)))
    print('counts')
    for table in ['building','equipment_group','meter_role','hardware_model','meter','redundancy_pair','measurement_code','triple']:
        cur.execute(f'SELECT count(*) FROM ontology.{table}')
        print(f'{table}|{cur.fetchone()[0]}')
    print('integrity')
    checks = {
        'orphan_meter_group': 'SELECT count(*) FROM ontology.meter m LEFT JOIN ontology.equipment_group g USING (equipment_group_code) WHERE g.equipment_group_code IS NULL',
        'orphan_meter_building': 'SELECT count(*) FROM ontology.meter m LEFT JOIN ontology.building b USING (building_code) WHERE b.building_code IS NULL',
        'orphan_meter_role': 'SELECT count(*) FROM ontology.meter m LEFT JOIN ontology.meter_role r USING (meter_role_code) WHERE r.meter_role_code IS NULL',
        'orphan_meter_hardware': 'SELECT count(*) FROM ontology.meter m LEFT JOIN ontology.hardware_model h USING (hardware_model_code) WHERE h.hardware_model_code IS NULL',
        'orphan_redundancy_primary': 'SELECT count(*) FROM ontology.redundancy_pair rp LEFT JOIN ontology.meter m ON m.meter_urn=rp.primary_meter_urn WHERE m.meter_urn IS NULL',
        'orphan_redundancy_redundant': 'SELECT count(*) FROM ontology.redundancy_pair rp LEFT JOIN ontology.meter m ON m.meter_urn=rp.redundant_meter_urn WHERE m.meter_urn IS NULL',
        'same_redundancy_endpoint': 'SELECT count(*) FROM ontology.redundancy_pair WHERE primary_meter_urn=redundant_meter_urn',
    }
    for name, sql in checks.items():
        cur.execute(sql); print(f'{name}|{cur.fetchone()[0]}')
    print('sample_queries')
    cur.execute("SELECT meter_urn,equipment_group_code,building_code,meter_role_code,redundant_meter_urn FROM ontology.meter_context WHERE meter_urn='H2.Z64'")
    print('H2.Z64|' + '|'.join(map(str,cur.fetchone())))
    cur.execute("SELECT count(*) FROM ontology.meter WHERE equipment_group_code='server_power'")
    print('server_power_meter_count|' + str(cur.fetchone()[0]))
    cur.execute("SELECT count(*) FROM ontology.redundancy_pair WHERE equipment_group_code='server_power'")
    print('server_power_redundancy_pair_count|' + str(cur.fetchone()[0]))
    cur.execute('SELECT building_code, count(*) FROM ontology.meter GROUP BY 1 ORDER BY 1')
    for row in cur.fetchall(): print('building_count|' + '|'.join(map(str,row)))
    cur.execute('SELECT hardware_model_code, count(*) FROM ontology.meter GROUP BY 1 ORDER BY 1')
    for row in cur.fetchall(): print('hardware_count|' + '|'.join(map(str,row)))
    cur.execute("SELECT load_id,status,counts->>'meters',counts->>'measurement_codes',counts->>'ttl_triples' FROM ontology.load_log WHERE load_id='ontology_projection_20260616'")
    print('load_log|' + '|'.join(map(str,cur.fetchone())))
    print('helper_role')
    cur.execute("SELECT has_schema_privilege('cms_backend_model_results_read','ontology','USAGE')")
    print('schema_usage|' + str(cur.fetchone()[0]))
    for priv in ['SELECT','INSERT','UPDATE','DELETE']:
        cur.execute("SELECT bool_and(has_table_privilege('cms_backend_model_results_read', schemaname || '.' || tablename, %s)) FROM pg_tables WHERE schemaname='ontology'", (priv,))
        print(f'all_tables_{priv.lower()}|{cur.fetchone()[0]}')
