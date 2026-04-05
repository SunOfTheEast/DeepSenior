#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
RecommendToolRegistry — 推荐系统 Tool-Use 注册中心

为 RecommendAgent 提供 9 个 tools，覆盖三个维度：
  - 学生画像：get_student_profile, get_concept_mastery, get_concept_history
  - 知识图谱：get_bottlenecks, get_ready_to_learn, get_chapter_mastery
  - 题目空间：get_problem_profile, find_similar_problems, find_problems_by_tag

复用 TutorToolRegistry 的 ToolDefinition 模式。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from agent.infra.logging import get_logger
from agent.memory.memory_manager import MemoryManager

logger = get_logger("Recommend.ToolRegistry")


@dataclass
class ToolDefinition:
    schema: dict[str, Any]
    executor: Callable[..., Awaitable[str]]


# =============================================================================
# Tool JSON Schemas (OpenAI format)
# =============================================================================

_GET_STUDENT_PROFILE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_student_profile",
        "description": "获取学生整体画像摘要：擅长方法、薄弱知识点、常见错误模式、学习偏好。",
        "parameters": {"type": "object", "properties": {}},
    },
}

_GET_CONCEPT_MASTERY_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_concept_mastery",
        "description": "查询指定知识点的掌握度（含 Ebbinghaus 衰减和图推断）。",
        "parameters": {
            "type": "object",
            "properties": {
                "concept_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要查询的 concept_id 列表",
                },
            },
            "required": ["concept_ids"],
        },
    },
}

_GET_BOTTLENECKS_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_bottlenecks",
        "description": "查找学生的知识瓶颈：掌握度低且阻塞多个下游概念的节点。",
        "parameters": {
            "type": "object",
            "properties": {
                "threshold": {
                    "type": "number",
                    "description": "掌握度阈值，低于此值视为薄弱，默认 0.5",
                },
            },
        },
    },
}

_GET_READY_TO_LEARN_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_ready_to_learn",
        "description": "查找学生可以学习的新概念：前置知识已达标但自身尚未掌握。",
        "parameters": {
            "type": "object",
            "properties": {
                "threshold": {
                    "type": "number",
                    "description": "前置达标阈值，默认 0.6",
                },
            },
        },
    },
}

_GET_CHAPTER_MASTERY_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_chapter_mastery",
        "description": "查看某章节所有概念的掌握度全景。",
        "parameters": {
            "type": "object",
            "properties": {
                "chapter": {
                    "type": "string",
                    "description": "章节名称，如'解析几何'",
                },
            },
            "required": ["chapter"],
        },
    },
}

_GET_CONCEPT_HISTORY_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_concept_history",
        "description": "查看学生在某个知识点上的历史会话记录（做过几次、结果如何、用了什么方法）。",
        "parameters": {
            "type": "object",
            "properties": {
                "concept_id": {
                    "type": "string",
                    "description": "知识点 concept_id",
                },
                "limit": {
                    "type": "integer",
                    "description": "最多返回条数，默认 5",
                },
            },
            "required": ["concept_id"],
        },
    },
}

_GET_PROBLEM_PROFILE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_problem_profile",
        "description": "查看题目的详细特质：解法结构（多少种方法）、绑定的知识卡片、难度。",
        "parameters": {
            "type": "object",
            "properties": {
                "problem_id": {
                    "type": "string",
                    "description": "题目 ID",
                },
            },
            "required": ["problem_id"],
        },
    },
}

_FIND_SIMILAR_PROBLEMS_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "find_similar_problems",
        "description": (
            "搜索与指定题目语义相似的题，或用自然语言描述搜索。\n"
            "两种模式：\n"
            "1. 提供 problem_id → 找与该题结构/方法相似的题\n"
            "2. 提供 query → 用自然语言搜索（如'需要分类讨论的椭圆题'）"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "problem_id": {
                    "type": "string",
                    "description": "基准题目 ID（相似度搜索模式）",
                },
                "query": {
                    "type": "string",
                    "description": "自然语言搜索查询（文本搜索模式）",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回数量，默认 5",
                },
            },
        },
    },
}

_FIND_PROBLEMS_BY_TAG_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "find_problems_by_tag",
        "description": "按知识点标签和难度搜索题库中的题目。",
        "parameters": {
            "type": "object",
            "properties": {
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "知识点标签列表",
                },
                "difficulty": {
                    "type": "string",
                    "enum": ["基础", "中等", "进阶"],
                    "description": "难度等级",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回数量，默认 5",
                },
            },
            "required": ["tags"],
        },
    },
}


# =============================================================================
# Registry
# =============================================================================

