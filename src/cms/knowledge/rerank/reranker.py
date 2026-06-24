# -*- coding: utf-8 -*-
"""High-level reranker skeleton."""

from __future__ import annotations

from cms.contracts.retrieval import RetrievedChunk, RetrievalQuery


class NoopReranker:
    """Import-safe reranker that preserves backend ranking."""

    backend_name = "noop"

    def rerank(self, query: RetrievalQuery, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """Return chunks unchanged."""
        return list(chunks)


def rerank_chunks(
    query: RetrievalQuery,
    chunks: list[RetrievedChunk],
    backend: NoopReranker | None = None,
) -> list[RetrievedChunk]:
    """Rerank chunks through the provided backend or no-op backend."""

    reranker = backend or NoopReranker()
    return reranker.rerank(query, chunks)
