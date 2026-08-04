"""Contracts for the provisional AI-DLC orchestration foundation."""
from __future__ import annotations

import hashlib
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from agora.attention.models import AttentionItem
from agora.control_plane.models import (
    GateRecord,
    StageRecord,
    StageRouteDecision,
    TaskLifecycleDecision,
    TaskRecord,
)
from agora.protocol.models import (
    Approval,
    ArtifactVersionRef,
    ConsultationCandidate,
    ConsultationCandidateDisposition,
    Evidence,
    HashSealedModel,
    PinnedRuntimePreflightDecision,
    ProcessStatus,
    ProviderUsageObservation,
    ProtocolModel,
    SchemaStatus,
    SemanticStageResult,
    Sha256Hex,
    StableId,
    StageInventory,
    StageInventoryItem,
    TransportStatus,
)
from agora.protocol.agent_adapter import AgentAdapterResult
from agora.protocol.methodology_run_dispatch import (
    MethodologyRunDispatchClaim,
    MethodologyRunDispatchReceipt,
)
from agora.protocol.methodology_completion_review_dispatch import (
    MethodologyCompletionReviewDispatchClaim,
    MethodologyCompletionReviewDispatchReceipt,
)
from agora.protocol.methodology_stage_run_dispatch import (
    MethodologyStageRunDispatchClaim,
    MethodologyStageRunDispatchReceipt,
)
from agora.protocol.state_machines import TaskStatus
from agora.tasks.models import TaskManifest, TaskRisk, TaskState


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


