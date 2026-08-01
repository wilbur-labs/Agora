"""Authenticated formal Run claim for a later methodology Stage."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, model_validator

from .hashing import canonical_sha256
from .methodology_migration import MigrationRepositoryBinding
from .models import (
    ArtifactVersionRef,
    HashSealedModel,
    ProtocolModel,
    RequiredOutput,
    RunBudget,
    Sha256Hex,
    StableId,
)
from .state_machines import GateStatus, StageStatus, TaskStatus


def methodology_stage_run_id(
    *,
    task_id: str,
    execution_contract_sha256: str,
    stage_sequence: int,
    stage_key: str,
) -> str:
    """Return the canonical formal Run identity for one later Stage."""

    digest = canonical_sha256(
        {
            "task_id": task_id,
            "execution_contract_sha256": execution_contract_sha256,
            "stage_sequence": stage_sequence,
            "stage_key": stage_key,
        }
    )
    return f"methodology-stage-run:{digest[:32]}"


class MethodologyStageInputArtifactBinding(ProtocolModel):
    """One exact prior-Stage output or Task seed consumed by a Context."""

    consumer_stage_key: StableId
    source_artifact_id: StableId
    producer_stage_key: StableId
    producer_run_id: StableId | None
    artifact: ArtifactVersionRef

    @model_validator(mode="after")
    def validate_artifact_kind(self):
        if self.artifact.kind != self.source_artifact_id:
            raise ValueError(
                "methodology Stage input Artifact kind must match its source id"
            )
        if self.producer_run_id is None and self.artifact.location is None:
            raise ValueError(
                "methodology Task seed input requires an external Artifact location"
            )
        return self


class MethodologyStageRunClaimRequest(HashSealedModel):
    """Explicit request to claim one later formal Run without process launch."""

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
    stage_gate_receipt_id: StableId
    stage_gate_receipt_sha256: Sha256Hex
    predecessor_dispatch_receipt_id: StableId
    predecessor_dispatch_receipt_sha256: Sha256Hex
    repository: MigrationRepositoryBinding
    stage_sequence: int = Field(ge=2, le=200)
    stage_key: StableId
    gate_key: StableId
    runtime: StableId
    expected_stage_version: int = Field(ge=2)
    expected_gate_version: int = Field(ge=1)
    expected_gate_status: Literal[GateStatus.PENDING] = GateStatus.PENDING
    run_id: StableId
    context_pack_id: StableId
    context_pack_schema_version: Literal["1.0"] = "1.0"
    claim_formal_run: Literal[True] = True
    start_runtime_process: Literal[False] = False

    @model_validator(mode="after")
    def validate_run_identity(self):
        if self.context_pack_id != f"context:{self.run_id}":
            raise ValueError(
                "methodology Stage Context Pack identity must derive from its Run"
            )
        return self


class MethodologyStageRunClaimReceipt(HashSealedModel):
    """Authoritative receipt for one atomic, non-spawning later Run claim."""

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
    stage_gate_receipt_id: StableId
    stage_gate_receipt_sha256: Sha256Hex
    predecessor_dispatch_receipt_id: StableId
    predecessor_dispatch_receipt_sha256: Sha256Hex
    repository: MigrationRepositoryBinding
    stage_sequence: int = Field(ge=2, le=200)
    stage_key: StableId
    stage_version_before: int = Field(ge=2)
    stage_status_before: Literal[StageStatus.READY] = StageStatus.READY
    stage_version_after: int = Field(ge=3)
    stage_status_after: Literal[StageStatus.RUNNING] = StageStatus.RUNNING
    gate_key: StableId
    gate_version: int = Field(ge=1)
    gate_status: Literal[GateStatus.PENDING] = GateStatus.PENDING
    runtime: StableId
    run_id: StableId
    context_pack_id: StableId
    context_pack_sha256: Sha256Hex
    input_artifact_bindings: list[MethodologyStageInputArtifactBinding] = Field(
        default_factory=list,
        max_length=200,
    )
    required_outputs: list[RequiredOutput] = Field(
        default_factory=list,
        max_length=100,
    )
    budget: RunBudget
    request_authenticated: Literal[True] = True
    stage_gate_receipt_reused: Literal[True] = True
    predecessor_dispatch_receipt_reused: Literal[True] = True
    context_pack_materialized: Literal[True] = True
    formal_run_created: Literal[True] = True
    usage_reservation_recorded: Literal[True] = True
    compatibility_run_created: Literal[False] = False
    protocol_artifacts_created: Literal[False] = False
    protocol_evidence_created: Literal[False] = False
    runtime_preflight_created: Literal[False] = False
    process_started: Literal[False] = False
    runtime_spawned: Literal[False] = False
    process_spawn_authority: Literal[False] = False
    provider_substitution: Literal[False] = False

    @model_validator(mode="after")
    def validate_claim_binding(self):
        if self.task_version_after != self.task_version_before + 1:
            raise ValueError(
                "methodology Stage Run claim must increment Task version once"
            )
        if self.control_task_version_after < self.control_task_version_before:
            raise ValueError(
                "methodology Stage Run claim may not regress Control Task lifecycle"
            )
        if self.stage_version_after != self.stage_version_before + 1:
            raise ValueError(
                "methodology Stage Run claim must advance Stage version once"
            )
        if self.context_pack_id != f"context:{self.run_id}":
            raise ValueError(
                "methodology Stage Run Context identity must match its Run"
            )
        binding_keys = [
            (
                item.consumer_stage_key,
                item.source_artifact_id,
                item.producer_stage_key,
                item.artifact.artifact_id,
            )
            for item in self.input_artifact_bindings
        ]
        artifact_ids = [
            item.artifact.artifact_id
            for item in self.input_artifact_bindings
        ]
        if (
            len(binding_keys) != len(set(binding_keys))
            or len(artifact_ids) != len(set(artifact_ids))
        ):
            raise ValueError(
                "methodology Stage input Artifact bindings must be unique"
            )
        output_ids = [item.output_id for item in self.required_outputs]
        if len(output_ids) != len(set(output_ids)):
            raise ValueError(
                "methodology Stage Run required outputs must be unique"
            )
        return self
