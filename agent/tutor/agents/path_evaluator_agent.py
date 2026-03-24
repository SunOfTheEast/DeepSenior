#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PathEvaluatorAgent - 替代解法教学对齐评估器

在检测到学生使用了与知识卡片通性通法不同的方法时触发。
判断三个维度：
  1. 数学有效性：这条路能走通吗？
  2. 教学对齐：走通了，但目标知识点练到了吗？
  3. 处置建议：accept / redirect_gentle / accept_with_flag

调用时机（由 TutorManager 控制）：
  - Grader 返回 uses_alternative_method=True 时
  - Router.evaluate_checkpoint 返回 used_alternative_method=True 时
  不在每次 submission 都调用，只在方法实质性偏离时才触发。
"""

import json
import re
from typing import Any

from agent.base_agent import BaseAgent
from agent.context_governance.assembler import assemble
from agent.context_governance.budget_policy import PATH_EVALUATOR_POLICY
from ..data_structures import (
    AlternativeRecommendation,
    KnowledgeCard,
    PathEvaluationResult,
    PedagogicalAlignment,
    ProblemContext,
)

# 学生解题过程片段最大长度
_MAX_WORK_EXCERPT_CHARS = 500


class PathEvaluatorAgent(BaseAgent):
    """替代解法教学对齐评估器"""

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
            agent_name="path_evaluator_agent",
            api_key=api_key,
            base_url=base_url,
            api_version=api_version,
            language=language,
            binding=binding,
        )

    def _parse(self, response: str) -> dict[str, Any]:
        try:
            return json.loads(response.strip())
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", response)
            if match:
                return json.loads(match.group())
            raise ValueError(f"PathEvaluatorAgent: cannot parse JSON: {response[:300]}")

    @staticmethod
    def _build_target_skills(cards: list[KnowledgeCard], max_cards: int = 3) -> str:
        """从知识卡片提取核心技能：标题 + 第一条通性通法。"""
        parts: list[str] = []
        for kc in cards[:max_cards]:
            skill = kc.general_methods[0] if kc.general_methods else kc.title
            parts.append(f"- {kc.title}（{skill}）")
        return "\n".join(parts)

    @staticmethod
    def _build_alignment_constraints(cards: list[KnowledgeCard], max_cards: int = 2) -> str:
        """提取不可绕过的核心训练点：每卡 1 条易错点。"""
        parts: list[str] = []
        for kc in cards[:max_cards]:
            if kc.common_mistakes:
                parts.append(f"- {kc.title}: {kc.common_mistakes[0]}")
        return "\n".join(parts) if parts else "（无特殊约束）"

    @staticmethod
    def _truncate_excerpt(text: str) -> str:
        if len(text) <= _MAX_WORK_EXCERPT_CHARS:
            return text
        half = _MAX_WORK_EXCERPT_CHARS // 2
        return text[:half] + "\n…（省略）…\n" + text[-half:]

    async def process(
        self,
        problem_context: ProblemContext,
        student_approach: str,
        student_work_excerpt: str,
    ) -> PathEvaluationResult:
        """
        评估学生的替代解法

        Args:
            problem_context: 题目上下文（含答案和知识卡片）
            student_approach: 学生使用方法的一句话概括（来自 GraderResult）
            student_work_excerpt: 学生解题过程片段（用于判断是否能走通）

        Returns:
            PathEvaluationResult
        """
        system_prompt = self.get_prompt("system")
        user_template = self.get_prompt("user_template")

        if not system_prompt or not user_template:
            raise ValueError("PathEvaluatorAgent prompts not configured")

        # 预算化：用精简投影替代全量聚合
        target_skills = self._build_target_skills(problem_context.knowledge_cards)
        target_methods = "、".join(problem_context.get_methods_for_llm(max_methods=3))
        alignment_constraints = self._build_alignment_constraints(
            problem_context.knowledge_cards,
        )
        excerpt = self._truncate_excerpt(student_work_excerpt)
        answer_outline = student_approach  # PathEval 不需要完整答案细节

        # 整 prompt 预算仲裁
        candidate = {
            "problem": problem_context.problem,
            "student_approach": student_approach,
            "student_work_excerpt": excerpt,
            "target_skills": target_skills,
            "target_methods": target_methods,
            "alignment_constraints": alignment_constraints,
            "answer_outline": problem_context.answer,
        }
        assembly = assemble(
            candidate,
            PATH_EVALUATOR_POLICY,
            sig_parts={
                "task": "path_evaluator",
                "question_id": problem_context.problem_id,
            },
        )
        p = assembly.payload

        user_prompt = user_template.format(
            problem=p.get("problem", problem_context.problem),
            answer=p.get("answer_outline", problem_context.answer),
            target_skills=p.get("target_skills", target_skills),
            target_methods=p.get("target_methods", target_methods),
            alignment_constraints=p.get("alignment_constraints", alignment_constraints),
            student_approach=p.get("student_approach", student_approach),
            student_work_excerpt=p.get("student_work_excerpt", excerpt),
        )

        response = await self.call_llm(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            response_format={"type": "json_object"},
            temperature=0.1,
            stage="path_evaluation",
            context_meta=assembly.to_llm_context_metadata(),
        )

        data = self._parse(response)

        return PathEvaluationResult(
            is_mathematically_valid=data["is_mathematically_valid"],
            pedagogical_alignment=PedagogicalAlignment(data["pedagogical_alignment"]),
            recommendation=AlternativeRecommendation(data["recommendation"]),
            student_approach_summary=data["student_approach_summary"],
            student_method_name=data.get("student_method_name", ""),
            redirect_reason=data.get("redirect_reason"),
            replan_start_from=data.get("replan_start_from"),
        )
