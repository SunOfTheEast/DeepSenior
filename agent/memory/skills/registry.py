#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MemorySkillRegistry - Memory 模块技能注册中心
"""

from typing import Any, Callable

from agent.skills_common import SkillMeta

from ..agents.memory_distiller_agent import MemoryDistillerAgent


class MemorySkillRegistry:
    _SKILL_META: list[SkillMeta] = [
        SkillMeta(
            name="distill_memory",
            description="将单次会话情节记忆蒸馏为长期语义更新指令",
            inputs=["episode: EpisodicMemory", "semantic: SemanticMemory"],
            output="MemoryUpdate",
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
        self._distiller = MemoryDistillerAgent(
            api_key=api_key,
            base_url=base_url,
            language=language,
            api_version=api_version,
            binding=binding,
        )
        self._skills: dict[str, Callable] = {
            "distill_memory": self._distiller.distill,
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
        return f"MemorySkillRegistry(skills={names})"
