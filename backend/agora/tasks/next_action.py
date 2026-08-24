"""The deterministic Next Action engine — one explicit next step per task, and
the single most important next step for the whole project.

The point of the control plane is that a human always knows *what to do next*
without reading the whole board. For every task this derives exactly one next
action from its lifecycle state and a few task-level signals (its spec state,
whether clarifying questions are still unanswered, whether a worker run has
succeeded). Each action carries:

- owner     — who must act: ``human`` (the user), ``agent`` (work is running,
              wait), or ``none`` (terminal, nothing to do).
- action    — a stable machine slug the UI localizes (see ``ACTIONS``). English
              ``summary`` is a fallback for API consumers without the UI.
- attention — True only when the task is stuck (blocked/failed) and needs a
              human to unstick it, matching the project ``health`` metric.

Like ``progress.py`` this is pure and derives from signals only — no I/O, no
store access — so the same inputs always yield the same next action.
"""
from __future__ import annotations

from typing import Any

from .models import TaskState

try:  # SpecState is only used for type parity; avoid a hard import cycle.
    from agora.requirements.models import SpecState
except Exception:  # pragma: no cover - defensive
    SpecState = None  # type: ignore[assignment]

# Every action slug the engine can emit, with an English fallback summary. The
# UI maps the slug to a localized label; keep this the single source of truth.
ACTIONS: dict[str, str] = {
    "none": "Nothing to do — the task is complete or cancelled.",
    "resolve_blocker": "Task is blocked — resolve the blocker to continue.",
    "investigate_failure": "A run failed — investigate, then retry or cancel.",
    "start_requirements": "Start requirements for this task.",
    "answer_clarifications": "Answer the open clarifying questions.",
    "draft_spec": "Draft the requirement spec.",
    "approve_spec": "Review and approve the draft spec.",
    "revise_spec": "Revise the rejected spec.",
    "advance_to_design": "Spec approved — move the task to design.",
    "advance_to_plan": "Finish the design and plan the work.",
    "start_run": "Start the worker run.",
    "await_run": "A worker run is in progress — waiting for it to finish.",
    "verify_review": "Review the result and verify the task.",
    "mark_done": "Verified — mark the task done.",
}

# Selection order when rolling up to one project-wide next action. Higher wins.
# Attention (blocked/failed) outranks every routine step; a run in progress is
# the lowest actionable item because it needs no human right now.
_PRIORITY: dict[str, int] = {
    "resolve_blocker": 100,
    "investigate_failure": 100,
    "answer_clarifications": 80,
    "approve_spec": 78,
    "verify_review": 76,
    "mark_done": 74,
    "revise_spec": 72,
    "start_run": 60,
    "advance_to_plan": 58,
    "advance_to_design": 56,
    "draft_spec": 54,
    "start_requirements": 52,
    "await_run": 20,
    "none": 0,
}


def has_pending_clarifications(clarifications: Any) -> bool:
    """True when clarifying questions exist and at least one is unanswered."""
    if not clarifications:
        return False
    questions = clarifications.get("questions") or []
    if not questions:
        return False
    answers = clarifications.get("answers") or {}
    for question in questions:
        qid = str(question.get("id") or "")
        answer = answers.get(qid)
        if not (answer and str(answer).strip()):
            return True
    return False


def _action(owner: str, action: str, *, attention: bool = False) -> dict[str, Any]:
    return {
        "owner": owner,
        "action": action,
        "attention": attention,
        "summary": ACTIONS[action],
    }


def task_next_action(
    task_state: TaskState,
    *,
    spec_state: Any = None,
    pending_clarifications: bool = False,
    has_worker_success: bool = False,
) -> dict[str, Any]:
    """The single next step for one task, derived from its state and signals.

    ``spec_state`` is the current spec's ``SpecState`` (or None if the task has
    no spec yet) — any state, not only APPROVED, so requirements can route a
    draft to approval or a rejection to revision.
    """
    if task_state == TaskState.CANCELLED or task_state == TaskState.DONE:
        return _action("none", "none")
    if task_state == TaskState.FAILED:
        return _action("human", "investigate_failure", attention=True)
    if task_state == TaskState.BLOCKED:
        return _action("human", "resolve_blocker", attention=True)
    if task_state == TaskState.BACKLOG:
        return _action("human", "start_requirements")
    if task_state == TaskState.REQUIREMENTS:
        if pending_clarifications:
            return _action("human", "answer_clarifications")
        state_value = getattr(spec_state, "value", spec_state)
        if state_value is None:
            return _action("human", "draft_spec")
        if state_value == "draft":
            return _action("human", "approve_spec")
        if state_value == "approved":
            return _action("human", "advance_to_design")
        # rejected / superseded — a fresh draft is needed.
        return _action("human", "revise_spec")
    if task_state == TaskState.DESIGN:
        return _action("human", "advance_to_plan")
    if task_state == TaskState.PLANNED:
        return _action("human", "start_run")
    if task_state == TaskState.RUNNING:
        return _action("agent", "await_run")
    if task_state == TaskState.REVIEW:
        return _action("human", "verify_review")
    if task_state == TaskState.VERIFIED:
        return _action("human", "mark_done")
    # Unknown/unhandled state — fail safe to nothing rather than guess.
    return _action("none", "none")  # pragma: no cover


def project_next_action(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll per-task next actions up to one project-wide next action.

    ``items`` is a list of ``{"task_id", "title", "priority", "next_action"}``.
    The winner is the highest-priority action; ties break toward the higher task
    priority, then the earlier task (list order, which the store returns newest
    last within a page — callers pass tasks in a stable order). Also reports how
    many tasks need attention and how many await a human, for the cockpit header.
    """
    attention_count = sum(1 for it in items if it["next_action"]["attention"])
    pending_count = sum(1 for it in items if it["next_action"]["owner"] == "human")

    best: dict[str, Any] | None = None
    best_key: tuple[int, int] | None = None
    for index, it in enumerate(items):
        action = it["next_action"]["action"]
        # Rank first by action priority, then by task priority (higher first).
        # Negate the index so an earlier task wins an otherwise exact tie.
        key = (_PRIORITY.get(action, 0), int(it.get("priority", 0)), -index)
        if action == "none":
            continue
        if best_key is None or key > best_key:
            best_key = key
            best = it

    if best is None:
        result = _action("none", "none")
        result.update({"task_id": None, "title": None})
    else:
        result = dict(best["next_action"])
        result.update({"task_id": best["task_id"], "title": best["title"]})
    result["attention_count"] = attention_count
    result["pending_count"] = pending_count
    return result
