#!/usr/bin/env python3
"""Build 15-minute peak mart features from 1-minute corrected_resampled CSV.gz files."""

from __future__ import annotations

import argparse
import csv
import gzip
import os
import sys
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from cms.data.peak_features import PeakFeatureRow, PeakSample, aggregate_peak_features

DEFAULT_ROOT = Path("/data/fems/src/corrected_resampled")
DEFAULT_ENV_PATH = Path("/home/ubuntu/cms-deploy/.env")
TARGET_TABLE = "mart.peak_feature_15min"
INPUT_VIEW = "mart.peak_input_15min"


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key] = value.strip().strip('"').strip("'")
    return values


def split_series_name(path: Path) -> tuple[str, str]:
    stem = path.name.removesuffix("_corrected_resampled_1min.csv.gz")
    if "." not in stem:
        raise ValueError(f"cannot split meter/measurement from {path.name}")
    meter_urn, measurement = stem.rsplit(".", 1)
    return meter_urn, measurement


def meter_in_scope(meter_urn: str, meter_prefixes: tuple[str, ...], exact_meters: tuple[str, ...]) -> bool:
    if not meter_prefixes and not exact_meters:
        return True
    return meter_urn in exact_meters or any(meter_urn.startswith(prefix) for prefix in meter_prefixes)


def discover_files(
    root: Path,
    measurements: set[str],
    limit_files: int | None,
    *,
    meter_prefixes: tuple[str, ...] = (),
    exact_meters: tuple[str, ...] = (),
) -> list[Path]:
    files: list[Path] = []
    effective_limit = None if limit_files is not None and limit_files <= 0 else limit_files
    for path in sorted(root.glob("*/*_corrected_resampled_1min.csv.gz")):
        meter_urn, measurement = split_series_name(path)
        if measurement in measurements and meter_in_scope(meter_urn, meter_prefixes, exact_meters):
            files.append(path)
        if effective_limit is not None and len(files) >= effective_limit:
            break
    return files


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_float(value: str) -> float | None:
    if value in ("", "nan", "NaN", "None", "null", "NULL"):
        return None
    return float(value)


def read_samples(path: Path) -> Iterable[PeakSample]:
    with gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        if len(header) < 2 or header[0] != "datetime_utc":
            raise ValueError(f"unexpected header in {path}: {header}")
        for row in reader:
            if len(row) < 2 or not row[0]:
                continue
            yield PeakSample(timestamp=parse_timestamp(row[0]), value=parse_float(row[1]))


