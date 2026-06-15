#!/usr/bin/env python3
"""Run CMS operational serving workers inside the service runtime.

This entrypoint is for Docker/Airflow/systemd-style service execution, not
Hermes automation. It performs bounded, idempotent passes for:

- live.promotion_check -> canonical.measurement_15min/1h
- live.measurement_1h -> mart.anomaly_feature_1h
- mart.peak_feature_15min + reference anomaly warm-start -> mart/ops/qa model outputs

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
from typing import Any

import psycopg

from cms.data.anomaly_feature_materializer import (
    execute_anomaly_feature_materialization_command,
    make_anomaly_feature_materialization_command,
)
from cms.data.canonical_promotion_runner import execute_canonical_promotion_command, make_canonical_promotion_command
from cms.data.runtime_postgres import load_postgres_config_from_env
from cms.workflow.replay_clock import replay_virtual_now

PMAX_METERS = ("V.Z81", "V.Z82", "H2.Z351", "H2.Z361")
PMAX_MEASUREMENTS = ("P", "U1", "PF")
PMAX_REQUIRED_WINDOWS = 288


def main() -> int:
    parser = argparse.ArgumentParser(description="Run CMS operational service workers")
    parser.add_argument("--lane", choices=("all", "canonical", "anomaly-feature", "hybrid-model-serving"), default="all")
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

    last_model_serving_at: float | None = None
    while True:
        payloads: list[dict[str, Any]] = []
        if args.lane in ("all", "canonical"):
            payloads.append(run_canonical(batch_size=args.canonical_batch_size))
        if args.lane in ("all", "anomaly-feature"):
            payloads.append(run_anomaly_feature(batch_size=args.anomaly_batch_size))
        if args.lane in ("all", "hybrid-model-serving"):
            now = time.monotonic()
            due = last_model_serving_at is None or now - last_model_serving_at >= args.model_serving_interval_seconds
            if due:
                payloads.append(
                    run_hybrid_model_serving(
                        pmax_artifact_root=args.pmax_artifact_root,
                        anomaly_artifact_root=args.anomaly_artifact_root,
                        anomaly_reference_meters=tuple(args.anomaly_reference_meter or ("H1.K11",)),
                    )
                )
                last_model_serving_at = now

        emitted = [payload for payload in payloads if payload.get("changed") or payload.get("ok") is False]
        for payload in emitted:
            print(json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True), flush=True)

        if not args.loop:
            return 0 if all(payload.get("ok", False) for payload in payloads) else 2
        time.sleep(max(1, args.interval_seconds))


def connect() -> psycopg.Connection[Any]:
    config = load_postgres_config_from_env(dict(os.environ))
    return psycopg.connect(**config.connect_kwargs())


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


def run_hybrid_model_serving(*, pmax_artifact_root: str, anomaly_artifact_root: str, anomaly_reference_meters: tuple[str, ...]) -> dict[str, Any]:
    selected = select_hybrid_base_ts(anomaly_reference_meters=anomaly_reference_meters)
    if selected is None:
        return {"lane": "hybrid_model_serving", "ok": True, "changed": False, "reason": "no_new_eligible_base_ts"}

    run_id = "service_hybrid_model_serving_" + selected.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    cmd = [
        sys.executable,
        "scripts/serving/run_model_serving.py",
        "--base-ts",
        selected.isoformat(),
        "--pmax-artifact-root",
        pmax_artifact_root,
        "--allow-harmonized-observed-input",
        "--enable-anomaly-reference-read",
        "--allow-missing-anomaly",
        "--anomaly-artifact-root",
        anomaly_artifact_root,
        "--execute-write",
        "--allow-nonprod-warm-start-write",
        "--run-id",
        run_id,
        "--job-id",
        "service_hybrid_model_serving_scheduler",
        "--json",
    ]
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
        "lane": "hybrid_model_serving",
        "ok": ok,
        "changed": ok and bool(result.get("written_rows", 0)),
        "base_ts": selected,
        "run_id": run_id,
        "returncode": proc.returncode,
        "pmax_prediction_count": result.get("pmax_prediction_count"),
        "anomaly_prediction_count": result.get("anomaly_prediction_count"),
        "written_rows": result.get("written_rows"),
        "errors": result.get("errors") or result.get("write_errors") or ([stderr] if stderr else []),
    }


def select_hybrid_base_ts(*, anomaly_reference_meters: tuple[str, ...]) -> datetime | None:
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
                  AND (source_mode IN ('live_observed','reference_backfill') OR source_mode IS NULL)
                """,
                (list(PMAX_METERS), list(PMAX_MEASUREMENTS)),
            )
            max_window = cur.fetchone()[0]
            if max_window is None:
                return None
            virtual_now = replay_virtual_now(env=os.environ)
            if virtual_now is not None:
                max_window = min(max_window, virtual_now.astimezone(max_window.tzinfo) - timedelta(minutes=15))
            lower = max_window - timedelta(days=10)
            cur.execute(
                """
                SELECT window_ts, meter_urn, measurement
                FROM mart.peak_feature_15min
                WHERE window_ts BETWEEN %s AND %s
                  AND meter_urn = ANY(%s)
                  AND measurement = ANY(%s)
                  AND (source_mode IN ('live_observed','reference_backfill') OR source_mode IS NULL)
                ORDER BY window_ts DESC
                """,
                (lower, max_window, list(PMAX_METERS), list(PMAX_MEASUREMENTS)),
            )
            rows = cur.fetchall()
            cur.execute(
                """
                SELECT max(ts) + interval '1 hour'
                FROM reference.corrected_resampled_1h
                WHERE meter_urn = ANY(%s)
                """,
                (list(anomaly_reference_meters),),
            )
            anomaly_reference_max_base = cur.fetchone()[0]
            if anomaly_reference_max_base is None:
                return None
            if virtual_now is not None:
                anomaly_reference_max_base = min(anomaly_reference_max_base, virtual_now.astimezone(anomaly_reference_max_base.tzinfo))

    coverage: dict[datetime, set[tuple[str, str]]] = {}
    for window_ts, meter_urn, measurement in rows:
        coverage.setdefault(window_ts, set()).add((meter_urn, measurement))

    complete_key_count = len(PMAX_METERS) * len(PMAX_MEASUREMENTS)
    candidates = []
    for window_ts, keys in coverage.items():
        if len(keys) == complete_key_count:
            base_ts = window_ts + timedelta(minutes=15)
            if base_ts <= anomaly_reference_max_base:
                candidates.append(base_ts)

    for base_ts in sorted(set(candidates), reverse=True):
        input_windows = [base_ts - timedelta(minutes=15 * idx) for idx in range(1, PMAX_REQUIRED_WINDOWS + 1)]
        if all(len(coverage.get(window_ts, set())) == complete_key_count for window_ts in input_windows):
            if not model_serving_evidence_exists(base_ts):
                return base_ts
    return None


def model_serving_evidence_exists(base_ts: datetime) -> bool:
    with connect() as conn:
        conn.execute("SET default_transaction_read_only = on")
        conn.execute("SET statement_timeout = 15000")
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*)
                FROM qa.model_serving_evidence_packet
                WHERE base_ts = %s
                  AND writes_enabled = true
                  AND pmax_prediction_count > 0
                  AND anomaly_prediction_count > 0
                """,
                (base_ts,),
            )
            return int(cur.fetchone()[0] or 0) > 0


if __name__ == "__main__":
    raise SystemExit(main())
