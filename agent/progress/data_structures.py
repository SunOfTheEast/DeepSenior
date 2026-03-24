#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Progress 模块数据结构

DailyTask      — 今日单条任务
TaskPlan       — 今日完整任务计划（含 LLM 生成的自然语言说明）
ProgressSummary — 一段时间内的学习进展报告
DecayRecord    — Ebbinghaus 衰减快照（供调试和存档）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from agent.recommend.data_structures import ProblemQuery


# =============================================================================
# Enums
# =============================================================================

class TaskType(str, Enum):
    REVIEW        = "review"         # Ebbinghaus 触发的复习
    PRACTICE      = "practice"       # 同类型巩固
    NEW_CONCEPT   = "new_concept"    # 推进到新知识点
    CHALLENGE     = "challenge"      # 升难度挑战
    WEAK_CONCEPT  = "weak_concept"   # 针对语义记忆中的薄弱点


class TaskPriority(int, Enum):
    URGENT     = 1   # 必须今天做（Ebbinghaus 临界）
    IMPORTANT  = 2   # 建议今天做
    SUGGESTED  = 3   # 有时间可做


# =============================================================================
# 核心数据结构
# =============================================================================

@dataclass
class DailyTask:
    """今日学习计划中的单条任务"""
    task_id: str
    task_type: TaskType
    priority: TaskPriority
    description: str           # 自然语言说明（LLM 生成）
    reason: str                # 为什么推荐（结合记忆，LLM 生成）

    # 执行载体（三选一，其余为 None）
    concept_id: str | None = None          # REVIEW / WEAK_CONCEPT 时：目标知识点
    problem_query: ProblemQuery | None = None  # 需要题库时：查询条件（占位）
    problem_id: str | None = None          # 若有具体题目 ID

    estimated_minutes: int = 15
    decay_score: float | None = None       # 当前 Ebbinghaus 保留率（0~1）


@dataclass
class TaskPlan:
    """今日完整任务计划"""
    student_id: str
    generated_at: datetime
    tasks: list[DailyTask]
    plan_summary: str          # LLM 生成的今日计划概述
    estimated_total_minutes: int = 0

    def urgent_tasks(self) -> list[DailyTask]:
        return [t for t in self.tasks if t.priority == TaskPriority.URGENT]

    def to_display(self) -> dict[str, Any]:
        return {
            "summary": self.plan_summary,
            "estimated_minutes": self.estimated_total_minutes,
            "tasks": [
                {
                    "type": t.task_type.value,
                    "priority": t.priority.value,
                    "description": t.description,
                    "reason": t.reason,
                    "minutes": t.estimated_minutes,
                }
                for t in self.tasks
            ],
        }


@dataclass
class ProgressSummary:
    """一段时间的学习进展报告（LLM 生成的定性 + 定量混合）"""
    student_id: str
    generated_at: datetime
    period: str                      # "week" | "month" | "all_time"

    # 定量统计（从 EpisodicMemory 聚合）
    sessions_completed: int = 0
    problems_solved: int = 0
    total_hints_given: int = 0
    avg_hints_per_session: float = 0.0
    methods_used: list[str] = field(default_factory=list)

    # 定性叙述（LLM 生成）
    narrative: str = ""              # "这周你..."
    strengths: list[str] = field(default_factory=list)
    areas_to_improve: list[str] = field(default_factory=list)
    notable_achievements: list[str] = field(default_factory=list)
    suggested_focus: str = ""        # 下一阶段建议聚焦的方向


@dataclass
class DecayRecord:
    """单个知识点的 Ebbinghaus 衰减快照（调试 / 存档用）"""
    concept_id: str
    original_level: float      # 未衰减的 level
    retention: float           # 当前保留率
    elapsed_days: float
    stability: float           # 稳定性（天数）
    needs_review: bool
    next_review_at: datetime
