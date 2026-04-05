#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DigestStore - Digest 层（L1.5）持久化

目录结构：
  {base_dir}/{student_id}/digests/
    weekly/{2026-W14}.json
    chapter/{解析几何}.json
"""

import json
from pathlib import Path

from agent.infra.logging import get_logger

from .data_structures import MemoryDigest


class DigestStore:
    """Digest 文件系统持久化层。"""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.logger = get_logger("DigestStore")

    def save_digest(self, digest: MemoryDigest) -> None:
        path = self._digest_path(digest.student_id, digest.digest_type, digest.period_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(digest.to_dict(), f, ensure_ascii=False, indent=2)
        tmp_path.replace(path)
        self.logger.debug(
            f"Digest saved: {digest.student_id}/{digest.digest_type}/{digest.period_key}"
        )

    def load_digest(
        self,
        student_id: str,
        digest_type: str,
        period_key: str,
    ) -> MemoryDigest | None:
        path = self._digest_path(student_id, digest_type, period_key)
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return MemoryDigest.from_dict(json.load(f))
        except Exception as e:
            self.logger.warning(f"Failed to load digest {path}: {e}")
            return None

    def list_digests(
        self,
        student_id: str,
        digest_type: str | None = None,
    ) -> list[MemoryDigest]:
        """列出某学生的所有 digest（可选按类型过滤），按时间倒序。"""
        digests: list[MemoryDigest] = []
        types = [digest_type] if digest_type else ["weekly", "chapter"]
        for dt in types:
            dir_path = self.base_dir / student_id / "digests" / dt
            if not dir_path.exists():
                continue
            for path in dir_path.glob("*.json"):
                try:
                    with open(path, encoding="utf-8") as f:
                        digests.append(MemoryDigest.from_dict(json.load(f)))
                except Exception as e:
                    self.logger.warning(f"Failed to load digest {path}: {e}")
        digests.sort(key=lambda d: d.created_at, reverse=True)
        return digests

    def _digest_path(self, student_id: str, digest_type: str, period_key: str) -> Path:
        safe_key = period_key.replace("/", "_").replace("\\", "_")
        return self.base_dir / student_id / "digests" / digest_type / f"{safe_key}.json"
