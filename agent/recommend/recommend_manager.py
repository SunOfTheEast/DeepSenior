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

import re
from typing import Any, Callable

from agent.infra.logging import get_logger
from agent.knowledge import build_card_retriever
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
from .recommendation_store import RecommendationStore
from .skills.registry import RecommendSkillRegistry
from .tools.tool_registry import RecommendToolRegistry


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
        self._owned_card_retriever = None
        if retrieve_cards:
            self._retrieve_cards = retrieve_cards
        else:
            enable_llm_agents = bool(api_key and base_url and api_key != "mock" and base_url != "mock")
            self._owned_card_retriever = build_card_retriever(
                api_key=api_key,
                base_url=base_url,
                language=language,
                api_version=api_version,
                binding=binding,
                enable_llm_agents=enable_llm_agents,
            )
            self._retrieve_cards = self._owned_card_retriever.retrieve
        self._skill_registry = skill_registry or RecommendSkillRegistry(
            api_key=api_key,
            base_url=base_url,
            language=language,
            api_version=api_version,
            binding=binding,
        )
        self._decide_recommendation = self._skill_registry.get("decide_recommendation")
        self._recommendation_store = RecommendationStore()
        self._recommend_agent = self._skill_registry._agent  # direct access for tool-use

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
        # 1. 构建 tool registry
        tool_registry = RecommendToolRegistry(
            student_id=ctx.student_id,
            memory_manager=self._memory,
            problem_bank=self._bank,
            current_problem_id=ctx.current_problem_id,
            current_tags=ctx.current_tags,
            current_chapter=ctx.current_chapter,
        )

        # 2. LLM tool-use 决策
        decision = await self._recommend_agent.decide_with_tools(
            ctx, tool_registry=tool_registry,
        )
        rec_type: RecommendationType = decision["recommendation_type"]

        self.logger.info(
            f"[{ctx.student_id}] Recommend decision: {rec_type.value} "
            f"(source={ctx.source.value})"
        )

        # 3. 不需要查题库的类型直接返回
        if rec_type == RecommendationType.REST:
            result = Recommendation(type=rec_type, explanation=decision["explanation"])
            self._save_recommendation(ctx.student_id, result)
            return result

        if rec_type == RecommendationType.RETRY_WITH_METHOD:
            result = Recommendation(
                type=rec_type,
                explanation=decision["explanation"],
                retry_method=decision.get("retry_method"),
            )
            self._save_recommendation(ctx.student_id, result)
            return result

        if rec_type == RecommendationType.REVIEW_CONCEPT:
            concept = decision.get("concept_to_review")
            summaries = await self._fetch_concept_cards(ctx, concept)
            result = Recommendation(
                type=rec_type,
                explanation=decision["explanation"],
                concept_to_review=concept,
                concept_card_summaries=summaries,
            )
            self._save_recommendation(ctx.student_id, result)
            return result

        # 4. LLM 可能直接通过 tool 找到了推荐题目
        llm_problems = decision.get("recommended_problems", [])
        if llm_problems:
            # 规则排序
            scored = await self._score_llm_candidates(ctx, llm_problems)
            if scored:
                best = scored[0]
                problem = await self._bank.get_by_id(best["problem_id"])
                if problem:
                    result = Recommendation(
                        type=rec_type,
                        explanation=decision["explanation"],
                        problem=problem,
                    )
                    self._save_recommendation(ctx.student_id, result)
                    return result

        # 5. 回退到传统查询
        query = self._build_query(decision, ctx)
        problems = await self._bank.query(query)

        if problems:
            result = Recommendation(
                type=rec_type,
                explanation=decision["explanation"],
                problem=problems[0],
                query_used=query,
            )
            self._save_recommendation(ctx.student_id, result)
            return result

        # 6. 题库为空：降级
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
            summaries = await self._fetch_concept_cards(ctx, weak[0])
            return Recommendation(
                type=RecommendationType.REVIEW_CONCEPT,
                explanation=f"暂时没有找到合适的新题，先复习一下「{weak[0]}」这个知识点吧。",
                concept_to_review=weak[0],
                concept_card_summaries=summaries,
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
            focus_terms = self._build_recommend_focus_terms(ctx, concept)
            request = CardRetrieveRequest(
                consumer=RetrievalConsumer.RECOMMEND.value,
                question_id=ctx.current_problem_id or None,
                active_solution_id=ctx.session_export.get("solution_id"),
                chapter=ctx.current_chapter,
                topic=None,
                problem_text=None,
                student_work=None,
                student_approach=ctx.session_export.get("alternative_method") or ctx.session_export.get("retry_method"),
                target_card_ids=list(ctx.current_tags),
                focus_terms=focus_terms,
                retrieval_goal=RetrievalGoal.REINFORCEMENT.value,
                session_id=ctx.session_export.get("session_id"),
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

    @staticmethod
    def _build_recommend_focus_terms(
        ctx: RecommendContext,
        concept: str,
        *,
        max_terms: int = 5,
    ) -> list[str]:
        raw_terms = [
            concept,
            ctx.session_export.get("alternative_method") or "",
            ctx.session_export.get("retry_method") or "",
            *ctx.current_tags[:2],
        ]
        terms: list[str] = []
        seen: set[str] = set()
        for text in raw_terms:
            for chunk in re.split(r"[\s,，。！？；：:、（）()\n]+", str(text or "").strip()):
                cleaned = chunk.strip()
                if len(cleaned) < 2:
                    continue
                normalized = cleaned[:48]
                if normalized in seen:
                    continue
                seen.add(normalized)
                terms.append(normalized)
                if len(terms) >= max_terms:
                    return terms
        return [concept[:48]]

    # =========================================================================
    # 推荐记录持久化
    # =========================================================================

    def _save_recommendation(self, student_id: str, rec: Recommendation) -> None:
        try:
            self._recommendation_store.save_recommendation(student_id, {
                "type": rec.type.value if rec.type else "",
                "problem_id": rec.problem.problem_id if rec.problem else "",
                "explanation": rec.explanation,
                "recommended_problems": getattr(rec, "recommended_problems", []),
            })
        except Exception as exc:
            self.logger.warning(f"[{student_id}] Failed to save recommendation: {exc}")

    def get_recent_recommendations(
        self, student_id: str, limit: int = 10,
    ) -> list[dict[str, Any]]:
        return self._recommendation_store.get_recent_recommendations(student_id, limit)

    # =========================================================================
    # 候选题排序
    # =========================================================================

    async def _score_llm_candidates(
        self,
        ctx: RecommendContext,
        candidates: list[dict],
    ) -> list[dict]:
        """
        对 LLM 推荐的候选题做规则打分排序。

        打分维度：
          - 命中 bottleneck concept → +2.0
          - 命中 ready_to_learn → +1.5
          - 涉及 persistent_error → +1.0
          - 最近推荐过 → -3.0
        """
        mastery_view = self._memory.get_mastery_view(ctx.student_id)
        recently_recommended = self._recommendation_store.get_recently_recommended_problem_ids(
            ctx.student_id, limit=20,
        )

        bottleneck_ids = set(mastery_view.bottlenecks()) if mastery_view else set()
        ready_ids = set(mastery_view.ready_to_learn()) if mastery_view else set()
        error_types = set()
        if ctx.semantic_memory:
            error_types = set(ctx.semantic_memory.persistent_errors.keys())

        scored: list[tuple[float, dict]] = []
        for candidate in candidates:
            pid = candidate.get("problem_id", "")
            if not pid:
                continue
            score = 0.0
            problem = await self._bank.get_by_id(pid)
            if problem is None:
                continue
            # Bottleneck bonus
            for tag in problem.tags:
                if tag in bottleneck_ids:
                    score += 2.0
            # Ready-to-learn bonus
            for tag in problem.tags:
                if tag in ready_ids:
                    score += 1.5
            # Recently recommended penalty
            if pid in recently_recommended:
                score -= 3.0
            scored.append((score, candidate))

        scored.sort(key=lambda x: -x[0])
        return [c for _, c in scored]
