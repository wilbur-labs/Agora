from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agora.api.app import app
from agora.orchestration.router import get_task_orchestration_service
from agora.orchestration.processes import ProcessState
from agora.orchestration.runtime import RuntimeCommand, RuntimeResult
from agora.orchestration.service import TaskOrchestrationService
from agora.projects import ProjectRegistry
from agora.tasks.models import CreateTaskRequest
from agora.tasks.store import TaskStore


PASS = (
    '{"status":"pass","summary":"stage passed","findings":[],'
    '"recommended_next_action":"continue"}'
)


class FakeRunner:
    def __init__(self):
        self.results: list[RuntimeResult] = []

    async def run(self, _runtime, _prompt, **kwargs):
        await kwargs["on_process"](424_242)
        return self.results.pop(0) if self.results else RuntimeResult(0, PASS, "")


@pytest.fixture
def api_system(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    config = {
        "projects": {
            "registry_path": str(tmp_path / "projects.yaml"),
            "default": "alpha",
            "projects": {
                "alpha": {
                    "name": "Alpha",
                    "root": str(root),
                    "workspaces": {},
                }
            },
        }
    }
    tasks = TaskStore(tmp_path / "agora.db")
    service = TaskOrchestrationService(
        tasks,
        ProjectRegistry(config, project_root=tmp_path),
        {
            name: RuntimeCommand(adapter=name, command_template=("fake", "{prompt}"))
            for name in ("codex", "claude", "kiro")
        },
        runner=FakeRunner(),
    )
    app.dependency_overrides[get_task_orchestration_service] = lambda: service
    try:
        yield TestClient(app), tasks, service
    finally:
        app.dependency_overrides.pop(get_task_orchestration_service, None)


def test_create_and_read_bounded_orchestration(api_system):
    client, _, _ = api_system
    response = client.post("/api/orchestrations", json={
        "project_id": "alpha",
        "title": "Try the workbench",
        "description": "Plan a demo safely",
        "total_token_budget": 30_000,
        "total_cost_budget_usd": 10,
    })
    assert response.status_code == 201
    task = response.json()
    assert task["kind"] == "aidlc_foundation"
    assert task["metadata"]["methodology_provisional"] is True

    status_response = client.get(f"/api/tasks/{task['task_id']}/orchestration")
    assert status_response.status_code == 200
    payload = status_response.json()
    assert payload["plan"]["task_id"] == task["task_id"]
    assert payload["plan"]["provisional"] is True
    assert [stage["adapter"] for stage in payload["stages"]] == [
        "codex", "claude", "kiro",
    ]


def test_attach_run_retry_and_approve_actions_are_state_guarded(api_system):
    client, tasks, _ = api_system
    task = tasks.create(CreateTaskRequest(project_id="alpha", title="Existing task"))

    attached = client.post(
        f"/api/tasks/{task.task_id}/orchestration",
        json={"total_token_budget": 12_000},
    )
    assert attached.status_code == 201
    assert attached.json()["tokens_remaining"] == 12_000

    premature = client.post(
        f"/api/tasks/{task.task_id}/orchestration/approve",
        json={"reason": "too soon"},
    )
    assert premature.status_code == 409

    for adapter in ("codex", "claude", "kiro"):
        run = client.post(f"/api/tasks/{task.task_id}/orchestration/next")
        assert run.status_code == 200
        assert run.json()["adapter"] == adapter
        assert run.json()["state"] == "passed"

    approved = client.post(
        f"/api/tasks/{task.task_id}/orchestration/approve",
        json={"reason": "Reviewed in the demo"},
    )
    assert approved.status_code == 200
    assert approved.json()["plan"]["state"] == "ready_for_implementation"

    duplicate = client.post(f"/api/tasks/{task.task_id}/orchestration/next")
    assert duplicate.status_code == 409


def test_api_maps_missing_scope_and_rejects_unbounded_payloads(api_system):
    client, _, _ = api_system
    missing = client.get(
        "/api/tasks/task_00000000000000000000000000000000/orchestration"
    )
    assert missing.status_code == 404

    unknown_project = client.post("/api/orchestrations", json={
        "project_id": "unknown",
        "title": "No project",
    })
    assert unknown_project.status_code == 422

    too_small = client.post("/api/orchestrations", json={
        "project_id": "alpha",
        "title": "Bad budget",
        "total_token_budget": 2_999,
    })
    assert too_small.status_code == 422

    extra_field = client.post("/api/orchestrations", json={
        "project_id": "alpha",
        "title": "Unexpected input",
        "unknown": True,
    })
    assert extra_field.status_code == 422


def test_blocked_stage_can_be_retried_through_api(api_system):
    client, _, service = api_system
    service.runner.results.append(RuntimeResult(
        0,
        '{"status":"needs_work","summary":"revise","findings":["gap"],'
        '"recommended_next_action":"retry"}',
        "",
    ))
    created = client.post("/api/orchestrations", json={
        "project_id": "alpha", "title": "Blocked demo",
    }).json()

    blocked = client.post(f"/api/tasks/{created['task_id']}/orchestration/next")
    assert blocked.status_code == 200
    assert blocked.json()["state"] == "blocked"

    retried = client.post(
        f"/api/tasks/{created['task_id']}/orchestration/stages/solution_design/retry"
    )
    assert retried.status_code == 200
    assert retried.json()["plan"]["state"] == "active"
    assert retried.json()["stages"][0]["state"] == "pending"
    assert len(retried.json()["runs"]) == 1


def test_resume_api_refuses_duplicate_dispatch_for_live_pid(api_system):
    client, _, service = api_system
    created = client.post("/api/orchestrations", json={
        "project_id": "alpha", "title": "Live process demo",
    }).json()
    status_payload = service.status(created["task_id"])
    run = service.store.claim_current_stage(
        created["task_id"],
        prompt_sha256="a" * 64,
        operation_key=f"{status_payload.plan.plan_id}:solution_design:manual",
    )
    service.store.attach_pid(run.run_id, 424_242)
    service.process_inspector = lambda _pid: ProcessState.ALIVE

    response = client.post(
        f"/api/tasks/{created['task_id']}/orchestration/resume"
    )
    assert response.status_code == 409
    assert "refusing duplicate dispatch" in response.json()["detail"]
    assert service.status(created["task_id"]).runs[0].state.value == "running"
