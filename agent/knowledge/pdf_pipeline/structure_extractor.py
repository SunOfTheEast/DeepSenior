#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Pass 1: Extract hierarchical outline from textbook TOC text."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from agent.base_agent import BaseAgent
from .data_structures import BookOutline, SectionOutline


class StructureExtractor(BaseAgent):
    """Pass 1: Extract hierarchical outline from textbook TOC text."""

    def __init__(self, **kwargs):
        super().__init__(
            module_name="knowledge",
            agent_name="structure_extractor",
            **kwargs,
        )
        prompt_path = Path(__file__).parent / "prompts" / "zh" / "structure_extractor.yaml"
        with open(prompt_path, "r", encoding="utf-8") as f:
            self._prompts = yaml.safe_load(f)

    async def process(self, toc_text: str, book_name: str) -> BookOutline:
        """Extract book outline from TOC text via LLM.

        Args:
            toc_text: Raw table-of-contents text extracted from the PDF.
            book_name: Human-readable name of the textbook.

        Returns:
            BookOutline with a tree of SectionOutline nodes.
        """
        system_prompt = self._prompts["system"]
        user_prompt = self._prompts["user_template"].format(
            toc_text=toc_text,
            book_name=book_name,
        )

        response = await self.call_llm(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=0.2,
            response_format={"type": "json_object"},
            stage="structure_extraction",
        )

        return self._parse_response(response, book_name)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_response(self, response: str, book_name: str) -> BookOutline:
        """Parse the LLM JSON response into a BookOutline."""
        data = self._extract_json(response)
        chapters_raw = data.get("chapters", [])
        if not chapters_raw:
            self.logger.warning("LLM returned no chapters; producing empty outline")

        chapters = [self._parse_section(ch) for ch in chapters_raw]
        total_pages = self._compute_total_pages(chapters)

        return BookOutline(
            book_name=book_name,
            chapters=chapters,
            total_pages=total_pages,
            extraction_model=self.get_model(),
            extraction_timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def _parse_section(self, raw: dict) -> SectionOutline:
        """Recursively parse a section dict into a SectionOutline."""
        children = [self._parse_section(child) for child in raw.get("children", [])]
        return SectionOutline(
            section_id=str(raw.get("section_id", "")),
            title=str(raw.get("title", "")),
            level=int(raw.get("level", 1)),
            page_start=int(raw.get("page_start", -1)),
            page_end=int(raw.get("page_end", -1)),
            knowledge_types=raw.get("knowledge_types", []),
            section_type=raw.get("section_type", "content"),
            children=children,
        )

    @staticmethod
    def _compute_total_pages(chapters: list[SectionOutline]) -> int:
        """Infer total page count from the outermost sections."""
        max_page = 0
        for ch in chapters:
            if ch.page_end > max_page:
                max_page = ch.page_end
        return max_page

    @staticmethod
    def _extract_json(response: str) -> dict:
        """Extract JSON object from LLM response, tolerating markdown fences and truncation."""
        text = response.strip()
        # Strip ```json ... ``` wrappers if present
        fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if fence_match:
            text = fence_match.group(1).strip()
        # Find the JSON object
        brace_start = text.find("{")
        if brace_start >= 0:
            text = text[brace_start:]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to repair truncated JSON by closing open brackets
            repaired = StructureExtractor._repair_truncated_json(text)
            if repaired:
                try:
                    return json.loads(repaired)
                except json.JSONDecodeError:
                    pass
            raise

    @staticmethod
    def _repair_truncated_json(text: str) -> str | None:
        """Attempt to close unclosed brackets/braces in truncated JSON."""
        # Remove trailing incomplete key-value pair
        text = re.sub(r',\s*"[^"]*"?\s*$', '', text)
        text = re.sub(r',\s*$', '', text)
        # Count unclosed brackets
        opens = []
        in_string = False
        escape = False
        for ch in text:
            if escape:
                escape = False
                continue
            if ch == '\\':
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch in ('{', '['):
                opens.append(ch)
            elif ch == '}' and opens and opens[-1] == '{':
                opens.pop()
            elif ch == ']' and opens and opens[-1] == '[':
                opens.pop()
        if not opens:
            return None
        # Close all open brackets in reverse order
        closers = {'[': ']', '{': '}'}
        suffix = ''.join(closers[b] for b in reversed(opens))
        return text + suffix
