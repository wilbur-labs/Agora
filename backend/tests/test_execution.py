from __future__ import annotations

import asyncio
import sys

import pytest
from fastapi.testclient import TestClient

from agora.api.app import app
from agora.execution.adapters import ExecutionAdapter, build_adapter_registry
from agora.execution.dispatcher import ExecutionDispatcher, redact_output
from agora.execution.models import CancelRunRequest, CreateRunRequest, RunState
from agora.execution.router import get_execution_dispatcher, get_execution_store
from agora.execution.store import ExecutionStore, RunConflictError, RunValidationError
from agora.projects import ProjectRegistry
from agora.tasks.models import AppendEventRequest, CreateTaskRequest, TaskState
from agora.tasks.store import TaskStore


def _config(tmp_path, command: list[str]) -> dict:
    workspace = tmp_path / "workspaces" / "codex"
    workspace.mkdir(parents=True)
    return {
        "projects": {
            "registry_path": str(tmp_path / "projects.yaml"),
            "default": "alpha",
            "projects": {
                "alpha": {
                    "name": "Alpha",
                    "root": str(tmp_path / "alpha"),
                    "workspaces": {
                        "codex": str(workspace),
                        "claude": str(tmp_path / "workspaces" / "claude"),
                        "kiro": str(tmp_path / "workspaces" / "kiro"),
                    },
                }
            },
        },
        "execution": {
            "max_concurrent_global": 2,
            "max_concurrent_per_project": 1,
            "allowed_workspace_roots": [str(tmp_path / "workspaces")],
            "adapters": {
                "codex": {"command": [*command, "{prompt}"], "workspace_key": "codex"},
                "claude": {"enabled": False},
                "kiro": {"enabled": False},
            },
        },
    }


def _planned_task(tasks: TaskStore):
    task = tasks.create(CreateTaskRequest(project_id="alpha", title="Implement feature"))
    with tasks._transaction() as db:
        db.execute(
            "UPDATE tasks SET state = ?, version = 2 WHERE task_id = ?",
            (TaskState.PLANNED.value, task.task_id),
        )
    return tasks.get(task.task_id)


def _system(tmp_path, command: list[str], *, per_project: int = 1):
    config = _config(tmp_path, command)
    tasks = TaskStore(tmp_path / "agora.db")
    store = ExecutionStore(tasks)
    projects = ProjectRegistry(config)
    adapters = build_adapter_registry(config)
    dispatcher = ExecutionDispatcher(
        store, projects, adapters, max_concurrent_global=2, max_concurrent_per_project=per_project,
        allowed_workspace_roots=[tmp_path / "workspaces"],
    )
    return tasks, store, dispatcher


def _request(task_id: str, *, version: int = 2, timeout: int = 10) -> CreateRunRequest:
    return CreateRunRequest(
        task_id=task_id,
        adapter="codex",
        prompt="implement safely; echo $HOME && never shell-expand this",
        timeout_seconds=timeout,
        expected_task_version=version,
    )


def test_run_gate_is_atomic_and_events_are_reserved(tmp_path):
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))

    assert run.state == RunState.QUEUED
    assert run.command[-1] == "{prompt}"
    assert "$HOME" not in " ".join(run.command)
    updated = tasks.get(task.task_id)
    assert updated and updated.state == TaskState.RUNNING and updated.version == 3
    assert [event.event_type for event in tasks.events(task.task_id)][-2:] == [
        "state_changed", "run.queued"
    ]
    with pytest.raises(ValueError, match="reserved"):
        AppendEventRequest(event_type="run.succeeded")

    metadata_run = dispatcher.queue(CreateRunRequest(
        task_id=task.task_id, adapter="codex", prompt="next", timeout_seconds=10,
        expected_task_version=3, metadata={"api_key": "must-not-persist", "note": "password=hunter2"},
    ))
    assert metadata_run.result_metadata == {"api_key": "[REDACTED]", "note": "password=[REDACTED]"}

    other = tasks.create(CreateTaskRequest(project_id="alpha", title="Not ready"))
    with pytest.raises(RunConflictError, match="planned or running"):
        dispatcher.queue(_request(other.task_id, version=1))


def test_stale_task_and_run_versions_are_rejected(tmp_path):
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    task = _planned_task(tasks)
    with pytest.raises(RunConflictError, match="Expected task version"):
        dispatcher.queue(_request(task.task_id, version=1))
    run = dispatcher.queue(_request(task.task_id))
    with pytest.raises(RunConflictError, match="Expected run version"):
        store.cancel(run.run_id, CancelRunRequest(expected_version=9))


def test_cancellation_reason_is_redacted_in_run_and_event(tmp_path):
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))

    cancelled = store.cancel(
        run.run_id,
        CancelRunRequest(expected_version=run.version, reason="password=do-not-persist"),
    )

    assert cancelled.error_message == "password=[REDACTED]"
    event = tasks.events(task.task_id)[-1]
    assert event.payload["reason"] == "password=[REDACTED]"


