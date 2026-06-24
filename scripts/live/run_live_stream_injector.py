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
import os
import resource
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
    SOURCE_AUTHORITY_PC1_ARCHIVE,
    raw_payload_digest,
    raw_payload_size_bytes,
)
from cms.contracts.live_pipeline import validate_live_injector_source_authority

DEFAULT_SOURCE_ROOT = "/home/ubuntu/cms-stream-deploy/stacks/stream_runtime/source_mounts/live_source/harmonized"
DEFAULT_SOURCE_SYSTEM = "cms_live_source_archive"
DEFAULT_FAST_MERGE_MAX_FILES = 512
FAST_MERGE_FD_RESERVE = 64


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
    duration_minutes: float
    start_ts: str | None
    end_ts: str | None
    run_id: str | None
    emitted_count: int
    accepted_count: int
    error_count: int
    first_event_ts: str | None
    last_event_ts: str | None
    api_url: str
    source_system: str
    source_authority: str
    source_root_authorized: bool
    selected_measurement_count: int
    selected_measurements: list[str]
    selected_meter_urn_sample: list[str]
    selected_file_sample: list[str]
    merge_strategy: str
    soft_nofile_limit: int | str | None
    fast_merge_fd_safe_limit: int
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
    parser.add_argument("--source-authority", default=SOURCE_AUTHORITY_PC1_ARCHIVE, help="source authority for observed live injector payloads.")
    parser.add_argument("--run-id", default=None, help="Optional run id prefix for cleanup-identifiable source_event_id values.")
    parser.add_argument("--max-files", type=int, default=16, help="Maximum source files to open for a bounded live run.")
    parser.add_argument("--max-events", type=int, default=100, help="Maximum events to emit.")
    parser.add_argument("--selection-mode", choices=("first-files", "meter-balanced", "all-meters"), default="first-files", help="Source file selection policy.")
    parser.add_argument("--merge-mode", choices=("event-time", "file-order"), default="event-time", help="Use global event_ts merge or stream each file in source order.")
    parser.add_argument("--required-meters", type=int, default=0, help="Fail if selected unique meter count is below this value.")
    parser.add_argument("--replay-clock", choices=("fixed-rate", "event-time"), default="fixed-rate", help="Use fixed event rate or preserve source event_ts gaps.")
    parser.add_argument("--time-scale", type=float, default=1.0, help="Event-time replay scale. 1 preserves real gaps; 10 makes a 10s source gap wait 1s.")
    parser.add_argument("--duration-minutes", type=float, default=0.0, help="Optional source event-time window length from the first emitted event. 0 disables the window.")
    parser.add_argument("--start-ts", default=None, help="Optional inclusive source event_ts where the replay window starts.")
    parser.add_argument("--end-ts", default=None, help="Optional exclusive source event_ts where the replay window stops.")
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
    if args.duration_minutes < 0:
        raise SystemExit("--duration-minutes must be non-negative")
    if args.replay_clock == "event-time" and args.events_per_second > 0:
        raise SystemExit("--events-per-second is only valid with --replay-clock fixed-rate")
    if args.start_ts is not None:
        try:
            parse_timestamp(args.start_ts)
        except ValueError as exc:
            raise SystemExit("--start-ts must be an ISO timestamp") from exc
    if args.end_ts is not None:
        try:
            parse_timestamp(args.end_ts)
        except ValueError as exc:
            raise SystemExit("--end-ts must be an ISO timestamp") from exc
    if args.start_ts is not None and args.end_ts is not None and parse_timestamp(args.end_ts) <= parse_timestamp(args.start_ts):
        raise SystemExit("--end-ts must be after --start-ts")
    return args


