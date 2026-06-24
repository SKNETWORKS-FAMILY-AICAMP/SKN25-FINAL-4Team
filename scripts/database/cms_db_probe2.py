from pathlib import Path
import os
import psycopg

def load_env(path):
    for line in Path(path).read_text(errors='ignore').splitlines():
        s = line.strip()
        if s and not s.startswith('#') and '=' in s:
            k, v = s.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

load_env('/workspace/.env')
print('db_env_present|' + '|'.join(f'{k}={bool(os.environ.get(k))}' for k in ['DB_HOST','DB_PORT','DB_NAME','DB_USER','DB_PASSWORD']))
conn = psycopg.connect(host=os.environ['DB_HOST'], port=int(os.environ.get('DB_PORT','5432')), dbname=os.environ['DB_NAME'], user=os.environ['DB_USER'], password=os.environ['DB_PASSWORD'], connect_timeout=10)
with conn, conn.cursor() as cur:
    cur.execute('select current_user, current_database(), inet_server_addr()::text, inet_server_port()')
    print('session|' + '|'.join(map(str, cur.fetchone())))
    tests = [
        ("pmax_rows_jan_nov", "SELECT count(*) FROM mart.pmax_forecast_15min WHERE target_ts >= '2023-01-01 00:00:00+09'::timestamptz AND target_ts < '2023-12-01 00:00:00+09'::timestamptz"),
        ("anomaly_feature_rows_jan_nov", "SELECT count(*) FROM mart.anomaly_feature_1h WHERE bucket_ts >= '2023-01-01 00:00:00+09'::timestamptz AND bucket_ts < '2023-12-01 00:00:00+09'::timestamptz"),
        ("anomaly_warning_rows_jan_nov", "SELECT count(*) FROM mart.anomaly_warning_1h WHERE target_ts >= '2023-01-01 00:00:00+09'::timestamptz AND target_ts < '2023-12-01 00:00:00+09'::timestamptz"),
        ("anomaly_warning_distinct_meters", "SELECT count(DISTINCT meter_urn) FROM mart.anomaly_warning_1h WHERE target_ts >= '2023-01-01 00:00:00+09'::timestamptz AND target_ts < '2023-12-01 00:00:00+09'::timestamptz"),
        ("dec_plus_pmax", "SELECT EXISTS(SELECT 1 FROM mart.pmax_forecast_15min WHERE target_ts >= '2023-12-01 00:00:00+09'::timestamptz OR base_ts >= '2023-12-01 00:00:00+09'::timestamptz OR actual_window_ts >= '2023-12-01 00:00:00+09'::timestamptz)"),
        ("dec_plus_feature", "SELECT EXISTS(SELECT 1 FROM mart.anomaly_feature_1h WHERE bucket_ts >= '2023-12-01 00:00:00+09'::timestamptz)"),
        ("dec_plus_warning", "SELECT EXISTS(SELECT 1 FROM mart.anomaly_warning_1h WHERE target_ts >= '2023-12-01 00:00:00+09'::timestamptz)"),
    ]
    for name, sql in tests:
        try:
            cur.execute(sql)
            print(f'{name}|OK|{cur.fetchone()[0]}')
        except Exception as e:
            print(f'{name}|ERROR|{type(e).__name__}|{str(e).splitlines()[0]}')
            conn.rollback()
    print('privileges')
    for tbl in ['pmax_forecast_15min','anomaly_feature_1h','anomaly_warning_1h']:
        for priv in ['SELECT','INSERT','UPDATE']:
            try:
                cur.execute('select has_table_privilege(current_user, %s, %s)', (f'mart.{tbl}', priv))
                print(f'mart.{tbl}|{priv}|{cur.fetchone()[0]}')
            except Exception as e:
                print(f'mart.{tbl}|{priv}|ERROR|{type(e).__name__}')
                conn.rollback()
    print('per_meter_counts')
    try:
        cur.execute("""
          SELECT replace(meter_urn,'urn:ngsi-ld:Meter:','') AS meter, count(*)
          FROM mart.anomaly_warning_1h
          WHERE target_ts >= '2023-01-01 00:00:00+09'::timestamptz AND target_ts < '2023-12-01 00:00:00+09'::timestamptz
          GROUP BY 1 ORDER BY 1
        """)
        for meter, count in cur.fetchall(): print(f'{meter}|{count}')
    except Exception as e:
        print(f'per_meter_counts|ERROR|{type(e).__name__}|{str(e).splitlines()[0]}')
        conn.rollback()
