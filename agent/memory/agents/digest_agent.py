#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DigestAgent - Episode 聚合摘要生成

将一批 EpisodicMemory 压缩为一段自然语言摘要（MemoryDigest）。
支持按时间（weekly）和按章节（chapter）两种聚合维度。
"""

import json
import re
import uuid
from datetime import datetime
from typing import Any

from agent.base_agent import BaseAgent
from ..data_structures import EpisodicMemory, MemoryDigest


class DigestAgent(BaseAgent):
    """LLM 聚合器：将多条 episode 压缩为 digest 摘要。"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        language: str = "zh",
        api_version: str | None = None,
        binding: str = "openai",
    ):
        super().__init__(
            module_name="memory",
            agent_name="digest_agent",
            api_key=api_key,
            base_url=base_url,
            api_version=api_version,
            language=language,
            binding=binding,
        )

    async def generate_weekly(
        self,
        student_id: str,
        period_key: str,
        episodes: list[EpisodicMemory],
    ) -> MemoryDigest:
        """生成周摘要。"""
        return await self._generate(
            student_id=student_id,
            digest_type="weekly",
            period_key=period_key,
            episodes=episodes,
            extra_context=f"时间段: {period_key}",
        )

    async def generate_chapter(
        self,
        student_id: str,
        chapter: str,
        episodes: list[EpisodicMemory],
    ) -> MemoryDigest:
        """生成章节摘要。"""
        return await self._generate(
            student_id=student_id,
            digest_type="chapter",
            period_key=chapter,
            episodes=episodes,
            extra_context=f"章节: {chapter}",
        )

    async def _generate(
        self,
        student_id: str,
        digest_type: str,
        period_key: str,
        episodes: list[EpisodicMemory],
        extra_context: str = "",
    ) -> MemoryDigest:
        # 聚合统计
        stats = self._compute_stats(episodes)
        all_tags = self._collect_tags(episodes)
        all_methods = self._collect_methods(episodes)

        # 构建 prompt
        episode_text = self._format_episodes(episodes)

        system_prompt = self.get_prompt("system")
        generate_template = self.get_prompt("generate")

        if not system_prompt or not generate_template:
            summary = self._rule_based_summary(episodes, stats)
        else:
            user_prompt = generate_template.format(
                digest_type=digest_type,
                period_key=period_key,
                extra_context=extra_context,
                episode_count=len(episodes),
                episodes=episode_text,
                stats_summary=json.dumps(stats, ensure_ascii=False),
                tags_covered=", ".join(all_tags[:15]) or "无",
                methods_used=", ".join(all_methods[:10]) or "无",
            )
            response = await self.call_llm(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.3,
                stage="generate_digest",
            )
            summary = self._extract_summary(response)
            if not summary:
                summary = self._rule_based_summary(episodes, stats)

        return MemoryDigest(
            digest_id=str(uuid.uuid4()),
            student_id=student_id,
            digest_type=digest_type,
            period_key=period_key,
            created_at=datetime.utcnow(),
            episode_ids=[ep.memory_id for ep in episodes],
            summary=summary,
            stats=stats,
            tags_covered=all_tags,
            methods_used=all_methods,
        )

    def _format_episodes(self, episodes: list[EpisodicMemory]) -> str:
        lines: list[str] = []
        for ep in episodes[:20]:  # 最多 20 条
            dt = ep.created_at.strftime("%m-%d")
            narrative = getattr(ep, "session_narrative", "") or ""
            narrative_part = f" — {narrative[:100]}" if narrative else ""
            lines.append(
                f"  [{dt}] {ep.chapter} | {ep.outcome} | "
                f"提示{ep.hints_given}次 | 方法: {', '.join(ep.methods_used) or '?'}"
                f"{narrative_part}"
            )
        return "\n".join(lines) or "  （无记录）"

    @staticmethod
    def _compute_stats(episodes: list[EpisodicMemory]) -> dict[str, Any]:
        total = len(episodes)
        solved = sum(1 for ep in episodes if ep.outcome == "solved")
        gave_up = sum(1 for ep in episodes if ep.outcome == "gave_up")
        total_hints = sum(ep.hints_given for ep in episodes)
        total_attempts = sum(ep.attempts for ep in episodes)
        error_counts: dict[str, int] = {}
        for ep in episodes:
            for et in ep.error_types:
                error_counts[et] = error_counts.get(et, 0) + 1
        return {
            "total_sessions": total,
            "solved": solved,
            "gave_up": gave_up,
            "solve_rate": round(solved / total, 2) if total > 0 else 0,
            "total_hints": total_hints,
            "total_attempts": total_attempts,
            "top_errors": sorted(error_counts.items(), key=lambda x: -x[1])[:5],
        }

    @staticmethod
    def _collect_tags(episodes: list[EpisodicMemory]) -> list[str]:
        seen: dict[str, int] = {}
        for ep in episodes:
            for tag in ep.tags:
                seen[tag] = seen.get(tag, 0) + 1
        return [t for t, _ in sorted(seen.items(), key=lambda x: -x[1])]

    @staticmethod
    def _collect_methods(episodes: list[EpisodicMemory]) -> list[str]:
        seen: dict[str, int] = {}
        for ep in episodes:
            for m in ep.methods_used:
                seen[m] = seen.get(m, 0) + 1
        return [m for m, _ in sorted(seen.items(), key=lambda x: -x[1])]

    @staticmethod
    def _extract_summary(response: str) -> str:
        """从 LLM 响应中提取摘要文本。"""
        try:
            data = json.loads(response.strip())
            return data.get("summary", "")
        except json.JSONDecodeError:
            pass
        # fallback: 尝试提取 JSON
        match = re.search(r"\{[\s\S]*\}", response)
        if match:
            try:
                data = json.loads(match.group())
                return data.get("summary", "")
            except json.JSONDecodeError:
                pass
        # 无法解析 JSON 时直接用全文
        return response.strip()[:500] if response.strip() else ""

    @staticmethod
    def _rule_based_summary(
        episodes: list[EpisodicMemory],
        stats: dict[str, Any],
    ) -> str:
        """无 prompt 时的降级摘要。"""
        total = stats.get("total_sessions", 0)
        solved = stats.get("solved", 0)
        gave_up = stats.get("gave_up", 0)
        parts = [f"共{total}次会话"]
        if solved:
            parts.append(f"解决{solved}题")
        if gave_up:
            parts.append(f"放弃{gave_up}题")
        chapters = list({ep.chapter for ep in episodes if ep.chapter})
        if chapters:
            parts.append(f"涉及章节: {', '.join(chapters[:3])}")
        return "，".join(parts) + "。"