def run(args: argparse.Namespace, *, sleep_fn: Callable[[float], None] = time.sleep) -> InjectorSummary:
    source_root = Path(args.source_root).expanduser().resolve()
    validate_live_injector_source_authority(source_root, args.source_authority)
    all_files = discover_harmonized_files(source_root, include_backup=True)
    files = discover_harmonized_files(source_root)
    selected = select_source_files(files, max_files=args.max_files, selection_mode=args.selection_mode)
    selected_meter_count = len(meter_urns_for_files(selected))
    selected_measurements = sorted(measurements_for_files(selected))
    if selected_meter_count < args.required_meters:
        raise SystemExit(f"selected_meter_count {selected_meter_count} is below --required-meters {args.required_meters}")
    emitted = 0
    accepted = 0
    errors = 0
    first_event_ts: str | None = None
    last_event_ts: str | None = None
    fixed_delay_sec = 1.0 / args.events_per_second if args.events_per_second > 0 else 0.0
    previous_sort_ts: datetime | None = None
    window_start_ts: datetime | None = parse_timestamp(args.start_ts) if args.start_ts is not None else None
    window_end_ts: datetime | None = parse_timestamp(args.end_ts) if args.end_ts is not None else None
    window_seconds = args.duration_minutes * 60.0 if args.duration_minutes else 0.0
    merge_strategy = args.merge_mode if args.merge_mode == "file-order" else ("fast" if should_use_fast_merge(len(selected)) else "bounded")
    soft_nofile_limit, fd_safe_limit = nofile_limits()

    row_iter = file_order_rows(selected, start_ts=window_start_ts) if args.merge_mode == "file-order" else merged_rows(selected, start_ts=window_start_ts)
    for row in row_iter:
        if emitted >= args.max_events:
            break
        if window_start_ts is not None and row.sort_ts < window_start_ts:
            continue
        if window_end_ts is not None and row.sort_ts >= window_end_ts:
            break
        if window_start_ts is None:
            window_start_ts = row.sort_ts
        elif window_seconds and (row.sort_ts - window_start_ts).total_seconds() > window_seconds:
            break
        if args.runtime_post and args.replay_clock == "event-time" and previous_sort_ts is not None:
            delay_sec = compute_replay_delay(previous_sort_ts, row.sort_ts, time_scale=args.time_scale)
            if delay_sec:
                sleep_fn(delay_sec)
        payload = build_payload(
            row,
            source_system=args.source_system,
            source_authority=args.source_authority,
            source_root=source_root,
            run_id=args.run_id,
        )
        emitted += 1
        first_event_ts = first_event_ts or row.event_ts
        last_event_ts = row.event_ts
        if args.runtime_post:
            ok = post_payload(args.api_url, payload)
            if ok:
                accepted += 1
            else:
                errors += 1
        if args.runtime_post and args.replay_clock == "fixed-rate" and fixed_delay_sec:
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
        duration_minutes=args.duration_minutes,
        start_ts=args.start_ts,
        end_ts=args.end_ts,
        run_id=args.run_id,
        emitted_count=emitted,
        accepted_count=accepted,
        error_count=errors,
        first_event_ts=first_event_ts,
        last_event_ts=last_event_ts,
        api_url=args.api_url,
        source_system=args.source_system,
        source_authority=args.source_authority,
        source_root_authorized=True,
        selected_measurement_count=len(selected_measurements),
        selected_measurements=selected_measurements,
        selected_meter_urn_sample=sorted(meter_urns_for_files(selected))[:20],
        selected_file_sample=[path.as_posix() for path in selected[:20]],
        merge_strategy=merge_strategy,
        soft_nofile_limit=soft_nofile_limit,
        fast_merge_fd_safe_limit=fd_safe_limit,
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
    if selection_mode == "all-meters":
        return paths
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


def measurements_for_files(paths: list[Path]) -> set[str]:
    measurements: set[str] = set()
    for path in paths:
        try:
            with gzip.open(path, "rt", newline="") as handle:
                reader = csv.reader(handle)
                header = next(reader)
        except (OSError, StopIteration):
            continue
        _, measurement = parse_meter_measurement(header)
        measurements.add(measurement)
    return measurements


def compute_replay_delay(previous_ts: datetime, current_ts: datetime, *, time_scale: float) -> float:
    if time_scale <= 0:
        raise ValueError("time_scale must be positive")
    return max(0.0, (current_ts - previous_ts).total_seconds() / time_scale)


def merged_rows(paths: list[Path], *, start_ts: datetime | None = None) -> Iterator[SourceRow]:
    if should_use_fast_merge(len(paths)):
        yield from fast_merged_rows(paths, start_ts=start_ts)
        return
    yield from bounded_merged_rows(paths, start_ts=start_ts)


