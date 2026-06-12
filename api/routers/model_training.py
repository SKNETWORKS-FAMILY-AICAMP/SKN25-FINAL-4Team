from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from api.routers.model_auth import require_model_ops_token, validate_run_id
from api.routers.model_paths import TRAINING_JOBS_DIR
from src.forecasting.import_pmax.operations import new_run_id
from src.forecasting.import_pmax.training import LOGICAL_METERS


router = APIRouter()

RUNPOD_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
DEFAULT_RUNPOD_TIMEOUT_SECONDS = 60
TERMINAL_RUNPOD_STATUSES = {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}
SENSITIVE_RESPONSE_KEYS = {
    "authorization",
    "token",
    "api_key",
    "upload_token",
    "upload_url",
    "artifact_upload_token",
    "runpod_api_key",
}


class TrainingStartRequest(BaseModel):
    run_id: str | None = None
    meters: list[str] | None = None
    seed: int | None = Field(default=None, ge=0)
    timeout_seconds: int | None = Field(default=None, ge=60)
    overwrite_upload: bool = True
    overwrite_candidate: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise HTTPException(status_code=500, detail=f"{name} is not configured")
    return value


def _runpod_timeout() -> int:
    raw = os.getenv("RUNPOD_API_TIMEOUT_SECONDS")
    if raw is None:
        return DEFAULT_RUNPOD_TIMEOUT_SECONDS
    try:
        value = int(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=500,
            detail="RUNPOD_API_TIMEOUT_SECONDS must be an integer",
        ) from exc
    if value <= 0:
        raise HTTPException(
            status_code=500,
            detail="RUNPOD_API_TIMEOUT_SECONDS must be positive",
        )
    return value


def _validate_job_id(job_id: str) -> None:
    if not RUNPOD_JOB_ID_RE.fullmatch(job_id):
        raise HTTPException(status_code=400, detail="invalid RunPod job_id")


def _job_path(job_id: str):
    _validate_job_id(job_id)
    return TRAINING_JOBS_DIR / f"{job_id}.json"


def _write_job_record(record: dict[str, Any]) -> None:
    TRAINING_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    path = _job_path(record["job_id"])
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_job_record(job_id: str) -> dict[str, Any] | None:
    path = _job_path(job_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"training job record parse failed: {exc}",
        ) from exc


def _latest_job_record() -> dict[str, Any] | None:
    if not TRAINING_JOBS_DIR.exists():
        return None
    records = []
    for path in TRAINING_JOBS_DIR.glob("*.json"):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return max(records, key=lambda item: item.get("created_at") or "") if records else None


def _call_runpod(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    endpoint_id: str | None = None,
) -> dict[str, Any]:
    endpoint = endpoint_id or _required_env("RUNPOD_ENDPOINT_ID")
    request = urllib.request.Request(
        f"https://api.runpod.ai/v2/{endpoint}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {_required_env('RUNPOD_API_KEY')}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_runpod_timeout()) as response:
            body = response.read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise HTTPException(
            status_code=502,
            detail={"runpod_status": exc.code, "body": body[:2000]},
        ) from exc
    except urllib.error.URLError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"RunPod API request failed: {exc}",
        ) from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"RunPod API returned non-JSON response: {body[:1000]}",
        ) from exc


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in SENSITIVE_RESPONSE_KEYS else _sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def _build_runpod_input(body: TrainingStartRequest) -> dict[str, Any]:
    run_id = body.run_id or new_run_id()
    validate_run_id(run_id)
    if body.meters:
        invalid = sorted(set(body.meters) - set(LOGICAL_METERS))
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"unknown logical meters: {invalid}",
            )

    payload: dict[str, Any] = {
        "run_id": run_id,
        "overwrite_upload": body.overwrite_upload,
        "overwrite_candidate": body.overwrite_candidate,
    }
    if body.meters is not None:
        payload["meters"] = body.meters
    if body.seed is not None:
        payload["seed"] = body.seed
    if body.timeout_seconds is not None:
        payload["timeout_seconds"] = body.timeout_seconds

    upload_url = (
        os.getenv("RUNPOD_ARTIFACT_UPLOAD_URL", "").strip()
        or os.getenv("MODEL_ARTIFACT_UPLOAD_URL", "").strip()
    )
    if upload_url:
        payload["upload_url"] = upload_url
    return payload


def _next_action(record: dict[str, Any]) -> str:
    response = record.get("runpod_status_response")
    if isinstance(response, dict) and isinstance(response.get("output"), dict):
        output = response["output"]
        if output.get("status") == "uploaded" and output.get("upload"):
            return "validate"
        if output.get("status") == "failed":
            return "inspect_failure"
    if record.get("status") in TERMINAL_RUNPOD_STATUSES:
        return "inspect_result"
    return "wait"


def _refresh_job_record(record: dict[str, Any]) -> dict[str, Any]:
    response = _sanitize(_call_runpod("GET", f"/status/{record['job_id']}"))
    record.update(
        {
            "status": response.get("status"),
            "updated_at": _now_iso(),
            "runpod_status_response": response,
        }
    )
    if isinstance(response.get("output"), dict):
        output = response["output"]
        record["upload"] = output.get("upload")
    record["next_action"] = _next_action(record)
    _write_job_record(record)
    return record


@router.post("/start")
def start_training(
    body: TrainingStartRequest | None = None,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    require_model_ops_token(authorization)
    body = body or TrainingStartRequest()
    endpoint_id = _required_env("RUNPOD_ENDPOINT_ID")
    runpod_input = _build_runpod_input(body)
    response = _call_runpod(
        "POST",
        "/run",
        {"input": runpod_input},
        endpoint_id=endpoint_id,
    )
    job_id = response.get("id")
    if not isinstance(job_id, str) or not job_id:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "RunPod response does not include job id",
                "response": _sanitize(response),
            },
        )
    _validate_job_id(job_id)
    record = {
        "job_id": job_id,
        "run_id": runpod_input["run_id"],
        "status": response.get("status"),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "runpod_endpoint_id": endpoint_id,
        "request_input": _sanitize(runpod_input),
        "runpod_response": _sanitize(response),
        "next_action": "wait",
    }
    _write_job_record(record)
    return {
        "status": "submitted",
        "job_id": job_id,
        "run_id": runpod_input["run_id"],
        "runpod_status": response.get("status"),
        "next_action": "wait",
    }


@router.get("/latest")
def get_latest_training(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    require_model_ops_token(authorization)
    record = _latest_job_record()
    if record is None:
        raise HTTPException(status_code=404, detail="training job record not found")
    if record.get("status") not in TERMINAL_RUNPOD_STATUSES:
        return _refresh_job_record(record)
    return record


@router.get("/{job_id}/status")
def get_training_status(
    job_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    require_model_ops_token(authorization)
    record = _read_job_record(job_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail="training job was not submitted from this server",
        )
    return _refresh_job_record(record)
