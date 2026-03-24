#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""CardRetriever skeleton for RAG v2."""

from __future__ import annotations

import hashlib
import json
from typing import Iterable

from agent.infra.logging import get_logger

from .card_index import CardIndexBase, NullCardIndex
from .card_store import CardStoreBase, NullCardStore
from .data_structures import (
    CandidateCardSummary,
    CardRetrieveRequest,
    CardRetrieveResult,
    CardSelectorRequest,
    MethodRouterRequest,
    MethodRouterResult,
    RagAuditEntry,
    RetrievalBundle,
    RetrievalGoal,
    RetrievedCard,
)
from .method_catalog import MethodCatalog


class CardRetriever:
    """Orchestrates method routing, candidate lookup, selection, and fallback."""

    def __init__(
        self,
        *,
        method_catalog: MethodCatalog,
        card_store: CardStoreBase | None = None,
        card_index: CardIndexBase | None = None,
        method_router=None,
        card_selector=None,
        audit_store=None,
    ):
        self.method_catalog = method_catalog
        self.card_store = card_store or NullCardStore()
        self.card_index = card_index or NullCardIndex()
        self.method_router = method_router
        self.card_selector = card_selector
        self.audit_store = audit_store
        self.logger = get_logger("Knowledge.CardRetriever")

    async def retrieve(self, request: CardRetrieveRequest) -> RetrievalBundle:
        warnings: list[str] = []
        fallback_used = False

        resolved = self.method_catalog.resolve_topic(
            question_id=request.question_id,
            chapter=request.chapter,
            requested_topic=request.topic,
        )
        topic = request.topic or resolved.topic
        if resolved.source == "missing":
            warnings.append("missing_topic_mapping")

        topic_catalog = (
            self.method_catalog.get_topic_catalog(chapter=request.chapter, topic=topic)
            if topic
            else None
        )
        cross_catalog = self.method_catalog.get_cross_topic_catalog()

        router_result = None
        selector_result = None
        candidate_cards = []
        selected_card_ids: list[str] = []

        should_route = bool(
            self.method_router
            and topic_catalog
            and (
                request.retrieval_goal == RetrievalGoal.METHOD_REFERENCE.value
                or request.student_work
                or request.student_approach
            )
        )
        if should_route:
            router_result = await self.method_router.route(
                MethodRouterRequest(
                    question_id=request.question_id,
                    chapter=request.chapter,
                    topic=topic or "",
                    problem_text=request.problem_text or "",
                    student_work=request.student_work or "",
                    student_approach=request.student_approach,
                    topic_methods=topic_catalog.active_methods(),
                    cross_topic_methods=cross_catalog.active_methods(),
                    target_card_ids=request.target_card_ids,
                )
            )
            if router_result.primary_slot is None:
                warnings.append("empty_primary_slot")
            if router_result.confidence < 0.5:
                warnings.append("low_router_confidence")
            candidate_cards = await self._pull_candidate_cards(router_result)
            if not candidate_cards:
                warnings.append("empty_candidate_cards")

        if candidate_cards and self.card_selector:
            selector_result = await self.card_selector.select(
                CardSelectorRequest(
                    question_id=request.question_id,
                    chapter=request.chapter,
                    topic=topic or "",
                    problem_text=request.problem_text,
                    student_work=request.student_work,
                    student_approach=request.student_approach,
                    retrieval_goal=request.retrieval_goal,
                    focus_terms=list(request.focus_terms),
                    target_card_ids=list(request.target_card_ids),
                    router_result=router_result,
                    candidate_cards=candidate_cards,
                    top_k=request.top_k,
                )
            )
            selected_card_ids = list(selector_result.selected_card_ids)
            if selector_result.confidence < 0.6:
                warnings.append("low_selector_confidence")
            if selector_result.additional_need:
                fallback_used = True
                selected_card_ids = self._merge_ids(
                    selected_card_ids,
                    self._fallback_search(
                        query_text=selector_result.additional_need,
                        request=request,
                        exclude_ids=request.target_card_ids + selected_card_ids,
                    ),
                    request.top_k,
                )
        elif request.focus_terms or request.student_approach or request.student_work:
            fallback_used = True
            seed_query = " ".join(
                part
                for part in [
                    " ".join(request.focus_terms),
                    request.student_approach or "",
                    request.student_work or "",
                ]
                if part
            )
            selected_card_ids = self._fallback_search(
                query_text=seed_query,
                request=request,
                exclude_ids=request.target_card_ids,
            )[: request.top_k]
            if not selected_card_ids:
                warnings.append("empty_fallback_retrieval")

        supplementary_cards = await self._load_cards(
            selected_card_ids,
            source="selector" if candidate_cards else "embedding_fallback",
        )
        result = CardRetrieveResult(
            supplementary_cards=supplementary_cards,
            router_result=router_result,
            selector_result=selector_result,
            fallback_used=fallback_used,
            warnings=warnings,
            retrieval_signature=self._build_signature(
                request=request,
                topic=topic,
                selected_card_ids=selected_card_ids,
                warnings=warnings,
                router_result=router_result,
            ),
        )
        audit_entries = self._collect_audit_entries(
            request=request,
            topic=topic,
            router_result=router_result,
            selector_result=selector_result,
            selected_card_ids=selected_card_ids,
        )
        if audit_entries:
            self.logger.info(
                f"[{request.session_id or '?'}] audit entries: "
                f"{[e.task_type for e in audit_entries]}"
            )
            if self.audit_store:
                try:
                    self.audit_store.append(audit_entries)
                except Exception as exc:
                    self.logger.warning(f"Failed to persist audit entries: {exc}")
        return RetrievalBundle(
            request=request,
            result=result,
            selected_card_ids=selected_card_ids,
            router_primary_slot=router_result.primary_slot if router_result else None,
            router_confidence=router_result.confidence if router_result else None,
            selector_confidence=selector_result.confidence if selector_result else None,
            audit_entries=audit_entries,
        )

    @staticmethod
    def _collect_audit_entries(
        *,
        request: CardRetrieveRequest,
        topic: str | None,
        router_result: MethodRouterResult | None,
        selector_result,
        selected_card_ids: list[str],
    ) -> list[RagAuditEntry]:
        entries: list[RagAuditEntry] = []
        base = dict(
            question_id=request.question_id,
            chapter=request.chapter,
            topic=topic,
            student_approach=request.student_approach,
            router_primary_slot=router_result.primary_slot if router_result else None,
            router_confidence=router_result.confidence if router_result else None,
            selector_confidence=selector_result.confidence if selector_result else None,
            selected_card_ids=list(selected_card_ids),
        )
        if router_result and router_result.primary_slot is None:
            entries.append(RagAuditEntry(
                task_type="empty_slot",
                notes="MethodRouter 未匹配到任何 slot，可能需要新增 method slot",
                **base,
            ))
        if router_result and router_result.confidence < 0.5 and router_result.primary_slot is not None:
            entries.append(RagAuditEntry(
                task_type="low_router_confidence",
                notes=f"MethodRouter confidence={router_result.confidence:.2f}，slot 匹配不确定",
                **base,
            ))
        if selector_result and selector_result.confidence < 0.6:
            entries.append(RagAuditEntry(
                task_type="low_selector_confidence",
                notes=f"CardSelector confidence={selector_result.confidence:.2f}，候选卡质量不足",
                **base,
            ))
        return entries

    async def _pull_candidate_cards(self, router_result: MethodRouterResult) -> list[CandidateCardSummary]:
        if router_result is None:
            return []

        card_ids: list[str] = []
        slots_to_visit: list[str] = []
        if router_result.primary_slot:
            slots_to_visit.append(router_result.primary_slot)
        slots_to_visit.extend(router_result.cross_slots)

        for slot_id in slots_to_visit:
            slot = self.method_catalog.get_slot(slot_id)
            if not slot:
                continue
            card_ids = self._merge_ids(card_ids, slot.card_ids, limit=999)
            if router_result.confidence < 0.8:
                for cross_slot_id in slot.cross_ref:
                    cross_slot = self.method_catalog.get_slot(cross_slot_id)
                    if cross_slot:
                        card_ids = self._merge_ids(card_ids, cross_slot.card_ids, limit=999)

        summaries = await self.card_store.get_card_summaries(card_ids)
        return summaries

    def _fallback_search(
        self,
        *,
        query_text: str,
        request: CardRetrieveRequest,
        exclude_ids: list[str],
    ) -> list[str]:
        if not query_text.strip():
            return []
        hits = self.card_index.search(
            query_text,
            chapter=request.chapter,
            topic=request.topic,
            exclude_card_ids=exclude_ids,
            top_k=max(request.top_k, 5),
        )
        return [card_id for card_id, _ in hits]

    async def _load_cards(self, card_ids: Iterable[str], *, source: str) -> list[RetrievedCard]:
        cards: list[RetrievedCard] = []
        seen: set[str] = set()
        score = 1.0
        for card_id in card_ids:
            if card_id in seen:
                continue
            seen.add(card_id)
            card = await self.card_store.get_card(card_id)
            if card is None:
                continue
            cards.append(RetrievedCard(card=card, score=score, source=source))
            score = max(0.1, score - 0.1)
        return cards

    @staticmethod
    def _merge_ids(existing: list[str], new_ids: Iterable[str], limit: int) -> list[str]:
        merged = list(existing)
        for item in new_ids:
            if item and item not in merged:
                merged.append(item)
            if len(merged) >= limit:
                break
        return merged

    @staticmethod
    def _build_signature(
        *,
        request: CardRetrieveRequest,
        topic: str | None,
        selected_card_ids: list[str],
        warnings: list[str],
        router_result: MethodRouterResult | None,
    ) -> str:
        payload = {
            "consumer": request.consumer,
            "question_id": request.question_id,
            "solution_id": request.active_solution_id,
            "chapter": request.chapter,
            "topic": topic,
            "goal": request.retrieval_goal,
            "selected_card_ids": selected_card_ids,
            "router_primary_slot": getattr(router_result, "primary_slot", None),
            "warnings": warnings,
        }
        digest = hashlib.sha1(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return f"retrv_{digest[:12]}"
