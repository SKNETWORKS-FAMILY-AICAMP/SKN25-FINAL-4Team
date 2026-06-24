# -*- coding: utf-8 -*-
"""CMS source-family router for retrieval."""

from __future__ import annotations

from cms.contracts.retrieval import SourceFamily
from cms.knowledge.retrieval.keywords import keyword_source_scores


def infer_source_family(text: str, explicit: str | None = None) -> SourceFamily:
    """Infer CMS source family from explicit hint or deterministic keyword scores."""

    valid: set[SourceFamily] = {
        "anomaly_results",
        "monthly_report",
        "work_orders",
        "measurement",
        "unknown",
    }
    if explicit in valid:
        return explicit  # type: ignore[return-value]

    scores = keyword_source_scores(text)
    if not scores:
        return "unknown"
    best_family, best_score = max(scores.items(), key=lambda item: item[1])
    if best_score <= 0:
        return "unknown"
    return best_family
