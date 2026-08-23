"""Progress vs verified-completion vs health — the metrics that let a human see
where a project really is.

Deliberately split three ways so an agent producing a lot of code can never make
a project *look* done:

- progress   — how much has been PRODUCED (a worker run succeeded and beyond).
- completion — how much has been VERIFIED (produced, reviewed, and the human
               approved). Only verified weight counts toward completion.
- health     — normal, or needs-attention when something is blocked/failed.

1.2 computes a criterion's state from task-level signals (its runs + task state),
so every criterion of one task shares that task's state. 1.3's Result Envelope
will make evidence per-criterion; the metric definitions here do not change.
"""
from __future__ import annotations

from typing import Any

from .models import TaskState

# A criterion advances pending -> produced -> reviewed -> verified.
CRITERION_STATES = ("pending", "produced", "reviewed", "verified")
_PRODUCED_OR_BEYOND = {"produced", "reviewed", "verified"}

# Task states that no longer count as active work needing attention.
_TERMINAL = {TaskState.DONE, TaskState.VERIFIED, TaskState.CANCELLED, TaskState.FAILED}


def derive_criteria(spec: Any) -> list[dict[str, Any]]:
    """One acceptance criterion per approved-spec acceptance scenario (equal
    weight in 1.2). Returns [] when the task has no spec yet."""
    if spec is None:
        return []
    criteria: list[dict[str, Any]] = []
    for scenario in getattr(spec, "acceptance_scenarios", []):
        criteria.append({
            "id": scenario.scenario_id,
            "description": f"{scenario.given} → {scenario.when} → {scenario.then}",
            "weight": 1,
        })
    return criteria


def task_criterion_status(
    task_state: TaskState, has_worker_success: bool, has_review_success: bool
) -> str:
    """The state shared by a task's criteria, derived from its signals (1.2).

    A terminal verified/done task counts as verified. Otherwise nothing is
    "produced" or "reviewed" without an actual successful worker run — a review
    (or the review state) alone never manufactures produced weight."""
    if task_state in (TaskState.VERIFIED, TaskState.DONE):
        return "verified"
    if not has_worker_success:
        return "pending"
    if has_review_success or task_state == TaskState.REVIEW:
        return "reviewed"
    return "produced"


def task_metrics(
    task_state: TaskState,
    spec: Any,
    *,
    has_worker_success: bool,
    has_review_success: bool,
) -> dict[str, Any]:
    """Criteria (each tagged with the task-derived status) and weight rollups."""
    criteria = derive_criteria(spec)
    status = task_criterion_status(task_state, has_worker_success, has_review_success)
    for c in criteria:
        c["status"] = status
    # A task with no spec still counts as one implicit unit, so per-task and
    # project rollups reconcile (an unspecified task is not invisible).
    total = sum(c["weight"] for c in criteria) if criteria else 1
    produced = total if status in _PRODUCED_OR_BEYOND else 0
    verified = total if status == "verified" else 0
    return {
        "status": status,
        "criteria": criteria,
        "has_spec": bool(criteria),
        "total_weight": total,
        "produced_weight": produced,
        "verified_weight": verified,
    }


def _pct(numerator: int, denominator: int) -> int:
    # Conventional half-up rounding (Python's round() is banker's rounding).
    return int(numerator * 100 / denominator + 0.5) if denominator else 0


def project_metrics(task_metric_list: list[dict[str, Any]], task_states: list[TaskState]) -> dict[str, Any]:
    """Aggregate task metrics into project progress / verified completion / health.

    Cancelled tasks are out of scope and excluded from the denominator; every
    task carries a self-consistent weight (>=1) from task_metrics."""
    total = produced = verified = 0
    for m, state in zip(task_metric_list, task_states):
        if state == TaskState.CANCELLED:
            continue
        total += m["total_weight"]
        produced += m["produced_weight"]
        verified += m["verified_weight"]
    health = "attention" if any(
        s in (TaskState.BLOCKED, TaskState.FAILED) for s in task_states
    ) else "normal"
    return {
        "progress_pct": _pct(produced, total),
        "verified_pct": _pct(verified, total),
        "total_weight": total,
        "produced_weight": produced,
        "verified_weight": verified,
        "health": health,
    }
