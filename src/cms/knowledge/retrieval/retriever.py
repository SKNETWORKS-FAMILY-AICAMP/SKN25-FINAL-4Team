# -*- coding: utf-8 -*-
"""High-level retriever skeletons."""

from __future__ import annotations

from cms.contracts.retrieval import RetrievalQuery, RetrievalResult


class NoopRetriever:
    """Import-safe retriever that returns no chunks.

    This allows workflow/evaluation wiring to be tested before Chroma/BM25/dense
    backends are configured.
    """

    backend_name = "noop"

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        """Return an empty retrieval result with diagnostics."""
        return RetrievalResult(
            query=query,
            chunks=[],
            backend_name=self.backend_name,
            diagnostics={"note": "noop retriever returned no chunks"},
        )


def retrieve(query: RetrievalQuery, backend: NoopRetriever | None = None) -> RetrievalResult:
    """Retrieve chunks through the provided backend or the no-op backend."""

    retriever = backend or NoopRetriever()
    return retriever.retrieve(query)
