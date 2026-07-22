from __future__ import annotations

import pytest

from agora.attention.models import (
    AttentionKind,
    CreateAttentionRequest,
    RespondAttentionRequest,
    ResponseAction,
)
from agora.attention.store import AttentionStore
from agora.control_plane.lifecycle import (
    TaskLifecycleDerivationError,
    derive_task_lifecycle,
    task_transition_path,
)
from agora.control_plane.models import TaskLifecycleReason, TaskTransitionCause
from agora.control_plane.store import ControlPlaneConflictError, ControlPlaneStore
from agora.protocol.hashing import seal_model_payload
from agora.protocol.models import StageInventory
from agora.protocol.state_machines import GateStatus, StageStatus, TaskStatus
from agora.tasks.models import CreateTaskRequest, utc_now
from agora.tasks.store import TaskStore


def _decision(**overrides):
    values = {
        "current_status": TaskStatus.READY,
        "inventory_id": "inventory:lifecycle",
        "inventory_sha256": "a" * 64,
        "inventory_stage_keys": ["design", "review"],
        "stage_statuses": {},
        "gate_statuses": {},
        "open_blockers": 0,
        "open_questions": 0,
        "open_approvals": 0,
    }
    values.update(overrides)
    return derive_task_lifecycle(**values)


@pytest.mark.parametrize(
    ("overrides", "target", "reason"),
    [
        ({}, TaskStatus.READY, TaskLifecycleReason.INVENTORY_READY),
        (
            {"stage_statuses": {"design": StageStatus.RUNNING}},
            TaskStatus.ACTIVE,
            TaskLifecycleReason.WORK_ACTIVE,
        ),
        (
            {"stage_statuses": {"design": StageStatus.BLOCKED}},
            TaskStatus.BLOCKED,
            TaskLifecycleReason.STAGE_OR_GATE_BLOCKED,
        ),
        (
            {"stage_statuses": {"design": StageStatus.RECONCILIATION_REQUIRED}},
            TaskStatus.BLOCKED,
            TaskLifecycleReason.RECONCILIATION_REQUIRED,
        ),
        (
            {"gate_statuses": {"design": GateStatus.STALE}},
            TaskStatus.BLOCKED,
            TaskLifecycleReason.INVALIDATION_REQUIRED,
        ),
        (
            {"open_questions": 1},
            TaskStatus.BLOCKED,
            TaskLifecycleReason.BLOCKING_ATTENTION,
        ),
        (
            {"open_approvals": 1},
            TaskStatus.NEEDS_REVIEW,
            TaskLifecycleReason.REVIEW_REQUIRED,
        ),
        (
            {"stage_statuses": {"design": StageStatus.FAILED}},
            TaskStatus.FAILED,
            TaskLifecycleReason.STAGE_FAILED,
        ),
        (
            {"stage_statuses": {"design": StageStatus.CANCELLED}},
            TaskStatus.CANCELLED,
            TaskLifecycleReason.STAGE_CANCELLED,
        ),
    ],
)
def test_task_lifecycle_precedence_is_deterministic(overrides, target, reason):
    decision = _decision(**overrides)

    assert decision.target_status == target
    assert decision.reason == reason


def test_all_stages_and_gates_passed_require_explicit_task_completion():
    completed = {
        "design": StageStatus.COMPLETED,
        "review": StageStatus.COMPLETED,
    }
    passed = {"design": GateStatus.PASSED, "review": GateStatus.PASSED}

    pending_approval = _decision(
        stage_statuses=completed,
        gate_statuses=passed,
    )
    explicitly_completed = _decision(
        current_status=TaskStatus.COMPLETED,
        stage_statuses=completed,
        gate_statuses=passed,
    )

    assert pending_approval.target_status == TaskStatus.NEEDS_REVIEW
    assert pending_approval.reason == TaskLifecycleReason.ALL_STAGES_PASSED
    assert explicitly_completed.target_status == TaskStatus.COMPLETED
    assert explicitly_completed.reason == TaskLifecycleReason.EXPLICIT_COMPLETION


def test_completed_stage_without_passed_gate_fails_closed():
    with pytest.raises(TaskLifecycleDerivationError, match="passed formal Gate"):
        _decision(stage_statuses={"design": StageStatus.COMPLETED})


def test_transition_paths_are_shortest_and_preserve_reopen_boundary():
    assert task_transition_path(TaskStatus.BACKLOG, TaskStatus.NEEDS_REVIEW) == [
        TaskStatus.READY,
        TaskStatus.ACTIVE,
        TaskStatus.NEEDS_REVIEW,
    ]
    assert task_transition_path(TaskStatus.COMPLETED, TaskStatus.BLOCKED) == [
        TaskStatus.ACTIVE,
        TaskStatus.BLOCKED,
    ]


