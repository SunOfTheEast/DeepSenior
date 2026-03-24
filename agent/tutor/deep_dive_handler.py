#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DeepDiveHandler - 深问子流程管理

管理深问的启动、轮次推进、超纲检测、收束恢复。
从 TutorManager 中提取，通过 ask_socratic_fn 回调与主线协作。
"""

from datetime import datetime
from typing import Any, Callable

from agent.infra.logging import get_logger

from .data_structures import TutorSession


def _clamp_checkpoint_index(session: TutorSession, index: int | None) -> int:
    if not session.solution_plan or not session.solution_plan.checkpoints:
        return 0
    max_idx = len(session.solution_plan.checkpoints) - 1
    if index is None:
        index = max(session.current_checkpoint - 1, 0)
    return max(0, min(int(index), max_idx))


class DeepDiveHandler:

    _DEEP_DIVE_MAX_ROUNDS = 2

    def __init__(self, ask_socratic_fn: Callable, logger=None):
        self._ask_socratic = ask_socratic_fn
        self.logger = logger or get_logger("DeepDiveHandler")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(
        self,
        session: TutorSession,
        student_message: str,
        intent_decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._ensure_state(session)
        topic = self._truncate_topic(student_message)
        return_checkpoint = _clamp_checkpoint_index(session, session.current_checkpoint)
        session.deep_dive_active = True
        session.deep_dive_rounds = 1
        session.deep_dive_return_checkpoint = return_checkpoint
        session.deep_dive_topic = topic

        self._append_record(
            session,
            "start",
            topic=topic,
            return_checkpoint=return_checkpoint,
            action_reason=str((intent_decision or {}).get("reason", "")),
        )

        msg = self._build_response(session, student_message, round_no=1)
        session.add_interaction("tutor", msg, {
            "type": "deep_dive",
            "round": 1,
            "topic": topic,
            "return_checkpoint": return_checkpoint,
        })
        return {
            "mode": "deep_dive",
            "message": msg,
            "deep_dive_round": 1,
            "deep_dive_max_rounds": self._DEEP_DIVE_MAX_ROUNDS,
            "return_checkpoint": return_checkpoint + 1,
        }

    async def handle_turn(
        self,
        session: TutorSession,
        student_message: str,
    ) -> dict[str, Any]:
        """
        处理深问中的一轮对话。

        子路由（resume/understood/out-of-scope）已移至 LLM action classifier，
        此方法只做轮次计数 + 预算检查 + 响应构建。
        """
        self._ensure_state(session)

        # 规则护栏：轮次预算
        rounds = int(getattr(session, "deep_dive_rounds", 0))
        if rounds >= self._DEEP_DIVE_MAX_ROUNDS:
            topic = getattr(session, "deep_dive_topic", "") or student_message
            self._enqueue_deferred(
                session,
                topic=topic,
                reason="本次深问超过轮次预算，自动收束并回主线",
            )
            return await self._do_close_and_resume(
                session=session,
                closure_message=(
                    "这次深问我们先到这里（已达本次 2 轮预算）。"
                    "我已把后续延伸点登记为课后任务，先回主线继续做题。"
                ),
                closed_reason="budget_exceeded",
                understanding="partial",
            )

        rounds += 1
        session.deep_dive_rounds = rounds
        msg = self._build_response(session, student_message, round_no=rounds)
        if rounds >= self._DEEP_DIVE_MAX_ROUNDS:
            msg += "\n\n这轮之后如果还想继续深挖，我会先登记课后任务，再带你回到主线。"

        session.add_interaction("tutor", msg, {
            "type": "deep_dive",
            "round": rounds,
            "topic": getattr(session, "deep_dive_topic", ""),
            "return_checkpoint": getattr(session, "deep_dive_return_checkpoint", None),
        })
        self._append_record(
            session,
            "round",
            round=rounds,
            student_message=self._truncate_topic(student_message, limit=200),
        )
        return {
            "mode": "deep_dive",
            "message": msg,
            "deep_dive_round": rounds,
            "deep_dive_max_rounds": self._DEEP_DIVE_MAX_ROUNDS,
            "return_checkpoint": _clamp_checkpoint_index(
                session,
                getattr(session, "deep_dive_return_checkpoint", session.current_checkpoint),
            ) + 1,
        }

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _ensure_state(session: TutorSession) -> None:
        if not hasattr(session, "deep_dive_active"):
            session.deep_dive_active = False
        if not hasattr(session, "deep_dive_rounds"):
            session.deep_dive_rounds = 0
        if not hasattr(session, "deep_dive_return_checkpoint"):
            session.deep_dive_return_checkpoint = None
        if not hasattr(session, "deep_dive_topic"):
            session.deep_dive_topic = ""
        if not hasattr(session, "deep_dive_records"):
            session.deep_dive_records = []
        if not hasattr(session, "deferred_deep_dive_tasks"):
            session.deferred_deep_dive_tasks = []

    ensure_state = _ensure_state  # allow external access

    # ------------------------------------------------------------------
    # Public close (called by TutorManager on CLOSE_DEEP_DIVE action)
    # ------------------------------------------------------------------

    async def close_and_resume(
        self,
        session: TutorSession,
        student_message: str,
    ) -> dict[str, Any]:
        """LLM 判定深问结束时，由 TutorManager 调用。"""
        self._ensure_state(session)
        return await self._do_close_and_resume(
            session=session,
            closure_message="好的，这个深问先到这里。我们回到主线继续。",
            closed_reason="llm_close",
            understanding="unknown",
        )

    # ------------------------------------------------------------------
    # Record management
    # ------------------------------------------------------------------

    def _append_record(
        self,
        session: TutorSession,
        event: str,
        **kwargs: Any,
    ) -> None:
        self._ensure_state(session)
        record: dict[str, Any] = {
            "event": event,
            "timestamp": datetime.now().timestamp(),
        }
        record.update(kwargs)
        session.deep_dive_records.append(record)

    def _enqueue_deferred(
        self,
        session: TutorSession,
        topic: str,
        reason: str,
    ) -> None:
        self._ensure_state(session)
        task = {
            "task_type": "deferred_deep_dive",
            "topic": self._truncate_topic(topic),
            "reason": reason,
            "status": "pending",
            "created_at": datetime.now().timestamp(),
        }
        session.deferred_deep_dive_tasks.append(task)
        self._append_record(
            session,
            "defer",
            topic=task["topic"],
            reason=reason,
        )

    # ------------------------------------------------------------------
    # Response building
    # ------------------------------------------------------------------

    @staticmethod
    def _truncate_topic(message: str, limit: int = 180) -> str:
        text = (message or "").strip().replace("\n", " ")
        return text[:limit] if text else "（未命名深问）"

    def _build_response(
        self,
        session: TutorSession,
        question: str,
        round_no: int,
    ) -> str:
        checkpoint = session.get_current_checkpoint()
        cp_desc = checkpoint.description if checkpoint else "当前步骤"
        cp_question = (
            checkpoint.guiding_question
            if checkpoint
            else "你可以先说说你卡在哪个推导点"
        )
        methods = session.problem_context.get_methods_summary() or "当前方法"
        topic = self._truncate_topic(question, limit=120)

        intro = (
            f"这是一个很好的深度问题（第 {round_no}/{self._DEEP_DIVE_MAX_ROUNDS} 轮）。"
            "我们先短暂停主线，做一次小型深挖。"
        )
        intuitive = (
            f"把「{topic}」放回这道题，可以先从直觉理解："
            f"这一步（{cp_desc}）是在把问题变成一个更稳定、可验证的形式。"
        )
        rigorous = (
            f"严谨层面：我们依赖的是「{methods}」对应的可逆变换/等价关系。"
            f"只要关键前提成立，当前推导与目标结论保持等价，不会引入额外解或漏解。"
        )
        confirm = (
            f"确认一下：如果你要判断这一步是否成立，你会优先检查哪个前提？"
            f"（可结合引导问题：{cp_question}）"
        )
        return (
            f"{intro}\n\n"
            f"**直觉层**：{intuitive}\n\n"
            f"**严谨层**：{rigorous}\n\n"
            f"**确认问题**：{confirm}"
        )

    # ------------------------------------------------------------------
    # Close & resume
    # ------------------------------------------------------------------

    async def _do_close_and_resume(
        self,
        session: TutorSession,
        closure_message: str,
        closed_reason: str,
        understanding: str,
    ) -> dict[str, Any]:
        self._ensure_state(session)
        resume_checkpoint = _clamp_checkpoint_index(
            session,
            getattr(session, "deep_dive_return_checkpoint", session.current_checkpoint),
        )
        session.current_checkpoint = resume_checkpoint

        topic = getattr(session, "deep_dive_topic", "")
        rounds = int(getattr(session, "deep_dive_rounds", 0))
        self._append_record(
            session,
            "close",
            topic=topic,
            rounds=rounds,
            closed_reason=closed_reason,
            understanding=understanding,
            resume_checkpoint=resume_checkpoint,
        )

        session.deep_dive_active = False
        session.deep_dive_rounds = 0
        session.deep_dive_return_checkpoint = None
        session.deep_dive_topic = ""

        resumed = await self._ask_socratic(
            session,
            student_response=f"[结束深问，回到第{resume_checkpoint + 1}步主线]",
        )
        combined = closure_message + "\n\n---\n\n" + resumed.get("message", "")
        resumed["message"] = combined
        resumed["mode"] = "socratic"
        resumed["deep_dive_closed"] = True
        resumed["deep_dive_closed_reason"] = closed_reason

        if session.interaction_history:
            last = session.interaction_history[-1]
            metadata = last.get("metadata", {}) if isinstance(last, dict) else {}
            if (
                isinstance(last, dict)
                and last.get("role") == "tutor"
                and metadata.get("type") == "socratic"
            ):
                last["content"] = combined
                metadata["type"] = "deep_dive_resume"
                metadata["deep_dive_closed_reason"] = closed_reason
                metadata["deep_dive_understanding"] = understanding
                metadata["resume_checkpoint"] = resume_checkpoint
                last["metadata"] = metadata

        return resumed
