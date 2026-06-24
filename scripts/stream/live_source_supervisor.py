"""Long-running FastAPI live-source supervisor.

Keeps the PC1 harmonized source replay running until the configured event-time
window is exhausted. The supervisor resumes from PostgreSQL by `run_id` so a
container restart repeats only the last timestamp boundary; duplicate events are
left to the existing ingestion idempotency contract.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from scripts.live.run_live_stream_injector import parse_timestamp, run as run_replay

DEFAULT_TARGET_START_TS = "2022-12-31T15:00:00+00:00"  # 2023-01-01 00:00 KST
DEFAULT_TARGET_END_TS = "2023-01-30T15:00:00+00:00"  # 2023-01-31 00:00 KST


def main() -> int:
    args = parse_args()
    if args.runtime_post and os.getenv("CMS_ENABLE_LIVE_SOURCE_REPLAY") != "1":
        raise SystemExit("runtime supervisor requires CMS_ENABLE_LIVE_SOURCE_REPLAY=1")
    run_supervisor(args)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Keep FastAPI source replay alive across a 30-day event-time window.")
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--day-cache-parent", default=None, help="Optional parent containing clocked_cache_YYYYMMDD day roots.")
    parser.add_argument("--day-cache-prefix", default="clocked_cache_", help="Day cache directory prefix under --day-cache-parent.")
    parser.add_argument("--cache-day-tz-offset-hours", type=float, default=9.0, help="Fixed local offset used for day cache names and boundaries.")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000/ingest/measurements")
    parser.add_argument("--source-system", default="cms_live_source_archive")
    parser.add_argument("--run-id", default="live_20230101_clocked")
    parser.add_argument("--selection-mode", choices=("first-files", "meter-balanced", "all-meters"), default="all-meters")
    parser.add_argument("--merge-mode", choices=("event-time", "file-order"), default="event-time")
    parser.add_argument("--max-files", type=int, default=10000)
    parser.add_argument("--max-events-per-chunk", type=int, default=5000000)
    parser.add_argument("--required-meters", type=int, default=1)
    parser.add_argument("--target-start-ts", default=DEFAULT_TARGET_START_TS)
    parser.add_argument("--target-end-ts", default=DEFAULT_TARGET_END_TS)
    parser.add_argument("--chunk-hours", type=float, default=24.0)
    parser.add_argument("--time-scale", type=float, default=1.0)
    parser.add_argument("--resume-from-db", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--empty-window-sleep-seconds", type=float, default=300.0)
    parser.add_argument("--runtime-post", action="store_true")
    args = parser.parse_args()
    target_start = parse_timestamp(args.target_start_ts)
    target_end = parse_timestamp(args.target_end_ts)
    if target_end <= target_start:
        raise SystemExit("--target-end-ts must be after --target-start-ts")
    if args.chunk_hours <= 0:
        raise SystemExit("--chunk-hours must be positive")
    if args.time_scale <= 0:
        raise SystemExit("--time-scale must be positive")
    if args.empty_window_sleep_seconds < 0:
        raise SystemExit("--empty-window-sleep-seconds must be non-negative")
    return args


def run_supervisor(args: argparse.Namespace) -> None:
    target_start = parse_timestamp(args.target_start_ts)
    target_end = parse_timestamp(args.target_end_ts)
    while True:
        start_ts = choose_resume_start(
            target_start=target_start,
            target_end=target_end,
            run_id=args.run_id,
            resume_from_db=args.resume_from_db,
        )
        if start_ts >= target_end:
            print(json.dumps({"status": "complete", "run_id": args.run_id, "target_end_ts": target_end.isoformat()}), flush=True)
            return
        source_root = source_root_for_start(args, start_ts=start_ts)
        end_ts = chunk_end(
            start_ts=start_ts,
            target_end=target_end,
            chunk_hours=args.chunk_hours,
            day_cache_enabled=bool(args.day_cache_parent),
            cache_day_tz_offset_hours=args.cache_day_tz_offset_hours,
        )
        replay_args = replay_namespace(args, start_ts=start_ts, end_ts=end_ts)
        replay_args.source_root = source_root
        print(
            json.dumps(
                {
                    "status": "chunk_start",
                    "run_id": args.run_id,
                    "source_root": source_root,
                    "start_ts": start_ts.isoformat(),
                    "end_ts": end_ts.isoformat(),
                    "replay_clock": "event-time",
                    "time_scale": args.time_scale,
                }
            ),
            flush=True,
        )
        summary = run_replay(replay_args)
        print(json.dumps({"status": "chunk_done", "summary": asdict(summary)}, ensure_ascii=False), flush=True)
        if summary.emitted_count == 0:
            print(
                json.dumps(
                    {
                        "status": "empty_window_retry",
                        "run_id": args.run_id,
                        "start_ts": start_ts.isoformat(),
                        "end_ts": end_ts.isoformat(),
                        "sleep_seconds": args.empty_window_sleep_seconds,
                    }
                ),
                flush=True,
            )
            time.sleep(args.empty_window_sleep_seconds)


def choose_resume_start(*, target_start: datetime, target_end: datetime, run_id: str, resume_from_db: bool) -> datetime:
    if not resume_from_db:
        return target_start
    max_event_ts = latest_event_ts_for_run(run_id=run_id, target_start=target_start, target_end=target_end)
    if max_event_ts is None:
        return target_start
    # Keep this inclusive: if only part of a timestamp was committed before a restart,
    # replaying that same timestamp is safer than skipping unprocessed peer rows.
    return max(target_start, min(max_event_ts, target_end))


def latest_event_ts_for_run(*, run_id: str, target_start: datetime, target_end: datetime) -> datetime | None:
    try:
        import psycopg  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError("psycopg is required for --resume-from-db") from exc
    conninfo = postgres_conninfo_from_env()
    with psycopg.connect(conninfo, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT max(event_ts)
                FROM live.measurement_event
                WHERE source_event_id LIKE %s
                  AND event_ts >= %s
                  AND event_ts < %s
                """,
                (f"{run_id}:%", target_start, target_end),
            )
            value = cur.fetchone()[0]
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def postgres_conninfo_from_env() -> str:
    host = os.getenv("DB_HOST") or os.getenv("POSTGRES_HOST")
    dbname = os.getenv("DB_NAME") or os.getenv("POSTGRES_DB") or "cms"
    user = os.getenv("DB_USER") or os.getenv("POSTGRES_USER")
    password = os.getenv("DB_PASSWORD") or os.getenv("POSTGRES_PASSWORD")
    port = os.getenv("DB_PORT") or os.getenv("POSTGRES_PORT") or "5432"
    sslmode = os.getenv("DB_SSLMODE") or os.getenv("POSTGRES_SSLMODE") or "disable"
    missing = [name for name, value in {"host": host, "user": user, "password": password}.items() if not value]
    if missing:
        raise RuntimeError("missing PostgreSQL env for live supervisor: " + ",".join(missing))
    return f"host={host} port={port} dbname={dbname} user={user} password={password} sslmode={sslmode}"


