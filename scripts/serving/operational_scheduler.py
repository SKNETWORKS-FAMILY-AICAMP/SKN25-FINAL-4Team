#!/usr/bin/env python3
"""Run CMS operational serving workers inside the service runtime.

This entrypoint is for Docker/Airflow/systemd-style service execution, not
Hermes automation. It performs bounded, idempotent passes for:

- live.measurement_1min + live.promotion_check -> canonical.measurement_1min/15min/1h
- live.measurement_1h -> mart.anomaly_feature_1h
- mart.peak_feature_15min -> P-Max mart/ops/qa model outputs
- mart.anomaly_feature_1h -> anomaly warning mart/ops/qa model outputs

Runtime writes still require the underlying lane-specific double gates.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any

import psycopg

from cms.data.anomaly_feature_materializer import (
    execute_anomaly_feature_materialization_command,
    make_anomaly_feature_materialization_command,
)
from cms.data.canonical_promotion_runner import execute_canonical_promotion_command, make_canonical_promotion_command
from cms.data.runtime_postgres import load_postgres_config_from_env
try:
    from cms.data.worker_heartbeat import (  # type: ignore[assignment]
        make_worker_event_log_insert_command,
        make_worker_heartbeat_upsert_command,
    )
except ImportError:  # pragma: no cover - production hot-patch fallback for images without the helper module.
    def make_worker_heartbeat_upsert_command(
        *,
        worker_name: str,
        status: str,
        processed_count: int = 0,
        failed_count: int = 0,
        restart_count: int = 0,
        last_error: str | None = None,
        details: dict[str, object] | None = None,
    ) -> Any:
        details_json = json.dumps(dict(details or {}), ensure_ascii=False, sort_keys=True)

        class _WorkerHeartbeatCommand:
            sql = """
INSERT INTO ops.worker_heartbeat (
    worker_name,
    status,
    heartbeat_at,
    updated_at,
    last_error,
    restart_count,
    processed_count,
    failed_count,
    details
)
VALUES (
    %(worker_name)s,
    %(status)s,
    now(),
    now(),
    %(last_error)s,
    %(restart_count)s,
    %(processed_count)s,
    %(failed_count)s,
    %(details_json)s::jsonb
)
ON CONFLICT (worker_name) DO UPDATE SET
    status = EXCLUDED.status,
    heartbeat_at = EXCLUDED.heartbeat_at,
    updated_at = now(),
    last_error = EXCLUDED.last_error,
    restart_count = ops.worker_heartbeat.restart_count + EXCLUDED.restart_count,
    processed_count = ops.worker_heartbeat.processed_count + EXCLUDED.processed_count,
    failed_count = ops.worker_heartbeat.failed_count + EXCLUDED.failed_count,
    details = EXCLUDED.details
""".strip()
            params = {
                "worker_name": worker_name,
                "status": status,
                "last_error": last_error,
                "restart_count": restart_count,
                "processed_count": processed_count,
                "failed_count": failed_count,
                "details_json": details_json,
            }

        return _WorkerHeartbeatCommand()

    def make_worker_event_log_insert_command(
        *,
        worker_name: str,
        status: str,
        processed_count: int = 0,
        failed_count: int = 0,
        restart_count: int = 0,
        last_error: str | None = None,
        details: dict[str, object] | None = None,
    ) -> Any:
        details_json = json.dumps(dict(details or {}), ensure_ascii=False, sort_keys=True)

        class _WorkerEventLogCommand:
            sql = """
