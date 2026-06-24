from pathlib import Path
import os, psycopg
vals={}
for line in Path('/workspace/.env').read_text(errors='ignore').splitlines():
    s=line.strip()
    if s and not s.startswith('#') and '=' in s:
        k,v=s.split('=',1); vals[k.strip()]=v.strip().strip('"').strip("'")
conn=psycopg.connect(host=vals['DB_HOST'], port=int(vals.get('DB_PORT','5432')), dbname=vals['DB_NAME'], user=vals['DB_USER'], password=vals['DB_PASSWORD'], connect_timeout=10)
with conn, conn.cursor() as cur:
    print('counts')
    for name, sql in {
        'meter_measurement_rows': 'select count(*) from ontology.meter_measurement',
        'meter_measurement_meters': 'select count(distinct meter_urn) from ontology.meter_measurement',
        'meter_measurement_codes': 'select count(distinct measurement_code) from ontology.meter_measurement',
        'source_policy_rows': 'select count(*) from live.measurement_policy',
        'smoke_excluded': "select count(*) from live.measurement_policy where meter_urn like 'SMOKE.%'",
    }.items():
        cur.execute(sql); print(f'{name}|{cur.fetchone()[0]}')
    print('integrity')
    checks={
        'orphan_meter': 'select count(*) from ontology.meter_measurement mm left join ontology.meter m using (meter_urn) where m.meter_urn is null',
        'orphan_measurement': 'select count(*) from ontology.meter_measurement mm left join ontology.measurement_code mc using (measurement_code) where mc.measurement_code is null',
        'duplicate_pairs': 'select count(*) from (select meter_urn, measurement_code, count(*) from ontology.meter_measurement group by 1,2 having count(*)>1) d',
        'disabled_rows': 'select count(*) from ontology.meter_measurement where enabled=false',
    }
    for name, sql in checks.items():
        cur.execute(sql); print(f'{name}|{cur.fetchone()[0]}')
    print('mode_cadence')
    cur.execute('select source_update_mode, cadence_group, count(*) from ontology.meter_measurement group by 1,2 order by 1,2')
    for row in cur.fetchall(): print('|'.join(map(str,row)))
    print('sample')
    cur.execute("select meter_urn, measurement_code, equipment_group_code, building_code, source_update_mode, cadence_group, canonical_eligible from ontology.meter_measurement_context where meter_urn='H2.Z64' order by measurement_code limit 20")
    for row in cur.fetchall(): print('|'.join(map(str,row)))
    print('load_log')
    cur.execute("select load_id,status,counts->>'source_rows',counts->>'eligible_rows',counts->>'excluded_rows',counts->>'final_rows' from ontology.load_log where load_id='ontology_meter_measurement_20260616'")
    print('|'.join(map(str, cur.fetchone())))
    print('helper_role')
    for obj in ['ontology.meter_measurement','ontology.meter_measurement_context']:
        for priv in ['SELECT','INSERT','UPDATE','DELETE']:
            cur.execute('select has_table_privilege(%s,%s,%s)', ('cms_backend_model_results_read', obj, priv))
            print(f'{obj}|{priv}|{cur.fetchone()[0]}')
