from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Header, HTTPException

from api.routers.model_auth import require_model_ops_token, validate_run_id
from api.routers.model_paths import CANDIDATE_DIR, PROJECT_ROOT


router = APIRouter()
DEFAULT_VALIDATE_TIMEOUT_SECONDS = 300
DEFAULT_PROMOTE_TIMEOUT_SECONDS = 1800


def _read_positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=f"{name} must be an integer") from exc
    if value <= 0:
        raise HTTPException(status_code=500, detail=f"{name} must be positive")
    return value


def _candidate_dir(run_id: str) -> Path:
    return CANDIDATE_DIR / run_id


def _read_marker(run_id: str) -> dict | None:
    marker_path = _candidate_dir(run_id) / "validated.marker"
    if not marker_path.exists():
        return None
    try:
        return json.loads(marker_path.read_text())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"validated.marker parse failed: {exc}") from exc


def _summary_preview(run_id: str, horizon: int) -> dict | None:
    path = _candidate_dir(run_id) / f"train_summary_{horizon}h.csv"
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path)
        return {
            "rows": int(len(df)),
            "unique_meters": int(df["meter_urn"].nunique()) if "meter_urn" in df.columns else None,
            "test_mae_nan": int(df["test_mae"].isna().sum()) if "test_mae" in df.columns else None,
            "beats_persistence": int(df["beats_persistence"].sum()) if "beats_persistence" in df.columns else None,
        }
    except Exception as exc:
        return {"error": str(exc)}


def _run_script(args: list[str], timeout_seconds: int) -> dict:
    python_cmd = os.getenv("MODEL_SCRIPT_PYTHON")
    executable = shlex.split(python_cmd) if python_cmd else [sys.executable]
    proc = subprocess.run(
        [*executable, *args],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
    )
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


@router.get("/{run_id}")
def get_model_run(
    run_id: str,
    horizon: int = 3,
    authorization: str | None = Header(default=None),
) -> dict:
    require_model_ops_token(authorization)
    validate_run_id(run_id)
    if horizon not in (1, 3):
        raise HTTPException(status_code=400, detail="horizon must be 1 or 3")

    cand = _candidate_dir(run_id)
    horizon_dir = cand / f"{horizon}h"
    return {
        "run_id": run_id,
        "horizon": horizon,
        "candidate_exists": cand.exists(),
        "horizon_dir_exists": horizon_dir.exists(),
        "meter_dir_count": len([p for p in horizon_dir.iterdir() if p.is_dir()]) if horizon_dir.exists() else 0,
        "summary": _summary_preview(run_id, horizon),
        "marker": _read_marker(run_id),
    }


@router.post("/{run_id}/validate")
def validate_model_run(
    run_id: str,
    horizon: int = 3,
    authorization: str | None = Header(default=None),
) -> dict:
    require_model_ops_token(authorization)
    validate_run_id(run_id)
    if horizon not in (1, 3):
        raise HTTPException(status_code=400, detail="horizon must be 1 or 3")
    if not _candidate_dir(run_id).exists():
        raise HTTPException(status_code=404, detail=f"candidate run not found: {run_id}")

    result = _run_script(
        ["scripts/validate_candidate.py", "--run", run_id, "--horizon", str(horizon)],
        timeout_seconds=_read_positive_int_env("MODEL_VALIDATE_TIMEOUT_SECONDS", DEFAULT_VALIDATE_TIMEOUT_SECONDS),
    )
    marker = _read_marker(run_id)

    if result["returncode"] == 1:
        raise HTTPException(status_code=400, detail={**result, "marker": marker})
    if result["returncode"] not in (0, 2):
        raise HTTPException(status_code=500, detail={**result, "marker": marker})

    status = "pass" if result["returncode"] == 0 else "warn"
    return {
        "status": status,
        "run_id": run_id,
        "horizon": horizon,
        "marker": marker,
        "script": result,
    }


@router.post("/{run_id}/promote")
def promote_model_run(
    run_id: str,
    horizon: int = 3,
    confirm: bool = False,
    allow_warn: bool = False,
    authorization: str | None = Header(default=None),
) -> dict:
    require_model_ops_token(authorization)
    validate_run_id(run_id)
    if horizon not in (1, 3):
        raise HTTPException(status_code=400, detail="horizon must be 1 or 3")
    if not confirm:
        raise HTTPException(status_code=400, detail="confirm=true is required for promotion")

    marker = _read_marker(run_id)
    if marker is None:
        raise HTTPException(status_code=400, detail="validated.marker not found. Validate first.")
    if marker.get("horizon") != horizon:
        raise HTTPException(status_code=400, detail="marker horizon does not match request")
    if marker.get("result") == "warn" and not allow_warn:
        raise HTTPException(status_code=400, detail="marker result is warn. Set allow_warn=true to promote.")
    if marker.get("result") not in ("pass", "warn"):
        raise HTTPException(status_code=400, detail=f"invalid marker result: {marker.get('result')}")

    result = _run_script(
        ["scripts/promote_candidate.py", "--run", run_id, "--horizon", str(horizon), "--yes"],
        timeout_seconds=_read_positive_int_env("MODEL_PROMOTE_TIMEOUT_SECONDS", DEFAULT_PROMOTE_TIMEOUT_SECONDS),
    )
    if result["returncode"] != 0:
        raise HTTPException(status_code=500, detail=result)

    return {
        "status": "promoted",
        "run_id": run_id,
        "horizon": horizon,
        "marker": marker,
        "script": result,
    }
