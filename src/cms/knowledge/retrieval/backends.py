# -*- coding: utf-8 -*-
"""Retrieval backend factory skeletons.

The default backend is no-op so the package stays import-safe. Real Chroma/BM25/
dense backends should be added behind lazy imports in `make_retriever(enabled=True)`.
"""

from __future__ import annotations

from dataclasses import dataclass

from cms.knowledge.retrieval.retriever import NoopRetriever
from cms.knowledge.retrieval.interfaces import Retriever


@dataclass(frozen=True)
class RetrievalBackendDescriptor:
    """Descriptor returned when a real backend is not enabled."""

    name: str = "noop"
    enabled: bool = False
    reason: str = "retrieval backend disabled or not configured"


def make_retriever(enabled: bool = False, backend: str = "noop") -> Retriever | RetrievalBackendDescriptor:
    """Create a retrieval backend or return an import-safe descriptor.

    Args:
        enabled: If False, return a descriptor and avoid optional imports.
        backend: Backend name. Currently only `noop` is implemented.
    """

    if not enabled:
        return RetrievalBackendDescriptor(name=backend, enabled=False)
    if backend == "noop":
        return NoopRetriever()
    return RetrievalBackendDescriptor(
        name=backend,
        enabled=False,
        reason=f"backend '{backend}' is not implemented in the skeleton",
    )
