#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
TutorContextBuilder - 从 TutorSession 提取结构化上下文快照

输出稳定 key-value 格式供 LLM action classifier 的 {session_context} 变量使用。
字段名固定、顺序固定、缺失字段有明确默认值。

安全边界：不暴露 answer 字段。
"""

from .data_structures import DEEP_DIVE_MAX_ROUNDS, TutorSession
from .pipeline.pending_slot import PendingSlot


class TutorContextBuilder:

    @staticmethod
    def build(session: TutorSession) -> str:
        kv: list[tuple[str, str]] = []

        # 当前模式
        kv.append(("mode", session.mode.value))

        # 深问状态
        if getattr(session, "deep_dive_active", False):
            rounds = getattr(session, "deep_dive_rounds", 0)
            topic = getattr(session, "deep_dive_topic", "")
            ret_cp = getattr(session, "deep_dive_return_checkpoint", None)
            win_id = getattr(session, "deep_dive_active_window_id", None)
            kv.append(("deep_dive", "active"))
            kv.append(("deep_dive.round", f"{rounds}/{DEEP_DIVE_MAX_ROUNDS}"))
            if topic:
                kv.append(("deep_dive.topic", topic[:60]))
            if ret_cp is not None:
                kv.append(("deep_dive.return_checkpoint", str(ret_cp + 1)))
            if win_id:
                kv.append(("deep_dive.window_id", str(win_id)))
        else:
            kv.append(("deep_dive", "inactive"))

        # Checkpoint 进度
        plan = session.solution_plan
        if plan and plan.checkpoints:
            total = len(plan.checkpoints)
            current = session.current_checkpoint
            cp = session.get_current_checkpoint()

            kv.append(("checkpoint.current", str(current + 1)))
            kv.append(("checkpoint.total", str(total)))

            if cp:
                kv.append(("checkpoint.attempts", str(cp.attempts)))
                kv.append(("checkpoint.hint_level", f"{cp.hint_level}/3"))
                if cp.description:
                    kv.append(("checkpoint.current_desc", cp.description[:90]))
                if cp.guiding_question:
                    kv.append(("checkpoint.current_question", cp.guiding_question[:90]))

            if current > 0 and current - 1 < total:
                prev_cp = plan.checkpoints[current - 1]
                if prev_cp.description:
                    kv.append(("checkpoint.prev_desc", prev_cp.description[:90]))
                if prev_cp.guiding_question:
                    kv.append(("checkpoint.prev_question", prev_cp.guiding_question[:90]))

            passed_indices = [
                str(i + 1) for i, c in enumerate(plan.checkpoints) if c.passed
            ]
            if passed_indices:
                kv.append(("checkpoint.passed", ",".join(passed_indices)))
        else:
            kv.append(("checkpoint", "no_plan"))

        # 累计统计
        kv.append(("total_hints", str(session.total_hints_given)))
        kv.append(("total_attempts", str(session.total_attempts)))

        # 上次批改
        gr = session.last_grader_result
        if gr:
            kv.append(("last_error_type", gr.error_type.value))
            # 这里保留的是"判题原文摘要"，主要给 action classifier 理解上下文。
            # 它可能带评审语体（如"学生……"），所以只截断做背景，不当作对学生直出话术。
            kv.append(("last_error_desc", gr.error_description[:80]))

        # 统一待澄清交互槽
        kv.extend(PendingSlot.build_context(session))

        # 替代解法
        if session.used_alternative_method:
            kv.append(("alternative_method", "used"))
            kv.append(("alternative_flagged", str(session.alternative_flagged).lower()))

        # L0 card menu (visible to all agents)
        card_menu = session.problem_context.get_l0_menu_for_llm()
        if card_menu:
            kv.append(("card_menu", card_menu))

        # 最近一条导师消息类型，帮助 LLM 判断"当前语境是深问/主线/回顾"等
        for item in reversed(session.interaction_history):
            if item.get("role") != "tutor":
                continue
            meta = item.get("metadata", {}) or {}
            msg_type = str(meta.get("type", "")).strip()
            if msg_type:
                kv.append(("dialog.last_tutor_type", msg_type))
            break

        return "\n".join(f"{k}={v}" for k, v in kv)
