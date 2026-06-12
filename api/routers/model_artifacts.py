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
from src.forecasting.import_pmax.validation import SUMMARY_FILENAME


router = APIRouter()
logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024
DEFAULT_MAX_UPLOAD_MB = 2048
DEFAULT_PROBE_MAX_MB = 1


def _safe_extract_tar(archive_path: Path, destination: Path) -> None:
    with tarfile.open(archive_path) as archive:
        dest = destination.resolve()
        for member in archive.getmembers():
            target = (destination / member.name).resolve()
            if target != dest and dest not in target.parents:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsafe tar member: {member.name}",
                )
            if member.issym() or member.islnk():
                raise HTTPException(
                    status_code=400,
                    detail=f"Archive links are not allowed: {member.name}",
                )
            if member.isdev() or member.isfifo():
                raise HTTPException(
                    status_code=400,
                    detail=f"Archive special files are not allowed: {member.name}",
                )
            if not (member.isdir() or member.isfile()):
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported tar member: {member.name}",
                )

        for member in archive.getmembers():
            target = (destination / member.name).resolve()
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            source = archive.extractfile(member)
            if source is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot read tar member: {member.name}",
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _safe_extract_zip(archive_path: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        dest = destination.resolve()
        for info in archive.infolist():
            target = (destination / info.filename).resolve()
            if target != dest and dest not in target.parents:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsafe zip member: {info.filename}",
                )
            file_type = (info.external_attr >> 16) & 0o170000
            if file_type == 0o120000:
                raise HTTPException(
                    status_code=400,
                    detail=f"Archive symlinks are not allowed: {info.filename}",
                )
            if file_type not in (0, 0o040000, 0o100000):
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported zip member: {info.filename}",
                )

        for info in archive.infolist():
            target = (destination / info.filename).resolve()
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _extract_archive(archive_path: Path, destination: Path) -> None:
    suffixes = "".join(archive_path.suffixes).lower()
    if suffixes.endswith((".tar.gz", ".tgz", ".tar")):
        _safe_extract_tar(archive_path, destination)
    elif suffixes.endswith(".zip"):
        _safe_extract_zip(archive_path, destination)
    else:
        raise HTTPException(
            status_code=400,
            detail="Only .tar, .tar.gz, .tgz, or .zip archives are supported",
        )


def _has_candidate_layout(path: Path) -> bool:
    runtime = path / "input_24h" / "predict_60min"
    return runtime.is_dir() and (path / SUMMARY_FILENAME).is_file()


def _find_candidate_root(extract_dir: Path, run_id: str) -> Path:
    candidates = [
        extract_dir / run_id,
        extract_dir / "candidate" / run_id,
        extract_dir,
    ]
    for candidate in candidates:
        if _has_candidate_layout(candidate):
            return candidate
    raise HTTPException(
        status_code=400,
        detail=(
            "Archive does not contain input_24h/predict_60min/ and "
            f"{SUMMARY_FILENAME} for run_id={run_id}"
        ),
    )


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


async def _save_upload(file: UploadFile, destination: Path, max_mb: int) -> int:
    max_bytes = max_mb * 1024 * 1024
    total = 0
    with destination.open("wb") as output:
        while chunk := await file.read(CHUNK_SIZE):
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"Artifact exceeds {max_mb}MB limit",
                )
            output.write(chunk)
    return total


@router.post("/upload")
async def upload_model_artifact(
    run_id: str = Form(...),
    file: UploadFile = File(...),
    overwrite: bool = Form(False),
    authorization: str | None = Header(default=None),
) -> dict:
    require_model_ops_token(authorization)
    validate_run_id(run_id)
    if not file.filename:
        raise HTTPException(status_code=400, detail="archive filename is required")

    target_dir = CANDIDATE_DIR / run_id
    if target_dir.exists() and not overwrite:
        raise HTTPException(
            status_code=409,
            detail=f"candidate run already exists: {run_id}",
        )

    INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)
    suffixes = "".join(Path(file.filename).suffixes)
    archive_path = INCOMING_DIR / f"{run_id}{suffixes}"
    extract_dir = Path(
        tempfile.mkdtemp(prefix=f"artifact_{run_id}_", dir=str(INCOMING_DIR))
    )
    staged_target = CANDIDATE_DIR / f".{run_id}.uploading"

    try:
        size_bytes = await _save_upload(
            file,
            archive_path,
            _read_positive_int_env("MAX_ARTIFACT_UPLOAD_MB", DEFAULT_MAX_UPLOAD_MB),
        )
        _extract_archive(archive_path, extract_dir)
        source_root = _find_candidate_root(extract_dir, run_id)
        if staged_target.exists():
            shutil.rmtree(staged_target)
        shutil.copytree(source_root, staged_target)
        if target_dir.exists():
            shutil.rmtree(target_dir)
        staged_target.rename(target_dir)

        meter_root = target_dir / "input_24h" / "predict_60min"
        meter_count = len([path for path in meter_root.iterdir() if path.is_dir()])
        logger.info(
            "P-Max candidate upload complete run_id=%s size=%s meters=%s",
            run_id,
            size_bytes,
            meter_count,
        )
        try:
            candidate_path = str(target_dir.relative_to(PROJECT_ROOT))
        except ValueError:
            candidate_path = str(target_dir)
        return {
            "status": "uploaded",
            "run_id": run_id,
            "size_bytes": size_bytes,
            "meter_count": meter_count,
            "candidate_path": candidate_path,
            "next_action": "validate",
        }
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)
        shutil.rmtree(staged_target, ignore_errors=True)
        if os.getenv("KEEP_UPLOADED_ARCHIVES", "0") != "1":
            archive_path.unlink(missing_ok=True)


@router.post("/probe")
async def probe_upload(
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
) -> dict:
    require_model_ops_token(authorization)
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename is required")
    INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    probe_path = INCOMING_DIR / f"probe_{Path(file.filename).name}"
    try:
        size_bytes = await _save_upload(
            file,
            probe_path,
            _read_positive_int_env("PROBE_MAX_UPLOAD_MB", DEFAULT_PROBE_MAX_MB),
        )
        return {"status": "ok", "filename": file.filename, "size_bytes": size_bytes}
    finally:
        probe_path.unlink(missing_ok=True)
