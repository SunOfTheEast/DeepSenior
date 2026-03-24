#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ReviewContextBuilder - 从 ReviewSession 提取结构化上下文快照

输出稳定 key-value 格式供 LLM action classifier 的 {session_context} 变量使用。
字段名固定、顺序固定、缺失字段有明确默认值。

安全边界：不暴露 answer 字段。
"""

from .data_structures import ReviewSession


class ReviewContextBuilder:

    @staticmethod
    def build(session: ReviewSession) -> str:
        kv: list[tuple[str, str]] = []

        # 验证状态
        if session.pending_verification:
            kv.append(("mode", "verification"))
            kv.append(("verification.stage", session.pending_verification_stage or "concept"))
            kv.append(("verification.method", session.pending_verification))
        else:
            kv.append(("mode", "free_review"))

        # 已探索方法（最多 4 个）
        if session.discovered_methods:
            method_entries = []
            for m in session.discovered_methods[:4]:
                solved = m.name in session.solved_demonstrations
                check = session.understanding_checks.get(m.name)
                parts: list[str] = []
                if solved:
                    parts.append("demo")
                if check:
                    parts.append(check.final_quality().value)
                status = "+".join(parts) if parts else "pending"
                method_entries.append(f"{m.name}({status})")
            kv.append(("known_methods", ",".join(method_entries)))
        else:
            kv.append(("known_methods", "none"))

        # 最近演示的方法
        if session.solved_demonstrations:
            last_demo = list(session.solved_demonstrations.keys())[-1]
            kv.append(("last_demo_method", last_demo))

        # 最近动作类型
        if session.interaction_history:
            last_meta = session.interaction_history[-1].get("meta", {})
            last_type = last_meta.get("type", "")
            if last_type:
                kv.append(("last_action_type", last_type))

        # 错误记录
        n_errors = len(session.error_snapshots)
        n_struggle = len(session.struggle_points)
        if n_errors or n_struggle:
            kv.append(("errors", str(n_errors)))
            kv.append(("struggle_points", str(n_struggle)))

        # 学生原方法
        kv.append(("student_method", session.student_method_used))

        return "\n".join(f"{k}={v}" for k, v in kv)
