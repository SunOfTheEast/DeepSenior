"""集中维护预算常量和优先级规则。

所有面向 LLM 的上下文裁剪参数在此定义，避免各 Agent 各写一份。
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# 单字段预算
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FieldBudget:
    """单个上下文字段的预算约束。"""
    max_items: int = 4
    max_chars: int = 240
    max_items_per_card: int = 2


# ---------------------------------------------------------------------------
# 全局默认值
# ---------------------------------------------------------------------------

# ProblemContext helper 默认预算
METHODS_BUDGET = FieldBudget(max_items=4, max_chars=120)
HINTS_BUDGET = FieldBudget(max_items=4, max_chars=240, max_items_per_card=2)
MISTAKES_BUDGET = FieldBudget(max_items=4, max_chars=240, max_items_per_card=2)
CARD_TITLES_BUDGET = FieldBudget(max_items=3, max_chars=200)

# 历史 / checkpoint 预算
MAX_HISTORY_CONTENT_CHARS = 150
MAX_PASSED_CHECKPOINTS = 10
MAX_RECENT_HISTORY_TURNS = 6


# ---------------------------------------------------------------------------
# 整 prompt 优先级（数值越小越优先保留）
# ---------------------------------------------------------------------------

@dataclass
class BudgetPolicy:
    """Per-agent 的整 prompt 裁剪策略。

    fields_priority 列表顺序即裁剪顺序：靠后的字段先被裁。
    """
    name: str
    fields_priority: list[str] = field(default_factory=list)
    max_total_chars: int = 6000

    def trim_order(self) -> list[str]:
        """返回从后往前的裁剪顺序（最不重要的先裁）。"""
        return list(reversed(self.fields_priority))


# ---------------------------------------------------------------------------
# 预定义策略
# ---------------------------------------------------------------------------

PLANNER_POLICY = BudgetPolicy(
    name="planner",
    fields_priority=[
        "problem",
        "error_description",
        "progress_snapshot",
        "student_approach",
        "target_cards",
        "selected_methods",
        "planning_hints",
        "supplementary_cards",
        "answer_outline",
    ],
    max_total_chars=6000,
)

PATH_EVALUATOR_POLICY = BudgetPolicy(
    name="path_evaluator",
    fields_priority=[
        "problem",
        "student_approach",
        "student_work_excerpt",
        "target_skills",
        "target_methods",
        "alignment_constraints",
        "answer_outline",
    ],
    max_total_chars=5000,
)

GRADER_POLICY = BudgetPolicy(
    name="grader",
    fields_priority=[
        "problem",
        "answer",
        "student_work",
        "intended_methods",
        "common_mistakes",
    ],
    max_total_chars=6000,
)

REVIEW_EXPLAIN_POLICY = BudgetPolicy(
    name="review_explain",
    fields_priority=[
        "problem",
        "focus_concept",
        "relevant_cards",
        "student_context",
    ],
    max_total_chars=4000,
)

MEMORY_DISTILL_POLICY = BudgetPolicy(
    name="memory_distill",
    fields_priority=[
        "episode",
        "current_profile",
        "current_mastery_summary",
        "turns_context",               # 最低优先级，超预算时优先截断
    ],
    max_total_chars=8000,              # 增大预算以容纳 turns 上下文
)

PROGRESS_POLICY = BudgetPolicy(
    name="progress",
    fields_priority=[
        "long_term_profile",
        "concept_mastery",
        "decay_due",
        "recent_episodes",
    ],
    max_total_chars=5000,
)

RECOMMEND_POLICY = BudgetPolicy(
    name="recommend",
    fields_priority=[
        "student_profile",
        "weak_concepts",
        "recent_problems",
    ],
    max_total_chars=4000,
)

# 按名称查找
_POLICIES: dict[str, BudgetPolicy] = {
    p.name: p
    for p in [
        PLANNER_POLICY,
        PATH_EVALUATOR_POLICY,
        GRADER_POLICY,
        REVIEW_EXPLAIN_POLICY,
        MEMORY_DISTILL_POLICY,
        PROGRESS_POLICY,
        RECOMMEND_POLICY,
    ]
}


def get_policy(name: str) -> BudgetPolicy:
    """按名称获取预定义策略，不存在时返回无裁剪的默认策略。"""
    return _POLICIES.get(name, BudgetPolicy(name=name))
