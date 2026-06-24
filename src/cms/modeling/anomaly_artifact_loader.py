"""Lazy anomaly detection artifact inventory boundaries.

The shared ``test6_residual`` v84 bundle contains PyTorch, LightGBM, CatBoost,
and joblib artifacts. This module validates the extracted layout without loading
those binary model dependencies at import time.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cms.contracts.anomaly_detection_1h import (
    ANOMALY_DETECTION_ALLOWED_FEATURE_SETS,
    ANOMALY_DETECTION_ARTIFACT_ADAPTER_STUB,
    ANOMALY_DETECTION_ARTIFACT_MODEL_URNS,
    ANOMALY_DETECTION_HORIZON_HOURS,
    ANOMALY_DETECTION_LEAD_STEPS,
    ANOMALY_DETECTION_MODEL_METERS,
    ANOMALY_DETECTION_MODEL_VERSION,
    ANOMALY_DETECTION_RELEASE,
    ANOMALY_DETECTION_RELEASE_SHA256,
    AnomalyDetectionArtifactBoundary,
    anomaly_meters_for_model_urn,
)

ANOMALY_REQUIRED_ARTIFACT_FILES = (
    "input_scaler.joblib",
    "target_scaler.joblib",
    "ridge.joblib",
    "catboost.cbm",
    "lightgbm_t_plus_1.txt",
    "lightgbm_t_plus_2.txt",
    "lightgbm_t_plus_3.txt",
    "lstm_v1.pt",
    "lstm_v2.pt",
    "lstm_v3.pt",
    "lstm_v4.pt",
    "lstm_v6.pt",
    "lstm_v7.pt",
    "hour_bias_corrections.csv",
    "feature_columns.json",
    "routing.json",
    "train_meta.json",
)
ANOMALY_THRESHOLD_FILE = "val_thresholds.csv"
ANOMALY_BIAS_CORRECTION_COLUMNS = (
    "forecast_step",
    "target_hour_utc",
    "median_residual_correction",
    "fallback_global_correction",
)
ANOMALY_THRESHOLD_COLUMNS = ("meter_urn", "hour", "p_lower", "p_upper")


class AnomalyArtifactLoaderError(RuntimeError):
    """Base error for anomaly artifact inventory failures."""


class AnomalyArtifactUnavailableError(AnomalyArtifactLoaderError):
    """Raised when an extracted anomaly artifact path is unavailable."""


class AnomalyArtifactIntegrityError(AnomalyArtifactLoaderError):
    """Raised when anomaly artifact metadata does not match the contract."""


@dataclass(frozen=True)
class AnomalyArtifactDescriptor:
    """Repo-local descriptor for the external anomaly warning artifact."""

    adapter_name: str = ANOMALY_DETECTION_ARTIFACT_ADAPTER_STUB
    release_name: str | None = ANOMALY_DETECTION_RELEASE
    model_version: str = ANOMALY_DETECTION_MODEL_VERSION
    artifact_uri: str | None = None
    artifact_path: Path | None = None
    expected_sha256: str | None = ANOMALY_DETECTION_RELEASE_SHA256
    drive_artifact_verified: bool = False
    external_io_enabled: bool = False

    @property
    def available(self) -> bool:
        return bool(self.model_version and (self.artifact_path is not None or (self.drive_artifact_verified and self.artifact_uri)) and not self.external_io_enabled)

    def as_boundary(self) -> AnomalyDetectionArtifactBoundary:
        return AnomalyDetectionArtifactBoundary(
            adapter_name=self.adapter_name,
            release_name=self.release_name,
            model_version=self.model_version,
            drive_artifact_verified=self.drive_artifact_verified,
            external_io_enabled=self.external_io_enabled,
            artifact_uri=self.artifact_uri or (self.artifact_path.as_posix() if self.artifact_path is not None else None),
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


@dataclass(frozen=True)
class AnomalyMeterArtifact:
    """Validated artifact metadata for one model meter."""

    meter_urn: str
    model_urn: str
    predict_meter_urns: tuple[str, ...]
    feature_columns: tuple[str, ...]
    routing: Mapping[str, Any]
    train_meta: Mapping[str, Any]
    files: tuple[str, ...]


@dataclass(frozen=True)
class AnomalyArtifactInventory:
    """Validated inventory of an extracted anomaly artifact bundle."""

    release_root: Path
    artifact_base: Path
    threshold_path: Path
    meters: Mapping[str, AnomalyMeterArtifact]

    @property
    def meter_count(self) -> int:
        return len(self.meters)

    @property
    def predict_meter_count(self) -> int:
        return len(ANOMALY_DETECTION_MODEL_METERS)


class AnomalyArtifactInventoryLoader:
    """Validate an extracted ``test6_residual`` artifact directory."""

    def __init__(self, release_root: Path | str) -> None:
        self.release_root = Path(release_root)

    def load(self) -> AnomalyArtifactInventory:
        if not self.release_root.is_dir():
            raise AnomalyArtifactUnavailableError(f"release_root does not exist or is not a directory: {self.release_root}")
        base = self._artifact_base()
        if not base.is_dir():
            raise AnomalyArtifactUnavailableError(f"anomaly 3h artifact directory is missing: {base}")
        threshold_path = self._threshold_path(base)
        _validate_threshold_file(threshold_path)
        meters = {model_urn: self._load_meter(base, model_urn) for model_urn in ANOMALY_DETECTION_ARTIFACT_MODEL_URNS}
        return AnomalyArtifactInventory(release_root=self.release_root, artifact_base=base, threshold_path=threshold_path, meters=meters)

    def _artifact_base(self) -> Path:
        candidates = (
            self.release_root / "artifacts" / "3h",
            self.release_root / "3h",
            self.release_root / "test6_residual" / "pipeline" / "artifacts" / "3h",
            self.release_root / "share_test6_residual_v84_20260609" / "test6_residual" / "pipeline" / "artifacts" / "3h",
        )
        for candidate in candidates:
            if candidate.is_dir():
                return candidate
        return candidates[0]

    def _threshold_path(self, base: Path) -> Path:
        candidates = (
            self.release_root / "artifacts" / "thresholds" / ANOMALY_THRESHOLD_FILE,
            self.release_root / "thresholds" / ANOMALY_THRESHOLD_FILE,
            base.parent / "thresholds" / ANOMALY_THRESHOLD_FILE,
            self.release_root / "test6_residual" / "pipeline" / "artifacts" / "thresholds" / ANOMALY_THRESHOLD_FILE,
            self.release_root / "share_test6_residual_v84_20260609" / "test6_residual" / "pipeline" / "artifacts" / "thresholds" / ANOMALY_THRESHOLD_FILE,
        )
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        raise AnomalyArtifactUnavailableError(f"anomaly threshold file is missing: {candidates[0]}")

    def _load_meter(self, base: Path, model_urn: str) -> AnomalyMeterArtifact:
        meter_root = base / model_urn
        if not meter_root.is_dir():
            raise AnomalyArtifactUnavailableError(f"missing anomaly meter artifact directory: {meter_root}")
        missing = tuple(file_name for file_name in ANOMALY_REQUIRED_ARTIFACT_FILES if not (meter_root / file_name).is_file())
        if missing:
            raise AnomalyArtifactUnavailableError(f"missing anomaly artifact files for {model_urn}: {','.join(missing)}")
        feature_columns = tuple(json.loads((meter_root / "feature_columns.json").read_text(encoding="utf-8")))
        if feature_columns not in ANOMALY_DETECTION_ALLOWED_FEATURE_SETS:
            raise AnomalyArtifactIntegrityError(f"unexpected feature_columns for {model_urn}: {feature_columns}")
        routing = _read_json_object(meter_root / "routing.json")
        train_meta = _read_json_object(meter_root / "train_meta.json")
        if train_meta.get("meter_urn") != model_urn:
            raise AnomalyArtifactIntegrityError(f"train_meta meter_urn mismatch for {model_urn}")
        if train_meta.get("horizon") != ANOMALY_DETECTION_HORIZON_HOURS:
            raise AnomalyArtifactIntegrityError(f"train_meta horizon mismatch for {model_urn}")
        if "anomaly_threshold" not in train_meta:
            raise AnomalyArtifactIntegrityError(f"train_meta anomaly_threshold missing for {model_urn}")
        _validate_bias_file(meter_root / "hour_bias_corrections.csv", model_urn)
        return AnomalyMeterArtifact(
            meter_urn=model_urn,
            model_urn=model_urn,
            predict_meter_urns=anomaly_meters_for_model_urn(model_urn),
            feature_columns=feature_columns,
            routing=routing,
            train_meta=train_meta,
            files=ANOMALY_REQUIRED_ARTIFACT_FILES,
        )


def _validate_bias_file(path: Path, model_urn: str) -> None:
    rows = _read_csv_rows(path)
    if not rows:
        raise AnomalyArtifactIntegrityError(f"empty hour_bias_corrections.csv for {model_urn}")
    missing_columns = tuple(column for column in ANOMALY_BIAS_CORRECTION_COLUMNS if column not in rows[0])
    if missing_columns:
        raise AnomalyArtifactIntegrityError(f"hour_bias_corrections.csv missing columns for {model_urn}: {','.join(missing_columns)}")
    by_step: dict[int, set[int]] = {step: set() for step in ANOMALY_DETECTION_LEAD_STEPS}
    for row in rows:
        try:
            step = int(row["forecast_step"])
            hour = int(row["target_hour_utc"])
            float(row["median_residual_correction"])
            float(row["fallback_global_correction"])
        except (TypeError, ValueError) as exc:
            raise AnomalyArtifactIntegrityError(f"invalid hour_bias_corrections.csv value for {model_urn}") from exc
        if step not in ANOMALY_DETECTION_LEAD_STEPS:
            raise AnomalyArtifactIntegrityError(f"unsupported forecast_step in hour_bias_corrections.csv for {model_urn}: {step}")
        if hour < 0 or hour > 23:
            raise AnomalyArtifactIntegrityError(f"invalid target_hour_utc in hour_bias_corrections.csv for {model_urn}: {hour}")
        by_step[step].add(hour)
    for step, hours in by_step.items():
        if hours != set(range(24)):
            raise AnomalyArtifactIntegrityError(f"hour_bias_corrections.csv incomplete hours for {model_urn} step {step}")


def _validate_threshold_file(path: Path) -> None:
    rows = _read_csv_rows(path)
    if not rows:
        raise AnomalyArtifactIntegrityError("empty anomaly threshold file")
    missing_columns = tuple(column for column in ANOMALY_THRESHOLD_COLUMNS if column not in rows[0])
    if missing_columns:
        raise AnomalyArtifactIntegrityError(f"anomaly threshold file missing columns: {','.join(missing_columns)}")
    by_meter: dict[str, set[int]] = {meter_urn: set() for meter_urn in ANOMALY_DETECTION_MODEL_METERS}
    for row in rows:
        meter_urn = str(row["meter_urn"])
        if meter_urn not in by_meter:
            raise AnomalyArtifactIntegrityError(f"threshold file contains unsupported meter_urn: {meter_urn}")
        try:
            hour = int(row["hour"])
            lower = float(row["p_lower"])
            upper = float(row["p_upper"])
        except (TypeError, ValueError) as exc:
            raise AnomalyArtifactIntegrityError(f"invalid threshold value for {meter_urn}") from exc
        if hour < 0 or hour > 23:
            raise AnomalyArtifactIntegrityError(f"invalid threshold hour for {meter_urn}: {hour}")
        if lower > upper:
            raise AnomalyArtifactIntegrityError(f"threshold lower > upper for {meter_urn} hour {hour}")
        by_meter[meter_urn].add(hour)
    incomplete = tuple(meter_urn for meter_urn, hours in by_meter.items() if hours != set(range(24)))
    if incomplete:
        raise AnomalyArtifactIntegrityError("threshold file incomplete meter/hour coverage: " + ",".join(incomplete[:5]))


def _read_csv_rows(path: Path) -> tuple[dict[str, str], ...]:
    if not path.is_file():
        raise AnomalyArtifactUnavailableError(f"missing anomaly csv artifact file: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        return tuple(dict(row) for row in csv.DictReader(handle))


def descriptor_from_boundary(boundary: AnomalyDetectionArtifactBoundary, *, artifact_path: str | Path | None = None) -> AnomalyArtifactDescriptor:
    """Build a loader descriptor from the contract boundary without loading binaries."""

    return AnomalyArtifactDescriptor(
        adapter_name=boundary.adapter_name,
        release_name=boundary.release_name,
        model_version=boundary.model_version or ANOMALY_DETECTION_MODEL_VERSION,
        artifact_uri=boundary.artifact_uri,
        artifact_path=Path(artifact_path) if artifact_path is not None else None,
        drive_artifact_verified=boundary.drive_artifact_verified,
        external_io_enabled=boundary.external_io_enabled,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json_object(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise AnomalyArtifactIntegrityError(f"expected JSON object: {path}")
    return value


__all__ = [
    "ANOMALY_BIAS_CORRECTION_COLUMNS",
    "ANOMALY_REQUIRED_ARTIFACT_FILES",
    "ANOMALY_THRESHOLD_COLUMNS",
    "ANOMALY_THRESHOLD_FILE",
    "AnomalyArtifactDescriptor",
    "AnomalyArtifactIntegrityError",
    "AnomalyArtifactInventory",
    "AnomalyArtifactInventoryLoader",
    "AnomalyArtifactLoaderError",
    "AnomalyArtifactUnavailableError",
    "AnomalyMeterArtifact",
    "descriptor_from_boundary",
    "sha256_file",
]