class ConsultationState(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PROTOCOL_FAILED = "protocol_failed"
    INTERRUPTED = "interrupted"


class MethodologyDispatchState(str, Enum):
    CLAIMED = "claimed"
    RUNNING = "running"
    TERMINAL_OBSERVED = "terminal_observed"
    SETTLED = "settled"


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


class ControlPlanePlanApprovalReceipt(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    operation_key: Annotated[
        str,
        Field(
            min_length=1,
            max_length=200,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
        ),
    ]
    task: TaskRecord
    plan: OrchestrationPlan
    previous_task_status: TaskStatus
    previous_plan_state: PlanState
    task_completed: Literal[True] = True
    formal_approval_created: Literal[False] = False
    methodology_completion_approval_created: Literal[False] = False
    replayed: bool = False


class ControlPlaneCandidateDispositionReceipt(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    operation_key: Annotated[
        str,
        Field(
            min_length=1,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
        ),
    ]
    disposition: ConsultationCandidateDisposition
    candidate_authority: Literal[False] = False
    task_decision_bound: bool
    plan_version_changed: bool
    task_state_mutated: Literal[False] = False
    stage_state_mutated: Literal[False] = False
    gate_state_mutated: Literal[False] = False
    formal_approval_created: Literal[False] = False
    runtime_called: Literal[False] = False
    replayed: bool = False

    @model_validator(mode="after")
    def validate_disposition_effects(self):
        adopted = self.disposition.action == "adopted"
        if self.task_decision_bound != adopted:
            raise ValueError("Only candidate adoption may bind a Task decision")
        if self.plan_version_changed != adopted:
            raise ValueError("Only candidate adoption may change the Plan version")
        if self.operation_key != self.disposition.operation_key:
            raise ValueError("Candidate disposition receipt operation key drifted")
        return self


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


class RoutingConstraintCheck(ProtocolModel):
    constraint: Literal[
        "stage_assignment",
        "runtime_capability",
        "reviewer_coverage",
        "risk_coverage",
        "protected_budget",
    ]
    satisfied: bool
    detail: Annotated[str, Field(min_length=1, max_length=1000)]


class RoutingReviewerAssignment(ProtocolModel):
    runtime: StableId
    role: StableId
    stage_key: StableId
    independent_from_roles: list[StableId] = Field(min_length=1, max_length=20)
    required_capabilities: list[StableId] = Field(min_length=1, max_length=20)


class RoutingPolicyDecision(HashSealedModel):
    """Hash-bound explanation for one pinned formal Run dispatch."""

    schema_version: Literal["1.0"] = "1.0"
    decision_id: StableId
    policy_id: StableId
    policy_version: Literal["1.0"] = "1.0"
    policy_sha256: Sha256Hex
    task_id: StableId
    project_id: StableId
    plan_id: StableId
    inventory_id: StableId
    inventory_sha256: Sha256Hex
    methodology_id: StableId
    methodology_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")]
    methodology_sha256: Sha256Hex
    stage_key: StableId
    role: StableId
    pinned_runtime: StableId
    task_risk: TaskRisk
    # Empty collections are valid evidence in a blocked decision. For example,
    # an unknown role has no declared required capabilities and a methodology
    # without reviewer stages has no reviewer assignments. The checks below
    # must carry those failures as structured policy blockers instead of
    # turning fail-closed derivation into an unrelated model-validation crash.
    required_capabilities: list[StableId] = Field(max_length=20)
    runtime_capabilities: list[StableId] = Field(max_length=20)
    required_reviewers: list[StableId] = Field(max_length=10)
    reviewer_assignments: list[RoutingReviewerAssignment] = Field(
        max_length=10,
    )
    task_token_budget: int = Field(ge=0)
    settled_token_debit: int = Field(ge=0)
    active_token_reservations: int = Field(ge=0)
    available_tokens_before_dispatch: int = Field(ge=0)
    current_run_token_reservation: int = Field(ge=0)
    protected_future_reviewer_tokens: int = Field(ge=0)
    task_cost_budget_usd: float | None = Field(default=None, ge=0)
    settled_cost_debit_usd: float | None = Field(default=None, ge=0)
    active_cost_reservations_usd: float | None = Field(default=None, ge=0)
    available_cost_before_dispatch_usd: float | None = Field(default=None, ge=0)
    current_run_cost_reservation_usd: float | None = Field(default=None, ge=0)
    protected_future_reviewer_cost_usd: float | None = Field(default=None, ge=0)
    checks: list[RoutingConstraintCheck] = Field(min_length=5, max_length=5)
    dispatchable: bool
    blockers: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(
        default_factory=list,
        max_length=10,
    )
    rationale: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(
        min_length=5,
        max_length=10,
    )


class BudgetAmendment(HashSealedModel):
    """Versioned, hash-bound increase to one Task orchestration envelope."""

    schema_version: Literal["1.0"] = "1.0"
    amendment_id: StableId
    amendment_version: int = Field(ge=1)
    operation_key: StableId
    task_id: StableId
    project_id: StableId
    plan_id: StableId
    task_version_before: int = Field(ge=1)
    task_version_after: int = Field(ge=2)
    plan_version_before: int = Field(ge=1)
    plan_version_after: int = Field(ge=2)
    inventory_id: StableId
    inventory_sha256: Sha256Hex
    methodology_id: StableId
    methodology_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")]
    methodology_sha256: Sha256Hex
    contract_id: StableId
    contract_schema_version: Annotated[str, Field(pattern=r"^1\.[0-9]+$")]
    contract_sha256: Sha256Hex
    stage_key: StableId
    stage_allocations_sha256: Sha256Hex
    previous_total_token_budget: int = Field(ge=3_000, le=10_000_000)
    amended_total_token_budget: int = Field(ge=3_000, le=10_000_000)
    previous_total_cost_budget_usd: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    amended_total_cost_budget_usd: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    prior_policy: RoutingPolicyDecision
    resulting_policy: RoutingPolicyDecision
    claim_requires_policy_rederivation: Literal[True] = True
    actor: Annotated[str, Field(min_length=1, max_length=128)]
    reason: Annotated[str, Field(min_length=1, max_length=1000)]
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_amendment_semantics(self):
        if self.task_version_after != self.task_version_before + 1:
            raise ValueError("Budget amendment must increment the Task version once")
        if self.plan_version_after != self.plan_version_before + 1:
            raise ValueError("Budget amendment must increment the Plan version once")

        token_increased = (
            self.amended_total_token_budget > self.previous_total_token_budget
        )
        if self.amended_total_token_budget < self.previous_total_token_budget:
            raise ValueError("Budget amendment cannot decrease the Token envelope")
        if (self.previous_total_cost_budget_usd is None) != (
            self.amended_total_cost_budget_usd is None
        ):
            raise ValueError("Budget amendment cannot add or remove a cost ceiling")
        cost_increased = False
        if self.previous_total_cost_budget_usd is not None:
            assert self.amended_total_cost_budget_usd is not None
            if (
                self.amended_total_cost_budget_usd
                < self.previous_total_cost_budget_usd
            ):
                raise ValueError("Budget amendment cannot decrease the cost envelope")
            cost_increased = (
                self.amended_total_cost_budget_usd
                > self.previous_total_cost_budget_usd
            )
        if not token_increased and not cost_increased:
            raise ValueError(
                "Budget amendment must strictly increase at least one envelope"
            )

        expected_bindings = {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "plan_id": self.plan_id,
            "inventory_id": self.inventory_id,
            "inventory_sha256": self.inventory_sha256,
            "methodology_id": self.methodology_id,
            "methodology_version": self.methodology_version,
            "methodology_sha256": self.methodology_sha256,
            "stage_key": self.stage_key,
        }
        for policy_name, policy in (
            ("prior", self.prior_policy),
            ("resulting", self.resulting_policy),
        ):
            for field_name, expected in expected_bindings.items():
                if getattr(policy, field_name) != expected:
                    raise ValueError(
                        f"Budget amendment {policy_name} policy {field_name} binding differs"
                    )

        if self.prior_policy.task_token_budget != self.previous_total_token_budget:
            raise ValueError("Prior policy Token budget differs from the prior envelope")
        if self.resulting_policy.task_token_budget != self.amended_total_token_budget:
            raise ValueError("Resulting policy Token budget differs from the amended envelope")
        if (
            self.prior_policy.task_cost_budget_usd
            != self.previous_total_cost_budget_usd
        ):
            raise ValueError("Prior policy cost budget differs from the prior envelope")
        if (
            self.resulting_policy.task_cost_budget_usd
            != self.amended_total_cost_budget_usd
        ):
            raise ValueError("Resulting policy cost budget differs from the amended envelope")

        invariant_policy_fields = (
            "policy_id",
            "policy_version",
            "policy_sha256",
            "role",
            "pinned_runtime",
            "task_risk",
            "required_capabilities",
            "runtime_capabilities",
            "required_reviewers",
            "reviewer_assignments",
            "settled_token_debit",
            "active_token_reservations",
            "current_run_token_reservation",
            "protected_future_reviewer_tokens",
            "settled_cost_debit_usd",
            "active_cost_reservations_usd",
            "current_run_cost_reservation_usd",
            "protected_future_reviewer_cost_usd",
        )
        for field_name in invariant_policy_fields:
            if getattr(self.prior_policy, field_name) != getattr(
                self.resulting_policy,
                field_name,
            ):
                raise ValueError(
                    f"Budget amendment changed invariant policy field {field_name}"
                )
        if self.prior_policy.decision_id == self.resulting_policy.decision_id:
            raise ValueError(
                "Prior and resulting policies must have distinct decision IDs"
            )

        prior_checks = {
            check.constraint: check.satisfied for check in self.prior_policy.checks
        }
        resulting_checks = {
            check.constraint: check.satisfied
            for check in self.resulting_policy.checks
        }
        expected_constraints = {
            "stage_assignment",
            "runtime_capability",
            "reviewer_coverage",
            "risk_coverage",
            "protected_budget",
        }
        if set(prior_checks) != expected_constraints or len(prior_checks) != 5:
            raise ValueError("Prior policy must contain every routing constraint once")
        if set(resulting_checks) != expected_constraints or len(resulting_checks) != 5:
            raise ValueError("Resulting policy must contain every routing constraint once")
        if self.prior_policy.dispatchable or prior_checks["protected_budget"]:
            raise ValueError("Prior policy must be blocked by protected budget")
        if not all(
            prior_checks[constraint]
            for constraint in expected_constraints - {"protected_budget"}
        ):
            raise ValueError("Prior policy may only be blocked by protected budget")
        if not self.resulting_policy.dispatchable or not all(resulting_checks.values()):
            raise ValueError("Resulting policy must be fully dispatchable")
        return self


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
    routing_policy: RoutingPolicyDecision | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    runtime_preflight: PinnedRuntimePreflightDecision | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    usage_observation: ProviderUsageObservation | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    started_at: str
    finished_at: str | None


class RuntimePreflightPreview(StrictModel):
    """Read-only Task-scoped projection of the exact pinned-runtime decision."""

    schema_version: Literal["1.0"] = "1.0"
    generated_at: AwareDatetime
    task_id: StableId
    project_id: StableId
    preview_only: Literal[True] = True
    run_claimed: Literal[False] = False
    observation_persisted: Literal[False] = False
    runtime_spawned: Literal[False] = False
    decision: PinnedRuntimePreflightDecision
    remediation: list[
        Annotated[str, Field(min_length=1, max_length=1000)]
    ] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def validate_preview_bindings(self):
        if (
            self.decision.task_id != self.task_id
            or self.decision.project_id != self.project_id
        ):
            raise ValueError("preflight preview does not match its Task scope")
        if not self.decision.run_id.startswith("preview_"):
            raise ValueError("preflight preview decision must use a preview Run identity")
        return self


class MethodologyRunDispatchState(StrictModel):
    """Durable recovery state for one methodology process attachment."""

    dispatch_id: str
    state: MethodologyDispatchState
    claim: MethodologyRunDispatchClaim
    receipt: MethodologyRunDispatchReceipt | None = None
    pid: int | None = Field(default=None, ge=1)
    process_started: bool = False
    exit_code: int | None = None
    timed_out: bool = False
    output: str = ""
    error_message: str | None = None
    repository_unchanged: bool | None = None
    adapter_result: AgentAdapterResult | None = None
    usage_observation: ProviderUsageObservation | None = None
    claimed_at: str
    process_attached_at: str | None = None
    terminal_observed_at: str | None = None
    settled_at: str | None = None

    @model_validator(mode="after")
    def validate_recovery_state(self):
        if self.dispatch_id != self.claim.dispatch_id:
            raise ValueError("methodology dispatch state differs from its claim")
        terminal_fields = (
            self.repository_unchanged,
            self.adapter_result,
            self.usage_observation,
            self.terminal_observed_at,
        )
        if self.state == MethodologyDispatchState.CLAIMED:
            if (
                self.pid is not None
                or self.process_started
                or self.process_attached_at is not None
                or self.exit_code is not None
                or self.timed_out
                or self.output
                or self.error_message is not None
                or any(item is not None for item in terminal_fields)
                or self.receipt is not None
                or self.settled_at is not None
            ):
                raise ValueError(
                    "claimed methodology dispatch may not carry process or terminal facts"
                )
            return self
        if self.state == MethodologyDispatchState.RUNNING:
            if (
                self.pid is None
                or not self.process_started
                or self.process_attached_at is None
                or self.exit_code is not None
                or self.timed_out
                or self.output
                or self.error_message is not None
                or any(item is not None for item in terminal_fields)
                or self.receipt is not None
                or self.settled_at is not None
            ):
                raise ValueError(
                    "running methodology dispatch requires only an attached process"
                )
            return self
        if any(item is None for item in terminal_fields):
            raise ValueError(
                "terminal methodology dispatch requires complete observed facts"
            )
        if self.process_started:
            if self.pid is None or self.process_attached_at is None:
                raise ValueError(
                    "started methodology dispatch requires its attached process"
                )
        elif (
            self.pid is not None
            or self.process_attached_at is not None
            or self.exit_code is not None
            or self.timed_out
        ):
            raise ValueError(
                "unstarted methodology dispatch cannot carry process facts"
            )
        if self.adapter_result is not None and (
            self.adapter_result.protocol_state.run_id != self.claim.run_id
        ):
            raise ValueError(
                "methodology dispatch adapter result differs from its claimed Run"
            )
        if self.usage_observation is not None and (
            self.usage_observation.run_id != self.claim.run_id
        ):
            raise ValueError(
                "methodology dispatch usage differs from its claimed Run"
            )
        if self.state == MethodologyDispatchState.TERMINAL_OBSERVED:
            if self.receipt is not None or self.settled_at is not None:
                raise ValueError(
                    "terminal-observed methodology dispatch is not yet settled"
                )
            return self
        if self.receipt is None or self.settled_at is None:
            raise ValueError(
                "settled methodology dispatch requires its immutable receipt"
            )
        if self.receipt.dispatch_claim != self.claim:
            raise ValueError(
                "methodology dispatch receipt differs from its persisted claim"
            )
        if (
            self.receipt.settled_at.isoformat() != self.settled_at
            or self.receipt.pid != self.pid
            or self.receipt.process_started != self.process_started
            or self.receipt.exit_code != self.exit_code
            or self.receipt.timed_out != self.timed_out
            or self.receipt.output_sha256
            != hashlib.sha256(self.output.encode("utf-8")).hexdigest()
            or self.receipt.error_sha256
            != hashlib.sha256(
                (self.error_message or "").encode("utf-8")
            ).hexdigest()
            or self.receipt.repository_unchanged
            != self.repository_unchanged
            or self.receipt.usage_observation != self.usage_observation
            or self.adapter_result is None
            or self.receipt.protocol_state
            != self.adapter_result.protocol_state
        ):
            raise ValueError(
                "settled methodology dispatch receipt differs from terminal facts"
            )
        return self


class MethodologyStageRunDispatchState(StrictModel):
    """Durable recovery state for one later methodology process attachment."""

    dispatch_id: str
    state: MethodologyDispatchState
    claim: MethodologyStageRunDispatchClaim
    receipt: MethodologyStageRunDispatchReceipt | None = None
    pid: int | None = Field(default=None, ge=1)
    process_started: bool = False
    exit_code: int | None = None
    timed_out: bool = False
    output: str = ""
    error_message: str | None = None
    repository_unchanged: bool | None = None
    adapter_result: AgentAdapterResult | None = None
    usage_observation: ProviderUsageObservation | None = None
    claimed_at: str
    process_attached_at: str | None = None
    terminal_observed_at: str | None = None
    settled_at: str | None = None

    @model_validator(mode="after")
    def validate_recovery_state(self):
        if self.dispatch_id != self.claim.dispatch_id:
            raise ValueError(
                "methodology Stage dispatch state differs from its claim"
            )
        terminal_fields = (
            self.repository_unchanged,
            self.adapter_result,
            self.usage_observation,
            self.terminal_observed_at,
        )
        if self.state == MethodologyDispatchState.CLAIMED:
            if (
                self.pid is not None
                or self.process_started
                or self.process_attached_at is not None
                or self.exit_code is not None
                or self.timed_out
                or self.output
                or self.error_message is not None
                or any(item is not None for item in terminal_fields)
                or self.receipt is not None
                or self.settled_at is not None
            ):
                raise ValueError(
                    "claimed methodology Stage dispatch carries terminal facts"
                )
            return self
        if self.state == MethodologyDispatchState.RUNNING:
            if (
                self.pid is None
                or not self.process_started
                or self.process_attached_at is None
                or self.exit_code is not None
                or self.timed_out
                or self.output
                or self.error_message is not None
                or any(item is not None for item in terminal_fields)
                or self.receipt is not None
                or self.settled_at is not None
            ):
                raise ValueError(
                    "running methodology Stage dispatch requires one process"
                )
            return self
        if any(item is None for item in terminal_fields):
            raise ValueError(
                "terminal methodology Stage dispatch requires complete facts"
            )
        if self.process_started:
            if self.pid is None or self.process_attached_at is None:
                raise ValueError(
                    "started methodology Stage dispatch requires its process"
                )
        elif (
            self.pid is not None
            or self.process_attached_at is not None
            or self.exit_code is not None
            or self.timed_out
        ):
            raise ValueError(
                "unstarted methodology Stage dispatch cannot carry process facts"
            )
        if self.adapter_result is not None and (
            self.adapter_result.protocol_state.run_id != self.claim.run_id
        ):
            raise ValueError(
                "methodology Stage adapter result differs from its Run"
            )
        if self.usage_observation is not None and (
            self.usage_observation.run_id != self.claim.run_id
        ):
            raise ValueError(
                "methodology Stage usage differs from its Run"
            )
        if self.state == MethodologyDispatchState.TERMINAL_OBSERVED:
            if self.receipt is not None or self.settled_at is not None:
                raise ValueError(
                    "terminal-observed methodology Stage dispatch is unsettled"
                )
            return self
        if self.receipt is None or self.settled_at is None:
            raise ValueError(
                "settled methodology Stage dispatch requires its receipt"
            )
        if self.receipt.dispatch_claim != self.claim:
            raise ValueError(
                "methodology Stage receipt differs from its persisted claim"
            )
        if (
            self.receipt.settled_at.isoformat() != self.settled_at
            or self.receipt.pid != self.pid
            or self.receipt.process_started != self.process_started
            or self.receipt.exit_code != self.exit_code
            or self.receipt.timed_out != self.timed_out
            or self.receipt.output_sha256
            != hashlib.sha256(self.output.encode("utf-8")).hexdigest()
            or self.receipt.error_sha256
            != hashlib.sha256(
                (self.error_message or "").encode("utf-8")
            ).hexdigest()
            or self.receipt.repository_unchanged
            != self.repository_unchanged
            or self.receipt.usage_observation != self.usage_observation
            or self.adapter_result is None
            or self.receipt.protocol_state
            != self.adapter_result.protocol_state
        ):
            raise ValueError(
                "settled methodology Stage receipt differs from terminal facts"
            )
        return self


class MethodologyCompletionReviewDispatchState(StrictModel):
    """Durable recovery state for one claimed completion reviewer process."""

    dispatch_id: str
    state: MethodologyDispatchState
    claim: MethodologyCompletionReviewDispatchClaim
    receipt: MethodologyCompletionReviewDispatchReceipt | None = None
    pid: int | None = Field(default=None, ge=1)
    process_started: bool = False
    exit_code: int | None = None
    timed_out: bool = False
    output: str = ""
    error_message: str | None = None
    repository_unchanged: bool | None = None
    adapter_result: AgentAdapterResult | None = None
    usage_observation: ProviderUsageObservation | None = None
    claimed_at: str
    process_attached_at: str | None = None
    terminal_observed_at: str | None = None
    settled_at: str | None = None

    @model_validator(mode="after")
    def validate_recovery_state(self):
        if self.dispatch_id != self.claim.dispatch_id:
            raise ValueError(
                "methodology completion-review state differs from its claim"
            )
        terminal_fields = (
            self.repository_unchanged,
            self.adapter_result,
            self.usage_observation,
            self.terminal_observed_at,
        )
        if self.state == MethodologyDispatchState.CLAIMED:
            if (
                self.pid is not None
                or self.process_started
                or self.process_attached_at is not None
                or self.exit_code is not None
                or self.timed_out
                or self.output
                or self.error_message is not None
                or any(item is not None for item in terminal_fields)
                or self.receipt is not None
                or self.settled_at is not None
            ):
                raise ValueError(
                    "claimed completion-review dispatch carries terminal facts"
                )
            return self
        if self.state == MethodologyDispatchState.RUNNING:
            if (
                self.pid is None
                or not self.process_started
                or self.process_attached_at is None
                or self.exit_code is not None
                or self.timed_out
                or self.output
                or self.error_message is not None
                or any(item is not None for item in terminal_fields)
                or self.receipt is not None
                or self.settled_at is not None
            ):
                raise ValueError(
                    "running completion-review dispatch requires one process"
                )
            return self
        if any(item is None for item in terminal_fields):
            raise ValueError(
                "terminal completion-review dispatch requires complete facts"
            )
        if self.process_started:
            if self.pid is None or self.process_attached_at is None:
                raise ValueError(
                    "started completion-review dispatch requires its process"
                )
        elif (
            self.pid is not None
            or self.process_attached_at is not None
            or self.exit_code is not None
            or self.timed_out
        ):
            raise ValueError(
                "unstarted completion-review dispatch cannot carry process facts"
            )
        if self.adapter_result is not None and (
            self.adapter_result.protocol_state.run_id
            != self.claim.review_run_id
        ):
            raise ValueError(
                "completion-review adapter result differs from its Run"
            )
        if self.usage_observation is not None and (
            self.usage_observation.run_id != self.claim.review_run_id
        ):
            raise ValueError(
                "completion-review usage differs from its Run"
            )
        if self.adapter_result is not None and self.usage_observation is not None:
            protocol = self.adapter_result.protocol_state
            launch_failed = protocol.process_status == ProcessStatus.LAUNCH_FAILED
            if (
                self.process_started == launch_failed
                or protocol.process_status
                in {ProcessStatus.PENDING, ProcessStatus.RUNNING}
                or self.exit_code != protocol.process_exit_code
                or self.timed_out
                != (protocol.process_status == ProcessStatus.TIMED_OUT)
            ):
                raise ValueError(
                    "completion-review process facts differ from protocol state"
                )
            if not self.process_started and (
                self.usage_observation.total_tokens != 0
                or self.usage_observation.cost_usd != 0
                or self.adapter_result.handoff_pack is not None
                or protocol.semantic_stage_result
                != SemanticStageResult.BLOCKED
            ):
                raise ValueError(
                    "unstarted completion-review process requires exact-zero "
                    "blocked terminal facts"
                )
            if protocol.semantic_stage_result == SemanticStageResult.SUCCEEDED and (
                not self.process_started
                or not self.repository_unchanged
                or self.adapter_result.handoff_pack is None
            ):
                raise ValueError(
                    "successful completion review requires an attached process, "
                    "stable repository, and Handoff"
                )
        if self.state == MethodologyDispatchState.TERMINAL_OBSERVED:
            if self.receipt is not None or self.settled_at is not None:
                raise ValueError(
                    "terminal-observed completion-review dispatch is unsettled"
                )
            return self
        if self.receipt is None or self.settled_at is None:
            raise ValueError(
                "settled completion-review dispatch requires its receipt"
            )
        if self.receipt.dispatch_claim != self.claim:
            raise ValueError(
                "completion-review receipt differs from its persisted claim"
            )
        if (
            self.receipt.settled_at.isoformat() != self.settled_at
            or self.receipt.pid != self.pid
            or self.receipt.process_started != self.process_started
            or self.receipt.exit_code != self.exit_code
            or self.receipt.timed_out != self.timed_out
            or self.receipt.output_sha256
            != hashlib.sha256(self.output.encode("utf-8")).hexdigest()
            or self.receipt.error_sha256
            != hashlib.sha256(
                (self.error_message or "").encode("utf-8")
            ).hexdigest()
            or self.receipt.repository_unchanged
            != self.repository_unchanged
            or self.receipt.usage_observation != self.usage_observation
            or self.adapter_result is None
            or self.receipt.protocol_state
            != self.adapter_result.protocol_state
        ):
            raise ValueError(
                "settled completion-review receipt differs from terminal facts"
            )
        handoff = self.adapter_result.handoff_pack
        if handoff is None:
            if (
                self.receipt.handoff_pack_id is not None
                or self.receipt.handoff_pack_sha256 is not None
                or self.receipt.evidence_ids
            ):
                raise ValueError(
                    "settled completion-review empty Handoff binding drifted"
                )
        elif (
            self.receipt.handoff_pack_id != handoff.pack_id
            or self.receipt.handoff_pack_sha256 != handoff.content_sha256
            or self.receipt.evidence_ids
            != sorted(item.evidence_id for item in handoff.evidence)
        ):
            raise ValueError(
                "settled completion-review Handoff differs from terminal result"
            )
        return self


class MethodologyMigrationStateSnapshot(StrictModel):
    """Focused read-only state used by methodology migration preview."""

    task: TaskManifest
    control_task: TaskRecord | None
    plan: OrchestrationPlan
    current_methodology: MethodologyDefinition
    stage_inventory: StageInventory | None
    active_runs: int = Field(ge=0)
    active_consultations: int = Field(ge=0)
    unsettled_protocol_runs: int = Field(ge=0)


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


class ConsultationRun(StrictModel):
    consultation_id: str
    operation_key: str
    project_id: str
    task_id: str
    plan_id: str
    plan_version_observed: int = Field(ge=1)
    inventory_id: str
    inventory_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    stage_key: str
    role: str
    runtime: str
    repository_id: str
    repository_ref: str
    repository_commit: str = Field(pattern=r"^[0-9a-f]{7,64}$")
    decision_key: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    state: ConsultationState
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pid: int | None = Field(default=None, ge=1)
    process_status: ProcessStatus | None = None
    transport_status: TransportStatus | None = None
    schema_status: SchemaStatus
    repair_attempts: int = Field(default=0, ge=0, le=1)
    candidate_id: str | None = None
    output_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    error_code: str | None = None
    error_message: str | None = Field(default=None, max_length=4_000)
    token_reserved: int = Field(ge=1)
    cost_reserved_usd: float | None = Field(default=None, ge=0)
    usage_observation: ProviderUsageObservation | None = None
    started_at: str
    finished_at: str | None = None

    @model_validator(mode="after")
    def validate_consultation_state(self):
        if self.state == ConsultationState.RUNNING:
            if (
                self.process_status is not None
                or self.transport_status is not None
                or self.candidate_id is not None
                or self.usage_observation is not None
                or self.finished_at is not None
            ):
                raise ValueError("Running consultation may not carry settlement facts")
            return self
        if (
            self.process_status is None
            or self.transport_status is None
            or self.usage_observation is None
            or self.finished_at is None
            or self.output_sha256 is None
        ):
            raise ValueError("Terminal consultation requires complete settlement facts")
        if self.state == ConsultationState.COMPLETED:
            if (
                self.candidate_id is None
                or self.process_status != ProcessStatus.EXITED
                or self.transport_status != TransportStatus.COMPLETED
                or self.schema_status not in {
                    SchemaStatus.VALID,
                    SchemaStatus.REPAIRED,
                }
            ):
                raise ValueError(
                    "Completed consultation requires one schema-valid candidate"
                )
        elif self.candidate_id is not None:
            raise ValueError("Non-completed consultation may not bind a candidate")
        if (
            self.state == ConsultationState.PROTOCOL_FAILED
            and self.schema_status != SchemaStatus.PROTOCOL_FAILED
        ):
            raise ValueError(
                "Protocol-failed consultation requires protocol-failed schema state"
            )
        if (
            self.state == ConsultationState.INTERRUPTED
            and self.process_status != ProcessStatus.INTERRUPTED
        ):
            raise ValueError(
                "Interrupted consultation requires interrupted process state"
            )
        return self


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


class UnifiedTaskIndexItem(StrictModel):
    task_id: str
    project_id: str
    title: str
    description: str
    kind: str
    risk: TaskRisk
    priority: int = Field(ge=0, le=100)
    task_state: TaskStatus
    task_state_source: Literal["control_plane"] = "control_plane"
    task_state_version: int = Field(ge=1)
    compatibility_state: TaskState
    plan_id: str
    plan_state: PlanState
    methodology_id: str
    methodology_version: str
    provisional: bool
    task_updated_at: str
    plan_updated_at: str


class UnifiedTaskIndexPage(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    snapshot_at: str
    project_id: str
    tasks: list[UnifiedTaskIndexItem]
    page: ProjectionPage


class RunWaitState(str, Enum):
    SETTLED = "settled"
    OPERATIONAL_RUNTIME_PENDING = "operational_runtime_pending"
    PROTOCOL_START_PENDING = "protocol_start_pending"
    RUNTIME_OR_SETTLEMENT_PENDING = "runtime_or_settlement_pending"
    COMPATIBILITY_PROJECTION_PENDING = "compatibility_projection_pending"


class UnifiedStageGroupProgress(StrictModel):
    group_key: str
    sequence: int = Field(ge=1)
    title: str
    total_stages: int = Field(ge=1)
    completed_stages: int = Field(ge=0)
    remaining_stage_keys: list[str]


class UnifiedTaskProgress(StrictModel):
    source: Literal["control_plane_stage_inventory", "unavailable"] = (
        "control_plane_stage_inventory"
    )
    inventory_complete: bool
    inventory_unavailable_reason: str | None = None
    total_stages: int | None = Field(default=None, ge=0)
    completed_stages: int | None = Field(default=None, ge=0)
    current_stage_key: str | None
    current_stage_source: Literal[
        "control_plane_route",
        "compatibility_plan",
    ] | None = None
    completed_stage_keys: list[str]
    remaining_stage_keys: list[str]
    groups: list[UnifiedStageGroupProgress]


class UnifiedStageProjection(StrictModel):
    stage_key: str
    group_key: str | None = None
    sequence: int | None = Field(default=None, ge=1)
    title: str | None = None
    runtime: str | None = None
    inventory_stage: StageInventoryItem | None = None
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
    routing_policy: RoutingPolicyDecision | None = None
    runtime_preflight: PinnedRuntimePreflightDecision | None = None
    usage_observation: ProviderUsageObservation | None = None
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
    kind: Literal["attention", "plan_approval", "candidate_disposition"]
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
    schema_version: Literal["12.0"] = "12.0"
    snapshot_at: str
    task: TaskManifest
    task_state: TaskStatus | None
    task_state_source: Literal["control_plane"] = "control_plane"
    task_state_version: int | None = Field(default=None, ge=1)
    task_state_unavailable_reason: str | None = None
    task_state_lifecycle: Literal[
        "control_plane_managed",
        "reconciliation_required",
        "unavailable",
    ]
    task_lifecycle_decision: TaskLifecycleDecision | None = None
    stage_inventory: StageInventory | None = None
    stage_inventory_unavailable_reason: str | None = None
    stage_route: StageRouteDecision | None = None
    stage_route_unavailable_reason: str | None = None
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
    consultation_runs: list[ConsultationRun]
    consultation_candidates: list[ConsultationCandidate]
    consultation_candidate_dispositions: list[ConsultationCandidateDisposition]
    budget_amendments: list[BudgetAmendment]
    usage: list[UsageLedgerEntry]
    audit_events: list[UnifiedAuditEvent]
    budget: UnifiedBudgetProjection
    next_safe_action: GateDerivedNextSafeAction
    compatibility_next_action: str
    collection_totals: dict[str, int]
    collection_pages: dict[str, ProjectionPage]
