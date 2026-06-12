from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from config.meter_metadata import get_all_meters, get_metadata


router = APIRouter()
logger = logging.getLogger(__name__)

TYPE_LABELS = {
    "electric": "electricity",
    "thermal": "heat",
    "weather": "weather",
}


@router.get("")
def get_meters() -> list[dict]:
    logger.info("GET /meters 요청")
    meters = []
    for meter_urn in get_all_meters():
        metadata = get_metadata(meter_urn)
        if metadata is None:
            continue
        meters.append({"meter_urn": meter_urn, **metadata})
    return meters


@router.get("/types")
def get_meter_types() -> dict[str, list[str]]:
    logger.info("GET /meters/types 요청")
    grouped: dict[str, list[str]] = {
        "electricity": [],
        "heat": [],
        "weather": [],
    }

    for meter_urn in get_all_meters():
        metadata = get_metadata(meter_urn)
        if metadata is None:
            continue
        meter_type = TYPE_LABELS.get(str(metadata.get("meter_type")), str(metadata.get("meter_type")))
        grouped.setdefault(meter_type, []).append(meter_urn)

    return grouped


@router.get("/{meter_urn}")
def get_meter(meter_urn: str) -> dict:
    logger.info("GET /meters/%s 요청", meter_urn)
    metadata = get_metadata(meter_urn)
    if metadata is None:
        raise HTTPException(status_code=404, detail=f"meter_urn {meter_urn} not found")
    return {"meter_urn": meter_urn, **metadata}
