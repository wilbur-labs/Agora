from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from agora.control_plane.models import TaskTransitionCause
from agora.control_plane.store import (
    ControlPlaneConflictError,
    ControlPlaneNotFoundError,
    ControlPlaneStore,
    ControlPlaneValidationError,
)
from agora.protocol.state_machines import TaskStatus
from agora.tasks.models import CreateTaskRequest, TaskState
from agora.tasks.store import TaskStore


def _stores(tmp_path):
    tasks = TaskStore(tmp_path / "agora.db")
    task = tasks.create(
        CreateTaskRequest(
            project_id="agora",
            title="Persist frozen Task state",
            kind="architecture",
        )
    )
    return tasks, ControlPlaneStore(tasks), task


def _transition(store, task_id, current_version, target, operation_key, **kwargs):
    return store.transition_task_state(
        task_id,
        target,
        expected_version=current_version,
        cause=kwargs.pop("cause", TaskTransitionCause.ORCHESTRATION),
        actor=kwargs.pop("actor", "orchestrator"),
        reason=kwargs.pop("reason", f"Advance to {target.value}"),
        operation_key=operation_key,
        **kwargs,
    )


def test_frozen_task_state_is_additive_explicit_and_idempotent(tmp_path):
    tasks, store, legacy = _stores(tmp_path)

    assert store.get_task_state(legacy.task_id) is None

    state = store.ensure_task_state(legacy.task_id, actor="orchestrator")
    replay = store.ensure_task_state(legacy.task_id, actor="another-actor")

    assert state == replay
    assert state.status == TaskStatus.BACKLOG
    assert state.version == 1
    assert tasks.get(legacy.task_id).state == TaskState.BACKLOG
    assert store.get_task_state(legacy.task_id).status == TaskStatus.BACKLOG
    events = [
        event for event in store.events(legacy.task_id)
        if event.event_type == "task.state_initialized"
    ]
    assert len(events) == 1
    assert events[0].payload == {"status": "backlog", "version": 1}


def test_frozen_task_transitions_are_optimistic_replay_safe_and_audited(tmp_path):
    _, store, task = _stores(tmp_path)
    store.ensure_task_state(task.task_id)

    receipt = _transition(store, task.task_id, 1, TaskStatus.READY, "task-ready")
    replay = _transition(store, task.task_id, 1, TaskStatus.READY, "task-ready")

    assert receipt.replayed is False
    assert receipt.previous_status == TaskStatus.BACKLOG
    assert receipt.task.status == TaskStatus.READY
    assert receipt.task.version == 2
    assert replay.replayed is True
    assert replay.task == receipt.task
    events = [
        event for event in store.events(task.task_id)
        if event.event_type == "task.state_changed"
    ]
    assert len(events) == 1
    assert events[0].payload["cause"] == "orchestration"
    assert events[0].payload["from"] == "backlog"
    assert events[0].payload["to"] == "ready"

    with pytest.raises(ControlPlaneConflictError, match="different input"):
        _transition(store, task.task_id, 2, TaskStatus.ACTIVE, "task-ready")
    with pytest.raises(ControlPlaneConflictError, match="Expected Task version 1"):
        _transition(store, task.task_id, 1, TaskStatus.ACTIVE, "task-active-stale")
    with pytest.raises(ControlPlaneConflictError, match="invalid transition"):
        _transition(store, task.task_id, 2, TaskStatus.COMPLETED, "task-complete-early")


def test_completed_task_reopens_only_for_invalidation_or_reconciliation(tmp_path):
    tasks, store, task = _stores(tmp_path)
    store.ensure_task_state(task.task_id)
    path = (
        TaskStatus.READY,
        TaskStatus.ACTIVE,
        TaskStatus.NEEDS_REVIEW,
        TaskStatus.COMPLETED,
    )
    version = 1
    for target in path:
        receipt = _transition(
            store,
            task.task_id,
            version,
            target,
            f"task-{target.value}",
        )
        version = receipt.task.version

    with pytest.raises(ControlPlaneConflictError, match="only through invalidation"):
        _transition(
            store,
            task.task_id,
            version,
            TaskStatus.ACTIVE,
            "task-reopen-user",
            cause=TaskTransitionCause.USER_ACTION,
        )
    reopened = _transition(
        store,
        task.task_id,
        version,
        TaskStatus.ACTIVE,
        "task-reopen-invalidation",
        cause=TaskTransitionCause.INVALIDATION,
    )
    assert reopened.task.status == TaskStatus.ACTIVE
    assert tasks.get(task.task_id).state == TaskState.BACKLOG


