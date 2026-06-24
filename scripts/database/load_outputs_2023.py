#!/usr/bin/env python3
"""Server-side Jan-Nov 2023 anomaly/P-Max preload loader.

Runs inside the CMS server container, not from the local Hermes VM. It writes only
Jan-Nov 2023 active rows and excludes Dec+ live-replay holdout rows.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Any

import requests
from psycopg import connect
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from cms.contracts.anomaly_detection_1h import (
    ANOMALY_DETECTION_HORIZON_HOURS,
    ANOMALY_DETECTION_MODEL_VERSION,
    ANOMALY_DETECTION_RELEASE,
    anomaly_model_urn_for_meter,
)

START_TS = "2023-01-01 00:00:00+09"
CUTOFF_TS = "2023-12-01 00:00:00+09"
PMAX_FILE_ID = "1w_wIdj_LTrH6JDxA5x6D-oxST0y9H-oS"
RUN_ID = "preload_2023_jan_nov_20260616"
JOB_ID = "server_preload_2023_jan_nov"
ROOT = Path(__file__).resolve().parents[2]
PMAX_PATH = ROOT / "artifacts" / "pmax" / "import_pmax_forecast_2023.csv"
ANOMALY_ROOT = ROOT / "artifacts" / "anomaly" / "3h"
REPORT_PATH = ROOT / "reports" / "model_serving" / "load_outputs_2023_report.json"


def load_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def env_value(key: str, fallback_key: str | None = None) -> str | None:
    dotenv = load_dotenv(ROOT / ".env")
    return dotenv.get(key) or os.environ.get(key) or (dotenv.get(fallback_key) if fallback_key else None) or (os.environ.get(fallback_key) if fallback_key else None)


def db_kwargs() -> dict[str, Any]:
    host = env_value("DB_HOST", "POSTGRES_HOST")
    port = env_value("DB_PORT", "POSTGRES_PORT") or "5432"
    dbname = env_value("DB_NAME", "POSTGRES_DB")
    user = env_value("DB_USER", "POSTGRES_USER")
    password = env_value("DB_PASSWORD", "POSTGRES_PASSWORD")
    missing = [name for name, value in {"host": host, "dbname": dbname, "user": user, "password": password}.items() if not value]
    if missing:
        raise SystemExit(f"missing DB environment values: {','.join(missing)}")
    return {"host": host, "port": int(port), "dbname": dbname, "user": user, "password": password}


def download_pmax(path: Path, file_id: str) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return {"downloaded": False, "path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}
    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    with requests.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    fh.write(chunk)
        tmp.replace(path)
    return {"downloaded": True, "path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def insert_pmax_batch(conn, rows: list[tuple[object, ...]]) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO mart.pmax_forecast_15min (
                logical_meter, source_meter_urn, base_ts, input_end_ts, target_ts,
                actual_window_ts, horizon_minutes, predicted_p_max, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (logical_meter, base_ts, target_ts)
            DO UPDATE SET
                source_meter_urn = EXCLUDED.source_meter_urn,
                input_end_ts = EXCLUDED.input_end_ts,
                actual_window_ts = EXCLUDED.actual_window_ts,
                horizon_minutes = EXCLUDED.horizon_minutes,
                predicted_p_max = EXCLUDED.predicted_p_max,
                created_at = EXCLUDED.created_at
            """,
            rows,
        )
    conn.commit()


