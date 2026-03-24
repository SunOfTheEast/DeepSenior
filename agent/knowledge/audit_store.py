#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AuditStore — RagAuditEntry 持久化与查询

将 RagAuditEntry 追加写入 JSONL 文件，支持：
  - append(): 追加一条或多条 audit entry
  - query():  按 chapter / task_type / 时间范围筛选
  - stats():  按 task_type 统计计数
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.infra.logging import get_logger

from .data_structures import RagAuditEntry


class AuditStore:
    """Append-only JSONL store for RAG audit entries."""

    def __init__(self, path: str | Path | None = None):
        self._path = Path(path) if path else (
            Path(__file__).resolve().parents[2] / "data" / "rag_audit" / "entries.jsonl"
        )
        self.logger = get_logger("Knowledge.AuditStore")

    def append(self, entries: list[RagAuditEntry] | RagAuditEntry) -> int:
        """追加 audit entry 到 JSONL 文件，返回写入条数。"""
        if isinstance(entries, RagAuditEntry):
            entries = [entries]
        if not entries:
            return 0
        self._path.parent.mkdir(parents=True, exist_ok=True)
        now_iso = datetime.utcnow().isoformat()
        count = 0
        with open(self._path, "a", encoding="utf-8") as f:
            for entry in entries:
                record = asdict(entry)
                record["_ts"] = now_iso
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1
        self.logger.debug(f"Appended {count} audit entries to {self._path}")
        return count

    def query(
        self,
        *,
        chapter: str | None = None,
        task_type: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """按条件筛选 audit 记录。"""
        if not self._path.exists():
            return []
        results: list[dict[str, Any]] = []
        since_iso = since.isoformat() if since else None
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if chapter and record.get("chapter") != chapter:
                    continue
                if task_type and record.get("task_type") != task_type:
                    continue
                if since_iso and record.get("_ts", "") < since_iso:
                    continue
                results.append(record)
                if len(results) >= limit:
                    break
        return results

    def stats(self) -> dict[str, int]:
        """按 task_type 统计各类 audit entry 数量。"""
        if not self._path.exists():
            return {}
        counts: dict[str, int] = {}
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                tt = record.get("task_type", "unknown")
                counts[tt] = counts.get(tt, 0) + 1
        return counts

    def count(self) -> int:
        """总条目数。"""
        if not self._path.exists():
            return 0
        with open(self._path, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
