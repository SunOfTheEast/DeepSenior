#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Pass 2b: Generate draft knowledge cards from section analysis + few-shot examples."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from agent.base_agent import BaseAgent
from agent.infra.logging import get_logger
from agent.knowledge.data_structures import PublishedKnowledgeCard
from .data_structures import DraftCard, SectionAnalysis, SectionOutline

_logger = get_logger("Knowledge.CardGenerator")


class CardGenerator(BaseAgent):
    """Pass 2b: Generate draft cards from section analysis + few-shot examples."""

    def __init__(
        self,
        few_shot_cards: list[PublishedKnowledgeCard] | None = None,
        noise_tags: dict | None = None,
        **kwargs,
    ):
        super().__init__(
            module_name="knowledge",
            agent_name="card_generator",
            **kwargs,
        )
        self._few_shot_cards = few_shot_cards or []
        self._noise_tags = noise_tags or {}
        prompt_path = Path(__file__).parent / "prompts" / "zh" / "card_generator.yaml"
        with open(prompt_path, "r", encoding="utf-8") as f:
            self._prompts = yaml.safe_load(f)

    async def process(
        self,
        analysis: SectionAnalysis,
        section_content: str,
        section: SectionOutline,
        chapter_name: str,
        chapter_index: int = 0,
        seq_start: int = 0,
    ) -> tuple[list[DraftCard], int]:
        """Generate draft knowledge cards for a section.

        Args:
            analysis: Pedagogical analysis from Pass 2a.
            section_content: Full text of the section extracted from the PDF.
            section: The section's outline metadata.
            chapter_name: Name of the chapter this section belongs to.
            chapter_index: Zero-based chapter index for opaque card_id.
            seq_start: Starting sequence number for card_id allocation.

        Returns:
            (list of DraftCard objects, next available seq number)
        """
        analysis_json = json.dumps(
            self._analysis_to_dict(analysis),
            ensure_ascii=False,
            indent=2,
        )
        few_shot_examples = self._format_few_shot_examples()

        # Inject noise tag examples into system prompt
        bad_method = self._noise_tags.get("method_tags", [
            "计算技巧", "结构分析", "验证", "概念辨析", "定义识别",
        ])
        bad_thinking = self._noise_tags.get("thinking_tags", [
            "概念应用", "性质应用", "条件判断", "检查验证",
        ])
        system_prompt = self._prompts["system"].replace(
            "{bad_method_examples}", "、".join(f'"{t}"' for t in bad_method[:8]),
        ).replace(
            "{bad_thinking_examples}", "、".join(f'"{t}"' for t in bad_thinking[:8]),
        )
        user_prompt = self._prompts["user_template"].format(
            chapter_name=chapter_name,
            section_title=section.title,
            analysis_json=analysis_json,
            section_content=section_content,
            few_shot_examples=few_shot_examples,
        )

        response = await self.call_llm(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=0.4,
            response_format={"type": "json_object"},
            stage="card_generation",
        )

        return self._parse_and_assign(
            response, chapter_name, chapter_index, seq_start,
            section.section_id, section.page_start, section.page_end,
            analysis,
        )

    # ------------------------------------------------------------------
    # Few-shot formatting
    # ------------------------------------------------------------------

    def _format_few_shot_examples(self) -> str:
        """Format existing PublishedKnowledgeCards as YAML blocks for the prompt."""
        if not self._few_shot_cards:
            return "（暂无示例卡片）"

        blocks: list[str] = []
        for card in self._few_shot_cards:
            card_dict = {
                "card_id": card.card_id,
                "card_type": card.card_type,
                "chapter": card.chapter,
                "title": card.title,
                "summary": card.summary,
                "general_methods": card.general_methods,
                "hints": card.hints,
                "common_mistakes": card.common_mistakes,
                "prerequisite_card_ids": card.prerequisite_card_ids,
                "problem_tags": card.problem_tags,
                "method_tags": card.method_tags,
                "thinking_tags": card.thinking_tags,
                "formula_cues": card.formula_cues,
            }
            blocks.append(yaml.dump(
                card_dict,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            ))

        return "\n---\n".join(blocks)

    # ------------------------------------------------------------------
    # Parsing + ID assignment
    # ------------------------------------------------------------------

    def _parse_and_assign(
        self,
        response: str,
        chapter_name: str,
        chapter_index: int,
        seq_start: int,
        section_id: str,
        page_start: int,
        page_end: int,
        analysis: SectionAnalysis,
    ) -> tuple[list[DraftCard], int]:
        """Parse LLM response + assign opaque IDs + build structure."""
        data = self._extract_json(response)
        cards_raw = data.get("cards", [])
        if not cards_raw:
            _logger.warning("LLM returned no cards for section %s", section_id)
            return [], seq_start

        return self._assign_ids_and_structure(
            cards_raw, chapter_index, seq_start,
            chapter_name, section_id, page_start, page_end, analysis,
        )

    def _assign_ids_and_structure(
        self,
        raw_cards: list[dict],
        chapter_index: int,
        seq_start: int,
        chapter_name: str,
        section_id: str,
        page_start: int,
        page_end: int,
        analysis: SectionAnalysis,
    ) -> tuple[list[DraftCard], int]:
        """Assign opaque card_ids, build children by inverting parent pointers, derive card_type.

        Returns:
            (list of DraftCard, next available seq number)
        """
        model = self.get_model()

        # Step 1: assign card_id for each raw card
        ref_to_id: dict[str, str] = {}
        seq = seq_start
        for raw in raw_cards:
            local_ref = str(raw.get("local_ref", f"r{seq - seq_start + 1}"))
            card_id = f"c_{chapter_index:02d}_{seq:03d}"
            ref_to_id[local_ref] = card_id
            raw["_card_id"] = card_id
            raw["_local_ref"] = local_ref
            seq += 1

        # Step 2: resolve parent_local_ref → parent_card_id
        for raw in raw_cards:
            parent_ref = raw.get("parent_local_ref")
            if parent_ref and parent_ref in ref_to_id:
                raw["_parent_card_id"] = ref_to_id[parent_ref]
            else:
                raw["_parent_card_id"] = None

        # Step 3: build children by inverting parent pointers
        children_map: dict[str, list[str]] = {}
        for raw in raw_cards:
            parent_id = raw["_parent_card_id"]
            if parent_id:
                children_map.setdefault(parent_id, []).append(raw["_card_id"])

        # Step 3.5: orphan adoption — parentless leaves find best anchor by concept overlap
        anchors_in_section = [
            raw for raw in raw_cards
            if raw["_parent_card_id"] is None and children_map.get(raw["_card_id"])
        ]
        if anchors_in_section:
            for raw in raw_cards:
                if raw["_parent_card_id"] is not None:
                    continue
                if children_map.get(raw["_card_id"]):
                    continue  # this is an anchor itself
                # Parentless leaf — find best anchor by concept overlap
                best_anchor, best_score = None, 0
                orphan_teaches = set(raw.get("teaches_concepts", []))
                orphan_requires = set(raw.get("requires_concepts", []))
                for anchor in anchors_in_section:
                    anchor_teaches = set(anchor.get("teaches_concepts", []))
                    score = (len(orphan_requires & anchor_teaches)
                             + len(orphan_teaches & anchor_teaches))
                    if score > best_score:
                        best_score = score
                        best_anchor = anchor
                if best_anchor and best_score >= 1:
                    raw["_parent_card_id"] = best_anchor["_card_id"]
                    children_map.setdefault(best_anchor["_card_id"], []).append(raw["_card_id"])
                    _logger.info(
                        "Orphan adopted: %s → %s (score=%d)",
                        raw["_card_id"], best_anchor["_card_id"], best_score,
                    )

        # Step 4: create DraftCard objects
        draft_cards: list[DraftCard] = []
        for raw in raw_cards:
            card_id = raw["_card_id"]
            children = children_map.get(card_id, [])
            card_type = "anchor" if children else "leaf"

            hints = self._normalize_hints(raw.get("hints", {}))
            card = DraftCard(
                card_id=card_id,
                card_type=card_type,
                parent_card_id=raw["_parent_card_id"],
                chapter=chapter_name,
                title=str(raw.get("title", "")),
                summary=str(raw.get("summary", "")),
                general_methods=raw.get("general_methods", []),
                hints=hints,
                common_mistakes=raw.get("common_mistakes", []),
                prerequisite_card_ids=[],  # Pass 3 fills these
                children=children,
                problem_tags=raw.get("problem_tags", []),
                method_tags=raw.get("method_tags", []),
                thinking_tags=raw.get("thinking_tags", []),
                formula_cues=raw.get("formula_cues", []),
                thought_ids=[],
                source_section_id=section_id,
                generation_model=model,
                figures=raw.get("figures", []),
                review_status="draft",
                teaches_concepts=raw.get("teaches_concepts", []),
                requires_concepts=raw.get("requires_concepts", []),
                aliases=[],
                formulae_spoken=raw.get("formulae_spoken", []),
                formulae_raw=raw.get("formulae_raw", []),
                source_page_start=page_start,
                source_page_end=page_end,
            )
            draft_cards.append(card)

        _logger.info(
            "Section %s: %d cards (%d anchor, %d leaf), IDs %s..%s",
            section_id, len(draft_cards),
            sum(1 for c in draft_cards if c.card_type == "anchor"),
            sum(1 for c in draft_cards if c.card_type == "leaf"),
            f"c_{chapter_index:02d}_{seq_start:03d}",
            f"c_{chapter_index:02d}_{seq - 1:03d}" if seq > seq_start else "N/A",
        )
        return draft_cards, seq

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_hints(hints_raw: dict | list) -> dict[int, str]:
        """Normalize hints to {int: str} regardless of LLM output quirks."""
        result: dict[int, str] = {}
        if isinstance(hints_raw, dict):
            for key, value in hints_raw.items():
                try:
                    result[int(key)] = str(value)
                except (ValueError, TypeError):
                    continue
        elif isinstance(hints_raw, list):
            for i, value in enumerate(hints_raw, start=1):
                result[i] = str(value)
        return result

    @staticmethod
    def _analysis_to_dict(analysis: SectionAnalysis) -> dict:
        """Convert SectionAnalysis to a plain dict for JSON serialization."""
        return {
            "section_id": analysis.section_id,
            "knowledge_atoms": [
                {"name": a.name, "atom_type": a.atom_type,
                 "importance": a.importance, "description": a.description}
                for a in analysis.knowledge_atoms
            ],
            "student_pain_points": analysis.student_pain_points,
            "hidden_traps": analysis.hidden_traps,
            "dependencies": [
                {"from_atom": d.from_atom, "to_atom": d.to_atom,
                 "relationship": d.relationship}
                for d in analysis.dependencies
            ],
            "hint_ideas": [
                {"target_atom": h.target_atom, "hint_level": h.hint_level,
                 "text": h.text}
                for h in analysis.hint_ideas
            ],
            "raw_content_summary": analysis.raw_content_summary,
            "teaches_concepts": analysis.teaches_concepts,
            "requires_concepts": analysis.requires_concepts,
            "formulae_spoken": analysis.formulae_spoken,
            "formulae_raw": analysis.formulae_raw,
        }

    @staticmethod
    def _extract_json(response: str) -> dict:
        """Extract JSON object from LLM response, tolerating markdown fences."""
        text = response.strip()
        fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if fence_match:
            text = fence_match.group(1).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            brace_match = re.search(r"\{[\s\S]*\}", text)
            if brace_match:
                return json.loads(brace_match.group())
            raise
