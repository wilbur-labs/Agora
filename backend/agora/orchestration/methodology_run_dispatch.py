"""Validation and preflight for one already claimed methodology Run dispatch."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from agora.control_plane.models import (
    GateRecord,
    ProtocolRunRecord,
    StageRecord,
    StageRouteDecision,
    TaskRecord,
)
from agora.protocol.hashing import seal_model_payload
from agora.protocol.methodology_execution import MethodologyExecutionContract
from agora.protocol.methodology_route_activation import (
    MethodologyRouteActivationReceipt,
)
from agora.protocol.methodology_run_claim import MethodologyRunClaimReceipt
from agora.protocol.methodology_run_dispatch import (
    MethodologyRunDispatchClaim,
    MethodologyRunDispatchPolicyCheck,
    MethodologyRunDispatchPolicyDecision,
)
from agora.protocol.models import (
    NativeRuntimeCapabilityObservation,
    PinnedRuntimePreflightDecision,
    StageInventory,
)
from agora.protocol.state_machines import GateStatus, StageStatus, TaskStatus
from agora.tasks.models import TaskManifest

from .models import OrchestrationPlan, PlanState
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
class MethodologyRunDispatchSnapshot:
    task: TaskManifest
    control_task: TaskRecord
    plan: OrchestrationPlan
    inventory: StageInventory
    execution_contract: MethodologyExecutionContract
    route_activation_receipt: MethodologyRouteActivationReceipt
    run_claim_receipt: MethodologyRunClaimReceipt
    protocol_run: ProtocolRunRecord
    route: StageRouteDecision
    formal_stage: StageRecord
    formal_gate: GateRecord


def derive_methodology_run_dispatch_policy(
    *,
    snapshot: MethodologyRunDispatchSnapshot,
    repository: RepositoryRevision | None,
    runtimes: dict[str, RuntimeCommand],
    evaluated_at: datetime | str,
) -> MethodologyRunDispatchPolicyDecision:
    """Explain one existing-Run dispatch without selecting or substituting."""

    task = snapshot.task
    control_task = snapshot.control_task
    plan = snapshot.plan
    inventory = snapshot.inventory
    contract = snapshot.execution_contract
    first_stage = contract.stages[0]
    activation = snapshot.route_activation_receipt
    claim = snapshot.run_claim_receipt
    protocol_run = snapshot.protocol_run
    route = snapshot.route
    formal_stage = snapshot.formal_stage
    formal_gate = snapshot.formal_gate
    context = protocol_run.context_pack

    claimed_formal_run = bool(
        task.task_id == claim.task_id
        and task.project_id == claim.project_id
        and task.metadata.get("methodology_run_claimed") is True
        and task.metadata.get("methodology_run_id") == claim.run_id
        and task.metadata.get("methodology_dispatch_authority") is False
        and control_task.status == TaskStatus.ACTIVE
        and plan.plan_id == claim.plan_id
        and plan.state == PlanState.READY_FOR_IMPLEMENTATION
        and plan.version == claim.plan_version
        and inventory.inventory_id == claim.inventory_id
        and inventory.content_sha256 == claim.inventory_sha256
        and contract.contract_id == claim.execution_contract_id
        and contract.content_sha256 == claim.execution_contract_sha256
        and activation.receipt_id == claim.route_activation_receipt_id
        and activation.content_sha256 == claim.route_activation_receipt_sha256
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
        and context.stage_key == first_stage.stage_key
        and context.run_id == claim.run_id
        and context.budget == claim.budget
    )
    current_route = bool(
        route.task_id == claim.task_id
        and route.project_id == claim.project_id
        and route.inventory_id == claim.inventory_id
        and route.inventory_sha256 == claim.inventory_sha256
        and route.stage_key == first_stage.stage_key
        and route.gate_key == first_stage.gate_key
        and route.role == first_stage.role
        and route.runtime == first_stage.runtime
        and route.stage_status == StageStatus.RUNNING
        and route.gate_status == GateStatus.PENDING
        and not route.runnable
        and formal_stage.status == StageStatus.RUNNING
        and formal_stage.stage_key == first_stage.stage_key
        and formal_stage.gate_key == first_stage.gate_key
        and formal_gate.status == GateStatus.PENDING
        and formal_gate.gate_key == first_stage.gate_key
        and formal_gate.stage_key == first_stage.stage_key
    )
    repository_binding = bool(
        repository is not None
        and repository.repository_id == contract.repository.repository_id
        and repository.ref == contract.repository.ref
        and repository.commit_sha == contract.repository.commit_sha
        and claim.repository == contract.repository
        and activation.repository == contract.repository
    )
    pin_by_runtime = {item.runtime: item for item in contract.runtime_pins}
    runtime_binding = bool(
        set(pin_by_runtime) == set(runtimes)
        and first_stage.runtime in runtimes
        and all(
            pin_by_runtime[name].runtime_command_sha256
            == runtime_command_sha256(runtime)
            for name, runtime in runtimes.items()
        )
    )
    usage_reservation = bool(
        claim.budget.max_model_tokens is not None
        and claim.budget.max_model_tokens > 0
        and claim.budget == first_stage.context.budget
        and claim.budget == context.budget
    )
    checks = sorted(
        [
            MethodologyRunDispatchPolicyCheck(
                check="claimed_formal_run",
                satisfied=claimed_formal_run,
                detail=(
                    "The exact unsettled formal Run retains its authenticated claim."
                    if claimed_formal_run
                    else "The formal Run claim or lifecycle binding changed."
                ),
            ),
            MethodologyRunDispatchPolicyCheck(
                check="context_binding",
                satisfied=context_binding,
                detail=(
                    "The dispatch reuses the exact sealed first Context Pack."
                    if context_binding
                    else "The claimed Context Pack binding changed."
                ),
            ),
            MethodologyRunDispatchPolicyCheck(
                check="current_route",
                satisfied=current_route,
                detail=(
                    "The running first Stage and pending Gate retain the sealed route."
                    if current_route
                    else "The authoritative first Stage route changed."
                ),
            ),
            MethodologyRunDispatchPolicyCheck(
                check="repository_binding",
                satisfied=repository_binding,
                detail=(
                    "The clean repository/ref/commit matches the execution contract."
                    if repository_binding
                    else "The methodology repository binding is unavailable or stale."
                ),
            ),
            MethodologyRunDispatchPolicyCheck(
                check="runtime_binding",
                satisfied=runtime_binding,
                detail=(
                    "Every configured runtime command matches its immutable pin."
                    if runtime_binding
                    else "The configured runtime registry or command pins changed."
                ),
            ),
            MethodologyRunDispatchPolicyCheck(
                check="usage_reservation",
                satisfied=usage_reservation,
                detail=(
                    "The claimed Run retains its bounded Token and cost reservation."
                    if usage_reservation
                    else "The claimed Run usage reservation changed or is unbounded."
                ),
            ),
        ],
        key=lambda item: item.check,
    )
    blockers = [item.detail for item in checks if not item.satisfied]
    payload = {
        "schema_version": "1.0",
        "decision_id": (
            "methodology-dispatch-policy:"
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
        "route_activation_receipt_id": activation.receipt_id,
        "route_activation_receipt_sha256": activation.content_sha256,
        "run_claim_receipt_id": claim.receipt_id,
        "run_claim_receipt_sha256": claim.content_sha256,
        "repository": contract.repository,
        "stage_key": first_stage.stage_key,
        "gate_key": first_stage.gate_key,
        "role": first_stage.role,
        "pinned_runtime": first_stage.runtime,
        "run_id": claim.run_id,
        "context_pack_id": context.pack_id,
        "context_pack_sha256": context.content_sha256,
        "runtime_capabilities": sorted(
            RUNTIME_CAPABILITIES.get(first_stage.runtime, ())
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
    return MethodologyRunDispatchPolicyDecision.model_validate(
        seal_model_payload(MethodologyRunDispatchPolicyDecision, payload)
    )


def derive_methodology_runtime_preflight(
    *,
    snapshot: MethodologyRunDispatchSnapshot,
    dispatch_policy: MethodologyRunDispatchPolicyDecision,
    observation: NativeRuntimeCapabilityObservation,
    runtimes: dict[str, RuntimeCommand],
) -> PinnedRuntimePreflightDecision:
    """Bind native preflight to the exact per-Run methodology policy decision."""

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
        or dispatch_policy.run_id != snapshot.run_claim_receipt.run_id
    ):
        raise ValueError(
            "Methodology dispatch policy differs from the authoritative route"
        )
    return derive_pinned_runtime_preflight(
        observation=observation,
        runtimes=runtimes,
        route=route,
        routing_policy=dispatch_policy,
        run_id=dispatch_policy.run_id,
    )


def build_methodology_run_dispatch_claim(
    *,
    snapshot: MethodologyRunDispatchSnapshot,
    repository: RepositoryRevision | None,
    runtimes: dict[str, RuntimeCommand],
    dispatch_policy: MethodologyRunDispatchPolicyDecision,
    runtime_preflight: PinnedRuntimePreflightDecision,
    prompt_sha256: str,
    claimed_at: str,
) -> MethodologyRunDispatchClaim:
    """Fail closed over every live binding before granting one spawn attempt."""

    task = snapshot.task
    control_task = snapshot.control_task
    plan = snapshot.plan
    inventory = snapshot.inventory
    contract = snapshot.execution_contract
    activation = snapshot.route_activation_receipt
    claim = snapshot.run_claim_receipt
    protocol_run = snapshot.protocol_run
    route = snapshot.route
    formal_stage = snapshot.formal_stage
    formal_gate = snapshot.formal_gate
    first_stage = contract.stages[0]
    context_pack = protocol_run.context_pack
    expected_policy = derive_methodology_run_dispatch_policy(
        snapshot=snapshot,
        repository=repository,
        runtimes=runtimes,
        evaluated_at=dispatch_policy.evaluated_at,
    )
    if dispatch_policy != expected_policy:
        raise ValueError("Methodology dispatch policy binding is stale or differs")

    if (
        task.task_id != claim.task_id
        or task.project_id != claim.project_id
        or task.metadata.get("methodology_run_claimed") is not True
        or task.metadata.get("methodology_run_id") != claim.run_id
        or task.metadata.get("methodology_dispatch_authority") is not False
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
            "Methodology dispatch Task, lifecycle, Plan, or inventory binding drifted"
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
        raise ValueError("Methodology dispatch execution contract binding drifted")
    if (
        activation.receipt_id != claim.route_activation_receipt_id
        or activation.content_sha256 != claim.route_activation_receipt_sha256
        or activation.task_id != task.task_id
        or activation.execution_contract_id != contract.contract_id
        or activation.execution_contract_sha256 != contract.content_sha256
        or activation.first_stage_key != first_stage.stage_key
        or activation.first_gate_key != first_stage.gate_key
        or not activation.route_activated
        or activation.dispatch_authority
        or activation.runtime_spawned
    ):
        raise ValueError("Methodology dispatch route-activation binding drifted")
    if (
        protocol_run.run_id != claim.run_id
        or protocol_run.task_id != task.task_id
        or protocol_run.project_id != task.project_id
        or protocol_run.stage_key != first_stage.stage_key
        or protocol_run.gate_key != first_stage.gate_key
        or protocol_run.context_pack.pack_id != claim.context_pack_id
        or protocol_run.context_pack.content_sha256
        != claim.context_pack_sha256
        or protocol_run.protocol_state is not None
        or protocol_run.handoff_pack is not None
        or protocol_run.settled_at is not None
        or context_pack.run_id != claim.run_id
        or context_pack.stage_key != first_stage.stage_key
    ):
        raise ValueError("Methodology dispatch formal Run binding drifted")
    if (
        route.task_id != task.task_id
        or route.project_id != task.project_id
        or route.inventory_id != inventory.inventory_id
        or route.inventory_sha256 != inventory.content_sha256
        or route.stage_key != first_stage.stage_key
        or route.gate_key != first_stage.gate_key
        or route.role != first_stage.role
        or route.runtime != first_stage.runtime
        or route.stage_status != StageStatus.RUNNING
        or route.gate_status != GateStatus.PENDING
        or route.runnable
        or formal_stage.stage_key != first_stage.stage_key
        or formal_stage.gate_key != first_stage.gate_key
        or formal_stage.status != StageStatus.RUNNING
        or formal_gate.gate_key != first_stage.gate_key
        or formal_gate.stage_key != first_stage.stage_key
        or formal_gate.status != GateStatus.PENDING
    ):
        raise ValueError("Methodology dispatch authoritative route drifted")
    expected_requirements = sorted(
        [item.requirement for item in first_stage.gate.evidence_contracts],
        key=lambda item: item.requirement_id,
    )
    if formal_gate.requirements != expected_requirements:
        raise ValueError("Methodology dispatch first Gate requirements drifted")
    if repository is None or (
        repository.repository_id != contract.repository.repository_id
        or repository.ref != contract.repository.ref
        or repository.commit_sha != contract.repository.commit_sha
        or claim.repository != contract.repository
        or activation.repository != contract.repository
    ):
        raise ValueError("Methodology dispatch repository binding is stale")
    if runtime_registry_sha256(runtimes) != runtime_preflight.runtime_registry_sha256:
        raise ValueError("Methodology dispatch runtime registry binding is stale")
    pin_by_runtime = {item.runtime: item for item in contract.runtime_pins}
    if set(pin_by_runtime) != set(runtimes):
        raise ValueError("Methodology dispatch runtime registry set drifted")
    for runtime_name, runtime in runtimes.items():
        pin = pin_by_runtime.get(runtime_name)
        if (
            pin is None
            or pin.runtime_command_sha256 != runtime_command_sha256(runtime)
        ):
            raise ValueError("Methodology dispatch runtime command binding is stale")
    if (
        not runtime_preflight.allowed
        or runtime_preflight.task_id != task.task_id
        or runtime_preflight.project_id != task.project_id
        or runtime_preflight.run_id != claim.run_id
        or runtime_preflight.inventory_id != inventory.inventory_id
        or runtime_preflight.inventory_sha256 != inventory.content_sha256
        or runtime_preflight.stage_key != first_stage.stage_key
        or runtime_preflight.role != first_stage.role
        or runtime_preflight.pinned_runtime != first_stage.runtime
        or runtime_preflight.routing_policy_decision_id
        != dispatch_policy.decision_id
        or runtime_preflight.routing_policy_decision_sha256
        != dispatch_policy.content_sha256
        or runtime_preflight.routing_policy_declaration_sha256
        != dispatch_policy.policy_sha256
    ):
        raise ValueError("Methodology dispatch runtime preflight binding differs")

    dispatch_id = (
        "methodology-dispatch:"
        + hashlib.sha256(claim.run_id.encode("utf-8")).hexdigest()[:32]
    )
    payload = {
        "schema_version": "1.0",
        "dispatch_id": dispatch_id,
        "claimed_at": claimed_at,
        "project_id": task.project_id,
        "task_id": task.task_id,
        "plan_id": plan.plan_id,
        "inventory_id": inventory.inventory_id,
        "inventory_sha256": inventory.content_sha256,
        "execution_contract_id": contract.contract_id,
        "execution_contract_sha256": contract.content_sha256,
        "route_activation_receipt_id": activation.receipt_id,
        "route_activation_receipt_sha256": activation.content_sha256,
        "run_claim_receipt_id": claim.receipt_id,
        "run_claim_receipt_sha256": claim.content_sha256,
        "repository": contract.repository,
        "first_stage_key": first_stage.stage_key,
        "first_gate_key": first_stage.gate_key,
        "role": first_stage.role,
        "runtime": first_stage.runtime,
        "run_id": claim.run_id,
        "context_pack_id": claim.context_pack_id,
        "context_pack_sha256": claim.context_pack_sha256,
        "prompt_sha256": prompt_sha256,
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
    return MethodologyRunDispatchClaim.model_validate(
        seal_model_payload(MethodologyRunDispatchClaim, payload)
    )
