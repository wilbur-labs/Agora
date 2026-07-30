"""Read-only methodology migration preview contracts.

These contracts can explain whether one exact successor-Task proposal is
eligible for a later transactional migration path. They never mutate a Task,
select a route, dispatch a runtime, or authorize migration.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from .models import (
    GitCommit,
    HashSealedModel,
    NonBlank,
    ProtocolModel,
    Sha256Hex,
    StableId,
)
from .paths import canonical_repository_path
from .state_machines import TaskStatus


MigrationResponsibility = Literal[
    "production_execution",
    "independent_correctness",
    "methodology_stewardship",
]


class MigrationRepositoryBinding(ProtocolModel):
    repository_id: StableId
    ref: NonBlank
    commit_sha: GitCommit


class MigrationArtifactBinding(MigrationRepositoryBinding):
    path: Annotated[str, Field(min_length=1, max_length=4000)]
    sha256: Sha256Hex

    @field_validator("path")
    @classmethod
    def canonical_relative_path(cls, value: str) -> str:
        return canonical_repository_path(value)


class MigrationSeedArtifact(MigrationArtifactBinding):
    consumer_stage_key: StableId
    artifact_id: StableId
    source_producer_stage_key: StableId


class MigrationRuntimePin(ProtocolModel):
    responsibility: MigrationResponsibility
    runtime: StableId
    runtime_command_sha256: Sha256Hex


class MigrationRuntimeReservation(ProtocolModel):
    runtime: StableId
    token_reservation: int = Field(ge=1, le=10_000_000)
    cost_reservation_usd: float | None = Field(default=None, ge=0)


class MigrationStageBudgetAllocation(ProtocolModel):
    source_stage_key: StableId
    instance_count: int = Field(ge=1, le=100)
    token_allocation_per_instance: int = Field(ge=1, le=10_000_000)
    max_run_token_reservation_per_instance: int = Field(
        ge=1,
        le=10_000_000,
    )
    cost_allocation_per_instance_usd: float | None = Field(default=None, ge=0)
    max_run_cost_reservation_per_instance_usd: float | None = Field(
        default=None,
        ge=0,
    )

    @model_validator(mode="after")
    def validate_per_instance_reservation(self):
        if (
            self.max_run_token_reservation_per_instance
            > self.token_allocation_per_instance
        ):
            raise ValueError(
                "migration Stage Run Token reservation exceeds its allocation"
            )
        if self.cost_allocation_per_instance_usd is None:
            if self.max_run_cost_reservation_per_instance_usd is not None:
                raise ValueError(
                    "migration Stage cost reservation requires a cost allocation"
                )
        elif (
            self.max_run_cost_reservation_per_instance_usd is None
            or self.max_run_cost_reservation_per_instance_usd
            > self.cost_allocation_per_instance_usd
        ):
            raise ValueError(
                "migration Stage Run cost reservation must fit its allocation"
            )
        return self


class MigrationBudgetProposal(ProtocolModel):
    task_token_budget: int = Field(ge=3_000, le=10_000_000)
    task_cost_budget_usd: float | None = Field(default=None, ge=0)
    unit_of_work_count: int = Field(ge=1, le=100)
    stage_allocations: list[MigrationStageBudgetAllocation] = Field(
        min_length=1,
        max_length=200,
    )
    protected_runtime_reservations: list[MigrationRuntimeReservation] = Field(
        min_length=1,
        max_length=3,
    )

    @model_validator(mode="after")
    def validate_reservation_identity(self):
        stage_keys = [
            allocation.source_stage_key
            for allocation in self.stage_allocations
        ]
        if len(stage_keys) != len(set(stage_keys)):
            raise ValueError("migration Stage budget allocations must be unique")
        runtimes = [
            reservation.runtime
            for reservation in self.protected_runtime_reservations
        ]
        if len(runtimes) != len(set(runtimes)):
            raise ValueError("migration runtime reservations must be unique")
        return self


class MethodologyMigrationGateAssertion(HashSealedModel):
    """Explicit human assertion consumed only as preview input."""

    schema_version: Literal["1.0"] = "1.0"
    assertion_id: StableId
    gate_key: Literal["methodology-migration"] = "methodology-migration"
    migration_strategy: Literal["successor_task"] = "successor_task"
    human_approved: Literal[True] = True
    approved_by: Annotated[str, Field(min_length=1, max_length=256)]
    approved_at: AwareDatetime
    project_id: StableId
    task_id: StableId
    expected_task_version: int = Field(ge=1)
    expected_control_task_version: int = Field(ge=1)
    expected_task_status: TaskStatus
    plan_id: StableId
    plan_version: int = Field(ge=1)
    current_methodology_id: StableId
    current_methodology_version: Annotated[
        str,
        Field(pattern=r"^\d+\.\d+(?:\.\d+)?$"),
    ]
    current_methodology_sha256: Sha256Hex
    repository: MigrationRepositoryBinding
    migration_artifact: MigrationArtifactBinding
    target_activation_id: StableId
    target_methodology_id: StableId
    target_methodology_version: Annotated[
        str,
        Field(pattern=r"^\d+\.\d+\.\d+$"),
    ]
    target_source_graph_sha256: Sha256Hex
    target_activation_definition_sha256: Sha256Hex
    selected_scope: StableId
    runtime_registry_sha256: Sha256Hex
    budget_sha256: Sha256Hex
    seed_artifacts_sha256: Sha256Hex

    @model_validator(mode="after")
    def validate_repository_binding(self):
        artifact = self.migration_artifact
        if (
            artifact.repository_id != self.repository.repository_id
            or artifact.ref != self.repository.ref
            or artifact.commit_sha != self.repository.commit_sha
        ):
            raise ValueError(
                "migration Gate artifact must match its repository binding"
            )
        return self


class MethodologyMigrationPreviewRequest(HashSealedModel):
    """Exact Task-scoped proposal for a read-only eligibility preview."""

    schema_version: Literal["1.0"] = "1.0"
    request_id: StableId
    migration_strategy: Literal["successor_task"] = "successor_task"
    project_id: StableId
    task_id: StableId
    expected_task_version: int = Field(ge=1)
    expected_control_task_version: int = Field(ge=1)
    expected_task_status: TaskStatus
    plan_id: StableId
    expected_plan_version: int = Field(ge=1)
    current_methodology_id: StableId
    current_methodology_version: Annotated[
        str,
        Field(pattern=r"^\d+\.\d+(?:\.\d+)?$"),
    ]
    current_methodology_sha256: Sha256Hex
    repository: MigrationRepositoryBinding
    target_activation_id: StableId
    target_methodology_id: StableId
    target_methodology_version: Annotated[
        str,
        Field(pattern=r"^\d+\.\d+\.\d+$"),
    ]
    target_source_graph_sha256: Sha256Hex
    target_activation_definition_sha256: Sha256Hex
    selected_scope: StableId
    seed_artifacts: list[MigrationSeedArtifact] = Field(
        default_factory=list,
        max_length=500,
    )
    runtime_registry_sha256: Sha256Hex
    runtime_pins: list[MigrationRuntimePin] = Field(min_length=1, max_length=3)
    budget: MigrationBudgetProposal
    human_gate: MethodologyMigrationGateAssertion | None = None

    @model_validator(mode="after")
    def validate_collection_identity(self):
        seed_keys = [
            (
                seed.consumer_stage_key,
                seed.artifact_id,
                seed.source_producer_stage_key,
            )
            for seed in self.seed_artifacts
        ]
        if len(seed_keys) != len(set(seed_keys)):
            raise ValueError("migration scope seed requirements must be unique")

        responsibilities = [pin.responsibility for pin in self.runtime_pins]
        runtimes = [pin.runtime for pin in self.runtime_pins]
        if len(responsibilities) != len(set(responsibilities)):
            raise ValueError("migration runtime responsibilities must be unique")
        if len(runtimes) != len(set(runtimes)):
            raise ValueError("migration runtime pins must be pairwise distinct")
        return self


MigrationPreviewConstraint = Literal[
    "task_binding",
    "current_methodology_binding",
    "repository_binding",
    "target_source_binding",
    "scope_selection",
    "scope_seed_artifacts",
    "runtime_pins",
    "budget",
    "human_gate",
    "task_quiescence",
]

MIGRATION_PREVIEW_CONSTRAINTS: tuple[MigrationPreviewConstraint, ...] = (
    "task_binding",
    "current_methodology_binding",
    "repository_binding",
    "target_source_binding",
    "scope_selection",
    "scope_seed_artifacts",
    "runtime_pins",
    "budget",
    "human_gate",
    "task_quiescence",
)


class MethodologyMigrationPreviewCheck(ProtocolModel):
    constraint: MigrationPreviewConstraint
    satisfied: bool
    detail: Annotated[str, Field(min_length=1, max_length=1000)]


class MethodologyMigrationPreviewDecision(HashSealedModel):
    """Non-authoritative result of one exact read-only migration preview."""

    schema_version: Literal["1.0"] = "1.0"
    decision_id: StableId
    generated_at: AwareDatetime
    request_id: StableId
    request_sha256: Sha256Hex
    migration_strategy: Literal["successor_task"] = "successor_task"
    project_id: StableId
    task_id: StableId
    observed_task_version: int = Field(ge=1)
    observed_control_task_version: int | None = Field(default=None, ge=1)
    observed_task_status: TaskStatus | None = None
    plan_id: StableId
    observed_plan_version: int = Field(ge=1)
    current_methodology_id: StableId
    current_methodology_version: Annotated[
        str,
        Field(pattern=r"^\d+\.\d+(?:\.\d+)?$"),
    ]
    current_methodology_sha256: Sha256Hex
    observed_repository: MigrationRepositoryBinding | None = None
    target_activation_id: StableId
    target_methodology_id: StableId
    target_methodology_version: Annotated[
        str,
        Field(pattern=r"^\d+\.\d+\.\d+$"),
    ]
    target_source_graph_sha256: Sha256Hex
    target_activation_definition_sha256: Sha256Hex
    selected_scope: StableId
    active_runs: int = Field(ge=0)
    active_consultations: int = Field(ge=0)
    unsettled_protocol_runs: int = Field(ge=0)
    checks: list[MethodologyMigrationPreviewCheck] = Field(
        min_length=len(MIGRATION_PREVIEW_CONSTRAINTS),
        max_length=len(MIGRATION_PREVIEW_CONSTRAINTS),
    )
    blockers: list[MigrationPreviewConstraint] = Field(
        default_factory=list,
        max_length=len(MIGRATION_PREVIEW_CONSTRAINTS),
    )
    eligible: bool
    preview_only: Literal[True] = True
    state_mutated: Literal[False] = False
    plan_mutated: Literal[False] = False
    inventory_mutated: Literal[False] = False
    runtime_spawned: Literal[False] = False
    migration_executed: Literal[False] = False
    routing_authority: Literal[False] = False
    dispatch_authority: Literal[False] = False
    migration_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_decision_consistency(self):
        constraints = tuple(check.constraint for check in self.checks)
        if constraints != MIGRATION_PREVIEW_CONSTRAINTS:
            raise ValueError(
                "migration preview checks must be complete and deterministically ordered"
            )
        failed = [
            check.constraint
            for check in self.checks
            if not check.satisfied
        ]
        if self.blockers != failed:
            raise ValueError("migration preview blockers must match failed checks")
        if self.eligible != (not failed):
            raise ValueError("migration preview eligibility must match failed checks")
        return self
