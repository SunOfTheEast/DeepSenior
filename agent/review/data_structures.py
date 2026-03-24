#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Review (复盘) 模块数据结构

学生完成 Tutor 会话后进入复盘模式：
- 错误回放：结合 Tutor 会话的具体失误，看新方法如何处理同一步骤
- 探索解法：列举/演示其他方法
- 理解验证：展示解法后主动提问，确认是否真正理解而非走马观花
  （两阶段：概念理解 + 迁移验证）
- 对比分析：方法优劣对比
- 重新挑战：用新方法重做

核心实体：
  ReviewIntent      — 意图类型（含错误回放和理解验证）
  ErrorSnapshot     — 从 Tutor 会话提取的具体失误快照
  UnderstandingCheck — 一次理解验证的 Q&A 记录
  MethodInfo        — 解法元数据
  SolvedMethod      — 解法完整演示
  ReviewSession     — 单次复盘会话状态
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from agent.tutor.data_structures import ProblemContext


# =============================================================================
# Enums
# =============================================================================

class ReviewIntent(str, Enum):
    ENUMERATE_METHODS    = "enumerate_methods"    # "有哪些方法？"
    SHOW_SOLUTION        = "show_solution"         # "用X方法怎么做？"
    COMPARE_METHODS      = "compare_methods"       # "哪种方法更好？"
    EXPLAIN_CONCEPT      = "explain_concept"       # "为什么X成立？"
    RETRY_WITH_METHOD    = "retry_with_method"     # "我想用X方法重新做"
    REPLAY_ERRORS        = "replay_errors"         # "我哪里做错了？"
    VERIFY_UNDERSTANDING = "verify_understanding"  # 理解验证的回答（内部路由）
    GENERAL              = "general"               # 其他


class ReviewAction(str, Enum):
    """LLM 路由动作（替代 ReviewIntent + 信号列表）"""
    REPLAY_ERRORS          = "replay_errors"
    ENUMERATE_METHODS      = "enumerate_methods"
    SHOW_SOLUTION          = "show_solution"
    COMPARE_METHODS        = "compare_methods"
    EXPLAIN_CONCEPT        = "explain_concept"
    RETRY_WITH_METHOD      = "retry_with_method"
    ANSWER_VERIFICATION    = "answer_verification"
    INTERRUPT_VERIFICATION = "interrupt_verification"
    GENERAL                = "general"


class UnderstandingQuality(str, Enum):
    UNDERSTOOD     = "understood"      # 准确抓住关键
    PARTIAL        = "partial"         # 有理解但不完整
    NOT_UNDERSTOOD = "not_understood"  # 没有抓住核心


# =============================================================================
# 错误快照（来自 Tutor 会话）
# =============================================================================

@dataclass
class ErrorSnapshot:
    """
    从 Tutor 会话提取的单条失误记录。
    用于复盘时的错误回放：对比学生原来的错误和新方法的处理方式。
    """
    error_type: str               # ErrorType.value
    error_location: str           # 出错位置描述
    error_description: str        # 错误内容
    correction_note: str          # 正确做法


@dataclass
class StrugglePoint:
    """
    Tutor 会话中学生需要最高 hint_level 的 checkpoint。
    标记学生最吃力的具体步骤。
    """
    checkpoint_index: int
    description: str              # checkpoint 描述（出现了什么难点）
    guiding_question: str         # 当时的引导问题
    hint_level_reached: int       # 达到的最高提示级别（2 or 3）
    was_passed: bool              # 最终是否通过


# =============================================================================
# 理解验证记录
# =============================================================================

@dataclass
class UnderstandingCheck:
    """
    展示解法后的主动理解验证记录。
    追踪"学生是否真的理解了这种方法的核心"。
    """
    method_name: str
    question_asked: str           # 第一阶段：概念理解问题
    key_points: str               # 第一阶段：期望学生提到的关键点（LLM 内部评估用）
    student_response: str = ""    # 第一阶段：学生回答
    quality: UnderstandingQuality = UnderstandingQuality.NOT_UNDERSTOOD
    tutor_feedback: str = ""      # 第一阶段：系统反馈

    # 第二阶段：迁移验证（微变式）
    transfer_question: str = ""
    transfer_key_points: str = ""
    transfer_response: str = ""
    transfer_quality: UnderstandingQuality | None = None
    transfer_feedback: str = ""

    def final_quality(self) -> UnderstandingQuality:
        """
        两阶段融合后的最终质量：
          - 任一阶段 not_understood → not_understood
          - 两阶段都 understood      → understood
          - 其他组合                 → partial
        """
        if self.transfer_quality is None:
            return self.quality
        if (
            self.quality == UnderstandingQuality.NOT_UNDERSTOOD
            or self.transfer_quality == UnderstandingQuality.NOT_UNDERSTOOD
        ):
            return UnderstandingQuality.NOT_UNDERSTOOD
        if (
            self.quality == UnderstandingQuality.UNDERSTOOD
            and self.transfer_quality == UnderstandingQuality.UNDERSTOOD
        ):
            return UnderstandingQuality.UNDERSTOOD
        return UnderstandingQuality.PARTIAL


