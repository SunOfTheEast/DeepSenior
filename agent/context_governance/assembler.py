"""上下文组装器 — 把原始字段按预算策略组装成最终 payload。

输出统一的 ContextAssemblyResult，供各 Agent 消费。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .budget_policy import BudgetPolicy
from .signature import context_signature
from .telemetry import log_assembly


@dataclass
class ContextAssemblyResult:
    """统一返回结构，各 Agent 的 projection builder 复用。"""
    payload: dict[str, Any]
    token_estimate: int
    coverage_status: str            # "full" | "partial" | "degraded"
    degraded_mode: bool
    warnings: list[str] = field(default_factory=list)
    dropped_fields: list[str] = field(default_factory=list)
    context_signature: str = ""
    kept_fields: list[str] = field(default_factory=list)
    original_field_count: int = 0
    final_field_count: int = 0
    original_char_count: int = 0
    final_char_count: int = 0
    max_total_chars: int = 0
    budget_remaining_chars: int = 0
    budget_utilization: float = 0.0
    field_char_counts_before: dict[str, int] = field(default_factory=dict)
    field_char_counts_after: dict[str, int] = field(default_factory=dict)

    def to_llm_context_metadata(self) -> dict[str, Any]:
        """导出供 LLM 调用日志串联的轻量元信息。"""
        return {
            "context_signature": self.context_signature,
            "coverage_status": self.coverage_status,
            "degraded_mode": self.degraded_mode,
            "warnings": list(self.warnings),
            "dropped_fields": list(self.dropped_fields),
            "kept_fields": list(self.kept_fields),
            "token_estimate": self.token_estimate,
            "final_char_count": self.final_char_count,
            "max_total_chars": self.max_total_chars,
            "budget_remaining_chars": self.budget_remaining_chars,
            "budget_utilization": self.budget_utilization,
        }


def _char_len(value: Any) -> int:
    """以字符数衡量字段体积，兼容非字符串值。"""
    return len(str(value))


def _estimate_tokens(text: str) -> int:
    """粗略估计 token 数（中文约 1.5 字/token，英文约 4 字符/token）。"""
    cn_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other_chars = len(text) - cn_chars
    return int(cn_chars * 1.5 + other_chars / 4)


def assemble(
    candidate: dict[str, str],
    policy: BudgetPolicy,
    *,
    warnings: list[str] | None = None,
    sig_parts: dict[str, str] | None = None,
) -> ContextAssemblyResult:
    """按预算策略组装上下文。

    Args:
        candidate: 字段名 -> 文本内容的候选 payload
        policy: 裁剪策略（定义字段优先级和总预算）
        warnings: 外部传入的告警（如 concept_link_missing）
        sig_parts: 用于生成 context_signature 的 key-value
    """
    warnings = list(warnings or [])
    dropped: list[str] = []
    payload: dict[str, str] = dict(candidate)
    before_field_sizes = {name: _char_len(value) for name, value in candidate.items()}

    # 按优先级从低到高裁剪
    total_chars = sum(before_field_sizes.values())
    if total_chars > policy.max_total_chars:
        for field_name in policy.trim_order():
            if field_name not in payload:
                continue
            if total_chars <= policy.max_total_chars:
                break
            removed_len = _char_len(payload[field_name])
            del payload[field_name]
            dropped.append(field_name)
            total_chars -= removed_len

    # coverage 状态
    if not dropped:
        coverage = "full"
    elif len(dropped) <= 2:
        coverage = "partial"
    else:
        coverage = "degraded"

    token_est = _estimate_tokens("".join(payload.values()))
    sig = context_signature(sig_parts or {})
    after_field_sizes = {name: _char_len(value) for name, value in payload.items()}
    final_chars = sum(after_field_sizes.values())
    max_total_chars = max(policy.max_total_chars, 0)
    budget_remaining = max(max_total_chars - final_chars, 0)
    budget_utilization = (
        round(final_chars / max_total_chars, 4)
        if max_total_chars
        else 0.0
    )

    result = ContextAssemblyResult(
        payload=payload,
        token_estimate=token_est,
        coverage_status=coverage,
        degraded_mode=coverage == "degraded",
        warnings=warnings,
        dropped_fields=dropped,
        context_signature=sig,
        kept_fields=list(payload.keys()),
        original_field_count=len(candidate),
        final_field_count=len(payload),
        original_char_count=sum(before_field_sizes.values()),
        final_char_count=final_chars,
        max_total_chars=max_total_chars,
        budget_remaining_chars=budget_remaining,
        budget_utilization=budget_utilization,
        field_char_counts_before=before_field_sizes,
        field_char_counts_after=after_field_sizes,
    )

    log_assembly(policy.name, candidate, result)
    return result
