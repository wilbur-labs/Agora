"""Deterministic Stage routing over one sealed grouped inventory."""
from __future__ import annotations

from collections.abc import Mapping

from agora.protocol.models import StageInventory
from agora.protocol.state_machines import GateStatus, StageStatus

from .models import StageRouteDecision


class StageRoutingError(ValueError):
    pass


def derive_stage_route(
    *,
    inventory: StageInventory,
    stage_statuses: Mapping[str, StageStatus],
    stage_gate_keys: Mapping[str, str],
    gate_statuses: Mapping[str, GateStatus],
    gate_keys: Mapping[str, str],
    task_dispatchable: bool,
) -> StageRouteDecision | None:
    """Select the first incomplete inventory Stage without mutating state."""

    ordered = [
        (group, stage)
        for group in inventory.groups
        for stage in group.stages
    ]
    inventory_keys = {stage.stage_key for _, stage in ordered}
    if not set(stage_statuses).issubset(inventory_keys):
        raise StageRoutingError(
            "Formal Stage exists outside the immutable Task Stage inventory"
        )
    if set(stage_gate_keys) != set(stage_statuses):
        raise StageRoutingError("Formal Stage routing metadata is incomplete")
    if not set(gate_statuses).issubset(set(stage_statuses)):
        raise StageRoutingError(
            "Formal Gate is missing its authoritative Stage"
        )
    if set(gate_keys) != set(gate_statuses):
        raise StageRoutingError("Formal Gate routing metadata is incomplete")

    first_incomplete = None
    incomplete_seen = False
    for position, (group, stage) in enumerate(ordered, start=1):
        status = stage_statuses.get(stage.stage_key)
        gate_status = gate_statuses.get(stage.stage_key)
        stage_gate_key = stage_gate_keys.get(stage.stage_key)
        gate_key = gate_keys.get(stage.stage_key)
        if stage_gate_key is not None and stage_gate_key != stage.gate_key:
            raise StageRoutingError(
                f"Formal Stage {stage.stage_key} is bound to a different Gate"
            )
        if gate_key is not None and gate_key != stage.gate_key:
            raise StageRoutingError(
                f"Formal Gate for Stage {stage.stage_key} does not match the inventory"
            )
        if status == StageStatus.COMPLETED:
            if gate_status != GateStatus.PASSED:
                raise StageRoutingError(
                    f"Completed Stage {stage.stage_key} does not have a passed formal Gate"
                )
            if incomplete_seen:
                raise StageRoutingError(
                    "Completed formal Stages must form an ordered inventory prefix"
                )
            continue
        incomplete_seen = True
        if first_incomplete is None:
            first_incomplete = (position, group, stage, status, gate_status)
            continue
        if status not in {None, StageStatus.PENDING}:
            raise StageRoutingError(
                "A later inventory Stage became active before the routed Stage completed"
            )

    if first_incomplete is None:
        return None
    position, group, stage, status, gate_status = first_incomplete
    return StageRouteDecision(
        task_id=inventory.task_id,
        project_id=inventory.project_id,
        inventory_id=inventory.inventory_id,
        inventory_sha256=inventory.content_sha256,
        group_key=group.group_key,
        group_sequence=group.sequence,
        stage_key=stage.stage_key,
        gate_key=stage.gate_key,
        stage_sequence=stage.sequence,
        inventory_sequence=position,
        title=stage.title,
        role=stage.role,
        runtime=stage.runtime,
        stage_status=status,
        gate_status=gate_status,
        runnable=status == StageStatus.READY and task_dispatchable,
    )
