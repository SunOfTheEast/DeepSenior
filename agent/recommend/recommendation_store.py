#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
RecommendationStore — 推荐记录持久化

轻量存储，用于：
  - 避免重复推荐同一题
  - 追踪推荐历史供分析
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.infra.logging import get_logger

logger = get_logger("Recommend.Store")

_DEFAULT_BASE = Path(__file__).resolve().parents[2] / "data" / "memory"


class RecommendationStore:

    def __init__(self, base_dir: str | Path | None = None):
        self.base_dir = Path(base_dir) if base_dir else _DEFAULT_BASE

    def save_recommendation(
        self,
        student_id: str,
        recommendation: dict[str, Any],
    ) -> None:
        """保存一条推荐记录。"""
        dir_path = self.base_dir / student_id / "recommendations"
        dir_path.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        record = {
            "timestamp": datetime.utcnow().isoformat(),
            "type": str(recommendation.get("type", "")),
            "problem_id": recommendation.get("problem_id", ""),
            "explanation": recommendation.get("explanation", ""),
            "recommended_problems": recommendation.get("recommended_problems", []),
        }
        path = dir_path / f"{ts}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

    def get_recent_recommendations(
        self,
        student_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """获取最近的推荐记录（按时间倒序）。"""
        dir_path = self.base_dir / student_id / "recommendations"
        if not dir_path.exists():
            return []
        records: list[dict[str, Any]] = []
        for path in sorted(dir_path.glob("*.json"), reverse=True):
            if len(records) >= limit:
                break
            try:
                with open(path, encoding="utf-8") as f:
                    records.append(json.load(f))
            except Exception as e:
                logger.warning(f"Failed to load recommendation {path}: {e}")
        return records

    def get_recently_recommended_problem_ids(
        self,
        student_id: str,
        limit: int = 20,
    ) -> set[str]:
        """获取最近推荐过的题目 ID 集合。"""
        records = self.get_recent_recommendations(student_id, limit)
        ids: set[str] = set()
        for r in records:
            pid = r.get("problem_id", "")
            if pid:
                ids.add(pid)
            for rp in r.get("recommended_problems", []):
                rpid = rp.get("problem_id", "")
                if rpid:
                    ids.add(rpid)
        return ids
