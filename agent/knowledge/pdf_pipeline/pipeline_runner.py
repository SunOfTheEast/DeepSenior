#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PipelineRunner — orchestrates the multi-pass PDF book splitting pipeline.

Usage::

    runner = PipelineRunner(book_name="高中数学选修1", api_key="...", base_url="...")
    state = await runner.run_full(toc_text, section_contents)
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.infra.logging import get_logger

from ..card_store import FileCardStore
from ..data_structures import PublishedKnowledgeCard, ThoughtEntity
from .catalog_builder import CatalogBuilder
from .data_structures import (
    BookOutline,
    DraftCard,
    PipelineState,
    SectionOutline,
)
from .draft_store import DraftStore

logger = get_logger("Knowledge.PipelineRunner")

# Default few-shot card IDs (best hand-crafted examples)
_DEFAULT_FEW_SHOT = [
    "card_derivative_parameter",
    "card_ellipse_parametric",
    "card_sequence_auxiliary_transform",
]


def _flatten_sections(sections: list[SectionOutline]) -> list[SectionOutline]:
    """Flatten a section tree into a list (pre-order DFS)."""
    result = []
    for s in sections:
        result.append(s)
        if s.children:
            result.extend(_flatten_sections(s.children))
    return result


class PipelineRunner:
    """Orchestrates all pipeline passes with state persistence and resumability."""

    def __init__(
        self,
        book_name: str,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        strong_model: str | None = None,
        few_shot_card_ids: list[str] | None = None,
        drafts_root: Path | None = None,
        card_store_root: Path | None = None,
        max_concurrency: int = 16,
        # PDF preprocessing (Pass 0)
        pdf_path: Path | None = None,
        vision_api_key: str | None = None,
        vision_base_url: str | None = None,
        vision_model: str | None = None,
        vision_default_headers: dict[str, str] | None = None,
        toc_page_range: tuple[int, int] | None = None,
    ):
        self.book_name = book_name
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._strong_model = strong_model or model
        self._few_shot_ids = few_shot_card_ids or _DEFAULT_FEW_SHOT
        self._max_concurrency = max_concurrency
        # Vision / PDF
        self._pdf_path = pdf_path
        self._vision_api_key = vision_api_key
        self._vision_base_url = vision_base_url
        self._vision_model = vision_model
        self._vision_default_headers = vision_default_headers
        self._toc_page_range = toc_page_range

        self.store = DraftStore(book_name, drafts_root=drafts_root)
        self._card_store_root = card_store_root or (
            Path(__file__).resolve().parents[3] / "content" / "knowledge_cards"
        )

    def _agent_kwargs(self, model_override: str | None = None) -> dict[str, Any]:
        return dict(
            api_key=self._api_key,
            base_url=self._base_url,
            model=model_override or self._model,
        )

    def _load_few_shot_cards(self) -> list[PublishedKnowledgeCard]:
        fs = FileCardStore(self._card_store_root)
        cards = []
        for cid in self._few_shot_ids:
            c = fs._ensure_loaded().get(cid)
            if c:
                cards.append(c)
        return cards

    # ------------------------------------------------------------------
    # Individual passes
    # ------------------------------------------------------------------

    async def run_pass1(self, toc_text: str) -> BookOutline:
        """Pass 1: Extract book outline from TOC text."""
        from .structure_extractor import StructureExtractor

        logger.info("Pass 1: extracting book structure...")
        extractor = StructureExtractor(**self._agent_kwargs())
        outline = await extractor.process(toc_text, self.book_name)
        self.store.save_outline(outline)

        state = self._get_or_create_state()
        state.current_pass = "pass1"
        state.outline = outline
        self.store.save_state(state)
        logger.info(f"Pass 1 complete: {len(outline.chapters)} chapters")
        return outline

    async def run_pass2_section(
        self,
        section: SectionOutline,
        section_content: str,
        chapter_name: str,
        chapter_index: int = 0,
        seq_start: int = 0,
        few_shot_cards: list[PublishedKnowledgeCard] | None = None,
    ) -> tuple[list[DraftCard], int]:
        """Pass 2a+2b for a single section: analyze then generate cards.

        Returns:
            (list of DraftCard, next available seq number)
        """
        from .card_generator import CardGenerator
        from .section_analyzer import SectionAnalyzer

        # Pass 2a
        logger.info(f"Pass 2a: analyzing section '{section.title}'...")
        analyzer = SectionAnalyzer(**self._agent_kwargs())
        analysis = await analyzer.process(section, section_content)
        self.store.save_analysis(section.section_id, analysis)

        # Pass 2b
        logger.info(f"Pass 2b: generating cards for '{section.title}'...")
        few_shot = few_shot_cards if few_shot_cards is not None else self._load_few_shot_cards()
        # Load noise tags from previous runs for dynamic prompt feedback
        existing_clusters = self.store.load_tag_clusters()
        noise_tags = existing_clusters.get("filtered_tags") if existing_clusters else None
        generator = CardGenerator(
            few_shot_cards=few_shot, noise_tags=noise_tags, **self._agent_kwargs(),
        )
        cards, next_seq = await generator.process(
            analysis, section_content, section, chapter_name,
            chapter_index=chapter_index,
            seq_start=seq_start,
        )
        if cards:
            self.store.save_cards(cards)
        logger.info(f"Pass 2b: generated {len(cards)} cards for '{section.title}'")
        return cards, next_seq

    async def run_pass2_all(
        self,
        section_contents: dict[str, str],
    ) -> list[DraftCard]:
        """Pass 2: process all sections (bounded concurrency)."""
        state = self._get_or_create_state()
        outline = state.outline or self.store.load_outline()
        if not outline:
            raise RuntimeError("No outline found. Run Pass 1 first.")

        all_sections = _flatten_sections(outline.chapters)
        # Only process leaf-level sections (level >= 2)
        sections_to_process = [s for s in all_sections if s.level >= 2]

        # Pre-allocate seq ranges: each section gets 100 slots to avoid concurrency conflicts
        # Build (section_id → chapter_index, chapter_name, seq_start) mapping
        section_meta: dict[str, tuple[int, str, int]] = {}
        current_seq = 0
        for ch_idx, ch in enumerate(outline.chapters):
            ch_sections = [s for s in _flatten_sections([ch]) if s.level >= 2]
            for sec in ch_sections:
                section_meta[sec.section_id] = (ch_idx, ch.title, current_seq)
                current_seq += 100  # 100 slots per section
        logger.info(f"Pre-allocated seq ranges for {len(section_meta)} sections, max_seq={current_seq}")

        # Load few-shot cards once, reuse across all sections
        few_shot = self._load_few_shot_cards()
        logger.info(f"Loaded {len(few_shot)} few-shot example cards (once)")

        sem = asyncio.Semaphore(self._max_concurrency)
        all_cards: list[DraftCard] = []

        async def _process_one(sec: SectionOutline) -> list[DraftCard]:
            if sec.section_id in state.completed_sections:
                logger.info(f"Skipping completed section: {sec.title}")
                return []
            content = section_contents.get(sec.section_id, "")
            if not content.strip():
                logger.warning(f"No content for section {sec.section_id}, skipping")
                return []
            ch_idx, chapter_name, seq_start = section_meta.get(
                sec.section_id, (0, sec.title, 0)
            )
            async with sem:
                try:
                    cards, _next_seq = await self.run_pass2_section(
                        sec, content, chapter_name,
                        chapter_index=ch_idx,
                        seq_start=seq_start,
                        few_shot_cards=few_shot,
                    )
                    state.completed_sections.append(sec.section_id)
                    state.total_draft_cards += len(cards)
                    self.store.save_state(state)
                    return cards
                except Exception as exc:
                    logger.error(f"Pass 2 failed for '{sec.title}': {exc}")
                    state.errors.append({
                        "pass": "pass2",
                        "section_id": sec.section_id,
                        "error": str(exc),
                    })
                    self.store.save_state(state)
                    return []

        tasks = [_process_one(s) for s in sections_to_process]
        results = await asyncio.gather(*tasks)
        for cards in results:
            all_cards.extend(cards)

        state.current_pass = "pass2b"
        self.store.save_state(state)
        logger.info(f"Pass 2 complete: {len(all_cards)} total cards")
        return all_cards

    async def run_pass2c(self) -> list:
        """Pass 2c: Extract questions (archetypes + exercises) from all sections."""
        from .question_extractor import QuestionExtractor

        logger.info("Pass 2c: extracting questions...")
        state = self._get_or_create_state()
        outline = state.outline or self.store.load_outline()
        if not outline:
            raise RuntimeError("No outline found. Run Pass 1 first.")

        all_sections = _flatten_sections(outline.chapters)
        sections_to_process = [
            s for s in all_sections
            if s.level >= 2 and getattr(s, "section_type", "content") != "answer"
        ]

        # Pre-allocate question ID ranges (100 per section)
        section_meta: dict[str, tuple[int, str, int]] = {}
        current_seq = 0
        for ch_idx, ch in enumerate(outline.chapters):
            ch_sections = [s for s in _flatten_sections([ch]) if s.level >= 2]
            for sec in ch_sections:
                section_meta[sec.section_id] = (ch_idx, ch.title, current_seq)
                current_seq += 100

        extractor = QuestionExtractor(**self._agent_kwargs())
        sem = asyncio.Semaphore(self._max_concurrency)
        all_questions = []

        async def _process_one(sec: SectionOutline):
            sc = self.store.load_section_content(sec.section_id)
            if not sc or not sc.text.strip():
                return []
            ch_idx, chapter_name, seq_start = section_meta.get(
                sec.section_id, (0, sec.title, 0)
            )
            analysis = self.store.load_analysis(sec.section_id)
            async with sem:
                try:
                    questions, _ = await extractor.process(
                        analysis, sc.text, sec, chapter_name,
                        chapter_index=ch_idx, seq_start=seq_start,
                    )
                    if questions:
                        self.store.save_questions(questions)
                    return questions
                except Exception as exc:
                    logger.error(f"Pass 2c failed for '{sec.title}': {exc}")
                    return []

        tasks = [_process_one(s) for s in sections_to_process]
        results = await asyncio.gather(*tasks)
        for questions in results:
            all_questions.extend(questions)

        logger.info(
            f"Pass 2c complete: {len(all_questions)} questions "
            f"({sum(1 for q in all_questions if q.question_type == 'archetype')} archetypes, "
            f"{sum(1 for q in all_questions if q.question_type == 'exercise')} exercises)"
        )
        return all_questions

    async def run_pass3(self) -> list[DraftCard]:
        """Pass 3: Cross-section relationship inference."""
        from .relationship_builder import RelationshipBuilder

        logger.info("Pass 3: inferring cross-section relationships...")
        all_cards = self.store.list_draft_cards()
        existing = self._load_existing_cards()

        builder = RelationshipBuilder(
            max_concurrency=self._max_concurrency,
            **self._agent_kwargs(),
        )
        updated_cards, concept_groupings = await builder.process(all_cards, existing)

        # Re-save updated cards
        self.store.save_cards(updated_cards)

        # Persist concept groupings for Pass 4
        if concept_groupings:
            self.store.save_concept_groupings(concept_groupings)

        state = self._get_or_create_state()
        state.current_pass = "pass3"
        self.store.save_state(state)
        logger.info(f"Pass 3 complete: {len(updated_cards)} cards updated, {len(concept_groupings)} concept groups")
        return updated_cards

    async def run_foundation(self) -> list:
        """Foundation layer: detect book-external concepts, cluster, name, backfill."""
        from .relationship_builder import RelationshipBuilder

        logger.info("Foundation: detecting book-external concepts...")
        all_cards = self.store.list_draft_cards()

        builder = RelationshipBuilder(
            max_concurrency=self._max_concurrency,
            **self._agent_kwargs(),
        )
        foundation_concepts = await builder.build_foundation_concepts(
            all_cards, self.book_name,
        )

        # Re-save cards (assumed_knowledge backfilled)
        self.store.save_cards(all_cards)

        if foundation_concepts:
            self.store.save_foundation_concepts(foundation_concepts)

        logger.info(f"Foundation complete: {len(foundation_concepts)} concepts")
        return foundation_concepts

    async def run_pass3_5(self) -> list[ThoughtEntity]:
        """Pass 3.5: DeepThink — discover cross-chapter semantic patterns."""
        from .deep_think import DeepThinkAgent

        logger.info("Pass 3.5: DeepThink discovering cross-chapter patterns...")
        all_cards = self.store.list_draft_cards()
        existing = self._load_existing_cards()

        summaries = []
        for c in list(all_cards) + list(existing):
            summaries.append({
                "card_id": c.card_id if isinstance(c, DraftCard) else c.card_id,
                "title": c.title,
                "summary": c.summary,
                "chapter": c.chapter,
                "thinking_tags": c.thinking_tags if hasattr(c, "thinking_tags") else [],
                "formula_cues": c.formula_cues if hasattr(c, "formula_cues") else [],
            })

        agent = DeepThinkAgent(**self._agent_kwargs(model_override=self._strong_model))
        thoughts = await agent.process(summaries)

        if thoughts:
            self.store.save_thoughts(thoughts)
        state = self._get_or_create_state()
        state.current_pass = "pass3.5"
        self.store.save_state(state)
        logger.info(f"Pass 3.5 complete: {len(thoughts)} Thought candidates discovered")
        return thoughts

    async def run_pass4(self) -> dict[str, int]:
        """Pass 4: Tag clustering (LLM) + method catalogs + concept files."""
        logger.info("Pass 4: tag clustering + catalogs...")
        all_cards = self.store.list_draft_cards()

        # Step 1: Tag clustering (LLM)
        from .tag_clusterer import TagClusterer
        clusterer = TagClusterer(drafts_base=self.store.base, **self._agent_kwargs())
        cluster_mappings = await clusterer.process(all_cards)
        if cluster_mappings:
            self.store.save_cards(all_cards)  # re-save with cluster fields
            self.store.save_tag_clusters(cluster_mappings)

        # Step 2: Method catalogs (pure code)
        builder = CatalogBuilder()
        catalogs = builder.build_method_catalogs(all_cards)
        self.store.save_catalogs(catalogs)

        # Step 3: Concept files (pure code)
        concept_groupings = self.store.load_concept_groupings()
        logger.info(f"Loaded {len(concept_groupings)} concept groupings from Pass 3")
        concepts = builder.build_concept_files(all_cards, concept_groupings)
        self.store.save_concepts(concepts)

        state = self._get_or_create_state()
        state.current_pass = "pass4"
        self.store.save_state(state)
        logger.info(
            f"Pass 4 complete: {len(catalogs)} catalogs, {len(concepts)} concept files, "
            f"{len(cluster_mappings)} tag cluster fields"
        )
        return {"catalogs": len(catalogs), "concepts": len(concepts)}

    # ------------------------------------------------------------------
    # Pass 0: PDF preprocessing
    # ------------------------------------------------------------------

    async def run_pass0(
        self,
    ) -> tuple[str, dict[str, "SectionContent"]]:
        """Pass 0: PDF → TOC text + per-section SectionContent with figures.

        Requires ``pdf_path`` to be set in constructor.
        """
        from .pdf_preprocessor import PDFPreprocessor
        from .data_structures import SectionContent

        if not self._pdf_path:
            raise RuntimeError("pdf_path not set. Use run_full() for text-only mode.")

        preprocessor = PDFPreprocessor(
            pdf_path=self._pdf_path,
            book_name=self.book_name,
            store=self.store,
            vision_api_key=self._vision_api_key,
            vision_base_url=self._vision_base_url,
            vision_model=self._vision_model,
            vision_default_headers=self._vision_default_headers,
            toc_page_range=self._toc_page_range,
        )
        try:
            # 0a: TOC extraction (bookmarks → native text → vision)
            toc_text = await preprocessor.extract_toc()

            # Pass 1: structure extraction (reuse existing)
            outline = await self.run_pass1(toc_text)

            # Calibrate page offset
            preprocessor.calibrate_page_offset(outline)

            # 0b: Section content extraction (native + quality gate)
            section_contents = await preprocessor.extract_all_sections(outline)

            # 0c: Figure extraction + description
            all_sections = _flatten_sections(outline.chapters)
            for sec in all_sections:
                if sec.level < 2 or sec.section_id not in section_contents:
                    continue
                sc = section_contents[sec.section_id]
                if not sc.figures:  # only if figures not yet populated
                    raw_figs = preprocessor.extract_figures(sec)
                    if raw_figs:
                        described = await preprocessor.describe_figures(raw_figs)
                        sc.figures = described
                        self.store.save_section_content(sc)

            return toc_text, section_contents
        finally:
            preprocessor.close()

    async def run_from_pdf(
        self,
        *,
        skip_deepthink: bool = False,
    ) -> PipelineState:
        """Run the complete pipeline starting from a raw PDF (Pass 0 → 4)."""
        from .pdf_preprocessor import PDFPreprocessor

        _toc_text, section_contents = await self.run_pass0()

        # 0d: Enrich text with figure index, then feed into Pass 2+
        text_map = {
            sid: PDFPreprocessor.enrich_text_with_figures(sc)
            for sid, sc in section_contents.items()
        }
        await self.run_pass2_all(text_map)
        await self.run_pass2c()
        await self.run_pass3()
        await self.run_foundation()
        if not skip_deepthink:
            await self.run_pass3_5()
        await self.run_pass4()

        state = self._get_or_create_state()
        state.current_pass = "done"
        self.store.save_state(state)
        logger.info(f"PDF pipeline complete for '{self.book_name}': {state.total_draft_cards} cards")
        return state

    # ------------------------------------------------------------------
    # Full pipeline (text-only, original interface)
    # ------------------------------------------------------------------

    async def run_full(
        self,
        toc_text: str,
        section_contents: dict[str, str],
        *,
        skip_deepthink: bool = False,
    ) -> PipelineState:
        """Run the complete pipeline: Pass 1 → 2 → 2c → 3 → 3.5 → 4."""
        await self.run_pass1(toc_text)
        await self.run_pass2_all(section_contents)
        await self.run_pass2c()
        await self.run_pass3()
        await self.run_foundation()
        if not skip_deepthink:
            await self.run_pass3_5()
        await self.run_pass4()

        state = self._get_or_create_state()
        state.current_pass = "done"
        self.store.save_state(state)
        logger.info(f"Pipeline complete for '{self.book_name}': {state.total_draft_cards} cards")
        return state

    async def resume(self, section_contents: dict[str, str] | None = None) -> PipelineState:
        """Resume pipeline from last saved state."""
        state = self.store.load_state()
        if not state:
            raise RuntimeError("No saved state found. Run the pipeline from scratch.")

        logger.info(f"Resuming from pass={state.current_pass}")
        if state.current_pass in ("init", "pass1"):
            if section_contents:
                await self.run_pass2_all(section_contents)
            await self.run_pass2c()
            await self.run_pass3()
            await self.run_pass3_5()
            await self.run_pass4()
        elif state.current_pass == "pass2b":
            await self.run_pass2c()
            await self.run_pass3()
            await self.run_pass3_5()
            await self.run_pass4()
        elif state.current_pass == "pass2c":
            await self.run_pass3()
            await self.run_pass3_5()
            await self.run_pass4()
        elif state.current_pass == "pass3":
            await self.run_pass3_5()
            await self.run_pass4()
        elif state.current_pass == "pass3.5":
            await self.run_pass4()

        state = self._get_or_create_state()
        state.current_pass = "done"
        self.store.save_state(state)
        return state

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_or_create_state(self) -> PipelineState:
        state = self.store.load_state()
        if not state:
            state = PipelineState(book_name=self.book_name)
        return state

    def _load_existing_cards(self) -> list[PublishedKnowledgeCard]:
        fs = FileCardStore(self._card_store_root)
        return fs.all_cards()
