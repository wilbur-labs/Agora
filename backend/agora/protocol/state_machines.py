"""Pure transition guards for the Agora 1.x domain."""
from __future__ import annotations

from enum import Enum


class TransitionError(ValueError):
    pass


class TaskStatus(str, Enum):
    BACKLOG = "backlog"
    READY = "ready"
    ACTIVE = "active"
    BLOCKED = "blocked"
    NEEDS_REVIEW = "needs_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    BLOCKED = "blocked"
    NEEDS_REVIEW = "needs_review"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class GateStatus(str, Enum):
    PENDING = "pending"
    EVALUATING = "evaluating"
    PASSED = "passed"
    BLOCKED = "blocked"
    STALE = "stale"


TASK_TRANSITIONS = {
    TaskStatus.BACKLOG: {TaskStatus.READY, TaskStatus.CANCELLED},
    TaskStatus.READY: {TaskStatus.ACTIVE, TaskStatus.BLOCKED, TaskStatus.CANCELLED},
    TaskStatus.ACTIVE: {
        TaskStatus.BLOCKED,
        TaskStatus.NEEDS_REVIEW,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.BLOCKED: {
        TaskStatus.READY,
        TaskStatus.ACTIVE,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.NEEDS_REVIEW: {
        TaskStatus.ACTIVE,
        TaskStatus.BLOCKED,
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.COMPLETED: {TaskStatus.ACTIVE},
    TaskStatus.FAILED: {TaskStatus.READY, TaskStatus.CANCELLED},
    TaskStatus.CANCELLED: set(),
}

STAGE_TRANSITIONS = {
    StageStatus.PENDING: {StageStatus.READY, StageStatus.CANCELLED},
    StageStatus.READY: {
        StageStatus.RUNNING,
        StageStatus.BLOCKED,
        StageStatus.CANCELLED,
    },
    StageStatus.RUNNING: {
        StageStatus.BLOCKED,
        StageStatus.NEEDS_REVIEW,
        StageStatus.RECONCILIATION_REQUIRED,
        StageStatus.COMPLETED,
        StageStatus.FAILED,
        StageStatus.CANCELLED,
    },
    StageStatus.BLOCKED: {
        StageStatus.READY,
        StageStatus.RUNNING,
        StageStatus.RECONCILIATION_REQUIRED,
        StageStatus.FAILED,
        StageStatus.CANCELLED,
    },
    StageStatus.NEEDS_REVIEW: {
        StageStatus.RUNNING,
        StageStatus.BLOCKED,
        StageStatus.COMPLETED,
        StageStatus.CANCELLED,
    },
    StageStatus.RECONCILIATION_REQUIRED: {
        StageStatus.READY,
        StageStatus.RUNNING,
        StageStatus.BLOCKED,
        StageStatus.CANCELLED,
    },
    StageStatus.COMPLETED: {StageStatus.READY},
    StageStatus.FAILED: {StageStatus.READY, StageStatus.CANCELLED},
    StageStatus.CANCELLED: set(),
}

GATE_TRANSITIONS = {
    GateStatus.PENDING: {GateStatus.EVALUATING, GateStatus.BLOCKED},
    GateStatus.EVALUATING: {GateStatus.PASSED, GateStatus.BLOCKED},
    GateStatus.PASSED: {GateStatus.STALE},
    GateStatus.BLOCKED: {GateStatus.EVALUATING},
    GateStatus.STALE: {GateStatus.EVALUATING, GateStatus.BLOCKED},
}


def _transition(current: Enum, target: Enum, transitions: dict[Enum, set[Enum]]) -> Enum:
    if target not in transitions[current]:
        raise TransitionError(f"invalid transition: {current.value} -> {target.value}")
    return target


def transition_task(current: TaskStatus, target: TaskStatus) -> TaskStatus:
    return _transition(current, target, TASK_TRANSITIONS)  # type: ignore[return-value]


def transition_stage(current: StageStatus, target: StageStatus) -> StageStatus:
    return _transition(current, target, STAGE_TRANSITIONS)  # type: ignore[return-value]


def transition_gate(current: GateStatus, target: GateStatus) -> GateStatus:
    return _transition(current, target, GATE_TRANSITIONS)  # type: ignore[return-value]
