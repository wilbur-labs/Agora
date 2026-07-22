from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from agora.control_plane.store import ControlPlaneConflictError, ControlPlaneStore
from agora.protocol.hashing import seal_model_payload
from agora.protocol.models import StageInventory
from agora.protocol.state_machines import GateStatus, StageStatus, TaskStatus
from agora.tasks.models import CreateTaskRequest, utc_now
from agora.tasks.store import TaskStore


def _system(tmp_path):
    tasks = TaskStore(tmp_path / "agora.db")
    task = tasks.create(
        CreateTaskRequest(
            project_id="agora",
            title="Route sealed inventory Stages",
            kind="architecture",
        )
    )
    store = ControlPlaneStore(tasks)
    store.ensure_task_state(task.task_id)
    payload = {
        "schema_version": "1.0",
        "inventory_id": "inventory:routing",
        "task_id": task.task_id,
        "project_id": task.project_id,
        "plan_id": "plan_routing",
        "methodology_id": "bounded_method",
        "methodology_version": "1.0",
        "methodology_sha256": "a" * 64,
        "provisional": True,
        "contract": None,
        "groups": [
            {
                "group_key": "planning",
                "sequence": 1,
                "title": "Planning",
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


def _complete_stage(tasks, task, *, stage_key, gate_key):
    now = utc_now()
    with tasks._transaction() as db:
        db.execute(
            """
            UPDATE control_stages SET status = ?, version = version + 1, updated_at = ?
            WHERE task_id = ? AND stage_key = ?
            """,
            (StageStatus.COMPLETED.value, now, task.task_id, stage_key),
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
                GateStatus.PASSED.value,
                now,
                now,
            ),
        )


def _insert_stage(tasks, task, *, stage_key, gate_key, status):
    now = utc_now()
    with tasks._transaction() as db:
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
                status.value,
                now,
                now,
            ),
        )


def test_stage_route_activates_only_the_first_incomplete_inventory_stage(tmp_path):
    _, store, task = _system(tmp_path)

    pending = store.get_stage_route(task.task_id)
    receipt = store.activate_stage_route(
        task_id=task.task_id,
        expected_stage_key="design",
        actor="orchestrator",
        operation_key="route:design",
    )
    replay = store.activate_stage_route(
        task_id=task.task_id,
        expected_stage_key="design",
        actor="another-actor",
        operation_key="route:design",
    )

    assert pending.stage_key == "design"
    assert pending.stage_status is None
    assert receipt.previous_status is None
    assert receipt.activated is True
    assert receipt.route.stage_status == StageStatus.READY
    assert receipt.route.runtime == "codex"
    assert receipt.route.runnable is True
    assert replay.replayed is True
    assert store.get_stage(task.task_id, "review") is None
    assert store.get_task_state(task.task_id).status == TaskStatus.READY
    assert len(
        [event for event in store.events(task.task_id) if event.event_type == "stage.activated"]
    ) == 1


def test_stage_route_runnable_requires_reconciled_dispatchable_task(tmp_path):
    tasks, store, task = _system(tmp_path)
    store.activate_stage_route(
        task_id=task.task_id,
        expected_stage_key="design",
        actor="orchestrator",
        operation_key="route:design",
    )
    with tasks._transaction() as db:
        db.execute(
            "UPDATE control_tasks SET status = ? WHERE task_id = ?",
            (TaskStatus.BACKLOG.value, task.task_id),
        )

    drifted = store.get_stage_route(task.task_id)
    repaired = store.activate_stage_route(
        task_id=task.task_id,
        expected_stage_key="design",
        actor="reconciler",
        operation_key="route:design:reconcile",
    )

    assert drifted.stage_status == StageStatus.READY
    assert drifted.runnable is False
    assert repaired.activated is False
    assert repaired.route.runnable is True
    assert store.get_task_state(task.task_id).status == TaskStatus.READY


def test_concurrent_stage_route_activation_creates_one_transition_and_event(tmp_path):
    _, store, task = _system(tmp_path)

    def activate(_):
        return store.activate_stage_route(
            task_id=task.task_id,
            expected_stage_key="design",
            actor="orchestrator",
            operation_key="route:design:concurrent",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(activate, range(2)))

    assert sorted(receipt.replayed for receipt in receipts) == [False, True]
    assert all(receipt.route.stage_status == StageStatus.READY for receipt in receipts)
    assert len(
        [event for event in store.events(task.task_id) if event.event_type == "stage.activated"]
    ) == 1


