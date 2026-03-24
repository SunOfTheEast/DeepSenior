#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
RegressionHandler - 追问、软回顾与硬回退管理

处理学生对已完成 checkpoint 的追问和回退请求。
从 TutorManager 中提取，通过 ask_socratic_fn 回调执行回退后的引导。
"""

import re
from typing import Any, Callable

from agent.infra.logging import get_logger

from ..utils import compact_text
from .data_structures import TutorSession


def _clamp_checkpoint_index(session: TutorSession, index: int | None) -> int:
    if not session.solution_plan or not session.solution_plan.checkpoints:
        return 0
    max_idx = len(session.solution_plan.checkpoints) - 1
    if index is None:
        index = max(session.current_checkpoint - 1, 0)
    return max(0, min(int(index), max_idx))


class RegressionHandler:

    _STEP_INDEX_RE = re.compile(r"第(\d+)步")

    def __init__(self, ask_socratic_fn: Callable, logger=None):
        self._ask_socratic = ask_socratic_fn
        self.logger = logger or get_logger("RegressionHandler")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def handle_followup(
        self,
        session: TutorSession,
        student_message: str,
        target_checkpoint: int | None = None,
        reason: str | None = None,
        from_regression: bool = False,
    ) -> dict[str, Any]:
        if not session.solution_plan or not session.solution_plan.checkpoints:
            msg = "好的，我们先把你刚才的问题说清楚。你可以告诉我是哪个式子看不懂吗？"
            session.add_interaction("tutor", msg, {"type": "followup_no_plan"})
            return {"mode": "socratic", "message": msg, "followup": True}

        target_idx = self._resolve_followup_target(
            session=session,
            student_message=student_message,
            target_checkpoint=target_checkpoint,
        )
        cp = session.solution_plan.checkpoints[target_idx]
        current_idx = _clamp_checkpoint_index(session, session.current_checkpoint)

        msg = (
            f"好的，我们先回顾第 {target_idx + 1} 步，不改当前进度。\n\n"
            f"这一步的目标是：{cp.description}\n"
            f"你可以先围绕这个引导问题来想：**{cp.guiding_question}**"
        )
        if target_idx != current_idx:
            msg += f"\n\n回顾完后，我们再回到当前第 {current_idx + 1} 步继续。"
        if from_regression:
            note = reason or "检测到你在前置步骤可能有遗忘。"
            msg += f"\n\n我先做追问回顾（软回退），暂不重置进度。原因：{note}"

        session.add_interaction("tutor", msg, {
            "type": "followup",
            "target_checkpoint": target_idx,
            "current_checkpoint": current_idx,
            "from_regression": from_regression,
        })
        return {
            "mode": "socratic",
            "message": msg,
            "followup": True,
            "target_checkpoint": target_idx + 1,
            "current_checkpoint": current_idx + 1,
        }

    def decide_regression_action(
        self,
        session: TutorSession,
        student_message: str,
        regressed_to_checkpoint: int,
        checkpoint: Any | None,
    ) -> tuple[str, int]:
        """规则护栏：根据 attempts/hint_level 决定硬/软回退"""
        target_checkpoint = _clamp_checkpoint_index(session, regressed_to_checkpoint)
        if checkpoint:
            hard_by_attempts = checkpoint.attempts >= 3
            hard_by_hint_exhausted = (
                checkpoint.hint_level >= 3 and checkpoint.attempts >= 2
            )
            if hard_by_attempts or hard_by_hint_exhausted:
                return "hard", target_checkpoint

        return "soft", target_checkpoint

    async def handle_hard_regress(
        self,
        session: TutorSession,
        target_checkpoint: int,
        reason: str,
        student_message: str,
    ) -> dict[str, Any]:
        clamped_target = _clamp_checkpoint_index(session, target_checkpoint)
        session.regress_to(clamped_target)
        session.add_interaction(
            "system",
            f"[系统] 硬回退到 checkpoint {clamped_target + 1}。原因：{reason}",
        )
        return await self._ask_socratic(
            session,
            student_response=f"[硬回退到第{clamped_target + 1}步] {student_message}",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_referenced_checkpoint(
        self,
        session: TutorSession,
        message: str,
    ) -> int | None:
        compact = compact_text(message)
        match = self._STEP_INDEX_RE.search(compact)
        if not match:
            return None
        try:
            step_no = int(match.group(1))
        except (TypeError, ValueError):
            return None
        return _clamp_checkpoint_index(session, step_no - 1)

    def _resolve_followup_target(
        self,
        session: TutorSession,
        student_message: str,
        target_checkpoint: int | None = None,
    ) -> int:
        if target_checkpoint is not None:
            return _clamp_checkpoint_index(session, target_checkpoint)

        referenced = self._extract_referenced_checkpoint(session, student_message)
        if referenced is not None:
            return referenced

        compact = compact_text(student_message)
        if "上一步" in compact or "前一步" in compact or "前面" in compact:
            return _clamp_checkpoint_index(session, session.current_checkpoint - 1)
        return _clamp_checkpoint_index(session, session.current_checkpoint)