def connect(env_path: Path):
    psycopg = __import__("psycopg")

    env = parse_env(env_path)
    return psycopg.connect(
        host="127.0.0.1",
        port=env.get("POSTGRES_PORT", "5432"),
        dbname=env.get("POSTGRES_DB", "cms"),
        user=env.get("POSTGRES_USER", "cms"),
        password=env["POSTGRES_PASSWORD"],
        autocommit=False,
    )


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS mart")
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TARGET_TABLE} (
                window_ts timestamptz NOT NULL,
                meter_urn text NOT NULL,
                measurement text NOT NULL,
                mean_value double precision,
                max_value double precision,
                min_value double precision,
                p95_value double precision,
                p99_value double precision,
                std_value double precision,
                last_value double precision,
                peak_ts timestamptz,
                peak_value double precision,
                observed_points integer NOT NULL,
                expected_points integer NOT NULL DEFAULT 15,
                coverage_ratio double precision NOT NULL,
                source_file text NOT NULL,
                run_id text NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (window_ts, meter_urn, measurement, run_id)
            )
            """
        )
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_peak_feature_15min_meter_ts ON {TARGET_TABLE} (meter_urn, window_ts)")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_peak_feature_15min_measurement_ts ON {TARGET_TABLE} (measurement, window_ts)")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_peak_feature_15min_run ON {TARGET_TABLE} (run_id)")
    conn.commit()


def ensure_input_view(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(f"DROP VIEW IF EXISTS {INPUT_VIEW}")
        cur.execute(
            f"""
            CREATE OR REPLACE VIEW {INPUT_VIEW} AS
            WITH p AS (
                SELECT * FROM {TARGET_TABLE} WHERE measurement = 'P'
            ),
            u1 AS (
                SELECT * FROM {TARGET_TABLE} WHERE measurement = 'U1'
            ),
            pf AS (
                SELECT * FROM {TARGET_TABLE} WHERE measurement = 'PF'
            ),
            ta AS (
                SELECT window_ts, run_id, mean_value, last_value
                FROM {TARGET_TABLE}
                WHERE measurement = 'Ta' AND meter_urn = 'WeatherStation.Weather'
            ),
            igm AS (
                SELECT window_ts, run_id, mean_value, last_value
                FROM {TARGET_TABLE}
                WHERE measurement = 'Igm' AND meter_urn = 'WeatherStation.Weather'
            )
            SELECT
                p.window_ts,
                p.meter_urn,
                p.run_id,
                p.mean_value AS p_mean,
                p.max_value AS p_max,
                p.min_value AS p_min,
                p.p95_value AS p_p95,
                p.p99_value AS p_p99,
                p.std_value AS p_std,
                p.last_value AS p_last,
                p.peak_ts AS p_peak_ts,
                p.peak_value AS target_max_p_15min,
                lead(p.peak_value) OVER (PARTITION BY p.meter_urn, p.run_id ORDER BY p.window_ts) AS target_next_15min_max_p,
                u1.mean_value AS u1_mean,
                u1.max_value AS u1_max,
                u1.last_value AS u1_last,
                pf.mean_value AS pf_mean,
                pf.max_value AS pf_max,
                pf.last_value AS pf_last,
                ta.mean_value AS ta_mean,
                ta.last_value AS ta_last,
                igm.mean_value AS lgm_mean,
                igm.last_value AS lgm_last,
                extract(hour FROM p.window_ts)::integer AS hour,
                extract(isodow FROM p.window_ts)::integer AS day_of_week,
                extract(month FROM p.window_ts)::integer AS month
            FROM p
            LEFT JOIN u1
              ON u1.window_ts = p.window_ts
             AND u1.meter_urn = p.meter_urn
             AND u1.run_id = p.run_id
            LEFT JOIN pf
              ON pf.window_ts = p.window_ts
             AND pf.meter_urn = p.meter_urn
             AND pf.run_id = p.run_id
            LEFT JOIN ta
              ON ta.window_ts = p.window_ts
             AND ta.run_id = p.run_id
            LEFT JOIN igm
              ON igm.window_ts = p.window_ts
             AND igm.run_id = p.run_id
            """
        )
    conn.commit()


def copy_rows(conn, rows: list[PeakFeatureRow]) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        with cur.copy(
            f"""
            COPY {TARGET_TABLE} (
                window_ts, meter_urn, measurement, mean_value, max_value, min_value,
                p95_value, p99_value, std_value, last_value, peak_ts, peak_value,
                observed_points, expected_points, coverage_ratio, source_file, run_id
            ) FROM STDIN
            """
        ) as copy:
            for row in rows:
                copy.write_row(
                    (
                        row.window_ts,
                        row.meter_urn,
                        row.measurement,
                        row.mean_value,
                        row.max_value,
                        row.min_value,
                        row.p95_value,
                        row.p99_value,
                        row.std_value,
                        row.last_value,
                        row.peak_ts,
                        row.peak_value,
                        row.observed_points,
                        row.expected_points,
                        row.coverage_ratio,
                        row.source_file,
                        row.run_id,
                    )
                )
    conn.commit()


def run(args: argparse.Namespace) -> int:
    measurements = set(args.measurement)
    files = discover_files(
        args.root,
        measurements,
        args.limit_files,
        meter_prefixes=tuple(args.meter_prefix),
        exact_meters=tuple(args.meter),
    )
    if not files:
        print(f"no files for measurements={sorted(measurements)} under {args.root}", file=sys.stderr)
        return 1

    run_id = args.run_id or f"peak15_pilot_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    print(f"run_id={run_id}")
    print(f"target_table={TARGET_TABLE}")
    print(f"input_view={INPUT_VIEW}")
    print(f"files={len(files)}")
    for path in files[:10]:
        print(f"file={path.relative_to(args.root)}")

    if args.dry_run:
        return 0

    with connect(args.env_path) as conn:
        ensure_schema(conn)
        if args.replace_run_id:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {TARGET_TABLE} WHERE run_id=%s", (run_id,))
            conn.commit()
        total_rows = 0
        for index, path in enumerate(files, 1):
            meter_urn, measurement = split_series_name(path)
            rel = path.relative_to(args.root).as_posix()
            rows = aggregate_peak_features(
                read_samples(path),
                meter_urn=meter_urn,
                measurement=measurement,
                source_file=rel,
                run_id=run_id,
            )
            copy_rows(conn, rows)
            total_rows += len(rows)
            print(f"copied {index}/{len(files)} {rel} rows={len(rows)}", flush=True)
        ensure_input_view(conn)
    print(f"done run_id={run_id} rows={total_rows}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--env-path", type=Path, default=DEFAULT_ENV_PATH)
    parser.add_argument("--measurement", action="append", default=["P"], help="measurement to include; repeatable")
    parser.add_argument("--meter-prefix", action="append", default=[], help="include meters with this prefix; repeatable")
    parser.add_argument("--meter", action="append", default=[], help="include an exact meter_urn; repeatable")
    parser.add_argument("--limit-files", type=int, default=10, help="max files to load; <=0 means no limit")
    parser.add_argument("--run-id")
    parser.add_argument("--replace-run-id", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
