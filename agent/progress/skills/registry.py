#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ProgressSkillRegistry - Progress 模块技能注册中心
"""

from typing import Any, Callable

from agent.skills_common import SkillMeta

from ..agents.progress_summary_agent import ProgressSummaryAgent
from ..agents.task_planner_agent import TaskPlannerAgent


class ProgressSkillRegistry:
    _SKILL_META: list[SkillMeta] = [
        SkillMeta(
            name="plan_tasks",
            description="基于长期与近期记忆规划今日学习任务",
            inputs=["semantic", "decay_due", "recent_episodes", "max_tasks", "max_minutes"],
            output="tuple[list[DailyTask], str]",
            tags=["llm", "stateless"],
        ),
        SkillMeta(
            name="summarize_progress",
            description="生成阶段性学习进展总结",
            inputs=["student_id", "semantic", "episodes", "period"],
            output="ProgressSummary",
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
    ):
        _kw = dict(
            api_key=api_key,
            base_url=base_url,
            language=language,
            api_version=api_version,
            binding=binding,
        )
        self._summary = ProgressSummaryAgent(**_kw)
        self._planner = TaskPlannerAgent(**_kw)
        self._skills: dict[str, Callable] = {
            "plan_tasks": self._planner.plan,
            "summarize_progress": self._summary.summarize,
        }
        self._meta: dict[str, SkillMeta] = {m.name: m for m in self._SKILL_META}

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
        return f"ProgressSkillRegistry(skills={names})"
