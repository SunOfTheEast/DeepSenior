#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
TutorAgent 核心数据结构

双模式辅导系统：苏格拉底引导（A tutor）+ 批改判题（B tutor）
Router 根据状态机在两者之间切换。

题目上下文由 ProblemContext（JSON 库驱动）提供，包含题目、答案和关联知识卡片。
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


# =============================================================================
# Problem Context（题库 JSON 驱动结构）
# =============================================================================

@dataclass
class KnowledgeCard:
    """
    知识卡片（与题目通过索引关联）

    每张卡片对应一个知识点，包含通性通法、提示和易错点。
    Grader 用它做方法对齐检测，Planner 用它生成 outcome-focused checkpoints，
    PathEvaluator 用它判断替代解法的教学有效性。
    """
    card_id: str
    title: str
    general_methods: list[str]    # 通性通法（用于替代解法检测）
    hints: list[str]              # 提示
    common_mistakes: list[str]    # 易错点
    prerequisite_ids: list[str] = field(default_factory=list)


@dataclass
class ProblemContext:
    """
    题目上下文（题库 JSON 的 Python 表示）

    作为整个 TutorSession 的驱动结构。
    Agents 可见：题目文本、答案（仅内部使用）、知识卡片。
    答案绝对不能直接传给学生端。
    """
    problem_id: str
    problem: str
    answer: str                         # 标准答案（agents 内部用于事实核查）
    knowledge_cards: list[KnowledgeCard]
    difficulty: int = 1                 # 1-5
    chapter: str = ""
    tags: list[str] = field(default_factory=list)
    solution_slice_hints_by_card: dict[str, list[str]] = field(default_factory=dict)

    # -------------------------------------------------------------------------
    # 便捷聚合方法（供 agent prompt 格式化使用）
    # -------------------------------------------------------------------------

    def get_methods_summary(self) -> str:
        """所有知识卡片的通性通法汇总（去重）"""
        methods: list[str] = []
        for kc in self.knowledge_cards:
            methods.extend(kc.general_methods)
        return "、".join(dict.fromkeys(methods))  # 保序去重

    def get_hints_summary(self) -> str:
        hints: list[str] = []
        for kc in self.knowledge_cards:
            hints.extend(kc.hints)
        return "\n".join(f"- {h}" for h in hints)

    def get_common_mistakes_summary(self) -> str:
        mistakes: list[str] = []
        for kc in self.knowledge_cards:
            mistakes.extend(kc.common_mistakes)
        return "\n".join(f"- {m}" for m in mistakes)

    def get_card_titles(self) -> str:
        return "、".join(kc.title for kc in self.knowledge_cards)

    # -------------------------------------------------------------------------
    # 预算化聚合方法（供 Context Governance Layer 消费）
    # -------------------------------------------------------------------------

    def get_methods_for_llm(
        self,
        max_methods: int = 4,
        max_chars: int = 120,
    ) -> list[str]:
        """去重后取 top-k 方法，总长不超 max_chars。"""
        seen: dict[str, None] = {}
        for kc in self.knowledge_cards:
            for m in kc.general_methods:
                seen.setdefault(m, None)
        result: list[str] = []
        total = 0
        for m in seen:
            if len(result) >= max_methods:
                break
            if total + len(m) > max_chars:
                break
            result.append(m)
            total += len(m)
        return result

    def get_hints_for_llm(
        self,
        *,
        max_cards: int = 2,
        max_items_per_card: int = 2,
        max_total_items: int = 4,
        max_chars: int = 240,
        include_slice_hints: bool = False,
    ) -> list[str]:
        """按卡片优先级取 top-k 提示，可选包含 slice 级提示。"""
        result: list[str] = []
        total_chars = 0
        for kc in self.knowledge_cards[:max_cards]:
            card_count = 0
            for h in kc.hints:
                if len(result) >= max_total_items:
                    break
                if total_chars + len(h) > max_chars:
                    break
                if card_count >= max_items_per_card:
                    break
                result.append(h)
                total_chars += len(h)
                card_count += 1
            if include_slice_hints:
                for h in self.solution_slice_hints_by_card.get(kc.card_id, []):
                    if len(result) >= max_total_items:
                        break
                    if total_chars + len(h) > max_chars:
                        break
                    if card_count >= max_items_per_card:
                        break
                    result.append(h)
                    total_chars += len(h)
                    card_count += 1
            if len(result) >= max_total_items:
                break
        return result

    def get_common_mistakes_for_llm(
        self,
        *,
        max_cards: int = 2,
        max_items_per_card: int = 2,
        max_total_items: int = 4,
        max_chars: int = 240,
    ) -> list[str]:
        """按卡片优先级取 top-k 易错点。"""
        result: list[str] = []
        total_chars = 0
        for kc in self.knowledge_cards[:max_cards]:
            card_count = 0
            for m in kc.common_mistakes:
                if len(result) >= max_total_items:
                    break
                if total_chars + len(m) > max_chars:
                    break
                if card_count >= max_items_per_card:
                    break
                result.append(m)
                total_chars += len(m)
                card_count += 1
            if len(result) >= max_total_items:
                break
        return result

    def get_card_titles_for_llm(self, max_cards: int = 3) -> list[str]:
        """取 top-k 卡片标题。"""
        return [kc.title for kc in self.knowledge_cards[:max_cards]]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProblemContext":
        """
        从 JSON 字典构建（对接题库）。

        兼容两种输入：
          1) legacy：problem_id/problem/answer/knowledge_cards
          2) unified：question_id/stem/answer_schema/solutions/question_cards
        """
        if "problem_id" not in data and "question_id" in data:
            return cls.from_unified_question(data)

        cards = [
            KnowledgeCard(
                card_id=kc["card_id"],
                title=kc["title"],
                general_methods=kc.get("general_methods", []),
                hints=kc.get("hints", []),
                common_mistakes=kc.get("common_mistakes", []),
                prerequisite_ids=kc.get("prerequisite_ids", []),
            )
            for kc in data.get("knowledge_cards", [])
        ]
        return cls(
            problem_id=data["problem_id"],
            problem=data["problem"],
            answer=data["answer"],
            knowledge_cards=cards,
            difficulty=data.get("difficulty", 1),
            chapter=data.get("chapter", ""),
            tags=data.get("tags", []),
        )

    @classmethod
    def from_unified_question(
        cls,
        question: dict[str, Any],
        card_resolver: dict[str, Any] | None = None,
        solution_id: str | None = None,
    ) -> "ProblemContext":
        """
        将统一题目结构（question -> solution -> slice）适配成 Tutor 运行时结构。

        Args:
            question: 统一题目 JSON（含 question_id/stem/answer_schema/solutions）
            card_resolver: 可选知识卡索引（card_id -> card payload）
            solution_id: 指定使用哪条 solution 注入 slice 级提示；默认第一条
        """
        qid = str(question.get("question_id") or "").strip()
        stem = str(question.get("stem") or "").strip()
        if not qid or not stem:
            raise ValueError("Unified question missing required fields: question_id/stem")

        selected_solution = cls._pick_solution(question.get("solutions", []), solution_id)
        slice_hints_by_card = cls._collect_slice_hints_by_card(selected_solution)
        card_links = question.get("question_cards", []) or []

        cards: list[KnowledgeCard] = []
        seen_ids: set[str] = set()
        for link in card_links:
            card_id = str(link.get("card_id") or "").strip()
            if not card_id or card_id in seen_ids:
                continue
            seen_ids.add(card_id)
            payload = cls._resolve_card_payload(card_id, card_resolver, question)
            card = cls._build_knowledge_card(payload, card_id)
            cards.append(card)

        answer = cls._extract_answer_from_schema(question.get("answer_schema", {}))
        if not cards:
            # 保底：至少保留 tags，避免下游出现“无知识点”导致流程退化
            fallback_tags = [str(t) for t in question.get("tags", []) if str(t).strip()]
            cards = [
                KnowledgeCard(
                    card_id=t,
                    title=t,
                    general_methods=[],
                    hints=[],
                    common_mistakes=[],
                )
                for t in fallback_tags
            ]

        return cls(
            problem_id=qid,
            problem=stem,
            answer=answer,
            knowledge_cards=cards,
            difficulty=int(question.get("difficulty", 1) or 1),
            chapter=str(question.get("chapter", "") or ""),
            tags=[c.card_id for c in cards],
            solution_slice_hints_by_card=slice_hints_by_card,
        )

    @staticmethod
    def _pick_solution(
        solutions: list[dict[str, Any]] | None,
        solution_id: str | None,
    ) -> dict[str, Any] | None:
        if not solutions:
            return None
        if solution_id:
            for sol in solutions:
                if str(sol.get("solution_id")) == solution_id:
                    return sol
        return solutions[0]

    @staticmethod
    def _collect_slice_hints_by_card(solution: dict[str, Any] | None) -> dict[str, list[str]]:
        if not solution:
            return {}
        result: dict[str, list[str]] = {}
        for slice_data in solution.get("slices", []) or []:
            title = str(slice_data.get("title") or "步骤")
            hint_pack = slice_data.get("hint_pack", {}) or {}
            compact_hints: list[str] = []
            for level in ("l1", "l2", "l3"):
                text = str(hint_pack.get(level) or "").strip()
                if text:
                    compact_hints.append(f"[{title}|{level}] {text}")
            if not compact_hints:
                continue
            for link in slice_data.get("card_links", []) or []:
                card_id = str(link.get("card_id") or "").strip()
                if not card_id:
                    continue
                bucket = result.setdefault(card_id, [])
                for hint in compact_hints:
                    if hint not in bucket:
                        bucket.append(hint)
        return result

    @staticmethod
    def _resolve_card_payload(
        card_id: str,
        card_resolver: dict[str, Any] | None,
        question: dict[str, Any],
    ) -> dict[str, Any]:
        if card_resolver and card_id in card_resolver:
            payload = card_resolver[card_id]
            if isinstance(payload, dict):
                return payload

        for payload in question.get("knowledge_cards", []) or []:
            if str(payload.get("card_id") or "").strip() == card_id:
                return payload

        return {"card_id": card_id, "title": card_id}

    @staticmethod
    def _build_knowledge_card(payload: dict[str, Any], fallback_card_id: str) -> KnowledgeCard:
        card_id = str(payload.get("card_id") or fallback_card_id).strip() or fallback_card_id
        title = str(payload.get("title") or card_id).strip() or card_id
        return KnowledgeCard(
            card_id=card_id,
            title=title,
            general_methods=[str(x) for x in payload.get("general_methods", []) or []],
            hints=[str(x) for x in payload.get("hints", []) or []],
            common_mistakes=[str(x) for x in payload.get("common_mistakes", []) or []],
            prerequisite_ids=[str(x) for x in payload.get("prerequisite_ids", []) or []],
        )

    @staticmethod
    def _extract_answer_from_schema(answer_schema: dict[str, Any]) -> str:
        """
        将统一 answer_schema 适配为 Tutor 当前的 answer 文本字段。
        """
        if not isinstance(answer_schema, dict):
            return ""

        text = str(answer_schema.get("answer_text") or "").strip()
        if text:
            return text
        ref = str(answer_schema.get("reference_answer") or "").strip()
        if ref:
            return ref

        correct = answer_schema.get("correct")
        if isinstance(correct, list):
            values = [str(v).strip() for v in correct if str(v).strip()]
            return "/".join(values)
        if correct is not None:
            return str(correct)

        accepted = answer_schema.get("accepted")
        if isinstance(accepted, list):
            values = [str(v).strip() for v in accepted if str(v).strip()]
            return "/".join(values)
        if accepted is not None:
            return str(accepted)

        return ""


