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

# 목록 API 응답 필드: 프론트 표시용 핵심 속성만 반환하고 내부 검수 필드는 상세 API에 남깁니다.
METER_LIST_FIELDS = (
    "meter_type",
    "energy_type",
    "thermal_mode",
    "group_name",
    "description",
)


def _meter_list_item(meter_urn: str, metadata: dict) -> dict:
    return {
        "meter_urn": meter_urn,
        **{field: metadata.get(field) for field in METER_LIST_FIELDS},
    }


@router.get("")
def get_meters() -> list[dict]:
    logger.info("GET /meters 요청")
    meters = []
    for meter_urn in get_all_meters():
        metadata = get_metadata(meter_urn)
        if metadata is None:
            continue
        meters.append(_meter_list_item(meter_urn, metadata))
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
