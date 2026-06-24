#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

KST = timezone(timedelta(hours=9))
LOAD_ID = 'anomaly_feature_2018_2023_split_20260616'
RANGES = [
    (datetime(2018, 1, 1, tzinfo=KST), datetime(2023, 1, 1, tzinfo=KST)),
    (datetime(2023, 12, 1, tzinfo=KST), datetime(2024, 1, 1, tzinfo=KST)),
]
MEASUREMENTS = ('P', 'U1', 'PF', 'qv', 'Tdiff')

def load_env(path: str = '/workspace/.env') -> dict[str, str]:
    vals = {}
    for line in Path(path).read_text(errors='ignore').splitlines():
        s = line.strip()
        if s and not s.startswith('#') and '=' in s:
            k, v = s.split('=', 1)
            vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals

ENV = load_env()

def connect():
    return psycopg.connect(
        host=ENV['DB_HOST'],
        port=int(ENV.get('DB_PORT', '5432')),
        dbname=ENV['DB_NAME'],
        user=ENV['DB_USER'],
        password=ENV['DB_PASSWORD'],
        connect_timeout=10,
        autocommit=False,
    )

INSERT_SQL = """
WITH pivoted AS (
    SELECT
        ts AS bucket_ts,
        meter_urn,
        max(value) FILTER (WHERE measurement = 'P') AS p_value,
        max(value) FILTER (WHERE measurement = 'U1') AS u1_value,
        max(value) FILTER (WHERE measurement = 'PF') AS pf_value,
        max(value) FILTER (WHERE measurement = 'qv') AS qv_value,
        max(value) FILTER (WHERE measurement = 'Tdiff') AS tdiff_value,
        jsonb_agg(
            jsonb_build_object(
                'source_table', 'reference.corrected_resampled_1h',
                'ts', ts,
                'meter_urn', meter_urn,
                'measurement', measurement,
                'source_file', source_file,
                'run_id', run_id
            ) ORDER BY measurement
        ) FILTER (WHERE measurement IS NOT NULL) AS source_refs
    FROM reference.corrected_resampled_1h
    WHERE ts >= %(start_ts)s
      AND ts < %(end_ts)s
      AND measurement = ANY(%(measurements)s)
    GROUP BY ts, meter_urn
), shaped AS (
    SELECT
        bucket_ts,
        meter_urn,
        CASE
            WHEN p_value IS NOT NULL AND u1_value IS NOT NULL THEN 'electric'
            WHEN p_value IS NOT NULL AND qv_value IS NOT NULL AND tdiff_value IS NOT NULL THEN 'heat'
            ELSE 'insufficient'
        END AS feature_set,
        p_value,
        u1_value,
        pf_value,
        qv_value,
        tdiff_value,
        jsonb_build_object(
            'source_mode', 'reference_backfill',
            'load_id', %(load_id)s::text,
            'hour_sin', sin((extract(hour from bucket_ts)::double precision / 24.0) * 2.0 * pi()),
            'hour_cos', cos((extract(hour from bucket_ts)::double precision / 24.0) * 2.0 * pi()),
            'day_of_week', extract(dow from bucket_ts)::integer
        ) AS derived_features,
        CASE
            WHEN p_value IS NULL THEN 'bad'
            WHEN u1_value IS NULL AND qv_value IS NULL THEN 'warning'
            ELSE 'good'
        END AS input_quality,
        COALESCE(source_refs, '[]'::jsonb) AS source_refs
    FROM pivoted
)
INSERT INTO mart.anomaly_feature_1h (
    bucket_ts, meter_urn, feature_set, p_value, u1_value, pf_value, qv_value,
    tdiff_value, derived_features, input_quality, source_refs, created_at
)
SELECT
    bucket_ts, meter_urn, feature_set, p_value, u1_value, pf_value, qv_value,
    tdiff_value, derived_features, input_quality, source_refs, now()
FROM shaped
WHERE feature_set <> 'insufficient'
ON CONFLICT (bucket_ts, meter_urn)
DO UPDATE SET
    feature_set = EXCLUDED.feature_set,
    p_value = EXCLUDED.p_value,
    u1_value = EXCLUDED.u1_value,
    pf_value = EXCLUDED.pf_value,
    qv_value = EXCLUDED.qv_value,
    tdiff_value = EXCLUDED.tdiff_value,
    derived_features = EXCLUDED.derived_features,
    input_quality = EXCLUDED.input_quality,
    source_refs = EXCLUDED.source_refs,
    created_at = EXCLUDED.created_at
RETURNING 1
"""

