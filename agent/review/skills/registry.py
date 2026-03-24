#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ReviewSkillRegistry - Review 模块技能注册中心
"""

from typing import Any, Callable

from agent.skills_common import SkillMeta
from agent.tutor.data_structures import (
    AlternativeRecommendation,
    PathEvaluationResult,
    PedagogicalAlignment,
)

from ..agents import MethodEnumeratorAgent, MethodSolverAgent, ReviewChatAgent


class ReviewSkillRegistry:
    """
    Review 模块技能注册中心。

    可选接入外部 evaluate_approach skill（通常来自 Tutor SkillRegistry），
    未提供时使用保守兜底：默认认为方法数学有效并继续演示。
    """

    _SKILL_META: list[SkillMeta] = [
        SkillMeta(
            name="evaluate_approach",
            description="评估学生提出的方法在当前题目上的数学有效性",
            inputs=["problem_context: ProblemContext", "student_approach: str", "student_work_excerpt: str"],
            output="PathEvaluationResult",
            tags=["llm", "stateless"],
        ),
        SkillMeta(
            name="enumerate_methods",
            description="枚举题目的可行解法列表",
            inputs=["problem_context: ProblemContext"],
            output="list[MethodInfo]",
            tags=["llm", "stateless"],
        ),
        SkillMeta(
            name="solve_method",
            description="按指定方法给出完整解题演示",
            inputs=["problem_context: ProblemContext", "method_name: str"],
            output="SolvedMethod",
            tags=["llm", "stateless"],
        ),
        SkillMeta(
            name="classify_intent",
            description="识别复盘对话意图",
            inputs=[
                "problem_context: ProblemContext",
                "student_message: str",
                "known_methods: list[MethodInfo]",
                "interaction_history: list[dict]",
            ],
            output="tuple[ReviewIntent, method_target]",
            tags=["llm", "stateless"],
        ),
        SkillMeta(
            name="respond_review",
            description="生成复盘阶段的通用回复",
            inputs=["problem_context", "student_message", "context_str", "interaction_history"],
            output="str",
            tags=["llm", "stateless"],
        ),
        SkillMeta(
            name="replay_errors",
            description="错误回放与方法对比说明",
            inputs=["problem_context", "error_snapshots", "struggle_points", "student_method_used", "target_method", "solved_demo", "interaction_history"],
            output="str",
            tags=["llm", "stateless"],
        ),
        SkillMeta(
            name="ask_understanding",
            description="生成理解验证问题（概念阶段）",
            inputs=["problem_context", "solved_method", "student_method_used"],
            output="tuple[question, key_points]",
            tags=["llm", "stateless"],
        ),
        SkillMeta(
            name="ask_transfer",
            description="生成迁移验证问题（变式阶段）",
            inputs=["problem_context", "solved_method", "student_method_used"],
            output="tuple[question, key_points]",
            tags=["llm", "stateless"],
        ),
        SkillMeta(
            name="evaluate_understanding",
            description="评估学生对验证问题的回答质量",
            inputs=["problem_context", "method_name", "question", "key_points", "student_response", "solved_demo"],
            output="tuple[UnderstandingQuality, feedback]",
            tags=["llm", "stateless"],
        ),
    ]

    def __init__(
        self,
        api_key: str,
        base_url: str,
        language: str = "zh",
        api_version: str | None = None,
        binding: str = "openai",
        evaluate_approach_skill: Callable | None = None,
    ):
        _kw = dict(
            api_key=api_key,
            base_url=base_url,
            language=language,
            api_version=api_version,
            binding=binding,
        )
        self._enumerator = MethodEnumeratorAgent(**_kw)
        self._solver = MethodSolverAgent(**_kw)
        self._chat = ReviewChatAgent(**_kw)

        self._skills: dict[str, Callable] = {
            "evaluate_approach": evaluate_approach_skill or self._fallback_evaluate_approach,
            "enumerate_methods": self._enumerator.process,
            "solve_method": self._solver.process,
            "classify_intent": self._chat.classify_intent,
            "respond_review": self._chat.respond,
            "replay_errors": self._chat.replay_errors,
            "ask_understanding": self._chat.ask_understanding,
            "ask_transfer": self._chat.ask_transfer,
            "evaluate_understanding": self._chat.evaluate_understanding,
        }
        self._meta: dict[str, SkillMeta] = {m.name: m for m in self._SKILL_META}

    async def _fallback_evaluate_approach(
        self,
        problem_context,
        student_approach: str,
        student_work_excerpt: str,
    ) -> PathEvaluationResult:
        return PathEvaluationResult(
            is_mathematically_valid=True,
            pedagogical_alignment=PedagogicalAlignment.ALIGNED,
            recommendation=AlternativeRecommendation.ACCEPT,
            student_approach_summary=student_approach or "学生方法",
            redirect_reason=None,
            replan_start_from=None,
        )

    def get(self, name: str) -> Callable:
        if name not in self._skills:
            available = list(self._skills.keys())
            raise KeyError(f"Skill '{name}' not found. Available: {available}")
        return self._skills[name]

    async def call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        skill = self.get(name)
        return await skill(*args, **kwargs)

    def describe(self, name: str) -> SkillMeta:
        if name not in self._meta:
            raise KeyError(f"Skill '{name}' not found.")
        return self._meta[name]

    def list_skills(self) -> list[SkillMeta]:
        return list(self._meta.values())

    def has(self, name: str) -> bool:
        return name in self._skills

    def __repr__(self) -> str:
        names = list(self._skills.keys())
        return f"ReviewSkillRegistry(skills={names})"
