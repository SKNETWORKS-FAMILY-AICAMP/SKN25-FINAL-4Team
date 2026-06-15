# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  모듈: src/cms/contracts/evaluation.py                                     ║
║  역할: CMS evaluation plane-neutral contracts                              ║
║  포함 클래스: EvalCase, EvalPrediction, EvalResult, EvalSummary            ║
║  설계 원칙: import-safe, deterministic metrics-friendly DTOs               ║
║  참조: docs/fairdata_to_cms_mapping_260610.md                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1: Evaluation case/result contracts
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class EvalCase:
    """Single evaluation input row.

    Args:
        case_id: Stable case id.
        query: User/eval query.
        expected_route: Expected route label, if route evaluation.
        source_doc: Expected source document/family, if evidence evaluation.
        reference_context: Ground-truth context/facts.
        reference_answer: Ground-truth answer text.
        metadata: JSON-like auxiliary fields.
    """

    case_id: str
    query: str
    expected_route: str | None = None
    source_doc: str | None = None
    reference_context: dict[str, Any] | str | None = None
    reference_answer: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvalPrediction:
    """Prediction payload produced by a route/retrieval/answer runner."""

    case_id: str
    output: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvalResult:
    """Metric result for one case."""

    case_id: str
    metrics: dict[str, float] = field(default_factory=dict)
    passed: bool = True
    errors: list[str] = field(default_factory=list)
    prediction: EvalPrediction | None = None


@dataclass(frozen=True)
class EvalSummary:
    """Aggregate evaluation summary."""

    test_id: str
    dataset_count: int
    metrics: dict[str, float] = field(default_factory=dict)
    per_group: dict[str, dict[str, float]] = field(default_factory=dict)
    error_count: int = 0
    artifact_paths: dict[str, str] = field(default_factory=dict)
