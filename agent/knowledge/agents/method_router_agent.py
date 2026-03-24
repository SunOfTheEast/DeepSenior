#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""MethodRouterAgent - closed-menu method routing for RAG v2."""

from __future__ import annotations

import re

from agent.base_agent import BaseAgent

from ._parsing import JsonParseMixin
from ..data_structures import MethodRouterRequest, MethodRouterResult, MethodSlot


class MethodRouterAgent(JsonParseMixin, BaseAgent):
    """Routes student work to a known method slot."""

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
            agent_name="method_router_agent",
            api_key=api_key,
            base_url=base_url,
            api_version=api_version,
            language=language,
            binding=binding,
        )

    async def process(self, request: MethodRouterRequest) -> MethodRouterResult:
        return await self.route(request)

    async def route(self, request: MethodRouterRequest) -> MethodRouterResult:
        system_prompt = self.get_prompt("system")
        user_template = self.get_prompt("user_template")
        if not system_prompt or not user_template:
            return self._fallback_route(request, reason="prompts_not_configured")

        topic_methods = self._fmt_methods(request.topic_methods)
        cross_methods = self._fmt_methods(request.cross_topic_methods)
        user_prompt = user_template.format(
            chapter=request.chapter,
            topic=request.topic,
            problem_text=request.problem_text,
            student_work=request.student_work,
            student_approach=request.student_approach or "（无）",
            topic_methods=topic_methods,
            cross_topic_methods=cross_methods,
        )
        try:
            response = await self.call_llm(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                response_format={"type": "json_object"},
                temperature=0.1,
                stage="method_routing",
            )
            return self._parse_response(response, request)
        except Exception:
            return self._fallback_route(request, reason="llm_fallback")

    def _parse_response(self, response: str, request: MethodRouterRequest) -> MethodRouterResult:
        data = self._parse_json(response)
        allowed = {slot.slot_id for slot in request.topic_methods + request.cross_topic_methods}

        primary_slot = data.get("primary_slot")
        if primary_slot not in allowed:
            primary_slot = None

        cross_slots: list[str] = []
        for slot_id in data.get("cross_slots", []):
            if slot_id in allowed and slot_id != primary_slot and slot_id not in cross_slots:
                cross_slots.append(slot_id)
        cross_slots = cross_slots[:2]

        slot_candidates: list[str] = []
        if primary_slot:
            slot_candidates.append(primary_slot)
        for slot_id in data.get("slot_candidates", []):
            if slot_id in allowed and slot_id not in slot_candidates:
                slot_candidates.append(slot_id)
        slot_candidates = slot_candidates[:3]

        confidence = self._normalize_confidence(data.get("confidence", 0.0))
        reasoning = str(data.get("reasoning", "") or "").strip() or "llm_routing"

        return MethodRouterResult(
            primary_slot=primary_slot,
            cross_slots=cross_slots,
            confidence=confidence,
            reasoning=reasoning,
            slot_candidates=slot_candidates,
        )

    def _fallback_route(self, request: MethodRouterRequest, *, reason: str) -> MethodRouterResult:
        combined = " ".join(
            part for part in [request.problem_text, request.student_work, request.student_approach or ""] if part
        ).lower()
        scored: list[tuple[float, MethodSlot]] = []
        for slot in request.topic_methods + request.cross_topic_methods:
            score = self._score_slot(slot, combined)
            if score > 0:
                scored.append((score, slot))
        scored.sort(key=lambda item: (-item[0], item[1].slot_id))

        if not scored:
            return MethodRouterResult(
                primary_slot=None,
                cross_slots=[],
                confidence=0.0,
                reasoning=reason,
                slot_candidates=[],
            )

        primary = scored[0][1].slot_id
        cross = [slot.slot_id for _, slot in scored[1:3] if slot.slot_id != primary]
        confidence = min(0.85, 0.45 + scored[0][0] * 0.1)
        return MethodRouterResult(
            primary_slot=primary,
            cross_slots=cross,
            confidence=confidence,
            reasoning=reason,
            slot_candidates=[slot.slot_id for _, slot in scored[:3]],
        )

    @staticmethod
    def _score_slot(slot: MethodSlot, combined: str) -> float:
        score = 0.0
        for token in MethodRouterAgent._extract_keywords(f"{slot.name} {slot.trigger}"):
            if token and token in combined:
                score += 1.0
        return score

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        raw = re.split(r"[\s,，。；;、/()（）:+-]+", text.lower())
        return [token for token in raw if len(token) >= 2 and token not in {"学生", "使用", "方法", "处理"}]

    @staticmethod
    def _fmt_methods(methods: list[MethodSlot]) -> str:
        if not methods:
            return "（无）"
        return "\n".join(f"- {slot.slot_id} | {slot.name} | {slot.trigger}" for slot in methods)


