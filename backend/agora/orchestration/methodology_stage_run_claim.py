"""Validation and Context materialization for a later methodology Run."""
from __future__ import annotations

import hashlib
import json
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
from agora.protocol.hashing import canonical_sha256, seal_model_payload
from agora.protocol.methodology_execution import (
    MethodologyExecutionContract,
    MethodologyStageExecutionContract,
)
from agora.protocol.methodology_migration import (
    AuthenticatedMethodologyMigrationGate,
    MethodologyMigrationActivationReceipt,
    MethodologyMigrationPreviewRequest,
)
from agora.protocol.methodology_stage_gate import (
    MethodologyStageGateReceipt,
    MethodologyStageGateRequest,
)
from agora.protocol.methodology_stage_run_claim import (
    MethodologyStageInputArtifactBinding,
    MethodologyStageRunClaimRequest,
    methodology_stage_run_id,
)
from agora.protocol.models import (
    ContextEntry,
    ContextPack,
    RequiredOutput,
    StageInventory,
)
from agora.protocol.state_machines import GateStatus, StageStatus, TaskStatus
from agora.tasks.models import TaskManifest

from .models import OrchestrationPlan, OrchestrationStage, PlanState, StageState
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


STAGE_RUN_CLAIM_REQUEST_LIMIT = 1_000_000
CONTEXT_ENTRY_CONTENT_LIMIT = 20_000


def load_methodology_stage_run_claim_request(
    path: Path,
) -> MethodologyStageRunClaimRequest:
    """Load one strict bounded later-Stage Run-claim request."""

    try:
        if not path.is_file():
            raise ValueError(
                "Methodology Stage Run claim request must be a file"
            )
        if path.stat().st_size > STAGE_RUN_CLAIM_REQUEST_LIMIT:
            raise ValueError(
                "Methodology Stage Run claim request exceeds 1 MiB"
            )
        with path.open("rb") as handle:
            payload = handle.read(STAGE_RUN_CLAIM_REQUEST_LIMIT + 1)
        if len(payload) > STAGE_RUN_CLAIM_REQUEST_LIMIT:
            raise ValueError(
                "Methodology Stage Run claim request exceeds 1 MiB"
            )
    except OSError as exc:
        raise ValueError(
            "Methodology Stage Run claim request is unavailable"
        ) from exc
    return MethodologyStageRunClaimRequest.model_validate_json(payload)


@dataclass(frozen=True)
class MethodologyStageRunClaimSnapshot:
    task: TaskManifest
    control_task: TaskRecord
    plan: OrchestrationPlan
    plan_stages: tuple[OrchestrationStage, ...]
    inventory: StageInventory
    migration_request: MethodologyMigrationPreviewRequest
    migration_gate: AuthenticatedMethodologyMigrationGate
    migration_receipt: MethodologyMigrationActivationReceipt
    execution_contract: MethodologyExecutionContract
    stage_gate_request: MethodologyStageGateRequest
    stage_gate_receipt: MethodologyStageGateReceipt
    predecessor_dispatch_receipt: MethodologyPredecessorDispatchReceipt
    predecessor_protocol_run: ProtocolRunRecord
    predecessor_stage: StageRecord
    predecessor_gate: GateRecord
    route: StageRouteDecision
    formal_stage: StageRecord
    formal_gate: GateRecord
    input_artifact_bindings: tuple[MethodologyStageInputArtifactBinding, ...]


def build_methodology_stage_required_outputs(
    *,
    task_id: str,
    stage_contract: MethodologyStageExecutionContract,
    run_id: str,
) -> tuple[RequiredOutput, ...]:
    """Materialize the exact output identities sealed into a later Run."""

    return tuple(
        RequiredOutput(
            output_id=(
                "artifact:"
                + canonical_sha256(
                    {
                        "task_id": task_id,
                        "stage_key": stage_contract.stage_key,
                        "run_id": run_id,
                        "source_output_id": output.source_output_id,
                    }
                )[:32]
            ),
            kind=output.kind,
            required=output.required,
        )
        for output in stage_contract.context.output_contracts
    )


