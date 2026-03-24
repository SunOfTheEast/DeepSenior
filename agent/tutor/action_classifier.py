#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ActionClassifier - LLM 主导的动作路由

  - 单次 LLM 调用 classify_action（含完整 session_context）
  - 极简 fallback（LLM 失败时）
"""

from typing import Any, Callable

from agent.infra.logging import get_logger

from .context_builder import TutorContextBuilder
from .data_structures import TutorAction, TutorSession

_HISTORY_WINDOW = 6
_CONTENT_MAX_CHARS = 150


class ActionClassifier:

    def __init__(self, classify_action_skill: Callable, logger=None):
        self._classify = classify_action_skill
        self.logger = logger or get_logger("ActionClassifier")

    @staticmethod
    def _truncate_history(
        history: list[dict[str, Any]],
        max_entries: int = _HISTORY_WINDOW,
        max_content_chars: int = _CONTENT_MAX_CHARS,
    ) -> list[dict[str, Any]]:
        recent = history[-max_entries:]
        return [
            {**entry, "content": entry.get("content", "")[:max_content_chars]}
            for entry in recent
        ]

    async def classify(
        self,
        session: TutorSession,
        student_message: str,
    ) -> dict[str, Any]:
        context_text = TutorContextBuilder.build(session)
        try:
            result = await self._classify(
                problem_context=session.problem_context,
                student_message=student_message,
                interaction_history=self._truncate_history(session.interaction_history),
                session_context=context_text,
            )
            action = self._parse_action(result)
            action["is_fallback"] = False
            self.logger.info(
                f"[{session.session_id}] action={action['primary_action'].value}, "
                f"conf={action['confidence']:.2f}, reason={action['reason'][:80]}"
            )
            return action
        except Exception as e:
            self.logger.warning(
                f"[{session.session_id}] classify failed, using fallback: {e}"
            )
            fallback = self._fallback(session)
            fallback["is_fallback"] = True
            return fallback

    @staticmethod
    def _parse_action(raw: dict[str, Any]) -> dict[str, Any]:
        action_str = str(raw.get("primary_action", "continue_socratic")).strip().lower()
        try:
            action = TutorAction(action_str)
        except ValueError:
            action = TutorAction.CONTINUE_SOCRATIC

        target_step = raw.get("target_step")
        if isinstance(target_step, (int, float)):
            target_step = int(target_step)
        else:
            target_step = None

        try:
            confidence = float(raw.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5

        return {
            "primary_action": action,
            "target_step": target_step,
            "confidence": max(0.0, min(1.0, confidence)),
            "reason": str(raw.get("reason", "")),
        }

    @staticmethod
    def _fallback(session: TutorSession) -> dict[str, Any]:
        """LLM 失败时的极简护栏"""
        if getattr(session, "deep_dive_active", False):
            return {
                "primary_action": TutorAction.CONTINUE_DEEP_DIVE,
                "target_step": None,
                "confidence": 0.3,
                "reason": "llm_fallback:deep_dive_active",
            }
        return {
            "primary_action": TutorAction.CONTINUE_SOCRATIC,
            "target_step": None,
            "confidence": 0.3,
            "reason": "llm_fallback:default",
        }
