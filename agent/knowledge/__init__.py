#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Knowledge layer skeleton for RAG v2."""

from .card_index import CardIndexBase, NullCardIndex, SimpleCardIndex
from .card_retriever import CardRetriever
from .card_store import CardStoreBase, FileCardStore, InMemoryCardStore, NullCardStore
from .data_structures import (
    CandidateCardSummary,
    CardRetrieveRequest,
    CardRetrieveResult,
    CardSelectorRequest,
    CardSelectorResult,
    MethodCatalogTopic,
    MethodRouterRequest,
    MethodRouterResult,
    MethodSlot,
    PublishedKnowledgeCard,
    PublishedQuestion,
    PublishedSolution,
    QuestionCardLink,
    RagAuditEntry,
    RetrievalBundle,
    RetrievalConsumer,
    RetrievalGoal,
    RetrievedCard,
    SolutionCardLink,
    TopicResolveResult,
)
from .factory import build_card_retriever
from .method_catalog import MethodCatalog

__all__ = [
    "build_card_retriever",
    "CandidateCardSummary",
    "CardIndexBase",
    "CardRetriever",
    "CardRetrieveRequest",
    "CardRetrieveResult",
    "CardSelectorRequest",
    "CardSelectorResult",
    "CardStoreBase",
    "FileCardStore",
    "InMemoryCardStore",
    "MethodCatalog",
    "MethodCatalogTopic",
    "MethodRouterRequest",
    "MethodRouterResult",
    "MethodSlot",
    "NullCardIndex",
    "NullCardStore",
    "PublishedKnowledgeCard",
    "PublishedQuestion",
    "PublishedSolution",
    "QuestionCardLink",
    "RagAuditEntry",
    "RetrievalBundle",
    "RetrievalConsumer",
    "RetrievalGoal",
    "RetrievedCard",
    "SimpleCardIndex",
    "SolutionCardLink",
    "TopicResolveResult",
]
