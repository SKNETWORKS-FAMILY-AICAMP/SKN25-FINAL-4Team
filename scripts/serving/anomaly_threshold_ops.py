#!/usr/bin/env python3
"""Build, validate, promote, and roll back anomaly warning thresholds.

This is a one-shot operational batch script. It does not run as a service and
does not write to the database. Threshold candidates are written under
``artifacts/anomaly_threshold_candidates`` by default, then promoted explicitly
with ``promote --confirm``.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

try:
    from cms.modeling.anomaly.mapping import METER_MAP
except Exception as exc:  # pragma: no cover - import guard
    raise SystemExit(f"failed to import anomaly meter mapping: {exc}") from exc


RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
REQUIRED_COLUMNS = ("meter_urn", "hour", "p_lower", "p_upper", "n_samples", "low_sample")
DEFAULT_VAL_START = "2022-01-01T00:00:00+00:00"
DEFAULT_VAL_END = "2023-01-01T00:00:00+00:00"


class ThresholdOpsError(RuntimeError):
    pass


@dataclass(frozen=True)
class ThresholdPaths:
    artifacts_root: Path
    deployed_file: Path
    candidates_root: Path
    archives_root: Path


def utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def read_env_file(path: Path) -> None:
    if not path.is_file():
        raise ThresholdOpsError(f"env file not found: {path}")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def validate_run_id(run_id: str) -> str:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ThresholdOpsError("run_id must use only letters, numbers, dot, underscore, or hyphen")
    return run_id


def paths_from_env() -> ThresholdPaths:
    default_root = PROJECT_ROOT / "artifacts" / "anomaly"
    artifacts_root = Path(
        os.getenv("ANOMALY_ARTIFACTS_DIR")
        or os.getenv("MODEL_ARTIFACTS_DIR")
        or str(default_root)
    ).resolve()
    return ThresholdPaths(
        artifacts_root=artifacts_root,
        deployed_file=(artifacts_root / "thresholds" / "val_thresholds.csv").resolve(),
        candidates_root=Path(
            os.getenv("ANOMALY_THRESHOLD_CANDIDATES_ROOT")
            or str(artifacts_root.parent / "anomaly_threshold_candidates")
        ).resolve(),
        archives_root=Path(
            os.getenv("ANOMALY_THRESHOLD_ARCHIVES_ROOT")
            or str(artifacts_root.parent / "anomaly_threshold_archives")
        ).resolve(),
    )


def selected_meters(scope: str) -> list[str]:
    meters = sorted(meter for meter, info in METER_MAP.items() if info.get("action") == "predict")
    if scope == "all":
        return meters
    if scope == "training":
        return sorted({info["model_urn"] for info in METER_MAP.values() if info.get("action") == "predict"})
    raise ThresholdOpsError(f"unsupported meter scope: {scope}")


def connect_db():
    try:
        import psycopg2
    except ImportError as exc:  # pragma: no cover - environment guard
        raise ThresholdOpsError("psycopg2 is required for DB-backed threshold build") from exc

    host = os.getenv("POSTGRES_HOST") or os.getenv("DB_HOST")
    port = os.getenv("POSTGRES_PORT") or os.getenv("DB_PORT") or "5432"
    dbname = os.getenv("POSTGRES_DB") or os.getenv("DB_NAME")
    user = os.getenv("POSTGRES_USER") or os.getenv("DB_USER")
    password = os.getenv("POSTGRES_PASSWORD") or os.getenv("DB_PASSWORD") or os.getenv("DB_PASS")
    missing = [
        name
        for name, value in {
            "POSTGRES_HOST/DB_HOST": host,
            "POSTGRES_DB/DB_NAME": dbname,
            "POSTGRES_USER/DB_USER": user,
            "POSTGRES_PASSWORD/DB_PASSWORD/DB_PASS": password,
        }.items()
        if not value
    ]
    if missing:
        raise ThresholdOpsError(f"missing DB environment values: {', '.join(missing)}")
    return psycopg2.connect(host=host, port=port, dbname=dbname, user=user, password=password)


def require_table_name(value: str) -> str:
    if not TABLE_RE.fullmatch(value):
        raise ThresholdOpsError(f"invalid schema-qualified table name: {value!r}")
    return value


def require_identifier(value: str) -> str:
    if not IDENT_RE.fullmatch(value):
        raise ThresholdOpsError(f"invalid SQL identifier: {value!r}")
    return value


def parse_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def bool_text(value: bool) -> str:
    return "True" if value else "False"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def latest_candidate_dir(paths: ThresholdPaths) -> Path:
    if not paths.candidates_root.is_dir():
        raise ThresholdOpsError(f"candidate root not found: {paths.candidates_root}")
    candidates = [path for path in paths.candidates_root.iterdir() if path.is_dir()]
    if not candidates:
        raise ThresholdOpsError(f"no threshold candidates under: {paths.candidates_root}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def candidate_dir(paths: ThresholdPaths, run_id: str | None) -> Path:
    if run_id:
        return paths.candidates_root / validate_run_id(run_id)
    return latest_candidate_dir(paths)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_candidate(args: argparse.Namespace) -> dict[str, Any]:
    paths = paths_from_env()
    run_id = validate_run_id(args.run_id or f"threshold_{utc_now_compact()}")
    candidate = paths.candidates_root / run_id
    if candidate.exists() and not args.overwrite:
        raise ThresholdOpsError(f"candidate already exists: {candidate}")
    if candidate.exists():
        shutil.rmtree(candidate)
    candidate.mkdir(parents=True, exist_ok=True)

    source_table = require_table_name(args.source_table or os.getenv("CMS_ANOMALY_SOURCE_TABLE", "mart.anomaly_feature_1h"))
    ts_column = require_identifier(args.ts_column or os.getenv("CMS_ANOMALY_SOURCE_TS_COLUMN", "bucket_ts"))
    value_column = require_identifier(args.value_column)
    meters = selected_meters(args.meter_scope)
    if not meters:
        raise ThresholdOpsError("no meters selected")

    query = f"""
