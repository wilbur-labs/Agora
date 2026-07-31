"""Validation and Context materialization for the first methodology Run claim."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from agora.control_plane.auth import ControlPrincipal
from agora.control_plane.models import (
    GateRecord,
    StageRecord,
    StageRouteDecision,
    TaskRecord,
)
from agora.protocol.hashing import canonical_sha256, seal_model_payload
from agora.protocol.methodology_execution import MethodologyExecutionContract
from agora.protocol.methodology_migration import (
    AuthenticatedMethodologyMigrationGate,
    MethodologyMigrationActivationReceipt,
    MethodologyMigrationPreviewRequest,
)
from agora.protocol.methodology_route_activation import (
    MethodologyRouteActivationReceipt,
    MethodologyRouteActivationRequest,
    MethodologySeedArtifactRegistration,
)
from agora.protocol.methodology_run_claim import MethodologyRunClaimRequest
from agora.protocol.models import (
    ContextEntry,
    ContextPack,
    RequiredOutput,
    StageInventory,
)
from agora.protocol.state_machines import GateStatus, StageStatus
from agora.tasks.models import TaskManifest

from .models import OrchestrationPlan, OrchestrationStage, PlanState, StageState
from .protocol_context import RepositoryRevision
from .runtime import RuntimeCommand
from .runtime_capabilities import (
    runtime_command_sha256,
    runtime_registry_sha256,
)


RUN_CLAIM_REQUEST_LIMIT = 1_000_000
CONTEXT_ENTRY_CONTENT_LIMIT = 20_000


def load_methodology_run_claim_request(path: Path) -> MethodologyRunClaimRequest:
    """Load one strict bounded methodology Run-claim request."""

    try:
        if not path.is_file():
            raise ValueError("Methodology Run claim request must be a file")
        if path.stat().st_size > RUN_CLAIM_REQUEST_LIMIT:
            raise ValueError("Methodology Run claim request exceeds 1 MiB")
        with path.open("rb") as handle:
            payload = handle.read(RUN_CLAIM_REQUEST_LIMIT + 1)
        if len(payload) > RUN_CLAIM_REQUEST_LIMIT:
            raise ValueError("Methodology Run claim request exceeds 1 MiB")
    except OSError as exc:
        raise ValueError("Methodology Run claim request is unavailable") from exc
    return MethodologyRunClaimRequest.model_validate_json(payload)


@dataclass(frozen=True)
class MethodologyRunClaimSnapshot:
    task: TaskManifest
    control_task: TaskRecord
    plan: OrchestrationPlan
    plan_stages: tuple[OrchestrationStage, ...]
    inventory: StageInventory
    migration_request: MethodologyMigrationPreviewRequest
    migration_gate: AuthenticatedMethodologyMigrationGate
    migration_receipt: MethodologyMigrationActivationReceipt
    execution_contract: MethodologyExecutionContract
    route_activation_request: MethodologyRouteActivationRequest
    route_activation_receipt: MethodologyRouteActivationReceipt
    route: StageRouteDecision
    formal_stage: StageRecord
    formal_gate: GateRecord
    seed_artifacts: tuple[MethodologySeedArtifactRegistration, ...]


def build_methodology_run_claim_context(
    *,
    snapshot: MethodologyRunClaimSnapshot,
    request: MethodologyRunClaimRequest,
    principal: ControlPrincipal,
    repository: RepositoryRevision | None,
    observed_artifact_sha256s: dict[str, str | None],
    runtimes: dict[str, RuntimeCommand],
    claimed_at: str,
) -> ContextPack:
    """Recheck every live dependency and materialize one exact Context Pack."""

    task = snapshot.task
    control_task = snapshot.control_task
    plan = snapshot.plan
    inventory = snapshot.inventory
    migration_request = snapshot.migration_request
    migration_gate = snapshot.migration_gate
    migration_receipt = snapshot.migration_receipt
    contract = snapshot.execution_contract
    activation_request = snapshot.route_activation_request
    activation_receipt = snapshot.route_activation_receipt
    route = snapshot.route
    formal_stage = snapshot.formal_stage
    formal_gate = snapshot.formal_gate

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
        or principal.principal_id
        != activation_receipt.authenticated_principal_id
        or principal.principal_id != migration_gate.authenticated_principal_id
    ):
        raise ValueError(
            "Run claim principal does not match the authenticated migration chain"
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
        or request.route_activation_receipt_id != activation_receipt.receipt_id
        or request.route_activation_receipt_sha256
        != activation_receipt.content_sha256
    ):
        raise ValueError("Methodology Run claim request binding is stale or differs")
    if (
        activation_receipt.request_id != activation_request.request_id
        or activation_receipt.request_sha256
        != activation_request.content_sha256
        or activation_receipt.execution_contract_id != contract.contract_id
        or activation_receipt.execution_contract_sha256
        != contract.content_sha256
        or activation_receipt.task_version_after != task.version
        or activation_receipt.control_task_status_after != control_task.status
        or activation_receipt.control_task_version_after != control_task.version
        or activation_receipt.inventory_id != inventory.inventory_id
        or activation_receipt.inventory_sha256 != inventory.content_sha256
    ):
        raise ValueError("Methodology route activation provenance is stale or differs")
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
        raise ValueError("Methodology Run claim migration provenance differs")
    if (
        task.metadata.get("methodology_route_activated") is not True
        or task.metadata.get("methodology_dispatch_authority") is not False
        or task.metadata.get("methodology_run_claimed", False) is not False
        or task.metadata.get("methodology_route_activation_request_id")
        != activation_request.request_id
        or task.metadata.get("methodology_route_activation_request_sha256")
        != activation_request.content_sha256
        or task.metadata.get("methodology_execution_contract_id")
        != contract.contract_id
        or task.metadata.get("methodology_execution_contract_sha256")
        != contract.content_sha256
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
            "Methodology successor is not in the activated unclaimed state"
        )

    first_stage = contract.stages[0]
    if (
        request.first_stage_key != first_stage.stage_key
        or request.first_gate_key != first_stage.gate_key
        or request.runtime != first_stage.runtime
        or route.task_id != task.task_id
        or route.project_id != task.project_id
        or route.inventory_id != inventory.inventory_id
        or route.inventory_sha256 != inventory.content_sha256
        or route.stage_key != first_stage.stage_key
        or route.gate_key != first_stage.gate_key
        or route.stage_status != StageStatus.READY
        or route.gate_status != GateStatus.PENDING
        or not route.runnable
        or formal_stage.stage_key != first_stage.stage_key
        or formal_stage.gate_key != first_stage.gate_key
        or formal_stage.status != StageStatus.READY
        or formal_gate.gate_key != first_stage.gate_key
        or formal_gate.stage_key != first_stage.stage_key
        or formal_gate.status != GateStatus.PENDING
    ):
        raise ValueError(
            "Only the exact ready first methodology route may claim a Run"
        )
    expected_requirements = sorted(
        [item.requirement for item in first_stage.gate.evidence_contracts],
        key=lambda item: item.requirement_id,
    )
    if formal_gate.requirements != expected_requirements:
        raise ValueError("Methodology first Gate requirements drifted")
    if request.repository != contract.repository:
        raise ValueError("Methodology Run claim repository differs from the contract")
    if repository is None or (
        repository.repository_id != contract.repository.repository_id
        or repository.ref != contract.repository.ref
        or repository.commit_sha != contract.repository.commit_sha
    ):
        raise ValueError("Methodology Run claim repository binding is stale")
    for artifact in [
        *migration_request.seed_artifacts,
        migration_gate.assertion.migration_artifact,
    ]:
        if observed_artifact_sha256s.get(artifact.path) != artifact.sha256:
            raise ValueError("Methodology Run claim Artifact binding is stale")
    if migration_request.runtime_registry_sha256 != runtime_registry_sha256(
        runtimes
    ):
        raise ValueError("Methodology Run claim runtime registry binding is stale")
    for pin in migration_request.runtime_pins:
        runtime = runtimes.get(pin.runtime)
        if (
            runtime is None
            or runtime_command_sha256(runtime) != pin.runtime_command_sha256
        ):
            raise ValueError("Methodology Run claim runtime command binding is stale")

    seed_by_source = {
        item.source_artifact_id: item for item in snapshot.seed_artifacts
    }
    migration_seed_sha256_by_path = {
        item.path: item.sha256 for item in migration_request.seed_artifacts
    }
    expected_seed_sources: list[str] = []
    input_artifacts = []
    for input_contract in first_stage.context.input_contracts:
        if input_contract.resolution == "optional_absent":
            continue
        if input_contract.resolution != "hash_bound_task_seed":
            raise ValueError(
                "First methodology Run may consume only registered Task seeds"
            )
        if not input_contract.required or input_contract.seed_artifact is None:
            raise ValueError(
                "First methodology Stage seed input is not an exact required reference"
            )
        expected_seed_sources.append(input_contract.source_artifact_id)
        registration = seed_by_source.get(input_contract.source_artifact_id)
        location = (
            registration.artifact.location
            if registration is not None
            else None
        )
        if (
            registration is None
            or registration.consumer_stage_key != first_stage.stage_key
            or registration.artifact != input_contract.seed_artifact
            or location is None
            or migration_seed_sha256_by_path.get(location.path)
            != registration.artifact.sha256
            or observed_artifact_sha256s.get(location.path)
            != registration.artifact.sha256
        ):
            raise ValueError(
                "Registered methodology seed reference differs from the contract"
            )
        input_artifacts.append(registration.artifact)
    if set(seed_by_source) != set(expected_seed_sources):
        raise ValueError(
            "Registered methodology seed reference set differs from the contract"
        )
    if first_stage.context.budget.max_model_tokens is None:
        raise ValueError(
            "Methodology Run claim requires a bounded model Token reservation"
        )

    required_outputs = [
        RequiredOutput(
            output_id=(
                "artifact:"
                + canonical_sha256(
                    {
                        "task_id": task.task_id,
                        "stage_key": first_stage.stage_key,
                        "run_id": request.run_id,
                        "source_output_id": output.source_output_id,
                    }
                )[:32]
            ),
            kind=output.kind,
            required=output.required,
        )
        for output in first_stage.context.output_contracts
    ]
    policies = [
        _context_entry(
            prefix="methodology-contract",
            title="Pinned methodology execution and activation binding",
            content=_compact_json(
                {
                    "execution_contract": {
                        "contract_id": contract.contract_id,
                        "content_sha256": contract.content_sha256,
                        "activation_id": contract.activation_id,
                        "methodology_id": contract.methodology_id,
                        "methodology_version": contract.methodology_version,
                        "source_graph_sha256": contract.source_graph_sha256,
                        "activation_definition_sha256": (
                            contract.activation_definition_sha256
                        ),
                        "selected_scope": contract.selected_scope,
                    },
                    "route_activation": {
                        "request_id": activation_request.request_id,
                        "request_sha256": activation_request.content_sha256,
                        "receipt_id": activation_receipt.receipt_id,
                        "receipt_sha256": activation_receipt.content_sha256,
                    },
                    "repository": contract.repository.model_dump(mode="json"),
                }
            ),
            source_ref=(
                f"methodology-contract:{contract.contract_id}:"
                f"{contract.content_sha256}"
            ),
        ),
        _context_entry(
            prefix="methodology-stage",
            title="Pinned methodology Stage role and sensor template",
            content=_compact_json(
                {
                    "stage_key": first_stage.stage_key,
                    "source_stage_key": first_stage.source_stage_key,
                    "gate_key": first_stage.gate_key,
                    "sequence": first_stage.sequence,
                    "instance_index": first_stage.instance_index,
                    "instance_count": first_stage.instance_count,
                    "runtime": first_stage.runtime,
                    "source_role_profile": (
                        first_stage.source_role_profile.model_dump(mode="json")
                    ),
                    "sensors": [
                        item.model_dump(mode="json")
                        for item in first_stage.context.sensors
                    ],
                }
            ),
            source_ref=(
                f"methodology-stage:{contract.contract_id}:"
                f"{first_stage.stage_key}"
            ),
        ),
        _context_entry(
            prefix="methodology-source-inputs",
            title="Pinned methodology source inputs",
            content=first_stage.context.source_inputs_text,
            source_ref=(
                f"methodology-source-inputs:{contract.contract_id}:"
                f"{first_stage.stage_key}"
            ),
        ),
        _context_entry(
            prefix="methodology-handoff-gate",
            title="Pinned methodology Handoff and Gate template",
            content=_compact_json(
                {
                    "handoff": first_stage.handoff.model_dump(mode="json"),
                    "gate": first_stage.gate.model_dump(mode="json"),
                }
            ),
            source_ref=(
                f"methodology-handoff-gate:{contract.contract_id}:"
                f"{first_stage.stage_key}"
            ),
        ),
    ]
    payload = {
        "schema_version": "1.0",
        "pack_id": request.context_pack_id,
        "project_id": task.project_id,
        "task_id": task.task_id,
        "stage_key": first_stage.stage_key,
        "run_id": request.run_id,
        "generated_at": claimed_at,
        "stage_contract": first_stage.context.stage_contract,
        "input_artifacts": input_artifacts,
        "required_outputs": required_outputs,
        "forbidden_constraints": first_stage.context.forbidden_constraints,
        "policies": policies,
        "task_memory": [],
        "project_knowledge": [],
        "user_preferences": [],
        "budget": first_stage.context.budget,
    }
    return ContextPack.model_validate(seal_model_payload(ContextPack, payload))


def _context_entry(
    *,
    prefix: str,
    title: str,
    content: str,
    source_ref: str,
) -> ContextEntry:
    if len(content) > CONTEXT_ENTRY_CONTENT_LIMIT:
        raise ValueError(
            f"Methodology Context entry exceeds 20,000 characters: {title}"
        )
    digest = canonical_sha256(
        {"title": title, "content": content, "source_ref": source_ref}
    )
    return ContextEntry(
        entry_id=f"{prefix}:{digest[:32]}",
        version=1,
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        title=title,
        content=content,
        source_ref=source_ref,
    )


def _compact_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
