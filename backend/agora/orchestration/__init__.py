"""CLI-first, methodology-driven task orchestration foundation."""

from .methodology import FOUNDATION_METHODOLOGY, MethodologyDefinition
from .protocol_adapter import adapt_runtime_result
from .service import TaskOrchestrationService
from .store import OrchestrationStore

__all__ = [
    "FOUNDATION_METHODOLOGY",
    "MethodologyDefinition",
    "OrchestrationStore",
    "TaskOrchestrationService",
    "adapt_runtime_result",
]
