"""Validation for the next contract-bound methodology Stage Gate."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agora.control_plane.auth import ControlPrincipal
from agora.control_plane.models import (
    GateRecord,
    ProtocolRunRecord,
    StageRecord,
    StageRouteDecision,
    TaskRecord,
)
from agora.protocol.methodology_execution import MethodologyExecutionContract
from agora.protocol.methodology_migration import (
    AuthenticatedMethodologyMigrationGate,
    MethodologyMigrationActivationReceipt,
    MethodologyMigrationPreviewRequest,
)
from agora.protocol.methodology_stage_gate import MethodologyStageGateRequest
from agora.protocol.models import GateRequirement, StageInventory
from agora.protocol.state_machines import GateStatus, StageStatus, TaskStatus
from agora.tasks.models import TaskManifest

from .models import OrchestrationPlan, PlanState
from .methodology_stage_predecessor import (
    MethodologyPredecessorDispatchReceipt,
    methodology_dispatch_gate_key,
    methodology_dispatch_sequence,
    methodology_dispatch_stage_key,
)
from .protocol_context import RepositoryRevision
from .runtime import RuntimeCommand
from .runtime_capabilities import (
    runtime_command_sha256,
    runtime_registry_sha256,
)


STAGE_GATE_REQUEST_LIMIT = 1_000_000


def load_methodology_stage_gate_request(
    path: Path,
) -> MethodologyStageGateRequest:
    """Load one strict bounded next-Stage Gate request."""

    try:
        if not path.is_file():
            raise ValueError(
                "Methodology Stage Gate request must be a file"
            )
        if path.stat().st_size > STAGE_GATE_REQUEST_LIMIT:
            raise ValueError(
                "Methodology Stage Gate request exceeds 1 MiB"
            )
        with path.open("rb") as handle:
            payload = handle.read(STAGE_GATE_REQUEST_LIMIT + 1)
        if len(payload) > STAGE_GATE_REQUEST_LIMIT:
            raise ValueError(
                "Methodology Stage Gate request exceeds 1 MiB"
            )
    except OSError as exc:
        raise ValueError(
            "Methodology Stage Gate request is unavailable"
        ) from exc
    return MethodologyStageGateRequest.model_validate_json(payload)


@dataclass(frozen=True)
class MethodologyStageGateSnapshot:
    task: TaskManifest
    control_task: TaskRecord
    plan: OrchestrationPlan
    inventory: StageInventory
    migration_request: MethodologyMigrationPreviewRequest
    migration_gate: AuthenticatedMethodologyMigrationGate
    migration_receipt: MethodologyMigrationActivationReceipt
    execution_contract: MethodologyExecutionContract
    authenticated_principal_id: str
    predecessor_dispatch_receipt: MethodologyPredecessorDispatchReceipt
    predecessor_protocol_run: ProtocolRunRecord
    predecessor_stage: StageRecord
    predecessor_gate: GateRecord
    route: StageRouteDecision
    formal_stage: StageRecord
    formal_gate: GateRecord | None


def validate_methodology_stage_gate(
    *,
    snapshot: MethodologyStageGateSnapshot,
    request: MethodologyStageGateRequest,
    principal: ControlPrincipal,
    repository: RepositoryRevision | None,
    observed_artifact_sha256s: dict[str, str | None],
    runtimes: dict[str, RuntimeCommand],
) -> tuple[GateRequirement, ...]:
    """Recheck the settled predecessor and return the exact next Gate."""

    task = snapshot.task
    control_task = snapshot.control_task
    plan = snapshot.plan
    inventory = snapshot.inventory
    migration_request = snapshot.migration_request
    migration_gate = snapshot.migration_gate
    migration_receipt = snapshot.migration_receipt
    contract = snapshot.execution_contract
    dispatch = snapshot.predecessor_dispatch_receipt
    protocol_run = snapshot.predecessor_protocol_run
    predecessor_stage = snapshot.predecessor_stage
    predecessor_gate = snapshot.predecessor_gate
    route = snapshot.route
    formal_stage = snapshot.formal_stage

    if "control_plane.approve" not in principal.permissions:
        raise ValueError(
            "Authenticated principal lacks control_plane.approve permission"
        )
    if task.project_id not in principal.projects:
        raise ValueError(
            "Authenticated principal is not authorized for the successor project"
        )
    if (
        principal.principal_id != contract.authenticated_principal_id
        or principal.principal_id != migration_gate.authenticated_principal_id
        or principal.principal_id != snapshot.authenticated_principal_id
    ):
        raise ValueError(
            "Stage Gate principal does not match the authenticated migration chain"
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
        or request.predecessor_dispatch_receipt_id != dispatch.receipt_id
        or request.predecessor_dispatch_receipt_sha256
        != dispatch.content_sha256
    ):
        raise ValueError(
            "Methodology Stage Gate request binding is stale or differs"
        )
    if (
        contract.project_id != task.project_id
        or contract.task_id != task.task_id
        or contract.plan_id != plan.plan_id
        or contract.plan_version != plan.version
        or contract.inventory_id != inventory.inventory_id
        or contract.inventory_sha256 != inventory.content_sha256
        or plan.state != PlanState.READY_FOR_IMPLEMENTATION
    ):
        raise ValueError(
            "Methodology execution contract live binding is stale or differs"
        )
    if (
        migration_receipt.receipt_id != contract.migration_receipt_id
        or migration_receipt.content_sha256
        != contract.migration_receipt_sha256
        or migration_receipt.request_id != migration_request.request_id
        or migration_receipt.request_sha256
        != migration_request.content_sha256
        or migration_receipt.authenticated_gate_id != migration_gate.gate_id
        or migration_receipt.authenticated_gate_sha256
        != migration_gate.content_sha256
    ):
        raise ValueError(
            "Methodology Stage Gate migration provenance differs"
        )
    predecessor_sequence = methodology_dispatch_sequence(dispatch)
    predecessor_stage_key = methodology_dispatch_stage_key(dispatch)
    predecessor_gate_key = methodology_dispatch_gate_key(dispatch)
    predecessor_metadata_bound = (
        task.metadata.get("methodology_run_id")
        == dispatch.dispatch_claim.run_id
        if predecessor_sequence == 1
        else task.metadata.get("methodology_current_stage_run_claimed") is True
        and task.metadata.get("methodology_current_stage_sequence")
        == predecessor_sequence
        and task.metadata.get("methodology_current_stage_key")
        == predecessor_stage_key
        and task.metadata.get("methodology_current_gate_key")
        == predecessor_gate_key
        and task.metadata.get("methodology_current_run_id")
        == dispatch.dispatch_claim.run_id
    )
    if (
        task.metadata.get("methodology_route_activated") is not True
        or task.metadata.get("methodology_run_claimed") is not True
        or not predecessor_metadata_bound
        or task.metadata.get("methodology_dispatch_authority") is not False
        or contract.route_activated
        or contract.runtime_spawned
        or contract.routing_authority
        or contract.dispatch_authority
    ):
        raise ValueError(
            "Methodology successor does not retain the settled predecessor chain"
        )

    if (
        request.stage_sequence not in {2, 3}
        or len(contract.stages) < request.stage_sequence
        or predecessor_sequence != request.stage_sequence - 1
    ):
        raise ValueError(
            "This bounded increment may configure only successor Stage "
            "sequences 2 or 3 from the immediately preceding dispatch"
        )
    predecessor_contract = contract.stages[request.stage_sequence - 2]
    stage_contract = contract.stages[request.stage_sequence - 1]
    handoff = protocol_run.handoff_pack
    if (
        dispatch.dispatch_claim.task_id != task.task_id
        or dispatch.dispatch_claim.execution_contract_id
        != contract.contract_id
        or dispatch.dispatch_claim.execution_contract_sha256
        != contract.content_sha256
        or predecessor_stage_key != predecessor_contract.stage_key
        or predecessor_gate_key != predecessor_contract.gate_key
        or dispatch.next_stage_key != stage_contract.stage_key
        or dispatch.stage_status != StageStatus.COMPLETED
        or dispatch.gate_status != GateStatus.PASSED
        or request.predecessor_run_id != dispatch.dispatch_claim.run_id
        or request.predecessor_stage_key != predecessor_stage_key
        or request.predecessor_gate_key != predecessor_gate_key
        or request.predecessor_handoff_pack_id != dispatch.handoff_pack_id
        or request.predecessor_handoff_pack_sha256
        != dispatch.handoff_pack_sha256
        or protocol_run.run_id != dispatch.dispatch_claim.run_id
        or protocol_run.protocol_state != dispatch.protocol_state
        or protocol_run.settled_at is None
        or handoff is None
        or handoff.pack_id != dispatch.handoff_pack_id
        or handoff.content_sha256 != dispatch.handoff_pack_sha256
        or predecessor_stage.stage_key != predecessor_contract.stage_key
        or predecessor_stage.gate_key != predecessor_contract.gate_key
        or predecessor_stage.status != StageStatus.COMPLETED
        or predecessor_gate.stage_key != predecessor_contract.stage_key
        or predecessor_gate.gate_key != predecessor_contract.gate_key
        or predecessor_gate.status != GateStatus.PASSED
    ):
        raise ValueError(
            "Methodology predecessor Run, Handoff, Stage, or Gate binding differs"
        )

    if (
        request.stage_key != stage_contract.stage_key
        or request.gate_key != stage_contract.gate_key
        or request.runtime != stage_contract.runtime
        or request.expected_stage_version != formal_stage.version
        or route.task_id != task.task_id
        or route.project_id != task.project_id
        or route.inventory_id != inventory.inventory_id
        or route.inventory_sha256 != inventory.content_sha256
        or route.inventory_sequence != stage_contract.sequence
        or route.stage_key != stage_contract.stage_key
        or route.gate_key != stage_contract.gate_key
        or route.runtime != stage_contract.runtime
        or route.stage_status != StageStatus.READY
        or route.gate_status is not None
        or not route.runnable
        or formal_stage.stage_key != stage_contract.stage_key
        or formal_stage.gate_key != stage_contract.gate_key
        or formal_stage.status != StageStatus.READY
        or snapshot.formal_gate is not None
    ):
        raise ValueError(
            "Only the exact ready, unconfigured next methodology Gate may be configured"
        )
    if request.repository != contract.repository:
        raise ValueError(
            "Methodology Stage Gate repository differs from the contract"
        )
    if repository is None or (
        repository.repository_id != contract.repository.repository_id
        or repository.ref != contract.repository.ref
        or repository.commit_sha != contract.repository.commit_sha
    ):
        raise ValueError(
            "Methodology Stage Gate repository binding is stale"
        )
    for artifact in [
        *migration_request.seed_artifacts,
        migration_gate.assertion.migration_artifact,
    ]:
        if observed_artifact_sha256s.get(artifact.path) != artifact.sha256:
            raise ValueError(
                "Methodology Stage Gate Artifact binding is stale"
            )
    if migration_request.runtime_registry_sha256 != runtime_registry_sha256(
        runtimes
    ):
        raise ValueError(
            "Methodology Stage Gate runtime registry binding is stale"
        )
    for pin in migration_request.runtime_pins:
        runtime = runtimes.get(pin.runtime)
        if (
            runtime is None
            or runtime_command_sha256(runtime) != pin.runtime_command_sha256
        ):
            raise ValueError(
                "Methodology Stage Gate runtime command binding is stale"
            )

    requirements = tuple(
        sorted(
            [
                item.requirement
                for item in stage_contract.gate.evidence_contracts
            ],
            key=lambda item: item.requirement_id,
        )
    )
    if not requirements:
        raise ValueError(
            "Methodology Stage Gate contract has no requirements"
        )
    return requirements
