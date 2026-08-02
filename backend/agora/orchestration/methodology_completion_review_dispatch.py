"""Context, policy, and spawn claim for one final methodology reviewer."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from agora.control_plane.models import (
    GateRecord,
    ProtocolRunRecord,
    StageRecord,
    StageRouteDecision,
    TaskRecord,
)
from agora.protocol.hashing import canonical_sha256, seal_model_payload
from agora.protocol.methodology_completion_review_claim import (
    MethodologyCompletionReviewClaimRequest,
    MethodologyCompletionReviewClaimReceipt,
)
from agora.protocol.methodology_completion_review_dispatch import (
    MethodologyCompletionReviewDispatchClaim,
    MethodologyCompletionReviewDispatchPolicyCheck,
    MethodologyCompletionReviewDispatchPolicyDecision,
    methodology_completion_review_evidence_id,
)
from agora.protocol.methodology_execution import MethodologyExecutionContract
from agora.protocol.methodology_migration import MethodologyMigrationPreviewRequest
from agora.protocol.methodology_stage_run_dispatch import (
    MethodologyStageRunDispatchReceipt,
)
from agora.protocol.models import (
    Artifact,
    ContextEntry,
    ContextPack,
    Evidence,
    EvidenceStatus,
    NativeRuntimeCapabilityObservation,
    PinnedRuntimePreflightDecision,
    RunBudget,
    StageContract,
    StageInventory,
)
from agora.protocol.state_machines import GateStatus, StageStatus, TaskStatus
from agora.tasks.models import TaskManifest

from .methodology_completion_review_claim import (
    MethodologyCompletionReviewBudgetSnapshot,
)
from .models import OrchestrationPlan, PlanState
from .protocol_context import RepositoryRevision
from .routing_policy import (
    ROUTING_POLICY_ID,
    ROUTING_POLICY_SHA256,
    ROUTING_POLICY_VERSION,
    RUNTIME_CAPABILITIES,
)
from .runtime import RuntimeCommand
from .runtime_capabilities import runtime_command_sha256, runtime_registry_sha256
from .runtime_preflight import derive_pinned_runtime_preflight


COMPLETION_REVIEW_TIMEOUT_SECONDS = 3_600
COMPLETION_REVIEW_MAX_OUTPUT_BYTES = 1_000_000
COMPLETION_REVIEW_RECOVERY_LEASE_SECONDS = 30


@dataclass(frozen=True)
class MethodologyCompletionReviewDispatchSnapshot:
    task: TaskManifest
    control_task: TaskRecord
    plan: OrchestrationPlan
    inventory: StageInventory
    migration_request: MethodologyMigrationPreviewRequest
    execution_contract: MethodologyExecutionContract
    final_dispatch_receipt: MethodologyStageRunDispatchReceipt
    completion_review_claim_receipt: MethodologyCompletionReviewClaimReceipt
    completion_review_claim_request: MethodologyCompletionReviewClaimRequest
    production_protocol_run: ProtocolRunRecord
    review_protocol_run: ProtocolRunRecord | None
    final_stage: StageRecord
    final_gate: GateRecord
    active_evidence: tuple[Evidence, ...]
    output_artifacts: tuple[Artifact, ...]
    provider_budget: MethodologyCompletionReviewBudgetSnapshot


def completion_review_expected_details(
    claim: MethodologyCompletionReviewClaimReceipt,
    *,
    verdict: str,
) -> dict[str, object]:
    """Return the exact authority details a native review Evidence must echo."""

    if verdict not in {"pass", "fail"}:
        raise ValueError("completion-review verdict must be pass or fail")
    return {
        "methodology_completion_review": {
            "claim_receipt_id": claim.receipt_id,
            "claim_receipt_sha256": claim.content_sha256,
            "responsibility": claim.responsibility,
            "production_run_id": claim.production_run_id,
            "production_handoff_pack_id": claim.production_handoff_pack_id,
            "production_handoff_pack_sha256": (
                claim.production_handoff_pack_sha256
            ),
            "verdict": verdict,
        }
    }


def build_methodology_completion_review_context(
    *,
    snapshot: MethodologyCompletionReviewDispatchSnapshot,
    generated_at: datetime | str,
) -> ContextPack:
    """Build a review-only Context Pack from immutable production authority."""

    claim = snapshot.completion_review_claim_receipt
    requirement = claim.requirement
    evidence_id = methodology_completion_review_evidence_id(claim.review_run_id)
    authority = {
        "claim_receipt_id": claim.receipt_id,
        "claim_receipt_sha256": claim.content_sha256,
        "responsibility": claim.responsibility,
        "runtime": claim.runtime,
        "review_run_id": claim.review_run_id,
        "production_run_id": claim.production_run_id,
        "production_handoff_pack_id": claim.production_handoff_pack_id,
        "production_handoff_pack_sha256": claim.production_handoff_pack_sha256,
        "artifact_versions": [
            item.model_dump(mode="json") for item in claim.artifact_versions
        ],
        "requirement": requirement.model_dump(mode="json"),
        "eligible_evidence": {
            "evidence_id": evidence_id,
            "passed_details": completion_review_expected_details(
                claim,
                verdict="pass",
            ),
            "failed_details": completion_review_expected_details(
                claim,
                verdict="fail",
            ),
        },
    }
    content = json.dumps(
        authority,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    policy_id = f"completion-review-authority:{claim.content_sha256[:32]}"
    policy = ContextEntry(
        entry_id=policy_id,
        version=1,
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        title="Pinned final methodology completion-review authority",
        content=content,
        source_ref=f"{claim.receipt_id}:{claim.content_sha256}",
    )
    stage_contract = StageContract(
        contract_id=(
            "completion-review-stage-contract:"
            + canonical_sha256(
                {
                    "execution_contract_sha256": (
                        claim.execution_contract_sha256
                    ),
                    "claim_receipt_sha256": claim.content_sha256,
                    "responsibility": claim.responsibility,
                }
            )[:32]
        ),
        title=f"Final methodology review: {claim.responsibility}",
        objective=(
            "Independently review the exact final production Handoff and its "
            "registered Artifact versions for the pinned Gate requirement."
        ),
        completion_conditions=[
            "Return exactly one Handoff Pack for this reviewer Run.",
            "Return no output Artifacts, questions, native state, or memory candidates.",
            "Return exactly one scoped Evidence item with the pinned identity and details.",
            "Use passed only for a positive review; use failed_product for a rejection.",
        ],
    )
    payload = {
        "schema_version": "1.0",
        "pack_id": f"context:{claim.review_run_id}",
        "project_id": claim.project_id,
        "task_id": claim.task_id,
        "stage_key": claim.stage_key,
        "run_id": claim.review_run_id,
        "generated_at": generated_at,
        "stage_contract": stage_contract,
        "input_artifacts": claim.artifact_versions,
        "required_outputs": [],
        "forbidden_constraints": [
            "Do not modify the repository or any native runtime files.",
            "Do not write Task, Stage, Gate, Approval, Artifact, or Evidence state.",
            "Do not claim process exit as semantic review success.",
            "Do not impersonate production or the other completion reviewer.",
            "Do not invent rework, approval, routing, or provider substitution authority.",
        ],
        "policies": [policy],
        "task_memory": [],
        "project_knowledge": [],
        "user_preferences": [],
        "budget": RunBudget(
            max_seconds=COMPLETION_REVIEW_TIMEOUT_SECONDS,
            max_output_bytes=COMPLETION_REVIEW_MAX_OUTPUT_BYTES,
            max_model_tokens=claim.token_reservation,
            max_cost_usd=claim.cost_reservation_usd,
        ),
    }
    return ContextPack.model_validate(seal_model_payload(ContextPack, payload))


def _completion_review_route(
    snapshot: MethodologyCompletionReviewDispatchSnapshot,
) -> StageRouteDecision:
    claim = snapshot.completion_review_claim_receipt
    match = [
        (group, stage)
        for group in snapshot.inventory.groups
        for stage in group.stages
        if stage.stage_key == claim.stage_key
    ]
    if len(match) != 1:
        raise ValueError("completion-review final inventory Stage is unavailable")
    group, stage = match[0]
    return StageRouteDecision(
        task_id=claim.task_id,
        project_id=claim.project_id,
        inventory_id=claim.inventory_id,
        inventory_sha256=claim.inventory_sha256,
        group_key=group.group_key,
        group_sequence=group.sequence,
        stage_key=claim.stage_key,
        gate_key=claim.gate_key,
        stage_sequence=stage.sequence,
        inventory_sequence=claim.stage_sequence,
        title=f"Completion review: {claim.responsibility}",
        role=claim.responsibility,
        runtime=claim.runtime,
        stage_status=StageStatus.BLOCKED,
        gate_status=GateStatus.BLOCKED,
        runnable=False,
    )


def _active_completion_responsibilities(
    snapshot: MethodologyCompletionReviewDispatchSnapshot,
) -> set[str]:
    requirements = {
        item.requirement.requirement_id: item
        for item in snapshot.execution_contract.stages[-1].gate.evidence_contracts
        if item.source == "completion_review"
    }
    result: set[str] = set()
    for evidence in snapshot.active_evidence:
        contract = requirements.get(evidence.requirement_id)
        if contract is None:
            continue
        if (
            evidence.status == EvidenceStatus.PASSED
            and evidence.producer.runtime == contract.producer_runtime
        ):
            result.add(contract.producer_responsibility)
    return result


def derive_methodology_completion_review_dispatch_policy(
    *,
    snapshot: MethodologyCompletionReviewDispatchSnapshot,
    context_pack: ContextPack,
    repository: RepositoryRevision | None,
    runtimes: dict[str, RuntimeCommand],
    evaluated_at: datetime | str,
) -> MethodologyCompletionReviewDispatchPolicyDecision:
    """Explain one reviewer dispatch without selecting a route or runtime."""

    task = snapshot.task
    control_task = snapshot.control_task
    plan = snapshot.plan
    inventory = snapshot.inventory
    migration = snapshot.migration_request
    contract = snapshot.execution_contract
    production = snapshot.final_dispatch_receipt
    claim = snapshot.completion_review_claim_receipt
    stage = snapshot.final_stage
    gate = snapshot.final_gate
    active_responsibilities = _active_completion_responsibilities(snapshot)

    claimed_review_run = bool(
        task.task_id == claim.task_id
        and task.project_id == claim.project_id
        and control_task.status == TaskStatus.BLOCKED
        and plan.plan_id == claim.plan_id
        and plan.state == PlanState.READY_FOR_IMPLEMENTATION
        and inventory.inventory_id == claim.inventory_id
        and inventory.content_sha256 == claim.inventory_sha256
        and contract.contract_id == claim.execution_contract_id
        and contract.content_sha256 == claim.execution_contract_sha256
        and production.receipt_id == claim.final_dispatch_receipt_id
        and production.content_sha256 == claim.final_dispatch_receipt_sha256
        and snapshot.review_protocol_run is None
        and claim.responsibility not in active_responsibilities
    )
    completion_context = bool(
        context_pack.pack_id == f"context:{claim.review_run_id}"
        and context_pack.run_id == claim.review_run_id
        and context_pack.project_id == claim.project_id
        and context_pack.task_id == claim.task_id
        and context_pack.stage_key == claim.stage_key
        and context_pack.input_artifacts == claim.artifact_versions
        and context_pack.required_outputs == []
        and context_pack.budget.max_model_tokens == claim.token_reservation
        and context_pack.budget.max_cost_usd == claim.cost_reservation_usd
    )
    current_final_stage = bool(
        contract.completion_stage_key == claim.stage_key
        and contract.stages[-1].sequence == claim.stage_sequence
        and stage.stage_key == claim.stage_key
        and stage.gate_key == claim.gate_key
        and stage.status == StageStatus.BLOCKED
        and gate.stage_key == claim.stage_key
        and gate.gate_key == claim.gate_key
        and gate.status == GateStatus.BLOCKED
        and production.stage_status == StageStatus.BLOCKED
        and production.gate_status == GateStatus.BLOCKED
        and production.next_stage_key is None
    )
    repository_binding = bool(
        repository is not None
        and repository.repository_id == claim.repository.repository_id
        and repository.ref == claim.repository.ref
        and repository.commit_sha == claim.repository.commit_sha
        and claim.repository == contract.repository
    )
    pins = {
        item.responsibility: item for item in contract.runtime_pins
    }
    pin = pins.get(claim.responsibility)
    runtime_binding = bool(
        runtime_registry_sha256(runtimes) == migration.runtime_registry_sha256
        and set(item.runtime for item in contract.runtime_pins) == set(runtimes)
        and pin is not None
        and pin.runtime == claim.runtime
        and claim.runtime in runtimes
        and pin.runtime_command_sha256
        == runtime_command_sha256(runtimes[claim.runtime])
        and claim.runtime_command_sha256
        == runtime_command_sha256(runtimes[claim.runtime])
    )
    production_pin = pins.get("production_execution")
    other_pins = [
        item
        for responsibility, item in pins.items()
        if responsibility != claim.responsibility
    ]
    reviewer_independence = bool(
        production_pin is not None
        and claim.review_run_id != claim.production_run_id
        and claim.runtime != production_pin.runtime
        and all(claim.runtime != item.runtime for item in other_pins)
    )

    remaining_reservations = []
    for responsibility in (
        "independent_correctness",
        "methodology_stewardship",
    ):
        if responsibility in active_responsibilities:
            continue
        responsibility_pin = pins.get(responsibility)
        if responsibility_pin is None:
            continue
        remaining_reservations.extend(
            item
            for item in migration.budget.protected_runtime_reservations
            if item.runtime == responsibility_pin.runtime
        )
    remaining_tokens = sum(
        item.token_reservation for item in remaining_reservations
    )
    token_ok = bool(
        remaining_tokens >= claim.token_reservation
        and snapshot.provider_budget.settled_tokens
        + snapshot.provider_budget.active_tokens
        + remaining_tokens
        <= plan.total_token_budget
    )
    if plan.total_cost_budget_usd is None:
        cost_ok = all(
            item.cost_reservation_usd is None for item in remaining_reservations
        )
    else:
        cost_ok = bool(
            all(
                item.cost_reservation_usd is not None
                for item in remaining_reservations
            )
            and snapshot.provider_budget.settled_cost
            + snapshot.provider_budget.active_cost
            + sum(
                float(item.cost_reservation_usd)
                for item in remaining_reservations
                if item.cost_reservation_usd is not None
            )
            <= plan.total_cost_budget_usd
        )
    usage_reservation = bool(
        claim.token_reservation > 0
        and token_ok
        and cost_ok
        and any(
            item.runtime == claim.runtime
            and item.token_reservation == claim.token_reservation
            and item.cost_reservation_usd == claim.cost_reservation_usd
            for item in remaining_reservations
        )
    )

    facts = {
        "claimed_review_run": (
            claimed_review_run,
            "The immutable authenticated reviewer claim remains unused."
            if claimed_review_run
            else "The reviewer claim, lifecycle, or Run occupancy changed.",
        ),
        "completion_context": (
            completion_context,
            "The review-only Context binds the exact production Artifact versions."
            if completion_context
            else "The completion-review Context binding changed.",
        ),
        "current_final_stage": (
            current_final_stage,
            "The final Stage and Gate remain blocked only for completion review."
            if current_final_stage
            else "The final methodology Stage or Gate lifecycle changed.",
        ),
        "repository_binding": (
            repository_binding,
            "The clean repository/ref/commit matches the frozen contract."
            if repository_binding
            else "The completion-review repository binding is stale.",
        ),
        "reviewer_independence": (
            reviewer_independence,
            "The reviewer runtime and Run are independent from all other roles."
            if reviewer_independence
            else "The completion-review independence binding changed.",
        ),
        "runtime_binding": (
            runtime_binding,
            "The complete runtime registry and reviewer command match their pins."
            if runtime_binding
            else "The completion-review runtime registry or command pin changed.",
        ),
        "usage_reservation": (
            usage_reservation,
            "All still-required completion reviewers fit the remaining Plan budget."
            if usage_reservation
            else "The completion-review reservation no longer fits the Plan budget.",
        ),
    }
    checks = [
        MethodologyCompletionReviewDispatchPolicyCheck(
            check=name,
            satisfied=value[0],
            detail=value[1],
        )
        for name, value in sorted(facts.items())
    ]
    blockers = [item.detail for item in checks if not item.satisfied]
    payload = {
        "schema_version": "1.0",
        "decision_id": (
            "methodology-completion-review-dispatch-policy:"
            + hashlib.sha256(claim.review_run_id.encode("utf-8")).hexdigest()[:32]
        ),
        "evaluated_at": evaluated_at,
        "policy_id": ROUTING_POLICY_ID,
        "policy_version": ROUTING_POLICY_VERSION,
        "policy_sha256": ROUTING_POLICY_SHA256,
        "project_id": claim.project_id,
        "task_id": claim.task_id,
        "plan_id": claim.plan_id,
        "inventory_id": claim.inventory_id,
        "inventory_sha256": claim.inventory_sha256,
        "execution_contract_id": claim.execution_contract_id,
        "execution_contract_sha256": claim.execution_contract_sha256,
        "completion_review_claim_receipt_id": claim.receipt_id,
        "completion_review_claim_receipt_sha256": claim.content_sha256,
        "final_dispatch_receipt_id": claim.final_dispatch_receipt_id,
        "final_dispatch_receipt_sha256": claim.final_dispatch_receipt_sha256,
        "repository": claim.repository,
        "stage_sequence": claim.stage_sequence,
        "stage_key": claim.stage_key,
        "gate_key": claim.gate_key,
        "responsibility": claim.responsibility,
        "pinned_runtime": claim.runtime,
        "result_format": runtimes[claim.runtime].result_format.value,
        "review_run_id": claim.review_run_id,
        "context_pack_id": context_pack.pack_id,
        "context_pack_sha256": context_pack.content_sha256,
        "runtime_capabilities": sorted(
            RUNTIME_CAPABILITIES.get(claim.runtime, ())
        ),
        "token_reservation": claim.token_reservation,
        "cost_reservation_usd": claim.cost_reservation_usd,
        "checks": checks,
        "dispatchable": not blockers,
        "blockers": blockers,
        "route_selection_authority": False,
        "runtime_substitution_allowed": False,
        "provider_serviceability_verified": False,
    }
    return MethodologyCompletionReviewDispatchPolicyDecision.model_validate(
        seal_model_payload(
            MethodologyCompletionReviewDispatchPolicyDecision,
            payload,
        )
    )


def derive_methodology_completion_review_runtime_preflight(
    *,
    snapshot: MethodologyCompletionReviewDispatchSnapshot,
    dispatch_policy: MethodologyCompletionReviewDispatchPolicyDecision,
    observation: NativeRuntimeCapabilityObservation,
    runtimes: dict[str, RuntimeCommand],
) -> PinnedRuntimePreflightDecision:
    """Bind fresh native capability facts to the already selected reviewer."""

    route = _completion_review_route(snapshot)
    claim = snapshot.completion_review_claim_receipt
    if (
        not dispatch_policy.dispatchable
        or dispatch_policy.task_id != route.task_id
        or dispatch_policy.project_id != route.project_id
        or dispatch_policy.inventory_id != route.inventory_id
        or dispatch_policy.inventory_sha256 != route.inventory_sha256
        or dispatch_policy.stage_key != route.stage_key
        or dispatch_policy.gate_key != route.gate_key
        or dispatch_policy.responsibility != route.role
        or dispatch_policy.pinned_runtime != route.runtime
        or dispatch_policy.review_run_id != claim.review_run_id
    ):
        raise ValueError(
            "methodology completion-review policy differs from its pinned route"
        )
    return derive_pinned_runtime_preflight(
        observation=observation,
        runtimes=runtimes,
        route=route,
        routing_policy=dispatch_policy,
        run_id=claim.review_run_id,
    )


def build_methodology_completion_review_dispatch_claim(
    *,
    snapshot: MethodologyCompletionReviewDispatchSnapshot,
    context_pack: ContextPack,
    repository: RepositoryRevision | None,
    runtimes: dict[str, RuntimeCommand],
    dispatch_policy: MethodologyCompletionReviewDispatchPolicyDecision,
    runtime_preflight: PinnedRuntimePreflightDecision,
    prompt_sha256: str,
    spawn_owner_id: str,
    claimed_at: str,
) -> MethodologyCompletionReviewDispatchClaim:
    """Recheck every live completion-review binding before granting spawn."""

    claim = snapshot.completion_review_claim_receipt
    expected_context = build_methodology_completion_review_context(
        snapshot=snapshot,
        generated_at=context_pack.generated_at,
    )
    if expected_context != context_pack:
        raise ValueError("methodology completion-review Context changed")
    expected_policy = derive_methodology_completion_review_dispatch_policy(
        snapshot=snapshot,
        context_pack=context_pack,
        repository=repository,
        runtimes=runtimes,
        evaluated_at=dispatch_policy.evaluated_at,
    )
    if dispatch_policy != expected_policy:
        raise ValueError("methodology completion-review policy changed")
    if runtime_registry_sha256(runtimes) != runtime_preflight.runtime_registry_sha256:
        raise ValueError("methodology completion-review runtime registry changed")
    if (
        not runtime_preflight.allowed
        or runtime_preflight.task_id != claim.task_id
        or runtime_preflight.project_id != claim.project_id
        or runtime_preflight.run_id != claim.review_run_id
        or runtime_preflight.inventory_id != claim.inventory_id
        or runtime_preflight.inventory_sha256 != claim.inventory_sha256
        or runtime_preflight.stage_key != claim.stage_key
        or runtime_preflight.role != claim.responsibility
        or runtime_preflight.pinned_runtime != claim.runtime
        or runtime_preflight.routing_policy_decision_id
        != dispatch_policy.decision_id
        or runtime_preflight.routing_policy_decision_sha256
        != dispatch_policy.content_sha256
        or runtime_preflight.routing_policy_declaration_sha256
        != dispatch_policy.policy_sha256
    ):
        raise ValueError("methodology completion-review preflight changed")
    if repository is None or (
        repository.repository_id != claim.repository.repository_id
        or repository.ref != claim.repository.ref
        or repository.commit_sha != claim.repository.commit_sha
    ):
        raise ValueError("methodology completion-review repository changed")
    dispatch_id = (
        "methodology-completion-review-dispatch:"
        + hashlib.sha256(claim.review_run_id.encode("utf-8")).hexdigest()[:32]
    )
    claimed = datetime.fromisoformat(claimed_at)
    payload = {
        "schema_version": "1.0",
        "dispatch_id": dispatch_id,
        "claimed_at": claimed_at,
        "project_id": claim.project_id,
        "task_id": claim.task_id,
        "plan_id": claim.plan_id,
        "inventory_id": claim.inventory_id,
        "inventory_sha256": claim.inventory_sha256,
        "execution_contract_id": claim.execution_contract_id,
        "execution_contract_sha256": claim.execution_contract_sha256,
        "completion_review_claim_receipt_id": claim.receipt_id,
        "completion_review_claim_receipt_sha256": claim.content_sha256,
        "final_dispatch_receipt_id": claim.final_dispatch_receipt_id,
        "final_dispatch_receipt_sha256": claim.final_dispatch_receipt_sha256,
        "production_run_id": claim.production_run_id,
        "production_handoff_pack_id": claim.production_handoff_pack_id,
        "production_handoff_pack_sha256": claim.production_handoff_pack_sha256,
        "repository": claim.repository,
        "stage_sequence": claim.stage_sequence,
        "stage_key": claim.stage_key,
        "gate_key": claim.gate_key,
        "responsibility": claim.responsibility,
        "runtime": claim.runtime,
        "result_format": runtimes[claim.runtime].result_format.value,
        "review_run_id": claim.review_run_id,
        "context_pack_id": context_pack.pack_id,
        "context_pack_sha256": context_pack.content_sha256,
        "prompt_sha256": prompt_sha256,
        "spawn_owner_id": spawn_owner_id,
        "recovery_not_before": claimed + timedelta(
            seconds=COMPLETION_REVIEW_RECOVERY_LEASE_SECONDS
        ),
        "dispatch_policy": dispatch_policy,
        "runtime_preflight": runtime_preflight,
        "unbounded_native_usage_acknowledged": True,
        "formal_review_run_created": True,
        "review_context_pack_created": True,
        "compatibility_run_created": False,
        "process_started": False,
        "process_spawn_authority": True,
        "route_selection_authority": False,
        "runtime_substitution_allowed": False,
        "provider_serviceability_verified": False,
    }
    return MethodologyCompletionReviewDispatchClaim.model_validate(
        seal_model_payload(MethodologyCompletionReviewDispatchClaim, payload)
    )
