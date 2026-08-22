"""Advance a task's lifecycle when a human settles an auto cross-review.

The dispatcher raises an ``approval`` (or ``blocker``) attention tagged with
``REVIEW_REQUESTER`` once a review run finishes. When the human responds to that
item, the work should move on without a second manual click: an approval carries
the task to ``verified``; a rejection sends it back to ``blocked`` for rework.

This is intentionally best-effort. The human's response is already committed by
the time we run, so a task that cannot advance (unexpected state, concurrent
edit, missing task) is left untouched rather than failing the response.
"""
from __future__ import annotations

from .models import AttentionItem, AttentionKind, ResponseAction, REVIEW_REQUESTER
from agora.tasks.models import TaskManifest, TaskState
from agora.tasks.store import (
    InvalidTransitionError,
    StaleTaskVersionError,
    TaskNotFoundError,
    TaskStore,
)

# Steps taken on approval. A review run does not itself move the task past
# ``running`` (queueing only bumps planned -> running), so an approval usually
# walks running -> review -> verified. We stop at ``verified`` — reviewed and
# accepted — leaving the terminal ``done`` for an explicit handoff step.
_APPROVE_PATH = (TaskState.REVIEW, TaskState.VERIFIED)


def settle_review_approval(
    task_store: TaskStore,
    item: AttentionItem,
    action: ResponseAction,
    *,
    actor: str,
) -> TaskManifest | None:
    """Advance the reviewed task after a human settles a cross-review item.

    Returns the updated manifest if the task moved, else ``None`` (item is not a
    cross-review approval, the action is not approve/reject, the task is gone, or
    it was already past the point this gate would advance it to).
    """
    if item.requester != REVIEW_REQUESTER or item.kind != AttentionKind.APPROVAL:
        return None
    if action not in (ResponseAction.APPROVE, ResponseAction.REJECT):
        return None
    try:
        task = task_store.get(item.task_id)
    except TaskNotFoundError:
        return None
    if task is None:
        return None

    verdict = "approved" if action is ResponseAction.APPROVE else "rejected"
    reason = f"Cross-review {verdict} by {actor} (attention {item.item_id})"
    targets = _APPROVE_PATH if action is ResponseAction.APPROVE else (TaskState.BLOCKED,)

    current = task
    moved = False
    for target in targets:
        if current.state == target:
            continue
        try:
            current = task_store.transition(
                item.task_id,
                target,
                actor=actor,
                reason=reason,
                expected_version=current.version,
            )
        except (InvalidTransitionError, StaleTaskVersionError, TaskNotFoundError):
            break
        moved = True
    return current if moved else None
