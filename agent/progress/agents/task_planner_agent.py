#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
TaskPlannerAgent - 今日任务计划生成

输入：
  - SemanticMemory（长期画像：偏好、薄弱点、方法习惯）
  - Ebbinghaus 待复习列表（DecayRecord 列表）
  - 近期 EpisodicMemory（短期上下文：最近做了什么、疲劳度）

输出：
  - tasks: list of task dicts（由 ProgressManager 转换为 DailyTask）
  - plan_summary: 今日计划概述

LLM 的核心价值：
  不只是"哪个分低就补哪个"的规则推荐，
  而是结合学习模式分析（如连续多天做同类题产生审美疲劳、
  偏好代数但几何严重退步、近期做题量大需要缩减强度等）
  给出更有教学洞察力的安排。
"""

import json
import re

from agent.base_agent import BaseAgent
from agent.context_governance.assembler import assemble
from agent.context_governance.budget_policy import PROGRESS_POLICY
from agent.memory.data_structures import EpisodicMemory, SemanticMemory
from ..data_structures import DecayRecord, DailyTask, TaskPriority, TaskType
from agent.recommend.data_structures import ProblemQuery


class TaskPlannerAgent(BaseAgent):

    def __init__(self, api_key, base_url, language="zh", api_version=None, binding="openai"):
        super().__init__(
            module_name="progress",
            agent_name="task_planner_agent",
            api_key=api_key, base_url=base_url,
            api_version=api_version, language=language, binding=binding,
        )

    async def process(
        self,
        semantic: SemanticMemory,
        decay_due: list[tuple[str, DecayRecord]],
        recent_episodes: list[EpisodicMemory],
        max_tasks: int = 5,
        max_minutes: int = 60,
    ) -> tuple[list[DailyTask], str]:
        """BaseAgent 兼容入口，路由到 plan。"""
        return await self.plan(
            semantic=semantic,
            decay_due=decay_due,
            recent_episodes=recent_episodes,
            max_tasks=max_tasks,
            max_minutes=max_minutes,
        )

    async def plan(
        self,
        semantic: SemanticMemory,
        decay_due: list[tuple[str, DecayRecord]],
        recent_episodes: list[EpisodicMemory],
        max_tasks: int = 5,
        max_minutes: int = 60,
    ) -> tuple[list[DailyTask], str]:
        """
        生成今日任务列表。

        Args:
            decay_due: Ebbinghaus 计算出的需要复习的知识点列表（已按紧迫度排序）
            max_tasks: 最多生成几条任务
            max_minutes: 今日学习时间上限（分钟）

        Returns:
            (tasks, plan_summary)
        """
        system_prompt = self.get_prompt("system")
        template = self.get_prompt("plan")

        if not system_prompt or not template:
            return self._rule_based_plan(
                semantic,
                decay_due,
                recent_episodes,
                max_tasks=max_tasks,
                max_minutes=max_minutes,
            )

        long_term_profile = semantic.to_progress_snapshot()
        concept_mastery = self._fmt_mastery(semantic)
        decay_text = self._fmt_decay(decay_due)
        recent_text = self._fmt_episodes(recent_episodes)

        assembly = assemble(
            {
                "long_term_profile": long_term_profile,
                "concept_mastery": concept_mastery,
                "decay_due": decay_text,
                "recent_episodes": recent_text,
            },
            PROGRESS_POLICY,
            sig_parts={
                "task": "task_planner",
                "student_id": semantic.student_id,
            },
        )
        ap = assembly.payload

        user_prompt = template.format(
            long_term_profile=ap.get("long_term_profile", long_term_profile),
            concept_mastery=ap.get("concept_mastery", concept_mastery),
            decay_due=ap.get("decay_due", decay_text),
            recent_episodes=ap.get("recent_episodes", recent_text),
            recent_load=self._estimate_load(recent_episodes),
            max_tasks=max_tasks,
            max_minutes=max_minutes,
        )

        response = await self.call_llm(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            response_format={"type": "json_object"},
            temperature=0.4,
            stage="task_plan",
            context_meta=assembly.to_llm_context_metadata(),
        )
        return self._parse(
            response,
            decay_due,
            semantic,
            max_tasks=max_tasks,
            max_minutes=max_minutes,
        )

    # -------------------------------------------------------------------------
    # Parsing
    # -------------------------------------------------------------------------

    def _parse(
        self,
        response: str,
        decay_due: list[tuple[str, DecayRecord]],
        semantic: SemanticMemory,
        max_tasks: int,
        max_minutes: int,
    ) -> tuple[list[DailyTask], str]:
        import uuid
        try:
            data = json.loads(response.strip())
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", response)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    return self._rule_based_plan(
                        semantic,
                        decay_due,
                        [],
                        max_tasks=max_tasks,
                        max_minutes=max_minutes,
                    )
            else:
                return self._rule_based_plan(
                    semantic,
                    decay_due,
                    [],
                    max_tasks=max_tasks,
                    max_minutes=max_minutes,
                )

        tasks = []
        for item in data.get("tasks", []):
            task_type_raw = str(item.get("task_type", "practice")).strip().lower()
            try:
                task_type = TaskType(task_type_raw)
            except ValueError:
                task_type = TaskType.PRACTICE

            priority = self._parse_priority(item.get("priority", 2))

            concept_id = item.get("concept_id") or None
            difficulty = item.get("suggested_difficulty") or None

            # 构建 ProblemQuery 占位（题库接入后填充）
            query = None
            if concept_id or task_type != TaskType.REVIEW:
                query = ProblemQuery(
                    tags=[concept_id] if concept_id else [],
                    difficulty=difficulty,
                    limit=1,
                )

            decay_record = next(
                (dr for cid, dr in decay_due if cid == concept_id), None
            )

            tasks.append(DailyTask(
                task_id=str(uuid.uuid4()),
                task_type=task_type,
                priority=priority,
                description=item.get("description", ""),
                reason=item.get("reason", ""),
                concept_id=concept_id,
                problem_query=query,
                estimated_minutes=self._parse_minutes(item.get("estimated_minutes", 15)),
                decay_score=decay_record.retention if decay_record else None,
            ))

        tasks = self._apply_limits(tasks, max_tasks=max_tasks, max_minutes=max_minutes)
        plan_summary = data.get("plan_summary", "今日学习计划已生成。")
        return tasks, plan_summary

    def _rule_based_plan(
        self,
        semantic: SemanticMemory,
        decay_due: list[tuple[str, DecayRecord]],
        recent_episodes: list[EpisodicMemory],
        max_tasks: int,
        max_minutes: int,
    ) -> tuple[list[DailyTask], str]:
        """无 prompt 时的规则降级"""
        import uuid
        tasks = []

        # 1. 紧急复习（Ebbinghaus 触发）
        for cid, dr in decay_due[:2]:
            tasks.append(DailyTask(
                task_id=str(uuid.uuid4()),
                task_type=TaskType.REVIEW,
                priority=TaskPriority.URGENT if dr.retention < 0.4 else TaskPriority.IMPORTANT,
                description=f"复习知识点：{cid}",
                reason=f"保留率已降至 {dr.retention:.0%}，需要及时巩固",
                concept_id=cid,
                problem_query=ProblemQuery(tags=[cid], difficulty="中等", limit=1),
                estimated_minutes=15,
                decay_score=dr.retention,
            ))

        # 2. 薄弱点练习
        weak = semantic.get_weak_concepts(0.5) if semantic else []
        for cid in weak[:max_tasks - len(tasks)]:
            if cid not in {t.concept_id for t in tasks}:
                tasks.append(DailyTask(
                    task_id=str(uuid.uuid4()),
                    task_type=TaskType.WEAK_CONCEPT,
                    priority=TaskPriority.IMPORTANT,
                    description=f"强化薄弱知识点：{cid}",
                    reason="长期记忆显示此知识点掌握度较低",
                    concept_id=cid,
                    problem_query=ProblemQuery(tags=[cid], difficulty="基础", limit=1),
                    estimated_minutes=15,
                ))

        tasks = self._apply_limits(tasks, max_tasks=max_tasks, max_minutes=max_minutes)
        summary = f"今日计划：{len(tasks)} 项任务，重点复习遗忘曲线临界的知识点。"
        return tasks, summary

    # -------------------------------------------------------------------------
    # Formatting helpers
    # -------------------------------------------------------------------------

    def _parse_priority(self, raw: object) -> TaskPriority:
        mapping = {
            "urgent": TaskPriority.URGENT,
            "important": TaskPriority.IMPORTANT,
            "suggested": TaskPriority.SUGGESTED,
            "1": TaskPriority.URGENT,
            "2": TaskPriority.IMPORTANT,
            "3": TaskPriority.SUGGESTED,
        }
        if isinstance(raw, str):
            v = raw.strip().lower()
            if v in mapping:
                return mapping[v]
        try:
            return TaskPriority(int(raw))  # type: ignore[arg-type]
        except (ValueError, TypeError):
            return TaskPriority.IMPORTANT

    def _parse_minutes(self, raw: object, default: int = 15) -> int:
        if isinstance(raw, (int, float)):
            value = int(raw)
        elif isinstance(raw, str):
            m = re.search(r"\d+", raw)
            value = int(m.group()) if m else default
        else:
            value = default
        return max(5, min(120, value))

    def _apply_limits(
        self,
        tasks: list[DailyTask],
        max_tasks: int,
        max_minutes: int,
    ) -> list[DailyTask]:
        if max_tasks <= 0 or not tasks:
            return []

        budget = max(1, max_minutes)
        selected: list[DailyTask] = []
        used = 0

        for task in tasks:
            if len(selected) >= max_tasks:
                break

            task.estimated_minutes = self._parse_minutes(task.estimated_minutes)
            if used + task.estimated_minutes <= budget:
                selected.append(task)
                used += task.estimated_minutes
                continue

            # 至少保留一个任务，避免预算太小时返回空计划
            if not selected:
                task.estimated_minutes = budget
                selected.append(task)
            break

        return selected

    def _fmt_mastery(self, semantic: SemanticMemory) -> str:
        if not semantic.concept_mastery:
            return "（暂无）"
        # top 5 弱项（按 level 升序）+ top 3 高频错误关联
        sorted_by_level = sorted(
            semantic.concept_mastery.items(), key=lambda x: x[1].level
        )
        weak = sorted_by_level[:5]
        high_error = sorted(
            semantic.concept_mastery.items(),
            key=lambda x: -x[1].error_count,
        )[:3]
        seen = {cid for cid, _ in weak}
        combined = list(weak) + [(c, r) for c, r in high_error if c not in seen]
        lines = []
        for cid, rec in combined:
            lines.append(
                f"  {cid}: {rec.level:.2f}（练{rec.practice_count}次，"
                f"错{rec.error_count}次，连续对{rec.consecutive_correct}次）"
            )
        return "\n".join(lines)

    def _fmt_decay(self, decay_due: list[tuple[str, DecayRecord]]) -> str:
        if not decay_due:
            return "（当前无需复习的知识点）"
        lines = []
        for cid, dr in decay_due[:5]:
            lines.append(
                f"  {cid}：保留率 {dr.retention:.0%}，"
                f"距上次练习 {dr.elapsed_days:.1f} 天"
                + ("【紧急】" if dr.retention < 0.4 else "")
            )
        return "\n".join(lines)

    def _fmt_episodes(self, episodes: list[EpisodicMemory]) -> str:
        if not episodes:
            return "（无近期记录）"
        lines = []
        for e in episodes[:5]:
            dt = e.created_at.strftime("%m-%d")
            lines.append(f"  {dt} {e.chapter}（{e.source.value}）— {e.outcome}，hints={e.hints_given}")
        return "\n".join(lines)

    def _estimate_load(self, episodes: list[EpisodicMemory]) -> str:
        """估计近3天的学习负荷"""
        from datetime import timedelta
        now = __import__("datetime").datetime.utcnow()
        recent_3days = [e for e in episodes if (now - e.created_at).days <= 3]
        n = len(recent_3days)
        if n >= 6:
            return f"近3天已完成 {n} 次会话，负荷较高，建议今日适当减量"
        elif n >= 3:
            return f"近3天完成 {n} 次会话，负荷适中"
        return f"近3天仅 {n} 次会话，可适当加量"
