"""Inspection-candidate helpers for user-facing reports.

Only actual warning rows (`warning_flag=true`) become inspection candidates.
Normal/reference rows can remain evidence, but they are not shown as incidents.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

PHYSICAL_REASON_CODES = ("PHYSICAL", "PHYSICAL_LIMIT", "PLAUSIBILITY", "RANGE", "SPIKE")
INPUT_REASON_CODES = ("INPUT", "INPUT_QUALITY", "INPUT_QUALITY_ISSUE", "MISSING_INPUT", "BAD_INPUT")
LOW_SAMPLE_REASON_CODES = ("LOW_SAMPLE", "INSUFFICIENT_DATA", "LOW_COVERAGE", "SPARSE")


def build_inspection_candidates(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return sorted user-facing inspection candidates from anomaly rows."""

    candidates = []
    for row in rows:
        if row.get("warning_flag") is not True:
            continue
        reason_code = _text(row.get("warning_reason_code") or row.get("warning_type") or "UNKNOWN")
        reason_class = classify_reason(reason_code, row.get("status"))
        candidates.append(
            {
                "meter_urn": _text(row.get("meter_urn")) or "계량기 미확인",
                "target_ts": _text(row.get("target_ts") or row.get("forecast_origin_ts")),
                "warning_type": _text(row.get("warning_type")) or "warning",
                "reason_code": reason_code,
                "reason_class": reason_class,
                "priority": priority_for_reason(reason_class),
                "user_message": user_message(reason_class),
                "operator_action": operator_action(reason_class),
                "source_status": _text(row.get("status")) or "unknown",
            }
        )
    return sorted(candidates, key=lambda item: (item["priority"], item["target_ts"], item["meter_urn"]))


def classify_reason(reason_code: str, status: Any = None) -> str:
    text = f"{reason_code} {_text(status)}".upper()
    if any(token in text for token in PHYSICAL_REASON_CODES):
        return "physical"
    if any(token in text for token in INPUT_REASON_CODES):
        return "input"
    if any(token in text for token in LOW_SAMPLE_REASON_CODES):
        return "low_sample"
    return "operational_review"


def priority_for_reason(reason_class: str) -> int:
    return {"physical": 1, "input": 2, "low_sample": 3}.get(reason_class, 4)


def user_message(reason_class: str) -> str:
    return {
        "physical": "설비 또는 계량값 범위 확인이 필요한 경고입니다.",
        "input": "입력 데이터 품질을 먼저 확인해야 하는 경고입니다.",
        "low_sample": "관측 표본이 부족해 추가 확인이 필요한 경고입니다.",
    }.get(reason_class, "운영자가 원자료와 설비 상태를 함께 확인할 후보입니다.")


def operator_action(reason_class: str) -> str:
    return {
        "physical": "설비 상태, 계량기 결선, 해당 시간대 부하 변화를 확인합니다.",
        "input": "해당 계량기 원천 데이터 수집 상태와 누락 여부를 확인합니다.",
        "low_sample": "표본 부족 구간의 수집 지연 또는 결측 여부를 확인합니다.",
    }.get(reason_class, "관련 계량기와 시간대의 원자료를 대조합니다.")


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


__all__ = [
    "build_inspection_candidates",
    "classify_reason",
    "operator_action",
    "priority_for_reason",
    "user_message",
]
