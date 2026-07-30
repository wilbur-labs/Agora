"""Pure materialization for authenticated AWS AI-DLC successor Tasks."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agora.control_plane.auth import ControlPrincipal
from agora.protocol.hashing import seal_model_payload
from agora.protocol.methodology_migration import (
    AuthenticatedMethodologyMigrationGate,
    MethodologyMigrationPreviewDecision,
    MethodologyMigrationPreviewRequest,
)
from agora.protocol.models import StageInventory
from agora.tasks.models import TaskManifest

from .aws_aidlc import AWS_AIDLC_V2_3_SOURCE_GRAPH
from .aws_aidlc_activation import AWS_AIDLC_V2_3_ACTIVATION_DEFINITION


@dataclass(frozen=True)
class SuccessorStagePlan:
    stage_key: str
    source_stage_key: str
    phase: str
    title: str
    role: str
    runtime: str
    token_budget: int
    cost_budget_usd: float | None


@dataclass(frozen=True)
class MethodologySuccessorMaterialization:
    recheck_decision: MethodologyMigrationPreviewDecision
    authenticated_gate: AuthenticatedMethodologyMigrationGate
    inventory: StageInventory
    stages: tuple[SuccessorStagePlan, ...]
    methodology_payload: dict[str, Any]
    task_metadata: dict[str, Any]
    task_title: str
    task_description: str
    task_kind: str
    task_risk: str
    task_priority: int
    task_primary_agent: str
    task_reviewers: tuple[str, ...]
    task_acceptance: tuple[str, ...]


def authenticate_methodology_migration_gate(
    request: MethodologyMigrationPreviewRequest,
    principal: ControlPrincipal,
    *,
    persisted_at: datetime | str,
) -> AuthenticatedMethodologyMigrationGate:
    """Bind the asserted approver to one live Control Plane principal."""

    assertion = request.human_gate
    if assertion is None:
        raise ValueError("Methodology migration requires a human Gate assertion")
    if "control_plane.approve" not in principal.permissions:
        raise ValueError(
            "Authenticated principal lacks control_plane.approve permission"
        )
    if request.project_id not in principal.projects:
        raise ValueError(
            "Authenticated principal is not authorized for the migration project"
        )
    if principal.principal_id != assertion.approved_by:
        raise ValueError(
            "Authenticated principal does not match the migration Gate approver"
        )
    payload = {
        "schema_version": "1.0",
        "gate_id": assertion.assertion_id,
        "assertion": assertion.model_dump(mode="json"),
        "assertion_sha256": assertion.content_sha256,
        "authenticated_principal_id": principal.principal_id,
        "authenticated_permission": "control_plane.approve",
        "authenticated_project_id": request.project_id,
        "credential_verified": True,
        "persisted_at": persisted_at,
    }
    return AuthenticatedMethodologyMigrationGate.model_validate(
        seal_model_payload(AuthenticatedMethodologyMigrationGate, payload)
    )


def build_methodology_successor_materialization(
    *,
    source_task: TaskManifest,
    request: MethodologyMigrationPreviewRequest,
    recheck_decision: MethodologyMigrationPreviewDecision,
    principal: ControlPrincipal,
    successor_task_id: str,
    successor_plan_id: str,
    activated_at: datetime | str,
) -> MethodologySuccessorMaterialization:
    """Build the exact Plan/inventory payload after an eligible live recheck."""

    if not recheck_decision.eligible or recheck_decision.blockers:
        raise ValueError("Successor materialization requires an eligible recheck")
    if (
        recheck_decision.request_id != request.request_id
        or recheck_decision.request_sha256 != request.content_sha256
        or recheck_decision.task_id != source_task.task_id
    ):
        raise ValueError("Successor materialization recheck binding differs")

    activation = AWS_AIDLC_V2_3_ACTIVATION_DEFINITION
    source_graph = AWS_AIDLC_V2_3_SOURCE_GRAPH
    gate = authenticate_methodology_migration_gate(
        request,
        principal,
        persisted_at=activated_at,
    )
    runtime_by_responsibility = {
        pin.responsibility: pin.runtime for pin in request.runtime_pins
    }
    expected_responsibilities = {
        "production_execution",
        "independent_correctness",
        "methodology_stewardship",
    }
    if set(runtime_by_responsibility) != expected_responsibilities:
        raise ValueError("Successor migration requires all three runtime pins")
    allocation_by_stage = {
        item.source_stage_key: item for item in request.budget.stage_allocations
    }
    selected_source_stages = [
        stage
        for stage in source_graph.stages
        if request.selected_scope in stage.scopes
    ]
    if set(allocation_by_stage) != {
        stage.stage_key for stage in selected_source_stages
    }:
        raise ValueError("Successor Stage allocations do not match the selected scope")
    activation_by_stage = {
        stage.source_stage_key: stage for stage in activation.stages
    }

    production_runtime = runtime_by_responsibility["production_execution"]
    planned_stages: list[SuccessorStagePlan] = []
    for source_stage in selected_source_stages:
        allocation = allocation_by_stage[source_stage.stage_key]
        activation_stage = activation_by_stage[source_stage.stage_key]
        for instance in range(1, allocation.instance_count + 1):
            expanded = allocation.instance_count > 1
            stage_key = (
                f"{source_stage.stage_key}-unit-{instance:03d}"
                if expanded
                else source_stage.stage_key
            )
            title = (
                f"{activation_stage.stage_contract.title} "
                f"(unit {instance} of {allocation.instance_count})"
                if expanded
                else activation_stage.stage_contract.title
            )
            planned_stages.append(
                SuccessorStagePlan(
                    stage_key=stage_key,
                    source_stage_key=source_stage.stage_key,
                    phase=source_stage.phase,
                    title=title,
                    role="production_execution",
                    runtime=production_runtime,
                    token_budget=allocation.token_allocation_per_instance,
                    cost_budget_usd=allocation.cost_allocation_per_instance_usd,
                )
            )

    groups = []
    for phase in source_graph.phases:
        phase_stages = [
            stage for stage in planned_stages if stage.phase == phase
        ]
        if not phase_stages:
            continue
        groups.append(
            {
                "group_key": f"phase:{phase}",
                "sequence": len(groups) + 1,
                "title": f"AWS AI-DLC {phase} phase",
                "stages": [
                    {
                        "stage_key": stage.stage_key,
                        "gate_key": f"gate:{stage.stage_key}",
                        "sequence": sequence,
                        "title": stage.title,
                        "role": stage.role,
                        "runtime": stage.runtime,
                    }
                    for sequence, stage in enumerate(phase_stages, start=1)
                ],
            }
        )
    inventory_payload = {
        "schema_version": "1.0",
        "inventory_id": f"inventory:{successor_plan_id}",
        "task_id": successor_task_id,
        "project_id": source_task.project_id,
        "plan_id": successor_plan_id,
        "methodology_id": activation.methodology_id,
        "methodology_version": activation.methodology_version,
        "methodology_sha256": activation.content_sha256,
        "provisional": False,
        "contract": None,
        "groups": groups,
    }
    inventory = StageInventory.model_validate(
        seal_model_payload(StageInventory, inventory_payload)
    )
    task_metadata = {
        "methodology": (
            f"{activation.methodology_id}@{activation.methodology_version}"
        ),
        "methodology_provisional": False,
        "methodology_activation_id": activation.activation_id,
        "methodology_activation_sha256": activation.content_sha256,
        "methodology_source_graph_sha256": activation.source_graph_sha256,
        "methodology_selected_scope": request.selected_scope,
        "methodology_migration_request_id": request.request_id,
        "methodology_migration_request_sha256": request.content_sha256,
        "methodology_migration_gate_id": gate.gate_id,
        "methodology_migration_gate_sha256": gate.content_sha256,
        "methodology_predecessor_task_id": source_task.task_id,
        "methodology_route_activated": False,
        "methodology_dispatch_authority": False,
        "methodology_stage_instance_key_version": "1.0",
    }
    return MethodologySuccessorMaterialization(
        recheck_decision=recheck_decision,
        authenticated_gate=gate,
        inventory=inventory,
        stages=tuple(planned_stages),
        methodology_payload=activation.model_dump(mode="json"),
        task_metadata=task_metadata,
        task_title=source_task.title,
        task_description=source_task.description,
        task_kind="aws_aidlc_successor",
        task_risk=source_task.risk.value,
        task_priority=source_task.priority,
        task_primary_agent=production_runtime,
        task_reviewers=(
            runtime_by_responsibility["independent_correctness"],
            runtime_by_responsibility["methodology_stewardship"],
        ),
        task_acceptance=(
            "Every selected AWS AI-DLC Stage Gate passes against version-bound evidence",
            "Independent correctness and methodology completion Gates pass",
            "Human completion approval is recorded",
        ),
    )
