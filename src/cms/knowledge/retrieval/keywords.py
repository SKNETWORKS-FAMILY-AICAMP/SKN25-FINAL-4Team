# -*- coding: utf-8 -*-
"""Deterministic keyword helpers for CMS retrieval source hints."""

from __future__ import annotations

from cms.contracts.retrieval import SourceFamily


_KEYWORDS: dict[SourceFamily, tuple[str, ...]] = {
    "anomaly_results": (
        "anomaly", "이상", "탐지", "severity", "잔차", "residual", "total_count",
    ),
    "monthly_report": (
        "월간", "리포트", "cop", "자가소비율", "계통의존도", "총소비전력", "냉방에너지", "난방에너지",
    ),
    "work_orders": (
        "작업 지시", "작업지시", "work order", "ahu", "건강 점수", "health score", "유지보수",
    ),
    "measurement": (
        "계량기", "meter", "measurement", "coverage", "결측", "gap", "kwh", "유효전력",
    ),
    "unknown": (),
}


def keyword_source_scores(text: str) -> dict[SourceFamily, int]:
    """Return simple keyword hit counts by source family."""

    lowered = text.lower()
    return {
        family: sum(1 for keyword in keywords if keyword.lower() in lowered)
        for family, keywords in _KEYWORDS.items()
        if family != "unknown"
    }
