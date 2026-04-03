#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Pass 4 tag clustering: group fine-grained tags into coarse clusters."""

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from agent.base_agent import BaseAgent
from agent.infra.logging import get_logger
from .data_structures import DraftCard

_logger = get_logger("Knowledge.TagClusterer")


def _load_yaml_safe(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class TagClusterer(BaseAgent):
    """Cluster problem_tags, method_tags, thinking_tags into coarse types via LLM."""

    def __init__(self, drafts_base: Path | None = None, **kwargs):
        super().__init__(
            module_name="knowledge",
            agent_name="tag_clusterer",
            **kwargs,
        )
        self._drafts_base = drafts_base
        prompt_path = Path(__file__).parent / "prompts" / "zh" / "tag_clusterer.yaml"
        with open(prompt_path, "r", encoding="utf-8") as f:
            self._prompts = yaml.safe_load(f)

    async def process(self, cards: list[DraftCard]) -> dict:
        """Cluster all raw tags, write coarse labels back to cards.

        Returns:
            Raw cluster mappings dict (for persistence).
        """
        # 1. Collect unique tags per field
        fields = {
            "problem_type": sorted({t for c in cards for t in c.problem_tags}),
            "method_type": sorted({t for c in cards for t in c.method_tags}),
            "thinking_type": sorted({t for c in cards for t in c.thinking_tags}),
        }
        system_keys = {
            "problem_type": "system_problem",
            "method_type": "system_method",
            "thinking_type": "system_thinking",
        }

        total_raw = sum(len(v) for v in fields.values())
        if total_raw == 0:
            _logger.info("No tags found — skipping clustering")
            return {}

        # 1.5 Per-field whitelist filtering
        method_ref = self._build_method_reference(cards)
        problem_ref = self._build_problem_reference(cards)
        thinking_ref = self._build_thinking_reference(cards)

        ref_map = {
            "method_type": method_ref,
            "problem_type": problem_ref,
            "thinking_type": thinking_ref,
        }
        field_to_raw = {
            "problem_type": "problem_tags",
            "method_type": "method_tags",
            "thinking_type": "thinking_tags",
        }
        filtered_tags: dict[str, list[str]] = {
            "problem_tags": [], "method_tags": [], "thinking_tags": [],
        }

        for field_key, raw_key in field_to_raw.items():
            ref = ref_map[field_key]
            verified = []
            for tag in fields[field_key]:
                if tag in ref:
                    verified.append(tag)
                else:
                    filtered_tags[raw_key].append(tag)
            fields[field_key] = verified

        total_filtered = sum(len(v) for v in filtered_tags.values())
        total_verified = sum(len(v) for v in fields.values())
        _logger.info(
            "Whitelist filter: %d/%d tags verified, %d filtered out "
            "(problem %d→%d, method %d→%d, thinking %d→%d)",
            total_verified, total_raw, total_filtered,
            total_raw - total_verified + len(fields["problem_type"]), len(fields["problem_type"]),
            total_raw - total_verified + len(fields["method_type"]), len(fields["method_type"]),
            total_raw - total_verified + len(fields["thinking_type"]), len(fields["thinking_type"]),
        )

        if total_verified == 0:
            return {"filtered_tags": filtered_tags}

        # 2. Three parallel LLM calls
        async def _cluster_one(field_key: str, tags: list[str]) -> dict:
            if not tags:
                return {}
            tags_text = "\n".join(f"  - {t}" for t in tags)
            system_prompt = self._prompts[system_keys[field_key]]
            user_prompt = self._prompts["user_template"].format(
                n_tags=len(tags),
                tags_text=tags_text,
            )
            try:
                response = await self.call_llm(
                    user_prompt=user_prompt,
                    system_prompt=system_prompt,
                    temperature=0.3,
                    response_format={"type": "json_object"},
                    stage=f"tag_clustering_{field_key}",
                )
                result = self._extract_json(response)
                if isinstance(result, dict):
                    if field_key in result:
                        return result[field_key]
                    return result
            except Exception as e:
                _logger.error("Tag clustering failed for %s: %s", field_key, e)
            return {}

        results = await asyncio.gather(
            _cluster_one("problem_type", fields["problem_type"]),
            _cluster_one("method_type", fields["method_type"]),
            _cluster_one("thinking_type", fields["thinking_type"]),
        )

        cluster_data = {
            "problem_type": results[0],
            "method_type": results[1],
            "thinking_type": results[2],
            "filtered_tags": filtered_tags,
        }

        # 3. Build reverse mappings: raw_tag -> cluster_name
        reverse = {"problem_type": {}, "method_type": {}, "thinking_type": {}}
        for field_key in reverse:
            for cluster_name, tags in cluster_data[field_key].items():
                for tag in tags:
                    reverse[field_key][tag] = cluster_name

        # 4. Write back to cards
        backfill_count = 0
        for card in cards:
            p_types = sorted({reverse["problem_type"][t] for t in card.problem_tags if t in reverse["problem_type"]})
            m_types = sorted({reverse["method_type"][t] for t in card.method_tags if t in reverse["method_type"]})
            t_types = sorted({reverse["thinking_type"][t] for t in card.thinking_tags if t in reverse["thinking_type"]})
            if p_types or m_types or t_types:
                card.problem_type = p_types
                card.method_type = m_types
                card.thinking_type = t_types
                backfill_count += 1

        # 5. Extract applies-to (method_type → problem_type co-occurrence)
        applies_to_raw: dict[str, set[str]] = {}
        for card in cards:
            for m in card.method_type:
                if m not in applies_to_raw:
                    applies_to_raw[m] = set()
                for p in card.problem_type:
                    applies_to_raw[m].add(p)
        cluster_data["applies_to"] = {m: sorted(ps) for m, ps in applies_to_raw.items()}

        _logger.info(
            "Tag clustering complete: %d problem_type, %d method_type, %d thinking_type clusters, "
            "%d applies-to mappings, backfilled %d/%d cards",
            len(cluster_data["problem_type"]),
            len(cluster_data["method_type"]),
            len(cluster_data["thinking_type"]),
            len(cluster_data["applies_to"]),
            backfill_count, len(cards),
        )
        return cluster_data

    # ------------------------------------------------------------------
    # Per-field reference set builders
    # ------------------------------------------------------------------

    def _build_method_reference(self, cards: list[DraftCard]) -> set[str]:
        """method_tags whitelist: anchor titles + teaches_concepts + catalog names."""
        ref = set()
        # Anchor card titles = canonical method names
        for card in cards:
            if card.card_type == "anchor":
                ref.add(card.title)
        # teaches_concepts (methods are often taught)
        for card in cards:
            ref.update(card.teaches_concepts)
        # method_catalog slot names
        if self._drafts_base:
            catalogs_dir = self._drafts_base / "catalogs"
            if catalogs_dir.exists():
                for path in catalogs_dir.rglob("*.yaml"):
                    cat = _load_yaml_safe(path)
                    if isinstance(cat, dict):
                        for m in cat.get("methods", []):
                            ref.add(m.get("name", ""))
        _logger.info("Method reference: %d entries", len(ref))
        return ref

    def _build_problem_reference(self, cards: list[DraftCard]) -> set[str]:
        """problem_tags whitelist: method-group co-occurrence ≥2 OR global freq ≥3."""
        # Group cards by parent (method group)
        method_groups: dict[str, list[DraftCard]] = defaultdict(list)
        for card in cards:
            group_key = card.parent_card_id or card.card_id
            method_groups[group_key].append(card)

        ref = set()
        # Path 1: co-occur ≥2 within same method group
        for group_cards in method_groups.values():
            if len(group_cards) < 2:
                continue
            tag_freq = Counter()
            for card in group_cards:
                for t in card.problem_tags:
                    tag_freq[t] += 1
            for tag, cnt in tag_freq.items():
                if cnt >= 2:
                    ref.add(tag)

        # Path 2: global frequency ≥3 (cross-method structural tags)
        global_freq = Counter()
        for card in cards:
            for t in card.problem_tags:
                global_freq[t] += 1
        for tag, cnt in global_freq.items():
            if cnt >= 3:
                ref.add(tag)

        # Also add foundation concept covers (structural terms)
        if self._drafts_base:
            fnd_data = _load_yaml_safe(self._drafts_base / "foundation_concepts.yaml")
            if isinstance(fnd_data, list):
                for fc in fnd_data:
                    ref.update(fc.get("covers", []))

        _logger.info("Problem reference: %d entries", len(ref))
        return ref

    @staticmethod
    def _build_thinking_reference(cards: list[DraftCard]) -> set[str]:
        """thinking_tags whitelist: high-frequency tags (≥5 occurrences)."""
        freq = Counter()
        for card in cards:
            for t in card.thinking_tags:
                freq[t] += 1
        ref = {t for t, cnt in freq.items() if cnt >= 5}
        _logger.info("Thinking reference: %d entries (freq>=5)", len(ref))
        return ref

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
            for pattern in [r"\{[\s\S]*\}", r"\[[\s\S]*\]"]:
                m = re.search(pattern, text)
                if m:
                    return json.loads(m.group())
            raise