WITH selected AS (
    SELECT unnest(%s::text[]) AS meter_urn
),
hours AS (
    SELECT generate_series(0, 23)::int AS hour
),
aggregated AS (
    SELECT
        s.meter_urn,
        h.hour,
        percentile_cont(%s) WITHIN GROUP (ORDER BY t.{value_column}) AS p_lower,
        percentile_cont(%s) WITHIN GROUP (ORDER BY t.{value_column}) AS p_upper,
        count(t.{value_column})::int AS n_samples
    FROM selected s
    CROSS JOIN hours h
    LEFT JOIN {source_table} t
      ON t.meter_urn = s.meter_urn
     AND extract(hour from t.{ts_column} AT TIME ZONE 'UTC')::int = h.hour
     AND t.{ts_column} >= %s::timestamptz
     AND t.{ts_column} < %s::timestamptz
     AND t.{value_column} IS NOT NULL
    GROUP BY s.meter_urn, h.hour
)
SELECT meter_urn, hour, p_lower, p_upper, n_samples
FROM aggregated
ORDER BY meter_urn, hour
"""
    rows: list[dict[str, Any]] = []
    with connect_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                query,
                (meters, args.lower_quantile, args.upper_quantile, args.start_ts, args.end_ts),
            )
            for meter_urn, hour, p_lower, p_upper, n_samples in cur.fetchall():
                lower = None if p_lower is None else float(p_lower)
                upper = None if p_upper is None else float(p_upper)
                if lower is not None and args.lower_floor is not None:
                    lower = max(float(args.lower_floor), lower)
                rows.append(
                    {
                        "meter_urn": meter_urn,
                        "hour": int(hour),
                        "p_lower": lower,
                        "p_upper": upper,
                        "n_samples": int(n_samples),
                        "low_sample": int(n_samples) < args.min_samples,
                    }
                )

    threshold_file = candidate / "val_thresholds.csv"
    with threshold_file.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=REQUIRED_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "meter_urn": row["meter_urn"],
                    "hour": row["hour"],
                    "p_lower": "" if row["p_lower"] is None else row["p_lower"],
                    "p_upper": "" if row["p_upper"] is None else row["p_upper"],
                    "n_samples": row["n_samples"],
                    "low_sample": bool_text(bool(row["low_sample"])),
                }
            )

    missing_bounds = sum(1 for row in rows if row["p_lower"] is None or row["p_upper"] is None)
    low_sample_rows = sum(1 for row in rows if row["low_sample"])
    summary = {
        "status": "built",
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_table": source_table,
        "ts_column": ts_column,
        "value_column": value_column,
        "start_ts": args.start_ts,
        "end_ts": args.end_ts,
        "meter_scope": args.meter_scope,
        "meter_count": len(meters),
        "rows": len(rows),
        "expected_rows": len(meters) * 24,
        "missing_bounds": missing_bounds,
        "low_sample_rows": low_sample_rows,
        "lower_quantile": args.lower_quantile,
        "upper_quantile": args.upper_quantile,
        "lower_floor": args.lower_floor,
        "min_samples": args.min_samples,
        "candidate_dir": str(candidate),
        "threshold_file": str(threshold_file),
        "sha256": file_sha256(threshold_file),
    }
    write_json(candidate / "threshold_summary.json", summary)
    return summary


def read_threshold_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise ThresholdOpsError(f"threshold file not found: {path}")
    with path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != list(REQUIRED_COLUMNS):
            raise ThresholdOpsError(f"unexpected threshold columns: {reader.fieldnames}")
        return list(reader)


def validate_threshold_file(
    path: Path,
    *,
    meter_scope: str,
    min_samples: int,
    deployed_file: Path | None = None,
    warn_change_ratio: float = 1.0,
) -> dict[str, Any]:
    rows = read_threshold_rows(path)
    expected_meters = selected_meters(meter_scope)
    expected_pairs = {(meter, hour) for meter in expected_meters for hour in range(24)}
    seen_pairs: set[tuple[str, int]] = set()
    errors: list[str] = []
    warnings: list[str] = []
    low_sample_rows = 0
    missing_bound_rows = 0

    for index, row in enumerate(rows, start=2):
        meter = row["meter_urn"]
        try:
            hour = int(row["hour"])
        except ValueError:
            errors.append(f"line {index}: invalid hour {row['hour']!r}")
            continue
        pair = (meter, hour)
        if pair in seen_pairs:
            errors.append(f"line {index}: duplicate meter/hour {pair}")
        seen_pairs.add(pair)
        if pair not in expected_pairs:
            errors.append(f"line {index}: unexpected meter/hour {pair}")
        if hour < 0 or hour > 23:
            errors.append(f"line {index}: hour out of range: {hour}")
        try:
            p_lower = float(row["p_lower"])
            p_upper = float(row["p_upper"])
        except ValueError:
            missing_bound_rows += 1
            errors.append(f"line {index}: p_lower/p_upper must be numeric")
            continue
        if not math.isfinite(p_lower) or not math.isfinite(p_upper):
            errors.append(f"line {index}: p_lower/p_upper must be finite")
        if p_lower > p_upper:
            errors.append(f"line {index}: p_lower > p_upper")
        try:
            n_samples = int(row["n_samples"])
        except ValueError:
            errors.append(f"line {index}: invalid n_samples {row['n_samples']!r}")
            continue
        low_sample = parse_bool(row["low_sample"])
        if n_samples < min_samples or low_sample:
            low_sample_rows += 1

    missing_pairs = sorted(expected_pairs - seen_pairs)
    if missing_pairs:
        errors.append(f"missing meter/hour pairs: {len(missing_pairs)}")
    extra_pairs = sorted(seen_pairs - expected_pairs)
    if extra_pairs:
        errors.append(f"unexpected meter/hour pairs: {len(extra_pairs)}")
    if low_sample_rows:
        warnings.append(f"low_sample rows: {low_sample_rows}")

    max_relative_change: float | None = None
    changed_rows = 0
    if deployed_file and deployed_file.is_file():
        current_rows = {
            (row["meter_urn"], int(row["hour"])): row
            for row in read_threshold_rows(deployed_file)
            if row["hour"].isdigit()
        }
        for row in rows:
            try:
                key = (row["meter_urn"], int(row["hour"]))
                old = current_rows.get(key)
                if old is None:
                    continue
                old_upper = float(old["p_upper"])
                new_upper = float(row["p_upper"])
                denom = max(abs(old_upper), 1.0)
                change = abs(new_upper - old_upper) / denom
            except Exception:
                continue
            max_relative_change = change if max_relative_change is None else max(max_relative_change, change)
            if change > warn_change_ratio:
                changed_rows += 1
        if changed_rows:
            warnings.append(f"large p_upper relative change rows: {changed_rows}")

    result = {
        "result": "fail" if errors else ("warn" if warnings else "pass"),
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "threshold_file": str(path),
        "meter_scope": meter_scope,
        "rows": len(rows),
        "expected_rows": len(expected_pairs),
        "meter_count": len({row["meter_urn"] for row in rows}),
        "low_sample_rows": low_sample_rows,
        "missing_bound_rows": missing_bound_rows,
        "max_relative_change": max_relative_change,
        "errors": errors[:100],
        "warnings": warnings,
        "sha256": file_sha256(path),
    }
    return result


def validate_candidate(args: argparse.Namespace) -> dict[str, Any]:
    paths = paths_from_env()
    candidate = candidate_dir(paths, args.run_id)
    threshold_file = candidate / "val_thresholds.csv"
    result = validate_threshold_file(
        threshold_file,
        meter_scope=args.meter_scope,
        min_samples=args.min_samples,
        deployed_file=paths.deployed_file,
        warn_change_ratio=args.warn_change_ratio,
    )
    write_json(candidate / "validation.json", result)
    return result | {"candidate_dir": str(candidate)}


def promote_candidate(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise ThresholdOpsError("promote requires --confirm")
    paths = paths_from_env()
    candidate = candidate_dir(paths, args.run_id)
    threshold_file = candidate / "val_thresholds.csv"
    validation_path = candidate / "validation.json"
    if not validation_path.is_file() or args.revalidate:
        validation = validate_threshold_file(
            threshold_file,
            meter_scope=args.meter_scope,
            min_samples=args.min_samples,
            deployed_file=paths.deployed_file,
            warn_change_ratio=args.warn_change_ratio,
        )
        write_json(validation_path, validation)
    else:
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
    if validation.get("result") == "fail":
        raise ThresholdOpsError("candidate validation failed; promotion refused")
    if validation.get("result") == "warn" and not args.allow_warn:
        raise ThresholdOpsError("candidate validation is warn; pass --allow-warn to promote")

    paths.deployed_file.parent.mkdir(parents=True, exist_ok=True)
    paths.archives_root.mkdir(parents=True, exist_ok=True)
    stamp = utc_now_compact()
    archive_root = paths.archives_root / f"{stamp}_{candidate.name}_previous"
    if archive_root.exists():
        raise ThresholdOpsError(f"archive already exists: {archive_root}")
    archive_root.mkdir(parents=True)
    backup_file = None
    if paths.deployed_file.exists():
        backup_file = archive_root / "val_thresholds.csv"
        shutil.copy2(paths.deployed_file, backup_file)

    tmp_file = paths.deployed_file.with_name(".val_thresholds.csv.promoting")
    shutil.copy2(threshold_file, tmp_file)
    try:
        tmp_file.replace(paths.deployed_file)
    except Exception:
        tmp_file.unlink(missing_ok=True)
        if backup_file is not None:
            shutil.copy2(backup_file, paths.deployed_file)
        raise

    result = {
        "status": "promoted",
        "promoted_at": datetime.now(timezone.utc).isoformat(),
        "candidate_dir": str(candidate),
        "deployed_file": str(paths.deployed_file),
        "archive_root": str(archive_root),
        "backup_file": str(backup_file) if backup_file else None,
        "validation": validation,
        "sha256": file_sha256(paths.deployed_file),
    }
    write_json(archive_root / "promotion.json", result)
    write_json(candidate / "promotion.json", result)
    return result


def rollback(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise ThresholdOpsError("rollback requires --confirm")
    paths = paths_from_env()
    archive_root = Path(args.archive_root).resolve()
    backup_file = archive_root / "val_thresholds.csv"
    if not backup_file.is_file():
        raise ThresholdOpsError(f"archive threshold file not found: {backup_file}")
    validation = validate_threshold_file(
        backup_file,
        meter_scope=args.meter_scope,
        min_samples=args.min_samples,
        deployed_file=None,
    )
    if validation["result"] == "fail":
        raise ThresholdOpsError("archive threshold validation failed; rollback refused")
    paths.deployed_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup_file, paths.deployed_file)
    result = {
        "status": "rolled_back",
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
        "archive_root": str(archive_root),
        "deployed_file": str(paths.deployed_file),
        "validation": validation,
        "sha256": file_sha256(paths.deployed_file),
    }
    write_json(archive_root / f"rollback_{utc_now_compact()}.json", result)
    return result


def status(_: argparse.Namespace) -> dict[str, Any]:
    paths = paths_from_env()
    candidates = []
    if paths.candidates_root.is_dir():
        candidates = sorted(
            (path.name for path in paths.candidates_root.iterdir() if path.is_dir()),
            reverse=True,
        )[:10]
    archives = []
    if paths.archives_root.is_dir():
        archives = sorted(
            (path.name for path in paths.archives_root.iterdir() if path.is_dir()),
            reverse=True,
        )[:10]
    return {
        "artifacts_root": str(paths.artifacts_root),
        "deployed_file": str(paths.deployed_file),
        "deployed_exists": paths.deployed_file.is_file(),
        "deployed_sha256": file_sha256(paths.deployed_file) if paths.deployed_file.is_file() else None,
        "candidates_root": str(paths.candidates_root),
        "latest_candidates": candidates,
        "archives_root": str(paths.archives_root),
        "latest_archives": archives,
    }


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--env-file",
        type=Path,
        default=PROJECT_ROOT / "docker" / "model_serving.env",
        help="Environment file with DB and artifact paths.",
    )


def add_validation_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--meter-scope", choices=("all", "training"), default="all")
    parser.add_argument("--min-samples", type=int, default=100)
    parser.add_argument("--warn-change-ratio", type=float, default=1.0)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build a threshold candidate from DB data.")
    add_common_args(build)
    build.add_argument("--run-id")
    build.add_argument("--overwrite", action="store_true")
    build.add_argument("--meter-scope", choices=("all", "training"), default="all")
    build.add_argument("--source-table")
    build.add_argument("--ts-column")
    build.add_argument("--value-column", default="p_value")
    build.add_argument("--start-ts", default=DEFAULT_VAL_START)
    build.add_argument("--end-ts", default=DEFAULT_VAL_END)
    build.add_argument("--lower-quantile", type=float, default=0.02)
    build.add_argument("--upper-quantile", type=float, default=0.98)
    build.add_argument("--lower-floor", type=float, default=-50.0)
    build.add_argument("--min-samples", type=int, default=100)
    build.set_defaults(func=build_candidate)

    validate = subparsers.add_parser("validate", help="Validate a threshold candidate.")
    add_common_args(validate)
    add_validation_args(validate)
    validate.add_argument("--run-id")
    validate.set_defaults(func=validate_candidate)

    promote = subparsers.add_parser("promote", help="Promote a validated threshold candidate.")
    add_common_args(promote)
    add_validation_args(promote)
    promote.add_argument("--run-id")
    promote.add_argument("--confirm", action="store_true")
    promote.add_argument("--allow-warn", action="store_true")
    promote.add_argument("--revalidate", action="store_true")
    promote.set_defaults(func=promote_candidate)

    roll = subparsers.add_parser("rollback", help="Roll back to an archived threshold file.")
    add_common_args(roll)
    add_validation_args(roll)
    roll.add_argument("--archive-root", required=True)
    roll.add_argument("--confirm", action="store_true")
    roll.set_defaults(func=rollback)

    show = subparsers.add_parser("status", help="Show threshold deployment/candidate status.")
    add_common_args(show)
    show.set_defaults(func=status)
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.env_file:
        read_env_file(args.env_file)
    try:
        result = args.func(args)
    except ThresholdOpsError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
