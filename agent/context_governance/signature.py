"""上下文签名生成 — 供缓存、诊断和回归对比使用。"""

from __future__ import annotations

import hashlib


def context_signature(parts: dict[str, str]) -> str:
    """根据关键标识字段生成稳定签名。

    Args:
        parts: 如 {"task": "planner", "question_id": "q123",
                    "active_solution_id": "s1", ...}

    Returns:
        16 位 hex 签名
    """
    canonical = "|".join(f"{k}={v}" for k, v in sorted(parts.items()) if v)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
