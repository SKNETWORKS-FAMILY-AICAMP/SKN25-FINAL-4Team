import asyncio
import json
from typing import AsyncGenerator
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter(prefix="/notifications", tags=["notifications"])

class NotificationManager:
    def __init__(self):
        self.queues: list[asyncio.Queue] = []

    async def broadcast(self, message: dict):
        for q in self.queues:
            await q.put(message)

    async def subscribe(self) -> AsyncGenerator[str, None]:
        q = asyncio.Queue()
        self.queues.append(q)
        try:
            while True:
                msg = await q.get()
                yield f"data: {json.dumps(msg)}\n\n"
        finally:
            self.queues.remove(q)

manager = NotificationManager()


@router.get("/stream")
async def stream():
    """SSE endpoint for real-time proactive alerts."""
    return StreamingResponse(manager.subscribe(), media_type="text/event-stream")


class DemoAlertRequest(BaseModel):
    type:    str = "alert"
    level:   str = "HIGH"   # CRITICAL | HIGH | WARNING | INFO
    message: str = "🚨 [긴급 알림] H1동에서 PowerSpike 이상이 감지되었습니다. 평소 대비 +340 kW. 상세 원인을 분석할까요?"


@router.post("/demo")
async def send_demo_alert(req: DemoAlertRequest):
    """시연용 알림 즉시 발송 — 라이브 데모에서 안전하게 호출."""
    await manager.broadcast(req.dict())
    return {"sent": True, "subscribers": len(manager.queues)}
