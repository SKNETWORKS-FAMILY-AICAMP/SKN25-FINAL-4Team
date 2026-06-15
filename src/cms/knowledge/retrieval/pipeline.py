# -*- coding: utf-8 -*-
"""Source-aware retrieval pipeline skeleton."""

from __future__ import annotations

from cms.contracts.retrieval import RetrievalQuery, RetrievalResult
from cms.knowledge.retrieval.retriever import NoopRetriever, retrieve
from cms.knowledge.retrieval.router import infer_source_family


def retrieve_for_evidence(
    query_id: str,
    text: str,
    source_family: str | None = None,
    top_k: int = 5,
    backend: NoopRetriever | None = None,
) -> RetrievalResult:
    """Build a source-aware RetrievalQuery and execute retrieval.

    This skeleton keeps the API stable for later source-split retrieval. For now it
    routes to a single inferred source family and uses a no-op backend by default.
    """

    family = infer_source_family(text, explicit=source_family)
    query = RetrievalQuery(
        query_id=query_id,
        text=text,
        source_family=family,
        top_k=top_k,
    )
    result = retrieve(query, backend=backend)
    merged_diagnostics = dict(result.diagnostics)
    merged_diagnostics["inferred_source_family"] = family
    return RetrievalResult(
        query=result.query,
        chunks=result.chunks,
        backend_name=result.backend_name,
        latency_ms=result.latency_ms,
        diagnostics=merged_diagnostics,
    )
