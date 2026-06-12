from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import inference, operations, training


SUMMARY_FILENAME = "pmax_model_comparison_summary.csv"
VALIDATION_FILENAME = "validation.json"
DEPLOYMENT_METRICS_FILENAME = "deployment_metrics.json"
DEFAULT_RMSE_DEGRADATION_THRESHOLD = 0.05
SUMMARY_REQUIRED_COLUMNS = {
    "meter",
    "model_mae",
    "model_rmse",
    "persistence_mae",
    "persistence_rmse",
    "rmse_improvement_pct",
}


class CandidateValidationError(ValueError):
    pass


def _threshold_from_env() -> float:
    raw = os.getenv("IMPORT_PMAX_MAX_RMSE_DEGRADATION")
    if raw is None:
        return DEFAULT_RMSE_DEGRADATION_THRESHOLD
    try:
        value = float(raw)
    except ValueError as exc:
        raise CandidateValidationError(
            "IMPORT_PMAX_MAX_RMSE_DEGRADATION must be a number"
        ) from exc
    if value < 0:
        raise CandidateValidationError(
            "IMPORT_PMAX_MAX_RMSE_DEGRADATION must be non-negative"
        )
    return value


def _runtime_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    for meter in training.LOGICAL_METERS:
        model_dir, manifest_path, weights_path = inference.artifact_paths(root, meter)
        paths.extend([manifest_path, weights_path])
        paths.extend(
            model_dir / f"{version}.joblib"
            for version in inference.EXPECTED_CANDIDATES
        )
    return paths


def candidate_digest(candidate_root: Path) -> str:
    paths = [candidate_root / SUMMARY_FILENAME, *_runtime_paths(candidate_root)]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise CandidateValidationError(
            f"Cannot hash incomplete candidate artifacts: {missing}"
        )

    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: str(item.relative_to(candidate_root))):
        relative = path.relative_to(candidate_root).as_posix().encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def _load_summary(candidate_root: Path) -> pd.DataFrame:
    path = candidate_root / SUMMARY_FILENAME
    if not path.is_file():
        raise CandidateValidationError(f"Candidate summary not found: {path}")
    try:
        frame = pd.read_csv(path)
    except Exception as exc:
        raise CandidateValidationError(
            f"Candidate summary could not be read: {exc}"
        ) from exc
    missing_columns = SUMMARY_REQUIRED_COLUMNS - set(frame.columns)
    if missing_columns:
        raise CandidateValidationError(
            f"Candidate summary is missing columns: {sorted(missing_columns)}"
        )
    expected = set(training.LOGICAL_METERS)
    actual = set(frame["meter"].astype(str))
    if actual != expected or len(frame) != len(expected):
        raise CandidateValidationError(
            f"Candidate summary meters must be exactly {sorted(expected)}, got {sorted(actual)}"
        )
    numeric_columns = sorted(SUMMARY_REQUIRED_COLUMNS - {"meter"})
    if frame[numeric_columns].apply(pd.to_numeric, errors="coerce").isna().any().any():
        raise CandidateValidationError(
            "Candidate summary contains non-numeric or missing required metrics"
        )
    return frame.sort_values("meter").reset_index(drop=True)


def _load_active_metrics(deployed_root: Path) -> pd.DataFrame | None:
    path = deployed_root / DEPLOYMENT_METRICS_FILENAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload["meters"]
        frame = pd.DataFrame(rows)
    except Exception as exc:
        raise CandidateValidationError(
            f"Active deployment metrics could not be read: {exc}"
        ) from exc
    required = {"meter", "model_rmse", "model_mae"}
    if required - set(frame.columns):
        raise CandidateValidationError(
            "Active deployment metrics are missing required fields"
        )
    expected = set(training.LOGICAL_METERS)
    actual = set(frame["meter"].astype(str))
    if actual != expected or len(frame) != len(expected):
        raise CandidateValidationError(
            f"Active deployment metrics must contain exactly {sorted(expected)}"
        )
    numeric = frame[["model_rmse", "model_mae"]].apply(
        pd.to_numeric,
        errors="coerce",
    )
    if numeric.isna().any().any():
        raise CandidateValidationError(
            "Active deployment metrics contain invalid numeric values"
        )
    return frame


