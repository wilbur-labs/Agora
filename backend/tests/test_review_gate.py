"""The cross-review gate: a human settling a review advances the task."""
from __future__ import annotations

from fastapi.testclient import TestClient

from agora.api.app import app
from agora.attention.models import (
    AttentionKind, CreateAttentionRequest, REVIEW_REQUESTER, ResponseAction,
)
from agora.attention.review_gate import settle_review_approval
from agora.attention.router import get_attention_store
from agora.attention.store import AttentionStore
from agora.tasks.models import CreateTaskRequest, TaskState
from agora.tasks.router import get_task_store
from agora.tasks.store import TaskStore


def _running_system(tmp_path):
    tasks = TaskStore(tmp_path / "agora.db")
    task = tasks.create(CreateTaskRequest(project_id="alpha", title="Ship feature"))
    # Queueing a run is what normally moves planned -> running; force it directly
    # so the gate test does not depend on the spec-approval path into design.
    with tasks._transaction() as db:
        db.execute(
            "UPDATE tasks SET state = ?, version = version + 1 WHERE task_id = ?",
            (TaskState.RUNNING.value, task.task_id),
        )
    return tasks, AttentionStore(tasks), tasks.get(task.task_id)


def _review_approval(store: AttentionStore, task_id: str):
    return store.create(
        CreateAttentionRequest(
            task_id=task_id,
            kind=AttentionKind.APPROVAL,
            title="评审完成,待你批准(评审:claude)",
            body="Looks correct.",
            requester=REVIEW_REQUESTER,
        )
    )


def test_approve_advances_running_task_to_verified(tmp_path):
    tasks, store, task = _running_system(tmp_path)
    item = _review_approval(store, task.task_id)

    result = settle_review_approval(tasks, item, ResponseAction.APPROVE, actor="user")

    assert result is not None and result.state == TaskState.VERIFIED
    assert tasks.get(task.task_id).state == TaskState.VERIFIED


def test_reject_sends_running_task_to_blocked(tmp_path):
    tasks, store, task = _running_system(tmp_path)
    item = _review_approval(store, task.task_id)

    result = settle_review_approval(tasks, item, ResponseAction.REJECT, actor="user")

    assert result is not None and result.state == TaskState.BLOCKED


def test_non_review_approval_is_ignored(tmp_path):
    tasks, store, task = _running_system(tmp_path)
    item = store.create(
        CreateAttentionRequest(
            task_id=task.task_id, kind=AttentionKind.APPROVAL,
            title="Deploy to prod?", requester="codex",
        )
    )

    assert settle_review_approval(tasks, item, ResponseAction.APPROVE, actor="user") is None
    assert tasks.get(task.task_id).state == TaskState.RUNNING


def test_answer_action_does_not_advance(tmp_path):
    tasks, store, task = _running_system(tmp_path)
    item = _review_approval(store, task.task_id)

    assert settle_review_approval(tasks, item, ResponseAction.ANSWER, actor="user") is None
    assert tasks.get(task.task_id).state == TaskState.RUNNING


def test_already_verified_task_is_left_alone(tmp_path):
    tasks, store, task = _running_system(tmp_path)
    with tasks._transaction() as db:
        db.execute(
            "UPDATE tasks SET state = ?, version = version + 1 WHERE task_id = ?",
            (TaskState.VERIFIED.value, task.task_id),
        )
    item = _review_approval(store, task.task_id)

    assert settle_review_approval(tasks, item, ResponseAction.APPROVE, actor="user") is None
    assert tasks.get(task.task_id).state == TaskState.VERIFIED


def test_respond_endpoint_advances_task_on_approval(tmp_path):
    tasks, store, task = _running_system(tmp_path)
    item = _review_approval(store, task.task_id)
    app.dependency_overrides[get_attention_store] = lambda: store
    app.dependency_overrides[get_task_store] = lambda: tasks
    try:
        with TestClient(app) as client:
            resp = client.post(
                f"/api/attention/{item.item_id}/respond",
                json={"action": "approve", "response": "ok", "actor": "user", "expected_version": 1},
            )
            assert resp.status_code == 200 and resp.json()["state"] == "responded"
            assert tasks.get(task.task_id).state == TaskState.VERIFIED
    finally:
        app.dependency_overrides.clear()