def build_methodology_stage_run_claim_context(
    *,
    snapshot: MethodologyStageRunClaimSnapshot,
    request: MethodologyStageRunClaimRequest,
    principal: ControlPrincipal,
    repository: RepositoryRevision | None,
    observed_artifact_sha256s: dict[str, str | None],
    runtimes: dict[str, RuntimeCommand],
    claimed_at: str,
) -> ContextPack:
    """Recheck every live dependency and seal the exact later Context Pack."""

    task = snapshot.task
    control_task = snapshot.control_task
    plan = snapshot.plan
    inventory = snapshot.inventory
    migration_request = snapshot.migration_request
    migration_gate = snapshot.migration_gate
    migration_receipt = snapshot.migration_receipt
    contract = snapshot.execution_contract
    stage_gate_request = snapshot.stage_gate_request
    stage_gate_receipt = snapshot.stage_gate_receipt
    dispatch = snapshot.predecessor_dispatch_receipt
    protocol_run = snapshot.predecessor_protocol_run
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
        or principal.principal_id != migration_gate.authenticated_principal_id
        or principal.principal_id
        != stage_gate_receipt.authenticated_principal_id
    ):
        raise ValueError(
            "Stage Run claim principal does not match the authenticated chain"
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
        or request.stage_gate_receipt_id != stage_gate_receipt.receipt_id
        or request.stage_gate_receipt_sha256
        != stage_gate_receipt.content_sha256
        or request.predecessor_dispatch_receipt_id != dispatch.receipt_id
        or request.predecessor_dispatch_receipt_sha256
        != dispatch.content_sha256
    ):
        raise ValueError(
            "Methodology Stage Run claim request binding is stale or differs"
        )
    if (
        contract.project_id != task.project_id
        or contract.task_id != task.task_id
        or contract.plan_id != plan.plan_id
        or contract.plan_version != plan.version
        or contract.inventory_id != inventory.inventory_id
        or contract.inventory_sha256 != inventory.content_sha256
        or plan.state != PlanState.READY_FOR_IMPLEMENTATION
        or any(stage.state != StageState.PENDING for stage in snapshot.plan_stages)
        or [stage.stage_key for stage in snapshot.plan_stages]
        != [stage.stage_key for stage in contract.stages]
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
            "Methodology Stage Run claim migration provenance differs"
        )
    predecessor_sequence = methodology_dispatch_sequence(dispatch)
    predecessor_stage_key = methodology_dispatch_stage_key(dispatch)
    predecessor_gate_key = methodology_dispatch_gate_key(dispatch)
    current_predecessor_bound = (
        task.metadata.get("methodology_run_id")
        == dispatch.dispatch_claim.run_id
        and task.metadata.get(
            "methodology_current_stage_run_claimed",
            False,
        )
        is False
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
        or task.metadata.get("methodology_dispatch_authority") is not False
        or not current_predecessor_bound
        or contract.route_activated
        or contract.runtime_spawned
        or contract.routing_authority
        or contract.dispatch_authority
    ):
        raise ValueError(
            "Methodology successor is not in the next-Stage claimable state"
        )

    if (
        request.stage_sequence not in {2, 3, 4, 5, 6}
        or len(contract.stages) < request.stage_sequence
        or predecessor_sequence != request.stage_sequence - 1
    ):
        raise ValueError(
            "This bounded increment may claim only methodology Stage "
            "sequences 2 through 6 from the immediately preceding dispatch"
        )
    predecessor_contract = contract.stages[request.stage_sequence - 2]
    stage_contract = contract.stages[request.stage_sequence - 1]
    handoff = protocol_run.handoff_pack
    if (
        stage_gate_receipt.request_id != stage_gate_request.request_id
        or stage_gate_receipt.request_sha256
        != stage_gate_request.content_sha256
        or stage_gate_receipt.task_id != task.task_id
        or stage_gate_receipt.task_version_after != task.version
        or stage_gate_receipt.control_task_status_after != control_task.status
        or stage_gate_receipt.control_task_version_after
        != control_task.version
        or stage_gate_receipt.plan_version_after != plan.version
        or stage_gate_receipt.inventory_id != inventory.inventory_id
        or stage_gate_receipt.inventory_sha256 != inventory.content_sha256
        or stage_gate_receipt.execution_contract_id != contract.contract_id
        or stage_gate_receipt.execution_contract_sha256
        != contract.content_sha256
        or stage_gate_receipt.predecessor_dispatch_receipt_id
        != dispatch.receipt_id
        or stage_gate_receipt.predecessor_dispatch_receipt_sha256
        != dispatch.content_sha256
        or predecessor_stage_key != predecessor_contract.stage_key
        or predecessor_gate_key != predecessor_contract.gate_key
        or dispatch.next_stage_key != stage_contract.stage_key
        or dispatch.stage_status != StageStatus.COMPLETED
        or dispatch.gate_status != GateStatus.PASSED
        or protocol_run.run_id != dispatch.dispatch_claim.run_id
        or protocol_run.protocol_state != dispatch.protocol_state
        or protocol_run.settled_at is None
        or handoff is None
        or handoff.pack_id != dispatch.handoff_pack_id
        or handoff.content_sha256 != dispatch.handoff_pack_sha256
        or snapshot.predecessor_stage.status != StageStatus.COMPLETED
        or snapshot.predecessor_gate.status != GateStatus.PASSED
    ):
        raise ValueError(
            "Methodology Stage Run predecessor or Gate receipt chain differs"
        )
    if (
        request.stage_key != stage_contract.stage_key
        or request.gate_key != stage_contract.gate_key
        or request.runtime != stage_contract.runtime
        or request.expected_stage_version != formal_stage.version
        or request.expected_gate_version != formal_gate.version
        or request.expected_gate_status != formal_gate.status
        or stage_gate_receipt.stage_sequence != stage_contract.sequence
        or stage_gate_receipt.stage_key != stage_contract.stage_key
        or stage_gate_receipt.gate_key != stage_contract.gate_key
        or stage_gate_receipt.runtime != stage_contract.runtime
        or route.task_id != task.task_id
        or route.project_id != task.project_id
        or route.inventory_id != inventory.inventory_id
        or route.inventory_sha256 != inventory.content_sha256
        or route.inventory_sequence != stage_contract.sequence
        or route.stage_key != stage_contract.stage_key
        or route.gate_key != stage_contract.gate_key
        or route.runtime != stage_contract.runtime
        or route.stage_status != StageStatus.READY
        or route.gate_status != GateStatus.PENDING
        or not route.runnable
        or formal_stage.stage_key != stage_contract.stage_key
        or formal_stage.gate_key != stage_contract.gate_key
        or formal_stage.status != StageStatus.READY
        or formal_gate.stage_key != stage_contract.stage_key
        or formal_gate.gate_key != stage_contract.gate_key
        or formal_gate.status != GateStatus.PENDING
    ):
        raise ValueError(
            "Only the exact Gate-configured successor route may claim a Run"
        )
    if request.run_id != methodology_stage_run_id(
        task_id=task.task_id,
        execution_contract_sha256=contract.content_sha256,
        stage_sequence=stage_contract.sequence,
        stage_key=stage_contract.stage_key,
    ):
        raise ValueError(
            "Methodology Stage Run identity differs from the canonical binding"
        )
    expected_requirements = sorted(
        [
            item.requirement
            for item in stage_contract.gate.evidence_contracts
        ],
        key=lambda item: item.requirement_id,
    )
    if (
        formal_gate.requirements != expected_requirements
        or stage_gate_receipt.requirements != expected_requirements
    ):
        raise ValueError(
            "Methodology Stage Run formal Gate requirements drifted"
        )

    input_contracts = stage_contract.context.input_contracts
    if request.stage_sequence <= 4:
        if input_contracts or snapshot.input_artifact_bindings:
            raise ValueError(
                "Methodology sequence-2/3/4 contract must retain its exact "
                "empty input set"
            )
    else:
        resolved_inputs = [
            item
            for item in input_contracts
            if item.resolution != "optional_absent"
        ]
        allowed_resolutions = {"optional_absent", "selected_stage_output"}
        if request.stage_sequence == 6:
            allowed_resolutions.add("hash_bound_task_seed")
        if any(
            item.resolution not in allowed_resolutions
            or (
                item.resolution == "selected_stage_output"
                and (
                    item.instance_binding != "single"
                    or len(item.producer_stage_keys) != 1
                    or item.source_producer_stage_key
                    != item.producer_stage_keys[0]
                )
            )
            or (
                item.resolution == "hash_bound_task_seed"
                and (
                    not item.required
                    or item.instance_binding != "task_seed"
                    or item.source_producer_stage_key is None
                    or item.producer_stage_keys
                    or item.seed_artifact is None
                )
            )
            for item in input_contracts
        ):
            raise ValueError(
                "Methodology Stage input resolution exceeds its bounded scope"
            )
        if len(resolved_inputs) != len(snapshot.input_artifact_bindings):
            raise ValueError(
                "Methodology Stage resolved input Artifact set differs"
            )
        for input_contract, binding in zip(
            resolved_inputs,
            snapshot.input_artifact_bindings,
            strict=True,
        ):
            if (
                binding.consumer_stage_key != stage_contract.stage_key
                or binding.source_artifact_id
                != input_contract.source_artifact_id
                or binding.producer_stage_key
                != input_contract.source_producer_stage_key
                or binding.artifact.kind != input_contract.kind
                or (
                    input_contract.resolution == "selected_stage_output"
                    and binding.producer_run_id is None
                )
                or (
                    input_contract.resolution == "hash_bound_task_seed"
                    and (
                        binding.producer_run_id is not None
                        or binding.artifact != input_contract.seed_artifact
                    )
                )
            ):
                raise ValueError(
                    "Methodology Stage input Artifact binding differs"
                )
    if request.repository != contract.repository:
        raise ValueError(
            "Methodology Stage Run repository differs from the contract"
        )
    if repository is None or (
        repository.repository_id != contract.repository.repository_id
        or repository.ref != contract.repository.ref
        or repository.commit_sha != contract.repository.commit_sha
    ):
        raise ValueError(
            "Methodology Stage Run repository binding is stale"
        )
    for artifact in [
        *migration_request.seed_artifacts,
        migration_gate.assertion.migration_artifact,
    ]:
        if observed_artifact_sha256s.get(artifact.path) != artifact.sha256:
            raise ValueError(
                "Methodology Stage Run Artifact binding is stale"
            )
    if migration_request.runtime_registry_sha256 != runtime_registry_sha256(
        runtimes
    ):
        raise ValueError(
            "Methodology Stage Run runtime registry binding is stale"
        )
    for pin in migration_request.runtime_pins:
        runtime = runtimes.get(pin.runtime)
        if (
            runtime is None
            or runtime_command_sha256(runtime) != pin.runtime_command_sha256
        ):
            raise ValueError(
                "Methodology Stage Run runtime command binding is stale"
            )
    if stage_contract.context.budget.max_model_tokens is None:
        raise ValueError(
            "Methodology Stage Run requires a bounded Token reservation"
        )

    required_outputs = build_methodology_stage_required_outputs(
        task_id=task.task_id,
        stage_contract=stage_contract,
        run_id=request.run_id,
    )
    policies = [
        _context_entry(
            prefix="methodology-contract",
            title="Pinned methodology execution binding",
            content=_compact_json(
                {
                    "execution_contract": {
                        "contract_id": contract.contract_id,
                        "content_sha256": contract.content_sha256,
                        "activation_id": contract.activation_id,
                        "methodology_id": contract.methodology_id,
                        "methodology_version": contract.methodology_version,
                        "selected_scope": contract.selected_scope,
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
            prefix="methodology-predecessor",
            title="Settled predecessor and configured Gate binding",
            content=_compact_json(
                {
                    "predecessor_dispatch_receipt": {
                        "receipt_id": dispatch.receipt_id,
                        "content_sha256": dispatch.content_sha256,
                        "run_id": dispatch.dispatch_claim.run_id,
                        "handoff_pack_id": dispatch.handoff_pack_id,
                        "handoff_pack_sha256": dispatch.handoff_pack_sha256,
                    },
                    "stage_gate": {
                        "request_id": stage_gate_request.request_id,
                        "request_sha256": stage_gate_request.content_sha256,
                        "receipt_id": stage_gate_receipt.receipt_id,
                        "receipt_sha256": stage_gate_receipt.content_sha256,
                    },
                }
            ),
            source_ref=(
                f"methodology-stage-gate:{stage_gate_receipt.receipt_id}:"
                f"{stage_gate_receipt.content_sha256}"
            ),
        ),
        _context_entry(
            prefix="methodology-stage",
            title="Pinned methodology Stage role and sensor template",
            content=_compact_json(
                {
                    "stage_key": stage_contract.stage_key,
                    "source_stage_key": stage_contract.source_stage_key,
                    "gate_key": stage_contract.gate_key,
                    "sequence": stage_contract.sequence,
                    "instance_index": stage_contract.instance_index,
                    "instance_count": stage_contract.instance_count,
                    "runtime": stage_contract.runtime,
                    "source_role_profile": (
                        stage_contract.source_role_profile.model_dump(
                            mode="json"
                        )
                    ),
                    "sensors": [
                        item.model_dump(mode="json")
                        for item in stage_contract.context.sensors
                    ],
                }
            ),
            source_ref=(
                f"methodology-stage:{contract.contract_id}:"
                f"{stage_contract.stage_key}"
            ),
        ),
        _context_entry(
            prefix="methodology-source-inputs",
            title="Pinned methodology source inputs",
            content=stage_contract.context.source_inputs_text,
            source_ref=(
                f"methodology-source-inputs:{contract.contract_id}:"
                f"{stage_contract.stage_key}"
            ),
        ),
        _context_entry(
            prefix="methodology-handoff-gate",
            title="Pinned methodology Handoff and Gate projection",
            content=_compact_json(
                {
                    "handoff_contract_sha256": canonical_sha256(
                        stage_contract.handoff.model_dump(mode="json")
                    ),
                    "gate_contract_sha256": canonical_sha256(
                        stage_contract.gate.model_dump(mode="json")
                    ),
                    "producer_runtime": stage_contract.handoff.producer_runtime,
                    "allowed_output_kinds": (
                        stage_contract.handoff.allowed_output_kinds
                    ),
                    "required_output_kinds": (
                        stage_contract.handoff.required_output_kinds
                    ),
                    "exact_context_echo_required": (
                        stage_contract.handoff.exact_context_echo_required
                    ),
                    "unbound_output_allowed": (
                        stage_contract.handoff.unbound_output_allowed
                    ),
                    "native_state_authority": (
                        stage_contract.handoff.native_state_authority
                    ),
                    "suggested_next_action_authority": (
                        stage_contract.handoff.suggested_next_action_authority
                    ),
                    "format_only_repair_attempts": (
                        stage_contract.handoff.format_only_repair_attempts
                    ),
                    "gate_key": stage_contract.gate.gate_key,
                    "gate_requirement_ids": [
                        item.requirement.requirement_id
                        for item in stage_contract.gate.evidence_contracts
                    ],
                }
            ),
            source_ref=(
                f"methodology-handoff-gate:{contract.contract_id}:"
                f"{stage_contract.stage_key}"
            ),
        ),
    ]
    payload = {
        "schema_version": "1.0",
        "pack_id": request.context_pack_id,
        "project_id": task.project_id,
        "task_id": task.task_id,
        "stage_key": stage_contract.stage_key,
        "run_id": request.run_id,
        "generated_at": claimed_at,
        "stage_contract": stage_contract.context.stage_contract,
        "input_artifacts": [
            item.artifact for item in snapshot.input_artifact_bindings
        ],
        "required_outputs": required_outputs,
        "forbidden_constraints": (
            stage_contract.context.forbidden_constraints
        ),
        "policies": policies,
        "task_memory": [],
        "project_knowledge": [],
        "user_preferences": [],
        "budget": stage_contract.context.budget,
    }
    return ContextPack.model_validate(
        seal_model_payload(ContextPack, payload)
    )


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
