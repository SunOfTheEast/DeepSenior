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


# Grader 只需要"足够判断对错和路径"的信息，不需要整篇作文。
# 这个上限是质量/成本折中：太短会误判，太长会让 prompt 噪声变大且更贵。
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
        # 经验上，首段常含"设了什么"、尾段常含"结论写到哪"。
        # 中段如果整段保留，容易把 token 用在重复推导上，因此只抓关键段落。
        head_budget = max_chars // 3          # ~266 chars for setup
        tail_budget = max_chars // 3          # ~266 chars for conclusion
        mid_budget = max_chars - head_budget - tail_budget

        head = text[:head_budget]
        tail = text[-tail_budget:]

        # 从中间部分按段落选择最长的（通常含关键推导和转折）
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

    @staticmethod
    def _coerce_bool(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "y"}:
                return True
            if normalized in {"false", "0", "no", "n"}:
                return False
        return default

    @staticmethod
    def _normalize_text(value: Any, fallback: str) -> str:
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
        return fallback

    @staticmethod
    def _normalize_optional_text(value: Any) -> str | None:
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
        return None

    @staticmethod
    def _fallback_student_approach(student_work: str) -> str:
        condensed = " ".join(
            part.strip() for part in str(student_work).splitlines() if part.strip()
        )
        condensed = re.sub(r"\s+", " ", condensed).strip()
        if not condensed:
            return "学生提交了一版未能可靠解析的解题尝试"
        if len(condensed) > 80:
            return condensed[:80] + "…"
        return condensed

    @staticmethod
    def _normalize_error_type(value: Any) -> ErrorType:
        if isinstance(value, ErrorType):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            try:
                return ErrorType(normalized)
            except ValueError:
                pass
        return ErrorType.WRONG_PATH_MINOR

    @staticmethod
    def _normalize_granularity(value: Any) -> GranularityLevel:
        if isinstance(value, GranularityLevel):
            return value
        try:
            return GranularityLevel(int(value))
        except (TypeError, ValueError):
            return GranularityLevel.MEDIUM

    def _parse_result(self, response: str) -> dict[str, Any]:
        # 主路径：要求模型直接回 JSON。
        # 兜底路径：若模型前后多说了文本，尝试抽取第一个 JSON 块。
        # 这里故意"严格失败"，避免 silent wrong parse。
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

        # 注意：这里把 methods/mistakes 作为"判题参考上下文"喂给 Grader。
        # 好处是减少瞎判；风险是会把"常见易错点"写进 error_description（例如"建议验算"）。
        # 这不是 bug，而是 prompt 设计带来的行为倾向。
        intended_methods = "、".join(problem_context.get_methods_for_llm(max_methods=3))
        common_mistakes = "\n".join(
            f"- {m}" for m in problem_context.get_common_mistakes_for_llm(
                max_total_items=3, max_chars=200,
            )
        )
        solution_paths = problem_context.get_solution_paths_for_llm(max_paths=2)
        trimmed_work = self._trim_student_work(student_work)

        # candidate -> assemble：让 Context Governance 在预算内裁剪字段，
        # 避免"学生作答太长"拖垮判题稳定性。
        candidate = {
            "problem": problem_context.problem,
            "answer": problem_context.answer,
            "intended_methods": intended_methods,
            "common_mistakes": common_mistakes,
            "solution_paths": solution_paths,
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

        # 这里非常关键：一旦 student_work 被裁剪，绝不回退全文。
        # 否则会重新把超长文本塞回 prompt，等于绕过预算治理。
        user_prompt = user_template.format(
            problem=p.get("problem", problem_context.problem),
            answer=p.get("answer", problem_context.answer),
            intended_methods=p.get("intended_methods", intended_methods),
            common_mistakes=p.get("common_mistakes", common_mistakes),
            solution_paths=p.get("solution_paths", solution_paths),
            student_work=p.get("student_work", trimmed_work),
        )

        response = await self.call_llm(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            response_format={"type": "json_object"},
            temperature=0.1,
            stage="grading",
            context_meta=assembly.to_llm_context_metadata(),
        )

        try:
            data = self._parse_result(response)
        except Exception as exc:
            self.logger.warning(
                f"GraderAgent parse failed; falling back to conservative defaults: {exc}"
            )
            data = {}

        error_type = self._normalize_error_type(data.get("error_type"))
        return GraderResult(
            error_type=error_type,
            is_correct=self._coerce_bool(data.get("is_correct"), default=False),
            error_description=self._normalize_text(
                data.get("error_description"),
                "判题结果字段缺失，按保守引导处理。",
            ),
            student_approach=self._normalize_text(
                data.get("student_approach"),
                self._fallback_student_approach(trimmed_work),
            ),
            error_location=self._normalize_optional_text(data.get("error_location")),
            correction_note=self._normalize_optional_text(data.get("correction_note")),
            suggested_granularity=self._normalize_granularity(
                data.get("suggested_granularity")
            ),
            uses_alternative_method=self._coerce_bool(
                data.get("uses_alternative_method"),
                default=False,
            ),
            alternative_method_name=self._normalize_optional_text(
                data.get("alternative_method_name")
            ),
        )
