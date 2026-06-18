from __future__ import annotations

import json
import os
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from api.routers.model_auth import require_model_ops_token, validate_run_id
from api.routers.model_paths import TRAINING_DATA_DIR, TRAINING_JOBS_DIR
from energy_v84.common.config import METER_SPECS_BY_URN
from energy_v84.export_training_data import export_training_data_archive


router = APIRouter()

RUNPOD_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
DEFAULT_RUNPOD_TIMEOUT_SECONDS = 60
VALID_GROUPS = {"electric", "thermal"}
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
    horizon: int = Field(default=3)
    run_id: str | None = None
    meters: list[str] | None = None
    groups: list[str] | None = None
    epochs: int | None = None
    batch_size: int | None = None
    seed: int | None = Field(default=None, ge=0)
    timeout_seconds: int | None = None
    overwrite_upload: bool = True
    overwrite_candidate: bool = False
    export_training_data: bool = True
    overwrite_training_data: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_run_id() -> str:
    suffix = secrets.token_hex(4)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run_{stamp}_{suffix}"


def _runpod_endpoint_id() -> str:
    endpoint_id = os.getenv("RUNPOD_ENDPOINT_ID", "").strip()
    if not endpoint_id:
        raise HTTPException(status_code=500, detail="RUNPOD_ENDPOINT_ID is not configured")
    return endpoint_id


def _runpod_api_key() -> str:
    api_key = os.getenv("RUNPOD_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="RUNPOD_API_KEY is not configured")
    return api_key


def _runpod_timeout() -> int:
    raw = os.getenv("RUNPOD_API_TIMEOUT_SECONDS")
    if raw is None:
        return DEFAULT_RUNPOD_TIMEOUT_SECONDS
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="RUNPOD_API_TIMEOUT_SECONDS must be an integer") from exc
    if parsed <= 0:
        raise HTTPException(status_code=500, detail="RUNPOD_API_TIMEOUT_SECONDS must be positive")
    return parsed


def _validate_job_id(job_id: str) -> None:
    if not RUNPOD_JOB_ID_RE.fullmatch(job_id):
        raise HTTPException(status_code=400, detail="invalid RunPod job_id")


def _job_path(job_id: str) -> Path:
    _validate_job_id(job_id)
    return TRAINING_JOBS_DIR / f"{job_id}.json"


def _write_job_record(record: dict[str, Any]) -> None:
    TRAINING_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    path = _job_path(record["job_id"])
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    tmp.replace(path)


def _read_job_record(job_id: str) -> dict[str, Any] | None:
    path = _job_path(job_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"training job record parse failed: {exc}") from exc


def _latest_job_record() -> dict[str, Any] | None:
    if not TRAINING_JOBS_DIR.exists():
        return None
    records: list[dict[str, Any]] = []
    for path in TRAINING_JOBS_DIR.glob("*.json"):
        try:
            records.append(json.loads(path.read_text()))
        except json.JSONDecodeError:
            continue
    if not records:
        return None
    return max(records, key=lambda r: r.get("created_at") or "")


def _call_runpod(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    endpoint_id: str | None = None,
) -> dict[str, Any]:
    endpoint_id = endpoint_id or _runpod_endpoint_id()
    url = f"https://api.runpod.ai/v2/{endpoint_id}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {_runpod_api_key()}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_runpod_timeout()) as resp:
            body = resp.read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise HTTPException(status_code=502, detail={"runpod_status": exc.code, "body": body[:2000]}) from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"RunPod API request failed: {exc}") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"RunPod API returned non-JSON response: {body[:1000]}") from exc


