#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ProgressSummaryAgent - 学习进展叙述生成

输入：SemanticMemory（长期画像）+ 近期 EpisodicMemory 列表
输出：ProgressSummary 中的定性叙述字段

重点在于：利用长期记忆的模式 + 短期记忆的近况，生成有洞见的叙述，
而不只是"你做了X题，对了Y道"这种流水账。
"""

import json
import re
from datetime import datetime

from agent.base_agent import BaseAgent
from agent.context_governance.assembler import assemble
from agent.context_governance.budget_policy import PROGRESS_POLICY
from agent.memory.data_structures import EpisodicMemory, SemanticMemory
from ..data_structures import ProgressSummary


class ProgressSummaryAgent(BaseAgent):

    def __init__(self, api_key, base_url, language="zh", api_version=None, binding="openai"):
        super().__init__(
            module_name="progress",
            agent_name="progress_summary_agent",
            api_key=api_key, base_url=base_url,
            api_version=api_version, language=language, binding=binding,
        )

    async def process(
        self,
        student_id: str,
        semantic: SemanticMemory,
        episodes: list[EpisodicMemory],
        period: str = "week",
    ) -> ProgressSummary:
        """BaseAgent 兼容入口，路由到 summarize。"""
        return await self.summarize(
            student_id=student_id,
            semantic=semantic,
            episodes=episodes,
            period=period,
        )

    async def summarize(
        self,
        student_id: str,
        semantic: SemanticMemory,
        episodes: list[EpisodicMemory],
        period: str = "week",
    ) -> ProgressSummary:
        """
        生成学习进展报告。

        Args:
            semantic: 长期语义画像（cross-session 的稳定事实）
            episodes: 近期情节记忆（短期上下文，按时间倒序）
            period: "week" | "month" | "all_time"
        """
        # ---- 定量统计（纯计算，不走 LLM）----
        stats = self._compute_stats(episodes)

        # ---- 定性叙述（LLM）----
        system_prompt = self.get_prompt("system")
        template = self.get_prompt("summarize")

        if not system_prompt or not template:
            return self._fallback_summary(student_id, stats, semantic, period)

        long_term_profile = semantic.to_progress_snapshot()
        concept_mastery_summary = self._fmt_mastery(semantic)
        recent_episodes = self._fmt_episodes(episodes)

        assembly = assemble(
            {
                "long_term_profile": long_term_profile,
                "concept_mastery": concept_mastery_summary,
                "recent_episodes": recent_episodes,
            },
            PROGRESS_POLICY,
            sig_parts={
                "task": "progress_summary",
                "student_id": student_id,
            },
        )
        ap = assembly.payload

        user_prompt = template.format(
            period=period,
            sessions_completed=stats["sessions_completed"],
            problems_solved=stats["problems_solved"],
            avg_hints=f"{stats['avg_hints']:.1f}",
            methods_used=", ".join(stats["methods_used"]) or "（未记录）",
            long_term_profile=ap.get("long_term_profile", long_term_profile),
            concept_mastery_summary=ap.get("concept_mastery", concept_mastery_summary),
            recent_episodes=ap.get("recent_episodes", recent_episodes),
            persistence_events=semantic.persistence_events,
        )

        response = await self.call_llm(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            response_format={"type": "json_object"},
            temperature=0.5,
            stage="progress_summary",
            context_meta=assembly.to_llm_context_metadata(),
        )
        return self._parse(response, student_id, stats, period)

    # -------------------------------------------------------------------------

    def _compute_stats(self, episodes: list[EpisodicMemory]) -> dict:
        solved = [e for e in episodes if e.outcome == "solved"]
        all_methods = []
        for e in episodes:
            all_methods.extend(e.methods_used)
        hints_total = sum(e.hints_given for e in episodes)
        n = len(episodes) or 1
        return {
            "sessions_completed": len(episodes),
            "problems_solved": len(solved),
            "avg_hints": hints_total / n,
            "methods_used": list(dict.fromkeys(all_methods)),  # 去重保序
        }

    def _fmt_mastery(self, semantic: SemanticMemory) -> str:
        if not semantic.concept_mastery:
            return "（暂无掌握记录）"
        sorted_items = sorted(
            semantic.concept_mastery.items(), key=lambda x: x[1].level
        )
        # top 5 弱项 + top 3 进步项（按 consecutive_correct 降序）
        weak = sorted_items[:5]
        improving = sorted(
            semantic.concept_mastery.items(),
            key=lambda x: -x[1].consecutive_correct,
        )[:3]
        seen = {cid for cid, _ in weak}
        combined = list(weak) + [(c, r) for c, r in improving if c not in seen]
        lines = []
        for cid, rec in combined:
            lines.append(f"  {cid}: {rec.level:.2f}（练习{rec.practice_count}次）")
        return "\n".join(lines)

    def _fmt_episodes(self, episodes: list[EpisodicMemory]) -> str:
        if not episodes:
            return "（无近期记录）"
        lines = []
        for e in episodes[:5]:
            dt = e.created_at.strftime("%m-%d")
            methods = ", ".join(e.methods_used) or "?"
            lines.append(
                f"  {dt} [{e.source.value}] {e.chapter} — "
                f"{e.outcome}，提示{e.hints_given}次，方法：{methods}"
            )
        return "\n".join(lines)

    def _parse(self, response: str, student_id: str, stats: dict, period: str) -> ProgressSummary:
        try:
            data = json.loads(response.strip())
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", response)
            data = {}
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    pass

        return ProgressSummary(
            student_id=student_id,
            generated_at=datetime.utcnow(),
            period=period,
            sessions_completed=stats["sessions_completed"],
            problems_solved=stats["problems_solved"],
            total_hints_given=int(stats["avg_hints"] * stats["sessions_completed"]),
            avg_hints_per_session=stats["avg_hints"],
            methods_used=stats["methods_used"],
            narrative=data.get("narrative", ""),
            strengths=data.get("strengths", []),
            areas_to_improve=data.get("areas_to_improve", []),
            notable_achievements=data.get("notable_achievements", []),
            suggested_focus=data.get("suggested_focus", ""),
        )

    def _fallback_summary(
        self, student_id: str, stats: dict, semantic: SemanticMemory, period: str
    ) -> ProgressSummary:
        weak = semantic.get_weak_concepts() if semantic else []
        preferred = semantic.get_preferred_methods() if semantic else []
        return ProgressSummary(
            student_id=student_id,
            generated_at=datetime.utcnow(),
            period=period,
            sessions_completed=stats["sessions_completed"],
            problems_solved=stats["problems_solved"],
            avg_hints_per_session=stats["avg_hints"],
            methods_used=stats["methods_used"],
            narrative=f"完成了 {stats['problems_solved']} 道题。",
            strengths=preferred,
            areas_to_improve=weak,
        )
