"""Publish PC1 harmonized source rows directly to the backfill Kafka lane.

This runner is intentionally separate from the live FastAPI injector so historical
catch-up cannot accidentally share the live topic. Runtime publish requires both
``--runtime-publish`` and ``CMS_ENABLE_BACKFILL_PUBLISH=1``.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from cms.contracts.ingestion import (
    kafka_message_key,
    measurement_raw_event_from_mapping,
    raw_event_to_kafka_value,
    validate_raw_event,
)
from cms.data.runtime_kafka import make_kafka_producer_config
from scripts.live.run_live_stream_injector import (
    DEFAULT_SOURCE_ROOT,
    build_payload,
    discover_harmonized_files,
    measurements_for_files,
    merged_rows,
    meter_urns_for_files,
    parse_timestamp,
    select_source_files,
)

DEFAULT_BACKFILL_TOPIC = "measurement_backfill_v1"


@dataclass(frozen=True)
class BackfillPublishSummary:
    mode: str
    source_root: str
    topic: str
    run_id: str
    start_ts: str
    end_ts: str
    discovered_file_count: int
    selected_file_count: int
    selected_meter_count: int
    selected_measurement_count: int
    emitted_count: int
    acknowledged_count: int
    validation_error_count: int
    delivery_error_count: int
    first_event_ts: str | None
    last_event_ts: str | None
    elapsed_seconds: float
    kafka_client_imported: bool
    postgres_write_attempted: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish a bounded PC1 archive window to the Kafka backfill topic.")
    parser.add_argument("--source-root", default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--topic", default=DEFAULT_BACKFILL_TOPIC)
    parser.add_argument("--run-id", required=True, help="Base run id. With --day-key, the effective id is run-id:day-key.")
    parser.add_argument("--day-key", default=None, help="Optional YYYYMMDD namespace appended to --run-id.")
    parser.add_argument("--source-system", default="cms_live_source_archive")
    parser.add_argument("--start-ts", required=True)
    parser.add_argument("--end-ts", required=True)
    parser.add_argument("--max-files", type=int, default=10000)
    parser.add_argument("--max-events", type=int, default=5000000)
    parser.add_argument("--selection-mode", choices=("first-files", "meter-balanced", "all-meters"), default="all-meters")
    parser.add_argument("--required-meters", type=int, default=1)
    parser.add_argument("--progress-every", type=int, default=10000)
    parser.add_argument("--runtime-publish", action="store_true")
    parser.add_argument("--allow-non-backfill-topic", action="store_true")
    args = parser.parse_args()
    if parse_timestamp(args.end_ts) <= parse_timestamp(args.start_ts):
        raise SystemExit("--end-ts must be after --start-ts")
    if args.max_files <= 0:
        raise SystemExit("--max-files must be positive")
    if args.max_events <= 0:
        raise SystemExit("--max-events must be positive")
    if args.required_meters < 0:
        raise SystemExit("--required-meters must be non-negative")
    if args.progress_every <= 0:
        raise SystemExit("--progress-every must be positive")
    if not args.allow_non_backfill_topic and "backfill" not in args.topic:
        raise SystemExit("backfill publisher refuses non-backfill topic without --allow-non-backfill-topic")
    if args.runtime_publish and os.getenv("CMS_ENABLE_BACKFILL_PUBLISH") != "1":
        raise SystemExit("runtime publish requires CMS_ENABLE_BACKFILL_PUBLISH=1")
    return args


def effective_run_id(args: argparse.Namespace) -> str:
    return f"{args.run_id}:{args.day_key}" if args.day_key else args.run_id


def run(args: argparse.Namespace) -> BackfillPublishSummary:
    started = time.monotonic()
    source_root = Path(args.source_root).expanduser().resolve()
    start_ts = parse_timestamp(args.start_ts)
    end_ts = parse_timestamp(args.end_ts)
    files = discover_harmonized_files(source_root)
    selected = select_source_files(files, max_files=args.max_files, selection_mode=args.selection_mode)
    selected_meter_count = len(meter_urns_for_files(selected))
    selected_measurement_count = len(measurements_for_files(selected))
    if selected_meter_count < args.required_meters:
        raise SystemExit(f"selected_meter_count {selected_meter_count} is below --required-meters {args.required_meters}")

    producer = None
    delivery_errors: list[str] = []
    acknowledged = 0
    if args.runtime_publish:
        from confluent_kafka import Producer  # type: ignore[import-not-found]

        producer = Producer(make_kafka_producer_config())

    def on_delivery(error: Any, message: Any) -> None:
        nonlocal acknowledged
        if error is not None:
            delivery_errors.append(str(error))
        else:
            acknowledged += 1

    emitted = 0
    validation_errors = 0
    first_event_ts: str | None = None
    last_event_ts: str | None = None
    run_id = effective_run_id(args)

    for row in merged_rows(selected, start_ts=start_ts):
        if row.sort_ts >= end_ts or emitted >= args.max_events:
            break
        payload = build_payload(row, source_system=args.source_system, source_root=source_root, run_id=run_id)
        event = measurement_raw_event_from_mapping(payload)
        errors = validate_raw_event(event)
        if errors:
            validation_errors += 1
            if validation_errors <= 5:
                print(json.dumps({"status": "validation_error", "source_event_id": event.source_event_id, "errors": errors}, ensure_ascii=False), flush=True)
            continue
        emitted += 1
        first_event_ts = first_event_ts or row.event_ts
        last_event_ts = row.event_ts
        if producer is not None:
            key = kafka_message_key(event).encode("utf-8")
            value = json.dumps(raw_event_to_kafka_value(event), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            while True:
                try:
                    producer.produce(topic=args.topic, key=key, value=value, on_delivery=on_delivery)
                    producer.poll(0)
                    break
                except BufferError:
                    producer.poll(1.0)
        if emitted % args.progress_every == 0:
            if producer is not None:
                producer.poll(0)
            print(json.dumps({"status": "progress", "topic": args.topic, "run_id": run_id, "emitted": emitted, "acknowledged": acknowledged, "last_event_ts": last_event_ts}, ensure_ascii=False), flush=True)

    if producer is not None:
        remaining = producer.flush(60.0)
        if remaining:
            delivery_errors.append(f"producer_flush_remaining={remaining}")

    return BackfillPublishSummary(
        mode="runtime_publish" if args.runtime_publish else "dry_run",
        source_root=str(source_root),
        topic=args.topic,
        run_id=run_id,
        start_ts=start_ts.isoformat(),
        end_ts=end_ts.isoformat(),
        discovered_file_count=len(files),
        selected_file_count=len(selected),
        selected_meter_count=selected_meter_count,
        selected_measurement_count=selected_measurement_count,
        emitted_count=emitted,
        acknowledged_count=acknowledged if args.runtime_publish else 0,
        validation_error_count=validation_errors,
        delivery_error_count=len(delivery_errors),
        first_event_ts=first_event_ts,
        last_event_ts=last_event_ts,
        elapsed_seconds=round(time.monotonic() - started, 3),
        kafka_client_imported=args.runtime_publish,
        postgres_write_attempted=False,
    )


def main() -> int:
    args = parse_args()
    summary = run(args)
    print(json.dumps({"status": "complete", "summary": asdict(summary)}, ensure_ascii=False, sort_keys=True), flush=True)
    if summary.validation_error_count or summary.delivery_error_count:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
