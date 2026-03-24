#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Shared utilities for agent modules.

- compact_text: 去空白、小写化，用于关键词匹配
- safe_parse_json: 宽容 JSON 解析（支持 LLM 输出中嵌套的 JSON）
"""

import json
import re


def compact_text(message: str) -> str:
    """去除所有空白并转小写，用于中文关键词匹配。"""
    return re.sub(r"\s+", "", (message or "").lower())


def safe_parse_json(response: str) -> dict:
    """宽容解析 JSON：先尝试整体解析，失败后提取第一个 {...} 块。"""
    text = (response or "").strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group())
        except (json.JSONDecodeError, ValueError):
            pass
    return {}
