#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SocraticAgent - 苏格拉底引导助手（A Tutor）

通过提问引导学生自己发现解法。
根据当前 checkpoint 和 hint_level 调整提示程度。
支持流式输出。
"""

from typing import Any, AsyncGenerator

from agent.base_agent import BaseAgent
from ..data_structures import Checkpoint


class SocraticAgent(BaseAgent):
    """
    苏格拉底式引导助手

    核心原则：
    1. 永远不直接给出答案
    2. 根据 hint_level 控制提示明确程度
       - level 1: 宽泛启发（"你能想到什么方法？"）
       - level 2: 方向提示（"这里和xxx定理有关系吗？"）
       - level 3: 明确引导（"试试用xxx公式展开这里"）
    3. 对学生的思考先认可再追问
    4. 如果学生偏题，温柔拉回来
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        language: str = "zh",
        api_version: str | None = None,
        binding: str = "openai",
    ):
        super().__init__(
            module_name="tutor",
            agent_name="socratic_agent",
            api_key=api_key,
            base_url=base_url,
            api_version=api_version,
            language=language,
            binding=binding,
        )

    _CONTENT_MAX_CHARS = 150

    def _format_history(self, history: list[dict[str, Any]], last_n: int = 6) -> str:
        """格式化最近 N 条对话历史，content 截取前 _CONTENT_MAX_CHARS 字符"""
        recent = history[-last_n:]
        label_map = {"student": "学生", "tutor": "老师", "system": "系统"}
        limit = self._CONTENT_MAX_CHARS
        lines = [
            f"[{label_map.get(m['role'], m['role'])}]: {m['content'][:limit]}"
            for m in recent
        ]
        return "\n".join(lines) if lines else "（对话刚开始）"

    def _build_user_prompt(
        self,
        problem: str,
        checkpoint: Checkpoint,
        interaction_history: list[dict[str, Any]],
        student_response: str,
    ) -> str:
        template = self.get_prompt("user_template")
        if not template:
            raise ValueError("SocraticAgent: user_template prompt not configured")
        return template.format(
            problem=problem,
            checkpoint_description=checkpoint.description,
            guiding_question=checkpoint.guiding_question,
            hint_level=checkpoint.hint_level,
            chat_history=self._format_history(interaction_history),
            student_response=student_response,
        )

    async def process(
        self,
        problem: str,
        checkpoint: Checkpoint,
        interaction_history: list[dict[str, Any]],
        student_response: str,
    ) -> str:
        """
        生成下一句引导语

        Args:
            problem: 题目原文
            checkpoint: 当前 checkpoint（含 guiding_question 和 hint_level）
            interaction_history: 完整对话历史
            student_response: 学生最新回复

        Returns:
            tutor 的下一句话（以问题形式结尾）
        """
        system_prompt = self.get_prompt("system")
        if not system_prompt:
            raise ValueError("SocraticAgent: system prompt not configured")

        user_prompt = self._build_user_prompt(
            problem, checkpoint, interaction_history, student_response
        )

        return await self.call_llm(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=0.7,  # 引导语需要一些自然变化
            stage="socratic",
        )

    async def stream_process(
        self,
        problem: str,
        checkpoint: Checkpoint,
        interaction_history: list[dict[str, Any]],
        student_response: str,
    ) -> AsyncGenerator[str, None]:
        """流式版本，用于前端实时显示"""
        system_prompt = self.get_prompt("system")
        if not system_prompt:
            raise ValueError("SocraticAgent: system prompt not configured")

        user_prompt = self._build_user_prompt(
            problem, checkpoint, interaction_history, student_response
        )

        async for chunk in self.stream_llm(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=0.7,
            stage="socratic_stream",
        ):
            yield chunk
