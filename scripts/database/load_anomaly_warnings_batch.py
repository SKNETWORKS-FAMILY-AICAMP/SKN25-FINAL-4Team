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
BATCH_SIZE = 50
START_AT_FILE = '/workspace/artifacts/anomaly/3h/H1.Z16/test_predictions.csv'


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
                    warning_id,
                    RUN_ID,
                    'anomaly_warning',
                    ANOMALY_DETECTION_MODEL_VERSION,
                    ANOMALY_DETECTION_RELEASE,
                    meter,
                    model_urn,
                    forecast_origin,
                    target,
                    step,
                    ANOMALY_DETECTION_HORIZON_HOURS,
                    float(row[f'pred_t_plus_{step}']) if row.get(f'pred_t_plus_{step}') else None,
                    None,
                    None,
                    is_anomaly,
                    warning_type,
                    'success',
                    False,
                    'good',
                    reason,
                    Jsonb(refs),
                    datetime.now(timezone.utc),
                )


def insert_batch(rows: list[tuple[object, ...]]) -> int:
    if not rows:
        return 0
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
                rows,
            )
        conn.commit()
    return len(rows)


def write_log(row_count: int, source_files: int, warning_count: int, meter_count: int) -> None:
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
                    'prediction_count': row_count,
                    'warning_count': warning_count,
                    'details': Jsonb({'source_mode': 'historical_preload', 'source_files': source_files, 'cutoff_ts': CUTOFF_TS, 'batch_size': BATCH_SIZE}),
                },
            )
        conn.commit()


def main() -> int:
    files = prediction_files(ANOMALY_ROOT)
    if START_AT_FILE:
        start_index = next((idx for idx, item in enumerate(files) if str(item) == START_AT_FILE), 0)
        files = files[start_index:]
    batch: list[tuple[object, ...]] = []
    inserted = 0
    warning_count = 0
    meters: set[str] = set()
    for idx, path in enumerate(files, start=1):
        file_rows = 0
        for item in make_rows(path):
            batch.append(item)
            file_rows += 1
            meters.add(str(item[5]))
            if bool(item[14]):
                warning_count += 1
            if len(batch) >= BATCH_SIZE:
                inserted += insert_batch(batch)
                print(json.dumps({'stage': 'anomaly_warning_batch', 'inserted_rows': inserted, 'last_file': str(path)}, sort_keys=True), flush=True)
                batch.clear()
                time.sleep(0.2)
        print(json.dumps({'stage': 'anomaly_warning_file', 'file_index': idx, 'file': str(path), 'rows': file_rows}, sort_keys=True), flush=True)
    inserted += insert_batch(batch)
    write_log(inserted, len(files), warning_count, len(meters))
    print(json.dumps({'stage': 'anomaly_warning_done', 'inserted_rows': inserted, 'source_files': len(files), 'meter_count': len(meters), 'warning_count': warning_count}, sort_keys=True), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
