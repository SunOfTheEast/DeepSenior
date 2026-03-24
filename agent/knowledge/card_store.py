#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Card store skeleton for RAG v2."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import yaml

from agent.infra.logging import get_logger

from .data_structures import (
    CandidateCardSummary,
    PublishedKnowledgeCard,
    PublishedQuestion,
    PublishedSolution,
    QuestionCardLink,
    SolutionCardLink,
)


class CardStoreBase(ABC):
    """Online read-only entry for published question/card/solution data."""

    @abstractmethod
    async def get_question(self, question_id: str) -> PublishedQuestion | None:
        ...

    @abstractmethod
    async def get_solution(self, solution_id: str) -> PublishedSolution | None:
        ...

    @abstractmethod
    async def get_card(self, card_id: str) -> PublishedKnowledgeCard | None:
        ...

    @abstractmethod
    async def list_question_cards(
        self,
        question_id: str,
        *,
        relation: str | None = None,
    ) -> list[QuestionCardLink]:
        ...

    @abstractmethod
    async def list_solution_cards(
        self,
        solution_id: str,
        *,
        relation: str | None = None,
    ) -> list[SolutionCardLink]:
        ...

    @abstractmethod
    async def list_card_primary_concepts(
        self,
        card_id: str,
        *,
        layer: str,
    ) -> list[str]:
        ...

    @abstractmethod
    async def get_card_summaries(
        self,
        card_ids: list[str],
        *,
        source_slot_id: str | None = None,
    ) -> list[CandidateCardSummary]:
        ...


class NullCardStore(CardStoreBase):
    """Conservative placeholder that always returns empty values."""

    async def get_question(self, question_id: str) -> PublishedQuestion | None:
        return None

    async def get_solution(self, solution_id: str) -> PublishedSolution | None:
        return None

    async def get_card(self, card_id: str) -> PublishedKnowledgeCard | None:
        return None

    async def list_question_cards(
        self,
        question_id: str,
        *,
        relation: str | None = None,
    ) -> list[QuestionCardLink]:
        return []

    async def list_solution_cards(
        self,
        solution_id: str,
        *,
        relation: str | None = None,
    ) -> list[SolutionCardLink]:
        return []

    async def list_card_primary_concepts(
        self,
        card_id: str,
        *,
        layer: str,
    ) -> list[str]:
        return []

    async def get_card_summaries(
        self,
        card_ids: list[str],
        *,
        source_slot_id: str | None = None,
    ) -> list[CandidateCardSummary]:
        return []


