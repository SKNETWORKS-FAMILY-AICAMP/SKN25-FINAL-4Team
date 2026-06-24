#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import psycopg

ROOT = Path('/workspace')
START = '2023-11-30 18:00:00+09'
END = '2023-12-01 00:00:00+09'


def load_dotenv(path: Path) -> dict[str, str]:
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

SQL = """
WITH candidate_buckets AS (
    SELECT DISTINCT ts AS bucket_ts, meter_urn
    FROM reference.corrected_resampled_1h
    WHERE ts >= %(start)s::timestamptz AND ts < %(end)s::timestamptz
), pivoted AS (
    SELECT
        c.bucket_ts,
        c.meter_urn,
        max(src.value) FILTER (WHERE src.measurement = 'P') AS p_value,
        max(src.value) FILTER (WHERE src.measurement = 'U1') AS u1_value,
        max(src.value) FILTER (WHERE src.measurement = 'PF') AS pf_value,
        max(src.value) FILTER (WHERE src.measurement = 'qv') AS qv_value,
        max(src.value) FILTER (WHERE src.measurement = 'Tdiff') AS tdiff_value,
        jsonb_agg(jsonb_build_object(
            'source_table', 'reference.corrected_resampled_1h',
            'ts', src.ts,
            'meter_urn', src.meter_urn,
            'measurement', src.measurement,
            'source_file', src.source_file,
            'run_id', src.run_id
        ) ORDER BY src.measurement) FILTER (WHERE src.measurement IS NOT NULL) AS source_refs
    FROM candidate_buckets AS c
    JOIN reference.corrected_resampled_1h AS src
      ON src.ts = c.bucket_ts AND src.meter_urn = c.meter_urn
     AND src.measurement IN ('P', 'U1', 'PF', 'qv', 'Tdiff')
    GROUP BY c.bucket_ts, c.meter_urn
), shaped AS (
    SELECT
        bucket_ts,
        meter_urn,
        CASE
            WHEN p_value IS NOT NULL AND u1_value IS NOT NULL THEN 'electric'
            WHEN p_value IS NOT NULL AND qv_value IS NOT NULL AND tdiff_value IS NOT NULL THEN 'heat'
            ELSE 'insufficient'
        END AS feature_set,
        p_value, u1_value, pf_value, qv_value, tdiff_value,
        jsonb_build_object(
            'source_mode', 'historical_preload',
            'source_table', 'reference.corrected_resampled_1h',
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
), upserted AS (
    INSERT INTO mart.anomaly_feature_1h (
        bucket_ts, meter_urn, feature_set, p_value, u1_value, pf_value,
        qv_value, tdiff_value, derived_features, input_quality, source_refs, created_at
    )
    SELECT bucket_ts, meter_urn, feature_set, p_value, u1_value, pf_value,
           qv_value, tdiff_value, derived_features, input_quality, source_refs, now()
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
)
SELECT count(*) FROM upserted
"""


def main() -> int:
    env = load_dotenv(ROOT / '.env')
    current = parse_ts(START)
    cutoff = parse_ts(END)
    total = 0
    while current < cutoff:
        end = min(current + timedelta(hours=1), cutoff)
        with psycopg.connect(host=env['DB_HOST'], port=int(env.get('DB_PORT', '5432')), dbname=env['DB_NAME'], user=env['DB_USER'], password=env['DB_PASSWORD']) as conn:
            with conn.cursor() as cur:
                cur.execute(SQL, {'start': current.isoformat(), 'end': end.isoformat()})
                count = cur.fetchone()[0]
            conn.commit()
        total += count
        print(json.dumps({'stage': 'anomaly_feature_range', 'start': current.isoformat(), 'end': end.isoformat(), 'upserted_rows': count}, sort_keys=True), flush=True)
        current = end
    print(json.dumps({'stage': 'anomaly_feature_nov30_done', 'upserted_rows': total}, sort_keys=True), flush=True)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
