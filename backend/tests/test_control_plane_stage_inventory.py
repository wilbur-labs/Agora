from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from agora.control_plane.store import (
    ControlPlaneConflictError,
    ControlPlaneNotFoundError,
    ControlPlaneStore,
    ControlPlaneValidationError,
)
from agora.protocol.hashing import seal_model_payload
from agora.protocol.models import StageInventory
from agora.protocol.state_machines import StageStatus, TaskStatus
from agora.tasks.models import CreateTaskRequest
from agora.tasks.store import TaskStore


def _stores(tmp_path):
    tasks = TaskStore(tmp_path / "agora.db")
    task = tasks.create(
        CreateTaskRequest(
            project_id="agora",
            title="Persist grouped Stage inventory",
            kind="architecture",
        )
    )
    store = ControlPlaneStore(tasks)
    store.ensure_task_state(task.task_id)
    return tasks, store, task


def _inventory(task, *, suffix="", project_id=None, task_id=None):
    payload = {
        "schema_version": "1.0",
        "inventory_id": f"inventory:plan_inventory{suffix}",
        "task_id": task_id or task.task_id,
        "project_id": project_id or task.project_id,
        "plan_id": f"plan_inventory{suffix}",
        "methodology_id": "bounded_method",
        "methodology_version": "1.0",
        "methodology_sha256": "a" * 64,
        "provisional": True,
        "contract": {
            "contract_id": "bounded_contract",
            "schema_version": "1.0",
            "sha256": "b" * 64,
        },
        "groups": [
            {
                "group_key": f"plan_inventory{suffix}",
                "sequence": 1,
                "title": "Pinned planning and review workflow",
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
    return StageInventory.model_validate(seal_model_payload(StageInventory, payload))


def test_grouped_stage_inventory_is_sealed_immutable_and_idempotent(tmp_path):
    tasks, store, task = _stores(tmp_path)
    inventory = _inventory(task)

    created = store.ensure_stage_inventory(inventory, actor="orchestrator")
    replay = store.ensure_stage_inventory(inventory, actor="another-actor")

    assert created == replay == inventory
    assert store.get_stage_inventory(task.task_id) == inventory
    assert [item.stage_key for item in inventory.groups[0].stages] == [
        "design",
        "review",
    ]
    assert store.get_stage(task.task_id, "design") is None
    assert store.get_task_state(task.task_id).status == TaskStatus.READY
    events = [
        event
        for event in store.events(task.task_id)
        if event.event_type == "stage.inventory_initialized"
    ]
    assert len(events) == 1
    assert events[0].payload["content_sha256"] == inventory.content_sha256
    assert events[0].payload["group_count"] == 1
    assert events[0].payload["stage_count"] == 2
    assert tasks.get(task.task_id) is not None


def test_stage_inventory_model_rejects_noncontiguous_and_duplicate_stages(tmp_path):
    _, _, task = _stores(tmp_path)
    payload = _inventory(task).model_dump(mode="json")
    payload.pop("content_sha256")
    payload["groups"][0]["stages"][1]["sequence"] = 3
    with pytest.raises(ValidationError, match="contiguous"):
        seal_model_payload(StageInventory, payload)

    payload["groups"][0]["stages"][1]["sequence"] = 2
    payload["groups"][0]["stages"][1]["stage_key"] = "design"
    with pytest.raises(ValidationError, match="unique"):
        seal_model_payload(StageInventory, payload)


def test_stage_inventory_rejects_more_than_200_stages_across_groups(tmp_path):
    _, _, task = _stores(tmp_path)
    payload = _inventory(task).model_dump(mode="json")
    payload.pop("content_sha256")
    payload["groups"] = []
    next_stage = 0
    for group_sequence, group_size in enumerate((101, 100), start=1):
        stages = []
        for stage_sequence in range(1, group_size + 1):
            next_stage += 1
            stages.append(
                {
                    "stage_key": f"stage_{next_stage}",
                    "gate_key": f"gate:stage_{next_stage}",
                    "sequence": stage_sequence,
                    "title": f"Stage {next_stage}",
                    "role": "worker",
                    "runtime": "codex",
                }
            )
        payload["groups"].append(
            {
                "group_key": f"group_{group_sequence}",
                "sequence": group_sequence,
                "title": f"Group {group_sequence}",
                "stages": stages,
            }
        )

    with pytest.raises(ValidationError, match="at most 200"):
        seal_model_payload(StageInventory, payload)


def test_stage_inventory_requires_exact_task_and_project_scope(tmp_path):
    tasks, store, task = _stores(tmp_path)
    with pytest.raises(ControlPlaneValidationError, match="project"):
        store.ensure_stage_inventory(
            _inventory(task, project_id="another"),
            actor="orchestrator",
        )
    with pytest.raises(ControlPlaneNotFoundError, match="Task not found"):
        store.ensure_stage_inventory(
            _inventory(task, suffix="_missing", task_id="task_missing"),
            actor="orchestrator",
        )
    with tasks._connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM control_stage_inventories"
        ).fetchone()[0] == 0


def test_stage_inventory_requires_frozen_task_state_first(tmp_path):
    tasks = TaskStore(tmp_path / "agora.db")
    task = tasks.create(
        CreateTaskRequest(
            project_id="agora",
            title="Inventory initialization ordering",
            kind="architecture",
        )
    )
    store = ControlPlaneStore(tasks)

    with pytest.raises(ControlPlaneConflictError, match="Task state"):
        store.ensure_stage_inventory(_inventory(task), actor="orchestrator")

    store.ensure_task_state(task.task_id)
    assert store.ensure_stage_inventory(
        _inventory(task), actor="orchestrator"
    ).task_id == task.task_id


def test_stage_inventory_conflicting_redefinition_fails_closed(tmp_path):
    _, store, task = _stores(tmp_path)
    store.ensure_stage_inventory(_inventory(task), actor="orchestrator")

    with pytest.raises(ControlPlaneConflictError, match="different immutable"):
        store.ensure_stage_inventory(
            _inventory(task, suffix="_changed"),
            actor="orchestrator",
        )


def test_concurrent_stage_inventory_writers_allow_one_definition(tmp_path):
    _, store, task = _stores(tmp_path)

    def initialize(suffix):
        try:
            return store.ensure_stage_inventory(
                _inventory(task, suffix=suffix),
                actor="orchestrator",
            )
        except ControlPlaneConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(initialize, ("_a", "_b")))

    assert sum(isinstance(item, StageInventory) for item in results) == 1
    assert sum(isinstance(item, ControlPlaneConflictError) for item in results) == 1
    assert store.get_stage_inventory(task.task_id) is not None


def test_stage_inventory_rolls_back_payload_and_events_together(
    tmp_path,
    monkeypatch,
):
    tasks, store, task = _stores(tmp_path)
    events_before = len(tasks.events(task.task_id))

    def fail_event(*_args, **_kwargs):
        raise RuntimeError("event persistence failed")

    monkeypatch.setattr(store, "_event", fail_event)
    with pytest.raises(RuntimeError, match="event persistence failed"):
        store.ensure_stage_inventory(_inventory(task), actor="orchestrator")

    assert store.get_stage_inventory(task.task_id) is None
    assert len(tasks.events(task.task_id)) == events_before


def test_formal_stages_must_match_the_immutable_inventory(tmp_path):
    _, store, task = _stores(tmp_path)
    store.ensure_stage_inventory(_inventory(task), actor="orchestrator")

    with pytest.raises(ControlPlaneConflictError, match="not part"):
        store.ensure_stage(
            task_id=task.task_id,
            stage_key="invented",
            gate_key="gate:invented",
        )
    with pytest.raises(ControlPlaneConflictError, match="different gate"):
        store.ensure_stage(
            task_id=task.task_id,
            stage_key="design",
            gate_key="gate:wrong",
        )
    with pytest.raises(ControlPlaneConflictError, match="authoritative route"):
        store.ensure_stage(
            task_id=task.task_id,
            stage_key="design",
            gate_key="gate:design",
        )
    with pytest.raises(ControlPlaneConflictError, match="authoritative route"):
        store.ensure_stage(
            task_id=task.task_id,
            stage_key="review",
            gate_key="gate:review",
            status=StageStatus.PENDING,
        )
    stage = store.activate_stage_route(
        task_id=task.task_id,
        expected_stage_key="design",
        actor="orchestrator",
        operation_key="route:design",
    ).route
    assert stage.stage_key == "design"
    assert stage.stage_status == StageStatus.READY


def test_persisted_stage_inventory_binding_is_revalidated_on_read(tmp_path):
    tasks, store, task = _stores(tmp_path)
    store.ensure_stage_inventory(_inventory(task), actor="orchestrator")
    with tasks._transaction() as db:
        db.execute(
            "UPDATE control_stage_inventories SET content_sha256 = ? WHERE task_id = ?",
            ("f" * 64, task.task_id),
        )

    with pytest.raises(ControlPlaneValidationError, match="ledger binding"):
        store.get_stage_inventory(task.task_id)