def test_stage_route_rejects_skipping_and_conflicting_operation_reuse(tmp_path):
    _, store, task = _system(tmp_path)

    with pytest.raises(ControlPlaneConflictError, match="not the authoritative"):
        store.activate_stage_route(
            task_id=task.task_id,
            expected_stage_key="review",
            actor="orchestrator",
            operation_key="route:shared",
        )
    store.activate_stage_route(
        task_id=task.task_id,
        expected_stage_key="design",
        actor="orchestrator",
        operation_key="route:shared",
    )
    with pytest.raises(ControlPlaneConflictError, match="different input"):
        store.activate_stage_route(
            task_id=task.task_id,
            expected_stage_key="review",
            actor="orchestrator",
            operation_key="route:shared",
        )


def test_stage_route_advances_only_after_the_exact_gate_passes(tmp_path):
    tasks, store, task = _system(tmp_path)
    store.activate_stage_route(
        task_id=task.task_id,
        expected_stage_key="design",
        actor="orchestrator",
        operation_key="route:design",
    )
    _complete_stage(
        tasks,
        task,
        stage_key="design",
        gate_key="gate:design",
    )

    next_route = store.get_stage_route(task.task_id)
    activated = store.activate_stage_route(
        task_id=task.task_id,
        expected_stage_key="review",
        actor="orchestrator",
        operation_key="route:review",
    )

    assert next_route.stage_key == "review"
    assert next_route.stage_status is None
    assert activated.route.stage_status == StageStatus.READY
    assert activated.route.runtime == "claude"
    assert store.get_stage(task.task_id, "design").status == StageStatus.COMPLETED


def test_passed_gate_does_not_bypass_a_semantically_blocked_stage(tmp_path):
    tasks, store, task = _system(tmp_path)
    store.activate_stage_route(
        task_id=task.task_id,
        expected_stage_key="design",
        actor="orchestrator",
        operation_key="route:design",
    )
    now = utc_now()
    with tasks._transaction() as db:
        db.execute(
            """UPDATE control_stages SET status = ?, version = version + 1,
                      updated_at = ? WHERE task_id = ? AND stage_key = ?""",
            (StageStatus.BLOCKED.value, now, task.task_id, "design"),
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
                "gate:design",
                "design",
                GateStatus.PASSED.value,
                now,
                now,
            ),
        )

    route = store.get_stage_route(task.task_id)

    assert route.stage_key == "design"
    assert route.stage_status == StageStatus.BLOCKED
    assert route.gate_status == GateStatus.PASSED
    assert route.runnable is False


def test_stage_route_fails_closed_on_out_of_order_completion(tmp_path):
    tasks, store, task = _system(tmp_path)
    store.activate_stage_route(
        task_id=task.task_id,
        expected_stage_key="design",
        actor="orchestrator",
        operation_key="route:design",
    )
    _insert_stage(
        tasks,
        task,
        stage_key="review",
        gate_key="gate:review",
        status=StageStatus.PENDING,
    )
    _complete_stage(
        tasks,
        task,
        stage_key="review",
        gate_key="gate:review",
    )

    with pytest.raises(ControlPlaneConflictError, match="ordered inventory prefix"):
        store.get_stage_route(task.task_id)


def test_stage_route_fails_closed_on_later_active_stage(tmp_path):
    tasks, store, task = _system(tmp_path)
    store.activate_stage_route(
        task_id=task.task_id,
        expected_stage_key="design",
        actor="orchestrator",
        operation_key="route:design",
    )
    _insert_stage(
        tasks,
        task,
        stage_key="review",
        gate_key="gate:review",
        status=StageStatus.READY,
    )

    with pytest.raises(ControlPlaneConflictError, match="later inventory Stage"):
        store.get_stage_route(task.task_id)


def test_stage_route_fails_closed_on_inventory_gate_binding_drift(tmp_path):
    tasks, store, task = _system(tmp_path)
    store.activate_stage_route(
        task_id=task.task_id,
        expected_stage_key="design",
        actor="orchestrator",
        operation_key="route:design",
    )
    with tasks._transaction() as db:
        db.execute(
            """UPDATE control_stages SET gate_key = ?, version = version + 1
               WHERE task_id = ? AND stage_key = ?""",
            ("gate:tampered", task.task_id, "design"),
        )

    with pytest.raises(ControlPlaneConflictError, match="Gate.*inventory"):
        store.get_stage_route(task.task_id)


def test_stage_activation_rolls_back_stage_and_event_together(tmp_path, monkeypatch):
    _, store, task = _system(tmp_path)
    original_event = store._event

    def fail_activation(*args, **kwargs):
        if kwargs.get("event_type") == "stage.activated":
            raise RuntimeError("activation event failed")
        return original_event(*args, **kwargs)

    monkeypatch.setattr(store, "_event", fail_activation)
    with pytest.raises(RuntimeError, match="activation event failed"):
        store.activate_stage_route(
            task_id=task.task_id,
            expected_stage_key="design",
            actor="orchestrator",
            operation_key="route:design",
        )

    assert store.get_stage(task.task_id, "design") is None
    assert store.get_stage_route(task.task_id).stage_status is None