def _stores(tmp_path):
    tasks = TaskStore(tmp_path / "agora.db")
    task = tasks.create(
        CreateTaskRequest(
            project_id="agora",
            title="Derive frozen Task lifecycle",
            kind="architecture",
        )
    )
    store = ControlPlaneStore(tasks)
    store.ensure_task_state(task.task_id)
    payload = {
        "schema_version": "1.0",
        "inventory_id": "inventory:lifecycle",
        "task_id": task.task_id,
        "project_id": task.project_id,
        "plan_id": "plan_lifecycle",
        "methodology_id": "bounded_method",
        "methodology_version": "1.0",
        "methodology_sha256": "b" * 64,
        "provisional": True,
        "contract": None,
        "groups": [
            {
                "group_key": "delivery",
                "sequence": 1,
                "title": "Delivery",
                "stages": [
                    {
                        "stage_key": "design",
                        "gate_key": "gate:design",
                        "sequence": 1,
                        "title": "Design",
                        "role": "planner",
                        "runtime": "codex",
                    },
                    {
                        "stage_key": "review",
                        "gate_key": "gate:review",
                        "sequence": 2,
                        "title": "Review",
                        "role": "reviewer",
                        "runtime": "claude",
                    },
                ],
            }
        ],
    }
    inventory = StageInventory.model_validate(
        seal_model_payload(StageInventory, payload)
    )
    store.ensure_stage_inventory(inventory, actor="orchestrator")
    return tasks, store, task


def _replace_formal_state(tasks, task, *, stage_status, gate_status):
    now = utc_now()
    with tasks._transaction() as db:
        db.execute("DELETE FROM control_gates WHERE task_id = ?", (task.task_id,))
        db.execute("DELETE FROM control_stages WHERE task_id = ?", (task.task_id,))
        for stage_key in ("design", "review"):
            gate_key = f"gate:{stage_key}"
            db.execute(
                """
                INSERT INTO control_stages (
                    task_id, project_id, stage_key, gate_key, status,
                    version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    task.task_id,
                    task.project_id,
                    stage_key,
                    gate_key,
                    stage_status.value,
                    now,
                    now,
                ),
            )
            db.execute(
                """
                INSERT INTO control_gates (
                    task_id, project_id, gate_key, stage_key, status,
                    version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    task.task_id,
                    task.project_id,
                    gate_key,
                    stage_key,
                    gate_status.value,
                    now,
                    now,
                ),
            )


def test_inventory_initialization_and_reconciliation_are_atomic_and_idempotent(
    tmp_path,
):
    _, store, task = _stores(tmp_path)

    ready = store.get_task_state(task.task_id)
    replay = store.reconcile_task_lifecycle(task.task_id)

    assert ready.status == TaskStatus.READY
    assert ready.version == 2
    assert replay.task == ready
    assert replay.transitions == []
    transitions = [
        event
        for event in store.events(task.task_id)
        if event.event_type == "task.state_changed"
    ]
    assert [(item.payload["from"], item.payload["to"]) for item in transitions] == [
        ("backlog", "ready")
    ]
    assert transitions[0].payload["lifecycle_decision"]["reason"] == "inventory_ready"


def test_reconcile_moves_passed_inventory_to_review_then_explicit_completion(
    tmp_path,
):
    tasks, store, task = _stores(tmp_path)
    _replace_formal_state(
        tasks,
        task,
        stage_status=StageStatus.COMPLETED,
        gate_status=GateStatus.PASSED,
    )

    reconciled = store.reconcile_task_lifecycle(task.task_id)
    completed = store.transition_task_state(
        task.task_id,
        TaskStatus.COMPLETED,
        expected_version=reconciled.task.version,
        cause=TaskTransitionCause.USER_ACTION,
        actor="owner",
        reason="Explicitly approved the reviewed Task",
        operation_key="task-lifecycle-complete",
    )

    assert reconciled.previous_status == TaskStatus.READY
    assert reconciled.transitions == [TaskStatus.ACTIVE, TaskStatus.NEEDS_REVIEW]
    assert completed.task.status == TaskStatus.COMPLETED
    replay = store.reconcile_task_lifecycle(task.task_id)
    assert replay.decision.reason == TaskLifecycleReason.EXPLICIT_COMPLETION
    assert replay.transitions == []


def test_explicit_completion_cannot_bypass_authoritative_stage_and_gate_facts(
    tmp_path,
):
    _, store, task = _stores(tmp_path)
    active = store.transition_task_state(
        task.task_id,
        TaskStatus.ACTIVE,
        expected_version=2,
        cause=TaskTransitionCause.USER_ACTION,
        actor="owner",
        reason="Begin work",
        operation_key="task-lifecycle-bypass-active",
    )
    review = store.transition_task_state(
        task.task_id,
        TaskStatus.NEEDS_REVIEW,
        expected_version=active.task.version,
        cause=TaskTransitionCause.USER_ACTION,
        actor="owner",
        reason="Request review",
        operation_key="task-lifecycle-bypass-review",
    )

    with pytest.raises(ControlPlaneConflictError, match="exact formal Gate"):
        store.transition_task_state(
            task.task_id,
            TaskStatus.COMPLETED,
            expected_version=review.task.version,
            cause=TaskTransitionCause.USER_ACTION,
            actor="owner",
            reason="Attempt premature completion",
            operation_key="task-lifecycle-bypass-complete",
        )


