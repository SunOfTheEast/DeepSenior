#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
TutorToolRegistry — LLM function calling tool 定义与执行

提供 OpenAI 格式的 tool schema + 对应的 executor。
当前只注册 search_knowledge，后续 tool 在此扩展。

设计原则：
  - tool 执行是纯内存操作（L1 查找 + 概念树遍历），不涉及 LLM 调用
  - 返回值是 JSON 字符串，直接作为 tool role message content
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from agent.infra.logging import get_logger

if TYPE_CHECKING:
    from agent.knowledge.concept_registry import ConceptRegistry
    from agent.tutor.data_structures import ProblemContext

logger = get_logger("Tutor.ToolRegistry")

# ─── search_knowledge schema ───

_SEARCH_KNOWLEDGE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search_knowledge",
        "description": (
            "知识卡片检索。两种模式：\n"
            "1. 展开模式：提供 card_ids，返回卡片完整内容（方法、提示、易错点）\n"
            "2. 搜索模式：提供 query 关键词，搜索相关卡片并返回摘要列表"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "card_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要展开的卡片 ID（从知识索引中选取），最多 3 张",
                },
                "query": {
                    "type": "string",
                    "description": "搜索关键词，如'十字相乘法'、'齐次式分解'。提供此参数时进入搜索模式",
                },
                "include_prerequisites": {
                    "type": "boolean",
                    "description": "是否包含前置知识卡片",
                },
            },
        },
    },
}


_GET_SIMILAR_PROBLEM_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_similar_problem",
        "description": (
            "查找相似练习题。根据当前题目的知识点和方法，推荐同类型或相关的练习题。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要匹配的知识点或方法标签，如['十字相乘法', '二次三项式']",
                },
                "difficulty": {
                    "type": "string",
                    "enum": ["基础", "中等", "进阶"],
                    "description": "期望难度",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回题目数量，默认3",
                },
            },
            "required": ["tags"],
        },
    },
}


@dataclass
class ToolDefinition:
    """A tool's JSON schema + its async executor."""
    schema: dict[str, Any]
    executor: Callable[..., Awaitable[str]]


