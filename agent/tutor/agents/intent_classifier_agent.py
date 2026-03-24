#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
TutorActionClassifierAgent - LLM 动作路由分类器

输入：
  - problem_context
  - student_message
  - interaction_history
  - session_context

输出：
  {"primary_action": str, "target_step": int|None,
   "confidence": float, "reason": str}
"""

from typing import Any

from agent.base_agent import BaseAgent
from agent.utils import safe_parse_json
from ..data_structures import ProblemContext, TutorAction


class TutorActionClassifierAgent(BaseAgent):
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
            agent_name="intent_classifier_agent",
            api_key=api_key,
            base_url=base_url,
            api_version=api_version,
            language=language,
            binding=binding,
        )

    async def process(
        self,
        problem_context: ProblemContext,
        student_message: str,
        interaction_history: list[dict[str, Any]],
        session_context: str,
    ) -> dict[str, Any]:
        """
        LLM 动作分类。

        Returns:
            {"primary_action": str, "target_step": int|None,
             "confidence": float, "reason": str}
        """
        return await self.classify_action(
            problem_context=problem_context,
            student_message=student_message,
            interaction_history=interaction_history,
            session_context=session_context,
        )

    async def classify_action(
        self,
        problem_context: ProblemContext,
        student_message: str,
        interaction_history: list[dict[str, Any]],
        session_context: str,
    ) -> dict[str, Any]:
        """
        LLM 动作分类。

        Returns:
            {"primary_action": str, "target_step": int|None,
             "confidence": float, "reason": str}
        """
        system_prompt = self.get_prompt("system")
        template = self.get_prompt("classify_action")
        if not system_prompt or not template:
            return {
                "primary_action": TutorAction.CONTINUE_SOCRATIC.value,
                "target_step": None,
                "confidence": 0.0,
                "reason": "missing_prompt",
            }

        user_prompt = template.format(
            problem=problem_context.problem,
            session_context=session_context,
            recent_history=self._fmt_rich_history(interaction_history[-6:]),
            student_message=student_message,
        )
        response = await self.call_llm(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            response_format={"type": "json_object"},
            temperature=0.1,
            stage="classify_action",
        )
        return self._parse_action(response)

    _safe_json = staticmethod(safe_parse_json)

    def _parse_action(self, response: str) -> dict[str, Any]:
        data = self._safe_json(response)
        action_str = str(data.get("primary_action", "continue_socratic")).strip().lower()
        try:
            TutorAction(action_str)
        except ValueError:
            action_str = TutorAction.CONTINUE_SOCRATIC.value
        target_step = data.get("target_step")
        if isinstance(target_step, (int, float)):
            target_step = int(target_step)
        else:
            target_step = None
        try:
            confidence = float(data.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        return {
            "primary_action": action_str,
            "target_step": target_step,
            "confidence": max(0.0, min(1.0, confidence)),
            "reason": str(data.get("reason", "")),
        }

    _CONTENT_MAX_CHARS = 150

    @staticmethod
    def _fmt_rich_history(
        history: list[dict[str, Any]],
        max_content_chars: int = 150,
    ) -> str:
        """带 metadata 标注的历史格式，content 截取前 max_content_chars 字符"""
        if not history:
            return "（无历史）"
        lines: list[str] = []
        for h in history:
            role = h.get("role", "")
            meta = h.get("metadata", {}) or {}
            content = h.get("content", "")[:max_content_chars]
            if role == "student":
                cp_idx = meta.get("checkpoint_index")
                attempt = meta.get("attempt")
                tags = []
                if cp_idx is not None:
                    tags.append(f"checkpoint={cp_idx}")
                if attempt is not None:
                    tags.append(f"attempt={attempt}")
                tag_str = f" [{', '.join(tags)}]" if tags else ""
                lines.append(f"学生{tag_str}：{content}")
            elif role == "tutor":
                msg_type = meta.get("type", "")
                hint_level = meta.get("hint_level")
                rnd = meta.get("round")
                tags = []
                if msg_type:
                    tags.append(msg_type)
                if hint_level is not None:
                    tags.append(f"hint={hint_level}")
                if rnd is not None:
                    tags.append(f"round={rnd}")
                tag_str = f" [{', '.join(str(t) for t in tags)}]" if tags else ""
                lines.append(f"导师{tag_str}：{content}")
            else:
                lines.append(f"系统：{content}")
        return "\n".join(lines)
