"""上下文组装的观测埋点。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from agent.infra.logging import get_logger

if TYPE_CHECKING:
    from .assembler import ContextAssemblyResult

_logger = get_logger("context_governance")


def log_assembly(
    policy_name: str,
    candidate: dict[str, Any],
    result: "ContextAssemblyResult",
) -> None:
    """记录一次上下文组装的关键指标。"""
    record = {
        "event": "context_assembly",
        "policy": policy_name,
        "context_signature": result.context_signature,
        "coverage_status": result.coverage_status,
        "degraded_mode": result.degraded_mode,
        "fields": {
            "before": result.original_field_count or len(candidate),
            "after": result.final_field_count or len(result.payload),
        },
        "chars": {
            "before": result.original_char_count,
            "after": result.final_char_count,
            "max_total": result.max_total_chars,
            "remaining": result.budget_remaining_chars,
            "utilization": result.budget_utilization,
        },
        "token_estimate": result.token_estimate,
        "kept_fields": result.kept_fields,
        "dropped_fields": result.dropped_fields,
        "field_char_counts_before": result.field_char_counts_before,
        "field_char_counts_after": result.field_char_counts_after,
        "warnings": result.warnings,
    }
    message = json.dumps(record, ensure_ascii=False, sort_keys=True)

    if result.degraded_mode:
        _logger.warning(message)
    else:
        _logger.info(message)