COUNT_SQL = """
SELECT count(*), count(DISTINCT meter_urn), min(bucket_ts), max(bucket_ts)
FROM mart.anomaly_feature_1h
WHERE bucket_ts >= TIMESTAMPTZ '2018-01-01 00:00:00+09'
  AND bucket_ts <  TIMESTAMPTZ '2024-01-01 00:00:00+09'
"""

def insert_window(start: datetime, end: datetime) -> int:
    for attempt in range(1, 4):
        try:
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SET statement_timeout = '120s'")
                    cur.execute(INSERT_SQL, {'start_ts': start, 'end_ts': end, 'measurements': list(MEASUREMENTS), 'load_id': LOAD_ID})
                    rows = len(cur.fetchall())
                conn.commit()
                return rows
        except Exception as exc:
            print(json.dumps({'stage': 'window_error', 'start': start.isoformat(), 'end': end.isoformat(), 'attempt': attempt, 'error': str(exc).splitlines()[0]}, ensure_ascii=False), flush=True)
            time.sleep(2 * attempt)
    if end - start > timedelta(hours=1):
        total = 0
        cursor = start
        while cursor < end:
            nxt = min(cursor + timedelta(hours=1), end)
            total += insert_window(cursor, nxt)
            cursor = nxt
        return total
    raise RuntimeError(f'failed window {start.isoformat()} {end.isoformat()}')

def main() -> int:
    total = 0
    for range_start, range_end in RANGES:
        day = range_start
        while day < range_end:
            nxt = min(day + timedelta(days=1), range_end)
            n = insert_window(day, nxt)
            total += n
            if day.day == 1 or n:
                print(json.dumps({'stage': 'anomaly_feature_split_batch', 'day': day.isoformat(), 'upserted_rows': n, 'total_returned': total}, ensure_ascii=False), flush=True)
            day = nxt
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(COUNT_SQL)
            count, meters, min_ts, max_ts = cur.fetchone()
            cur.execute("""
                INSERT INTO ops.anomaly_log (
                    run_id, job_id, model_name, model_version, release_version,
                    artifact_ref, status, meter_count, prediction_count, warning_count,
                    details, started_at, finished_at
                )
                VALUES (%s, %s, 'anomaly_feature_materialization', 'v84', 'split_backfill',
                        'mart.anomaly_feature_1h', 'success', %s, %s, 0,
                        %s::jsonb, now(), now())
                ON CONFLICT (run_id) DO UPDATE SET
                    status='success', meter_count=EXCLUDED.meter_count,
                    prediction_count=EXCLUDED.prediction_count, warning_count=EXCLUDED.warning_count,
                    details=EXCLUDED.details, finished_at=EXCLUDED.finished_at
            """, (LOAD_ID, LOAD_ID, meters, count, json.dumps({'source_table': 'reference.corrected_resampled_1h', 'range_start': '2018-01-01T00:00:00+09:00', 'range_end': '2024-01-01T00:00:00+09:00', 'note': 'feature backfill for 4/1/1 train/validation/test split; warning outputs are separate'})))
        conn.commit()
    print(json.dumps({'stage': 'anomaly_feature_split_done', 'count': count, 'meters': meters, 'min_ts': str(min_ts), 'max_ts': str(max_ts), 'returned_rows': total}, ensure_ascii=False), flush=True)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