def source_root_for_start(args: argparse.Namespace, *, start_ts: datetime) -> str:
    if not args.day_cache_parent:
        return args.source_root
    from pathlib import Path

    day = local_day_key(start_ts, offset_hours=args.cache_day_tz_offset_hours)
    candidate = Path(args.day_cache_parent) / f"{args.day_cache_prefix}{day}"
    return candidate.as_posix() if candidate.exists() else args.source_root


def local_day_key(value: datetime, *, offset_hours: float) -> str:
    tz = timezone(timedelta(hours=offset_hours))
    return value.astimezone(tz).strftime("%Y%m%d")


def next_local_midnight(value: datetime, *, offset_hours: float) -> datetime:
    tz = timezone(timedelta(hours=offset_hours))
    local = value.astimezone(tz)
    next_day = (local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
    return next_day.astimezone(UTC)


def chunk_end(
    *,
    start_ts: datetime,
    target_end: datetime,
    chunk_hours: float,
    day_cache_enabled: bool = False,
    cache_day_tz_offset_hours: float = 9.0,
) -> datetime:
    end_ts = min(start_ts + timedelta(hours=chunk_hours), target_end)
    if day_cache_enabled:
        end_ts = min(end_ts, next_local_midnight(start_ts, offset_hours=cache_day_tz_offset_hours), target_end)
    return end_ts


def replay_namespace(args: argparse.Namespace, *, start_ts: datetime, end_ts: datetime) -> Any:
    return SimpleNamespace(
        source_root=args.source_root,
        api_url=args.api_url,
        source_system=args.source_system,
        source_authority="pc1_archive",
        run_id=args.run_id,
        max_files=args.max_files,
        max_events=args.max_events_per_chunk,
        selection_mode=args.selection_mode,
        merge_mode=args.merge_mode,
        required_meters=args.required_meters,
        replay_clock="event-time",
        time_scale=args.time_scale,
        duration_minutes=0.0,
        start_ts=start_ts.isoformat(),
        end_ts=end_ts.isoformat(),
        events_per_second=0.0,
        runtime_post=args.runtime_post,
    )


if __name__ == "__main__":
    raise SystemExit(main())
