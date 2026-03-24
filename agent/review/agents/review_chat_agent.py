#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ReviewChatAgent - 复盘对话 LLM

四个 skill：
  classify_intent      — 意图识别（JSON）
  respond              — 对比/解释/通用对话生成
  replay_errors        — 错误回放：对比学生原始失误与新方法的处理
  ask_understanding    — 生成理解验证问题（展示解法后调用）
  evaluate_understanding — 评估学生对理解验证问题的回答
"""

from typing import Any

from agent.base_agent import BaseAgent
from agent.tutor.data_structures import ProblemContext
from agent.utils import safe_parse_json
from ..data_structures import (
    ErrorSnapshot,
    MethodInfo,
    ReviewAction,
    SolvedMethod,
    StrugglePoint,
    UnderstandingQuality,
)


class ReviewChatAgent(BaseAgent):

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
            agent_name="review_chat_agent",
            api_key=api_key,
            base_url=base_url,
            api_version=api_version,
            language=language,
            binding=binding,
        )

    async def process(self, *args, **kwargs) -> Any:
        """
        BaseAgent 兼容入口。

        ReviewChatAgent 是多 skill 代理，这里默认路由到 `respond`，
        也支持通过 `skill` 参数显式指定子能力。
        """
        skill = kwargs.pop("skill", "respond")
        if skill == "respond":
            return await self.respond(*args, **kwargs)
        if skill == "classify_intent":
            return await self.classify_intent(*args, **kwargs)
        if skill == "replay_errors":
            return await self.replay_errors(*args, **kwargs)
        if skill == "ask_understanding":
            return await self.ask_understanding(*args, **kwargs)
        if skill == "ask_transfer":
            return await self.ask_transfer(*args, **kwargs)
        if skill == "evaluate_understanding":
            return await self.evaluate_understanding(*args, **kwargs)
        raise ValueError(f"Unsupported review skill for process(): {skill}")

    # -------------------------------------------------------------------------
    # Skill 1: 意图识别
    # -------------------------------------------------------------------------

    async def classify_intent(
        self,
        problem_context: ProblemContext,
        student_message: str,
        known_methods: list[MethodInfo],
        interaction_history: list[dict],
        session_context: str = "",
    ) -> tuple[ReviewAction, str | None]:
        system_prompt = self.get_prompt("system")
        template = self.get_prompt("classify_intent")

        if not system_prompt or not template:
            return self._fallback_action(student_message, known_methods)

        known_names = "\n".join(f"- {m.name}" for m in known_methods) or "（尚未枚举）"
        user_prompt = template.format(
            problem=problem_context.problem,
            session_context=session_context or "（无上下文）",
            known_methods=known_names,
            recent_history=self._fmt_history(interaction_history[-4:]),
            student_message=student_message,
        )
        try:
            response = await self.call_llm(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                response_format={"type": "json_object"},
                temperature=0.1,
                stage="classify_intent",
            )
            return self._parse_action_intent(response)
        except Exception as e:
            self.logger.warning(f"classify_intent LLM failed, using fallback: {e}")
            return self._fallback_action(student_message, known_methods)

    # -------------------------------------------------------------------------
    # Skill 2: 通用对话生成（对比/解释/自由）
    # -------------------------------------------------------------------------

    async def respond(
        self,
        problem_context: ProblemContext,
        student_message: str,
        context_str: str,
        interaction_history: list[dict],
    ) -> str:
        system_prompt = self.get_prompt("system")
        template = self.get_prompt("respond")
        if not system_prompt or not template:
            return "（复盘助手暂时无法回复，请重试）"
        user_prompt = template.format(
            problem=problem_context.problem,
            context=context_str,
            recent_history=self._fmt_history(interaction_history[-6:]),
            student_message=student_message,
        )
        return await self.call_llm(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=0.4,
            stage="review_respond",
        )

    # -------------------------------------------------------------------------
    # Skill 3: 错误回放
    # -------------------------------------------------------------------------

    async def replay_errors(
        self,
        problem_context: ProblemContext,
        error_snapshots: list[ErrorSnapshot],
        struggle_points: list[StrugglePoint],
        student_method_used: str,
        target_method: str | None,
        solved_demo: SolvedMethod | None,
        interaction_history: list[dict],
    ) -> str:
        """
        生成错误回放回复。

        对比学生原始失误（ErrorSnapshot）和挣扎点（StrugglePoint），
        展示目标方法如何处理同一步骤（如有 solved_demo）。
        """
        system_prompt = self.get_prompt("system")
        template = self.get_prompt("replay_errors")
        if not system_prompt or not template:
            return self._fallback_replay(error_snapshots, struggle_points)

        errors_text = "\n".join(
            f"- 类型：{e.error_type}，位置：{e.error_location}，"
            f"错误：{e.error_description}，正确做法：{e.correction_note}"
            for e in error_snapshots
        ) or "（无具体错误记录）"

        struggle_text = "\n".join(
            f"- 第{s.checkpoint_index+1}步「{s.description}」"
            f"（提示级别达到{s.hint_level_reached}，{'通过' if s.was_passed else '未通过'}）"
            for s in struggle_points
        ) or "（无挣扎记录）"

        demo_text = solved_demo.to_display() if solved_demo else "（尚未演示）"

        user_prompt = template.format(
            problem=problem_context.problem,
            student_method=student_method_used,
            target_method=target_method or "（未指定）",
            errors=errors_text,
            struggle_points=struggle_text,
            method_demo=demo_text,
            recent_history=self._fmt_history(interaction_history[-4:]),
        )
        return await self.call_llm(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=0.3,
            stage="replay_errors",
        )

    # -------------------------------------------------------------------------
    # Skill 4: 生成理解验证问题
    # -------------------------------------------------------------------------

    async def ask_understanding(
        self,
        problem_context: ProblemContext,
        solved_method: SolvedMethod,
        student_method_used: str,
    ) -> tuple[str, str]:
        """
        在展示解法后生成一个针对性的理解验证问题。

        Returns:
            (question_text, key_points)
            key_points 是评估学生回答时的参考要点（不展示给学生）
        """
        system_prompt = self.get_prompt("system")
        template = self.get_prompt("ask_understanding")
        if not system_prompt or not template:
            return (
                f"你觉得「{solved_method.method_name}」和你原来的「{student_method_used}」"
                f"最关键的差异是什么？",
                solved_method.key_insight,
            )

        user_prompt = template.format(
            problem=problem_context.problem,
            method_name=solved_method.method_name,
            key_insight=solved_method.key_insight,
            steps_summary="\n".join(f"{i+1}. {s}" for i, s in enumerate(solved_method.steps)),
            student_method=student_method_used,
        )
        response = await self.call_llm(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            response_format={"type": "json_object"},
            temperature=0.3,
            stage="ask_understanding",
        )
        return self._parse_understanding_question(response, solved_method)

    async def ask_transfer(
        self,
        problem_context: ProblemContext,
        solved_method: SolvedMethod,
        student_method_used: str,
    ) -> tuple[str, str]:
        """
        生成“迁移验证”问题（微变式），用于判断学生能否把方法迁移到相近情境。
        """
        system_prompt = self.get_prompt("system")
        template = self.get_prompt("ask_transfer")
        fallback_question = (
            f"如果把题目条件稍作变化，你认为「{solved_method.method_name}」"
            f"哪一步必须调整？为什么？"
        )
        if not system_prompt or not template:
            return fallback_question, solved_method.key_insight

        user_prompt = template.format(
            problem=problem_context.problem,
            method_name=solved_method.method_name,
            key_insight=solved_method.key_insight,
            steps_summary="\n".join(f"{i+1}. {s}" for i, s in enumerate(solved_method.steps)),
            student_method=student_method_used,
        )
        response = await self.call_llm(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            response_format={"type": "json_object"},
            temperature=0.3,
            stage="ask_transfer",
        )
        return self._parse_understanding_question(
            response=response,
            solved_method=solved_method,
            fallback_question=fallback_question,
        )

    # -------------------------------------------------------------------------
    # Skill 5: 评估理解验证回答
    # -------------------------------------------------------------------------

    async def evaluate_understanding(
        self,
        problem_context: ProblemContext,
        method_name: str,
        question: str,
        key_points: str,
        student_response: str,
        solved_demo: SolvedMethod,
    ) -> tuple[UnderstandingQuality, str]:
        """
        评估学生对理解验证问题的回答。

        Returns:
            (quality, feedback_message)
        """
        system_prompt = self.get_prompt("system")
        template = self.get_prompt("evaluate_understanding")
        if not system_prompt or not template:
            # 规则降级：回答长于 20 字视为 PARTIAL
            quality = (
                UnderstandingQuality.PARTIAL
                if len(student_response.strip()) > 20
                else UnderstandingQuality.NOT_UNDERSTOOD
            )
            return quality, "感谢你的回答！让我们继续探索。"

        user_prompt = template.format(
            problem=problem_context.problem,
            method_name=method_name,
            question=question,
            key_points=key_points,
            student_response=student_response,
            key_insight=solved_demo.key_insight,
        )
        response = await self.call_llm(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            response_format={"type": "json_object"},
            temperature=0.2,
            stage="evaluate_understanding",
        )
        return self._parse_understanding_eval(response)

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _parse_action_intent(self, response: str) -> tuple[ReviewAction, str | None]:
        data = self._safe_json(response)
        intent_str = str(data.get("intent", ReviewAction.GENERAL.value)).strip().lower()
        try:
            action = ReviewAction(intent_str)
        except ValueError:
            action = ReviewAction.GENERAL
        method_target = data.get("method_target")
        if isinstance(method_target, str):
            method_target = method_target.strip() or None
        else:
            method_target = None
        return action, method_target

    @staticmethod
    def _fallback_action(
        message: str, known_methods: list[MethodInfo]
    ) -> tuple[ReviewAction, str | None]:
        msg = message
        if any(kw in msg for kw in ["哪里错", "出错", "错在", "我的错误", "做错"]):
            return ReviewAction.REPLAY_ERRORS, None
        if any(kw in msg for kw in ["有哪些", "几种方法", "哪些方法"]):
            return ReviewAction.ENUMERATE_METHODS, None
        if any(kw in msg for kw in ["重新做", "我来试试", "我想试"]):
            for m in known_methods:
                if m.name in message:
                    return ReviewAction.RETRY_WITH_METHOD, m.name
            return ReviewAction.RETRY_WITH_METHOD, None
        if any(kw in msg for kw in ["怎么做", "演示", "展示", "讲解"]):
            for m in known_methods:
                if m.name in message:
                    return ReviewAction.SHOW_SOLUTION, m.name
            return ReviewAction.SHOW_SOLUTION, None
        if any(kw in msg for kw in ["哪个更好", "对比", "比较"]):
            return ReviewAction.COMPARE_METHODS, None
        if any(kw in msg for kw in ["为什么", "原理", "推导"]):
            return ReviewAction.EXPLAIN_CONCEPT, None
        return ReviewAction.GENERAL, None

    def _parse_understanding_question(
        self,
        response: str,
        solved_method: SolvedMethod,
        fallback_question: str | None = None,
    ) -> tuple[str, str]:
        data = self._safe_json(response)
        question = data.get("question") or (
            fallback_question
            or f"「{solved_method.method_name}」的核心切入点是什么？"
        )
        key_points = data.get("key_points") or solved_method.key_insight
        return question, key_points

    def _parse_understanding_eval(self, response: str) -> tuple[UnderstandingQuality, str]:
        data = self._safe_json(response)
        quality_str = data.get("quality", "not_understood")
        try:
            quality = UnderstandingQuality(quality_str)
        except ValueError:
            quality = UnderstandingQuality.PARTIAL
        feedback = data.get("feedback", "好的，我们继续。")
        return quality, feedback

    @staticmethod
    def _safe_json(response: str) -> dict:
        return safe_parse_json(response)

    def _fallback_replay(
        self,
        error_snapshots: list[ErrorSnapshot],
        struggle_points: list[StrugglePoint],
    ) -> str:
        lines = ["**你的辅导记录回放：**\n"]
        for e in error_snapshots:
            lines.append(f"- **{e.error_type}**：{e.error_location}\n  错误：{e.error_description}\n  正确：{e.correction_note}")
        for s in struggle_points:
            lines.append(f"- 步骤「{s.description}」花了最多提示（级别{s.hint_level_reached}）")
        return "\n".join(lines)

    def _fmt_history(self, history: list[dict]) -> str:
        lines = []
        for h in history:
            role = "学生" if h.get("role") == "student" else "复盘助手"
            lines.append(f"{role}：{h.get('content', '')}")
        return "\n".join(lines) or "（无历史）"
