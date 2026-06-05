"""Live stream injector for CMS harmonized source archives.

Default mode is a dry run: read gzip source rows, merge selected streams by
``event_ts``, and print a JSON summary. Runtime POST to FastAPI requires the
explicit ``--runtime-post`` gate. This script never writes PostgreSQL and never
imports Kafka or database clients.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import heapq
import json
import time
import urllib.error
import urllib.request
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cms.contracts.ingestion import (
    MEASUREMENT_RAW_SCHEMA_VERSION,
    raw_payload_digest,
    raw_payload_size_bytes,
)

DEFAULT_SOURCE_ROOT = "/home/ubuntu/cms-stream-deploy/data/live_source/harmonized"
DEFAULT_SOURCE_SYSTEM = "cms_live_source_archive"


@dataclass(frozen=True)
class SourceRow:
    sort_ts: datetime
    sequence: int
    path: str
    row_number: int
    meter_urn: str
    measurement: str
    event_ts: str
    value_text: str | None
    value_numeric: float | None


@dataclass(frozen=True)
class InjectorSummary:
    mode: str
    source_root: str
    discovered_file_count: int
    eligible_file_count: int
    excluded_backup_file_count: int
    selected_file_count: int
    selected_meter_count: int
    required_meter_count: int
    selection_mode: str
    replay_clock: str
    time_scale: float
    emitted_count: int
    accepted_count: int
    error_count: int
    first_event_ts: str | None
    last_event_ts: str | None
    api_url: str
    source_system: str
    dry_run: bool
    postgres_write_attempted: bool
    kafka_client_imported: bool
    db_client_imported: bool


def main() -> None:
    args = parse_args()
    summary = run(args)
    print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inject harmonized gzip rows as a bounded live stream.")
    parser.add_argument("--source-root", default=DEFAULT_SOURCE_ROOT, help="Root containing *_harmonized.csv.gz files.")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000/ingest/measurements", help="FastAPI ingest endpoint.")
    parser.add_argument("--source-system", default=DEFAULT_SOURCE_SYSTEM, help="source_system field for idempotency.")
    parser.add_argument("--max-files", type=int, default=16, help="Maximum source files to open for a bounded live run.")
    parser.add_argument("--max-events", type=int, default=100, help="Maximum events to emit.")
    parser.add_argument("--selection-mode", choices=("first-files", "meter-balanced"), default="first-files", help="Source file selection policy.")
    parser.add_argument("--required-meters", type=int, default=0, help="Fail if selected unique meter count is below this value.")
    parser.add_argument("--replay-clock", choices=("fixed-rate", "event-time"), default="fixed-rate", help="Use fixed event rate or preserve source event_ts gaps.")
    parser.add_argument("--time-scale", type=float, default=1.0, help="Event-time replay scale. 1 preserves real gaps; 10 makes a 10s source gap wait 1s.")
    parser.add_argument("--events-per-second", type=float, default=0.0, help="Optional fixed-rate throttle. 0 means no sleep.")
    parser.add_argument("--runtime-post", action="store_true", help="Actually POST events to FastAPI. Default is dry-run only.")
    args = parser.parse_args()
    if args.max_files <= 0:
        raise SystemExit("--max-files must be positive")
    if args.max_events <= 0:
        raise SystemExit("--max-events must be positive")
    if args.events_per_second < 0:
        raise SystemExit("--events-per-second must be non-negative")
    if args.required_meters < 0:
        raise SystemExit("--required-meters must be non-negative")
    if args.time_scale <= 0:
        raise SystemExit("--time-scale must be positive")
    if args.replay_clock == "event-time" and args.events_per_second > 0:
        raise SystemExit("--events-per-second is only valid with --replay-clock fixed-rate")
    return args


def run(args: argparse.Namespace, *, sleep_fn: Callable[[float], None] = time.sleep) -> InjectorSummary:
    source_root = Path(args.source_root).expanduser().resolve()
    all_files = discover_harmonized_files(source_root, include_backup=True)
    files = discover_harmonized_files(source_root)
    selected = select_source_files(files, max_files=args.max_files, selection_mode=args.selection_mode)
    selected_meter_count = len(meter_urns_for_files(selected))
    if selected_meter_count < args.required_meters:
        raise SystemExit(f"selected_meter_count {selected_meter_count} is below --required-meters {args.required_meters}")
    emitted = 0
    accepted = 0
    errors = 0
    first_event_ts: str | None = None
    last_event_ts: str | None = None
    fixed_delay_sec = 1.0 / args.events_per_second if args.events_per_second > 0 else 0.0
    previous_sort_ts: datetime | None = None

    for row in merged_rows(selected):
        if emitted >= args.max_events:
            break
        if args.replay_clock == "event-time" and previous_sort_ts is not None:
            delay_sec = compute_replay_delay(previous_sort_ts, row.sort_ts, time_scale=args.time_scale)
            if delay_sec:
                sleep_fn(delay_sec)
        payload = build_payload(row, source_system=args.source_system)
        emitted += 1
        first_event_ts = first_event_ts or row.event_ts
        last_event_ts = row.event_ts
        if args.runtime_post:
            ok = post_payload(args.api_url, payload)
            if ok:
                accepted += 1
            else:
                errors += 1
        if args.replay_clock == "fixed-rate" and fixed_delay_sec:
            sleep_fn(fixed_delay_sec)
        previous_sort_ts = row.sort_ts

    mode = "runtime_post" if args.runtime_post else "dry_run"
    return InjectorSummary(
        mode=mode,
        source_root=str(source_root),
        discovered_file_count=len(all_files),
        eligible_file_count=len(files),
        excluded_backup_file_count=len(all_files) - len(files),
        selected_file_count=len(selected),
        selected_meter_count=selected_meter_count,
        required_meter_count=args.required_meters,
        selection_mode=args.selection_mode,
        replay_clock=args.replay_clock,
        time_scale=args.time_scale,
        emitted_count=emitted,
        accepted_count=accepted,
        error_count=errors,
        first_event_ts=first_event_ts,
        last_event_ts=last_event_ts,
        api_url=args.api_url,
        source_system=args.source_system,
        dry_run=not args.runtime_post,
        postgres_write_attempted=False,
        kafka_client_imported=False,
        db_client_imported=False,
    )


def discover_harmonized_files(source_root: Path, *, include_backup: bool = False) -> list[Path]:
    if not source_root.exists():
        return []
    return sorted(
        path
        for path in source_root.rglob("*_harmonized.csv.gz")
        if include_backup or "/backup/" not in path.as_posix()
    )


def select_source_files(paths: list[Path], *, max_files: int, selection_mode: str) -> list[Path]:
    if selection_mode == "first-files":
        return paths[:max_files]
    if selection_mode != "meter-balanced":
        raise ValueError(f"unsupported selection_mode: {selection_mode}")
    groups: dict[str, list[Path]] = defaultdict(list)
    for path in paths:
        groups[meter_urn_for_file(path)].append(path)
    selected: list[Path] = []
    ordered_meters = sorted(groups)
    round_index = 0
    while len(selected) < max_files:
        added = False
        for meter in ordered_meters:
            series = groups[meter]
            if round_index < len(series):
                selected.append(series[round_index])
                added = True
                if len(selected) >= max_files:
                    break
        if not added:
            break
        round_index += 1
    return selected


def meter_urns_for_files(paths: list[Path]) -> set[str]:
    return {meter_urn_for_file(path) for path in paths}


def meter_urn_for_file(path: Path) -> str:
    try:
        with gzip.open(path, "rt", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader)
    except (OSError, StopIteration):
        return path.parent.name
    meter_urn, _ = parse_meter_measurement(header)
    return meter_urn


def compute_replay_delay(previous_ts: datetime, current_ts: datetime, *, time_scale: float) -> float:
    if time_scale <= 0:
        raise ValueError("time_scale must be positive")
    return max(0.0, (current_ts - previous_ts).total_seconds() / time_scale)


def merged_rows(paths: list[Path]) -> Iterator[SourceRow]:
    streams = [iter_source_rows(path, sequence=index) for index, path in enumerate(paths)]
    yield from heapq.merge(*streams, key=lambda row: (row.sort_ts, row.sequence, row.row_number))


def iter_source_rows(path: Path, *, sequence: int) -> Iterator[SourceRow]:
    with gzip.open(path, "rt", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            return
        meter_urn, measurement = parse_meter_measurement(header)
        for row_number, row in enumerate(reader, start=2):
            if len(row) < 2 or not row[0]:
                continue
            event_ts = row[0]
            value_text = row[1] if row[1] != "" else None
            try:
                sort_ts = parse_timestamp(event_ts)
            except ValueError:
                continue
            value_numeric = parse_float(value_text)
            yield SourceRow(
                sort_ts=sort_ts,
                sequence=sequence,
                path=path.as_posix(),
                row_number=row_number,
                meter_urn=meter_urn,
                measurement=measurement,
                event_ts=event_ts,
                value_text=value_text,
                value_numeric=value_numeric,
            )


def parse_meter_measurement(header: list[str]) -> tuple[str, str]:
    label = header[1].strip() if len(header) > 1 and header[1].strip() else "unknown.unknown"
    meter_urn, separator, measurement = label.rpartition(".")
    if not separator or not meter_urn or not measurement:
        return label, "value"
    return meter_urn, measurement


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def build_payload(row: SourceRow, *, source_system: str, received_at: datetime | None = None) -> dict[str, Any]:
    received = received_at or datetime.now(tz=UTC)
    base_payload: dict[str, Any] = {
        "schema_version": MEASUREMENT_RAW_SCHEMA_VERSION,
        "source_system": source_system,
        "source_event_id": f"{row.path}:{row.row_number}",
        "meter_urn": row.meter_urn,
        "measurement": row.measurement,
        "event_ts": row.event_ts,
        "value_text": row.value_text,
        "value_numeric": row.value_numeric,
        "unit": None,
        "received_at": received.isoformat(),
    }
    base_payload["raw_payload_hash"] = raw_payload_digest(base_payload)
    base_payload["raw_payload_size_bytes"] = raw_payload_size_bytes(base_payload)
    return base_payload


def post_payload(api_url: str, payload: dict[str, Any]) -> bool:
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(api_url, data=encoded, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return False
    return response.status == 200 and int(body.get("status_code", 0)) == 202 and bool(body.get("accepted"))


if __name__ == "__main__":
    main()
