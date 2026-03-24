#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动化测试脚本 - 模拟学生与 debug_cli 交互，输出到 test_log.md
"""

import asyncio
import os
import sys
import time
from pathlib import Path

# Reuse debug_cli bootstrap
sys.argv = ["auto_test.py", "--live"]
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "tools"))

import debug_cli as cli

# ─── Log ─────────────────────────────────────────────────────────────────────

_log: list[str] = []


def L(text: str = ""):
    _log.append(text)
    print(text, flush=True)


def flush_log():
    path = _PROJECT_ROOT / "test_log.md"
    path.write_text("\n".join(_log), encoding="utf-8")
    print(f"\n>>> 日志已写入: {path}", flush=True)


# ─── Test ────────────────────────────────────────────────────────────────────

async def main():
    L("# DeepSenior Debug CLI — Live 端到端测试日志")
    L()
    L(f"- **日期**: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    L(f"- **模式**: Live LLM")
    L(f"- **API**: `{os.environ.get('OPENAI_BASE_URL', 'N/A')}`")
    L(f"- **模型**: `{os.environ.get('OPENAI_MODEL', 'N/A')}`")
    L()

    repl = cli.DebugREPL(live=True)
    mgr = repl.tutor_mgr
    rmgr = repl.review_mgr

    # ==================================================================
    # Phase 1: 创建 Tutor 会话
    # ==================================================================
    L("## Phase 1: 创建 Tutor 会话")
    L()

    session = mgr.create_session(cli.SAMPLE_PROBLEMS[0])
    repl.tutor_session = session
    repl.active_mode = "tutor"
    problem = cli.SAMPLE_PROBLEMS[0]

    L(f"- session_id: `{session.session_id[:12]}...`")
    L(f"- 题目: {problem.problem}")
    L(f"- mode: `{session.mode.value}`, status: `{session.status}`")
    L()

    # ==================================================================
    # Phase 2: 提交含计算错误的解答
    # ==================================================================
    L("## Phase 2: 提交解题过程（含计算错误）")
    L()

    work1 = (
        "令f(x)=0，即x²-4x+3=0。"
        "我用求根公式：x = (4 ± √(16-12)) / 2 = (4 ± √4) / 2 = (4 ± 2) / 2。"
        "所以 x = (4+2)/2 = 3 或 x = (4-2)/2 = 0。"
        "答：x=3 或 x=0。"
    )
    L(f"> **学生提交**: {work1}")
    L()

    t0 = time.time()
    r1 = await mgr.handle_submission(session.session_id, work1)
    dt = time.time() - t0

    L(f"**Tutor 回复** (耗时 {dt:.1f}s):")
    L()
    L(f"- mode: `{r1.get('mode')}`")
    L(f"- message:")
    L()
    for line in r1.get("message", "").split("\n"):
        L(f"  > {line}")
    L()
    L(f"- 会话状态: mode=`{session.mode.value}`, status=`{session.status}`")
    L()

    # ==================================================================
    # Phase 3: 如果回到 idle，用配方法重新提交（不完整）
    # ==================================================================
    if session.mode.value == "idle" and session.status == "active":
        L("## Phase 3: 重新提交（配方法，不完整 — 触发 Socratic 引导）")
        L()

        work2 = (
            "我换个方法，用配方法。"
            "x²-4x+3=0，把常数移过去：x²-4x = -3。"
            "接下来要配方，但我不确定怎么弄..."
        )
        L(f"> **学生提交**: {work2}")
        L()

        t0 = time.time()
        r2 = await mgr.handle_submission(session.session_id, work2)
        dt = time.time() - t0

        L(f"**Tutor 回复** (耗时 {dt:.1f}s):")
        L()
        L(f"- mode: `{r2.get('mode')}`")
        L(f"- message:")
        L()
        for line in r2.get("message", "").split("\n"):
            L(f"  > {line}")
        L()
        L(f"- 会话状态: mode=`{session.mode.value}`, status=`{session.status}`")
        if session.solution_plan:
            L(f"- checkpoints: {len(session.solution_plan.checkpoints)} 个")
            for cp in session.solution_plan.checkpoints:
                marker = "✓" if cp.passed else " "
                L(f"  - [{marker}] {cp.index+1}. {cp.description}")
        L()

    # ==================================================================
    # Phase 4: Socratic 对话
    # ==================================================================
    if session.mode.value == "socratic":
        L("## Phase 4: Socratic 对话（逐步引导）")
        L()

        responses = [
            "我想一下...要让x²-4x变成完全平方式，需要加上(-4/2)²=4，所以两边都加4",
            "加了4之后，左边是x²-4x+4=(x-2)²，右边是-3+4=1，所以(x-2)²=1",
            "两边开平方，得到x-2=±1，所以x=2+1=3或者x=2-1=1",
            "验证：f(1)=1-4+3=0 ✓，f(3)=9-12+3=0 ✓，答案是x=1和x=3",
        ]

        for i, msg in enumerate(responses, 1):
            L(f"### Round {i}")
            L()
            L(f"> **学生**: {msg}")
            L()

            t0 = time.time()
            r = await mgr.handle_student_message(session.session_id, msg)
            dt = time.time() - t0

            L(f"**Tutor 回复** (耗时 {dt:.1f}s):")
            L()
            L(f"- mode: `{r.get('mode')}`")
            for line in r.get("message", "").split("\n"):
                L(f"  > {line}")
            L()

            # Checkpoint progress
            cp = session.get_current_checkpoint()
            if session.solution_plan:
                total = len(session.solution_plan.checkpoints)
                passed = sum(1 for c in session.solution_plan.checkpoints if c.passed)
                L(f"- checkpoint 进度: {passed}/{total}")
            L(f"- mode=`{session.mode.value}`, status=`{session.status}`")
            L()

            if session.status == "solved":
                L("**会话已解决!**")
                L()
                break
            if session.mode.value not in ("socratic",):
                L(f"_模式变为 `{session.mode.value}`，Socratic 对话结束_")
                L()
                break

    # ==================================================================
    # Phase 5: 查看最终 Checkpoint 状态
    # ==================================================================
    if session.solution_plan:
        L("## Phase 5: Checkpoint 最终状态")
        L()
        L("| # | 描述 | 通过 | 提示级别 | 尝试次数 |")
        L("|---|------|------|----------|----------|")
        for cp in session.solution_plan.checkpoints:
            L(f"| {cp.index+1} | {cp.description[:30]} | {'✓' if cp.passed else '✗'} | {cp.hint_level} | {cp.attempts} |")
        L()

    # ==================================================================
    # Phase 6: 交互历史摘要
    # ==================================================================
    L("## Phase 6: 交互历史")
    L()
    for h in session.interaction_history:
        role = {"student": "学生", "tutor": "导师", "system": "系统"}.get(h.get("role", "?"), h.get("role", "?"))
        content = h.get("content", "")[:120].replace("\n", " ")
        meta = h.get("metadata", h.get("meta", {}))
        msg_type = meta.get("type", "")
        tag = f" `[{msg_type}]`" if msg_type else ""
        L(f"- **{role}**{tag}: {content}")
    L()

    # ==================================================================
    # Phase 7: Review 复盘
    # ==================================================================
    L("## Phase 7: Review 复盘会话")
    L()

    tutor_export = mgr.export_session(session.session_id)
    review_result = rmgr.create_session(problem, tutor_session_export=tutor_export)
    review_session = rmgr.get_session(review_result["session_id"])
    repl.review_session = review_session
    repl.active_mode = "review"

    L(f"- review session: `{review_session.session_id[:12]}...`")
    L(f"- status: `{review_session.status}`")
    if review_result.get("opener"):
        L(f"- 开场白: {review_result['opener'][:120]}")
    L()

    # Ask about methods
    L("### 7.1 询问其他解法")
    L()
    L("> **学生**: 这道题还有哪些其他解法？")
    L()

    t0 = time.time()
    r_review1 = await rmgr.chat(review_session.session_id, "这道题还有哪些其他解法？")
    dt = time.time() - t0

    L(f"**复盘助手回复** (耗时 {dt:.1f}s):")
    L()
    for line in r_review1.get("message", "").split("\n"):
        L(f"  > {line}")
    L()

    # Compare methods
    L("### 7.2 方法对比")
    L()
    L("> **学生**: 因式分解法和求根公式法，对于这道题哪个更好？")
    L()

    t0 = time.time()
    r_review2 = await rmgr.chat(
        review_session.session_id, "因式分解法和求根公式法，对于这道题哪个更好？"
    )
    dt = time.time() - t0

    L(f"**复盘助手回复** (耗时 {dt:.1f}s):")
    L()
    for line in r_review2.get("message", "").split("\n"):
        L(f"  > {line}")
    L()

    # Review state summary
    L("### 7.3 复盘会话状态")
    L()
    L(f"- discovered_methods: {len(review_session.discovered_methods)}")
    for m in review_session.discovered_methods:
        L(f"  - {m.name}: {m.summary}")
    L(f"- solved_demonstrations: {len(review_session.solved_demonstrations)}")
    L(f"- error_snapshots: {len(review_session.error_snapshots)}")
    L(f"- interaction_history: {len(review_session.interaction_history)} 条")
    L()

    # ==================================================================
    # Done
    # ==================================================================
    L("---")
    L(f"\n测试于 {time.strftime('%Y-%m-%d %H:%M:%S')} 完成。")

    flush_log()


if __name__ == "__main__":
    asyncio.run(main())
