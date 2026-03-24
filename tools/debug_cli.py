#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepSenior Debug CLI — 状态机与上下文调试工具

用法:
    python tools/debug_cli.py                # Mock 模式（默认）
    python tools/debug_cli.py --live         # 真实 LLM 模式
    python tools/debug_cli.py -p problem.json  # 加载外部题目

Mock 模式下所有 LLM 调用返回确定性结果，方便逐步调试状态机行为。

Live 模式环境变量:
    OPENAI_API_KEY      必填，API 密钥
    OPENAI_BASE_URL     可选，默认 https://api.openai.com/v1（兼容任何 OpenAI 格式 API）
    OPENAI_MODEL        可选，默认 gpt-4o
"""

import argparse
import asyncio
import json
import os
import sys
import textwrap
from dataclasses import asdict, fields
from pathlib import Path

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Section 1: Bootstrap
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
_REQUESTED_LIVE = "--live" in sys.argv[1:]
_BOOTSTRAP_MODE = "mock"
_BOOTSTRAP_ERROR: Exception | None = None


def _bootstrap():
    """Bootstrap runtime: configure LLM backend and patch abstract methods."""
    global _BOOTSTRAP_MODE, _BOOTSTRAP_ERROR

    if _REQUESTED_LIVE:
        if not os.environ.get("OPENAI_API_KEY"):
            print(
                "ERROR: --live 需要设置环境变量:\n"
                "  export OPENAI_API_KEY=sk-xxx\n"
                "  export OPENAI_BASE_URL=https://api.openai.com/v1  # 可选\n"
                "  export OPENAI_MODEL=gpt-4o                        # 可选\n"
            )
            sys.exit(1)
        try:
            import openai  # noqa: F401
        except ImportError:
            print("ERROR: --live 需要安装 openai：pip install openai")
            sys.exit(1)
        # Configure agent.infra.llm with env vars (already the default behavior)
        from agent.infra import llm as infra_llm
        infra_llm.configure()
    else:
        # Mock mode: replace LLM calls with no-ops
        from agent.infra import llm as infra_llm

        async def _noop_complete(**kw):
            return "{}"

        async def _noop_stream(**kw):
            yield ""

        infra_llm.complete = _noop_complete  # type: ignore[assignment]
        infra_llm.stream = _noop_stream  # type: ignore[assignment]

    # Patch out @abstractmethod on BaseAgent.process so agents without
    # a process() override (e.g. RouterAgent) can be instantiated.
    from agent.base_agent import BaseAgent
    if getattr(getattr(BaseAgent, "process", None), "__isabstractmethod__", False):
        async def _noop_process(self, *args, **kwargs):
            raise NotImplementedError(f"{type(self).__name__}.process() not implemented")
        BaseAgent.process = _noop_process

    _BOOTSTRAP_MODE = "live" if _REQUESTED_LIVE else "mock"


_bootstrap()

# Section 2: Imports (safe after bootstrap)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from agent.tutor.data_structures import (  # noqa: E402
    AlternativeRecommendation,
    Checkpoint,
    CheckpointEvaluation,
    ErrorType,
    GraderResult,
    GranularityLevel,
    PedagogicalAlignment,
    PathEvaluationResult,
    ProblemContext,
    KnowledgeCard,
    RouterDecision,
    SolutionPlan,
    TutorAction,
    TutorMode,
    TutorSession,
)
from agent.tutor.tutor_manager import TutorManager  # noqa: E402
from agent.tutor.context_builder import TutorContextBuilder  # noqa: E402
from agent.tutor.skills import SkillRegistry  # noqa: E402
from agent.skills_common import SkillMeta, wrap_sync_as_async  # noqa: E402

from agent.review.data_structures import (  # noqa: E402
    MethodInfo,
    ReviewAction,
    ReviewSession,
    SolvedMethod,
    UnderstandingQuality,
)
from agent.review.review_chat_manager import ReviewChatManager  # noqa: E402
from agent.review.context_builder import ReviewContextBuilder  # noqa: E402
from agent.review.skills import ReviewSkillRegistry  # noqa: E402

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Section 3: Sample Problems
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SAMPLE_PROBLEMS = [
    ProblemContext(
        problem_id="sample_01",
        problem="已知二次函数 f(x) = x² - 4x + 3，求 f(x) = 0 的解。",
        answer="x = 1 或 x = 3",
        knowledge_cards=[
            KnowledgeCard(
                card_id="quadratic_factoring",
                title="二次方程因式分解",
                general_methods=["因式分解法", "求根公式"],
                hints=["观察常数项 3 的因子", "尝试将 x² - 4x + 3 分解为两个一次因式的乘积"],
                common_mistakes=["忘记验根", "因式分解符号错误"],
            ),
        ],
        difficulty=2,
        chapter="二次函数与方程",
        tags=["quadratic_factoring"],
    ),
    ProblemContext(
        problem_id="sample_02",
        problem="求不等式 2x + 3 > 7 的解集。",
        answer="x > 2",
        knowledge_cards=[
            KnowledgeCard(
                card_id="linear_inequality",
                title="一次不等式",
                general_methods=["移项", "系数化简"],
                hints=["先移项把常数移到右边", "再除以 x 的系数"],
                common_mistakes=["除以负数时忘记变号"],
            ),
        ],
        difficulty=1,
        chapter="不等式",
        tags=["linear_inequality"],
    ),
    ProblemContext(
        problem_id="sample_03",
        problem=(
            "已知等差数列 {aₙ} 中，a₁ = 2，公差 d = 3。\n"
            "(1) 求通项公式 aₙ；\n"
            "(2) 求前 10 项和 S₁₀。"
        ),
        answer="(1) aₙ = 3n - 1  (2) S₁₀ = 155",
        knowledge_cards=[
            KnowledgeCard(
                card_id="arithmetic_sequence",
                title="等差数列",
                general_methods=["通项公式 aₙ = a₁ + (n-1)d", "求和公式 Sₙ = n(a₁+aₙ)/2"],
                hints=["代入 a₁ 和 d 写出通项", "先求 a₁₀ 再用求和公式"],
                common_mistakes=["n-1 写成 n", "求和公式分子漏除 2"],
            ),
        ],
        difficulty=2,
        chapter="数列",
        tags=["arithmetic_sequence"],
    ),
]


def load_problem_from_json(path: str) -> ProblemContext:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return ProblemContext.from_dict(data)


def _get_live_llm_kwargs() -> dict:
    """Read live LLM config from environment variables."""
    from agent.infra.llm import get_llm_config
    cfg = get_llm_config()
    return {
        "api_key": cfg.api_key,
        "base_url": cfg.base_url,
        "api_version": cfg.api_version,
        "binding": cfg.binding,
        "language": "zh",
    }


def _extract_method_target(student_message: str, known_methods) -> str | None:
    msg = student_message.strip().lower()
    for method in known_methods or []:
        name = getattr(method, "name", "")
        if name and name.lower() in msg:
            return name
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Section 4: Mock Skills
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_mock_overrides: dict[str, dict] = {}


async def mock_grade_work(problem_context, student_work):
    ovr = _mock_overrides.get("grade_work", {})
    et_str = ovr.get("error_type", "on_track_stuck")
    try:
        et = ErrorType(et_str)
    except ValueError:
        et = ErrorType.ON_TRACK_STUCK
    is_correct = et == ErrorType.CORRECT
    return GraderResult(
        error_type=et,
        is_correct=is_correct,
        error_description=ovr.get("desc", "思路方向基本正确但在关键步骤卡住"),
        student_approach="直接求解",
        suggested_granularity=GranularityLevel.MEDIUM,
    )


async def mock_plan_guidance(problem_context, error_description, start_from,
                              granularity, alternative_method=None):
    return SolutionPlan(
        approach_summary="分析 → 建模 → 求解 → 验证",
        reset_reason=error_description,
        checkpoints=[
            Checkpoint(0, "理解题意与条件提取", "题目给了哪些已知条件？哪些是未知量？"),
            Checkpoint(1, "建立数学模型", "如何用数学表达式描述这些条件之间的关系？"),
            Checkpoint(2, "执行关键推导", "从模型出发能推导出什么中间结论？"),
            Checkpoint(3, "求解并验证", "最终结果是什么？代回原条件是否成立？"),
        ],
        granularity=granularity,
        alternative_method=alternative_method,
    )


async def mock_generate_hint(problem, checkpoint, interaction_history, student_response):
    return (
        f"让我们聚焦当前这一步。\n\n"
        f"**目标**：{checkpoint.description}\n\n"
        f"**引导**：{checkpoint.guiding_question}\n\n"
        f"你觉得应该从哪里入手？"
    )


async def mock_stream_hint(problem, checkpoint, interaction_history, student_response):
    text = await mock_generate_hint(problem, checkpoint, interaction_history, student_response)
    for char in text:
        yield char


async def mock_evaluate_checkpoint(checkpoint, passed_checkpoints_history,
                                    student_response, total_checkpoints,
                                    interaction_context=""):
    ovr = _mock_overrides.get("eval", {})
    if "pass" in ovr:
        passed = ovr["pass"]
    else:
        text = student_response.strip()
        passed = len(text) > 30 and not text.startswith("[")
    return CheckpointEvaluation(
        checkpoint_passed=passed,
        student_understanding=student_response[:80] if passed else "回答缺少实质内容",
        next_hint_level=min(checkpoint.hint_level + (0 if passed else 1), 3),
        reason="mock: 基于回答长度判断" if "pass" not in ovr else f"mock: override={passed}",
    )


async def mock_route_decision(grader_result):
    RULES = {
        ErrorType.CORRECT:          (TutorMode.GRADING,  False, False),
        ErrorType.COMPUTATIONAL:    (TutorMode.GRADING,  False, True),
        ErrorType.MISCONCEPTION:    (TutorMode.GRADING,  False, True),
        ErrorType.INCOMPLETE:       (TutorMode.GRADING,  False, True),
        ErrorType.NO_ATTEMPT:       (TutorMode.SOCRATIC, True,  False),
        ErrorType.ON_TRACK_STUCK:   (TutorMode.SOCRATIC, True,  False),
        ErrorType.WRONG_PATH_MINOR: (TutorMode.SOCRATIC, True,  False),
        ErrorType.WRONG_PATH_MAJOR: (TutorMode.SOCRATIC, True,  False),
    }
    GRAN = {
        ErrorType.NO_ATTEMPT:       GranularityLevel.COARSE,
        ErrorType.ON_TRACK_STUCK:   GranularityLevel.FINE,
        ErrorType.WRONG_PATH_MINOR: GranularityLevel.MEDIUM,
        ErrorType.WRONG_PATH_MAJOR: GranularityLevel.COARSE,
    }
    et = grader_result.error_type
    mode, needs_new_plan, deliver_fb = RULES[et]
    return RouterDecision(
        mode=mode,
        reason=f"错误类型：{et.value}",
        needs_new_plan=needs_new_plan,
        suggested_granularity=GRAN.get(et, GranularityLevel.MEDIUM),
        deliver_grader_feedback=deliver_fb,
        reset_from_error=grader_result.error_description if needs_new_plan else None,
    )


async def mock_classify_action(problem_context, student_message,
                                 interaction_history, session_context):
    ovr = _mock_overrides.get("classify_action", {})
    if "action" in ovr:
        return {
            "primary_action": ovr["action"],
            "target_step": ovr.get("target_step"),
            "confidence": 0.9,
            "reason": f"mock: override={ovr['action']}",
        }

    msg = student_message.strip().lower()
    if any(w in msg for w in ["不会", "太难", "放弃", "不想做", "烦"]):
        action, reason = TutorAction.HANDLE_FRUSTRATION.value, "检测到挫败信号"
    elif any(w in msg for w in ["答案", "直接告诉", "结果是"]):
        action, reason = TutorAction.HANDLE_ANSWER_REQ.value, "检测到要答案"
    elif any(w in msg for w in ["题错了", "题目有问题", "无解"]):
        action, reason = TutorAction.HANDLE_CHALLENGE.value, "质疑题目"
    elif any(w in msg for w in ["为什么", "本质", "证明", "推广", "严格"]):
        deep_dive_active = "【深问状态】活跃" in session_context
        if deep_dive_active:
            action, reason = TutorAction.CONTINUE_DEEP_DIVE.value, "深问中继续"
        else:
            action, reason = TutorAction.START_DEEP_DIVE.value, "深度问题"
    elif "回到主线" in msg or "不深究" in msg:
        deep_dive_active = "【深问状态】活跃" in session_context
        if deep_dive_active:
            action, reason = TutorAction.CLOSE_DEEP_DIVE.value, "收束深问"
        else:
            action, reason = TutorAction.CONTINUE_SOCRATIC.value, "默认"
    elif any(w in msg for w in ["上一步", "前面那步", "回到"]):
        action, reason = TutorAction.FOLLOWUP_QUESTION.value, "追问前置步骤"
    else:
        action, reason = TutorAction.CONTINUE_SOCRATIC.value, "默认继续引导"

    return {
        "primary_action": action,
        "target_step": None,
        "confidence": 0.8,
        "reason": f"mock: {reason}",
    }


async def mock_evaluate_approach(problem_context, student_approach, student_work_excerpt):
    return PathEvaluationResult(
        is_mathematically_valid=True,
        pedagogical_alignment=PedagogicalAlignment.ALIGNED,
        recommendation=AlternativeRecommendation.ACCEPT,
        student_approach_summary=student_approach,
        student_method_name=student_approach,
    )


# Review mocks

async def mock_review_classify_intent(problem_context, student_message,
                                       known_methods, interaction_history,
                                       session_context=""):
    msg = student_message.strip().lower()
    target = _extract_method_target(student_message, known_methods)
    if any(w in msg for w in ["有哪些", "所有方法", "解法"]):
        return ReviewAction.ENUMERATE_METHODS, target
    if any(w in msg for w in ["怎么做", "演示", "用"]):
        return ReviewAction.SHOW_SOLUTION, target
    if any(w in msg for w in ["对比", "哪个好", "区别"]):
        return ReviewAction.COMPARE_METHODS, target
    if any(w in msg for w in ["重新做", "再试", "挑战"]):
        return ReviewAction.RETRY_WITH_METHOD, target
    if any(w in msg for w in ["哪里错", "错误", "回放"]):
        return ReviewAction.REPLAY_ERRORS, target
    return ReviewAction.GENERAL, target


async def mock_review_respond(problem_context, student_message, context_str,
                               interaction_history):
    return f"[Mock 回复] 收到你的问题：「{student_message[:60]}」。这是一个很好的思考方向。"


async def mock_enumerate_methods(problem_context):
    return [
        MethodInfo("因式分解法", "将多项式分解为因式之积", "基础", [], True),
        MethodInfo("求根公式", "直接代入公式计算", "基础", ["判别式"], True),
        MethodInfo("配方法", "通过配方化为完全平方", "中等", [], False),
    ]


async def mock_solve_method(problem_context, method_name):
    return SolvedMethod(
        method_name=method_name,
        steps=[f"步骤 1：分析题意", f"步骤 2：应用{method_name}", f"步骤 3：求解并验证"],
        key_insight=f"使用{method_name}的关键在于正确识别问题结构",
        comparison_note=f"{method_name}适用于本题的条件",
    )


async def mock_replay_errors(problem_context, error_snapshots, struggle_points,
                               student_method_used, target_method, solved_demo,
                               interaction_history):
    return "[Mock] 错误回放：你之前在关键步骤的推导中出现了方向偏差。"


async def mock_ask_understanding(problem_context, solved_method, student_method_used):
    return f"你觉得{solved_method.method_name}的核心思路是什么？", "核心关键点"


async def mock_ask_transfer(problem_context, solved_method, student_method_used):
    return "如果题目条件变化，这个方法的哪一步需要调整？", "变式适应能力"


async def mock_evaluate_understanding(problem_context, method_name, question,
                                        key_points, student_response, solved_demo):
    if len(student_response.strip()) > 20:
        return UnderstandingQuality.UNDERSTOOD, "很好，你抓住了核心。"
    return UnderstandingQuality.PARTIAL, "思路有了，但关键细节还可以更清晰。"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Section 5: Mock Registry
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MockSkillRegistry:
    """Duck-types as SkillRegistry for TutorManager."""

    _SKILLS = {
        "grade_work":          mock_grade_work,
        "plan_guidance":       mock_plan_guidance,
        "generate_hint":       mock_generate_hint,
        "stream_hint":         mock_stream_hint,
        "evaluate_approach":   mock_evaluate_approach,
        "evaluate_checkpoint": mock_evaluate_checkpoint,
        "route_decision":      mock_route_decision,
        "classify_action":     mock_classify_action,
    }

    _META = {
        name: SkillMeta(name=name, description=f"mock:{name}", tags=["mock"])
        for name in _SKILLS
    }

    def get(self, name: str):
        if name not in self._SKILLS:
            raise KeyError(f"Skill '{name}' not found. Available: {list(self._SKILLS)}")
        return self._SKILLS[name]

    async def call(self, name: str, *args, **kwargs):
        return await self.get(name)(*args, **kwargs)

    def describe(self, name: str) -> SkillMeta:
        return self._META[name]

    def list_skills(self) -> list[SkillMeta]:
        return list(self._META.values())

    def has(self, name: str) -> bool:
        return name in self._SKILLS


class MockReviewSkillRegistry:
    """Duck-types as ReviewSkillRegistry for ReviewChatManager."""

    _SKILLS = {
        "evaluate_approach":      mock_evaluate_approach,
        "enumerate_methods":      mock_enumerate_methods,
        "solve_method":           mock_solve_method,
        "classify_intent":        mock_review_classify_intent,
        "respond_review":         mock_review_respond,
        "replay_errors":          mock_replay_errors,
        "ask_understanding":      mock_ask_understanding,
        "ask_transfer":           mock_ask_transfer,
        "evaluate_understanding": mock_evaluate_understanding,
    }
    _META = {
        name: SkillMeta(name=name, description=f"mock:{name}", tags=["mock", "review"])
        for name in _SKILLS
    }

    def get(self, name: str):
        if name not in self._SKILLS:
            raise KeyError(f"Skill '{name}' not found. Available: {list(self._SKILLS)}")
        return self._SKILLS[name]

    async def call(self, name: str, *args, **kwargs):
        return await self.get(name)(*args, **kwargs)

    def describe(self, name: str) -> SkillMeta:
        return self._META[name]

    def list_skills(self) -> list[SkillMeta]:
        return list(self._META.values())

    def has(self, name: str) -> bool:
        return name in self._SKILLS


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Section 6: Display Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _box(title: str, lines: list[str], width: int = 60) -> str:
    border = "─" * (width - 2)
    parts = [f"┌{border}┐", f"│ {title:<{width-4}} │", f"├{border}┤"]
    for line in lines:
        # Truncate long lines
        display = line[:width - 4]
        parts.append(f"│ {display:<{width-4}} │")
    parts.append(f"└{border}┘")
    return "\n".join(parts)


def _checkpoint_progress(session: TutorSession) -> tuple[int, int, str, bool] | None:
    if not session.solution_plan:
        return None
    checkpoints = session.solution_plan.checkpoints
    total = len(checkpoints)
    if total == 0:
        return None
    cp = session.get_current_checkpoint()
    done = cp is None and session.current_checkpoint >= total
    display_index = min(session.current_checkpoint + 1, total)
    desc = cp.description[:30] if cp else "已完成"
    return display_index, total, desc, done


def show_session_state(session: TutorSession):
    lines = [
        f"session_id: {session.session_id[:12]}...",
        f"mode: {session.mode.value}",
        f"status: {session.status}",
    ]
    cp = session.get_current_checkpoint()
    progress = _checkpoint_progress(session)
    if progress:
        display_index, total, desc, _done = progress
        lines.append(f"checkpoint: {display_index}/{total} ({desc})")
        if cp:
            lines.append(f"  hint_level: {cp.hint_level}/3")
            lines.append(f"  attempts: {cp.attempts}")
    else:
        lines.append("checkpoint: 无引导计划")
    lines.append(f"total_hints: {session.total_hints_given}")
    lines.append(f"total_attempts: {session.total_attempts}")
    dd = "活跃" if session.deep_dive_active else "未激活"
    if session.deep_dive_active:
        dd += f" (轮次 {session.deep_dive_rounds}/2)"
    lines.append(f"deep_dive: {dd}")
    lines.append(f"alternative: {'是' if session.used_alternative_method else '否'}")
    print(_box("Session State", lines))


def show_review_state(session: ReviewSession):
    lines = [
        f"session_id: {session.session_id[:12]}...",
        f"status: {session.status}",
        f"student_method: {session.student_method_used}",
        f"methods_found: {len(session.discovered_methods)}",
        f"demos_solved: {len(session.solved_demonstrations)}",
        f"errors: {len(session.error_snapshots)}",
        f"struggles: {len(session.struggle_points)}",
    ]
    if session.pending_verification:
        lines.append(f"pending_verify: {session.pending_verification} ({session.pending_verification_stage})")
    checks = session.get_understanding_summary()
    if checks:
        lines.append(f"understanding: {checks}")
    print(_box("Review Session", lines))


def show_checkpoint_detail(session: TutorSession):
    if not session.solution_plan:
        print("  (无引导计划)")
        return
    lines = []
    for cp in session.solution_plan.checkpoints:
        marker = "✓" if cp.passed else ("→" if cp.index == session.current_checkpoint else " ")
        lines.append(f" {marker} [{cp.index+1}] {cp.description[:40]}")
        lines.append(f"     问题: {cp.guiding_question[:40]}")
        lines.append(f"     hint={cp.hint_level} attempts={cp.attempts}")
    print(_box("Checkpoints", lines, width=70))


def show_history(session, n: int = 6):
    history = (
        session.interaction_history[-n:]
        if hasattr(session, "interaction_history")
        else []
    )
    if not history:
        print("  (无历史)")
        return
    lines = []
    for h in history:
        role = h.get("role", "?")
        content = h.get("content", "")[:80].replace("\n", " ")
        meta = h.get("metadata", h.get("meta", {}))
        msg_type = meta.get("type", "")
        tag = f" [{msg_type}]" if msg_type else ""
        prefix = {"student": "学生", "tutor": "导师", "system": "系统"}.get(role, role)
        lines.append(f"{prefix}{tag}: {content}")
    print(_box(f"History (last {n})", lines, width=90))


def show_context(session: TutorSession):
    text = TutorContextBuilder.build(session)
    print(_box("Session Context (ActionClassifier input)", text.split("\n"), width=70))


def show_response(result: dict):
    mode = result.get("mode", "?")
    msg = result.get("message", "(no message)")
    lines = [f"mode: {mode}"]
    for k, v in result.items():
        if k not in ("mode", "message"):
            lines.append(f"{k}: {v}")
    lines.append("─" * 40)
    for line in msg.split("\n"):
        lines.append(line[:80])
    print(_box("Response", lines, width=82))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Section 7: REPL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HELP_TEXT = """\
