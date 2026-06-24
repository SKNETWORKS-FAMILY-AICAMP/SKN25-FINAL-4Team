# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  모듈: src/cms/contracts/retrieval.py                                      ║
║  역할: CMS retrieval plane-neutral contracts                               ║
║  포함 클래스: RetrievalQuery, RetrievedChunk, RetrievalResult              ║
║  설계 원칙: import-safe, no DB/vector/LLM dependency                       ║
║  참조: docs/fairdata_to_cms_mapping_260610.md                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


SourceFamily = Literal[
    "anomaly_results",
    "monthly_report",
    "work_orders",
    "measurement",
    "unknown",
]


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1: Query / Chunk / Result contracts
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class RetrievalQuery:
    """Source-aware retrieval request.

    Args:
        query_id: Stable evaluation/runtime query id.
        text: User query text.
        source_family: Optional CMS source family hint.
        filters: Metadata filters such as period/anomaly_type/equipment_id.
        top_k: Requested number of chunks.
    """

    query_id: str
    text: str
    source_family: SourceFamily = "unknown"
    filters: dict[str, Any] = field(default_factory=dict)
    top_k: int = 5


@dataclass(frozen=True)
class RetrievedChunk:
    """A retrieval hit returned by a backend or merged pipeline.

    Args:
        chunk_id: Stable chunk identifier.
        text: Chunk text.
        source_family: CMS source family.
        score: Backend-normalized score; higher is better.
        metadata: JSON-like metadata used for grounding/evaluation.
    """

    chunk_id: str
    text: str
    source_family: SourceFamily = "unknown"
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalResult:
    """Retrieval output bundle.

    Args:
        query: Original retrieval query.
        chunks: Ranked chunks.
        backend_name: Backend descriptor name.
        latency_ms: Measured latency in milliseconds.
        diagnostics: Non-sensitive debug counters/notes.
    """

    query: RetrievalQuery
    chunks: list[RetrievedChunk] = field(default_factory=list)
    backend_name: str = "noop"
    latency_ms: float = 0.0
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def chunk_count(self) -> int:
        """Return number of retrieved chunks."""
        return len(self.chunks)
