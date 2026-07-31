"""Validation for authenticated, non-dispatching first-route activation."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agora.control_plane.auth import ControlPrincipal
from agora.control_plane.models import StageRouteDecision, TaskRecord
from agora.protocol.methodology_execution import MethodologyExecutionContract
from agora.protocol.methodology_migration import (
    AuthenticatedMethodologyMigrationGate,
    MethodologyMigrationActivationReceipt,
    MethodologyMigrationPreviewRequest,
)
from agora.protocol.methodology_route_activation import (
    MethodologyRouteActivationRequest,
    MethodologySeedArtifactRegistration,
)
from agora.protocol.models import StageInventory
from agora.tasks.models import TaskManifest

from .models import OrchestrationPlan, OrchestrationStage, PlanState, StageState
from .protocol_context import RepositoryRevision
from .runtime import RuntimeCommand
from .runtime_capabilities import (
    runtime_command_sha256,
    runtime_registry_sha256,
)


ROUTE_ACTIVATION_REQUEST_LIMIT = 1_000_000


def load_methodology_route_activation_request(
    path: Path,
) -> MethodologyRouteActivationRequest:
    """Load one strict bounded route-activation request."""

    try:
        if not path.is_file():
            raise ValueError(
                "Methodology route activation request must be a file"
            )
        if path.stat().st_size > ROUTE_ACTIVATION_REQUEST_LIMIT:
            raise ValueError(
                "Methodology route activation request exceeds 1 MiB"
            )
        with path.open("rb") as handle:
            payload = handle.read(ROUTE_ACTIVATION_REQUEST_LIMIT + 1)
        if len(payload) > ROUTE_ACTIVATION_REQUEST_LIMIT:
            raise ValueError(
                "Methodology route activation request exceeds 1 MiB"
            )
    except OSError as exc:
        raise ValueError(
            "Methodology route activation request is unavailable"
        ) from exc
    return MethodologyRouteActivationRequest.model_validate_json(payload)


@dataclass(frozen=True)
class MethodologyRouteActivationSnapshot:
    task: TaskManifest
    control_task: TaskRecord
    plan: OrchestrationPlan
    plan_stages: tuple[OrchestrationStage, ...]
    inventory: StageInventory
    migration_request: MethodologyMigrationPreviewRequest
    migration_gate: AuthenticatedMethodologyMigrationGate
    migration_receipt: MethodologyMigrationActivationReceipt
    execution_contract: MethodologyExecutionContract
    route: StageRouteDecision


def validate_methodology_route_activation(
    *,
    snapshot: MethodologyRouteActivationSnapshot,
    request: MethodologyRouteActivationRequest,
    principal: ControlPrincipal,
    repository: RepositoryRevision | None,
    observed_artifact_sha256s: dict[str, str | None],
    runtimes: dict[str, RuntimeCommand],
) -> tuple[MethodologySeedArtifactRegistration, ...]:
    """Recheck every live contract dependency and return first-Stage seeds."""

    task = snapshot.task
    control_task = snapshot.control_task
    plan = snapshot.plan
    inventory = snapshot.inventory
    migration_request = snapshot.migration_request
    migration_gate = snapshot.migration_gate
    migration_receipt = snapshot.migration_receipt
    contract = snapshot.execution_contract
    route = snapshot.route

    if "control_plane.approve" not in principal.permissions:
        raise ValueError(
            "Authenticated principal lacks control_plane.approve permission"
        )
    if task.project_id not in principal.projects:
        raise ValueError(
            "Authenticated principal is not authorized for the successor project"
        )
    if (
        principal.principal_id != migration_gate.authenticated_principal_id
        or principal.principal_id != contract.authenticated_principal_id
    ):
        raise ValueError(
            "Route activation principal does not match the authenticated migration Gate"
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
    ):
        raise ValueError(
            "Methodology route activation request binding is stale or differs"
        )
    if (
        contract.project_id != task.project_id
        or contract.task_id != task.task_id
        or contract.task_version != task.version
        or contract.control_task_version != control_task.version
        or contract.plan_id != plan.plan_id
        or contract.plan_version != plan.version
        or contract.inventory_id != inventory.inventory_id
        or contract.inventory_sha256 != inventory.content_sha256
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
        or migration_receipt.authenticated_gate_id
        != migration_gate.gate_id
        or migration_receipt.authenticated_gate_sha256
        != migration_gate.content_sha256
    ):
        raise ValueError(
            "Methodology route activation migration provenance differs"
        )
    if (
        task.metadata.get("methodology_route_activated") is not False
        or task.metadata.get("methodology_dispatch_authority") is not False
        or plan.state != PlanState.READY_FOR_IMPLEMENTATION
        or any(stage.state != StageState.PENDING for stage in snapshot.plan_stages)
        or [stage.stage_key for stage in snapshot.plan_stages]
        != [stage.stage_key for stage in contract.stages]
        or contract.route_activated
        or contract.runtime_spawned
        or contract.routing_authority
        or contract.dispatch_authority
    ):
        raise ValueError(
            "Methodology successor is not in the inert contract-bound state"
        )

    first_stage = contract.stages[0]
    if (
        request.first_stage_key != first_stage.stage_key
        or request.first_gate_key != first_stage.gate_key
        or route.task_id != task.task_id
        or route.project_id != task.project_id
        or route.inventory_id != inventory.inventory_id
        or route.inventory_sha256 != inventory.content_sha256
        or route.stage_key != first_stage.stage_key
        or route.gate_key != first_stage.gate_key
        or route.stage_status is not None
        or route.gate_status is not None
        or route.runnable
    ):
        raise ValueError(
            "Only the exact unconfigured first methodology route may be activated"
        )
    if request.repository != contract.repository:
        raise ValueError(
            "Methodology route activation repository request differs from the contract"
        )
    if repository is None or (
        repository.repository_id != contract.repository.repository_id
        or repository.ref != contract.repository.ref
        or repository.commit_sha != contract.repository.commit_sha
    ):
        raise ValueError(
            "Methodology route activation repository binding is stale"
        )
    for artifact in [
        *migration_request.seed_artifacts,
        migration_gate.assertion.migration_artifact,
    ]:
        if observed_artifact_sha256s.get(artifact.path) != artifact.sha256:
            raise ValueError(
                "Methodology route activation Artifact binding is stale"
            )
    if migration_request.runtime_registry_sha256 != runtime_registry_sha256(
        runtimes
    ):
        raise ValueError(
            "Methodology route activation runtime registry binding is stale"
        )
    for pin in migration_request.runtime_pins:
        runtime = runtimes.get(pin.runtime)
        if (
            runtime is None
            or runtime_command_sha256(runtime) != pin.runtime_command_sha256
        ):
            raise ValueError(
                "Methodology route activation runtime command binding is stale"
            )

    seeds: list[MethodologySeedArtifactRegistration] = []
    for input_contract in first_stage.context.input_contracts:
        if input_contract.resolution != "hash_bound_task_seed":
            continue
        if not input_contract.required or input_contract.seed_artifact is None:
            raise ValueError(
                "First methodology Stage seed input is not an exact required reference"
            )
        seeds.append(
            MethodologySeedArtifactRegistration(
                consumer_stage_key=first_stage.stage_key,
                source_artifact_id=input_contract.source_artifact_id,
                artifact=input_contract.seed_artifact,
            )
        )
    return tuple(seeds)
