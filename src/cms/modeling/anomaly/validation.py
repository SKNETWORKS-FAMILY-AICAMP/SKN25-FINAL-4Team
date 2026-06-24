from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .config import TRAINING_METER_SPECS


VALIDATION_FILENAME = "model_ops_validation.json"
EXPECTED_MODEL_URNS: tuple[str, ...] = tuple(sorted({spec.meter_urn for spec in TRAINING_METER_SPECS}))
SUMMARY_REQUIRED_COLUMNS = {"meter_urn", "test_mae"}
MAE_DEGRADATION_THRESHOLD = 0.05
BASE_REQUIRED_FILES = (
    "routing.json",
    "input_scaler.joblib",
    "target_scaler.joblib",
    "feature_columns.json",
    "hour_bias_corrections.csv",
    "ridge.joblib",
)


class CandidateValidationError(ValueError):
    pass


def _threshold_from_env() -> float:
    raw = os.getenv("ANOMALY_MAX_MAE_DEGRADATION")
    if raw is None:
        return MAE_DEGRADATION_THRESHOLD
    try:
        value = float(raw)
    except ValueError as exc:
        raise CandidateValidationError("ANOMALY_MAX_MAE_DEGRADATION must be a number") from exc
    if value < 0:
        raise CandidateValidationError("ANOMALY_MAX_MAE_DEGRADATION must be non-negative")
    return value


def _expected_model_urns() -> list[str]:
    return list(EXPECTED_MODEL_URNS)


def _summary_path(root: Path, horizon: int) -> Path:
    return root / f"train_summary_{horizon}h.csv"


def _horizon_dir(root: Path, horizon: int) -> Path:
    return root / f"{horizon}h"


def _routing_required_files(routing: dict[str, Any], horizon: int) -> list[str]:
    files: list[str] = []
    v57 = routing.get("v57", "v52")
    v63 = routing.get("v63", "v57")

    if v57 == "v53":
        files.append("catboost.cbm")
    if v63 == "v61":
        files.extend(f"lightgbm_t_plus_{step}.txt" for step in range(1, horizon + 1))

    lstm_versions: set[str] = set()
    top2_versions = routing.get("lstm_top2_versions", [])
    if isinstance(top2_versions, list):
        lstm_versions.update(str(version) for version in top2_versions if isinstance(version, str))
    if routing.get("v52_source") == "v3":
        lstm_versions.add("v3")
    for key in ("v12_step_versions", "v15_step_versions"):
        step_versions_list = routing.get(key, [])
        if not isinstance(step_versions_list, list):
            continue
        for step_versions in step_versions_list:
            if isinstance(step_versions, list):
                lstm_versions.update(str(version) for version in step_versions if isinstance(version, str))
    files.extend(f"lstm_{version}.pt" for version in sorted(lstm_versions))

    return files


def candidate_digest(candidate_root: Path) -> str:
    if not candidate_root.is_dir():
        raise CandidateValidationError(f"Candidate root not found: {candidate_root}")

    digest = hashlib.sha256()
    for path in sorted(
        (
            item for item in candidate_root.rglob("*")
            if item.is_file() and item.name != VALIDATION_FILENAME
        ),
        key=lambda item: item.relative_to(candidate_root).as_posix(),
    ):
        relative = path.relative_to(candidate_root).as_posix().encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def validate_runtime_artifacts(candidate_root: Path, horizon: int) -> list[dict[str, Any]]:
    if horizon not in (1, 3):
        raise CandidateValidationError("anomaly horizon must be 1 or 3")
    if not candidate_root.is_dir():
        raise CandidateValidationError(f"Candidate root not found: {candidate_root}")

    expected = _expected_model_urns()
    horizon_dir = _horizon_dir(candidate_root, horizon)
    if not horizon_dir.is_dir():
        raise CandidateValidationError(f"Candidate horizon directory not found: {horizon_dir}")

    existing = {path.name for path in horizon_dir.iterdir() if path.is_dir()}
    missing_dirs = [urn for urn in expected if urn not in existing]
    if missing_dirs:
        raise CandidateValidationError(f"Missing artifact directories: {missing_dirs}")

    meters: list[dict[str, Any]] = []
    missing_files: list[str] = []
    routing_errors: list[str] = []
    for urn in expected:
        meter_dir = horizon_dir / urn
        required = list(BASE_REQUIRED_FILES)
        routing_path = meter_dir / "routing.json"
        if routing_path.is_file():
            try:
                routing = json.loads(routing_path.read_text(encoding="utf-8"))
                if not isinstance(routing, dict):
                    raise ValueError("routing payload must be a JSON object")
                required.extend(_routing_required_files(routing, horizon))
            except Exception as exc:
                routing_errors.append(f"{urn}/routing.json: {exc}")
        for filename in sorted(set(required)):
            if not (meter_dir / filename).is_file():
                missing_files.append(f"{urn}/{filename}")
        meters.append(
            {
                "meter_urn": urn,
                "required_file_count": len(set(required)),
                "present": not any(item.startswith(f"{urn}/") for item in missing_files),
            }
        )

    if routing_errors:
        raise CandidateValidationError(f"Routing parse failed: {routing_errors}")
    if missing_files:
        raise CandidateValidationError(f"Missing required artifact files: {missing_files}")

    return meters