Commands:
  tutor [n]              创建 Tutor 会话 (n=样例编号, 默认1)
  review [n]             创建 Review 会话
  submit <text>          提交解题过程 (Tutor)
  say <text>             发送消息
  state                  查看 Session 状态
  context                查看 ActionClassifier 的 context 输入
  history [n]            查看交互历史 (最近 n 条)
  checkpoint             查看 Checkpoint 详情
  skills                 列出所有 Skills
  call <skill>           交互式调用单个 Skill（支持 tutor.xxx / review.xxx）
  problems               列出样例题目
  load <path>            加载外部题目 JSON
  mock <skill> <k=v>     配置 Mock 行为
  mock reset             重置所有 Mock 覆写
  quit                   退出
"""


class DebugREPL:

    def __init__(self, problem: ProblemContext | None = None, live: bool = False):
        self.live = live
        self.problem = problem or SAMPLE_PROBLEMS[0]
        self.tutor_registry = None
        self.review_skill_registry = None
        self.tutor_mgr: TutorManager | None = None
        self.review_mgr: ReviewChatManager | None = None
        self.tutor_session: TutorSession | None = None
        self.review_session: ReviewSession | None = None
        self.active_mode: str | None = None  # "tutor" or "review"

        if live:
            live_kw = _get_live_llm_kwargs()
            self.tutor_registry = SkillRegistry(**live_kw)
            self.review_skill_registry = ReviewSkillRegistry(
                evaluate_approach_skill=self.tutor_registry.get("evaluate_approach"),
                **live_kw,
            )
            self.tutor_mgr = TutorManager(
                registry=self.tutor_registry,
                session_store_dir=_PROJECT_ROOT / "data" / "sessions" / "_debug_live",
            )
            self.review_mgr = ReviewChatManager(
                registry=self.tutor_registry,
                api_key=live_kw["api_key"],
                base_url=live_kw["base_url"],
                language=live_kw["language"],
                api_version=live_kw["api_version"],
                binding=live_kw["binding"],
                session_store_dir=_PROJECT_ROOT / "data" / "sessions" / "_debug_review_live",
                skill_registry=self.review_skill_registry,
            )
        else:
            self.tutor_registry = MockSkillRegistry()
            self.review_skill_registry = MockReviewSkillRegistry()
            self.tutor_mgr = TutorManager(
                registry=self.tutor_registry,
                session_store_dir=_PROJECT_ROOT / "data" / "sessions" / "_debug",
            )
            self.review_mgr = ReviewChatManager(
                registry=None,
                api_key="mock", base_url="mock",
                session_store_dir=_PROJECT_ROOT / "data" / "sessions" / "_debug_review",
                skill_registry=self.review_skill_registry,
            )

    def _prompt(self) -> str:
        if self.active_mode == "tutor" and self.tutor_session:
            sid = self.tutor_session.session_id[:8]
            mode = self.tutor_session.mode.value
            cp = ""
            progress = _checkpoint_progress(self.tutor_session)
            if progress:
                display_index, total, _desc, done = progress
                cp = f" cp={display_index}/{total}"
                if done:
                    cp += " done"
            return f"tutor:{sid} [{mode}{cp}]> "
        if self.active_mode == "review" and self.review_session:
            sid = self.review_session.session_id[:8]
            return f"review:{sid}> "
        return "debug> "

    def _current_tutor_export(self) -> dict | None:
        if not self.tutor_mgr or not self.tutor_session:
            return None
        if self.tutor_session.problem_context.problem_id != self.problem.problem_id:
            return None
        return self.tutor_mgr.export_session(self.tutor_session.session_id)

    async def run(self):
        print()
        print("╔══════════════════════════════════════════════╗")
        print(f"║  DeepSenior Debug CLI v0.1                   ║")
        print(f"║  Mode: {'Live LLM' if self.live else 'Mock'}    "
              f"Problem: {self.problem.problem_id:<11} ║")
        print("╚══════════════════════════════════════════════╝")
        print()
        print("输入 help 查看命令列表")
        print()

        while True:
            try:
                raw = input(self._prompt()).strip()
            except (EOFError, KeyboardInterrupt):
                print("\nBye!")
                break

            if not raw:
                continue

            parts = raw.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""

            try:
                if cmd in ("quit", "exit", "q"):
                    print("Bye!")
                    break
                elif cmd == "help":
                    print(HELP_TEXT)
                elif cmd == "tutor":
                    await self._cmd_tutor(arg)
                elif cmd == "review":
                    await self._cmd_review(arg)
                elif cmd == "submit":
                    await self._cmd_submit(arg)
                elif cmd == "say":
                    await self._cmd_say(arg)
                elif cmd == "state":
                    self._cmd_state()
                elif cmd == "context":
                    self._cmd_context()
                elif cmd == "history":
                    self._cmd_history(arg)
                elif cmd == "checkpoint":
                    self._cmd_checkpoint()
                elif cmd == "skills":
                    self._cmd_skills()
                elif cmd == "call":
                    await self._cmd_call(arg)
                elif cmd == "problems":
                    self._cmd_problems()
                elif cmd == "load":
                    self._cmd_load(arg)
                elif cmd == "mock":
                    self._cmd_mock(arg)
                else:
                    # Shorthand: if in a session, treat unrecognized input as "say"
                    if self.active_mode:
                        await self._cmd_say(raw)
                    else:
                        print(f"  Unknown command: {cmd}. Type 'help' for usage.")
            except Exception as e:
                print(f"  [ERROR] {type(e).__name__}: {e}")

    # ── Command handlers ────────────────────────────────────────

    async def _cmd_tutor(self, arg: str):
        idx = int(arg) - 1 if arg.strip().isdigit() else 0
        if 0 <= idx < len(SAMPLE_PROBLEMS):
            self.problem = SAMPLE_PROBLEMS[idx]
        self.tutor_session = self.tutor_mgr.create_session(self.problem)
        self.active_mode = "tutor"
        print(f"\n  [Tutor] 会话已创建: {self.tutor_session.session_id[:12]}...")
        print(f"  [题目] {self.problem.problem[:60]}")
        print(f"  [模式] {self.tutor_session.mode.value} — 等待 submit 提交解题过程\n")

    async def _cmd_review(self, arg: str):
        idx = int(arg) - 1 if arg.strip().isdigit() else 0
        if 0 <= idx < len(SAMPLE_PROBLEMS):
            self.problem = SAMPLE_PROBLEMS[idx]
        tutor_export = self._current_tutor_export()
        result = self.review_mgr.create_session(
            self.problem,
            tutor_session_export=tutor_export,
        )
        self.review_session = self.review_mgr.get_session(result["session_id"])
        self.active_mode = "review"
        print(f"\n  [Review] 会话已创建: {self.review_session.session_id[:12]}...")
        print(f"  [题目] {self.problem.problem[:60]}")
        if tutor_export:
            print(f"  [来源] 已承接 Tutor 会话: {tutor_export['session_id'][:12]}...")
        if result.get("opener"):
            print(f"  [开场白] {result['opener'][:80]}")
        print()

    async def _cmd_submit(self, arg: str):
        if not arg:
            print("  Usage: submit <your work>")
            return
        if self.active_mode != "tutor" or not self.tutor_session:
            print("  请先创建 Tutor 会话: tutor")
            return
        print()
        result = await self.tutor_mgr.handle_submission(
            self.tutor_session.session_id, arg
        )
        show_response(result)
        print()

    async def _cmd_say(self, arg: str):
        if not arg:
            print("  Usage: say <message>")
            return
        print()
        if self.active_mode == "tutor" and self.tutor_session:
            result = await self.tutor_mgr.handle_student_message(
                self.tutor_session.session_id, arg
            )
            show_response(result)
        elif self.active_mode == "review" and self.review_session:
            result = await self.review_mgr.chat(
                self.review_session.session_id, arg
            )
            show_response(result)
        else:
            print("  请先创建会话: tutor 或 review")
        print()

    def _cmd_state(self):
        print()
        if self.active_mode == "tutor" and self.tutor_session:
            show_session_state(self.tutor_session)
        elif self.active_mode == "review" and self.review_session:
            show_review_state(self.review_session)
        else:
            print("  无活跃会话")
        print()

    def _cmd_context(self):
        print()
        if self.active_mode == "tutor" and self.tutor_session:
            show_context(self.tutor_session)
        else:
            print("  仅 Tutor 会话支持 context 查看")
        print()

    def _cmd_history(self, arg: str):
        n = int(arg) if arg.strip().isdigit() else 6
        session = self.tutor_session if self.active_mode == "tutor" else self.review_session
        if session:
            print()
            show_history(session, n)
            print()
        else:
            print("  无活跃会话")

    def _cmd_checkpoint(self):
        if self.active_mode == "tutor" and self.tutor_session:
            print()
            show_checkpoint_detail(self.tutor_session)
            print()
        else:
            print("  仅 Tutor 会话支持 checkpoint 查看")

    def _resolve_skill(self, raw_name: str):
        name = raw_name.strip()
        namespace = None
        for sep in (".", ":"):
            if sep in name:
                namespace, name = name.split(sep, 1)
                namespace = namespace.strip().lower()
                name = name.strip()
                break

        if namespace == "tutor" and self.tutor_registry and self.tutor_registry.has(name):
            return "tutor", self.tutor_registry, name
        if namespace == "review" and self.review_skill_registry and self.review_skill_registry.has(name):
            return "review", self.review_skill_registry, name
        if namespace:
            return None

        if self.tutor_registry and self.tutor_registry.has(name):
            return "tutor", self.tutor_registry, name
        if self.review_skill_registry and self.review_skill_registry.has(name):
            return "review", self.review_skill_registry, name
        return None

    def _cmd_skills(self):
        for namespace, registry in (
            ("Tutor", self.tutor_registry),
            ("Review", self.review_skill_registry),
        ):
            if not registry:
                continue
            print(f"\n  {namespace} Skills:")
            for meta in registry.list_skills():
                tag_str = ", ".join(meta.tags)
                print(f"    [{namespace.lower()}.{meta.name}] ({tag_str}) — {meta.description}")
        print()

    async def _cmd_call(self, arg: str):
        raw_name = arg.strip()
        if not raw_name:
            print("  Usage: call <skill_name>")
            return
        resolved = self._resolve_skill(raw_name)
        if not resolved:
            print(f"  Skill '{raw_name}' not found. Use 'skills' to list.")
            return
        namespace, registry, skill_name = resolved

        print(f"\n  Calling {namespace}.{skill_name}...")

        if namespace == "tutor" and skill_name == "grade_work":
            work = input("  student_work> ").strip() or "设 x=1 代入方程"
            result = await registry.call(skill_name, self.problem, work)
            print(f"  Result: error_type={result.error_type.value}, "
                  f"is_correct={result.is_correct}")
            print(f"    desc: {result.error_description}")

        elif namespace == "tutor" and skill_name == "classify_action":
            msg = input("  student_message> ").strip() or "我不会做"
            ctx = TutorContextBuilder.build(self.tutor_session) if self.tutor_session else "（无会话）"
            result = await registry.call(skill_name, self.problem, msg, [], ctx)
            print(f"  Result: action={result['primary_action']}, "
                  f"conf={result['confidence']:.2f}")
            print(f"    reason: {result['reason']}")

        elif namespace == "tutor" and skill_name == "evaluate_checkpoint":
            if self.tutor_session and self.tutor_session.get_current_checkpoint():
                cp = self.tutor_session.get_current_checkpoint()
            else:
                cp = Checkpoint(0, "测试 checkpoint", "测试问题？")
            response = input("  student_response> ").strip() or "我觉得应该先设 x"
            result = await registry.call(skill_name, cp, "", response, "?")
            print(f"  Result: passed={result.checkpoint_passed}, "
                  f"hint_level={result.next_hint_level}")
            print(f"    understanding: {result.student_understanding}")

        elif namespace == "tutor" and skill_name == "plan_guidance":
            result = await registry.call(
                skill_name, self.problem, "测试", "从头开始", GranularityLevel.MEDIUM
            )
            print(f"  Result: {len(result.checkpoints)} checkpoints")
            for cp in result.checkpoints:
                print(f"    [{cp.index+1}] {cp.description}")

        elif namespace == "tutor" and skill_name == "route_decision":
            et_str = input("  error_type> ").strip() or "on_track_stuck"
            try:
                et = ErrorType(et_str)
            except ValueError:
                print(f"  Invalid error_type. Options: {[e.value for e in ErrorType]}")
                return
            gr = GraderResult(error_type=et, is_correct=(et == ErrorType.CORRECT),
                              error_description="test", student_approach="test")
            result = await registry.call(skill_name, gr)
            print(f"  Result: mode={result.mode.value}, "
                  f"needs_plan={result.needs_new_plan}, "
                  f"granularity={result.suggested_granularity.name}")

        elif namespace == "review" and skill_name == "classify_intent":
            msg = input("  student_message> ").strip() or "想看看配方法怎么做"
            known_methods = (
                self.review_session.discovered_methods
                if self.review_session and self.review_session.discovered_methods
                else await self.review_skill_registry.call("enumerate_methods", self.problem)
            )
            history = self.review_session.get_recent_history() if self.review_session else []
            ctx = ReviewContextBuilder.build(self.review_session) if self.review_session else "（无会话）"
            action, target = await registry.call(
                skill_name,
                self.problem,
                msg,
                known_methods,
                history,
                ctx,
            )
            print(f"  Result: action={action.value}, target={target}")

        elif namespace == "review" and skill_name == "enumerate_methods":
            result = await registry.call(skill_name, self.problem)
            print(f"  Result: {len(result)} methods")
            for method in result:
                print(f"    - {method.name}: {method.summary}")

        elif namespace == "review" and skill_name == "solve_method":
            method_name = input("  method_name> ").strip() or "配方法"
            result = await registry.call(skill_name, self.problem, method_name)
            print(f"  Result: method={result.method_name}, steps={len(result.steps)}")
            print(f"    key_insight: {result.key_insight}")

        elif namespace == "review" and skill_name == "evaluate_approach":
            method_name = input("  student_approach> ").strip() or "配方法"
            result = await registry.call(skill_name, self.problem, method_name, "（debug_cli）")
            print(f"  Result: valid={result.is_mathematically_valid}, rec={result.recommendation.value}")
            print(f"    summary: {result.student_approach_summary}")

        elif namespace == "review" and skill_name == "respond_review":
            msg = input("  student_message> ").strip() or "这个方法为什么更好？"
            history = self.review_session.get_recent_history() if self.review_session else []
            result = await registry.call(skill_name, self.problem, msg, "（debug_cli）", history)
            print(f"  Result: {result}")

        elif namespace == "review" and skill_name == "replay_errors":
            history = self.review_session.get_recent_history() if self.review_session else []
            error_snapshots = self.review_session.error_snapshots if self.review_session else []
            struggle_points = self.review_session.struggle_points if self.review_session else []
            student_method = self.review_session.student_method_used if self.review_session else "标准方法"
            result = await registry.call(
                skill_name,
                self.problem,
                error_snapshots,
                struggle_points,
                student_method,
                None,
                None,
                history,
            )
            print(f"  Result: {result}")

        elif namespace == "review" and skill_name in {"ask_understanding", "ask_transfer", "evaluate_understanding"}:
            method_name = input("  method_name> ").strip() or "配方法"
            solved = await self.review_skill_registry.call("solve_method", self.problem, method_name)
            student_method = self.review_session.student_method_used if self.review_session else "标准方法"
            if skill_name == "ask_understanding":
                question, key_points = await registry.call(skill_name, self.problem, solved, student_method)
                print(f"  Result: question={question}")
                print(f"    key_points: {key_points}")
            elif skill_name == "ask_transfer":
                question, key_points = await registry.call(skill_name, self.problem, solved, student_method)
                print(f"  Result: question={question}")
                print(f"    key_points: {key_points}")
            else:
                question = input("  question> ").strip() or f"{method_name}的核心思路是什么？"
                response = input("  student_response> ").strip() or "我觉得关键是先把题目转成适合这个方法的结构。"
                quality, feedback = await registry.call(
                    skill_name,
                    self.problem,
                    method_name,
                    question,
                    "核心关键点",
                    response,
                    solved,
                )
                print(f"  Result: quality={quality.value}")
                print(f"    feedback: {feedback}")

        else:
            print(f"  (调用了 {skill_name}，但此 skill 没有交互式入口，请使用代码调用)")

        print()

    def _cmd_problems(self):
        print("\n  样例题目:")
        for i, p in enumerate(SAMPLE_PROBLEMS, 1):
            print(f"    [{i}] {p.problem_id}: {p.problem[:50]}...")
            print(f"        难度={p.difficulty} 章节={p.chapter}")
        print()

    def _cmd_load(self, arg: str):
        if not arg:
            print("  Usage: load <path/to/problem.json>")
            return
        try:
            self.problem = load_problem_from_json(arg.strip())
            print(f"  已加载: {self.problem.problem_id} — {self.problem.problem[:50]}")
        except Exception as e:
            print(f"  加载失败: {e}")

    def _cmd_mock(self, arg: str):
        if not arg:
            print("  Usage: mock <skill> <k=v ...> | mock reset")
            return
        if arg.strip() == "reset":
            _mock_overrides.clear()
            print("  Mock 覆写已重置")
            return
        parts = arg.split()
        skill = parts[0]
        overrides = {}
        for kv in parts[1:]:
            if "=" in kv:
                k, v = kv.split("=", 1)
                if v.lower() in ("true", "false"):
                    overrides[k] = v.lower() == "true"
                else:
                    overrides[k] = v
        _mock_overrides[skill] = overrides
        print(f"  Mock override set: {skill} = {overrides}")
        print(f"  示例:")
        print(f"    mock grade_work error_type=correct")
        print(f"    mock eval pass=true")
        print(f"    mock classify_action action=handle_frustration")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Section 8: Main
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    parser = argparse.ArgumentParser(description="DeepSenior Debug CLI")
    parser.add_argument("--live", action="store_true",
                        help="使用真实 LLM（需要配置 .env）")
    parser.add_argument("-p", "--problem", type=str, default=None,
                        help="加载外部题目 JSON 文件")
    args = parser.parse_args()

    if args.live and _BOOTSTRAP_ERROR is not None:
        print("  [ERROR] --live 初始化失败，未能加载真实 src/ 基础设施。")
        print(f"  具体错误: {type(_BOOTSTRAP_ERROR).__name__}: {_BOOTSTRAP_ERROR}")
        print("  请修复真实运行环境后重试，或去掉 --live 使用 Mock 模式。")
        return 2

    problem = None
    if args.problem:
        try:
            problem = load_problem_from_json(args.problem)
            print(f"  已加载题目: {problem.problem_id}")
        except Exception as e:
            print(f"  加载题目失败: {e}，使用内置样例")

    repl = DebugREPL(problem=problem, live=args.live)
    asyncio.run(repl.run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
