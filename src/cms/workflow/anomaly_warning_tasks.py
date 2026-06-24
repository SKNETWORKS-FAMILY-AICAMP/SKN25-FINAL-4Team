"""Import-safe anomaly warning workflow task callables.

These functions are repo-local and in-memory only. They do not perform DB writes,
Grafana changes, Airflow scheduler/webserver work, Kafka/AWS access, filesystem
artifact loading, or canonical-table mutation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from cms.contracts.anomaly_detection_1h import (
    ANOMALY_DETECTION_ALLOWED_EVIDENCE_TABLES,
    ANOMALY_DETECTION_LEAD_STEPS,
    ANOMALY_DETECTION_MODEL_METERS,
    ANOMALY_DETECTION_MODEL_VERSION,
    AnomalyDetectionArtifactBoundary,
    AnomalyDetectionLongRow,
    AnomalyDetectionValidationIssue,
    validate_anomaly_detection_batch,
)
from cms.modeling.anomaly_artifact_loader import AnomalyArtifactDescriptor, AnomalyArtifactLoaderError
from cms.modeling.anomaly_warning_adapter import AnomalyWarningAdapter, AnomalyWarningPredictionError

PRODUCTION_ENVIRONMENTS = ("prod", "production")


@dataclass(frozen=True)
class AnomalyWarningRunConfig:
    """Explicit non-production/manual anomaly warning run configuration."""

    forecast_origin_ts: datetime
    environment: str = "nonprod"
    manual_run: bool = True
    dry_run: bool = True
    writes_enabled: bool = False
    canonical_writes_enabled: bool = False
    meter_urns: tuple[str, ...] = tuple(ANOMALY_DETECTION_MODEL_METERS)


@dataclass(frozen=True)
class AnomalyWarningTaskResult:
    """Structured in-memory task outcome returned by anomaly warning callables."""

    task_id: str
    ok: bool
    data: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    blocked: bool = False


TASK_IDS = (
    "load_anomaly_run_config",
    "gate_anomaly_manual_nonprod_run",
    "gate_anomaly_model_artifact",
    "run_anomaly_warning_adapter",
    "validate_anomaly_warning_output",
    "record_anomaly_pipeline_metrics",
    "publish_anomaly_evidence_packet",
)


def airflow_task_entrypoint(task_id: str, **context: Any) -> AnomalyWarningTaskResult:
    """Safe Airflow ``PythonOperator`` entrypoint for anomaly lane wiring.

    Config and artifact gates can run from ``dag_run.conf``. Adapter execution
    still requires materialized input rows and a model adapter from the runtime
    task layer, so those task IDs return precise blocked results.
    """

    if not task_id:
        return _blocked_airflow_result("unknown", "task_id is required")
    if task_id not in TASK_IDS:
        return _blocked_airflow_result(task_id, "unknown anomaly warning task_id")
    payload = _dag_run_conf(context)
    if payload is None:
        return _blocked_airflow_result(task_id, "dag_run.conf with forecast_origin_ts is required")
    try:
        if task_id == "load_anomaly_run_config":
            config = load_anomaly_run_config(payload)
            return AnomalyWarningTaskResult(task_id=task_id, ok=True, data={"config": config})
        if task_id == "gate_anomaly_manual_nonprod_run":
            return gate_anomaly_manual_nonprod_run(load_anomaly_run_config(payload))
        if task_id == "gate_anomaly_model_artifact":
            artifact_payload = payload.get("artifact", payload.get("artifact_boundary", payload))
            if not isinstance(artifact_payload, Mapping):
                return _blocked_airflow_result(task_id, "artifact must be a mapping when provided")
            return gate_anomaly_model_artifact(artifact_payload)
    except (TypeError, ValueError) as exc:
        return _blocked_airflow_result(task_id, str(exc))

    blocked_reasons = {
        "run_anomaly_warning_adapter": "anomaly feature rows and model adapter are required",
        "validate_anomaly_warning_output": "anomaly warning rows are required",
        "record_anomaly_pipeline_metrics": "anomaly warning rows are required",
        "publish_anomaly_evidence_packet": "anomaly metrics and evidence inputs are required",
    }
    return _blocked_airflow_result(task_id, blocked_reasons[task_id])


def load_anomaly_run_config(payload: Mapping[str, Any] | None = None, **overrides: Any) -> AnomalyWarningRunConfig:
    """Load and validate explicit anomaly warning run config from an in-memory mapping."""

    values: dict[str, Any] = dict(payload or {})
    values.update(overrides)
    if "forecast_origin_ts" not in values and "timestamp" in values:
        values["forecast_origin_ts"] = values["timestamp"]
    if "forecast_origin_ts" not in values or values["forecast_origin_ts"] in (None, ""):
        raise ValueError("forecast_origin_ts is required")

    meter_urns = values.get("meter_urns", tuple(ANOMALY_DETECTION_MODEL_METERS))
    if isinstance(meter_urns, str):
        meter_tuple = tuple(part.strip() for part in meter_urns.split(",") if part.strip())
    else:
        meter_tuple = tuple(str(meter) for meter in meter_urns)

    return AnomalyWarningRunConfig(
        forecast_origin_ts=_parse_origin_ts(values["forecast_origin_ts"]),
        environment=str(values.get("environment", "nonprod")),
        manual_run=bool(values.get("manual_run", values.get("manual", True))),
        dry_run=bool(values.get("dry_run", True)),
        writes_enabled=bool(values.get("writes_enabled", False)),
        canonical_writes_enabled=bool(values.get("canonical_writes_enabled", False)),
        meter_urns=meter_tuple,
    )


def gate_anomaly_manual_nonprod_run(config: AnomalyWarningRunConfig | Mapping[str, Any]) -> AnomalyWarningTaskResult:
    """Block production, scheduler/non-manual, non-dry-run, and write-enabled runs."""

    run_config = _ensure_config(config)
    errors: list[str] = []
    if run_config.environment.lower() in PRODUCTION_ENVIRONMENTS:
        errors.append("production environment is blocked for anomaly warning dry-run tasks")
    if not run_config.manual_run:
        errors.append("manual_run must be true")
    if not run_config.dry_run:
        errors.append("dry_run must be true")
    if run_config.writes_enabled:
        errors.append("writes_enabled must be false")
    if run_config.canonical_writes_enabled:
        errors.append("canonical_writes_enabled must be false")
    unsupported = tuple(meter for meter in run_config.meter_urns if meter not in ANOMALY_DETECTION_MODEL_METERS)
    if unsupported:
        errors.append("unsupported meter_urns: " + ",".join(unsupported))

    return AnomalyWarningTaskResult(
        task_id="gate_anomaly_manual_nonprod_run",
        ok=not errors,
        data={"environment": run_config.environment, "writes_enabled": run_config.writes_enabled, "meter_urns": run_config.meter_urns},
        errors=tuple(errors),
        blocked=bool(errors),
    )


def gate_anomaly_model_artifact(artifact: AnomalyArtifactDescriptor | AnomalyDetectionArtifactBoundary | Mapping[str, Any]) -> AnomalyWarningTaskResult:
    """Validate an in-memory anomaly artifact descriptor/boundary without loading it."""

    descriptor = _artifact_descriptor(artifact)
    errors: list[str] = []
    if descriptor.model_version != ANOMALY_DETECTION_MODEL_VERSION:
        errors.append(f"model_version must be {ANOMALY_DETECTION_MODEL_VERSION}")
    if not descriptor.available:
        errors.append("artifact must be available")
    if descriptor.external_io_enabled:
        errors.append("external_io_enabled must be false")

    return AnomalyWarningTaskResult(
        task_id="gate_anomaly_model_artifact",
        ok=not errors,
        data=descriptor.as_dict(),
        errors=tuple(errors),
        blocked=bool(errors),
    )


def run_anomaly_warning_adapter(
    *,
    input_rows: Iterable[Mapping[str, Any]],
    config: AnomalyWarningRunConfig | Mapping[str, Any],
    model: Any | None = None,
    adapter: AnomalyWarningAdapter | None = None,
) -> AnomalyWarningTaskResult:
    """Run anomaly warning inference with an explicit fake model/adapter."""

    run_config = _ensure_config(config)
    if adapter is None:
        if model is None:
            return AnomalyWarningTaskResult(task_id="run_anomaly_warning_adapter", ok=False, errors=("model or adapter is required",), blocked=True)
        adapter = AnomalyWarningAdapter(model=model)
    try:
        result = adapter.predict(tuple(input_rows))
    except (AnomalyWarningPredictionError, AnomalyArtifactLoaderError) as exc:
        return AnomalyWarningTaskResult(task_id="run_anomaly_warning_adapter", ok=False, errors=(str(exc),), blocked=True)
    config_issues = _validate_long_rows_against_config(result.long_rows, run_config)
    if config_issues:
        return AnomalyWarningTaskResult(
            task_id="run_anomaly_warning_adapter",
            ok=False,
            data={"wide_rows": result.wide_rows, "long_rows": result.long_rows, "validation_issues": config_issues},
            errors=tuple(issue.issue for issue in config_issues),
            blocked=True,
        )
    return AnomalyWarningTaskResult(
        task_id="run_anomaly_warning_adapter",
        ok=result.ok,
        data={"wide_rows": result.wide_rows, "long_rows": result.long_rows, "validation_issues": result.validation_issues},
        errors=tuple(issue.issue for issue in result.validation_issues),
        blocked=not result.ok,
    )


def validate_anomaly_warning_output(
    *, rows: Iterable[AnomalyDetectionLongRow], config: AnomalyWarningRunConfig | Mapping[str, Any] | None = None
) -> AnomalyWarningTaskResult:
    """Validate long output rows and expected lead-step coverage in memory."""

    materialized_rows = tuple(rows)
    issues = list(validate_anomaly_detection_batch(materialized_rows))
    if config is not None:
        issues.extend(_validate_long_rows_against_config(materialized_rows, _ensure_config(config)))
    steps_by_meter_origin: dict[tuple[str, datetime], set[int]] = {}
    for row in materialized_rows:
        steps_by_meter_origin.setdefault((row.meter_urn, row.forecast_origin_ts), set()).add(row.lead_step)
    expected_steps = set(ANOMALY_DETECTION_LEAD_STEPS)
    for (meter_urn, origin_ts), observed_steps in steps_by_meter_origin.items():
        missing = expected_steps - observed_steps
        if missing:
            issues.append(
                AnomalyDetectionValidationIssue("missing_lead_steps", f"{meter_urn}:{origin_ts.isoformat()}", "1|2|3", str(sorted(missing)))
            )
    if not materialized_rows:
        errors = ("warning rows are required",)
    else:
        errors = tuple(issue.issue for issue in issues)
    return AnomalyWarningTaskResult(
        task_id="validate_anomaly_warning_output",
        ok=not errors,
        data={"row_count": len(materialized_rows), "validation_issues": tuple(issues)},
        errors=errors,
        blocked=bool(errors),
    )


def _validate_long_rows_against_config(
    rows: Iterable[AnomalyDetectionLongRow], config: AnomalyWarningRunConfig
) -> tuple[AnomalyDetectionValidationIssue, ...]:
    materialized_rows = tuple(rows)
    issues: list[AnomalyDetectionValidationIssue] = []
    observed_meters = {row.meter_urn for row in materialized_rows}
    expected_meters = set(config.meter_urns)
    missing_meters = tuple(sorted(expected_meters - observed_meters))
    extra_meters = tuple(sorted(observed_meters - expected_meters))
    if missing_meters:
        issues.append(AnomalyDetectionValidationIssue("missing_configured_meters", "meter_urns", ",".join(config.meter_urns), ",".join(missing_meters)))
    if extra_meters:
        issues.append(AnomalyDetectionValidationIssue("unexpected_output_meters", "meter_urns", ",".join(config.meter_urns), ",".join(extra_meters)))
    wrong_origin = tuple(row for row in materialized_rows if row.forecast_origin_ts != config.forecast_origin_ts)
    if wrong_origin:
        issues.append(
            AnomalyDetectionValidationIssue(
                "forecast_origin_ts_mismatch",
                "forecast_origin_ts",
                config.forecast_origin_ts.isoformat(),
                wrong_origin[0].forecast_origin_ts.isoformat(),
            )
        )
    expected_steps = set(ANOMALY_DETECTION_LEAD_STEPS)
    for meter_urn in config.meter_urns:
        observed_steps = {row.lead_step for row in materialized_rows if row.meter_urn == meter_urn and row.forecast_origin_ts == config.forecast_origin_ts}
        missing_steps = expected_steps - observed_steps
        if missing_steps:
            issues.append(
                AnomalyDetectionValidationIssue(
                    "missing_configured_lead_steps",
                    f"{meter_urn}:{config.forecast_origin_ts.isoformat()}",
                    "1|2|3",
                    str(sorted(missing_steps)),
                )
            )
    return tuple(issues)


def record_anomaly_pipeline_metrics(*, rows: Iterable[AnomalyDetectionLongRow]) -> AnomalyWarningTaskResult:
    """Build in-memory anomaly pipeline metrics without writing them anywhere."""

    materialized_rows = tuple(rows)
    meter_urns = tuple(sorted({row.meter_urn for row in materialized_rows}))
    warnings = sum(1 for row in materialized_rows if row.warning_flag)
    source_refs = tuple(dict.fromkeys(ref for row in materialized_rows for ref in row.source_input_refs))
    return AnomalyWarningTaskResult(
        task_id="record_anomaly_pipeline_metrics",
        ok=True,
        data={
            "prediction_count": len(materialized_rows),
            "meter_count": len(meter_urns),
            "meter_urns": meter_urns,
            "warning_count": warnings,
            "source_input_ref_count": len(source_refs),
        },
    )


def publish_anomaly_evidence_packet(
    *, config: AnomalyWarningRunConfig | Mapping[str, Any], metrics_result: AnomalyWarningTaskResult, evidence: Mapping[str, Any]
) -> AnomalyWarningTaskResult:
    """Build an in-memory evidence packet; no publication side effects occur."""

    run_config = _ensure_config(config)
    packet = {
        "forecast_origin_ts": run_config.forecast_origin_ts.isoformat(),
        "environment": run_config.environment,
        "dry_run": run_config.dry_run,
        "writes_enabled": run_config.writes_enabled,
        "model_version": ANOMALY_DETECTION_MODEL_VERSION,
        "evidence_tables": ANOMALY_DETECTION_ALLOWED_EVIDENCE_TABLES,
        "metrics": dict(metrics_result.data),
        "evidence": dict(evidence),
    }
    return AnomalyWarningTaskResult(task_id="publish_anomaly_evidence_packet", ok=True, data={"packet": packet})


def _artifact_descriptor(artifact: AnomalyArtifactDescriptor | AnomalyDetectionArtifactBoundary | Mapping[str, Any]) -> AnomalyArtifactDescriptor:
    if isinstance(artifact, AnomalyArtifactDescriptor):
        return artifact
    if isinstance(artifact, AnomalyDetectionArtifactBoundary):
        return AnomalyArtifactDescriptor(
            adapter_name=artifact.adapter_name,
            release_name=artifact.release_name,
            model_version=artifact.model_version or ANOMALY_DETECTION_MODEL_VERSION,
            artifact_uri=artifact.artifact_uri,
            drive_artifact_verified=artifact.drive_artifact_verified,
            external_io_enabled=artifact.external_io_enabled,
            expected_sha256=None,
        )
    artifact_path = artifact.get("artifact_path")
    return AnomalyArtifactDescriptor(
        adapter_name=str(artifact.get("adapter_name", "anomaly_warning_adapter")),
        release_name=artifact.get("release_name"),
        model_version=str(artifact.get("model_version", "")),
        artifact_uri=artifact.get("artifact_uri"),
        artifact_path=Path(artifact_path) if artifact_path not in (None, "") else None,
        expected_sha256=artifact.get("expected_sha256"),
        drive_artifact_verified=bool(artifact.get("drive_artifact_verified", artifact.get("available", False))),
        external_io_enabled=bool(artifact.get("external_io_enabled", False)),
    )


def _dag_run_conf(context: Mapping[str, Any]) -> Mapping[str, Any] | None:
    dag_run = context.get("dag_run")
    if dag_run is None:
        return None
    conf = getattr(dag_run, "conf", None)
    if conf is None or not isinstance(conf, Mapping):
        return None
    return conf


def _blocked_airflow_result(task_id: str, reason: str) -> AnomalyWarningTaskResult:
    return AnomalyWarningTaskResult(task_id=task_id, ok=False, errors=(reason,), blocked=True)


def _parse_origin_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        origin_ts = value
    elif isinstance(value, str):
        raw_value = value.replace("Z", "+00:00") if value.endswith("Z") else value
        try:
            origin_ts = datetime.fromisoformat(raw_value)
        except ValueError as exc:
            raise ValueError("forecast_origin_ts must be ISO-8601 parseable") from exc
    else:
        raise TypeError("forecast_origin_ts must be a datetime or ISO-8601 string")
    if origin_ts.tzinfo is None or origin_ts.utcoffset() is None:
        raise ValueError("forecast_origin_ts must be timezone-aware")
    if origin_ts.minute != 0 or origin_ts.second != 0 or origin_ts.microsecond != 0:
        raise ValueError("forecast_origin_ts must be aligned to a 1h boundary")
    return origin_ts


def _ensure_config(config: AnomalyWarningRunConfig | Mapping[str, Any]) -> AnomalyWarningRunConfig:
    if isinstance(config, AnomalyWarningRunConfig):
        return config
    return load_anomaly_run_config(config)


__all__ = [
    "AnomalyWarningRunConfig",
    "AnomalyWarningTaskResult",
    "TASK_IDS",
    "airflow_task_entrypoint",
    "gate_anomaly_manual_nonprod_run",
    "gate_anomaly_model_artifact",
    "load_anomaly_run_config",
    "publish_anomaly_evidence_packet",
    "record_anomaly_pipeline_metrics",
    "run_anomaly_warning_adapter",
    "validate_anomaly_warning_output",
]
