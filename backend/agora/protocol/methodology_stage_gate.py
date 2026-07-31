"""Authenticated Gate configuration for the next methodology Stage."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, model_validator

from .methodology_migration import MigrationRepositoryBinding
from .models import GateRequirement, HashSealedModel, Sha256Hex, StableId
from .state_machines import GateStatus, StageStatus, TaskStatus


class MethodologyStageGateRequest(HashSealedModel):
    """Explicit request to configure only the currently routed next Gate."""

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
    predecessor_dispatch_receipt_id: StableId
    predecessor_dispatch_receipt_sha256: Sha256Hex
    predecessor_run_id: StableId
    predecessor_stage_key: StableId
    predecessor_gate_key: StableId
    predecessor_handoff_pack_id: StableId
    predecessor_handoff_pack_sha256: Sha256Hex
    repository: MigrationRepositoryBinding
    stage_sequence: int = Field(ge=2, le=200)
    stage_key: StableId
    gate_key: StableId
    runtime: StableId
    expected_stage_version: int = Field(ge=2)
    configure_formal_gate: Literal[True] = True
    claim_formal_run: Literal[False] = False
    start_runtime_process: Literal[False] = False


class MethodologyStageGateReceipt(HashSealedModel):
    """Immutable receipt for one non-dispatching next-Stage Gate setup."""

    schema_version: Literal["1.0"] = "1.0"
    receipt_id: StableId
    configured_at: AwareDatetime
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
    task_version_after: int = Field(ge=1)
    control_task_status_before: TaskStatus
    control_task_version_before: int = Field(ge=1)
    control_task_status_after: TaskStatus
    control_task_version_after: int = Field(ge=1)
    plan_id: StableId
    plan_version_before: int = Field(ge=1)
    plan_version_after: int = Field(ge=1)
    inventory_id: StableId
    inventory_sha256: Sha256Hex
    execution_contract_id: StableId
    execution_contract_sha256: Sha256Hex
    predecessor_dispatch_receipt_id: StableId
    predecessor_dispatch_receipt_sha256: Sha256Hex
    predecessor_run_id: StableId
    predecessor_stage_key: StableId
    predecessor_stage_status: Literal[StageStatus.COMPLETED] = (
        StageStatus.COMPLETED
    )
    predecessor_gate_key: StableId
    predecessor_gate_status: Literal[GateStatus.PASSED] = GateStatus.PASSED
    predecessor_handoff_pack_id: StableId
    predecessor_handoff_pack_sha256: Sha256Hex
    repository: MigrationRepositoryBinding
    stage_sequence: int = Field(ge=2, le=200)
    stage_key: StableId
    stage_version_before: int = Field(ge=2)
    stage_status_before: Literal[StageStatus.READY] = StageStatus.READY
    stage_version_after: int = Field(ge=2)
    stage_status_after: Literal[StageStatus.READY] = StageStatus.READY
    gate_key: StableId
    gate_version: Literal[1] = 1
    gate_status: Literal[GateStatus.PENDING] = GateStatus.PENDING
    runtime: StableId
    requirements: list[GateRequirement] = Field(min_length=1, max_length=210)
    request_authenticated: Literal[True] = True
    predecessor_protocol_settled: Literal[True] = True
    formal_gate_configured: Literal[True] = True
    route_runnable_before: Literal[True] = True
    route_runnable_after: Literal[True] = True
    formal_run_claimable_before: Literal[False] = False
    formal_run_claimable_after: Literal[True] = True
    task_mutated: Literal[False] = False
    control_task_mutated: Literal[False] = False
    plan_mutated: Literal[False] = False
    stage_mutated: Literal[False] = False
    protocol_artifacts_created: Literal[False] = False
    protocol_evidence_created: Literal[False] = False
    context_pack_created: Literal[False] = False
    run_created: Literal[False] = False
    process_started: Literal[False] = False
    dispatch_authority: Literal[False] = False
    provider_substitution: Literal[False] = False

    @model_validator(mode="after")
    def validate_configuration_binding(self):
        if (
            self.task_version_after != self.task_version_before
            or self.control_task_version_after
            != self.control_task_version_before
            or self.control_task_status_after
            != self.control_task_status_before
            or self.plan_version_after != self.plan_version_before
            or self.stage_version_after != self.stage_version_before
            or self.stage_status_after != self.stage_status_before
        ):
            raise ValueError(
                "methodology Gate configuration may mutate only the formal Gate"
            )
        requirement_ids = [item.requirement_id for item in self.requirements]
        if (
            len(requirement_ids) != len(set(requirement_ids))
            or self.requirements
            != sorted(
                self.requirements,
                key=lambda item: item.requirement_id,
            )
        ):
            raise ValueError(
                "methodology Gate requirements must be unique and canonical"
            )
        return self
