#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AuditStore — RagAuditEntry 持久化与查询

将 RagAuditEntry 追加写入 JSONL 文件，支持：
  - append(): 追加一条或多条 audit entry
  - query():  按 chapter / task_type / status / 时间范围筛选
  - stats():  按 task_type 或 status 统计计数
  - get_by_id(): 按 id 查找单条记录
  - update_status(): 状态机推进 (pending → proposed → approved/rejected → done)
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.infra.logging import get_logger

from .data_structures import AuditStatus, RagAuditEntry

_VALID_TRANSITIONS: dict[str, set[str]] = {
    AuditStatus.PENDING.value: {AuditStatus.PROPOSED.value, AuditStatus.REJECTED.value},
    AuditStatus.PROPOSED.value: {AuditStatus.APPROVED.value, AuditStatus.REJECTED.value},
    AuditStatus.APPROVED.value: {AuditStatus.DONE.value},
    AuditStatus.REJECTED.value: set(),
    AuditStatus.DONE.value: set(),
}


class AuditStore:
    """Append-only JSONL store for RAG audit entries with status lifecycle."""

    def __init__(self, path: str | Path | None = None):
        self._path = Path(path) if path else (
            Path(__file__).resolve().parents[2] / "data" / "rag_audit" / "entries.jsonl"
        )
        self.logger = get_logger("Knowledge.AuditStore")

    @property
    def path(self) -> Path:
        return self._path

    # ── Write ────────────────────────────────────────────────────────────

    def append(self, entries: list[RagAuditEntry] | RagAuditEntry) -> int:
        """追加 audit entry 到 JSONL 文件，返回写入条数。"""
        if isinstance(entries, RagAuditEntry):
            entries = [entries]
        if not entries:
            return 0
        self._path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with open(self._path, "a", encoding="utf-8") as f:
            for entry in entries:
                record = asdict(entry)
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1
        self.logger.debug(f"Appended {count} audit entries to {self._path}")
        return count

    # ── Status transitions ───────────────────────────────────────────────

    def update_status(self, entry_id: str, new_status: str, *, notes: str | None = None) -> bool:
        """推进 audit entry 状态机，返回是否成功。

        状态机：pending → proposed → approved/rejected → done
        通过原地重写 JSONL 实现（文件规模可控）。
        """
        if new_status not in {s.value for s in AuditStatus}:
            self.logger.warning(f"Invalid status: {new_status}")
            return False

        if not self._path.exists():
            return False

        lines = self._path.read_text(encoding="utf-8").splitlines()
        updated = False
        new_lines: list[str] = []

        for line in lines:
            if not line.strip():
                new_lines.append(line)
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                new_lines.append(line)
                continue

            if record.get("id") == entry_id and not updated:
                old_status = record.get("status", AuditStatus.PENDING.value)
                allowed = _VALID_TRANSITIONS.get(old_status, set())
                if new_status not in allowed:
                    self.logger.warning(
                        f"Invalid transition: {old_status} → {new_status} (entry {entry_id})"
                    )
                    return False
                record["status"] = new_status
                record["updated_at"] = datetime.now(timezone.utc).isoformat()
                if notes is not None:
                    record["notes"] = notes
                new_lines.append(json.dumps(record, ensure_ascii=False))
                updated = True
            else:
                new_lines.append(line)

        if updated:
            self._path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            self.logger.debug(f"Updated entry {entry_id} → {new_status}")
        return updated

    # ── Read ─────────────────────────────────────────────────────────────

    def get_by_id(self, entry_id: str) -> dict[str, Any] | None:
        """按 id 查找单条记录。"""
        if not self._path.exists():
            return None
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("id") == entry_id:
                return record
        return None

    def query(
        self,
        *,
        chapter: str | None = None,
        task_type: str | None = None,
        status: str | None = None,
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
                if status and record.get("status") != status:
                    continue
                if since_iso and record.get("created_at", "") < since_iso:
                    continue
                results.append(record)
                if len(results) >= limit:
                    break
        return results

    def get_pending(self, limit: int = 50) -> list[dict[str, Any]]:
        """获取所有 pending 状态的 audit 条目。"""
        return self.query(status=AuditStatus.PENDING.value, limit=limit)

    def get_actionable(self, limit: int = 50) -> list[dict[str, Any]]:
        """获取 pending + proposed（需要处理的）条目。"""
        if not self._path.exists():
            return []
        results: list[dict[str, Any]] = []
        actionable = {AuditStatus.PENDING.value, AuditStatus.PROPOSED.value}
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("status") in actionable:
                    results.append(record)
                    if len(results) >= limit:
                        break
        return results

    # ── Stats ────────────────────────────────────────────────────────────

    def stats(self, *, group_by: str = "task_type") -> dict[str, int]:
        """按指定字段统计各类 audit entry 数量。支持 group_by: task_type, status, chapter。"""
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
                key = record.get(group_by, "unknown")
                counts[key] = counts.get(key, 0) + 1
        return counts

    def coverage_gaps(self) -> list[dict[str, Any]]:
        """找出 empty_slot 和 low_confidence 条目，按 chapter+topic 聚合，
        返回按出现次数降序排列的缺口列表，用于指导内容建设优先级。"""
        if not self._path.exists():
            return []
        gap_types = {"empty_slot", "low_router_confidence", "low_selector_confidence"}
        aggregated: dict[str, dict[str, Any]] = {}
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("task_type") not in gap_types:
                    continue
                key = f"{record.get('chapter', '?')}:{record.get('topic', '?')}"
                if key not in aggregated:
                    aggregated[key] = {
                        "chapter": record.get("chapter"),
                        "topic": record.get("topic"),
                        "count": 0,
                        "task_types": set(),
                        "approaches": set(),
                    }
                agg = aggregated[key]
                agg["count"] += 1
                agg["task_types"].add(record.get("task_type"))
                if record.get("student_approach"):
                    agg["approaches"].add(record["student_approach"])

        result = []
        for agg in sorted(aggregated.values(), key=lambda x: -x["count"]):
            result.append({
                "chapter": agg["chapter"],
                "topic": agg["topic"],
                "count": agg["count"],
                "task_types": sorted(agg["task_types"]),
                "sample_approaches": sorted(agg["approaches"])[:5],
            })
        return result

    def count(self) -> int:
        """总条目数。"""
        if not self._path.exists():
            return 0
        with open(self._path, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