def file_order_rows(paths: list[Path], *, start_ts: datetime | None = None) -> Iterator[SourceRow]:
    for sequence, path in enumerate(paths):
        yield from iter_source_rows(path, sequence=sequence, start_ts=start_ts)


def should_use_fast_merge(path_count: int) -> bool:
    if path_count <= 0:
        return True
    _, fd_safe_limit = nofile_limits()
    return path_count <= fd_safe_limit


def nofile_limits() -> tuple[int | str | None, int]:
    configured_limit = int(os.environ.get("CMS_LIVE_INJECTOR_FAST_MERGE_MAX_FILES", str(DEFAULT_FAST_MERGE_MAX_FILES)))
    if configured_limit <= 0:
        raise ValueError("CMS_LIVE_INJECTOR_FAST_MERGE_MAX_FILES must be positive")
    try:
        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    except (OSError, ValueError):
        return None, configured_limit
    if soft_limit == resource.RLIM_INFINITY:
        return "infinity", configured_limit
    return int(soft_limit), max(1, min(configured_limit, int(soft_limit) - FAST_MERGE_FD_RESERVE))


def fast_merged_rows(paths: list[Path], *, start_ts: datetime | None = None) -> Iterator[SourceRow]:
    streams = [iter_source_rows(path, sequence=sequence, start_ts=start_ts) for sequence, path in enumerate(paths)]
    try:
        yield from heapq.merge(*streams, key=lambda row: (row.sort_ts, row.sequence, row.row_number))
    finally:
        for stream in streams:
            close = getattr(stream, "close", None)
            if close is not None:
                close()


def bounded_merged_rows(paths: list[Path], *, start_ts: datetime | None = None) -> Iterator[SourceRow]:
    heap: list[tuple[datetime, int, int, SourceRow]] = []
    for sequence, path in enumerate(paths):
        row = next_source_row(path, sequence=sequence, after_row_number=0, start_ts=start_ts)
        if row is not None:
            heapq.heappush(heap, (row.sort_ts, row.sequence, row.row_number, row))

    while heap:
        _, _, _, row = heapq.heappop(heap)
        yield row
        next_row = next_source_row(Path(row.path), sequence=row.sequence, after_row_number=row.row_number, start_ts=start_ts)
        if next_row is not None:
            heapq.heappush(heap, (next_row.sort_ts, next_row.sequence, next_row.row_number, next_row))


def next_source_row(path: Path, *, sequence: int, after_row_number: int, start_ts: datetime | None = None) -> SourceRow | None:
    for row in iter_source_rows(path, sequence=sequence, start_ts=start_ts):
        if row.row_number > after_row_number:
            return row
    return None


def iter_source_rows(path: Path, *, sequence: int, start_ts: datetime | None = None) -> Iterator[SourceRow]:
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
            if start_ts is not None and sort_ts < start_ts:
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


def build_payload(
    row: SourceRow,
    *,
    source_system: str,
    source_authority: str = SOURCE_AUTHORITY_PC1_ARCHIVE,
    source_root: Path | None = None,
    run_id: str | None = None,
    received_at: datetime | None = None,
) -> dict[str, Any]:
    received = received_at or datetime.now(tz=UTC)
    base_payload: dict[str, Any] = {
        "schema_version": MEASUREMENT_RAW_SCHEMA_VERSION,
        "source_system": source_system,
        "source_event_id": source_event_id_for_row(row, source_root=source_root, run_id=run_id),
        "meter_urn": row.meter_urn,
        "measurement": row.measurement,
        "event_ts": row.event_ts,
        "value_text": row.value_text,
        "value_numeric": row.value_numeric,
        "unit": None,
        "received_at": received.isoformat(),
        "source_authority": source_authority,
        "source_path": row.path,
    }
    base_payload["raw_payload_hash"] = raw_payload_digest(base_payload)
    base_payload["raw_payload_size_bytes"] = raw_payload_size_bytes(base_payload)
    return base_payload


def source_event_id_for_row(row: SourceRow, *, source_root: Path | None = None, run_id: str | None = None) -> str:
    path = Path(row.path)
    try:
        path_text = path.relative_to(source_root).as_posix() if source_root is not None else row.path
    except ValueError:
        path_text = row.path
    event_id = f"{path_text}:{row.row_number}"
    return f"{run_id}:{event_id}" if run_id else event_id


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