# =============================================================================
# PathEvaluator 相关
# =============================================================================

class PedagogicalAlignment(str, Enum):
    """
    替代解法与知识卡片目标的教学对齐程度

    ALIGNED  → 方法不同但覆盖了目标知识点，可接受
    BYPASS   → 方法绕过了目标知识点（高维/跨 chapter），需标记
    INFERIOR → 方法合法但难以走通（计算量极大/路径不可行）
    """
    ALIGNED  = "aligned"
    BYPASS   = "bypass"
    INFERIOR = "inferior"


class AlternativeRecommendation(str, Enum):
    """
    PathEvaluator 给出的处置建议

    ACCEPT           → 按学生方法重新规划 checkpoints
    REDIRECT_GENTLE  → 温和引回知识卡片的方法
    ACCEPT_WITH_FLAG → 引导完成但不更新知识卡片熟练度
    """
    ACCEPT           = "accept"
    REDIRECT_GENTLE  = "redirect_gentle"
    ACCEPT_WITH_FLAG = "accept_with_flag"


@dataclass
class PathEvaluationResult:
    """PathEvaluatorAgent 的输出"""
    is_mathematically_valid: bool
    pedagogical_alignment: PedagogicalAlignment
    recommendation: AlternativeRecommendation
    student_approach_summary: str     # 对学生方法的一句话描述
    student_method_name: str = ""    # 方法简称（如"配方法"），用于展示给学生
    redirect_reason: str | None = None    # redirect 时给 SocraticAgent 的说明
    replan_start_from: str | None = None  # accept 时，从哪里开始重新规划


