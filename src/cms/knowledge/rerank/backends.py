# -*- coding: utf-8 -*-
"""Rerank backend factory skeleton."""

from __future__ import annotations

from dataclasses import dataclass

from cms.knowledge.rerank.interfaces import Reranker
from cms.knowledge.rerank.reranker import NoopReranker


@dataclass(frozen=True)
class RerankBackendDescriptor:
    """Descriptor returned when real rerank backend is disabled."""

    name: str = "noop"
    enabled: bool = False
    reason: str = "rerank backend disabled or not configured"


def make_reranker(enabled: bool = False, backend: str = "noop") -> Reranker | RerankBackendDescriptor:
    """Create a reranker backend or return an import-safe descriptor."""

    if not enabled:
        return RerankBackendDescriptor(name=backend, enabled=False)
    if backend == "noop":
        return NoopReranker()
    return RerankBackendDescriptor(
        name=backend,
        enabled=False,
        reason=f"backend '{backend}' is not implemented in the skeleton",
    )