def _sanitize_runpod_response(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() in SENSITIVE_RESPONSE_KEYS:
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = _sanitize_runpod_response(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_runpod_response(item) for item in value]
    return value


def _sanitize_job_input_for_record(value: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(value)
    if "upload_url" in sanitized:
        sanitized["upload_url"] = "[REDACTED]"
    if "training_data_url" in sanitized:
        sanitized["training_data_url"] = "[REDACTED]"
    return sanitized


def _build_runpod_input(body: TrainingStartRequest) -> dict[str, Any]:
    if body.horizon not in (1, 3):
        raise HTTPException(status_code=400, detail="horizon must be 1 or 3")
    if body.groups:
        bad_groups = sorted(set(body.groups) - VALID_GROUPS)
        if bad_groups:
            raise HTTPException(status_code=400, detail=f"invalid groups: {bad_groups}")
    if body.meters:
        bad_meters = sorted(set(body.meters) - set(METER_SPECS_BY_URN))
        if bad_meters:
            raise HTTPException(status_code=400, detail=f"unknown meters: {bad_meters}")
    if body.run_id:
        validate_run_id(body.run_id)
        run_id = body.run_id
    else:
        run_id = _new_run_id()

    payload: dict[str, Any] = {
        "run_id": run_id,
        "horizon": body.horizon,
        "overwrite_upload": body.overwrite_upload,
        "overwrite_candidate": body.overwrite_candidate,
    }

    optional_values = {
        "meters": body.meters,
        "groups": body.groups,
        "epochs": body.epochs,
        "batch_size": body.batch_size,
        "seed": body.seed,
        "timeout_seconds": body.timeout_seconds,
    }
    for key, value in optional_values.items():
        if value is not None:
            payload[key] = value

    upload_url = (
        os.getenv("RUNPOD_ARTIFACT_UPLOAD_URL", "").strip()
        or os.getenv("MODEL_ARTIFACT_UPLOAD_URL", "").strip()
    )
    if upload_url:
        payload["upload_url"] = upload_url

    return payload


def _runpod_public_base_url() -> str:
    raw = os.getenv("RUNPOD_TRAINING_DATA_BASE_URL", "").strip()
    if raw:
        parsed = urllib.parse.urlparse(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(status_code=500, detail="RUNPOD_TRAINING_DATA_BASE_URL must be an absolute URL")
        return raw.rstrip("/")

    upload_url = (
        os.getenv("RUNPOD_ARTIFACT_UPLOAD_URL", "").strip()
        or os.getenv("MODEL_ARTIFACT_UPLOAD_URL", "").strip()
    )
    if not upload_url:
        raise HTTPException(status_code=500, detail="RUNPOD_TRAINING_DATA_BASE_URL or RUNPOD_ARTIFACT_UPLOAD_URL is required")
    parsed = urllib.parse.urlparse(upload_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=500, detail="artifact upload URL must be an absolute URL")
    return f"{parsed.scheme}://{parsed.netloc}"


def _next_action(record: dict[str, Any]) -> str:
    response = record.get("runpod_status_response")
    if isinstance(response, dict) and isinstance(response.get("output"), dict):
        output = response["output"]
        if output.get("status") == "uploaded" and output.get("upload"):
            return "validate"
        if output.get("status") == "failed":
            return "inspect_failure"
    status = record.get("status")
    if status in TERMINAL_RUNPOD_STATUSES:
        return "inspect_result"
    return "wait"


def _refresh_job_record(record: dict[str, Any]) -> dict[str, Any]:
    job_id = record["job_id"]
    response = _sanitize_runpod_response(_call_runpod("GET", f"/status/{job_id}"))
    record.update(
        {
            "status": response.get("status"),
            "updated_at": _now_iso(),
            "runpod_status_response": response,
        }
    )
    if isinstance(response.get("output"), dict):
        output = response["output"]
        record["run_id"] = record.get("run_id") or output.get("run_id")
        record["horizon"] = record.get("horizon") or output.get("horizon")
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
    endpoint_id = _runpod_endpoint_id()
    runpod_input = _build_runpod_input(body)
    training_data: dict[str, Any] | None = None
    if body.export_training_data:
        try:
            training_data = export_training_data_archive(
                TRAINING_DATA_DIR,
                runpod_input["run_id"],
                meters=body.meters,
                groups=body.groups,
                overwrite=body.overwrite_training_data,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"training data export failed: {type(exc).__name__}: {exc}") from exc
        runpod_input["training_data_url"] = f"{_runpod_public_base_url()}/training/data/{runpod_input['run_id']}"

    response = _call_runpod("POST", "/run", {"input": runpod_input}, endpoint_id=endpoint_id)
    sanitized_response = _sanitize_runpod_response(response)
    job_id = response.get("id")
    if not isinstance(job_id, str) or not job_id:
        raise HTTPException(status_code=502, detail={"message": "RunPod response does not include job id", "response": response})
    _validate_job_id(job_id)

    record = {
        "job_id": job_id,
        "run_id": runpod_input["run_id"],
        "horizon": runpod_input["horizon"],
        "status": response.get("status"),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "runpod_endpoint_id": endpoint_id,
        "request_input": _sanitize_job_input_for_record(runpod_input),
        "runpod_response": sanitized_response,
        "training_data": training_data,
        "next_action": "wait",
    }
    _write_job_record(record)

    return {
        "status": "submitted",
        "job_id": job_id,
        "run_id": runpod_input["run_id"],
        "horizon": runpod_input["horizon"],
        "runpod_status": response.get("status"),
        "runpod_response": sanitized_response,
        "training_data": training_data,
    }


@router.get("/data/{run_id}")
def download_training_data(
    run_id: str,
    authorization: str | None = Header(default=None),
) -> FileResponse:
    require_model_ops_token(authorization)
    validate_run_id(run_id)
    archive_path = TRAINING_DATA_DIR / f"{run_id}.tar.gz"
    if not archive_path.is_file():
        raise HTTPException(status_code=404, detail=f"training data archive not found: {run_id}")
    return FileResponse(
        archive_path,
        media_type="application/gzip",
        filename=f"{run_id}.tar.gz",
    )


@router.get("/latest")
def get_latest_training(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    require_model_ops_token(authorization)
    record = _latest_job_record()
    if record is None:
        raise HTTPException(status_code=404, detail="training job record not found")
    if record.get("job_id") and record.get("status") not in TERMINAL_RUNPOD_STATUSES:
        try:
            return _refresh_job_record(record)
        except HTTPException as exc:
            record["runpod_refresh_error"] = str(exc.detail)
            record["runpod_refresh_failed_at"] = _now_iso()
            return record
    return record


@router.get("/{job_id}/status")
def get_training_status(
    job_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    require_model_ops_token(authorization)
    _validate_job_id(job_id)
    record = _read_job_record(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="training job was not submitted from this server")
    return _refresh_job_record(record)
