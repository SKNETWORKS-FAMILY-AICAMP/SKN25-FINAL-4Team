from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import anomaly, meters, predict, upload


logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
)

app = FastAPI(title="Energy Platform API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(anomaly.router, prefix="/anomaly")
app.include_router(predict.router, prefix="/predict")
app.include_router(upload.router, prefix="/upload")
app.include_router(meters.router, prefix="/meters")


@app.get("/")
def health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "message": "Energy Platform API is running",
    }
