"""Versioned contracts for the Agora 1.x delivery protocol."""

from .gates import evaluate_gate
from .hashing import canonical_json_bytes, canonical_sha256, seal_model_payload, seal_payload
from .invalidation import ArtifactChange, InvalidationPlan, invalidate_approvals
from .memory import M2UpdateAction, RunOutcome, decide_m2_update
from .models import (
    Approval,
    ApprovalStatus,
    Artifact,
    ArtifactStorage,
    ContextPack,
    Evidence,
    EvidenceStatus,
    GateDecision,
    GateEvaluation,
    GateRequirement,
    HandoffPack,
    NativeStateSnapshot,
    RunProtocolState,
    RunnerIsolationContract,
)
from .state_machines import (
    GateStatus,
    StageStatus,
    TaskStatus,
    TransitionError,
    transition_gate,
    transition_stage,
    transition_task,
)
from .repair import RepairAction, RepairDecision, decide_schema_repair
from .runner import CleanupFailurePlan, plan_cleanup_failure

__all__ = [
    "Approval",
    "ApprovalStatus",
    "Artifact",
    "ArtifactChange",
    "ArtifactStorage",
    "ContextPack",
    "Evidence",
    "EvidenceStatus",
    "GateDecision",
    "GateEvaluation",
    "GateRequirement",
    "GateStatus",
    "HandoffPack",
    "InvalidationPlan",
    "M2UpdateAction",
    "NativeStateSnapshot",
    "RunOutcome",
    "RepairAction",
    "RepairDecision",
    "RunProtocolState",
    "RunnerIsolationContract",
    "CleanupFailurePlan",
    "StageStatus",
    "TaskStatus",
    "TransitionError",
    "canonical_json_bytes",
    "canonical_sha256",
    "decide_m2_update",
    "decide_schema_repair",
    "evaluate_gate",
    "invalidate_approvals",
    "seal_model_payload",
    "seal_payload",
    "plan_cleanup_failure",
    "transition_gate",
    "transition_stage",
    "transition_task",
]
