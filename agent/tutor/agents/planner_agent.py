#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PlannerAgent - 解题引导方案规划师

生成苏格拉底引导的 checkpoint 序列。

核心设计原则（减少不必要的 PathEvaluator 触发）：
  Checkpoint 以结果（outcome）为目标，而非方法（method）。
  这样因式分解和解方程等等价变体在同一个 checkpoint 内都能通过，
  不会因为路径细节不同而触发替代解法评估。

  ❌  "使用配方法将 f(x) 变形为 (x-2)² - 1"
  ✓   "找到函数取最小值时的 x 和对应的函数值"
"""

import json
import re
from typing import Any

from agent.base_agent import BaseAgent
from agent.context_governance.assembler import assemble
from agent.context_governance.budget_policy import PLANNER_POLICY
from ..data_structures import Checkpoint, GranularityLevel, ProblemContext, SolutionPlan

# 答案超过此长度时截断为 outline
_ANSWER_OUTLINE_THRESHOLD = 300


class PlannerAgent(BaseAgent):
    """引导路线规划师（结果导向，方法中立）"""

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
            agent_name="planner_agent",
            api_key=api_key,
            base_url=base_url,
            api_version=api_version,
            language=language,
            binding=binding,
        )

    def _parse_plan(self, response: str) -> dict[str, Any]:
        try:
            return json.loads(response.strip())
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", response)
            if match:
                return json.loads(match.group())
            raise ValueError(f"PlannerAgent: cannot parse JSON: {response[:300]}")

    @staticmethod
    def _make_answer_outline(answer: str) -> str:
        """长答案截断为 outline，保留首尾关键内容。"""
        if len(answer) <= _ANSWER_OUTLINE_THRESHOLD:
            return answer
        half = _ANSWER_OUTLINE_THRESHOLD // 2
        return answer[:half] + "\n…（中间步骤省略）…\n" + answer[-half:]

    async def process(
        self,
        problem_context: ProblemContext,
        error_description: str,
        start_from: str,
        granularity: GranularityLevel,
        alternative_method: str | None = None,
        supplementary_cards: str | None = None,
        progress_snapshot: str | None = None,
    ) -> SolutionPlan:
        """
        生成引导方案

        Args:
            problem_context: 题目上下文（含答案和知识卡片）
            error_description: 学生错误情况
            start_from: 从哪里开始引导
            granularity: 引导粒度
            alternative_method: 若学生使用了替代解法，此处传入方法名，
                                Planner 将按该方法生成 checkpoints
            supplementary_cards: RAG 检索到的补充知识卡（已格式化文本）
            progress_snapshot: 重建时的既有进度快照（已通过步骤 + 当前目标 + 本轮提交摘要）

        Returns:
            SolutionPlan
        """
        system_prompt = self.get_prompt("system")
        user_template = self.get_prompt("user_template")

        if not system_prompt or not user_template:
            raise ValueError("PlannerAgent prompts not configured")

        # 预算化：用 top-k helper 替代全量聚合
        selected_methods = "、".join(problem_context.get_methods_for_llm(max_methods=3))
        planning_hints = "\n".join(
            f"- {h}" for h in problem_context.get_hints_for_llm(
                max_cards=2, max_items_per_card=2, max_total_items=3,
            )
        )
        solution_paths = problem_context.get_solution_paths_for_llm(max_paths=3)
        answer_outline = self._make_answer_outline(problem_context.answer)

        # 整 prompt 预算仲裁
        candidate = {
            "problem": problem_context.problem,
            "error_description": error_description,
            "selected_methods": selected_methods,
            "planning_hints": planning_hints,
            "solution_paths": solution_paths,
            "answer_outline": answer_outline,
        }
        if supplementary_cards:
            candidate["supplementary_cards"] = supplementary_cards
        if progress_snapshot:
            candidate["progress_snapshot"] = progress_snapshot
        assembly = assemble(
            candidate,
            PLANNER_POLICY,
            sig_parts={
                "task": "planner",
                "question_id": problem_context.problem_id,
            },
        )
        p = assembly.payload

        # supplementary_cards 可能被预算裁剪掉
        supp_text = p.get("supplementary_cards", supplementary_cards or "")

        user_prompt = user_template.format(
            problem=p.get("problem", problem_context.problem),
            answer_outline=p.get("answer_outline", answer_outline),
            selected_methods=p.get("selected_methods", selected_methods),
            planning_hints=p.get("planning_hints", planning_hints),
            solution_paths=p.get("solution_paths", solution_paths),
            error_description=p.get("error_description", error_description),
            progress_snapshot=p.get("progress_snapshot", progress_snapshot or "（无进度快照）"),
            start_from=start_from,
            granularity=granularity.value,
            alternative_method=alternative_method or "（按通性通法规划）",
            supplementary_cards=supp_text or "（无补充知识卡）",
        )

        response = await self.call_llm(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            response_format={"type": "json_object"},
            temperature=0.3,
            stage="planning",
            context_meta=assembly.to_llm_context_metadata(),
        )

        data = self._parse_plan(response)

        checkpoints = [
            Checkpoint(
                index=cp["index"],
                description=cp["description"],
                guiding_question=cp["guiding_question"],
                hint_level=cp.get("hint_level", 1),
                prerequisite_tags=cp.get("prerequisite_tags", []),
            )
            for cp in data["checkpoints"]
        ]

        return SolutionPlan(
            approach_summary=data["approach_summary"],
            reset_reason=data.get("reset_reason", ""),
            checkpoints=checkpoints,
            granularity=granularity,
            alternative_method=alternative_method,
        )
