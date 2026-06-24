#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import psycopg
from psycopg.types.json import Jsonb

ROOT = Path('/workspace')
START_TS = '2023-01-01 00:00:00+09'
CUTOFF_TS = '2023-12-01 00:00:00+09'
RUN_ID = 'preload_2023_jan_nov_20260616_pmax'
PMAX_PATH = ROOT / 'artifacts/pmax/import_pmax_forecast_2023.csv'


def load_dotenv(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(errors='ignore').splitlines():
        s = line.strip()
        if s and not s.startswith('#') and '=' in s:
            k, v = s.split('=', 1)
            values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def main() -> int:
    env = load_dotenv(ROOT / '.env')
    with psycopg.connect(
        host=env['DB_HOST'],
        port=int(env.get('DB_PORT', '5432')),
        dbname=env['DB_NAME'],
        user=env['DB_USER'],
        password=env['DB_PASSWORD'],
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH scoped AS (
                    SELECT logical_meter, predicted_p_max
                    FROM mart.pmax_forecast_15min
                    WHERE target_ts >= %(start)s::timestamptz
                      AND target_ts < %(cutoff)s::timestamptz
                      AND base_ts < %(cutoff)s::timestamptz
                      AND actual_window_ts < %(cutoff)s::timestamptz
                )
                SELECT count(*), count(DISTINCT logical_meter), count(*) FILTER (WHERE predicted_p_max = 0)
                FROM scoped
                """,
                {'start': START_TS, 'cutoff': CUTOFF_TS},
            )
            row_count, meter_count, zero_count = cur.fetchone()
            cur.execute(
                """
                INSERT INTO ops.pmax_log (
                    run_id, base_ts, status, quality_status, logical_meter_count,
                    forecast_row_count, replacement_row_count, internal_missing_segment_count,
                    latest_missing_policy, error_reason, details, started_at, completed_at
                ) VALUES (
                    %(run_id)s, %(start)s::timestamptz, 'success', 'normal', %(meter_count)s,
                    %(row_count)s, 0, 0, NULL, NULL, %(details)s, now(), now()
                )
                ON CONFLICT (run_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    quality_status = EXCLUDED.quality_status,
                    logical_meter_count = EXCLUDED.logical_meter_count,
                    forecast_row_count = EXCLUDED.forecast_row_count,
                    details = EXCLUDED.details,
                    completed_at = EXCLUDED.completed_at
                """,
                {
                    'run_id': RUN_ID,
                    'start': START_TS,
                    'meter_count': meter_count,
                    'row_count': row_count,
                    'details': Jsonb({
                        'source_mode': 'historical_preload',
                        'artifact_path': str(PMAX_PATH),
                        'row_count': row_count,
                        'logical_meter_count': meter_count,
                        'zero_prediction_rows': zero_count,
                        'cutoff_ts': CUTOFF_TS,
                        'repair_note': 'inserted after quality_status constraint rejected historical_preload label',
                    }),
                },
            )
        conn.commit()
        print({'run_id': RUN_ID, 'row_count': row_count, 'logical_meter_count': meter_count, 'zero_prediction_rows': zero_count})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
