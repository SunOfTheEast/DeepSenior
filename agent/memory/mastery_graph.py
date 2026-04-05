#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MasteryGraph — 知识图谱掌握度推断

基于 ConceptRegistry 的 prerequisite DAG，为每个学生提供图感知的掌握度视图。

设计原则：
  - concept_mastery 只存直接观测，推断在查询层实时计算
  - 父节点 effective_mastery = 子节点等权平均（或与直接观测取 max）
  - 纯计算，无 LLM 调用，微秒级响应
  - 等权聚合是第一步最小实现，未来可接 DKT 等数据驱动方法
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from agent.knowledge.concept_registry import ConceptNode, ConceptRegistry

from .data_structures import MasteryRecord, SemanticMemory

_DEFAULT_CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "concept_graph_cache.json"


def _compute_retention(record: MasteryRecord, now: datetime) -> float:
    """延迟导入 ebbinghaus 避免循环依赖。"""
    from agent.progress.ebbinghaus import compute_retention
    return compute_retention(record, now)


class MasteryGraph:
    """
    知识图谱 + 学生掌握度叠加层。

    静态图（所有学生共享）从 ConceptRegistry 构建，支持磁盘缓存。
    动态掌握度通过 overlay() 为特定学生创建视图。

    Usage:
        graph = MasteryGraph(concept_registry)
        view = graph.overlay(semantic_memory)
        print(view.effective_mastery("concept_ellipse_parametric"))
        print(view.bottlenecks())
        print(view.ready_to_learn())
    """

    def __init__(
        self,
        concept_registry: ConceptRegistry,
        cache_path: str | Path | None = _DEFAULT_CACHE_PATH,
    ):
        self._registry = concept_registry
        self._children: dict[str, list[str]] = defaultdict(list)
        self._cache_path = Path(cache_path) if cache_path else None

        if not self._try_load_cache():
            self._build_reverse_index()
            self._save_cache()

    def _build_reverse_index(self) -> None:
        """遍历所有 concept，为每个 prerequisite 记录其 dependent（子节点）。"""
        self._children.clear()
        for node in self._registry.all_nodes():
            if not node.is_active():
                continue
            for pre_id in node.prerequisites:
                self._children[pre_id].append(node.concept_id)

    def _try_load_cache(self) -> bool:
        """尝试从磁盘缓存加载反向索引。缓存过期则返回 False。"""
        if self._cache_path is None or not self._cache_path.exists():
            return False
        try:
            yaml_mtime = self._get_yaml_max_mtime()
            if yaml_mtime and self._cache_path.stat().st_mtime < yaml_mtime:
                return False  # YAML 比缓存新，需要重建
            with open(self._cache_path, encoding="utf-8") as f:
                data = json.load(f)
            self._children = defaultdict(list, {k: list(v) for k, v in data.items()})
            return True
        except Exception:
            return False

    def _save_cache(self) -> None:
        """将反向索引序列化到磁盘。"""
        if self._cache_path is None:
            return
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._cache_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(dict(self._children), f, ensure_ascii=False)
            tmp.replace(self._cache_path)
        except Exception:
            pass  # 缓存写入失败不影响功能

    def _get_yaml_max_mtime(self) -> float | None:
        """获取 concepts YAML 目录下最新文件的 mtime。"""
        root = self._registry.root
        if not root.exists():
            return None
        max_mt = 0.0
        for p in root.rglob("*.yaml"):
            mt = p.stat().st_mtime
            if mt > max_mt:
                max_mt = mt
        return max_mt if max_mt > 0 else None

    def get_children(self, concept_id: str) -> list[str]:
        """获取直接依赖此概念的下游概念 ID 列表。"""
        return list(self._children.get(concept_id, []))

    def get_parents(self, concept_id: str) -> list[str]:
        """获取此概念的直接前置概念 ID 列表。"""
        node = self._registry.get(concept_id)
        return list(node.prerequisites) if node else []

    def is_leaf(self, concept_id: str) -> bool:
        """是否为叶子节点（无下游依赖）。"""
        return len(self._children.get(concept_id, [])) == 0

    def overlay(self, semantic: SemanticMemory) -> "StudentMasteryView":
        """为特定学生创建掌握度视图。"""
        return StudentMasteryView(self, semantic)

    @property
    def all_concept_ids(self) -> list[str]:
        return self._registry.all_concept_ids()

    def get_node(self, concept_id: str) -> ConceptNode | None:
        return self._registry.get(concept_id)


