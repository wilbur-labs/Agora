"""Validation for one non-dispatching final methodology review claim."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agora.control_plane.auth import ControlPrincipal
from agora.control_plane.models import (
    GateRecord,
    ProtocolRunRecord,
    StageRecord,
    TaskRecord,
)
from agora.protocol.methodology_completion_review_claim import (
    MethodologyCompletionReviewClaimRequest,
)
from agora.protocol.methodology_execution import (
    MethodologyExecutionContract,
    MethodologyStageEvidenceContract,
)
from agora.protocol.methodology_migration import (
    MethodologyMigrationPreviewRequest,
    MigrationRuntimeReservation,
)
from agora.protocol.methodology_stage_run_dispatch import (
    MethodologyStageRunDispatchReceipt,
)
from agora.protocol.models import Artifact, Evidence, StageInventory
from agora.protocol.state_machines import GateStatus, StageStatus, TaskStatus
from agora.tasks.models import TaskManifest

from .models import OrchestrationPlan, PlanState
from .protocol_context import RepositoryRevision
from .runtime import RuntimeCommand
from .runtime_capabilities import runtime_command_sha256, runtime_registry_sha256


COMPLETION_REVIEW_CLAIM_REQUEST_LIMIT = 1_000_000


def load_methodology_completion_review_claim_request(
    path: Path,
) -> MethodologyCompletionReviewClaimRequest:
    """Load one strict bounded completion-review claim request."""

    try:
        if not path.is_file():
            raise ValueError(
                "Methodology completion-review claim request must be a file"
            )
        if path.stat().st_size > COMPLETION_REVIEW_CLAIM_REQUEST_LIMIT:
            raise ValueError(
                "Methodology completion-review claim request exceeds 1 MiB"
            )
        with path.open("rb") as handle:
            payload = handle.read(COMPLETION_REVIEW_CLAIM_REQUEST_LIMIT + 1)
        if len(payload) > COMPLETION_REVIEW_CLAIM_REQUEST_LIMIT:
            raise ValueError(
                "Methodology completion-review claim request exceeds 1 MiB"
            )
    except OSError as exc:
        raise ValueError(
            "Methodology completion-review claim request is unavailable"
        ) from exc
    return MethodologyCompletionReviewClaimRequest.model_validate_json(payload)


@dataclass(frozen=True)
class MethodologyCompletionReviewBudgetSnapshot:
    settled_tokens: int
    settled_cost: float
    active_tokens: int
    active_cost: float


@dataclass(frozen=True)
class MethodologyCompletionReviewClaimSnapshot:
    task: TaskManifest
    control_task: TaskRecord
    plan: OrchestrationPlan
    inventory: StageInventory
    migration_request: MethodologyMigrationPreviewRequest
    execution_contract: MethodologyExecutionContract
    final_dispatch_receipt: MethodologyStageRunDispatchReceipt
    production_protocol_run: ProtocolRunRecord
    final_stage: StageRecord
    final_gate: GateRecord
    active_evidence: tuple[Evidence, ...]
    output_artifacts: tuple[Artifact, ...]
    provider_budget: MethodologyCompletionReviewBudgetSnapshot


@dataclass(frozen=True)
class MethodologyCompletionReviewClaimAuthority:
    evidence_contract: MethodologyStageEvidenceContract
    runtime_reservation: MigrationRuntimeReservation
    runtime_command_sha256: str
    task_token_budget: int
    task_cost_budget_usd: float | None
    provider_settled_tokens: int
    provider_settled_cost_usd: float
    provider_active_tokens: int
    provider_active_cost_usd: float
    completion_review_protected_tokens: int
    completion_review_protected_cost_usd: float | None


def validate_methodology_completion_review_claim(
    *,
    snapshot: MethodologyCompletionReviewClaimSnapshot,
    request: MethodologyCompletionReviewClaimRequest,
    principal: ControlPrincipal,
    repository: RepositoryRevision | None,
    observed_artifact_sha256s: dict[str, str | None],
    runtimes: dict[str, RuntimeCommand],
) -> MethodologyCompletionReviewClaimAuthority:
    """Recheck final production authority without granting process authority."""

    task = snapshot.task
    control_task = snapshot.control_task
    plan = snapshot.plan
    inventory = snapshot.inventory
    migration_request = snapshot.migration_request
    contract = snapshot.execution_contract
    dispatch = snapshot.final_dispatch_receipt
    protocol_run = snapshot.production_protocol_run
    stage = snapshot.final_stage
    gate = snapshot.final_gate
    provider_budget = snapshot.provider_budget

    if "control_plane.approve" not in principal.permissions:
        raise ValueError(
            "Authenticated principal lacks control_plane.approve permission"
        )
    if task.project_id not in principal.projects:
        raise ValueError(
            "Authenticated principal is not authorized for the successor project"
        )
    if principal.principal_id != contract.authenticated_principal_id:
        raise ValueError(
            "Completion-review claim principal differs from the migration chain"
        )

    if (
        request.project_id != task.project_id
        or request.task_id != task.task_id
        or request.expected_task_version != task.version
        or request.expected_control_task_version != control_task.version
        or request.expected_control_task_status != control_task.status
        or request.plan_id != plan.plan_id
        or request.expected_plan_version != plan.version
        or request.inventory_id != inventory.inventory_id
        or request.inventory_sha256 != inventory.content_sha256
        or request.execution_contract_id != contract.contract_id
        or request.execution_contract_sha256 != contract.content_sha256
        or request.final_dispatch_id
        != dispatch.dispatch_claim.dispatch_id
        or request.final_dispatch_receipt_id != dispatch.receipt_id
        or request.final_dispatch_receipt_sha256 != dispatch.content_sha256
        or request.production_run_id != dispatch.dispatch_claim.run_id
        or request.production_handoff_pack_id != dispatch.handoff_pack_id
        or request.production_handoff_pack_sha256
        != dispatch.handoff_pack_sha256
    ):
        raise ValueError(
            "Methodology completion-review claim binding is stale or differs"
        )
    if (
        plan.methodology_id != contract.methodology_id
        or plan.methodology_id != migration_request.target_methodology_id
        or plan.methodology_version != contract.methodology_version
        or plan.methodology_version
        != migration_request.target_methodology_version
        or plan.methodology_sha256
        != contract.activation_definition_sha256
        or plan.methodology_sha256
        != migration_request.target_activation_definition_sha256
        or plan.provisional is not False
        or plan.current_stage_key != contract.stages[0].stage_key
    ):
        raise ValueError(
            "Plan methodology identity differs from the frozen migration"
        )
    if (
        plan.state != PlanState.READY_FOR_IMPLEMENTATION
        or control_task.status != TaskStatus.BLOCKED
        or control_task.task_id != task.task_id
        or control_task.project_id != task.project_id
        or plan.task_id != task.task_id
        or plan.project_id != task.project_id
        or inventory.task_id != task.task_id
        or inventory.project_id != task.project_id
        or contract.project_id != task.project_id
        or contract.task_id != task.task_id
        or contract.plan_id != plan.plan_id
        or contract.plan_version != plan.version
        or contract.inventory_id != inventory.inventory_id
        or contract.inventory_sha256 != inventory.content_sha256
        or contract.completion_stage_key != contract.stages[-1].stage_key
        or dispatch.dispatch_claim.stage_sequence != len(contract.stages)
        or dispatch.dispatch_claim.execution_contract_id != contract.contract_id
        or dispatch.dispatch_claim.execution_contract_sha256
        != contract.content_sha256
        or dispatch.dispatch_claim.project_id != task.project_id
        or dispatch.dispatch_claim.task_id != task.task_id
        or dispatch.dispatch_claim.stage_key != contract.completion_stage_key
        or dispatch.protocol_state.semantic_stage_result.value != "succeeded"
        or dispatch.handoff_pack_id is None
        or dispatch.handoff_pack_sha256 is None
        or dispatch.stage_status != StageStatus.BLOCKED
        or dispatch.gate_status != GateStatus.BLOCKED
        or dispatch.next_stage_key is not None
    ):
        raise ValueError(
            "Final methodology production dispatch is not review-claimable"
        )

    stage_contract = contract.stages[-1]
    handoff = protocol_run.handoff_pack
    expected_requirements = sorted(
        [item.requirement for item in stage_contract.gate.evidence_contracts],
        key=lambda item: item.requirement_id,
    )
    if (
        request.repository != contract.repository
        or request.stage_sequence != stage_contract.sequence
        or request.stage_key != stage_contract.stage_key
        or request.gate_key != stage_contract.gate_key
        or request.expected_stage_version != stage.version
        or request.expected_gate_version != gate.version
        or stage.stage_key != stage_contract.stage_key
        or stage.task_id != task.task_id
        or stage.project_id != task.project_id
        or stage.gate_key != stage_contract.gate_key
        or stage.status != StageStatus.BLOCKED
        or gate.stage_key != stage_contract.stage_key
        or gate.task_id != task.task_id
        or gate.project_id != task.project_id
        or gate.gate_key != stage_contract.gate_key
        or gate.status != GateStatus.BLOCKED
        or gate.requirements != expected_requirements
        or protocol_run.run_id != dispatch.dispatch_claim.run_id
        or protocol_run.project_id != task.project_id
        or protocol_run.task_id != task.task_id
        or protocol_run.stage_key != stage_contract.stage_key
        or protocol_run.gate_key != stage_contract.gate_key
        or protocol_run.settled_at is None
        or protocol_run.protocol_state != dispatch.protocol_state
        or protocol_run.adapter_error_code is not None
        or protocol_run.attention_required
        or protocol_run.attention_item_id is not None
        or protocol_run.context_pack.pack_id
        != dispatch.dispatch_claim.context_pack_id
        or protocol_run.context_pack.content_sha256
        != dispatch.dispatch_claim.context_pack_sha256
        or protocol_run.context_pack.project_id != task.project_id
        or protocol_run.context_pack.task_id != task.task_id
        or protocol_run.context_pack.stage_key != stage_contract.stage_key
        or protocol_run.context_pack.run_id
        != dispatch.dispatch_claim.run_id
        or handoff is None
        or handoff.pack_id != dispatch.handoff_pack_id
        or handoff.content_sha256 != dispatch.handoff_pack_sha256
    ):
        raise ValueError(
            "Final methodology Stage, Gate, Run, Context, or Handoff binding differs"
        )

    completion_contracts = [
        item
        for item in stage_contract.gate.evidence_contracts
        if item.source == "completion_review"
        and item.producer_responsibility == request.responsibility
    ]
    pins = [
        item
        for item in contract.runtime_pins
        if item.responsibility == request.responsibility
    ]
    reservations = [
        item
        for item in migration_request.budget.protected_runtime_reservations
        if item.runtime == request.runtime
    ]
    if len(completion_contracts) != 1 or len(pins) != 1 or len(reservations) != 1:
        raise ValueError(
            "Completion-review responsibility authority is unavailable"
        )
    evidence_contract = completion_contracts[0]
    pin = pins[0]
    reservation = reservations[0]
    runtime = runtimes.get(request.runtime)
    command_sha256 = (
        runtime_command_sha256(runtime) if runtime is not None else None
    )
    if (
        request.runtime != evidence_contract.producer_runtime
        or request.runtime != pin.runtime
        or command_sha256 is None
        or command_sha256 != pin.runtime_command_sha256
        or migration_request.runtime_registry_sha256
        != runtime_registry_sha256(runtimes)
        or request.token_reservation != reservation.token_reservation
        or request.cost_reservation_usd != reservation.cost_reservation_usd
    ):
        raise ValueError(
            "Completion-review runtime or protected budget binding differs"
        )

    completion_responsibilities = {
        "independent_correctness",
        "methodology_stewardship",
    }
    completion_pins = [
        item
        for item in contract.runtime_pins
        if item.responsibility in completion_responsibilities
    ]
    completion_reservations = [
        item
        for item in migration_request.budget.protected_runtime_reservations
        if item.runtime in {pin.runtime for pin in completion_pins}
    ]
    if (
        {item.responsibility for item in completion_pins}
        != completion_responsibilities
        or len(completion_pins) != 2
        or len(completion_reservations) != 2
        or {item.runtime for item in completion_reservations}
        != {item.runtime for item in completion_pins}
    ):
        raise ValueError(
            "Both completion-review protected reservations are unavailable"
        )
    if (
        plan.total_token_budget
        != migration_request.budget.task_token_budget
        or plan.total_cost_budget_usd
        != migration_request.budget.task_cost_budget_usd
    ):
        raise ValueError(
            "Plan budget differs from the frozen methodology migration budget"
        )
    protected_tokens = sum(
        item.token_reservation for item in completion_reservations
    )
    if (
        provider_budget.settled_tokens
        + provider_budget.active_tokens
        + protected_tokens
        > plan.total_token_budget
    ):
        raise ValueError(
            "Completion-review Token reservations exceed the remaining Plan budget"
        )
    protected_cost: float | None
    if plan.total_cost_budget_usd is None:
        if any(
            item.cost_reservation_usd is not None
            for item in completion_reservations
        ):
            raise ValueError(
                "Unbounded Plan cost cannot carry bounded completion-review reservations"
            )
        protected_cost = None
    else:
        if any(
            item.cost_reservation_usd is None
            for item in completion_reservations
        ):
            raise ValueError(
                "Cost-bounded Plan requires both completion-review reservations"
            )
        protected_cost = sum(
            float(item.cost_reservation_usd)
            for item in completion_reservations
            if item.cost_reservation_usd is not None
        )
        if (
            provider_budget.settled_cost
            + provider_budget.active_cost
            + protected_cost
            > plan.total_cost_budget_usd
        ):
            raise ValueError(
                "Completion-review cost reservations exceed the remaining Plan budget"
            )

    if repository is None or (
        repository.repository_id != contract.repository.repository_id
        or repository.ref != contract.repository.ref
        or repository.commit_sha != contract.repository.commit_sha
    ):
        raise ValueError(
            "Methodology completion-review repository binding is stale"
        )
    for artifact in migration_request.seed_artifacts:
        if observed_artifact_sha256s.get(artifact.path) != artifact.sha256:
            raise ValueError(
                "Methodology completion-review seed Artifact binding is stale"
            )

    handoff_artifacts = tuple(handoff.output_artifacts)
    if (
        not handoff_artifacts
        or snapshot.output_artifacts != handoff_artifacts
        or sorted(item.artifact_id for item in handoff_artifacts)
        != dispatch.artifact_ids
    ):
        raise ValueError(
            "Completion-review output Artifact authority differs"
        )
    active_ids = [item.evidence_id for item in snapshot.active_evidence]
    if (
        active_ids != gate.active_evidence_ids
        or active_ids != dispatch.evidence_ids
        or active_ids != dispatch.active_evidence_ids
        or any(
            item.requirement_id == evidence_contract.requirement.requirement_id
            for item in snapshot.active_evidence
        )
    ):
        raise ValueError(
            "Completion-review Gate Evidence authority differs"
        )

    return MethodologyCompletionReviewClaimAuthority(
        evidence_contract=evidence_contract,
        runtime_reservation=reservation,
        runtime_command_sha256=command_sha256,
        task_token_budget=plan.total_token_budget,
        task_cost_budget_usd=plan.total_cost_budget_usd,
        provider_settled_tokens=provider_budget.settled_tokens,
        provider_settled_cost_usd=provider_budget.settled_cost,
        provider_active_tokens=provider_budget.active_tokens,
        provider_active_cost_usd=provider_budget.active_cost,
        completion_review_protected_tokens=protected_tokens,
        completion_review_protected_cost_usd=protected_cost,
    )
