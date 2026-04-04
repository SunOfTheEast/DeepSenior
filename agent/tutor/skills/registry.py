#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SkillRegistry - 全局技能注册中心

所有 skill 均为无状态调用（context 显式传入），
可被任意 orchestrator（TutorManager / ResearchModule / ProgressModule）使用。

使用方式：
    registry = SkillRegistry(api_key=..., base_url=...)

    # 直接调用
    result = await registry.call("grade_work", problem_context, student_work)

    # 或者取出 callable 存起来
    grade = registry.get("grade_work")
    result = await grade(problem_context, student_work)

    # 查看所有可用 skill
    for meta in registry.list_skills():
        print(meta)
"""

from typing import Any, Callable

from agent.knowledge import build_card_retriever, CardRetriever
from agent.skills_common import SkillMeta, wrap_sync_as_async
from ..agents import (
    ActAgent,
    GraderAgent,
    PlannerAgent,
    RouterAgent,
    SocraticAgent,
    SignalAgent,
    StrategyAgent,
    ThinkAgent,
)
from ..agents.path_evaluator_agent import PathEvaluatorAgent


class SkillRegistry:
    """
    技能注册中心

    - 每个 agent 只初始化一次，所有 orchestrator 共享同一批实例
    - Skill 调用均为无状态：所有上下文显式传入，不依赖 TutorSession
    - rule_based skill（route_decision）为同步函数，registry 统一包装为 async
    """

    _SKILL_META: list[SkillMeta] = [
        SkillMeta(
            name="grade_work",
            description="批改学生解题过程，返回错误类型、定位和替代解法标记",
            inputs=["problem_context: ProblemContext", "student_work: str"],
            output="GraderResult",
            tags=["llm", "stateless"],
        ),
        SkillMeta(
            name="plan_guidance",
            description="生成苏格拉底引导的 checkpoint 序列（结果导向，方法中立）",
            inputs=[
                "problem_context: ProblemContext",
                "error_description: str",
                "start_from: str",
                "granularity: GranularityLevel",
                "alternative_method: str | None = None",
                "supplementary_cards: str | None = None",
                "progress_snapshot: str | None = None",
            ],
            output="SolutionPlan",
            tags=["llm", "stateless"],
        ),
        SkillMeta(
            name="generate_hint",
            description="生成苏格拉底引导语（单次，非流式）",
            inputs=[
                "problem: str",
                "checkpoint: Checkpoint",
                "interaction_history: list[dict]",
                "student_response: str",
            ],
            output="str",
            tags=["llm", "stateless"],
        ),
        SkillMeta(
            name="evaluate_approach",
            description="评估替代解法的数学有效性和教学对齐程度",
            inputs=[
                "problem_context: ProblemContext",
                "student_approach: str",
                "student_work_excerpt: str",
            ],
            output="PathEvaluationResult",
            tags=["llm", "stateless"],
        ),
        SkillMeta(
            name="evaluate_checkpoint",
            description="评估学生是否通过了当前 checkpoint（含回退检测、替代解法标记）",
            inputs=[
                "checkpoint: Checkpoint",
                "passed_checkpoints_history: str",
                "student_response: str",
                "total_checkpoints: int | str",
            ],
            output="CheckpointEvaluation",
            tags=["llm", "stateless"],
        ),
        SkillMeta(
            name="evaluate_progress_map",
            description="一次评估从当前起点开始的连续多个 checkpoint（返回连续通过数量）",
            inputs=[
                "checkpoints: list[Checkpoint]",
                "current_checkpoint: int",
                "passed_checkpoints_history: str",
                "student_response: str",
                "total_checkpoints: int | str",
                "interaction_context: str",
                "max_steps: int",
            ],
            output="dict[passed_count, next_hint_level, reason, regressed_to_checkpoint, used_alternative_method, alternative_method_name]",
            tags=["llm", "stateless"],
        ),
        SkillMeta(
            name="route_decision",
            description="根据批改结果决定路由策略（规则驱动，无 LLM 调用）",
            inputs=["grader_result: GraderResult"],
            output="RouterDecision",
            tags=["rule_based", "stateless"],
        ),
        SkillMeta(
            name="retrieve_cards",
            description="二阶段知识卡检索（MethodRouter + CardSelector）",
            inputs=["request: CardRetrieveRequest"],
            output="RetrievalBundle",
            tags=["llm", "stateless", "rag"],
        ),
    ]

    def __init__(
        self,
        api_key: str,
        base_url: str,
        language: str = "zh",
        api_version: str | None = None,
        binding: str = "openai",
    ):
        """
        初始化所有 agent（每个 agent 全局单例，所有 orchestrator 共享）
        """
        _kwargs = dict(
            api_key=api_key,
            base_url=base_url,
            language=language,
            api_version=api_version,
            binding=binding,
        )
        self._grader         = GraderAgent(**_kwargs)
        self._planner        = PlannerAgent(**_kwargs)
        self._socratic       = SocraticAgent(**_kwargs)
        self._path_evaluator = PathEvaluatorAgent(**_kwargs)
        self._router         = RouterAgent(**_kwargs)
        self._card_retriever = build_card_retriever(**_kwargs)

        # Think→Act agents
        self._think_agent    = ThinkAgent(**_kwargs)
        self._signal_agent   = SignalAgent(**_kwargs)
        self._strategy_agent = StrategyAgent(**_kwargs)
        self._act_agent      = ActAgent(**_kwargs)

        # skill name → callable（async 或被包装为 async）
        self._skills: dict[str, Callable] = {
            # 事实判题入口：只输出结构化结果，不直接生成对学生话术。
            "grade_work":          self._grader.process,
            "plan_guidance":       self._planner.process,
            "generate_hint":       self._socratic.process,
            "evaluate_approach":   self._path_evaluator.process,
            "evaluate_checkpoint": self._router.evaluate_checkpoint,
            "evaluate_progress_map": self._router.evaluate_progress_map,
            # route_decision 是同步规则函数，这里统一包装成 async 以简化上层调用。
            "route_decision":      wrap_sync_as_async(self._router.decide_after_grading),
            "retrieve_cards":      self._card_retriever.retrieve,
        }

        self._meta: dict[str, SkillMeta] = {
            m.name: m for m in self._SKILL_META
        }

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    @property
    def card_store(self):
        """Expose the FileCardStore for sync pre-loading at session creation."""
        return self._card_retriever.card_store

    @property
    def card_index(self):
        """Expose the CardIndex for TutorToolRegistry search_knowledge."""
        return self._card_retriever.card_index

    def get(self, name: str) -> Callable:
        """
        取出 skill callable。

        所有 skill 均可 `await`（同步 skill 已被包装为 async）。

        Usage:
            grade = registry.get("grade_work")
            result = await grade(problem_context, student_work)
        """
        if name not in self._skills:
            available = list(self._skills.keys())
            raise KeyError(f"Skill '{name}' not found. Available: {available}")
        return self._skills[name]

    async def call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """
        直接调用 skill（无需先 get）。

        Usage:
            result = await registry.call("grade_work", problem_context, student_work)
        """
        skill = self.get(name)
        return await skill(*args, **kwargs)

    def describe(self, name: str) -> SkillMeta:
        """查询 skill 元数据"""
        if name not in self._meta:
            raise KeyError(f"Skill '{name}' not found.")
        return self._meta[name]

    def list_skills(self) -> list[SkillMeta]:
        """列出所有已注册 skill 的元数据"""
        return list(self._meta.values())

    def has(self, name: str) -> bool:
        return name in self._skills

    # Think→Act agent instances (used by ThinkActManager)
    @property
    def think_agent(self) -> ThinkAgent:
        return self._think_agent

    @property
    def signal_agent(self) -> SignalAgent:
        return self._signal_agent

    @property
    def strategy_agent(self) -> StrategyAgent:
        return self._strategy_agent

    @property
    def act_agent(self) -> ActAgent:
        return self._act_agent

    @property
    def planner_agent(self) -> PlannerAgent:
        return self._planner

    # -------------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------------

    def __repr__(self) -> str:
        names = list(self._skills.keys())
        return f"SkillRegistry(skills={names})"