def test_adapter_keeps_prompt_as_one_argv_item_and_validates_template():
    adapter = ExecutionAdapter("fake", ("tool", "--flag", "{prompt}"), "codex")
    prompt = "hello; rm -rf / && $(whoami)"
    assert adapter.build_command(prompt) == ["tool", "--flag", prompt]
    assert adapter.stored_command()[-1] == "{prompt}"
    with pytest.raises(ValueError, match="exactly one"):
        build_adapter_registry({"execution": {"adapters": {"codex": {"command": ["codex"]}}}})
    with pytest.raises(ValueError):
        CreateRunRequest(
            task_id="task", adapter="codex", prompt="x" * 16_001,
            expected_task_version=1,
        )


def test_workspace_must_be_under_an_explicit_allowed_root(tmp_path):
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    task = _planned_task(tasks)
    dispatcher.allowed_workspace_roots = [tmp_path / "somewhere-else"]
    with pytest.raises(RunValidationError, match="allowed roots"):
        dispatcher.queue(_request(task.task_id))


def test_dispatcher_exposes_only_bridge_correlation_environment(tmp_path):
    script = (
        "import os; print(os.environ['AGORA_TASK_ID']); print(os.environ['AGORA_RUN_ID']); "
        "print(os.environ['AGORA_PROJECT_ID'])"
    )
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", script])
    task = _planned_task(tasks)
    queued = dispatcher.queue(_request(task.task_id))
    result = asyncio.run(dispatcher.execute(queued.run_id))

    assert result.state == RunState.SUCCEEDED
    assert result.stdout_tail.splitlines() == [task.task_id, queued.run_id, "alpha"]


def test_workspace_confinement_is_rechecked_before_process_start(tmp_path):
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('must not run')"])
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))
    outside = tmp_path / "outside"
    outside.mkdir()
    with store._transaction() as db:
        db.execute("UPDATE execution_runs SET workspace = ? WHERE run_id = ?", (str(outside), run.run_id))

    result = asyncio.run(dispatcher.execute(run.run_id))

    assert result.state == RunState.FAILED
    assert result.error_message == "workspace is no longer allowed"


def test_dispatch_success_captures_and_redacts_output(tmp_path):
    script = "import sys; print('api_key=super-secret-value'); print(sys.argv[-1])"
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", script])
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))
    result = asyncio.run(dispatcher.execute(run.run_id))

    assert result.state == RunState.SUCCEEDED
    assert result.exit_code == 0
    assert "super-secret-value" not in result.stdout_tail
    assert "api_key=[REDACTED]" in result.stdout_tail
    assert "run.started" in [event.event_type for event in tasks.events(task.task_id)]
    assert "run.succeeded" in [event.event_type for event in tasks.events(task.task_id)]


def test_store_enforces_output_redaction_and_tail_limit(tmp_path):
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    task = _planned_task(tasks)
    queued = dispatcher.queue(_request(task.task_id))
    running = store.start(queued.run_id, expected_version=queued.version)

    result = store.finish(
        queued.run_id,
        RunState.FAILED,
        expected_version=running.version,
        exit_code=1,
        stdout_tail="x" * (70 * 1024) + " password=do-not-persist",
        stderr_tail="api_key=also-secret",
    )

    assert len(result.stdout_tail) <= 64 * 1024
    assert "do-not-persist" not in result.stdout_tail
    assert result.stderr_tail == "api_key=[REDACTED]"


def test_dispatch_failure_missing_workspace_and_recovery(tmp_path):
    tasks, store, dispatcher = _system(
        tmp_path, [sys.executable, "-c", "import sys; print('bad', file=sys.stderr); sys.exit(3)"]
    )
    task = _planned_task(tasks)
    failed = asyncio.run(dispatcher.execute(dispatcher.queue(_request(task.task_id)).run_id))
    assert failed.state == RunState.FAILED
    assert failed.exit_code == 3 and "bad" in failed.stderr_tail

    current = tasks.get(task.task_id)
    assert current
    queued = dispatcher.queue(_request(task.task_id, version=current.version))
    workspace = dispatcher.projects.get("alpha").workspaces["codex"]
    workspace.rmdir()
    missing = asyncio.run(dispatcher.execute(queued.run_id))
    assert missing.state == RunState.FAILED
    assert missing.error_message == "workspace not found"

    with store._transaction() as db:
        db.execute(
            "UPDATE execution_runs SET state = 'running', version = version + 1, pid = 999999 WHERE run_id = ?",
            (missing.run_id,),
        )
    recovered = store.recover_abandoned()
    assert recovered[0].state == RunState.ABANDONED


