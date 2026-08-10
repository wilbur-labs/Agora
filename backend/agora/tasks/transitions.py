"""Lifecycle rules for delivery tasks."""
from __future__ import annotations

from .models import TaskState


# States with no outgoing transitions. Removing a finished task from the board
# is a display-only `archived_at` flag (see TaskStore.set_archived), NOT a
# lifecycle transition, so these stay terminal and the authoritative and
# compatibility states never diverge. A graph-consistency test asserts this set
# matches ALLOWED_TRANSITIONS.
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
    TaskState.DONE: set(),
    TaskState.FAILED: set(),
    TaskState.CANCELLED: set(),
}


def can_transition(current: TaskState, target: TaskState) -> bool:
    # Fail closed: an unregistered current state has no permitted transitions.
    return target in ALLOWED_TRANSITIONS.get(current, set())
