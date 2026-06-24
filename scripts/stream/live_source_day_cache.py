"""Build per-day live-source caches from a harmonized archive root.

The output preserves the original relative path and gzip CSV header, but keeps only
rows whose event timestamp belongs to the requested local day window. This is a
file-cache operation only; it does not call FastAPI, Kafka, or PostgreSQL.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TextIO

from scripts.live.run_live_stream_injector import parse_timestamp


def main() -> int:
    args = parse_args()
    summary = build_day_caches(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create clocked_cache_YYYYMMDD source-day caches.")
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--cache-parent", required=True)
    parser.add_argument("--start-day", required=True, help="Inclusive local day YYYYMMDD")
    parser.add_argument("--end-day", required=True, help="Inclusive local day YYYYMMDD")
    parser.add_argument("--day-prefix", default="clocked_cache_")
    parser.add_argument("--tz-offset-hours", type=float, default=9.0)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.end_day < args.start_day:
        raise SystemExit("--end-day must be >= --start-day")
    return args


def build_day_caches(args: argparse.Namespace) -> dict[str, object]:
    source_root = Path(args.source_root).expanduser().resolve()
    cache_parent = Path(args.cache_parent).expanduser().resolve()
    tz = timezone(timedelta(hours=args.tz_offset_hours))
    start_local = datetime.strptime(args.start_day, "%Y%m%d").replace(tzinfo=tz)
    end_local_exclusive = (datetime.strptime(args.end_day, "%Y%m%d").replace(tzinfo=tz) + timedelta(days=1))
    start_utc = start_local.astimezone(UTC)
    end_utc = end_local_exclusive.astimezone(UTC)
    files = sorted(source_root.rglob("*_harmonized.csv.gz"))
    day_counts: dict[str, int] = {}
    output_files = 0
    input_rows = 0
    kept_rows = 0
    for source_file in files:
        rel = source_file.relative_to(source_root)
        writers: dict[str, tuple[TextIO, Any]] = {}
        try:
            with gzip.open(source_file, "rt", newline="") as handle:
                reader = csv.reader(handle)
                try:
                    header = next(reader)
                except StopIteration:
                    continue
                for row in reader:
                    if not row or not row[0]:
                        continue
                    try:
                        event_ts = parse_timestamp(row[0])
                    except ValueError:
                        continue
                    if event_ts < start_utc:
                        continue
                    if event_ts >= end_utc:
                        break
                    input_rows += 1
                    day = event_ts.astimezone(tz).strftime("%Y%m%d")
                    if day not in writers:
                        out_path = cache_parent / f"{args.day_prefix}{day}" / rel
                        if out_path.exists() and not args.overwrite:
                            continue
                        out_path.parent.mkdir(parents=True, exist_ok=True)
                        gz = gzip.open(out_path, "wt", newline="")
                        writer = csv.writer(gz)
                        writer.writerow(header)
                        writers[day] = (gz, writer)
                        output_files += 1
                    writers[day][1].writerow(row)
                    day_counts[day] = day_counts.get(day, 0) + 1
                    kept_rows += 1
        finally:
            for gz, _ in writers.values():
                gz.close()
    return {
        "source_root": str(source_root),
        "cache_parent": str(cache_parent),
        "start_day": args.start_day,
        "end_day": args.end_day,
        "input_file_count": len(files),
        "output_file_count": output_files,
        "input_rows_in_window": input_rows,
        "kept_rows": kept_rows,
        "day_counts": day_counts,
    }


if __name__ == "__main__":
    raise SystemExit(main())
