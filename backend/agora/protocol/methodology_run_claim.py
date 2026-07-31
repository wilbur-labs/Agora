"""Authenticated first-Run claim contracts for methodology successors."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, model_validator

from .methodology_migration import MigrationRepositoryBinding
from .methodology_route_activation import MethodologySeedArtifactRegistration
from .models import (
    HashSealedModel,
    RequiredOutput,
    RunBudget,
    Sha256Hex,
    StableId,
)
from .state_machines import GateStatus, StageStatus, TaskStatus


class MethodologyRunClaimRequest(HashSealedModel):
    """Explicit request to claim one formal first Run without process launch."""

    schema_version: Literal["1.0"] = "1.0"
    request_id: StableId
    requested_at: AwareDatetime
    project_id: StableId
    task_id: StableId
    expected_task_version: int = Field(ge=1)
    expected_control_task_version: int = Field(ge=1)
    expected_control_task_status: TaskStatus
    plan_id: StableId
    expected_plan_version: int = Field(ge=1)
    inventory_id: StableId
    inventory_sha256: Sha256Hex
    execution_contract_id: StableId
    execution_contract_sha256: Sha256Hex
    route_activation_receipt_id: StableId
    route_activation_receipt_sha256: Sha256Hex
    repository: MigrationRepositoryBinding
    first_stage_key: StableId
    first_gate_key: StableId
    runtime: StableId
    run_id: StableId
    context_pack_id: StableId
    context_pack_schema_version: Literal["1.0"] = "1.0"
    claim_formal_run: Literal[True] = True
    start_runtime_process: Literal[False] = False

    @model_validator(mode="after")
    def validate_run_identity(self):
        if self.context_pack_id != f"context:{self.run_id}":
            raise ValueError(
                "methodology Context Pack identity must be derived from its Run"
            )
        return self


class MethodologyRunClaimReceipt(HashSealedModel):
    """Authoritative receipt for one atomic, non-spawning formal Run claim."""

    schema_version: Literal["1.0"] = "1.0"
    receipt_id: StableId
    claimed_at: AwareDatetime
    request_id: StableId
    request_sha256: Sha256Hex
    authenticated_principal_id: Annotated[
        str,
        Field(min_length=1, max_length=256),
    ]
    authenticated_permission: Literal["control_plane.approve"] = (
        "control_plane.approve"
    )
    project_id: StableId
    task_id: StableId
    task_version_before: int = Field(ge=1)
    task_version_after: int = Field(ge=2)
    control_task_status_before: TaskStatus
    control_task_version_before: int = Field(ge=1)
    control_task_status_after: TaskStatus
    control_task_version_after: int = Field(ge=1)
    plan_id: StableId
    plan_version: int = Field(ge=1)
    inventory_id: StableId
    inventory_sha256: Sha256Hex
    execution_contract_id: StableId
    execution_contract_sha256: Sha256Hex
    route_activation_receipt_id: StableId
    route_activation_receipt_sha256: Sha256Hex
    repository: MigrationRepositoryBinding
    first_stage_key: StableId
    first_stage_version_before: int = Field(ge=1)
    first_stage_status_before: Literal[StageStatus.READY] = StageStatus.READY
    first_stage_version_after: int = Field(ge=2)
    first_stage_status_after: Literal[StageStatus.RUNNING] = StageStatus.RUNNING
    first_gate_key: StableId
    first_gate_version: int = Field(ge=1)
    first_gate_status: Literal[GateStatus.PENDING] = GateStatus.PENDING
    runtime: StableId
    run_id: StableId
    context_pack_id: StableId
    context_pack_sha256: Sha256Hex
    seed_artifacts: list[MethodologySeedArtifactRegistration] = Field(
        default_factory=list,
        max_length=100,
    )
    required_outputs: list[RequiredOutput] = Field(
        default_factory=list,
        max_length=100,
    )
    budget: RunBudget
    request_authenticated: Literal[True] = True
    context_pack_materialized: Literal[True] = True
    formal_run_created: Literal[True] = True
    usage_reservation_recorded: Literal[True] = True
    compatibility_run_created: Literal[False] = False
    protocol_artifacts_created: Literal[False] = False
    runtime_preflight_created: Literal[False] = False
    process_started: Literal[False] = False
    runtime_spawned: Literal[False] = False
    process_spawn_authority: Literal[False] = False
    provider_substitution: Literal[False] = False

    @model_validator(mode="after")
    def validate_claim_binding(self):
        if self.task_version_after != self.task_version_before + 1:
            raise ValueError(
                "methodology Run claim must increment the Task version once"
            )
        if self.control_task_version_after < self.control_task_version_before:
            raise ValueError(
                "methodology Run claim may not regress Control Task lifecycle"
            )
        if self.first_stage_version_after != self.first_stage_version_before + 1:
            raise ValueError(
                "methodology Run claim must advance the first Stage version once"
            )
        if self.context_pack_id != f"context:{self.run_id}":
            raise ValueError(
                "methodology Run claim Context Pack identity must match its Run"
            )
        seed_keys = [
            (item.consumer_stage_key, item.source_artifact_id)
            for item in self.seed_artifacts
        ]
        if len(seed_keys) != len(set(seed_keys)):
            raise ValueError(
                "methodology Run claim seed registrations must be unique"
            )
        output_ids = [item.output_id for item in self.required_outputs]
        if len(output_ids) != len(set(output_ids)):
            raise ValueError(
                "methodology Run claim required outputs must be unique"
            )
        return self
