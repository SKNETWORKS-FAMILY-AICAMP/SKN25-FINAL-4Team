from pathlib import Path
import os
import importlib.util
import psycopg

vals = {}
for line in Path('/workspace/.env').read_text(errors='ignore').splitlines():
    s = line.strip()
    if s and not s.startswith('#') and '=' in s:
        k, v = s.split('=', 1)
        vals[k.strip()] = v.strip().strip('"').strip("'")
print('env_keys|' + '|'.join(f'{k}={bool(vals.get(k))}' for k in ['DB_HOST','DB_PORT','DB_NAME','DB_USER','DB_PASSWORD']))
print('rdflib_installed|' + str(importlib.util.find_spec('rdflib') is not None))
conn = psycopg.connect(host=vals['DB_HOST'], port=int(vals.get('DB_PORT','5432')), dbname=vals['DB_NAME'], user=vals['DB_USER'], password=vals['DB_PASSWORD'], connect_timeout=10)
with conn, conn.cursor() as cur:
    cur.execute('select current_user, current_database()')
    print('session|' + '|'.join(map(str, cur.fetchone())))
    checks = [
        ('ontology_schema_exists', "select exists(select 1 from information_schema.schemata where schema_name='ontology')"),
        ('metadata_tables', "select count(*) from information_schema.tables where table_schema='cms_metadata' and table_name = any(array['meter_definition','meter_redundancy','meter_hardware_model','meter_hardware_assignment'])"),
        ('meter_definition', 'select count(*) from cms_metadata.meter_definition'),
        ('meter_redundancy', 'select count(*) from cms_metadata.meter_redundancy'),
        ('hardware_model', 'select count(*) from cms_metadata.meter_hardware_model'),
        ('hardware_assignment', 'select count(*) from cms_metadata.meter_hardware_assignment'),
    ]
    for name, sql in checks:
        cur.execute(sql)
        print(f'{name}|{cur.fetchone()[0]}')
    cur.execute('select meter_domain, count(*) from cms_metadata.meter_definition group by 1 order by 1')
    for r in cur.fetchall(): print('domain|' + '|'.join(map(str, r)))
    cur.execute('select building_code, count(*) from cms_metadata.meter_definition group by 1 order by 1')
    for r in cur.fetchall(): print('building|' + '|'.join(map(str, r)))
    cur.execute('select primary_meter_urn, redundant_meter_urn, equipment_group from cms_metadata.meter_redundancy order by primary_meter_urn, redundant_meter_urn')
    for r in cur.fetchall(): print('redundancy|' + '|'.join(map(str, r)))