# =============================================================================
# 核心枚举
# =============================================================================

class TutorMode(str, Enum):
    """当前辅导模式"""
    SOCRATIC = "socratic"
    GRADING  = "grading"
    IDLE     = "idle"


class ErrorType(str, Enum):
    """
    学生错误类型分类（来自批改结果）

    路由策略速查：
      CORRECT          → 直接表扬，结束
      COMPUTATIONAL    → 直接纠正，不走苏格拉底
      MISCONCEPTION    → 直接澄清概念，不走苏格拉底
      INCOMPLETE       → 针对性追问，不建完整 plan
      NO_ATTEMPT       → 跳过 Grader，从审题开始引导
      ON_TRACK_STUCK   → 苏格拉底，FINE 粒度
      WRONG_PATH_MINOR → 苏格拉底，MEDIUM 粒度
      WRONG_PATH_MAJOR → 苏格拉底，COARSE 粒度，从头重置
    """
    CORRECT           = "correct"
    ON_TRACK_STUCK    = "on_track_stuck"
    WRONG_PATH_MINOR  = "wrong_path_minor"
    WRONG_PATH_MAJOR  = "wrong_path_major"
    COMPUTATIONAL     = "computational"
    INCOMPLETE        = "incomplete"
    MISCONCEPTION     = "misconception"
    NO_ATTEMPT        = "no_attempt"


