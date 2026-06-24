-- Gate 28 read-only verification gate for dedicated model-serving runtime roles.
--
-- Run after the Gate 28 role proposal is applied in an approved environment:
--   psql "$DATABASE_URL" -f scripts/database/verify/gate28_model_serving_runtime_privilege_check.sql
--
-- This script is intentionally read-only: catalog SELECTs only, no temp tables,
-- no DDL, no GRANT/REVOKE, no writes, and no secrets.

\set ON_ERROR_STOP on

WITH
managed_schemas(schema_name) AS (
    VALUES ('live'), ('mart'), ('ops'), ('qa'), ('reference'), ('canonical')
),
roles(role_name, role_oid) AS (
    SELECT role_name, to_regrole(role_name) AS role_oid
    FROM (VALUES
        ('cms_model_serving_runtime'),
        ('cms_model_serving_reference_read')
    ) AS v(role_name)
),
expected_schema_privileges(role_name, schema_name, privilege) AS (
    VALUES
        ('cms_model_serving_runtime', 'mart', 'USAGE'),
        ('cms_model_serving_runtime', 'ops', 'USAGE'),
        ('cms_model_serving_runtime', 'qa', 'USAGE'),
        ('cms_model_serving_reference_read', 'reference', 'USAGE')
),
expected_table_privileges(role_name, schema_name, table_name, privilege) AS (
    VALUES
        ('cms_model_serving_runtime', 'mart', 'peak_feature_15min', 'SELECT'),
        ('cms_model_serving_runtime', 'mart', 'anomaly_feature_1h', 'SELECT'),
        ('cms_model_serving_runtime', 'mart', 'pmax_forecast_15min', 'SELECT'),
        ('cms_model_serving_runtime', 'mart', 'pmax_forecast_15min', 'INSERT'),
        ('cms_model_serving_runtime', 'mart', 'pmax_forecast_15min', 'UPDATE'),
        ('cms_model_serving_runtime', 'mart', 'anomaly_warning_1h', 'SELECT'),
        ('cms_model_serving_runtime', 'mart', 'anomaly_warning_1h', 'INSERT'),
        ('cms_model_serving_runtime', 'mart', 'anomaly_warning_1h', 'UPDATE'),
        ('cms_model_serving_runtime', 'ops', 'pmax_log', 'SELECT'),
        ('cms_model_serving_runtime', 'ops', 'pmax_log', 'INSERT'),
        ('cms_model_serving_runtime', 'ops', 'pmax_log', 'UPDATE'),
        ('cms_model_serving_runtime', 'ops', 'anomaly_log', 'SELECT'),
        ('cms_model_serving_runtime', 'ops', 'anomaly_log', 'INSERT'),
        ('cms_model_serving_runtime', 'ops', 'anomaly_log', 'UPDATE'),
        ('cms_model_serving_runtime', 'qa', 'pmax_eval', 'SELECT'),
        ('cms_model_serving_runtime', 'qa', 'pmax_eval', 'INSERT'),
        ('cms_model_serving_runtime', 'qa', 'pmax_eval', 'UPDATE'),
        ('cms_model_serving_runtime', 'qa', 'anomaly_eval', 'SELECT'),
        ('cms_model_serving_runtime', 'qa', 'anomaly_eval', 'INSERT'),
        ('cms_model_serving_runtime', 'qa', 'anomaly_eval', 'UPDATE'),
        ('cms_model_serving_runtime', 'qa', 'serving_evidence', 'SELECT'),
        ('cms_model_serving_runtime', 'qa', 'serving_evidence', 'INSERT'),
        ('cms_model_serving_runtime', 'qa', 'serving_evidence', 'UPDATE'),
        ('cms_model_serving_reference_read', 'reference', 'corrected_15min', 'SELECT'),
        ('cms_model_serving_reference_read', 'reference', 'corrected_1h', 'SELECT')
),
all_checked_table_privileges(privilege) AS (
    VALUES ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE'), ('TRUNCATE'), ('REFERENCES'), ('TRIGGER')
),
all_checked_schema_privileges(privilege) AS (
    VALUES ('USAGE'), ('CREATE')
),
managed_relations AS (
    SELECT n.nspname AS schema_name, c.relname AS table_name, c.oid AS relation_oid
    FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    JOIN managed_schemas AS ms ON ms.schema_name = n.nspname
    WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
),
managed_namespaces AS (
    SELECT n.nspname AS schema_name, n.oid AS namespace_oid
    FROM pg_namespace AS n
    JOIN managed_schemas AS ms ON ms.schema_name = n.nspname
),
expected_table_checks AS (
    SELECT
        'expected_table_privilege:' || e.role_name || ':' || e.schema_name || '.' || e.table_name || ':' || e.privilege AS check_name,
        CASE
            WHEN r.role_oid IS NULL THEN false
            WHEN to_regclass(format('%I.%I', e.schema_name, e.table_name)) IS NULL THEN false
            ELSE has_table_privilege(r.role_oid, to_regclass(format('%I.%I', e.schema_name, e.table_name)), e.privilege)
        END AS pass,
        jsonb_build_object(
            'role_exists', r.role_oid IS NOT NULL,
            'relation_exists', to_regclass(format('%I.%I', e.schema_name, e.table_name)) IS NOT NULL,
            'role', e.role_name,
            'relation', e.schema_name || '.' || e.table_name,
            'privilege', e.privilege
        ) AS detail
    FROM expected_table_privileges AS e
    JOIN roles AS r ON r.role_name = e.role_name
),
expected_schema_checks AS (
    SELECT
        'expected_schema_privilege:' || e.role_name || ':' || e.schema_name || ':' || e.privilege AS check_name,
        CASE
            WHEN r.role_oid IS NULL THEN false
            WHEN n.namespace_oid IS NULL THEN false
            ELSE has_schema_privilege(r.role_oid, n.namespace_oid, e.privilege)
        END AS pass,
        jsonb_build_object(
            'role_exists', r.role_oid IS NOT NULL,
            'schema_exists', n.namespace_oid IS NOT NULL,
            'role', e.role_name,
            'schema', e.schema_name,
            'privilege', e.privilege
        ) AS detail
    FROM expected_schema_privileges AS e
    JOIN roles AS r ON r.role_name = e.role_name
    LEFT JOIN managed_namespaces AS n ON n.schema_name = e.schema_name
),
role_property_checks AS (
    SELECT
        'role_property:' || r.role_name || ':nologin_no_admin_bits' AS check_name,
        pg_roles.rolname IS NOT NULL
            AND pg_roles.rolcanlogin IS false
            AND pg_roles.rolsuper IS false
            AND pg_roles.rolcreaterole IS false
            AND pg_roles.rolcreatedb IS false
            AND pg_roles.rolreplication IS false
            AND pg_roles.rolbypassrls IS false AS pass,
        jsonb_build_object(
            'role_exists', pg_roles.rolname IS NOT NULL,
            'rolcanlogin', COALESCE(pg_roles.rolcanlogin, false),
            'rolsuper', COALESCE(pg_roles.rolsuper, false),
            'rolcreaterole', COALESCE(pg_roles.rolcreaterole, false),
            'rolcreatedb', COALESCE(pg_roles.rolcreatedb, false),
            'rolreplication', COALESCE(pg_roles.rolreplication, false),
            'rolbypassrls', COALESCE(pg_roles.rolbypassrls, false)
        ) AS detail
    FROM roles AS r
    LEFT JOIN pg_roles ON pg_roles.rolname = r.role_name
),
actual_table_privileges AS (
    SELECT r.role_name, mr.schema_name, mr.table_name, p.privilege
    FROM roles AS r
    CROSS JOIN managed_relations AS mr
    CROSS JOIN all_checked_table_privileges AS p
    WHERE r.role_oid IS NOT NULL
      AND has_table_privilege(r.role_oid, mr.relation_oid, p.privilege)
),
forbidden_table_privileges AS (
    SELECT atp.*
    FROM actual_table_privileges AS atp
    WHERE NOT EXISTS (
        SELECT 1
        FROM expected_table_privileges AS e
        WHERE e.role_name = atp.role_name
          AND e.schema_name = atp.schema_name
          AND e.table_name = atp.table_name
          AND e.privilege = atp.privilege
    )
),
actual_schema_privileges AS (
    SELECT r.role_name, mn.schema_name, p.privilege
    FROM roles AS r
    CROSS JOIN managed_namespaces AS mn
    CROSS JOIN all_checked_schema_privileges AS p
    WHERE r.role_oid IS NOT NULL
      AND has_schema_privilege(r.role_oid, mn.namespace_oid, p.privilege)
),
forbidden_schema_privileges AS (
    SELECT asp.*
    FROM actual_schema_privileges AS asp
    WHERE NOT EXISTS (
        SELECT 1
        FROM expected_schema_privileges AS e
        WHERE e.role_name = asp.role_name
          AND e.schema_name = asp.schema_name
          AND e.privilege = asp.privilege
    )
),
schema_create_findings AS (
    SELECT r.role_name, n.nspname AS schema_name
    FROM roles AS r
    CROSS JOIN pg_namespace AS n
    WHERE r.role_oid IS NOT NULL
      AND n.nspname <> 'information_schema'
      AND n.nspname NOT LIKE 'pg_%'
      AND has_schema_privilege(r.role_oid, n.oid, 'CREATE')
),
default_acl_findings AS (
    SELECT
        r.role_name,
        COALESCE(n.nspname, '<global>') AS schema_name,
        d.defaclobjtype,
        x.privilege_type,
        CASE WHEN x.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) END AS grantee
    FROM pg_default_acl AS d
    LEFT JOIN pg_namespace AS n ON n.oid = d.defaclnamespace
    JOIN managed_schemas AS ms ON ms.schema_name = n.nspname
    CROSS JOIN roles AS r
    CROSS JOIN LATERAL aclexplode(d.defaclacl) AS x
    WHERE r.role_oid IS NOT NULL
      AND (x.grantee = r.role_oid OR x.grantee = 0)
),
aggregate_checks AS (
    SELECT
        'forbidden_table_privileges:no_extra_no_canonical_no_delete' AS check_name,
        COUNT(*) = 0 AS pass,
        COALESCE(jsonb_agg(to_jsonb(forbidden_table_privileges) ORDER BY role_name, schema_name, table_name, privilege), '[]'::jsonb) AS detail
    FROM forbidden_table_privileges
    UNION ALL
    SELECT
        'forbidden_schema_privileges:no_broad_create_or_extra_usage' AS check_name,
        COUNT(*) = 0 AS pass,
        COALESCE(jsonb_agg(to_jsonb(forbidden_schema_privileges) ORDER BY role_name, schema_name, privilege), '[]'::jsonb) AS detail
    FROM forbidden_schema_privileges
    UNION ALL
    SELECT
        'schema_create_privileges:no_create_on_any_non_system_schema' AS check_name,
        COUNT(*) = 0 AS pass,
        COALESCE(jsonb_agg(to_jsonb(schema_create_findings) ORDER BY role_name, schema_name), '[]'::jsonb) AS detail
    FROM schema_create_findings
    UNION ALL
    SELECT
        'default_privileges:no_managed_schema_defaults_to_serving_roles_or_public' AS check_name,
        COUNT(*) = 0 AS pass,
        COALESCE(jsonb_agg(to_jsonb(default_acl_findings) ORDER BY role_name, schema_name, defaclobjtype, privilege_type), '[]'::jsonb) AS detail
    FROM default_acl_findings
),
base_checks AS (
    SELECT * FROM role_property_checks
    UNION ALL SELECT * FROM expected_schema_checks
    UNION ALL SELECT * FROM expected_table_checks
    UNION ALL SELECT * FROM aggregate_checks
),
summary AS (
    SELECT
        'gate28_model_serving_runtime_privilege_boundary' AS check_name,
        bool_and(pass) AS pass,
        jsonb_build_object(
            'passed_checks', COUNT(*) FILTER (WHERE pass),
            'failed_checks', COUNT(*) FILTER (WHERE NOT pass),
            'runtime_role', 'cms_model_serving_runtime',
            'reference_read_role', 'cms_model_serving_reference_read',
            'canonical_writes_allowed', false,
            'ordinary_delete_allowed', false,
            'broad_schema_create_allowed', false,
            'default_privileges_expected', false
        ) AS detail
    FROM base_checks
)
SELECT check_name, CASE WHEN pass THEN 'PASS' ELSE 'FAIL' END AS status, detail
FROM base_checks
UNION ALL
SELECT check_name, CASE WHEN pass THEN 'PASS' ELSE 'FAIL' END AS status, detail
FROM summary
ORDER BY status, check_name;
