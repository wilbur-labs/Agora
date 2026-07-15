from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agora.api.app import app
from agora.projects import ProjectRegistry
from agora.tasks.models import CreateTaskRequest
from agora.tasks.router import get_project_registry
from agora.tasks.store import TaskStore
from agora.workflows.models import (
    CreateWorkflowRequest, TransitionWorkflowStepRequest, WorkflowActionRequest,
    WorkflowState, WorkflowStepState,
)
from agora.workflows.router import get_workflow_store
from agora.workflows.store import WorkflowConflictError, WorkflowStore, WorkflowValidationError


def _request(*, cyclic: bool = False, task_id: str | None = None) -> CreateWorkflowRequest:
    return CreateWorkflowRequest(title="Ship across projects", steps=[
        {"key": "spec", "title": "Specify", "project_id": "alpha", "task_id": task_id,
         "adapter": "kiro", "prompt": "write spec", "depends_on": ["review"] if cyclic else []},
        {"key": "build", "title": "Build", "project_id": "beta", "adapter": "codex",
         "prompt": "implement", "depends_on": ["spec"]},
        {"key": "review", "title": "Review", "project_id": "beta", "adapter": "claude",
         "prompt": "review", "depends_on": ["build"]},
    ])


def _projects(tmp_path):
    return ProjectRegistry({"projects": {"registry_path": str(tmp_path / "projects.yaml"), "default": "alpha", "projects": {
        "alpha": {"name": "Alpha", "root": str(tmp_path / "alpha"), "workspaces": {}},
        "beta": {"name": "Beta", "root": str(tmp_path / "beta"), "workspaces": {}},
    }}})


def test_create_activate_and_project_readiness(tmp_path):
    tasks = TaskStore(tmp_path / "db.sqlite")
    task = tasks.create(CreateTaskRequest(project_id="alpha", title="Shared task"))
    store = WorkflowStore(tasks)
    workflow = store.create(_request(task_id=task.task_id))
    assert workflow.state == WorkflowState.DRAFT
    assert [step.state for step in workflow.steps] == [WorkflowStepState.PENDING] * 3
    assert workflow.steps[1].project_id == "beta"

    active = store.activate(workflow.workflow_id, WorkflowActionRequest(expected_version=1))
    assert active.version == 2
    assert [step.state for step in active.steps] == [
        WorkflowStepState.READY, WorkflowStepState.PENDING, WorkflowStepState.PENDING,
    ]
    assert store.list(project_id="beta")[0].ready_count == 1

    spec = active.steps[0]
    running = store.transition_step(active.workflow_id, spec.step_id, TransitionWorkflowStepRequest(
        target_state="running", expected_version=spec.version,
    ))
    spec = running.steps[0]
    advanced = store.transition_step(active.workflow_id, spec.step_id, TransitionWorkflowStepRequest(
        target_state="succeeded", expected_version=spec.version,
    ))
    assert advanced.steps[1].state == WorkflowStepState.READY
    assert [event.event_type for event in store.events(active.workflow_id)] == [
        "workflow.created", "workflow.activated", "workflow.step_transitioned", "workflow.step_transitioned",
    ]


def test_cycle_task_integrity_and_versions(tmp_path):
    tasks = TaskStore(tmp_path / "db.sqlite")
    store = WorkflowStore(tasks)
    with pytest.raises(WorkflowValidationError, match="cycle"):
        store.create(_request(cyclic=True))
    assert store.list() == []
    with pytest.raises(WorkflowValidationError, match="Unknown task_id"):
        store.create(_request(task_id="task_missing"))

    workflow = store.create(_request())
    with pytest.raises(WorkflowConflictError, match="version"):
        store.activate(workflow.workflow_id, WorkflowActionRequest(expected_version=9))
    with pytest.raises(ValueError, match="64 KiB"):
        CreateWorkflowRequest(title="Too large", steps=_request().steps, metadata={"blob": "x" * (64 * 1024)})


def test_failure_and_cancel_do_not_mutate_referenced_tasks(tmp_path):
    tasks = TaskStore(tmp_path / "db.sqlite")
    task = tasks.create(CreateTaskRequest(project_id="alpha", title="Shared task"))
    store = WorkflowStore(tasks)
    workflow = store.activate(store.create(_request(task_id=task.task_id)).workflow_id,
                              WorkflowActionRequest(expected_version=1))
    first = workflow.steps[0]
    workflow = store.transition_step(workflow.workflow_id, first.step_id, TransitionWorkflowStepRequest(
        target_state="running", expected_version=first.version,
    ))
    first = workflow.steps[0]
    failed = store.transition_step(workflow.workflow_id, first.step_id, TransitionWorkflowStepRequest(
        target_state="failed", expected_version=first.version,
    ))
    assert failed.state == WorkflowState.FAILED
    assert [step.state for step in failed.steps] == [
        WorkflowStepState.FAILED, WorkflowStepState.CANCELLED, WorkflowStepState.CANCELLED,
    ]
    assert tasks.get(task.task_id).state.value == "backlog"

    other = store.create(_request(task_id=task.task_id))
    cancelled = store.cancel(other.workflow_id, WorkflowActionRequest(expected_version=1, reason="stop"))
    assert cancelled.state == WorkflowState.CANCELLED
    assert all(step.state == WorkflowStepState.CANCELLED for step in cancelled.steps)
    assert tasks.get(task.task_id).state.value == "backlog"