class StudentMasteryView:
    """
    单个学生在知识图谱上的掌握度视图。

    所有查询实时计算，不缓存（图规模小，微秒级）。
    """

    def __init__(self, graph: MasteryGraph, semantic: SemanticMemory):
        self._graph = graph
        self._semantic = semantic
        self._now = datetime.utcnow()

    def direct_mastery(self, concept_id: str) -> float | None:
        """
        直接观测的掌握度（含 Ebbinghaus 衰减）。
        无观测数据时返回 None。
        """
        rec = self._semantic.concept_mastery.get(concept_id)
        if rec is None:
            return None
        return _compute_retention(rec, self._now)

    def effective_mastery(self, concept_id: str) -> float | None:
        """
        综合直接证据 + 子节点聚合 + Ebbinghaus 衰减。

        规则：
          - 叶子节点：纯直接观测 + 衰减
          - 有子节点 + 有直接观测：max(衰减后直接值, 子节点等权平均)
          - 有子节点 + 无直接观测：子节点等权平均
          - 无子节点 + 无直接观测：None
        """
        return self._effective_mastery_impl(concept_id, visited=set())

    def _effective_mastery_impl(self, concept_id: str, visited: set[str]) -> float | None:
        """带环检测的递归实现。"""
        if concept_id in visited:
            return self.direct_mastery(concept_id)
        visited.add(concept_id)

        direct = self.direct_mastery(concept_id)
        children = self._graph.get_children(concept_id)

        if not children:
            return direct

        child_scores: list[float] = []
        for child_id in children:
            score = self._effective_mastery_impl(child_id, visited)
            if score is not None:
                child_scores.append(score)

        if not child_scores:
            return direct

        inferred = sum(child_scores) / len(child_scores)

        if direct is None:
            return inferred
        return max(direct, inferred)

    def ready_to_learn(self, threshold: float = 0.6) -> list[str]:
        """
        可以学习的概念：前置概念全部 >= threshold，自身 < threshold 或无数据。

        返回 concept_id 列表，按前置完成度降序。
        """
        candidates: list[tuple[str, float]] = []
        for cid in self._graph.all_concept_ids:
            node = self._graph.get_node(cid)
            if node is None or not node.is_active():
                continue
            own = self.effective_mastery(cid)
            if own is not None and own >= threshold:
                continue

            parents = self._graph.get_parents(cid)
            if not parents:
                # 无前置，always ready
                candidates.append((cid, 1.0))
                continue

            parent_scores = [self.effective_mastery(pid) for pid in parents]
            if any(s is None or s < threshold for s in parent_scores):
                continue  # 前置未达标
            avg_parent = sum(s for s in parent_scores if s is not None) / len(parent_scores)
            candidates.append((cid, avg_parent))

        candidates.sort(key=lambda x: -x[1])
        return [cid for cid, _ in candidates]

    def bottlenecks(self, threshold: float = 0.5) -> list[str]:
        """
        瓶颈概念：自身 effective_mastery < threshold，且阻塞的下游概念最多。

        返回按影响范围降序排列的 concept_id 列表。
        """
        bottleneck_scores: list[tuple[str, int]] = []
        for cid in self._graph.all_concept_ids:
            em = self.effective_mastery(cid)
            if em is None or em >= threshold:
                continue
            blocked = self._count_blocked_descendants(cid)
            if blocked > 0:
                bottleneck_scores.append((cid, blocked))

        bottleneck_scores.sort(key=lambda x: -x[1])
        return [cid for cid, _ in bottleneck_scores]

    def _count_blocked_descendants(self, concept_id: str) -> int:
        """BFS 计算被此概念阻塞的下游概念数量。"""
        count = 0
        visited: set[str] = set()
        queue = list(self._graph.get_children(concept_id))
        while queue:
            cid = queue.pop(0)
            if cid in visited:
                continue
            visited.add(cid)
            count += 1
            queue.extend(self._graph.get_children(cid))
        return count

    def weakness_subgraph(self, threshold: float = 0.5) -> dict[str, dict]:
        """
        所有弱概念及其关联边，供画像可视化。

        Returns:
            {
                "nodes": {concept_id: {"name": ..., "mastery": ..., "difficulty": ...}},
                "edges": [{"from": ..., "to": ..., "type": "prerequisite"}]
            }
        """
        nodes: dict[str, dict] = {}
        edges: list[dict] = []

        for cid in self._graph.all_concept_ids:
            em = self.effective_mastery(cid)
            if em is not None and em < threshold:
                node = self._graph.get_node(cid)
                nodes[cid] = {
                    "name": node.name if node else cid,
                    "mastery": round(em, 3),
                    "difficulty": node.difficulty if node else 2,
                }

        for cid in nodes:
            for parent_id in self._graph.get_parents(cid):
                if parent_id in nodes:
                    edges.append({"from": parent_id, "to": cid, "type": "prerequisite"})

        return {"nodes": nodes, "edges": edges}

    def chapter_mastery(self, chapter: str) -> dict[str, float]:
        """某章节所有概念的 effective_mastery 快照。"""
        result: dict[str, float] = {}
        for node in self._graph._registry.list_by_chapter(chapter):
            em = self.effective_mastery(node.concept_id)
            if em is not None:
                result[node.concept_id] = round(em, 3)
        return result
