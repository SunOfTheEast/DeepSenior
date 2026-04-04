#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
TutorManager - 双模式辅导系统协调器

状态机入口：
  handle_submission()       -> 学生提交解题过程（v2 管线：Analyzer→Normalizer→Decision→Act）
  handle_student_message()  -> 学生在对话中的回复（Think→Act 管线）
  stream_student_message()  -> 流式版本（Think→Act 管线）

重构说明（2026-03-30）：
  v3 对话管线：Think→Act + SessionListener + StrategyHooks
  - handle_student_message / stream_student_message 由 ThinkActManager 驱动
  - 删除旧 classifier→handler 管线（ActionClassifier, DeepDiveHandler, MethodInquiryHandler 等）
  - handle_submission 管线保持不变
"""

from pathlib import Path
from typing import Any, AsyncGenerator

from agent.infra.logging import get_logger
from agent.knowledge.concept_registry import ConceptRegistry
from agent.knowledge.solution_link_store import SolutionLinkStore
from agent.memory.memory_manager import MemoryManager

from .skills import SkillRegistry
from .data_structures import (
    ProblemContext,
    TutorMode,
    TutorSession,
)
from .pipeline.pending_slot import PendingSlot
from .pipeline.session_store import SessionStore
from .pipeline.session_exporter import SessionExporter
from .pipeline.knowledge_bridge import KnowledgeBridge
from .pipeline.attempt_analyzer import AttemptAnalyzer
from .pipeline.diagnosis_normalizer import DiagnosisNormalizer
from .pipeline.escalation_policy import EscalationPolicy
from .pipeline.strong_method_verifier import (
    StrongMethodVerifier,
    get_active_alternative_method,
    reset_alternative_state,
)
from .pipeline.decision_policy import DecisionPolicy
from .pipeline.action_planner import ActionPlanner
from .pipeline.state_reducer import StateReducer
from .pipeline.reply_composer import ReplyComposer
from .pipeline.session_listener import SessionListener
from .pipeline.strategy_hooks import TaggingHook, GuardrailHook, DataHook
from .think_act_manager import ThinkActManager
from .think_act_types import TeachingSkill
from .tools.tool_registry import TutorToolRegistry


class TutorManager:
    """
    双模式辅导管理器

    职责：
      - 会话生命周期管理（创建/获取/关闭/快照）
      - 提交判题主流程（handle_submission）— v2 管线编排
      - 对话推进（handle_student_message / stream_student_message）— Think→Act 管线
      - 会话导出

    对话管线（Think→Act）：
      - ThinkActManager 编排：Listener → Think → hooks → state_update → Act → post_hooks
      - 教学方法由 LLM 自由决定，不受枚举约束
      - SessionListener 暴露信号，代码只在结构性安全边界拦截

    提交管线（v2，不变）：
      - AttemptAnalyzer → DiagnosisNormalizer → EscalationPolicy
      - → DecisionPolicy → ActionPlanner → StateReducer → ReplyComposer
    """

    _MAX_INTERACTIONS = 200
    _MAX_CONSECUTIVE_BLANK_SUBMISSIONS = 3

    # TeachingSkill YAML 默认路径
    _DEFAULT_SKILL_PATH = Path(__file__).parent / "skills" / "yang_teacher.yaml"

    def __init__(
        self,
        registry: SkillRegistry,
        session_store_dir: str | Path | None = None,
        teaching_skill_path: str | Path | None = None,
    ):
        self.registry = registry
        self.logger = get_logger("TutorManager")

        # Skills (submission pipeline)
        self._grade = registry.get("grade_work")
        self._plan = registry.get("plan_guidance")
        self._hint = registry.get("generate_hint")
        self._eval_approach = registry.get("evaluate_approach")
        self._eval_checkpoint = registry.get("evaluate_checkpoint")
        self._eval_progress_map = registry.get("evaluate_progress_map")
        self._retrieve_cards = registry.get("retrieve_cards")
        self._concept_registry = ConceptRegistry()
        self._solution_link_store = SolutionLinkStore()

        # Knowledge bridge
        self._knowledge = KnowledgeBridge(
            retrieve_cards_fn=self._retrieve_cards,
            registry=registry,
        )
        self._memory = MemoryManager(
            api_key=registry.think_agent.api_key or "",
            base_url=registry.think_agent.base_url or "",
            language=registry.think_agent.language,
            api_version=registry.think_agent.api_version,
            binding=registry.think_agent.binding,
        )

        # Reply composer (submission pipeline)
        self._reply_composer = ReplyComposer()

        # Submission pipeline components (v2, unchanged)
        self._attempt_analyzer = AttemptAnalyzer(grade_fn=self._grade)
        self._normalizer = DiagnosisNormalizer()
        self._escalation = EscalationPolicy()
        self._decision_policy = DecisionPolicy()
        self._action_planner = ActionPlanner()

        self._state_reducer = StateReducer(
            plan_fn=self._plan,
            hint_fn=self._hint,
            eval_checkpoint_fn=self._eval_checkpoint,
            eval_progress_map_fn=self._eval_progress_map,
            eval_approach_fn=self._eval_approach,
            handle_alternative_path_fn=None,  # type: ignore[arg-type]
            regression_handler=None,  # type: ignore[arg-type]
            knowledge_bridge=self._knowledge,
            reply_composer=self._reply_composer,
        )

        self._verifier = StrongMethodVerifier(
            eval_approach_fn=self._eval_approach,
            rebuild_plan_fn=self._state_reducer._rebuild_plan_and_restore,
            ask_socratic_fn=self._state_reducer._ask_socratic,
            knowledge_bridge=self._knowledge,
        )
        self._state_reducer._handle_alternative_path = (
            self._verifier.handle_alternative_path
        )

        # RegressionHandler — still needed by StateReducer for submission pipeline
        from .regression_handler import RegressionHandler
        self._regression = RegressionHandler(
            ask_socratic_fn=self._state_reducer._ask_socratic,
            logger=self.logger,
        )
        self._state_reducer._regression = self._regression

        # ── Think→Act 对话管线 ──
        skill_path = Path(teaching_skill_path) if teaching_skill_path else self._DEFAULT_SKILL_PATH
        self._teaching_skill = TeachingSkill.from_yaml(skill_path)

        listener = SessionListener()
        pre_hooks = [TaggingHook(), GuardrailHook()]
        post_hooks = [DataHook()]

        def _make_tool_registry(pc: ProblemContext) -> TutorToolRegistry:
            return TutorToolRegistry(
                problem_context=pc,
                concept_registry=self._concept_registry,
                card_store=self.registry.card_store,
                card_index=self.registry.card_index,
            )

        self._think_act = ThinkActManager(
            signal_agent=registry.signal_agent,
            strategy_agent=registry.strategy_agent,
            act_agent=registry.act_agent,
            planner_agent=registry.planner_agent,
            teaching_skill=self._teaching_skill,
            progress_verifier_fn=self._eval_progress_map,
            knowledge_bridge=self._knowledge,
            memory_manager=self._memory,
            listener=listener,
            pre_hooks=pre_hooks,
            post_hooks=post_hooks,
            tool_registry_factory=_make_tool_registry,
        )

        # Session store & exporter
        self._store = SessionStore(store_dir=session_store_dir)
        self._exporter = SessionExporter(
            concept_registry=self._concept_registry,
            solution_link_store=self._solution_link_store,
        )

    # =========================================================================
    # Session Lifecycle
    # =========================================================================

    def create_session(
        self,
        problem_context: ProblemContext,
        student_id: str | None = None,
        mastery_before: float | None = None,
    ) -> TutorSession:
        # Pre-load published knowledge cards (L0 menu + L1 full)
        from .pipeline.card_preloader import preload_published_cards
        preload_published_cards(problem_context, self.registry.card_store)

        return self._store.create(problem_context, student_id, mastery_before)

    def get_session(self, session_id: str) -> TutorSession | None:
        return self._store.get(session_id)

    def close_session(
        self,
        session_id: str,
        final_status: str | None = None,
    ) -> dict[str, Any]:
        session = self._store.require(session_id)
        allowed = {"active", "solved", "abandoned"}
        if final_status in allowed:
            session.status = final_status
        elif session.status == "active":
            session.status = "abandoned"

        PendingSlot.cancel(session, reason="session_closed", logger=self.logger)
        self._store.save_snapshot(session)
        self.logger.info(
            f"[{session_id}] TutorSession closed with status={session.status}"
        )
        return self.export_session(session_id)

    # =========================================================================
    # Main Interface 1: Student submits work (v2 Pipeline)
    # =========================================================================

    async def handle_submission(
        self,
        session_id: str,
        student_work: str,
    ) -> dict[str, Any]:
        session = self._store.require(session_id)
        if session.status in ("solved", "abandoned"):
            return {
                "mode": "closed",
                "message": "\u672c\u6b21\u4f1a\u8bdd\u5df2\u7ed3\u675f\uff0c\u65e0\u6cd5\u7ee7\u7eed\u63d0\u4ea4\u3002",
                "status": session.status,
            }
        try:
            # 提交解题过程时，取消任何待澄清交互
            PendingSlot.cancel(session, reason="submission_received", logger=self.logger)

            session.total_attempts += 1
            session.add_interaction(
                "student",
                f"[\u63d0\u4ea4\u89e3\u9898\u8fc7\u7a0b]\n{student_work}",
                {"type": "submission", "attempt": session.total_attempts},
            )

            # ── Pipeline Step 1: Analyze ──
            analysis = await self._attempt_analyzer.analyze(
                session.problem_context, student_work
            )
            self.logger.info(
                f"[{session.session_id}] Submission attempt level: "
                f"{analysis.attempt_level}"
            )

            # ── Pre-pipeline guard: blank streak ──
            if analysis.attempt_level == "blank":
                session.blank_submission_streak += 1
                if (
                    session.blank_submission_streak
                    >= self._MAX_CONSECUTIVE_BLANK_SUBMISSIONS
                ):
                    msg = ReplyComposer.compose_blank_streak(session)
                    session.add_interaction(
                        "tutor", msg,
                        {
                            "type": "blank_submission_guard",
                            "blank_submission_streak":
                                session.blank_submission_streak,
                        },
                    )
                    return {
                        "mode": "socratic",
                        "message": msg,
                        "blank_submission_streak":
                            session.blank_submission_streak,
                    }
            else:
                session.blank_submission_streak = 0

            # ── Store grader result on session ──
            grader_result = analysis.grader_result
            if grader_result is not None:
                session.last_grader_result = grader_result
                if not grader_result.uses_alternative_method:
                    reset_alternative_state(session)

                # ── Pipeline Step 1.5: Alternative path escalation ──
                if self._escalation.should_handle_alternative(grader_result):
                    path_result = await self._verifier.eval_approach_cached(
                        session,
                        problem_context=session.problem_context,
                        student_approach=grader_result.student_approach,
                        student_work_excerpt=student_work[:800],
                    )
                    if grader_result.is_correct:
                        self._verifier.apply_correct_alternative(
                            session=session, path_result=path_result,
                        )
                    else:
                        alt_action = await self._verifier.handle_alternative_path(
                            session, grader_result, path_result, student_work,
                        )
                        if alt_action is not None:
                            return alt_action

            # ── Pipeline Step 2: Normalize diagnosis ──
            diagnosis = self._normalizer.normalize(
                analysis.diagnosis, analysis.attempt_level
            )

            # ── Pipeline Step 3: Decide ──
            decision = self._decision_policy.decide(diagnosis, session)
            self.logger.info(
                f"[{session.session_id}] Decision: "
                f"feedback={decision.feedback_mode.value}, "
                f"plan={decision.plan_control.value}"
            )

            # ── Pipeline Step 4: Plan actions ──
            action_plan = self._action_planner.plan(
                decision, diagnosis, session
            )

            # ── Pipeline Step 5: Execute ──
            return await self._state_reducer.execute(
                action_plan, session, diagnosis, decision,
                grader_result, student_work,
            )
        finally:
            self._store.save_snapshot(session)

    # =========================================================================
    # Main Interface 2: Student replies in dialogue (Think→Act pipeline)
    # =========================================================================

    async def handle_student_message(
        self,
        session_id: str,
        student_message: str,
    ) -> dict[str, Any]:
        session = self._store.require(session_id)
        if session.status in ("solved", "abandoned"):
            return {
                "mode": "closed",
                "message": "本次会话已结束，无法继续对话。",
                "status": session.status,
            }
        try:
            if session.mode == TutorMode.IDLE:
                return {"mode": "idle", "message": "请先提交你的解题过程。"}
            if len(session.interaction_history) >= self._MAX_INTERACTIONS:
                return self._close_for_interaction_limit(session)
            if student_message.strip():
                session.blank_submission_streak = 0

            return await self._think_act.process(session, student_message)
        finally:
            self._store.save_snapshot(session)

    async def stream_student_message(
        self,
        session_id: str,
        student_message: str,
    ) -> AsyncGenerator[str, None]:
        session = self._store.require(session_id)
        if session.status in ("solved", "abandoned"):
            yield "本次会话已结束，无法继续对话。"
            return
        try:
            if session.mode == TutorMode.IDLE:
                yield "请先提交你的解题过程。"
                return
            if len(session.interaction_history) >= self._MAX_INTERACTIONS:
                result = self._close_for_interaction_limit(session)
                yield result.get("message", "")
                return
            if student_message.strip():
                session.blank_submission_streak = 0

            async for chunk in self._think_act.stream_process(session, student_message):
                yield chunk
        finally:
            self._store.save_snapshot(session)

    # =========================================================================
    # Internal Helpers
    # =========================================================================

    def _close_for_interaction_limit(self, session: TutorSession) -> dict[str, Any]:
        session.status = "abandoned"
        session.mode = TutorMode.IDLE
        msg = ReplyComposer.compose_closed_limit()
        session.add_interaction(
            "system",
            f"[\u7cfb\u7edf] {msg}",
            {"type": "auto_closed_limit"},
        )
        return {"mode": "closed", "message": msg, "status": session.status}

    # =========================================================================
    # Export
    # =========================================================================

    def export_session(self, session_id: str) -> dict[str, Any]:
        session = self._store.require(session_id)
        return self._exporter.export(
            session,
            get_active_alternative_method_fn=get_active_alternative_method,
        )
