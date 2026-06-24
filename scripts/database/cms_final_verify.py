from __future__ import annotations

import csv
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

ROOT = Path('/workspace')
ANOMALY_ROOT = ROOT / 'artifacts/anomaly/3h'
START_TS = '2023-01-01 00:00:00+09'
CUTOFF_TS = '2023-12-01 00:00:00+09'


def load_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(errors='ignore').splitlines():
        s = line.strip()
        if s and not s.startswith('#') and '=' in s:
            k, v = s.split('=', 1)
            values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def prediction_files() -> list[Path]:
    return sorted(ANOMALY_ROOT.glob('*/test_predictions.csv')) + sorted(ANOMALY_ROOT.glob('*/validation_predictions.csv'))


def expected_anomaly_keys() -> tuple[set[tuple[str, datetime, int]], dict[str, int], int, int]:
    start = parse_ts(START_TS)
    cutoff = parse_ts(CUTOFF_TS)
    keys: set[tuple[str, datetime, int]] = set()
    by_meter: dict[str, int] = defaultdict(int)
    warning_count = 0
    source_rows = 0
    for path in prediction_files():
        with path.open('r', encoding='utf-8', newline='') as fh:
            for row in csv.DictReader(fh):
                meter = row['meter_urn'].strip()
                origin = parse_ts(row['input_end_ts'].strip())
                if origin >= cutoff:
                    continue
                target_start = parse_ts(row['target_start_ts'].strip())
                is_anomaly = str(row.get('is_anomaly', '')).strip().lower() == 'true'
                for step in (1, 2, 3):
                    target = target_start + timedelta(hours=step - 1)
                    if start <= target < cutoff:
                        key = (meter, origin, step)
                        if key not in keys:
                            keys.add(key)
                            by_meter[meter] += 1
                            if is_anomaly:
                                warning_count += 1
                        source_rows += 1
    return keys, by_meter, warning_count, source_rows


def main() -> None:
    env = load_env(ROOT / '.env')
    conn = psycopg.connect(
        host=env['DB_HOST'],
        port=int(env.get('DB_PORT', '5432')),
        dbname=env['DB_NAME'],
        user=env['DB_USER'],
        password=env['DB_PASSWORD'],
        connect_timeout=10,
    )
    expected_keys, expected_by_meter, expected_warning_count, source_rows = expected_anomaly_keys()
    with conn, conn.cursor() as cur:
        cur.execute('select current_user, current_database(), inet_server_addr()::text, inet_server_port()')
        print('session|' + '|'.join(map(str, cur.fetchone())))
        metric_queries = [
            ('pmax_rows_jan_nov', "SELECT count(*) FROM mart.pmax_forecast_15min WHERE target_ts >= %(start)s::timestamptz AND target_ts < %(cutoff)s::timestamptz"),
            ('anomaly_feature_rows_jan_nov', "SELECT count(*) FROM mart.anomaly_feature_1h WHERE bucket_ts >= %(start)s::timestamptz AND bucket_ts < %(cutoff)s::timestamptz"),
            ('anomaly_warning_rows_jan_nov', "SELECT count(*) FROM mart.anomaly_warning_1h WHERE target_ts >= %(start)s::timestamptz AND target_ts < %(cutoff)s::timestamptz"),
            ('anomaly_warning_distinct_meters', "SELECT count(DISTINCT meter_urn) FROM mart.anomaly_warning_1h WHERE target_ts >= %(start)s::timestamptz AND target_ts < %(cutoff)s::timestamptz"),
            ('dec_plus_pmax', "SELECT EXISTS(SELECT 1 FROM mart.pmax_forecast_15min WHERE target_ts >= %(cutoff)s::timestamptz OR base_ts >= %(cutoff)s::timestamptz OR actual_window_ts >= %(cutoff)s::timestamptz)"),
            ('dec_plus_feature', "SELECT EXISTS(SELECT 1 FROM mart.anomaly_feature_1h WHERE bucket_ts >= %(cutoff)s::timestamptz)"),
            ('dec_plus_warning', "SELECT EXISTS(SELECT 1 FROM mart.anomaly_warning_1h WHERE target_ts >= %(cutoff)s::timestamptz OR forecast_origin_ts >= %(cutoff)s::timestamptz)"),
            ('pmax_actual_window_contract_violations', "SELECT count(*) FROM mart.pmax_forecast_15min WHERE target_ts >= %(start)s::timestamptz AND target_ts < %(cutoff)s::timestamptz AND actual_window_ts <> target_ts - interval '15 minutes'"),
            ('pmax_negative_predictions', "SELECT count(*) FROM mart.pmax_forecast_15min WHERE target_ts >= %(start)s::timestamptz AND target_ts < %(cutoff)s::timestamptz AND predicted_p_max < 0"),
        ]
        for name, sql in metric_queries:
            cur.execute(sql, {'start': START_TS, 'cutoff': CUTOFF_TS})
            print(f'{name}|{cur.fetchone()[0]}')

        cur.execute(
            """
            SELECT meter_urn, forecast_origin_ts, lead_step
            FROM mart.anomaly_warning_1h
            WHERE target_ts >= %(start)s::timestamptz
              AND target_ts < %(cutoff)s::timestamptz
            """,
            {'start': START_TS, 'cutoff': CUTOFF_TS},
        )
        actual_keys = {(meter, origin, int(step)) for meter, origin, step in cur.fetchall()}
        missing = expected_keys - actual_keys
        extra = actual_keys - expected_keys
        print(f'anomaly_expected_unique|{len(expected_keys)}')
        print(f'anomaly_source_rows_after_filter|{source_rows}')
        print(f'anomaly_actual_unique|{len(actual_keys)}')
        print(f'anomaly_missing_keys|{len(missing)}')
        print(f'anomaly_extra_keys|{len(extra)}')
        print(f'anomaly_expected_meters|{len(expected_by_meter)}')
        print(f'anomaly_expected_warning_count|{expected_warning_count}')

        cur.execute("SELECT run_id,status,quality_status,forecast_row_count,logical_meter_count FROM ops.pmax_log WHERE run_id='preload_2023_jan_nov_20260616_pmax'")
        for row in cur.fetchall():
            print('pmax_log|' + '|'.join(map(str, row)))
        cur.execute("SELECT run_id,status,meter_count,prediction_count,warning_count,blocked_reason FROM ops.anomaly_log WHERE run_id='preload_2023_jan_nov_20260616_anomaly'")
        rows = cur.fetchall()
        if rows:
            for row in rows:
                print('anomaly_log|' + '|'.join(map(str, row)))
        else:
            print('anomaly_log|missing')


if __name__ == '__main__':
    main()
