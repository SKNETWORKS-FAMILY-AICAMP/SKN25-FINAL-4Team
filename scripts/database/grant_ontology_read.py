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
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname='cms_backend_model_results_read'")
    role_exists = cur.fetchone() is not None
    print(f'role_exists|cms_backend_model_results_read|{role_exists}')
    if role_exists:
        cur.execute('GRANT USAGE ON SCHEMA ontology TO cms_backend_model_results_read')
        cur.execute('GRANT SELECT ON ALL TABLES IN SCHEMA ontology TO cms_backend_model_results_read')
        cur.execute('ALTER DEFAULT PRIVILEGES IN SCHEMA ontology GRANT SELECT ON TABLES TO cms_backend_model_results_read')
        conn.commit()
        for priv in ['SELECT','INSERT','UPDATE','DELETE']:
            cur.execute("SELECT bool_and(has_table_privilege(%s, schemaname || '.' || tablename, %s)) FROM pg_tables WHERE schemaname=%s", ('cms_backend_model_results_read', priv, 'ontology'))
            print(f'all_tables_priv|{priv}|{cur.fetchone()[0]}')
        cur.execute("SELECT has_schema_privilege('cms_backend_model_results_read','ontology','USAGE')")
        print(f'schema_usage|{cur.fetchone()[0]}')
