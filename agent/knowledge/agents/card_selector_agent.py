#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""CardSelectorAgent - selects final cards from candidate summaries."""

from __future__ import annotations

from agent.base_agent import BaseAgent

from ._parsing import JsonParseMixin
from ..data_structures import CardSelectorRequest, CardSelectorResult


class CardSelectorAgent(JsonParseMixin, BaseAgent):
    """Selects the final 1-3 cards from candidate summaries."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        language: str = "zh",
        api_version: str | None = None,
        binding: str = "openai",
    ):
        super().__init__(
            module_name="knowledge",
            agent_name="card_selector_agent",
            api_key=api_key,
            base_url=base_url,
            api_version=api_version,
            language=language,
            binding=binding,
        )

    async def process(self, request: CardSelectorRequest) -> CardSelectorResult:
        return await self.select(request)

    async def select(self, request: CardSelectorRequest) -> CardSelectorResult:
        if not request.candidate_cards:
            return CardSelectorResult(selected_card_ids=[], confidence=0.0)

        system_prompt = self.get_prompt("system")
        user_template = self.get_prompt("user_template")
        if not system_prompt or not user_template:
            return self._fallback_select(request)

        user_prompt = user_template.format(
            chapter=request.chapter,
            topic=request.topic,
            retrieval_goal=request.retrieval_goal,
            student_approach=request.student_approach or "（无）",
            student_work=request.student_work or "（无）",
            focus_terms="、".join(request.focus_terms) or "（无）",
            candidate_cards=self._fmt_candidates(request),
            top_k=request.top_k,
        )
        try:
            response = await self.call_llm(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                response_format={"type": "json_object"},
                temperature=0.1,
                stage="card_selection",
            )
            return self._parse_response(response, request)
        except Exception:
            return self._fallback_select(request)

    def _parse_response(self, response: str, request: CardSelectorRequest) -> CardSelectorResult:
        data = self._parse_json(response)
        allowed = {card.card_id for card in request.candidate_cards}
        selected: list[str] = []
        for card_id in data.get("selected_card_ids", []):
            if card_id in allowed and card_id not in request.target_card_ids and card_id not in selected:
                selected.append(card_id)
        selected = selected[:request.top_k]
        if not selected:
            return self._fallback_select(request)
        return CardSelectorResult(
            selected_card_ids=selected,
            additional_need=str(data.get("additional_need", "") or "").strip() or None,
            additional_reason=str(data.get("additional_reason", "") or "").strip() or None,
            confidence=self._normalize_confidence(data.get("confidence", 0.0)),
        )

    def _fallback_select(self, request: CardSelectorRequest) -> CardSelectorResult:
        selected: list[str] = []
        for card in request.candidate_cards:
            if card.card_id in request.target_card_ids:
                continue
            if card.card_id not in selected:
                selected.append(card.card_id)
            if len(selected) >= request.top_k:
                break
        confidence = 0.65 if selected else 0.0
        return CardSelectorResult(selected_card_ids=selected, confidence=confidence)

    @staticmethod
    def _fmt_candidates(request: CardSelectorRequest) -> str:
        lines: list[str] = []
        for card in request.candidate_cards:
            cues = "、".join(card.formula_cues) or "（无）"
            lines.append(
                f"- {card.card_id} | {card.title}\n"
                f"  摘要：{card.summary}\n"
                f"  关键点：{card.key_insight}\n"
                f"  公式线索：{cues}"
            )
        return "\n".join(lines)


