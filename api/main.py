from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import model_artifacts, model_runs, model_training


logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
)

app = FastAPI(title="Import P-Max Model Operations API")

cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", "*").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(model_artifacts.router, prefix="/model-artifacts")
app.include_router(model_runs.router, prefix="/model-runs")
app.include_router(model_training.router, prefix="/training")


@app.get("/")
def health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "import-pmax-model-operations",
    }
