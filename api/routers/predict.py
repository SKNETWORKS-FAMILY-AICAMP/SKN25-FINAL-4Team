from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from config.meter_metadata import get_all_meters, get_metadata
from scripts.predict_h1z16 import predict_meter

router = APIRouter()

logger = logging.getLogger(__name__)
ALL_METERS = set(get_all_meters())


@router.get("/{meter_urn}")
async def get_prediction(meter_urn: str, steps: int = 24) -> JSONResponse:
    if steps < 1 or steps > 168:
        raise HTTPException(status_code=400, detail="steps must be between 1 and 168")

    metadata = get_metadata(meter_urn)
    if metadata is None or meter_urn not in ALL_METERS:
        raise HTTPException(status_code=404, detail=f"meter_urn {meter_urn} not found")

    meter_type = metadata.get("meter_type")
    if meter_type != "electric":
        raise HTTPException(
            status_code=400,
            detail="Prediction is only supported for electricity meters",
        )

    logger.info("%s 예측 요청 - steps: %s", meter_urn, steps)
    start_time = time.time()

    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: predict_meter(meter_urn, steps),
        )
    except Exception as exc:
        logger.error("%s 예측 실패: %s", meter_urn, exc)
        raise HTTPException(status_code=500, detail=f"Prediction failed: {exc}") from exc

    elapsed = time.time() - start_time
    logger.info("%s 예측 완료 - 소요시간: %.1f초", meter_urn, elapsed)
    return JSONResponse(content=result)