INSERT INTO ops.worker_event_log (
    worker_name,
    event_at,
    status,
    processed_delta,
    failed_delta,
    restart_delta,
    error_message,
    details
)
VALUES (
    %(worker_name)s,
    now(),
    %(status)s,
    %(processed_count)s,
    %(failed_count)s,
    %(restart_count)s,
    %(last_error)s,
    %(details_json)s::jsonb
)
""".strip()
            params = {
                "worker_name": worker_name,
                "status": status,
                "last_error": last_error,
                "restart_count": restart_count,
                "processed_count": processed_count,
                "failed_count": failed_count,
                "details_json": details_json,
            }

        return _WorkerEventLogCommand()
from cms.workflow.replay_clock import replay_virtual_now

PMAX_METERS = ("V.Z81", "V.Z82", "H2.Z351", "H2.Z361")
PMAX_MEASUREMENTS = ("P", "U1", "PF")
PMAX_REQUIRED_WINDOWS = 288
PMAX_REFERENCE_WARM_START_EXCLUSIVE_END_TS = "2023-12-01T00:00:00+00:00"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run CMS operational service workers")
    parser.add_argument("--lane", choices=("all", "canonical", "anomaly-feature", "anomaly", "pmax", "hybrid-model-serving"), default="all")
    parser.add_argument("--loop", action="store_true", help="Run forever with sleep between bounded passes")
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--canonical-batch-size", type=int, default=250)
    parser.add_argument("--anomaly-batch-size", type=int, default=2000)
    parser.add_argument("--model-serving-interval-seconds", type=int, default=900)
    parser.add_argument("--pmax-artifact-root", default=os.environ.get("PMAX_ARTIFACT_ROOT", "/artifacts/pmax"))
    parser.add_argument("--anomaly-artifact-root", default=os.environ.get("ANOMALY_ARTIFACT_ROOT", "/artifacts/anomaly"))
    parser.add_argument("--anomaly-reference-meter", action="append", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    heartbeat_worker_name = scheduler_worker_name(args.lane)
    max_consecutive_failures = int(os.environ.get("CMS_SCHEDULER_MAX_CONSECUTIVE_FAILURES", "5"))
    last_model_serving_at: float | None = None
    consecutive_failures = 0
    write_scheduler_heartbeat(
        worker_name=heartbeat_worker_name,
        status="starting",
        details={"lane": args.lane, "loop": args.loop},
    )
    while True:
        pass_start = perf_counter()
        try:
            payloads, last_model_serving_at = run_scheduler_pass(args, last_model_serving_at=last_model_serving_at)
        except Exception as exc:  # noqa: BLE001 - service loop must report scheduler-level failures instead of dying silently.
            consecutive_failures += 1
            error = redact_error(str(exc))
            status = "failed" if not args.loop or consecutive_failures >= max_consecutive_failures else "degraded"
            failure_payload = {
                "lane": args.lane,
                "ok": False,
                "changed": False,
                "reason": "scheduler_pass_exception",
                "consecutive_failures": consecutive_failures,
                "max_consecutive_failures": max_consecutive_failures,
                "error": error,
            }
            print(json.dumps(failure_payload, ensure_ascii=False, default=str, sort_keys=True), flush=True)
            write_scheduler_heartbeat(
                worker_name=heartbeat_worker_name,
                status=status,
                failed_count=1,
                last_error=error,
                details={**failure_payload, "batch_elapsed_ms": elapsed_ms(pass_start)},
            )
            if status == "failed":
                return 3
            time.sleep(max(1, args.interval_seconds))
            continue

        emit_unchanged = os.environ.get("CMS_SCHEDULER_EMIT_UNCHANGED", "1") != "0"
        emitted = payloads if emit_unchanged else [payload for payload in payloads if payload.get("changed") or payload.get("ok") is False]
        for payload in emitted:
            print(json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True), flush=True)

        ok = all(payload.get("ok", False) for payload in payloads)
        consecutive_failures = 0 if ok else consecutive_failures + 1
        heartbeat_status = "running" if ok else "degraded"
        write_scheduler_heartbeat(
            worker_name=heartbeat_worker_name,
            status=heartbeat_status,
            processed_count=scheduler_processed_count(payloads),
            failed_count=0 if ok else 1,
            last_error=None if ok else scheduler_last_error(payloads),
            details={
                "lane": args.lane,
                "loop": args.loop,
                "batch_elapsed_ms": elapsed_ms(pass_start),
                "consecutive_failures": consecutive_failures,
                "payloads": compact_payloads(payloads),
            },
        )
        if not args.loop:
            return 0 if ok else 2
        if consecutive_failures >= max_consecutive_failures:
            write_scheduler_heartbeat(
                worker_name=heartbeat_worker_name,
                status="failed",
                failed_count=1,
                last_error=scheduler_last_error(payloads) or "scheduler reached max consecutive failures",
                details={"lane": args.lane, "consecutive_failures": consecutive_failures},
            )
            return 3
        time.sleep(max(1, args.interval_seconds))


def connect() -> psycopg.Connection[Any]:
    config = load_postgres_config_from_env(dict(os.environ))
    return psycopg.connect(**config.connect_kwargs())


def run_scheduler_pass(args: argparse.Namespace, *, last_model_serving_at: float | None) -> tuple[list[dict[str, Any]], float | None]:
    payloads: list[dict[str, Any]] = []
    next_last_model_serving_at = last_model_serving_at
    if args.lane in ("all", "canonical"):
        payloads.append(run_canonical(batch_size=args.canonical_batch_size))
    if args.lane in ("all", "anomaly-feature"):
        payloads.append(run_anomaly_feature(batch_size=args.anomaly_batch_size))
    if args.lane in ("all", "pmax", "hybrid-model-serving"):
        now = time.monotonic()
        due = next_last_model_serving_at is None or now - next_last_model_serving_at >= args.model_serving_interval_seconds
        if due:
            payloads.append(
                run_pmax_serving(
                    pmax_artifact_root=args.pmax_artifact_root,
                    anomaly_artifact_root=args.anomaly_artifact_root,
                    anomaly_reference_meters=tuple(args.anomaly_reference_meter or ("H1.K11",)),
                )
            )
            next_last_model_serving_at = now
    if args.lane in ("all", "anomaly"):
        payloads.append(run_anomaly_serving(anomaly_artifact_root=args.anomaly_artifact_root))
    return payloads, next_last_model_serving_at


def scheduler_worker_name(lane: str) -> str:
    suffix = lane.replace("-", "_")
    if suffix == "all":
        suffix = "all_lanes"
    return f"{suffix}_scheduler"


def elapsed_ms(start: float) -> int:
    return max(0, round((perf_counter() - start) * 1000))


def scheduler_processed_count(payloads: Iterable[dict[str, Any]]) -> int:
    total = 0
    for payload in payloads:
        for key in (
            "written_rows",
            "materialized_count",
            "marked_promotion_check_count",
            "promoted_15min_count",
            "promoted_1h_count",
        ):
            value = payload.get(key)
            if isinstance(value, int) and value > 0:
                total += value
    return total


def scheduler_last_error(payloads: Iterable[dict[str, Any]]) -> str | None:
    for payload in payloads:
        if payload.get("ok") is False:
            errors = payload.get("errors")
            if isinstance(errors, list) and errors:
                return redact_error(str(errors[0]))
            if errors:
                return redact_error(str(errors))
            reason = payload.get("reason")
            if reason:
                return str(reason)
    return None


def compact_payloads(payloads: Iterable[dict[str, Any]]) -> list[dict[str, object]]:
    keep_keys = (
        "lane",
        "ok",
        "changed",
        "reason",
        "base_ts",
        "forecast_origin_ts",
        "run_id",
        "returncode",
        "written_rows",
        "pmax_prediction_count",
        "pmax_feature_count",
        "pmax_readiness_issue_count",
        "anomaly_prediction_count",
        "materialized_count",
        "promotion_check_count",
        "marked_promotion_check_count",
        "promoted_15min_count",
        "promoted_1h_count",
        "errors",
    )
    compacted: list[dict[str, object]] = []
    for payload in payloads:
        compacted.append({key: json_safe(payload[key]) for key in keep_keys if key in payload})
    return compacted


def json_safe(value: Any) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def write_scheduler_heartbeat(
    *,
    worker_name: str,
    status: str,
    processed_count: int = 0,
    failed_count: int = 0,
    last_error: str | None = None,
    details: dict[str, object] | None = None,
) -> None:
    """Best-effort ``ops.worker_heartbeat`` update for the scheduler loop."""

    try:
        heartbeat_command = make_worker_heartbeat_upsert_command(
            worker_name=worker_name,
            status=status,  # type: ignore[arg-type]
            processed_count=processed_count,
            failed_count=failed_count,
            last_error=redact_error(last_error) if last_error else None,
            details=json_safe(details or {}),  # type: ignore[arg-type]
        )
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(heartbeat_command.sql, heartbeat_command.params)  # type: ignore[arg-type]
            conn.commit()
        event_command = make_worker_event_log_insert_command(
            worker_name=worker_name,
            status=status,  # type: ignore[arg-type]
            processed_count=processed_count,
            failed_count=failed_count,
            last_error=redact_error(last_error) if last_error else None,
            details=json_safe(details or {}),  # type: ignore[arg-type]
        )
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(event_command.sql, event_command.params)  # type: ignore[arg-type]
            conn.commit()
    except Exception:  # noqa: BLE001 - observability must never stop scheduler work.
        return


def redact_error(message: str | None) -> str:
    if not message:
        return ""
    cleaned = message
    for key in ("POSTGRES_PASSWORD", "DB_PASSWORD"):
        secret = os.environ.get(key)
        if secret:
            cleaned = cleaned.replace(secret, "[REDACTED]")
    return cleaned


def run_canonical(*, batch_size: int) -> dict[str, Any]:
    promotion_id = os.environ.get("CMS_CANONICAL_PROMOTION_ID", "service_continuous_canonical")
    approval_id = os.environ.get("CMS_CANONICAL_APPROVAL_ID", "service_continuous_approval")
    virtual_now = replay_virtual_now(env=os.environ)
    command = make_canonical_promotion_command(
        promotion_id=promotion_id,
        approval_id=approval_id,
        batch_size=batch_size,
        min_coverage_ratio=float(os.environ.get("CMS_CANONICAL_MIN_COVERAGE_RATIO", "0.0")),
        max_bucket_ts=virtual_now,
    )
    result = execute_canonical_promotion_command(command, allow_write=True, env=os.environ)
    return {
        "lane": "canonical",
        "ok": result.ok,
        "changed": result.marked_promotion_check_count > 0,
        "promotion_id": promotion_id,
        "replay_virtual_now": virtual_now,
        "promotion_check_count": result.promotion_check_count,
        "marked_promotion_check_count": result.marked_promotion_check_count,
        "promoted_15min_count": result.promoted_15min_count,
        "promoted_1h_count": result.promoted_1h_count,
        "errors": list(result.errors),
    }


def run_anomaly_feature(*, batch_size: int) -> dict[str, Any]:
    with connect() as conn:
        conn.execute("SET default_transaction_read_only = on")
        conn.execute("SET statement_timeout = 15000")
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(max(bucket_ts), timestamptz '1970-01-01 00:00:00+00') FROM mart.anomaly_feature_1h")
            max_existing = cur.fetchone()[0]
            cur.execute(
                """
                WITH grouped AS (
                  SELECT bucket_ts, meter_urn,
                         bool_or(measurement='P') AS has_p,
                         bool_or(measurement='U1') AS has_u1,
                         bool_or(measurement='qv') AS has_qv,
                         bool_or(measurement='Tdiff') AS has_tdiff
                  FROM live.measurement_1h
                  WHERE measurement IN ('P','U1','qv','Tdiff')
                  GROUP BY bucket_ts, meter_urn
                )
                SELECT COALESCE(max(bucket_ts), timestamptz '1970-01-01 00:00:00+00')
                FROM grouped
                WHERE (has_p AND has_u1) OR (has_p AND has_qv AND has_tdiff)
                """
            )
            max_possible = cur.fetchone()[0]

    virtual_now = replay_virtual_now(env=os.environ)
    if virtual_now is not None:
        capped_end_ts = min(max_possible, virtual_now.astimezone(max_possible.tzinfo) - timedelta(hours=1))
    else:
        capped_end_ts = max_possible
    end_exclusive = capped_end_ts + timedelta(microseconds=1)
    if end_exclusive <= max_existing:
        return {
            "lane": "anomaly_feature",
            "ok": True,
            "changed": False,
            "max_existing": max_existing,
            "max_possible": max_possible,
            "replay_virtual_now": virtual_now,
        }

    command = make_anomaly_feature_materialization_command(
        start_ts=max_existing.astimezone(UTC),
        end_ts=end_exclusive.astimezone(UTC),
        source_table="live.measurement_1h",
        source_mode="live_observed",
        batch_size=batch_size,
    )
    result = execute_anomaly_feature_materialization_command(command, allow_write=True, env=os.environ)
    return {
        "lane": "anomaly_feature",
        "ok": result.ok,
        "changed": result.materialized_count > 0,
        "materialized_count": result.materialized_count,
        "max_existing": max_existing,
        "max_possible": max_possible,
        "end_exclusive": end_exclusive,
        "replay_virtual_now": virtual_now,
        "errors": list(result.errors),
    }


def run_pmax_serving(*, pmax_artifact_root: str, anomaly_artifact_root: str, anomaly_reference_meters: tuple[str, ...]) -> dict[str, Any]:
    selected = select_pmax_base_ts()
    if selected is None:
        return {"lane": "pmax", "ok": True, "changed": False, "reason": "no_new_eligible_base_ts"}

    anomaly_reference_max_base = select_anomaly_reference_max_base(anomaly_reference_meters=anomaly_reference_meters)
    enable_anomaly_reference = anomaly_reference_max_base is not None and selected <= anomaly_reference_max_base
    run_id = "service_pmax_" + selected.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    cmd = [
        sys.executable,
        "scripts/serving/model_serving.py",
        "--base-ts",
        selected.isoformat(),
        "--pmax-artifact-root",
        pmax_artifact_root,
        "--allow-harmonized-observed-input",
        "--allow-missing-anomaly",
        "--execute-write",
        "--allow-nonprod-warm-start-write",
        "--run-id",
        run_id,
        "--job-id",
        "pmax_scheduler",
        "--json",
    ]
    if enable_anomaly_reference:
        cmd.extend(["--enable-anomaly-reference-read", "--anomaly-artifact-root", anomaly_artifact_root])
        for meter in anomaly_reference_meters:
            cmd.extend(["--anomaly-reference-meter", meter])
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=int(os.environ.get("CMS_MODEL_SERVING_TIMEOUT_SECONDS", "900")))
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    try:
        result = json.loads(stdout) if stdout else {}
    except json.JSONDecodeError:
        result = {"raw_stdout": stdout}
    ok = proc.returncode == 0 and bool(result.get("ok"))
    return {
        "lane": "pmax",
        "ok": ok,
        "changed": ok and bool(result.get("written_rows", 0)),
        "base_ts": selected,
        "run_id": run_id,
        "returncode": proc.returncode,
        "pmax_prediction_count": result.get("pmax_prediction_count"),
        "pmax_feature_count": result.get("pmax_feature_count"),
        "pmax_readiness_issue_count": result.get("pmax_readiness_issue_count"),
        "pmax_source_mode": result.get("pmax_source_mode"),
        "anomaly_prediction_count": result.get("anomaly_prediction_count"),
        "written_rows": result.get("written_rows"),
        "errors": result.get("errors") or result.get("write_errors") or ([stderr] if stderr else []),
    }


def run_anomaly_serving(*, anomaly_artifact_root: str) -> dict[str, Any]:
    selected = select_anomaly_base_ts()
    if selected is None:
        return {"lane": "anomaly", "ok": True, "changed": False, "reason": "no_new_eligible_forecast_origin_ts"}

    run_id = "service_anomaly_" + selected.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    cmd = [
        sys.executable,
        "scripts/serving/model_serving.py",
        "--base-ts",
        selected.isoformat(),
        "--skip-pmax",
        "--enable-anomaly-db-read",
        "--anomaly-artifact-root",
        anomaly_artifact_root,
        "--execute-write",
        "--run-id",
        run_id,
        "--job-id",
        "anomaly_scheduler",
        "--json",
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=int(os.environ.get("CMS_MODEL_SERVING_TIMEOUT_SECONDS", "900")))
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    try:
        result = json.loads(stdout) if stdout else {}
    except json.JSONDecodeError:
        result = {"raw_stdout": stdout}
    ok = proc.returncode == 0 and bool(result.get("ok"))
    return {
        "lane": "anomaly",
        "ok": ok,
        "changed": ok and bool(result.get("written_rows", 0)),
        "forecast_origin_ts": selected,
        "run_id": run_id,
        "returncode": proc.returncode,
        "anomaly_feature_rows": result.get("anomaly_feature_rows"),
        "anomaly_prediction_count": result.get("anomaly_prediction_count"),
        "anomaly_source_mode": result.get("anomaly_source_mode"),
        "anomaly_source_table": result.get("anomaly_source_table"),
        "written_rows": result.get("written_rows"),
        "errors": result.get("errors") or result.get("write_errors") or ([stderr] if stderr else []),
    }


def select_anomaly_base_ts() -> datetime | None:
    with connect() as conn:
        conn.execute("SET default_transaction_read_only = on")
        conn.execute("SET statement_timeout = 15000")
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT max(bucket_ts)
                FROM mart.anomaly_feature_1h
                WHERE COALESCE(derived_features->>'source_mode', '') = 'live_observed'
                """
            )
            latest_feature_bucket = cur.fetchone()[0]
            if latest_feature_bucket is None:
                return None
    if anomaly_serving_evidence_exists(latest_feature_bucket):
        return None
    return latest_feature_bucket


