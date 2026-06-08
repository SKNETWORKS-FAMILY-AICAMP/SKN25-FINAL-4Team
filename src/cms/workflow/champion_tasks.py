"""Import-safe executable task callables for the champion 1h workflow.

The functions in this module are intentionally pure and in-memory only: no database,
AWS, Kafka, Airflow, filesystem, network, or canonical-table writes occur here.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from cms.contracts.model_input_1h import ModelInput1HRow, ModelInput1HValidationResult, validate_model_input_1h
from cms.modeling.fake_champion_adapter import FakeChampionAdapter, FakeChampionPrediction

KAFKA_EVIDENCE_TOPICS = ("t3b", "t4")
PRE_WARNING_HORIZONS = ("pred_t_plus_1", "pred_t_plus_2", "pred_t_plus_3")
PRODUCTION_ENVIRONMENTS = ("prod", "production")


@dataclass(frozen=True)
class ChampionRunConfig:
    """Explicit non-production/manual champion workflow run configuration."""

    base_ts: datetime
    environment: str = "nonprod"
    manual_run: bool = True
    dry_run: bool = True
    writes_enabled: bool = False
    canonical_writes_enabled: bool = False


@dataclass(frozen=True)
class ChampionTaskResult:
    """Structured in-memory task outcome returned by champion workflow callables."""

    task_id: str
    ok: bool
    data: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    blocked: bool = False


def load_run_config(payload: Mapping[str, Any] | None = None, **overrides: Any) -> ChampionRunConfig:
    """Load and validate an explicit run config from an in-memory mapping.

    ``base_ts`` is mandatory and must be parsed explicitly as a timezone-aware,
    hour-aligned ISO-8601 timestamp. ``Z`` is accepted as UTC.
    """

    values: dict[str, Any] = dict(payload or {})
    values.update(overrides)
    if "base_ts" not in values or values["base_ts"] in (None, ""):
        raise ValueError("base_ts is required")

    return ChampionRunConfig(
        base_ts=_parse_base_ts(values["base_ts"]),
        environment=str(values.get("environment", "nonprod")),
        manual_run=bool(values.get("manual_run", values.get("manual", True))),
        dry_run=bool(values.get("dry_run", True)),
        writes_enabled=bool(values.get("writes_enabled", False)),
        canonical_writes_enabled=bool(values.get("canonical_writes_enabled", False)),
    )


def airflow_task_entrypoint(task_id: str, **context: Any) -> ChampionTaskResult:
    """Safe Airflow ``PythonOperator`` entrypoint for the champion skeleton.

    Airflow can import and call this wrapper without passing the in-memory inputs
    required by the pure task functions below. The wrapper therefore only parses
    the manual run config from ``dag_run.conf`` for ``load_run_config`` and blocks
    every task that lacks enough import-safe context instead of allowing Airflow to
    call required-argument functions directly.
    """

    if not task_id:
        return _blocked_airflow_result("unknown", "task_id is required")

    if task_id != "load_run_config":
        return _blocked_airflow_result(task_id, "Airflow skeleton entrypoint has no import-safe in-memory inputs for this task")

    payload = _dag_run_conf(context)
    if payload is None:
        return _blocked_airflow_result(task_id, "dag_run.conf with base_ts is required")

    try:
        config = load_run_config(payload)
    except (TypeError, ValueError) as exc:
        return _blocked_airflow_result(task_id, str(exc))

    return ChampionTaskResult(task_id=task_id, ok=True, data={"config": config})


def gate_manual_nonprod_run(config: ChampionRunConfig | Mapping[str, Any]) -> ChampionTaskResult:
    """Block production, scheduled/non-manual, or write-enabled runs."""

    run_config = _ensure_config(config)
    errors: list[str] = []
    environment = run_config.environment.lower()
    if environment in PRODUCTION_ENVIRONMENTS:
        errors.append("production environment is blocked for champion dry-run tasks")
    if not run_config.manual_run:
        errors.append("manual_run must be true")
    if not run_config.dry_run:
        errors.append("dry_run must be true")
    if run_config.writes_enabled:
        errors.append("writes_enabled must be false")
    if run_config.canonical_writes_enabled:
        errors.append("canonical_writes_enabled must be false")

    return ChampionTaskResult(
        task_id="gate_manual_nonprod_run",
        ok=not errors,
        data={"environment": run_config.environment, "writes_enabled": run_config.writes_enabled},
        errors=tuple(errors),
        blocked=bool(errors),
    )


def gate_kafka_t3b_t4_evidence(evidence: Mapping[str, Mapping[str, Any]]) -> ChampionTaskResult:
    """Validate in-memory Kafka T3B/T4 evidence for zero lag, DLQ, and retry.

    When consumer accounting fields are present, also enforce:
    ``processed == inserted + duplicate + dlq`` and ``committed == processed``.
    """

    errors: list[str] = []
    for topic in KAFKA_EVIDENCE_TOPICS:
        topic_evidence = evidence.get(topic)
        if topic_evidence is None:
            errors.append(f"{topic} evidence is required")
            continue

        for field_name in ("lag_after", "dlq", "retry"):
            observed = _int_value(topic_evidence, field_name)
            if observed is None:
                errors.append(f"{topic} {field_name} is required")
            elif observed != 0:
                errors.append(f"{topic} {field_name} must be 0")

        processed = _optional_int_value(topic_evidence, "processed")
        inserted = _optional_int_value(topic_evidence, "inserted")
        duplicate = _optional_int_value(topic_evidence, "duplicate")
        dlq = _optional_int_value(topic_evidence, "dlq")
        committed = _optional_int_value(topic_evidence, "committed")
        if processed is not None and inserted is not None and duplicate is not None and dlq is not None and processed != inserted + duplicate + dlq:
            errors.append(f"{topic} processed must equal inserted + duplicate + dlq")
        if processed is not None and committed is not None and committed != processed:
            errors.append(f"{topic} committed must equal processed")

    return ChampionTaskResult(task_id="gate_kafka_t3b_t4_evidence", ok=not errors, data={"evidence": evidence}, errors=tuple(errors), blocked=bool(errors))


def gate_champion_model_artifact(artifact: Mapping[str, Any]) -> ChampionTaskResult:
    """Check that an in-memory champion artifact descriptor is available."""

    errors: list[str] = []
    model_version = str(artifact.get("model_version", ""))
    if not model_version:
        errors.append("model_version is required")
    if artifact.get("available") is not True:
        errors.append("artifact must be available")

    return ChampionTaskResult(
        task_id="gate_champion_model_artifact",
        ok=not errors,
        data={"model_version": model_version, "adapter_name": artifact.get("adapter_name")},
        errors=tuple(errors),
        blocked=bool(errors),
    )


def check_live_1h_readiness(*, rows: Iterable[ModelInput1HRow], config: ChampionRunConfig | Mapping[str, Any]) -> ChampionTaskResult:
    """Validate source 1h rows against the explicit run ``base_ts`` readiness gate."""

    run_config = _ensure_config(config)
    validation_result = validate_model_input_1h(tuple(rows), base_ts=run_config.base_ts)
    return _validation_task_result("check_live_1h_readiness", validation_result)


def validate_model_input_contract(*, rows: Iterable[ModelInput1HRow], config: ChampionRunConfig | Mapping[str, Any]) -> ChampionTaskResult:
    """Validate candidate model input rows against the champion 1h contract."""

    run_config = _ensure_config(config)
    validation_result = validate_model_input_1h(tuple(rows), base_ts=run_config.base_ts)
    return _validation_task_result("validate_model_input_contract", validation_result)


def run_champion_1h_inference_adapter(*, rows: Iterable[ModelInput1HRow], config: ChampionRunConfig | Mapping[str, Any]) -> ChampionTaskResult:
    """Run the import-safe fake champion adapter with the explicit ``base_ts``."""

    run_config = _ensure_config(config)
    predictions = FakeChampionAdapter().predict(tuple(rows), base_ts=run_config.base_ts)
    return ChampionTaskResult(task_id="run_champion_1h_inference_adapter", ok=True, data={"predictions": predictions})


def evaluate_pre_warning_thresholds(
    *, predictions: Iterable[FakeChampionPrediction], thresholds: Mapping[str, float | int]
) -> ChampionTaskResult:
    """Evaluate prediction-only pre-warning thresholds.

    This function intentionally emits only ``pre_warnings`` and never consumes
    actuals/errors or emits posthoc anomaly keys.
    """

    default_threshold = _float_threshold(thresholds.get("max_prediction", math.inf))
    warnings: list[dict[str, float | str]] = []
    for prediction in predictions:
        for horizon in PRE_WARNING_HORIZONS:
            value = float(getattr(prediction, horizon))
            threshold = _float_threshold(thresholds.get(f"{horizon}_max", default_threshold))
            if value >= threshold:
                warnings.append(
                    {"meter_urn": prediction.meter_urn, "horizon": horizon, "prediction": value, "threshold": threshold}
                )

    return ChampionTaskResult(
        task_id="evaluate_pre_warning_thresholds",
        ok=not warnings,
        data={"pre_warnings": tuple(warnings)},
        warnings=tuple(f"{warning['meter_urn']} {warning['horizon']} exceeded pre-warning threshold" for warning in warnings),
    )


def join_posthoc_actuals_and_errors(
    *, predictions: Iterable[FakeChampionPrediction], actuals: Mapping[str, Mapping[str, float | int]]
) -> ChampionTaskResult:
    """Join predictions to in-memory actuals and calculate posthoc errors."""

    errors: list[str] = []
    posthoc_errors: list[dict[str, float | str]] = []
    for prediction in predictions:
        meter_actuals = actuals.get(prediction.meter_urn)
        if meter_actuals is None:
            errors.append(f"{prediction.meter_urn} actuals are required")
            continue
        for horizon in PRE_WARNING_HORIZONS:
            if horizon not in meter_actuals:
                errors.append(f"{prediction.meter_urn} {horizon} actual is required")
                continue
            predicted_value = float(getattr(prediction, horizon))
            actual_value = float(meter_actuals[horizon])
            error = actual_value - predicted_value
            posthoc_errors.append(
                {
                    "meter_urn": prediction.meter_urn,
                    "horizon": horizon,
                    "prediction": predicted_value,
                    "actual": actual_value,
                    "error": error,
                    "abs_error": abs(error),
                }
            )

    return ChampionTaskResult(
        task_id="join_posthoc_actuals_and_errors",
        ok=not errors,
        data={"posthoc_errors": tuple(posthoc_errors)},
        errors=tuple(errors),
        blocked=bool(errors),
    )


def evaluate_posthoc_anomaly_thresholds(
    *, posthoc_errors: Iterable[Mapping[str, Any]], thresholds: Mapping[str, float | int]
) -> ChampionTaskResult:
    """Evaluate actual/error-only posthoc anomaly thresholds.

    This function intentionally emits only ``posthoc_anomalies`` and never emits
    pre-warning keys.
    """

    threshold = _float_threshold(thresholds.get("max_abs_error", math.inf))
    anomalies: list[dict[str, float | str]] = []
    for posthoc_error in posthoc_errors:
        abs_error = float(posthoc_error["abs_error"])
        if abs_error > threshold:
            anomalies.append(
                {
                    "meter_urn": str(posthoc_error["meter_urn"]),
                    "horizon": str(posthoc_error["horizon"]),
                    "abs_error": abs_error,
                    "threshold": threshold,
                }
            )

    return ChampionTaskResult(
        task_id="evaluate_posthoc_anomaly_thresholds",
        ok=not anomalies,
        data={"posthoc_anomalies": tuple(anomalies)},
        warnings=tuple(f"{anomaly['meter_urn']} {anomaly['horizon']} exceeded posthoc anomaly threshold" for anomaly in anomalies),
    )


def record_pipeline_metrics(
    *,
    predictions: Sequence[Any] | Iterable[Any],
    pre_warning_result: ChampionTaskResult,
    posthoc_result: ChampionTaskResult,
) -> ChampionTaskResult:
    """Build in-memory pipeline metrics without writing them anywhere."""

    materialized_predictions = tuple(predictions)
    pre_warnings = tuple(pre_warning_result.data.get("pre_warnings", ()))
    posthoc_anomalies = tuple(posthoc_result.data.get("posthoc_anomalies", ()))
    return ChampionTaskResult(
        task_id="record_pipeline_metrics",
        ok=True,
        data={
            "prediction_count": len(materialized_predictions),
            "pre_warning_count": len(pre_warnings),
            "posthoc_anomaly_count": len(posthoc_anomalies),
        },
    )


def publish_evidence_packet(
    *, config: ChampionRunConfig | Mapping[str, Any], metrics_result: ChampionTaskResult, evidence: Mapping[str, Any]
) -> ChampionTaskResult:
    """Build an in-memory evidence packet; no publication side effects occur."""

    run_config = _ensure_config(config)
    packet = {
        "base_ts": run_config.base_ts.isoformat(),
        "environment": run_config.environment,
        "dry_run": run_config.dry_run,
        "writes_enabled": run_config.writes_enabled,
        "metrics": dict(metrics_result.data),
        "evidence": dict(evidence),
    }
    return ChampionTaskResult(task_id="publish_evidence_packet", ok=True, data={"packet": packet})


def _dag_run_conf(context: Mapping[str, Any]) -> Mapping[str, Any] | None:
    dag_run = context.get("dag_run")
    if dag_run is None:
        return None
    conf = getattr(dag_run, "conf", None)
    if conf is None:
        return None
    if not isinstance(conf, Mapping):
        return None
    return conf


def _blocked_airflow_result(task_id: str, reason: str) -> ChampionTaskResult:
    return ChampionTaskResult(task_id=task_id, ok=False, errors=(reason,), blocked=True)


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
    if base_ts.minute != 0 or base_ts.second != 0 or base_ts.microsecond != 0:
        raise ValueError("base_ts must be aligned to a 1h boundary")
    return base_ts


def _ensure_config(config: ChampionRunConfig | Mapping[str, Any]) -> ChampionRunConfig:
    if isinstance(config, ChampionRunConfig):
        return config
    return load_run_config(config)


def _validation_task_result(task_id: str, validation_result: ModelInput1HValidationResult) -> ChampionTaskResult:
    return ChampionTaskResult(
        task_id=task_id,
        ok=validation_result.ok,
        data={"validation_result": validation_result},
        errors=tuple(issue.issue for issue in validation_result.issues),
        blocked=not validation_result.ok,
    )


def _int_value(mapping: Mapping[str, Any], field_name: str) -> int | None:
    if field_name not in mapping:
        return None
    return int(mapping[field_name])


def _optional_int_value(mapping: Mapping[str, Any], field_name: str) -> int | None:
    if field_name not in mapping:
        return None
    return int(mapping[field_name])


def _float_threshold(value: float | int) -> float:
    return float(value)


__all__ = [
    "ChampionRunConfig",
    "ChampionTaskResult",
    "airflow_task_entrypoint",
    "check_live_1h_readiness",
    "evaluate_posthoc_anomaly_thresholds",
    "evaluate_pre_warning_thresholds",
    "gate_champion_model_artifact",
    "gate_kafka_t3b_t4_evidence",
    "gate_manual_nonprod_run",
    "join_posthoc_actuals_and_errors",
    "load_run_config",
    "publish_evidence_packet",
    "record_pipeline_metrics",
    "run_champion_1h_inference_adapter",
    "validate_model_input_contract",
]