def validate_runtime_artifacts(root: Path) -> list[dict[str, Any]]:
    if not root.is_dir():
        raise CandidateValidationError(f"Artifact root not found: {root}")

    meters: list[dict[str, Any]] = []
    for logical_meter in training.LOGICAL_METERS:
        model_dir, manifest_path, weights_path = inference.artifact_paths(
            root, logical_meter
        )
        required = [manifest_path, weights_path]
        required.extend(
            model_dir / f"{version}.joblib"
            for version in inference.EXPECTED_CANDIDATES
        )
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise CandidateValidationError(
                f"Artifact set is incomplete for {logical_meter}: {missing}"
            )

        try:
            artifacts = inference.load_artifacts(root, logical_meter)
        except Exception as exc:
            raise CandidateValidationError(
                f"Artifact loading failed for {logical_meter}: {exc}"
            ) from exc
        meters.append(
            {
                "logical_meter": logical_meter,
                "feature_count": len(artifacts.manifest["feature_columns"]),
                "candidate_versions": artifacts.manifest["candidate_versions"],
                "weights": artifacts.weights,
            }
        )
    return meters


def validate_candidate(
    candidate_root: Path,
    deployed_root: Path,
    *,
    run_id: str | None = None,
    rmse_degradation_threshold: float | None = None,
    write_result: bool = True,
) -> dict[str, Any]:
    resolved_run_id = run_id or candidate_root.name
    try:
        operations.validate_run_id(resolved_run_id)
    except ValueError as exc:
        raise CandidateValidationError(str(exc)) from exc

    meters = validate_runtime_artifacts(candidate_root)
    summary = _load_summary(candidate_root)
    active = _load_active_metrics(deployed_root)
    threshold = (
        _threshold_from_env()
        if rmse_degradation_threshold is None
        else rmse_degradation_threshold
    )
    if threshold < 0:
        raise CandidateValidationError(
            "rmse_degradation_threshold must be non-negative"
        )

    comparisons: list[dict[str, Any]] = []
    degraded: list[dict[str, Any]] = []
    if active is not None:
        merged = summary.merge(
            active[["meter", "model_rmse", "model_mae"]],
            on="meter",
            suffixes=("_candidate", "_active"),
            how="left",
            validate="one_to_one",
        )
        for row in merged.to_dict(orient="records"):
            active_rmse = float(row["model_rmse_active"])
            candidate_rmse = float(row["model_rmse_candidate"])
            change = (
                (candidate_rmse - active_rmse) / active_rmse
                if active_rmse > 0
                else np.nan
            )
            comparison = {
                "meter": row["meter"],
                "active_rmse": active_rmse,
                "candidate_rmse": candidate_rmse,
                "rmse_change_pct": float(change * 100) if np.isfinite(change) else None,
            }
            comparisons.append(comparison)
            if np.isfinite(change) and change > threshold:
                degraded.append(comparison)

    result = "warn" if degraded else "pass"
    payload: dict[str, Any] = {
        "run_id": resolved_run_id,
        "result": result,
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_root": str(candidate_root),
        "deployed_root": str(deployed_root),
        "candidate_digest": candidate_digest(candidate_root),
        "logical_meter_count": len(meters),
        "rmse_degradation_threshold_pct": threshold * 100,
        "degraded_count": len(degraded),
        "degraded_meters": degraded,
        "active_comparison_available": active is not None,
        "comparisons": comparisons,
        "meters": meters,
        "summary": summary.to_dict(orient="records"),
    }
    if write_result:
        (candidate_root / VALIDATION_FILENAME).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return payload


def read_validation(candidate_root: Path) -> dict[str, Any]:
    path = candidate_root / VALIDATION_FILENAME
    if not path.is_file():
        raise CandidateValidationError(
            f"{VALIDATION_FILENAME} not found. Validate the candidate first."
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CandidateValidationError(
            f"{VALIDATION_FILENAME} could not be read: {exc}"
        ) from exc


def assert_validation_current(
    candidate_root: Path,
    *,
    allow_warn: bool = False,
) -> dict[str, Any]:
    result = read_validation(candidate_root)
    status = result.get("result")
    if status not in {"pass", "warn"}:
        raise CandidateValidationError(f"Invalid validation result: {status!r}")
    if status == "warn" and not allow_warn:
        raise CandidateValidationError(
            "Candidate validation result is warn; explicit allow_warn is required"
        )
    actual_digest = candidate_digest(candidate_root)
    if result.get("candidate_digest") != actual_digest:
        raise CandidateValidationError(
            "Candidate artifacts changed after validation; validate again"
        )
    return result


def deployment_metrics_payload(
    candidate_root: Path,
    validation_result: dict[str, Any],
) -> dict[str, Any]:
    summary = _load_summary(candidate_root)
    return {
        "run_id": validation_result["run_id"],
        "validated_at": validation_result["validated_at"],
        "candidate_digest": validation_result["candidate_digest"],
        "meters": summary.to_dict(orient="records"),
    }