class TutorAction(str, Enum):
    """LLM 路由动作"""
    CONTINUE_SOCRATIC  = "continue_socratic"
    HANDLE_FRUSTRATION = "handle_frustration"
    HANDLE_ANSWER_REQ  = "handle_answer_request"
    HANDLE_CHALLENGE   = "handle_challenge"
    START_DEEP_DIVE    = "start_deep_dive"
    CONTINUE_DEEP_DIVE = "continue_deep_dive"
    CLOSE_DEEP_DIVE    = "close_deep_dive"
    FOLLOWUP_QUESTION  = "followup_question"
    EXPLICIT_REGRESS   = "explicit_regress"
    OFF_TOPIC          = "off_topic"


class GranularityLevel(int, Enum):
    """
    苏格拉底引导的 checkpoint 粒度

    注意：无论粒度如何，checkpoints 应以结果（outcome）而非方法（method）为目标，
    以吸收等价替代解法（如因式分解 vs 解方程），避免不必要地触发 PathEvaluator。
    """
    COARSE = 1   # 3-4 个大阶段
    MEDIUM = 2   # 5-7 个主步骤
    FINE   = 3   # 2-3 个卡点附近子步骤


# =============================================================================
# Agent 输入/输出数据结构
# =============================================================================

@dataclass
class Checkpoint:
    """单个引导检查点（结果导向，不绑定具体解法）"""
    index: int
    description: str          # 学生应达到的结果（tutor 视角，不含方法细节）
    guiding_question: str     # 引导问题（结果导向，方法中立）
    hint_level: int = 1       # 1=隐晦, 2=方向, 3=明确
    prerequisite_tags: list[str] = field(default_factory=list)
    passed: bool = False
    attempts: int = 0

    def escalate_hint(self) -> None:
        self.hint_level = min(self.hint_level + 1, 3)


@dataclass
class SolutionPlan:
    """解题引导方案"""
    approach_summary: str        # 正确思路（tutor 可见）
    reset_reason: str
    checkpoints: list[Checkpoint]
    granularity: GranularityLevel
    # 若此 plan 是按学生替代方法生成的，记录替代方法名
    alternative_method: str | None = None
    created_at: float = field(default_factory=lambda: datetime.now().timestamp())


