#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
EmbeddingDigestIndex - Digest 语义检索

复用 EmbeddingCardIndex 的模式：ZhipuAI embedding-3 + numpy cosine + 磁盘缓存。
对 MemoryDigest 的 summary 做 embedding，支持自然语言语义搜索。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from agent.infra.logging import get_logger

from .data_structures import MemoryDigest


_BATCH_SIZE = 50
_MAX_TEXT_CHARS = 500


class EmbeddingDigestIndex:
    """
    Digest 语义搜索索引。

    Usage:
        index = EmbeddingDigestIndex(api_key="...", cache_dir="...")
        index.build(digests)
        results = index.search("学生对参数化方法的掌握", top_k=5)
    """

    def __init__(
        self,
        api_key: str,
        model: str = "embedding-3",
        cache_dir: str | Path | None = None,
    ):
        self._api_key = api_key
        self._model = model
        self._cache_dir = Path(cache_dir) if cache_dir else None
        self._digest_ids: list[str] = []
        self._embeddings: np.ndarray | None = None
        self._client: Any = None
        self.logger = get_logger("EmbeddingDigestIndex")

    def _get_client(self) -> Any:
        if self._client is None:
            from zhipuai import ZhipuAI
            self._client = ZhipuAI(api_key=self._api_key)
        return self._client

    def _embed_texts(self, texts: list[str]) -> np.ndarray:
        client = self._get_client()
        all_vecs: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = [t[:_MAX_TEXT_CHARS] for t in texts[i:i + _BATCH_SIZE]]
            resp = client.embeddings.create(model=self._model, input=batch)
            for item in resp.data:
                all_vecs.append(item.embedding)
        return np.array(all_vecs, dtype=np.float32)

    @staticmethod
    def _digest_to_text(digest: MemoryDigest) -> str:
        """将 digest 转为适合 embedding 的文本。"""
        parts = [digest.summary]
        if digest.tags_covered:
            parts.append("知识点: " + ", ".join(digest.tags_covered[:10]))
        if digest.methods_used:
            parts.append("方法: " + ", ".join(digest.methods_used[:5]))
        parts.append(f"类型: {digest.digest_type} {digest.period_key}")
        return " ".join(parts)

    def build(self, digests: list[MemoryDigest]) -> None:
        """构建索引（尝试从缓存加载）。"""
        self._digest_ids.clear()
        if not digests:
            self._embeddings = None
            return

        if self._cache_dir and self._try_load_cache(len(digests)):
            return

        texts: list[str] = []
        for d in digests:
            self._digest_ids.append(d.digest_id)
            texts.append(self._digest_to_text(d))

        self._embeddings = self._embed_texts(texts)
        if self._cache_dir:
            self._save_cache()

    def upsert(self, digests: list[MemoryDigest]) -> None:
        """增量添加新 digest 到索引。"""
        existing = set(self._digest_ids)
        new_digests = [d for d in digests if d.digest_id not in existing]
        if not new_digests:
            return

        texts = []
        for d in new_digests:
            self._digest_ids.append(d.digest_id)
            texts.append(self._digest_to_text(d))

        new_embs = self._embed_texts(texts)
        if self._embeddings is not None and len(self._embeddings) > 0:
            self._embeddings = np.vstack([self._embeddings, new_embs])
        else:
            self._embeddings = new_embs

        if self._cache_dir:
            self._save_cache()

    def search(
        self,
        query_text: str,
        *,
        top_k: int = 5,
    ) -> list[tuple[str, float]]:
        """语义搜索，返回 (digest_id, score) 列表。"""
        if self._embeddings is None or len(self._digest_ids) == 0:
            return []

        q_vec = self._embed_texts([query_text])[0]
        norms = np.linalg.norm(self._embeddings, axis=1) * np.linalg.norm(q_vec)
        scores = self._embeddings @ q_vec / np.where(norms > 0, norms, 1.0)

        results: list[tuple[str, float]] = []
        for idx in np.argsort(-scores):
            results.append((self._digest_ids[idx], float(scores[idx])))
            if len(results) >= top_k:
                break
        return results

    # -- cache --

    def _save_cache(self) -> None:
        if self._cache_dir is None or self._embeddings is None:
            return
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        np.save(str(self._cache_dir / "digest_embeddings.npy"), self._embeddings)
        with open(self._cache_dir / "digest_ids.json", "w", encoding="utf-8") as f:
            json.dump(self._digest_ids, f, ensure_ascii=False)

    def _try_load_cache(self, expected_count: int) -> bool:
        if self._cache_dir is None:
            return False
        emb_path = self._cache_dir / "digest_embeddings.npy"
        ids_path = self._cache_dir / "digest_ids.json"
        if not emb_path.exists() or not ids_path.exists():
            return False
        try:
            with open(ids_path, encoding="utf-8") as f:
                ids = json.load(f)
            embs = np.load(str(emb_path))
            if len(ids) != expected_count or embs.shape[0] != expected_count:
                return False
            self._digest_ids = ids
            self._embeddings = embs
            self.logger.info(f"Loaded digest embedding cache: {expected_count} entries")
            return True
        except Exception:
            return False
