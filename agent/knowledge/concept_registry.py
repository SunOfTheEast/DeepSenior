#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ConceptRegistry — 概念节点注册表

从 YAML 文件加载概念定义，提供：
  - 按 concept_id 查询
  - 按 chapter 列举
  - 查询 concept → method_slot 关联
  - 查询前置依赖链

概念与方法的关系：
  concept 描述「是什么」（如「椭圆的定义与性质」）
  method_slot 描述「怎么做」（如「椭圆参数方程法」）
  一个 concept 可关联多个 method_slot，一个 method_slot 也可被多个 concept 引用。

YAML 文件布局::

    content/concepts/
      <chapter>/
        <topic>.yaml        # 每个 topic 一个文件，包含多个 concept 节点
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml

from agent.infra.logging import get_logger


@dataclass
class ConceptNode:
    """一个可寻址的知识概念。"""
    concept_id: str
    name: str
    chapter: str
    topic: str
    difficulty: int = 2          # 1=基础, 2=中等, 3=进阶
    prerequisites: list[str] = field(default_factory=list)   # concept_id 列表
    related_slots: list[str] = field(default_factory=list)   # method_slot_id 列表
    related_card_ids: list[str] = field(default_factory=list)
    description: str = ""
    status: str = "active"

    def is_active(self) -> bool:
        return self.status == "active"


class ConceptRegistry:
    """从 YAML 文件加载并索引概念节点。"""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root else Path(__file__).resolve().parents[2] / "content" / "concepts"
        self.logger = get_logger("Knowledge.ConceptRegistry")
        self._nodes: dict[str, ConceptNode] | None = None

    def _ensure_loaded(self) -> dict[str, ConceptNode]:
        if self._nodes is not None:
            return self._nodes
        self._nodes = {}
        if not self.root.exists():
            self.logger.warning(f"Concept root not found: {self.root}")
            return self._nodes
        for path in sorted(self.root.rglob("*.yaml")):
            try:
                nodes = self._load_file(path)
                for node in nodes:
                    self._nodes[node.concept_id] = node
            except Exception as exc:
                self.logger.warning(f"Failed to load concept file {path}: {exc}")
        self.logger.info(f"Loaded {len(self._nodes)} concept nodes from {self.root}")
        return self._nodes

    @staticmethod
    def _load_file(path: Path) -> list[ConceptNode]:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not data or not data.get("concepts"):
            return []
        chapter = str(data.get("chapter", "")).strip() or path.parent.name
        topic = str(data.get("topic", "")).strip() or path.stem
        nodes: list[ConceptNode] = []
        for item in data["concepts"]:
            cid = str(item.get("concept_id", "")).strip()
            if not cid:
                continue
            nodes.append(ConceptNode(
                concept_id=cid,
                name=str(item.get("name", "")).strip(),
                chapter=chapter,
                topic=topic,
                difficulty=int(item.get("difficulty", 2)),
                prerequisites=[str(p) for p in item.get("prerequisites", [])],
                related_slots=[str(s) for s in item.get("related_slots", [])],
                related_card_ids=[str(c) for c in item.get("related_card_ids", [])],
                description=str(item.get("description", "")).strip(),
                status=str(item.get("status", "active")),
            ))
        return nodes

    # -- query API --

    def get(self, concept_id: str) -> ConceptNode | None:
        return self._ensure_loaded().get(concept_id)

    def list_by_chapter(self, chapter: str) -> list[ConceptNode]:
        return [n for n in self._ensure_loaded().values() if n.chapter == chapter and n.is_active()]

    def list_by_topic(self, chapter: str, topic: str) -> list[ConceptNode]:
        return [
            n for n in self._ensure_loaded().values()
            if n.chapter == chapter and n.topic == topic and n.is_active()
        ]

    def find_by_slot(self, slot_id: str) -> list[ConceptNode]:
        """找到关联某个 method_slot 的所有 concept。"""
        return [
            n for n in self._ensure_loaded().values()
            if slot_id in n.related_slots and n.is_active()
        ]

    def find_by_card(self, card_id: str) -> list[ConceptNode]:
        """找到关联某张知识卡的所有 concept。"""
        return [
            n for n in self._ensure_loaded().values()
            if card_id in n.related_card_ids and n.is_active()
        ]

    def get_prerequisites(self, concept_id: str, max_depth: int = 3) -> list[ConceptNode]:
        """BFS 展开前置依赖链（防环，限深度）。"""
        nodes = self._ensure_loaded()
        visited: set[str] = set()
        result: list[ConceptNode] = []
        queue: list[tuple[str, int]] = [(concept_id, 0)]
        while queue:
            cid, depth = queue.pop(0)
            if cid in visited or depth > max_depth:
                continue
            visited.add(cid)
            node = nodes.get(cid)
            if node is None:
                continue
            if cid != concept_id:
                result.append(node)
            for pre_id in node.prerequisites:
                if pre_id not in visited:
                    queue.append((pre_id, depth + 1))
        return result

    def all_nodes(self) -> list[ConceptNode]:
        return list(self._ensure_loaded().values())

    def all_concept_ids(self) -> list[str]:
        return list(self._ensure_loaded().keys())
