#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Pass 3: Three-stage prerequisite inference and concept grouping.

Stage 3a — Candidate Recall (pure code):
    For each target card, score all earlier-page cards and select top-K candidates.

Stage 3b — Local Discrimination (small-context LLM):
    For each target + its candidates, LLM picks true prerequisites (using idx, not card_id).

Stage 3c — Global Validation (pure code):
    DAG check, existence check, dedup, max-5 cap.

Concept grouping uses teaches_concepts intersection + graph clustering.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import defaultdict
from pathlib import Path

import yaml

from agent.base_agent import BaseAgent
from agent.infra.logging import get_logger
from .data_structures import DraftCard, FoundationConcept
from ..data_structures import PublishedKnowledgeCard

_logger = get_logger("Knowledge.RelationshipBuilder")

# ---------------------------------------------------------------------------
# 3a: Candidate Recall (pure code)
# ---------------------------------------------------------------------------

_WEIGHT_CONCEPT = 3.0
_WEIGHT_FORMULA = 2.0
_WEIGHT_TAG = 1.0
_TOP_K = 15


def _recall_candidates(
    target: DraftCard,
    all_cards: list[DraftCard],
    formula_templates: dict[str, str | None] | None = None,
) -> list[DraftCard]:
    """Score and rank candidate prerequisites for *target*.

    Hard constraints:
    - Candidate must come from an earlier page (source_page_end <= target.source_page_start)
    - Candidate must be from a different section
    """
    target_requires = set(target.requires_concepts)
    target_formulae_raw = set(target.formulae_raw)
    target_method_tags = set(target.method_tags)

    scored: list[tuple[float, DraftCard]] = []

    for card in all_cards:
        if card.card_id == target.card_id:
            continue
        # Hard constraint: earlier page
        if card.source_page_end > target.source_page_start and target.source_page_start > 0:
            continue
        # Hard constraint: different section
        if card.source_section_id == target.source_section_id:
            continue

        score = 0.0

        # Signal 1: teaches/requires concept intersection
        card_teaches = set(card.teaches_concepts)
        concept_overlap = target_requires & card_teaches
        if concept_overlap:
            score += _WEIGHT_CONCEPT * len(concept_overlap)

        # Signal 2: formula template matching
        if formula_templates and target_formulae_raw and card.formulae_raw:
            for tf in target_formulae_raw:
                t_tmpl = formula_templates.get(tf)
                if not t_tmpl:
                    continue
                for cf in card.formulae_raw:
                    c_tmpl = formula_templates.get(cf)
                    if c_tmpl and t_tmpl == c_tmpl:
                        score += _WEIGHT_FORMULA
                        break

        # Signal 3: method tag overlap
        card_tags = set(card.method_tags)
        tag_overlap = target_method_tags & card_tags
        if tag_overlap:
            score += _WEIGHT_TAG * len(tag_overlap)

        if score > 0:
            scored.append((score, card))

    # Sort by score descending, take top-K
    scored.sort(key=lambda x: x[0], reverse=True)
    return [card for _, card in scored[:_TOP_K]]


# ---------------------------------------------------------------------------
# 3c: Global Validation (pure code)
# ---------------------------------------------------------------------------


def _validate_prerequisites(cards: list[DraftCard]) -> int:
    """Validate and fix prerequisite graph. Returns number of fixes applied."""
    card_ids = {c.card_id for c in cards}
    fixes = 0

    for card in cards:
        original_len = len(card.prerequisite_card_ids)

        # 1. Remove non-existent references
        card.prerequisite_card_ids = [
            pid for pid in card.prerequisite_card_ids if pid in card_ids
        ]

        # 2. Remove self-references
        card.prerequisite_card_ids = [
            pid for pid in card.prerequisite_card_ids if pid != card.card_id
        ]

        # 3. Dedup
        seen = set()
        deduped = []
        for pid in card.prerequisite_card_ids:
            if pid not in seen:
                seen.add(pid)
                deduped.append(pid)
        card.prerequisite_card_ids = deduped

        # 4. Cap at 5
        if len(card.prerequisite_card_ids) > 5:
            card.prerequisite_card_ids = card.prerequisite_card_ids[:5]

        fixes += original_len - len(card.prerequisite_card_ids)

    # 5. DAG cycle detection + removal
    cycle_fixes = _remove_cycles(cards)
    fixes += cycle_fixes

    return fixes


