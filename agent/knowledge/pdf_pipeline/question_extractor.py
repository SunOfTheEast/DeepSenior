#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Pass 2c: Extract questions (archetypes + exercises) from PDF sections."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from agent.base_agent import BaseAgent
from agent.infra.logging import get_logger
from .data_structures import DraftQuestion, SectionAnalysis, SectionOutline

_logger = get_logger("Knowledge.QuestionExtractor")


class QuestionExtractor(BaseAgent):
    """Pass 2c: Extract example problems (archetypes) and exercises from sections."""

    def __init__(self, **kwargs):
        super().__init__(
            module_name="knowledge",
            agent_name="question_extractor",
            **kwargs,
        )
        prompt_path = Path(__file__).parent / "prompts" / "zh" / "question_extractor.yaml"
        with open(prompt_path, "r", encoding="utf-8") as f:
            self._prompts = yaml.safe_load(f)

    async def process(
        self,
        analysis: SectionAnalysis | None,
        section_content: str,
        section: SectionOutline,
        chapter_name: str,
        chapter_index: int = 0,
        seq_start: int = 0,
    ) -> tuple[list[DraftQuestion], int]:
        """Extract questions from a single section.

        Returns:
            (list of DraftQuestion, next available seq number)
        """
        section_type = getattr(section, "section_type", "content")
        # Fallback: infer from title if section_type not set
        if section_type == "content":
            title_lower = section.title
            if any(kw in title_lower for kw in ("习题", "练习", "思考题", "训练")):
                section_type = "exercise"
            elif any(kw in title_lower for kw in ("答案", "解答", "参考答案")):
                section_type = "answer"

        if section_type == "answer":
            return [], seq_start

        # Select prompt based on section type
        if section_type == "exercise":
            system_prompt = self._prompts["system_exercise"]
            question_type = "exercise"
            question_type_label = "练习题"
        else:
            system_prompt = self._prompts["system_archetype"]
            question_type = "archetype"
            question_type_label = "例题"

        user_prompt = self._prompts["user_template"].format(
            chapter_name=chapter_name,
            section_title=section.title,
            section_content=section_content[:8000],  # cap to avoid overflow
            question_type_label=question_type_label,
        )

        model = self.get_model()

        try:
            response = await self.call_llm(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.2,
                response_format={"type": "json_object"},
                stage="question_extraction",
            )
            result = self._extract_json(response)
        except Exception as e:
            _logger.error("Question extraction failed for %s: %s", section.section_id, e)
            return [], seq_start

        raw_questions = []
        if isinstance(result, dict):
            raw_questions = result.get("questions", [])
        elif isinstance(result, list):
            raw_questions = result

        # Build DraftQuestion objects
        questions: list[DraftQuestion] = []
        seq = seq_start
        for raw in raw_questions:
            stem = str(raw.get("stem", "")).strip()
            if not stem:
                continue
            question_id = f"q_{chapter_index:02d}_{seq:03d}"
            q = DraftQuestion(
                question_id=question_id,
                chapter=chapter_name,
                section_id=section.section_id,
                question_type=question_type,
                source_label=str(raw.get("source_label", "")),
                stem=stem,
                solution_text=str(raw.get("solution_text", "")) if question_type == "archetype" else "",
                difficulty=int(raw.get("difficulty", 0)),
                source_page=section.page_start,
                generation_model=model,
            )
            questions.append(q)
            seq += 1

        _logger.info(
            "Section %s: extracted %d %s questions",
            section.section_id, len(questions), question_type,
        )
        return questions, seq

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