def load_pmax(conn, path: Path) -> dict[str, object]:
    start = parse_ts(START_TS)
    cutoff = parse_ts(CUTOFF_TS)
    source_rows = 0
    active_rows = 0
    clipped_negative_rows = 0
    logical_meters: set[str] = set()
    batch: list[tuple[object, ...]] = []
    batch_size = 5000
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            source_rows += 1
            base_ts = parse_ts(row["base_ts"])
            input_end_ts = parse_ts(row["input_end_ts"])
            target_ts = parse_ts(row["target_ts"])
            actual_window_ts = target_ts - timedelta(minutes=15)
            if not (start <= target_ts < cutoff and base_ts < cutoff and input_end_ts < cutoff and actual_window_ts < cutoff):
                continue
            predicted = float(row["predicted_p_max"])
            if predicted < 0:
                predicted = 0.0
                clipped_negative_rows += 1
            created_at = parse_ts(row["created_at"]) if row.get("created_at") else datetime.now(timezone.utc)
            logical_meters.add(row["logical_meter"])
            batch.append((
                row["logical_meter"],
                row["source_meter_urn"],
                base_ts,
                input_end_ts,
                target_ts,
                actual_window_ts,
                int(row["horizon_minutes"]),
                predicted,
                created_at,
            ))
            active_rows += 1
            if len(batch) >= batch_size:
                insert_pmax_batch(conn, batch)
                batch.clear()
        insert_pmax_batch(conn, batch)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ops.pmax_log (
                run_id, base_ts, status, quality_status, logical_meter_count,
                forecast_row_count, replacement_row_count, internal_missing_segment_count,
                latest_missing_policy, error_reason, details, started_at, completed_at
            ) VALUES (
                %(run_id)s, %(start_ts)s::timestamptz, 'success', 'normal',
                %(logical_meter_count)s, %(active_rows)s, 0, 0, NULL, NULL,
                %(details)s, now(), now()
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
                "run_id": RUN_ID + "_pmax",
                "start_ts": START_TS,
                "logical_meter_count": len(logical_meters),
                "active_rows": active_rows,
                "details": Jsonb({
                    "source_mode": "historical_preload",
                    "artifact_path": str(path),
                    "source_rows": source_rows,
                    "active_rows": active_rows,
                    "clipped_negative_rows": clipped_negative_rows,
                    "cutoff_ts": CUTOFF_TS,
                }),
            },
        )
    conn.commit()
    return {"source_rows": source_rows, "upserted_rows": active_rows, "logical_meter_count": len(logical_meters), "clipped_negative_rows": clipped_negative_rows}


def month_ranges() -> Iterable[tuple[str, str]]:
    current = parse_ts(START_TS)
    cutoff = parse_ts(CUTOFF_TS)
    while current < cutoff:
        end = min(current + timedelta(days=1), cutoff)
        yield current.isoformat(), end.isoformat()
        current = end


def load_anomaly_features(conn) -> dict[str, object]:
    total = 0
    by_month: list[dict[str, object]] = []
    with conn.cursor() as cur:
        for start, end in month_ranges():
            cur.execute(
                """
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
                """,
                {"start": start, "end": end},
            )
            count = cur.fetchone()[0]
            conn.commit()
            total += count
            by_month.append({"start": start, "end": end, "upserted_rows": count})
            print(json.dumps({"stage": "anomaly_feature_day", "start": start, "end": end, "upserted_rows": count}, sort_keys=True), flush=True)
    return {"upserted_rows": total, "months": by_month}


def anomaly_files(root: Path) -> list[Path]:
    files = sorted(root.glob("*/test_predictions.csv")) + sorted(root.glob("*/validation_predictions.csv"))
    return files


