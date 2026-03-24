#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Embedding fallback skeleton for RAG v2."""

from __future__ import annotations

from abc import ABC, abstractmethod
import re

from .data_structures import PublishedKnowledgeCard


class CardIndexBase(ABC):
    @abstractmethod
    def build(self, cards: list[PublishedKnowledgeCard]) -> None:
        ...

    @abstractmethod
    def upsert(self, cards: list[PublishedKnowledgeCard]) -> None:
        ...

    @abstractmethod
    def remove(self, card_ids: list[str]) -> None:
        ...

    @abstractmethod
    def search(
        self,
        query_text: str,
        *,
        candidate_card_ids: list[str] | None = None,
        chapter: str | None = None,
        topic: str | None = None,
        exclude_card_ids: list[str] | None = None,
        top_k: int = 5,
    ) -> list[tuple[str, float]]:
        ...


class NullCardIndex(CardIndexBase):
    def build(self, cards: list[PublishedKnowledgeCard]) -> None:
        return None

    def upsert(self, cards: list[PublishedKnowledgeCard]) -> None:
        return None

    def remove(self, card_ids: list[str]) -> None:
        return None

    def search(
        self,
        query_text: str,
        *,
        candidate_card_ids: list[str] | None = None,
        chapter: str | None = None,
        topic: str | None = None,
        exclude_card_ids: list[str] | None = None,
        top_k: int = 5,
    ) -> list[tuple[str, float]]:
        return []


class SimpleCardIndex(CardIndexBase):
    """Simple token-overlap search for early fallback wiring."""

    def __init__(self):
        self._docs: dict[str, str] = {}
        self._chapters: dict[str, str] = {}

    def build(self, cards: list[PublishedKnowledgeCard]) -> None:
        self._docs.clear()
        self._chapters.clear()
        self.upsert(cards)

    def upsert(self, cards: list[PublishedKnowledgeCard]) -> None:
        for card in cards:
            text = " ".join(
                [
                    card.title,
                    card.summary,
                    " ".join(card.general_methods),
                    " ".join(card.method_tags),
                ]
            ).strip()
            self._docs[card.card_id] = text
            self._chapters[card.card_id] = card.chapter

    def remove(self, card_ids: list[str]) -> None:
        for card_id in card_ids:
            self._docs.pop(card_id, None)
            self._chapters.pop(card_id, None)

    def search(
        self,
        query_text: str,
        *,
        candidate_card_ids: list[str] | None = None,
        chapter: str | None = None,
        topic: str | None = None,
        exclude_card_ids: list[str] | None = None,
        top_k: int = 5,
    ) -> list[tuple[str, float]]:
        del topic  # reserved for future filtering
        exclude = set(exclude_card_ids or [])
        query_tokens = self._tokenize(query_text)
        if not query_tokens:
            return []

        pool = candidate_card_ids or list(self._docs.keys())
        scored: list[tuple[str, float]] = []
        for card_id in pool:
            if card_id in exclude:
                continue
            if chapter and self._chapters.get(card_id) != chapter:
                continue
            doc = self._docs.get(card_id, "")
            if not doc:
                continue
            score = self._score(query_tokens, doc)
            if score > 0:
                scored.append((card_id, score))
        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored[:top_k]

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        lowered = (text or "").lower()
        return {t for t in re.findall(r"[\u4e00-\u9fff]+|[a-z0-9_]+", lowered) if len(t) >= 2}

    def _score(self, query_tokens: set[str], doc: str) -> float:
        doc_lower = doc.lower()
        score = 0.0
        for token in query_tokens:
            if token in doc_lower:
                score += 1.0
        return score

