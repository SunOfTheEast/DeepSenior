#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Shared parsing helpers for knowledge agents."""

from __future__ import annotations

import json
import re


class JsonParseMixin:
    """Mixin providing common JSON parsing and confidence normalization."""

    @staticmethod
    def _parse_json(response: str) -> dict:
        try:
            return json.loads(response.strip())
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", response)
            if match:
                return json.loads(match.group())
            raise

    @staticmethod
    def _normalize_confidence(value: object) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0
