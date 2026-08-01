"""Validation and preflight for a claimed later methodology Run dispatch."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from agora.control_plane.models import (
    GateRecord,
    ProtocolRunRecord,
    StageRecord,
    StageRouteDecision,
    TaskRecord,
)
from agora.protocol.hashing import seal_model_payload
from agora.protocol.methodology_execution import MethodologyExecutionContract
from agora.protocol.methodology_migration import MethodologyMigrationPreviewRequest
from agora.protocol.methodology_stage_gate import MethodologyStageGateReceipt
from agora.protocol.methodology_stage_run_claim import (
    MethodologyStageRunClaimReceipt,
)
from agora.protocol.methodology_stage_run_dispatch import (
    MethodologyStageRunDispatchClaim,
    MethodologyStageRunDispatchPolicyCheck,
    MethodologyStageRunDispatchPolicyDecision,
)
from agora.protocol.models import (
    NativeRuntimeCapabilityObservation,
    PinnedRuntimePreflightDecision,
    StageInventory,
)
from agora.protocol.state_machines import GateStatus, StageStatus, TaskStatus
from agora.tasks.models import TaskManifest

from .models import OrchestrationPlan, PlanState
from .methodology_stage_predecessor import (
    MethodologyPredecessorDispatchReceipt,
)
from .protocol_context import RepositoryRevision
from .routing_policy import (
    ROUTING_POLICY_ID,
    ROUTING_POLICY_SHA256,
    ROUTING_POLICY_VERSION,
    RUNTIME_CAPABILITIES,
)
from .runtime import RuntimeCommand
from .runtime_capabilities import (
    runtime_command_sha256,
    runtime_registry_sha256,
)
from .runtime_preflight import derive_pinned_runtime_preflight


@dataclass(frozen=True)
class MethodologyStageRunDispatchSnapshot:
    task: TaskManifest
    control_task: TaskRecord
    plan: OrchestrationPlan
    inventory: StageInventory
    migration_request: MethodologyMigrationPreviewRequest
    execution_contract: MethodologyExecutionContract
    stage_gate_receipt: MethodologyStageGateReceipt
    stage_run_claim_receipt: MethodologyStageRunClaimReceipt
    predecessor_dispatch_receipt: MethodologyPredecessorDispatchReceipt
    protocol_run: ProtocolRunRecord
    route: StageRouteDecision
    formal_stage: StageRecord
    formal_gate: GateRecord


def derive_methodology_stage_run_dispatch_policy(
    *,
    snapshot: MethodologyStageRunDispatchSnapshot,
    repository: RepositoryRevision | None,
    runtimes: dict[str, RuntimeCommand],
    evaluated_at: datetime | str,
) -> MethodologyStageRunDispatchPolicyDecision:
    """Explain one later-Stage dispatch without route or runtime selection."""

    task = snapshot.task
    control_task = snapshot.control_task
    plan = snapshot.plan
    inventory = snapshot.inventory
    contract = snapshot.execution_contract
    migration_request = snapshot.migration_request
    gate_receipt = snapshot.stage_gate_receipt
    claim = snapshot.stage_run_claim_receipt
    predecessor = snapshot.predecessor_dispatch_receipt
    protocol_run = snapshot.protocol_run
    route = snapshot.route
    formal_stage = snapshot.formal_stage
    formal_gate = snapshot.formal_gate
    if (
        claim.stage_sequence not in {2, 3, 4, 5, 6}
        or len(contract.stages) < claim.stage_sequence
    ):
        raise ValueError(
            "This bounded dispatch may evaluate only methodology Stage "
            "sequences 2 through 6"
        )
    stage = contract.stages[claim.stage_sequence - 1]
    context = protocol_run.context_pack

    claimed_formal_run = bool(
        task.task_id == claim.task_id
        and task.project_id == claim.project_id
        and task.metadata.get("methodology_current_stage_run_claimed") is True
        and task.metadata.get("methodology_current_stage_sequence")
        == claim.stage_sequence
        and task.metadata.get("methodology_current_run_id") == claim.run_id
        and control_task.status == TaskStatus.ACTIVE
        and plan.plan_id == claim.plan_id
        and plan.state == PlanState.READY_FOR_IMPLEMENTATION
        and plan.version == claim.plan_version
        and inventory.inventory_id == claim.inventory_id
        and inventory.content_sha256 == claim.inventory_sha256
        and contract.contract_id == claim.execution_contract_id
        and contract.content_sha256 == claim.execution_contract_sha256
        and gate_receipt.receipt_id == claim.stage_gate_receipt_id
        and gate_receipt.content_sha256 == claim.stage_gate_receipt_sha256
        and predecessor.receipt_id == claim.predecessor_dispatch_receipt_id
        and predecessor.content_sha256
        == claim.predecessor_dispatch_receipt_sha256
        and protocol_run.run_id == claim.run_id
        and protocol_run.protocol_state is None
        and protocol_run.handoff_pack is None
        and protocol_run.settled_at is None
    )
    context_binding = bool(
        context.pack_id == claim.context_pack_id
        and context.content_sha256 == claim.context_pack_sha256
        and context.task_id == task.task_id
        and context.project_id == task.project_id
        and context.stage_key == stage.stage_key
        and context.run_id == claim.run_id
        and context.budget == claim.budget
    )
    current_route = bool(
        route.task_id == claim.task_id
        and route.project_id == claim.project_id
        and route.inventory_id == claim.inventory_id
        and route.inventory_sha256 == claim.inventory_sha256
        and route.inventory_sequence == claim.stage_sequence
        and route.stage_key == stage.stage_key
        and route.gate_key == stage.gate_key
        and route.role == stage.role
        and route.runtime == stage.runtime
        and route.stage_status == StageStatus.RUNNING
        and route.gate_status == GateStatus.PENDING
        and not route.runnable
        and formal_stage.status == StageStatus.RUNNING
        and formal_stage.stage_key == stage.stage_key
        and formal_stage.gate_key == stage.gate_key
        and formal_gate.status == GateStatus.PENDING
        and formal_gate.gate_key == stage.gate_key
        and formal_gate.stage_key == stage.stage_key
    )
    repository_binding = bool(
        repository is not None
        and repository.repository_id == contract.repository.repository_id
        and repository.ref == contract.repository.ref
        and repository.commit_sha == contract.repository.commit_sha
        and claim.repository == contract.repository
        and gate_receipt.repository == contract.repository
    )
    pins = {item.runtime: item for item in contract.runtime_pins}
    runtime_binding = bool(
        runtime_registry_sha256(runtimes)
        == migration_request.runtime_registry_sha256
        and set(pins) == set(runtimes)
        and stage.runtime in runtimes
        and all(
            pins[name].runtime_command_sha256
            == runtime_command_sha256(runtime)
            for name, runtime in runtimes.items()
        )
    )
    usage_reservation = bool(
        claim.budget.max_model_tokens is not None
        and claim.budget.max_model_tokens > 0
        and claim.budget == stage.context.budget
        and claim.budget == context.budget
    )
    checks = sorted(
        [
            MethodologyStageRunDispatchPolicyCheck(
                check="claimed_formal_run",
                satisfied=claimed_formal_run,
                detail=(
                    "The exact later formal Run retains its authenticated claim."
                    if claimed_formal_run
                    else "The later formal Run claim or lifecycle binding changed."
                ),
            ),
            MethodologyStageRunDispatchPolicyCheck(
                check="context_binding",
                satisfied=context_binding,
                detail=(
                    "The dispatch reuses the exact sealed later Context Pack."
                    if context_binding
                    else "The later Context Pack binding changed."
                ),
            ),
            MethodologyStageRunDispatchPolicyCheck(
                check="current_route",
                satisfied=current_route,
                detail=(
                    "The running later Stage and pending Gate retain the route."
                    if current_route
                    else "The authoritative later Stage route changed."
                ),
            ),
            MethodologyStageRunDispatchPolicyCheck(
                check="repository_binding",
                satisfied=repository_binding,
                detail=(
                    "The repository/ref/commit matches the execution contract."
                    if repository_binding
                    else "The methodology repository binding is stale."
                ),
            ),
            MethodologyStageRunDispatchPolicyCheck(
                check="runtime_binding",
                satisfied=runtime_binding,
                detail=(
                    "Every configured runtime command matches its pin."
                    if runtime_binding
                    else "The runtime registry or command pins changed."
                ),
            ),
            MethodologyStageRunDispatchPolicyCheck(
                check="usage_reservation",
                satisfied=usage_reservation,
                detail=(
                    "The later Run retains its bounded usage reservation."
                    if usage_reservation
                    else "The later Run usage reservation changed or is unbounded."
                ),
            ),
        ],
        key=lambda item: item.check,
    )
    blockers = [item.detail for item in checks if not item.satisfied]
    payload = {
        "schema_version": "1.0",
        "decision_id": (
            "methodology-stage-dispatch-policy:"
            + hashlib.sha256(claim.run_id.encode("utf-8")).hexdigest()[:32]
        ),
        "evaluated_at": evaluated_at,
        "policy_id": ROUTING_POLICY_ID,
        "policy_version": ROUTING_POLICY_VERSION,
        "policy_sha256": ROUTING_POLICY_SHA256,
        "project_id": task.project_id,
        "task_id": task.task_id,
        "plan_id": plan.plan_id,
        "inventory_id": inventory.inventory_id,
        "inventory_sha256": inventory.content_sha256,
        "execution_contract_id": contract.contract_id,
        "execution_contract_sha256": contract.content_sha256,
        "runtime_registry_sha256": (
            migration_request.runtime_registry_sha256
        ),
        "stage_gate_receipt_id": gate_receipt.receipt_id,
        "stage_gate_receipt_sha256": gate_receipt.content_sha256,
        "stage_run_claim_receipt_id": claim.receipt_id,
        "stage_run_claim_receipt_sha256": claim.content_sha256,
        "predecessor_dispatch_receipt_id": predecessor.receipt_id,
        "predecessor_dispatch_receipt_sha256": predecessor.content_sha256,
        "repository": contract.repository,
        "stage_sequence": stage.sequence,
        "stage_key": stage.stage_key,
        "gate_key": stage.gate_key,
        "role": stage.role,
        "pinned_runtime": stage.runtime,
        "result_format": runtimes[stage.runtime].result_format.value,
        "run_id": claim.run_id,
        "context_pack_id": context.pack_id,
        "context_pack_sha256": context.content_sha256,
        "runtime_capabilities": sorted(
            RUNTIME_CAPABILITIES.get(stage.runtime, ())
        ),
        "token_reservation": claim.budget.max_model_tokens or 0,
        "cost_reservation_usd": claim.budget.max_cost_usd,
        "checks": [item.model_dump(mode="json") for item in checks],
        "dispatchable": not blockers,
        "blockers": blockers,
        "route_selection_authority": False,
        "runtime_substitution_allowed": False,
        "provider_serviceability_verified": False,
    }
    return MethodologyStageRunDispatchPolicyDecision.model_validate(
        seal_model_payload(MethodologyStageRunDispatchPolicyDecision, payload)
    )


def derive_methodology_stage_runtime_preflight(
    *,
    snapshot: MethodologyStageRunDispatchSnapshot,
    dispatch_policy: MethodologyStageRunDispatchPolicyDecision,
    observation: NativeRuntimeCapabilityObservation,
    runtimes: dict[str, RuntimeCommand],
) -> PinnedRuntimePreflightDecision:
    """Bind native preflight to the exact later-Stage policy decision."""

    route = snapshot.route
    if (
        not dispatch_policy.dispatchable
        or dispatch_policy.task_id != route.task_id
        or dispatch_policy.project_id != route.project_id
        or dispatch_policy.inventory_id != route.inventory_id
        or dispatch_policy.inventory_sha256 != route.inventory_sha256
        or dispatch_policy.stage_key != route.stage_key
        or dispatch_policy.gate_key != route.gate_key
        or dispatch_policy.role != route.role
        or dispatch_policy.pinned_runtime != route.runtime
        or dispatch_policy.run_id != snapshot.stage_run_claim_receipt.run_id
    ):
        raise ValueError(
            "Methodology Stage dispatch policy differs from the route"
        )
    return derive_pinned_runtime_preflight(
        observation=observation,
        runtimes=runtimes,
        route=route,
        routing_policy=dispatch_policy,
        run_id=dispatch_policy.run_id,
    )


def build_methodology_stage_run_dispatch_claim(
    *,
    snapshot: MethodologyStageRunDispatchSnapshot,
    repository: RepositoryRevision | None,
    runtimes: dict[str, RuntimeCommand],
    dispatch_policy: MethodologyStageRunDispatchPolicyDecision,
    runtime_preflight: PinnedRuntimePreflightDecision,
    prompt_sha256: str,
    spawn_owner_id: str,
    claimed_at: str,
) -> MethodologyStageRunDispatchClaim:
    """Recheck every live later-Stage binding before one spawn attempt."""

    task = snapshot.task
    control_task = snapshot.control_task
    plan = snapshot.plan
    inventory = snapshot.inventory
    contract = snapshot.execution_contract
    migration_request = snapshot.migration_request
    gate_receipt = snapshot.stage_gate_receipt
    claim = snapshot.stage_run_claim_receipt
    predecessor = snapshot.predecessor_dispatch_receipt
    protocol_run = snapshot.protocol_run
    route = snapshot.route
    formal_stage = snapshot.formal_stage
    formal_gate = snapshot.formal_gate
    if (
        claim.stage_sequence not in {2, 3, 4, 5, 6}
        or len(contract.stages) < claim.stage_sequence
    ):
        raise ValueError(
            "This bounded dispatch may execute only methodology Stage "
            "sequences 2 through 6"
        )
    stage = contract.stages[claim.stage_sequence - 1]
    expected_policy = derive_methodology_stage_run_dispatch_policy(
        snapshot=snapshot,
        repository=repository,
        runtimes=runtimes,
        evaluated_at=dispatch_policy.evaluated_at,
    )
    if dispatch_policy != expected_policy:
        raise ValueError(
            "Methodology Stage dispatch policy binding is stale or differs"
        )
    if (
        task.task_id != claim.task_id
        or task.project_id != claim.project_id
        or task.metadata.get("methodology_current_stage_run_claimed") is not True
        or task.metadata.get("methodology_current_stage_sequence")
        != claim.stage_sequence
        or task.metadata.get("methodology_current_run_id") != claim.run_id
        or control_task.task_id != task.task_id
        or control_task.project_id != task.project_id
        or control_task.status != TaskStatus.ACTIVE
        or plan.plan_id != claim.plan_id
        or plan.state != PlanState.READY_FOR_IMPLEMENTATION
        or plan.version != claim.plan_version
        or inventory.inventory_id != claim.inventory_id
        or inventory.content_sha256 != claim.inventory_sha256
        or inventory.task_id != task.task_id
        or inventory.project_id != task.project_id
        or inventory.plan_id != plan.plan_id
    ):
        raise ValueError(
            "Methodology Stage dispatch lifecycle or inventory binding drifted"
        )
    if (
        contract.contract_id != claim.execution_contract_id
        or contract.content_sha256 != claim.execution_contract_sha256
        or contract.task_id != task.task_id
        or contract.project_id != task.project_id
        or contract.plan_id != plan.plan_id
        or contract.inventory_id != inventory.inventory_id
        or contract.inventory_sha256 != inventory.content_sha256
        or contract.route_activated
        or contract.runtime_spawned
        or contract.routing_authority
        or contract.dispatch_authority
    ):
        raise ValueError(
            "Methodology Stage dispatch execution contract drifted"
        )
    if (
        gate_receipt.receipt_id != claim.stage_gate_receipt_id
        or gate_receipt.content_sha256 != claim.stage_gate_receipt_sha256
        or gate_receipt.stage_sequence != claim.stage_sequence
        or gate_receipt.stage_key != claim.stage_key
        or gate_receipt.gate_key != claim.gate_key
        or predecessor.receipt_id != claim.predecessor_dispatch_receipt_id
        or predecessor.content_sha256
        != claim.predecessor_dispatch_receipt_sha256
        or predecessor.stage_status != StageStatus.COMPLETED
        or predecessor.gate_status != GateStatus.PASSED
        or predecessor.next_stage_key != claim.stage_key
    ):
        raise ValueError(
            "Methodology Stage dispatch predecessor or Gate chain drifted"
        )
    context = protocol_run.context_pack
    if (
        protocol_run.run_id != claim.run_id
        or protocol_run.task_id != task.task_id
        or protocol_run.project_id != task.project_id
        or protocol_run.stage_key != stage.stage_key
        or protocol_run.gate_key != stage.gate_key
        or context.pack_id != claim.context_pack_id
        or context.content_sha256 != claim.context_pack_sha256
        or protocol_run.protocol_state is not None
        or protocol_run.handoff_pack is not None
        or protocol_run.settled_at is not None
        or context.run_id != claim.run_id
        or context.stage_key != stage.stage_key
        or context.budget != claim.budget
    ):
        raise ValueError("Methodology Stage dispatch formal Run drifted")
    if (
        route.task_id != task.task_id
        or route.project_id != task.project_id
        or route.inventory_id != inventory.inventory_id
        or route.inventory_sha256 != inventory.content_sha256
        or route.inventory_sequence != stage.sequence
        or route.stage_key != stage.stage_key
        or route.gate_key != stage.gate_key
        or route.role != stage.role
        or route.runtime != stage.runtime
        or route.stage_status != StageStatus.RUNNING
        or route.gate_status != GateStatus.PENDING
        or route.runnable
        or formal_stage.stage_key != stage.stage_key
        or formal_stage.gate_key != stage.gate_key
        or formal_stage.status != StageStatus.RUNNING
        or formal_gate.stage_key != stage.stage_key
        or formal_gate.gate_key != stage.gate_key
        or formal_gate.status != GateStatus.PENDING
    ):
        raise ValueError("Methodology Stage dispatch route drifted")
    expected_requirements = sorted(
        [item.requirement for item in stage.gate.evidence_contracts],
        key=lambda item: item.requirement_id,
    )
    if (
        formal_gate.requirements != expected_requirements
        or gate_receipt.requirements != expected_requirements
    ):
        raise ValueError("Methodology Stage dispatch Gate requirements drifted")
    if repository is None or (
        repository.repository_id != contract.repository.repository_id
        or repository.ref != contract.repository.ref
        or repository.commit_sha != contract.repository.commit_sha
        or claim.repository != contract.repository
        or gate_receipt.repository != contract.repository
    ):
        raise ValueError("Methodology Stage dispatch repository is stale")
    pins = {item.runtime: item for item in contract.runtime_pins}
    if (
        runtime_registry_sha256(runtimes)
        != migration_request.runtime_registry_sha256
        or runtime_preflight.runtime_registry_sha256
        != migration_request.runtime_registry_sha256
        or set(pins) != set(runtimes)
    ):
        raise ValueError("Methodology Stage dispatch runtime registry drifted")
    for name, runtime in runtimes.items():
        if pins[name].runtime_command_sha256 != runtime_command_sha256(runtime):
            raise ValueError("Methodology Stage dispatch runtime command drifted")
    if (
        not runtime_preflight.allowed
        or runtime_preflight.task_id != task.task_id
        or runtime_preflight.project_id != task.project_id
        or runtime_preflight.run_id != claim.run_id
        or runtime_preflight.inventory_id != inventory.inventory_id
        or runtime_preflight.inventory_sha256 != inventory.content_sha256
        or runtime_preflight.stage_key != stage.stage_key
        or runtime_preflight.role != stage.role
        or runtime_preflight.pinned_runtime != stage.runtime
        or runtime_preflight.routing_policy_decision_id
        != dispatch_policy.decision_id
        or runtime_preflight.routing_policy_decision_sha256
        != dispatch_policy.content_sha256
        or runtime_preflight.routing_policy_declaration_sha256
        != dispatch_policy.policy_sha256
    ):
        raise ValueError("Methodology Stage dispatch preflight differs")

    payload = {
        "schema_version": "1.0",
        "dispatch_id": (
            "methodology-stage-dispatch:"
            + hashlib.sha256(claim.run_id.encode("utf-8")).hexdigest()[:32]
        ),
        "claimed_at": claimed_at,
        "project_id": task.project_id,
        "task_id": task.task_id,
        "plan_id": plan.plan_id,
        "inventory_id": inventory.inventory_id,
        "inventory_sha256": inventory.content_sha256,
        "execution_contract_id": contract.contract_id,
        "execution_contract_sha256": contract.content_sha256,
        "runtime_registry_sha256": (
            migration_request.runtime_registry_sha256
        ),
        "stage_gate_receipt_id": gate_receipt.receipt_id,
        "stage_gate_receipt_sha256": gate_receipt.content_sha256,
        "stage_run_claim_receipt_id": claim.receipt_id,
        "stage_run_claim_receipt_sha256": claim.content_sha256,
        "predecessor_dispatch_receipt_id": predecessor.receipt_id,
        "predecessor_dispatch_receipt_sha256": predecessor.content_sha256,
        "repository": contract.repository,
        "stage_sequence": stage.sequence,
        "stage_key": stage.stage_key,
        "gate_key": stage.gate_key,
        "role": stage.role,
        "runtime": stage.runtime,
        "result_format": runtimes[stage.runtime].result_format.value,
        "run_id": claim.run_id,
        "context_pack_id": claim.context_pack_id,
        "context_pack_sha256": claim.context_pack_sha256,
        "prompt_sha256": prompt_sha256,
        "spawn_owner_id": spawn_owner_id,
        "recovery_not_before": (
            datetime.fromisoformat(claimed_at) + timedelta(minutes=5)
        ),
        "dispatch_policy": dispatch_policy,
        "runtime_preflight": runtime_preflight,
        "unbounded_native_usage_acknowledged": True,
        "existing_formal_run_reused": True,
        "existing_context_pack_reused": True,
        "compatibility_run_created": False,
        "process_started": False,
        "process_spawn_authority": True,
        "route_selection_authority": False,
        "runtime_substitution_allowed": False,
        "provider_serviceability_verified": False,
    }
    return MethodologyStageRunDispatchClaim.model_validate(
        seal_model_payload(MethodologyStageRunDispatchClaim, payload)
    )
