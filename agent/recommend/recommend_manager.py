#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
RecommendManager - 推荐调度器

对外两个入口：
  recommend_after_tutor()   — Tutor 会话结束后推荐
  recommend_after_review()  — Review 会话结束后推荐

内部流程：
  1. 构建 RecommendContext（会话结果 + 长期记忆 + 近期情节）
  2. RecommendAgent.decide() → 推荐类型 + 查询条件 + 说明文字
  3. 按类型查题库（ProblemBankBase），题库为空时降级
  4. 返回 Recommendation

题库接口（ProblemBankBase）由外部注入，NullProblemBank 为默认占位实现。
"""

from typing import Any, Callable

from agent.infra.logging import get_logger
from agent.knowledge.data_structures import (
    CardRetrieveRequest,
    RetrievalConsumer,
    RetrievalGoal,
)
from agent.memory.memory_manager import MemoryManager

from .data_structures import (
    ProblemQuery,
    Recommendation,
    RecommendContext,
    RecommendSource,
    RecommendationType,
)
from .problem_bank import NullProblemBank, ProblemBankBase
from .skills.registry import RecommendSkillRegistry


class RecommendManager:
    """
    推荐管理器。

    与 TutorManager / ReviewChatManager 通过 session export dict 通信，
    无需持有对方的实例，保持模块解耦。
    """

    def __init__(
        self,
        memory_manager: MemoryManager,
        api_key: str,
        base_url: str,
        language: str = "zh",
        api_version: str | None = None,
        binding: str = "openai",
        problem_bank: ProblemBankBase | None = None,
        skill_registry: RecommendSkillRegistry | None = None,
        retrieve_cards: Callable | None = None,
    ):
        self.logger = get_logger("RecommendManager")
        self._memory = memory_manager
        self._bank = problem_bank or NullProblemBank()
        self._retrieve_cards = retrieve_cards
        self._skill_registry = skill_registry or RecommendSkillRegistry(
            api_key=api_key,
            base_url=base_url,
            language=language,
            api_version=api_version,
            binding=binding,
        )
        self._decide_recommendation = self._skill_registry.get("decide_recommendation")

    # =========================================================================
    # 公开入口
    # =========================================================================

    async def recommend_after_tutor(
        self,
        student_id: str,
        session_export: dict[str, Any],
        working_memory_limit: int = 10,
    ) -> Recommendation:
        """
        Tutor 会话结束后推荐下一步。

        Args:
            session_export: TutorManager.export_session() 的返回值
            working_memory_limit: 加载最近多少条情节记忆作为中期上下文
        """
        ctx = self._build_context(
            student_id=student_id,
            source=RecommendSource.TUTOR,
            session_export=session_export,
            limit=working_memory_limit,
        )
        return await self._recommend(ctx)

    async def recommend_after_review(
        self,
        student_id: str,
        session_export: dict[str, Any],
        working_memory_limit: int = 10,
    ) -> Recommendation:
        """
        Review 会话结束（close_session）后推荐下一步。

        Args:
            session_export: ReviewChatManager.close_session() 的返回值
        """
        ctx = self._build_context(
            student_id=student_id,
            source=RecommendSource.REVIEW,
            session_export=session_export,
            limit=working_memory_limit,
        )
        return await self._recommend(ctx)

    # =========================================================================
    # 内部：构建上下文
    # =========================================================================

    def _build_context(
        self,
        student_id: str,
        source: RecommendSource,
        session_export: dict[str, Any],
        limit: int,
    ) -> RecommendContext:
        semantic = self._memory.get_semantic(student_id)
        recent = self._memory.get_recent_episodes(student_id, limit=limit)
        return RecommendContext(
            student_id=student_id,
            source=source,
            session_export=session_export,
            semantic_memory=semantic,
            recent_episodes=recent,
            current_problem_id=session_export.get("problem_id", ""),
            current_tags=session_export.get("tags", []),
            current_chapter=session_export.get("chapter", ""),
        )

    # =========================================================================
    # 内部：核心推荐逻辑
    # =========================================================================

    async def _recommend(self, ctx: RecommendContext) -> Recommendation:
        # 1. LLM 决策
        decision = await self._decide_recommendation(ctx)
        rec_type: RecommendationType = decision["recommendation_type"]

        self.logger.info(
            f"[{ctx.student_id}] Recommend decision: {rec_type.value} "
            f"(source={ctx.source.value})"
        )

        # 2. 不需要查题库的类型直接返回
        if rec_type == RecommendationType.REST:
            return Recommendation(
                type=rec_type,
                explanation=decision["explanation"],
            )

        if rec_type == RecommendationType.RETRY_WITH_METHOD:
            return Recommendation(
                type=rec_type,
                explanation=decision["explanation"],
                retry_method=decision.get("retry_method"),
            )

        if rec_type == RecommendationType.REVIEW_CONCEPT:
            concept = decision.get("concept_to_review")
            summaries = await self._fetch_concept_cards(ctx, concept)
            return Recommendation(
                type=rec_type,
                explanation=decision["explanation"],
                concept_to_review=concept,
                concept_card_summaries=summaries,
            )

        # 3. 需要题库的类型：构建查询
        query = self._build_query(decision, ctx)
        problems = await self._bank.query(query)

        if problems:
            return Recommendation(
                type=rec_type,
                explanation=decision["explanation"],
                problem=problems[0],
                query_used=query,
            )

        # 4. 题库为空：尝试降级
        return await self._fallback(ctx, decision, query)

    def _build_query(
        self, decision: dict, ctx: RecommendContext
    ) -> ProblemQuery:
        rec_type: RecommendationType = decision["recommendation_type"]
        target_tags: list[str] = decision.get("target_tags") or ctx.current_tags
        difficulty: str | None = decision.get("target_difficulty")

        # 根据推荐类型自动调整难度
        if difficulty is None:
            if rec_type == RecommendationType.EASIER_PROBLEM:
                difficulty = "基础"
            elif rec_type == RecommendationType.HARDER_PROBLEM:
                difficulty = "进阶"

        return ProblemQuery(
            tags=target_tags,
            tag_mode="any",
            chapter=ctx.current_chapter or None,
            difficulty=difficulty,
            exclude_ids=ctx.get_done_problem_ids(),
            limit=3,
        )

    async def _fallback(
        self,
        ctx: RecommendContext,
        decision: dict,
        original_query: ProblemQuery,
    ) -> Recommendation:
        """
        题库返回空时的降级策略：
          1. 放宽条件重试（去掉 difficulty 限制）
          2. 仍为空 → 降级为 REVIEW_CONCEPT（找语义记忆中的薄弱点）
          3. 无薄弱点 → REST
        """
        # 降级 1：放宽难度
        relaxed_query = ProblemQuery(
            tags=original_query.tags,
            tag_mode="any",
            exclude_ids=original_query.exclude_ids,
            limit=3,
        )
        problems = await self._bank.query(relaxed_query)
        if problems:
            self.logger.info(f"[{ctx.student_id}] Fallback: relaxed query found {len(problems)} problems")
            return Recommendation(
                type=decision["recommendation_type"],
                explanation=decision["explanation"],
                problem=problems[0],
                query_used=relaxed_query,
                fallback_used=True,
            )

        # 降级 2：推荐复习知识点
        weak = ctx.get_weak_tags()
        if weak:
            self.logger.info(f"[{ctx.student_id}] Fallback: recommend review_concept {weak[0]}")
            return Recommendation(
                type=RecommendationType.REVIEW_CONCEPT,
                explanation=f"暂时没有找到合适的新题，先复习一下「{weak[0]}」这个知识点吧。",
                concept_to_review=weak[0],
                fallback_used=True,
            )

        # 降级 3：建议休息
        self.logger.info(f"[{ctx.student_id}] Fallback: recommend rest")
        return Recommendation(
            type=RecommendationType.REST,
            explanation="今天做了不少，先休息一下！",
            fallback_used=True,
        )

    async def _fetch_concept_cards(
        self, ctx: RecommendContext, concept: str | None,
    ) -> list[str]:
        """为 REVIEW_CONCEPT 推荐检索相关知识卡摘要。"""
        if not self._retrieve_cards or not concept:
            return []
        try:
            request = CardRetrieveRequest(
                consumer=RetrievalConsumer.RECOMMEND.value,
                question_id=ctx.current_problem_id or None,
                active_solution_id=None,
                chapter=ctx.current_chapter,
                topic=None,
                problem_text=None,
                student_work=None,
                student_approach=None,
                target_card_ids=[],
                focus_terms=[concept],
                retrieval_goal=RetrievalGoal.REINFORCEMENT.value,
                top_k=2,
            )
            bundle = await self._retrieve_cards(request)
            summaries: list[str] = []
            for rc in bundle.result.supplementary_cards[:2]:
                c = rc.card
                method = c.general_methods[0] if c.general_methods else ""
                summaries.append(f"【{c.title}】{method}" if method else f"【{c.title}】{c.summary[:60]}")
            return summaries
        except Exception as exc:
            self.logger.warning(f"[{ctx.student_id}] RAG fetch for recommend failed: {exc}")
            return []
