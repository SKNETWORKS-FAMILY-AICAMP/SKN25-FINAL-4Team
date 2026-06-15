"""Import-safe combined model-serving pipeline skeleton.

This module connects the already separated P-Max forecast lane and anomaly
warning lane for repo-local dry-run verification. It performs no database,
Kafka, Grafana, Airflow scheduler, AWS, artifact filesystem, or canonical-table
side effects.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from typing import Any

from cms.contracts.anomaly_detection_1h import (
    ANOMALY_DETECTION_EVALUATION_TABLE,
    ANOMALY_DETECTION_FORECAST_TABLE,
    ANOMALY_DETECTION_INFERENCE_LOG_TABLE,
    ANOMALY_DETECTION_MODEL_VERSION,
    ANOMALY_DETECTION_RELEASE,
    AnomalyDetectionLongRow,
)
from cms.contracts.pmax_forecast_15min import (
    PMAX_FORECAST_EVALUATION_TABLE,
    PMAX_FORECAST_INFERENCE_LOG_TABLE,
    PMAX_FORECAST_MODEL_VERSION,
    PMAX_FORECAST_REQUIRED_HISTORY_WINDOWS,
    PMAX_FORECAST_REQUIRED_MEASUREMENTS,
    PMAX_FORECAST_TABLE,
    PmaxFeatureReadinessRow,
    PmaxForecastRow,
    pmax_live_observed_source_meters,
)
from cms.contracts.live_pipeline import MART_PEAK_FEATURE_15MIN, SOURCE_MODE_LIVE_OBSERVED
from cms.data.model_serving_queries import build_anomaly_feature_query, build_pmax_feature_query
from cms.data.model_serving_sink import MODEL_SERVING_ABSENT_AWS_TABLES, MODEL_SERVING_EVIDENCE_TABLE, build_model_serving_write_batch
from cms.workflow.anomaly_warning_tasks import (
    AnomalyWarningRunConfig,
    gate_anomaly_manual_nonprod_run,
    gate_anomaly_model_artifact,
    load_anomaly_run_config,
    publish_anomaly_evidence_packet,
    record_anomaly_pipeline_metrics,
    run_anomaly_warning_adapter,
    validate_anomaly_warning_output,
)
from cms.workflow.pmax_forecast_tasks import (
    PmaxForecastRunConfig,
    build_pmax_features,
    check_pmax_feature_readiness,
    gate_manual_nonprod_run,
    gate_pmax_model_artifact,
    load_run_config,
    publish_pmax_evidence_packet,
    record_pmax_pipeline_metrics,
    run_pmax_forecast_adapter,
    validate_pmax_forecast_output,
)

ARTIFACT_ROOT_DEFAULT = "artifacts"
PMAX_ARTIFACT_RELEASE_DIR = "import_pmax_v29_60min"
PRODUCTION_ENVIRONMENTS = ("prod", "production")
FIXTURE_MODE_KEYS = ("runtime_fixture_enabled", "no_write_fixture_mode", "fixture_mode")

TASK_IDS = (
    "load_model_serving_run_config",
    "gate_model_serving_manual_nonprod_run",
    "gate_model_serving_artifacts",
    "build_model_serving_input_queries",
    "run_model_serving_dry_run",
    "validate_cross_lane_consistency",
    "publish_model_serving_evidence_packet",
)


@dataclass(frozen=True)
class ModelServingRunConfig:
    """Explicit dry-run config shared by the two model-serving lanes."""

    base_ts: datetime
    forecast_origin_ts: datetime
    environment: str = "nonprod"
    manual_run: bool = True
    dry_run: bool = True
    writes_enabled: bool = False
    canonical_writes_enabled: bool = False
    pmax_logical_meters: tuple[str, ...] = ("V.Z81",)
    anomaly_meter_urns: tuple[str, ...] = ("H1.K11",)
    anomaly_lane_enabled: bool = False
    run_id: str | None = None
    job_id: str = "model_serving_dry_run"

    def pmax_config(self) -> PmaxForecastRunConfig:
        return PmaxForecastRunConfig(
            base_ts=self.base_ts,
            environment=self.environment,
            manual_run=self.manual_run,
            dry_run=self.dry_run,
            writes_enabled=self.writes_enabled,
            canonical_writes_enabled=self.canonical_writes_enabled,
            logical_meters=self.pmax_logical_meters,
        )

    def anomaly_config(self) -> AnomalyWarningRunConfig:
        return AnomalyWarningRunConfig(
            forecast_origin_ts=self.forecast_origin_ts,
            environment=self.environment,
            manual_run=self.manual_run,
            dry_run=self.dry_run,
            writes_enabled=self.writes_enabled,
            canonical_writes_enabled=self.canonical_writes_enabled,
            meter_urns=self.anomaly_meter_urns,
        )


@dataclass(frozen=True)
class ModelServingArtifactMount:
    """Runtime artifact mount descriptor without checking local files."""

    root_path: str = ARTIFACT_ROOT_DEFAULT
    pmax_artifact_dir: str = "pmax"
    anomaly_artifact_dir: str = "anomaly"
    pmax_release: str = PMAX_ARTIFACT_RELEASE_DIR
    anomaly_release: str = ANOMALY_DETECTION_RELEASE
    pmax_drive_verified: bool = False
    anomaly_drive_verified: bool = False
    external_io_enabled: bool = False

    @property
    def pmax_uri(self) -> str:
        return str(PurePosixPath(self.root_path) / self.pmax_artifact_dir / self.pmax_release)

    @property
    def anomaly_uri(self) -> str:
        return str(PurePosixPath(self.root_path) / self.anomaly_artifact_dir / self.anomaly_release)


@dataclass(frozen=True)
class ModelServingTaskResult:
    """Structured side-effect-free outcome for the combined pipeline."""

    task_id: str
    ok: bool
    data: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    blocked: bool = False


def load_model_serving_run_config(payload: Mapping[str, Any] | None = None, **overrides: Any) -> ModelServingRunConfig:
    """Load a combined P-Max/anomaly config from an in-memory mapping."""

    values: dict[str, Any] = dict(payload or {})
    values.update(overrides)
    pmax_payload = {
        "base_ts": values.get("base_ts"),
        "environment": values.get("environment", "nonprod"),
        "manual_run": values.get("manual_run", values.get("manual", True)),
        "dry_run": values.get("dry_run", True),
        "writes_enabled": values.get("writes_enabled", False),
        "canonical_writes_enabled": values.get("canonical_writes_enabled", False),
        "logical_meters": values.get("pmax_logical_meters", values.get("logical_meters", ("V.Z81",))),
    }
    anomaly_payload = {
        "forecast_origin_ts": values.get("forecast_origin_ts", values.get("base_ts")),
        "environment": values.get("environment", "nonprod"),
        "manual_run": values.get("manual_run", values.get("manual", True)),
        "dry_run": values.get("dry_run", True),
        "writes_enabled": values.get("writes_enabled", False),
        "canonical_writes_enabled": values.get("canonical_writes_enabled", False),
        "meter_urns": values.get("anomaly_meter_urns", values.get("meter_urns", ("H1.K11",))),
    }
    pmax_config = load_run_config(pmax_payload)
    anomaly_config = load_anomaly_run_config(anomaly_payload)
    return ModelServingRunConfig(
        base_ts=pmax_config.base_ts,
        forecast_origin_ts=anomaly_config.forecast_origin_ts,
        environment=pmax_config.environment,
        manual_run=pmax_config.manual_run,
        dry_run=pmax_config.dry_run,
        writes_enabled=pmax_config.writes_enabled,
        canonical_writes_enabled=pmax_config.canonical_writes_enabled,
        pmax_logical_meters=pmax_config.logical_meters,
        anomaly_meter_urns=anomaly_config.meter_urns,
        anomaly_lane_enabled=bool(values.get("anomaly_lane_enabled", values.get("enable_anomaly_lane", False))),
        run_id=str(values["run_id"]) if values.get("run_id") not in (None, "") else None,
        job_id=str(values.get("job_id", "model_serving_dry_run")),
    )


def airflow_task_entrypoint(task_id: str, **context: Any) -> ModelServingTaskResult:
    """Safe Airflow ``PythonOperator`` entrypoint for disabled DAG wiring.

    The import-safe Airflow skeleton can execute config/artifact gates from
    ``dag_run.conf``. Adapter execution remains blocked by default. An explicit
    no-write fixture flag in ``dag_run.conf`` exercises the in-memory callables
    with generated rows and fake models, without artifact filesystem or DB I/O.
    """

    if task_id not in TASK_IDS:
        return _blocked(task_id or "unknown", "unknown combined model-serving task_id")
    payload = _dag_run_conf(context)
    if payload is None:
        return _blocked(task_id, "dag_run.conf with base_ts is required")
    try:
        if task_id == "load_model_serving_run_config":
            config = load_model_serving_run_config(payload)
            return ModelServingTaskResult(task_id=task_id, ok=True, data={"config": config})
        if task_id == "gate_model_serving_manual_nonprod_run":
            return gate_model_serving_manual_nonprod_run(load_model_serving_run_config(payload))
        if task_id == "gate_model_serving_artifacts":
            artifact_payload = payload.get("artifact_mount", payload)
            if not isinstance(artifact_payload, Mapping):
                return _blocked(task_id, "artifact_mount must be a mapping when provided")
            return gate_model_serving_artifacts(artifact_payload)
        if task_id == "build_model_serving_input_queries":
            return build_model_serving_input_queries(load_model_serving_run_config(payload))
        if _fixture_mode_enabled(payload):
            dry_run_result = _run_fixture_model_serving_dry_run(payload)
            if task_id == "run_model_serving_dry_run":
                return dry_run_result
            if dry_run_result.blocked:
                return _blocked(task_id, ";".join(dry_run_result.errors) or "fixture dry-run failed")
            if task_id == "validate_cross_lane_consistency":
                cross = dry_run_result.data.get("cross_validation")
                if isinstance(cross, ModelServingTaskResult):
                    return cross
                return _blocked(task_id, "fixture dry-run did not return cross-validation data")
            if task_id == "publish_model_serving_evidence_packet":
                return ModelServingTaskResult(task_id=task_id, ok=True, data={"packet": dry_run_result.data["packet"]})
    except (TypeError, ValueError) as exc:
        return _blocked(task_id, str(exc))

    blocked_reasons = {
        "run_model_serving_dry_run": "runtime materialized P-Max rows, anomaly feature rows, and model adapters are required",
        "validate_cross_lane_consistency": "P-Max and anomaly output rows are required",
        "publish_model_serving_evidence_packet": "lane evidence packets and cross-validation result are required",
    }
    return _blocked(task_id, blocked_reasons[task_id])


def airflow_xcom_task_entrypoint(task_id: str, **context: Any) -> dict[str, Any]:
    """Airflow ``PythonOperator`` entrypoint that returns XCom-safe plain data."""

    return model_serving_task_result_to_xcom(airflow_task_entrypoint(task_id, **context))


def model_serving_task_result_to_xcom(result: ModelServingTaskResult) -> dict[str, Any]:
    """Convert a task result into JSON/XCom-friendly primitives."""

    return {
        "task_id": result.task_id,
        "ok": result.ok,
        "blocked": result.blocked,
        "warnings": list(result.warnings),
        "errors": list(result.errors),
        "data": _xcom_safe(result.data),
    }


def _xcom_safe(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _xcom_safe(asdict(value))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _xcom_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_xcom_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "__dict__"):
        return _xcom_safe(vars(value))
    return str(value)


def gate_model_serving_manual_nonprod_run(config: ModelServingRunConfig | Mapping[str, Any]) -> ModelServingTaskResult:
    """Gate both lanes and block any write, scheduler, or production execution."""

    run_config = _ensure_config(config)
    pmax_gate = gate_manual_nonprod_run(run_config.pmax_config())
    anomaly_gate = gate_anomaly_manual_nonprod_run(run_config.anomaly_config())
    errors = list(pmax_gate.errors) + list(anomaly_gate.errors)
    if run_config.environment.lower() in PRODUCTION_ENVIRONMENTS:
        errors.append("production environment is blocked for combined model-serving dry runs")
    if run_config.base_ts != run_config.forecast_origin_ts:
        errors.append("base_ts and forecast_origin_ts must match for combined dry-run evidence")
    if run_config.base_ts.minute != 0:
        errors.append("combined model-serving dry-run tick must be 1h aligned")
    return ModelServingTaskResult(
        task_id="gate_model_serving_manual_nonprod_run",
        ok=not errors,
        data={
            "environment": run_config.environment,
            "writes_enabled": run_config.writes_enabled,
            "canonical_writes_enabled": run_config.canonical_writes_enabled,
            "pmax_logical_meters": run_config.pmax_logical_meters,
            "anomaly_meter_urns": run_config.anomaly_meter_urns,
            "anomaly_lane_enabled": run_config.anomaly_lane_enabled,
            "disabled_tables": _disabled_optional_tables(),
        },
        errors=tuple(errors),
        blocked=bool(errors),
    )


def gate_model_serving_artifacts(mount: ModelServingArtifactMount | Mapping[str, Any]) -> ModelServingTaskResult:
    """Validate P-Max and anomaly artifact mount descriptors without file I/O."""

    artifact_mount = _ensure_mount(mount)
    pmax_gate = gate_pmax_model_artifact(
        {
            "adapter_name": "pmax_forecast_adapter",
            "model_version": PMAX_FORECAST_MODEL_VERSION,
            "artifact_uri": artifact_mount.pmax_uri,
            "drive_artifact_verified": artifact_mount.pmax_drive_verified,
            "external_io_enabled": artifact_mount.external_io_enabled,
        }
    )
    anomaly_gate = gate_anomaly_model_artifact(
        {
            "adapter_name": "anomaly_warning_adapter",
            "release_name": artifact_mount.anomaly_release,
            "model_version": ANOMALY_DETECTION_MODEL_VERSION,
            "artifact_uri": artifact_mount.anomaly_uri,
            "drive_artifact_verified": artifact_mount.anomaly_drive_verified,
            "external_io_enabled": artifact_mount.external_io_enabled,
        }
    )
    errors = list(pmax_gate.errors) + list(anomaly_gate.errors)
    return ModelServingTaskResult(
        task_id="gate_model_serving_artifacts",
        ok=not errors,
        data={"pmax": pmax_gate.data, "anomaly": anomaly_gate.data, "artifact_root": artifact_mount.root_path},
        errors=tuple(errors),
        blocked=bool(errors),
    )


def build_model_serving_input_queries(config: ModelServingRunConfig | Mapping[str, Any]) -> ModelServingTaskResult:
    """Build side-effect-free SQL specs for AWS-available model-serving inputs."""

    run_config = _ensure_config(config)
    try:
        pmax_query = build_pmax_feature_query(base_ts=run_config.base_ts, logical_meters=run_config.pmax_logical_meters)
        anomaly_query = (
            build_anomaly_feature_query(forecast_origin_ts=run_config.forecast_origin_ts, meter_urns=run_config.anomaly_meter_urns)
            if run_config.anomaly_lane_enabled
            else None
        )
    except ValueError as exc:
        return _blocked("build_model_serving_input_queries", str(exc))
    return ModelServingTaskResult(
        task_id="build_model_serving_input_queries",
        ok=True,
        data={
            "pmax_feature_query": pmax_query,
            "anomaly_feature_query": anomaly_query,
            "source_tables": pmax_query.source_tables + (anomaly_query.source_tables if anomaly_query is not None else ()),
            "disabled_tables": _disabled_optional_tables(),
        },
        warnings=() if run_config.anomaly_lane_enabled else ("anomaly_lane_disabled_by_config",),
    )


def run_model_serving_dry_run(
    *,
    config: ModelServingRunConfig | Mapping[str, Any],
    artifact_mount: ModelServingArtifactMount | Mapping[str, Any],
    pmax_feature_rows: Iterable[PmaxFeatureReadinessRow],
    pmax_model: Any,
    anomaly_feature_rows: Iterable[Mapping[str, Any]] = (),
    anomaly_model: Any = None,
) -> ModelServingTaskResult:
    """Execute both lanes in memory and return one combined evidence packet."""

    run_config = _ensure_config(config)
    run_gate = gate_model_serving_manual_nonprod_run(run_config)
    artifact_gate = gate_model_serving_artifacts(artifact_mount)
    if run_gate.blocked or artifact_gate.blocked:
        return ModelServingTaskResult(
            task_id="run_model_serving_dry_run",
            ok=False,
            data={"run_gate": run_gate, "artifact_gate": artifact_gate},
            errors=run_gate.errors + artifact_gate.errors,
            blocked=True,
        )

    pmax_rows = tuple(pmax_feature_rows)
    pmax_readiness = check_pmax_feature_readiness(rows=pmax_rows, config=run_config.pmax_config())
    pmax_build = build_pmax_features(rows=pmax_rows, config=run_config.pmax_config())
    if pmax_readiness.blocked or pmax_build.blocked:
        return _lane_blocked("run_model_serving_dry_run", "pmax", pmax_readiness, pmax_build)
    pmax_inference = run_pmax_forecast_adapter(features=pmax_build.data["features"], config=run_config.pmax_config(), model=pmax_model)
    if pmax_inference.blocked:
        return _lane_blocked("run_model_serving_dry_run", "pmax", pmax_inference)
    pmax_forecast_rows = tuple(pmax_inference.data["forecast_rows"])
    pmax_validation = validate_pmax_forecast_output(rows=pmax_forecast_rows)
    if pmax_validation.blocked:
        return _lane_blocked("run_model_serving_dry_run", "pmax", pmax_validation)
    pmax_metrics = record_pmax_pipeline_metrics(rows=pmax_forecast_rows, readiness_result=pmax_readiness.data["readiness_result"])
    pmax_packet = publish_pmax_evidence_packet(
        config=run_config.pmax_config(),
        metrics_result=pmax_metrics,
        evidence={"artifact_uri": _ensure_mount(artifact_mount).pmax_uri, "mode": "combined-dry-run"},
    )

    anomaly_long_rows: tuple[AnomalyDetectionLongRow, ...] = ()
    anomaly_packet_data: Mapping[str, Any] = {
        "status": "disabled",
        "reason": "anomaly lane disabled by config",
        "missing_tables": _disabled_optional_tables(),
    }
    if run_config.anomaly_lane_enabled:
        if anomaly_model is None:
            return _blocked("run_model_serving_dry_run", "anomaly_model is required when anomaly_lane_enabled is true")
        anomaly_inference = run_anomaly_warning_adapter(input_rows=tuple(anomaly_feature_rows), config=run_config.anomaly_config(), model=anomaly_model)
        if anomaly_inference.blocked:
            return _lane_blocked("run_model_serving_dry_run", "anomaly", anomaly_inference)
        anomaly_long_rows = tuple(anomaly_inference.data["long_rows"])
        anomaly_validation = validate_anomaly_warning_output(rows=anomaly_long_rows, config=run_config.anomaly_config())
        if anomaly_validation.blocked:
            return _lane_blocked("run_model_serving_dry_run", "anomaly", anomaly_validation)
        anomaly_metrics = record_anomaly_pipeline_metrics(rows=anomaly_long_rows)
        anomaly_packet = publish_anomaly_evidence_packet(
            config=run_config.anomaly_config(),
            metrics_result=anomaly_metrics,
            evidence={"artifact_uri": _ensure_mount(artifact_mount).anomaly_uri, "mode": "combined-dry-run"},
        )
        anomaly_packet_data = anomaly_packet.data["packet"]

    cross = validate_cross_lane_consistency(config=run_config, pmax_rows=pmax_forecast_rows, anomaly_rows=anomaly_long_rows)
    if cross.blocked:
        return ModelServingTaskResult(
            task_id="run_model_serving_dry_run",
            ok=False,
            data={"pmax_packet": pmax_packet.data["packet"], "anomaly_packet": anomaly_packet_data, "cross_validation": cross},
            errors=cross.errors,
            blocked=True,
        )

    packet = publish_model_serving_evidence_packet(
        config=run_config,
        pmax_packet=pmax_packet.data["packet"],
        anomaly_packet=anomaly_packet_data,
        cross_validation=cross,
    )
    artifact_descriptor = _ensure_mount(artifact_mount)
    write_batch = build_model_serving_write_batch(
        run_id=_run_id(run_config),
        job_id=run_config.job_id,
        started_at=run_config.base_ts,
        finished_at=run_config.base_ts,
        artifact_refs={"pmax": artifact_descriptor.pmax_uri, "anomaly": artifact_descriptor.anomaly_uri},
        pmax_rows=pmax_forecast_rows,
        anomaly_rows=anomaly_long_rows,
        evidence_packet=packet.data["packet"],
        writes_enabled=run_config.writes_enabled,
        canonical_writes_enabled=run_config.canonical_writes_enabled,
    )
    return ModelServingTaskResult(
        task_id="run_model_serving_dry_run",
        ok=True,
        data={
            "packet": packet.data["packet"],
            "pmax_forecast_rows": pmax_forecast_rows,
            "anomaly_long_rows": anomaly_long_rows,
            "cross_validation": cross,
            "write_batch": write_batch,
            "write_attempted": False,
            "canonical_write_attempted": False,
        },
    )


def validate_cross_lane_consistency(
    *, config: ModelServingRunConfig | Mapping[str, Any], pmax_rows: Iterable[PmaxForecastRow], anomaly_rows: Iterable[AnomalyDetectionLongRow]
) -> ModelServingTaskResult:
    """Validate shared schedule, side-effect boundary, and lane separation."""

    run_config = _ensure_config(config)
    forecast_rows = tuple(pmax_rows)
    warning_rows = tuple(anomaly_rows)
    errors: list[str] = []
    if run_config.base_ts != run_config.forecast_origin_ts:
        errors.append("base_ts and forecast_origin_ts must match")
    if run_config.writes_enabled or run_config.canonical_writes_enabled:
        errors.append("combined dry-run requires all writes to stay disabled")
    for row in forecast_rows:
        if row.base_ts != run_config.base_ts:
            errors.append("pmax row base_ts does not match combined config")
            break
    for row in warning_rows:
        if row.forecast_origin_ts != run_config.forecast_origin_ts:
            errors.append("anomaly row forecast_origin_ts does not match combined config")
            break
    if run_config.anomaly_lane_enabled and not warning_rows:
        errors.append("anomaly warning rows are required when anomaly_lane_enabled is true")
    output_tables = _output_tables(include_anomaly=run_config.anomaly_lane_enabled)
    if any(table.startswith("canonical.") for table in output_tables):
        errors.append("model-serving output tables must not target canonical schema")
    if len(set(output_tables)) != len(output_tables):
        errors.append("P-Max and anomaly lanes must not share output table names")
    if not forecast_rows:
        errors.append("pmax forecast rows are required")
    return ModelServingTaskResult(
        task_id="validate_cross_lane_consistency",
        ok=not errors,
        data={"output_tables": output_tables, "pmax_row_count": len(forecast_rows), "anomaly_row_count": len(warning_rows)},
        errors=tuple(errors),
        blocked=bool(errors),
    )


def publish_model_serving_evidence_packet(
    *,
    config: ModelServingRunConfig | Mapping[str, Any],
    pmax_packet: Mapping[str, Any],
    anomaly_packet: Mapping[str, Any],
    cross_validation: ModelServingTaskResult,
) -> ModelServingTaskResult:
    """Build one combined evidence packet; no publication side effects occur."""

    run_config = _ensure_config(config)
    packet = {
        "base_ts": run_config.base_ts.isoformat(),
        "forecast_origin_ts": run_config.forecast_origin_ts.isoformat(),
        "environment": run_config.environment,
        "dry_run": run_config.dry_run,
        "writes_enabled": run_config.writes_enabled,
        "canonical_writes_enabled": run_config.canonical_writes_enabled,
        "disabled_tables": _disabled_optional_tables(),
        "evidence_table": {"table": MODEL_SERVING_EVIDENCE_TABLE, "status": "write_gated"},
        "lanes": {
            "pmax": dict(pmax_packet),
            "anomaly": dict(anomaly_packet),
        },
        "cross_validation": dict(cross_validation.data),
    }
    return ModelServingTaskResult(task_id="publish_model_serving_evidence_packet", ok=True, data={"packet": packet})


class _FixturePmaxModel:
    """Tiny deterministic model for explicit no-write Airflow fixture runs."""

    def predict(self, rows: Any) -> list[list[float]]:
        return [[10.0, 11.0, 12.0, 13.0] for _ in rows]


def _fixture_mode_enabled(payload: Mapping[str, Any]) -> bool:
    return any(_explicit_true(payload.get(key)) for key in FIXTURE_MODE_KEYS)


def _run_fixture_model_serving_dry_run(payload: Mapping[str, Any]) -> ModelServingTaskResult:
    config = _fixture_run_config(payload)
    artifact_mount = _fixture_artifact_mount()
    return run_model_serving_dry_run(
        config=config,
        artifact_mount=artifact_mount,
        pmax_feature_rows=_fixture_pmax_feature_rows(config),
        pmax_model=_FixturePmaxModel(),
        anomaly_feature_rows=(),
        anomaly_model=None,
    )


def _fixture_run_config(payload: Mapping[str, Any]) -> ModelServingRunConfig:
    if "base_ts" not in payload or payload.get("base_ts") in (None, ""):
        raise ValueError("base_ts is required for no-write fixture mode")
    environment = str(payload.get("environment", "nonprod"))
    if environment.lower() in PRODUCTION_ENVIRONMENTS:
        raise ValueError("fixture mode is blocked for production environments")
    if _explicit_true(payload.get("writes_enabled")):
        raise ValueError("fixture mode requires writes_enabled=false")
    if _explicit_true(payload.get("canonical_writes_enabled")):
        raise ValueError("fixture mode requires canonical_writes_enabled=false")
    if "manual_run" in payload and not _config_bool(payload.get("manual_run"), default=True):
        raise ValueError("fixture mode requires manual_run=true")
    if "manual" in payload and not _config_bool(payload.get("manual"), default=True):
        raise ValueError("fixture mode requires manual_run=true")
    if "dry_run" in payload and not _config_bool(payload.get("dry_run"), default=True):
        raise ValueError("fixture mode requires dry_run=true")

    values = dict(payload)
    values["environment"] = "nonprod"
    values["manual_run"] = True
    values["dry_run"] = True
    values["writes_enabled"] = False
    values["canonical_writes_enabled"] = False
    values["forecast_origin_ts"] = values["base_ts"]
    return load_model_serving_run_config(values)


def _fixture_artifact_mount() -> ModelServingArtifactMount:
    return ModelServingArtifactMount(
        root_path="in_memory_fixture_artifacts",
        pmax_drive_verified=True,
        anomaly_drive_verified=True,
        external_io_enabled=False,
    )


def _fixture_pmax_feature_rows(config: ModelServingRunConfig) -> tuple[PmaxFeatureReadinessRow, ...]:
    input_end_ts = config.base_ts - timedelta(minutes=15)
    start_ts = input_end_ts - timedelta(minutes=15 * (PMAX_FORECAST_REQUIRED_HISTORY_WINDOWS - 1))
    rows: list[PmaxFeatureReadinessRow] = []
    for logical_meter in config.pmax_logical_meters:
        try:
            source_meter = pmax_live_observed_source_meters(logical_meter)[0]
        except KeyError as exc:
            raise ValueError(f"unsupported logical_meters: {logical_meter}") from exc
        for offset in range(PMAX_FORECAST_REQUIRED_HISTORY_WINDOWS):
            window_ts = start_ts + timedelta(minutes=15 * offset)
            for measurement in PMAX_FORECAST_REQUIRED_MEASUREMENTS:
                rows.append(_fixture_pmax_feature_row(window_ts, source_meter, measurement, config=config, offset=offset))
    return tuple(rows)


def _fixture_pmax_feature_row(
    window_ts: datetime, source_meter: str, measurement: str, *, config: ModelServingRunConfig, offset: int
) -> PmaxFeatureReadinessRow:
    p_value = 10.0 + float(offset % 96) / 10.0
    mean_value = p_value if measurement == "P" else (220.0 if measurement == "U1" else 0.95)
    max_value = p_value + 0.5 if measurement == "P" else mean_value
    return PmaxFeatureReadinessRow(
        window_ts=window_ts,
        meter_urn=source_meter,
        measurement=measurement,
        mean_value=mean_value,
        max_value=max_value,
        min_value=mean_value,
        p95_value=max_value,
        p99_value=max_value,
        std_value=0.1 if measurement == "P" else 0.0,
        last_value=mean_value,
        peak_ts=window_ts,
        peak_value=max_value,
        observed_points=15,
        expected_points=15,
        coverage_ratio=1.0,
        source_file="fixture_live_observed.jsonl",
        run_id=_run_id(config),
        created_at=config.base_ts,
        source_layer=MART_PEAK_FEATURE_15MIN,
        source_mode=SOURCE_MODE_LIVE_OBSERVED,
        provenance={"fixture_mode": True, "external_io": False},
    )


def _explicit_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "y", "on"}
    return value == 1


def _config_bool(value: Any, *, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "t", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "f", "no", "n", "off"}:
            return False
    return bool(value)


def _ensure_config(config: ModelServingRunConfig | Mapping[str, Any]) -> ModelServingRunConfig:
    if isinstance(config, ModelServingRunConfig):
        return config
    return load_model_serving_run_config(config)


def _ensure_mount(mount: ModelServingArtifactMount | Mapping[str, Any]) -> ModelServingArtifactMount:
    if isinstance(mount, ModelServingArtifactMount):
        return mount
    return ModelServingArtifactMount(
        root_path=str(mount.get("root_path", ARTIFACT_ROOT_DEFAULT)),
        pmax_artifact_dir=str(mount.get("pmax_artifact_dir", "pmax")),
        anomaly_artifact_dir=str(mount.get("anomaly_artifact_dir", "anomaly")),
        pmax_release=str(mount.get("pmax_release", PMAX_ARTIFACT_RELEASE_DIR)),
        anomaly_release=str(mount.get("anomaly_release", ANOMALY_DETECTION_RELEASE)),
        pmax_drive_verified=bool(mount.get("pmax_drive_verified", False)),
        anomaly_drive_verified=bool(mount.get("anomaly_drive_verified", False)),
        external_io_enabled=bool(mount.get("external_io_enabled", False)),
    )


def _run_id(config: ModelServingRunConfig) -> str:
    if config.run_id:
        return config.run_id
    return "model_serving_" + config.base_ts.strftime("%Y%m%dT%H%M%SZ")


def _dag_run_conf(context: Mapping[str, Any]) -> Mapping[str, Any] | None:
    dag_run = context.get("dag_run")
    if dag_run is None:
        return None
    conf = getattr(dag_run, "conf", None)
    if conf is None or not isinstance(conf, Mapping):
        return None
    return conf


def _blocked(task_id: str, reason: str) -> ModelServingTaskResult:
    return ModelServingTaskResult(task_id=task_id, ok=False, errors=(reason,), blocked=True)


def _lane_blocked(task_id: str, lane: str, *results: Any) -> ModelServingTaskResult:
    errors = tuple(error for result in results for error in getattr(result, "errors", ()))
    return ModelServingTaskResult(task_id=task_id, ok=False, data={"lane": lane, "results": results}, errors=errors, blocked=True)


def _disabled_optional_tables() -> tuple[str, ...]:
    return MODEL_SERVING_ABSENT_AWS_TABLES


def _anomaly_absent_blocker() -> str:
    absent = ",".join(table for table in MODEL_SERVING_ABSENT_AWS_TABLES if table != MODEL_SERVING_EVIDENCE_TABLE)
    return "anomaly feature source is not materialized: " + absent


def _output_tables(*, include_anomaly: bool = False) -> tuple[str, ...]:
    tables = (
        PMAX_FORECAST_TABLE,
        PMAX_FORECAST_INFERENCE_LOG_TABLE,
        PMAX_FORECAST_EVALUATION_TABLE,
    )
    if include_anomaly:
        tables = tables + (
            ANOMALY_DETECTION_FORECAST_TABLE,
            ANOMALY_DETECTION_INFERENCE_LOG_TABLE,
            ANOMALY_DETECTION_EVALUATION_TABLE,
        )
    return tables


__all__ = [
    "ARTIFACT_ROOT_DEFAULT",
    "TASK_IDS",
    "ModelServingArtifactMount",
    "ModelServingRunConfig",
    "ModelServingTaskResult",
    "airflow_task_entrypoint",
    "airflow_xcom_task_entrypoint",
    "build_model_serving_input_queries",
    "gate_model_serving_artifacts",
    "gate_model_serving_manual_nonprod_run",
    "load_model_serving_run_config",
    "publish_model_serving_evidence_packet",
    "run_model_serving_dry_run",
    "validate_cross_lane_consistency",
]
