"""Pure materialization for one inert methodology successor execution contract."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from agora.control_plane.auth import ControlPrincipal
from agora.control_plane.models import TaskRecord
from agora.protocol.hashing import canonical_sha256, seal_model_payload
from agora.protocol.methodology_execution import MethodologyExecutionContract
from agora.protocol.methodology_migration import (
    AuthenticatedMethodologyMigrationGate,
    MethodologyMigrationActivationReceipt,
    MethodologyMigrationPreviewRequest,
)
from agora.protocol.models import ArtifactLocation, GateRequirement, StageInventory
from agora.tasks.models import TaskManifest

from .aws_aidlc import AWS_AIDLC_V2_3_SOURCE_GRAPH
from .aws_aidlc_activation import AWS_AIDLC_V2_3_ACTIVATION_DEFINITION
from .models import OrchestrationPlan, OrchestrationStage, PlanState, StageState
from .protocol_context import RepositoryRevision
from .runtime import RuntimeCommand
from .runtime_capabilities import (
    runtime_command_sha256,
    runtime_registry_sha256,
)


EXECUTION_FORBIDDEN_CONSTRAINTS = (
    "Do not modify repository content or native Codex, Claude, Kiro, or AI-DLC files.",
    "Agora alone writes authoritative Task, Stage, Gate, Artifact, Evidence, and Approval state.",
    "Do not infer semantic success from process exit code or transport completion.",
    "Use the sealed Context Pack and Handoff Pack instead of a prior transcript.",
    "Do not perform automatic cross-Stage rework; block and escalate for an explicit Task decision.",
)


def _bind_selected_producer_instances(
    *,
    producer_stage_keys: list[str],
    consumer_stage_keys: list[str],
    instance_index: int,
) -> tuple[list[str], str]:
    """Select exact producer instances for one consumer instance."""

    if len(producer_stage_keys) == 1:
        return producer_stage_keys, "single"
    if len(consumer_stage_keys) > 1:
        if len(producer_stage_keys) != len(consumer_stage_keys):
            raise ValueError(
                "Expanded successor Stage dependencies must have matching unit counts"
            )
        return [producer_stage_keys[instance_index - 1]], "matching_unit"
    return producer_stage_keys, "all_units"


@dataclass(frozen=True)
class MethodologyExecutionSnapshot:
    task: TaskManifest
    control_task: TaskRecord
    plan: OrchestrationPlan
    plan_stages: tuple[OrchestrationStage, ...]
    inventory: StageInventory
    request: MethodologyMigrationPreviewRequest
    gate: AuthenticatedMethodologyMigrationGate
    receipt: MethodologyMigrationActivationReceipt


def build_methodology_execution_contract(
    *,
    snapshot: MethodologyExecutionSnapshot,
    principal: ControlPrincipal,
    repository: RepositoryRevision | None,
    observed_artifact_sha256s: dict[str, str | None],
    runtimes: dict[str, RuntimeCommand],
    timeout_seconds: int,
    max_output_bytes: int,
    materialized_at: datetime | str,
) -> MethodologyExecutionContract:
    """Build the exact non-dispatching contract from current sealed bindings."""

    task = snapshot.task
    control_task = snapshot.control_task
    plan = snapshot.plan
    inventory = snapshot.inventory
    request = snapshot.request
    gate = snapshot.gate
    receipt = snapshot.receipt
    activation = AWS_AIDLC_V2_3_ACTIVATION_DEFINITION
    source_graph = AWS_AIDLC_V2_3_SOURCE_GRAPH

    if "control_plane.approve" not in principal.permissions:
        raise ValueError(
            "Authenticated principal lacks control_plane.approve permission"
        )
    if task.project_id not in principal.projects:
        raise ValueError(
            "Authenticated principal is not authorized for the successor project"
        )
    if (
        principal.principal_id != gate.authenticated_principal_id
        or principal.principal_id != gate.assertion.approved_by
    ):
        raise ValueError(
            "Execution contract principal does not match the authenticated migration Gate"
        )
    if request.project_id != task.project_id or receipt.project_id != task.project_id:
        raise ValueError("Execution contract Project binding differs")
    if (
        receipt.successor_task_id != task.task_id
        or receipt.successor_plan_id != plan.plan_id
        or receipt.successor_inventory_id != inventory.inventory_id
        or receipt.successor_inventory_sha256 != inventory.content_sha256
    ):
        raise ValueError("Execution contract migration receipt binding differs")
    if (
        receipt.request_id != request.request_id
        or receipt.request_sha256 != request.content_sha256
        or receipt.authenticated_gate_id != gate.gate_id
        or receipt.authenticated_gate_sha256 != gate.content_sha256
    ):
        raise ValueError("Execution contract migration provenance differs")
    if (
        task.version != receipt.successor_task_version
        or control_task.version != receipt.successor_control_task_version
        or plan.version != receipt.successor_plan_version
    ):
        raise ValueError("Execution contract successor version binding is stale")
    if (
        task.metadata.get("methodology_activation_id") != activation.activation_id
        or task.metadata.get("methodology_activation_sha256")
        != activation.content_sha256
        or task.metadata.get("methodology_source_graph_sha256")
        != source_graph.content_sha256
        or task.metadata.get("methodology_selected_scope")
        != request.selected_scope
        or task.metadata.get("methodology_migration_request_sha256")
        != request.content_sha256
        or task.metadata.get("methodology_migration_gate_sha256")
        != gate.content_sha256
        or task.metadata.get("methodology_route_activated") is not False
        or task.metadata.get("methodology_dispatch_authority") is not False
    ):
        raise ValueError("Execution contract successor metadata is unavailable or drifted")
    if (
        plan.state != PlanState.READY_FOR_IMPLEMENTATION
        or plan.methodology_id != activation.methodology_id
        or plan.methodology_version != activation.methodology_version
        or plan.methodology_sha256 != activation.content_sha256
        or inventory.task_id != task.task_id
        or inventory.project_id != task.project_id
        or inventory.plan_id != plan.plan_id
        or inventory.methodology_id != plan.methodology_id
        or inventory.methodology_version != plan.methodology_version
        or inventory.methodology_sha256 != plan.methodology_sha256
        or inventory.provisional
    ):
        raise ValueError("Execution contract Plan/inventory binding differs")
    if repository is None or (
        repository.repository_id != request.repository.repository_id
        or repository.ref != request.repository.ref
        or repository.commit_sha != request.repository.commit_sha
    ):
        raise ValueError("Execution contract repository binding is stale")
    for artifact in [*request.seed_artifacts, gate.assertion.migration_artifact]:
        if observed_artifact_sha256s.get(artifact.path) != artifact.sha256:
            raise ValueError(
                "Execution contract migration Artifact binding is stale"
            )

    responsibility_order = (
        "production_execution",
        "independent_correctness",
        "methodology_stewardship",
    )
    pin_by_responsibility = {
        pin.responsibility: pin for pin in request.runtime_pins
    }
    if set(pin_by_responsibility) != set(responsibility_order):
        raise ValueError("Execution contract requires all three runtime pins")
    if request.runtime_registry_sha256 != runtime_registry_sha256(runtimes):
        raise ValueError("Execution contract runtime registry binding is stale")
    for pin in request.runtime_pins:
        runtime = runtimes.get(pin.runtime)
        if (
            runtime is None
            or runtime_command_sha256(runtime) != pin.runtime_command_sha256
        ):
            raise ValueError("Execution contract runtime command binding is stale")
    runtime_pins = [
        pin_by_responsibility[responsibility]
        for responsibility in responsibility_order
    ]
    production_runtime = runtime_pins[0].runtime

    selected_source_stages = [
        stage
        for stage in source_graph.stages
        if request.selected_scope in stage.scopes
    ]
    allocation_by_source = {
        item.source_stage_key: item for item in request.budget.stage_allocations
    }
    if set(allocation_by_source) != {
        stage.stage_key for stage in selected_source_stages
    }:
        raise ValueError("Execution contract Stage allocations differ from scope")
    activation_by_source = {
        stage.source_stage_key: stage for stage in activation.stages
    }
    output_producer = {
        output.output_id: stage.source_stage_key
        for stage in activation.stages
        for output in stage.output_artifacts
    }

    expected_stage_keys_by_source: dict[str, list[str]] = {}
    expected_stage_keys: list[str] = []
    for source_stage in selected_source_stages:
        allocation = allocation_by_source[source_stage.stage_key]
        keys = [
            (
                f"{source_stage.stage_key}-unit-{index:03d}"
                if allocation.instance_count > 1
                else source_stage.stage_key
            )
            for index in range(1, allocation.instance_count + 1)
        ]
        expected_stage_keys_by_source[source_stage.stage_key] = keys
        expected_stage_keys.extend(keys)

    inventory_items = [
        item for group in inventory.groups for item in group.stages
    ]
    inventory_stage_keys = [item.stage_key for item in inventory_items]
    plan_stage_keys = [item.stage_key for item in snapshot.plan_stages]
    if inventory_stage_keys != expected_stage_keys or plan_stage_keys != expected_stage_keys:
        raise ValueError(
            "Execution contract Stage order differs from the sealed successor"
        )
    if any(item.state != StageState.PENDING for item in snapshot.plan_stages):
        raise ValueError("Execution contract requires every successor Stage pending")

    inventory_by_key = {item.stage_key: item for item in inventory_items}
    plan_stage_by_key = {item.stage_key: item for item in snapshot.plan_stages}
    seed_by_key = {
        (
            seed.consumer_stage_key,
            seed.artifact_id,
            seed.source_producer_stage_key,
        ): seed
        for seed in request.seed_artifacts
    }
    consumed_seed_keys: set[tuple[str, str, str]] = set()

    stages = []
    sequence = 0
    for source_stage in selected_source_stages:
        activation_stage = activation_by_source[source_stage.stage_key]
        allocation = allocation_by_source[source_stage.stage_key]
        stage_keys = expected_stage_keys_by_source[source_stage.stage_key]
        for instance_index, stage_key in enumerate(stage_keys, start=1):
            sequence += 1
            inventory_item = inventory_by_key[stage_key]
            plan_stage = plan_stage_by_key[stage_key]
            if (
                inventory_item.sequence < 1
                or inventory_item.runtime != production_runtime
                or inventory_item.role != "production_execution"
                or plan_stage.adapter != inventory_item.runtime
                or plan_stage.role != inventory_item.role
                or plan_stage.title != inventory_item.title
                or plan_stage.token_budget
                != allocation.token_allocation_per_instance
                or plan_stage.cost_budget_usd
                != allocation.cost_allocation_per_instance_usd
            ):
                raise ValueError(
                    "Execution contract Stage metadata or budget binding differs"
                )

            input_contracts = []
            for input_artifact in activation_stage.input_artifacts:
                producer_source_key = output_producer[input_artifact.artifact_id]
                producer_stage_keys = expected_stage_keys_by_source.get(
                    producer_source_key
                )
                payload = {
                    "source_artifact_id": input_artifact.artifact_id,
                    "kind": input_artifact.artifact_id,
                    "required": input_artifact.required,
                    "condition": input_artifact.condition,
                }
                if producer_stage_keys is not None:
                    bound_keys, instance_binding = (
                        _bind_selected_producer_instances(
                            producer_stage_keys=producer_stage_keys,
                            consumer_stage_keys=stage_keys,
                            instance_index=instance_index,
                        )
                    )
                    payload.update(
                        {
                            "resolution": "selected_stage_output",
                            "instance_binding": instance_binding,
                            "source_producer_stage_key": producer_source_key,
                            "producer_stage_keys": bound_keys,
                            "seed_artifact": None,
                        }
                    )
                else:
                    seed = seed_by_key.get(
                        (
                            source_stage.stage_key,
                            input_artifact.artifact_id,
                            producer_source_key,
                        )
                    )
                    if seed is None:
                        if input_artifact.required:
                            raise ValueError(
                                "Required successor Stage input lacks its Task seed"
                            )
                        payload.update(
                            {
                                "resolution": "optional_absent",
                                "instance_binding": "optional_absent",
                                "source_producer_stage_key": None,
                                "producer_stage_keys": [],
                                "seed_artifact": None,
                            }
                        )
                    else:
                        consumed_seed_keys.add(
                            (
                                source_stage.stage_key,
                                input_artifact.artifact_id,
                                producer_source_key,
                            )
                        )
                        seed_id = "seed:" + canonical_sha256(
                            {
                                "request_sha256": request.content_sha256,
                                "consumer_stage_key": source_stage.stage_key,
                                "artifact_id": seed.artifact_id,
                                "source_producer_stage_key": (
                                    seed.source_producer_stage_key
                                ),
                            }
                        )[:32]
                        payload.update(
                            {
                                "resolution": "hash_bound_task_seed",
                                "instance_binding": "task_seed",
                                "source_producer_stage_key": producer_source_key,
                                "producer_stage_keys": [],
                                "seed_artifact": {
                                    "artifact_id": seed_id,
                                    "version": 1,
                                    "sha256": seed.sha256,
                                    "kind": seed.artifact_id,
                                    "location": ArtifactLocation(
                                        repository_id=seed.repository_id,
                                        ref=seed.ref,
                                        commit_sha=seed.commit_sha,
                                        path=seed.path,
                                    ).model_dump(mode="json"),
                                },
                            }
                        )
                input_contracts.append(payload)

            output_contracts = [
                {
                    "source_output_id": output.output_id,
                    "kind": output.output_id,
                    "required": output.required,
                    "applicable_unit_kinds": output.applicable_unit_kinds,
                    "artifact_identity_strategy": (
                        "task_stage_run_template_sha256_v1"
                    ),
                }
                for output in activation_stage.output_artifacts
            ]
            production_evidence_contracts = []
            for requirement in activation_stage.gate_requirements:
                if not requirement.requirement_id.startswith(
                    source_stage.stage_key
                ):
                    raise ValueError(
                        "Activation Gate requirement lost its source Stage prefix"
                    )
                requirement_id = (
                    stage_key
                    + requirement.requirement_id[len(source_stage.stage_key) :]
                )
                source_review = requirement.source == "source_review"
                production_evidence_contracts.append(
                    {
                        "requirement": GateRequirement(
                            requirement_id=requirement_id,
                            title=(
                                f"{inventory_item.title}: "
                                f"{requirement.evidence_kind} "
                                f"({requirement.subject_id})"
                            )[:300],
                            repository_id=request.repository.repository_id,
                            ref=request.repository.ref,
                            commit_sha=request.repository.commit_sha,
                            evidence_kind=requirement.evidence_kind,
                            failure_action=requirement.failure_action,
                        ).model_dump(mode="json"),
                        "source": requirement.source,
                        "subject_id": requirement.subject_id,
                        "producer_responsibility": "production_execution",
                        "producer_runtime": production_runtime,
                        "source_reviewer_role": (
                            activation_stage.role_profile.source_reviewer_role
                            if source_review
                            else None
                        ),
                        "source_reviewer_max_iterations": (
                            activation_stage.role_profile.source_reviewer_max_iterations
                            if source_review
                            else 0
                        ),
                    }
                )

            gate_evidence_contracts = list(production_evidence_contracts)
            is_completion_stage = sequence == len(expected_stage_keys)
            if is_completion_stage:
                for responsibility, evidence_kind, title in (
                    (
                        "independent_correctness",
                        "independent-correctness-completion",
                        "Independent correctness completion review",
                    ),
                    (
                        "methodology_stewardship",
                        "methodology-stewardship-completion",
                        "Methodology stewardship completion review",
                    ),
                ):
                    gate_evidence_contracts.append(
                        {
                            "requirement": GateRequirement(
                                requirement_id=f"task-completion-{responsibility}",
                                title=title,
                                repository_id=request.repository.repository_id,
                                ref=request.repository.ref,
                                commit_sha=request.repository.commit_sha,
                                evidence_kind=evidence_kind,
                                failure_action="block_task_completion",
                            ).model_dump(mode="json"),
                            "source": "completion_review",
                            "subject_id": responsibility,
                            "producer_responsibility": responsibility,
                            "producer_runtime": pin_by_responsibility[
                                responsibility
                            ].runtime,
                            "source_reviewer_role": None,
                            "source_reviewer_max_iterations": 0,
                        }
                    )

            stages.append(
                {
                    "stage_key": stage_key,
                    "source_stage_key": source_stage.stage_key,
                    "gate_key": inventory_item.gate_key,
                    "sequence": sequence,
                    "instance_index": instance_index,
                    "instance_count": allocation.instance_count,
                    "title": inventory_item.title,
                    "role": "production_execution",
                    "runtime": production_runtime,
                    "source_role_profile": (
                        activation_stage.role_profile.model_dump(mode="json")
                    ),
                    "context": {
                        "context_pack_schema_version": "1.0",
                        "stage_contract": (
                            activation_stage.stage_contract.model_dump(mode="json")
                        ),
                        "source_inputs_text": activation_stage.source_inputs_text,
                        "input_contracts": input_contracts,
                        "output_contracts": output_contracts,
                        "sensors": [
                            sensor.model_dump(mode="json")
                            for sensor in activation_stage.sensors
                        ],
                        "forbidden_constraints": list(
                            EXECUTION_FORBIDDEN_CONSTRAINTS
                        ),
                        "budget": {
                            "max_seconds": timeout_seconds,
                            "max_output_bytes": max_output_bytes,
                            "max_model_tokens": (
                                allocation.max_run_token_reservation_per_instance
                            ),
                            "max_cost_usd": (
                                allocation.max_run_cost_reservation_per_instance_usd
                            ),
                        },
                    },
                    "handoff": {
                        "handoff_pack_schema_version": "1.0",
                        "producer_runtime": production_runtime,
                        "allowed_output_kinds": [
                            output.output_id
                            for output in activation_stage.output_artifacts
                        ],
                        "required_output_kinds": [
                            output.output_id
                            for output in activation_stage.output_artifacts
                            if output.required
                        ],
                        "evidence_contracts": production_evidence_contracts,
                        "exact_context_echo_required": True,
                        "unbound_output_allowed": False,
                        "native_state_authority": False,
                        "suggested_next_action_authority": False,
                        "format_only_repair_attempts": 1,
                    },
                    "gate": {
                        "gate_key": inventory_item.gate_key,
                        "evidence_contracts": gate_evidence_contracts,
                    },
                }
            )

    if consumed_seed_keys != set(seed_by_key):
        raise ValueError(
            "Execution contract Task seed set contains unused or missing bindings"
        )

    payload = {
        "schema_version": "1.0",
        "contract_id": f"methodology-execution:{task.task_id}",
        "materialized_at": materialized_at,
        "authenticated_principal_id": principal.principal_id,
        "authenticated_permission": "control_plane.approve",
        "project_id": task.project_id,
        "task_id": task.task_id,
        "task_version": task.version,
        "control_task_version": control_task.version,
        "plan_id": plan.plan_id,
        "plan_version": plan.version,
        "inventory_id": inventory.inventory_id,
        "inventory_sha256": inventory.content_sha256,
        "migration_request_id": request.request_id,
        "migration_request_sha256": request.content_sha256,
        "migration_gate_id": gate.gate_id,
        "migration_gate_sha256": gate.content_sha256,
        "migration_receipt_id": receipt.receipt_id,
        "migration_receipt_sha256": receipt.content_sha256,
        "repository": request.repository.model_dump(mode="json"),
        "activation_id": activation.activation_id,
        "methodology_id": activation.methodology_id,
        "methodology_version": activation.methodology_version,
        "source_graph_sha256": source_graph.content_sha256,
        "activation_definition_sha256": activation.content_sha256,
        "selected_scope": request.selected_scope,
        "runtime_pins": [
            pin.model_dump(mode="json") for pin in runtime_pins
        ],
        "stages": stages,
        "completion_stage_key": expected_stage_keys[-1],
        "approval_policy": activation.approval_policy.model_dump(mode="json"),
        "source_profiles_are_routing_authority": False,
        "route_activated": False,
        "runtime_spawned": False,
        "routing_authority": False,
        "dispatch_authority": False,
    }
    return MethodologyExecutionContract.model_validate(
        seal_model_payload(MethodologyExecutionContract, payload)
    )