class RecommendToolRegistry:
    """
    推荐系统 tool-use 注册中心。

    每次推荐创建一个实例（绑定 student_id + 当前会话上下文）。
    """

    def __init__(
        self,
        student_id: str,
        memory_manager: MemoryManager,
        problem_bank: Any,  # DraftQuestionBank or ProblemBankBase
        current_problem_id: str = "",
        current_tags: list[str] | None = None,
        current_chapter: str = "",
    ):
        self._student_id = student_id
        self._memory = memory_manager
        self._bank = problem_bank
        self._current_problem_id = current_problem_id
        self._current_tags = current_tags or []
        self._current_chapter = current_chapter

        # 缓存 mastery view（同一次推荐中多次查询复用）
        self._mastery_view = None

        self._tools: dict[str, ToolDefinition] = {
            "get_student_profile": ToolDefinition(
                schema=_GET_STUDENT_PROFILE_SCHEMA,
                executor=self._exec_get_student_profile,
            ),
            "get_concept_mastery": ToolDefinition(
                schema=_GET_CONCEPT_MASTERY_SCHEMA,
                executor=self._exec_get_concept_mastery,
            ),
            "get_bottlenecks": ToolDefinition(
                schema=_GET_BOTTLENECKS_SCHEMA,
                executor=self._exec_get_bottlenecks,
            ),
            "get_ready_to_learn": ToolDefinition(
                schema=_GET_READY_TO_LEARN_SCHEMA,
                executor=self._exec_get_ready_to_learn,
            ),
            "get_chapter_mastery": ToolDefinition(
                schema=_GET_CHAPTER_MASTERY_SCHEMA,
                executor=self._exec_get_chapter_mastery,
            ),
            "get_concept_history": ToolDefinition(
                schema=_GET_CONCEPT_HISTORY_SCHEMA,
                executor=self._exec_get_concept_history,
            ),
            "get_problem_profile": ToolDefinition(
                schema=_GET_PROBLEM_PROFILE_SCHEMA,
                executor=self._exec_get_problem_profile,
            ),
            "find_similar_problems": ToolDefinition(
                schema=_FIND_SIMILAR_PROBLEMS_SCHEMA,
                executor=self._exec_find_similar_problems,
            ),
            "find_problems_by_tag": ToolDefinition(
                schema=_FIND_PROBLEMS_BY_TAG_SCHEMA,
                executor=self._exec_find_problems_by_tag,
            ),
        }

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return [td.schema for td in self._tools.values()]

    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        td = self._tools.get(name)
        if td is None:
            logger.warning(f"Unknown recommend tool: {name}")
            return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
        try:
            return await td.executor(arguments)
        except Exception as exc:
            logger.error(f"Recommend tool '{name}' failed: {exc}")
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

    # =========================================================================
    # Tool Executors
    # =========================================================================

    def _get_mastery_view(self):
        if self._mastery_view is None:
            self._mastery_view = self._memory.get_mastery_view(self._student_id)
        return self._mastery_view

    async def _exec_get_student_profile(self, args: dict[str, Any]) -> str:
        semantic = self._memory.get_semantic(self._student_id)
        if semantic is None:
            return json.dumps({"profile": "新学生，暂无画像数据。"}, ensure_ascii=False)
        snapshot = semantic.to_recommend_snapshot(self._current_tags)
        return json.dumps({"profile": snapshot}, ensure_ascii=False)

    async def _exec_get_concept_mastery(self, args: dict[str, Any]) -> str:
        concept_ids = args.get("concept_ids", [])
        view = self._get_mastery_view()
        results: list[dict] = []
        for cid in concept_ids[:10]:
            entry: dict[str, Any] = {"concept_id": cid}
            if view:
                em = view.effective_mastery(cid)
                dm = view.direct_mastery(cid)
                entry["effective_mastery"] = round(em, 3) if em is not None else None
                entry["direct_mastery"] = round(dm, 3) if dm is not None else None
            else:
                entry["effective_mastery"] = None
                entry["direct_mastery"] = None
            node = self._memory._concept_registry.get(cid)
            if node:
                entry["name"] = node.name
                entry["difficulty"] = node.difficulty
            results.append(entry)
        return json.dumps({"concepts": results}, ensure_ascii=False)

    async def _exec_get_bottlenecks(self, args: dict[str, Any]) -> str:
        threshold = args.get("threshold", 0.5)
        view = self._get_mastery_view()
        if view is None:
            return json.dumps({"bottlenecks": [], "note": "新学生，无掌握度数据"}, ensure_ascii=False)
        bottleneck_ids = view.bottlenecks(threshold)[:8]
        results = []
        for cid in bottleneck_ids:
            node = self._memory._concept_registry.get(cid)
            em = view.effective_mastery(cid)
            blocked = view._count_blocked_descendants(cid)
            results.append({
                "concept_id": cid,
                "name": node.name if node else cid,
                "mastery": round(em, 3) if em is not None else None,
                "blocked_count": blocked,
            })
        return json.dumps({"bottlenecks": results}, ensure_ascii=False)

    async def _exec_get_ready_to_learn(self, args: dict[str, Any]) -> str:
        threshold = args.get("threshold", 0.6)
        view = self._get_mastery_view()
        if view is None:
            return json.dumps({"ready": [], "note": "新学生，无掌握度数据"}, ensure_ascii=False)
        ready_ids = view.ready_to_learn(threshold)[:8]
        results = []
        for cid in ready_ids:
            node = self._memory._concept_registry.get(cid)
            results.append({
                "concept_id": cid,
                "name": node.name if node else cid,
                "difficulty": node.difficulty if node else None,
            })
        return json.dumps({"ready": results}, ensure_ascii=False)

    async def _exec_get_chapter_mastery(self, args: dict[str, Any]) -> str:
        chapter = args.get("chapter", "")
        view = self._get_mastery_view()
        if view is None:
            return json.dumps({"chapter": chapter, "concepts": {}, "note": "新学生"}, ensure_ascii=False)
        mastery_map = view.chapter_mastery(chapter)
        results = {}
        for cid, em in mastery_map.items():
            node = self._memory._concept_registry.get(cid)
            results[cid] = {
                "name": node.name if node else cid,
                "mastery": em,
            }
        return json.dumps({"chapter": chapter, "concepts": results}, ensure_ascii=False)

    async def _exec_get_concept_history(self, args: dict[str, Any]) -> str:
        concept_id = args.get("concept_id", "")
        limit = args.get("limit", 5)
        episodes = self._memory.query_episodes(
            self._student_id,
            concept_ids=[concept_id],
            limit=limit,
            load_full=True,
        )
        results = []
        for ep in episodes:
            results.append({
                "date": ep.created_at.strftime("%Y-%m-%d"),
                "chapter": ep.chapter,
                "outcome": ep.outcome,
                "hints_given": ep.hints_given,
                "methods_used": ep.methods_used,
                "narrative": (getattr(ep, "session_narrative", "") or "")[:100],
            })
        return json.dumps({"concept_id": concept_id, "history": results}, ensure_ascii=False)

    async def _exec_get_problem_profile(self, args: dict[str, Any]) -> str:
        problem_id = args.get("problem_id", "")
        problem = await self._bank.get_by_id(problem_id)
        if problem is None:
            return json.dumps({"error": f"题目 {problem_id} 不存在"}, ensure_ascii=False)
        paths = []
        for sp in (problem.solution_paths or []):
            paths.append({
                "method": sp.get("method", ""),
                "card_ids": sp.get("card_ids", [])[:5],
                "key_steps_count": len(sp.get("key_steps", [])),
            })
        return json.dumps({
            "problem_id": problem_id,
            "chapter": problem.chapter,
            "difficulty": problem.difficulty,
            "tags": problem.tags[:10],
            "solution_paths": paths,
            "bound_card_count": len(problem.bound_card_ids),
            "stem_preview": problem.problem[:150],
        }, ensure_ascii=False)

    async def _exec_find_similar_problems(self, args: dict[str, Any]) -> str:
        problem_id = args.get("problem_id")
        query = args.get("query")
        top_k = args.get("top_k", 5)
        exclude_ids = [self._current_problem_id] if self._current_problem_id else []

        problems = []
        if problem_id and hasattr(self._bank, "find_similar"):
            problems = await self._bank.find_similar(problem_id, top_k, exclude_ids)
        elif query and hasattr(self._bank, "search_by_text"):
            problems = await self._bank.search_by_text(query, top_k, exclude_ids)

        results = []
        for p in problems:
            results.append({
                "problem_id": p.problem_id,
                "chapter": p.chapter,
                "difficulty": p.difficulty,
                "tags": p.tags[:5],
                "stem_preview": p.problem[:120],
            })
        if not results:
            return json.dumps({"problems": [], "note": "未找到相似题目"}, ensure_ascii=False)
        return json.dumps({"problems": results}, ensure_ascii=False)

    async def _exec_find_problems_by_tag(self, args: dict[str, Any]) -> str:
        from ..data_structures import ProblemQuery
        tags = args.get("tags", [])
        difficulty = args.get("difficulty")
        limit = args.get("limit", 5)
        exclude_ids = [self._current_problem_id] if self._current_problem_id else []

        query = ProblemQuery(
            tags=tags,
            tag_mode="any",
            chapter=None,
            difficulty=difficulty,
            exclude_ids=exclude_ids,
            limit=limit,
        )
        problems = await self._bank.query(query)
        results = []
        for p in problems:
            results.append({
                "problem_id": p.problem_id,
                "chapter": p.chapter,
                "difficulty": p.difficulty,
                "tags": p.tags[:5],
                "stem_preview": p.problem[:120],
            })
        return json.dumps({"problems": results}, ensure_ascii=False)
