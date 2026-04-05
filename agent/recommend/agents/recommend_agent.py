#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
RecommendAgent - 推荐决策 LLM

输入：RecommendContext（会话结果 + 长期记忆 + 近期情节）
输出：推荐决策（类型 + 目标 tags + 难度 + 说明文字）

不负责查题库，只负责：
  1. 判断该推荐哪种类型
  2. 指定查询目标（tags / difficulty / concept）
  3. 生成对学生的说明文字
"""

import json
import re
from typing import Any

from agent.base_agent import BaseAgent
from agent.context_governance.assembler import assemble
from agent.context_governance.budget_policy import RECOMMEND_POLICY
from ..data_structures import RecommendContext, RecommendationType


class RecommendAgent(BaseAgent):

    def __init__(
        self,
        api_key: str,
        base_url: str,
        language: str = "zh",
        api_version: str | None = None,
        binding: str = "openai",
    ):
        super().__init__(
            module_name="recommend",
            agent_name="recommend_agent",
            api_key=api_key,
            base_url=base_url,
            api_version=api_version,
            language=language,
            binding=binding,
        )

    async def process(self, ctx: RecommendContext) -> dict:
        """BaseAgent 兼容入口，路由到 decide。"""
        return await self.decide(ctx)

    async def decide(self, ctx: RecommendContext) -> dict:
        """
        决定推荐策略。

        Returns dict with keys:
          recommendation_type: str
          target_tags: list[str]
          target_difficulty: str | None
          concept_to_review: str | None
          retry_method: str | None
          explanation: str
        """
        system_prompt = self.get_prompt("system")
        template = self.get_prompt("decide")

        if not system_prompt or not template:
            return self._rule_based_decide(ctx)

        student_profile = (
            ctx.semantic_memory.to_recommend_snapshot(ctx.current_tags)
            if ctx.semantic_memory
            else "（暂无长期记忆）"
        )
        recent_problems = self._fmt_recent(ctx.recent_episodes)
        weak_concepts = ", ".join(ctx.get_weak_tags()[:4]) or "（暂无）"

        assembly = assemble(
            {
                "student_profile": student_profile,
                "weak_concepts": weak_concepts,
                "recent_problems": recent_problems,
            },
            RECOMMEND_POLICY,
            sig_parts={
                "task": "recommend",
                "student_id": ctx.student_id,
                "problem_id": ctx.current_problem_id,
            },
        )
        ap = assembly.payload

        user_prompt = template.format(
            source=ctx.source.value,
            outcome=self._extract_outcome(ctx),
            current_chapter=ctx.current_chapter,
            current_tags=", ".join(ctx.current_tags) or "（无）",
            understanding_quality=ctx.get_understanding_quality() or "（非复盘会话）",
            methods_explored=", ".join(
                ctx.session_export.get("methods_explored", [])
            ) or "（无）",
            retry_triggered=ctx.session_export.get("retry_triggered", False),
            weak_concepts=ap.get("weak_concepts", weak_concepts),
            student_profile=ap.get("student_profile", student_profile),
            recent_problems=ap.get("recent_problems", recent_problems),
            hints_given=ctx.session_export.get("total_hints_given", 0),
            total_attempts=ctx.session_export.get("total_attempts", 1),
        )

        response = await self.call_llm(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            response_format={"type": "json_object"},
            temperature=0.3,
            stage="recommend_decide",
            context_meta=assembly.to_llm_context_metadata(),
        )
        return self._parse(response, ctx)

    # -------------------------------------------------------------------------
    # Tool-Use 模式
    # -------------------------------------------------------------------------

    async def decide_with_tools(
        self,
        ctx: RecommendContext,
        tool_registry: Any,
        max_tool_rounds: int = 5,
    ) -> dict:
        """
        Tool-use 循环模式推荐决策。

        LLM 通过调用 tools 按需获取学生状态、题目信息，然后输出推荐。
        异常时 fallback 到单次调用的 decide()。
        """
        system_prompt = self.get_prompt("system")
        decide_template = self.get_prompt("decide")

        if not system_prompt or not decide_template or tool_registry is None:
            return await self.decide(ctx)

        # 基础 context（极轻量，只有当次会话摘要）
        user_prompt = decide_template.format(
            source=ctx.source.value,
            outcome=self._extract_outcome(ctx),
            current_chapter=ctx.current_chapter,
            current_tags=", ".join(ctx.current_tags) or "（无）",
            current_problem_id=ctx.current_problem_id or "（无）",
            hints_given=ctx.session_export.get("total_hints_given", 0),
            total_attempts=ctx.session_export.get("total_attempts", 1),
            methods_used=", ".join(ctx.session_export.get("methods_used", [])) or "未知",
            error_types=", ".join(ctx.session_export.get("error_types_seen", [])) or "无",
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        tools = tool_registry.get_tool_schemas()

        for round_idx in range(max_tool_rounds):
            try:
                result = await self.call_llm_with_tools(
                    user_prompt="",
                    system_prompt="",
                    tools=tools,
                    tool_choice="auto",
                    messages=messages,
                    temperature=0.3,
                    stage=f"recommend_tool_round_{round_idx}",
                )
            except Exception as exc:
                self.logger.warning(f"Tool-use round {round_idx} failed: {exc}, falling back")
                return await self.decide(ctx)

            if not result.has_tool_calls:
                return self._parse(result.content, ctx)

            # Append assistant message with tool calls
            messages.append({
                "role": "assistant",
                "content": result.content or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in result.tool_calls
                ],
            })

            # Execute tools and append results
            for tc in result.tool_calls:
                tool_result = await tool_registry.execute(tc.name, tc.arguments)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result,
                })

        # Max rounds exceeded: force final response
        self.logger.warning(f"Tool-use exceeded {max_tool_rounds} rounds, forcing final")
        try:
            final = await self.call_llm(
                user_prompt="",
                system_prompt="",
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.3,
                stage="recommend_tool_final",
            )
            return self._parse(final, ctx)
        except Exception:
            return await self.decide(ctx)

    # -------------------------------------------------------------------------
    # Parsing
    # -------------------------------------------------------------------------

    def _parse(self, response: str, ctx: RecommendContext) -> dict:
        try:
            data = json.loads(response.strip())
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", response)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    return self._rule_based_decide(ctx)
            else:
                return self._rule_based_decide(ctx)

        try:
            rec_type = RecommendationType(data.get("recommendation_type", "similar_problem"))
        except ValueError:
            rec_type = RecommendationType.SIMILAR_PROBLEM

        return {
            "recommendation_type": rec_type,
            "target_tags": data.get("target_tags", ctx.current_tags),
            "target_difficulty": data.get("target_difficulty"),
            "concept_to_review": data.get("concept_to_review"),
            "retry_method": data.get("retry_method"),
            "explanation": data.get("explanation", "继续练习吧！"),
            "recommended_problems": data.get("recommended_problems", []),
            "reasoning": data.get("reasoning", ""),
        }

    def _rule_based_decide(self, ctx: RecommendContext) -> dict:
        """无 prompt 时的规则降级"""
        outcome = self._extract_outcome(ctx)
        understanding = ctx.get_understanding_quality()

        # Review 后的降级策略
        if ctx.source.value == "review":
            if understanding == "not_understood":
                rec_type = RecommendationType.EASIER_PROBLEM
                explanation = "这道题的核心概念还需要再巩固，先做一道基础题。"
            elif understanding == "partial":
                rec_type = RecommendationType.SIMILAR_PROBLEM
                explanation = "理解有些模糊，再做一道类似的题巩固一下。"
            else:
                rec_type = RecommendationType.HARDER_PROBLEM
                explanation = "掌握得不错！挑战一道难一点的题。"
        # Tutor 后的降级策略
        elif outcome == "solved":
            hints = ctx.session_export.get("total_hints_given", 0)
            rec_type = (
                RecommendationType.HARDER_PROBLEM if hints <= 2
                else RecommendationType.SIMILAR_PROBLEM
            )
            explanation = "做出来了！继续保持。" if hints <= 2 else "做出来了，再巩固一道相似的题。"
        elif outcome == "gave_up":
            rec_type = RecommendationType.EASIER_PROBLEM
            explanation = "这道题有点难，先从基础题找回感觉。"
        else:
            rec_type = RecommendationType.SIMILAR_PROBLEM
            explanation = "继续练习吧！"

        weak = ctx.get_weak_tags()
        target_tags = weak[:2] if weak and rec_type == RecommendationType.EASIER_PROBLEM else ctx.current_tags

        return {
            "recommendation_type": rec_type,
            "target_tags": target_tags,
            "target_difficulty": None,
            "concept_to_review": weak[0] if weak and rec_type == RecommendationType.REVIEW_CONCEPT else None,
            "retry_method": None,
            "explanation": explanation,
        }

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _extract_outcome(self, ctx: RecommendContext) -> str:
        export = ctx.session_export
        outcome = export.get("outcome", "")
        status = export.get("status", "")
        if status == "solved" or outcome == "solved":
            return "solved"
        if outcome == "gave_up" or status == "abandoned":
            return "gave_up"
        if outcome == "explored":
            return "explored"
        return outcome or status or "in_progress"

    def _fmt_recent(self, episodes: list) -> str:
        if not episodes:
            return "（无近期记录）"
        lines = []
        for ep in episodes[:5]:
            lines.append(
                f"  {ep.created_at.strftime('%m-%d')} "
                f"[{ep.source.value}] {ep.chapter} — {ep.outcome}"
            )
        return "\n".join(lines)
