#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动化测试脚本 - 模拟学生与 debug_cli 交互，输出到 test_log.md

用法：
  python auto_test.py                 # 默认运行椭圆点差法场景
  python auto_test.py ellipse         # 椭圆点差法
  python auto_test.py sequence        # 数列构造辅助数列法
"""

import asyncio
import os
import sys
import time
from pathlib import Path

# Save real argv before debug_cli bootstrap overwrites it
_real_argv = sys.argv[:]
sys.argv = ["auto_test.py", "--live"]
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "tools"))

import debug_cli as cli

# ─── Scenarios ────────────────────────────────────────────────────────────────

SCENARIOS = {
    "ellipse": {
        "problem_index": 3,  # sample_04_ellipse
        "label": "椭圆点差法",
        "submission": (
            "椭圆 x²/4+y²/3=1，a²=4, b²=3, c²=1, 右焦点 F(1,0)。\n"
            "我用点差法。设 A(x₁,y₁), B(x₂,y₂) 都在椭圆上，则：\n"
            "x₁²/4+y₁²/3=1 ... ①\n"
            "x₂²/4+y₂²/3=1 ... ②\n"
            "①-② 得 (x₁²-x₂²)/4+(y₁²-y₂²)/3=0\n"
            "即 (x₁+x₂)(x₁-x₂)/4+(y₁+y₂)(y₁-y₂)/3=0\n"
            "设中点 M(x₀,y₀)，则 x₁+x₂=2x₀, y₁+y₂=2y₀，\n"
            "弦斜率 k=(y₁-y₂)/(x₁-x₂)，代入得 2x₀/4+2y₀·k/3=0，\n"
            "所以 k=-3x₀/(4y₀)。\n"
            "又直线过焦点 F(1,0)，所以 k=(y₀-0)/(x₀-1)=y₀/(x₀-1)。\n"
            "两个 k 联立... 但后面我不太会化简了"
        ),
        "alt_method": "点差法",
        "responses": [
            "两个 k 相等，所以 -3x₀/(4y₀) = y₀/(x₀-1)，交叉相乘得 -3x₀(x₀-1) = 4y₀²",
            "展开得 -3x₀²+3x₀ = 4y₀²，整理为 3x₀²+4y₀²-3x₀ = 0",
            "配方：3(x₀²-x₀)+4y₀²=0 → 3(x₀-1/2)²-3/4+4y₀²=0 → 3(x₀-1/2)²+4y₀²=3/4，"
            "即 (x₀-1/2)²/(1/4) + y₀²/(3/16) = 1，这是一个以(1/2,0)为中心的椭圆",
            "所以中点 M 的轨迹方程为 (x-1/2)²/(1/4)+y²/(3/16)=1，"
            "轨迹是椭圆内部的一段弧（需满足直线与原椭圆有两个交点的约束）。",
        ],
    },
    "sequence": {
        "problem_index": 4,  # sample_05_sequence
        "label": "数列构造辅助数列法",
        "submission": (
            "a₁=1, a_{n+1}=a_n+2n+1。\n"
            "我注意到 2n+1 = (n+1)² - n²，\n"
            "所以我想构造辅助数列 b_n = a_n - n²。\n"
            "那么 b_{n+1} = a_{n+1} - (n+1)²，但是展开后我搞不清楚了...\n"
            "b_{n+1} 和 b_n 到底是什么关系？"
        ),
        "alt_method": "构造辅助数列法",
        "responses": [
            "b_{n+1} = a_{n+1}-(n+1)² = (a_n+2n+1)-(n²+2n+1) = a_n-n² = b_n，所以 b_{n+1}=b_n",
            "因此 {b_n} 是常数列，b_n=b_1=a_1-1=0",
            "代回去：a_n = b_n + n² = 0 + n² = n²，所以通项公式 aₙ = n²",
            "验证递推式：a_{n+1}=(n+1)²=n²+2n+1=a_n+2n+1 ✓，对任意 n≥1 恒成立",
            "再检查初始条件：a_1=1²=1 ✓，全部验证通过",
            "总结：通过构造辅助数列 b_n=a_n-n²，证明其为常数列，得到 aₙ=n²",
        ],
    },
}

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
    # 选择场景
    scenario_name = "ellipse"
    for arg in _real_argv[1:]:
        if arg in SCENARIOS:
            scenario_name = arg
    sc = SCENARIOS[scenario_name]

    L("# DeepSenior Debug CLI — Live 端到端测试日志")
    L()
    L(f"- **日期**: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    L(f"- **模式**: Live LLM")
    L(f"- **场景**: `{sc['label']}`")
    L(f"- **API**: `{os.environ.get('OPENAI_BASE_URL', 'N/A')}`")
    L(f"- **模型**: `{os.environ.get('OPENAI_MODEL', 'N/A')}`")
    L()

    repl = cli.DebugREPL(live=True)
    mgr = repl.tutor_mgr

    problem = cli.SAMPLE_PROBLEMS[sc["problem_index"]]

    # ==================================================================
    # Phase 1: 创建 Tutor 会话
    # ==================================================================
    L(f"## Phase 1: 创建 Tutor 会话（{sc['label']} — RAG 命中测试）")
    L()

    session = mgr.create_session(problem)
    repl.tutor_session = session
    repl.active_mode = "tutor"

    L(f"- session_id: `{session.session_id[:12]}...`")
    L(f"- 题目: {problem.problem}")
    L(f"- chapter: `{problem.chapter}`")
    L(f"- tags: `{problem.tags}`")
    L(f"- mode: `{session.mode.value}`, status: `{session.status}`")
    L()

    # ==================================================================
    # Phase 2: 提交解答（替代方法 → 触发 RAG）
    # ==================================================================
    L(f"## Phase 2: 提交解题过程（{sc['alt_method']} — 替代方法，触发 RAG 检索）")
    L()

    work1 = sc["submission"]
    L("> **学生提交**:")
    L()
    for line in work1.split("\n"):
        L(f">   {line}")
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
    # Phase 3: RAG 检索诊断
    # ==================================================================
    L("## Phase 3: RAG 知识卡检索诊断")
    L()

    bundle = getattr(session, "last_retrieval_bundle", None)
    if bundle is None:
        L("- **RAG 未触发**（可能 Grader 判定为标准方法，无需 RAG）")
    else:
        L(f"- **router_primary_slot**: `{bundle.router_primary_slot}`")
        L(f"- **selected_card_ids**: `{bundle.selected_card_ids}`")
        L(f"- **audit_entries**: {len(bundle.audit_entries)} 条")
        L()

        if bundle.result.supplementary_cards:
            L("### 命中的补充知识卡")
            L()
            L("| card_id | title | methods | hints |")
            L("|---------|-------|---------|-------|")
            for rc in bundle.result.supplementary_cards:
                card = rc.card
                methods_n = len(card.general_methods) if hasattr(card, 'general_methods') else 0
                hints_n = len(card.hints) if hasattr(card, 'hints') else 0
                L(f"| `{card.card_id}` | {card.title} | {methods_n} | {hints_n} |")
            L()
        else:
            L("- **补充知识卡**: 无（检索未命中或无匹配卡片）")
            L()

        if bundle.audit_entries:
            L("### RAG 审计条目")
            L()
            for ae in bundle.audit_entries:
                L(f"- task_type=`{ae.task_type}`, slot=`{ae.router_primary_slot}`, conf={ae.router_confidence}, notes: {ae.notes[:80] if ae.notes else 'N/A'}")
            L()

    # ==================================================================
    # Phase 4: Socratic 对话（如果进入了引导模式）
    # ==================================================================
    if session.mode.value == "socratic":
        L("## Phase 4: Socratic 对话（逐步引导）")
        L()

        for i, msg in enumerate(sc["responses"], 1):
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

            if session.solution_plan:
                total = len(session.solution_plan.checkpoints)
                passed = sum(1 for c in session.solution_plan.checkpoints if c.passed)
                L(f"- checkpoint 进度: {passed}/{total}")
            L(f"- mode=`{session.mode.value}`, status=`{session.status}`")
            L()

            if session.status == "solved" or session.mode.value != "socratic":
                break

    # ==================================================================
    # Phase 5: Checkpoint 最终状态
    # ==================================================================
    if session.solution_plan:
        L("## Phase 5: Checkpoint 最终状态")
        L()
        L("| # | 描述 | 通过 | 提示级别 | 尝试次数 |")
        L("|---|------|------|----------|----------|")
        for cp in session.solution_plan.checkpoints:
            L(f"| {cp.index+1} | {cp.description[:40]} | {'✓' if cp.passed else '✗'} | {cp.hint_level} | {cp.attempts} |")
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
    # Done
    # ==================================================================
    L("---")
    L(f"\n测试于 {time.strftime('%Y-%m-%d %H:%M:%S')} 完成。")

    flush_log()


if __name__ == "__main__":
    asyncio.run(main())
