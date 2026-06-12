from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS_DIR = Path(
    os.getenv(
        "MODEL_ARTIFACTS_DIR",
        str(PROJECT_ROOT / "artifacts"),
    )
).resolve()
CANDIDATE_DIR = ARTIFACTS_DIR / "candidate"
INCOMING_DIR = ARTIFACTS_DIR / "incoming_uploads"
TRAINING_JOBS_DIR = ARTIFACTS_DIR / "training_jobs"
