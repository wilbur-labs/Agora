"""Contracts for the provisional AI-DLC orchestration foundation."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agora.attention.models import AttentionItem
from agora.control_plane.models import GateRecord, StageRecord
from agora.protocol.models import (
    Approval,
    ArtifactVersionRef,
    Evidence,
    ProcessStatus,
    SchemaStatus,
    SemanticStageResult,
    TransportStatus,
)
from agora.tasks.models import TaskManifest, TaskState


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanState(str, Enum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    AWAITING_APPROVAL = "awaiting_approval"
    READY_FOR_IMPLEMENTATION = "ready_for_implementation"


class StageState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    BLOCKED = "blocked"


class RunState(str, Enum):
    RUNNING = "running"
    PASSED = "passed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class SemanticStatus(str, Enum):
    PASS = "pass"
    NEEDS_WORK = "needs_work"
    BLOCKED = "blocked"


class Measurement(str, Enum):
    EXACT = "exact"
    ESTIMATED = "estimated"
    UNAVAILABLE = "unavailable"


class LedgerEntryType(str, Enum):
    RESERVATION = "reservation"
    SETTLEMENT = "settlement"


class StageDefinition(StrictModel):
    stage_key: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=100)
    adapter: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    token_weight: int = Field(ge=1, le=100)
    objective: str = Field(min_length=1, max_length=4000)


class MethodologyDefinition(StrictModel):
    methodology_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    version: str = Field(pattern=r"^\d+\.\d+$")
    provisional: bool
    description: str = Field(min_length=1, max_length=2000)
    stages: list[StageDefinition] = Field(min_length=1, max_length=20)


class SemanticResult(StrictModel):
    status: SemanticStatus
    summary: str = Field(min_length=1, max_length=4000)
    findings: list[str] = Field(default_factory=list, max_length=50)
    recommended_next_action: str = Field(min_length=1, max_length=2000)


class OrchestrationPlan(StrictModel):
    plan_id: str
    task_id: str
    project_id: str
    methodology_id: str
    methodology_version: str
    methodology_sha256: str
    provisional: bool
    state: PlanState
    total_token_budget: int
    total_cost_budget_usd: float | None
    current_stage_key: str | None
    version: int
    created_at: str
    updated_at: str
    approved_at: str | None = None
    approved_by: str | None = None


class OrchestrationStage(StrictModel):
    stage_id: str
    plan_id: str
    stage_key: str
    sequence: int
    title: str
    role: str
    adapter: str
    state: StageState
    token_budget: int
    cost_budget_usd: float | None
    attempt_count: int
    latest_run_id: str | None
    semantic_summary: str | None
    blockers: list[str]
    updated_at: str


class OrchestrationRun(StrictModel):
    run_id: str
    plan_id: str
    task_id: str
    stage_key: str
    adapter: str
    state: RunState
    operation_key: str
    prompt_sha256: str
    pid: int | None
    exit_code: int | None
    timed_out: bool
    output: str
    error_message: str | None
    semantic_status: SemanticStatus | None
    semantic_summary: str | None
    findings: list[str]
    token_reserved: int
    token_used: int | None
    token_measurement: Measurement
    cost_reserved_usd: float | None
    cost_used_usd: float | None
    cost_measurement: Measurement
    attempt: int
    started_at: str
    finished_at: str | None


class UsageLedgerEntry(StrictModel):
    entry_id: str
    task_id: str
    plan_id: str
    stage_key: str
    run_id: str
    entry_type: LedgerEntryType
    tokens: int | None
    token_measurement: Measurement
    cost_usd: float | None
    cost_measurement: Measurement
    adapter: str
    created_at: str


class TaskDecision(StrictModel):
    decision_id: str
    plan_id: str
    task_id: str
    decision_key: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    decision_value: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=500)
    decision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    version: int = Field(ge=1)
    actor: str = Field(min_length=1, max_length=128)
    created_at: str


class TaskOrchestrationStatus(StrictModel):
    plan: OrchestrationPlan
    stages: list[OrchestrationStage]
    runs: list[OrchestrationRun]
    usage: list[UsageLedgerEntry]
    decisions: list[TaskDecision]
    tokens_reserved: int
    tokens_used: int | None
    token_measurement: Measurement
    tokens_remaining: int | None
    cost_used_usd: float | None
    cost_measurement: Measurement
    next_safe_action: str


class ProjectionPage(StrictModel):
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)


class RunWaitState(str, Enum):
    SETTLED = "settled"
    OPERATIONAL_RUNTIME_PENDING = "operational_runtime_pending"
    PROTOCOL_START_PENDING = "protocol_start_pending"
    RUNTIME_OR_SETTLEMENT_PENDING = "runtime_or_settlement_pending"
    COMPATIBILITY_PROJECTION_PENDING = "compatibility_projection_pending"


class UnifiedTaskProgress(StrictModel):
    total_stages: int = Field(ge=0)
    completed_stages: int = Field(ge=0)
    current_stage_key: str | None
    completed_stage_keys: list[str]
    remaining_stage_keys: list[str]


class UnifiedStageProjection(StrictModel):
    stage_key: str
    sequence: int | None = Field(default=None, ge=1)
    title: str | None = None
    runtime: str | None = None
    current: bool
    operational_state: StageState | None = None
    authoritative_stage: StageRecord | None = None
    gate: GateRecord | None = None
    attempt_count: int = Field(default=0, ge=0)
    latest_run_id: str | None = None
    semantic_summary: str | None = None
    blockers: list[str] = Field(default_factory=list, max_length=100)


class UnifiedRunProjection(StrictModel):
    run_id: str
    stage_key: str
    runtime: str | None = None
    attempt: int | None = Field(default=None, ge=1)
    operational_state: RunState | None = None
    wait_state: RunWaitState
    process_status: ProcessStatus | None = None
    transport_status: TransportStatus | None = None
    schema_status: SchemaStatus | None = None
    semantic_result: SemanticStageResult | SemanticStatus | None = None
    semantic_source: Literal["protocol", "compatibility", "unavailable"]
    process_exit_code: int | None = None
    timed_out: bool = False
    semantic_summary: str | None = None
    findings: list[str] = Field(default_factory=list, max_length=100)
    failure: str | None = None
    context_pack_id: str | None = None
    context_sha256: str | None = None
    handoff_pack_id: str | None = None
    handoff_sha256: str | None = None
    adapter_error_code: str | None = None
    attention_required: bool = False
    attention_item_id: str | None = None
    token_reserved: int | None = Field(default=None, ge=0)
    token_settled: int | None = Field(default=None, ge=0)
    token_measurement: Measurement | None = None
    cost_reserved_usd: float | None = Field(default=None, ge=0)
    cost_settled_usd: float | None = Field(default=None, ge=0)
    cost_measurement: Measurement | None = None
    started_at: str
    finished_at: str | None = None
    elapsed_seconds: float = Field(ge=0)


class ArtifactSummary(StrictModel):
    version_ref: ArtifactVersionRef
    project_id: str
    task_id: str
    stage_key: str
    producer_runtime: str
    producer_run_id: str
    media_type: str
    created_at: str


class UnifiedAuditEvent(StrictModel):
    event_id: str
    source: Literal["task", "control_plane"]
    event_key: str | None = None
    event_type: str
    actor: str
    payload: dict[str, Any]
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_truncated: bool = False
    created_at: str


class RequiredHumanAction(StrictModel):
    action_id: str
    kind: Literal["attention", "plan_approval"]
    title: str
    source_id: str


class GateDerivedNextSafeAction(StrictModel):
    value: str | None
    source_gate_key: str | None
    unavailable_reason: str | None


class UnifiedBudgetProjection(StrictModel):
    token_allocated: int = Field(ge=0)
    token_reserved: int = Field(ge=0)
    token_settled: int | None = Field(default=None, ge=0)
    token_measurement: Measurement
    token_remaining: int | None = Field(default=None, ge=0)
    cost_allocated_usd: float | None = Field(default=None, ge=0)
    cost_reserved_usd: float | None = Field(default=None, ge=0)
    cost_settled_usd: float | None = Field(default=None, ge=0)
    cost_measurement: Measurement
    cost_remaining_usd: float | None = Field(default=None, ge=0)


class UnifiedTaskProjection(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    snapshot_at: str
    task: TaskManifest
    task_state: TaskState
    task_state_source: Literal["task_manifest"] = "task_manifest"
    plan: OrchestrationPlan
    progress: UnifiedTaskProgress
    stages: list[UnifiedStageProjection]
    runs: list[UnifiedRunProjection]
    artifacts: list[ArtifactSummary]
    evidence: list[Evidence]
    approvals: list[Approval]
    attention: list[AttentionItem]
    required_human_actions: list[RequiredHumanAction]
    decisions: list[TaskDecision]
    usage: list[UsageLedgerEntry]
    audit_events: list[UnifiedAuditEvent]
    budget: UnifiedBudgetProjection
    next_safe_action: GateDerivedNextSafeAction
    compatibility_next_action: str
    collection_totals: dict[str, int]
    collection_pages: dict[str, ProjectionPage]