def test_invalidation_reopens_completed_task_through_active_to_blocked(tmp_path):
    tasks, store, task = _stores(tmp_path)
    _replace_formal_state(
        tasks,
        task,
        stage_status=StageStatus.COMPLETED,
        gate_status=GateStatus.PASSED,
    )
    review = store.reconcile_task_lifecycle(task.task_id)
    completed = store.transition_task_state(
        task.task_id,
        TaskStatus.COMPLETED,
        expected_version=review.task.version,
        cause=TaskTransitionCause.USER_ACTION,
        actor="owner",
        reason="Explicit approval",
        operation_key="task-lifecycle-approved",
    )
    with tasks._transaction() as db:
        db.execute(
            "UPDATE control_stages SET status = 'ready' WHERE task_id = ?",
            (task.task_id,),
        )
        db.execute(
            "UPDATE control_gates SET status = 'stale' WHERE task_id = ?",
            (task.task_id,),
        )

    reopened = store.reconcile_task_lifecycle(
        task.task_id,
        cause=TaskTransitionCause.INVALIDATION,
        actor="invalidator",
    )

    assert completed.task.status == TaskStatus.COMPLETED
    assert reopened.transitions == [TaskStatus.ACTIVE, TaskStatus.BLOCKED]
    assert reopened.task.status == TaskStatus.BLOCKED
    assert reopened.decision.reason == TaskLifecycleReason.INVALIDATION_REQUIRED


def test_attention_changes_require_explicit_idempotent_lifecycle_reconciliation(
    tmp_path,
):
    tasks, store, task = _stores(tmp_path)
    attention = AttentionStore(tasks)
    item = attention.create(
        CreateAttentionRequest(
            task_id=task.task_id,
            kind=AttentionKind.BLOCKER,
            title="Decision required",
            requester="orchestrator",
        )
    )

    blocked = store.reconcile_task_lifecycle(task.task_id)
    attention.respond(
        item.item_id,
        RespondAttentionRequest(
            action=ResponseAction.ANSWER,
            response="Proceed with the bounded option",
            actor="owner",
            expected_version=item.version,
        ),
    )
    ready = store.reconcile_task_lifecycle(task.task_id)

    assert blocked.task.status == TaskStatus.BLOCKED
    assert blocked.decision.reason == TaskLifecycleReason.BLOCKING_ATTENTION
    assert ready.task.status == TaskStatus.READY
    assert ready.transitions == [TaskStatus.READY]


def test_inventory_and_initial_lifecycle_transition_roll_back_together(
    tmp_path,
    monkeypatch,
):
    tasks = TaskStore(tmp_path / "agora.db")
    task = tasks.create(
        CreateTaskRequest(
            project_id="agora",
            title="Rollback derived lifecycle",
            kind="architecture",
        )
    )
    store = ControlPlaneStore(tasks)
    store.ensure_task_state(task.task_id)
    payload = {
        "schema_version": "1.0",
        "inventory_id": "inventory:rollback",
        "task_id": task.task_id,
        "project_id": task.project_id,
        "plan_id": "plan_rollback",
        "methodology_id": "bounded_method",
        "methodology_version": "1.0",
        "methodology_sha256": "c" * 64,
        "provisional": True,
        "contract": None,
        "groups": [
            {
                "group_key": "delivery",
                "sequence": 1,
                "title": "Delivery",
                "stages": [
                    {
                        "stage_key": "design",
                        "gate_key": "gate:design",
                        "sequence": 1,
                        "title": "Design",
                        "role": "planner",
                        "runtime": "codex",
                    }
                ],
            }
        ],
    }
    inventory = StageInventory.model_validate(
        seal_model_payload(StageInventory, payload)
    )
    original_event = store._event

    def fail_lifecycle_event(*args, **kwargs):
        if kwargs.get("event_type") == "task.state_changed":
            raise RuntimeError("lifecycle event persistence failed")
        return original_event(*args, **kwargs)

    monkeypatch.setattr(store, "_event", fail_lifecycle_event)
    with pytest.raises(RuntimeError, match="lifecycle event persistence failed"):
        store.ensure_stage_inventory(inventory, actor="orchestrator")

    assert store.get_stage_inventory(task.task_id) is None
    frozen = store.get_task_state(task.task_id)
    assert frozen.status == TaskStatus.BACKLOG
    assert frozen.version == 1
