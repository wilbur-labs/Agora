"""Deterministic frozen Task lifecycle derivation."""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence

from agora.protocol.state_machines import (
    TASK_TRANSITIONS,
    GateStatus,
    StageStatus,
    TaskStatus,
)

from .models import TaskLifecycleDecision, TaskLifecycleReason


class TaskLifecycleDerivationError(ValueError):
    pass


def derive_task_lifecycle(
    *,
    current_status: TaskStatus,
    inventory_id: str,
    inventory_sha256: str,
    inventory_stage_keys: Sequence[str],
    stage_statuses: Mapping[str, StageStatus],
    gate_statuses: Mapping[str, GateStatus],
    open_blockers: int = 0,
    open_questions: int = 0,
    open_approvals: int = 0,
) -> TaskLifecycleDecision:
    """Derive one Task target from complete authoritative lifecycle inputs."""

    inventory_keys = tuple(inventory_stage_keys)
    if not inventory_keys or len(inventory_keys) != len(set(inventory_keys)):
        raise TaskLifecycleDerivationError(
            "Task lifecycle requires a non-empty unique Stage inventory"
        )
    inventory_set = set(inventory_keys)
    if not set(stage_statuses).issubset(inventory_set):
        raise TaskLifecycleDerivationError(
            "Formal Stage exists outside the immutable Task Stage inventory"
        )
    if not set(gate_statuses).issubset(inventory_set):
        raise TaskLifecycleDerivationError(
            "Formal Gate exists outside the immutable Task Stage inventory"
        )
    for stage_key, status in stage_statuses.items():
        if (
            status == StageStatus.COMPLETED
            and gate_statuses.get(stage_key) != GateStatus.PASSED
        ):
            raise TaskLifecycleDerivationError(
                f"Completed Stage {stage_key} does not have a passed formal Gate"
            )

    statuses = set(stage_statuses.values())
    gate_values = set(gate_statuses.values())
    completed = sum(
        status == StageStatus.COMPLETED for status in stage_statuses.values()
    )

    def decision(target: TaskStatus, reason: TaskLifecycleReason):
        return TaskLifecycleDecision(
            target_status=target,
            reason=reason,
            inventory_id=inventory_id,
            inventory_sha256=inventory_sha256,
            total_stages=len(inventory_keys),
            formal_stages=len(stage_statuses),
            completed_stages=completed,
            open_blockers=open_blockers,
            open_questions=open_questions,
            open_approvals=open_approvals,
        )

    if current_status == TaskStatus.CANCELLED:
        return decision(TaskStatus.CANCELLED, TaskLifecycleReason.EXPLICIT_CANCELLATION)
    if StageStatus.CANCELLED in statuses:
        return decision(TaskStatus.CANCELLED, TaskLifecycleReason.STAGE_CANCELLED)
    if StageStatus.FAILED in statuses:
        return decision(TaskStatus.FAILED, TaskLifecycleReason.STAGE_FAILED)
    if StageStatus.RECONCILIATION_REQUIRED in statuses:
        return decision(
            TaskStatus.BLOCKED,
            TaskLifecycleReason.RECONCILIATION_REQUIRED,
        )
    if GateStatus.STALE in gate_values:
        return decision(TaskStatus.BLOCKED, TaskLifecycleReason.INVALIDATION_REQUIRED)
    if open_blockers or open_questions:
        return decision(TaskStatus.BLOCKED, TaskLifecycleReason.BLOCKING_ATTENTION)
    if StageStatus.BLOCKED in statuses or GateStatus.BLOCKED in gate_values:
        return decision(TaskStatus.BLOCKED, TaskLifecycleReason.STAGE_OR_GATE_BLOCKED)
    if (
        StageStatus.NEEDS_REVIEW in statuses
        or GateStatus.EVALUATING in gate_values
        or open_approvals
    ):
        return decision(TaskStatus.NEEDS_REVIEW, TaskLifecycleReason.REVIEW_REQUIRED)

    all_completed = (
        len(stage_statuses) == len(inventory_keys)
        and completed == len(inventory_keys)
        and len(gate_statuses) == len(inventory_keys)
        and all(status == GateStatus.PASSED for status in gate_statuses.values())
    )
    if all_completed:
        if current_status == TaskStatus.COMPLETED:
            return decision(
                TaskStatus.COMPLETED,
                TaskLifecycleReason.EXPLICIT_COMPLETION,
            )
        return decision(TaskStatus.NEEDS_REVIEW, TaskLifecycleReason.ALL_STAGES_PASSED)
    if (
        StageStatus.RUNNING in statuses
        or completed
        or GateStatus.PASSED in gate_values
    ):
        return decision(TaskStatus.ACTIVE, TaskLifecycleReason.WORK_ACTIVE)
    return decision(TaskStatus.READY, TaskLifecycleReason.INVENTORY_READY)


def task_transition_path(
    current: TaskStatus,
    target: TaskStatus,
) -> list[TaskStatus]:
    """Return the deterministic shortest legal frozen Task transition path."""

    if current == target:
        return []
    queue = deque([(current, [])])
    visited = {current}
    while queue:
        status, path = queue.popleft()
        for candidate in sorted(TASK_TRANSITIONS[status], key=lambda item: item.value):
            if candidate in visited:
                continue
            next_path = [*path, candidate]
            if candidate == target:
                return next_path
            visited.add(candidate)
            queue.append((candidate, next_path))
    raise TaskLifecycleDerivationError(
        f"No legal frozen Task transition path from {current.value} to {target.value}"
    )
