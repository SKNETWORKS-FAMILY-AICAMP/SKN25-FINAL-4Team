from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, NoReturn

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse

from cms.modeling import model_ops

router = APIRouter(prefix="/model-ops", tags=["model-ops"])

CHUNK_SIZE = 1024 * 1024
DEFAULT_MAX_UPLOAD_MB = 2048
DEFAULT_PROBE_MAX_MB = 1


def _raise_http(exc: model_ops.ModelOpsError) -> NoReturn:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _authorize(authorization: str | None) -> None:
    try:
        model_ops.require_model_ops_token(authorization)
    except model_ops.ModelOpsError as exc:
        _raise_http(exc)


def _positive_int_env(name: str, default: int) -> int:
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


async def _save_upload_from_request(request: Request, destination: Path, *, max_mb: int) -> tuple[int, str]:
    try:
        form = await request.form()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"multipart form parsing failed: {exc}") from exc
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(status_code=400, detail="multipart field 'file' is required")
    filename = Path(str(getattr(upload, "filename", "") or "artifact.bin")).name
    max_bytes = max_mb * 1024 * 1024
    total = 0
    with destination.open("wb") as output:
        while True:
            chunk = await upload.read(CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(status_code=413, detail=f"Artifact exceeds {max_mb}MB limit")
            output.write(chunk)
    return total, filename


@router.post("/{model_kind}/training/start")
async def start_training(model_kind: str, request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _authorize(authorization)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be a JSON object")
    try:
        return model_ops.start_training(model_kind, payload)
    except model_ops.ModelOpsError as exc:
        _raise_http(exc)


@router.get("/{model_kind}/training/latest")
def latest_training(model_kind: str, authorization: str | None = Header(default=None), refresh: bool = True) -> dict[str, Any]:
    _authorize(authorization)
    try:
        return model_ops.training_status(model_kind, latest=True, refresh=refresh)
    except model_ops.ModelOpsError as exc:
        _raise_http(exc)


@router.get("/{model_kind}/training/data/{run_id}")
def download_training_data(model_kind: str, run_id: str, authorization: str | None = Header(default=None)) -> FileResponse:
    _authorize(authorization)
    try:
        archive_path = model_ops.training_data_archive_path(model_kind, run_id)
        if not archive_path.is_file():
            raise HTTPException(status_code=404, detail=f"training data archive not found: {run_id}")
        return FileResponse(
            archive_path,
            media_type="application/gzip",
            filename=f"{run_id}.tar.gz",
        )
    except model_ops.ModelOpsError as exc:
        _raise_http(exc)

@router.get("/{model_kind}/training/{job_id}/status")
def training_status(model_kind: str, job_id: str, authorization: str | None = Header(default=None), refresh: bool = True) -> dict[str, Any]:
    _authorize(authorization)
    try:
        return model_ops.training_status(model_kind, job_id=job_id, refresh=refresh)
    except model_ops.ModelOpsError as exc:
        _raise_http(exc)


@router.post("/{model_kind}/artifacts/upload")
async def upload_artifact(
    model_kind: str,
    request: Request,
    run_id: str,
    horizon: int | None = None,
    overwrite: bool = False,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize(authorization)
    archive_path: Path | None = None
    try:
        model_ops.require_env_gate("CMS_MODEL_OPS_ENABLE_ARTIFACT_WRITES", "artifact upload writes")
        paths = model_ops.paths_for(model_kind)
        model_ops.validate_run_id(run_id)
        paths.incoming_root.mkdir(parents=True, exist_ok=True)
        archive_path = paths.incoming_root / f"{run_id}.upload"
        size_bytes, filename = await _save_upload_from_request(
            request,
            archive_path,
            max_mb=_positive_int_env("MAX_ARTIFACT_UPLOAD_MB", DEFAULT_MAX_UPLOAD_MB),
        )
        suffixes = "".join(Path(filename).suffixes)
        if suffixes:
            renamed = paths.incoming_root / f"{run_id}{suffixes}"
            archive_path.replace(renamed)
            archive_path = renamed
        result = model_ops.install_candidate_archive(
            model_kind,
            run_id,
            archive_path,
            horizon=horizon,
            overwrite=overwrite,
        )
        result["size_bytes"] = size_bytes
        result["filename"] = filename
        return result
    except model_ops.ModelOpsError as exc:
        _raise_http(exc)
    finally:
        if archive_path is not None and os.getenv("KEEP_UPLOADED_ARCHIVES", "0") != "1":
            archive_path.unlink(missing_ok=True)


@router.post("/{model_kind}/artifacts/probe")
async def probe_upload(model_kind: str, request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _authorize(authorization)
    probe_path: Path | None = None
    try:
        model_ops.require_env_gate("CMS_MODEL_OPS_ENABLE_ARTIFACT_WRITES", "artifact probe writes")
        paths = model_ops.paths_for(model_kind)
        probe_path = paths.incoming_root / "probe.upload"
        size_bytes, filename = await _save_upload_from_request(
            request,
            probe_path,
            max_mb=_positive_int_env("PROBE_MAX_UPLOAD_MB", DEFAULT_PROBE_MAX_MB),
        )
        return {"status": "ok", "model_kind": model_kind, "filename": filename, "size_bytes": size_bytes}
    except model_ops.ModelOpsError as exc:
        _raise_http(exc)
    finally:
        if probe_path is not None:
            probe_path.unlink(missing_ok=True)


@router.get("/{model_kind}/runs/{run_id}")
def get_model_run(model_kind: str, run_id: str, horizon: int | None = None, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _authorize(authorization)
    try:
        return model_ops.candidate_status(model_kind, run_id, horizon=horizon)
    except model_ops.ModelOpsError as exc:
        _raise_http(exc)


@router.post("/{model_kind}/runs/{run_id}/validate")
def validate_model_run(
    model_kind: str,
    run_id: str,
    horizon: int | None = None,
    write_result: bool = False,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize(authorization)
    try:
        return model_ops.validate_candidate(model_kind, run_id, horizon=horizon, write_result=write_result)
    except model_ops.ModelOpsError as exc:
        _raise_http(exc)


@router.post("/{model_kind}/runs/{run_id}/promote")
def promote_model_run(
    model_kind: str,
    run_id: str,
    horizon: int | None = None,
    confirm: bool = False,
    allow_warn: bool = False,
    approval_note: str | None = None,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize(authorization)
    try:
        return model_ops.promote_candidate(
            model_kind,
            run_id,
            horizon=horizon,
            confirm=confirm,
            allow_warn=allow_warn,
            approval_note=approval_note,
        )
    except model_ops.ModelOpsError as exc:
        _raise_http(exc)


@router.post("/{model_kind}/rollback")
async def rollback_model(model_kind: str, request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _authorize(authorization)
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="JSON body is required") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be a JSON object")
    try:
        return model_ops.rollback_deployment(
            model_kind,
            payload.get("archive_root", ""),
            horizon=payload.get("horizon"),
            confirm=bool(payload.get("confirm", False)),
            approval_note=payload.get("approval_note"),
        )
    except model_ops.ModelOpsError as exc:
        _raise_http(exc)


@router.delete("/{model_kind}/runs/{run_id}")
def delete_candidate(model_kind: str, run_id: str, confirm: bool = False, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _authorize(authorization)
    try:
        model_ops.require_env_gate("CMS_MODEL_OPS_ENABLE_ARTIFACT_WRITES", "candidate deletion writes")
        paths = model_ops.paths_for(model_kind)
        model_ops.validate_run_id(run_id)
        if not confirm:
            raise model_ops.ModelOpsError(400, "confirm=true is required for candidate deletion")
        candidate_root = paths.candidates_root / run_id
        existed = candidate_root.exists()
        if existed:
            shutil.rmtree(candidate_root)
        return {"status": "deleted", "model_kind": model_kind, "run_id": run_id, "existed": existed}
    except model_ops.ModelOpsError as exc:
        _raise_http(exc)
