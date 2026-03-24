#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Recommend 模块数据结构

RecommendationType  — 推荐类型枚举
ProblemQuery        — 传给题库的查询条件（与题库格式解耦）
RecommendContext    — 推荐所需的完整上下文（来自 Tutor / Review + 记忆层）
Recommendation      — 推荐结果
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agent.tutor.data_structures import ProblemContext
from agent.memory.data_structures import EpisodicMemory, SemanticMemory


# =============================================================================
# Enums
# =============================================================================

class RecommendationType(str, Enum):
    SIMILAR_PROBLEM    = "similar_problem"    # 同类型、相似难度，用于巩固
    EASIER_PROBLEM     = "easier_problem"     # 降难度，针对前置薄弱点
    HARDER_PROBLEM     = "harder_problem"     # 升难度，挑战提升
    REVIEW_CONCEPT     = "review_concept"     # 复习特定知识点（不一定有新题）
    RETRY_WITH_METHOD  = "retry_with_method"  # 同题换方法（不需要题库）
    REST               = "rest"               # 今日已做足，建议休息


class RecommendSource(str, Enum):
    TUTOR  = "tutor"
    REVIEW = "review"


# =============================================================================
# 题库查询条件（与具体题库实现解耦）
# =============================================================================

@dataclass
class ProblemQuery:
    """
    传给 ProblemBankBase.query() 的查询条件。

    题库设计者根据此结构实现 query()，不需要修改 Recommend 模块内部逻辑。
    """
    tags: list[str]                           # 必须匹配的知识点 ID
    tag_mode: str = "any"                     # "any"（OR）| "all"（AND）
    chapter: str | None = None                # 限定章节
    difficulty: str | None = None             # "基础" | "中等" | "进阶"
    exclude_ids: list[str] = field(default_factory=list)  # 已做过的题，排除
    limit: int = 3                            # 最多返回条数


# =============================================================================
# 推荐上下文
# =============================================================================

@dataclass
class RecommendContext:
    """
    RecommendManager 构建的完整推荐上下文。

    来源：
      - session_export：TutorManager 或 ReviewChatManager 的 export/close 结果
      - semantic_memory：MemoryManager 读取的长期语义画像
      - recent_episodes：最近 N 条情节记忆（用于避免重复推荐）
    """
    student_id: str
    source: RecommendSource
    session_export: dict[str, Any]

    # 来自 MemoryManager
    semantic_memory: SemanticMemory | None
    recent_episodes: list[EpisodicMemory]

    # 当前题目信息（从 session_export 提取，方便后续使用）
    current_problem_id: str
    current_tags: list[str]
    current_chapter: str

    def get_done_problem_ids(self) -> list[str]:
        """返回学生近期做过的所有题目 ID，用于题库排除"""
        ids = {self.current_problem_id}
        for ep in self.recent_episodes:
            if ep.problem_id:
                ids.add(ep.problem_id)
        return [i for i in ids if i]

    def get_weak_tags(self) -> list[str]:
        """从语义记忆中提取薄弱知识点"""
        if self.semantic_memory:
            return self.semantic_memory.get_weak_concepts(threshold=0.5)
        return []

    def get_understanding_quality(self) -> str | None:
        """从 Review export 中提取整体理解质量（最差的那个）"""
        if self.source != RecommendSource.REVIEW:
            return None
        summary = self.session_export.get("understanding_summary", {})
        if not summary:
            return None
        order = ["not_understood", "partial", "understood"]
        worst = min(summary.values(), key=lambda q: order.index(q) if q in order else 2)
        return worst


# =============================================================================
# 推荐结果
# =============================================================================

@dataclass
class Recommendation:
    """
    RecommendManager 返回给调用方的推荐结果。

    调用方（API 层 / Progress 调度器）根据 type 决定后续行动：
      SIMILAR/EASIER/HARDER → 展示 problem 给学生，可以直接开新 TutorSession
      REVIEW_CONCEPT        → 展示知识卡片摘要，引导学生自行复习
      RETRY_WITH_METHOD     → 调用 ReviewChatManager 或新 TutorSession
      REST                  → 展示鼓励语，不开新会话
    """
    type: RecommendationType
    explanation: str                      # 对学生的自然语言说明（含推荐理由）

    problem: ProblemContext | None = None           # 推荐的题目
    concept_to_review: str | None = None            # REVIEW_CONCEPT 时的知识点 ID
    retry_method: str | None = None                 # RETRY_WITH_METHOD 时的目标方法

    # REVIEW_CONCEPT 时附带的知识卡摘要（CardRetriever 检索）
    concept_card_summaries: list[str] = field(default_factory=list)

    # 调试 / 日志
    query_used: ProblemQuery | None = None
    fallback_used: bool = False                     # True 表示题库返回空，用了降级逻辑

    def to_display(self) -> dict[str, Any]:
        """结构化展示给前端"""
        result: dict[str, Any] = {
            "type": self.type.value,
            "explanation": self.explanation,
        }
        if self.problem:
            result["problem_id"] = self.problem.problem_id
            result["problem_preview"] = self.problem.problem[:100]
            result["chapter"] = self.problem.chapter
        if self.concept_to_review:
            result["concept_to_review"] = self.concept_to_review
        if self.concept_card_summaries:
            result["concept_card_summaries"] = self.concept_card_summaries
        if self.retry_method:
            result["retry_method"] = self.retry_method
        return result