class TutorToolRegistry:
    """Manages LLM-callable tools for the tutor Act step."""

    _MAX_CARD_IDS = 3
    _MAX_PREREQ_CARDS = 2
    _MAX_SEARCH_RESULTS = 5

    def __init__(
        self,
        problem_context: ProblemContext,
        concept_registry: ConceptRegistry,
        card_store: Any,
        card_index: Any | None = None,
        problem_bank: Any | None = None,
    ):
        self._pc = problem_context
        self._concepts = concept_registry
        self._card_store = card_store
        self._card_index = card_index  # SimpleCardIndex for query search
        self._problem_bank = problem_bank  # ProblemBankBase for similar problems

        self._tools: dict[str, ToolDefinition] = {
            "search_knowledge": ToolDefinition(
                schema=_SEARCH_KNOWLEDGE_SCHEMA,
                executor=self._exec_search_knowledge,
            ),
        }
        if self._problem_bank is not None:
            self._tools["get_similar_problem"] = ToolDefinition(
                schema=_GET_SIMILAR_PROBLEM_SCHEMA,
                executor=self._exec_get_similar_problem,
            )

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Return OpenAI-format tool list for LLM."""
        return [td.schema for td in self._tools.values()]

    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool call. Returns JSON string for tool role message."""
        td = self._tools.get(name)
        if td is None:
            logger.warning(f"Unknown tool: {name}")
            return json.dumps({"error": f"Unknown tool: {name}"}, ensure_ascii=False)
        try:
            return await td.executor(arguments)
        except Exception as exc:
            logger.error(f"Tool '{name}' execution failed: {exc}")
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

    # ─── search_knowledge executor ───

    async def _exec_search_knowledge(self, args: dict[str, Any]) -> str:
        query: str | None = args.get("query")
        card_ids: list[str] = args.get("card_ids", [])[:self._MAX_CARD_IDS]
        include_prereqs: bool = args.get("include_prerequisites", False)

        # Mode 2: search by query → return L0 summaries
        if query and not card_ids:
            search_results = self._search_cards(query)
            return json.dumps(
                {"search_results": search_results, "hint": "用 card_ids 展开感兴趣的卡片"},
                ensure_ascii=False,
            )

        # Mode 1: expand by card_ids → return full content
        results: list[dict[str, Any]] = []
        for card_id in card_ids:
            card_data = self._expand_card(card_id)
            if card_data:
                results.append(card_data)

        # prerequisite expansion
        prereq_results: list[dict[str, Any]] = []
        if include_prereqs:
            prereq_card_ids = self._collect_prerequisite_card_ids(card_ids)
            for pid in prereq_card_ids[:self._MAX_PREREQ_CARDS]:
                if pid not in card_ids:
                    card_data = self._expand_card(pid)
                    if card_data:
                        card_data["is_prerequisite"] = True
                        prereq_results.append(card_data)

        return json.dumps(
            {"cards": results, "prerequisites": prereq_results},
            ensure_ascii=False,
        )

    def _search_cards(self, query: str) -> list[dict[str, Any]]:
        """Search cards by query text. Returns L0-style summaries."""
        results: list[dict[str, Any]] = []

        # Path 1: SimpleCardIndex text search
        if self._card_index is not None:
            hits = self._card_index.search(
                query_text=query,
                top_k=self._MAX_SEARCH_RESULTS,
            )
            for card_id, score in hits:
                card = None
                if hasattr(self._card_store, "get_card_sync"):
                    card = self._card_store.get_card_sync(card_id)
                if card:
                    results.append({
                        "card_id": card.card_id,
                        "title": card.title,
                        "summary": card.summary,
                        "score": round(score, 2),
                    })

        # Path 2: check L0 menu for matches (always available)
        if not results:
            query_lower = query.lower()
            for summary in self._pc.published_card_menu:
                if (query_lower in summary.title.lower()
                        or query_lower in summary.summary.lower()):
                    results.append({
                        "card_id": summary.card_id,
                        "title": summary.title,
                        "summary": summary.summary,
                        "score": 0.5,
                    })
                    if len(results) >= self._MAX_SEARCH_RESULTS:
                        break

        return results[:self._MAX_SEARCH_RESULTS]

    def _expand_card(self, card_id: str) -> dict[str, Any] | None:
        """Expand a single card to dict. Checks L1 cache first, then card_store."""
        card = self._pc.published_card_full.get(card_id)
        if card is None and hasattr(self._card_store, "get_card_sync"):
            card = self._card_store.get_card_sync(card_id)
        if card is None:
            return None

        # Bridge hints: dict[int,str] (Published) vs list[str] (Legacy)
        hints = card.hints
        if isinstance(hints, dict):
            hints_list = [hints[k] for k in sorted(hints)]
        else:
            hints_list = list(hints) if hints else []

        return {
            "card_id": card.card_id,
            "title": card.title,
            "general_methods": list(card.general_methods) if card.general_methods else [],
            "hints": hints_list,
            "common_mistakes": list(card.common_mistakes) if card.common_mistakes else [],
        }

    def _collect_prerequisite_card_ids(self, card_ids: list[str]) -> list[str]:
        """Use ConceptRegistry to find prerequisite cards."""
        prereq_card_ids: list[str] = []
        seen: set[str] = set(card_ids)

        for card_id in card_ids:
            concepts = self._concepts.find_by_card(card_id)
            for concept in concepts:
                prereqs = self._concepts.get_prerequisites(concept.concept_id)
                for prereq in prereqs:
                    for rcid in prereq.related_card_ids:
                        if rcid not in seen:
                            seen.add(rcid)
                            prereq_card_ids.append(rcid)

        return prereq_card_ids

    # ─── get_similar_problem executor ───

    async def _exec_get_similar_problem(self, args: dict[str, Any]) -> str:
        tags: list[str] = args.get("tags", [])
        difficulty: str | None = args.get("difficulty")
        limit: int = args.get("limit", 3)

        if not tags:
            # Fallback: use current problem's tags
            tags = list(self._pc.tags)

        from agent.recommend.data_structures import ProblemQuery
        query = ProblemQuery(
            tags=tags,
            tag_mode="any",
            chapter=self._pc.chapter or None,
            difficulty=difficulty,
            exclude_ids=[self._pc.problem_id] if self._pc.problem_id else [],
            limit=limit,
        )
        problems = await self._problem_bank.query(query)

        results = []
        for p in problems:
            results.append({
                "problem_id": p.problem_id,
                "stem": p.problem[:200],
                "difficulty": p.difficulty,
                "chapter": p.chapter,
                "tags": p.tags[:5],
            })

        return json.dumps(
            {"problems": results, "total": len(results)},
            ensure_ascii=False,
        )
