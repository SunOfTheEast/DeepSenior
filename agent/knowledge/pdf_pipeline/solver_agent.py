#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SolverAgent — 批量求解题目 + 自动绑卡

使用 function calling 在求解过程中检索知识卡片，
解题的副产品就是题目-卡片绑定关系。

流程：
  1. 拿到题目 stem + 同章卡片列表
  2. LLM 用 search_knowledge tool 检索方法卡片
  3. 按卡片方法步骤求解
  4. 从 tool_calls 日志提取绑定的 card_ids
  5. 要求"换一种方法再解"→ 枚举多条解法路径
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agent.base_agent import BaseAgent
from agent.infra.logging import get_logger
from agent.infra.llm import complete_with_tools, ToolUseResponse

_logger = get_logger("Knowledge.SolverAgent")


@dataclass
class SolutionPath:
    """One solution path for a question."""
    method: str
    card_ids: list[str]
    key_steps: list[str]
    solution_text: str = ""


@dataclass
class SolveResult:
    """Complete solve result for a question."""
    question_id: str
    solution_paths: list[SolutionPath] = field(default_factory=list)
    all_card_ids: list[str] = field(default_factory=list)  # union of all paths


# ─── Tool schema for the solver ───

_SOLVER_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search_knowledge",
        "description": (
            "检索知识卡片。输入关键词搜索相关方法卡片，返回卡片的方法步骤和提示。"
            "解题时请主动使用此工具查找可能适用的方法。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词，如'十字相乘法'、'轮换对称式分解'",
                },
            },
            "required": ["query"],
        },
    },
}


