#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Memory 模块数据结构

分层设计：
  EpisodicMemory  — 单次会话的紧凑快照（事实性，不含原始对话）
  SemanticMemory  — 学生的长期语义画像（由 MemoryDistillerAgent 从情节中提炼）
  MasteryRecord   — 单个知识点的掌握状态（供 Progress 模块的遗忘曲线使用）
  MemoryUpdate    — MemoryDistillerAgent 输出的结构化更新指令
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


# =============================================================================
# Enums
# =============================================================================

class SessionSource(str, Enum):
    TUTOR  = "tutor"    # 来自 TutorManager
    REVIEW = "review"   # 来自 ReviewChatManager


class MethodObservation(str, Enum):
    USED_SUCCESSFULLY  = "used_successfully"   # 主动使用且成功
    AVOIDED            = "avoided"             # 有机会用但回避了
    ATTEMPTED_FAILED   = "attempted_failed"    # 尝试但失败
    EXPLORED_IN_REVIEW = "explored_in_review"  # 复盘时探索（未实际做题）


# =============================================================================
# 情节记忆（Episodic）
# =============================================================================

@dataclass
class EpisodicMemory:
    """
    单次会话的紧凑快照。

    只保留对长期分析有用的聚合信息，不保存完整对话文本。
    原始对话由 TutorSession / ReviewSession 自行存储（如有需要）。
    """
    memory_id: str
    student_id: str
    session_id: str
    source: SessionSource
    created_at: datetime

    # 题目元信息
    problem_id: str
    chapter: str
    tags: list[str]                  # 来自 knowledge_cards 的知识点标签

    # 会话结果
    outcome: str                     # "solved" | "gave_up" | "in_progress" | "explored"
    hints_given: int
    checkpoints_completed: int
    total_checkpoints: int
    error_types: list[str]           # ErrorType.value 列表，记录出现过的错误类型
    attempts: int

    # 方法使用
    methods_used: list[str]          # 实际使用的方法（含替代方法）
    alternative_flagged: bool        # 是否用了高级替代方法但被标记
    used_alternative_method: bool
    solution_id: str | None = None   # standard / alt::<method_key>
    solution_method: str | None = None
    solution_tags: list[str] = field(default_factory=list)  # solution 分叉对应的知识点索引
    method_slot_matched: str | None = None    # RAG MethodRouter 匹配到的标准化 slot_id
    needs_solution_card_audit: bool = False
    solution_card_audit_reason: str | None = None
    deep_dive_count: int = 0
    deep_dive_topics: list[str] = field(default_factory=list)
    deep_dive_understanding: dict[str, str] = field(default_factory=dict)
    deferred_deep_dive_tasks: list[dict[str, Any]] = field(default_factory=list)

    # 定性摘要（由 MemoryDistiller 从原始对话 L0 提炼）
    session_narrative: str = ""

    # Review 专属（source==REVIEW 时有值）
    methods_explored: list[str] = field(default_factory=list)
    retry_triggered: bool = False
    retry_method: str | None = None
    understanding_summary: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items()}
        d["source"] = self.source.value
        d["created_at"] = self.created_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EpisodicMemory":
        d = dict(d)
        d["source"] = SessionSource(d["source"])
        d["created_at"] = datetime.fromisoformat(d["created_at"])
        d.setdefault("session_narrative", "")
        return cls(**d)


# =============================================================================
# 掌握记录（Mastery）
# =============================================================================

