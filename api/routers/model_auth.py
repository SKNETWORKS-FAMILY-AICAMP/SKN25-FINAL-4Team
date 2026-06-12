from __future__ import annotations

import os
import re
import secrets

from dotenv import load_dotenv
from fastapi import HTTPException


load_dotenv()

RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


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
    if not RUN_ID_RE.fullmatch(run_id):
        raise HTTPException(
            status_code=400,
            detail="run_id must use only letters, numbers, dot, underscore, or hyphen",
        )