def _remove_cycles(cards: list[DraftCard]) -> int:
    """Detect and break cycles in the prerequisite DAG using DFS."""
    adj: dict[str, list[str]] = {c.card_id: list(c.prerequisite_card_ids) for c in cards}
    card_map = {c.card_id: c for c in cards}

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {cid: WHITE for cid in adj}
    back_edges: list[tuple[str, str]] = []

    def dfs(u: str) -> None:
        color[u] = GRAY
        for v in adj.get(u, []):
            if v not in color:
                continue
            if color[v] == GRAY:
                back_edges.append((u, v))
            elif color[v] == WHITE:
                dfs(v)
        color[u] = BLACK

    for cid in adj:
        if color[cid] == WHITE:
            dfs(cid)

    # Remove back edges
    for u, v in back_edges:
        if u in card_map:
            card = card_map[u]
            if v in card.prerequisite_card_ids:
                card.prerequisite_card_ids.remove(v)
                _logger.warning("Removed cycle edge: %s → %s", u, v)

    return len(back_edges)


# ---------------------------------------------------------------------------
# Concept Grouping (Phase 5 / Batch D)
# ---------------------------------------------------------------------------


def _cluster_concepts(cards: list[DraftCard]) -> list[list[DraftCard]]:
    """Cluster cards by teaches_concepts overlap using connected components."""
    # Build adjacency: two cards are connected if they share a teaches_concept
    concept_to_cards: dict[str, list[int]] = defaultdict(list)
    for i, card in enumerate(cards):
        for concept in card.teaches_concepts:
            concept_to_cards[concept].append(i)

    # Union-Find
    parent = list(range(len(cards)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for concept, indices in concept_to_cards.items():
        for j in range(1, len(indices)):
            union(indices[0], indices[j])

    # Group by root
    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(len(cards)):
        clusters[find(i)].append(i)

    # Filter out singletons and sort by size descending
    result = [
        [cards[i] for i in indices]
        for indices in clusters.values()
        if len(indices) >= 2
    ]
    result.sort(key=len, reverse=True)
    return result


def _format_cluster_for_prompt(cluster_id: int, cards: list[DraftCard]) -> str:
    """Format a cluster for the concept naming prompt."""
    lines = [f"[分组 {cluster_id}] ({len(cards)} 张卡)"]
    for c in cards:
        teaches = ", ".join(c.teaches_concepts) if c.teaches_concepts else ""
        lines.append(f"  - {c.title} | {c.chapter} | teaches: {teaches}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class RelationshipBuilder(BaseAgent):
    """Pass 3: Three-stage prerequisite inference + concept grouping."""

    def __init__(self, max_concurrency: int = 16, **kwargs):
        super().__init__(
            module_name="knowledge",
            agent_name="relationship_builder",
            **kwargs,
        )
        prompt_path = (
            Path(__file__).parent / "prompts" / "zh" / "relationship_builder.yaml"
        )
        with open(prompt_path, "r", encoding="utf-8") as f:
            self._prompts = yaml.safe_load(f)
        self._max_concurrency = max_concurrency

    async def process(
        self,
        all_draft_cards: list[DraftCard],
        existing_cards: list[PublishedKnowledgeCard] | None = None,
    ) -> tuple[list[DraftCard], list[dict]]:
        """Run three-stage prerequisite inference + concept grouping.

        Returns:
            (updated cards with prerequisites, concept_groupings)
        """
        _logger.info("Pass 3 开始: %d 张草稿卡", len(all_draft_cards))

        # --- 3a: Compute formula templates ---
        all_formulae = set()
        for c in all_draft_cards:
            all_formulae.update(c.formulae_raw)
        formula_templates: dict[str, str | None] = {}
        if all_formulae:
            try:
                from .formula_utils import batch_compute_templates
                formula_templates = batch_compute_templates(list(all_formulae))
            except ImportError:
                _logger.warning("formula_utils not available, skipping formula matching")

        # --- 3a: Candidate recall ---
        card_candidates: dict[str, list[DraftCard]] = {}
        cards_with_candidates = 0
        for card in all_draft_cards:
            candidates = _recall_candidates(card, all_draft_cards, formula_templates)
            if candidates:
                card_candidates[card.card_id] = candidates
                cards_with_candidates += 1
        _logger.info(
            "3a 召回完成: %d/%d 张卡有候选前置",
            cards_with_candidates, len(all_draft_cards),
        )

        # --- 3b: Local discrimination (LLM) ---
        sem = asyncio.Semaphore(self._max_concurrency)
        card_map = {c.card_id: c for c in all_draft_cards}

        async def _discriminate_one(target_id: str) -> None:
            target = card_map[target_id]
            candidates = card_candidates[target_id]
            async with sem:
                prereq_ids = await self._discriminate(target, candidates)
                target.prerequisite_card_ids = prereq_ids

        tasks = [_discriminate_one(cid) for cid in card_candidates]
        await asyncio.gather(*tasks)

        _logger.info("3b 判别完成")

        # --- 3c: Global validation ---
        fixes = _validate_prerequisites(all_draft_cards)
        total_prereqs = sum(len(c.prerequisite_card_ids) for c in all_draft_cards)
        _logger.info("3c 校验完成: %d 条前置关系, %d 处修正", total_prereqs, fixes)

        # --- Concept grouping ---
        concept_groupings = await self._build_concept_groupings(all_draft_cards)

        _logger.info(
            "Pass 3 完成: %d 条前置关系, %d 个概念分组",
            total_prereqs, len(concept_groupings),
        )
        return all_draft_cards, concept_groupings

    # ------------------------------------------------------------------
    # 3b: LLM discrimination
    # ------------------------------------------------------------------

    async def _discriminate(
        self,
        target: DraftCard,
        candidates: list[DraftCard],
    ) -> list[str]:
        """Ask LLM which candidates are true prerequisites of target."""
        # Build candidates text with idx
        cand_lines = []
        for idx, c in enumerate(candidates):
            teaches = ", ".join(c.teaches_concepts) if c.teaches_concepts else "N/A"
            formulae = ", ".join(c.formulae_raw[:3]) if c.formulae_raw else "N/A"
            cand_lines.append(
                f"[{idx}] {c.title} | {c.chapter}\n"
                f"    摘要: {c.summary[:100]}\n"
                f"    teaches: {teaches}\n"
                f"    公式: {formulae}"
            )
        candidates_text = "\n".join(cand_lines)

        target_methods = ", ".join(target.method_tags) if target.method_tags else "N/A"
        target_formulae = ", ".join(target.formulae_raw[:3]) if target.formulae_raw else "N/A"
        target_requires = ", ".join(target.requires_concepts) if target.requires_concepts else "N/A"
        target_teaches = ", ".join(target.teaches_concepts) if target.teaches_concepts else "N/A"

        system_prompt = self._prompts["system"]
        user_prompt = self._prompts["user_template"].format(
            target_title=target.title,
            target_summary=target.summary[:200],
            target_methods=target_methods,
            target_formulae=target_formulae,
            target_requires=target_requires,
            target_teaches=target_teaches,
            candidates_text=candidates_text,
        )

        try:
            response = await self.call_llm(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.2,
                response_format={"type": "json_object"},
                stage="prerequisite_discrimination",
            )
            result = self._extract_json(response)
            indices = result.get("prerequisite_indices", [])
            # Map indices back to card_ids
            prereq_ids = []
            for idx in indices:
                if isinstance(idx, int) and 0 <= idx < len(candidates):
                    prereq_ids.append(candidates[idx].card_id)
            return prereq_ids
        except Exception as e:
            _logger.error("LLM discrimination failed for %s: %s", target.card_id, e)
            return []

    # ------------------------------------------------------------------
    # Concept grouping
    # ------------------------------------------------------------------

    async def _build_concept_groupings(
        self,
        cards: list[DraftCard],
    ) -> list[dict]:
        """Cluster cards by concept overlap, then use LLM to name clusters."""
        clusters = _cluster_concepts(cards)
        if not clusters:
            _logger.info("No concept clusters found (all singletons)")
            return []

        _logger.info("Found %d concept clusters, naming with LLM...", len(clusters))

        # Format clusters for naming prompt
        cluster_texts = []
        for i, cluster in enumerate(clusters):
            cluster_texts.append(_format_cluster_for_prompt(i, cluster))

        system_prompt = self._prompts["concept_namer_system"]
        user_prompt = self._prompts["concept_namer_user"].format(
            n_clusters=len(clusters),
            clusters_text="\n\n".join(cluster_texts),
        )

        try:
            response = await self.call_llm(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.3,
                response_format={"type": "json_object"},
                stage="concept_naming",
            )
            named = self._extract_json(response)
            # Handle both array and object-with-array responses
            if isinstance(named, list):
                naming_data = named
            else:
                naming_data = named.get("clusters", named.get("concept_groupings", []))
        except Exception as e:
            _logger.error("Concept naming LLM failed: %s", e)
            naming_data = []

        # Build final concept groupings
        groupings = []
        for i, cluster in enumerate(clusters):
            # Find naming data for this cluster
            name_info = {}
            for nd in naming_data:
                if nd.get("cluster_id") == i:
                    name_info = nd
                    break

            # Determine chapter from majority vote
            chapter_counts: dict[str, int] = defaultdict(int)
            for c in cluster:
                chapter_counts[c.chapter] += 1
            chapter = max(chapter_counts, key=chapter_counts.get)

            groupings.append({
                "concept_id": f"concept_{i:03d}",
                "name": name_info.get("name", f"概念组_{i}"),
                "chapter": chapter,
                "topic": chapter,
                "difficulty": name_info.get("difficulty", 1),
                "card_ids": [c.card_id for c in cluster],
                "prerequisites": [],
                "description": name_info.get("description", ""),
            })

        covered = sum(len(g["card_ids"]) for g in groupings)
        _logger.info(
            "概念分组完成: %d 个分组, 覆盖 %d/%d 张卡 (%.0f%%)",
            len(groupings), covered, len(cards),
            100 * covered / len(cards) if cards else 0,
        )
        return groupings

    # ------------------------------------------------------------------
    # Foundation concepts (方案 D) — independent pass
    # ------------------------------------------------------------------

    async def build_foundation_concepts(
        self,
        cards: list[DraftCard],
        book_name: str,
    ) -> list[FoundationConcept]:
        """Public entry: detect book-external concepts, cluster, name, backfill cards."""
        return await self._build_foundation_layer(cards, book_name)

    async def _build_foundation_layer(
        self,
        cards: list[DraftCard],
        book_name: str,
    ) -> list[FoundationConcept]:
        """Detect book-external concepts, LLM groups them, backfill cards."""
        # 1. Detect external concepts
        all_taught = {c for card in cards for c in card.teaches_concepts}
        external_concepts: set[str] = set()
        for card in cards:
            for c in card.requires_concepts:
                if c not in all_taught:
                    external_concepts.add(c)

        if not external_concepts:
            _logger.info("No external concepts found — skipping foundation layer")
            return []

        sorted_external = sorted(external_concepts)
        _logger.info(
            "Found %d external concepts, sending to LLM for grouping",
            len(sorted_external),
        )

        # 2. LLM does both grouping and naming in one call
        concepts_text = "\n".join(f"  - {c}" for c in sorted_external)
        grouping_data: list[dict] = []
        try:
            system_prompt = self._prompts["foundation_grouper_system"]
            user_prompt = self._prompts["foundation_grouper_user"].format(
                book_name=book_name,
                n_concepts=len(sorted_external),
                concepts_text=concepts_text,
            )
            response = await self.call_llm(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.3,
                response_format={"type": "json_object"},
                stage="foundation_grouping",
            )
            result = self._extract_json(response)
            if isinstance(result, list):
                grouping_data = result
            else:
                grouping_data = result.get("groups", result.get("foundations", []))
        except Exception as e:
            _logger.error("Foundation grouping LLM failed: %s", e)
            return []

        # 3. Build FoundationConcept objects
        foundations: list[FoundationConcept] = []
        concept_to_fnd_id: dict[str, str] = {}

        for i, group in enumerate(grouping_data):
            covers = group.get("concepts", [])
            if not covers:
                continue
            fnd_id = f"fnd_{i:03d}"
            fc = FoundationConcept(
                concept_id=fnd_id,
                name=group.get("name", f"基础概念_{i}"),
                covers=sorted(covers),
                description=group.get("description", ""),
                difficulty=group.get("difficulty", 1),
                source_book=book_name,
            )
            foundations.append(fc)
            for concept_str in covers:
                concept_to_fnd_id[concept_str] = fnd_id

        # 4. Backfill cards: populate assumed_knowledge
        backfill_count = 0
        for card in cards:
            fnd_ids: set[str] = set()
            for c in card.requires_concepts:
                if c in concept_to_fnd_id:
                    fnd_ids.add(concept_to_fnd_id[c])
            if fnd_ids:
                card.assumed_knowledge = sorted(fnd_ids)
                backfill_count += 1

        _logger.info(
            "Foundation layer complete: %d concepts, backfilled %d/%d cards",
            len(foundations), backfill_count, len(cards),
        )
        return foundations

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_json(response: str) -> dict | list:
        """Extract JSON from LLM response, tolerating markdown fences."""
        text = response.strip()
        fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if fence_match:
            text = fence_match.group(1).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to find JSON object or array
            for pattern in [r"\{[\s\S]*\}", r"\[[\s\S]*\]"]:
                m = re.search(pattern, text)
                if m:
                    return json.loads(m.group())
            raise
