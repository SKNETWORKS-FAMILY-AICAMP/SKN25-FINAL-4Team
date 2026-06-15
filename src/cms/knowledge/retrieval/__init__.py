# -*- coding: utf-8 -*-
"""CMS source-aware retrieval skeleton package."""

from cms.knowledge.retrieval.interfaces import Retriever
from cms.knowledge.retrieval.retriever import NoopRetriever, retrieve
from cms.knowledge.retrieval.router import infer_source_family
from cms.knowledge.retrieval.pipeline import retrieve_for_evidence

__all__ = [
    "Retriever",
    "NoopRetriever",
    "retrieve",
    "infer_source_family",
    "retrieve_for_evidence",
]
