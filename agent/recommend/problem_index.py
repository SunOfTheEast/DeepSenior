#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
EmbeddingProblemIndex — 题目语义相似度索引

对 DraftQuestion 做 ZhipuAI embedding-3 向量化，支持：
  - find_similar(problem_id): 找到与指定题最相似的其他题
  - search(query_text): 用自然语言查找相关题目

复用 EmbeddingCardIndex / EmbeddingDigestIndex 的模式。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from agent.infra.logging import get_logger


_BATCH_SIZE = 50
_MAX_TEXT_CHARS = 500

logger = get_logger("Recommend.EmbeddingProblemIndex")


class EmbeddingProblemIndex:
    """
    题目 embedding 索引。

    Usage:
        index = EmbeddingProblemIndex(api_key="...", cache_dir="...")
        index.build(questions)  # list[dict] from YAML
        results = index.find_similar("q_03_1205", top_k=5)
        results = index.search("椭圆联立消元", top_k=5)
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
        self._question_ids: list[str] = []
        self._embeddings: np.ndarray | None = None
        self._client: Any = None

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
    def _question_to_text(q: dict) -> str:
        """将 DraftQuestion dict 转为适合 embedding 的文本。"""
        parts = [q.get("stem", "")]
        # 解法方法名
        for sp in (q.get("solution_paths") or [])[:3]:
            method = sp.get("method", "")
            if method:
                parts.append(f"方法: {method}")
        # 章节和标签
        chapter = q.get("chapter", "")
        if chapter:
            parts.append(f"章节: {chapter}")
        tags = q.get("problem_tags", []) + q.get("method_tags", [])
        if tags:
            parts.append("标签: " + ", ".join(tags[:8]))
        return " ".join(parts)

    def build(self, questions: list[dict]) -> None:
        """构建索引。"""
        self._question_ids.clear()
        if not questions:
            self._embeddings = None
            return

        if self._cache_dir and self._try_load_cache(len(questions)):
            return

        texts: list[str] = []
        for q in questions:
            qid = q.get("question_id", "")
            if not qid:
                continue
            self._question_ids.append(qid)
            texts.append(self._question_to_text(q))

        self._embeddings = self._embed_texts(texts)
        logger.info(f"Built embedding index for {len(self._question_ids)} questions")
        if self._cache_dir:
            self._save_cache()

    def find_similar(
        self,
        problem_id: str,
        top_k: int = 5,
        exclude_ids: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        """用已有题的 embedding 做 query，找相似题。"""
        if self._embeddings is None or problem_id not in self._question_ids:
            return []
        idx = self._question_ids.index(problem_id)
        q_vec = self._embeddings[idx]
        return self._search_by_vector(q_vec, top_k, exclude_ids=set(exclude_ids or []) | {problem_id})

    def search(
        self,
        query_text: str,
        top_k: int = 5,
        exclude_ids: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        """用自然语言 query 搜索相关题目。"""
        if self._embeddings is None or len(self._question_ids) == 0:
            return []
        q_vec = self._embed_texts([query_text])[0]
        return self._search_by_vector(q_vec, top_k, exclude_ids=set(exclude_ids or []))

    def _search_by_vector(
        self,
        q_vec: np.ndarray,
        top_k: int,
        exclude_ids: set[str],
    ) -> list[tuple[str, float]]:
        norms = np.linalg.norm(self._embeddings, axis=1) * np.linalg.norm(q_vec)
        scores = self._embeddings @ q_vec / np.where(norms > 0, norms, 1.0)

        results: list[tuple[str, float]] = []
        for idx in np.argsort(-scores):
            qid = self._question_ids[idx]
            if qid in exclude_ids:
                continue
            results.append((qid, float(scores[idx])))
            if len(results) >= top_k:
                break
        return results

    # -- cache --

    def _save_cache(self) -> None:
        if self._cache_dir is None or self._embeddings is None:
            return
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        np.save(str(self._cache_dir / "problem_embeddings.npy"), self._embeddings)
        with open(self._cache_dir / "problem_ids.json", "w", encoding="utf-8") as f:
            json.dump(self._question_ids, f, ensure_ascii=False)

    def _try_load_cache(self, expected_count: int) -> bool:
        if self._cache_dir is None:
            return False
        emb_path = self._cache_dir / "problem_embeddings.npy"
        ids_path = self._cache_dir / "problem_ids.json"
        if not emb_path.exists() or not ids_path.exists():
            return False
        try:
            with open(ids_path, encoding="utf-8") as f:
                ids = json.load(f)
            embs = np.load(str(emb_path))
            if len(ids) != expected_count or embs.shape[0] != expected_count:
                return False
            self._question_ids = ids
            self._embeddings = embs
            logger.info(f"Loaded problem embedding cache: {expected_count} entries")
            return True
        except Exception:
            return False
