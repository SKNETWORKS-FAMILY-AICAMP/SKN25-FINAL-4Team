"""Side-effect-free CMS live stream dry-run helper.

This script only reads local ``*.csv.gz`` EMS/CMS source samples and prints a
JSON profile by default. With ``--write-artifacts`` it writes local profile
artifacts under ``outputs/cms_live_qa/<test_run_id>/``. It does not open
database clients, connect to the network, write MongoDB documents, or write
PostgreSQL rows.

Run from repository root:

    python scripts/live/dry_run_live_stream.py --data-root /mnt/hgfs/Windows/EMS/data --max-files 6 --sample-rows 1000

The output is a JSON profile with grain evidence for later approved
live streaming tests.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

CANONICAL_TABLE_15MIN = "canonical.measurement_15min"
REFERENCE_TABLE_1MIN = "reference.corrected_resampled_1min"
REFERENCE_TABLE_15MIN = "reference.corrected_resampled_15min"
REFERENCE_TABLE_1H = "reference.corrected_resampled_1h"
DEFAULT_PATTERNS = (
    "*_harmonized.csv.gz",
    "*_harmonized_15min.csv.gz",
    "*_corrected_resampled_1min.csv.gz",
    "*_corrected_resampled_15min.csv.gz",
    "*_corrected_resampled_1h.csv.gz",
)


@dataclass(frozen=True)
class SampleSummary:
    path: str
    layer: str
    lane_label: str
    grain_status: str
    expected_target_table: str | None
    meter_measurement: str | None
    header: tuple[str, ...]
    sampled_rows: int
    first_ts: str | None
    last_ts: str | None
    min_interval_seconds: float | None
    median_interval_seconds: float | None
    max_interval_seconds: float | None
    top_interval_seconds: float | None
    top_interval_count: int
    native_interval_seconds: float | None
    non_numeric_values: int


@dataclass(frozen=True)
class DryRunSummary:
    test_run_id: str
    data_root: str
    artifact_dir: str
    artifact_files: tuple[str, ...]
    patterns: tuple[str, ...]
    discovered_file_count: int
    selected_file_count: int
    layer_counts: dict[str, int]
    lane_counts: dict[str, int]
    side_effects_executed: bool
    writes_allowed: bool
    local_artifacts_written: bool
    expected_patterns: tuple[str, ...]
    samples: tuple[SampleSummary, ...]


def main() -> None:
    args = parse_args()
    data_root = Path(args.data_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    test_run_id = args.test_run_id or datetime.now(tz=UTC).strftime("dryrun_%Y%m%dT%H%M%SZ")
    artifact_dir = output_root / test_run_id
    patterns = tuple(args.pattern or DEFAULT_PATTERNS)
    files = discover_files(data_root, patterns)
    selected = files[: args.max_files]
    summaries = tuple(summarize_file(path, args.sample_rows) for path in selected)
    artifact_files: tuple[str, ...] = ()
    if args.write_artifacts:
        artifact_files = write_artifacts(
            artifact_dir=artifact_dir,
            test_run_id=test_run_id,
            data_root=data_root,
            patterns=patterns,
            discovered_file_count=len(files),
            summaries=summaries,
        )
    result = DryRunSummary(
        test_run_id=test_run_id,
        data_root=str(data_root),
        artifact_dir=artifact_dir.as_posix(),
        artifact_files=artifact_files,
        patterns=patterns,
        discovered_file_count=len(files),
        selected_file_count=len(selected),
        layer_counts=dict(Counter(summary.layer for summary in summaries)),
        lane_counts=dict(Counter(summary.lane_label for summary in summaries)),
        side_effects_executed=False,
        writes_allowed=False,
        local_artifacts_written=args.write_artifacts,
        expected_patterns=DEFAULT_PATTERNS,
        samples=summaries,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dry-run local CMS live stream sample discovery without DB writes.")
    parser.add_argument("--data-root", default="/mnt/hgfs/Windows/EMS/data", help="Root directory containing EMS/CMS CSV.GZ files.")
    parser.add_argument("--pattern", action="append", help="Glob pattern to scan. Defaults to harmonized and corrected_resampled files.")
    parser.add_argument("--max-files", type=int, default=8, help="Maximum discovered files to summarize.")
    parser.add_argument("--sample-rows", type=int, default=1000, help="Maximum data rows to read per file.")
    parser.add_argument("--test-run-id", help="Artifact directory name under --output-root. Defaults to UTC dryrun timestamp.")
    parser.add_argument("--output-root", default="outputs/cms_live_qa", help="Local artifact root for optional dry-run profile files.")
    parser.add_argument("--write-artifacts", action="store_true", help="Write profile artifacts: summary.json, samples.jsonl, samples.csv, and summary.md.")
    args = parser.parse_args()
    if args.max_files <= 0:
        raise SystemExit("--max-files must be positive")
    if args.sample_rows <= 0:
        raise SystemExit("--sample-rows must be positive")
    return args


def discover_files(data_root: Path, patterns: Iterable[str]) -> list[Path]:
    if not data_root.exists():
        return []
    found: dict[str, Path] = {}
    for pattern in patterns:
        for path in data_root.rglob(pattern):
            if "/backup/" in path.as_posix():
                continue
            found[path.as_posix()] = path
    return [found[key] for key in sorted(found)]


def summarize_file(path: Path, sample_rows: int) -> SampleSummary:
    timestamps: list[datetime] = []
    non_numeric_values = 0
    header: tuple[str, ...] = ()
    with gzip.open(path, "rt", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = tuple(next(reader))
        except StopIteration:
            return build_sample_summary(path=path, header=(), timestamps=[], non_numeric_values=0)
        value_index = 1 if len(header) > 1 else None
        for row_index, row in enumerate(reader):
            if row_index >= sample_rows:
                break
            if not row:
                continue
            timestamps.append(parse_timestamp(row[0]))
            if value_index is not None and len(row) > value_index:
                try:
                    float(row[value_index])
                except ValueError:
                    non_numeric_values += 1
    return build_sample_summary(path=path, header=header, timestamps=timestamps, non_numeric_values=non_numeric_values)


def build_sample_summary(path: Path, header: tuple[str, ...], timestamps: list[datetime], non_numeric_values: int) -> SampleSummary:
    intervals = interval_seconds(timestamps)
    interval_counter = Counter(intervals)
    top_interval_seconds, top_interval_count = interval_counter.most_common(1)[0] if interval_counter else (None, 0)
    median_interval_seconds = median(intervals) if intervals else None
    layer = classify_layer(path)
    lane_label = classify_lane(layer)
    grain_status = classify_grain(layer, median_interval_seconds, top_interval_seconds)
    native_interval_seconds = infer_native_interval_seconds(median_interval_seconds, top_interval_seconds)
    return SampleSummary(
        path=path.as_posix(),
        layer=layer,
        lane_label=lane_label,
        grain_status=grain_status,
        expected_target_table=expected_table(layer, native_interval_seconds),
        meter_measurement=header[1] if len(header) > 1 else None,
        header=header,
        sampled_rows=len(timestamps),
        first_ts=timestamps[0].isoformat() if timestamps else None,
        last_ts=timestamps[-1].isoformat() if timestamps else None,
        min_interval_seconds=min(intervals) if intervals else None,
        median_interval_seconds=median_interval_seconds,
        max_interval_seconds=max(intervals) if intervals else None,
        top_interval_seconds=top_interval_seconds,
        top_interval_count=top_interval_count,
        native_interval_seconds=native_interval_seconds,
        non_numeric_values=non_numeric_values,
    )


def classify_layer(path: Path) -> str:
    name = path.name
    if name.endswith("_harmonized_15min.csv.gz"):
        return "harmonized_15min"
    if name.endswith("_harmonized.csv.gz"):
        return "harmonized"
    if name.endswith("_corrected_resampled_1min.csv.gz"):
        return "corrected_resampled_1min"
    if name.endswith("_corrected_resampled_15min.csv.gz"):
        return "corrected_resampled_15min"
    if name.endswith("_corrected_resampled_1h.csv.gz"):
        return "corrected_resampled_1h"
    if "corrected" in name:
        return "corrected"
    return "unknown"


def classify_lane(layer: str) -> str:
    if layer == "harmonized_15min":
        return "HM15"
    if layer == "harmonized":
        return "HM1"
    if layer == "corrected_resampled_1min":
        return "CR1"
    if layer == "corrected_resampled_15min":
        return "CR15"
    if layer == "corrected_resampled_1h":
        return "CR1H"
    return "HM1"


def classify_grain(layer: str, median_interval_seconds: float | None, top_interval_seconds: float | None) -> str:
    if median_interval_seconds is None or top_interval_seconds is None:
        return "unproven_empty_or_single_row"
    if median_interval_seconds != top_interval_seconds:
        return "unproven_interval_mismatch"
    if top_interval_seconds == 60:
        return "proven_1min"
    if top_interval_seconds == 900:
        return "proven_15min"
    if top_interval_seconds == 3600:
        return "proven_1h"
    if layer == "harmonized":
        return "proven_native_other"
    return "proven_other"


def infer_native_interval_seconds(median_interval_seconds: float | None, top_interval_seconds: float | None) -> float | None:
    if median_interval_seconds is None or top_interval_seconds is None:
        return None
    if median_interval_seconds != top_interval_seconds:
        return None
    return top_interval_seconds


def expected_table(layer: str, native_interval_seconds: float | None) -> str | None:
    if layer == "harmonized_15min" and native_interval_seconds == 900:
        return CANONICAL_TABLE_15MIN
    if layer == "corrected_resampled_1min" and native_interval_seconds == 60:
        return REFERENCE_TABLE_1MIN
    if layer == "corrected_resampled_15min" and native_interval_seconds == 900:
        return REFERENCE_TABLE_15MIN
    if layer == "corrected_resampled_1h" and native_interval_seconds == 3600:
        return REFERENCE_TABLE_1H
    return None


def write_artifacts(
    *,
    artifact_dir: Path,
    test_run_id: str,
    data_root: Path,
    patterns: tuple[str, ...],
    discovered_file_count: int,
    summaries: tuple[SampleSummary, ...],
) -> tuple[str, ...]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    summary_json = artifact_dir / "summary.json"
    samples_jsonl = artifact_dir / "samples.jsonl"
    samples_csv = artifact_dir / "samples.csv"
    summary_md = artifact_dir / "summary.md"

    summary_payload = {
        "test_run_id": test_run_id,
        "data_root": data_root.as_posix(),
        "patterns": patterns,
        "discovered_file_count": discovered_file_count,
        "selected_file_count": len(summaries),
        "layer_counts": dict(Counter(summary.layer for summary in summaries)),
        "lane_counts": dict(Counter(summary.lane_label for summary in summaries)),
        "side_effects_executed": False,
        "writes_allowed": False,
        "local_artifacts_written": True,
    }
    summary_json.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    samples_jsonl.write_text("".join(json.dumps(asdict(summary), ensure_ascii=False) + "\n" for summary in summaries), encoding="utf-8")
    write_samples_csv(samples_csv, summaries)
    summary_md.write_text(render_summary_md(summary_payload, summaries), encoding="utf-8")
    return tuple(
        path.as_posix()
        for path in (
            summary_json,
            samples_jsonl,
            samples_csv,
            summary_md,
        )
    )




def write_samples_csv(path: Path, summaries: tuple[SampleSummary, ...]) -> None:
    fieldnames = [
        "path",
        "layer",
        "lane_label",
        "grain_status",
        "expected_target_table",
        "meter_measurement",
        "sampled_rows",
        "first_ts",
        "last_ts",
        "native_interval_seconds",
        "median_interval_seconds",
        "top_interval_seconds",
        "top_interval_count",
        "non_numeric_values",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for summary in summaries:
            row = asdict(summary)
            writer.writerow({field: row[field] for field in fieldnames})


def render_summary_md(summary_payload: dict[str, object], summaries: tuple[SampleSummary, ...]) -> str:
    lines = [
        "# CMS live stream dry-run QA summary",
        "",
        f"- test_run_id: `{summary_payload['test_run_id']}`",
        f"- data_root: `{summary_payload['data_root']}`",
        f"- discovered_file_count: {summary_payload['discovered_file_count']}",
        f"- selected_file_count: {summary_payload['selected_file_count']}",
        "- side_effects_executed: false",
        "- writes_allowed: false",
        "- local_artifacts_written: true",
        "",
        "## Lane counts",
        "",
    ]
    lane_counts = summary_payload["lane_counts"]
    if isinstance(lane_counts, dict) and lane_counts:
        for lane_label, count in sorted(lane_counts.items()):
            lines.append(f"- {lane_label}: {count}")
    else:
        lines.append("- no sampled files")
    lines.extend([
        "",
        "## Samples",
        "",
        "| lane | grain_status | native_interval_seconds | sampled_rows | expected_table | path |",
        "| --- | --- | ---: | ---: | --- | --- |",
    ])
    for summary in summaries:
        expected = summary.expected_target_table or ""
        native = "" if summary.native_interval_seconds is None else str(summary.native_interval_seconds)
        lines.append(
            "| "
            + " | ".join(
                [
                    summary.lane_label,
                    summary.grain_status,
                    native,
                    str(summary.sampled_rows),
                    expected,
                    summary.path.replace("|", "\\|"),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def interval_seconds(timestamps: list[datetime]) -> list[float]:
    return [(right - left).total_seconds() for left, right in zip(timestamps, timestamps[1:], strict=False)]


if __name__ == "__main__":
    main()
