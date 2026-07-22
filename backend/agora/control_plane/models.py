"""Internal records returned by Control Plane v2 persistence."""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Any

from pydantic import Field, field_validator, model_validator

from agora.protocol.invalidation import ArtifactChange
from agora.protocol.models import (
    ContextPack,
    GateEvaluation,
    GateRequirement,
    GitCommit,
    HandoffPack,
    NonBlank,
    ProtocolModel,
    RunProtocolState,
    Sha256Hex,
    StableId,
)
from agora.protocol.agent_adapter import AdapterErrorCode
from agora.protocol.state_machines import GateStatus, StageStatus, TaskStatus


class TaskTransitionCause(str, Enum):
    USER_ACTION = "user_action"
    ORCHESTRATION = "orchestration"
    RECONCILIATION = "reconciliation"
    INVALIDATION = "invalidation"


class TaskLifecycleReason(str, Enum):
    INVENTORY_READY = "inventory_ready"
    WORK_ACTIVE = "work_active"
    STAGE_OR_GATE_BLOCKED = "stage_or_gate_blocked"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    INVALIDATION_REQUIRED = "invalidation_required"
    BLOCKING_ATTENTION = "blocking_attention"
    REVIEW_REQUIRED = "review_required"
    ALL_STAGES_PASSED = "all_stages_passed"
    EXPLICIT_COMPLETION = "explicit_completion"
    STAGE_FAILED = "stage_failed"
    STAGE_CANCELLED = "stage_cancelled"
    EXPLICIT_CANCELLATION = "explicit_cancellation"


class TaskRecord(ProtocolModel):
    task_id: StableId
    project_id: StableId
    status: TaskStatus
    version: int = Field(ge=1)
    created_at: str
    updated_at: str


class TaskTransitionReceipt(ProtocolModel):
    task: TaskRecord
    previous_status: TaskStatus
    cause: TaskTransitionCause
    replayed: bool = False


class TaskLifecycleDecision(ProtocolModel):
    target_status: TaskStatus
    reason: TaskLifecycleReason
    inventory_id: StableId
    inventory_sha256: Sha256Hex
    total_stages: int = Field(ge=1, le=200)
    formal_stages: int = Field(ge=0, le=200)
    completed_stages: int = Field(ge=0, le=200)
    open_blockers: int = Field(ge=0)
    open_questions: int = Field(ge=0)
    open_approvals: int = Field(ge=0)


class TaskLifecycleReceipt(ProtocolModel):
    task: TaskRecord
    previous_status: TaskStatus
    decision: TaskLifecycleDecision
    transitions: list[TaskStatus] = Field(default_factory=list, max_length=8)
    cause: TaskTransitionCause


class RegistrationReceipt(ProtocolModel):
    entity_id: StableId
    version: int | None = Field(default=None, ge=1)
    created: bool


class StageRecord(ProtocolModel):
    task_id: StableId
    project_id: StableId
    stage_key: StableId
    gate_key: StableId
    status: StageStatus
    version: int = Field(ge=1)
    created_at: str
    updated_at: str


class StageRouteDecision(ProtocolModel):
    task_id: StableId
    project_id: StableId
    inventory_id: StableId
    inventory_sha256: Sha256Hex
    group_key: StableId
    group_sequence: int = Field(ge=1, le=20)
    stage_key: StableId
    gate_key: StableId
    stage_sequence: int = Field(ge=1, le=200)
    inventory_sequence: int = Field(ge=1, le=200)
    title: Annotated[str, Field(min_length=1, max_length=300)]
    role: Annotated[str, Field(min_length=1, max_length=128)]
    runtime: StableId
    stage_status: StageStatus | None = None
    gate_status: GateStatus | None = None
    runnable: bool

    @model_validator(mode="after")
    def runnable_requires_ready_status(self):
        if self.runnable and self.stage_status != StageStatus.READY:
            raise ValueError("Only the ready routed Stage may be runnable")
        return self


class StageActivationReceipt(ProtocolModel):
    route: StageRouteDecision
    previous_status: StageStatus | None = None
    activated: bool
    replayed: bool = False


class GateRecord(ProtocolModel):
    task_id: StableId
    project_id: StableId
    gate_key: StableId
    stage_key: StableId
    status: GateStatus
    version: int = Field(ge=1)
    requirements: list[GateRequirement]
    active_evidence_ids: list[StableId]
    last_evaluation: GateEvaluation | None
    created_at: str
    updated_at: str


class ProtocolRunRecord(ProtocolModel):
    run_id: StableId
    project_id: StableId
    task_id: StableId
    stage_key: StableId
    gate_key: StableId
    context_pack: ContextPack
    protocol_state: RunProtocolState | None = None
    handoff_pack: HandoffPack | None = None
    adapter_error_code: AdapterErrorCode | None = None
    attention_required: bool = False
    attention_item_id: StableId | None = None
    created_at: str
    settled_at: str | None = None


class RunSettlementReceipt(ProtocolModel):
    run: ProtocolRunRecord
    stage: StageRecord
    gate: GateRecord
    artifact_ids: list[StableId]
    evidence_ids: list[StableId]
    active_evidence_ids: list[StableId]
    next_stage_route: StageRouteDecision | None = None
    replayed: bool = False


class ArtifactInventory(ProtocolModel):
    repository_id: StableId
    ref: NonBlank
    commit_sha: GitCommit
    artifacts: list[ArtifactChange] = Field(default_factory=list, max_length=10_000)

    @field_validator("artifacts", mode="before")
    @classmethod
    def canonical_artifacts(cls, value):
        values = list(value or [])

        def key(item):
            if isinstance(item, dict):
                return item["path"], item["sha256"]
            return item.path, item.sha256

        keys = [key(item) for item in values]
        paths = [item[0] for item in keys]
        if len(paths) != len(set(paths)):
            raise ValueError("artifact inventory paths must be unique")
        return [
            item
            for _, item in sorted(
                zip(keys, values, strict=True),
                key=lambda pair: pair[0],
            )
        ]

    @model_validator(mode="after")
    def inventory_scope_matches_artifacts(self):
        for artifact in self.artifacts:
            if (
                artifact.repository_id != self.repository_id
                or artifact.ref != self.ref
                or artifact.commit_sha != self.commit_sha
            ):
                raise ValueError("artifact inventory entries must match its repository, ref, and commit")
        return self


class InvalidationReceipt(ProtocolModel):
    operation_key: Annotated[
        str,
        Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"),
    ]
    stale_approval_ids: list[StableId]
    stale_gate_keys: list[StableId]
    reopened_stage_keys: list[StableId]
    reconciliation_stage_keys: list[StableId]
    attention_item_ids: list[StableId] = Field(default_factory=list)
    event_ids: list[StableId]
    replayed: bool = False


class ControlEvent(ProtocolModel):
    event_id: StableId
    event_key: Annotated[str, Field(min_length=1, max_length=500)]
    task_id: StableId
    project_id: StableId
    event_type: Annotated[
        str,
        Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.-]*$"),
    ]
    actor: Annotated[str, Field(min_length=1, max_length=128)]
    payload: dict[str, Any]
    created_at: str
