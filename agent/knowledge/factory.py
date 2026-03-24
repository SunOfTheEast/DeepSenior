#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Factory for assembling the knowledge retrieval stack."""

from __future__ import annotations

from pathlib import Path

from .card_index import CardIndexBase, NullCardIndex, SimpleCardIndex
from .card_retriever import CardRetriever
from .card_store import CardStoreBase, FileCardStore, NullCardStore
from .method_catalog import MethodCatalog


_DEFAULT_CARD_ROOT = Path(__file__).resolve().parents[2] / "content" / "knowledge_cards"


def build_card_retriever(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    language: str = "zh",
    api_version: str | None = None,
    binding: str = "openai",
    catalog_root: str | Path | None = None,
    card_root: str | Path | None = None,
    card_store: CardStoreBase | None = None,
    card_index: CardIndexBase | None = None,
    enable_llm_agents: bool = True,
) -> CardRetriever:
    """Build a fully-wired CardRetriever.

    When ``api_key`` / ``base_url`` are provided and ``enable_llm_agents``
    is True, the real MethodRouterAgent and CardSelectorAgent are created.
    Otherwise the retriever falls back to keyword / embedding-only mode.

    If no ``card_store`` is given, a FileCardStore loading from ``card_root``
    (default: ``content/knowledge_cards/``) is used.  When the directory
    exists and contains cards, a SimpleCardIndex is also built automatically.
    """
    method_catalog = MethodCatalog(root=catalog_root)

    # -- card store --
    if card_store is None:
        resolved_root = Path(card_root) if card_root else _DEFAULT_CARD_ROOT
        if resolved_root.exists():
            card_store = FileCardStore(resolved_root)
        else:
            card_store = NullCardStore()

    # -- card index (auto-build from FileCardStore when available) --
    if card_index is None:
        if isinstance(card_store, FileCardStore):
            idx = SimpleCardIndex()
            idx.build(card_store.all_cards())
            card_index = idx
        else:
            card_index = NullCardIndex()

    # -- LLM agents --
    method_router = None
    card_selector = None

    if enable_llm_agents and api_key and base_url:
        from .agents.method_router_agent import MethodRouterAgent
        from .agents.card_selector_agent import CardSelectorAgent

        _kwargs = dict(
            api_key=api_key,
            base_url=base_url,
            language=language,
            api_version=api_version,
            binding=binding,
        )
        method_router = MethodRouterAgent(**_kwargs)
        card_selector = CardSelectorAgent(**_kwargs)

    return CardRetriever(
        method_catalog=method_catalog,
        card_store=card_store,
        card_index=card_index,
        method_router=method_router,
        card_selector=card_selector,
    )
