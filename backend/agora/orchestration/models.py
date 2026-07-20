"""Contracts for the provisional AI-DLC orchestration foundation."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


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
