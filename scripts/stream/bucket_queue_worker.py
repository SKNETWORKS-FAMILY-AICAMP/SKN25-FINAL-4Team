#!/usr/bin/env python3
"""Run a bounded ``live.bucket_queue`` worker pass.

Default mode is dry-run: build the import-safe SQL plan and print a redacted
runtime shape without opening PostgreSQL clients or attempting writes. Runtime is
explicitly gated by ``CMS_ENABLE_LIVE_BUCKET_QUEUE_WORKER=1``.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any

from cms.data.live_bucket_queue_runner import make_live_bucket_queue_worker_command
from cms.data.runtime_postgres import PsycopgLiveBucketQueueWorker, load_postgres_config_from_env
from cms.workflow.replay_clock import replay_virtual_now

WORKER_ENABLE_ENV = "CMS_ENABLE_LIVE_BUCKET_QUEUE_WORKER"


def main() -> int:
    args = parse_args()
    virtual_now = replay_virtual_now(env=os.environ)
    command = make_live_bucket_queue_worker_command(
        batch_size=args.batch_size,
        worker_id=args.worker_id,
        job_kinds=tuple(args.job_kind) or None,
        resolutions=tuple(args.resolution) or None,
        min_coverage_ratio=args.min_coverage_ratio,
        max_bucket_ts=virtual_now,
    )

    if not args.runtime or args.dry_run:
        return _finish(
            {
                "ok": True,
                "service": "live_bucket_queue_worker",
                "mode": "dry_run",
                "external_clients_started": False,
                "postgres_write_attempted": False,
                "adapter_status": command.runtime_adapter_status,
                "source_table": command.source_table,
                "source_detail_table": command.source_detail_table,
                "output_tables": command.output_tables,
                "forbidden_output_tables": command.forbidden_output_tables,
                "job_specs": command.job_specs,
                "batch_size": command.params["batch_size"],
                "worker_id": command.params["worker_id"],
                "min_coverage_ratio": command.params["min_coverage_ratio"],
                "replay_virtual_now": virtual_now,
                "count_columns": command.count_columns,
                "config": _redacted_env_config(dict(os.environ)),
            },
            json_only=args.json,
            exit_code=0,
        )

    if os.environ.get(WORKER_ENABLE_ENV) != "1":
        return _finish(
            {
                "ok": False,
                "blocked": True,
                "service": "live_bucket_queue_worker",
                "mode": "runtime",
                "error": f"runtime live.bucket_queue worker requires {WORKER_ENABLE_ENV}=1",
                "external_clients_started": False,
                "postgres_write_attempted": False,
                "adapter_status": command.runtime_adapter_status,
                "source_table": command.source_table,
                "output_tables": command.output_tables,
                "batch_size": command.params["batch_size"],
                "replay_virtual_now": virtual_now,
            },
            json_only=args.json,
            exit_code=2,
        )

    config = None
    try:
        config = load_postgres_config_from_env(dict(os.environ))
        result = PsycopgLiveBucketQueueWorker(config).run_once(command)
    except Exception as exc:  # noqa: BLE001 - CLI should return a redacted operational error packet.
        return _finish(
            {
                "ok": False,
                "blocked": False,
                "service": "live_bucket_queue_worker",
                "mode": "runtime",
                "error": _redact(str(exc), os.environ.get("POSTGRES_PASSWORD")),
                "external_clients_started": config is not None,
                "postgres_write_attempted": config is not None,
                "adapter_status": "psycopg_runtime_adapter",
                "source_table": command.source_table,
                "source_detail_table": command.source_detail_table,
                "output_tables": command.output_tables,
                "job_specs": command.job_specs,
                "batch_size": command.params["batch_size"],
                "replay_virtual_now": virtual_now,
                "count_columns": command.count_columns,
                "config": _redacted_env_config(dict(os.environ)),
            },
            json_only=args.json,
            exit_code=3,
        )

    return _finish(
        {
            "ok": True,
            "blocked": False,
            "service": "live_bucket_queue_worker",
            "mode": "runtime",
            "external_clients_started": True,
            "postgres_write_attempted": True,
            "adapter_status": "psycopg_runtime_adapter",
            "source_table": command.source_table,
            "source_detail_table": command.source_detail_table,
            "output_tables": command.output_tables,
            "job_specs": command.job_specs,
            "batch_size": command.params["batch_size"],
            "replay_virtual_now": virtual_now,
            "count_columns": command.count_columns,
            "result": result,
            "config": _redacted_env_config(dict(os.environ)),
        },
        json_only=args.json,
        exit_code=0,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build or run a bounded live.bucket_queue worker pass")
    parser.add_argument("--dry-run", action="store_true", help="build and report the worker SQL plan without opening PostgreSQL clients (default)")
    parser.add_argument("--runtime", action="store_true", help=f"enter gated runtime mode; requires {WORKER_ENABLE_ENV}=1")
    parser.add_argument("--batch-size", type=_positive_int, default=100, help="maximum queue rows to claim; must be positive")
    parser.add_argument("--worker-id", default="live-bucket-queue-worker-dry-run", help="worker identifier stamped into the SQL plan")
    parser.add_argument("--job-kind", action="append", choices=("mean_rollup", "peak_feature"), default=[], help="optional job_kind filter; may repeat")
    parser.add_argument("--resolution", action="append", choices=("15min", "1h"), default=[], help="optional resolution filter; may repeat")
    parser.add_argument("--min-coverage-ratio", type=_coverage_ratio, default=0.0, help="QA eligibility threshold between 0 and 1")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON only")
    return parser.parse_args()


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def _coverage_ratio(raw: str) -> float:
    value = float(raw)
    if not 0 <= value <= 1:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return value


def _redacted_env_config(env: dict[str, str]) -> dict[str, object]:
    return {
        "runtime_profile": env.get("CMS_RUNTIME_PROFILE", ""),
        "postgres_host_configured": bool(env.get("POSTGRES_HOST")),
        "postgres_port": int(env.get("POSTGRES_PORT", "5432")),
        "postgres_db_configured": bool(env.get("POSTGRES_DB")),
        "postgres_user_configured": bool(env.get("POSTGRES_USER")),
        "postgres_password_configured": bool(env.get("POSTGRES_PASSWORD")),
        "postgres_sslmode": env.get("POSTGRES_SSLMODE", "disable"),
    }


def _redact(message: str, secret: str | None) -> str:
    if not secret:
        return message
    return message.replace(secret, "[REDACTED]")


def _finish(payload: dict[str, Any], *, json_only: bool, exit_code: int) -> int:
    text = json.dumps(_json_safe(payload), ensure_ascii=False, indent=None if json_only else 2, sort_keys=True)
    print(text, flush=True)
    return exit_code


def _json_safe(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


if __name__ == "__main__":
    raise SystemExit(main())