def test_individual_step_cancellation_is_rejected_without_deadlocking_dependents(tmp_path):
    store = WorkflowStore(TaskStore(tmp_path / "cancel-step.sqlite"))
    workflow = store.activate(store.create(_request()).workflow_id, WorkflowActionRequest(expected_version=1))
    first = workflow.steps[0]
    with pytest.raises(WorkflowConflictError, match="Cannot transition"):
        store.transition_step(workflow.workflow_id, first.step_id, TransitionWorkflowStepRequest(
            target_state="cancelled", expected_version=first.version,
        ))
    unchanged = store.require(workflow.workflow_id)
    assert unchanged.state == WorkflowState.ACTIVE
    assert unchanged.steps[0].state == WorkflowStepState.READY
    assert unchanged.steps[1].state == WorkflowStepState.PENDING


def test_workflow_persistence_redacts_free_text_and_metadata(tmp_path):
    store = WorkflowStore(TaskStore(tmp_path / "redact.sqlite"))
    request = _request()
    request.title = "password=hunter2"
    request.description = "api_key=secret-value"
    request.steps[0].prompt = "access_token=do-not-store"
    request.metadata = {"password": "also-secret"}
    workflow = store.create(request)
    assert workflow.title == "password=[REDACTED]"
    assert workflow.description == "api_key=[REDACTED]"
    assert workflow.steps[0].prompt == "access_token=[REDACTED]"
    assert workflow.metadata == {"password": "[REDACTED]"}
    cancelled = store.cancel(workflow.workflow_id, WorkflowActionRequest(
        expected_version=1, reason="password=reason-secret",
    ))
    assert cancelled.state == WorkflowState.CANCELLED
    assert store.events(workflow.workflow_id)[-1].payload["reason"] == "password=[REDACTED]"

def test_workflow_api_round_trip_and_project_validation(tmp_path):
    store = WorkflowStore(TaskStore(tmp_path / "api.sqlite"))
    app.dependency_overrides[get_workflow_store] = lambda: store
    app.dependency_overrides[get_project_registry] = lambda: _projects(tmp_path)
    client = TestClient(app)
    try:
        created = client.post("/api/workflows", json=_request().model_dump(mode="json"))
        assert created.status_code == 201
        workflow = created.json()
        assert client.get(f"/api/workflows/{workflow['workflow_id']}").status_code == 200
        assert len(client.get("/api/workflows", params={"project_id": "beta"}).json()) == 1

        active = client.post(f"/api/workflows/{workflow['workflow_id']}/activate",
                             json={"expected_version": 1, "actor": "user"})
        assert active.status_code == 200
        assert active.json()["steps"][0]["state"] == "ready"
        conflict = client.post(f"/api/workflows/{workflow['workflow_id']}/activate",
                               json={"expected_version": 1, "actor": "user"})
        assert conflict.status_code == 409
        assert client.get(f"/api/workflows/{workflow['workflow_id']}/events").status_code == 200

        payload = _request().model_dump(mode="json")
        payload["steps"][0]["project_id"] = "missing"
        assert client.post("/api/workflows", json=payload).status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_fan_out_and_completion_are_atomic(tmp_path):
    tasks = TaskStore(tmp_path / "fanout.sqlite")
    store = WorkflowStore(tasks)
    request = CreateWorkflowRequest(title="Fan out", steps=[
        {"key": "root", "title": "Root", "project_id": "alpha", "adapter": "kiro", "prompt": "plan"},
        *[
            {"key": f"leaf-{index}", "title": f"Leaf {index}", "project_id": "beta",
             "adapter": "codex", "prompt": "build", "depends_on": ["root"]}
            for index in range(50)
        ],
    ])
    workflow = store.activate(store.create(request).workflow_id, WorkflowActionRequest(expected_version=1))
    root = workflow.steps[0]
    workflow = store.transition_step(workflow.workflow_id, root.step_id, TransitionWorkflowStepRequest(
        target_state="running", expected_version=root.version,
    ))
    root = workflow.steps[0]
    workflow = store.transition_step(workflow.workflow_id, root.step_id, TransitionWorkflowStepRequest(
        target_state="succeeded", expected_version=root.version,
    ))
    assert sum(step.state == WorkflowStepState.READY for step in workflow.steps) == 50

    for leaf in workflow.steps[1:]:
        current = store.require(workflow.workflow_id)
        step = next(item for item in current.steps if item.step_id == leaf.step_id)
        current = store.transition_step(current.workflow_id, step.step_id, TransitionWorkflowStepRequest(
            target_state="running", expected_version=step.version,
        ))
        step = next(item for item in current.steps if item.step_id == leaf.step_id)
        workflow = store.transition_step(current.workflow_id, step.step_id, TransitionWorkflowStepRequest(
            target_state="succeeded", expected_version=step.version,
        ))
    assert workflow.state == WorkflowState.COMPLETED
    with pytest.raises(WorkflowConflictError, match="not active"):
        store.transition_step(workflow.workflow_id, workflow.steps[-1].step_id, TransitionWorkflowStepRequest(
            target_state="running", expected_version=workflow.steps[-1].version,
        ))
