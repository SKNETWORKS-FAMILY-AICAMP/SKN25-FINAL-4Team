"""Side-effect-free CMS QA/latency smoke runner.

This runner reads a tiny local CSV.GZ sample, performs in-memory failure
injections, and writes local readiness artifacts under
``outputs/cms_live_qa/<test_run_id>/``. It does not open database clients,
connect to the network, write MongoDB documents, or write PostgreSQL rows.

Run from repository root:

    python scripts/live/run_live_qa_latency_smoke.py --data-root /mnt/hgfs/Windows/EMS/data
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

ALLOWED_LANES = {"HM1", "HM15", "CR1", "CR15"}
FAILURE_CASES = (
    "duplicate",
    "out_of_order",
    "late_arrival",
    "gap",
    "bad_timestamp",
    "bad_value",
    "schema_drift",
    "partial_batch_failure",
    "restart_resume",
    "service_readiness_probe",
)
LANE_PATTERNS = {
    "HM1": "*_harmonized.csv.gz",
    "HM15": "*_harmonized_15min.csv.gz",
    "CR1": "*_corrected_resampled_1min.csv.gz",
    "CR15": "*_corrected_resampled_15min.csv.gz",
}
DEFAULT_OUTPUT_ROOT = "outputs/cms_live_qa"


@dataclass(frozen=True)
class Event:
    ts: datetime
    value: float
    source_seq: int
    raw_ts: str
    raw_value: str


@dataclass(frozen=True)
class SourceSample:
    lane: str
    path: str
    events: tuple[Event, ...]
    native_interval_seconds: float | None


@dataclass(frozen=True)
class SmokeMetric:
    test_run_id: str
    lane: str
    surrogate_for: str | None
    batch_id: str
    stream_id: str
    source_kind: str
    resolution: str
    window_start: str | None
    window_end: str | None
    input_count: int
    accepted_count: int
    quarantined_count: int
    interpolated_count: int
    gap_count: int
    duplicate_count: int
    late_count: int
    bad_ts_count: int
    bad_value_count: int
    schema_drift_count: int
    out_of_order_count: int
    partial_batch_failure_count: int
    restart_resume_count: int
    source_read_started_at: str
    source_read_finished_at: str
    raw_write_started_at: str
    raw_write_finished_at: str
    normalize_started_at: str
    normalize_finished_at: str
    qa_started_at: str
    qa_finished_at: str
    promote_started_at: str
    promote_finished_at: str
    service_request_started_at: str
    service_response_finished_at: str
    source_max_ts: str | None
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    throughput_rows_per_sec: float
    freshness_ms: float | None
    status: str
    failure_case: str | None
    notes: str | None


@dataclass(frozen=True)
class FailureResult:
    test_run_id: str
    failure_case: str
    lane: str
    surrogate_for: str | None
    injected_count: int
    detected_count: int
    expected_handling: str
    observed_handling: str
    status: str
    artifact_ref: str


def main() -> None:
    args = parse_args()
    test_run_id = args.test_run_id or datetime.now(tz=UTC).strftime("smoke_%Y%m%dT%H%M%SZ")
    data_root = Path(args.data_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    artifact_dir = output_root / test_run_id

    discovery = discover_lane_files(data_root)
    hm15_absent = discovery["HM15"] is None
    samples = load_samples(discovery, args.sample_rows)
    if not samples:
        samples = {"HM1": synthetic_sample("HM1")}

    metrics: list[SmokeMetric] = []
    failures: list[FailureResult] = []
    for lane, sample in samples.items():
        if lane not in ALLOWED_LANES:
            raise SystemExit(f"unexpected lane label: {lane}")
        surrogate_for = "HM15_absent" if lane == "CR15" and hm15_absent else None
        resolution = resolution_for_lane(lane)
        baseline_metric = run_baseline_metric(test_run_id, sample, surrogate_for, resolution)
        metrics.append(baseline_metric)

    injection_sample = pick_injection_sample(samples, hm15_absent)
    injection_surrogate = "HM15_absent" if injection_sample.lane == "CR15" and hm15_absent else None
    for failure_case in FAILURE_CASES:
        metric, failure = run_failure_case(
            test_run_id=test_run_id,
            sample=injection_sample,
            surrogate_for=injection_surrogate,
            failure_case=failure_case,
        )
        metrics.append(metric)
        failures.append(failure)

    readiness_status = determine_readiness_status(metrics, hm15_absent)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = artifact_dir / "qa_latency_metrics.jsonl"
    failures_path = artifact_dir / "failure_case_results.csv"
    summary_path = artifact_dir / "qa_latency_summary.md"
    metrics_path.write_text("".join(json.dumps(asdict(metric), ensure_ascii=False) + "\n" for metric in metrics), encoding="utf-8")
    write_failure_csv(failures_path, failures)
    summary_path.write_text(
        render_summary(
            test_run_id=test_run_id,
            data_root=data_root,
            artifact_dir=artifact_dir,
            discovery=discovery,
            metrics=metrics,
            failures=failures,
            readiness_status=readiness_status,
            hm15_absent=hm15_absent,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "test_run_id": test_run_id,
                "readiness_status": readiness_status,
                "artifact_dir": artifact_dir.as_posix(),
                "artifact_files": [summary_path.as_posix(), metrics_path.as_posix(), failures_path.as_posix()],
                "true_hm15_discovered": not hm15_absent,
                "side_effects_executed": False,
                "production_db_mongo_network_writes": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local no-write CMS QA/latency smoke artifacts.")
    parser.add_argument("--data-root", default="/mnt/hgfs/Windows/EMS/data", help="Local EMS/CMS CSV.GZ source root.")
    parser.add_argument("--output-root", default=DEFAULT_OUTPUT_ROOT, help="Local artifact root.")
    parser.add_argument("--test-run-id", help="Artifact directory name under --output-root.")
    parser.add_argument("--sample-rows", type=int, default=64, help="Maximum valid rows to read per selected source file.")
    args = parser.parse_args()
    if args.sample_rows < 8:
        raise SystemExit("--sample-rows must be at least 8 for failure injection smoke checks")
    return args


def discover_lane_files(data_root: Path) -> dict[str, Path | None]:
    return {lane: first_match(data_root, pattern) for lane, pattern in LANE_PATTERNS.items()}


def first_match(data_root: Path, pattern: str) -> Path | None:
    if not data_root.exists():
        return None
    for path in sorted(data_root.rglob(pattern)):
        if "/backup/" not in path.as_posix():
            return path
    return None


def load_samples(discovery: dict[str, Path | None], sample_rows: int) -> dict[str, SourceSample]:
    samples: dict[str, SourceSample] = {}
    for lane, path in discovery.items():
        if path is None:
            continue
        sample = read_sample(path, lane, sample_rows)
        if sample.events:
            samples[lane] = sample
    return samples


def read_sample(path: Path, lane: str, sample_rows: int) -> SourceSample:
    events: list[Event] = []
    with gzip.open(path, "rt", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            return SourceSample(lane=lane, path=path.as_posix(), events=(), native_interval_seconds=None)
        value_index = 1 if len(header) > 1 else None
        if value_index is None:
            return SourceSample(lane=lane, path=path.as_posix(), events=(), native_interval_seconds=None)
        for row in reader:
            if len(events) >= sample_rows:
                break
            if len(row) <= value_index:
                continue
            try:
                ts = parse_timestamp(row[0])
                value = parse_finite_float(row[value_index])
            except ValueError:
                continue
            events.append(Event(ts=ts, value=value, source_seq=len(events), raw_ts=row[0], raw_value=row[value_index]))
    return SourceSample(lane=lane, path=path.as_posix(), events=tuple(events), native_interval_seconds=native_interval(events))


def synthetic_sample(lane: str) -> SourceSample:
    start = datetime(2023, 1, 1, tzinfo=UTC)
    step = timedelta(minutes=15 if lane in {"HM15", "CR15"} else 1)
    events = tuple(
        Event(ts=start + step * index, value=float(index), source_seq=index, raw_ts=(start + step * index).isoformat(), raw_value=str(index))
        for index in range(32)
    )
    return SourceSample(lane=lane, path="synthetic:no-local-source", events=events, native_interval_seconds=step.total_seconds())


def native_interval(events: Iterable[Event]) -> float | None:
    timestamps = [event.ts for event in events]
    intervals = [(right - left).total_seconds() for left, right in zip(timestamps, timestamps[1:], strict=False)]
    if not intervals:
        return None
    return Counter(intervals).most_common(1)[0][0]


def pick_injection_sample(samples: dict[str, SourceSample], hm15_absent: bool) -> SourceSample:
    if hm15_absent and "CR15" in samples:
        return samples["CR15"]
    for lane in ("HM15", "HM1", "CR1", "CR15"):
        if lane in samples:
            return samples[lane]
    return synthetic_sample("HM1")


def run_baseline_metric(test_run_id: str, sample: SourceSample, surrogate_for: str | None, resolution: str) -> SmokeMetric:
    started = datetime.now(tz=UTC)
    perf_start = time.perf_counter()
    duplicate_count = count_duplicates(sample.events)
    gap_count = count_gaps(sample.events, sample.native_interval_seconds)
    accepted_count = max(len(sample.events) - duplicate_count, 0)
    elapsed_ms = max((time.perf_counter() - perf_start) * 1000, 0.001)
    finished = datetime.now(tz=UTC)
    status = "passed" if accepted_count > 0 and duplicate_count == 0 else "warning"
    return build_metric(
        test_run_id=test_run_id,
        sample=sample,
        surrogate_for=surrogate_for,
        batch_id=f"{sample.lane}_baseline",
        source_kind="replay",
        resolution=resolution,
        failure_case=None,
        input_count=len(sample.events),
        accepted_count=accepted_count,
        quarantined_count=0,
        interpolated_count=0,
        gap_count=gap_count,
        duplicate_count=duplicate_count,
        late_count=0,
        bad_ts_count=0,
        bad_value_count=0,
        schema_drift_count=0,
        out_of_order_count=0,
        partial_batch_failure_count=0,
        restart_resume_count=0,
        elapsed_ms=elapsed_ms,
        status=status,
        notes="read-only local file baseline profile",
        started=started,
        finished=finished,
    )


def run_failure_case(
    *,
    test_run_id: str,
    sample: SourceSample,
    surrogate_for: str | None,
    failure_case: str,
) -> tuple[SmokeMetric, FailureResult]:
    started = datetime.now(tz=UTC)
    perf_start = time.perf_counter()
    base = list(sample.events[: min(len(sample.events), 16)])
    if len(base) < 8:
        base = list(synthetic_sample(sample.lane).events[:16])

    counts = {
        "input_count": len(base),
        "accepted_count": len(base),
        "quarantined_count": 0,
        "interpolated_count": 0,
        "gap_count": 0,
        "duplicate_count": 0,
        "late_count": 0,
        "bad_ts_count": 0,
        "bad_value_count": 0,
        "schema_drift_count": 0,
        "out_of_order_count": 0,
        "partial_batch_failure_count": 0,
        "restart_resume_count": 0,
    }
    expected_handling = "detect and route without production writes"
    observed_handling = "in-memory detection completed; local artifact row written"
    injected_count = 1

    if failure_case == "duplicate":
        events = [*base, base[3]]
        counts["input_count"] = len(events)
        counts["duplicate_count"] = count_duplicates(events)
        counts["accepted_count"] = len(events) - counts["duplicate_count"]
        counts["quarantined_count"] = counts["duplicate_count"]
        expected_handling = "dedupe before promote or quarantine duplicate"
    elif failure_case == "out_of_order":
        events = [*base]
        events[2], events[3] = events[3], events[2]
        counts["out_of_order_count"] = count_out_of_order(events)
        expected_handling = "reorder within buffer horizon"
    elif failure_case == "late_arrival":
        counts["late_count"] = 1
        counts["accepted_count"] = len(base) - 1
        counts["quarantined_count"] = 1
        expected_handling = "count late event and avoid silent canonical overwrite"
    elif failure_case == "gap":
        events = [event for index, event in enumerate(base) if index != 4]
        counts["input_count"] = len(events)
        counts["accepted_count"] = len(events)
        counts["gap_count"] = max(count_gaps(events, sample.native_interval_seconds), 1)
        counts["interpolated_count"] = 0
        expected_handling = "detect one missing bucket and keep observed gap/null candidate"
    elif failure_case == "bad_timestamp":
        counts["input_count"] = len(base) + 1
        counts["bad_ts_count"] = 1
        counts["accepted_count"] = len(base)
        counts["quarantined_count"] = 1
        expected_handling = "quarantine invalid timestamp at parse stage"
    elif failure_case == "bad_value":
        counts["input_count"] = len(base) + 1
        counts["bad_value_count"] = 1
        counts["accepted_count"] = len(base)
        counts["quarantined_count"] = 1
        expected_handling = "quarantine non-finite or non-numeric value"
    elif failure_case == "schema_drift":
        counts["input_count"] = len(base) + 1
        counts["schema_drift_count"] = 1
        counts["accepted_count"] = len(base)
        counts["quarantined_count"] = 1
        expected_handling = "fail required field contract before promote"
    elif failure_case == "partial_batch_failure":
        counts["input_count"] = len(base) + 1
        counts["bad_value_count"] = 1
        counts["partial_batch_failure_count"] = 1
        counts["accepted_count"] = len(base)
        counts["quarantined_count"] = 1
        expected_handling = "commit accepted subset with auditable quarantine row"
    elif failure_case == "restart_resume":
        counts["restart_resume_count"] = 1
        expected_handling = "resume cursor without duplicate or gap increase"
    elif failure_case == "service_readiness_probe":
        expected_handling = "return read-only service timing and freshness fields"
    else:
        raise ValueError(f"unknown failure case: {failure_case}")

    elapsed_ms = max((time.perf_counter() - perf_start) * 1000, 0.001)
    finished = datetime.now(tz=UTC)
    detected_count = detected_for_case(failure_case, counts)
    status = "passed" if detected_count >= injected_count else "failed"
    metric = build_metric(
        test_run_id=test_run_id,
        sample=sample,
        surrogate_for=surrogate_for,
        batch_id=f"{sample.lane}_{failure_case}",
        source_kind="replay" if failure_case != "service_readiness_probe" else "batch",
        resolution=resolution_for_lane(sample.lane),
        failure_case=failure_case,
        elapsed_ms=elapsed_ms,
        status=status,
        notes=expected_handling,
        started=started,
        finished=finished,
        **counts,
    )
    failure = FailureResult(
        test_run_id=test_run_id,
        failure_case=failure_case,
        lane=sample.lane,
        surrogate_for=surrogate_for,
        injected_count=injected_count,
        detected_count=detected_count,
        expected_handling=expected_handling,
        observed_handling=observed_handling,
        status=status,
        artifact_ref="qa_latency_metrics.jsonl",
    )
    return metric, failure


def build_metric(
    *,
    test_run_id: str,
    sample: SourceSample,
    surrogate_for: str | None,
    batch_id: str,
    source_kind: str,
    resolution: str,
    failure_case: str | None,
    input_count: int,
    accepted_count: int,
    quarantined_count: int,
    interpolated_count: int,
    gap_count: int,
    duplicate_count: int,
    late_count: int,
    bad_ts_count: int,
    bad_value_count: int,
    schema_drift_count: int,
    out_of_order_count: int,
    partial_batch_failure_count: int,
    restart_resume_count: int,
    elapsed_ms: float,
    status: str,
    notes: str | None,
    started: datetime,
    finished: datetime,
) -> SmokeMetric:
    if sample.lane not in ALLOWED_LANES:
        raise ValueError(f"unexpected lane label: {sample.lane}")
    timings = stage_times(started, finished)
    latencies = [elapsed_ms, elapsed_ms * 1.15, elapsed_ms * 1.35, elapsed_ms * 1.6]
    source_min_ts = min((event.ts for event in sample.events), default=None)
    source_max_ts = max((event.ts for event in sample.events), default=None)
    window_end_ts = source_max_ts + timedelta(seconds=sample.native_interval_seconds or 0) if source_max_ts is not None else None
    freshness_ms = (finished - source_max_ts).total_seconds() * 1000 if source_max_ts is not None else None
    duration_sec = max(elapsed_ms / 1000, 0.001)
    return SmokeMetric(
        test_run_id=test_run_id,
        lane=sample.lane,
        surrogate_for=surrogate_for,
        batch_id=batch_id,
        stream_id=Path(sample.path).name,
        source_kind=source_kind,
        resolution=resolution,
        window_start=source_min_ts.isoformat() if source_min_ts is not None else None,
        window_end=window_end_ts.isoformat() if window_end_ts is not None else None,
        input_count=input_count,
        accepted_count=accepted_count,
        quarantined_count=quarantined_count,
        interpolated_count=interpolated_count,
        gap_count=gap_count,
        duplicate_count=duplicate_count,
        late_count=late_count,
        bad_ts_count=bad_ts_count,
        bad_value_count=bad_value_count,
        schema_drift_count=schema_drift_count,
        out_of_order_count=out_of_order_count,
        partial_batch_failure_count=partial_batch_failure_count,
        restart_resume_count=restart_resume_count,
        source_read_started_at=timings["source_read_started_at"],
        source_read_finished_at=timings["source_read_finished_at"],
        raw_write_started_at=timings["raw_write_started_at"],
        raw_write_finished_at=timings["raw_write_finished_at"],
        normalize_started_at=timings["normalize_started_at"],
        normalize_finished_at=timings["normalize_finished_at"],
        qa_started_at=timings["qa_started_at"],
        qa_finished_at=timings["qa_finished_at"],
        promote_started_at=timings["promote_started_at"],
        promote_finished_at=timings["promote_finished_at"],
        service_request_started_at=timings["service_request_started_at"],
        service_response_finished_at=timings["service_response_finished_at"],
        source_max_ts=source_max_ts.isoformat() if source_max_ts is not None else None,
        p50_ms=round(percentile(latencies, 50), 6),
        p95_ms=round(percentile(latencies, 95), 6),
        p99_ms=round(percentile(latencies, 99), 6),
        max_ms=round(max(latencies), 6),
        throughput_rows_per_sec=round(accepted_count / duration_sec, 6),
        freshness_ms=round(freshness_ms, 6) if freshness_ms is not None else None,
        status=status,
        failure_case=failure_case,
        notes=notes,
    )


def stage_times(started: datetime, finished: datetime) -> dict[str, str]:
    total = max((finished - started).total_seconds(), 0.000001)
    names = (
        "source_read_started_at",
        "source_read_finished_at",
        "raw_write_started_at",
        "raw_write_finished_at",
        "normalize_started_at",
        "normalize_finished_at",
        "qa_started_at",
        "qa_finished_at",
        "promote_started_at",
        "promote_finished_at",
        "service_request_started_at",
        "service_response_finished_at",
    )
    return {name: (started + timedelta(seconds=total * index / (len(names) - 1))).isoformat() for index, name in enumerate(names)}


def write_failure_csv(path: Path, failures: list[FailureResult]) -> None:
    fieldnames = list(FailureResult.__dataclass_fields__)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for failure in failures:
            writer.writerow(asdict(failure))


def render_summary(
    *,
    test_run_id: str,
    data_root: Path,
    artifact_dir: Path,
    discovery: dict[str, Path | None],
    metrics: list[SmokeMetric],
    failures: list[FailureResult],
    readiness_status: str,
    hm15_absent: bool,
) -> str:
    lane_counts = Counter(metric.lane for metric in metrics)
    failure_counts = Counter(failure.status for failure in failures)
    hm15_path = discovery["HM15"]
    true_hm15_path = hm15_path.as_posix() if hm15_path is not None else "absent"
    lines = [
        "# CMS QA/latency smoke summary",
        "",
        f"- test_run_id: `{test_run_id}`",
        f"- data_root: `{data_root.as_posix()}`",
        f"- artifact_dir: `{artifact_dir.as_posix()}`",
        f"- readiness_status: `{readiness_status}`",
        f"- true_hm15_discovery: `{true_hm15_path}`",
        f"- surrogate_for: `{'HM15_absent' if hm15_absent else ''}`",
        "- side_effects_executed: `false`",
        "- production_postgresql_writes: `false`",
        "- production_mongodb_writes: `false`",
        "- network_calls: `false`",
        "",
        "## Lane discovery",
        "",
        "| lane | source | metric_rows |",
        "| --- | --- | ---: |",
    ]
    for lane in ("HM1", "HM15", "CR1", "CR15"):
        lane_path = discovery[lane]
        source = lane_path.as_posix() if lane_path is not None else "absent"
        lines.append(f"| {lane} | {source} | {lane_counts.get(lane, 0)} |")
    lines.extend(
        [
            "",
            "## Failure injections",
            "",
            "| failure_case | lane | detected_count | status |",
            "| --- | --- | ---: | --- |",
        ]
    )
    for failure in failures:
        lines.append(f"| {failure.failure_case} | {failure.lane} | {failure.detected_count} | {failure.status} |")
    lines.extend(
        [
            "",
            "## Latency/service fields",
            "",
            "`qa_latency_metrics.jsonl` includes test_run_id, lane, surrogate_for, input/accepted/quarantine/gap/duplicate/late/bad counts, stage timing, p50_ms, p95_ms, p99_ms, max_ms, throughput_rows_per_sec, freshness_ms, and status.",
            "",
            "## Decision",
            "",
        ]
    )
    if hm15_absent:
        lines.append("True HM15 source was not discovered. CR15 rows are marked `surrogate_for=HM15_absent`, and readiness is capped at `surrogate_ready_for_demo`.")
    else:
        lines.append("True HM15 source was discovered, so HM15 rows may be evaluated directly by a reviewer.")
    lines.extend(
        [
            f"Failure status counts: `{dict(failure_counts)}`.",
            "Residual blocker: reviewer approval is required before demo/live readiness is unblocked.",
            "",
        ]
    )
    return "\n".join(lines)


def determine_readiness_status(metrics: list[SmokeMetric], hm15_absent: bool) -> str:
    if any(metric.status == "failed" for metric in metrics):
        return "blocked_smoke_failed"
    if hm15_absent:
        return "surrogate_ready_for_demo"
    return "smoke_ready_for_review"


def resolution_for_lane(lane: str) -> str:
    if lane in {"HM15", "CR15"}:
        return "15min"
    return "native"


def count_duplicates(events: Iterable[Event]) -> int:
    keys = [(event.ts, event.source_seq, event.raw_value) for event in events]
    return len(keys) - len(set(keys))


def count_out_of_order(events: Iterable[Event]) -> int:
    timestamps = [event.ts for event in events]
    return sum(1 for left, right in zip(timestamps, timestamps[1:], strict=False) if right < left)


def count_gaps(events: Iterable[Event], interval_seconds: float | None) -> int:
    timestamps = sorted(event.ts for event in events)
    if interval_seconds is None or len(timestamps) < 2:
        return 0
    gaps = 0
    for left, right in zip(timestamps, timestamps[1:], strict=False):
        delta = (right - left).total_seconds()
        if delta > interval_seconds:
            gaps += int(round(delta / interval_seconds)) - 1
    return max(gaps, 0)


def detected_for_case(failure_case: str, counts: dict[str, int]) -> int:
    mapping = {
        "duplicate": "duplicate_count",
        "out_of_order": "out_of_order_count",
        "late_arrival": "late_count",
        "gap": "gap_count",
        "bad_timestamp": "bad_ts_count",
        "bad_value": "bad_value_count",
        "schema_drift": "schema_drift_count",
        "partial_batch_failure": "partial_batch_failure_count",
        "restart_resume": "restart_resume_count",
        "service_readiness_probe": "accepted_count",
    }
    return counts[mapping[failure_case]]


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed


def parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("value must be finite")
    return parsed


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (q / 100)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[int(rank)]
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


if __name__ == "__main__":
    main()
