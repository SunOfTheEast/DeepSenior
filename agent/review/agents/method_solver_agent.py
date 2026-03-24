#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MethodSolverAgent - 用指定方法完整演示解题过程

Skill 签名：(problem_context: ProblemContext, method_name: str) -> SolvedMethod

按照学生指定（或 MethodEnumerator 列出）的方法，逐步演示完整解法。
用于：学生说"用X方法怎么做？"时展示完整过程。
"""

import json
import re

from agent.base_agent import BaseAgent
from agent.tutor.data_structures import ProblemContext
from ..data_structures import SolvedMethod


class MethodSolverAgent(BaseAgent):
    """按指定方法完整演示解题过程"""

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
            agent_name="method_solver_agent",
            api_key=api_key,
            base_url=base_url,
            api_version=api_version,
            language=language,
            binding=binding,
        )

    async def process(
        self,
        problem_context: ProblemContext,
        method_name: str,
    ) -> SolvedMethod:
        """
        Skill: solve_with_method

        Args:
            problem_context: 题目上下文（含答案，用于 self-check）
            method_name: 解法名称，如"向量法"、"配方法"

        Returns:
            SolvedMethod 含完整步骤和说明
        """
        system_prompt = self.get_prompt("system")
        user_template = self.get_prompt("solve")

        if not system_prompt or not user_template:
            return self._fallback(method_name)

        method_guidance = self._build_method_guidance(problem_context, method_name)

        user_prompt = user_template.format(
            problem=problem_context.problem,
            answer=problem_context.answer,
            method_name=method_name,
            method_guidance=method_guidance,
        )

        response = await self.call_llm(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            response_format={"type": "json_object"},
            temperature=0.1,
            stage="solve_with_method",
        )

        return self._parse_solution(response, method_name)

    @staticmethod
    def _build_method_guidance(
        problem_context: ProblemContext, method_name: str,
    ) -> str:
        """提取与指定方法最相关的 1-2 条提示和 1 条易错点。"""
        parts: list[str] = []
        # 优先找 general_methods 包含该方法名的卡
        matched_cards = [
            kc for kc in problem_context.knowledge_cards
            if any(method_name in m or m in method_name for m in kc.general_methods)
        ]
        cards = matched_cards or problem_context.knowledge_cards[:1]
        for kc in cards[:1]:
            for h in kc.hints[:2]:
                parts.append(f"- 提示：{h}")
            if kc.common_mistakes:
                parts.append(f"- 易错：{kc.common_mistakes[0]}")
        return "\n".join(parts) if parts else "（无特殊提示）"

    def _parse_solution(self, response: str, method_name: str) -> SolvedMethod:
        try:
            data = json.loads(response.strip())
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", response)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    return self._fallback(method_name)
            else:
                return self._fallback(method_name)

        steps = data.get("steps", [])
        if isinstance(steps, list):
            steps = [str(s) for s in steps]
        else:
            steps = [str(steps)]

        return SolvedMethod(
            method_name=data.get("method_name", method_name),
            steps=steps,
            key_insight=data.get("key_insight", ""),
            comparison_note=data.get("comparison_note", ""),
        )

    def _fallback(self, method_name: str) -> SolvedMethod:
        return SolvedMethod(
            method_name=method_name,
            steps=["（暂时无法生成完整步骤，请重试）"],
            key_insight="",
            comparison_note="",
        )
