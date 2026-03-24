#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
TutorManager - 双模式辅导系统协调器

状态机入口：
  handle_submission()       -> 学生提交解题过程
  handle_student_message()  -> 学生在苏格拉底对话中的回复
  stream_student_message()  -> 流式版本

PathEvaluator 触发逻辑（避免频繁调用）：
  触发条件 A：Grader 返回 uses_alternative_method=True
  触发条件 B：Router.evaluate_checkpoint 返回 used_alternative_method=True
  不触发：等价小变体（因式分解 vs 解方程等）由 outcome-focused checkpoint 天然吸收

重构说明（2026-03-20）：
  - 深问子流程 -> DeepDiveHandler
  - 追问/回退 -> RegressionHandler
  - 流式/非流式统一 -> _process_message_core()
"""

import uuid
from datetime import datetime
import pickle
from pathlib import Path
import re
import time
from typing import Any, AsyncGenerator

from agent.infra.logging import get_logger
from agent.knowledge.data_structures import (
    CardRetrieveRequest,
    RetrievalBundle,
    RetrievalConsumer,
    RetrievalGoal,
)

from .skills import SkillRegistry
from .data_structures import (
    AlternativeRecommendation,
    ErrorType,
    GraderResult,
    GranularityLevel,
    ProblemContext,
    TutorAction,
    TutorMode,
    TutorSession,
)
from .action_classifier import ActionClassifier
from .deep_dive_handler import DeepDiveHandler
from .regression_handler import RegressionHandler


class TutorManager:
    """
    双模式辅导管理器

    职责：
      - 会话生命周期管理（创建/获取/关闭/快照）
      - 提交判题主流程（handle_submission）
      - 对话推进核心逻辑（_process_message_core，流式/非流式共用）
      - PathEvaluator 替代解法处理
      - 特殊意图响应
      - 会话导出

    委托给子模块：
      - ActionClassifier: LLM 动作路由
      - DeepDiveHandler: 深问子流程
      - RegressionHandler: 追问与回退
    """

    _NO_ATTEMPT_SIGNALS = [
        "不会", "没思路", "不知道", "不知从何", "没有思路", "不知道怎么做",
        "空白", "不会写", "没做", "做不来", "完全不会", "不会做",
    ]
    _ATTEMPT_ACTION_SIGNALS = [
        "设", "令", "移项", "代入", "化简", "展开", "配方", "求导", "求",
        "联立", "消元", "判别式", "构造", "证明", "所以", "因此", "可得",
    ]
    _MATH_CUE_RE = re.compile(r"\d|[=+\-*/^()（）\[\]{}<>≤≥≠√π]")

    _ACTIVE_SESSION_TTL_SECONDS = 6 * 3600
    _CLOSED_SESSION_TTL_SECONDS = 30 * 60
    _MAX_INTERACTIONS = 200
    _HISTORY_WINDOW = 6
    _CONTENT_MAX_CHARS = 150
    _DEFAULT_SESSION_STORE_DIR = (
        Path(__file__).resolve().parents[3] / "data" / "sessions" / "tutor"
    )

    def __init__(
        self,
        registry: SkillRegistry,
        session_store_dir: str | Path | None = None,
    ):
        self.registry = registry
        self.logger = get_logger("TutorManager")

        # Skills
        self._grade = registry.get("grade_work")
        self._plan = registry.get("plan_guidance")
        self._hint = registry.get("generate_hint")
        self._stream_hint = registry.get("stream_hint")
        self._eval_approach = registry.get("evaluate_approach")
        self._eval_checkpoint = registry.get("evaluate_checkpoint")
        self._route = registry.get("route_decision")
        self._retrieve_cards = registry.get("retrieve_cards")

        # Sub-modules (delegate specialized logic)
        self._action_cls = ActionClassifier(
            classify_action_skill=registry.get("classify_action"),
            logger=self.logger,
        )
        self._deep_dive = DeepDiveHandler(
            ask_socratic_fn=self._ask_socratic,
            logger=self.logger,
        )
        self._regression = RegressionHandler(
            ask_socratic_fn=self._ask_socratic,
            logger=self.logger,
        )

        # Session store
        self._sessions: dict[str, TutorSession] = {}
        self._session_last_access: dict[str, float] = {}
        self._session_store_dir = (
            Path(session_store_dir)
            if session_store_dir
            else self._DEFAULT_SESSION_STORE_DIR
        )
        self._session_store_dir.mkdir(parents=True, exist_ok=True)

    # =========================================================================
    # Session Lifecycle
    # =========================================================================

    def create_session(
        self,
        problem_context: ProblemContext,
        student_id: str | None = None,
        mastery_before: float | None = None,
    ) -> TutorSession:
        self._cleanup_sessions()
        session = TutorSession(
            session_id=str(uuid.uuid4()),
            problem_context=problem_context,
            created_at=datetime.now().timestamp(),
            student_id=student_id,
            mastery_before=mastery_before,
        )
        self._sessions[session.session_id] = session
        self._touch_session(session.session_id)
        self._save_session_snapshot(session)
        self.logger.info(
            f"Session created: {session.session_id} | "
            f"problem={problem_context.problem_id} | chapter={problem_context.chapter}"
        )
        return session

    def get_session(self, session_id: str) -> TutorSession | None:
        self._cleanup_sessions()
        session = self._sessions.get(session_id)
        if session is None:
            session = self._load_session_snapshot(session_id)
            if session:
                self._sessions[session_id] = session
        if session:
            self._touch_session(session_id)
        return session

    def close_session(
        self,
        session_id: str,
        final_status: str | None = None,
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        allowed = {"active", "solved", "abandoned"}
        if final_status in allowed:
            session.status = final_status
        elif session.status == "active":
            session.status = "abandoned"

        self._save_session_snapshot(session)
        self.logger.info(
            f"[{session_id}] TutorSession closed with status={session.status}"
        )
        return self.export_session(session_id)

    # =========================================================================
    # Main Interface 1: Student submits work
    # =========================================================================

    async def handle_submission(
        self,
        session_id: str,
        student_work: str,
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        if session.status in ("solved", "abandoned"):
            return {
                "mode": "closed",
                "message": "本次会话已结束，无法继续提交。",
                "status": session.status,
            }
        try:
            session.total_attempts += 1
            session.add_interaction(
                "student",
                f"[提交解题过程]\n{student_work}",
                {"type": "submission", "attempt": session.total_attempts},
            )

            attempt_level = self._classify_submission_attempt(student_work)
            self.logger.info(
                f"[{session.session_id}] Submission attempt level: {attempt_level}"
            )

            if attempt_level == "blank":
                return await self._handle_no_attempt(session)

            # Grader
            self.logger.info(
                f"[{session.session_id}] Grading attempt #{session.total_attempts}"
            )
            grader_result = await self._grade(session.problem_context, student_work)
            session.last_grader_result = grader_result
            if not grader_result.uses_alternative_method:
                self._reset_alternative_state(session)

            # 触发条件 A：Grader 检测到替代解法
            if (
                grader_result.uses_alternative_method
                and grader_result.alternative_method_name
            ):
                path_result = await self._eval_approach_cached(
                    session,
                    problem_context=session.problem_context,
                    student_approach=grader_result.student_approach,
                    student_work_excerpt=student_work[:800],
                )
                if grader_result.is_correct:
                    self._apply_correct_alternative_evaluation(
                        session=session,
                        path_result=path_result,
                    )
                else:
                    alt_action = await self._handle_alternative_path(
                        session, grader_result, path_result, student_work
                    )
                    if alt_action is not None:
                        return alt_action

            # Error-type dispatch
            if grader_result.is_correct:
                return self._handle_correct(session)
            if grader_result.error_type == ErrorType.COMPUTATIONAL:
                return self._handle_computational_error(session, grader_result)
            if grader_result.error_type == ErrorType.MISCONCEPTION:
                return self._handle_misconception(session, grader_result)
            if grader_result.error_type == ErrorType.INCOMPLETE:
                return self._handle_incomplete(session, grader_result)

            decision = await self._route(grader_result)

            if decision.needs_new_plan:
                await self._create_plan(
                    session, grader_result, decision.suggested_granularity
                )

            submission_context = "[刚提交了解题过程]"
            if attempt_level == "partial":
                seed = student_work.strip().replace("\n", " ")
                submission_context = f"[学生给出了简短思路] {seed[:160]}"
            return await self._ask_socratic(
                session, student_response=submission_context
            )
        finally:
            self._save_session_snapshot(session)

    # =========================================================================
    # Main Interface 2: Student replies in Socratic dialogue
    # =========================================================================

    async def handle_student_message(
        self,
        session_id: str,
        student_message: str,
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        if session.status in ("solved", "abandoned"):
            return {
                "mode": "closed",
                "message": "本次会话已结束，无法继续对话。",
                "status": session.status,
            }
        try:
            result = await self._process_message_core(session, student_message)
            if result is not None:
                return result
            return await self._ask_socratic(
                session, student_response=student_message
            )
        finally:
            self._save_session_snapshot(session)

    async def stream_student_message(
        self,
        session_id: str,
        student_message: str,
    ) -> AsyncGenerator[str, None]:
        session = self._require_session(session_id)
        if session.status in ("solved", "abandoned"):
            yield "本次会话已结束，无法继续对话。"
            return
        try:
            result = await self._process_message_core(session, student_message)
            if result is not None:
                yield result.get("message", "")
                return

            # Fall-through: stream socratic hint
            checkpoint = session.get_current_checkpoint()
            if not checkpoint:
                yield "你已经完成了！"
                return

            full_response = ""
            async for chunk in self._stream_hint(
                problem=session.problem,
                checkpoint=checkpoint,
                interaction_history=self._get_recent_history(session),
                student_response=student_message,
            ):
                full_response += chunk
                yield chunk

            session.add_interaction("tutor", full_response, {
                "type": "socratic",
                "checkpoint_index": session.current_checkpoint,
                "hint_level": checkpoint.hint_level,
            })
            session.total_hints_given += 1
        finally:
            self._save_session_snapshot(session)

    # =========================================================================
    # Core message processing (shared by stream & non-stream)
    # =========================================================================

    async def _process_message_core(
        self,
        session: TutorSession,
        student_message: str,
    ) -> dict[str, Any] | None:
        """
        统一的消息处理核心逻辑（LLM 主导路由）。

        Returns:
            dict — 已产生最终响应，调用方直接返回/yield
            None — 需要继续到 socratic hint 步骤
        """
        # 请求级上下文缓存：避免同一请求内重复构建
        session._req_recent_history = None
        session._req_passed_history = None
        session._req_recent_context = None

        if session.mode == TutorMode.IDLE:
            return {"mode": "idle", "message": "请先提交你的解题过程。"}

        if len(session.interaction_history) >= self._MAX_INTERACTIONS:
            return self._close_for_interaction_limit(session)

        session.add_interaction("student", student_message)

        # 1) LLM 路由（单次调用，替代旧步骤 0-3）
        decision = await self._action_cls.classify(session, student_message)
        action = decision["primary_action"]

        # 2) Action 分发
        if action == TutorAction.START_DEEP_DIVE:
            return await self._deep_dive.start(session, student_message, decision)

        if action == TutorAction.CONTINUE_DEEP_DIVE:
            return await self._deep_dive.handle_turn(session, student_message)

        if action == TutorAction.CLOSE_DEEP_DIVE:
            return await self._deep_dive.close_and_resume(session, student_message)

        if action == TutorAction.HANDLE_FRUSTRATION:
            return await self._generate_emotional_response(
                session, student_message, decision
            )

        if action == TutorAction.HANDLE_ANSWER_REQ:
            return await self._generate_emotional_response(
                session, student_message, decision
            )

        if action == TutorAction.HANDLE_CHALLENGE:
            return await self._generate_emotional_response(
                session, student_message, decision
            )

        if action == TutorAction.EXPLICIT_REGRESS:
            target = (decision.get("target_step") or session.current_checkpoint) - 1
            return await self._regression.handle_hard_regress(
                session=session,
                target_checkpoint=max(target, 0),
                reason=decision.get("reason", ""),
                student_message=student_message,
            )

        if action == TutorAction.FOLLOWUP_QUESTION:
            target = decision.get("target_step")
            return self._regression.handle_followup(
                session=session,
                student_message=student_message,
                target_checkpoint=(target - 1) if target else None,
            )

        if action == TutorAction.OFF_TOPIC:
            msg = "我们先集中在这道题上。你可以试着回答当前的引导问题。"
            session.add_interaction("tutor", msg, {"type": "off_topic"})
            return {"mode": "socratic", "message": msg}

        # 3) CONTINUE_SOCRATIC: checkpoint 评估 + 回退 + 推进
        evaluation = await self._eval_checkpoint(
            checkpoint=session.get_current_checkpoint(),
            passed_checkpoints_history=self._build_passed_history(session),
            student_response=student_message,
            total_checkpoints=(
                len(session.solution_plan.checkpoints)
                if session.solution_plan
                else "?"
            ),
            interaction_context=self._build_recent_context(session),
        )
        checkpoint = session.get_current_checkpoint()
        if checkpoint:
            checkpoint.attempts += 1

        # 回退检测
        if evaluation.regressed_to_checkpoint is not None:
            reg_action, reg_target = self._regression.decide_regression_action(
                session=session,
                student_message=student_message,
                regressed_to_checkpoint=evaluation.regressed_to_checkpoint,
                checkpoint=checkpoint,
            )
            if reg_action == "hard":
                return await self._regression.handle_hard_regress(
                    session=session,
                    target_checkpoint=reg_target,
                    reason=evaluation.reason or "检测到前置步骤遗忘",
                    student_message=student_message,
                )
            return self._regression.handle_followup(
                session=session,
                student_message=student_message,
                target_checkpoint=reg_target,
                reason=evaluation.reason or "检测到前置步骤遗忘",
                from_regression=True,
            )

        # 触发条件 B：Checkpoint 通过但检测到替代解法
        # 仅首次触发；已标记替代方法后走正常推进，避免重复 flag + 重建 plan
        if (
            evaluation.checkpoint_passed
            and evaluation.used_alternative_method
            and not session.used_alternative_method
        ):
            path_result = await self._eval_approach_cached(
                session,
                problem_context=session.problem_context,
                student_approach=evaluation.alternative_method_name or "替代解法",
                student_work_excerpt=student_message,
            )
            alt_action = await self._handle_alternative_path(
                session, session.last_grader_result, path_result, student_message
            )
            if alt_action is not None:
                return alt_action

        # 正常推进
        if evaluation.checkpoint_passed:
            has_more = session.advance_checkpoint()
            if not has_more:
                return self._handle_all_checkpoints_done(session)
        else:
            if checkpoint:
                checkpoint.hint_level = evaluation.next_hint_level

        # Fall through -> caller does socratic hint
        return None

    # =========================================================================
    # PathEvaluator Handling
    # =========================================================================

    def _apply_correct_alternative_evaluation(
        self,
        session: TutorSession,
        path_result: Any,
    ) -> None:
        session.used_alternative_method = True
        self._set_active_alternative_method(
            session,
            path_result.student_approach_summary,
        )
        rec = path_result.recommendation

        if rec == AlternativeRecommendation.ACCEPT:
            session.alternative_flagged = False
            self.logger.info(
                f"[{session.session_id}] Correct submission alt path ACCEPTED: "
                f"{path_result.student_approach_summary}"
            )
            return

        session.alternative_flagged = True
        if rec == AlternativeRecommendation.ACCEPT_WITH_FLAG:
            self.logger.info(
                f"[{session.session_id}] Correct submission alt path ACCEPT_WITH_FLAG: "
                f"{path_result.student_approach_summary}"
            )
            note = (
                "答案正确，但该方法绕过了本题目标知识点。"
                "本次将标记为「知识卡片未强化」。"
            )
        else:
            self.logger.warning(
                f"[{session.session_id}] Correct submission alt path REDIRECT_GENTLE: "
                f"{path_result.student_approach_summary}"
            )
            note = (
                "答案正确，但该替代方法不适合作为本题训练路径。"
                "本次将标记为「知识卡片未强化」。"
            )
        session.add_interaction("system", f"[系统] {note}")

    async def _handle_alternative_path(
        self,
        session: TutorSession,
        grader_result: GraderResult | None,
        path_result,
        student_work: str,
    ) -> dict[str, Any] | None:
        rec = path_result.recommendation
        session.used_alternative_method = True
        self._set_active_alternative_method(
            session,
            path_result.student_approach_summary
            or (grader_result.alternative_method_name if grader_result else None),
        )

        if rec == AlternativeRecommendation.ACCEPT:
            session.alternative_flagged = False
            self.logger.info(
                f"[{session.session_id}] Alternative method ACCEPTED: "
                f"{path_result.student_approach_summary}"
            )
            start_from = path_result.replan_start_from or "从学生当前进度开始"
            alt_method = path_result.student_approach_summary

            # 触发 RAG 检索替代方法的知识卡
            await self._retrieve_supplementary_cards(
                session,
                student_approach=alt_method,
                student_work=student_work,
            )

            fake_result = GraderResult(
                error_type=ErrorType.ON_TRACK_STUCK,
                is_correct=False,
                error_description="学生使用替代解法，重新规划引导路径",
                student_approach=alt_method,
            )
            await self._create_plan(
                session,
                fake_result,
                GranularityLevel.MEDIUM,
                alternative_method=alt_method,
                start_from=start_from,
            )
            return None  # continue to _ask_socratic

        if rec == AlternativeRecommendation.REDIRECT_GENTLE:
            self.logger.info(
                f"[{session.session_id}] Alternative method REDIRECT: "
                f"{path_result.student_approach_summary}"
            )
            session.used_alternative_method = False
            session.alternative_flagged = False
            self._clear_active_alternative_method(session)

            method_display = (
                path_result.student_method_name
                or path_result.student_approach_summary
            )
            redirect_msg = (
                f"你的思路很有创意！{method_display}"
                f"是可以考虑的方向。\n\n"
                f"不过这道题我们来练一下这里的核心方法："
                f"{session.problem_context.get_methods_summary()}。"
                f"原因是：{path_result.redirect_reason or '这个方法是这类题目的通用工具，掌握它以后会更灵活。'}\n\n"
                f"我们重新从这里开始，换个角度来看这道题——"
                f"{session.problem_context.get_hints_summary()[:100]}\n\n"
                "你愿意试试这个思路吗？"
            )
            session.add_interaction(
                "tutor", redirect_msg, {"type": "alternative_redirect"}
            )

            std_result = GraderResult(
                error_type=ErrorType.WRONG_PATH_MINOR,
                is_correct=False,
                error_description="引导回标准方法",
                student_approach=(
                    grader_result.student_approach if grader_result else ""
                ),
            )
            await self._create_plan(session, std_result, GranularityLevel.MEDIUM)
            return {"mode": "socratic", "message": redirect_msg}

        if rec == AlternativeRecommendation.ACCEPT_WITH_FLAG:
            self.logger.info(
                f"[{session.session_id}] Alternative method ACCEPT_WITH_FLAG: "
                f"{path_result.student_approach_summary}"
            )
            session.alternative_flagged = True

            method_display = (
                path_result.student_method_name
                or path_result.student_approach_summary
            )
            flag_msg = (
                f"你用了{method_display}，"
                f"这是更高级的工具，数学上完全正确！\n\n"
                f"我们按照你的思路继续。注意：这道题原本是练习"
                f"{session.problem_context.get_card_titles()}的，"
                f"等做完这道题，建议你也用一下标准方法试试，两种方法都掌握会更全面。"
            )
            session.add_interaction(
                "tutor", flag_msg, {"type": "alternative_accepted_flagged"}
            )

            # 触发 RAG 检索替代方法的知识卡
            await self._retrieve_supplementary_cards(
                session,
                student_approach=path_result.student_approach_summary,
                student_work=student_work,
            )

            start_from = path_result.replan_start_from or "从学生当前进度开始"
            fake_result = GraderResult(
                error_type=ErrorType.ON_TRACK_STUCK,
                is_correct=False,
                error_description="按替代方法继续引导（已标记）",
                student_approach=path_result.student_approach_summary,
            )
            await self._create_plan(
                session,
                fake_result,
                GranularityLevel.MEDIUM,
                alternative_method=path_result.student_approach_summary,
                start_from=start_from,
            )
            next_q = await self._ask_socratic(
                session, student_response=student_work
            )
            next_q["message"] = flag_msg + "\n\n" + next_q["message"]
            return next_q

        return None

    # =========================================================================
    # Error-type Handlers
    # =========================================================================

    async def _handle_no_attempt(self, session: TutorSession) -> dict[str, Any]:
        self._reset_alternative_state(session)
        fake_result = GraderResult(
            error_type=ErrorType.NO_ATTEMPT,
            is_correct=False,
            error_description="学生完全没有思路，需要从审题开始引导",
            student_approach="无",
            suggested_granularity=GranularityLevel.COARSE,
        )
        session.last_grader_result = fake_result
        await self._create_plan(
            session,
            fake_result,
            GranularityLevel.COARSE,
            start_from="从审题开始：引导学生读懂题目条件和要求",
        )
        return await self._ask_socratic(
            session, student_response="[学生表示没有思路]"
        )

    def _handle_correct(self, session: TutorSession) -> dict[str, Any]:
        session.status = "solved"
        session.mode = TutorMode.IDLE
        attempts = session.total_attempts
        msg = (
            "完全正确！第一次就做出来了，很棒！"
            if attempts == 1
            else f"完全正确！经过 {attempts} 次尝试，终于做出来了，坚持很重要！"
        )
        if session.alternative_flagged:
            msg += (
                "\n\n你这次使用了替代方法并得到了正确结果。"
                f"不过这道题的训练目标是「{session.problem_context.get_methods_summary()}」，"
                "建议你再用标准方法独立做一遍。"
            )
        elif session.used_alternative_method:
            msg += "\n\n你用了不同的方法也做对了，思路很灵活。"
        session.add_interaction("tutor", msg, {"type": "correct"})
        return {"mode": "correct", "message": msg}

    def _handle_computational_error(
        self, session: TutorSession, grader_result: GraderResult
    ) -> dict[str, Any]:
        msg = (
            f"你的解题思路完全正确！但这里有个计算失误：\n\n"
            f"**错误位置**：{grader_result.error_location}\n\n"
            f"**正确做法**：{grader_result.correction_note}\n\n"
            "改正后再验算一遍。"
        )
        session.add_interaction("tutor", msg, {"type": "computational_correction"})
        return {"mode": "grading", "message": msg}

    def _handle_misconception(
        self, session: TutorSession, grader_result: GraderResult
    ) -> dict[str, Any]:
        msg = (
            f"这里有个概念上的误解，我们先把它理清：\n\n"
            f"{grader_result.error_description}\n\n"
            f"**正确的概念/公式**：{grader_result.correction_note}\n\n"
            "理解了这个之后，再重新做一遍，你的思路方向其实是对的。"
        )
        session.add_interaction("tutor", msg, {"type": "misconception_correction"})
        return {"mode": "grading", "message": msg}

    def _handle_incomplete(
        self, session: TutorSession, grader_result: GraderResult
    ) -> dict[str, Any]:
        msg = (
            f"解题思路正确，计算也没问题！不过有一个地方需要补充：\n\n"
            f"{grader_result.error_description}\n\n"
            "想一想，你的解答覆盖了所有情况了吗？"
        )
        session.add_interaction("tutor", msg, {"type": "incomplete_prompt"})
        return {"mode": "grading", "message": msg}

    def _handle_all_checkpoints_done(
        self, session: TutorSession
    ) -> dict[str, Any]:
        session.status = "solved"
        session.mode = TutorMode.IDLE
        msg = self._completion_message(session)
        if session.alternative_flagged:
            msg += (
                f"\n\n顺便一提：你这次用了替代方法，建议也用"
                f"{session.problem_context.get_methods_summary()}试一遍，"
                "两种方法都会了才算真正掌握。"
            )
        session.add_interaction("tutor", msg, {"type": "completed"})
        return {"mode": "completed", "message": msg}

    # =========================================================================
    # Emotional / Special Intent Response (LLM-generated)
    # =========================================================================

    async def _generate_emotional_response(
        self,
        session: TutorSession,
        student_message: str,
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        """LLM 根据 action + 上下文直接生成情绪/特殊意图的回复"""
        checkpoint = session.get_current_checkpoint()
        action = decision["primary_action"]

        # 规则护栏：情绪时自动升提示
        if checkpoint and action in (
            TutorAction.HANDLE_FRUSTRATION,
            TutorAction.HANDLE_ANSWER_REQ,
        ):
            checkpoint.escalate_hint()

        response = await self._hint(
            problem=session.problem,
            checkpoint=checkpoint,
            interaction_history=self._get_recent_history(session),
            student_response=f"[{action.value}] {student_message}",
        )
        session.add_interaction("tutor", response, {
            "type": action.value,
            "reason": decision.get("reason", ""),
        })
        return {"mode": "socratic", "message": response}

    # =========================================================================
    # Internal Helpers
    # =========================================================================

    @staticmethod
    def _normalize_method_key(approach: str) -> str:
        """将 student_approach 归一化为缓存 key（去空格、小写）。"""
        return approach.strip().lower().replace(" ", "")

    async def _eval_approach_cached(self, session, **kwargs):
        """PathEvaluator 结果缓存：同一 student_approach 不重复调用。"""
        approach = kwargs.get("student_approach", "")
        cache_key = self._normalize_method_key(approach)
        cache = getattr(session, "_path_eval_cache", None)
        if cache is None:
            cache = {}
            session._path_eval_cache = cache
        if cache_key in cache:
            self.logger.debug(
                f"[{session.session_id}] PathEvaluator cache hit: {approach[:40]}"
            )
            return cache[cache_key]
        result = await self._eval_approach(**kwargs)
        cache[cache_key] = result
        return result

    async def _retrieve_supplementary_cards(
        self,
        session: TutorSession,
        student_approach: str | None = None,
        student_work: str | None = None,
    ) -> RetrievalBundle | None:
        """触发二阶段检索，结果存到 session.last_retrieval_bundle。"""
        pc = session.problem_context
        try:
            request = CardRetrieveRequest(
                consumer=RetrievalConsumer.PLANNER.value,
                question_id=pc.problem_id,
                active_solution_id=None,
                chapter=pc.chapter,
                topic=None,
                problem_text=pc.problem,
                student_work=student_work,
                student_approach=student_approach,
                target_card_ids=[c.card_id for c in pc.knowledge_cards] if pc.knowledge_cards else [],
                retrieval_goal=RetrievalGoal.METHOD_REFERENCE.value,
                session_id=session.session_id,
            )
            bundle = await self._retrieve_cards(request)
            session.last_retrieval_bundle = bundle
            self.logger.info(
                f"[{session.session_id}] RAG retrieve: "
                f"slot={bundle.router_primary_slot}, "
                f"cards={bundle.selected_card_ids}, "
                f"audits={len(bundle.audit_entries)}"
            )
            return bundle
        except Exception as e:
            self.logger.warning(
                f"[{session.session_id}] RAG retrieve failed, skipping: {e}"
            )
            return None

    @staticmethod
    def _format_supplementary_cards(bundle: RetrievalBundle | None) -> str:
        """将 RAG 检索结果格式化为 Planner 可消费的文本。"""
        if not bundle or not bundle.result.supplementary_cards:
            return ""
        lines = []
        for rc in bundle.result.supplementary_cards:
            card = rc.card
            methods = "、".join(card.general_methods[:2]) if card.general_methods else ""
            hints = "; ".join(card.hints.get(i, "") for i in sorted(card.hints)[:1]) if card.hints else ""
            mistakes = "；".join(card.common_mistakes[:2]) if card.common_mistakes else ""
            parts = [f"- {card.title}"]
            if methods:
                parts.append(f"  通法: {methods}")
            if hints:
                parts.append(f"  提示: {hints}")
            if mistakes:
                parts.append(f"  易错: {mistakes}")
            lines.append("\n".join(parts))
        return "\n".join(lines)

    async def _create_plan(
        self,
        session: TutorSession,
        grader_result: GraderResult,
        granularity: GranularityLevel,
        alternative_method: str | None = None,
        start_from: str | None = None,
    ) -> None:
        if start_from is None:
            if grader_result.error_type == ErrorType.WRONG_PATH_MAJOR:
                start_from = "从头开始"
            elif grader_result.error_type == ErrorType.NO_ATTEMPT:
                start_from = "从审题开始"
            else:
                loc = grader_result.error_location or "出错处"
                start_from = f"从错误处开始：{loc}"

        # 从 session 上读取 RAG 检索结果（如果有的话）
        retrieval_bundle = getattr(session, "last_retrieval_bundle", None)
        supplementary_cards_text = self._format_supplementary_cards(retrieval_bundle)

        plan = await self._plan(
            problem_context=session.problem_context,
            error_description=grader_result.error_description,
            start_from=start_from,
            granularity=granularity,
            alternative_method=alternative_method,
            supplementary_cards=supplementary_cards_text or None,
        )
        session.solution_plan = plan
        session.current_checkpoint = 0
        self.logger.info(
            f"[{session.session_id}] Plan: {len(plan.checkpoints)} checkpoints "
            f"({plan.granularity.name})"
            + (f" [alt: {alternative_method}]" if alternative_method else "")
        )

    async def _ask_socratic(
        self, session: TutorSession, student_response: str
    ) -> dict[str, Any]:
        session.mode = TutorMode.SOCRATIC
        checkpoint = session.get_current_checkpoint()
        if not checkpoint:
            return {
                "mode": "error",
                "message": "引导计划异常，请重新提交解题过程。",
            }

        response = await self._hint(
            problem=session.problem,
            checkpoint=checkpoint,
            interaction_history=self._get_recent_history(session),
            student_response=student_response,
        )

        session.add_interaction("tutor", response, {
            "type": "socratic",
            "checkpoint_index": session.current_checkpoint,
            "hint_level": checkpoint.hint_level,
        })
        session.total_hints_given += 1

        total = (
            len(session.solution_plan.checkpoints) if session.solution_plan else "?"
        )
        return {
            "mode": "socratic",
            "message": response,
            "checkpoint_index": session.current_checkpoint + 1,
            "checkpoint_total": total,
            "hint_level": checkpoint.hint_level,
        }

    def _completion_message(self, session: TutorSession) -> str:
        hints = session.total_hints_given
        if hints <= 2:
            return (
                "很好！你只需要一点点提示就找到了解法，思路很清晰。"
                "现在试着自己完整写一遍。"
            )
        elif hints <= 5:
            return (
                "不错！经过几次引导，你自己想清楚了。"
                "把完整过程写下来巩固一下。"
            )
        else:
            return (
                "你坚持下来了，这道题不容易，但你最终想通了。"
                "建议把它记录到错题本里，过几天再做一遍。"
            )

    def _close_for_interaction_limit(self, session: TutorSession) -> dict[str, Any]:
        session.status = "abandoned"
        session.mode = TutorMode.IDLE
        msg = "本次对话已达到轮次上限，建议先提交当前进度，之后开启新会话。"
        session.add_interaction(
            "system",
            f"[系统] {msg}",
            {"type": "auto_closed_limit"},
        )
        return {"mode": "closed", "message": msg, "status": session.status}

    @classmethod
    def _get_recent_history(
        cls,
        session: TutorSession,
        max_entries: int | None = None,
        max_content_chars: int | None = None,
    ) -> list[dict[str, Any]]:
        """返回截取后的最近历史，content 截断到 max_content_chars 字符。同一请求内缓存。"""
        cached = getattr(session, "_req_recent_history", None)
        if cached is not None:
            return cached
        window = max_entries or cls._HISTORY_WINDOW
        limit = max_content_chars or cls._CONTENT_MAX_CHARS
        recent = session.interaction_history[-window:]
        result = [
            {**entry, "content": entry.get("content", "")[:limit]}
            for entry in recent
        ]
        session._req_recent_history = result
        return result

    def _build_passed_history(self, session: TutorSession) -> str:
        cached = getattr(session, "_req_passed_history", None)
        if cached is not None:
            return cached
        if not session.solution_plan:
            result = "（尚无引导计划）"
        else:
            passed = [
                cp
                for i, cp in enumerate(session.solution_plan.checkpoints)
                if i < session.current_checkpoint
            ]
            if not passed:
                result = "（学生尚未通过任何 checkpoint）"
            else:
                items = " ".join(
                    f"{cp.index + 1}.{cp.description}" for cp in passed
                )
                result = f"已通过: {items}"
        session._req_passed_history = result
        return result

    @staticmethod
    def _build_recent_context(session: TutorSession, max_entries: int = 6) -> str:
        """构建最近对话上下文（含提交和导师引导），供 checkpoint 评估使用。同一请求内缓存。"""
        cached = getattr(session, "_req_recent_context", None)
        if cached is not None:
            return cached
        history = session.interaction_history[-max_entries:]
        if not history:
            result = "（无对话记录）"
        else:
            lines = []
            for h in history:
                role = {"student": "学生", "tutor": "导师", "system": "系统"}.get(
                    h.get("role", "?"), h.get("role", "?")
                )
                content = h.get("content", "")[:200]
                lines.append(f"{role}：{content}")
            result = "\n".join(lines)
        session._req_recent_context = result
        return result

    # =========================================================================
    # Submission Classification
    # =========================================================================

    def _is_no_attempt(self, student_work: str) -> bool:
        return self._classify_submission_attempt(student_work) == "blank"

    @classmethod
    def _classify_submission_attempt(cls, student_work: str) -> str:
        stripped = student_work.strip()
        if not stripped:
            return "blank"

        compact = re.sub(r"\s+", "", stripped)
        has_no_attempt_signal = any(
            kw in compact for kw in cls._NO_ATTEMPT_SIGNALS
        )
        has_math_symbol = bool(cls._MATH_CUE_RE.search(compact))
        has_action_signal = any(
            kw in compact for kw in cls._ATTEMPT_ACTION_SIGNALS
        )
        has_math_cue = has_math_symbol or has_action_signal

        if has_no_attempt_signal and not has_math_cue:
            return "blank"

        if len(compact) <= 28:
            if has_math_cue:
                return "partial"
            return "blank" if len(compact) <= 8 else "partial"

        if has_math_cue:
            return "effective"
        return "partial"

    # =========================================================================
    # Session Persistence
    # =========================================================================

    def _touch_session(self, session_id: str) -> None:
        self._session_last_access[session_id] = time.time()

    def _cleanup_sessions(self) -> None:
        now_ts = time.time()

        for session_id, session in list(self._sessions.items()):
            last_access = self._session_last_access.get(
                session_id, session.created_at
            )
            ttl = (
                self._ACTIVE_SESSION_TTL_SECONDS
                if session.status == "active"
                else self._CLOSED_SESSION_TTL_SECONDS
            )
            if now_ts - last_access <= ttl:
                continue

            self._sessions.pop(session_id, None)
            self._session_last_access.pop(session_id, None)
            if session.status != "active":
                self._delete_session_snapshot(session_id)

        for snapshot in self._session_store_dir.glob("*.pkl"):
            session_id = snapshot.stem
            if session_id in self._sessions:
                continue
            try:
                age = now_ts - snapshot.stat().st_mtime
            except OSError:
                continue
            if age <= self._CLOSED_SESSION_TTL_SECONDS:
                continue
            session = self._load_session_snapshot(session_id, touch=False)
            if session and session.status != "active":
                self._delete_session_snapshot(session_id)

    def _session_snapshot_path(self, session_id: str) -> Path:
        return self._session_store_dir / f"{session_id}.pkl"

    def _save_session_snapshot(self, session: TutorSession) -> None:
        path = self._session_snapshot_path(session.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        try:
            with open(tmp_path, "wb") as f:
                pickle.dump(session, f, protocol=pickle.HIGHEST_PROTOCOL)
            tmp_path.replace(path)
            self._touch_session(session.session_id)
        except Exception as e:
            self.logger.warning(
                f"[{session.session_id}] Failed to save TutorSession snapshot: {e}"
            )
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass

    def _load_session_snapshot(
        self,
        session_id: str,
        touch: bool = True,
    ) -> TutorSession | None:
        path = self._session_snapshot_path(session_id)
        if not path.exists():
            return None
        try:
            with open(path, "rb") as f:
                session = pickle.load(f)
            if not isinstance(session, TutorSession):
                self.logger.warning(
                    f"[{session_id}] Invalid TutorSession snapshot type"
                )
                return None
            if touch:
                self._touch_session(session_id)
            return session
        except Exception as e:
            self.logger.warning(
                f"[{session_id}] Failed to load TutorSession snapshot: {e}"
            )
            return None

    def _delete_session_snapshot(self, session_id: str) -> None:
        path = self._session_snapshot_path(session_id)
        try:
            if path.exists():
                path.unlink()
        except OSError as e:
            self.logger.warning(
                f"[{session_id}] Failed to delete TutorSession snapshot: {e}"
            )

    def _require_session(self, session_id: str) -> TutorSession:
        session = self.get_session(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")
        return session

    # =========================================================================
    # Export for Progress Module
    # =========================================================================

    @staticmethod
    def _normalize_outcome(status: str) -> str:
        mapping = {
            "active": "in_progress",
            "solved": "solved",
            "abandoned": "gave_up",
        }
        return mapping.get((status or "").strip().lower(), "in_progress")

    def export_session(self, session_id: str) -> dict[str, Any]:
        session = self._require_session(session_id)
        plan = session.solution_plan
        pc = session.problem_context
        checkpoints_completed = (
            sum(1 for cp in plan.checkpoints if cp.passed) if plan else 0
        )
        total_checkpoints = len(plan.checkpoints) if plan else 0

        error_types_seen: list[str] = []
        gr = session.last_grader_result
        if gr:
            error_types_seen.append(gr.error_type.value)

        struggle_checkpoints = []
        if plan:
            for cp in plan.checkpoints:
                if cp.hint_level >= 3:
                    struggle_checkpoints.append({
                        "index": cp.index,
                        "description": cp.description,
                        "guiding_question": cp.guiding_question,
                        "hint_level_reached": cp.hint_level,
                        "passed": cp.passed,
                    })

        outcome = self._normalize_outcome(session.status)
        question_tags = [kc.card_id for kc in pc.knowledge_cards]
        active_alt_method = self._get_active_alternative_method(session)
        alternative_method = (
            active_alt_method
            or (
                gr.alternative_method_name
                if gr and session.used_alternative_method and gr.alternative_method_name
                else None
            )
        )

        base_problem_id = (
            pc.problem_id if hasattr(pc, "problem_id") else "unknown_problem"
        )
        solution_id = f"{base_problem_id}::standard"
        solution_method = None
        solution_tags = list(question_tags)
        needs_solution_card_audit = False
        solution_card_audit_reason = None

        if session.used_alternative_method:
            solution_method = alternative_method or "替代方法（未命名）"
            solution_id = (
                f"{base_problem_id}::alt::"
                f"{self._normalize_method_key(solution_method)}"
            )
            mapped_tags = self._find_solution_tags_for_method(pc, solution_method)
            if mapped_tags:
                solution_tags = mapped_tags
            else:
                solution_tags = []
                needs_solution_card_audit = True
                solution_card_audit_reason = (
                    f"尚未建立方法「{solution_method}」的知识卡片索引，"
                    "需要后台 RAG 发现并提交审计任务。"
                )

        self._deep_dive.ensure_state(session)
        deep_records = list(getattr(session, "deep_dive_records", []))
        deferred_tasks = list(
            getattr(session, "deferred_deep_dive_tasks", [])
        )
        deep_topics: list[str] = []
        deep_understanding_by_topic: dict[str, str] = {}
        for rec in deep_records:
            topic = str(rec.get("topic") or "").strip()
            if topic and topic not in deep_topics:
                deep_topics.append(topic)
            if rec.get("event") == "close" and topic:
                deep_understanding_by_topic[topic] = str(
                    rec.get("understanding") or "unknown"
                )
        deep_dive_count = sum(
            1 for rec in deep_records if rec.get("event") == "start"
        )

        return {
            "session_id": session.session_id,
            "student_id": session.student_id,
            "problem_id": pc.problem_id if hasattr(pc, "problem_id") else "",
            "chapter": pc.chapter if hasattr(pc, "chapter") else "",
            "tags": question_tags,
            "status": session.status,
            "outcome": outcome,
            "total_hints_given": session.total_hints_given,
            "total_attempts": session.total_attempts,
            "checkpoints_completed": checkpoints_completed,
            "total_checkpoints": total_checkpoints,
            "error_types_seen": error_types_seen,
            "alternative_flagged": session.alternative_flagged,
            "used_alternative_method": session.used_alternative_method,
            "alternative_method": solution_method,
            # question -> solution 分叉索引
            "solution_id": solution_id,
            "solution_method": solution_method,
            "solution_tags": solution_tags,
            "method_slot_matched": getattr(
                getattr(session, "last_retrieval_bundle", None),
                "router_primary_slot", None,
            ),
            "needs_solution_card_audit": needs_solution_card_audit,
            "solution_card_audit_reason": solution_card_audit_reason,
            # 深问审计字段
            "deep_dive_count": deep_dive_count,
            "deep_dive_topics": deep_topics,
            "deep_dive_understanding": deep_understanding_by_topic,
            "deferred_deep_dive_tasks": deferred_tasks,
            # Review 用于错误回放
            "error_details": (
                {
                    "error_type": gr.error_type.value if gr else None,
                    "error_location": gr.error_location if gr else None,
                    "error_description": gr.error_description if gr else None,
                    "correction_note": gr.correction_note if gr else None,
                }
                if gr
                else None
            ),
            "struggle_checkpoints": struggle_checkpoints,
            # 扩展摘要
            "summary": {
                "solved": outcome == "solved",
                "granularity_used": plan.granularity.name if plan else None,
                "knowledge_cards": [kc.card_id for kc in pc.knowledge_cards],
                "deep_dive_count": deep_dive_count,
                "deferred_deep_dive_tasks": len(deferred_tasks),
            },
        }

    @staticmethod
    def _set_active_alternative_method(
        session: TutorSession,
        method_name: str | None,
    ) -> None:
        cleaned = str(method_name or "").strip()
        setattr(
            session,
            "active_alternative_method_name",
            cleaned or None,
        )

    @staticmethod
    def _get_active_alternative_method(session: TutorSession) -> str | None:
        method_name = getattr(session, "active_alternative_method_name", None)
        cleaned = str(method_name or "").strip()
        return cleaned or None

    @classmethod
    def _clear_active_alternative_method(cls, session: TutorSession) -> None:
        cls._set_active_alternative_method(session, None)

    @classmethod
    def _reset_alternative_state(cls, session: TutorSession) -> None:
        session.used_alternative_method = False
        session.alternative_flagged = False
        cls._clear_active_alternative_method(session)

    @staticmethod
    def _normalize_method_key(method_name: str) -> str:
        compact = re.sub(r"\s+", "", (method_name or "").strip().lower())
        compact = re.sub(
            r"[^0-9a-zA-Z\u4e00-\u9fff]+", "_", compact
        ).strip("_")
        return compact or "unknown"

    @classmethod
    def _method_aliases(cls, method_name: str) -> set[str]:
        compact = re.sub(r"\s+", "", (method_name or "").strip().lower())
        if not compact:
            return set()
        aliases = {compact}
        stripped = re.sub(r"[（(].*?[）)]", "", compact).strip()
        if stripped:
            aliases.add(stripped)
        return aliases

    @classmethod
    def _find_solution_tags_for_method(
        cls,
        problem_context: ProblemContext,
        method_name: str,
    ) -> list[str]:
        target_aliases = cls._method_aliases(method_name)
        if not target_aliases:
            return []

        matched: list[str] = []
        for kc in problem_context.knowledge_cards:
            aliases: set[str] = set()
            for gm in kc.general_methods or []:
                aliases.update(cls._method_aliases(gm))
            if not aliases:
                continue

            exact = bool(target_aliases & aliases)
            fuzzy = any(
                (t in a or a in t)
                for t in target_aliases
                for a in aliases
                if len(t) >= 2 and len(a) >= 2
            )
            if exact or fuzzy:
                matched.append(kc.card_id)

        return list(dict.fromkeys(matched))
