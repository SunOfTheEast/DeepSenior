#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MemoryDistillerAgent - 情节 → 语义蒸馏

输入：一条 EpisodicMemory（结构化会话快照）+ 当前 SemanticMemory（现有画像）
输出：MemoryUpdate（增量更新指令）

设计原则：
  - 无状态：每次调用是独立的，context 显式传入
  - 保守更新：level delta 有上界（±0.3），防止单次会话大幅波动
  - 增量而非覆盖：输出 MemoryUpdate 而不是完整 SemanticMemory
"""

import json
import re

from agent.base_agent import BaseAgent
from agent.context_governance.assembler import assemble
from agent.context_governance.budget_policy import MEMORY_DISTILL_POLICY
from ..data_structures import (
    ConceptUpdate,
    EpisodicMemory,
    MemoryUpdate,
    MethodObservation,
    SemanticMemory,
)


class MemoryDistillerAgent(BaseAgent):
    """LLM 蒸馏器：将情节记忆提炼为对语义画像的增量更新"""

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
            agent_name="memory_distiller_agent",
            api_key=api_key,
            base_url=base_url,
            api_version=api_version,
            language=language,
            binding=binding,
        )

    async def process(
        self,
        episode: EpisodicMemory,
        current_semantic: SemanticMemory,
    ) -> MemoryUpdate:
        """BaseAgent 兼容入口，路由到 distill。"""
        return await self.distill(episode=episode, current_semantic=current_semantic)

    async def distill(
        self,
        episode: EpisodicMemory,
        current_semantic: SemanticMemory,
    ) -> MemoryUpdate:
        """
        从一条情节记忆提炼增量更新。

        Args:
            episode: 刚结束的会话快照
            current_semantic: 当前学生语义画像（用于上下文，让 LLM 知道已有状态）

        Returns:
            MemoryUpdate 增量更新指令
        """
        system_prompt = self.get_prompt("system")
        distill_template = self.get_prompt("distill")

        if not system_prompt or not distill_template:
            return self._rule_based_distill(episode)

        target_tags = (
            episode.solution_tags
            if episode.used_alternative_method
            else episode.tags
        )

        current_profile = current_semantic.to_distill_snapshot(target_tags)
        current_mastery = self._format_mastery(current_semantic, target_tags)

        assembly = assemble(
            {"current_profile": current_profile, "current_mastery_summary": current_mastery},
            MEMORY_DISTILL_POLICY,
            sig_parts={
                "task": "distill",
                "student_id": current_semantic.student_id,
            },
        )
        ap = assembly.payload

        user_prompt = distill_template.format(
            source=episode.source.value,
            outcome=episode.outcome,
            chapter=episode.chapter,
            question_tags=", ".join(episode.tags) or "无",
            solution_id=episode.solution_id or "standard",
            solution_method=episode.solution_method or "标准方法",
            solution_tags=", ".join(episode.solution_tags) or "（待建立索引）",
            needs_solution_card_audit=episode.needs_solution_card_audit,
            tags=", ".join(target_tags) or "无",
            understanding_summary=", ".join(
                f"{m}:{q}" for m, q in episode.understanding_summary.items()
            ) or "无",
            hints_given=episode.hints_given,
            attempts=episode.attempts,
            checkpoints_completed=episode.checkpoints_completed,
            total_checkpoints=episode.total_checkpoints,
            error_types=", ".join(episode.error_types) or "无",
            methods_used=", ".join(episode.methods_used) or "未知",
            methods_explored=", ".join(episode.methods_explored) or "无",
            alternative_flagged=episode.alternative_flagged,
            retry_triggered=episode.retry_triggered,
            current_profile=ap.get("current_profile", current_profile),
            current_mastery_summary=ap.get("current_mastery_summary", current_mastery),
        )

        response = await self.call_llm(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            response_format={"type": "json_object"},
            temperature=0.2,
            stage="distill_memory",
            context_meta=assembly.to_llm_context_metadata(),
        )

        return self._parse_update(response, episode)

    # -------------------------------------------------------------------------
    # Parsing
    # -------------------------------------------------------------------------

    def _parse_update(self, response: str, episode: EpisodicMemory) -> MemoryUpdate:
        try:
            data = json.loads(response.strip())
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", response)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    return self._rule_based_distill(episode)
            else:
                return self._rule_based_distill(episode)

        concept_updates = []
        for cu in data.get("concept_updates", []):
            delta = float(cu.get("delta", 0))
            delta = max(-0.3, min(0.3, delta))  # 上界约束
            concept_updates.append(ConceptUpdate(
                concept_id=cu.get("concept_id", ""),
                delta=delta,
                reason=cu.get("reason", ""),
                consecutive_correct_reset=cu.get("consecutive_correct_reset", False),
            ))

        method_observations = {}
        for mname, obs_str in data.get("method_observations", {}).items():
            try:
                method_observations[mname] = MethodObservation(obs_str)
            except ValueError:
                pass

        return MemoryUpdate(
            concept_updates=concept_updates,
            method_observations=method_observations,
            new_error_types=data.get("new_error_types", []),
            profile_summary=data.get("profile_summary") or None,
            recent_focus=data.get("recent_focus") or None,
            persistence_event=bool(data.get("persistence_event", False)),
        )

    def _rule_based_distill(self, episode: EpisodicMemory) -> MemoryUpdate:
        """无 prompt 时的规则降级：根据 outcome 对涉及的 concept 做固定 delta"""
        delta = 0.1 if episode.outcome == "solved" else -0.05
        target_tags = (
            episode.solution_tags
            if episode.used_alternative_method
            else episode.tags
        )
        concept_updates = [
            ConceptUpdate(concept_id=tag, delta=delta, reason=f"outcome={episode.outcome}")
            for tag in target_tags
        ]
        method_observations = {}
        for method in episode.methods_used:
            obs = (
                MethodObservation.USED_SUCCESSFULLY
                if episode.outcome == "solved"
                else MethodObservation.ATTEMPTED_FAILED
            )
            method_observations[method] = obs

        return MemoryUpdate(
            concept_updates=concept_updates,
            method_observations=method_observations,
            new_error_types=episode.error_types,
            persistence_event=episode.hints_given > 5,
        )

    def _format_mastery(self, semantic: SemanticMemory, tags: list[str]) -> str:
        """格式化与本次会话相关的 concept 掌握情况"""
        lines = []
        for tag in tags:
            rec = semantic.concept_mastery.get(tag)
            if rec:
                lines.append(f"  {tag}: {rec.level:.2f} (练习{rec.practice_count}次，错误{rec.error_count}次)")
            else:
                lines.append(f"  {tag}: 首次接触")
        return "\n".join(lines) or "  （无相关记录）"
