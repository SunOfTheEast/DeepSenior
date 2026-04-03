#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Card search indices for RAG v2 — token overlap + embedding."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
import re
from typing import Any

import numpy as np

from .data_structures import PublishedKnowledgeCard

_logger = logging.getLogger("Knowledge.CardIndex")


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
                    " ".join(card.formula_cues),
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
        tokens: set[str] = set()
        for chunk in re.findall(r"[\u4e00-\u9fff]+|[a-z0-9_]+", lowered):
            if len(chunk) < 2:
                continue
            if re.fullmatch(r"[a-z0-9_]+", chunk):
                tokens.add(chunk)
                continue

            # 中文连续串按 2-3 字滑窗展开，避免整句查询无法命中卡片摘要。
            if len(chunk) <= 3:
                tokens.add(chunk)
            for n in (2, 3):
                if len(chunk) < n:
                    continue
                for i in range(len(chunk) - n + 1):
                    tokens.add(chunk[i:i + n])
        return tokens

    def _score(self, query_tokens: set[str], doc: str) -> float:
        doc_lower = doc.lower()
        score = 0.0
        for token in query_tokens:
            if token in doc_lower:
                score += 1.0
        return score


class EmbeddingCardIndex(CardIndexBase):
    """Semantic search using embedding vectors (ZhipuAI or OpenAI-compatible).

    Embeddings are computed on build/upsert and cached to disk.
    Queries embed on-the-fly and use cosine similarity.
    """

    _BATCH_SIZE = 50

    def __init__(
        self,
        api_key: str,
        model: str = "embedding-3",
        cache_dir: str | Path | None = None,
    ):
        self._api_key = api_key
        self._model = model
        self._cache_dir = Path(cache_dir) if cache_dir else None
        self._card_ids: list[str] = []
        self._chapters: dict[str, str] = {}
        self._embeddings: np.ndarray | None = None  # (N, dim)
        self._client: Any = None

    def _get_client(self):
        if self._client is None:
            from zhipuai import ZhipuAI
            self._client = ZhipuAI(api_key=self._api_key)
        return self._client

    def _embed_texts(self, texts: list[str]) -> np.ndarray:
        """Batch embed texts, returns (N, dim) array."""
        client = self._get_client()
        all_vecs = []
        for i in range(0, len(texts), self._BATCH_SIZE):
            batch = [t[:500] for t in texts[i:i + self._BATCH_SIZE]]
            resp = client.embeddings.create(model=self._model, input=batch)
            for item in resp.data:
                all_vecs.append(item.embedding)
        return np.array(all_vecs, dtype=np.float32)

    @staticmethod
    def _card_to_text(card: PublishedKnowledgeCard) -> str:
        parts = [
            card.title,
            card.summary,
            " ".join(card.general_methods),
            " ".join(card.method_tags),
            " ".join(card.problem_tags),
            " ".join(card.formula_cues[:3]),
        ]
        return " ".join(parts)

    def build(self, cards: list[PublishedKnowledgeCard]) -> None:
        self._card_ids.clear()
        self._chapters.clear()

        # Try loading from cache first
        if self._cache_dir and self._try_load_cache(len(cards)):
            for card in cards:
                self._chapters[card.card_id] = card.chapter
            _logger.info("Loaded %d embeddings from cache", len(self._card_ids))
            return

        texts = []
        for card in cards:
            self._card_ids.append(card.card_id)
            self._chapters[card.card_id] = card.chapter
            texts.append(self._card_to_text(card))

        _logger.info("Embedding %d cards with %s...", len(texts), self._model)
        self._embeddings = self._embed_texts(texts)
        _logger.info("Built embedding index: %s", self._embeddings.shape)

        if self._cache_dir:
            self._save_cache()

    def upsert(self, cards: list[PublishedKnowledgeCard]) -> None:
        if self._embeddings is None:
            self.build(cards)
            return
        texts = []
        new_ids = []
        for card in cards:
            if card.card_id in set(self._card_ids):
                continue
            new_ids.append(card.card_id)
            self._chapters[card.card_id] = card.chapter
            texts.append(self._card_to_text(card))
        if texts:
            new_vecs = self._embed_texts(texts)
            self._card_ids.extend(new_ids)
            self._embeddings = np.vstack([self._embeddings, new_vecs])

    def remove(self, card_ids: list[str]) -> None:
        remove_set = set(card_ids)
        keep = [i for i, cid in enumerate(self._card_ids) if cid not in remove_set]
        self._card_ids = [self._card_ids[i] for i in keep]
        if self._embeddings is not None:
            self._embeddings = self._embeddings[keep]
        for cid in card_ids:
            self._chapters.pop(cid, None)

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
        if self._embeddings is None or len(self._card_ids) == 0:
            return []

        q_vec = self._embed_texts([query_text])[0]
        # Cosine similarity
        norms = np.linalg.norm(self._embeddings, axis=1) * np.linalg.norm(q_vec)
        scores = self._embeddings @ q_vec / np.where(norms > 0, norms, 1.0)

        exclude = set(exclude_card_ids or [])
        candidates = set(candidate_card_ids) if candidate_card_ids else None

        results: list[tuple[str, float]] = []
        for idx in np.argsort(-scores):
            cid = self._card_ids[idx]
            if cid in exclude:
                continue
            if candidates and cid not in candidates:
                continue
            if chapter and self._chapters.get(cid) != chapter:
                continue
            results.append((cid, float(scores[idx])))
            if len(results) >= top_k:
                break

        return results

    # ── Cache ──

    def _save_cache(self) -> None:
        if not self._cache_dir:
            return
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        np.save(self._cache_dir / "embeddings.npy", self._embeddings)
        with open(self._cache_dir / "card_ids.json", "w") as f:
            json.dump(self._card_ids, f)
        _logger.info("Saved embedding cache to %s", self._cache_dir)

    def _try_load_cache(self, expected_count: int) -> bool:
        if not self._cache_dir:
            return False
        emb_path = self._cache_dir / "embeddings.npy"
        ids_path = self._cache_dir / "card_ids.json"
        if not emb_path.exists() or not ids_path.exists():
            return False
        with open(ids_path) as f:
            self._card_ids = json.load(f)
        if len(self._card_ids) != expected_count:
            return False  # stale cache
        self._embeddings = np.load(emb_path)
        return True
