"""
FastAPI 진입점.
실행: uvicorn src.api.main:app --reload --port 8000
"""

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

from api.routers import chat, anomalies, report, forecast, notifications, control, simulator, cms, settings, users


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    from api.scheduler import start_scheduler, stop_scheduler
    start_scheduler()
    simulator.start_worker(asyncio.get_running_loop())
    yield
    simulator.stop_worker()
    stop_scheduler()


app = FastAPI(
    title="EMS AI Agent API",
    description="에너지 관리 시스템 대화형 분석 에이전트",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # React dev server 허용
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(anomalies.router)
app.include_router(report.router)
app.include_router(forecast.router)
app.include_router(notifications.router)
app.include_router(control.router)
app.include_router(simulator.router)
app.include_router(cms.router)
app.include_router(settings.router)
app.include_router(users.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
