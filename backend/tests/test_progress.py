"""Progress vs verified-completion vs health metrics."""
from __future__ import annotations

from types import SimpleNamespace

from agora.tasks.models import TaskState
from agora.tasks.progress import (
    derive_criteria,
    project_metrics,
    task_criterion_status,
    task_metrics,
)


def _spec(*scenario_ids):
    return SimpleNamespace(acceptance_scenarios=[
        SimpleNamespace(scenario_id=sid, given="g", when="w", then="t") for sid in scenario_ids
    ])


def test_derive_criteria():
    assert derive_criteria(None) == []
    crit = derive_criteria(_spec("AC-1", "AC-2"))
    assert [c["id"] for c in crit] == ["AC-1", "AC-2"]
    assert all(c["weight"] == 1 for c in crit)


def test_task_criterion_status_ladder():
    assert task_criterion_status(TaskState.PLANNED, False, False) == "pending"
    assert task_criterion_status(TaskState.RUNNING, True, False) == "produced"
    assert task_criterion_status(TaskState.RUNNING, True, True) == "reviewed"
    # The review *state* is routing, not evidence: without a successful review
    # run the criterion is only "produced".
    assert task_criterion_status(TaskState.REVIEW, True, False) == "produced"
    assert task_criterion_status(TaskState.VERIFIED, True, True) == "verified"
    assert task_criterion_status(TaskState.DONE, True, True) == "verified"


def test_lifecycle_state_alone_never_reports_verified():
    # A task walked to verified/done through the transition API without any
    # successful worker run has no evidence behind it — reporting "verified"
    # here is precisely the false "done" this module exists to prevent.
    assert task_criterion_status(TaskState.VERIFIED, False, False) == "pending"
    assert task_criterion_status(TaskState.DONE, False, False) == "pending"
    assert task_criterion_status(TaskState.DONE, False, True) == "pending"
    spec = _spec("AC-1", "AC-2")
    m = task_metrics(TaskState.DONE, spec, has_worker_success=False, has_review_success=False)
    assert m["verified_weight"] == 0 and m["produced_weight"] == 0
    assert m["total_weight"] == 2  # still counted in the denominator, not hidden


def test_no_produced_weight_without_a_worker_run():
    # A review state (or review success) never manufactures produced weight.
    assert task_criterion_status(TaskState.REVIEW, False, True) == "pending"
    assert task_criterion_status(TaskState.RUNNING, False, True) == "pending"
    spec = _spec("AC-1", "AC-2")
    m = task_metrics(TaskState.REVIEW, spec, has_worker_success=False, has_review_success=True)
    assert m["produced_weight"] == 0 and m["verified_weight"] == 0


def test_task_metrics_only_verified_counts_as_verified():
    spec = _spec("AC-1", "AC-2")
    # produced but not approved: full progress, zero verified
    produced = task_metrics(TaskState.REVIEW, spec, has_worker_success=True, has_review_success=True)
    assert produced["total_weight"] == 2
    assert produced["produced_weight"] == 2
    assert produced["verified_weight"] == 0
    assert all(c["status"] == "reviewed" for c in produced["criteria"])

    # done: everything verified
    done = task_metrics(TaskState.DONE, spec, has_worker_success=True, has_review_success=True)
    assert done["verified_weight"] == 2


def test_project_metrics_spread_and_health():
    spec = _spec("AC-1", "AC-2")
    done = task_metrics(TaskState.DONE, spec, has_worker_success=True, has_review_success=True)
    in_review = task_metrics(TaskState.REVIEW, spec, has_worker_success=True, has_review_success=True)
    planned = task_metrics(TaskState.PLANNED, spec, has_worker_success=False, has_review_success=False)

    metrics = project_metrics(
        [done, in_review, planned],
        [TaskState.DONE, TaskState.REVIEW, TaskState.PLANNED],
    )
    # 6 total weight; produced = done(2)+review(2)=4 -> 67%; verified = done(2) -> 33%
    assert metrics["progress_pct"] == 67
    assert metrics["verified_pct"] == 33
    assert metrics["health"] == "normal"

    blocked = project_metrics([done], [TaskState.BLOCKED])
    assert blocked["health"] == "attention"


def test_task_without_spec_counts_one_unit_consistently():
    none_spec = task_metrics(TaskState.DONE, None, has_worker_success=True, has_review_success=True)
    # per-task and project rollup reconcile: one implicit unit, verified
    assert none_spec["total_weight"] == 1 and none_spec["verified_weight"] == 1
    assert none_spec["has_spec"] is False
    metrics = project_metrics([none_spec], [TaskState.DONE])
    assert metrics["total_weight"] == 1 and metrics["verified_pct"] == 100


def test_cancelled_tasks_excluded_from_denominator():
    spec = _spec("AC-1")
    done = task_metrics(TaskState.DONE, spec, has_worker_success=True, has_review_success=True)
    cancelled = task_metrics(TaskState.CANCELLED, spec, has_worker_success=False, has_review_success=False)
    metrics = project_metrics([done, cancelled], [TaskState.DONE, TaskState.CANCELLED])
    assert metrics["total_weight"] == 1 and metrics["verified_pct"] == 100  # cancelled not counted


def test_pct_is_half_up():
    # 1/8 = 12.5% -> 13 (half-up), not 12 (banker's rounding)
    verified_1 = task_metrics(TaskState.DONE, _spec("a"), has_worker_success=True, has_review_success=True)
    pending_7 = task_metrics(
        TaskState.PLANNED, _spec("b", "c", "d", "e", "f", "g", "h"),
        has_worker_success=False, has_review_success=False,
    )
    metrics = project_metrics([verified_1, pending_7], [TaskState.DONE, TaskState.PLANNED])
    assert metrics["total_weight"] == 8 and metrics["verified_pct"] == 13
