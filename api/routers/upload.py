from __future__ import annotations

import io
import logging
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException, UploadFile


router = APIRouter()
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "outputs"
MAX_FILE_SIZE = 100 * 1024 * 1024


@router.post("/csv")
async def upload_csv(file: UploadFile) -> dict:
    filename = file.filename or ""
    if not filename.lower().endswith(".csv"):
        logger.warning("CSV 업로드 실패 - 확장자 오류: %s", filename)
        raise HTTPException(status_code=400, detail="Only CSV files are allowed")

    contents = await file.read()
    size_bytes = len(contents)
    logger.info("CSV 업로드 시작 - filename=%s size_bytes=%s", filename, size_bytes)

    if size_bytes > MAX_FILE_SIZE:
        logger.warning("CSV 업로드 실패 - 크기 초과: %s (%s bytes)", filename, size_bytes)
        raise HTTPException(status_code=400, detail="File size exceeds 100MB limit")

    try:
        df = pd.read_csv(io.BytesIO(contents))
    except Exception as exc:
        logger.warning("CSV 업로드 실패 - 파싱 오류: %s", exc)
        raise HTTPException(status_code=400, detail=f"Invalid CSV format: {exc}") from exc

    saved_name = f"uploaded_{Path(filename).name}"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    saved_path = OUTPUT_DIR / saved_name
    saved_path.write_bytes(contents)

    logger.info(
        "CSV 저장 완료 - path=%s rows=%s columns=%s",
        saved_path,
        len(df),
        len(df.columns),
    )

    return {
        "filename": saved_name,
        "rows": int(len(df)),
        "columns": df.columns.tolist(),
        "size_bytes": size_bytes,
        "saved_path": f"outputs/{saved_name}",
    }
