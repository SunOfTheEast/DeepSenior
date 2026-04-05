#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MemoryManager - 分层记忆管理器

对外统一接口，屏蔽存储和蒸馏细节。
Tutor / Review 模块通过此类完成：
  1. 会话结束后写入记忆（commit_session）
  2. 新会话开始前读取上下文（get_student_context）

分层结构：
  短期（Short-term）：TutorSession / ReviewSession 对象本身（不在此管理）
  中期（Working）：最近 N 条情节记忆，按需加载，用于近期模式感知
  长期（Long-term）：语义画像（SemanticMemory），LLM 提炼的稳定事实
"""

import asyncio
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.infra.logging import get_logger
from agent.knowledge.concept_registry import ConceptRegistry
from agent.knowledge.solution_link_store import SolutionLinkStore

from .data_structures import (
    ConceptUpdate,
    EpisodicMemory,
    MemoryUpdate,
    MethodObservation,
    SemanticMemory,
    SessionSource,
    SolutionMasteryRecord,
)
from .digest_store import DigestStore
from .mastery_graph import MasteryGraph, StudentMasteryView
from .memory_index import MemoryIndex
from .memory_store import MemoryStore
from .skills.registry import MemorySkillRegistry


class MemoryManager:
    """
    分层记忆管理器。

    Usage（会话结束后）：
        episode = manager.build_episodic_from_tutor(student_id, session_export)
        await manager.commit_session(student_id, episode)

    Usage（会话开始前）：
        context = manager.get_student_context(student_id)
        # 将 context 字符串插入 LLM system prompt
    """

    # 中期记忆：加载最近 N 条情节记忆用于 working context
    WORKING_MEMORY_WINDOW = 8
    _ALLOWED_OUTCOMES = {"solved", "gave_up", "in_progress", "explored"}

    # 每个 student_id 一把锁，防止并发 commit 丢失更新
    _student_locks: dict[str, asyncio.Lock] = {}

    @classmethod
    def _get_student_lock(cls, student_id: str) -> asyncio.Lock:
        if student_id not in cls._student_locks:
            cls._student_locks[student_id] = asyncio.Lock()
        return cls._student_locks[student_id]

    def __init__(
        self,
        api_key: str,
        base_url: str,
        language: str = "zh",
        api_version: str | None = None,
        binding: str = "openai",
        store_base_dir: str | Path | None = None,
        skill_registry: MemorySkillRegistry | None = None,
    ):
        self.logger = get_logger("MemoryManager")
        self.store = MemoryStore(base_dir=store_base_dir)
        self._concept_registry = ConceptRegistry()
        self._solution_link_store = SolutionLinkStore()
        self._skill_registry = skill_registry or MemorySkillRegistry(
            api_key=api_key,
            base_url=base_url,
            language=language,
            api_version=api_version,
            binding=binding,
        )
        self._distill = self._skill_registry.get("distill_memory")
        self._index = MemoryIndex(self.store)
        self._mastery_graph = MasteryGraph(self._concept_registry)
        self._digest_store = DigestStore(self.store.base_dir)
        self._digest_agent = None  # lazy init to avoid LLM config at import time
        self._api_key = api_key
        self._base_url = base_url
        self._language = language
        self._api_version = api_version
        self._binding = binding

    # =========================================================================
    # 写入：会话结束后调用
    # =========================================================================

    async def commit_session(
        self,
        student_id: str,
        episode: EpisodicMemory,
        run_distillation: bool = True,
        turns: list[dict] | None = None,
    ) -> SemanticMemory:
        """
        提交一次会话记忆：
          0. 持久化原始对话 turns（L0）
          1. 持久化情节记忆
          2. （可选）调用 LLM 蒸馏，更新语义画像

        Args:
            run_distillation: False 时只写情节，不更新语义（用于批量导入历史数据）
            turns: 原始对话记录（L0），来自 session.interaction_history

        Returns:
            更新后的 SemanticMemory

        并发安全：同一 student_id 的 commit 通过 asyncio.Lock 串行化，
        防止 read-modify-write 竞争导致语义画像更新丢失。
        """
        if episode.student_id != student_id:
            raise ValueError(
                f"student_id mismatch: commit_session called with '{student_id}' "
                f"but episode.student_id is '{episode.student_id}'"
            )

        async with self._get_student_lock(student_id):
            return await self._commit_session_locked(
                student_id, episode, run_distillation, turns
            )

    async def _commit_session_locked(
        self,
        student_id: str,
        episode: EpisodicMemory,
        run_distillation: bool,
        turns: list[dict] | None = None,
    ) -> SemanticMemory:
        if self.store.has_episodic(student_id, episode.memory_id):
            self.logger.info(
                f"[{student_id}] Skip duplicate episodic commit: {episode.memory_id} "
                f"(session={episode.session_id}, source={episode.source.value})"
            )
            return self.store.load_or_create_semantic(student_id)

        # 0. 持久化原始对话 (L0)
        if turns and episode.session_id:
            self.store.save_turns(student_id, episode.session_id, turns)
            self.logger.info(
                f"[{student_id}] Turns saved: {episode.session_id} ({len(turns)} turns)"
            )

        # 1. 保存情节记忆
        self.store.save_episodic(episode)
        self.logger.info(
            f"[{student_id}] Episodic committed: {episode.memory_id} ({episode.source.value})"
        )

        # 2. 加载或创建语义画像
        semantic = self.store.load_or_create_semantic(student_id)

        if run_distillation:
            # 3. LLM 蒸馏（传入 turns 供 narrative 生成）
            update = await self._distill(episode, semantic, turns=turns)
            self._apply_update(semantic, episode, update)
            # 将 narrative 写回 episode 并重新保存
            if update.session_narrative:
                episode.session_narrative = update.session_narrative
                self.store.save_episodic(episode)
            self.store.save_semantic(semantic)
            self.logger.info(f"[{student_id}] Semantic updated after distillation")
        else:
            # 不蒸馏时也更新统计计数
            self._update_counters(semantic, episode)
            self.store.save_semantic(semantic)

        # 索引缓存失效
        self._index.invalidate(student_id)

        return semantic

    # =========================================================================
    # 读取：会话开始前调用
    # =========================================================================

    def get_student_context(
        self,
        student_id: str,
        include_recent_episodes: bool = True,
        include_digests: bool = True,
    ) -> str:
        """
        获取学生的完整上下文字符串，供 LLM prompt 使用。

        包含：
          - 长期语义画像摘要
          - 中期摘要（digest）
          - 近期会话记录
        """
        semantic = self.store.load_semantic(student_id)
        parts = []

        if semantic:
            long_term = semantic.to_context_string()
            if long_term and long_term != "（暂无长期记忆）":
                parts.append(f"【长期画像】\n{long_term}")

        if include_digests:
            weekly_digests = self._digest_store.list_digests(student_id, "weekly")
            if weekly_digests:
                lines = ["【近期学习摘要】"]
                for d in weekly_digests[:3]:
                    lines.append(f"  {d.period_key}: {d.summary[:150]}")
                parts.append("\n".join(lines))

        if include_recent_episodes:
            recent = self.store.list_episodic(student_id, limit=self.WORKING_MEMORY_WINDOW)
            if recent:
                lines = ["【近期会话记录】"]
                for ep in recent[:5]:  # 只展示最近5条
                    dt = ep.created_at.strftime("%m-%d")
                    extras: list[str] = []
                    if ep.deep_dive_count:
                        extras.append(f"深问{ep.deep_dive_count}次")
                    if ep.deferred_deep_dive_tasks:
                        extras.append(f"延后任务{len(ep.deferred_deep_dive_tasks)}条")
                    extra_text = f", {'; '.join(extras)}" if extras else ""
                    lines.append(
                        f"  {dt} [{ep.source.value}] {ep.chapter} — "
                        f"{ep.outcome}, 提示{ep.hints_given}次, "
                        f"方法: {', '.join(ep.methods_used) or '?'}{extra_text}"
                    )
                parts.append("\n".join(lines))

        return "\n\n".join(parts) if parts else "（该学生暂无历史记忆）"

    def get_semantic(self, student_id: str) -> SemanticMemory | None:
        """直接获取语义画像，供 Progress 模块使用"""
        return self.store.load_semantic(student_id)

    def get_mastery_view(self, student_id: str) -> StudentMasteryView | None:
        """获取图感知的掌握度视图，供 Progress / Recommend 使用。"""
        semantic = self.store.load_semantic(student_id)
        if semantic is None:
            return None
        return self._mastery_graph.overlay(semantic)

    def get_recent_episodes(
        self,
        student_id: str,
        limit: int = 10,
        source: str | None = None,
    ) -> list[EpisodicMemory]:
        return self.store.list_episodic(student_id, limit=limit, source=source)

    def query_episodes(
        self,
        student_id: str,
        *,
        concept_ids: list[str] | None = None,
        chapter: str | None = None,
        outcome: str | None = None,
        method_slot: str | None = None,
        error_types: list[str] | None = None,
        source: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 20,
        load_full: bool = False,
    ) -> list[Any]:
        """
        多维结构化检索 episode。

        Args:
            load_full: True 时返回 list[EpisodicMemory]，False 时返回 list[IndexEntry]（轻量）

        Returns:
            匹配的 episode 列表（按时间倒序）
        """
        from .memory_index import IndexEntry  # noqa: F811
        entries = self._index.query(
            student_id,
            concept_ids=concept_ids,
            chapter=chapter,
            outcome=outcome,
            method_slot=method_slot,
            error_types=error_types,
            source=source,
            since=since,
            until=until,
            limit=limit,
        )
        if load_full:
            return self._index.get_episodes(student_id, entries)
        return entries

    # =========================================================================
    # Digest 层（L1.5）：聚合摘要 + 语义检索
    # =========================================================================

    def _ensure_digest_agent(self):
        if self._digest_agent is None:
            from .agents.digest_agent import DigestAgent
            self._digest_agent = DigestAgent(
                api_key=self._api_key,
                base_url=self._base_url,
                language=self._language,
                api_version=self._api_version,
                binding=self._binding,
            )
        return self._digest_agent

    async def generate_digests(
        self,
        student_id: str,
        force: bool = False,
    ) -> list:
        """
        检查并生成需要更新的 digest（weekly + chapter）。

        Args:
            force: True 时强制重新生成所有 digest
        Returns:
            新生成的 MemoryDigest 列表
        """
        from .data_structures import MemoryDigest

        episodes = self.store.list_episodic(student_id, limit=None)
        if not episodes:
            return []

        agent = self._ensure_digest_agent()
        generated: list[MemoryDigest] = []

        # --- Weekly digests ---
        by_week: dict[str, list] = {}
        for ep in episodes:
            week_key = ep.created_at.strftime("%G-W%V")
            by_week.setdefault(week_key, []).append(ep)

        for week_key, week_eps in by_week.items():
            if len(week_eps) < 2 and not force:
                continue
            existing = self._digest_store.load_digest(student_id, "weekly", week_key)
            if existing and not force:
                # 检查是否有新 episode
                existing_ids = set(existing.episode_ids)
                if all(ep.memory_id in existing_ids for ep in week_eps):
                    continue
            digest = await agent.generate_weekly(student_id, week_key, week_eps)
            self._digest_store.save_digest(digest)
            generated.append(digest)

        # --- Chapter digests ---
        by_chapter: dict[str, list] = {}
        for ep in episodes:
            if ep.chapter:
                by_chapter.setdefault(ep.chapter, []).append(ep)

        for chapter, ch_eps in by_chapter.items():
            if len(ch_eps) < 2 and not force:
                continue
            existing = self._digest_store.load_digest(student_id, "chapter", chapter)
            if existing and not force:
                existing_ids = set(existing.episode_ids)
                if all(ep.memory_id in existing_ids for ep in ch_eps):
                    continue
            digest = await agent.generate_chapter(student_id, chapter, ch_eps)
            self._digest_store.save_digest(digest)
            generated.append(digest)

        if generated:
            self.logger.info(
                f"[{student_id}] Generated {len(generated)} digests "
                f"(weekly={sum(1 for d in generated if d.digest_type == 'weekly')}, "
                f"chapter={sum(1 for d in generated if d.digest_type == 'chapter')})"
            )
        return generated

    def get_digests(
        self,
        student_id: str,
        digest_type: str | None = None,
    ) -> list:
        """获取学生的 digest 列表。"""
        return self._digest_store.list_digests(student_id, digest_type)

    def search_memory(
        self,
        student_id: str,
        query: str,
        top_k: int = 5,
    ) -> dict[str, Any]:
        """
        语义检索学生记忆。

        结合 digest 层语义搜索 + 最近未 digest 的 episode 结构化补查。

        Returns:
            {
                "digests": list[MemoryDigest],
                "recent_undigested": list[EpisodicMemory],
            }
        """
        import os
        from .digest_index import EmbeddingDigestIndex

        digests = self._digest_store.list_digests(student_id)
        matched_digests = []

        if digests:
            zhipuai_key = os.environ.get("ZHIPUAI_API_KEY", "")
            if zhipuai_key:
                cache_dir = self.store.base_dir / student_id / ".digest_embedding_cache"
                idx = EmbeddingDigestIndex(api_key=zhipuai_key, cache_dir=str(cache_dir))
                idx.build(digests)
                results = idx.search(query, top_k=top_k)
                digest_map = {d.digest_id: d for d in digests}
                matched_digests = [
                    digest_map[did] for did, _ in results
                    if did in digest_map
                ]

        # 补查最近未被 digest 覆盖的 episode
        digested_ids: set[str] = set()
        for d in digests:
            digested_ids.update(d.episode_ids)
        recent = self.store.list_episodic(student_id, limit=10)
        undigested = [ep for ep in recent if ep.memory_id not in digested_ids]

        return {
            "digests": matched_digests,
            "recent_undigested": undigested,
        }

    def replay_pending_audit_tasks(
        self,
        student_id: str,
        limit: int = 50,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """对单个学生补回已具备索引条件的 pending audit 任务。"""
        semantic = self.store.load_semantic(student_id)
        if semantic is None:
            return {
                "student_id": student_id,
                "session_id": session_id,
                "replayed": 0,
                "unresolved": 0,
                "changed": False,
            }

        episodes = self.store.list_episodic(student_id, limit=None, source=SessionSource.TUTOR.value)
        episodes_by_session = {ep.session_id: ep for ep in episodes if ep.session_id}
        replayed = 0
        unresolved = 0
        changed = False
        bridged_solution_ids: set[str] = set()

        for task in semantic.pending_audit_tasks:
            if replayed >= limit:
                break
            if task.get("status") in {"done", "rejected"}:
                continue
            if session_id and task.get("session_id") != session_id:
                continue

            episode = episodes_by_session.get(task.get("session_id", ""))
            if episode is None:
                unresolved += 1
                continue

            resolved_concepts = self._resolve_replay_concepts(task=task, episode=episode)
            if not resolved_concepts:
                unresolved += 1
                continue

            now = datetime.utcnow()
            solution_id = episode.solution_id or task.get("solution_id")
            if solution_id:
                solution_rec = semantic.solution_mastery.get(solution_id)
                if solution_rec is None:
                    solution_rec, _ = self._update_solution_mastery(
                        semantic=semantic,
                        episode=episode,
                        now=now,
                    )
                if solution_rec:
                    was_ready = solution_rec.index_status == "ready"
                    solution_rec.linked_concepts = list(dict.fromkeys(resolved_concepts))
                    solution_rec.index_status = "ready"
                    if not was_ready and solution_id not in bridged_solution_ids:
                        self._bridge_solution_delta_to_concepts(
                            semantic=semantic,
                            linked_concepts=solution_rec.linked_concepts,
                            solution_delta=self._estimate_solution_delta(episode),
                            already_updated=set(),
                            now=now,
                        )
                        bridged_solution_ids.add(solution_id)

            task["status"] = "done"
            task["resolved_at"] = now.isoformat()
            task["resolved_concepts"] = list(dict.fromkeys(resolved_concepts))
            task["replay_source"] = "concept_registry"
            changed = True
            replayed += 1

        if changed:
            semantic.last_updated = datetime.utcnow()
            self.store.save_semantic(semantic)
            self.logger.info(
                f"[{student_id}] Replayed pending audit tasks: replayed={replayed}, unresolved={unresolved}"
            )
        return {
            "student_id": student_id,
            "session_id": session_id,
            "replayed": replayed,
            "unresolved": unresolved,
            "changed": changed,
        }

    def replay_from_audit_entry(
        self,
        record: dict[str, Any],
        limit_per_student: int = 50,
    ) -> dict[str, Any]:
        """按单条审计记录自动触发定向 replay。"""
        session_id = str(record.get("session_id") or "").strip() or None
        explicit_student_id = str(record.get("student_id") or "").strip() or None
        matched_by = "student_id" if explicit_student_id else "session_id"
        candidate_student_ids = (
            [explicit_student_id]
            if explicit_student_id
            else self._find_student_ids_by_session(session_id)
        )

        if not candidate_student_ids:
            return {
                "triggered": False,
                "matched_by": matched_by,
                "session_id": session_id,
                "student_ids": [],
                "results": {},
                "replayed": 0,
                "unresolved": 0,
                "changed": False,
                "reason": "student_not_found",
            }

        results: dict[str, Any] = {}
        total_replayed = 0
        total_unresolved = 0
        any_changed = False

        for student_id in candidate_student_ids:
            result = self.replay_pending_audit_tasks(
                student_id,
                limit=limit_per_student,
                session_id=session_id,
            )
            results[student_id] = result
            total_replayed += int(result.get("replayed", 0))
            total_unresolved += int(result.get("unresolved", 0))
            any_changed = any_changed or bool(result.get("changed", False))

        return {
            "triggered": True,
            "matched_by": matched_by,
            "session_id": session_id,
            "student_ids": candidate_student_ids,
            "results": results,
            "replayed": total_replayed,
            "unresolved": total_unresolved,
            "changed": any_changed,
        }

    def replay_all_pending_audit_tasks(
        self,
        limit_per_student: int = 50,
    ) -> dict[str, dict[str, Any]]:
        """批量回放所有学生的 pending audit 任务。"""
        results: dict[str, dict[str, Any]] = {}
        for student_id in self.store.list_students():
            results[student_id] = self.replay_pending_audit_tasks(
                student_id,
                limit=limit_per_student,
            )
        return results

    def _find_student_ids_by_session(self, session_id: str | None) -> list[str]:
        if not session_id:
            return []

        matched: list[str] = []
        for student_id in self.store.list_students():
            episodes = self.store.list_episodic(
                student_id,
                limit=None,
                source=SessionSource.TUTOR.value,
            )
            if any(ep.session_id == session_id for ep in episodes):
                matched.append(student_id)
        return matched

    # =========================================================================
    # 工厂方法：从 session export 构建 EpisodicMemory
    # =========================================================================

    @staticmethod
    def build_episodic_from_tutor(
        student_id: str,
        export: dict[str, Any],
    ) -> EpisodicMemory:
        """
        从 TutorManager.export_session() 的输出构建情节记忆。

        TutorManager 无需感知 MemoryManager，只需调用 export_session()，
        由调用方（API 层或 Progress 调度器）传给此方法。
        """
        now = datetime.utcnow()
        session_id = export.get("session_id", "")
        return EpisodicMemory(
            memory_id=_stable_memory_id(student_id, SessionSource.TUTOR, session_id),
            student_id=student_id,
            session_id=session_id,
            source=SessionSource.TUTOR,
            created_at=now,
            problem_id=export.get("problem_id", ""),
            chapter=export.get("chapter", ""),
            tags=export.get("tags", []),
            outcome=_normalize_outcome(export.get("outcome") or export.get("status")),
            hints_given=export.get("total_hints_given", 0),
            checkpoints_completed=export.get("checkpoints_completed", 0),
            total_checkpoints=export.get("total_checkpoints", 0),
            error_types=export.get("error_types_seen", []),
            attempts=export.get("total_attempts", 0),
            methods_used=_extract_methods(export),
            alternative_flagged=export.get("alternative_flagged", False),
            used_alternative_method=export.get("used_alternative_method", False),
            solution_id=export.get("solution_id"),
            solution_method=export.get("solution_method") or export.get("alternative_method"),
            solution_tags=export.get("solution_tags", []),
            method_slot_matched=export.get("method_slot_matched"),
            needs_solution_card_audit=bool(export.get("needs_solution_card_audit", False)),
            solution_card_audit_reason=export.get("solution_card_audit_reason"),
            deep_dive_count=export.get("deep_dive_count", 0),
            deep_dive_topics=export.get("deep_dive_topics", []),
            deep_dive_understanding=export.get("deep_dive_understanding", {}),
            deferred_deep_dive_tasks=export.get("deferred_deep_dive_tasks", []),
        )

    @staticmethod
    def build_episodic_from_review(
        student_id: str,
        export: dict[str, Any],
    ) -> EpisodicMemory:
        """
        从 ReviewChatManager.export_session() 的输出构建情节记忆。

        understanding_summary: {method_name: quality_str} 独立存储在 understanding_summary，
        避免污染 tags（tags 仅保留知识点 concept_id）。
        """
        now = datetime.utcnow()
        session_id = export.get("session_id", "")
        return EpisodicMemory(
            memory_id=_stable_memory_id(student_id, SessionSource.REVIEW, session_id),
            student_id=student_id,
            session_id=session_id,
            source=SessionSource.REVIEW,
            created_at=now,
            problem_id=export.get("problem_id", ""),
            chapter=export.get("chapter", ""),
            tags=export.get("tags", []),
            outcome=_normalize_outcome(export.get("outcome", "explored")),
            hints_given=0,
            checkpoints_completed=0,
            total_checkpoints=0,
            error_types=[],
            attempts=0,
            methods_used=[export.get("student_method_used", "")],
            alternative_flagged=False,
            used_alternative_method=False,
            solution_id="review",
            solution_method=export.get("student_method_used"),
            solution_tags=[],
            method_slot_matched=export.get("method_slot_matched"),
            needs_solution_card_audit=False,
            solution_card_audit_reason=None,
            deep_dive_count=0,
            deep_dive_topics=[],
            deep_dive_understanding={},
            deferred_deep_dive_tasks=[],
            methods_explored=export.get("methods_explored", []),
            retry_triggered=export.get("retry_triggered", False),
            retry_method=export.get("retry_method"),
            understanding_summary=export.get("understanding_summary", {}),
        )

    # =========================================================================
    # Internal: apply MemoryUpdate to SemanticMemory
    # =========================================================================

    def _apply_update(
        self,
        semantic: SemanticMemory,
        episode: EpisodicMemory,
        update: MemoryUpdate,
    ) -> None:
        now = datetime.utcnow()

        # 更新计数
        self._update_counters(semantic, episode)

        # Concept mastery
        allowed_solution_concepts: set[str] | None = None
        solution_tags = list(dict.fromkeys(episode.solution_tags or []))
        if episode.used_alternative_method:
            allowed_solution_concepts = set(solution_tags)
            if episode.needs_solution_card_audit or not allowed_solution_concepts:
                self._enqueue_solution_audit_task(
                    semantic=semantic,
                    episode=episode,
                    task_type="solution_card_index",
                    reason=(
                        episode.solution_card_audit_reason
                        or "替代解法缺少知识卡片索引，需要后台 RAG 建立并人工审计。"
                    ),
                    now=now,
                )

        first_time_alt_method = (
            bool(episode.used_alternative_method)
            and bool(episode.solution_method)
            and episode.solution_method not in semantic.method_observations
        )
        if first_time_alt_method:
            self._enqueue_solution_audit_task(
                semantic=semantic,
                episode=episode,
                task_type="new_method_rag",
                reason=(
                    f"首次观察到替代方法「{episode.solution_method}」，"
                    "请后台做知识卡片 RAG 检索并提交学生/教师审计任务。"
                ),
                now=now,
            )

        applied_concept_updates = 0
        applied_concept_ids: set[str] = set()
        for cu in update.concept_updates:
            if not cu.concept_id:
                continue
            if allowed_solution_concepts is not None and cu.concept_id not in allowed_solution_concepts:
                continue
            self._apply_concept_delta(
                semantic=semantic,
                concept_id=cu.concept_id,
                delta=cu.delta,
                consecutive_correct_reset=cu.consecutive_correct_reset,
                now=now,
            )
            applied_concept_updates += 1
            applied_concept_ids.add(cu.concept_id)

        # fallback：替代解法会话若已有 solution 索引，但 LLM 未输出对应 concept update，
        # 仍对该 solution 的知识点做小幅正向更新（仅 solved 时）。
        if (
            allowed_solution_concepts is not None
            and episode.outcome == "solved"
            and allowed_solution_concepts
            and applied_concept_updates == 0
        ):
            for concept_id in sorted(allowed_solution_concepts):
                self._apply_concept_delta(
                    semantic=semantic,
                    concept_id=concept_id,
                    delta=0.08,
                    consecutive_correct_reset=False,
                    now=now,
                )
                applied_concept_ids.add(concept_id)

        # Solution mastery（question -> solution 分叉熟练度）
        # 并通过 bridge 同步到 concept_mastery，保证 Progress/Recommend 可直接消费。
        solution_rec, solution_delta = self._update_solution_mastery(
            semantic=semantic,
            episode=episode,
            now=now,
        )
        if (
            solution_rec
            and solution_rec.index_status == "ready"
            and solution_rec.linked_concepts
        ):
            self._bridge_solution_delta_to_concepts(
                semantic=semantic,
                linked_concepts=solution_rec.linked_concepts,
                solution_delta=solution_delta,
                already_updated=applied_concept_ids,
                now=now,
            )

        # Slot mastery（双层 mastery 的方法维度）
        if episode.method_slot_matched:
            from .data_structures import MethodSlotMastery
            slot_id = episode.method_slot_matched
            sm = semantic.slot_mastery.get(slot_id)
            if sm is None:
                sm = MethodSlotMastery(slot_id=slot_id)
                semantic.slot_mastery[slot_id] = sm
            sm.use_count += 1
            sm.last_used_at = now
            if episode.outcome == "solved":
                sm.success_count += 1

        # Method observations（保留最近 20 条）
        for method, obs in update.method_observations.items():
            history = semantic.method_observations.setdefault(method, [])
            history.append(obs)
            if len(history) > 20:
                semantic.method_observations[method] = history[-20:]

        # Error patterns
        for error in update.new_error_types:
            semantic.persistent_errors[error] = semantic.persistent_errors.get(error, 0) + 1

        # Profile text
        if update.profile_summary:
            semantic.profile_summary = update.profile_summary
        if update.recent_focus:
            semantic.recent_focus = update.recent_focus

        # Persistence
        if update.persistence_event:
            semantic.persistence_events += 1

        semantic.last_updated = now

    def _apply_concept_delta(
        self,
        semantic: SemanticMemory,
        concept_id: str,
        delta: float,
        consecutive_correct_reset: bool,
        now: datetime,
    ) -> None:
        from .data_structures import MasteryRecord

        rec = semantic.concept_mastery.get(concept_id)
        if rec is None:
            rec = MasteryRecord(
                concept_id=concept_id,
                level=0.3,
                last_practiced=now,
            )
            semantic.concept_mastery[concept_id] = rec

        rec.level = max(0.0, min(1.0, rec.level + delta))
        rec.last_practiced = now
        rec.practice_count += 1
        if delta < 0:
            rec.error_count += 1
            if consecutive_correct_reset:
                rec.consecutive_correct = 0
        else:
            rec.consecutive_correct += 1

    def _update_solution_mastery(
        self,
        semantic: SemanticMemory,
        episode: EpisodicMemory,
        now: datetime,
    ) -> tuple[SolutionMasteryRecord | None, float]:
        """
        更新 solution-level 熟练度：
          - 仅对 tutor 场景落盘，避免 review 的泛化 solution_id 污染；
          - 记录 solution 与 concept 的关联，作为后续 bridge 的依据。
        """
        if episode.source != SessionSource.TUTOR:
            return None, 0.0

        solution_id = (episode.solution_id or "").strip()
        if not solution_id:
            return None, 0.0

        method_name = (
            (episode.solution_method or "").strip()
            or (episode.methods_used[0] if episode.methods_used else "")
            or "标准方法"
        )
        question_id = (episode.problem_id or "").strip() or solution_id.split("::", 1)[0]
        is_alt_solution = bool(episode.used_alternative_method)
        if is_alt_solution:
            # 关键规则：替代方法在索引缺失时，不能回退到 question-level tags。
            linked_concepts = list(dict.fromkeys(episode.solution_tags or []))
        else:
            linked_concepts = list(dict.fromkeys(episode.solution_tags or episode.tags or []))
        index_status = self._infer_solution_index_status(
            episode=episode,
            linked_concepts=linked_concepts,
        )
        delta = self._estimate_solution_delta(episode)

        rec = semantic.solution_mastery.get(solution_id)
        if rec is None:
            base_level = 0.3
            rec = SolutionMasteryRecord(
                solution_id=solution_id,
                question_id=question_id,
                method_name=method_name,
                level=max(0.0, min(1.0, base_level + delta)),
                first_seen_at=now,
                last_used_at=now,
                use_count=1,
                linked_concepts=linked_concepts,
                last_outcome=episode.outcome,
                index_status=index_status,
            )
            semantic.solution_mastery[solution_id] = rec
            return rec, delta

        rec.level = max(0.0, min(1.0, rec.level + delta))
        rec.last_used_at = now
        rec.use_count += 1
        rec.last_outcome = episode.outcome
        if method_name:
            rec.method_name = method_name
        if question_id:
            rec.question_id = question_id
        rec.index_status = self._resolve_solution_index_status(
            current=rec.index_status,
            observed=index_status,
        )
        if is_alt_solution:
            # 替代方法路径以当前索引结果为准，避免历史错误回退 tags 残留。
            rec.linked_concepts = linked_concepts
        else:
            for concept_id in linked_concepts:
                if concept_id and concept_id not in rec.linked_concepts:
                    rec.linked_concepts.append(concept_id)
        return rec, delta

    def _estimate_solution_delta(self, episode: EpisodicMemory) -> float:
        """根据会话结果估算 solution-level 熟练度增量。"""
        base = {
            "solved": 0.12,
            "in_progress": 0.02,
            "explored": 0.05,
            "gave_up": -0.08,
        }.get(episode.outcome, 0.0)

        if base > 0 and episode.hints_given >= 3:
            base *= 0.75
        if base > 0 and episode.attempts >= 4:
            base *= 0.8
        if base > 0 and episode.alternative_flagged and episode.used_alternative_method:
            # 绕开目标知识点时，solution 熟练度也适度保守增长
            base *= 0.7

        return round(base, 4)

    @staticmethod
    def _infer_solution_index_status(
        episode: EpisodicMemory,
        linked_concepts: list[str],
    ) -> str:
        """
        评估 solution 索引状态：
          - ready: 可安全桥接到 concept_mastery
          - pending: 仍需补齐/审计，禁止桥接
        """
        is_alt_solution = bool(episode.used_alternative_method)
        if not is_alt_solution:
            return "ready"
        if linked_concepts and not episode.needs_solution_card_audit:
            return "ready"
        return "pending"

    @staticmethod
    def _resolve_solution_index_status(current: str, observed: str) -> str:
        """
        处理状态流转：
          - ready 优先（一旦索引可用，放行 bridge）
          - rejected 在未 ready 前保持
          - 其他场景按 observed 覆盖
        """
        if observed == "ready":
            return "ready"
        if current == "rejected":
            return "rejected"
        return observed

    def _bridge_solution_delta_to_concepts(
        self,
        semantic: SemanticMemory,
        linked_concepts: list[str],
        solution_delta: float,
        already_updated: set[str],
        now: datetime,
    ) -> None:
        """
        将 solution-level 的掌握变化同步到 concept-level。

        目的：即使 distiller 没给全量 concept_update，Progress 仍能通过 concept_mastery
        感知到方法分叉带来的学习变化。
        """
        if not linked_concepts:
            return
        if abs(solution_delta) < 1e-9:
            return

        bridge_delta = max(-0.06, min(0.06, solution_delta * 0.4))
        if abs(bridge_delta) < 0.01:
            return

        for concept_id in linked_concepts:
            if not concept_id or concept_id in already_updated:
                continue
            self._apply_concept_delta(
                semantic=semantic,
                concept_id=concept_id,
                delta=bridge_delta,
                consecutive_correct_reset=(bridge_delta < 0),
                now=now,
            )

    def _enqueue_solution_audit_task(
        self,
        semantic: SemanticMemory,
        episode: EpisodicMemory,
        task_type: str,
        reason: str,
        now: datetime,
    ) -> None:
        # session 级别去重，避免网络重试导致重复任务
        for task in semantic.pending_audit_tasks:
            if (
                task.get("session_id") == episode.session_id
                and task.get("task_type") == task_type
            ):
                return

        task = {
            "task_id": str(uuid.uuid4()),
            "task_type": task_type,
            "status": "pending",
            "created_at": now.isoformat(),
            "session_id": episode.session_id,
            "problem_id": episode.problem_id,
            "chapter": episode.chapter,
            "solution_id": episode.solution_id,
            "solution_method": episode.solution_method,
            "method_slot_matched": episode.method_slot_matched,
            "question_tags": list(episode.tags),
            "solution_tags": list(episode.solution_tags),
            "reason": reason,
        }
        semantic.pending_audit_tasks.append(task)
        if len(semantic.pending_audit_tasks) > 200:
            semantic.pending_audit_tasks = semantic.pending_audit_tasks[-200:]
        self.logger.info(
            f"[{semantic.student_id}] Added audit task: {task_type} "
            f"(session={episode.session_id}, solution={episode.solution_id})"
        )

    def _update_counters(self, semantic: SemanticMemory, episode: EpisodicMemory) -> None:
        semantic.total_sessions += 1
        semantic.total_hints_given += episode.hints_given
        if episode.outcome == "solved":
            semantic.total_problems_solved += 1
        if semantic.total_sessions > 0:
            semantic.avg_hints_per_session = (
                semantic.total_hints_given / semantic.total_sessions
            )

    def _resolve_replay_concepts(
        self,
        *,
        task: dict[str, Any],
        episode: EpisodicMemory,
    ) -> list[str]:
        """从当前知识层为旧任务补出 concept 绑定。"""
        concept_ids: list[str] = []

        solution_id = episode.solution_id or task.get("solution_id")
        if solution_id:
            published = self._solution_link_store.get(solution_id=solution_id)
            if published:
                for concept_id in list(published.get("concept_ids", []) or []):
                    if concept_id and concept_id not in concept_ids:
                        concept_ids.append(concept_id)

        for concept_id in list(episode.solution_tags or []) + list(task.get("solution_tags", []) or []):
            if concept_id and concept_id not in concept_ids:
                concept_ids.append(concept_id)

        slot_id = episode.method_slot_matched or task.get("method_slot_matched")
        if slot_id:
            for node in self._concept_registry.find_by_slot(slot_id):
                if node.concept_id not in concept_ids:
                    concept_ids.append(node.concept_id)

        return concept_ids


# =============================================================================
# Helpers
# =============================================================================

def _extract_methods(export: dict[str, Any]) -> list[str]:
    """从 tutor export 提取使用的方法列表"""
    methods = []
    alt = export.get("alternative_method")
    if alt:
        methods.append(alt)
    elif export.get("used_alternative_method"):
        methods.append("替代方法（未命名）")
    else:
        methods.append("标准方法")
    return methods


def _stable_memory_id(student_id: str, source: SessionSource, session_id: str) -> str:
    """
    为同一 (student_id, source, session_id) 生成稳定 memory_id，用于幂等写入。
    session_id 缺失时回退到随机 UUID。
    """
    if not session_id:
        return str(uuid.uuid4())
    key = f"{student_id}:{source.value}:{session_id}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def _normalize_outcome(raw: Any) -> str:
    if isinstance(raw, str):
        value = raw.strip().lower()
    else:
        value = ""
    mapping = {
        "active": "in_progress",
        "abandoned": "gave_up",
        "closed": "explored",
    }
    value = mapping.get(value, value)
    allowed = {"solved", "gave_up", "in_progress", "explored"}
    return value if value in allowed else "in_progress"