# =============================================================================
# Method-level data
# =============================================================================

@dataclass
class MethodInfo:
    """一种解法的元数据（轻量，用于列举展示）"""
    name: str
    summary: str
    difficulty: str               # "基础" / "中等" / "进阶"
    prerequisites: list[str]
    is_standard: bool             # 是否为知识卡片中的通性通法

    def to_display(self) -> str:
        prereq = "、".join(self.prerequisites) if self.prerequisites else "无"
        tag = "（通性通法）" if self.is_standard else "（替代方法）"
        return (
            f"**{self.name}** {tag}\n"
            f"  思路：{self.summary}\n"
            f"  难度：{self.difficulty}　前置：{prereq}"
        )


@dataclass
class SolvedMethod:
    """一种解法的完整演示"""
    method_name: str
    steps: list[str]
    key_insight: str
    comparison_note: str

    def to_display(self) -> str:
        steps_text = "\n".join(f"**Step {i+1}**：{s}" for i, s in enumerate(self.steps))
        return (
            f'### 用「{self.method_name}」解题\n\n'
            f"**核心思路**：{self.key_insight}\n\n"
            f"{steps_text}\n\n"
            f"**方法特点**：{self.comparison_note}"
        )


# =============================================================================
# Session
# =============================================================================

@dataclass
class ReviewSession:
    """单次复盘会话的完整状态"""
    session_id: str
    problem_context: ProblemContext
    original_tutor_session_id: str | None
    student_method_used: str
    student_id: str | None = None

    interaction_history: list[dict[str, Any]] = field(default_factory=list)
    discovered_methods: list[MethodInfo] = field(default_factory=list)
    solved_demonstrations: dict[str, SolvedMethod] = field(default_factory=dict)

    # 错误回放数据（从 Tutor export 提取）
    error_snapshots: list[ErrorSnapshot] = field(default_factory=list)
    struggle_points: list[StrugglePoint] = field(default_factory=list)

    # 理解验证记录：method_name -> UnderstandingCheck
    understanding_checks: dict[str, UnderstandingCheck] = field(default_factory=dict)

    # 待验证状态：展示解法后设置，下一条消息将路由到验证流程
    pending_verification: str | None = None     # method_name waiting for verification
    pending_verification_stage: str = "concept"  # concept | transfer
    _pending_question: str = ""                  # 当前待回答的问题文本
    _pending_key_points: str = ""               # 内部：验证问题的期望要点

    created_at: datetime = field(default_factory=datetime.utcnow)
    status: str = "active"

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def add_interaction(self, role: str, content: str, meta: dict | None = None) -> None:
        self.interaction_history.append({
            "role": role,
            "content": content,
            "meta": meta or {},
        })

    def get_recent_history(self, n: int = 6) -> list[dict]:
        return self.interaction_history[-n:]

    def get_known_method_names(self) -> list[str]:
        return [m.name for m in self.discovered_methods]

    def get_solved_method_names(self) -> list[str]:
        return list(self.solved_demonstrations.keys())

    def has_errors(self) -> bool:
        return bool(self.error_snapshots or self.struggle_points)

    def get_understanding_summary(self) -> dict[str, str]:
        """返回各方法的理解质量，供记忆导出使用"""
        return {
            method: check.final_quality().value
            for method, check in self.understanding_checks.items()
        }

    def proactive_opener(self) -> str | None:
        """
        生成主动开场白（基于持久化的 Tutor 会话数据）。
        若有挣扎点则提示；否则返回 None（使用默认欢迎语）。
        """
        parts = []
        if self.struggle_points:
            sp = self.struggle_points[0]
            parts.append(
                f"你在「{sp.description}」这一步花了最多精力"
                f"（提示达到了第 {sp.hint_level_reached} 级）"
            )
        if self.error_snapshots:
            es = self.error_snapshots[0]
            parts.append(f"出现了{es.error_type}类型的错误：{es.error_location}")

        if not parts:
            return None

        return (
            "根据你刚才的辅导记录：" + "；".join(parts) + "。\n"
            "我们可以先回放一下这些地方，看看其他方法怎么处理——或者你想先探索有哪些解法？"
        )