def test_task_transition_validates_bounds_and_requires_initialization(tmp_path):
    _, store, task = _stores(tmp_path)
    with pytest.raises(ControlPlaneConflictError, match="not initialized"):
        _transition(store, task.task_id, 1, TaskStatus.READY, "task-not-initialized")

    store.ensure_task_state(task.task_id)
    with pytest.raises(ControlPlaneValidationError, match="reason"):
        _transition(
            store,
            task.task_id,
            1,
            TaskStatus.READY,
            "task-empty-reason",
            reason="   ",
        )
    with pytest.raises(ControlPlaneValidationError, match="actor"):
        _transition(
            store,
            task.task_id,
            1,
            TaskStatus.READY,
            "task-empty-actor",
            actor="",
        )
    with pytest.raises(ControlPlaneValidationError, match="actor"):
        _transition(
            store,
            task.task_id,
            1,
            TaskStatus.READY,
            "task-long-actor",
            actor="a" * 129,
        )
    with pytest.raises(ControlPlaneValidationError, match="reason"):
        _transition(
            store,
            task.task_id,
            1,
            TaskStatus.READY,
            "task-long-reason",
            reason="r" * 4_001,
        )
    with pytest.raises(ControlPlaneValidationError, match="operation_key"):
        _transition(
            store,
            task.task_id,
            1,
            TaskStatus.READY,
            "invalid operation key",
        )


def test_task_state_operations_fail_closed_for_unknown_tasks(tmp_path):
    tasks = TaskStore(tmp_path / "agora.db")
    store = ControlPlaneStore(tasks)

    with pytest.raises(ControlPlaneNotFoundError, match="Task not found"):
        store.ensure_task_state("task_missing")
    with pytest.raises(ControlPlaneNotFoundError, match="Task not found"):
        _transition(
            store,
            "task_missing",
            1,
            TaskStatus.READY,
            "task-missing-transition",
        )


def test_operation_keys_are_global_and_fail_closed_across_tasks(tmp_path):
    tasks, store, first = _stores(tmp_path)
    second = tasks.create(
        CreateTaskRequest(
            project_id="agora",
            title="Second frozen Task",
            kind="architecture",
        )
    )
    store.ensure_task_state(first.task_id)
    store.ensure_task_state(second.task_id)

    _transition(store, first.task_id, 1, TaskStatus.READY, "global-task-ready")
    with pytest.raises(ControlPlaneConflictError, match="different input"):
        _transition(store, second.task_id, 1, TaskStatus.READY, "global-task-ready")
    assert store.get_task_state(second.task_id).status == TaskStatus.BACKLOG
    assert store.get_task_state(second.task_id).version == 1


def test_concurrent_task_transition_allows_one_version_winner(tmp_path):
    _, store, task = _stores(tmp_path)
    store.ensure_task_state(task.task_id)

    def advance(operation_key):
        try:
            return _transition(
                store,
                task.task_id,
                1,
                TaskStatus.READY,
                operation_key,
            )
        except ControlPlaneConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(advance, ("task-ready-a", "task-ready-b")))

    assert sum(not isinstance(item, Exception) for item in results) == 1
    assert store.get_task_state(task.task_id).version == 2
    with store.tasks._connect() as db:
        operations = db.execute(
            "SELECT COUNT(*) FROM control_operations WHERE operation_key LIKE 'task-ready-%'"
        ).fetchone()[0]
    assert operations == 1


def test_task_transition_rolls_back_state_event_and_operation_together(
    tmp_path,
    monkeypatch,
):
    tasks, store, task = _stores(tmp_path)
    store.ensure_task_state(task.task_id)
    events_before = len(tasks.events(task.task_id))

    def fail_event(*_args, **_kwargs):
        raise RuntimeError("event persistence failed")

    monkeypatch.setattr(store, "_event", fail_event)
    with pytest.raises(RuntimeError, match="event persistence failed"):
        _transition(store, task.task_id, 1, TaskStatus.READY, "task-ready-rollback")

    state = store.get_task_state(task.task_id)
    assert state.status == TaskStatus.BACKLOG
    assert state.version == 1
    assert len(tasks.events(task.task_id)) == events_before
    with tasks._connect() as db:
        assert db.execute(
            "SELECT 1 FROM control_operations WHERE operation_key = ?",
            ("task-ready-rollback",),
        ).fetchone() is None
