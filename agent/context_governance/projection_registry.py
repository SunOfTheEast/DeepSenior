"""Projection Registry — 定义每类 Agent projection 的协议。

每个 projection 声明：
  - 允许哪些字段
  - 哪些字段可空（nullable）
  - 字段缺失时的降级策略
  - 协议版本号（用于兼容性检查）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class DegradationStrategy(Enum):
    """字段缺失时的降级策略。"""
    EMPTY_STRING = "empty_string"       # 填空字符串
    DEFAULT_TEXT = "default_text"       # 使用预定义默认文案
    SKIP = "skip"                      # 从 payload 中移除该字段
    FAIL = "fail"                      # 标记 assembly 为 degraded


@dataclass(frozen=True)
class FieldSpec:
    """单个字段的 projection 协议。"""
    name: str
    nullable: bool = False
    degradation: DegradationStrategy = DegradationStrategy.EMPTY_STRING
    default_text: str = ""


@dataclass(frozen=True)
class ProjectionSpec:
    """一类 Agent 的完整 projection 协议。"""
    name: str
    version: int = 1
    fields: tuple[FieldSpec, ...] = ()
    description: str = ""

    @property
    def allowed_fields(self) -> frozenset[str]:
        return frozenset(f.name for f in self.fields)

    @property
    def required_fields(self) -> frozenset[str]:
        return frozenset(f.name for f in self.fields if not f.nullable)

    def get_field(self, name: str) -> FieldSpec | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None

    def validate(self, candidate: dict[str, str]) -> ProjectionValidation:
        """校验 candidate dict 是否符合 projection 协议。"""
        unknown = set(candidate.keys()) - self.allowed_fields
        missing_required = self.required_fields - set(candidate.keys())
        degraded_fields: list[tuple[str, DegradationStrategy]] = []

        for fs in self.fields:
            val = candidate.get(fs.name)
            if val is None or (isinstance(val, str) and not val.strip()):
                if not fs.nullable:
                    degraded_fields.append((fs.name, fs.degradation))

        return ProjectionValidation(
            spec_name=self.name,
            spec_version=self.version,
            unknown_fields=unknown,
            missing_required=missing_required,
            degraded_fields=degraded_fields,
            is_valid=len(unknown) == 0 and len(missing_required) == 0,
        )


@dataclass
class ProjectionValidation:
    """projection 校验结果。"""
    spec_name: str
    spec_version: int
    unknown_fields: set[str] = field(default_factory=set)
    missing_required: set[str] = field(default_factory=set)
    degraded_fields: list[tuple[str, DegradationStrategy]] = field(
        default_factory=list
    )
    is_valid: bool = True


# ---------------------------------------------------------------------------
# 预定义 Projection 协议
# ---------------------------------------------------------------------------

PLANNER_PROJECTION = ProjectionSpec(
    name="planner",
    version=1,
    description="PlannerAgent — 教学规划",
    fields=(
        FieldSpec("problem"),
        FieldSpec("error_description", nullable=True, degradation=DegradationStrategy.SKIP),
        FieldSpec("student_approach", nullable=True, degradation=DegradationStrategy.SKIP),
        FieldSpec("target_cards"),
        FieldSpec("selected_methods"),
        FieldSpec("planning_hints", nullable=True, degradation=DegradationStrategy.EMPTY_STRING),
        FieldSpec("supplementary_cards", nullable=True, degradation=DegradationStrategy.SKIP),
        FieldSpec("answer_outline", nullable=True, degradation=DegradationStrategy.SKIP),
    ),
)

PATH_EVALUATOR_PROJECTION = ProjectionSpec(
    name="path_evaluator",
    version=1,
    description="PathEvaluatorAgent — 解题路径评估",
    fields=(
        FieldSpec("problem"),
        FieldSpec("student_approach"),
        FieldSpec("student_work_excerpt", nullable=True, degradation=DegradationStrategy.SKIP),
        FieldSpec("target_skills"),
        FieldSpec("target_methods"),
        FieldSpec("alignment_constraints", nullable=True, degradation=DegradationStrategy.EMPTY_STRING),
        FieldSpec("answer_outline", nullable=True, degradation=DegradationStrategy.SKIP),
    ),
)

GRADER_PROJECTION = ProjectionSpec(
    name="grader",
    version=1,
    description="GraderAgent — 作业评分",
    fields=(
        FieldSpec("problem"),
        FieldSpec("answer"),
        FieldSpec("student_work"),
        FieldSpec("intended_methods", nullable=True, degradation=DegradationStrategy.EMPTY_STRING),
        FieldSpec("common_mistakes", nullable=True, degradation=DegradationStrategy.EMPTY_STRING),
    ),
)

REVIEW_EXPLAIN_PROJECTION = ProjectionSpec(
    name="review_explain",
    version=1,
    description="ReviewExplain — 复盘讲解",
    fields=(
        FieldSpec("problem"),
        FieldSpec("focus_concept"),
        FieldSpec("relevant_cards", nullable=True, degradation=DegradationStrategy.DEFAULT_TEXT, default_text="（无相关知识卡）"),
        FieldSpec("student_context", nullable=True, degradation=DegradationStrategy.SKIP),
    ),
)

MEMORY_DISTILL_PROJECTION = ProjectionSpec(
    name="memory_distill",
    version=1,
    description="MemoryDistillerAgent — 记忆蒸馏",
    fields=(
        FieldSpec("episode"),
        FieldSpec("current_profile", nullable=True, degradation=DegradationStrategy.DEFAULT_TEXT, default_text="（首次会话，无历史画像）"),
        FieldSpec("current_mastery_summary", nullable=True, degradation=DegradationStrategy.EMPTY_STRING),
    ),
)

PROGRESS_PROJECTION = ProjectionSpec(
    name="progress",
    version=1,
    description="ProgressSummary / TaskPlanner — 学习进展与任务规划",
    fields=(
        FieldSpec("long_term_profile"),
        FieldSpec("concept_mastery", nullable=True, degradation=DegradationStrategy.DEFAULT_TEXT, default_text="（暂无掌握记录）"),
        FieldSpec("decay_due", nullable=True, degradation=DegradationStrategy.DEFAULT_TEXT, default_text="（当前无需复习）"),
        FieldSpec("recent_episodes", nullable=True, degradation=DegradationStrategy.DEFAULT_TEXT, default_text="（无近期记录）"),
    ),
)

RECOMMEND_PROJECTION = ProjectionSpec(
    name="recommend",
    version=1,
    description="RecommendAgent — 下一步推荐",
    fields=(
        FieldSpec("student_profile", nullable=True, degradation=DegradationStrategy.DEFAULT_TEXT, default_text="（暂无长期记忆）"),
        FieldSpec("weak_concepts", nullable=True, degradation=DegradationStrategy.DEFAULT_TEXT, default_text="（暂无）"),
        FieldSpec("recent_problems", nullable=True, degradation=DegradationStrategy.DEFAULT_TEXT, default_text="（无近期记录）"),
    ),
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, ProjectionSpec] = {
    spec.name: spec
    for spec in [
        PLANNER_PROJECTION,
        PATH_EVALUATOR_PROJECTION,
        GRADER_PROJECTION,
        REVIEW_EXPLAIN_PROJECTION,
        MEMORY_DISTILL_PROJECTION,
        PROGRESS_PROJECTION,
        RECOMMEND_PROJECTION,
    ]
}


def get_projection(name: str) -> ProjectionSpec | None:
    """按名称获取 projection 协议。"""
    return _REGISTRY.get(name)


def list_projections() -> list[str]:
    """列出所有已注册的 projection 名称。"""
    return list(_REGISTRY.keys())