def load_anomaly_warnings(conn, root: Path) -> dict[str, object]:
    files = anomaly_files(root)
    if not files:
        raise SystemExit(f"no anomaly prediction files under {root}")
    total_input = 0
    staged_rows = 0
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS pg_temp.anomaly_warning_import")
        cur.execute(
            """
            CREATE TEMP TABLE anomaly_warning_import (
                warning_id text,
                run_id text,
                model_name text,
                model_version text,
                release_version text,
                meter_urn text,
                model_urn text,
                forecast_origin_ts timestamptz,
                target_ts timestamptz,
                lead_step integer,
                horizon_hours integer,
                predicted_p double precision,
                threshold_lower double precision,
                threshold_upper double precision,
                warning_flag boolean,
                warning_type text,
                status text,
                physical_flag boolean,
                input_quality text,
                warning_reason_code text,
                source_input_refs jsonb,
                created_at timestamptz
            ) ON COMMIT DROP
            """
        )
        with cur.copy(
            """
            COPY anomaly_warning_import (
                warning_id, run_id, model_name, model_version, release_version,
                meter_urn, model_urn, forecast_origin_ts, target_ts, lead_step,
                horizon_hours, predicted_p, threshold_lower, threshold_upper,
                warning_flag, warning_type, status, physical_flag, input_quality,
                warning_reason_code, source_input_refs, created_at
            ) FROM STDIN
            """
        ) as cp:
            for path in files:
                with path.open("r", encoding="utf-8", newline="") as fh:
                    reader = csv.DictReader(fh)
                    for row in reader:
                        total_input += 1
                        meter = row["meter_urn"].strip()
                        try:
                            model_urn = anomaly_model_urn_for_meter(meter)
                        except Exception:
                            model_urn = meter
                        forecast_origin = row["input_end_ts"].strip()
                        for step in (1, 2, 3):
                            target_start = row["target_start_ts"].strip()
                            target_dt = datetime.fromisoformat(target_start.replace("Z", "+00:00"))
                            if target_dt.tzinfo is None:
                                target_dt = target_dt.replace(tzinfo=timezone.utc)
                            target = (target_dt + timedelta(hours=step - 1)).isoformat()
                            pred = row.get(f"pred_t_plus_{step}", "") or None
                            residual = float(row.get(f"residual_t_plus_{step}", "0") or 0.0)
                            is_anomaly = str(row.get("is_anomaly", "")).strip().lower() == "true"
                            warning_type = "high" if is_anomaly and residual > 0 else "low" if is_anomaly and residual < 0 else "none"
                            reason = "HIGH_LOAD_VS_USUAL_HOUR" if warning_type == "high" else "LOW_LOAD_VS_USUAL_HOUR" if warning_type == "low" else "NONE"
                            warning_id = hashlib.sha1(f"{RUN_ID}:anomaly:{meter}:{forecast_origin}:{step}".encode("utf-8")).hexdigest()
                            refs = [{
                                "artifact_path": str(path),
                                "source_mode": "historical_preload",
                                "input_start_ts": row.get("input_start_ts"),
                                "input_end_ts": row.get("input_end_ts"),
                                "target_start_ts": row.get("target_start_ts"),
                                "target_end_ts": row.get("target_end_ts"),
                                "source": row.get("source"),
                            }]
                            cp.write_row((
                                warning_id,
                                RUN_ID + "_anomaly",
                                "anomaly_warning",
                                ANOMALY_DETECTION_MODEL_VERSION,
                                ANOMALY_DETECTION_RELEASE,
                                meter,
                                model_urn,
                                forecast_origin,
                                target,
                                step,
                                ANOMALY_DETECTION_HORIZON_HOURS,
                                pred,
                                None,
                                None,
                                is_anomaly,
                                warning_type,
                                "success",
                                False,
                                "good",
                                reason,
                                Jsonb(refs),
                                datetime.now(timezone.utc),
                            ))
                            staged_rows += 1
        cur.execute(
            """
            WITH filtered AS (
                SELECT * FROM anomaly_warning_import
                WHERE target_ts >= %(start_ts)s::timestamptz
                  AND target_ts < %(cutoff_ts)s::timestamptz
                  AND forecast_origin_ts < %(cutoff_ts)s::timestamptz
            ), upserted AS (
                INSERT INTO mart.anomaly_warning_1h (
                    warning_id, run_id, model_name, model_version, release_version,
                    meter_urn, model_urn, forecast_origin_ts, target_ts, lead_step,
                    horizon_hours, predicted_p, threshold_lower, threshold_upper,
                    warning_flag, warning_type, status, physical_flag, input_quality,
                    warning_reason_code, source_input_refs, created_at
                )
                SELECT
                    warning_id, run_id, model_name, model_version, release_version,
                    meter_urn, model_urn, forecast_origin_ts, target_ts, lead_step,
                    horizon_hours, predicted_p, threshold_lower, threshold_upper,
                    warning_flag, warning_type, status, physical_flag, input_quality,
                    warning_reason_code, source_input_refs, created_at
                FROM filtered
                ON CONFLICT (meter_urn, forecast_origin_ts, lead_step)
                DO UPDATE SET
                    warning_id = EXCLUDED.warning_id,
                    run_id = EXCLUDED.run_id,
                    model_name = EXCLUDED.model_name,
                    model_version = EXCLUDED.model_version,
                    release_version = EXCLUDED.release_version,
                    model_urn = EXCLUDED.model_urn,
                    target_ts = EXCLUDED.target_ts,
                    horizon_hours = EXCLUDED.horizon_hours,
                    predicted_p = EXCLUDED.predicted_p,
                    threshold_lower = EXCLUDED.threshold_lower,
                    threshold_upper = EXCLUDED.threshold_upper,
                    warning_flag = EXCLUDED.warning_flag,
                    warning_type = EXCLUDED.warning_type,
                    status = EXCLUDED.status,
                    physical_flag = EXCLUDED.physical_flag,
                    input_quality = EXCLUDED.input_quality,
                    warning_reason_code = EXCLUDED.warning_reason_code,
                    source_input_refs = EXCLUDED.source_input_refs,
                    created_at = EXCLUDED.created_at
                RETURNING 1
            )
            SELECT count(*) FROM upserted
            """,
            {"start_ts": START_TS, "cutoff_ts": CUTOFF_TS},
        )
        upserted = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO ops.anomaly_log (
                run_id, job_id, model_name, model_version, release_version,
                forecast_origin_ts, artifact_ref, status, meter_count,
                prediction_count, warning_count, blocked_reason, details, started_at, finished_at
            )
            SELECT
                %(run_id)s,
                %(job_id)s,
                'anomaly_warning',
                %(model_version)s,
                %(release_version)s,
                %(start_ts)s::timestamptz,
                %(artifact_ref)s,
                'success',
                count(DISTINCT meter_urn),
                %(upserted)s,
                count(*) FILTER (WHERE warning_flag),
                NULL,
                %(details)s,
                now(),
                now()
            FROM anomaly_warning_import
            ON CONFLICT (run_id) DO UPDATE SET
                status = EXCLUDED.status,
                meter_count = EXCLUDED.meter_count,
                prediction_count = EXCLUDED.prediction_count,
                warning_count = EXCLUDED.warning_count,
                details = EXCLUDED.details,
                finished_at = EXCLUDED.finished_at
            """,
            {
                "run_id": RUN_ID + "_anomaly",
                "job_id": JOB_ID,
                "model_version": ANOMALY_DETECTION_MODEL_VERSION,
                "release_version": ANOMALY_DETECTION_RELEASE,
                "start_ts": START_TS,
                "artifact_ref": str(root),
                "upserted": upserted,
                "details": Jsonb({"source_mode": "historical_preload", "source_files": len(files), "input_wide_rows": total_input, "staged_long_rows": staged_rows, "cutoff_ts": CUTOFF_TS}),
            },
        )
    conn.commit()
    return {"source_files": len(files), "input_wide_rows": total_input, "staged_long_rows": staged_rows, "upserted_rows": upserted}


def verify(conn) -> dict[str, object]:
    checks = {}
    with conn.cursor(row_factory=dict_row) as cur:
        for name, sql in {
            "pmax_active_dec_exists": "SELECT EXISTS(SELECT 1 FROM mart.pmax_forecast_15min WHERE target_ts >= %(cutoff)s::timestamptz OR base_ts >= %(cutoff)s::timestamptz OR actual_window_ts >= %(cutoff)s::timestamptz)",
            "anomaly_warning_active_dec_exists": "SELECT EXISTS(SELECT 1 FROM mart.anomaly_warning_1h WHERE target_ts >= %(cutoff)s::timestamptz OR forecast_origin_ts >= %(cutoff)s::timestamptz)",
            "anomaly_feature_active_dec_exists": "SELECT EXISTS(SELECT 1 FROM mart.anomaly_feature_1h WHERE bucket_ts >= %(cutoff)s::timestamptz)",
            "pmax_rows": "SELECT count(*) FROM mart.pmax_forecast_15min WHERE target_ts >= %(start)s::timestamptz AND target_ts < %(cutoff)s::timestamptz",
            "anomaly_warning_rows": "SELECT count(*) FROM mart.anomaly_warning_1h WHERE target_ts >= %(start)s::timestamptz AND target_ts < %(cutoff)s::timestamptz",
            "anomaly_feature_rows": "SELECT count(*) FROM mart.anomaly_feature_1h WHERE bucket_ts >= %(start)s::timestamptz AND bucket_ts < %(cutoff)s::timestamptz",
        }.items():
            cur.execute(sql, {"start": START_TS, "cutoff": CUTOFF_TS})
            checks[name] = next(iter(cur.fetchone().values()))
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-pmax", action="store_true")
    parser.add_argument("--skip-anomaly-feature", action="store_true")
    parser.add_argument("--skip-anomaly-warning", action="store_true")
    parser.add_argument("--pmax-file-id", default=PMAX_FILE_ID)
    args = parser.parse_args()

    report: dict[str, object] = {"run_id": RUN_ID, "start_ts": START_TS, "cutoff_ts": CUTOFF_TS, "started_at": datetime.now(timezone.utc).isoformat()}
    with connect(**db_kwargs()) as conn:
        if not args.skip_pmax:
            report["pmax_download"] = download_pmax(PMAX_PATH, args.pmax_file_id)
            report["pmax_load"] = load_pmax(conn, PMAX_PATH)
        if not args.skip_anomaly_feature:
            report["anomaly_feature_load"] = load_anomaly_features(conn)
        if not args.skip_anomaly_warning:
            report["anomaly_warning_load"] = load_anomaly_warnings(conn, ANOMALY_ROOT)
        report["verification"] = verify(conn)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
