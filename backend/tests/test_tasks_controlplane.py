from __future__ import annotations

from fastapi.testclient import TestClient

from agora.api.app import app
from agora.projects import ProjectRegistry
from agora.tasks.models import CreateTaskRequest, TaskState
from agora.tasks.router import get_project_registry, get_task_store
from agora.tasks.store import (
    InvalidTransitionError,
    StaleTaskVersionError,
    TaskStore,
)


def _projects(tmp_path) -> ProjectRegistry:
    return ProjectRegistry(
        {
            "projects": {
                "registry_path": str(tmp_path / "registry.yaml"),
                "default": "alpha",
                "projects": {
                    "alpha": {
                        "name": "Alpha",
                        "root": str(tmp_path / "alpha"),
                        "workspaces": {},
                    },
                    "beta": {
                        "name": "Beta",
                        "root": str(tmp_path / "beta"),
                        "workspaces": {},
                    },
                    "alpha-team": {
                        "name": "Alpha Team",
                        "root": str(tmp_path / "alpha-team"),
                        "workspaces": {},
                    },
                },
            }
        }
    )


def _request(project_id: str = "alpha", title: str = "Define requirements") -> CreateTaskRequest:
    return CreateTaskRequest(
        project_id=project_id,
        title=title,
        kind="feature",
        primary_agent="kiro",
        reviewers=["claude"],
        acceptance=["requirements are approved", "tests pass"],
        budget={"max_cost_usd": 10, "max_minutes": 60},
    )


def test_store_create_filter_transition_and_events(tmp_path):
    store = TaskStore(tmp_path / "agora.db")
    alpha = store.create(_request())
    store.create(_request("beta", "Other project"))

    assert alpha.state == TaskState.BACKLOG
    assert alpha.version == 1
    assert [task.task_id for task in store.list(project_id="alpha")] == [alpha.task_id]

    requirements = store.transition(
        alpha.task_id,
        TaskState.REQUIREMENTS,
        actor="kiro",
        expected_version=1,
    )
    assert requirements.version == 2
    assert requirements.state == TaskState.REQUIREMENTS

    events = store.events(alpha.task_id)
    assert [event.event_type for event in events] == ["task_created", "state_changed"]
    assert events[1].payload["from"] == "backlog"
    assert events[1].payload["to"] == "requirements"


def test_store_rejects_invalid_and_stale_transitions(tmp_path):
    store = TaskStore(tmp_path / "agora.db")
    task = store.create(_request())

    try:
        store.transition(task.task_id, TaskState.RUNNING, actor="codex")
        raise AssertionError("invalid transition was accepted")
    except InvalidTransitionError:
        pass

    store.transition(task.task_id, TaskState.REQUIREMENTS, actor="kiro", expected_version=1)
    try:
        store.transition(task.task_id, TaskState.DESIGN, actor="claude", expected_version=1)
        raise AssertionError("stale transition was accepted")
    except StaleTaskVersionError:
        pass


def test_task_api_lifecycle_and_validation(tmp_path):
    store = TaskStore(tmp_path / "api.db")
    projects = _projects(tmp_path)
    app.dependency_overrides[get_task_store] = lambda: store
    app.dependency_overrides[get_project_registry] = lambda: projects
    client = TestClient(app)
    try:
        response = client.post("/api/tasks", json=_request().model_dump(mode="json"))
        assert response.status_code == 201
        task = response.json()
        assert task["state"] == "backlog"

        response = client.patch(
            f"/api/tasks/{task['task_id']}/state",
            json={
                "target_state": "requirements",
                "actor": "kiro",
                "expected_version": 1,
            },
        )
        assert response.status_code == 200
        assert response.json()["version"] == 2

        response = client.post(
            f"/api/tasks/{task['task_id']}/events",
            json={"event_type": "requirements.approved", "actor": "user", "payload": {"version": 1}},
        )
        assert response.status_code == 201

        events = client.get(f"/api/tasks/{task['task_id']}/events").json()
        assert [event["event_type"] for event in events] == [
            "task_created",
            "state_changed",
            "requirements.approved",
        ]

        filtered = client.get("/api/tasks", params={"project_id": "alpha", "state": "requirements"})
        assert filtered.status_code == 200
        assert [item["task_id"] for item in filtered.json()] == [task["task_id"]]

        invalid = client.patch(
            f"/api/tasks/{task['task_id']}/state",
            json={"target_state": "done", "expected_version": 2},
        )
        assert invalid.status_code == 409

        unknown = client.post("/api/tasks", json=_request("missing").model_dump(mode="json"))
        assert unknown.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_task_api_cancel_not_found_and_reserved_events(tmp_path):
    store = TaskStore(tmp_path / "api-edge.db")
    projects = _projects(tmp_path)
    app.dependency_overrides[get_task_store] = lambda: store
    app.dependency_overrides[get_project_registry] = lambda: projects
    client = TestClient(app)
    try:
        dashed = _request("alpha-team")
        created = client.post("/api/tasks", json=dashed.model_dump(mode="json"))
        assert created.status_code == 201
        task = created.json()

        reserved = client.post(
            f"/api/tasks/{task['task_id']}/events",
            json={"event_type": "state_changed", "actor": "user"},
        )
        assert reserved.status_code == 422

        cancelled = client.delete(
            f"/api/tasks/{task['task_id']}",
            params={"reason": "no longer needed", "expected_version": 1},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["state"] == "cancelled"

        terminal = client.delete(f"/api/tasks/{task['task_id']}")
        assert terminal.status_code == 409

        assert client.get("/api/tasks/missing").status_code == 404
        assert client.get("/api/tasks/missing/events").status_code == 404
        assert client.patch(
            "/api/tasks/missing/state", json={"target_state": "requirements"}
        ).status_code == 404
    finally:
        app.dependency_overrides.clear()
