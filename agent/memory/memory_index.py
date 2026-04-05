#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MemoryIndex - 内存倒排索引

基于 MemoryStore 的 _index.json 构建多维过滤索引，
支持按 concept/method/error_type/chapter/outcome/time 组合查询。

设计原则：
  - 零外部依赖：纯 Python dict/set 实现
  - 惰性加载：per-student 按需加载
  - 向后兼容：旧格式 index entry 缺失字段默认为空
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .data_structures import EpisodicMemory
from .memory_store import MemoryStore


@dataclass
class IndexEntry:
    """单条 index entry 的结构化表示。"""
    memory_id: str
    created_at: str
    source: str = ""
    problem_id: str = ""
    chapter: str = ""
    outcome: str = ""
    tags: list[str] = field(default_factory=list)
    methods_used: list[str] = field(default_factory=list)
    method_slot_matched: str | None = None
    error_types: list[str] = field(default_factory=list)
    solution_id: str | None = None
    session_id: str = ""
    narrative_preview: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "IndexEntry":
        return cls(
            memory_id=d.get("memory_id", ""),
            created_at=d.get("created_at", ""),
            source=d.get("source", ""),
            problem_id=d.get("problem_id", ""),
            chapter=d.get("chapter", ""),
            outcome=d.get("outcome", ""),
            tags=list(d.get("tags") or []),
            methods_used=list(d.get("methods_used") or []),
            method_slot_matched=d.get("method_slot_matched"),
            error_types=list(d.get("error_types") or []),
            solution_id=d.get("solution_id"),
            session_id=d.get("session_id", ""),
            narrative_preview=d.get("narrative_preview", ""),
        )


class MemoryIndex:
    """
    内存倒排索引，支持多维过滤。

    Usage:
        index = MemoryIndex(store)
        results = index.query("student_1", concept_ids=["ellipse_parametric"], limit=5)
        episodes = index.get_episodes("student_1", results)
    """

    def __init__(self, store: MemoryStore):
        self._store = store
        self._loaded: dict[str, list[IndexEntry]] = {}  # student_id → entries

    def load_student(self, student_id: str, force: bool = False) -> None:
        """从 _index.json 加载该学生的索引到内存。"""
        if not force and student_id in self._loaded:
            return
        raw_index = self._store._load_episodic_index(student_id)
        entries = [IndexEntry.from_dict(item) for item in raw_index.get("items", [])]
        entries.sort(key=lambda e: e.created_at, reverse=True)
        self._loaded[student_id] = entries

    def _ensure_loaded(self, student_id: str) -> list[IndexEntry]:
        self.load_student(student_id)
        return self._loaded.get(student_id, [])

    def query(
        self,
        student_id: str,
        *,
        concept_ids: list[str] | None = None,
        chapter: str | None = None,
        outcome: str | None = None,
        method_slot: str | None = None,
        error_types: list[str] | None = None,
        source: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 20,
    ) -> list[IndexEntry]:
        """
        多维过滤，返回匹配的 index entry 列表（按时间倒序）。

        过滤逻辑：所有条件 AND，列表型条件（concept_ids、error_types）为 any-match。
        """
        entries = self._ensure_loaded(student_id)
        concept_set = set(concept_ids) if concept_ids else None
        error_set = set(error_types) if error_types else None

        results: list[IndexEntry] = []
        for entry in entries:
            if source and entry.source != source:
                continue
            if chapter and entry.chapter != chapter:
                continue
            if outcome and entry.outcome != outcome:
                continue
            if method_slot and entry.method_slot_matched != method_slot:
                continue
            if concept_set and not concept_set.intersection(entry.tags):
                continue
            if error_set and not error_set.intersection(entry.error_types):
                continue
            if since and entry.created_at < since.isoformat():
                continue
            if until and entry.created_at > until.isoformat():
                continue
            results.append(entry)
            if len(results) >= limit:
                break
        return results

    def get_episodes(
        self,
        student_id: str,
        entries: list[IndexEntry],
    ) -> list[EpisodicMemory]:
        """按 memory_id 批量惰性加载完整 episode。"""
        episodes: list[EpisodicMemory] = []
        for entry in entries:
            ep = self._store.load_episodic(student_id, entry.memory_id)
            if ep:
                episodes.append(ep)
        return episodes

    def invalidate(self, student_id: str) -> None:
        """清除某学生的缓存索引（commit 后调用）。"""
        self._loaded.pop(student_id, None)

    def invalidate_all(self) -> None:
        """清除所有缓存。"""
        self._loaded.clear()
