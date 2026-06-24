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
conn = psycopg.connect(
    host=os.environ['DB_HOST'],
    port=int(os.environ.get('DB_PORT','5432')),
    dbname=os.environ['DB_NAME'],
    user=os.environ['DB_USER'],
    password=os.environ['DB_PASSWORD'],
    connect_timeout=10,
)
with conn, conn.cursor() as cur:
    cur.execute("select version()")
    print('db_version|' + cur.fetchone()[0].split(',')[0])
    queries = [
        ("pmax_rows_jan_nov", "SELECT count(*) FROM mart.pmax_forecast_15min WHERE target_ts >= '2023-01-01 00:00:00+09'::timestamptz AND target_ts < '2023-12-01 00:00:00+09'::timestamptz"),
        ("anomaly_feature_rows_jan_nov", "SELECT count(*) FROM mart.anomaly_feature_1h WHERE bucket_ts >= '2023-01-01 00:00:00+09'::timestamptz AND bucket_ts < '2023-12-01 00:00:00+09'::timestamptz"),
        ("anomaly_warning_rows_jan_nov", "SELECT count(*) FROM mart.anomaly_warning_1h WHERE target_ts >= '2023-01-01 00:00:00+09'::timestamptz AND target_ts < '2023-12-01 00:00:00+09'::timestamptz"),
        ("anomaly_warning_distinct_meters", "SELECT count(DISTINCT meter_urn) FROM mart.anomaly_warning_1h WHERE target_ts >= '2023-01-01 00:00:00+09'::timestamptz AND target_ts < '2023-12-01 00:00:00+09'::timestamptz"),
        ("dec_plus_pmax", "SELECT EXISTS(SELECT 1 FROM mart.pmax_forecast_15min WHERE target_ts >= '2023-12-01 00:00:00+09'::timestamptz OR base_ts >= '2023-12-01 00:00:00+09'::timestamptz OR actual_window_ts >= '2023-12-01 00:00:00+09'::timestamptz)"),
        ("dec_plus_feature", "SELECT EXISTS(SELECT 1 FROM mart.anomaly_feature_1h WHERE bucket_ts >= '2023-12-01 00:00:00+09'::timestamptz)"),
        ("dec_plus_warning", "SELECT EXISTS(SELECT 1 FROM mart.anomaly_warning_1h WHERE target_ts >= '2023-12-01 00:00:00+09'::timestamptz)"),
    ]
    for name, sql in queries:
        cur.execute(sql)
        print(f'{name}|{cur.fetchone()[0]}')
    print('per_meter_counts')
    cur.execute("""
      SELECT replace(meter_urn,'urn:ngsi-ld:Meter:','') AS meter, count(*)
      FROM mart.anomaly_warning_1h
      WHERE target_ts >= '2023-01-01 00:00:00+09'::timestamptz AND target_ts < '2023-12-01 00:00:00+09'::timestamptz
      GROUP BY 1 ORDER BY 1
    """)
    for meter, count in cur.fetchall():
        print(f'{meter}|{count}')
    print('logs')
    cur.execute("SELECT run_id,status,quality_status,forecast_row_count,logical_meter_count FROM ops.pmax_log WHERE run_id='preload_2023_jan_nov_20260616_pmax'")
    for row in cur.fetchall(): print('|'.join(map(str,row)))
    cur.execute("SELECT to_regclass('ops.anomaly_log')")
    if cur.fetchone()[0]:
        cur.execute("SELECT run_id,status,meter_count,prediction_count,warning_count FROM ops.anomaly_log WHERE run_id='preload_2023_jan_nov_20260616_anomaly'")
        rows=cur.fetchall()
        if rows:
            for row in rows: print('|'.join(map(str,row)))
        else:
            print('anomaly_log|missing_run')
