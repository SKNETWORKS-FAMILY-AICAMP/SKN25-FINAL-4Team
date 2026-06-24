from pathlib import Path
import os
import psycopg

def load_env(path):
    values = {}
    for line in Path(path).read_text(errors='ignore').splitlines():
        s = line.strip()
        if s and not s.startswith('#') and '=' in s:
            k, v = s.split('=', 1)
            values[k.strip()] = v.strip().strip('"').strip("'")
    return values

e = load_env('/workspace/.env')
conn = psycopg.connect(host=e['DB_HOST'], port=int(e.get('DB_PORT','5432')), dbname=e['DB_NAME'], user=e['DB_USER'], password=e['DB_PASSWORD'], connect_timeout=10)
with conn, conn.cursor() as cur:
    checks = [
        ('pmax_actual_window_contract_violations', "SELECT count(*) FROM mart.pmax_forecast_15min WHERE target_ts >= '2023-01-01 00:00:00+09'::timestamptz AND target_ts < '2023-12-01 00:00:00+09'::timestamptz AND actual_window_ts <> target_ts - interval '15 minutes'"),
        ('pmax_negative_predictions', "SELECT count(*) FROM mart.pmax_forecast_15min WHERE target_ts >= '2023-01-01 00:00:00+09'::timestamptz AND target_ts < '2023-12-01 00:00:00+09'::timestamptz AND predicted_p_max < 0"),
        ('warning_duplicate_keys', "SELECT count(*) FROM (SELECT meter_urn, forecast_origin_ts, lead_step, count(*) c FROM mart.anomaly_warning_1h WHERE target_ts >= '2023-01-01 00:00:00+09'::timestamptz AND target_ts < '2023-12-01 00:00:00+09'::timestamptz GROUP BY 1,2,3 HAVING count(*) > 1) d"),
    ]
    for name, sql in checks:
        cur.execute(sql)
        print(f'{name}|{cur.fetchone()[0]}')