class SolverAgent(BaseAgent):
    """Solve questions using knowledge card RAG, producing card bindings as byproduct."""

    _MAX_TOOL_ROUNDS = 8

    def __init__(self, card_index: Any, card_store: Any, **kwargs):
        super().__init__(
            module_name="knowledge",
            agent_name="solver_agent",
            **kwargs,
        )
        self._card_index = card_index  # EmbeddingCardIndex or SimpleCardIndex
        self._card_store = card_store  # FileCardStore or similar
        prompt_path = Path(__file__).parent / "prompts" / "zh" / "solver_agent.yaml"
        with open(prompt_path, "r", encoding="utf-8") as f:
            self._prompts = yaml.safe_load(f)

    async def process(self, *args, **kwargs):
        """Entry point conforming to BaseAgent interface."""
        return await self.solve(*args, **kwargs)

    async def solve(
        self,
        question_id: str,
        stem: str,
        chapter: str = "",
        existing_solution: str = "",
    ) -> SolveResult:
        """Solve a question, returning solution paths with card bindings.

        Args:
            question_id: Question ID
            stem: Problem text
            chapter: Chapter name for scoping search
            existing_solution: If archetype, the known solution (skip solving, just bind)
        """
        if existing_solution:
            return await self._bind_from_solution(
                question_id, stem, existing_solution, chapter,
            )
        return await self._solve_with_tools(question_id, stem, chapter)

    async def _solve_with_tools(
        self,
        question_id: str,
        stem: str,
        chapter: str,
    ) -> SolveResult:
        """Solve using LLM + search_knowledge tool calls."""
        system_prompt = self._prompts["system_solve"]
        user_prompt = self._prompts["user_solve"].format(
            stem=stem,
            chapter=chapter,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        tools = [_SOLVER_SEARCH_SCHEMA]

        all_card_ids: list[str] = []
        tool_call_log: list[dict] = []

        # Tool calling loop (with thinking mode for DeepSeek V3.2+)
        thinking_kwargs = {"thinking": {"type": "enabled"}}
        for _round in range(self._MAX_TOOL_ROUNDS):
            resp: ToolUseResponse = await complete_with_tools(
                prompt="",
                messages=messages,
                tools=tools,
                tool_choice="auto",
                model=self.model,
                api_key=self.api_key,
                base_url=self.base_url,
                **thinking_kwargs,
            )

            if not resp.has_tool_calls:
                # LLM finished solving, extract result
                return self._parse_solve_response(
                    question_id, resp.content, all_card_ids,
                )

            # Process tool calls — pass back reasoning_content for thinking continuity
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": resp.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in resp.tool_calls
                ],
            }
            if resp.reasoning_content:
                assistant_msg["reasoning_content"] = resp.reasoning_content
            messages.append(assistant_msg)

            for tc in resp.tool_calls:
                query = tc.arguments.get("query", "")
                tool_result = self._exec_search(query, chapter)
                # Track which cards were retrieved
                for card in json.loads(tool_result).get("cards", []):
                    cid = card.get("card_id", "")
                    if cid and cid not in all_card_ids:
                        all_card_ids.append(cid)

                tool_call_log.append({
                    "query": query,
                    "card_ids": [c.get("card_id") for c in json.loads(tool_result).get("cards", [])],
                })

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result,
                })

        # Max rounds reached, use what we have
        _logger.warning("Max tool rounds reached for %s", question_id)
        return SolveResult(
            question_id=question_id,
            all_card_ids=all_card_ids,
        )

    async def _bind_from_solution(
        self,
        question_id: str,
        stem: str,
        solution_text: str,
        chapter: str,
    ) -> SolveResult:
        """For archetypes: bind cards from existing solution without re-solving."""
        system_prompt = self._prompts["system_bind"]
        user_prompt = self._prompts["user_bind"].format(
            stem=stem,
            solution=solution_text,
            chapter=chapter,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        tools = [_SOLVER_SEARCH_SCHEMA]

        all_card_ids: list[str] = []

        thinking_kwargs = {"thinking": {"type": "enabled"}}
        for _round in range(self._MAX_TOOL_ROUNDS):
            resp = await complete_with_tools(
                prompt="",
                messages=messages,
                tools=tools,
                tool_choice="auto",
                model=self.model,
                api_key=self.api_key,
                base_url=self.base_url,
                **thinking_kwargs,
            )

            if not resp.has_tool_calls:
                return self._parse_solve_response(
                    question_id, resp.content, all_card_ids,
                )

            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": resp.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in resp.tool_calls
                ],
            }
            if resp.reasoning_content:
                assistant_msg["reasoning_content"] = resp.reasoning_content
            messages.append(assistant_msg)

            for tc in resp.tool_calls:
                query = tc.arguments.get("query", "")
                tool_result = self._exec_search(query, chapter)
                for card in json.loads(tool_result).get("cards", []):
                    cid = card.get("card_id", "")
                    if cid and cid not in all_card_ids:
                        all_card_ids.append(cid)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result,
                })

        return SolveResult(question_id=question_id, all_card_ids=all_card_ids)

    def _exec_search(self, query: str, chapter: str) -> str:
        """Execute search_knowledge tool locally."""
        hits = self._card_index.search(
            query_text=query,
            chapter=chapter if chapter else None,
            top_k=3,
        )

        cards = []
        for card_id, score in hits:
            card = None
            if hasattr(self._card_store, "get_card_sync"):
                card = self._card_store.get_card_sync(card_id)
            if card is None:
                continue

            hints = card.hints
            if isinstance(hints, dict):
                hints_list = [hints[k] for k in sorted(hints)]
            else:
                hints_list = list(hints) if hints else []

            cards.append({
                "card_id": card.card_id,
                "title": card.title,
                "summary": card.summary,
                "general_methods": list(card.general_methods)[:5],
                "hints": hints_list,
                "formula_cues": list(card.formula_cues)[:3],
                "score": round(score, 3),
            })

        return json.dumps({"cards": cards}, ensure_ascii=False)

    def _parse_solve_response(
        self,
        question_id: str,
        content: str,
        tool_card_ids: list[str],
    ) -> SolveResult:
        """Parse LLM's final response into SolveResult."""
        paths: list[SolutionPath] = []

        try:
            # Try to extract JSON from response
            text = content.strip()
            fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
            if fence_match:
                text = fence_match.group(1).strip()

            data = json.loads(text)
            raw_paths = data if isinstance(data, list) else data.get("solution_paths", [])

            for p in raw_paths:
                paths.append(SolutionPath(
                    method=p.get("method", ""),
                    card_ids=p.get("card_ids", []),
                    key_steps=p.get("key_steps", []),
                    solution_text=p.get("solution_text", ""),
                ))
        except (json.JSONDecodeError, TypeError):
            # Fallback: use tool call log as binding
            if tool_card_ids:
                paths.append(SolutionPath(
                    method="auto",
                    card_ids=tool_card_ids,
                    key_steps=[],
                    solution_text=content,
                ))

        # Collect all card_ids across paths + tool calls
        all_ids = list(tool_card_ids)
        for p in paths:
            for cid in p.card_ids:
                if cid not in all_ids:
                    all_ids.append(cid)

        return SolveResult(
            question_id=question_id,
            solution_paths=paths,
            all_card_ids=all_ids,
        )
