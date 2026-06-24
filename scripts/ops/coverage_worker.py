#!/usr/bin/env python3
"""Source-vs-live coverage QA worker for CMS live measurements.

This worker compares server-side day-cache source files with live DB coverage for
a bounded event-time window. It writes only *currently observed* gaps to
``qa.live_issue`` when ``--execute-write`` is supplied. Historical repaired gaps
should remain audit evidence files, not active QA rows.

Default mode is dry-run: produce JSON evidence and perform no database writes.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

WORKER_VERSION = "coverage_worker.v1"
DEFAULT_SOURCE_ROOT = "/home/skn25/cms-stream-deploy/data/live_source/day_cache"
DEFAULT_OUTPUT_DIR = "/home/skn25/cms-stream-deploy/audit/coverage_worker"

Resolution = str
Key = tuple[datetime, str, str]


@dataclass(frozen=True)
class DbConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str
    sslmode: str = "disable"


@dataclass
class SourceCoverage:
    window_start: datetime
    window_end: datetime
    files_seen: int = 0
    files_read: int = 0
    bad_files: list[dict[str, str]] = field(default_factory=list)
    source_rows: int = 0
    event_exact_keys: set[Key] = field(default_factory=set)
    rollup_keys: dict[Resolution, set[Key]] = field(
        default_factory=lambda: {"1min": set(), "15min": set(), "1h": set()}
    )

    def event_minute_counts(self) -> Counter[Key]:
        counts: Counter[Key] = Counter()
        for event_ts, meter_urn, measurement in self.event_exact_keys:
            counts[(floor_time(event_ts, "1min"), meter_urn, measurement)] += 1
        return counts


@dataclass(frozen=True)
class CoverageIssue:
    issue_kind: str
    severity: str
    meter_urn: str
    measurement: str
    bucket_ts: datetime
    resolution: str
    reason: str
    details: dict[str, object]

    @property
    def coverage_key(self) -> str:
        payload = "|".join(
            [
                self.issue_kind,
                self.resolution,
                self.bucket_ts.isoformat(),
                self.meter_urn,
                self.measurement,
            ]
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def to_live_issue_row(self) -> dict[str, object]:
        details = dict(self.details)
        details["coverage_key"] = self.coverage_key
        details["worker_version"] = WORKER_VERSION
        return {
            "issue_kind": self.issue_kind,
            "severity": self.severity,
            "meter_urn": self.meter_urn,
            "measurement": self.measurement,
            "event_id": None,
            "bucket_ts": self.bucket_ts,
            "resolution": self.resolution,
            "policy_id": None,
            "policy_version": None,
            "reason": self.reason,
            "details": details,
        }

    def to_json(self) -> dict[str, object]:
        row = self.to_live_issue_row()
        row["bucket_ts"] = self.bucket_ts.isoformat()
        details = row.get("details")
        row["details"] = dict(details) if isinstance(details, dict) else {}
        return row


def parse_iso_datetime(value: str, *, default_tz: ZoneInfo) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_tz)
    return parsed.astimezone(timezone.utc)


def floor_time(ts: datetime, resolution: Resolution) -> datetime:
    ts = ts.astimezone(timezone.utc)
    if resolution == "1min":
        return ts.replace(second=0, microsecond=0)
    if resolution == "15min":
        minute = (ts.minute // 15) * 15
        return ts.replace(minute=minute, second=0, microsecond=0)
    if resolution == "1h":
        return ts.replace(minute=0, second=0, microsecond=0)
    raise ValueError(f"unsupported resolution: {resolution}")


def iter_window_dates(start: datetime, end: datetime, tz: ZoneInfo) -> Iterable[str]:
    local_start = start.astimezone(tz).date()
    local_end = (end - timedelta(microseconds=1)).astimezone(tz).date()
    day = local_start
    while day <= local_end:
        yield day.strftime("%Y%m%d")
        day += timedelta(days=1)
    utc_start = start.astimezone(timezone.utc).date()
    utc_end = (end - timedelta(microseconds=1)).astimezone(timezone.utc).date()
    day = utc_start
    seen = set()
    while day <= utc_end:
        text = day.strftime("%Y%m%d")
        if text not in seen:
            yield text
            seen.add(text)
        day += timedelta(days=1)


def iter_source_files(source_root: Path, start: datetime, end: datetime, tz: ZoneInfo) -> list[Path]:
    candidates: list[Path] = []
    seen: set[Path] = set()
    for day_text in iter_window_dates(start, end, tz):
        day_dir = source_root / f"clocked_cache_{day_text}"
        if not day_dir.exists():
            continue
        for path in day_dir.glob("*/*.csv.gz"):
            if path not in seen:
                candidates.append(path)
                seen.add(path)
    return sorted(candidates)


def split_meter_measurement(column_name: str, path: Path) -> tuple[str, str]:
    name = column_name.strip()
    if "." in name:
        meter, measurement = name.rsplit(".", 1)
        if meter and measurement:
            return meter, measurement
    stem = path.name
    if stem.endswith(".csv.gz"):
        stem = stem[:-7]
    if stem.endswith("_harmonized"):
        stem = stem[: -len("_harmonized")]
    if "." not in stem:
        raise ValueError(f"cannot infer meter/measurement from {column_name!r} or {path}")
    meter, measurement = stem.rsplit(".", 1)
    return meter, measurement


def scan_source_coverage(source_root: Path, start: datetime, end: datetime, tz: ZoneInfo) -> SourceCoverage:
    coverage = SourceCoverage(window_start=start, window_end=end)
    files = iter_source_files(source_root, start, end, tz)
    coverage.files_seen = len(files)
    for path in files:
        try:
            with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
                reader = csv.reader(handle)
                header = next(reader)
                if len(header) < 2:
                    raise ValueError("expected at least datetime and one value column")
                meter_urn, measurement = split_meter_measurement(header[1], path)
                for row_no, row in enumerate(reader, start=2):
                    if not row or not row[0].strip():
                        continue
                    event_ts = parse_iso_datetime(row[0], default_tz=ZoneInfo("UTC"))
                    if event_ts < start or event_ts >= end:
                        continue
                    coverage.source_rows += 1
                    exact_key = (event_ts, meter_urn, measurement)
                    coverage.event_exact_keys.add(exact_key)
                    for resolution in ("1min", "15min", "1h"):
                        coverage.rollup_keys[resolution].add((floor_time(event_ts, resolution), meter_urn, measurement))
            coverage.files_read += 1
        except Exception as exc:  # keep scanning other files and surface evidence.
            coverage.bad_files.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
    return coverage


def load_db_config_from_env() -> DbConfig:
    host = os.getenv("POSTGRES_HOST") or os.getenv("PGHOST") or os.getenv("DB_HOST")
    dbname = os.getenv("POSTGRES_DB") or os.getenv("PGDATABASE") or os.getenv("DB_NAME")
    user = os.getenv("POSTGRES_USER") or os.getenv("PGUSER") or os.getenv("DB_USER")
    password = os.getenv("POSTGRES_PASSWORD") or os.getenv("PGPASSWORD") or os.getenv("DB_PASSWORD")
    if not all([host, dbname, user, password]):
        missing = [
            name
            for name, value in {
                "POSTGRES_HOST/PGHOST/DB_HOST": host,
                "POSTGRES_DB/PGDATABASE/DB_NAME": dbname,
                "POSTGRES_USER/PGUSER/DB_USER": user,
                "POSTGRES_PASSWORD/PGPASSWORD/DB_PASSWORD": password,
            }.items()
            if not value
        ]
        raise RuntimeError("missing database environment variables: " + ", ".join(missing))
    assert host is not None
    assert dbname is not None
    assert user is not None
    assert password is not None
    return DbConfig(
        host=host,
        port=int(os.getenv("POSTGRES_PORT") or os.getenv("PGPORT") or os.getenv("DB_PORT") or "5432"),
        dbname=dbname,
        user=user,
        password=password,
        sslmode=os.getenv("POSTGRES_SSLMODE") or os.getenv("PGSSLMODE") or "disable",
    )


def connect_db(config: DbConfig, *, autocommit: bool = False):
    import psycopg  # type: ignore[import-not-found]

    return psycopg.connect(
        host=config.host,
        port=config.port,
        dbname=config.dbname,
        user=config.user,
        password=config.password,
        sslmode=config.sslmode,
        connect_timeout=10,
        autocommit=autocommit,
    )



def iter_time_chunks(start: datetime, end: datetime, minutes: int) -> Iterable[tuple[datetime, datetime]]:
    if minutes <= 0:
        raise ValueError("chunk minutes must be positive")
    chunk = timedelta(minutes=minutes)
    cursor = start
    while cursor < end:
        nxt = min(cursor + chunk, end)
        yield cursor, nxt
        cursor = nxt


def fetch_event_minute_counts(config: DbConfig, start: datetime, end: datetime, *, chunk_minutes: int = 15) -> Counter[Key]:
    counts: Counter[Key] = Counter()
    sql = """
        SELECT date_trunc('minute', event_ts) AS bucket_ts,
               meter_urn,
               measurement,
               count(DISTINCT event_ts) AS actual_points
        FROM live.measurement_event
        WHERE event_ts >= %s AND event_ts < %s
        GROUP BY 1, 2, 3
    """
    for chunk_start, chunk_end in iter_time_chunks(start, end, chunk_minutes):
        with connect_db(config, autocommit=True) as conn:
            conn.execute("SET statement_timeout = '45s'")
            conn.execute("SET max_parallel_workers_per_gather = 0")
            with conn.cursor() as cur:
                cur.execute(sql, (chunk_start, chunk_end))
                for bucket_ts, meter_urn, measurement, actual_points in cur.fetchall():
                    counts[(bucket_ts.astimezone(timezone.utc), meter_urn, measurement)] = int(actual_points)
    return counts


def fetch_live_rollup_keys(config: DbConfig, resolution: Resolution, start: datetime, end: datetime, *, chunk_minutes: int = 60) -> set[Key]:
    table_by_resolution = {
        "1min": "live.measurement_1min",
        "15min": "live.measurement_15min",
        "1h": "live.measurement_1h",
    }
    table = table_by_resolution[resolution]
    keys: set[Key] = set()
    sql = f"""
        SELECT bucket_ts, meter_urn, measurement
        FROM {table}
        WHERE bucket_ts >= %s AND bucket_ts < %s
    """
    for chunk_start, chunk_end in iter_time_chunks(start, end, chunk_minutes):
        with connect_db(config, autocommit=True) as conn:
            conn.execute("SET statement_timeout = '45s'")
            conn.execute("SET max_parallel_workers_per_gather = 0")
            with conn.cursor() as cur:
                cur.execute(sql, (chunk_start, chunk_end))
                for bucket_ts, meter_urn, measurement in cur.fetchall():
                    keys.add((bucket_ts.astimezone(timezone.utc), meter_urn, measurement))
    return keys


def build_coverage_issues(
    source: SourceCoverage,
    *,
    db_event_minute_counts: Counter[Key],
    db_rollup_keys: dict[Resolution, set[Key]],
    source_root: Path,
) -> list[CoverageIssue]:
    issues: list[CoverageIssue] = []
    source_event_minute_counts = source.event_minute_counts()
    base_details = {
        "worker_version": WORKER_VERSION,
        "source_root": str(source_root),
        "window_start": source.window_start.isoformat(),
        "window_end": source.window_end.isoformat(),
        "issue_mode": "active_current_gap",
    }
    for (bucket_ts, meter_urn, measurement), expected in sorted(source_event_minute_counts.items()):
        actual = db_event_minute_counts.get((bucket_ts, meter_urn, measurement), 0)
        if actual < expected:
            missing = expected - actual
            issues.append(
                CoverageIssue(
                    issue_kind="source_live_event_minute_under_count",
                    severity="block",
                    meter_urn=meter_urn,
                    measurement=measurement,
                    bucket_ts=bucket_ts,
                    resolution="event_minute",
                    reason="source exact events exceed live.measurement_event distinct events in this minute",
                    details={**base_details, "expected_points": expected, "actual_points": actual, "missing_points": missing},
                )
            )
    for resolution, source_keys in source.rollup_keys.items():
        actual_keys = db_rollup_keys.get(resolution, set())
        for bucket_ts, meter_urn, measurement in sorted(source_keys - actual_keys):
            issues.append(
                CoverageIssue(
                    issue_kind="source_live_rollup_missing_key",
                    severity="block",
                    meter_urn=meter_urn,
                    measurement=measurement,
                    bucket_ts=bucket_ts,
                    resolution=resolution,
                    reason=f"source distinct {resolution} key is absent from live.measurement_{resolution}",
                    details={**base_details, "expected_points": 1, "actual_points": 0, "missing_points": 1},
                )
            )
    return issues


def insert_live_issues(config: DbConfig, issues: list[CoverageIssue]) -> dict[str, int]:
    if not issues:
        return {"attempted": 0, "inserted": 0, "skipped_existing": 0}
    insert_sql = """
        INSERT INTO qa.live_issue (
            issue_kind, severity, meter_urn, measurement, event_id, bucket_ts,
            resolution, policy_id, policy_version, reason, details
        )
        VALUES (
            %(issue_kind)s, %(severity)s, %(meter_urn)s, %(measurement)s, %(event_id)s,
            %(bucket_ts)s, %(resolution)s, %(policy_id)s, %(policy_version)s,
            %(reason)s, %(details)s::jsonb
        )
    """
    exists_sql = "SELECT 1 FROM qa.live_issue WHERE details->>'coverage_key' = %s LIMIT 1"
    attempted = inserted = skipped = 0
    with connect_db(config) as conn:
        conn.execute("SET statement_timeout = '60s'")
        with conn.cursor() as cur:
            for issue in issues:
                attempted += 1
                row = issue.to_live_issue_row()
                coverage_key = row["details"]["coverage_key"]  # type: ignore[index]
                cur.execute(exists_sql, (coverage_key,))
                if cur.fetchone():
                    skipped += 1
                    continue
                row["details"] = json.dumps(row["details"], ensure_ascii=False, sort_keys=True)
                cur.execute(insert_sql, row)
                inserted += 1
        conn.commit()
    return {"attempted": attempted, "inserted": inserted, "skipped_existing": skipped}


def write_evidence(output_dir: Path, summary: dict[str, object], issues: list[CoverageIssue]) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "coverage_summary.json"
    issues_path = output_dir / "coverage_issues.jsonl"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with issues_path.open("w", encoding="utf-8") as handle:
        for issue in issues:
            handle.write(json.dumps(issue.to_json(), ensure_ascii=False, sort_keys=True) + "\n")
    return {"summary_path": str(summary_path), "issues_path": str(issues_path)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare CMS source day-cache coverage with live DB and optionally write qa.live_issue rows.")
    parser.add_argument("--source-root", default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--window-start", required=True, help="Inclusive ISO timestamp. Naive values use --timezone.")
    parser.add_argument("--window-end", required=True, help="Exclusive ISO timestamp. Naive values use --timezone.")
    parser.add_argument("--timezone", default="Asia/Seoul")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--execute-write", action="store_true", help="Actually insert active current gaps into qa.live_issue.")
    parser.add_argument("--skip-event-minute", action="store_true", help="Skip source-vs-live event minute under-count comparison.")
    parser.add_argument("--skip-rollups", action="store_true", help="Skip live.measurement_1min/15min/1h missing-key comparison.")
    parser.add_argument("--db-event-chunk-minutes", type=int, default=15, help="DB chunk size for live.measurement_event coverage reads.")
    parser.add_argument("--db-rollup-chunk-minutes", type=int, default=60, help="DB chunk size for live rollup coverage reads.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    tz = ZoneInfo(args.timezone)
    start = parse_iso_datetime(args.window_start, default_tz=tz)
    end = parse_iso_datetime(args.window_end, default_tz=tz)
    if end <= start:
        raise SystemExit("--window-end must be after --window-start")
    source_root = Path(args.source_root)
    output_dir = Path(args.output_dir)

    source = scan_source_coverage(source_root, start, end, tz)
    db_config = load_db_config_from_env()

    db_event_minute_counts: Counter[Key] = Counter()
    db_rollup_keys: dict[Resolution, set[Key]] = {"1min": set(), "15min": set(), "1h": set()}
    if not args.skip_event_minute:
        db_event_minute_counts = fetch_event_minute_counts(
            db_config,
            start,
            end,
            chunk_minutes=args.db_event_chunk_minutes,
        )
    if not args.skip_rollups:
        for resolution in ("1min", "15min", "1h"):
            db_rollup_keys[resolution] = fetch_live_rollup_keys(
                db_config,
                resolution,
                start,
                end,
                chunk_minutes=args.db_rollup_chunk_minutes,
            )

    issues = build_coverage_issues(
        source,
        db_event_minute_counts=db_event_minute_counts,
        db_rollup_keys=db_rollup_keys,
        source_root=source_root,
    )

    write_result = {"attempted": 0, "inserted": 0, "skipped_existing": 0}
    if args.execute_write:
        write_result = insert_live_issues(db_config, issues)

    summary: dict[str, object] = {
        "worker_version": WORKER_VERSION,
        "mode": "execute_write" if args.execute_write else "dry_run",
        "target_table": "qa.live_issue",
        "source_root": str(source_root),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "files_seen": source.files_seen,
        "files_read": source.files_read,
        "bad_files": source.bad_files,
        "source_rows": source.source_rows,
        "source_event_exact_keys": len(source.event_exact_keys),
        "source_rollup_keys": {resolution: len(keys) for resolution, keys in source.rollup_keys.items()},
        "db_event_minute_keys": len(db_event_minute_counts),
        "db_rollup_keys": {resolution: len(keys) for resolution, keys in db_rollup_keys.items()},
        "issue_rows": len(issues),
        "issue_counts": dict(Counter(issue.issue_kind for issue in issues)),
        "write_result": write_result,
        "policy": "A: historical repaired gaps remain audit evidence; only currently observed source-vs-live gaps become active qa.live_issue rows.",
    }
    paths = write_evidence(output_dir, summary, issues)
    summary.update(paths)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not source.bad_files else 2


if __name__ == "__main__":
    raise SystemExit(main())
