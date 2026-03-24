#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ProgressManager - 进度管理调度器

对外三个入口：
  get_daily_plan()     — 生成今日任务计划（Ebbinghaus + LLM）
  get_summary()        — 生成学习进展报告（LLM）
  update_after_session() — 会话结束后更新衰减记录（写回 SemanticMemory）

与其他模块的关系：
  - 依赖 MemoryManager 读写 SemanticMemory / EpisodicMemory
  - 不持有 TutorManager / ReviewChatManager 实例（通过 session_export dict 通信）
  - RecommendManager 是平级关系，各自独立；Progress 侧重"今天做什么"，
    Recommend 侧重"刚做完这道题做什么"
"""

from datetime import datetime, timedelta
from typing import Any

from agent.infra.logging import get_logger
from agent.memory.memory_manager import MemoryManager
from agent.memory.data_structures import SemanticMemory

from .data_structures import DailyTask, ProgressSummary, TaskPlan, TaskPriority
from .ebbinghaus import rank_concepts_by_urgency
from .skills.registry import ProgressSkillRegistry


class ProgressManager:

    def __init__(
        self,
        memory_manager: MemoryManager,
        api_key: str,
        base_url: str,
        language: str = "zh",
        api_version: str | None = None,
        binding: str = "openai",
        skill_registry: ProgressSkillRegistry | None = None,
    ):
        self.logger = get_logger("ProgressManager")
        self._memory = memory_manager
        self._skill_registry = skill_registry or ProgressSkillRegistry(
            api_key=api_key,
            base_url=base_url,
            language=language,
            api_version=api_version,
            binding=binding,
        )
        self._plan_tasks = self._skill_registry.get("plan_tasks")
        self._summarize_progress = self._skill_registry.get("summarize_progress")

    # =========================================================================
    # 今日任务计划
    # =========================================================================

    async def get_daily_plan(
        self,
        student_id: str,
        max_tasks: int = 5,
        max_minutes: int = 60,
        episode_window: int = 20,
    ) -> TaskPlan:
        """
        生成今日个性化学习任务计划。

        流程：
          1. 读取 SemanticMemory（长期）+ 近期 EpisodicMemory（中期）
          2. Ebbinghaus 衰减计算 → 需要今日复习的知识点列表
          3. TaskPlannerAgent（LLM）综合两者 → 任务列表 + 计划概述
        """
        semantic = self._memory.get_semantic(student_id)
        recent = self._memory.get_recent_episodes(student_id, limit=episode_window)

        if not semantic:
            self.logger.info(f"[{student_id}] No semantic memory yet, returning empty plan")
            return TaskPlan(
                student_id=student_id,
                generated_at=datetime.utcnow(),
                tasks=[],
                plan_summary="暂时没有足够的学习记录来生成个性化计划，先做一道感兴趣的题吧！",
            )

        # Ebbinghaus：找出今日需要复习的知识点
        decay_due = rank_concepts_by_urgency(semantic.concept_mastery)
        self.logger.info(
            f"[{student_id}] Decay due: {len(decay_due)} concepts need review"
        )

        # LLM 规划
        tasks, plan_summary = await self._plan_tasks(
            semantic=semantic,
            decay_due=decay_due,
            recent_episodes=recent,
            max_tasks=max_tasks,
            max_minutes=max_minutes,
        )

        total_minutes = sum(t.estimated_minutes for t in tasks)
        plan = TaskPlan(
            student_id=student_id,
            generated_at=datetime.utcnow(),
            tasks=tasks,
            plan_summary=plan_summary,
            estimated_total_minutes=total_minutes,
        )
        self.logger.info(
            f"[{student_id}] Daily plan: {len(tasks)} tasks, "
            f"~{total_minutes} min, urgent={len(plan.urgent_tasks())}"
        )
        return plan

    # =========================================================================
    # 进展报告
    # =========================================================================

    async def get_summary(
        self,
        student_id: str,
        period: str = "week",
        episode_limit: int = 30,
    ) -> ProgressSummary:
        """
        生成学习进展报告。

        Args:
            period: "week" | "month" | "all_time"
            episode_limit: 最多读取多少条情节记忆
        """
        semantic = self._memory.get_semantic(student_id)
        episodes = self._memory.get_recent_episodes(student_id, limit=episode_limit)

        # 按 period 过滤时间窗
        episodes = self._filter_episodes_by_period(episodes, period)

        if not semantic and not episodes:
            return ProgressSummary(
                student_id=student_id,
                generated_at=datetime.utcnow(),
                period=period,
                narrative="还没有足够的学习记录，开始做题后这里会显示你的进展。",
            )

        return await self._summarize_progress(
            student_id=student_id,
            semantic=semantic or SemanticMemory.new(student_id),
            episodes=episodes,
            period=period,
        )

    @staticmethod
    def _filter_episodes_by_period(
        episodes: list,
        period: str,
    ) -> list:
        """按 period 时间窗过滤 episodes，all_time 不做过滤。"""
        if period == "all_time" or not episodes:
            return episodes
        now = datetime.utcnow()
        if period == "week":
            cutoff = now - timedelta(days=7)
        elif period == "month":
            cutoff = now - timedelta(days=30)
        else:
            return episodes
        return [ep for ep in episodes if ep.created_at >= cutoff]

    # =========================================================================
    # 会话后更新（将 next_review_at 写回 SemanticMemory）
    # =========================================================================

    def update_next_review_dates(self, student_id: str) -> int:
        """
        重新计算所有知识点的下次复习时间并写回 SemanticMemory。

        应在 MemoryManager.commit_session() 之后调用，确保掌握度已更新。

        Returns:
            更新的知识点数量
        """
        semantic = self._memory.get_semantic(student_id)
        if not semantic:
            return 0

        from .ebbinghaus import next_review_date
        updated = 0
        for rec in semantic.concept_mastery.values():
            prev = rec.next_review_at
            rec.next_review_at = next_review_date(rec)
            if prev != rec.next_review_at:
                updated += 1

        # 写回（next_review_at 已持久化在 MasteryRecord 中）
        semantic.last_updated = datetime.utcnow()
        self._memory.store.save_semantic(semantic)

        self.logger.debug(
            f"[{student_id}] Recomputed next_review_at for {updated}/"
            f"{len(semantic.concept_mastery)} concepts"
        )
        return updated

    # =========================================================================
    # 便捷查询
    # =========================================================================

    def get_overdue_concepts(self, student_id: str) -> list[dict]:
        """
        返回今日逾期（保留率 < 60%）的知识点列表，供前端展示提醒。

        Returns:
            list of {"concept_id": str, "retention": float, "priority": str}
        """
        semantic = self._memory.get_semantic(student_id)
        if not semantic:
            return []
        decay_due = rank_concepts_by_urgency(semantic.concept_mastery)
        return [
            {
                "concept_id": cid,
                "retention": round(dr.retention, 3),
                "elapsed_days": round(dr.elapsed_days, 1),
                "priority": "urgent" if dr.retention < 0.4 else "important",
            }
            for cid, dr in decay_due
        ]
