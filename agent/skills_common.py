#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Shared skill utilities for module registries.
"""

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class SkillMeta:
    """Skill metadata for discovery and docs."""
    name: str
    description: str
    inputs: list[str] = field(default_factory=list)
    output: str = ""
    tags: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        tag_str = ", ".join(self.tags)
        return f"[{self.name}] ({tag_str}) — {self.description}"


def wrap_sync_as_async(fn: Callable) -> Callable:
    """Wrap sync callable as async callable."""

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    wrapper.__name__ = fn.__name__
    return wrapper
