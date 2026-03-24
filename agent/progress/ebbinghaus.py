#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Ebbinghaus 遗忘曲线模型（纯数学，无 LLM，无 IO）

公式：retention = level × e^(−elapsed_days / stability)
稳定性由 consecutive_correct 决定（答对次数越多，记忆越稳定）

缺省值设计（题目 DB 未接入时）：
  - 所有知识点使用相同的初始稳定性参数
  - 复习阈值 0.6（保留率低于 60% 时触发复习任务）
  - 优先级 URGENT 阈值 0.4（低于 40% 即将遗忘）
"""

import math
from datetime import datetime, timedelta

from agent.memory.data_structures import MasteryRecord
from .data_structures import DecayRecord, TaskPriority


# ---- 缺省超参数 ----
BASE_STABILITY_DAYS   = 1.5    # 从未练习过时的初始稳定性（天）
STABILITY_INCREMENT   = 0.8    # 每次连续答对增加的稳定天数
MAX_STABILITY_DAYS    = 30.0   # 稳定性上限（防止过长不复习）
REVIEW_THRESHOLD      = 0.60   # 保留率低于此值 → 建议复习
URGENT_THRESHOLD      = 0.40   # 保留率低于此值 → 紧急复习


def compute_stability(consecutive_correct: int) -> float:
    """
    根据连续答对次数计算记忆稳定性（单位：天）。

    consecutive_correct=0 → 1.5 天（约36小时后开始遗忘到 60%）
    consecutive_correct=5 → 5.5 天
    consecutive_correct=10 → 9.5 天（上限 30 天）
    """
    raw = BASE_STABILITY_DAYS + consecutive_correct * STABILITY_INCREMENT
    return min(raw, MAX_STABILITY_DAYS)


def compute_retention(record: MasteryRecord, now: datetime | None = None) -> float:
    """
    计算当前保留率。

    Returns:
        float in [0.0, 1.0]，1.0 表示刚练习、完全记得
    """
    if now is None:
        now = datetime.utcnow()
    elapsed_days = (now - record.last_practiced).total_seconds() / 86400.0
    stability = compute_stability(record.consecutive_correct)
    retention = record.level * math.exp(-elapsed_days / stability)
    return max(0.0, min(1.0, retention))


def next_review_date(record: MasteryRecord, target_retention: float = 0.70) -> datetime:
    """
    计算下次复习的最佳时间点（保留率降至 target_retention 时）。

    Args:
        target_retention: 触发复习的保留率阈值（默认 70%）

    Returns:
        datetime（UTC）
    """
    now = datetime.utcnow()
    if record.level <= 0 or record.level <= target_retention:
        return now  # 已经低于目标，立即复习

    stability = compute_stability(record.consecutive_correct)
    # solve: target = level × e^(−t/S)  →  t = −S × ln(target/level)
    days_until = -stability * math.log(target_retention / record.level)
    days_until = max(0.0, days_until)
    return record.last_practiced + timedelta(days=days_until)


def get_priority(retention: float) -> TaskPriority:
    if retention < URGENT_THRESHOLD:
        return TaskPriority.URGENT
    if retention < REVIEW_THRESHOLD:
        return TaskPriority.IMPORTANT
    return TaskPriority.SUGGESTED


def build_decay_record(concept_id: str, record: MasteryRecord) -> DecayRecord:
    """从 MasteryRecord 构建可读的衰减快照"""
    from .data_structures import DecayRecord
    now = datetime.utcnow()
    retention = compute_retention(record, now)
    stability = compute_stability(record.consecutive_correct)
    elapsed = (now - record.last_practiced).total_seconds() / 86400.0
    return DecayRecord(
        concept_id=concept_id,
        original_level=record.level,
        retention=retention,
        elapsed_days=elapsed,
        stability=stability,
        needs_review=retention < REVIEW_THRESHOLD,
        next_review_at=next_review_date(record),
    )


def rank_concepts_by_urgency(
    concept_mastery: dict[str, MasteryRecord],
) -> list[tuple[str, DecayRecord]]:
    """
    对所有知识点按复习紧迫度排序（保留率最低的排最前）。

    Returns:
        list of (concept_id, DecayRecord), sorted urgent first
    """
    records = [
        (cid, build_decay_record(cid, rec))
        for cid, rec in concept_mastery.items()
    ]
    # 只返回需要复习的，按保留率升序（最紧迫在前）
    due = [(cid, dr) for cid, dr in records if dr.needs_review]
    due.sort(key=lambda x: x[1].retention)
    return due
