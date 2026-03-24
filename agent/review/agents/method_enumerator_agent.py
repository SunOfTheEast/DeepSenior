#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MethodEnumeratorAgent - 枚举所有有效解法

Skill 签名：(problem_context: ProblemContext) -> list[MethodInfo]

给定题目 + 答案 + 知识卡片，列举这道题所有数学上有效的解法，
标记哪些是通性通法（知识卡片中已有）、哪些是替代方法。
"""

import json
import re

from agent.base_agent import BaseAgent
from agent.tutor.data_structures import ProblemContext
from ..data_structures import MethodInfo


class MethodEnumeratorAgent(BaseAgent):
    """枚举一道题所有有效解法"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        language: str = "zh",
        api_version: str | None = None,
        binding: str = "openai",
    ):
        super().__init__(
            module_name="review",
            agent_name="method_enumerator_agent",
            api_key=api_key,
            base_url=base_url,
            api_version=api_version,
            language=language,
            binding=binding,
        )

    async def process(self, problem_context: ProblemContext) -> list[MethodInfo]:
        """
        Skill: enumerate_methods

        Args:
            problem_context: 题目上下文（含答案和知识卡片）

        Returns:
            list[MethodInfo] 所有有效解法的元数据列表
        """
        system_prompt = self.get_prompt("system")
        user_template = self.get_prompt("enumerate")

        if not system_prompt or not user_template:
            return self._fallback_methods(problem_context)

        # 从知识卡片提取通性通法
        standard_methods = []
        for card in problem_context.knowledge_cards:
            standard_methods.extend(card.general_methods)

        card_titles = "、".join(problem_context.get_card_titles_for_llm()) or "（未提供）"

        user_prompt = user_template.format(
            problem=problem_context.problem,
            answer=problem_context.answer,
            standard_methods="\n".join(f"- {m}" for m in standard_methods) or "（未提供）",
            card_titles=card_titles,
        )

        response = await self.call_llm(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            response_format={"type": "json_object"},
            temperature=0.3,
            stage="enumerate_methods",
        )

        return self._parse_methods(response, standard_methods)

    def _parse_methods(self, response: str, standard_methods: list[str]) -> list[MethodInfo]:
        try:
            data = json.loads(response.strip())
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", response)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    return []
            else:
                return []

        methods = []
        for item in data.get("methods", []):
            name = item.get("name", "")
            if not name:
                continue
            is_standard = any(
                name in sm or sm in name
                for sm in standard_methods
            )
            methods.append(MethodInfo(
                name=name,
                summary=item.get("summary", ""),
                difficulty=item.get("difficulty", "中等"),
                prerequisites=item.get("prerequisites", []),
                is_standard=item.get("is_standard", is_standard),
            ))
        return methods

    def _fallback_methods(self, problem_context: ProblemContext) -> list[MethodInfo]:
        """无 prompt 时的降级：只返回知识卡片中的通性通法"""
        methods = []
        for card in problem_context.knowledge_cards:
            for method in card.general_methods:
                methods.append(MethodInfo(
                    name=method,
                    summary="知识卡片标准方法",
                    difficulty="基础",
                    prerequisites=[],
                    is_standard=True,
                ))
        return methods
