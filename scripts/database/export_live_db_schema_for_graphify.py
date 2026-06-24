from pathlib import Path
import psycopg

schemas = [
    'live', 'queue', 'worker', 'mart', 'reference', 'ontology', 'ops', 'serving', 'model', 'qa', 'control'
]
vals = {}
for line in Path('/workspace/.env').read_text(errors='ignore').splitlines():
    s = line.strip()
    if s and not s.startswith('#') and '=' in s:
        k, v = s.split('=', 1)
        vals[k.strip()] = v.strip().strip('"').strip("'")

out = []
out.append('# Live PostgreSQL DB Structure Snapshot for Graphify')
out.append('')
out.append('Source: live PostgreSQL catalog introspection from `cms-backend-api` container.')
out.append('Scope: table/view/materialized view structure, columns, primary/unique/index metadata, and selected row-count summaries. Secrets and connection strings are intentionally excluded.')
out.append('')

with psycopg.connect(
    host=vals['DB_HOST'],
    port=int(vals.get('DB_PORT', '5432')),
    dbname=vals['DB_NAME'],
    user=vals['DB_USER'],
    password=vals['DB_PASSWORD'],
    connect_timeout=10,
    autocommit=True,
) as conn:
    with conn.cursor() as cur:
        cur.execute("SET statement_timeout='120s'")
        cur.execute(
            """
            SELECT table_schema, table_name, table_type
            FROM information_schema.tables
            WHERE table_schema = ANY(%s)
            ORDER BY table_schema, table_name
            """,
            (schemas,),
        )
        tables = cur.fetchall()
        out.append('## Schemas and relations')
        out.append('')
        for schema, name, typ in tables:
            out.append(f'- `{schema}.{name}` ({typ})')
        out.append('')

        for schema, name, typ in tables:
            out.append(f'## `{schema}.{name}`')
            out.append('')
            out.append(f'Type: `{typ}`')
            out.append('')
            out.append('| column | data_type | nullable | default |')
            out.append('|---|---:|---:|---|')
            cur.execute(
                """
                SELECT column_name, data_type, is_nullable, COALESCE(column_default, '')
                FROM information_schema.columns
                WHERE table_schema=%s AND table_name=%s
                ORDER BY ordinal_position
                """,
                (schema, name),
            )
            for col, data_type, nullable, default in cur.fetchall():
                default = str(default).replace('|', '\\|')
                if len(default) > 120:
                    default = default[:117] + '...'
                out.append(f'| `{col}` | `{data_type}` | `{nullable}` | `{default}` |')
            out.append('')
            cur.execute(
                """
                SELECT conname, contype, pg_get_constraintdef(c.oid)
                FROM pg_constraint c
                JOIN pg_class r ON r.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = r.relnamespace
                WHERE n.nspname=%s AND r.relname=%s
                ORDER BY conname
                """,
                (schema, name),
            )
            constraints = cur.fetchall()
            if constraints:
                out.append('Constraints:')
                for conname, contype, definition in constraints:
                    out.append(f'- `{conname}` `{contype}`: `{definition}`')
                out.append('')
            cur.execute(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE schemaname=%s AND tablename=%s
                ORDER BY indexname
                """,
                (schema, name),
            )
            indexes = cur.fetchall()
            if indexes:
                out.append('Indexes:')
                for idx, idxdef in indexes:
                    idxdef = idxdef.replace('|', '\\|')
                    if len(idxdef) > 240:
                        idxdef = idxdef[:237] + '...'
                    out.append(f'- `{idx}`: `{idxdef}`')
                out.append('')

        out.append('## Selected row-count summaries')
        out.append('')
        summary_queries = [
            ('mart.pmax_forecast_15min', "SELECT count(*)::text, min(target_ts)::text, max(target_ts)::text FROM mart.pmax_forecast_15min"),
            ('mart.anomaly_warning_1h', "SELECT count(*)::text, min(target_ts)::text, max(target_ts)::text FROM mart.anomaly_warning_1h"),
            ('mart.anomaly_feature_1h', "SELECT count(*)::text, min(bucket_ts)::text, max(bucket_ts)::text FROM mart.anomaly_feature_1h"),
            ('ontology.meter', "SELECT count(*)::text, NULL::text, NULL::text FROM ontology.meter"),
            ('ontology.meter_measurement', "SELECT count(*)::text, NULL::text, NULL::text FROM ontology.meter_measurement"),
            ('ontology.measurement_code', "SELECT count(*)::text, NULL::text, NULL::text FROM ontology.measurement_code"),
            ('ontology.triple', "SELECT count(*)::text, NULL::text, NULL::text FROM ontology.triple"),
        ]
        out.append('| relation | row_count | min_ts | max_ts |')
        out.append('|---|---:|---|---|')
        for rel, sql in summary_queries:
            try:
                cur.execute(sql)
                count, min_ts, max_ts = cur.fetchone()
                out.append(f'| `{rel}` | {count} | {min_ts or ""} | {max_ts or ""} |')
            except Exception as exc:
                out.append(f'| `{rel}` | ERROR: {type(exc).__name__} | | |')
        out.append('')

Path('/tmp/live_db_schema.md').write_text('\n'.join(out) + '\n', encoding='utf-8')
print('/tmp/live_db_schema.md')
