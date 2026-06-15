# -*- coding: utf-8 -*-
"""
Retrieval interface contracts for CMS knowledge backends.

Import-safe: protocol only, no Chroma/torch/sentence-transformers import.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from cms.contracts.retrieval import RetrievalQuery, RetrievalResult


@runtime_checkable
class Retriever(Protocol):
    """Protocol implemented by retrieval backends."""

    backend_name: str

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        """Return ranked chunks for a retrieval query."""
        ...
