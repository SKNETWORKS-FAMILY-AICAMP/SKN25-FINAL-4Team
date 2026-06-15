#!/usr/bin/env python3
"""Build a one-day clocked replay source cache from harmonized gzip files.

The source inventory already records the first row number and row count for
2023-01-01. This script copies only that day into a mirror source root so the
live injector can start from 2023 without rescanning years of pre-2023 rows.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CacheResult:
    source_path: str
    output_path: str
    meter_urn: str
    measurement: str
    expected_rows: int
    written_rows: int
    ok: bool
    error: str | None = None


def main() -> int:
    parser = argparse.ArgumentParser(description="Build one-day clocked replay source cache")
    parser.add_argument("--inventory-jsonl", required=True)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--cache-root", required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0, help="Optional file limit for smoke runs")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    inventory = Path(args.inventory_jsonl)
    source_root = Path(args.source_root).resolve()
    cache_root = Path(args.cache_root).resolve()
    entries = load_entries(inventory, source_root=source_root)
    if args.limit:
        entries = entries[: args.limit]
    cache_root.mkdir(parents=True, exist_ok=True)

    results: list[CacheResult] = []
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(copy_day_file, entry, source_root.as_posix(), cache_root.as_posix()) for entry in entries]
        for future in as_completed(futures):
            results.append(future.result())

    ok_count = sum(1 for result in results if result.ok)
    failed = [result for result in results if not result.ok]
    summary = {
        "source_root": source_root.as_posix(),
        "cache_root": cache_root.as_posix(),
        "inventory_jsonl": inventory.as_posix(),
        "selected_file_count": len(entries),
        "ok_count": ok_count,
        "failed_count": len(failed),
        "written_rows": sum(result.written_rows for result in results),
        "meters": len({result.meter_urn for result in results if result.ok}),
        "measurements": len({result.measurement for result in results if result.ok}),
        "errors": [result.__dict__ for result in failed[:20]],
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failed else 2


def load_entries(inventory: Path, *, source_root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    with inventory.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            rows = int(record.get("rows_on_20230101") or 0)
            first_row = int(record.get("first_20230101_row_number") or 0)
            if not record.get("ok") or rows <= 0 or first_row <= 1:
                continue
            path = Path(str(record["path"])).resolve()
            try:
                rel_path = path.relative_to(source_root)
            except ValueError:
                rel_path = Path(record.get("rel_path") or path.name)
            entries.append(
                {
                    "path": path.as_posix(),
                    "rel_path": rel_path.as_posix(),
                    "first_row_number": first_row,
                    "rows_on_day": rows,
                    "meter_urn": str(record.get("meter_urn") or ""),
                    "measurement": str(record.get("measurement") or ""),
                }
            )
    return sorted(entries, key=lambda item: item["rel_path"])


def copy_day_file(entry: dict[str, Any], source_root: str, cache_root: str) -> CacheResult:
    source_path = Path(entry["path"])
    output_path = Path(cache_root) / str(entry["rel_path"])
    first_row_number = int(entry["first_row_number"])
    rows_on_day = int(entry["rows_on_day"])
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(source_path, "rt", newline="") as src, gzip.open(output_path, "wt", newline="") as dst:
            reader = csv.reader(src)
            writer = csv.writer(dst)
            header = next(reader)
            writer.writerow(header)
            skip_data_rows = max(0, first_row_number - 2)
            for _ in islice(reader, skip_data_rows):
                pass
            written = 0
            for row in islice(reader, rows_on_day):
                writer.writerow(row)
                written += 1
        ok = written == rows_on_day
        return CacheResult(
            source_path=source_path.as_posix(),
            output_path=output_path.as_posix(),
            meter_urn=str(entry["meter_urn"]),
            measurement=str(entry["measurement"]),
            expected_rows=rows_on_day,
            written_rows=written,
            ok=ok,
            error=None if ok else f"expected {rows_on_day}, wrote {written}",
        )
    except Exception as exc:
        return CacheResult(
            source_path=source_path.as_posix(),
            output_path=output_path.as_posix(),
            meter_urn=str(entry.get("meter_urn") or ""),
            measurement=str(entry.get("measurement") or ""),
            expected_rows=rows_on_day,
            written_rows=0,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )


if __name__ == "__main__":
    raise SystemExit(main())
