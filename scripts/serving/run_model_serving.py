#!/usr/bin/env python3
"""Run a CMS model-serving pass.

This is the operational runner for the final serving path. It performs real
PostgreSQL reads and real local artifact inference, then builds the same
write-gated mart/ops/qa batch used by the Airflow boundary.

Default mode is no-write. To write, callers must pass ``--execute-write`` and
set ``ALLOW_MODEL_SERVING_WRITE=1``. Canonical writes are never enabled here.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cms.contracts.anomaly_detection_1h import anomaly_model_urn_for_meter
from cms.contracts.live_pipeline import (
    MART_PEAK_FEATURE_15MIN,
    SOURCE_MODE_HYBRID_WARM_START,
    SOURCE_MODE_LIVE_OBSERVED,
    SOURCE_MODE_REFERENCE_BACKFILL,
)
from cms.contracts.pmax_forecast_15min import PmaxFeatureReadinessRow
from cms.data.model_serving_postgres import PsycopgModelServingReader, PsycopgModelServingSink
from cms.data.model_serving_queries import build_anomaly_feature_query, build_anomaly_reference_feature_query, build_pmax_feature_query
from cms.data.model_serving_sink import build_model_serving_write_batch, write_model_serving_batch
from cms.data.runtime_postgres import load_postgres_config_from_env
from cms.modeling.anomaly_warning_adapter import AnomalyWarningAdapter
from cms.modeling.pmax_artifact_loader import PmaxReleaseArtifactLoader
from cms.modeling.pmax_feature_builder import build_pmax_feature_vectors
from cms.modeling.pmax_forecast_adapter import PmaxForecastAdapter

NONPROD_WARM_START_WRITE_ENV_FLAG = "ALLOW_NONPROD_WARM_START_MODEL_SERVING_WRITE"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run CMS model-serving pass")
    parser.add_argument("--base-ts", required=True, help="Forecast base timestamp, e.g. 2026-06-09T03:00:00+00:00")
    parser.add_argument("--pmax-artifact-root", required=True, help="Extracted P-Max release root")
    parser.add_argument("--anomaly-predictions-path", help="Optional JSON or CSV file containing wide anomaly prediction rows")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--job-id", default="manual_model_serving")
    parser.add_argument("--execute-write", action="store_true", help="Actually write mart/ops/qa rows when ALLOW_MODEL_SERVING_WRITE=1")
    parser.add_argument(
        "--allow-harmonized-observed-input",
        action="store_true",
        help=(
            "No-write/non-production compatibility mode: include legacy "
            "mart.peak_feature_15min rows whose lineage columns are empty. "
            "Strict release readiness still requires explicit live_observed source lineage."
        ),
    )
    parser.add_argument("--enable-anomaly-db-read", action="store_true", help="Opt in to the anomaly DB-read branch; it is disabled by default because AWS anomaly tables are absent")
    parser.add_argument("--enable-anomaly-reference-read", action="store_true", help="Non-production reference/backfill branch: read reference.corrected_resampled_1h and run anomaly artifacts directly")
    parser.add_argument("--anomaly-artifact-root", help="Extracted anomaly artifact root for --enable-anomaly-reference-read")
    parser.add_argument("--anomaly-reference-meter", action="append", default=None, help="Meter to run through the reference/backfill anomaly branch; repeat for multiple meters")
    parser.add_argument("--allow-missing-anomaly", action="store_true", help="Allow P-Max-only run; anomaly DB read is disabled unless --enable-anomaly-db-read is set")
    parser.add_argument(
        "--pmax-negative-prediction-policy",
        choices=("raise", "clip_zero"),
        default=os.environ.get("PMAX_NEGATIVE_PREDICTION_POLICY", "clip_zero"),
        help="How operational serving handles physically invalid negative P-Max predictions; default clips to 0 before contract validation.",
    )
    parser.add_argument(
        "--allow-nonprod-warm-start-write",
        action="store_true",
        help="Permit non-production reference/hybrid warm-start writes when ALLOW_NONPROD_WARM_START_MODEL_SERVING_WRITE=1 is also set",
    )
    parser.add_argument("--environment", default=os.environ.get("ENVIRONMENT", "dev"), help="Runtime environment label; production writes require an extra explicit gate")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only")
    args = parser.parse_args()
    if args.anomaly_predictions_path and args.enable_anomaly_reference_read:
        return _finish(
            {
                "ok": False,
                "blocked": True,
                "error": "choose either --anomaly-predictions-path or --enable-anomaly-reference-read, not both",
                "write_attempted": False,
            },
            json_only=args.json,
            exit_code=2,
        )
    if args.enable_anomaly_reference_read and not args.anomaly_artifact_root:
        return _finish(
            {
                "ok": False,
                "blocked": True,
                "error": "--enable-anomaly-reference-read requires --anomaly-artifact-root",
                "write_attempted": False,
            },
            json_only=args.json,
            exit_code=2,
        )
    warm_start_write_requested = args.execute_write and (args.allow_harmonized_observed_input or args.enable_anomaly_reference_read)
    if warm_start_write_requested and not _nonprod_warm_start_write_allowed(args=args, env=os.environ):
        return _finish(
            {
                "ok": False,
                "blocked": True,
                "error": (
                    "reference_backfill/hybrid_warm_start writes require --allow-nonprod-warm-start-write "
                    f"and {NONPROD_WARM_START_WRITE_ENV_FLAG}=1 in a non-production environment"
                ),
                "pmax_source_mode": SOURCE_MODE_HYBRID_WARM_START if args.allow_harmonized_observed_input else SOURCE_MODE_LIVE_OBSERVED,
                "anomaly_source_mode": SOURCE_MODE_REFERENCE_BACKFILL if args.enable_anomaly_reference_read else None,
                "write_attempted": False,
            },
            json_only=args.json,
            exit_code=2,
        )
    if args.execute_write and str(args.environment).lower() == "production" and os.environ.get("ALLOW_PRODUCTION_MODEL_SERVING_WRITE") != "1":
        return _finish(
            {
                "ok": False,
                "blocked": True,
                "error": "production model-serving writes require ALLOW_PRODUCTION_MODEL_SERVING_WRITE=1",
                "environment": args.environment,
                "write_attempted": False,
            },
            json_only=args.json,
            exit_code=2,
        )

    base_ts = _parse_ts(args.base_ts)
    started_at = datetime.now(tz=UTC)
    run_id = args.run_id or f"model_serving_{base_ts.strftime('%Y%m%dT%H%M%SZ')}"

    config = load_postgres_config_from_env(dict(os.environ))
    reader = PsycopgModelServingReader(config)

    pmax_source_mode = SOURCE_MODE_HYBRID_WARM_START if args.allow_harmonized_observed_input else SOURCE_MODE_LIVE_OBSERVED
    pmax_query = build_pmax_feature_query(
        base_ts=base_ts,
        source_mode=pmax_source_mode,
        allow_null_source_mode=args.allow_harmonized_observed_input,
    )
    try:
        pmax_read = reader.fetch(pmax_query)
    except Exception as exc:  # noqa: BLE001 - operational CLI should emit JSON instead of a traceback.
        return _finish(
            {
                "ok": False,
                "stage": "pmax_feature_read",
                "errors": [str(exc)],
                "write_attempted": False,
                "hint": "Verify POSTGRES_HOST/PORT/DB/USER and ensure mart.peak_feature_15min is reachable before model-serving.",
            },
            json_only=args.json,
            exit_code=3,
        )
    pmax_feature_rows = tuple(_pmax_feature_row(row) for row in pmax_read.rows)
    harmonized_inferred_rows = 0
    if args.allow_harmonized_observed_input:
        pmax_feature_rows, harmonized_inferred_rows = _infer_harmonized_observed_lineage(pmax_feature_rows)
    pmax_features = build_pmax_feature_vectors(pmax_feature_rows, base_ts=base_ts, strict_readiness=not args.allow_harmonized_observed_input, source_mode=pmax_source_mode)
    readiness_issue_count = len(pmax_features.readiness_result.issues)
    if not pmax_features.ok and (not args.allow_harmonized_observed_input or not pmax_features.features):
        issue_reasons = tuple(issue.issue for issue in pmax_features.readiness_result.issues)
        return _finish(
            {
                "ok": False,
                "stage": "pmax_feature_build",
                "errors": _compact_errors(pmax_features.errors or issue_reasons),
                "pmax_input_rows": pmax_read.row_count,
                "pmax_error_count": len(pmax_features.errors or issue_reasons),
                "pmax_readiness_issue_count": readiness_issue_count,
                "pmax_harmonized_inferred_rows": harmonized_inferred_rows,
            },
            json_only=args.json,
            exit_code=2,
        )

    pmax_model = PmaxReleaseArtifactLoader(Path(args.pmax_artifact_root)).load()
    pmax_result = PmaxForecastAdapter(
        model=pmax_model,
        created_at_factory=lambda _base: started_at,
        negative_prediction_policy=args.pmax_negative_prediction_policy,
    ).predict(pmax_features.features)

    anomaly_rows = ()
    anomaly_feature_count = None
    anomaly_source_mode = None
    anomaly_source_table = None
    anomaly_disabled_reason = "anomaly DB-read branch disabled by default; mart.anomaly_feature_1h is the approved feature source"
    if args.anomaly_predictions_path:
        anomaly_payload = _load_anomaly_predictions(Path(args.anomaly_predictions_path))
        anomaly_result = AnomalyWarningAdapter(model=_StaticAnomalyPredictor(anomaly_payload)).predict(({"forecast_origin_ts": base_ts},))
        anomaly_rows = anomaly_result.long_rows
        anomaly_disabled_reason = None
        anomaly_source_mode = _anomaly_payload_source_mode(anomaly_payload, fallback="external_predictions_payload")
        anomaly_source_table = _anomaly_payload_source_table(anomaly_payload, source_mode=anomaly_source_mode)
        anomaly_feature_count = _anomaly_payload_reference_input_count(anomaly_payload)
    elif args.enable_anomaly_reference_read:
        reference_meters = tuple(args.anomaly_reference_meter or ("H1.K12",))
        anomaly_query = build_anomaly_reference_feature_query(forecast_origin_ts=base_ts, meter_urns=reference_meters)
        try:
            anomaly_read = reader.fetch(anomaly_query)
            anomaly_feature_count = anomaly_read.row_count
            anomaly_payload = _predict_anomaly_reference_rows(
                rows=anomaly_read.rows,
                forecast_origin_ts=base_ts,
                artifact_root=Path(args.anomaly_artifact_root),
            )
            anomaly_result = AnomalyWarningAdapter(model=_StaticAnomalyPredictor(anomaly_payload)).predict(
                ({"forecast_origin_ts": base_ts, "source_mode": SOURCE_MODE_REFERENCE_BACKFILL},)
            )
            anomaly_rows = anomaly_result.long_rows
            anomaly_disabled_reason = None
            anomaly_source_mode = SOURCE_MODE_REFERENCE_BACKFILL
            anomaly_source_table = anomaly_query.source_tables[0]
        except Exception as exc:  # noqa: BLE001 - reference dry-run should report a compact operational blocker.
            if not args.allow_missing_anomaly:
                return _finish(
                    {
                        "ok": False,
                        "stage": "anomaly_reference_read_or_predict",
                        "errors": [str(exc)],
                        "anomaly_source_mode": SOURCE_MODE_REFERENCE_BACKFILL,
                        "anomaly_source_table": anomaly_query.source_tables[0],
                        "write_attempted": False,
                        "hint": "Reference/backfill anomaly runs require readable reference.corrected_resampled_1h rows and a torch-capable anomaly artifact runtime for all routed meters.",
                    },
                    json_only=args.json,
                    exit_code=4,
                )
            anomaly_disabled_reason = f"anomaly reference/backfill branch failed: {exc}"
            anomaly_source_mode = SOURCE_MODE_REFERENCE_BACKFILL
            anomaly_source_table = anomaly_query.source_tables[0]
    elif args.enable_anomaly_db_read:
        anomaly_query = build_anomaly_feature_query(forecast_origin_ts=base_ts)
        try:
            anomaly_read = reader.fetch(anomaly_query)
            anomaly_feature_count = anomaly_read.row_count
        except Exception as exc:  # noqa: BLE001 - operational report should surface exact blocker
            if not args.allow_missing_anomaly:
                return _finish(
                    {
                        "ok": False,
                        "stage": "anomaly_feature_read",
                        "errors": [str(exc)],
                        "hint": "Provide --anomaly-predictions-path from the anomaly artifact runner or materialize mart.anomaly_feature_1h before final serving.",
                    },
                    json_only=args.json,
                    exit_code=3,
                )
        if anomaly_feature_count is not None and not args.allow_missing_anomaly:
            return _finish(
                {
                    "ok": False,
                    "stage": "anomaly_model_runner_missing",
                    "errors": ["anomaly feature rows were readable, but no production anomaly predictor wrapper is wired in this repo runner"],
                    "anomaly_feature_rows": anomaly_feature_count,
                    "hint": "Wire the test6_residual artifact inference wrapper or pass --anomaly-predictions-path generated by that runner.",
                },
                json_only=args.json,
                exit_code=4,
            )

    finished_at = datetime.now(tz=UTC)
    evidence_packet = {
        "run_id": run_id,
        "base_ts": base_ts,
        "forecast_origin_ts": base_ts,
        "dry_run": not args.execute_write,
        "writes_enabled": args.execute_write,
        "pmax_input_rows": pmax_read.row_count,
        "pmax_feature_count": len(pmax_features.features),
        "pmax_prediction_count": len(pmax_result.rows),
        "pmax_readiness_issue_count": readiness_issue_count,
        "pmax_harmonized_inferred_rows": harmonized_inferred_rows,
        "pmax_source_mode": pmax_source_mode,
        "pmax_negative_prediction_policy": args.pmax_negative_prediction_policy,
        "harmonized_observed_input_mode": args.allow_harmonized_observed_input,
        "anomaly_feature_rows": anomaly_feature_count,
        "anomaly_prediction_count": len(anomaly_rows),
        "anomaly_disabled_reason": anomaly_disabled_reason,
        "anomaly_source_mode": anomaly_source_mode,
        "anomaly_source_table": anomaly_source_table,
    }
    batch = build_model_serving_write_batch(
        run_id=run_id,
        job_id=args.job_id,
        started_at=started_at,
        finished_at=finished_at,
        artifact_refs={"pmax": str(Path(args.pmax_artifact_root)), "anomaly": args.anomaly_predictions_path or args.anomaly_artifact_root or ""},
        pmax_rows=pmax_result.rows,
        anomaly_rows=anomaly_rows,
        evidence_packet=evidence_packet,
        writes_enabled=args.execute_write,
        canonical_writes_enabled=False,
    )
    write_result = write_model_serving_batch(
        batch=batch,
        sink=PsycopgModelServingSink(config),
        allow_write=args.execute_write,
        env=os.environ,
    )
    ok = bool(pmax_result.ok and (write_result.ok or not args.execute_write))
    return _finish(
        {
            "ok": ok,
            "run_id": run_id,
            "base_ts": base_ts,
            "pmax_input_rows": pmax_read.row_count,
            "pmax_feature_count": len(pmax_features.features),
            "pmax_prediction_count": len(pmax_result.rows),
            "pmax_readiness_issue_count": readiness_issue_count,
            "pmax_harmonized_inferred_rows": harmonized_inferred_rows,
            "pmax_source_mode": pmax_source_mode,
            "pmax_negative_prediction_policy": args.pmax_negative_prediction_policy,
            "harmonized_observed_input_mode": args.allow_harmonized_observed_input,
            "anomaly_feature_rows": anomaly_feature_count,
            "anomaly_prediction_count": len(anomaly_rows),
            "anomaly_disabled_reason": anomaly_disabled_reason,
            "anomaly_source_mode": anomaly_source_mode,
            "anomaly_source_table": anomaly_source_table,
            "write_attempted": write_result.attempted,
            "write_blocked": write_result.blocked,
            "written_tables": list(write_result.written_tables),
            "written_rows": write_result.written_rows,
            "write_errors": list(write_result.errors),
            "batch_row_count": batch.row_count,
            "batch_tables": {batch_item.table_name: len(batch_item.rows) for batch_item in batch.batches},
            "started_at": started_at,
            "finished_at": finished_at,
        },
        json_only=args.json,
        exit_code=0 if ok else 5,
    )


def _predict_anomaly_reference_rows(*, rows: Any, forecast_origin_ts: datetime, artifact_root: Path) -> tuple[dict[str, Any], ...]:
    """Run anomaly artifacts on long-form reference rows without writing results."""

    import pandas as pd

    os.environ["MODEL_ARTIFACTS_DIR"] = str(artifact_root)
    from cms.modeling.anomaly import predictor

    predictor._THRESHOLDS = predictor._load_thresholds()  # noqa: SLF001 - operational adapter around project artifact runner.
    predictor._METER_TAGS = predictor._load_meter_tags()  # noqa: SLF001

    materialized = tuple(dict(row) for row in rows)
    if not materialized:
        raise ValueError("reference/backfill anomaly query returned no rows")
    frame = pd.DataFrame(materialized)
    required = {"ts", "meter_urn", "measurement", "value"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError("reference/backfill anomaly rows missing columns: " + ",".join(missing))

    predictions: list[dict[str, Any]] = []
    ts: Any = pd.Timestamp(forecast_origin_ts)
    for meter_urn, meter_frame in frame.groupby("meter_urn", sort=True):
        model_urn = anomaly_model_urn_for_meter(str(meter_urn))
        wide = (
            meter_frame.pivot_table(index="ts", columns="measurement", values="value", aggfunc="last")
            .reset_index()
            .sort_values("ts")
        )
        result = predictor.predict_meter(None, str(meter_urn), model_urn, 3, ts, raw_data=wide)
        if not result:
            continue
        result = dict(result)
        result["forecast_origin_ts"] = forecast_origin_ts.isoformat()
        result["created_at"] = forecast_origin_ts.isoformat()
        result["source_mode"] = SOURCE_MODE_REFERENCE_BACKFILL
        result["source_input_refs"] = (
            f"reference.corrected_resampled_1h:{meter_urn}:{meter_frame['ts'].min()}:{meter_frame['ts'].max()}:source_mode={SOURCE_MODE_REFERENCE_BACKFILL}",
        )
        result["reference_input_row_count"] = int(len(meter_frame))
        result["reference_wide_row_count"] = int(len(wide))
        predictions.append(result)
    if not predictions:
        raise ValueError("reference/backfill anomaly artifact produced no predictions")
    return tuple(predictions)


def _load_anomaly_predictions(path: Path) -> Any:
    if path.is_dir():
        candidates = sorted(path.glob("predictions_*.csv"), key=lambda item: item.stat().st_mtime, reverse=True)
        if not candidates:
            raise FileNotFoundError(f"no predictions_*.csv found in anomaly predictions directory: {path}")
        path = candidates[0]
    suffix = path.suffix.lower()
    if suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    if suffix == ".csv":
        import csv

        with path.open(newline="", encoding="utf-8") as handle:
            return tuple(csv.DictReader(handle))
    raise ValueError(f"unsupported anomaly predictions file extension: {path.suffix}")


def _anomaly_payload_source_mode(payload: Any, *, fallback: str) -> str:
    rows = _prediction_mappings(payload)
    modes = {str(row.get("source_mode", "")).strip() for row in rows if str(row.get("source_mode", "")).strip()}
    return modes.pop() if len(modes) == 1 else fallback


def _anomaly_payload_source_table(payload: Any, *, source_mode: str | None) -> str | None:
    if source_mode != SOURCE_MODE_REFERENCE_BACKFILL:
        return None
    for row in _prediction_mappings(payload):
        refs = row.get("source_input_refs", row.get("source_refs"))
        if refs and "reference.corrected_resampled_1h" in str(refs):
            return "reference.corrected_resampled_1h"
    return None


def _anomaly_payload_reference_input_count(payload: Any) -> int | None:
    total = 0
    observed = False
    for row in _prediction_mappings(payload):
        value = row.get("reference_input_row_count")
        if value in (None, ""):
            continue
        observed = True
        total += int(float(value))
    return total if observed else None


def _prediction_mappings(payload: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(payload, Mapping):
        return (payload,)
    if isinstance(payload, (list, tuple)):
        return tuple(row for row in payload if isinstance(row, Mapping))
    return ()


def _nonprod_warm_start_write_allowed(*, args: argparse.Namespace, env: Mapping[str, str]) -> bool:
    return (
        bool(args.allow_nonprod_warm_start_write)
        and env.get(NONPROD_WARM_START_WRITE_ENV_FLAG) == "1"
        and str(args.environment).lower() != "production"
    )


class _StaticAnomalyPredictor:
    def __init__(self, payload: Any) -> None:
        if isinstance(payload, dict):
            payload = [payload]
        self.payload = tuple(payload)

    def predict(self, rows: Any) -> tuple[Any, ...]:
        if not rows:
            raise ValueError("anomaly predictor requires at least one input row")
        return self.payload


def _pmax_feature_row(row: dict[str, Any]) -> PmaxFeatureReadinessRow:
    return PmaxFeatureReadinessRow(**row)


def _infer_harmonized_observed_lineage(rows: tuple[PmaxFeatureReadinessRow, ...]) -> tuple[tuple[PmaxFeatureReadinessRow, ...], int]:
    inferred: list[PmaxFeatureReadinessRow] = []
    inferred_count = 0
    for row in rows:
        if _is_legacy_harmonized_observed_row(row):
            inferred.append(
                replace(
                    row,
                    source_layer=row.source_layer or MART_PEAK_FEATURE_15MIN,
                    source_mode=row.source_mode or SOURCE_MODE_LIVE_OBSERVED,
                    provenance=row.provenance
                    or {
                        "source_file": row.source_file,
                        "source_family": "harmonized_observed_input",
                        "inferred_by": "run_model_serving.allow_harmonized_observed_input",
                        "runtime_scope": "non_write_model_serving_dry_run",
                    },
                )
            )
            inferred_count += 1
        else:
            inferred.append(row)
    return tuple(inferred), inferred_count


def _is_legacy_harmonized_observed_row(row: PmaxFeatureReadinessRow) -> bool:
    source_file = row.source_file.lower()
    return (
        "harmonized" in source_file
        and "corrected_resampled" not in source_file
        and (not row.source_layer or not row.source_mode or not row.provenance)
    )


def _parse_ts(raw: str) -> datetime:
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--base-ts must be timezone-aware")
    return parsed.astimezone(UTC)


def _compact_errors(errors: Any) -> list[str]:
    return sorted({str(error) for error in errors})


def _finish(payload: dict[str, Any], *, json_only: bool, exit_code: int) -> int:
    normalized = _json_safe(payload)
    text = json.dumps(normalized, ensure_ascii=False, indent=None if json_only else 2, sort_keys=True)
    print(text)
    return exit_code


def _json_safe(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
