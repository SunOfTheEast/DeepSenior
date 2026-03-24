#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MemoryStore - 纯 I/O 持久化层

负责将 EpisodicMemory 和 SemanticMemory 读写到磁盘。
不含任何业务逻辑，只管文件 I/O。

目录结构：
  {base_dir}/
    {student_id}/
      episodic/
        {memory_id}.json    # 每条情节记忆独立文件
      semantic.json         # 语义画像（单文件，原地更新）
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Iterator

from agent.infra.logging import get_logger

from .data_structures import EpisodicMemory, SemanticMemory


_DEFAULT_BASE = Path(__file__).parent.parent.parent.parent / "data" / "memory"
_EPISODIC_INDEX_FILENAME = "_index.json"


class MemoryStore:
    """
    文件系统持久化层。

    线程安全性：单进程内顺序调用是安全的；多进程并发写同一学生需要外部锁。
    当前不引入额外依赖（无 SQLite / Redis），保持可迁移性。
    """

    def __init__(self, base_dir: str | Path | None = None):
        self.base_dir = Path(base_dir) if base_dir else _DEFAULT_BASE
        self.logger = get_logger("MemoryStore")

    # =========================================================================
    # Episodic
    # =========================================================================

    def save_episodic(self, memory: EpisodicMemory) -> None:
        path = self._episodic_path(memory.student_id, memory.memory_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 先写临时文件再原子重命名，防止写入中断导致文件损坏
        tmp_path = path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(memory.to_dict(), f, ensure_ascii=False, indent=2)
        tmp_path.replace(path)
        self._upsert_episodic_index(memory)
        self.logger.debug(f"Episodic saved: {memory.student_id}/{memory.memory_id}")

    def has_episodic(self, student_id: str, memory_id: str) -> bool:
        return self._episodic_path(student_id, memory_id).exists()

    def load_episodic(self, student_id: str, memory_id: str) -> EpisodicMemory | None:
        path = self._episodic_path(student_id, memory_id)
        if not path.exists():
            return None
        with open(path, encoding="utf-8") as f:
            return EpisodicMemory.from_dict(json.load(f))

    def list_episodic(
        self,
        student_id: str,
        limit: int | None = None,
        source: str | None = None,
    ) -> list[EpisodicMemory]:
        """
        返回该学生的情节记忆列表，按时间倒序（最新在前）。

        Args:
            limit: 最多返回条数
            source: 过滤来源 ("tutor" | "review")
        """
        episodic_dir = self._episodic_dir(student_id)
        if not episodic_dir.exists():
            return []

        index = self._load_episodic_index(student_id)
        if index.get("items"):
            memories: list[EpisodicMemory] = []
            stale_ids: list[str] = []
            items = sorted(
                index["items"],
                key=lambda x: x.get("created_at", ""),
                reverse=True,
            )
            for item in items:
                if source and item.get("source") != source:
                    continue
                memory_id = item.get("memory_id")
                if not memory_id:
                    continue
                m = self.load_episodic(student_id, memory_id)
                if not m:
                    stale_ids.append(memory_id)
                    continue
                memories.append(m)
                if limit and len(memories) >= limit:
                    break

            if stale_ids:
                index["items"] = [i for i in index["items"] if i.get("memory_id") not in set(stale_ids)]
                self._save_episodic_index(student_id, index)

            if memories:
                return memories

        memories = []
        for path in episodic_dir.glob("*.json"):
            if path.name == _EPISODIC_INDEX_FILENAME:
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    m = EpisodicMemory.from_dict(json.load(f))
                if source and m.source.value != source:
                    continue
                memories.append(m)
            except Exception as e:
                self.logger.warning(f"Failed to load episodic {path}: {e}")

        memories.sort(key=lambda m: m.created_at, reverse=True)
        # fallback 扫描后重建索引，后续读取走快路径
        self._rebuild_episodic_index(student_id, memories)
        return memories[:limit] if limit else memories

    def iter_episodic(self, student_id: str) -> Iterator[EpisodicMemory]:
        """流式迭代，适合大量记录"""
        for m in self.list_episodic(student_id):
            yield m

    def delete_episodic(self, student_id: str, memory_id: str) -> bool:
        path = self._episodic_path(student_id, memory_id)
        if path.exists():
            path.unlink()
            self._remove_episodic_from_index(student_id, memory_id)
            return True
        return False

    # =========================================================================
    # Semantic
    # =========================================================================

    def save_semantic(self, memory: SemanticMemory) -> None:
        path = self._semantic_path(memory.student_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 先写临时文件再原子重命名，防止写入中断导致文件损坏
        tmp_path = path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(memory.to_dict(), f, ensure_ascii=False, indent=2)
        tmp_path.replace(path)
        self.logger.debug(f"Semantic saved: {memory.student_id}")

    def load_semantic(self, student_id: str) -> SemanticMemory | None:
        path = self._semantic_path(student_id)
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return SemanticMemory.from_dict(json.load(f))
        except Exception as e:
            self.logger.error(f"Failed to load semantic for {student_id}: {e}")
            return None

    def load_or_create_semantic(self, student_id: str) -> SemanticMemory:
        """加载已有语义记忆，若不存在则创建空白记录"""
        return self.load_semantic(student_id) or SemanticMemory.new(student_id)

    # =========================================================================
    # Utility
    # =========================================================================

    def list_students(self) -> list[str]:
        """返回所有有记忆的学生 ID"""
        if not self.base_dir.exists():
            return []
        return [d.name for d in self.base_dir.iterdir() if d.is_dir()]

    def student_exists(self, student_id: str) -> bool:
        return (self.base_dir / student_id).exists()

    def episodic_count(self, student_id: str) -> int:
        d = self._episodic_dir(student_id)
        if not d.exists():
            return 0
        return sum(1 for f in d.glob("*.json") if f.name != _EPISODIC_INDEX_FILENAME)

    # =========================================================================
    # Private
    # =========================================================================

    def _episodic_index_path(self, student_id: str) -> Path:
        return self._episodic_dir(student_id) / _EPISODIC_INDEX_FILENAME

    def _load_episodic_index(self, student_id: str) -> dict:
        path = self._episodic_index_path(student_id)
        if not path.exists():
            return {"version": 1, "items": []}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {"version": 1, "items": []}
            items = data.get("items")
            if not isinstance(items, list):
                data["items"] = []
            data.setdefault("version", 1)
            return data
        except Exception as e:
            self.logger.warning(f"Failed to load episodic index for {student_id}: {e}")
            return {"version": 1, "items": []}

    def _save_episodic_index(self, student_id: str, index: dict) -> None:
        path = self._episodic_index_path(student_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)
        tmp_path.replace(path)

    def _upsert_episodic_index(self, memory: EpisodicMemory) -> None:
        student_id = memory.student_id
        index = self._load_episodic_index(student_id)
        items = [i for i in index.get("items", []) if i.get("memory_id") != memory.memory_id]
        items.append(
            {
                "memory_id": memory.memory_id,
                "created_at": memory.created_at.isoformat(),
                "source": memory.source.value,
                "problem_id": memory.problem_id,
                "chapter": memory.chapter,
                "outcome": memory.outcome,
            }
        )
        items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        index["items"] = items
        self._save_episodic_index(student_id, index)

    def _remove_episodic_from_index(self, student_id: str, memory_id: str) -> None:
        index = self._load_episodic_index(student_id)
        before = len(index.get("items", []))
        index["items"] = [i for i in index.get("items", []) if i.get("memory_id") != memory_id]
        if len(index["items"]) != before:
            self._save_episodic_index(student_id, index)

    def _rebuild_episodic_index(self, student_id: str, memories: list[EpisodicMemory]) -> None:
        items = [
            {
                "memory_id": m.memory_id,
                "created_at": m.created_at.isoformat(),
                "source": m.source.value,
                "problem_id": m.problem_id,
                "chapter": m.chapter,
                "outcome": m.outcome,
            }
            for m in memories
        ]
        index = {"version": 1, "items": items}
        self._save_episodic_index(student_id, index)

    def _episodic_dir(self, student_id: str) -> Path:
        return self.base_dir / student_id / "episodic"

    def _episodic_path(self, student_id: str, memory_id: str) -> Path:
        return self._episodic_dir(student_id) / f"{memory_id}.json"

    def _semantic_path(self, student_id: str) -> Path:
        return self.base_dir / student_id / "semantic.json"
