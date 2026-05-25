from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zipfile import ZipFile


def _to_datetime(timestamp: str | int | None) -> datetime:
    if timestamp is None:
        raise ValueError("timestamp is required")

    if isinstance(timestamp, (int, float)):
        return datetime.fromtimestamp(int(float(timestamp)), tz=timezone.utc)

    timestamp_str = str(timestamp).strip().strip('"').strip("'")
    try:
        return datetime.fromtimestamp(int(float(timestamp_str)), tz=timezone.utc)
    except ValueError:
        return datetime.strptime(timestamp_str, "%Y/%m/%d %H:%M:%S%z").astimezone(timezone.utc)


def _parse_issue_block(issue_key: str, issue_info: dict[str, Any]) -> dict[str, Any]:
    meter_urn = issue_info.get("reference") or str(issue_key).split("@", 1)[0]
    issue_type = issue_info.get("reason", "")
    method = issue_info.get("correction", "")

    return {
        "meter_urn": str(meter_urn),
        "issue_type": str(issue_type),
        "method": str(method),
        "start": _to_datetime(issue_info.get("time_start")),
        "end": _to_datetime(issue_info.get("time_end")),
    }


def _parse_yaml_like_text(raw_text: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    current_key: str | None = None
    current_info: dict[str, Any] = {}

    for line in raw_text.splitlines():
        if not line.strip():
            continue

        if not line.startswith(" ") and line.endswith(":"):
            if current_key is not None and current_info:
                issues.append(_parse_issue_block(current_key, current_info))
            current_key = line[:-1]
            current_info = {}
            continue

        if current_key is None or ":" not in line:
            continue

        stripped = line.strip()
        field, value = stripped.split(":", 1)
        value = value.strip()

        if field in {"reference", "reason", "correction"}:
            current_info[field] = value
        elif field in {"time_start", "time_end"}:
            current_info[field] = value

    if current_key is not None and current_info:
        issues.append(_parse_issue_block(current_key, current_info))

    return issues


def parse_issues(zip_path: str | Path) -> list[dict[str, Any]]:
    zip_path = Path(zip_path)
    issues: list[dict[str, Any]] = []

    with ZipFile(zip_path) as zip_file:
        target_names = [
            name
            for name in zip_file.namelist()
            if name.endswith(".yaml")
            and (name.startswith("automatic_issues/") or name.startswith("manual_issues/"))
        ]

        for name in target_names:
            if "template" in name.lower():
                continue
            raw_text = zip_file.read(name).decode("utf-8")
            issues.extend(_parse_yaml_like_text(raw_text))

    return issues
