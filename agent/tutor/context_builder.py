#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
TutorContextBuilder - 从 TutorSession 提取结构化上下文快照

输出稳定 key-value 格式供 LLM action classifier 的 {session_context} 变量使用。
字段名固定、顺序固定、缺失字段有明确默认值。

安全边界：不暴露 answer 字段。
"""

from .data_structures import TutorSession


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
            kv.append(("deep_dive", "active"))
            kv.append(("deep_dive.round", f"{rounds}/2"))
            if topic:
                kv.append(("deep_dive.topic", topic[:60]))
            if ret_cp is not None:
                kv.append(("deep_dive.return_checkpoint", str(ret_cp + 1)))
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
            kv.append(("last_error_desc", gr.error_description[:80]))

        # 替代解法
        if session.used_alternative_method:
            kv.append(("alternative_method", "used"))
            kv.append(("alternative_flagged", str(session.alternative_flagged).lower()))

        return "\n".join(f"{k}={v}" for k, v in kv)