@dataclass
class MasteryRecord:
    """
    单个知识点的掌握状态。

    Progress 模块基于此实现遗忘曲线（Ebbinghaus）。
    memory 模块负责更新 level 和 last_practiced；Progress 负责读取和调度复习。
    """
    concept_id: str
    level: float                     # [0.0, 1.0]，当前掌握程度
    last_practiced: datetime
    next_review_at: datetime | None = None  # 由 Progress 预计算的下次复习时间点（可为空）
    practice_count: int = 0
    error_count: int = 0
    consecutive_correct: int = 0     # 连续答对次数（用于判断是否已稳定掌握）

    def to_dict(self) -> dict[str, Any]:
        return {
            "concept_id": self.concept_id,
            "level": self.level,
            "last_practiced": self.last_practiced.isoformat(),
            "next_review_at": self.next_review_at.isoformat() if self.next_review_at else None,
            "practice_count": self.practice_count,
            "error_count": self.error_count,
            "consecutive_correct": self.consecutive_correct,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MasteryRecord":
        d = dict(d)
        d["last_practiced"] = datetime.fromisoformat(d["last_practiced"])
        if d.get("next_review_at"):
            d["next_review_at"] = datetime.fromisoformat(d["next_review_at"])
        else:
            d["next_review_at"] = None
        return cls(**d)


@dataclass
class SolutionMasteryRecord:
    """
    单个解法分叉（solution）的掌握状态。

    用于补充“同题不同法”的学习轨迹：既记录方法熟练度，也作为
    solution-level -> concept-level 同步的依据。
    """
    solution_id: str
    question_id: str
    method_name: str
    level: float
    first_seen_at: datetime
    last_used_at: datetime
    use_count: int = 0
    linked_concepts: list[str] = field(default_factory=list)
    last_outcome: str = ""
    index_status: str = "ready"  # pending | ready | rejected

    def to_dict(self) -> dict[str, Any]:
        return {
            "solution_id": self.solution_id,
            "question_id": self.question_id,
            "method_name": self.method_name,
            "level": self.level,
            "first_seen_at": self.first_seen_at.isoformat(),
            "last_used_at": self.last_used_at.isoformat(),
            "use_count": self.use_count,
            "linked_concepts": self.linked_concepts,
            "last_outcome": self.last_outcome,
            "index_status": self.index_status,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SolutionMasteryRecord":
        d = dict(d)
        d["first_seen_at"] = datetime.fromisoformat(d["first_seen_at"])
        d["last_used_at"] = datetime.fromisoformat(d["last_used_at"])
        d.setdefault("index_status", "ready")
        return cls(**d)


@dataclass
class MethodSlotMastery:
    """
    单个 method_slot 的掌握统计（双层 mastery 的方法维度）。

    与 MasteryRecord（concept 维度）并行，用于追踪学生对特定解题方法的熟练度。
    slot_id 来自 MethodCatalog 中的标准化 slot（如 ellipse_parametric）。
    """
    slot_id: str
    use_count: int = 0
    success_count: int = 0
    last_used_at: datetime | None = None

    @property
    def success_rate(self) -> float:
        return self.success_count / self.use_count if self.use_count > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "use_count": self.use_count,
            "success_count": self.success_count,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MethodSlotMastery":
        d = dict(d)
        lu = d.get("last_used_at")
        d["last_used_at"] = datetime.fromisoformat(lu) if lu else None
        return cls(**d)


# =============================================================================
# 语义记忆（Semantic）
# =============================================================================

@dataclass
class SemanticMemory:
    """
    学生的长期语义画像。

    由 MemoryDistillerAgent 在每次会话后增量更新。
    Progress 模块消费 concept_mastery；
    Tutor / Review 模块消费偏好和弱点来个性化引导。
    """
    student_id: str
    last_updated: datetime

    # 知识点掌握度
    concept_mastery: dict[str, MasteryRecord] = field(default_factory=dict)
    # key: concept_id（与 KnowledgeCard.card_id 对应）
    solution_mastery: dict[str, SolutionMasteryRecord] = field(default_factory=dict)
    # key: solution_id（question::standard / question::alt::method）
    slot_mastery: dict[str, MethodSlotMastery] = field(default_factory=dict)
    # key: slot_id（来自 MethodCatalog 的标准化方法标识）

    # 方法偏好
    method_observations: dict[str, list[MethodObservation]] = field(default_factory=dict)
    # key: method_name, value: 历次观测记录列表（保留最近 20 条）

    # 错误模式
    persistent_errors: dict[str, int] = field(default_factory=dict)
    # key: ErrorType.value, value: 累计出现次数

    # 学习风格
    total_sessions: int = 0
    total_hints_given: int = 0
    total_problems_solved: int = 0
    avg_hints_per_session: float = 0.0
    persistence_events: int = 0      # 卡住后继续坚持的次数（来自 FRUSTRATED 后继续的会话）

    # 自由文本摘要（由 LLM 生成，供其他 LLM 快速读取）
    profile_summary: str = ""        # 一段话描述学生画像
    recent_focus: str = ""           # 最近3-5次会话的主题摘要
    pending_audit_tasks: list[dict[str, Any]] = field(default_factory=list)
    # 异步审计任务（例如：替代方法的 solution knowledge-card 索引补齐）

    def get_preferred_methods(self, min_success: int = 2) -> list[str]:
        """返回至少成功使用过 min_success 次的方法"""
        return [
            m for m, obs in self.method_observations.items()
            if obs.count(MethodObservation.USED_SUCCESSFULLY) >= min_success
        ]

    def get_avoided_methods(self, min_avoid: int = 2) -> list[str]:
        return [
            m for m, obs in self.method_observations.items()
            if obs.count(MethodObservation.AVOIDED) >= min_avoid
        ]

    def get_weak_concepts(self, threshold: float = 0.5) -> list[str]:
        return [
            cid for cid, rec in self.concept_mastery.items()
            if rec.level < threshold
        ]

    def get_weak_slots(self, min_uses: int = 2, threshold: float = 0.5) -> list[MethodSlotMastery]:
        """返回使用次数 >= min_uses 且正确率 < threshold 的薄弱方法 slot，按正确率升序。"""
        weak = [
            m for m in self.slot_mastery.values()
            if m.use_count >= min_uses and m.success_rate < threshold
        ]
        weak.sort(key=lambda m: m.success_rate)
        return weak

    def get_strong_slots(self, min_uses: int = 2, threshold: float = 0.7) -> list[MethodSlotMastery]:
        """返回使用次数 >= min_uses 且正确率 >= threshold 的擅长方法 slot。"""
        return [
            m for m in self.slot_mastery.values()
            if m.use_count >= min_uses and m.success_rate >= threshold
        ]

    # -------------------------------------------------------------------------
    # 任务定制投影（各 Agent 按需消费，替代全量 to_context_string）
    # -------------------------------------------------------------------------

    def to_distill_snapshot(
        self, tags: list[str], max_methods: int = 3,
    ) -> str:
        """MemoryDistiller 专用：只含与本次会话 tags 相关的画像切面。"""
        parts: list[str] = []
        if self.profile_summary:
            parts.append(f"画像：{self.profile_summary[:120]}")
        if self.recent_focus:
            parts.append(f"近期重点：{self.recent_focus[:60]}")
        preferred = self.get_preferred_methods()[:max_methods]
        if preferred:
            parts.append(f"擅长方法：{', '.join(preferred)}")
        avoided = self.get_avoided_methods()[:max_methods]
        if avoided:
            parts.append(f"倾向回避：{', '.join(avoided)}")
        top_errors = sorted(self.persistent_errors.items(), key=lambda x: -x[1])[:3]
        if top_errors:
            parts.append(f"高频错误：{', '.join(f'{e}({n}次)' for e, n in top_errors)}")
        return "\n".join(parts) if parts else "（新学生，暂无画像）"

    def to_progress_snapshot(
        self, max_weak: int = 5, max_errors: int = 3,
    ) -> str:
        """Progress/TaskPlanner 专用：弱点 + 错误模式 + 学习统计。"""
        parts: list[str] = []
        if self.profile_summary:
            parts.append(f"画像：{self.profile_summary[:120]}")
        weak = self.get_weak_concepts()[:max_weak]
        if weak:
            parts.append(f"薄弱知识点：{', '.join(weak)}")
        weak_slots = self.get_weak_slots()[:3]
        if weak_slots:
            parts.append(f"薄弱方法：{', '.join(f'{s.slot_id}({s.success_rate:.0%})' for s in weak_slots)}")
        top_errors = sorted(self.persistent_errors.items(), key=lambda x: -x[1])[:max_errors]
        if top_errors:
            parts.append(f"高频错误：{', '.join(f'{e}({n}次)' for e, n in top_errors)}")
        parts.append(
            f"总会话：{self.total_sessions}，已解题：{self.total_problems_solved}，"
            f"坚持事件：{self.persistence_events}"
        )
        return "\n".join(parts) if parts else "（新学生，暂无画像）"

    def to_recommend_snapshot(
        self, current_tags: list[str], max_weak: int = 4,
    ) -> str:
        """Recommend 专用：当前题相关弱点 + 偏好。"""
        parts: list[str] = []
        if self.profile_summary:
            parts.append(f"画像：{self.profile_summary[:100]}")
        weak = self.get_weak_concepts()
        related = [w for w in weak if w in current_tags]
        others = [w for w in weak if w not in current_tags]
        weak_display = (related + others)[:max_weak]
        if weak_display:
            parts.append(f"薄弱知识点：{', '.join(weak_display)}")
        if self.recent_focus:
            parts.append(f"近期重点：{self.recent_focus[:60]}")
        preferred = self.get_preferred_methods()[:3]
        if preferred:
            parts.append(f"擅长方法：{', '.join(preferred)}")
        top_errors = sorted(self.persistent_errors.items(), key=lambda x: -x[1])[:2]
        if top_errors:
            parts.append(f"高频错误：{', '.join(f'{e}({n}次)' for e, n in top_errors)}")
        return "\n".join(parts) if parts else "（新学生，暂无画像）"

    def to_context_string(self) -> str:
        """
        生成供 LLM prompt 使用的上下文摘要字符串。
        Tutor / Review 模块在开始会话时调用此方法。
        """
        parts = []
        if self.profile_summary:
            parts.append(f"学生画像：{self.profile_summary}")
        if self.recent_focus:
            parts.append(f"近期学习重点：{self.recent_focus}")
        preferred = self.get_preferred_methods()
        if preferred:
            parts.append(f"擅长方法：{', '.join(preferred)}")
        avoided = self.get_avoided_methods()
        if avoided:
            parts.append(f"倾向回避：{', '.join(avoided)}")
        weak = self.get_weak_concepts()
        if weak:
            parts.append(f"薄弱知识点：{', '.join(weak)}")
        if self.solution_mastery:
            weak_solutions = sorted(
                self.solution_mastery.values(),
                key=lambda x: x.level,
            )[:3]
            if weak_solutions:
                sol_text = ", ".join(
                    f"{s.method_name}({s.level:.2f})"
                    for s in weak_solutions
                )
                parts.append(f"待巩固解法：{sol_text}")
        if self.pending_audit_tasks:
            pending = sum(1 for t in self.pending_audit_tasks if t.get("status") != "done")
            if pending:
                parts.append(f"待审计任务：{pending}条")
        top_errors = sorted(self.persistent_errors.items(), key=lambda x: -x[1])[:3]
        if top_errors:
            err_str = ', '.join(f"{e}({n}次)" for e, n in top_errors)
            parts.append(f"常见错误：{err_str}")
        return "\n".join(parts) if parts else "（暂无长期记忆）"

    def to_dict(self) -> dict[str, Any]:
        return {
            "student_id": self.student_id,
            "last_updated": self.last_updated.isoformat(),
            "concept_mastery": {k: v.to_dict() for k, v in self.concept_mastery.items()},
            "solution_mastery": {k: v.to_dict() for k, v in self.solution_mastery.items()},
            "slot_mastery": {k: v.to_dict() for k, v in self.slot_mastery.items()},
            "method_observations": {
                k: [o.value for o in v] for k, v in self.method_observations.items()
            },
            "persistent_errors": self.persistent_errors,
            "total_sessions": self.total_sessions,
            "total_hints_given": self.total_hints_given,
            "total_problems_solved": self.total_problems_solved,
            "avg_hints_per_session": self.avg_hints_per_session,
            "persistence_events": self.persistence_events,
            "profile_summary": self.profile_summary,
            "recent_focus": self.recent_focus,
            "pending_audit_tasks": self.pending_audit_tasks,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SemanticMemory":
        d = dict(d)
        d["last_updated"] = datetime.fromisoformat(d["last_updated"])
        d["concept_mastery"] = {
            k: MasteryRecord.from_dict(v)
            for k, v in d.get("concept_mastery", {}).items()
        }
        d["solution_mastery"] = {
            k: SolutionMasteryRecord.from_dict(v)
            for k, v in d.get("solution_mastery", {}).items()
        }
        d["slot_mastery"] = {
            k: MethodSlotMastery.from_dict(v)
            for k, v in d.get("slot_mastery", {}).items()
        }
        d["method_observations"] = {
            k: [MethodObservation(o) for o in v]
            for k, v in d.get("method_observations", {}).items()
        }
        d["pending_audit_tasks"] = list(d.get("pending_audit_tasks", []))
        return cls(**d)

    @classmethod
    def new(cls, student_id: str) -> "SemanticMemory":
        return cls(student_id=student_id, last_updated=datetime.utcnow())


# =============================================================================
# 摘要层（Digest / L1.5）
# =============================================================================

@dataclass
class MemoryDigest:
    """
    聚合摘要：多条 episode 压缩为一段自然语言摘要。

    两种类型：
      - weekly: 按周聚合，period_key 格式 "2026-W14"
      - chapter: 按章节聚合，period_key 为章节名（如 "解析几何"）
    """
    digest_id: str
    student_id: str
    digest_type: str             # "weekly" | "chapter"
    period_key: str              # "2026-W14" | "解析几何"
    created_at: datetime
    episode_ids: list[str]       # 关联的 memory_id 列表
    summary: str                 # LLM 生成的自然语言摘要
    stats: dict[str, Any] = field(default_factory=dict)
    tags_covered: list[str] = field(default_factory=list)
    methods_used: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "digest_id": self.digest_id,
            "student_id": self.student_id,
            "digest_type": self.digest_type,
            "period_key": self.period_key,
            "created_at": self.created_at.isoformat(),
            "episode_ids": self.episode_ids,
            "summary": self.summary,
            "stats": self.stats,
            "tags_covered": self.tags_covered,
            "methods_used": self.methods_used,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MemoryDigest":
        d = dict(d)
        d["created_at"] = datetime.fromisoformat(d["created_at"])
        return cls(**d)


# =============================================================================
# 更新指令（MemoryDistillerAgent 的输出）
# =============================================================================

@dataclass
class ConceptUpdate:
    concept_id: str
    delta: float          # 正数提升，负数下降，范围 [-0.3, +0.3]
    reason: str
    consecutive_correct_reset: bool = False  # 答错时重置连续答对


@dataclass
class MemoryUpdate:
    """MemoryDistillerAgent 对单次会话的提炼结果"""
    concept_updates: list[ConceptUpdate] = field(default_factory=list)
    method_observations: dict[str, MethodObservation] = field(default_factory=dict)
    # key: method_name, value: 这次会话的观测
    new_error_types: list[str] = field(default_factory=list)
    profile_summary: str | None = None    # 如有更新则非 None
    recent_focus: str | None = None
    persistence_event: bool = False       # 这次会话是否出现了坚持克服困难的事件
    session_narrative: str = ""           # 从原始对话提炼的定性摘要（情绪/风格/卡点质感）
