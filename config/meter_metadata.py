from __future__ import annotations

import json
from pathlib import Path
from typing import Any


METADATA_PATH = Path(__file__).with_name("meter_metadata.json")


def load_metadata() -> dict[str, dict[str, Any]]:
    with METADATA_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def get_metadata(meter_urn: str) -> dict[str, Any] | None:
    return load_metadata().get(meter_urn)


def get_all_meters() -> list[str]:
    return list(load_metadata().keys())


def get_meters_by_group(group_name: str) -> list[str]:
    metadata = load_metadata()
    return [
        meter_urn
        for meter_urn, info in metadata.items()
        if info.get("group_name") == group_name
    ]


def get_meters_by_type(meter_type: str) -> list[str]:
    metadata = load_metadata()
    return [
        meter_urn
        for meter_urn, info in metadata.items()
        if info.get("meter_type") == meter_type
    ]


def get_anomaly_target(meter_urn: str) -> str | None:
    metadata = get_metadata(meter_urn)
    if metadata is None:
        return None
    return metadata.get("anomaly_target")


def get_redundant_pair(meter_urn: str) -> str | None:
    metadata = get_metadata(meter_urn)
    if metadata is None:
        return None
    return metadata.get("redundant_pair")
