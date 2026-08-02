"""Authenticated, artifact-bound human completion of one methodology Task."""
from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, model_validator

from .hashing import canonical_sha256
from .methodology_completion_review_claim import (
    MethodologyCompletionReviewResponsibility,
)
from .methodology_migration import MigrationRepositoryBinding
from .models import (
    Approval,
    ApprovalArtifactBinding,
    ApprovalStatus,
    ArtifactVersionRef,
    HashSealedModel,
    ProtocolModel,
    Sha256Hex,
    StableId,
)
from .state_machines import GateStatus, StageStatus, TaskStatus


def methodology_completion_approval_id(
    *,
    task_id: str,
    execution_contract_sha256: str,
    final_dispatch_receipt_sha256: str,
    review_receipt_sha256s: list[str],
) -> str:
    """Derive one approval identity from the complete reviewed authority."""

    material = "\0".join(
        [
            task_id,
            execution_contract_sha256,
            final_dispatch_receipt_sha256,
            *review_receipt_sha256s,
        ]
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"methodology-completion-approval:{digest[:32]}"


class MethodologyCompletionReviewApprovalBinding(ProtocolModel):
    responsibility: MethodologyCompletionReviewResponsibility
    claim_receipt_id: StableId
    claim_receipt_sha256: Sha256Hex
    dispatch_id: StableId
    dispatch_receipt_id: StableId
    dispatch_receipt_sha256: Sha256Hex
    review_run_id: StableId
    handoff_pack_id: StableId
    handoff_pack_sha256: Sha256Hex
    evidence_id: StableId
    evidence_sha256: Sha256Hex


class MethodologyCompletionEvidenceApprovalBinding(ProtocolModel):
    evidence_id: StableId
    evidence_sha256: Sha256Hex
    requirement_id: StableId
    producer_run_id: StableId
    status: Literal["passed"] = "passed"


class MethodologyCompletionApprovalRequest(HashSealedModel):
    """One explicit human assertion over the final reviewed Task snapshot."""

    schema_version: Literal["1.0"] = "1.0"
    request_id: StableId
    requested_at: AwareDatetime
    approval_id: StableId
    approved_by: Annotated[str, Field(min_length=1, max_length=128)]
    approved_at: AwareDatetime
    approval_reason: Annotated[str, Field(min_length=1, max_length=4000)]
    human_approved: Literal[True] = True
    project_id: StableId
    task_id: StableId
    expected_task_version: int = Field(ge=1)
    expected_control_task_version: int = Field(ge=1)
    expected_control_task_status: Literal[TaskStatus.NEEDS_REVIEW] = (
        TaskStatus.NEEDS_REVIEW
    )
    plan_id: StableId
    expected_plan_version: int = Field(ge=1)
    inventory_id: StableId
    inventory_sha256: Sha256Hex
    execution_contract_id: StableId
    execution_contract_sha256: Sha256Hex
    repository: MigrationRepositoryBinding
    source_graph_sha256: Sha256Hex
    activation_definition_sha256: Sha256Hex
    stage_sequence: int = Field(ge=1, le=200)
    stage_key: StableId
    expected_stage_version: int = Field(ge=1)
    expected_stage_status: Literal[StageStatus.COMPLETED] = StageStatus.COMPLETED
    gate_key: StableId
    expected_gate_version: int = Field(ge=1)
    expected_gate_status: Literal[GateStatus.PASSED] = GateStatus.PASSED
    final_dispatch_id: StableId
    final_dispatch_receipt_id: StableId
    final_dispatch_receipt_sha256: Sha256Hex
    production_run_id: StableId
    production_handoff_pack_id: StableId
    production_handoff_pack_sha256: Sha256Hex
    completion_reviews: list[MethodologyCompletionReviewApprovalBinding] = Field(
        min_length=2,
        max_length=2,
    )
    artifact_versions: list[ArtifactVersionRef] = Field(
        min_length=1,
        max_length=100,
    )
    approval_artifacts: list[ApprovalArtifactBinding] = Field(
        min_length=1,
        max_length=100,
    )
    active_evidence: list[MethodologyCompletionEvidenceApprovalBinding] = Field(
        min_length=1,
        max_length=210,
    )
    complete_task: Literal[True] = True
    start_runtime_process: Literal[False] = False
    register_artifact: Literal[False] = False
    register_evidence: Literal[False] = False
    evaluate_gate: Literal[False] = False
    modify_stage: Literal[False] = False

    @model_validator(mode="after")
    def validate_completion_assertion(self):
        if self.approved_at > self.requested_at:
            raise ValueError("methodology completion approval predates human approval")
        responsibilities = [item.responsibility for item in self.completion_reviews]
        if responsibilities != [
            "independent_correctness",
            "methodology_stewardship",
        ]:
            raise ValueError(
                "methodology completion review approvals must be complete and ordered"
            )
        expected_approval_id = methodology_completion_approval_id(
            task_id=self.task_id,
            execution_contract_sha256=self.execution_contract_sha256,
            final_dispatch_receipt_sha256=self.final_dispatch_receipt_sha256,
            review_receipt_sha256s=[
                item.dispatch_receipt_sha256 for item in self.completion_reviews
            ],
        )
        if self.approval_id != expected_approval_id:
            raise ValueError("methodology completion approval identity differs")
        if self.request_id != f"{self.approval_id}:request":
            raise ValueError("methodology completion approval request identity differs")
        artifact_keys = [
            (item.artifact_id, item.version) for item in self.artifact_versions
        ]
        if artifact_keys != sorted(set(artifact_keys)):
            raise ValueError(
                "methodology completion approval Artifacts must be canonical"
            )
        approval_paths = [item.path for item in self.approval_artifacts]
        if approval_paths != sorted(set(approval_paths)):
            raise ValueError(
                "methodology completion Approval Artifacts must be canonical"
            )
        for item in self.approval_artifacts:
            if (
                item.repository_id != self.repository.repository_id
                or item.ref != self.repository.ref
                or item.commit_sha != self.repository.commit_sha
            ):
                raise ValueError(
                    "methodology completion Approval Artifact repository differs"
                )
        evidence_ids = [item.evidence_id for item in self.active_evidence]
        if evidence_ids != sorted(set(evidence_ids)):
            raise ValueError(
                "methodology completion approval Evidence must be canonical"
            )
        evidence_by_id = {
            item.evidence_id: item for item in self.active_evidence
        }
        for identity_field in (
            "claim_receipt_id",
            "dispatch_id",
            "dispatch_receipt_id",
            "review_run_id",
            "handoff_pack_id",
            "evidence_id",
        ):
            identities = [
                getattr(item, identity_field) for item in self.completion_reviews
            ]
            if len(set(identities)) != len(identities):
                raise ValueError(
                    "methodology completion reviewer identities must be distinct"
                )
        for review in self.completion_reviews:
            evidence = evidence_by_id.get(review.evidence_id)
            if (
                evidence is None
                or evidence.evidence_sha256 != review.evidence_sha256
                or evidence.producer_run_id != review.review_run_id
            ):
                raise ValueError(
                    "methodology completion review Evidence binding differs"
                )
        return self


class AuthenticatedMethodologyCompletionApproval(HashSealedModel):
    """Persisted final Approval whose human principal was authenticated."""

    schema_version: Literal["1.0"] = "1.0"
    authenticated_at: AwareDatetime
    request: MethodologyCompletionApprovalRequest
    request_sha256: Sha256Hex
    approval: Approval
    approval_sha256: Sha256Hex
    authenticated_principal_id: Annotated[
        str,
        Field(min_length=1, max_length=128),
    ]
    authenticated_permission: Literal["control_plane.approve"] = (
        "control_plane.approve"
    )
    authenticated_project_id: StableId
    credential_verified: Literal[True] = True

    @model_validator(mode="after")
    def validate_authenticated_approval(self):
        request = self.request
        approval = self.approval
        if (
            self.request_sha256 != request.content_sha256
            or self.approval_sha256 != canonical_sha256(approval)
        ):
            raise ValueError("authenticated methodology completion hash differs")
        if (
            self.authenticated_at < request.approved_at
            or self.authenticated_at < request.requested_at
            or self.authenticated_principal_id != request.approved_by
            or self.authenticated_project_id != request.project_id
            or approval.approval_id != request.approval_id
            or approval.project_id != request.project_id
            or approval.task_id != request.task_id
            or approval.stage_key != request.stage_key
            or approval.gate_key != request.gate_key
            or approval.repository_id != request.repository.repository_id
            or approval.ref != request.repository.ref
            or approval.commit_sha != request.repository.commit_sha
            or approval.artifact_versions != request.approval_artifacts
            or approval.approved_by != request.approved_by
            or approval.approved_at != request.approved_at
            or approval.status != ApprovalStatus.ACTIVE
            or approval.stale_reason is not None
        ):
            raise ValueError(
                "authenticated methodology completion Approval differs from request"
            )
        return self


class MethodologyCompletionApprovalReceipt(HashSealedModel):
    """Authoritative receipt for final human completion of one reviewed Task."""

    schema_version: Literal["1.0"] = "1.0"
    receipt_id: StableId
    completed_at: AwareDatetime
    authenticated_approval: AuthenticatedMethodologyCompletionApproval
    authenticated_approval_sha256: Sha256Hex
    project_id: StableId
    task_id: StableId
    task_version: int = Field(ge=1)
    plan_id: StableId
    plan_version: int = Field(ge=1)
    inventory_id: StableId
    inventory_sha256: Sha256Hex
    execution_contract_id: StableId
    execution_contract_sha256: Sha256Hex
    stage_key: StableId
    stage_status: Literal[StageStatus.COMPLETED] = StageStatus.COMPLETED
    gate_key: StableId
    gate_status: Literal[GateStatus.PASSED] = GateStatus.PASSED
    previous_task_status: Literal[TaskStatus.NEEDS_REVIEW] = TaskStatus.NEEDS_REVIEW
    previous_control_task_version: int = Field(ge=1)
    task_status: Literal[TaskStatus.COMPLETED] = TaskStatus.COMPLETED
    control_task_version: int = Field(ge=2)
    approval_id: StableId
    approval_sha256: Sha256Hex
    artifact_count: int = Field(ge=1, le=100)
    approval_artifact_count: int = Field(ge=1, le=100)
    active_evidence_ids: list[StableId] = Field(min_length=1, max_length=210)
    approval_registered: Literal[True] = True
    task_completed: Literal[True] = True
    compatibility_projection_mutated: Literal[False] = False
    runtime_spawned: Literal[False] = False
    artifact_created: Literal[False] = False
    evidence_created: Literal[False] = False
    gate_evaluated: Literal[False] = False
    stage_mutated: Literal[False] = False

    @model_validator(mode="after")
    def validate_completion_receipt(self):
        authenticated = self.authenticated_approval
        request = authenticated.request
        if (
            self.receipt_id
            != f"methodology-completion-approval-receipt:{request.approval_id}"
            or self.authenticated_approval_sha256 != authenticated.content_sha256
            or self.completed_at < authenticated.authenticated_at
            or self.project_id != request.project_id
            or self.task_id != request.task_id
            or self.task_version != request.expected_task_version
            or self.plan_id != request.plan_id
            or self.plan_version != request.expected_plan_version
            or self.inventory_id != request.inventory_id
            or self.inventory_sha256 != request.inventory_sha256
            or self.execution_contract_id != request.execution_contract_id
            or self.execution_contract_sha256
            != request.execution_contract_sha256
            or self.stage_key != request.stage_key
            or self.gate_key != request.gate_key
            or self.previous_control_task_version
            != request.expected_control_task_version
            or self.control_task_version != self.previous_control_task_version + 1
            or self.approval_id != request.approval_id
            or self.approval_sha256 != authenticated.approval_sha256
            or self.artifact_count != len(request.artifact_versions)
            or self.approval_artifact_count != len(request.approval_artifacts)
            or self.active_evidence_ids
            != [item.evidence_id for item in request.active_evidence]
        ):
            raise ValueError("methodology completion approval receipt differs")
        return self
