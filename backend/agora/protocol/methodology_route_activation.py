"""Authenticated first-route activation contracts for methodology successors."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, model_validator

from .methodology_migration import MigrationRepositoryBinding
from .models import (
    ArtifactVersionRef,
    HashSealedModel,
    ProtocolModel,
    Sha256Hex,
    StableId,
)
from .state_machines import GateStatus, StageStatus, TaskStatus


class MethodologySeedArtifactRegistration(ProtocolModel):
    """One external Task seed bound to a selected consumer Stage input."""

    consumer_stage_key: StableId
    source_artifact_id: StableId
    artifact: ArtifactVersionRef

    @model_validator(mode="after")
    def validate_seed_binding(self):
        if self.artifact.kind != self.source_artifact_id:
            raise ValueError(
                "methodology seed Artifact kind must retain its source Artifact id"
            )
        if self.artifact.location is None:
            raise ValueError(
                "methodology seed Artifact registration requires a repository location"
            )
        return self


class MethodologyRouteActivationRequest(HashSealedModel):
    """Explicit request to configure and activate only the first sealed route."""

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
    repository: MigrationRepositoryBinding
    first_stage_key: StableId
    first_gate_key: StableId
    activate_first_route: Literal[True] = True
    dispatch_runtime: Literal[False] = False


class MethodologyRouteActivationReceipt(HashSealedModel):
    """Authoritative receipt for one atomic, non-dispatching first route."""

    schema_version: Literal["1.0"] = "1.0"
    receipt_id: StableId
    activated_at: AwareDatetime
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
    migration_receipt_id: StableId
    migration_receipt_sha256: Sha256Hex
    repository: MigrationRepositoryBinding
    first_stage_key: StableId
    first_stage_version: int = Field(ge=2)
    first_stage_status: Literal[StageStatus.READY] = StageStatus.READY
    first_gate_key: StableId
    first_gate_version: Literal[1] = 1
    first_gate_status: Literal[GateStatus.PENDING] = GateStatus.PENDING
    seed_artifacts: list[MethodologySeedArtifactRegistration] = Field(
        default_factory=list,
        max_length=100,
    )
    request_authenticated: Literal[True] = True
    first_stage_configured: Literal[True] = True
    first_gate_configured: Literal[True] = True
    seed_artifact_references_registered: Literal[True] = True
    route_activated: Literal[True] = True
    protocol_artifacts_created: Literal[False] = False
    run_created: Literal[False] = False
    runtime_spawned: Literal[False] = False
    dispatch_authority: Literal[False] = False
    provider_substitution: Literal[False] = False

    @model_validator(mode="after")
    def validate_activation_binding(self):
        if self.task_version_after != self.task_version_before + 1:
            raise ValueError(
                "methodology route activation must increment the Task version once"
            )
        if self.control_task_version_after < self.control_task_version_before:
            raise ValueError(
                "methodology route activation may not regress Control Task lifecycle"
            )
        seed_keys = [
            (item.consumer_stage_key, item.source_artifact_id)
            for item in self.seed_artifacts
        ]
        if len(seed_keys) != len(set(seed_keys)):
            raise ValueError(
                "methodology route activation seed registrations must be unique"
            )
        return self
