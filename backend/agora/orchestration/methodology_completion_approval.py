"""Validation for authenticated final methodology Task completion."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agora.control_plane.auth import ControlPrincipal
from agora.protocol.hashing import canonical_sha256, seal_model_payload
from agora.protocol.methodology_completion_approval import (
    AuthenticatedMethodologyCompletionApproval,
    MethodologyCompletionApprovalRequest,
    MethodologyCompletionEvidenceApprovalBinding,
    MethodologyCompletionReviewApprovalBinding,
)
from agora.protocol.methodology_completion_review_claim import (
    MethodologyCompletionReviewClaimReceipt,
)
from agora.protocol.methodology_completion_review_dispatch import (
    MethodologyCompletionReviewDispatchReceipt,
)
from agora.protocol.models import Approval, ApprovalArtifactBinding, ApprovalStatus
from agora.protocol.state_machines import GateStatus, StageStatus, TaskStatus

from .methodology_completion_review_claim import (
    MethodologyCompletionReviewClaimSnapshot,
)
from .models import PlanState
from .protocol_context import RepositoryRevision


METHODOLOGY_COMPLETION_APPROVAL_REQUEST_LIMIT = 2_000_000


@dataclass(frozen=True)
class MethodologyCompletionReviewFinalAuthority:
    claim: MethodologyCompletionReviewClaimReceipt
    dispatch: MethodologyCompletionReviewDispatchReceipt


@dataclass(frozen=True)
class MethodologyCompletionApprovalSnapshot:
    completion: MethodologyCompletionReviewClaimSnapshot
    approval_artifacts: tuple[ApprovalArtifactBinding, ...]
    reviews: tuple[
        MethodologyCompletionReviewFinalAuthority,
        MethodologyCompletionReviewFinalAuthority,
    ]


def load_methodology_completion_approval_request(
    path: Path,
) -> MethodologyCompletionApprovalRequest:
    """Load one strict bounded final completion approval request."""

    try:
        if not path.is_file():
            raise ValueError("Methodology completion approval request must be a file")
        if path.stat().st_size > METHODOLOGY_COMPLETION_APPROVAL_REQUEST_LIMIT:
            raise ValueError("Methodology completion approval request exceeds 2 MiB")
        return MethodologyCompletionApprovalRequest.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except OSError as exc:
        raise ValueError("Methodology completion approval request is unreadable") from exc


def validate_methodology_completion_approval(
    *,
    snapshot: MethodologyCompletionApprovalSnapshot,
    request: MethodologyCompletionApprovalRequest,
    principal: ControlPrincipal,
    repository: RepositoryRevision | None,
    observed_artifact_sha256s: dict[str, str | None],
    authenticated_at,
) -> AuthenticatedMethodologyCompletionApproval:
    """Recheck complete final authority and bind the authenticated human."""

    base = snapshot.completion
    task = base.task
    control_task = base.control_task
    plan = base.plan
    inventory = base.inventory
    contract = base.execution_contract
    dispatch = base.final_dispatch_receipt
    protocol_run = base.production_protocol_run
    stage = base.final_stage
    gate = base.final_gate
    if "control_plane.approve" not in principal.permissions:
        raise ValueError(
            "Authenticated principal lacks control_plane.approve permission"
        )
    if task.project_id not in principal.projects:
        raise ValueError(
            "Authenticated principal is not authorized for the successor project"
        )
    if request.approved_by != principal.principal_id:
        raise ValueError(
            "Methodology completion approver differs from authenticated principal"
        )
    latest_reviewed_at = max(
        base.final_dispatch_receipt.settled_at,
        *(item.dispatch.settled_at for item in snapshot.reviews),
    )
    if not (
        latest_reviewed_at
        <= request.approved_at
        <= request.requested_at
        <= authenticated_at
    ):
        raise ValueError("Methodology completion approval chronology differs")
    control_task_matches = (
        control_task.status == TaskStatus.NEEDS_REVIEW
        and control_task.version == request.expected_control_task_version
    ) or (
        control_task.status == TaskStatus.COMPLETED
        and control_task.version == request.expected_control_task_version + 1
    )
    if (
        request.project_id != task.project_id
        or request.task_id != task.task_id
        or request.expected_task_version != task.version
        or not control_task_matches
        or request.plan_id != plan.plan_id
        or request.expected_plan_version != plan.version
        or plan.state != PlanState.READY_FOR_IMPLEMENTATION
        or request.inventory_id != inventory.inventory_id
        or request.inventory_sha256 != inventory.content_sha256
        or request.execution_contract_id != contract.contract_id
        or request.execution_contract_sha256 != contract.content_sha256
        or request.repository != contract.repository
        or request.source_graph_sha256 != contract.source_graph_sha256
        or request.activation_definition_sha256
        != contract.activation_definition_sha256
        or not contract.approval_policy.completion_human_approval_required
        or request.stage_sequence != len(contract.stages)
        or request.stage_key != contract.completion_stage_key
        or request.stage_key != stage.stage_key
        or request.expected_stage_version != stage.version
        or request.expected_stage_status != stage.status
        or stage.status != StageStatus.COMPLETED
        or request.gate_key != stage.gate_key
        or request.gate_key != gate.gate_key
        or request.expected_gate_version != gate.version
        or request.expected_gate_status != gate.status
        or gate.status != GateStatus.PASSED
        or request.final_dispatch_id != dispatch.dispatch_claim.dispatch_id
        or request.final_dispatch_receipt_id != dispatch.receipt_id
        or request.final_dispatch_receipt_sha256 != dispatch.content_sha256
        or request.production_run_id != dispatch.dispatch_claim.run_id
        or request.production_handoff_pack_id != dispatch.handoff_pack_id
        or request.production_handoff_pack_sha256
        != dispatch.handoff_pack_sha256
        or protocol_run.handoff_pack is None
        or protocol_run.handoff_pack.pack_id
        != request.production_handoff_pack_id
        or protocol_run.handoff_pack.content_sha256
        != request.production_handoff_pack_sha256
    ):
        raise ValueError("Methodology completion approval authority differs")
    if repository is None or (
        repository.repository_id != request.repository.repository_id
        or repository.ref != request.repository.ref
        or repository.commit_sha != request.repository.commit_sha
    ):
        raise ValueError("Methodology completion approval repository differs")

    expected_artifacts = sorted(
        (item.version_ref() for item in base.output_artifacts),
        key=lambda item: (item.artifact_id, item.version),
    )
    if request.artifact_versions != expected_artifacts:
        raise ValueError("Methodology completion approval Artifacts differ")
    expected_approval_artifacts = list(snapshot.approval_artifacts)
    if request.approval_artifacts != expected_approval_artifacts:
        raise ValueError("Methodology completion Approval Artifacts differ")
    for item in expected_approval_artifacts:
        if observed_artifact_sha256s.get(item.path) != item.sha256:
            raise ValueError(
                "Methodology completion approval Artifact content differs"
            )

    expected_evidence = [
        MethodologyCompletionEvidenceApprovalBinding(
            evidence_id=item.evidence_id,
            evidence_sha256=canonical_sha256(item),
            requirement_id=item.requirement_id,
            producer_run_id=item.producer.run_id,
            status="passed",
        )
        for item in sorted(base.active_evidence, key=lambda item: item.evidence_id)
    ]
    if (
        any(item.status.value != "passed" for item in base.active_evidence)
        or request.active_evidence != expected_evidence
        or request.active_evidence
        != sorted(request.active_evidence, key=lambda item: item.evidence_id)
    ):
        raise ValueError("Methodology completion approval active Evidence differs")

    expected_reviews: list[MethodologyCompletionReviewApprovalBinding] = []
    evidence_by_id = {item.evidence_id: item for item in base.active_evidence}
    for authority in snapshot.reviews:
        claim = authority.claim
        review = authority.dispatch
        if (
            review.dispatch_claim.completion_review_claim_receipt_id
            != claim.receipt_id
            or review.dispatch_claim.completion_review_claim_receipt_sha256
            != claim.content_sha256
            or not review.evidence_ids
            or review.handoff_pack_id is None
            or review.handoff_pack_sha256 is None
        ):
            raise ValueError("Methodology completion review approval provenance differs")
        evidence = evidence_by_id.get(review.evidence_ids[0])
        if evidence is None or evidence.status.value != "passed":
            raise ValueError("Methodology completion review approval Evidence differs")
        expected_reviews.append(
            MethodologyCompletionReviewApprovalBinding(
                responsibility=claim.responsibility,
                claim_receipt_id=claim.receipt_id,
                claim_receipt_sha256=claim.content_sha256,
                dispatch_id=review.dispatch_claim.dispatch_id,
                dispatch_receipt_id=review.receipt_id,
                dispatch_receipt_sha256=review.content_sha256,
                review_run_id=claim.review_run_id,
                handoff_pack_id=review.handoff_pack_id,
                handoff_pack_sha256=review.handoff_pack_sha256,
                evidence_id=evidence.evidence_id,
                evidence_sha256=canonical_sha256(evidence),
            )
        )
    if request.completion_reviews != expected_reviews:
        raise ValueError("Methodology completion review approval bindings differ")

    approval = Approval(
        approval_id=request.approval_id,
        project_id=request.project_id,
        task_id=request.task_id,
        stage_key=request.stage_key,
        gate_key=request.gate_key,
        repository_id=request.repository.repository_id,
        ref=request.repository.ref,
        commit_sha=request.repository.commit_sha,
        artifact_versions=request.approval_artifacts,
        status=ApprovalStatus.ACTIVE,
        approved_by=request.approved_by,
        approved_at=request.approved_at,
    )
    return AuthenticatedMethodologyCompletionApproval.model_validate(
        seal_model_payload(
            AuthenticatedMethodologyCompletionApproval,
            {
                "schema_version": "1.0",
                "authenticated_at": authenticated_at,
                "request": request,
                "request_sha256": request.content_sha256,
                "approval": approval,
                "approval_sha256": canonical_sha256(approval),
                "authenticated_principal_id": principal.principal_id,
                "authenticated_permission": "control_plane.approve",
                "authenticated_project_id": request.project_id,
                "credential_verified": True,
            },
        )
    )
