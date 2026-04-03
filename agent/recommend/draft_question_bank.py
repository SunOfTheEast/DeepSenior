#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DraftQuestionBank — ProblemBankBase 实现，从 DraftStore 加载题目。

将 DraftQuestion（管线产出）适配为 ProblemContext（Tutor 消费），
支持按 tags/chapter/difficulty 查询和推荐。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.infra.logging import get_logger
from agent.tutor.data_structures import ProblemContext

from .data_structures import ProblemQuery
from .problem_bank import ProblemBankBase

logger = get_logger("Recommend.DraftQuestionBank")


class DraftQuestionBank(ProblemBankBase):
    """ProblemBank backed by DraftQuestion YAML files from the PDF pipeline."""

    def __init__(self, drafts_root: str | Path | None = None):
        self._drafts_root = Path(drafts_root) if drafts_root else Path("content/drafts")
        self._questions: list[dict] = []
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        import yaml
        for book_dir in self._drafts_root.iterdir():
            questions_dir = book_dir / "questions"
            if not questions_dir.exists():
                continue
            for path in sorted(questions_dir.rglob("*.yaml")):
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if data and data.get("question_id"):
                    data["_book"] = book_dir.name
                    self._questions.append(data)
        self._loaded = True
        logger.info("Loaded %d draft questions from %s", len(self._questions), self._drafts_root)

    async def query(self, query: ProblemQuery) -> list[ProblemContext]:
        self._ensure_loaded()

        candidates = self._questions
        # Filter by chapter
        if query.chapter:
            candidates = [q for q in candidates if q.get("chapter") == query.chapter]
        # Filter by difficulty
        if query.difficulty:
            diff_map = {"基础": (1, 2), "中等": (3,), "进阶": (4, 5)}
            allowed = diff_map.get(query.difficulty, ())
            if allowed:
                candidates = [q for q in candidates if q.get("difficulty", 0) in allowed]
        # Exclude already done
        if query.exclude_ids:
            exclude_set = set(query.exclude_ids)
            candidates = [q for q in candidates if q["question_id"] not in exclude_set]
        # Tag matching: check tags fields first, fallback to stem text search
        if query.tags:
            query_tags = set(query.tags)
            scored = []
            for q in candidates:
                # Check structured tags if available
                q_tags = set(q.get("problem_tags", []) + q.get("method_tags", []))
                tag_overlap = len(query_tags & q_tags)
                # Fallback: check if any query tag appears in stem text
                stem = q.get("stem", "")
                text_hits = sum(1 for t in query_tags if t in stem)
                score = tag_overlap * 2 + text_hits  # structured tags weighted higher
                if query.tag_mode == "all" and tag_overlap < len(query_tags) and text_hits == 0:
                    continue
                if query.tag_mode == "any" and score == 0:
                    continue
                scored.append((score, q))
            scored.sort(key=lambda x: -x[0])
            candidates = [q for _, q in scored]

        return [self._to_problem_context(q) for q in candidates[:query.limit]]

    async def get_by_id(self, problem_id: str) -> ProblemContext | None:
        self._ensure_loaded()
        for q in self._questions:
            if q["question_id"] == problem_id:
                return self._to_problem_context(q)
        return None

    async def get_prerequisites(self, concept_id: str) -> list[ProblemContext]:
        # Simplified: return exercises that require this concept
        self._ensure_loaded()
        results = []
        for q in self._questions:
            if q.get("question_type") != "exercise":
                continue
            # Match by chapter (rough proxy for concept domain)
            if concept_id in q.get("chapter", ""):
                results.append(self._to_problem_context(q))
            if len(results) >= 3:
                break
        return results

    @staticmethod
    def _to_problem_context(q: dict) -> ProblemContext:
        """Convert DraftQuestion dict to ProblemContext."""
        return ProblemContext(
            problem_id=q["question_id"],
            problem=q.get("stem", ""),
            answer=q.get("solution_text", ""),
            knowledge_cards=[],
            difficulty=q.get("difficulty", 0),
            chapter=q.get("chapter", ""),
            tags=q.get("problem_tags", []) + q.get("method_tags", []),
        )