@dataclass
class GraderResult:
    """批改结果（由 GraderAgent 生成）"""
    error_type: ErrorType
    is_correct: bool
    error_description: str
    student_approach: str
    error_location: str | None = None
    correction_note: str | None = None
    suggested_granularity: GranularityLevel = GranularityLevel.MEDIUM
    # 替代解法标记：学生使用的方法是否不在知识卡片的通性通法中
    uses_alternative_method: bool = False
    alternative_method_name: str | None = None   # 如"导数法"、"向量法"


@dataclass
class CheckpointEvaluation:
    """Router 对学生回答某 checkpoint 的评估"""
    checkpoint_passed: bool
    student_understanding: str
    next_hint_level: int
    reason: str
    regressed_to_checkpoint: int | None = None
    # 替代解法检测：通过了但用了不同方法
    used_alternative_method: bool = False
    alternative_method_name: str | None = None


@dataclass
class RouterDecision:
    """Router 的调度决策"""
    mode: TutorMode
    reason: str
    needs_new_plan: bool = False
    suggested_granularity: GranularityLevel = GranularityLevel.MEDIUM
    reset_from_error: str | None = None
    deliver_grader_feedback: bool = False


# =============================================================================
# Session
# =============================================================================

@dataclass
class TutorSession:
    """
    一次辅导会话的完整状态

    作为数据飞轮的原始单元，最终被 Progress 模块归档。
    """
    session_id: str
    problem_context: ProblemContext    # 题目 + 答案 + 知识卡片（JSON 驱动）
    created_at: float

    mode: TutorMode = TutorMode.IDLE
    solution_plan: SolutionPlan | None = None
    current_checkpoint: int = 0
    last_grader_result: GraderResult | None = None

    interaction_history: list[dict[str, Any]] = field(default_factory=list)

    status: str = "active"            # active | solved | abandoned

    # Progress 模块字段
    student_id: str | None = None
    mastery_before: float | None = None
    total_hints_given: int = 0
    total_attempts: int = 0

    # 替代解法标记（供 Progress 模块决定是否更新知识卡片熟练度）
    used_alternative_method: bool = False
    # True = accept_with_flag，不更新题目原目标卡片；改由 solution 分叉知识卡片更新
    alternative_flagged: bool = False

    # 多问小题
    sub_plans: dict[str, Any] = field(default_factory=dict)
    active_sub_problem: str | None = None

    # 深问子流程（deep dive）
    deep_dive_active: bool = False
    deep_dive_rounds: int = 0
    deep_dive_return_checkpoint: int | None = None
    deep_dive_topic: str = ""
    deep_dive_records: list[dict[str, Any]] = field(default_factory=list)
    deferred_deep_dive_tasks: list[dict[str, Any]] = field(default_factory=list)

    # -------------------------------------------------------------------------
    # 便捷属性
    # -------------------------------------------------------------------------

    @property
    def problem(self) -> str:
        return self.problem_context.problem

    @property
    def problem_tags(self) -> list[str]:
        return self.problem_context.tags

    @property
    def chapter(self) -> str:
        return self.problem_context.chapter

    # -------------------------------------------------------------------------
    # Checkpoint 状态机
    # -------------------------------------------------------------------------

    def get_current_checkpoint(self) -> Checkpoint | None:
        if not self.solution_plan:
            return None
        cps = self.solution_plan.checkpoints
        return cps[self.current_checkpoint] if self.current_checkpoint < len(cps) else None

    def advance_checkpoint(self) -> bool:
        cp = self.get_current_checkpoint()
        if cp:
            cp.passed = True
        self.current_checkpoint += 1
        return self.get_current_checkpoint() is not None

    def is_all_checkpoints_done(self) -> bool:
        if not self.solution_plan:
            return False
        return self.current_checkpoint >= len(self.solution_plan.checkpoints)

    def regress_to(self, checkpoint_index: int) -> None:
        if not self.solution_plan:
            return
        cps = self.solution_plan.checkpoints
        for i in range(checkpoint_index, len(cps)):
            cps[i].passed = False
        if checkpoint_index < len(cps):
            cps[checkpoint_index].hint_level = 2
        self.current_checkpoint = checkpoint_index

    # -------------------------------------------------------------------------
    # 交互历史
    # -------------------------------------------------------------------------

    def add_interaction(
        self,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.interaction_history.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().timestamp(),
            "metadata": metadata or {},
        })

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)
