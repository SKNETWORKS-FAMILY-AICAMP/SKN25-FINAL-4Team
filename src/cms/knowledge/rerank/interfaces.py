# -*- coding: utf-8 -*-
"""Reranker protocol for CMS retrieval results."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from cms.contracts.retrieval import RetrievedChunk, RetrievalQuery


@runtime_checkable
class Reranker(Protocol):
    """Protocol implemented by rerank backends."""

    backend_name: str

    def rerank(self, query: RetrievalQuery, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """Return reranked chunks."""
        ...
