#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ReviewChatManager - 复盘会话调度器

入口：
  create_session()  — 创建复盘会话（可携带 Tutor 会话 export 以启用错误回放）
  chat()            — 处理学生消息

路由逻辑：
  pending_verification 状态  → 优先路由到理解验证评估
                              （两阶段：概念理解 → 迁移验证）
  REPLAY_ERRORS              → 错误回放（对比原始错误 vs 新方法处理方式）
  ENUMERATE_METHODS          → 枚举所有解法
  SHOW_SOLUTION              → 演示指定方法，完成后自动触发理解验证
  COMPARE_METHODS            → 方法对比
  RETRY_WITH_METHOD          → 返回 retry_signal 给前端
  EXPLAIN_CONCEPT / GENERAL  → 自由对话
"""

import pickle
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.context_governance.signature import context_signature
from agent.infra.logging import get_logger
from agent.knowledge.data_structures import (
    CardRetrieveRequest,
    RetrievalConsumer,
    RetrievalGoal,
)
from agent.tutor.skills import SkillRegistry as TutorSkillRegistry
from agent.tutor.data_structures import ProblemContext

from .context_builder import ReviewContextBuilder
from .data_structures import (
    ErrorSnapshot,
    MethodInfo,
    ReviewAction,
    ReviewSession,
    StrugglePoint,
    UnderstandingCheck,
    UnderstandingQuality,
)
from .skills.registry import ReviewSkillRegistry


class ReviewChatManager:
    _ACTIVE_SESSION_TTL_SECONDS = 6 * 3600
    _CLOSED_SESSION_TTL_SECONDS = 30 * 60
    _MAX_INTERACTIONS = 120
    _DEFAULT_SESSION_STORE_DIR = (
        Path(__file__).resolve().parents[3] / "data" / "sessions" / "review"
    )

    def __init__(
        self,
        registry: TutorSkillRegistry | None,
        api_key: str,
        base_url: str,
        language: str = "zh",
        api_version: str | None = None,
        binding: str = "openai",
        session_store_dir: str | Path | None = None,
        skill_registry: ReviewSkillRegistry | None = None,
    ):
        self.logger = get_logger("ReviewChatManager")
        evaluate_approach_skill = registry.get("evaluate_approach") if registry else None
        self._skill_registry = skill_registry or ReviewSkillRegistry(
            api_key=api_key,
            base_url=base_url,
            language=language,
            api_version=api_version,
            binding=binding,
            evaluate_approach_skill=evaluate_approach_skill,
        )
        self._retrieve_cards = registry.get("retrieve_cards") if registry and registry.has("retrieve_cards") else None
        self._eval_approach = self._skill_registry.get("evaluate_approach")
        self._enumerate_methods = self._skill_registry.get("enumerate_methods")
        self._solve_method = self._skill_registry.get("solve_method")
        self._classify_intent = self._skill_registry.get("classify_intent")
        self._respond_review = self._skill_registry.get("respond_review")
        self._replay_errors = self._skill_registry.get("replay_errors")
        self._ask_understanding = self._skill_registry.get("ask_understanding")
        self._ask_transfer = self._skill_registry.get("ask_transfer")
        self._evaluate_understanding = self._skill_registry.get("evaluate_understanding")
        self._sessions: dict[str, ReviewSession] = {}
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

    def close_session(self, session_id: str) -> dict[str, Any]:
        """
        关闭复盘会话，返回 export 供 MemoryManager 归档。

        调用方拿到返回值后应立即调用：
            episode = MemoryManager.build_episodic_from_review(student_id, export)
            await memory_manager.commit_session(student_id, episode)

        Returns:
            与 export_session() 相同的 dict，额外保证 status 已设为 "closed"
        """
        self._cleanup_sessions()
        session = self.get_session(session_id)
        if session.status == "closed":
            self.logger.warning(f"[{session_id}] close_session called on already-closed session")
        session.status = "closed"
        self._touch_session(session_id)
        self._save_session_snapshot(session)
        self.logger.info(f"[{session_id}] ReviewSession closed")
        return self.export_session(session_id)

    def export_session(self, session_id: str) -> dict[str, Any]:
        session = self.get_session(session_id)
        pc = session.problem_context
        retry_triggered = any(
            h.get("meta", {}).get("type") == "retry_signal"
            for h in session.interaction_history
        )
        retry_method = next(
            (h["meta"].get("method") for h in session.interaction_history
             if h.get("meta", {}).get("type") == "retry_signal"),
            None,
        )
        outcome = self._normalize_outcome(session.status)
        return {
            "session_id": session.session_id,
            "original_tutor_session_id": session.original_tutor_session_id,
            "student_method_used": session.student_method_used,
            "student_id": session.student_id,
            "status": session.status,
            "outcome": outcome,
            "problem_id": getattr(pc, "problem_id", ""),
            "chapter": getattr(pc, "chapter", ""),
            "tags": [kc.card_id for kc in pc.knowledge_cards],
            "methods_explored": session.get_solved_method_names(),
            "retry_triggered": retry_triggered,
            "retry_method": retry_method,
            "understanding_summary": session.get_understanding_summary(),
        }

    def create_session(
        self,
        problem_context: ProblemContext,
        tutor_session_export: dict[str, Any] | None = None,
        student_id: str | None = None,
    ) -> dict[str, Any]:
        """
        创建复盘会话。

        Returns:
            {
              "session_id": str,
              "opener": str | None   # 主动开场白，有则直接展示给学生；None 表示使用默认欢迎语
            }
        """
        self._cleanup_sessions()
        session_id = str(uuid.uuid4())

        student_method = "标准方法"
        if tutor_session_export:
            student_method = (
                tutor_session_export.get("alternative_method")
                or ("替代方法" if tutor_session_export.get("used_alternative_method") else "标准方法")
            )

        session = ReviewSession(
            session_id=session_id,
            problem_context=problem_context,
            original_tutor_session_id=(
                tutor_session_export.get("session_id") if tutor_session_export else None
            ),
            student_method_used=student_method,
            student_id=student_id,
            created_at=datetime.utcnow(),
        )

        if tutor_session_export:
            self._inject_tutor_errors(session, tutor_session_export)

        self._sessions[session_id] = session
        self._touch_session(session_id)
        opener = session.proactive_opener()
        if opener:
            session.add_interaction("tutor", opener, {"type": "proactive_opener"})
        self._save_session_snapshot(session)

        self.logger.info(
            f"[{session_id}] ReviewSession created "
            f"(errors={len(session.error_snapshots)}, "
            f"struggle_points={len(session.struggle_points)}, "
            f"opener={'yes' if opener else 'no'})"
        )
        return {"session_id": session_id, "opener": opener}

    def get_session(self, session_id: str) -> ReviewSession:
        self._cleanup_sessions()
        session = self._sessions.get(session_id)
        if session is None:
            session = self._load_session_snapshot(session_id)
            if session:
                self._sessions[session_id] = session
        if session:
            self._touch_session(session_id)
        if not session:
            raise ValueError(f"ReviewSession not found: {session_id}")
        return session

    # =========================================================================
    # Main Entry
    # =========================================================================

    async def chat(self, session_id: str, student_message: str) -> dict[str, Any]:
        session = self.get_session(session_id)
        if session.status == "closed":
            return {
                "mode": "closed",
                "message": "本次复盘会话已结束，无法继续对话。",
            }
        try:
            if len(session.interaction_history) >= self._MAX_INTERACTIONS:
                return self._close_for_interaction_limit(session)

            session.add_interaction("student", student_message)

            # LLM 统一路由（含验证状态感知）
            action, method_target = await self._classify_action(
                session,
                student_message,
            )

            if action == ReviewAction.ANSWER_VERIFICATION:
                return await self._handle_verification_response(session, student_message)

            if action == ReviewAction.INTERRUPT_VERIFICATION:
                interrupted_method = session.pending_verification
                self._clear_verification_state(session)
                self.logger.info(
                    f"[{session_id}] Verification interrupted by user "
                    f"(method={interrupted_method})"
                )
                action, method_target = await self._classify_action(
                    session,
                    student_message,
                )
                if action == ReviewAction.INTERRUPT_VERIFICATION:
                    action = ReviewAction.GENERAL
                    method_target = None

            if action == ReviewAction.REPLAY_ERRORS:
                return await self._handle_replay_errors(session, method_target)
            if action == ReviewAction.ENUMERATE_METHODS:
                return await self._handle_enumerate(session)
            if action == ReviewAction.SHOW_SOLUTION:
                return await self._handle_show_solution(session, method_target, student_message)
            if action == ReviewAction.COMPARE_METHODS:
                return await self._handle_compare(session, student_message)
            if action == ReviewAction.RETRY_WITH_METHOD:
                return self._handle_retry_signal(session, method_target)
            if action == ReviewAction.EXPLAIN_CONCEPT:
                return await self._handle_explain_concept(session, student_message)

            return await self._handle_general(session, student_message)
        finally:
            self._save_session_snapshot(session)

    # =========================================================================
    # Intent Handlers
    # =========================================================================

    async def _handle_replay_errors(
        self, session: ReviewSession, target_method: str | None
    ) -> dict[str, Any]:
        """错误回放：展示 Tutor 会话中的具体失误，对比新方法的处理方式"""
        if not session.has_errors():
            msg = "你这次做题过程中没有记录到明显错误，看来掌握得不错！想探索其他解法吗？"
            session.add_interaction("tutor", msg, {"type": "replay_no_errors"})
            return {"mode": "replay_errors", "message": msg}

        # 如果没有指定目标方法，用第一个非学生原用方法
        if not target_method:
            if not session.discovered_methods:
                session.discovered_methods = await self._enumerate_methods(session.problem_context)
            target_method = next(
                (m.name for m in session.discovered_methods
                 if m.name != session.student_method_used),
                None,
            )
            # 所有方法都等于学生方法时，回退到第一个可用方法
            if not target_method and session.discovered_methods:
                target_method = session.discovered_methods[0].name

        # 如有 target_method 且尚未演示，先演示
        solved_demo = None
        if target_method and target_method not in session.solved_demonstrations:
            solved_demo = await self._solve_method(session.problem_context, target_method)
            session.solved_demonstrations[target_method] = solved_demo
        elif target_method:
            solved_demo = session.solved_demonstrations[target_method]

        msg = await self._replay_errors(
            problem_context=session.problem_context,
            error_snapshots=session.error_snapshots,
            struggle_points=session.struggle_points,
            student_method_used=session.student_method_used,
            target_method=target_method,
            solved_demo=solved_demo,
            interaction_history=session.get_recent_history(),
        )
        session.add_interaction("tutor", msg, {
            "type": "replay_errors",
            "target_method": target_method,
        })
        return {"mode": "replay_errors", "message": msg}

    async def _handle_enumerate(self, session: ReviewSession) -> dict[str, Any]:
        if not session.discovered_methods:
            session.discovered_methods = await self._enumerate_methods(session.problem_context)

        methods = session.discovered_methods
        if not methods:
            msg = "暂时没有找到这道题的其他解法。"
            session.add_interaction("tutor", msg, {"type": "enumerate_empty"})
            return {"mode": "enumerate", "message": msg, "methods": []}

        lines = [f"这道题共有 **{len(methods)}** 种解法：\n"]
        for i, m in enumerate(methods, 1):
            lines.append(f"{i}. {m.to_display()}")
        lines.append('\n想看哪种方法的完整步骤？或者说「我想用X方法重新做」。')

        msg = "\n\n".join(lines)
        session.add_interaction("tutor", msg, {"type": "enumerate_methods"})
        return {"mode": "enumerate", "message": msg, "methods": [m.to_display() for m in methods]}

    async def _handle_show_solution(
        self, session: ReviewSession, method_name: str | None, student_message: str
    ) -> dict[str, Any]:
        if not method_name:
            await self._ensure_discovered_methods(session)
            method_name = next(
                (m.name for m in session.discovered_methods
                 if m.name != session.student_method_used),
                session.discovered_methods[0].name if session.discovered_methods else "标准方法",
            )

        # 若方法不在已知列表（学生自己提出的），先用 PathEvaluator 验证有效性
        known_names = session.get_known_method_names()
        if method_name not in known_names and method_name not in session.solved_demonstrations:
            path_result = await self._eval_approach(
                problem_context=session.problem_context,
                student_approach=method_name,
                student_work_excerpt="（复盘阶段，学生提出想了解此方法）",
            )
            if not path_result.is_mathematically_valid:
                msg = (
                    f"「{method_name}」在这道题上数学上不成立，无法用来解这道题。\n\n"
                    f"你可以探索其他有效解法——想看看有哪些方法吗？"
                )
                session.add_interaction("tutor", msg, {
                    "type": "invalid_method",
                    "method": method_name,
                })
                return {"mode": "invalid_method", "message": msg}

        # 从缓存取或重新生成
        if method_name not in session.solved_demonstrations:
            solved = await self._solve_method(session.problem_context, method_name)
            session.solved_demonstrations[method_name] = solved
        solved = session.solved_demonstrations[method_name]

        # 生成理解验证问题（同步生成，附在解法展示后面）
        question, key_points = await self._ask_understanding(
            problem_context=session.problem_context,
            solved_method=solved,
            student_method_used=session.student_method_used,
        )

        # 预存验证问题（学生回答前先占位，回答后补全其余字段）
        session.understanding_checks[method_name] = UnderstandingCheck(
            method_name=method_name,
            question_asked=question,
            key_points=key_points,
        )
        session.pending_verification = method_name
        session.pending_verification_stage = "concept"
        session._pending_question = question
        session._pending_key_points = key_points

        msg = solved.to_display() + f"\n\n---\n\n**理解确认**：{question}"
        session.add_interaction("tutor", msg, {
            "type": "show_solution",
            "method": method_name,
            "verification_pending": True,
        })
        return {"mode": "solution", "message": msg, "pending_verification": method_name}

    async def _handle_verification_response(
        self, session: ReviewSession, student_response: str
    ) -> dict[str, Any]:
        method_name = session.pending_verification
        solved_demo = session.solved_demonstrations.get(method_name)
        if not solved_demo:
            self._clear_verification_state(session)
            return await self._handle_general(session, student_response)

        stage = session.pending_verification_stage or "concept"
        check = session.understanding_checks.get(method_name) or UnderstandingCheck(
            method_name=method_name,
            question_asked=f"「{method_name}」的核心是什么？",
            key_points=solved_demo.key_insight,
        )
        session.understanding_checks[method_name] = check

        if stage == "transfer":
            question = (
                session._pending_question
                or check.transfer_question
                or f"如果题目条件变化，{method_name} 哪一步要调整？"
            )
            key_points = session._pending_key_points or check.transfer_key_points or solved_demo.key_insight
        else:
            question = session._pending_question or check.question_asked or f"「{method_name}」的核心是什么？"
            key_points = session._pending_key_points or check.key_points or solved_demo.key_insight

        quality, feedback = await self._evaluate_understanding(
            problem_context=session.problem_context,
            method_name=method_name,
            question=question,
            key_points=key_points,
            student_response=student_response,
            solved_demo=solved_demo,
        )

        if stage == "transfer":
            # 第二阶段：迁移验证完成，输出最终质量
            check.transfer_question = question
            check.transfer_key_points = key_points
            check.transfer_response = student_response
            check.transfer_quality = quality
            check.transfer_feedback = feedback

            final_quality = check.final_quality()
            self._clear_verification_state(session)

            if final_quality == UnderstandingQuality.UNDERSTOOD:
                feedback += "\n\n很好，你已经能把这个方法迁移到相近题型了！想用它重新挑战这道题吗？"
            elif final_quality == UnderstandingQuality.PARTIAL:
                feedback += "\n\n主干思路已经有了，再练一道同类变式会更稳。"
            else:
                feedback += "\n\n核心概念还有点松动，我们先回放关键一步再巩固。"

            session.add_interaction("tutor", feedback, {
                "type": "verification_result",
                "method": method_name,
                "stage": "transfer",
                "quality": final_quality.value,
            })
            self.logger.info(
                f"[{session.session_id}] Understanding check (2-stage): "
                f"{method_name} → concept={check.quality.value}, transfer={quality.value}, "
                f"final={final_quality.value}"
            )
            return {
                "mode": "verification",
                "message": feedback,
                "understanding_quality": final_quality.value,
                "verification_stage": "done",
            }

        # 第一阶段：概念理解验证
        check.question_asked = question
        check.key_points = key_points
        check.student_response = student_response
        check.quality = quality
        check.tutor_feedback = feedback

        if quality == UnderstandingQuality.NOT_UNDERSTOOD:
            self._clear_verification_state(session)
            feedback += "\n\n没关系，我们再看一遍核心步骤。"
            session.add_interaction("tutor", feedback, {
                "type": "verification_result",
                "method": method_name,
                "stage": "concept",
                "quality": quality.value,
            })
            self.logger.info(
                f"[{session.session_id}] Understanding check (concept): "
                f"{method_name} → {quality.value}"
            )
            return {
                "mode": "verification",
                "message": feedback,
                "understanding_quality": quality.value,
                "verification_stage": "done",
            }

        # 概念阶段通过（understood/partial）后进入迁移验证
        transfer_q, transfer_key_points = await self._ask_transfer(
            problem_context=session.problem_context,
            solved_method=solved_demo,
            student_method_used=session.student_method_used,
        )
        check.transfer_question = transfer_q
        check.transfer_key_points = transfer_key_points
        session.pending_verification_stage = "transfer"
        session._pending_question = transfer_q
        session._pending_key_points = transfer_key_points

        msg = feedback + f"\n\n---\n\n**迁移验证**：{transfer_q}"
        session.add_interaction("tutor", msg, {
            "type": "verification_stage_transition",
            "method": method_name,
            "from": "concept",
            "to": "transfer",
            "quality": quality.value,
        })
        self.logger.info(
            f"[{session.session_id}] Understanding check (concept): "
            f"{method_name} → {quality.value}, move_to=transfer"
        )
        return {
            "mode": "verification",
            "message": msg,
            "understanding_quality": quality.value,
            "verification_stage": "transfer",
            "pending_verification": method_name,
        }

    async def _handle_compare(
        self, session: ReviewSession, student_message: str
    ) -> dict[str, Any]:
        if len(session.solved_demonstrations) < 2:
            await self._ensure_discovered_methods(session)
            for m in [session.student_method_used] + session.get_known_method_names():
                if m not in session.solved_demonstrations and len(session.solved_demonstrations) < 2:
                    solved = await self._solve_method(session.problem_context, m)
                    session.solved_demonstrations[m] = solved

        # 最多比较 2 个方法，每个只保留 key_insight + step_count + comparison_note
        compare_items = list(session.solved_demonstrations.items())[:2]
        context_parts = [
            f"【{n}】核心：{s.key_insight}　步骤数：{len(s.steps)}　特点：{s.comparison_note}"
            for n, s in compare_items
        ]
        method_pair = "+".join(n for n, _ in compare_items)
        sig = context_signature({
            "task": "compare_methods",
            "question_id": session.problem_context.problem_id,
            "method_pair": method_pair,
            "student_method": session.student_method_used,
        })
        self.logger.debug(f"[{session.session_id}] compare sig={sig}")

        response = await self._respond_review(
            problem_context=session.problem_context,
            student_message=student_message,
            context_str="各解法对比：\n" + "\n".join(context_parts),
            interaction_history=session.get_recent_history(),
        )
        session.add_interaction("tutor", response, {"type": "compare_methods"})
        return {"mode": "compare", "message": response}

    def _handle_retry_signal(
        self, session: ReviewSession, method_name: str | None
    ) -> dict[str, Any]:
        if not method_name and session.discovered_methods:
            method_name = next(
                (m.name for m in session.discovered_methods
                 if m.name != session.student_method_used), None,
            )
        msg = (
            f"好的！我们用**{method_name}**重新挑战这道题。系统将为你开启新的辅导会话。"
            if method_name else "好的！告诉我你想用哪种方法？"
        )
        session.add_interaction("tutor", msg, {"type": "retry_signal", "method": method_name})
        return {"mode": "retry_signal", "message": msg, "retry_method": method_name}

    async def _handle_explain_concept(
        self, session: ReviewSession, student_message: str
    ) -> dict[str, Any]:
        """解释概念/原理：优先用 CardRetriever 检索补充卡，兜底用题目预挂载卡片"""
        pc = session.problem_context
        card_context_parts: list[str] = []

        # 尝试 RAG 检索补充卡片
        rag_card_ids: list[str] = []
        if self._retrieve_cards:
            try:
                request = CardRetrieveRequest(
                    consumer=RetrievalConsumer.REVIEW.value,
                    question_id=pc.problem_id,
                    active_solution_id=None,
                    chapter=pc.chapter,
                    topic=None,
                    problem_text=pc.problem,
                    student_work=None,
                    student_approach=None,
                    target_card_ids=[c.card_id for c in pc.knowledge_cards] if pc.knowledge_cards else [],
                    focus_terms=[student_message[:80]],
                    retrieval_goal=RetrievalGoal.CONCEPT_EXPLAIN.value,
                    session_id=session.session_id,
                    top_k=2,
                )
                bundle = await self._retrieve_cards(request)
                for rc in bundle.result.supplementary_cards[:2]:
                    c = rc.card
                    method = c.general_methods[0] if c.general_methods else "（无）"
                    hint = c.hints.get(1, c.hints.get(2, "（无）")) if c.hints else "（无）"
                    mistake = c.common_mistakes[0] if c.common_mistakes else "（无）"
                    card_context_parts.append(
                        f"【{c.title}】通性通法：{method}　提示：{hint}　易错：{mistake}"
                    )
                    rag_card_ids.append(c.card_id)
            except Exception as exc:
                self.logger.warning(f"[{session.session_id}] RAG explain_concept failed: {exc}")

        # 兜底：题目预挂载卡片（排除已从 RAG 获取的）
        if len(card_context_parts) < 2:
            remaining = 2 - len(card_context_parts)
            for kc in pc.knowledge_cards[:remaining + 1]:
                if kc.card_id in rag_card_ids:
                    continue
                if len(card_context_parts) >= 2:
                    break
                method = kc.general_methods[0] if kc.general_methods else "（无）"
                hint = kc.hints[0] if kc.hints else "（无）"
                mistake = kc.common_mistakes[0] if kc.common_mistakes else "（无）"
                card_context_parts.append(
                    f"【{kc.title}】通性通法：{method}　提示：{hint}　易错：{mistake}"
                )

        context_str = (
            "相关知识卡片：\n" + "\n".join(card_context_parts)
            if card_context_parts else "（无知识卡片）"
        )

        all_card_ids = rag_card_ids + [kc.card_id for kc in pc.knowledge_cards[:2] if kc.card_id not in rag_card_ids]
        sig = context_signature({
            "task": "explain_concept",
            "question_id": pc.problem_id,
            "target_cards": "+".join(all_card_ids[:2]),
        })
        self.logger.debug(f"[{session.session_id}] explain sig={sig}")

        response = await self._respond_review(
            problem_context=pc,
            student_message=student_message,
            context_str=context_str,
            interaction_history=session.get_recent_history(),
        )
        session.add_interaction("tutor", response, {"type": "explain_concept"})
        return {"mode": "explain_concept", "message": response}

    async def _handle_general(
        self, session: ReviewSession, student_message: str
    ) -> dict[str, Any]:
        known = session.get_known_method_names()
        context_str = f"已知解法：{', '.join(known)}" if known else "尚未枚举解法"
        response = await self._respond_review(
            problem_context=session.problem_context,
            student_message=student_message,
            context_str=context_str,
            interaction_history=session.get_recent_history(),
        )
        session.add_interaction("tutor", response, {"type": "general"})
        return {"mode": "general", "message": response}

    # =========================================================================
    # Internal
    # =========================================================================

    async def _classify_action(
        self,
        session: ReviewSession,
        student_message: str,
    ) -> tuple[ReviewAction, str | None]:
        context_text = ReviewContextBuilder.build(session)
        action, method_target = await self._classify_intent(
            problem_context=session.problem_context,
            student_message=student_message,
            known_methods=session.discovered_methods,
            interaction_history=session.get_recent_history(),
            session_context=context_text,
        )
        self.logger.info(
            f"[{session.session_id}] Action={action.value}, target={method_target}"
        )
        return action, method_target

    async def _ensure_discovered_methods(self, session: ReviewSession) -> None:
        if session.discovered_methods:
            return
        session.discovered_methods = await self._enumerate_methods(
            session.problem_context
        )

    @staticmethod
    def _close_for_interaction_limit(session: ReviewSession) -> dict[str, Any]:
        session.status = "closed"
        msg = "本次复盘对话已达到轮次上限，建议先关闭会话。"
        session.add_interaction(
            "system",
            f"[系统] {msg}",
            {"type": "auto_closed_limit"},
        )
        return {"mode": "closed", "message": msg}

    def _touch_session(self, session_id: str) -> None:
        self._session_last_access[session_id] = time.time()

    def _cleanup_sessions(self) -> None:
        now_ts = time.time()

        # 1) 清理内存中的过期会话
        for session_id, session in list(self._sessions.items()):
            last_access = self._session_last_access.get(
                session_id,
                session.created_at.timestamp(),
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

        # 2) 清理磁盘上的已关闭过期快照（进程重启后回收）
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

    def _save_session_snapshot(self, session: ReviewSession) -> None:
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
                f"[{session.session_id}] Failed to save ReviewSession snapshot: {e}"
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
    ) -> ReviewSession | None:
        path = self._session_snapshot_path(session_id)
        if not path.exists():
            return None
        try:
            with open(path, "rb") as f:
                session = pickle.load(f)
            if not isinstance(session, ReviewSession):
                self.logger.warning(f"[{session_id}] Invalid ReviewSession snapshot type")
                return None
            if touch:
                self._touch_session(session_id)
            return session
        except Exception as e:
            self.logger.warning(f"[{session_id}] Failed to load ReviewSession snapshot: {e}")
            return None

    def _delete_session_snapshot(self, session_id: str) -> None:
        path = self._session_snapshot_path(session_id)
        try:
            if path.exists():
                path.unlink()
        except OSError as e:
            self.logger.warning(f"[{session_id}] Failed to delete ReviewSession snapshot: {e}")

    @staticmethod
    def _normalize_outcome(status: str) -> str:
        value = (status or "").strip().lower()
        if value == "closed":
            return "explored"
        if value == "active":
            return "in_progress"
        return "explored"

    @staticmethod
    def _clear_verification_state(session: ReviewSession) -> None:
        session.pending_verification = None
        session.pending_verification_stage = "concept"
        session._pending_question = ""
        session._pending_key_points = ""

    @staticmethod
    def _inject_tutor_errors(session: ReviewSession, export: dict[str, Any]) -> None:
        """从 TutorManager.export_session() 提取错误快照和挣扎点"""
        ed = export.get("error_details")
        if ed and ed.get("error_type"):
            session.error_snapshots.append(ErrorSnapshot(
                error_type=ed["error_type"],
                error_location=ed.get("error_location") or "（位置未记录）",
                error_description=ed.get("error_description") or "",
                correction_note=ed.get("correction_note") or "",
            ))

        for sp_data in export.get("struggle_checkpoints", []):
            session.struggle_points.append(StrugglePoint(
                checkpoint_index=sp_data["index"],
                description=sp_data["description"],
                guiding_question=sp_data.get("guiding_question", ""),
                hint_level_reached=sp_data["hint_level_reached"],
                was_passed=sp_data.get("passed", False),
            ))
