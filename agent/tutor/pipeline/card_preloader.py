#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Pre-load published knowledge cards into ProblemContext at session creation.

Populates:
  - published_card_menu (L0): lightweight summaries, always in prompt
  - published_card_full (L1): full cards in memory, expanded on demand

Sync-safe: FileCardStore data is already loaded into memory at init time.
"""

from agent.infra.logging import get_logger
from ..data_structures import ProblemContext

logger = get_logger("Tutor.CardPreloader")


def preload_published_cards(
    problem_context: ProblemContext,
    card_store,
) -> None:
    """Fill problem_context.published_card_menu (L0) and published_card_full (L1).

    Args:
        problem_context: Mutable; fields are set in place.
        card_store: FileCardStore (duck typed — needs get_card_sync,
                    get_cards_by_chapter_sync, build_summaries_sync).
    """
    if not hasattr(card_store, "get_card_sync"):
        logger.debug("card_store does not support sync access; skipping preload")
        return

    # 1. Collect bound card IDs — SolverAgent bindings take priority
    solver_bound = getattr(problem_context, "bound_card_ids", []) or []
    legacy_bound = [kc.card_id for kc in problem_context.knowledge_cards]
    bound_ids = solver_bound or legacy_bound

    # 2. Expand by chapter — load all cards in the same chapter
    chapter = problem_context.chapter
    chapter_cards = card_store.get_cards_by_chapter_sync(chapter) if chapter else []

    # 3. Build L1: merge bound + chapter cards (bound cards first for priority)
    full: dict = {}
    for card_id in bound_ids:
        card = card_store.get_card_sync(card_id)
        if card:
            full[card.card_id] = card
        else:
            logger.debug(f"Bound card_id '{card_id}' not found in FileCardStore")
    for card in chapter_cards:
        if card.card_id not in full:
            full[card.card_id] = card

    if not full:
        logger.debug(
            f"No published cards found for problem '{problem_context.problem_id}' "
            f"(bound={bound_ids}, chapter='{chapter}')"
        )
        return

    # 4. Build L0 menu (summaries for all loaded cards)
    all_ids = list(full.keys())
    menu = card_store.build_summaries_sync(all_ids)

    # 5. Assign to problem_context
    problem_context.published_card_full = full
    problem_context.published_card_menu = menu

    logger.info(
        f"Pre-loaded {len(full)} published cards "
        f"(bound={len(bound_ids)}, chapter_expand={len(chapter_cards)}) "
        f"for problem '{problem_context.problem_id}'"
    )
