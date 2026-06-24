#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from cms.contracts.anomaly_detection_1h import (
    ANOMALY_DETECTION_HORIZON_HOURS,
    ANOMALY_DETECTION_MODEL_VERSION,
    ANOMALY_DETECTION_RELEASE,
    anomaly_model_urn_for_meter,
)

ROOT = Path('/workspace')
ANOMALY_ROOT = ROOT / 'artifacts/anomaly/3h'
START_TS = '2023-01-01 00:00:00+09'
CUTOFF_TS = '2023-12-01 00:00:00+09'
RUN_ID = 'preload_2023_jan_nov_20260616_anomaly'
JOB_ID = 'server_preload_2023_jan_nov'
BATCH_SIZE = 20
SLEEP_SECONDS = 0.05


def load_dotenv(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(errors='ignore').splitlines():
        s = line.strip()
        if s and not s.startswith('#') and '=' in s:
            k, v = s.split('=', 1)
            values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def db_kwargs() -> dict[str, object]:
    env = load_dotenv(ROOT / '.env')
    return {
        'host': env['DB_HOST'],
        'port': int(env.get('DB_PORT', '5432')),
        'dbname': env['DB_NAME'],
        'user': env['DB_USER'],
        'password': env['DB_PASSWORD'],
    }


def parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def prediction_files(root: Path) -> list[Path]:
    return sorted(root.glob('*/test_predictions.csv')) + sorted(root.glob('*/validation_predictions.csv'))


def make_rows(path: Path):
    start = parse_ts(START_TS)
    cutoff = parse_ts(CUTOFF_TS)
    with path.open('r', encoding='utf-8', newline='') as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            meter = row['meter_urn'].strip()
            try:
                model_urn = anomaly_model_urn_for_meter(meter)
            except Exception:
                model_urn = meter
            forecast_origin = parse_ts(row['input_end_ts'].strip())
            if forecast_origin >= cutoff:
                continue
            target_start = parse_ts(row['target_start_ts'].strip())
            for step in (1, 2, 3):
                target = target_start + timedelta(hours=step - 1)
                if not (start <= target < cutoff):
                    continue
                residual = float(row.get(f'residual_t_plus_{step}', '0') or 0.0)
                is_anomaly = str(row.get('is_anomaly', '')).strip().lower() == 'true'
                warning_type = 'high' if is_anomaly and residual > 0 else 'low' if is_anomaly and residual < 0 else 'none'
                reason = 'HIGH_LOAD_VS_USUAL_HOUR' if warning_type == 'high' else 'LOW_LOAD_VS_USUAL_HOUR' if warning_type == 'low' else 'NONE'
                warning_id = hashlib.sha1(f'{RUN_ID}:{meter}:{forecast_origin.isoformat()}:{step}'.encode('utf-8')).hexdigest()
                refs = [{
                    'artifact_path': str(path),
                    'source_mode': 'historical_preload',
                    'input_start_ts': row.get('input_start_ts'),
                    'input_end_ts': row.get('input_end_ts'),
                    'target_start_ts': row.get('target_start_ts'),
                    'target_end_ts': row.get('target_end_ts'),
                    'source': row.get('source'),
                }]
                yield (
                    warning_id, RUN_ID, 'anomaly_warning', ANOMALY_DETECTION_MODEL_VERSION,
                    ANOMALY_DETECTION_RELEASE, meter, model_urn, forecast_origin, target, step,
                    ANOMALY_DETECTION_HORIZON_HOURS,
                    float(row[f'pred_t_plus_{step}']) if row.get(f'pred_t_plus_{step}') else None,
                    None, None, is_anomaly, warning_type, 'success', False, 'good', reason,
                    Jsonb(refs), datetime.now(timezone.utc), path,
                )


def existing_keys() -> set[tuple[str, datetime, int]]:
    keys = set()
    with psycopg.connect(**db_kwargs()) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT meter_urn, forecast_origin_ts, lead_step
            FROM mart.anomaly_warning_1h
            WHERE target_ts >= %(start)s::timestamptz
              AND target_ts < %(cutoff)s::timestamptz
            """,
            {'start': START_TS, 'cutoff': CUTOFF_TS},
        )
        for meter, origin, step in cur.fetchall():
            keys.add((meter, origin, int(step)))
    return keys


def insert_batch(rows: list[tuple[object, ...]]) -> int:
    if not rows:
        return 0
    payload = [row[:-1] for row in rows]
    for attempt in range(1, 4):
        try:
            with psycopg.connect(**db_kwargs()) as conn:
                with conn.cursor() as cur:
                    cur.executemany(
                        """
                        INSERT INTO mart.anomaly_warning_1h (
                            warning_id, run_id, model_name, model_version, release_version,
                            meter_urn, model_urn, forecast_origin_ts, target_ts, lead_step,
                            horizon_hours, predicted_p, threshold_lower, threshold_upper,
                            warning_flag, warning_type, status, physical_flag, input_quality,
                            warning_reason_code, source_input_refs, created_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        ON CONFLICT (meter_urn, forecast_origin_ts, lead_step) DO NOTHING
                        """,
                        payload,
                    )
                conn.commit()
            return len(rows)
        except Exception as exc:
            print(json.dumps({'stage': 'insert_retry', 'attempt': attempt, 'rows': len(rows), 'error': type(exc).__name__, 'message': str(exc).splitlines()[0]}, sort_keys=True), flush=True)
            time.sleep(0.5 * attempt)
    # If a batch still kills the connection, fall back to one row at a time.
    inserted = 0
    for row in rows:
        payload_one = row[:-1]
        with psycopg.connect(**db_kwargs()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO mart.anomaly_warning_1h (
                        warning_id, run_id, model_name, model_version, release_version,
                        meter_urn, model_urn, forecast_origin_ts, target_ts, lead_step,
                        horizon_hours, predicted_p, threshold_lower, threshold_upper,
                        warning_flag, warning_type, status, physical_flag, input_quality,
                        warning_reason_code, source_input_refs, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (meter_urn, forecast_origin_ts, lead_step) DO NOTHING
                    """,
                    payload_one,
                )
            conn.commit()
        inserted += 1
        time.sleep(SLEEP_SECONDS)
    return inserted


def write_log(prediction_count: int, warning_count: int, meter_count: int, source_files: int) -> None:
    with psycopg.connect(**db_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ops.anomaly_log (
                    run_id, job_id, model_name, model_version, release_version,
                    forecast_origin_ts, artifact_ref, status, meter_count,
                    prediction_count, warning_count, blocked_reason, details, started_at, finished_at
                ) VALUES (
                    %(run_id)s, %(job_id)s, 'anomaly_warning', %(model_version)s, %(release_version)s,
                    %(start_ts)s::timestamptz, %(artifact_ref)s, 'success', %(meter_count)s,
                    %(prediction_count)s, %(warning_count)s, NULL, %(details)s, now(), now()
                )
                ON CONFLICT (run_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    meter_count = EXCLUDED.meter_count,
                    prediction_count = EXCLUDED.prediction_count,
                    warning_count = EXCLUDED.warning_count,
                    details = EXCLUDED.details,
                    finished_at = EXCLUDED.finished_at
                """,
                {
                    'run_id': RUN_ID,
                    'job_id': JOB_ID,
                    'model_version': ANOMALY_DETECTION_MODEL_VERSION,
                    'release_version': ANOMALY_DETECTION_RELEASE,
                    'start_ts': START_TS,
                    'artifact_ref': str(ANOMALY_ROOT),
                    'meter_count': meter_count,
                    'prediction_count': prediction_count,
                    'warning_count': warning_count,
                    'details': Jsonb({'source_mode': 'historical_preload', 'source_files': source_files, 'gap_driven': True, 'batch_size': BATCH_SIZE}),
                },
            )
        conn.commit()


def main() -> int:
    existing = existing_keys()
    files = prediction_files(ANOMALY_ROOT)
    expected = 0
    warning_count = 0
    meters = set()
    missing_rows = []
    missing_by_meter: dict[str, int] = {}
    for path in files:
        for row in make_rows(path):
            key = (row[5], row[7], row[9])
            expected += 1
            meters.add(row[5])
            if bool(row[14]):
                warning_count += 1
            if key not in existing:
                missing_rows.append(row)
                missing_by_meter[row[5]] = missing_by_meter.get(row[5], 0) + 1
    print(json.dumps({'stage': 'gap_scan', 'expected_rows': expected, 'existing_rows': len(existing), 'missing_rows': len(missing_rows), 'missing_by_meter': missing_by_meter}, sort_keys=True), flush=True)
    inserted = 0
    batch = []
    for row in missing_rows:
        batch.append(row)
        if len(batch) >= BATCH_SIZE:
            inserted += insert_batch(batch)
            print(json.dumps({'stage': 'gap_insert_batch', 'inserted_attempted': inserted, 'remaining': len(missing_rows) - inserted}, sort_keys=True), flush=True)
            batch.clear()
            time.sleep(SLEEP_SECONDS)
    if batch:
        inserted += insert_batch(batch)
    write_log(expected, warning_count, len(meters), len(files))
    print(json.dumps({'stage': 'gap_done', 'expected_rows': expected, 'missing_attempted': len(missing_rows), 'inserted_attempted': inserted, 'meter_count': len(meters), 'warning_count': warning_count}, sort_keys=True), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