def _load_candidate_summary(candidate_root: Path, horizon: int) -> pd.DataFrame:
    path = _summary_path(candidate_root, horizon)
    if not path.is_file():
        raise CandidateValidationError(f"Candidate summary not found: {path}")
    try:
        frame = pd.read_csv(path)
    except Exception as exc:
        raise CandidateValidationError(f"Candidate summary could not be read: {exc}") from exc
    if frame.empty:
        raise CandidateValidationError("Candidate summary is empty")
    missing_columns = SUMMARY_REQUIRED_COLUMNS - set(frame.columns)
    if missing_columns:
        raise CandidateValidationError(f"Candidate summary is missing columns: {sorted(missing_columns)}")

    expected = set(EXPECTED_MODEL_URNS)
    meter_values = frame["meter_urn"].astype(str)
    actual = set(meter_values)
    duplicate_urns = sorted(meter_values[meter_values.duplicated()].unique().tolist())
    if duplicate_urns:
        raise CandidateValidationError(f"Candidate summary has duplicate meter_urn values: {duplicate_urns}")
    missing_urns = sorted(expected - actual)
    extra_urns = sorted(actual - expected)
    if missing_urns or extra_urns:
        raise CandidateValidationError(
            f"Candidate summary meter mismatch. missing={missing_urns}, extra={extra_urns}"
        )
    frame = frame.copy()
    frame["test_mae"] = pd.to_numeric(frame["test_mae"], errors="coerce")
    failed = sorted(frame.loc[frame["test_mae"].isna(), "meter_urn"].astype(str).tolist())
    if failed:
        raise CandidateValidationError(f"Candidate summary has failed meters with missing test_mae: {failed}")
    return frame.sort_values("meter_urn").reset_index(drop=True)


def _load_active_summary(deployed_root: Path, horizon: int) -> pd.DataFrame | None:
    path = _summary_path(deployed_root, horizon)
    if not path.is_file():
        return None
    try:
        frame = pd.read_csv(path)
    except Exception as exc:
        raise CandidateValidationError(f"Active summary could not be read: {exc}") from exc
    missing_columns = SUMMARY_REQUIRED_COLUMNS - set(frame.columns)
    if missing_columns:
        raise CandidateValidationError(f"Active summary is missing columns: {sorted(missing_columns)}")
    frame = frame.copy()
    frame["meter_urn"] = frame["meter_urn"].astype(str)
    frame["test_mae"] = pd.to_numeric(frame["test_mae"], errors="coerce")
    return frame[["meter_urn", "test_mae"]]


def validate_candidate(
    candidate_root: Path,
    deployed_root: Path,
    *,
    run_id: str | None = None,
    horizon: int,
    mae_degradation_threshold: float | None = None,
    write_result: bool = True,
) -> dict[str, Any]:
    resolved_run_id = run_id or candidate_root.name
    if horizon not in (1, 3):
        raise CandidateValidationError("anomaly horizon must be 1 or 3")

    meters = validate_runtime_artifacts(candidate_root, horizon)
    candidate_summary = _load_candidate_summary(candidate_root, horizon)
    active_summary = _load_active_summary(deployed_root, horizon)
    threshold = _threshold_from_env() if mae_degradation_threshold is None else mae_degradation_threshold
    if threshold < 0:
        raise CandidateValidationError("mae_degradation_threshold must be non-negative")

    comparisons: list[dict[str, Any]] = []
    degraded: list[dict[str, Any]] = []
    if active_summary is not None:
        merged = candidate_summary.merge(
            active_summary,
            on="meter_urn",
            suffixes=("_candidate", "_active"),
            how="left",
            validate="one_to_one",
        )
        for row in merged.to_dict(orient="records"):
            active_mae = row.get("test_mae_active")
            candidate_mae = row.get("test_mae_candidate")
            if pd.isna(active_mae) or pd.isna(candidate_mae):
                continue
            active_mae = float(active_mae)
            candidate_mae = float(candidate_mae)
            change = (candidate_mae - active_mae) / active_mae if active_mae > 0 else None
            comparison = {
                "meter_urn": row["meter_urn"],
                "active_mae": active_mae,
                "candidate_mae": candidate_mae,
                "mae_change_pct": change * 100 if change is not None else None,
            }
            comparisons.append(comparison)
            if change is not None and change > threshold:
                degraded.append(comparison)

    result = "warn" if degraded else "pass"
    payload: dict[str, Any] = {
        "model_kind": "anomaly",
        "run_id": resolved_run_id,
        "horizon": horizon,
        "result": result,
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_root": str(candidate_root),
        "deployed_root": str(deployed_root),
        "candidate_digest": candidate_digest(candidate_root),
        "expected_model_urn_count": len(_expected_model_urns()),
        "mae_degradation_threshold_pct": threshold * 100,
        "degraded_count": len(degraded),
        "degraded_meters": degraded,
        "active_comparison_available": active_summary is not None,
        "comparisons": comparisons,
        "meters": meters,
        "summary": candidate_summary.to_dict(orient="records"),
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
        raise CandidateValidationError(f"{VALIDATION_FILENAME} not found. Validate the candidate first.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CandidateValidationError(f"{VALIDATION_FILENAME} could not be read: {exc}") from exc


def assert_validation_current(candidate_root: Path, *, allow_warn: bool = False) -> dict[str, Any]:
    result = read_validation(candidate_root)
    status = result.get("result")
    if status not in {"pass", "warn"}:
        raise CandidateValidationError(f"Invalid validation result: {status!r}")
    if status == "warn" and not allow_warn:
        raise CandidateValidationError("Candidate validation result is warn; explicit allow_warn is required")
    actual_digest = candidate_digest(candidate_root)
    if result.get("candidate_digest") != actual_digest:
        raise CandidateValidationError("Candidate artifacts changed after validation; validate again")
    return result
