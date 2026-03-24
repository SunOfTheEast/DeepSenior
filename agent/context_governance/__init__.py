"""Context Governance Layer — 上下文治理模块

统一负责 projection 契约、预算仲裁、降级状态暴露、上下文签名与观测埋点。
"""

from .assembler import ContextAssemblyResult
from .budget_policy import BudgetPolicy, FieldBudget
from .projection_registry import ProjectionSpec, FieldSpec, DegradationStrategy, get_projection, list_projections
from .signature import context_signature

__all__ = [
    "ContextAssemblyResult",
    "BudgetPolicy",
    "FieldBudget",
    "ProjectionSpec",
    "FieldSpec",
    "DegradationStrategy",
    "get_projection",
    "list_projections",
    "context_signature",
]
