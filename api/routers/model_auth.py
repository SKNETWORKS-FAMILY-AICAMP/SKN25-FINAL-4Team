from __future__ import annotations

import os
import secrets

from dotenv import load_dotenv
from fastapi import HTTPException

from src.forecasting.import_pmax.operations import validate_run_id as validate_id


load_dotenv()


def require_model_ops_token(authorization: str | None) -> None:
    expected = os.getenv("ARTIFACT_UPLOAD_TOKEN", "")
    if not expected:
        raise HTTPException(
            status_code=500,
            detail="ARTIFACT_UPLOAD_TOKEN is not configured",
        )

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Bearer token required")
    if not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="Invalid token")


def validate_run_id(run_id: str) -> None:
    try:
        validate_id(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
