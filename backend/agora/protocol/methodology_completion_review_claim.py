"""Authenticated, non-dispatching claim for one final methodology review."""
from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, model_validator

from .methodology_migration import MigrationRepositoryBinding
from .models import (
    ArtifactVersionRef,
    GateRequirement,
    HashSealedModel,
    Sha256Hex,
    StableId,
)
from .state_machines import GateStatus, StageStatus, TaskStatus


MethodologyCompletionReviewResponsibility = Literal[
    "independent_correctness",
    "methodology_stewardship",
]


def methodology_completion_review_run_id(
    *,
    task_id: str,
    execution_contract_sha256: str,
    final_dispatch_receipt_sha256: str,
    responsibility: MethodologyCompletionReviewResponsibility,
) -> str:
    """Derive one stable reviewer Run identity from immutable authority."""

    digest = hashlib.sha256(
        (
            task_id
            + "\0"
            + execution_contract_sha256
            + "\0"
            + final_dispatch_receipt_sha256
            + "\0"
            + responsibility
        ).encode("utf-8")
    ).hexdigest()
    return f"methodology-completion-review:{digest[:32]}"


class MethodologyCompletionReviewClaimRequest(HashSealedModel):
    """Explicit authority request for one reviewer Run without process start."""

    schema_version: Literal["1.0"] = "1.0"
    request_id: StableId
    requested_at: AwareDatetime
    project_id: StableId
    task_id: StableId
    expected_task_version: int = Field(ge=1)
    expected_control_task_version: int = Field(ge=1)
    expected_control_task_status: Literal[TaskStatus.BLOCKED] = TaskStatus.BLOCKED
    plan_id: StableId
    expected_plan_version: int = Field(ge=1)
    inventory_id: StableId
    inventory_sha256: Sha256Hex
    execution_contract_id: StableId
    execution_contract_sha256: Sha256Hex
    final_dispatch_id: StableId
    final_dispatch_receipt_id: StableId
    final_dispatch_receipt_sha256: Sha256Hex
    production_run_id: StableId
    production_handoff_pack_id: StableId
    production_handoff_pack_sha256: Sha256Hex
    repository: MigrationRepositoryBinding
    stage_sequence: int = Field(ge=1, le=200)
    stage_key: StableId
    gate_key: StableId
    expected_stage_version: int = Field(ge=1)
    expected_gate_version: int = Field(ge=1)
    responsibility: MethodologyCompletionReviewResponsibility
    runtime: StableId
    token_reservation: int = Field(ge=1, le=10_000_000)
    cost_reservation_usd: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    claim_review_run: Literal[True] = True
    start_runtime_process: Literal[False] = False
    register_evidence: Literal[False] = False
    evaluate_gate: Literal[False] = False
    finalize_stage: Literal[False] = False


