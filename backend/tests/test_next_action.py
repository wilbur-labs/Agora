"""The deterministic Next Action engine — per task and rolled up per project."""
from __future__ import annotations

from agora.tasks.models import TaskState
from agora.tasks.next_action import (
    ACTIONS,
    has_pending_clarifications,
    project_next_action,
    task_next_action,
)


def test_terminal_states_have_no_action():
    for state in (TaskState.DONE, TaskState.CANCELLED):
        na = task_next_action(state)
        assert na["owner"] == "none" and na["action"] == "none"
        assert na["attention"] is False


def test_stuck_states_need_human_attention():
    failed = task_next_action(TaskState.FAILED)
    assert failed == {**failed, "owner": "human", "action": "investigate_failure", "attention": True}
    blocked = task_next_action(TaskState.BLOCKED)
    assert blocked["owner"] == "human" and blocked["action"] == "resolve_blocker"
    assert blocked["attention"] is True


def test_requirements_routes_on_clarifications_then_spec_state():
    # Unanswered clarifying questions gate everything else in requirements.
    na = task_next_action(TaskState.REQUIREMENTS, spec_state="draft", pending_clarifications=True)
    assert na["action"] == "answer_clarifications"
    # No spec yet -> draft one.
    assert task_next_action(TaskState.REQUIREMENTS)["action"] == "draft_spec"
    # Draft awaiting approval.
    assert task_next_action(TaskState.REQUIREMENTS, spec_state="draft")["action"] == "approve_spec"
    # Approved -> advance out of requirements.
    assert task_next_action(TaskState.REQUIREMENTS, spec_state="approved")["action"] == "advance_to_design"
    # Rejected/superseded -> revise.
    assert task_next_action(TaskState.REQUIREMENTS, spec_state="rejected")["action"] == "revise_spec"


def test_spec_state_accepts_enum_or_value():
    from agora.requirements.models import SpecState

    assert task_next_action(TaskState.REQUIREMENTS, spec_state=SpecState.DRAFT)["action"] == "approve_spec"
    assert task_next_action(TaskState.REQUIREMENTS, spec_state=SpecState.APPROVED)["action"] == "advance_to_design"


def test_lifecycle_owner_ladder():
    assert task_next_action(TaskState.BACKLOG)["action"] == "start_requirements"
    assert task_next_action(TaskState.DESIGN)["action"] == "advance_to_plan"
    assert task_next_action(TaskState.PLANNED)["action"] == "start_run"
    assert task_next_action(TaskState.REVIEW)["action"] == "verify_review"
    assert task_next_action(TaskState.VERIFIED)["action"] == "mark_done"


def test_running_waits_only_while_a_run_is_active():
    # A run finishing does not move the task out of running; once a worker run
    # has succeeded the human must review the result, not keep waiting.
    active = task_next_action(TaskState.RUNNING, has_active_run=True)
    assert active["owner"] == "agent" and active["action"] == "await_run"
    finished = task_next_action(
        TaskState.RUNNING, has_active_run=False, has_worker_success=True
    )
    assert finished["owner"] == "human" and finished["action"] == "verify_review"


def test_running_without_a_worker_success_routes_to_investigation():
    # A failed / timed-out / abandoned run settles the run but leaves the task
    # in RUNNING. "No active run" alone must not send the human to verify a
    # result that was never produced.
    ended = task_next_action(
        TaskState.RUNNING, has_active_run=False, has_worker_success=False
    )
    assert ended["owner"] == "human"
    assert ended["action"] == "investigate_failure"
    assert ended["attention"] is True


def test_every_action_has_a_summary():
    for state in TaskState:
        na = task_next_action(state)
        assert na["summary"] == ACTIONS[na["action"]]


def test_has_pending_clarifications():
    assert has_pending_clarifications(None) is False
    assert has_pending_clarifications({}) is False
    assert has_pending_clarifications({"questions": []}) is False
    unanswered = {"questions": [{"id": "q1"}, {"id": "q2"}], "answers": {"q1": "yes"}}
    assert has_pending_clarifications(unanswered) is True
    answered = {"questions": [{"id": "q1"}], "answers": {"q1": "yes"}}
    assert has_pending_clarifications(answered) is False
    blank = {"questions": [{"id": "q1"}], "answers": {"q1": "  "}}
    assert has_pending_clarifications(blank) is True


def _item(task_id, state, priority=50, **signals):
    return {
        "task_id": task_id,
        "title": task_id.title(),
        "priority": priority,
        "next_action": task_next_action(state, **signals),
    }


def test_project_next_action_prefers_attention():
    items = [
        _item("a", TaskState.PLANNED),          # start_run
        _item("b", TaskState.BLOCKED),          # attention
        _item("c", TaskState.REVIEW),           # verify_review
    ]
    na = project_next_action(items)
    assert na["task_id"] == "b" and na["action"] == "resolve_blocker"
    assert na["attention"] is True
    assert na["attention_count"] == 1
    assert na["pending_count"] == 3  # all three need a human


def test_project_next_action_orders_human_decisions_over_runs():
    items = [
        _item("a", TaskState.PLANNED),                     # start_run (60)
        _item("b", TaskState.REQUIREMENTS, spec_state="draft"),  # approve_spec (78)
        _item("c", TaskState.RUNNING, has_active_run=True),  # await_run (20)
    ]
    na = project_next_action(items)
    assert na["task_id"] == "b" and na["action"] == "approve_spec"
    assert na["partial"] is False


def test_project_next_action_partial_flag():
    items = [_item("a", TaskState.PLANNED)]
    assert project_next_action(items, partial=True)["partial"] is True


def test_project_next_action_ties_break_on_task_priority_then_order():
    items = [
        _item("low", TaskState.PLANNED, priority=10),
        _item("high", TaskState.PLANNED, priority=90),
    ]
    na = project_next_action(items)
    assert na["task_id"] == "high"  # same action, higher task priority wins

    same = [
        _item("first", TaskState.PLANNED, priority=50),
        _item("second", TaskState.PLANNED, priority=50),
    ]
    assert project_next_action(same)["task_id"] == "first"  # earlier task wins exact tie


def test_project_next_action_all_terminal_is_none():
    items = [_item("a", TaskState.DONE), _item("b", TaskState.CANCELLED)]
    na = project_next_action(items)
    assert na["task_id"] is None and na["action"] == "none"
    assert na["owner"] == "none"
    assert na["attention_count"] == 0 and na["pending_count"] == 0


def test_project_next_action_empty():
    na = project_next_action([])
    assert na["action"] == "none" and na["task_id"] is None