def test_timeout_and_cancel_running_process(tmp_path):
    tasks, store, dispatcher = _system(
        tmp_path, [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(20)"]
    )
    task = _planned_task(tasks)
    timed = dispatcher.queue(_request(task.task_id, timeout=1))
    timed_result = asyncio.run(dispatcher.execute(timed.run_id))
    assert timed_result.state == RunState.TIMED_OUT

    current = tasks.get(task.task_id)
    assert current
    cancellable = dispatcher.queue(_request(task.task_id, version=current.version, timeout=30))

    async def run_and_cancel():
        execution = asyncio.create_task(dispatcher.execute(cancellable.run_id))
        for _ in range(100):
            active = store.require(cancellable.run_id)
            if active.state == RunState.RUNNING and active.pid is not None:
                break
            await asyncio.sleep(0.01)
        active = store.require(cancellable.run_id)
        cancelled = await dispatcher.cancel(
            cancellable.run_id, CancelRunRequest(expected_version=active.version, reason="user stop")
        )
        completed = await execution
        return cancelled, completed

    cancelled, completed = asyncio.run(run_and_cancel())
    assert cancelled.state == RunState.CANCELLED
    assert completed.state == RunState.CANCELLED
    assert completed.version == cancelled.version + 1
    assert tasks.events(cancellable.task_id)[-1].event_type == "run.cancelled_output"


def test_stop_escalates_to_kill_after_asyncio_timeout(monkeypatch):
    class StubbornProcess:
        returncode = None
        killed = False

        def terminate(self):
            pass

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            return self.returncode

    async def force_timeout(awaitable, *, timeout):
        awaitable.close()
        raise asyncio.TimeoutError

    process = StubbornProcess()
    monkeypatch.setattr(asyncio, "wait_for", force_timeout)

    asyncio.run(ExecutionDispatcher._stop(process))  # type: ignore[arg-type]

    assert process.killed is True


def test_redaction_caps_common_secret_forms(monkeypatch):
    monkeypatch.setenv("DEMO_ACCESS_TOKEN", "environment-secret-value")
    output = "password: hunter2 ghp_abcdefghijklmnop environment-secret-value"
    redacted = redact_output(output)
    assert "hunter2" not in redacted
    assert "ghp_" not in redacted
    assert "environment-secret-value" not in redacted


def test_per_project_parallelism_is_bounded(tmp_path):
    log = tmp_path / "order.log"
    script = (
        "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); label=sys.argv[-1]; "
        "p.open('a').write('start:'+label+'\\n'); time.sleep(.15); "
        "p.open('a').write('end:'+label+'\\n')"
    )
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", script, str(log)])
    task = _planned_task(tasks)
    first = dispatcher.queue(CreateRunRequest(
        task_id=task.task_id, adapter="codex", prompt="one", timeout_seconds=10,
        expected_task_version=2,
    ))
    second = dispatcher.queue(CreateRunRequest(
        task_id=task.task_id, adapter="codex", prompt="two", timeout_seconds=10,
        expected_task_version=3,
    ))

    async def execute_both():
        return await asyncio.gather(dispatcher.execute(first.run_id), dispatcher.execute(second.run_id))

    asyncio.run(execute_both())
    entries = log.read_text(encoding="utf-8").splitlines()
    assert entries[0].startswith("start:") and entries[1].startswith("end:")
    assert entries[2].startswith("start:") and entries[3].startswith("end:")


def test_duplicate_dispatch_claim_launches_only_one_process(tmp_path):
    log = tmp_path / "launches.log"
    script = (
        "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); "
        "p.open('a').write('launched\\n'); time.sleep(.15)"
    )
    tasks, store, dispatcher = _system(
        tmp_path, [sys.executable, "-c", script, str(log)], per_project=2
    )
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))

    async def execute_twice():
        return await asyncio.gather(dispatcher.execute(run.run_id), dispatcher.execute(run.run_id))

    results = asyncio.run(execute_twice())

    assert log.read_text(encoding="utf-8").splitlines() == ["launched"]
    assert store.require(run.run_id).state == RunState.SUCCEEDED
    assert {result.state for result in results}.issubset({RunState.RUNNING, RunState.SUCCEEDED})


def test_queued_runs_resume_after_restart(tmp_path):
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('resumed')"])
    task = _planned_task(tasks)
    queued = dispatcher.queue(_request(task.task_id))

    async def resume():
        dispatcher.resume_queued()
        await asyncio.gather(*list(dispatcher._scheduled))

    asyncio.run(resume())
    assert store.require(queued.run_id).state == RunState.SUCCEEDED


def test_execution_api_create_filter_cancel_and_not_found(tmp_path):
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    task = _planned_task(tasks)
    dispatcher.schedule = lambda run_id: None  # type: ignore[method-assign]
    app.dependency_overrides[get_execution_store] = lambda: store
    app.dependency_overrides[get_execution_dispatcher] = lambda: dispatcher
    client = TestClient(app)
    try:
        created = client.post("/api/runs", json=_request(task.task_id).model_dump(mode="json"))
        assert created.status_code == 201
        run = created.json()
        assert run["state"] == "queued"
        listed = client.get("/api/runs", params={"project_id": "alpha", "state": "queued"})
        assert [item["run_id"] for item in listed.json()] == [run["run_id"]]
        linked = client.get(f"/api/tasks/{task.task_id}/runs", params={"limit": 1, "offset": 0})
        assert linked.status_code == 200
        cancelled = client.post(
            f"/api/runs/{run['run_id']}/cancel",
            json={"actor": "user", "expected_version": run["version"], "reason": "stop"},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["state"] == "cancelled"
        assert client.get("/api/runs/missing").status_code == 404
        assert client.get("/api/tasks/missing/runs").status_code == 404
    finally:
        app.dependency_overrides.clear()
