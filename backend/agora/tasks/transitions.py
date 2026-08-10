"""Lifecycle rules for delivery tasks."""
from __future__ import annotations

from .models import TaskState


TERMINAL_STATES = {TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED}

ALLOWED_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.BACKLOG: {TaskState.REQUIREMENTS, TaskState.CANCELLED},
    TaskState.REQUIREMENTS: {TaskState.DESIGN, TaskState.BLOCKED, TaskState.CANCELLED},
    TaskState.DESIGN: {
        TaskState.REQUIREMENTS,
        TaskState.PLANNED,
        TaskState.BLOCKED,
        TaskState.CANCELLED,
    },
    TaskState.PLANNED: {
        TaskState.DESIGN,
        TaskState.RUNNING,
        TaskState.BLOCKED,
        TaskState.CANCELLED,
    },
    TaskState.RUNNING: {
        TaskState.BLOCKED,
        TaskState.REVIEW,
        TaskState.FAILED,
        TaskState.CANCELLED,
    },
    TaskState.BLOCKED: {
        TaskState.REQUIREMENTS,
        TaskState.DESIGN,
        TaskState.PLANNED,
        TaskState.RUNNING,
        TaskState.FAILED,
        TaskState.CANCELLED,
    },
    TaskState.REVIEW: {
        TaskState.RUNNING,
        TaskState.VERIFIED,
        TaskState.FAILED,
        TaskState.CANCELLED,
    },
    TaskState.VERIFIED: {TaskState.RUNNING, TaskState.DONE, TaskState.CANCELLED},
    # Finished tasks may be cancelled to remove/retire them from the active
    # board (a soft, audited transition — the record is retained). `cancelled`
    # itself remains fully terminal.
    TaskState.DONE: {TaskState.CANCELLED},
    TaskState.FAILED: {TaskState.CANCELLED},
    TaskState.CANCELLED: set(),
}


def can_transition(current: TaskState, target: TaskState) -> bool:
    return target in ALLOWED_TRANSITIONS[current]
