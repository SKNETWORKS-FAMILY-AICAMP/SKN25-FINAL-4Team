# -*- coding: utf-8 -*-
"""CMS rerank skeleton package."""

from cms.knowledge.rerank.interfaces import Reranker
from cms.knowledge.rerank.reranker import NoopReranker, rerank_chunks
from cms.knowledge.rerank.backends import RerankBackendDescriptor, make_reranker

__all__ = [
    "Reranker",
    "NoopReranker",
    "rerank_chunks",
    "RerankBackendDescriptor",
    "make_reranker",
]
