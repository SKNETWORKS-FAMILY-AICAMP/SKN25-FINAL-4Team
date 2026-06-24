from __future__ import annotations

import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[4]
ARTIFACTS_ROOT = Path(
    os.getenv("IMPORT_PMAX_ARTIFACTS_DIR", str(PROJECT_ROOT / "artifacts" / "pmax"))
).resolve()
DEPLOYED_ROOT = Path(
    os.getenv(
        "IMPORT_PMAX_DEPLOYED_ROOT",
        str(ARTIFACTS_ROOT / "import_pmax_v29_60min"),
    )
).resolve()
CANDIDATES_ROOT = Path(
    os.getenv(
        "IMPORT_PMAX_CANDIDATES_ROOT",
        str(ARTIFACTS_ROOT / "import_pmax_candidates"),
    )
).resolve()
ARCHIVES_ROOT = Path(
    os.getenv(
        "IMPORT_PMAX_ARCHIVES_ROOT",
        str(ARTIFACTS_ROOT / "import_pmax_archives"),
    )
).resolve()
INCOMING_ROOT = Path(
    os.getenv(
        "IMPORT_PMAX_INCOMING_ROOT",
        str(ARTIFACTS_ROOT / "import_pmax_incoming"),
    )
).resolve()
TRAINING_JOBS_ROOT = Path(
    os.getenv(
        "IMPORT_PMAX_TRAINING_JOBS_ROOT",
        str(ARTIFACTS_ROOT / "import_pmax_training_jobs"),
    )
).resolve()

RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run_{stamp}_{secrets.token_hex(4)}"


def validate_run_id(run_id: str) -> str:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError(
            "run_id must use only letters, numbers, dot, underscore, or hyphen"
        )
    return run_id


def candidate_root(run_id: str) -> Path:
    return CANDIDATES_ROOT / validate_run_id(run_id)
