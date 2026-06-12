from __future__ import annotations

import logging
import os
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile

from api.routers.model_auth import require_model_ops_token, validate_run_id
from api.routers.model_paths import CANDIDATE_DIR, INCOMING_DIR, PROJECT_ROOT


router = APIRouter()
logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024
DEFAULT_MAX_UPLOAD_MB = 2048
DEFAULT_PROBE_MAX_MB = 1


def _safe_extract_tar(archive_path: Path, destination: Path) -> None:
    with tarfile.open(archive_path) as tar:
        dest = destination.resolve()
        for member in tar.getmembers():
            target = (destination / member.name).resolve()
            if target != dest and dest not in target.parents:
                raise HTTPException(status_code=400, detail=f"Unsafe tar member: {member.name}")
            if member.issym() or member.islnk():
                raise HTTPException(status_code=400, detail=f"Archive links are not allowed: {member.name}")
            if member.isdev() or member.isfifo():
                raise HTTPException(status_code=400, detail=f"Archive special files are not allowed: {member.name}")
            if not (member.isdir() or member.isfile()):
                raise HTTPException(status_code=400, detail=f"Unsupported tar member: {member.name}")

        for member in tar.getmembers():
            target = (destination / member.name).resolve()
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            source = tar.extractfile(member)
            if source is None:
                raise HTTPException(status_code=400, detail=f"Cannot read tar member: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("wb") as out:
                shutil.copyfileobj(source, out)


def _safe_extract_zip(archive_path: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive_path) as zf:
        dest = destination.resolve()
        for info in zf.infolist():
            target = (destination / info.filename).resolve()
            if target != dest and dest not in target.parents:
                raise HTTPException(status_code=400, detail=f"Unsafe zip member: {info.filename}")
            file_type = (info.external_attr >> 16) & 0o170000
            if file_type == 0o120000:
                raise HTTPException(status_code=400, detail=f"Archive symlinks are not allowed: {info.filename}")
            if file_type not in (0, 0o040000, 0o100000):
                raise HTTPException(status_code=400, detail=f"Unsupported zip member: {info.filename}")

        for info in zf.infolist():
            target = (destination / info.filename).resolve()
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as source, target.open("wb") as out:
                shutil.copyfileobj(source, out)


def _extract_archive(archive_path: Path, destination: Path) -> None:
    suffixes = "".join(archive_path.suffixes).lower()
    if suffixes.endswith(".tar.gz") or suffixes.endswith(".tgz") or suffixes.endswith(".tar"):
        _safe_extract_tar(archive_path, destination)
    elif suffixes.endswith(".zip"):
        _safe_extract_zip(archive_path, destination)
    else:
        raise HTTPException(status_code=400, detail="Only .tar, .tar.gz, .tgz, or .zip archives are supported")


def _find_candidate_root(extract_dir: Path, run_id: str, horizon: int) -> Path:
    candidates = [
        extract_dir / run_id,
        extract_dir / "candidate" / run_id,
        extract_dir,
    ]
    for candidate in candidates:
        if (candidate / f"{horizon}h").is_dir() and (candidate / f"train_summary_{horizon}h.csv").is_file():
            return candidate

    raise HTTPException(
        status_code=400,
        detail=f"Archive does not contain {horizon}h/ and train_summary_{horizon}h.csv for run_id={run_id}",
    )


async def _save_upload(file: UploadFile, destination: Path) -> int:
    max_mb = _read_positive_int_env("MAX_ARTIFACT_UPLOAD_MB", DEFAULT_MAX_UPLOAD_MB)
    max_bytes = max_mb * 1024 * 1024
    total = 0
    with destination.open("wb") as out:
        while True:
            chunk = await file.read(CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(status_code=413, detail=f"Artifact exceeds {max_mb}MB limit")
            out.write(chunk)
    return total


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


async def _save_probe_upload(file: UploadFile, destination: Path) -> int:
    max_mb = _read_positive_int_env("PROBE_MAX_UPLOAD_MB", DEFAULT_PROBE_MAX_MB)
    max_bytes = max_mb * 1024 * 1024
    total = 0
    with destination.open("wb") as out:
        while True:
            chunk = await file.read(CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(status_code=413, detail=f"Probe file exceeds {max_mb}MB limit")
            out.write(chunk)
    return total


@router.post("/upload")
async def upload_model_artifact(
    run_id: str = Form(...),
    horizon: int = Form(...),
    file: UploadFile = File(...),
    overwrite: bool = Form(False),
    authorization: str | None = Header(default=None),
) -> dict:
    """Upload a RunPod-produced candidate artifact archive.

    The archive must contain one of these layouts:
      - {run_id}/{horizon}h/... and {run_id}/train_summary_{horizon}h.csv
      - candidate/{run_id}/{horizon}h/... and candidate/{run_id}/train_summary_{horizon}h.csv
      - {horizon}h/... and train_summary_{horizon}h.csv at archive root
    """
    require_model_ops_token(authorization)
    validate_run_id(run_id)
    if horizon not in (1, 3):
        raise HTTPException(status_code=400, detail="horizon must be 1 or 3")
    if not file.filename:
        raise HTTPException(status_code=400, detail="archive filename is required")

    target_dir = CANDIDATE_DIR / run_id
    if target_dir.exists() and not overwrite:
        raise HTTPException(status_code=409, detail=f"candidate run already exists: {run_id}")

    INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)

    archive_path = INCOMING_DIR / f"{run_id}{''.join(Path(file.filename).suffixes)}"
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"artifact_{run_id}_", dir=str(INCOMING_DIR)))

    try:
        size_bytes = await _save_upload(file, archive_path)
        _extract_archive(archive_path, tmp_dir)
        source_root = _find_candidate_root(tmp_dir, run_id, horizon)

        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(source_root, target_dir)

        meter_count = len([p for p in (target_dir / f"{horizon}h").iterdir() if p.is_dir()])
        logger.info(
            "artifact upload complete run_id=%s horizon=%s size=%s meters=%s",
            run_id,
            horizon,
            size_bytes,
            meter_count,
        )
        return {
            "status": "uploaded",
            "run_id": run_id,
            "horizon": horizon,
            "size_bytes": size_bytes,
            "meter_count": meter_count,
            "candidate_path": str(target_dir.relative_to(PROJECT_ROOT)),
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if os.getenv("KEEP_UPLOADED_ARCHIVES", "0") != "1":
            archive_path.unlink(missing_ok=True)


@router.post("/probe")
async def probe_upload(
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
) -> dict:
    """Small-file upload probe for tunnel/API connectivity tests."""
    require_model_ops_token(authorization)
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename is required")

    INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename).name
    probe_path = INCOMING_DIR / f"probe_{safe_name}"
    try:
        size_bytes = await _save_probe_upload(file, probe_path)
        return {
            "status": "ok",
            "filename": safe_name,
            "size_bytes": size_bytes,
        }
    finally:
        probe_path.unlink(missing_ok=True)