class MethodologyCompletionReviewClaimReceipt(HashSealedModel):
    """Immutable claim binding one reviewer Run to final production facts."""

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
    task_version_after: int = Field(ge=1)
    control_task_status_before: Literal[TaskStatus.BLOCKED] = TaskStatus.BLOCKED
    control_task_version_before: int = Field(ge=1)
    control_task_status_after: Literal[TaskStatus.BLOCKED] = TaskStatus.BLOCKED
    control_task_version_after: int = Field(ge=1)
    plan_id: StableId
    plan_version_before: int = Field(ge=1)
    plan_version_after: int = Field(ge=1)
    inventory_id: StableId
    inventory_sha256: Sha256Hex
    execution_contract_id: StableId
    execution_contract_sha256: Sha256Hex
    final_dispatch_id: StableId
    final_dispatch_receipt_id: StableId
    final_dispatch_receipt_sha256: Sha256Hex
    production_run_id: StableId
    production_handoff_pack_id: StableId
    production_handoff_pack_sha256: Sha256Hex
    repository: MigrationRepositoryBinding
    stage_sequence: int = Field(ge=1, le=200)
    stage_key: StableId
    gate_key: StableId
    stage_version_before: int = Field(ge=1)
    stage_status_before: Literal[StageStatus.BLOCKED] = StageStatus.BLOCKED
    stage_version_after: int = Field(ge=1)
    stage_status_after: Literal[StageStatus.BLOCKED] = StageStatus.BLOCKED
    gate_version_before: int = Field(ge=1)
    gate_status_before: Literal[GateStatus.BLOCKED] = GateStatus.BLOCKED
    gate_version_after: int = Field(ge=1)
    gate_status_after: Literal[GateStatus.BLOCKED] = GateStatus.BLOCKED
    active_evidence_ids_before: list[StableId] = Field(
        min_length=1,
        max_length=500,
    )
    active_evidence_ids_after: list[StableId] = Field(
        min_length=1,
        max_length=500,
    )
    responsibility: MethodologyCompletionReviewResponsibility
    runtime: StableId
    runtime_command_sha256: Sha256Hex
    review_run_id: StableId
    requirement: GateRequirement
    artifact_versions: list[ArtifactVersionRef] = Field(
        min_length=1,
        max_length=100,
    )
    token_reservation: int = Field(ge=1, le=10_000_000)
    cost_reservation_usd: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    task_token_budget: int = Field(ge=1, le=10_000_000)
    task_cost_budget_usd: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    provider_settled_tokens_before: int = Field(ge=0)
    provider_settled_cost_usd_before: float = Field(ge=0, allow_inf_nan=False)
    provider_active_tokens_before: int = Field(ge=0)
    provider_active_cost_usd_before: float = Field(ge=0, allow_inf_nan=False)
    completion_review_protected_tokens: int = Field(ge=1, le=20_000_000)
    completion_review_protected_cost_usd: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    budget_admission_passed: Literal[True] = True
    claim_persisted: Literal[True] = True
    task_mutated: Literal[False] = False
    control_task_mutated: Literal[False] = False
    plan_mutated: Literal[False] = False
    stage_mutated: Literal[False] = False
    gate_mutated: Literal[False] = False
    protocol_artifacts_created: Literal[False] = False
    protocol_evidence_created: Literal[False] = False
    process_started: Literal[False] = False
    process_spawn_authority: Literal[False] = False
    provider_substitution: Literal[False] = False

    @model_validator(mode="after")
    def validate_claim_binding(self):
        expected_run_id = methodology_completion_review_run_id(
            task_id=self.task_id,
            execution_contract_sha256=self.execution_contract_sha256,
            final_dispatch_receipt_sha256=(
                self.final_dispatch_receipt_sha256
            ),
            responsibility=self.responsibility,
        )
        if self.review_run_id != expected_run_id:
            raise ValueError(
                "methodology completion-review Run identity differs"
            )
        if self.receipt_id != (
            f"methodology-completion-review-claim-receipt:{self.request_id}"
        ):
            raise ValueError(
                "methodology completion-review claim receipt identity differs"
            )
        if self.review_run_id == self.production_run_id:
            raise ValueError(
                "methodology completion review must be independent from production"
            )
        if (
            self.task_version_after != self.task_version_before
            or self.control_task_version_after
            != self.control_task_version_before
            or self.plan_version_after != self.plan_version_before
            or self.stage_version_after != self.stage_version_before
            or self.gate_version_after != self.gate_version_before
            or self.active_evidence_ids_after
            != self.active_evidence_ids_before
        ):
            raise ValueError(
                "methodology completion-review claim may mutate only its claim ledger"
            )
        artifact_keys = [
            (item.artifact_id, item.version) for item in self.artifact_versions
        ]
        if len(artifact_keys) != len(set(artifact_keys)):
            raise ValueError(
                "methodology completion-review Artifact versions must be unique"
            )
        if len(self.active_evidence_ids_before) != len(
            set(self.active_evidence_ids_before)
        ):
            raise ValueError(
                "methodology completion-review active Evidence ids must be unique"
            )
        if (
            self.provider_settled_tokens_before
            + self.provider_active_tokens_before
            + self.completion_review_protected_tokens
            > self.task_token_budget
        ):
            raise ValueError(
                "methodology completion-review Token budget admission differs"
            )
        if self.task_cost_budget_usd is None:
            if self.completion_review_protected_cost_usd is not None:
                raise ValueError(
                    "unbounded methodology cost cannot seal a protected cost"
                )
        elif (
            self.completion_review_protected_cost_usd is None
            or self.provider_settled_cost_usd_before
            + self.provider_active_cost_usd_before
            + self.completion_review_protected_cost_usd
            > self.task_cost_budget_usd
        ):
            raise ValueError(
                "methodology completion-review cost budget admission differs"
            )
        return self
