"""Lazy P-Max forecast artifact loader boundaries.

This module is intentionally import-safe: importing it performs no filesystem,
joblib, Drive, network, database, or Airflow I/O. Actual joblib loading is
performed only when :meth:`PmaxArtifactLoader.load` is called.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cms.contracts.pmax_forecast_15min import (
    PMAX_FORECAST_ARTIFACT_ADAPTER_STUB,
    PMAX_FORECAST_CANDIDATE_VERSIONS,
    PMAX_FORECAST_FEATURE_COLUMNS,
    PMAX_FORECAST_HORIZON_MINUTES,
    PMAX_FORECAST_INPUT_TABLE,
    PMAX_FORECAST_LOGICAL_METER_SOURCES,
    PMAX_FORECAST_MODEL_VERSION,
    PMAX_FORECAST_PRODUCTION_RELEASE,
    PMAX_FORECAST_PRODUCTION_RELEASE_SHA256,
    PmaxForecastArtifactBoundary,
)

ArtifactLoaderFn = Callable[[Path], Any]


class PmaxArtifactLoaderError(RuntimeError):
    """Base error for P-Max artifact loader failures."""


class PmaxArtifactUnavailableError(PmaxArtifactLoaderError):
    """Raised when the configured P-Max artifact cannot be loaded locally."""


class PmaxArtifactIntegrityError(PmaxArtifactLoaderError):
    """Raised when a local artifact does not match the expected checksum."""


@dataclass(frozen=True)
class PmaxArtifactDescriptor:
    """Repo-local descriptor for a P-Max model artifact.

    ``artifact_uri`` may point at an external registry/Drive object, but this
    loader will not dereference external URIs. Use ``artifact_path`` for explicit
    local loading, and inject ``loader`` in tests to avoid joblib dependency/I/O.
    """

    adapter_name: str = PMAX_FORECAST_ARTIFACT_ADAPTER_STUB
    release_name: str | None = PMAX_FORECAST_PRODUCTION_RELEASE
    model_version: str = PMAX_FORECAST_MODEL_VERSION
    artifact_uri: str | None = None
    artifact_path: Path | None = None
    expected_sha256: str | None = PMAX_FORECAST_PRODUCTION_RELEASE_SHA256
    drive_artifact_verified: bool = False
    external_io_enabled: bool = False

    @property
    def available(self) -> bool:
        return bool(self.model_version and (self.artifact_path is not None or (self.drive_artifact_verified and self.artifact_uri)) and not self.external_io_enabled)

    def as_boundary(self) -> PmaxForecastArtifactBoundary:
        return PmaxForecastArtifactBoundary(
            adapter_name=self.adapter_name,
            drive_artifact_verified=self.drive_artifact_verified,
            external_io_enabled=self.external_io_enabled,
            artifact_uri=self.artifact_uri or (self.artifact_path.as_posix() if self.artifact_path is not None else None),
            model_version=self.model_version,
        )

    def as_dict(self) -> dict[str, str | bool | None]:
        return {
            "adapter_name": self.adapter_name,
            "release_name": self.release_name,
            "model_version": self.model_version,
            "artifact_uri": self.artifact_uri,
            "artifact_path": self.artifact_path.as_posix() if self.artifact_path is not None else None,
            "expected_sha256": self.expected_sha256,
            "drive_artifact_verified": self.drive_artifact_verified,
            "external_io_enabled": self.external_io_enabled,
            "available": self.available,
        }


class PmaxArtifactLoader:
    """Lazy loader for a local P-Max artifact.

    The optional ``loader`` hook is the test seam. In production-like local use,
    omitting it imports ``joblib`` only inside :meth:`load`.
    """

    def __init__(self, descriptor: PmaxArtifactDescriptor | Mapping[str, Any], *, loader: ArtifactLoaderFn | None = None) -> None:
        self.descriptor = _ensure_descriptor(descriptor)
        self._loader = loader
        self._loaded_model: Any | None = None

    @property
    def loaded(self) -> bool:
        return self._loaded_model is not None

    def describe(self) -> dict[str, str | bool | None]:
        description = self.descriptor.as_dict()
        description["loaded"] = self.loaded
        return description

    def load(self) -> Any:
        """Load and cache the artifact model.

        External URIs are deliberately not dereferenced. A local path or injected
        loader is required for real loading.
        """

        if self._loaded_model is not None:
            return self._loaded_model

        if self.descriptor.external_io_enabled:
            raise PmaxArtifactUnavailableError("external_io_enabled artifacts are blocked in repo-local loader")
        if self.descriptor.artifact_path is None:
            raise PmaxArtifactUnavailableError("artifact_path is required for repo-local lazy loading")
        if not self.descriptor.artifact_path.exists():
            raise PmaxArtifactUnavailableError(f"artifact_path does not exist: {self.descriptor.artifact_path}")

        if self.descriptor.expected_sha256:
            observed = sha256_file(self.descriptor.artifact_path)
            if observed != self.descriptor.expected_sha256:
                raise PmaxArtifactIntegrityError(f"artifact sha256 mismatch: expected {self.descriptor.expected_sha256}, observed {observed}")

        loader = self._loader or _joblib_load
        self._loaded_model = loader(self.descriptor.artifact_path)
        return self._loaded_model


@dataclass(frozen=True)
class PmaxMeterEnsemble:
    logical_meter: str
    manifest: Mapping[str, Any]
    weights: Mapping[str, float]
    models: Mapping[str, Any]


class PmaxReleaseEnsembleModel:
    """Loaded v29 per-meter ensemble model for P-Max feature vectors."""

    def __init__(self, meters: Mapping[str, PmaxMeterEnsemble]) -> None:
        self.meters = dict(meters)

    def predict_features(self, features: Sequence[Any], rows: Sequence[Sequence[float]]) -> tuple[tuple[float, ...], ...]:
        if len(features) != len(rows):
            raise PmaxArtifactLoaderError("feature metadata count must match model row count")
        predictions: list[tuple[float, ...]] = []
        for feature, row in zip(features, rows, strict=True):
            ensemble = self.meters.get(feature.logical_meter)
            if ensemble is None:
                raise PmaxArtifactLoaderError(f"no loaded ensemble for logical meter: {feature.logical_meter}")
            predictions.append(_predict_meter_ensemble(ensemble, row))
        return tuple(predictions)


class PmaxReleaseArtifactLoader:
    """Lazy loader for an extracted ``import_pmax_v29_60min`` release root."""

    def __init__(self, release_root: Path | str, *, model_loader: ArtifactLoaderFn | None = None) -> None:
        self.release_root = Path(release_root)
        self._model_loader = model_loader
        self._loaded_model: PmaxReleaseEnsembleModel | None = None

    @property
    def loaded(self) -> bool:
        return self._loaded_model is not None

    def load(self) -> PmaxReleaseEnsembleModel:
        if self._loaded_model is not None:
            return self._loaded_model
        if not self.release_root.is_dir():
            raise PmaxArtifactUnavailableError(f"release_root does not exist or is not a directory: {self.release_root}")
        base = self._release_base()
        meters = {logical_meter: self._load_meter(base, logical_meter) for logical_meter in PMAX_FORECAST_LOGICAL_METER_SOURCES}
        self._loaded_model = PmaxReleaseEnsembleModel(meters)
        return self._loaded_model

    def _release_base(self) -> Path:
        candidates = (
            self.release_root / "import_pmax_v29_60min" / "input_24h" / "predict_60min",
            self.release_root / "artifacts" / "import_pmax_v29_60min" / "input_24h" / "predict_60min",
            self.release_root
            / PMAX_FORECAST_PRODUCTION_RELEASE
            / "artifacts"
            / "import_pmax_v29_60min"
            / "input_24h"
            / "predict_60min",
        )
        for candidate in candidates:
            if candidate.is_dir():
                return candidate
        return candidates[0]

    def _load_meter(self, base: Path, logical_meter: str) -> PmaxMeterEnsemble:
        meter_root = _resolve_meter_root(base, logical_meter)
        manifest_path = meter_root / PMAX_FORECAST_MODEL_VERSION / "manifest.json"
        weights_path = meter_root / PMAX_FORECAST_MODEL_VERSION / "ensemble_weights.csv"
        model_root = meter_root / "_candidate_models"
        if not manifest_path.is_file() or not weights_path.is_file() or not model_root.is_dir():
            raise PmaxArtifactUnavailableError(f"incomplete P-Max meter artifact directory: {meter_root}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        _validate_manifest(manifest, logical_meter=logical_meter)
        weights = _read_weights(weights_path)
        loader = self._model_loader or _joblib_load
        model_paths = {version: model_root / f"{version}.joblib" for version in PMAX_FORECAST_CANDIDATE_VERSIONS}
        missing_paths = tuple(path for path in model_paths.values() if not path.is_file())
        if missing_paths:
            missing = ",".join(path.name for path in missing_paths)
            raise PmaxArtifactUnavailableError(f"missing P-Max candidate model files for {logical_meter}: {missing}")
        models = {version: loader(model_paths[version]) for version in PMAX_FORECAST_CANDIDATE_VERSIONS}
        return PmaxMeterEnsemble(logical_meter=logical_meter, manifest=manifest, weights=weights, models=models)


def _resolve_meter_root(base: Path, logical_meter: str) -> Path:
    candidates = (base / logical_meter, base / _artifact_meter_dir(logical_meter))
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def _artifact_meter_dir(logical_meter: str) -> str:
    return logical_meter.lower().replace(".", "_")


def descriptor_from_boundary(boundary: PmaxForecastArtifactBoundary, *, artifact_path: str | Path | None = None) -> PmaxArtifactDescriptor:
    """Build a loader descriptor from the contract boundary without loading."""

    return PmaxArtifactDescriptor(
        adapter_name=boundary.adapter_name,
        model_version=boundary.model_version or PMAX_FORECAST_MODEL_VERSION,
        artifact_uri=boundary.artifact_uri,
        artifact_path=Path(artifact_path) if artifact_path is not None else None,
        drive_artifact_verified=boundary.drive_artifact_verified,
        external_io_enabled=boundary.external_io_enabled,
    )


def _validate_manifest(manifest: Mapping[str, Any], *, logical_meter: str) -> None:
    expected = {
        "table": PMAX_FORECAST_INPUT_TABLE,
        "logical_meter": logical_meter,
        "output_range": "60min",
        "horizon_steps": len(PMAX_FORECAST_HORIZON_MINUTES),
        "step_minutes": 15,
        "input_hours": 24,
        "method": PMAX_FORECAST_MODEL_VERSION,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise PmaxArtifactIntegrityError(f"manifest mismatch for {logical_meter}.{key}: expected {value!r}, got {manifest.get(key)!r}")
    if tuple(manifest.get("feature_columns", ())) != PMAX_FORECAST_FEATURE_COLUMNS:
        raise PmaxArtifactIntegrityError(f"manifest feature_columns mismatch for {logical_meter}")
    if tuple(manifest.get("candidate_versions", ())) != PMAX_FORECAST_CANDIDATE_VERSIONS:
        raise PmaxArtifactIntegrityError(f"manifest candidate_versions mismatch for {logical_meter}")


def _read_weights(path: Path) -> Mapping[str, float]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = tuple(csv.DictReader(handle))
    try:
        versions = tuple(row["candidate_version"] for row in rows)
    except KeyError as exc:
        raise PmaxArtifactIntegrityError(f"ensemble weights missing required column: {exc}") from exc
    if versions != PMAX_FORECAST_CANDIDATE_VERSIONS:
        raise PmaxArtifactIntegrityError(f"ensemble weight versions mismatch: {versions}")
    try:
        weights = {row["candidate_version"]: float(row["weight"]) for row in rows}
    except (KeyError, ValueError) as exc:
        raise PmaxArtifactIntegrityError(f"invalid ensemble weights file: {path}") from exc
    if any(weight < 0 for weight in weights.values()) or abs(sum(weights.values()) - 1.0) > 1e-9:
        raise PmaxArtifactIntegrityError(f"invalid ensemble weights: {weights}")
    return weights


def _predict_meter_ensemble(ensemble: PmaxMeterEnsemble, row: Sequence[float]) -> tuple[float, ...]:
    totals = [0.0 for _ in PMAX_FORECAST_HORIZON_MINUTES]
    for version in PMAX_FORECAST_CANDIDATE_VERSIONS:
        model = ensemble.models[version]
        prediction = _predict_candidate_model(model, row)
        weight = ensemble.weights[version]
        for index, value in enumerate(prediction):
            totals[index] += weight * value
    return tuple(totals)


def _predict_candidate_model(model: Any, row: Sequence[float]) -> tuple[float, ...]:
    predict = getattr(model, "predict", None)
    if callable(predict):
        return _coerce_four_horizons(predict([list(row)]))
    if isinstance(model, Sequence) and not isinstance(model, str | bytes | bytearray):
        horizon_models = tuple(model)
        if len(horizon_models) != len(PMAX_FORECAST_HORIZON_MINUTES):
            raise PmaxArtifactLoaderError("candidate model list must contain four horizon predictors")
        values: list[float] = []
        for horizon_model in horizon_models:
            horizon_predict = getattr(horizon_model, "predict", None)
            if not callable(horizon_predict):
                raise PmaxArtifactLoaderError("candidate model list items must provide predict()")
            values.append(_coerce_single_horizon(horizon_predict([list(row)])))
        return tuple(values)
    raise PmaxArtifactLoaderError("candidate model must provide predict() or be a four-model horizon list")


def _coerce_four_horizons(value: Any) -> tuple[float, ...]:
    normalized = value.tolist() if hasattr(value, "tolist") else value
    if isinstance(normalized, Sequence) and not isinstance(normalized, str | bytes | bytearray):
        values = tuple(normalized)
        if len(values) == 1:
            nested = values[0].tolist() if hasattr(values[0], "tolist") else values[0]
            if isinstance(nested, Sequence) and not isinstance(nested, str | bytes | bytearray):
                values = tuple(nested)
        if len(values) == len(PMAX_FORECAST_HORIZON_MINUTES):
            try:
                return tuple(float(item) for item in values)
            except (TypeError, ValueError) as exc:
                raise PmaxArtifactLoaderError("candidate model prediction values must be numeric") from exc
    raise PmaxArtifactLoaderError("candidate model must return four horizon predictions")


def _coerce_single_horizon(value: Any) -> float:
    normalized = value.tolist() if hasattr(value, "tolist") else value
    if isinstance(normalized, Sequence) and not isinstance(normalized, str | bytes | bytearray):
        values = tuple(normalized)
        if len(values) != 1:
            raise PmaxArtifactLoaderError("horizon model must return one prediction value")
        normalized = values[0].tolist() if hasattr(values[0], "tolist") else values[0]
        if isinstance(normalized, Sequence) and not isinstance(normalized, str | bytes | bytearray):
            nested = tuple(normalized)
            if len(nested) != 1:
                raise PmaxArtifactLoaderError("horizon model must return one prediction value")
            normalized = nested[0]
    try:
        return float(normalized)
    except (TypeError, ValueError) as exc:
        raise PmaxArtifactLoaderError("horizon model prediction value must be numeric") from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ensure_descriptor(value: PmaxArtifactDescriptor | Mapping[str, Any]) -> PmaxArtifactDescriptor:
    if isinstance(value, PmaxArtifactDescriptor):
        return value
    artifact_path = value.get("artifact_path")
    return PmaxArtifactDescriptor(
        adapter_name=str(value.get("adapter_name", PMAX_FORECAST_ARTIFACT_ADAPTER_STUB)),
        release_name=value.get("release_name", PMAX_FORECAST_PRODUCTION_RELEASE),
        model_version=str(value.get("model_version", PMAX_FORECAST_MODEL_VERSION)),
        artifact_uri=value.get("artifact_uri"),
        artifact_path=Path(artifact_path) if artifact_path not in (None, "") else None,
        expected_sha256=value.get("expected_sha256", PMAX_FORECAST_PRODUCTION_RELEASE_SHA256),
        drive_artifact_verified=bool(value.get("drive_artifact_verified", False)),
        external_io_enabled=bool(value.get("external_io_enabled", False)),
    )


def _joblib_load(path: Path) -> Any:
    try:
        import joblib  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise PmaxArtifactUnavailableError("joblib is not installed; provide an injected loader for tests/local dry runs") from exc
    return joblib.load(path)


__all__ = [
    "PmaxArtifactDescriptor",
    "PmaxArtifactIntegrityError",
    "PmaxArtifactLoader",
    "PmaxArtifactLoaderError",
    "PmaxArtifactUnavailableError",
    "PmaxMeterEnsemble",
    "PmaxReleaseArtifactLoader",
    "PmaxReleaseEnsembleModel",
    "descriptor_from_boundary",
    "sha256_file",
]
