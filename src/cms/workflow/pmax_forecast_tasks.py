"""Import-safe P-Max forecast workflow task callables.

These functions are repo-local and in-memory only. They do not perform DB writes,
Grafana changes, Airflow scheduler/webserver work, Kafka/AWS access, filesystem
artifact loading, or canonical-table mutation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from cms.contracts.pmax_forecast_15min import (
    PMAX_FORECAST_HORIZON_MINUTES,
    PMAX_FORECAST_LOGICAL_METER_SOURCES,
    PMAX_FORECAST_MODEL_VERSION,
    PmaxFeatureReadinessRow,
    PmaxForecastArtifactBoundary,
    PmaxForecastRow,
    validate_pmax_feature_readiness,
    validate_pmax_forecast_row,
)
from cms.modeling.pmax_artifact_loader import PmaxArtifactDescriptor, PmaxArtifactLoader, PmaxArtifactLoaderError, PmaxReleaseArtifactLoader
from cms.modeling.pmax_feature_builder import PmaxFeatureBuildResult, PmaxFeatureVector, build_pmax_feature_vectors
from cms.modeling.pmax_forecast_adapter import PmaxForecastAdapter, PmaxForecastPredictionError

PRODUCTION_ENVIRONMENTS = ("prod", "production")


@dataclass(frozen=True)
class PmaxForecastRunConfig:
    """Explicit non-production/manual P-Max forecast run configuration."""

    base_ts: datetime
    environment: str = "nonprod"
    manual_run: bool = True
    dry_run: bool = True
    writes_enabled: bool = False
    canonical_writes_enabled: bool = False
    logical_meters: tuple[str, ...] = tuple(PMAX_FORECAST_LOGICAL_METER_SOURCES)


@dataclass(frozen=True)
class PmaxForecastTaskResult:
    """Structured in-memory task outcome returned by P-Max task callables."""

    task_id: str
    ok: bool
    data: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    blocked: bool = False


TASK_IDS = (
    "load_run_config",
    "gate_manual_nonprod_run",
    "gate_pmax_model_artifact",
    "check_pmax_feature_readiness",
    "build_pmax_features",
    "run_pmax_forecast_adapter",
    "validate_pmax_forecast_output",
    "record_pmax_pipeline_metrics",
    "publish_pmax_evidence_packet",
)


def load_run_config(payload: Mapping[str, Any] | None = None, **overrides: Any) -> PmaxForecastRunConfig:
    """Load and validate explicit run config from an in-memory mapping."""

    values: dict[str, Any] = dict(payload or {})
    values.update(overrides)
    if "base_ts" not in values or values["base_ts"] in (None, ""):
        raise ValueError("base_ts is required")

    logical_meters = values.get("logical_meters", tuple(PMAX_FORECAST_LOGICAL_METER_SOURCES))
    if isinstance(logical_meters, str):
        logical_meter_tuple = tuple(part.strip() for part in logical_meters.split(",") if part.strip())
    else:
        logical_meter_tuple = tuple(str(meter) for meter in logical_meters)

    return PmaxForecastRunConfig(
        base_ts=_parse_base_ts(values["base_ts"]),
        environment=str(values.get("environment", "nonprod")),
        manual_run=bool(values.get("manual_run", values.get("manual", True))),
        dry_run=bool(values.get("dry_run", True)),
        writes_enabled=bool(values.get("writes_enabled", False)),
        canonical_writes_enabled=bool(values.get("canonical_writes_enabled", False)),
        logical_meters=logical_meter_tuple,
    )


def airflow_task_entrypoint(task_id: str, **context: Any) -> PmaxForecastTaskResult:
    """Safe Airflow ``PythonOperator`` entrypoint for import-only DAG wiring.

    Config and artifact gates can run from ``dag_run.conf``. Feature readiness,
    feature building, and adapter execution require materialized rows/model
    objects from the runtime task layer and therefore return precise blocked
    results rather than touching external systems.
    """

    if not task_id:
        return _blocked_airflow_result("unknown", "task_id is required")
    if task_id not in TASK_IDS:
        return _blocked_airflow_result(task_id, "unknown P-Max task_id")
    payload = _dag_run_conf(context)
    if payload is None:
        return _blocked_airflow_result(task_id, "dag_run.conf with base_ts is required")
    try:
        if task_id == "load_run_config":
            config = load_run_config(payload)
            return PmaxForecastTaskResult(task_id=task_id, ok=True, data={"config": config})
        if task_id == "gate_manual_nonprod_run":
            return gate_manual_nonprod_run(load_run_config(payload))
        if task_id == "gate_pmax_model_artifact":
            artifact_payload = payload.get("artifact", payload.get("artifact_boundary", payload))
            if not isinstance(artifact_payload, Mapping):
                return _blocked_airflow_result(task_id, "artifact must be a mapping when provided")
            return gate_pmax_model_artifact(artifact_payload)
    except (TypeError, ValueError) as exc:
        return _blocked_airflow_result(task_id, str(exc))

    blocked_reasons = {
        "check_pmax_feature_readiness": "materialized P-Max feature readiness rows are required",
        "build_pmax_features": "materialized P-Max feature readiness rows are required",
        "run_pmax_forecast_adapter": "P-Max feature vectors and model adapter are required",
        "validate_pmax_forecast_output": "P-Max forecast rows are required",
        "record_pmax_pipeline_metrics": "P-Max forecast rows are required",
        "publish_pmax_evidence_packet": "P-Max metrics and evidence inputs are required",
    }
    return _blocked_airflow_result(task_id, blocked_reasons[task_id])


def gate_manual_nonprod_run(config: PmaxForecastRunConfig | Mapping[str, Any]) -> PmaxForecastTaskResult:
    """Block production, scheduler/non-manual, non-dry-run, and write-enabled runs."""

    run_config = _ensure_config(config)
    errors: list[str] = []
    if run_config.environment.lower() in PRODUCTION_ENVIRONMENTS:
        errors.append("production environment is blocked for P-Max dry-run tasks")
    if not run_config.manual_run:
        errors.append("manual_run must be true")
    if not run_config.dry_run:
        errors.append("dry_run must be true")
    if run_config.writes_enabled:
        errors.append("writes_enabled must be false")
    if run_config.canonical_writes_enabled:
        errors.append("canonical_writes_enabled must be false")
    unsupported = tuple(meter for meter in run_config.logical_meters if meter not in PMAX_FORECAST_LOGICAL_METER_SOURCES)
    if unsupported:
        errors.append("unsupported logical_meters: " + ",".join(unsupported))

    return PmaxForecastTaskResult(
        task_id="gate_manual_nonprod_run",
        ok=not errors,
        data={"environment": run_config.environment, "writes_enabled": run_config.writes_enabled, "logical_meters": run_config.logical_meters},
        errors=tuple(errors),
        blocked=bool(errors),
    )


def gate_pmax_model_artifact(artifact: PmaxArtifactDescriptor | PmaxForecastArtifactBoundary | Mapping[str, Any]) -> PmaxForecastTaskResult:
    """Validate an in-memory P-Max artifact descriptor/boundary without loading it."""

    descriptor = _artifact_descriptor(artifact)
    errors: list[str] = []
    if descriptor.model_version != PMAX_FORECAST_MODEL_VERSION:
        errors.append(f"model_version must be {PMAX_FORECAST_MODEL_VERSION}")
    if not descriptor.available:
        errors.append("artifact must be available")
    if descriptor.external_io_enabled:
        errors.append("external_io_enabled must be false")

    return PmaxForecastTaskResult(
        task_id="gate_pmax_model_artifact",
        ok=not errors,
        data=descriptor.as_dict(),
        errors=tuple(errors),
        blocked=bool(errors),
    )


def check_pmax_feature_readiness(*, rows: Iterable[PmaxFeatureReadinessRow], config: PmaxForecastRunConfig | Mapping[str, Any]) -> PmaxForecastTaskResult:
    """Validate latest 96-window P-Max feature readiness from in-memory rows."""

    run_config = _ensure_config(config)
    result = validate_pmax_feature_readiness(tuple(rows), base_ts=run_config.base_ts, logical_meters=run_config.logical_meters)
    return PmaxForecastTaskResult(
        task_id="check_pmax_feature_readiness",
        ok=result.ok,
        data={"readiness_result": result},
        errors=tuple(issue.issue for issue in result.issues),
        blocked=not result.ok,
    )


def build_pmax_features(
    *, rows: Iterable[PmaxFeatureReadinessRow], config: PmaxForecastRunConfig | Mapping[str, Any], history_windows: int | None = None
) -> PmaxForecastTaskResult:
    """Build ordered P-Max model feature vectors from in-memory rows."""

    run_config = _ensure_config(config)
    try:
        materialized_rows = tuple(rows)
        if history_windows is None:
            result: PmaxFeatureBuildResult = build_pmax_feature_vectors(
                materialized_rows,
                base_ts=run_config.base_ts,
                logical_meters=run_config.logical_meters,
            )
        else:
            result = build_pmax_feature_vectors(
                materialized_rows,
                base_ts=run_config.base_ts,
                logical_meters=run_config.logical_meters,
                history_windows=history_windows,
            )
    except ValueError as exc:
        return PmaxForecastTaskResult(task_id="build_pmax_features", ok=False, errors=(str(exc),), blocked=True)

    return PmaxForecastTaskResult(
        task_id="build_pmax_features",
        ok=result.ok,
        data={"feature_build_result": result, "features": result.features},
        errors=result.errors,
        warnings=result.warnings,
        blocked=not result.ok,
    )


def run_pmax_forecast_adapter(
    *,
    features: Iterable[PmaxFeatureVector],
    config: PmaxForecastRunConfig | Mapping[str, Any],
    model: Any | None = None,
    artifact_loader: PmaxArtifactLoader | PmaxReleaseArtifactLoader | None = None,
    adapter: PmaxForecastAdapter | None = None,
) -> PmaxForecastTaskResult:
    """Run P-Max inference with an explicit fake model/adapter or lazy loader."""

    _ensure_config(config)
    if adapter is None:
        model_or_loader = model if model is not None else artifact_loader
        if model_or_loader is None:
            return PmaxForecastTaskResult(task_id="run_pmax_forecast_adapter", ok=False, errors=("model or artifact_loader is required",), blocked=True)
        adapter = PmaxForecastAdapter(model=model_or_loader)
    try:
        result = adapter.predict(tuple(features))
    except (PmaxForecastPredictionError, PmaxArtifactLoaderError) as exc:
        return PmaxForecastTaskResult(task_id="run_pmax_forecast_adapter", ok=False, errors=(str(exc),), blocked=True)
    return PmaxForecastTaskResult(
        task_id="run_pmax_forecast_adapter",
        ok=result.ok,
        data={"forecast_rows": result.rows, "validation_issues": result.validation_issues},
        errors=tuple(issue.issue for issue in result.validation_issues),
        blocked=not result.ok,
    )


def validate_pmax_forecast_output(*, rows: Iterable[PmaxForecastRow]) -> PmaxForecastTaskResult:
    """Validate forecast rows and expected horizon coverage in memory."""

    materialized_rows = tuple(rows)
    issues = tuple(issue for row in materialized_rows for issue in validate_pmax_forecast_row(row))
    errors = [issue.issue for issue in issues]
    horizons_by_meter_base: dict[tuple[str, datetime], set[int]] = {}
    for row in materialized_rows:
        horizons_by_meter_base.setdefault((row.logical_meter, row.base_ts), set()).add(row.horizon_minutes)
    expected_horizons = set(PMAX_FORECAST_HORIZON_MINUTES)
    for (logical_meter, base_ts), observed_horizons in horizons_by_meter_base.items():
        missing = expected_horizons - observed_horizons
        if missing:
            errors.append(f"{logical_meter} {base_ts.isoformat()} missing horizons: {sorted(missing)}")
    if not materialized_rows:
        errors.append("forecast rows are required")

    return PmaxForecastTaskResult(
        task_id="validate_pmax_forecast_output",
        ok=not errors,
        data={"row_count": len(materialized_rows), "validation_issues": issues},
        errors=tuple(errors),
        blocked=bool(errors),
    )


def record_pmax_pipeline_metrics(*, rows: Sequence[PmaxForecastRow] | Iterable[PmaxForecastRow], readiness_result: Any | None = None) -> PmaxForecastTaskResult:
    """Build in-memory P-Max pipeline metrics without writing them anywhere."""

    materialized_rows = tuple(rows)
    logical_meters = tuple(sorted({row.logical_meter for row in materialized_rows}))
    readiness_ok = getattr(readiness_result, "ok", None)
    return PmaxForecastTaskResult(
        task_id="record_pmax_pipeline_metrics",
        ok=True,
        data={"prediction_count": len(materialized_rows), "logical_meter_count": len(logical_meters), "logical_meters": logical_meters, "readiness_ok": readiness_ok},
    )


def publish_pmax_evidence_packet(
    *, config: PmaxForecastRunConfig | Mapping[str, Any], metrics_result: PmaxForecastTaskResult, evidence: Mapping[str, Any]
) -> PmaxForecastTaskResult:
    """Build an in-memory evidence packet; no publication side effects occur."""

    run_config = _ensure_config(config)
    packet = {
        "base_ts": run_config.base_ts.isoformat(),
        "environment": run_config.environment,
        "dry_run": run_config.dry_run,
        "writes_enabled": run_config.writes_enabled,
        "model_version": PMAX_FORECAST_MODEL_VERSION,
        "metrics": dict(metrics_result.data),
        "evidence": dict(evidence),
    }
    return PmaxForecastTaskResult(task_id="publish_pmax_evidence_packet", ok=True, data={"packet": packet})


def _artifact_descriptor(artifact: PmaxArtifactDescriptor | PmaxForecastArtifactBoundary | Mapping[str, Any]) -> PmaxArtifactDescriptor:
    if isinstance(artifact, PmaxArtifactDescriptor):
        return artifact
    if isinstance(artifact, PmaxForecastArtifactBoundary):
        return PmaxArtifactDescriptor(
            adapter_name=artifact.adapter_name,
            model_version=artifact.model_version or PMAX_FORECAST_MODEL_VERSION,
            artifact_uri=artifact.artifact_uri,
            drive_artifact_verified=artifact.drive_artifact_verified,
            external_io_enabled=artifact.external_io_enabled,
            expected_sha256=None,
        )
    available_hint = bool(artifact.get("available", False))
    artifact_path = artifact.get("artifact_path")
    return PmaxArtifactDescriptor(
        adapter_name=str(artifact.get("adapter_name", "pmax_forecast_adapter")),
        model_version=str(artifact.get("model_version", "")),
        artifact_uri=artifact.get("artifact_uri"),
        artifact_path=Path(artifact_path) if artifact_path not in (None, "") else None,
        expected_sha256=artifact.get("expected_sha256"),
        drive_artifact_verified=bool(artifact.get("drive_artifact_verified", available_hint)),
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


def _blocked_airflow_result(task_id: str, reason: str) -> PmaxForecastTaskResult:
    return PmaxForecastTaskResult(task_id=task_id, ok=False, errors=(reason,), blocked=True)


def _parse_base_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        base_ts = value
    elif isinstance(value, str):
        raw_value = value.replace("Z", "+00:00") if value.endswith("Z") else value
        try:
            base_ts = datetime.fromisoformat(raw_value)
        except ValueError as exc:
            raise ValueError("base_ts must be ISO-8601 parseable") from exc
    else:
        raise TypeError("base_ts must be a datetime or ISO-8601 string")

    if base_ts.tzinfo is None or base_ts.utcoffset() is None:
        raise ValueError("base_ts must be timezone-aware")
    if base_ts.minute % 15 != 0 or base_ts.second != 0 or base_ts.microsecond != 0:
        raise ValueError("base_ts must be aligned to a 15min boundary")
    return base_ts


def _ensure_config(config: PmaxForecastRunConfig | Mapping[str, Any]) -> PmaxForecastRunConfig:
    if isinstance(config, PmaxForecastRunConfig):
        return config
    return load_run_config(config)


__all__ = [
    "PmaxForecastRunConfig",
    "PmaxForecastTaskResult",
    "TASK_IDS",
    "airflow_task_entrypoint",
    "build_pmax_features",
    "check_pmax_feature_readiness",
    "gate_manual_nonprod_run",
    "gate_pmax_model_artifact",
    "load_run_config",
    "publish_pmax_evidence_packet",
    "record_pmax_pipeline_metrics",
    "run_pmax_forecast_adapter",
    "validate_pmax_forecast_output",
]
