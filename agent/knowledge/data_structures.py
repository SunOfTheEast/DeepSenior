#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Core data structures for the RAG v2 knowledge layer."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class RetrievalConsumer(str, Enum):
    PLANNER = "planner"
    REVIEW = "review"
    RECOMMEND = "recommend"
    MEMORY = "memory"


class RetrievalGoal(str, Enum):
    METHOD_REFERENCE = "method_reference"
    CONCEPT_EXPLAIN = "concept_explain"
    REINFORCEMENT = "reinforcement"
    MEMORY_REPAIR = "memory_repair"


@dataclass
class PublishedKnowledgeCard:
    card_id: str
    chapter: str
    title: str
    summary: str
    general_methods: list[str] = field(default_factory=list)
    hints: dict[int, str] = field(default_factory=dict)
    common_mistakes: list[str] = field(default_factory=list)
    prerequisite_card_ids: list[str] = field(default_factory=list)
    problem_tags: list[str] = field(default_factory=list)
    method_tags: list[str] = field(default_factory=list)
    thinking_tags: list[str] = field(default_factory=list)


@dataclass
class QuestionCardLink:
    card_id: str
    relation: str
    weight: float = 1.0


@dataclass
class SolutionCardLink:
    card_id: str
    relation: str
    weight: float = 1.0


@dataclass
class PublishedSolution:
    solution_id: str
    question_id: str
    method_name: str
    is_standard: bool
    reference_cards: list[SolutionCardLink] = field(default_factory=list)


@dataclass
class PublishedQuestion:
    question_id: str
    chapter: str
    difficulty: int
    stem: str
    answer_schema: dict = field(default_factory=dict)
    question_cards: list[QuestionCardLink] = field(default_factory=list)
    solutions: list[PublishedSolution] = field(default_factory=list)


@dataclass
class MethodSlot:
    slot_id: str
    name: str
    trigger: str
    card_ids: list[str]
    cross_ref: list[str] = field(default_factory=list)
    status: str = "active"
    notes: str | None = None


@dataclass
class MethodCatalogTopic:
    chapter: str
    topic: str
    version: str = ""
    status: str = "active"
    methods: list[MethodSlot] = field(default_factory=list)

    def active_methods(self) -> list[MethodSlot]:
        return [m for m in self.methods if m.status == "active"]


@dataclass
class TopicResolveResult:
    question_id: str | None
    chapter: str
    topic: str | None
    fallback_topics: list[str] = field(default_factory=list)
    source: str = "missing"


@dataclass
class CandidateCardSummary:
    card_id: str
    title: str
    summary: str
    key_insight: str
    formula_cues: list[str] = field(default_factory=list)
    source_slot_ids: list[str] = field(default_factory=list)


@dataclass
class RetrievedCard:
    card: PublishedKnowledgeCard
    score: float
    source: str
    selected_reason: str | None = None


@dataclass
class MethodRouterRequest:
    question_id: str | None
    chapter: str
    topic: str
    problem_text: str
    student_work: str
    student_approach: str | None
    topic_methods: list[MethodSlot]
    cross_topic_methods: list[MethodSlot]
    target_card_ids: list[str] = field(default_factory=list)


@dataclass
class MethodRouterResult:
    primary_slot: str | None
    cross_slots: list[str] = field(default_factory=list)
    confidence: float = 0.0
    reasoning: str = ""
    slot_candidates: list[str] = field(default_factory=list)


@dataclass
class CardSelectorRequest:
    question_id: str | None
    chapter: str
    topic: str
    problem_text: str | None
    student_work: str | None
    student_approach: str | None
    retrieval_goal: str
    focus_terms: list[str]
    target_card_ids: list[str]
    router_result: MethodRouterResult | None
    candidate_cards: list[CandidateCardSummary]
    top_k: int = 3


@dataclass
class CardSelectorResult:
    selected_card_ids: list[str] = field(default_factory=list)
    additional_need: str | None = None
    additional_reason: str | None = None
    confidence: float = 0.0


@dataclass
class CardRetrieveRequest:
    consumer: str
    question_id: str | None
    active_solution_id: str | None
    chapter: str
    topic: str | None
    problem_text: str | None
    student_work: str | None
    student_approach: str | None
    target_card_ids: list[str]
    focus_terms: list[str] = field(default_factory=list)
    retrieval_goal: str = RetrievalGoal.METHOD_REFERENCE.value
    session_id: str | None = None
    top_k: int = 3


@dataclass
class CardRetrieveResult:
    supplementary_cards: list[RetrievedCard] = field(default_factory=list)
    router_result: MethodRouterResult | None = None
    selector_result: CardSelectorResult | None = None
    fallback_used: bool = False
    warnings: list[str] = field(default_factory=list)
    retrieval_signature: str = ""


class AuditStatus(str, Enum):
    PENDING = "pending"
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    DONE = "done"


@dataclass
class RagAuditEntry:
    """Audit record for RAG retrieval events — both anomalies and successes.

    Serves as the actionable audit task with a status lifecycle:
    pending → proposed → approved/rejected → done
    """
    task_type: str              # e.g. "empty_slot", "low_router_confidence", "retrieval_ok", "new_method"
    question_id: str | None
    chapter: str
    topic: str | None
    student_approach: str | None
    router_primary_slot: str | None = None
    router_confidence: float | None = None
    selector_confidence: float | None = None
    selected_card_ids: list[str] = field(default_factory=list)
    notes: str = ""
    # --- new fields for audit task lifecycle ---
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = AuditStatus.PENDING.value
    session_id: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str | None = None


@dataclass
class RetrievalBundle:
    request: CardRetrieveRequest
    result: CardRetrieveResult
    selected_card_ids: list[str] = field(default_factory=list)
    router_primary_slot: str | None = None
    router_confidence: float | None = None
    selector_confidence: float | None = None
    audit_entries: list[RagAuditEntry] = field(default_factory=list)

