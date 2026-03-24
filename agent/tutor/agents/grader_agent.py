#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
GraderAgent - 批改判题助手（B Tutor）

只做事实核查，不做引导。
现在接收 ProblemContext（含标准答案和知识卡片），可以：
  1. 用答案做可靠的正误判断
  2. 用通性通法检测学生是否使用了替代解法
"""

import json
import re
from typing import Any

from agent.base_agent import BaseAgent
from agent.context_governance.assembler import assemble
from agent.context_governance.budget_policy import GRADER_POLICY
from ..data_structures import ErrorType, GraderResult, GranularityLevel, ProblemContext


_STUDENT_WORK_MAX_CHARS = 800


class GraderAgent(BaseAgent):
    """批改助手：严谨的事实核查者"""

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
            agent_name="grader_agent",
            api_key=api_key,
            base_url=base_url,
            api_version=api_version,
            language=language,
            binding=binding,
        )

    @staticmethod
    def _trim_student_work(text: str, max_chars: int = _STUDENT_WORK_MAX_CHARS) -> str:
        """保留首段（理解/设定）+ 尾段（结论）+ 中间关键段，总长不超过 max_chars。"""
        if len(text) <= max_chars:
            return text
        head_budget = max_chars // 3          # ~266 chars for setup
        tail_budget = max_chars // 3          # ~266 chars for conclusion
        mid_budget = max_chars - head_budget - tail_budget

        head = text[:head_budget]
        tail = text[-tail_budget:]

        # 从中间部分按段落选择最长的（通常含关键推导）
        middle = text[head_budget:-tail_budget]
        paragraphs = [p.strip() for p in middle.split("\n") if p.strip()]
        if paragraphs:
            paragraphs.sort(key=len, reverse=True)
            mid_parts: list[str] = []
            mid_len = 0
            for p in paragraphs:
                if mid_len + len(p) + 1 > mid_budget:
                    break
                mid_parts.append(p)
                mid_len += len(p) + 1
            mid_text = "\n".join(mid_parts)
        else:
            mid_text = middle[:mid_budget]

        return head + "\n…（中间省略）…\n" + mid_text + "\n…\n" + tail

    def _parse_result(self, response: str) -> dict[str, Any]:
        try:
            return json.loads(response.strip())
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", response)
            if match:
                return json.loads(match.group())
            raise ValueError(f"GraderAgent: cannot parse JSON: {response[:300]}")

    async def process(
        self,
        problem_context: ProblemContext,
        student_work: str,
    ) -> GraderResult:
        """
        批改学生的解题过程

        Args:
            problem_context: 题目上下文（含答案和知识卡片）
            student_work: 学生的解题过程（文字或 OCR 内容）

        Returns:
            GraderResult，包含错误类型、定位，以及替代解法标记
        """
        system_prompt = self.get_prompt("system")
        user_template = self.get_prompt("user_template")

        if not system_prompt or not user_template:
            raise ValueError("GraderAgent prompts not configured")

        intended_methods = "、".join(problem_context.get_methods_for_llm(max_methods=3))
        common_mistakes = "\n".join(
            f"- {m}" for m in problem_context.get_common_mistakes_for_llm(
                max_total_items=3, max_chars=200,
            )
        )
        trimmed_work = self._trim_student_work(student_work)

        candidate = {
            "problem": problem_context.problem,
            "answer": problem_context.answer,
            "intended_methods": intended_methods,
            "common_mistakes": common_mistakes,
            "student_work": trimmed_work,
        }
        assembly = assemble(
            candidate,
            GRADER_POLICY,
            sig_parts={
                "task": "grader",
                "question_id": problem_context.problem_id,
            },
        )
        p = assembly.payload

        user_prompt = user_template.format(
            problem=p.get("problem", problem_context.problem),
            answer=p.get("answer", problem_context.answer),
            intended_methods=p.get("intended_methods", intended_methods),
            common_mistakes=p.get("common_mistakes", common_mistakes),
            student_work=p.get("student_work", student_work),
        )

        response = await self.call_llm(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            response_format={"type": "json_object"},
            temperature=0.1,
            stage="grading",
            context_meta=assembly.to_llm_context_metadata(),
        )

        data = self._parse_result(response)

        return GraderResult(
            error_type=ErrorType(data["error_type"]),
            is_correct=data["is_correct"],
            error_description=data["error_description"],
            student_approach=data["student_approach"],
            error_location=data.get("error_location"),
            correction_note=data.get("correction_note"),
            suggested_granularity=GranularityLevel(data.get("suggested_granularity", 2)),
            uses_alternative_method=data.get("uses_alternative_method", False),
            alternative_method_name=data.get("alternative_method_name"),
        )