def anomaly_serving_evidence_exists(forecast_origin_ts: datetime) -> bool:
    with connect() as conn:
        conn.execute("SET default_transaction_read_only = on")
        conn.execute("SET statement_timeout = 15000")
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*)
                FROM qa.serving_evidence
                WHERE forecast_origin_ts = %s
                  AND writes_enabled = true
                  AND anomaly_prediction_count > 0
                """,
                (forecast_origin_ts,),
            )
            return int(cur.fetchone()[0] or 0) > 0


def select_pmax_base_ts() -> datetime | None:
    with connect() as conn:
        conn.execute("SET default_transaction_read_only = on")
        conn.execute("SET statement_timeout = 15000")
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT max(window_ts)
                FROM mart.peak_feature_15min
                WHERE meter_urn = ANY(%s)
                  AND measurement = ANY(%s)
                  AND source_mode IN ('live_observed','reference_backfill')
                """,
                (list(PMAX_METERS), list(PMAX_MEASUREMENTS)),
            )
            live_or_mart_max_window = cur.fetchone()[0]
            reference_max_window = datetime.fromisoformat(PMAX_REFERENCE_WARM_START_EXCLUSIVE_END_TS) - timedelta(minutes=15)
            latest_candidate_window = max(filter(None, (live_or_mart_max_window, reference_max_window)), default=None)
            if latest_candidate_window is None:
                return None
            max_window = latest_candidate_window
            lower = max_window - timedelta(minutes=15 * (PMAX_REQUIRED_WINDOWS - 1))
            cur.execute(
                """
                WITH hybrid_peak_feature_adapter AS (
                  SELECT window_ts, meter_urn, measurement, source_mode, created_at, run_id
                  FROM mart.peak_feature_15min
                  WHERE window_ts BETWEEN %s AND %s
                    AND meter_urn = ANY(%s)
                    AND measurement = ANY(%s)
                    AND source_mode IN ('live_observed','reference_backfill')
                  UNION ALL
                  SELECT ts AS window_ts, meter_urn, measurement, 'reference_backfill' AS source_mode, created_at, run_id
                  FROM reference.corrected_resampled_15min
                  WHERE ts BETWEEN %s AND %s
                    AND meter_urn = ANY(%s)
                    AND measurement = ANY(%s)
                    AND ts < %s::timestamptz
                ), ranked AS (
                  SELECT window_ts, meter_urn, measurement,
                         row_number() OVER (
                           PARTITION BY window_ts, meter_urn, measurement
                           ORDER BY CASE
                             WHEN source_mode = 'live_observed' THEN 0
                             WHEN source_mode = 'reference_backfill' THEN 1
                             ELSE 2
                           END,
                           created_at DESC NULLS LAST,
                           run_id DESC NULLS LAST
                         ) AS source_rank
                  FROM hybrid_peak_feature_adapter
                )
                SELECT window_ts, meter_urn, measurement
                FROM ranked
                WHERE source_rank = 1
                ORDER BY window_ts DESC
                """,
                (
                    lower,
                    max_window,
                    list(PMAX_METERS),
                    list(PMAX_MEASUREMENTS),
                    lower,
                    max_window,
                    list(PMAX_METERS),
                    list(PMAX_MEASUREMENTS),
                    PMAX_REFERENCE_WARM_START_EXCLUSIVE_END_TS,
                ),
            )
            rows = cur.fetchall()
    coverage: dict[datetime, set[tuple[str, str]]] = {}
    for window_ts, meter_urn, measurement in rows:
        coverage.setdefault(window_ts, set()).add((meter_urn, measurement))

    complete_key_count = len(PMAX_METERS) * len(PMAX_MEASUREMENTS)
    meter_key_count = len(PMAX_MEASUREMENTS)
    candidates = []
    partial_candidates = []
    for window_ts, keys in coverage.items():
        if len(keys) == complete_key_count:
            candidates.append(window_ts + timedelta(minutes=15))
        if any(
            all((meter_urn, measurement) in keys for measurement in PMAX_MEASUREMENTS)
            for meter_urn in PMAX_METERS
        ):
            partial_candidates.append(window_ts + timedelta(minutes=15))

    for base_ts in sorted(set(candidates), reverse=True):
        input_windows = [base_ts - timedelta(minutes=15 * idx) for idx in range(1, PMAX_REQUIRED_WINDOWS + 1)]
        if all(len(coverage.get(window_ts, set())) == complete_key_count for window_ts in input_windows):
            if not pmax_serving_evidence_exists(base_ts):
                return base_ts

    for base_ts in sorted(set(partial_candidates), reverse=True):
        input_windows = [base_ts - timedelta(minutes=15 * idx) for idx in range(1, PMAX_REQUIRED_WINDOWS + 1)]
        complete_meters = [
            meter_urn
            for meter_urn in PMAX_METERS
            if all(
                len({measurement for key_meter, measurement in coverage.get(window_ts, set()) if key_meter == meter_urn}) == meter_key_count
                for window_ts in input_windows
            )
        ]
        if complete_meters and not pmax_serving_evidence_exists(base_ts):
            return base_ts

    return None


def select_anomaly_reference_max_base(*, anomaly_reference_meters: tuple[str, ...]) -> datetime | None:
    with connect() as conn:
        conn.execute("SET default_transaction_read_only = on")
        conn.execute("SET statement_timeout = 15000")
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT max(ts) + interval '1 hour'
                FROM reference.corrected_resampled_1h
                WHERE meter_urn = ANY(%s)
                """,
                (list(anomaly_reference_meters),),
            )
            return cur.fetchone()[0]


def pmax_serving_evidence_exists(base_ts: datetime) -> bool:
    with connect() as conn:
        conn.execute("SET default_transaction_read_only = on")
        conn.execute("SET statement_timeout = 15000")
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*)
                FROM qa.serving_evidence
                WHERE forecast_origin_ts = %s
                  AND writes_enabled = true
                  AND pmax_prediction_count > 0
                """,
                (base_ts,),
            )
            return int(cur.fetchone()[0] or 0) > 0


if __name__ == "__main__":
    raise SystemExit(main())