class FileCardStore(CardStoreBase):
    """Loads PublishedKnowledgeCard from YAML files on disk.

    Expected layout::

        root/
          <chapter>/
            <card_id>.yaml
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.logger = get_logger("Knowledge.FileCardStore")
        self._cards: dict[str, PublishedKnowledgeCard] | None = None

    def _ensure_loaded(self) -> dict[str, PublishedKnowledgeCard]:
        if self._cards is not None:
            return self._cards
        self._cards = {}
        if not self.root.exists():
            self.logger.warning(f"Card root not found: {self.root}")
            return self._cards
        for path in sorted(self.root.rglob("*.yaml")):
            try:
                card = self._load_card_file(path)
                if card:
                    self._cards[card.card_id] = card
            except Exception as exc:
                self.logger.warning(f"Failed to load card {path}: {exc}")
        self.logger.info(f"Loaded {len(self._cards)} knowledge cards from {self.root}")
        return self._cards

    @staticmethod
    def _load_card_file(path: Path) -> PublishedKnowledgeCard | None:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not data or not data.get("card_id"):
            return None
        hints_raw = data.get("hints", {})
        hints = {int(k): str(v) for k, v in hints_raw.items()} if isinstance(hints_raw, dict) else {}
        return PublishedKnowledgeCard(
            card_id=str(data["card_id"]).strip(),
            chapter=str(data.get("chapter", "")).strip(),
            title=str(data.get("title", "")).strip(),
            summary=str(data.get("summary", "")).strip(),
            general_methods=[str(m) for m in data.get("general_methods", [])],
            hints=hints,
            common_mistakes=[str(m) for m in data.get("common_mistakes", [])],
            prerequisite_card_ids=[str(c) for c in data.get("prerequisite_card_ids", [])],
            problem_tags=[str(t) for t in data.get("problem_tags", [])],
            method_tags=[str(t) for t in data.get("method_tags", [])],
            thinking_tags=[str(t) for t in data.get("thinking_tags", [])],
        )

    async def get_question(self, question_id: str) -> PublishedQuestion | None:
        return None  # questions not stored as card files

    async def get_solution(self, solution_id: str) -> PublishedSolution | None:
        return None  # solutions not stored as card files

    async def get_card(self, card_id: str) -> PublishedKnowledgeCard | None:
        return self._ensure_loaded().get(card_id)

    async def list_question_cards(
        self,
        question_id: str,
        *,
        relation: str | None = None,
    ) -> list[QuestionCardLink]:
        return []  # no question-card links in file store

    async def list_solution_cards(
        self,
        solution_id: str,
        *,
        relation: str | None = None,
    ) -> list[SolutionCardLink]:
        return []  # no solution-card links in file store

    async def list_card_primary_concepts(
        self,
        card_id: str,
        *,
        layer: str,
    ) -> list[str]:
        return []  # concept layer not yet implemented

    async def get_card_summaries(
        self,
        card_ids: list[str],
        *,
        source_slot_id: str | None = None,
    ) -> list[CandidateCardSummary]:
        cards = self._ensure_loaded()
        summaries: list[CandidateCardSummary] = []
        for card_id in card_ids:
            card = cards.get(card_id)
            if not card:
                continue
            key_insight = card.general_methods[0] if card.general_methods else card.summary
            summaries.append(
                CandidateCardSummary(
                    card_id=card.card_id,
                    title=card.title,
                    summary=card.summary,
                    key_insight=key_insight,
                    formula_cues=card.method_tags[:2],
                    source_slot_ids=[source_slot_id] if source_slot_id else [],
                )
            )
        return summaries

    def all_cards(self) -> list[PublishedKnowledgeCard]:
        """Return all loaded cards (useful for building CardIndex)."""
        return list(self._ensure_loaded().values())


class InMemoryCardStore(CardStoreBase):
    """Tiny in-memory store for early wiring and tests."""

    def __init__(
        self,
        *,
        questions: list[PublishedQuestion] | None = None,
        solutions: list[PublishedSolution] | None = None,
        cards: list[PublishedKnowledgeCard] | None = None,
        card_primary_concepts: dict[tuple[str, str], list[str]] | None = None,
    ):
        self._questions = {q.question_id: q for q in questions or []}
        self._solutions = {s.solution_id: s for s in solutions or []}
        self._cards = {c.card_id: c for c in cards or []}
        self._card_primary_concepts = card_primary_concepts or {}

    async def get_question(self, question_id: str) -> PublishedQuestion | None:
        return self._questions.get(question_id)

    async def get_solution(self, solution_id: str) -> PublishedSolution | None:
        return self._solutions.get(solution_id)

    async def get_card(self, card_id: str) -> PublishedKnowledgeCard | None:
        return self._cards.get(card_id)

    async def list_question_cards(
        self,
        question_id: str,
        *,
        relation: str | None = None,
    ) -> list[QuestionCardLink]:
        question = self._questions.get(question_id)
        if not question:
            return []
        links = question.question_cards
        if relation is not None:
            links = [link for link in links if link.relation == relation]
        return list(links)

    async def list_solution_cards(
        self,
        solution_id: str,
        *,
        relation: str | None = None,
    ) -> list[SolutionCardLink]:
        solution = self._solutions.get(solution_id)
        if not solution:
            return []
        links = solution.reference_cards
        if relation is not None:
            links = [link for link in links if link.relation == relation]
        return list(links)

    async def list_card_primary_concepts(
        self,
        card_id: str,
        *,
        layer: str,
    ) -> list[str]:
        return list(self._card_primary_concepts.get((card_id, layer), []))

    async def get_card_summaries(
        self,
        card_ids: list[str],
        *,
        source_slot_id: str | None = None,
    ) -> list[CandidateCardSummary]:
        summaries: list[CandidateCardSummary] = []
        for card_id in card_ids:
            card = self._cards.get(card_id)
            if not card:
                continue
            key_insight = card.general_methods[0] if card.general_methods else card.summary
            summaries.append(
                CandidateCardSummary(
                    card_id=card.card_id,
                    title=card.title,
                    summary=card.summary,
                    key_insight=key_insight,
                    formula_cues=card.method_tags[:2],
                    source_slot_ids=[source_slot_id] if source_slot_id else [],
                )
            )
        return summaries

