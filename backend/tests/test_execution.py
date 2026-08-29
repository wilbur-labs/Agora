from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import subprocess
import sys
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agora.api.app import app
from agora.execution.adapters import ExecutionAdapter, build_adapter_registry
from agora.execution.dispatcher import ExecutionDispatcher, redact_output
from agora.execution.models import (
    CancelRunRequest,
    CreateRunRequest,
    ROLE_REVIEW,
    ROLE_WORKER,
    RunState,
)
from agora.execution.router import get_execution_dispatcher, get_execution_store
from agora.execution.store import (
    ExecutionStore,
    GATE_CODE_ATTENTION_OPEN,
    GATE_CODE_REVIEW_ACTIVE,
    RunConflictError,
    RunGateError,
    RunValidationError,
)
from agora.projects import ProjectRegistry
from agora.tasks.models import AppendEventRequest, CreateTaskRequest, TaskState
from agora.tasks.store import TaskStore
from agora.attention.store import AttentionStore
from agora.attention.run_scope import ORPHANED_PROMPT_REASON
from agora.attention.bridges.models import BridgeEventRequest, BridgeVendor, DeliveryMode
from agora.attention.models import (
    AttentionKind,
    AttentionState,
    AttentionUrgency,
    CancelAttentionRequest,
    CreateAttentionRequest,
    RespondAttentionRequest,
    ResponseAction,
    REVIEW_REQUESTER,
)
from agora.workspaces.naming import worktree_branch

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git is required to provision run worktrees")


def _provision_codex_worktree(tmp_path):
    """Set up alpha as its own repo and its codex workspace as a provisioned
    linked worktree on the expected branch — the shape execution now requires."""
    root = tmp_path / "alpha"
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run([GIT, "-C", str(root), "init", "-q"], check=True, timeout=15)
    (root / "seed.txt").write_text("seed", encoding="utf-8")
    subprocess.run([GIT, "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t",
                    "add", "-A"], check=True, timeout=15)
    subprocess.run([GIT, "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "seed"], check=True, timeout=15)
    (tmp_path / "workspaces").mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspaces" / "codex"
    branch = worktree_branch("alpha", "codex")
    subprocess.run([GIT, "-C", str(root), "worktree", "add", "-q", "-b", branch,
                    str(workspace), "HEAD"], check=True, timeout=15)
    return workspace


def _config(tmp_path, command: list[str]) -> dict:
    workspace = _provision_codex_worktree(tmp_path)
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


def _system(tmp_path, command: list[str], *, per_project: int = 1, auto_review: bool = False):
    config = _config(tmp_path, command)
    tasks = TaskStore(tmp_path / "agora.db")
    store = ExecutionStore(tasks)
    projects = ProjectRegistry(config)
    adapters = build_adapter_registry(config)
    dispatcher = ExecutionDispatcher(
        store, projects, adapters, max_concurrent_global=2, max_concurrent_per_project=per_project,
        allowed_workspace_roots=[tmp_path / "workspaces"], auto_review=auto_review,
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

    # Settle the first run before queueing another: a task may hold only one
    # unfinished worker run, so this is also the shape a retry takes.
    store.cancel(run.run_id, CancelRunRequest(expected_version=run.version))
    metadata_run = dispatcher.queue(CreateRunRequest(
        task_id=task.task_id, adapter="codex", prompt="next", timeout_seconds=10,
        expected_task_version=3, metadata={"api_key": "must-not-persist", "note": "password=hunter2"},
    ))
    assert metadata_run.result_metadata == {
        "api_key": "[REDACTED]", "note": "password=[REDACTED]", "agora_role": "worker",
        # The task version this run was validated against, recorded so a QUEUED row
        # can be re-checked later instead of outliving the state it was checked
        # against. The task was already running here, so it is the version the
        # request carried, not a bumped one.
        "task_version": 3,
    }

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
    with pytest.raises(ValueError, match="app_server_command"):
        build_adapter_registry({"execution": {"adapters": {
            "codex": {"bridge_mode": "codex_app_server", "app_server_command": "codex app-server"}
        }}})


def test_adapter_capabilities_do_not_overstate_delivery():
    cli = ExecutionAdapter("claude", ("claude", "{prompt}"), "claude")
    assert cli.capability().attention_mode == "capture_only"
    assert cli.capability().supports_tool_approval is False
    codex = replace(
        ExecutionAdapter("codex", ("codex", "{prompt}"), "codex"),
        bridge_mode="codex_app_server",
    )
    capability = codex.capability()
    assert capability.attention_mode == "bidirectional"
    assert capability.supports_tool_approval is True
    assert capability.supports_user_questions is False


def test_execution_adapter_capability_api(monkeypatch, tmp_path):
    _, _, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    dispatcher.adapters["codex"] = replace(
        dispatcher.adapters["codex"], bridge_mode="codex_app_server",
    )
    app.dependency_overrides[get_execution_dispatcher] = lambda: dispatcher
    try:
        response = TestClient(app).get("/api/execution-adapters")
        assert response.status_code == 200
        assert response.json() == [{
            "name": "codex",
            "execution_mode": "codex_app_server",
            "attention_mode": "bidirectional",
            "supports_tool_approval": True,
            "supports_user_questions": False,
            "detail": "Stable command and file approvals are delivered through Codex app-server; user questions remain experimental.",
        }]
    finally:
        app.dependency_overrides.clear()


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


def test_codex_app_server_full_approval_round_trip(tmp_path):
    script = r'''
import json, sys
def read(): return json.loads(sys.stdin.readline())
def send(value): print(json.dumps(value), flush=True)
msg = read(); assert msg["method"] == "initialize"; send({"id": msg["id"], "result": {}})
assert read()["method"] == "initialized"
msg = read(); assert msg["method"] == "thread/start"; send({"id": msg["id"], "result": {"thread": {"id": "thread-test"}}})
msg = read(); assert msg["method"] == "turn/start"
# Exercise a legal server request arriving before the turn/start response.
send({"id": 77, "method": "item/commandExecution/requestApproval", "params": {"threadId": "thread-test", "turnId": "turn-test", "itemId": "item-test", "startedAtMs": 1, "command": "git push", "reason": "test"}})
response = read(); assert response == {"id": 77, "result": {"decision": "accept"}}
# Withhold turn/start until the interleaved approval is answered.
send({"id": msg["id"], "result": {"turn": {"id": "turn-test"}}})
send({"method": "turn/completed", "params": {"threadId": "thread-test", "turn": {"id": "turn-test", "status": "completed"}}})
'''
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('unused')"])
    task = _planned_task(tasks)
    dispatcher.adapters["codex"] = replace(
        dispatcher.adapters["codex"], bridge_mode="codex_app_server",
        app_server_command=(sys.executable, "-u", "-c", script),
    )
    queued = dispatcher.queue(_request(task.task_id))
    assert queued.command[:2] == [sys.executable, "-u"]

    async def scenario():
        execution = asyncio.create_task(dispatcher.execute(queued.run_id))
        attention = AttentionStore(tasks)
        item = None
        for _ in range(100):
            open_items = attention.list(run_id=queued.run_id)
            if open_items:
                item = open_items[0]
                break
            await asyncio.sleep(0.02)
        assert item is not None
        await asyncio.to_thread(attention.respond, item.item_id, RespondAttentionRequest(
            action="approve", response="ship it", expected_version=item.version,
        ))
        return await asyncio.wait_for(execution, timeout=10), item.item_id

    result, item_id = asyncio.run(scenario())
    assert result.state == RunState.SUCCEEDED
    with tasks._connect() as db:
        delivery = db.execute(
            "SELECT delivery_state FROM attention_bridge_events WHERE item_id = ?", (item_id,),
        ).fetchone()
    assert delivery["delivery_state"] == "delivered"


@pytest.mark.parametrize("mode", ["invalid_json", "timeout"])
def test_codex_app_server_failure_and_timeout_are_durable(tmp_path, mode):
    if mode == "invalid_json":
        script = "import sys; sys.stdin.readline(); print('not-json', flush=True)"
        timeout = 5
    else:
        script = r'''
import json, sys, time
def read(): return json.loads(sys.stdin.readline())
def send(value): print(json.dumps(value), flush=True)
msg=read(); send({"id": msg["id"], "result": {}}); read()
msg=read(); send({"id": msg["id"], "result": {"thread": {"id": "thread"}}})
msg=read(); send({"id": msg["id"], "result": {"turn": {"id": "turn"}}})
time.sleep(30)
'''
        timeout = 1
    tasks, _, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('unused')"])
    task = _planned_task(tasks)
    dispatcher.adapters["codex"] = replace(
        dispatcher.adapters["codex"], bridge_mode="codex_app_server",
        app_server_command=(sys.executable, "-u", "-c", script),
    )
    queued = dispatcher.queue(_request(task.task_id, timeout=timeout))
    result = asyncio.run(dispatcher.execute(queued.run_id))
    assert result.state == (RunState.FAILED if mode == "invalid_json" else RunState.TIMED_OUT)
    assert result.pid is None
    assert ("invalid JSON" in result.error_message if mode == "invalid_json" else "timeout" in result.error_message)


def test_codex_app_server_cancellation_stops_process(tmp_path):
    script = r'''
import json, sys, time
def read(): return json.loads(sys.stdin.readline())
def send(value): print(json.dumps(value), flush=True)
msg=read(); send({"id": msg["id"], "result": {}}); read()
msg=read(); send({"id": msg["id"], "result": {"thread": {"id": "thread"}}})
msg=read(); send({"id": msg["id"], "result": {"turn": {"id": "turn"}}})
time.sleep(30)
'''
    tasks, _, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('unused')"])
    task = _planned_task(tasks)
    dispatcher.adapters["codex"] = replace(
        dispatcher.adapters["codex"], bridge_mode="codex_app_server",
        app_server_command=(sys.executable, "-u", "-c", script),
    )
    queued = dispatcher.queue(_request(task.task_id, timeout=30))

    async def scenario():
        execution = asyncio.create_task(dispatcher.execute(queued.run_id))
        for _ in range(100):
            current = dispatcher.store.require(queued.run_id)
            if current.pid is not None:
                break
            await asyncio.sleep(0.02)
        assert current.pid is not None
        cancelled = await dispatcher.cancel(
            queued.run_id, CancelRunRequest(expected_version=current.version, reason="user stop"),
        )
        result = await asyncio.wait_for(execution, timeout=10)
        return cancelled, result

    cancelled, result = asyncio.run(scenario())
    assert cancelled.state == RunState.CANCELLED
    assert result.state == RunState.CANCELLED and result.pid is None


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


def test_auto_review_dispatches_review_run_after_worker(tmp_path):
    """A succeeded worker run drives _after_run -> await _dispatch_review, which
    queues an independent review run by the project's reviewer agent."""
    root = tmp_path / "alpha"
    _provision_codex_worktree(tmp_path)  # alpha repo + codex worktree
    claude_ws = tmp_path / "workspaces" / "claude"
    subprocess.run([GIT, "-C", str(root), "worktree", "add", "-q", "-b",
                    worktree_branch("alpha", "claude"), str(claude_ws), "HEAD"],
                   check=True, timeout=15)
    ok = [sys.executable, "-c", "print('ok')", "{prompt}"]
    config = {
        "projects": {"registry_path": str(tmp_path / "projects.yaml"), "default": "alpha", "projects": {
            "alpha": {"name": "Alpha", "root": str(root), "worker_agent": "codex", "reviewer_agent": "claude",
                      "workspaces": {"codex": str(tmp_path / "workspaces" / "codex"), "claude": str(claude_ws)}}}},
        "execution": {"adapters": {
            "codex": {"command": ok, "workspace_key": "codex"},
            "claude": {"command": ok, "workspace_key": "claude"},
            "kiro": {"enabled": False}}},
    }
    tasks = TaskStore(tmp_path / "agora.db")
    store = ExecutionStore(tasks)
    dispatcher = ExecutionDispatcher(
        store, ProjectRegistry(config), build_adapter_registry(config),
        allowed_workspace_roots=[tmp_path / "workspaces"], auto_review=True,
    )
    task = _planned_task(tasks)
    worker = dispatcher.queue(_request(task.task_id))

    async def run_and_drain():
        await dispatcher.execute(worker.run_id)
        for _ in range(50):
            pending = list(dispatcher._scheduled)
            if not pending:
                break
            await asyncio.gather(*pending, return_exceptions=True)

    asyncio.run(run_and_drain())

    roles = {r.run_id: store.require(r.run_id).result_metadata.get("agora_role")
             for r in store.list(task_id=task.task_id)}
    reviews = [rid for rid, role in roles.items() if role == "review"]
    assert len(reviews) == 1
    assert store.require(reviews[0]).adapter == "claude"


def test_second_worker_run_is_refused_while_one_is_unfinished(tmp_path):
    """Two clients can each observe "nothing is running" and POST with the same
    expected_task_version: the task is already RUNNING, so queueing does not bump
    its version and the version check passes twice. Only the create transaction
    can refuse the second one, and it must refuse it by *role* so cross-review —
    two runs on one task by design — still works."""
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    task = _planned_task(tasks)
    first = dispatcher.queue(_request(task.task_id))
    running = tasks.get(task.task_id)
    assert running and running.version == 3

    # Same version both times: this is the two-tab race, not a stale write.
    with pytest.raises(RunConflictError, match="unfinished worker run"):
        dispatcher.queue(_request(task.task_id, version=3))
    assert len(store.list(task_id=task.task_id)) == 1

    # A review run is a different role, so it is not blocked by the worker run.
    # The role is a server-side argument — a client cannot ask for it (see
    # test_a_client_cannot_choose_a_run_role).
    review = dispatcher.queue(
        CreateRunRequest(
            task_id=task.task_id, adapter="codex", prompt="review it", timeout_seconds=10,
            expected_task_version=3,
        ),
        role=ROLE_REVIEW,
    )
    assert review.result_metadata["agora_role"] == "review"
    # ...but a second review is, for the same reason as the worker.
    with pytest.raises(RunConflictError, match="unfinished review run"):
        dispatcher.queue(
            CreateRunRequest(
                task_id=task.task_id, adapter="codex", prompt="review again", timeout_seconds=10,
                expected_task_version=3,
            ),
            role=ROLE_REVIEW,
        )

    # Once the first run settles, the next worker run is allowed: this is the
    # retry path, and the guard is about overlap, not about a run limit. The
    # queued review above has to go first, though — Run -> Gate ordering refuses a
    # worker run while a review of this task is unsettled (see
    # docs/architecture/run-gate-ordering-v1.md), and that refusal carries its own
    # code rather than the same-role wording.
    store.cancel(first.run_id, CancelRunRequest(expected_version=first.version))
    with pytest.raises(RunGateError, match="under review") as gated:
        dispatcher.queue(_request(task.task_id, version=3))
    assert gated.value.code == GATE_CODE_REVIEW_ACTIVE
    store.cancel(review.run_id, CancelRunRequest(expected_version=review.version))
    assert dispatcher.queue(_request(task.task_id, version=3)).state == RunState.QUEUED


def test_untagged_runs_count_as_worker_runs_for_the_overlap_guard(tmp_path):
    """Runs recorded before role tags existed carry none. The gating reading is
    that such a run was somebody's implementation work, so it blocks a worker
    run rather than being waved through as "not a worker"."""
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))
    with store._transaction() as db:
        db.execute("UPDATE execution_runs SET result_metadata = '{}' WHERE run_id = ?", (run.run_id,))

    with pytest.raises(RunConflictError, match="unfinished worker run"):
        dispatcher.queue(_request(task.task_id, version=3))


def test_api_refuses_the_duplicate_dispatch_with_409(tmp_path):
    """The refusal reaches the client as a 409 — the status its dispatch failure
    classification already resolves by re-reading the task's active runs."""
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    task = _planned_task(tasks)
    dispatcher.schedule = lambda run_id: None  # type: ignore[method-assign]
    app.dependency_overrides[get_execution_store] = lambda: store
    app.dependency_overrides[get_execution_dispatcher] = lambda: dispatcher
    client = TestClient(app)
    try:
        payload = _request(task.task_id).model_dump(mode="json")
        assert client.post("/api/runs", json=payload).status_code == 201
        # Same body, same expected_task_version: the second tab's request.
        duplicate = client.post("/api/runs", json={**payload, "expected_task_version": 3})
        assert duplicate.status_code == 409
        assert "unfinished worker run" in duplicate.json()["detail"]
        # And the client's own question — "is a run active?" — is answerable
        # without inferring it from a truncated history.
        active = client.get("/api/runs", params={"task_id": task.task_id, "state": "queued"})
        assert len(active.json()) == 1
    finally:
        app.dependency_overrides.clear()


def test_a_client_cannot_choose_a_run_role(tmp_path):
    """The role decides which unfinished run blocks a new one, so a client able to
    set it could name a role nothing matches and start a second concurrent paid
    run on the same worktree — exactly what the same-role guard exists to stop.
    Refused on input (like AppendEventRequest's reserved event types) rather than
    silently rewritten: quietly changing a field the caller set would hide whether
    the caller was confused or hostile."""
    with pytest.raises(ValueError, match="reserved"):
        CreateRunRequest(
            task_id="t1", adapter="codex", prompt="anything",
            expected_task_version=1, metadata={"agora_role": "anything"},
        )
    with pytest.raises(ValueError, match="reserved"):
        CreateRunRequest(
            task_id="t1", adapter="codex", prompt="anything",
            expected_task_version=1, metadata={"reviews_run_id": "run_forged"},
        )
    # And the recorded task version, for the same reason one level along: it is what
    # the resume sweep and the pre-launch re-check compare against, so a client that
    # could set it could claim a version the task never had and walk past the
    # staleness question rather than answer it.
    with pytest.raises(ValueError, match="reserved"):
        CreateRunRequest(
            task_id="t1", adapter="codex", prompt="anything",
            expected_task_version=1, metadata={"task_version": 999},
        )

    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    task = _planned_task(tasks)
    dispatcher.schedule = lambda run_id: None  # type: ignore[method-assign]
    app.dependency_overrides[get_execution_store] = lambda: store
    app.dependency_overrides[get_execution_dispatcher] = lambda: dispatcher
    client = TestClient(app)
    try:
        forged = client.post("/api/runs", json={
            **_request(task.task_id).model_dump(mode="json"),
            "metadata": {"agora_role": "anything"},
        })
        assert forged.status_code == 422
        assert store.list(task_id=task.task_id) == []
        # A run created from a client request is a worker run, and the guard fires.
        first = client.post("/api/runs", json=_request(task.task_id).model_dump(mode="json"))
        assert first.status_code == 201
        assert first.json()["result_metadata"]["agora_role"] == "worker"
        second = client.post("/api/runs", json={
            **_request(task.task_id).model_dump(mode="json"), "expected_task_version": 3,
        })
        assert second.status_code == 409
    finally:
        app.dependency_overrides.clear()

    # The internal dispatch still marks a review run, because the role travels as
    # a server-side argument rather than in the request.
    review = dispatcher.queue(
        CreateRunRequest(
            task_id=task.task_id, adapter="codex", prompt="review it",
            timeout_seconds=10, expected_task_version=3,
        ),
        role=ROLE_REVIEW,
        reviews_run_id=first.json()["run_id"],
    )
    assert review.result_metadata["agora_role"] == "review"
    assert review.result_metadata["reviews_run_id"] == first.json()["run_id"]


def test_run_list_answers_several_states_in_one_query(tmp_path):
    """"Is any run of this task unsettled?" is one fact, so it must come from one
    read: asked as ``state=queued`` and then ``state=running``, a run that moves
    between the two requests appears in neither answer and the caller concludes
    nothing is active."""
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    queued = dispatcher.queue(_request(_planned_task(tasks).task_id))
    running = dispatcher.queue(_request(_planned_task(tasks).task_id))
    store.start(running.run_id, expected_version=running.version)
    settled = dispatcher.queue(_request(_planned_task(tasks).task_id))
    store.cancel(settled.run_id, CancelRunRequest(expected_version=settled.version))

    both = store.list(project_id="alpha", states=[RunState.QUEUED, RunState.RUNNING], limit=500)
    assert {run.run_id for run in both} == {queued.run_id, running.run_id}
    # The single-state filter is untouched.
    assert [run.run_id for run in store.list(project_id="alpha", state=RunState.QUEUED)] == [queued.run_id]
    # Two ways of asking one question is an ambiguity, not something to merge.
    with pytest.raises(RunValidationError, match="either state or states"):
        store.list(state=RunState.QUEUED, states=[RunState.RUNNING])

    app.dependency_overrides[get_execution_store] = lambda: store
    client = TestClient(app)
    try:
        multi = client.get("/api/runs", params={"project_id": "alpha", "states": ["queued", "running"]})
        assert multi.status_code == 200
        assert {row["run_id"] for row in multi.json()} == {queued.run_id, running.run_id}
        single = client.get("/api/runs", params={"project_id": "alpha", "state": "queued"})
        assert [row["run_id"] for row in single.json()] == [queued.run_id]
        assert client.get(
            "/api/runs", params={"state": "queued", "states": "running"}
        ).status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_a_run_cancelled_while_queued_is_settled_not_left_queued(tmp_path):
    """A run cancelled before it launches must reach a terminal state.

    Left QUEUED it is a run nothing will ever execute, and the same-role guard
    refuses every requeue until a human cancels it by hand. CANCELLED, not
    ABANDONED: no process was started, so nothing was abandoned mid-flight — and
    it is the only terminal state the store allows from QUEUED.

    The worktree slot is held directly here; that is what parks a real run behind
    a busy worktree (or behind capacity) when shutdown cancels it.
    """
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    task = _planned_task(tasks)
    queued = dispatcher.queue(_request(task.task_id))

    async def scenario():
        async with dispatcher._workspace_limits[dispatcher._workspace_key(queued.workspace)]:
            pending = asyncio.create_task(dispatcher._execute_guarded(queued.run_id))
            await asyncio.sleep(0.05)
            assert store.require(queued.run_id).state == RunState.QUEUED
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending

    asyncio.run(scenario())

    settled = store.require(queued.run_id)
    assert settled.state == RunState.CANCELLED
    assert settled.error_message == "dispatcher stopped before the run started"
    # And the task can be retried: the guard was about overlap, not a run limit.
    assert dispatcher.queue(_request(task.task_id, version=3)).state == RunState.QUEUED


def test_a_queued_run_cancelled_before_the_loop_starts_it_is_still_settled(tmp_path):
    """The other half of the cancel-before-launch case: the coroutine never ran.

    ``schedule`` only *schedules* the coroutine. If the cancellation lands before
    the loop first steps into it — which is exactly what ``shutdown`` does when it
    cancels a run queued moments earlier — the body never executes, so the
    ``except asyncio.CancelledError`` inside it never runs and cannot settle the
    row. Left QUEUED, the same-role guard then refuses every new worker run for
    that task forever: no restart clears a QUEUED row (``recover_abandoned`` only
    settles RUNNING ones).

    So the settle has to come from whoever owns the cancellation. Both owners are
    checked here: ``shutdown``'s sweep, and the scheduled task's done callback.
    """
    launched = tmp_path / "launched.log"
    script = f"import pathlib; pathlib.Path(r'{launched}').write_text('ran')"
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", script])
    task = _planned_task(tasks)
    queued = dispatcher.queue(_request(task.task_id))

    async def cancelled_by_shutdown():
        dispatcher.schedule(queued.run_id)
        # No await between schedule and shutdown, so the loop has had no chance to
        # enter the coroutine. Nothing here yields.
        assert store.require(queued.run_id).state == RunState.QUEUED
        await dispatcher.shutdown()

    asyncio.run(cancelled_by_shutdown())

    settled = store.require(queued.run_id)
    assert settled.state == RunState.CANCELLED
    assert settled.error_message == "dispatcher stopped before the run started"
    # Proof the body really never ran: the adapter's process was never launched.
    assert not launched.exists()
    # And the task is workable again — which is the point of settling it.
    requeued = dispatcher.queue(_request(task.task_id, version=3))
    assert requeued.state == RunState.QUEUED

    async def cancelled_by_callback():
        # The same hole reached without shutdown: a bare task.cancel() on a run
        # whose coroutine has not started. The done callback is the only place
        # that still knows which row was orphaned.
        dispatcher.schedule(requeued.run_id)
        handle = next(iter(dispatcher._scheduled))
        handle.cancel()
        with pytest.raises(asyncio.CancelledError):
            await handle
        await asyncio.sleep(0)  # let the done callback run

    asyncio.run(cancelled_by_callback())

    assert store.require(requeued.run_id).state == RunState.CANCELLED
    assert not launched.exists()
    assert dispatcher.queue(_request(task.task_id, version=3)).state == RunState.QUEUED


def test_a_worker_run_is_refused_while_a_human_gate_is_open(tmp_path):
    """Run -> Gate ordering, enforced in the create transaction rather than by the
    client (AGENTS.md: the mainline is Run -> Artifact/Evidence -> Gate -> Handoff,
    and Agora is the only writer of that order).

    ``expected_task_version`` cannot cover this: raising an Attention item does not
    bump the task's version, so a page holding a current manifest passes the
    optimistic check while a gate waits. Only a read inside this transaction closes
    it. See docs/architecture/run-gate-ordering-v1.md for the per-kind reasoning.
    """
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    attention = AttentionStore(tasks)
    task = _planned_task(tasks)

    def raise_item(kind: AttentionKind, title: str, **extra):
        return attention.create(CreateAttentionRequest(
            task_id=task.task_id, kind=kind, urgency=AttentionUrgency.NORMAL,
            title=title, requester="agora-review", **extra,
        ))

    approval = raise_item(AttentionKind.APPROVAL, "Review finished, approve it")
    # The task version is untouched by the gate, which is exactly why the version
    # check cannot stand in for this refusal.
    assert tasks.get(task.task_id).version == 2

    with pytest.raises(RunGateError, match="open gate item") as gated:
        dispatcher.queue(_request(task.task_id))
    assert gated.value.code == GATE_CODE_ATTENTION_OPEN
    # The refusal names the item and how to clear it, rather than only that
    # something is in the way.
    assert approval.item_id in str(gated.value)
    assert "cancel" in str(gated.value)
    assert gated.value.items == [
        {"item_id": approval.item_id, "kind": "approval", "title": "Review finished, approve it"}
    ]
    assert store.list(task_id=task.task_id) == []

    # A review run is never gated: it is how the gate gets something to decide,
    # so refusing it would deadlock the cycle it belongs to.
    review = dispatcher.queue(
        CreateRunRequest(task_id=task.task_id, adapter="codex", prompt="review it",
                         timeout_seconds=10, expected_task_version=2),
        role=ROLE_REVIEW,
    )
    store.cancel(review.run_id, CancelRunRequest(expected_version=review.version))

    # Answering it clears the refusal. Nothing else had to change.
    attention.respond(approval.item_id, RespondAttentionRequest(
        action=ResponseAction.APPROVE, expected_version=approval.version,
    ))
    first = dispatcher.queue(_request(task.task_id, version=3))
    store.cancel(first.run_id, CancelRunRequest(expected_version=first.version))

    # A blocker gates for its own reason: another run does not clear a wall a human
    # has to clear, and the auto-review failure path raises exactly this kind.
    blocker = raise_item(AttentionKind.BLOCKER, "Credentials missing")
    with pytest.raises(RunGateError, match="open gate item"):
        dispatcher.queue(_request(task.task_id, version=3))
    # Cancelling is the second documented way out, and it works without a restart.
    attention.cancel(blocker.item_id, CancelAttentionRequest(
        expected_version=blocker.version, reason="handled outside Agora",
    ))
    second = dispatcher.queue(_request(task.task_id, version=3))
    store.cancel(second.run_id, CancelRunRequest(expected_version=second.version))

    # A question does NOT gate: it binds to no artifact and settles no decision,
    # and a better-prompted run is often the answer. Promoting it would turn every
    # unanswered mid-run Codex permission question into a task-level lockout.
    raise_item(AttentionKind.QUESTION, "Which database should this use?")
    third = dispatcher.queue(_request(task.task_id, version=3))
    store.cancel(third.run_id, CancelRunRequest(expected_version=third.version))

    # An item past its expiry is expired, even before expire_overdue has swept it:
    # otherwise the refusal would depend on when that sweep last ran.
    overdue = raise_item(
        AttentionKind.APPROVAL, "Stale approval",
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    )
    with attention._transaction() as db:
        db.execute(
            "UPDATE attention_items SET expires_at = ? WHERE item_id = ?",
            ((datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(), overdue.item_id),
        )
    assert dispatcher.queue(_request(task.task_id, version=3)).state == RunState.QUEUED


def test_a_worker_run_is_refused_while_the_review_run_is_unsettled(tmp_path):
    """The half of Run -> Gate ordering that IS atomically enforceable.

    A review run is a row in this database, readable in the create transaction.
    Without this check a worker run created while its predecessor's review executes
    is refused by nothing: the same-role guard only compares worker against worker.
    The gap between a worker run settling and its review being *queued* is not
    enforceable here and is left to the client window — see the ADR.
    """
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    task = _planned_task(tasks)
    worker = dispatcher.queue(_request(task.task_id))
    running = store.start(worker.run_id, expected_version=worker.version)
    store.finish(worker.run_id, RunState.SUCCEEDED, expected_version=running.version,
                 exit_code=0, stdout_tail="done", stderr_tail="")
    review = dispatcher.queue(
        CreateRunRequest(task_id=task.task_id, adapter="codex", prompt="review it",
                         timeout_seconds=10, expected_task_version=3),
        role=ROLE_REVIEW,
    )

    # A QUEUED review gates, and so does a RUNNING one.
    for state in ("queued", "running"):
        if state == "running":
            store.start(review.run_id, expected_version=review.version)
        with pytest.raises(RunGateError, match="under review") as gated:
            dispatcher.queue(_request(task.task_id, version=3))
        assert gated.value.code == GATE_CODE_REVIEW_ACTIVE
        assert gated.value.items == [{"run_id": review.run_id, "state": state}]

    # Settling the review clears it. So does cancelling — the refusal names the run.
    latest = store.require(review.run_id)
    store.finish(review.run_id, RunState.SUCCEEDED, expected_version=latest.version,
                 exit_code=0, stdout_tail="looks fine", stderr_tail="")
    assert dispatcher.queue(_request(task.task_id, version=3)).state == RunState.QUEUED


def test_the_gate_refusal_reaches_the_client_as_a_distinguishable_409(tmp_path):
    """A plain 409 is not enough: the panel has to tell "wait for the run that is
    already going" from "settle the gate that is waiting for you", and those need
    different words and a different action. Sniffing prose is not a contract, so
    the gate refusal carries a machine-readable code in the body — while every
    other 409 from this route keeps its plain-string detail."""
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    attention = AttentionStore(tasks)
    task = _planned_task(tasks)
    item = attention.create(CreateAttentionRequest(
        task_id=task.task_id, kind=AttentionKind.APPROVAL, title="Approve the review",
        requester="agora-review",
    ))
    dispatcher.schedule = lambda run_id: None  # type: ignore[method-assign]
    app.dependency_overrides[get_execution_store] = lambda: store
    app.dependency_overrides[get_execution_dispatcher] = lambda: dispatcher
    client = TestClient(app)
    try:
        gated = client.post("/api/runs", json=_request(task.task_id).model_dump(mode="json"))
        assert gated.status_code == 409
        detail = gated.json()["detail"]
        assert detail["code"] == GATE_CODE_ATTENTION_OPEN
        assert item.item_id in detail["message"]
        assert detail["items"] == [
            {"item_id": item.item_id, "kind": "approval", "title": "Approve the review"}
        ]

        # The same-role refusal is a different answer and stays a plain string, so
        # nothing that reads today's 409 breaks.
        attention.respond(item.item_id, RespondAttentionRequest(
            action=ResponseAction.APPROVE, expected_version=item.version,
        ))
        payload = _request(task.task_id).model_dump(mode="json")
        assert client.post("/api/runs", json=payload).status_code == 201
        duplicate = client.post("/api/runs", json={**payload, "expected_task_version": 3})
        assert duplicate.status_code == 409
        assert isinstance(duplicate.json()["detail"], str)
    finally:
        app.dependency_overrides.clear()


def _bridge_prompt(
    attention: AttentionStore, task_id: str, run_id: str, *, rpc_id: int = 1,
) -> str:
    """Raise a Codex app-server permission prompt against a live run.

    The same path ``CodexApprovalBroker.capture`` takes: the bridge raises it as
    ``kind=approval`` — a decision bound to a command — with no ``expires_at``,
    and blocks the agent's turn on the answer travelling back over the
    app-server connection.
    """
    receipt = attention.create_bridge_event(
        BridgeEventRequest(
            vendor=BridgeVendor.CODEX,
            vendor_event_id=f"thread-1:turn-1:item-1:{rpc_id}",
            task_id=task_id,
            run_id=run_id,
            kind=AttentionKind.APPROVAL,
            title="Command execution approval",
            body="Command: git push",
            requester="codex-app-server",
            delivery_mode=DeliveryMode.BIDIRECTIONAL,
            correlation={
                "rpc_id": rpc_id, "method": "item/commandExecution/requestApproval",
                "thread_id": "thread-1", "turn_id": "turn-1", "item_id": "item-1",
            },
        ),
        trusted_bidirectional=True,
    )
    return receipt.item_id


def test_an_unanswered_permission_prompt_is_settled_with_its_run(tmp_path):
    """The lockout the open-gate refusal created, on the default runtime path.

    The Codex bridge raises a permission prompt as ``kind=approval`` — correctly:
    it is a decision bound to a command — with no ``expires_at``. If the human
    never answers, the run ends and the item stays ``open`` while being
    unanswerable, because the answer travels back over the app-server connection
    of a process that no longer exists. ``_reject_open_gate`` then refuses EVERY
    future worker run on that task until a human finds the orphan and cancels it
    by hand: a self-inflicted denial of service.

    So the prompt is settled in the same transaction that settles its run, for
    every terminal state — the run may end by timing out, by being cancelled, or by
    being abandoned on restart, and all three leave the prompt equally dead.
    """
    for terminal in ("cancelled", "failed", "abandoned"):
        tasks, store, dispatcher = _system(tmp_path / terminal, [sys.executable, "-c", "print('ok')"])
        attention = AttentionStore(tasks)
        task = _planned_task(tasks)
        run = dispatcher.queue(_request(task.task_id))
        item_id = _bridge_prompt(attention, task.task_id, run.run_id)

        # The three facts that make this a lockout rather than a passing prompt: it
        # is an ``approval``, so it gates; it is open; and it carries no expiry, so
        # nothing sweeps it later.
        raised = attention.require(item_id)
        assert raised.state.value == "open"
        assert raised.kind == AttentionKind.APPROVAL
        assert raised.expires_at is None

        if terminal == "cancelled":
            store.cancel(run.run_id, CancelRunRequest(expected_version=run.version))
        else:
            running = store.start(run.run_id, expected_version=run.version)
            if terminal == "failed":
                store.finish(run.run_id, RunState.FAILED, expected_version=running.version,
                             exit_code=1, stdout_tail="", stderr_tail="boom")
            else:
                store.abandon(run.run_id, expected_version=running.version,
                              reason="Agora restarted while the process was running")

        settled = attention.require(item_id)
        assert settled.state.value == "cancelled", terminal
        # The reason says why it can no longer be answered, not merely that it was
        # dismissed: the prompt died with its process.
        assert settled.cancellation_reason == ORPHANED_PROMPT_REASON
        # And the undelivered answer channel is closed rather than left pending
        # forever against a process that is gone.
        with tasks._connect() as db:
            delivery = db.execute(
                "SELECT delivery_state, delivery_error FROM attention_bridge_events WHERE item_id = ?",
                (item_id,),
            ).fetchone()
        assert tuple(delivery) == ("failed", ORPHANED_PROMPT_REASON)
        types = [event.event_type for event in tasks.events(task.task_id)]
        assert "attention.cancelled" in types and "attention.delivery_failed" in types

        # The point of all of it: the task is workable again, with no human sweep.
        assert dispatcher.queue(_request(task.task_id, version=3)).state == RunState.QUEUED

        # And that this is not vacuous: an item of exactly this shape, left open,
        # does refuse the next worker run. Settling it with its run is what unlocked
        # the task, not some other property of the sequence above.
        store.cancel(
            store.list(task_id=task.task_id, state=RunState.QUEUED)[0].run_id,
            CancelRunRequest(expected_version=1),
        )
        equivalent = attention.create(CreateAttentionRequest(
            task_id=task.task_id, kind=AttentionKind.APPROVAL,
            title="Command execution approval", requester="codex-app-server",
        ))
        with pytest.raises(RunGateError, match="open gate item") as gated:
            dispatcher.queue(_request(task.task_id, version=3))
        assert gated.value.code == GATE_CODE_ATTENTION_OPEN
        assert gated.value.items[0]["item_id"] == equivalent.item_id


def test_the_review_gate_survives_the_orphaned_prompt_sweep(tmp_path):
    """The regression that matters most: the sweep above must not eat the gate.

    ``ExecutionDispatcher._raise_review_attention`` raises the cross-review
    approval *after* its review run is already terminal, scoped (``run_id``) to the
    worker run it reviewed — which is terminal too. So the obvious fix for the
    lockout, "the run ended, so cancel its open items", would destroy the very gate
    this slice exists to create, and would do it silently.

    The cut is therefore by origin: only items with a row in
    ``attention_bridge_events`` are swept, and ``REVIEW_REQUESTER`` is excluded
    independently. A review gate is a decision that outlives its run; a permission
    prompt is not.

    **What makes this bite.** The distinguishing arrangement is a REVIEW_REQUESTER
    item scoped to the very run being settled and open at the moment that run
    settles: that is the only shape an origin cut keeps and a run-state cut
    destroys. Everything else in this test — a gate raised after its runs are
    already terminal, or an unrelated run of the same task settling — survives a
    run-state sweep too, because such a sweep keys on the settling run's own id and
    finds nothing open under it. So that arrangement is built explicitly below, and
    the guard is verified by mutation: widening
    ``settle_bridge_attention_for_run`` to match on run state alone must make this
    test fail.
    """
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    attention = AttentionStore(tasks)
    task = _planned_task(tasks)

    worker = dispatcher.queue(_request(task.task_id))
    running = store.start(worker.run_id, expected_version=worker.version)
    # A permission prompt from the same run, so both kinds of item are in play at
    # once and the sweep has to tell them apart rather than match on run_id.
    prompt_id = _bridge_prompt(attention, task.task_id, worker.run_id)
    store.finish(worker.run_id, RunState.SUCCEEDED, expected_version=running.version,
                 exit_code=0, stdout_tail="done", stderr_tail="")
    assert attention.require(prompt_id).state.value == "cancelled"

    # THE ARRANGEMENT THAT DISTINGUISHES THE TWO CUTS: a review gate scoped to a
    # review run that is still unsettled, so the item is open at the instant that
    # exact run reaches a terminal state. A run-state sweep ("this run ended, cancel
    # its open items") cancels it here; the origin sweep does not, because the item
    # has no attention_bridge_events row and its requester is REVIEW_REQUESTER.
    #
    # Deliberately not the shape _raise_review_attention produces today (which
    # scopes to the worker run and raises after the review run is already terminal).
    # That shape cannot fail either cut, which is exactly why testing only it left
    # the origin/state distinction unguarded.
    scoped_review = dispatcher.queue(
        CreateRunRequest(task_id=task.task_id, adapter="codex", prompt="review it",
                         timeout_seconds=10, expected_task_version=3),
        role=ROLE_REVIEW,
    )
    scoped_gate = attention.create(CreateAttentionRequest(
        task_id=task.task_id, run_id=scoped_review.run_id, kind=AttentionKind.APPROVAL,
        urgency=AttentionUrgency.NORMAL, title="Approve this review",
        requester=REVIEW_REQUESTER,
    ))
    assert attention.require(scoped_gate.item_id).state.value == "open"
    scoped_running = store.start(scoped_review.run_id, expected_version=scoped_review.version)
    store.finish(scoped_review.run_id, RunState.SUCCEEDED,
                 expected_version=scoped_running.version, exit_code=0,
                 stdout_tail="two findings", stderr_tail="")
    # Settling THAT run did not take its gate with it.
    assert attention.require(scoped_gate.item_id).state.value == "open"
    # And it still gates, so surviving is not merely surviving as a dead row.
    with pytest.raises(RunGateError, match="open gate item") as scoped_refusal:
        dispatcher.queue(_request(task.task_id, version=3))
    assert scoped_refusal.value.items[0]["item_id"] == scoped_gate.item_id
    attention.respond(scoped_gate.item_id, RespondAttentionRequest(
        action=ResponseAction.APPROVE, expected_version=scoped_gate.version,
    ))
    # ``AttentionStore.respond`` settles the item without touching the task's
    # lifecycle (the review gate that advances a task runs in the router), so the
    # task is still running at version 3 and the rest of this test goes on using it.

    # The shape _raise_review_attention actually produces: a review run, and the
    # gate it raises once that run is already terminal, scoped to the worker run.
    review = dispatcher.queue(
        CreateRunRequest(task_id=task.task_id, adapter="codex", prompt="review it",
                         timeout_seconds=10, expected_task_version=3),
        role=ROLE_REVIEW,
    )
    review_running = store.start(review.run_id, expected_version=review.version)
    store.finish(review.run_id, RunState.SUCCEEDED, expected_version=review_running.version,
                 exit_code=0, stdout_tail="two findings", stderr_tail="")
    gate = attention.create(CreateAttentionRequest(
        task_id=task.task_id, run_id=worker.run_id, kind=AttentionKind.APPROVAL,
        urgency=AttentionUrgency.NORMAL, title="Review finished, approve it",
        requester=REVIEW_REQUESTER,
    ))

    # Both runs it is scoped to are terminal. It is still open, and it still gates.
    assert attention.require(gate.item_id).state.value == "open"
    with pytest.raises(RunGateError, match="open gate item") as gated:
        dispatcher.queue(_request(task.task_id, version=3))
    assert gated.value.items == [
        {"item_id": gate.item_id, "kind": "approval", "title": "Review finished, approve it"}
    ]

    # Another run of the same task settling does not disturb it either: the sweep
    # is keyed on the settling run and on the bridge marker, not on the task.
    other = dispatcher.queue(
        CreateRunRequest(task_id=task.task_id, adapter="codex", prompt="review again",
                         timeout_seconds=10, expected_task_version=3),
        role=ROLE_REVIEW,
    )
    store.cancel(other.run_id, CancelRunRequest(expected_version=other.version))
    assert attention.require(gate.item_id).state.value == "open"

    # Nor does the resume sweep, which settles runs of its own.
    dispatcher.resume_queued()
    assert attention.require(gate.item_id).state.value == "open"

    # The recorded residual: a non-bridge item scoped to a run is NOT swept, even
    # when its requester is not the review gate's. That is deliberate — items from
    # AttentionStore.create are control-plane decisions — and it is pinned here so
    # the boundary is a choice rather than an accident.
    manual = attention.create(CreateAttentionRequest(
        task_id=task.task_id, run_id=worker.run_id, kind=AttentionKind.BLOCKER,
        title="Needs a credential", requester="operator",
    ))
    later = dispatcher.queue(
        CreateRunRequest(task_id=task.task_id, adapter="codex", prompt="x",
                         timeout_seconds=10, expected_task_version=3),
        role=ROLE_REVIEW,
    )
    store.cancel(later.run_id, CancelRunRequest(expected_version=later.version))
    assert attention.require(manual.item_id).state.value == "open"

    # And answering the gate is what clears it, which is the whole point.
    attention.respond(gate.item_id, RespondAttentionRequest(
        action=ResponseAction.APPROVE, expected_version=gate.version,
    ))
    attention.cancel(manual.item_id, CancelAttentionRequest(expected_version=manual.version))
    assert dispatcher.queue(_request(task.task_id, version=3)).state == RunState.QUEUED


def _insert_queued_run(tasks: TaskStore, task_id: str, *, role: str | None, adapter="codex"):
    """A persisted QUEUED row of the shape an older database — or a crash — leaves
    behind: inserted without passing through ``create``'s gates."""
    run_id = f"run_persisted_{uuid.uuid4().hex[:8]}"
    metadata = "{}" if role is None else json.dumps({"agora_role": role})
    with tasks._transaction() as db:
        db.execute(
            """INSERT INTO execution_runs
               (run_id, task_id, project_id, adapter, state, prompt, workspace, command,
                timeout_seconds, result_metadata, version, queued_at, actor)
               VALUES (?, ?, 'alpha', ?, 'queued', 'go', '.', '[]', 10, ?, 1, ?, 'user')""",
            (run_id, task_id, adapter, metadata,
             datetime.now(timezone.utc).isoformat()),
        )
    return run_id


def test_resume_revalidates_persisted_queued_runs_instead_of_scheduling_them_blindly(tmp_path):
    """``resume_queued`` used to bypass every gate ``create`` enforces.

    The gates live in the transaction that inserts the run, and nothing re-checks a
    row already inserted. A QUEUED row therefore outlives the state it was
    validated against — across an upgrade from a database written before these
    gates existed, and across a crash — and ``recover_abandoned`` only settles
    RUNNING rows. So a persisted worker row was rescheduled unconditionally and
    could start while its predecessor's review was still queued or running, or
    while a human gate waited.

    Re-validated at resume and settled CANCELLED, not refused at launch: a launch
    refusal would have to end FAILED, and a FAILED run reads as "the agent ran and
    did not succeed" — a lie about a process that never started. One sweep, oldest
    first, in one transaction, also makes which run survives a conflict a matter of
    queue order rather than of scheduler luck.
    """
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    attention = AttentionStore(tasks)
    scheduled: list[str] = []
    dispatcher.schedule = lambda run_id: scheduled.append(run_id)  # type: ignore[method-assign]

    # 1. A queued worker run whose task's review is unsettled. The review is the
    #    evidence the human gate decides on, so the review survives.
    under_review = _planned_task(tasks)
    review = dispatcher.queue(
        CreateRunRequest(task_id=under_review.task_id, adapter="codex", prompt="review",
                         timeout_seconds=10, expected_task_version=2),
        role=ROLE_REVIEW,
    )
    store.start(review.run_id, expected_version=review.version)
    blocked_by_review = _insert_queued_run(tasks, under_review.task_id, role="worker")

    # 2. A queued worker run on a task with an open human gate.
    gated_task = _planned_task(tasks)
    gate = attention.create(CreateAttentionRequest(
        task_id=gated_task.task_id, kind=AttentionKind.APPROVAL,
        title="Approve the review", requester=REVIEW_REQUESTER,
    ))
    blocked_by_gate = _insert_queued_run(tasks, gated_task.task_id, role="worker")

    # 3. Two queued worker rows on one task: the older one wins, deterministically.
    doubled = _planned_task(tasks)
    first = _insert_queued_run(tasks, doubled.task_id, role="worker")
    second = _insert_queued_run(tasks, doubled.task_id, role=None)  # untagged = worker

    # 4. A queued review run on a gated task is admitted: gating it would deadlock
    #    the cycle the gate belongs to.
    review_gated = _planned_task(tasks)
    attention.create(CreateAttentionRequest(
        task_id=review_gated.task_id, kind=AttentionKind.BLOCKER,
        title="Review said no", requester=REVIEW_REQUESTER,
    ))
    queued_review = _insert_queued_run(tasks, review_gated.task_id, role="review")

    # 5. A role this build cannot schedule fails closed rather than slipping past
    #    every check above.
    unknown_role_task = _planned_task(tasks)
    unknown_role = _insert_queued_run(tasks, unknown_role_task.task_id, role="auditor")

    # 6. A clean queued run, which must still be resumed — the sweep is a gate, not
    #    a purge.
    clean_task = _planned_task(tasks)
    clean = dispatcher.queue(_request(clean_task.task_id))

    refused = dispatcher.resume_queued()

    assert set(scheduled) == {queued_review, first, clean.run_id}
    by_run = {entry["run_id"]: entry["reason"] for entry in refused}
    assert set(by_run) == {blocked_by_review, blocked_by_gate, second, unknown_role}
    assert review.run_id in by_run[blocked_by_review]
    assert "review run" in by_run[blocked_by_review]
    assert gate.item_id in by_run[blocked_by_gate]
    assert first in by_run[second] and "worker run" in by_run[second]
    assert "auditor" in by_run[unknown_role]

    for run_id in by_run:
        settled = store.require(run_id)
        # CANCELLED, and it says why. Not FAILED: no process was ever started.
        assert settled.state == RunState.CANCELLED, run_id
        assert settled.error_message.startswith("Not resumed after restart: ")
        assert settled.finished_at is not None
    for run_id in scheduled:
        assert store.require(run_id).state == RunState.QUEUED

    # The refusals are on the record, not only in the return value.
    cancelled_events = [
        event for event in tasks.events(under_review.task_id)
        if event.event_type == "run.cancelled"
    ]
    assert cancelled_events and cancelled_events[-1].payload["run_id"] == blocked_by_review

    # Idempotent: a second sweep finds nothing left to settle, and re-admits the
    # same rows rather than cancelling the survivors it just admitted.
    scheduled.clear()
    assert dispatcher.resume_queued() == []
    assert set(scheduled) == {queued_review, first, clean.run_id}

    # And a queued run whose blocker is settled resumes on the next restart, so
    # this is a delay, not a one-way door.
    attention.respond(gate.item_id, RespondAttentionRequest(
        action=ResponseAction.APPROVE, expected_version=gate.version,
    ))
    retry = _insert_queued_run(tasks, gated_task.task_id, role="worker")
    scheduled.clear()
    assert dispatcher.resume_queued() == []
    assert retry in scheduled


def test_resume_settles_the_orphaned_prompts_of_a_run_it_refuses(tmp_path):
    """A run the sweep cancels is terminal, so its bridge prompts die with it too —
    otherwise refusing a persisted run would leave behind the exact orphan that
    locks the task, and the sweep would create the problem it exists to prevent."""
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    attention = AttentionStore(tasks)
    dispatcher.schedule = lambda run_id: None  # type: ignore[method-assign]
    task = _planned_task(tasks)

    # A running worker run holds the role's slot, so the persisted queued one is
    # refused. Its own permission prompt is scoped to itself, which is why the gate
    # check excludes it — an item a run raised is not prior work it must wait behind.
    live = dispatcher.queue(_request(task.task_id))
    store.start(live.run_id, expected_version=live.version)
    orphan_run = _insert_queued_run(tasks, task.task_id, role="worker")
    prompt_id = _bridge_prompt(attention, task.task_id, orphan_run, rpc_id=5)

    refused = dispatcher.resume_queued()

    assert [entry["run_id"] for entry in refused] == [orphan_run]
    assert store.require(orphan_run).state == RunState.CANCELLED
    settled = attention.require(prompt_id)
    assert settled.state.value == "cancelled"
    assert settled.cancellation_reason == ORPHANED_PROMPT_REASON


def test_resume_re_asks_the_task_questions_of_a_queued_run(tmp_path):
    """A queued run used to be resumed without anyone reading its task row again.

    ``revalidate_queued`` re-checked role conflicts and attention gates and never
    looked at the task, so a worker queued while the task was planned or running
    was still rescheduled after the task had been cancelled, failed, or carried to
    ``verified`` by the review gate — and after its version had moved, which is the
    exact condition on which ``create`` refuses the identical request. Both
    questions are now asked of the task that exists now.
    """
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    scheduled: list[str] = []
    dispatcher.schedule = lambda run_id: scheduled.append(run_id)  # type: ignore[method-assign]

    # 1. The task left planned/running while its run waited.
    abandoned = _planned_task(tasks)
    doomed = dispatcher.queue(_request(abandoned.task_id))  # planned -> running, v3
    tasks.cancel(abandoned.task_id, expected_version=3)

    # 2. The task is still runnable, but its version moved after the run was
    #    validated: running -> blocked -> running, ending at v5. ``create`` would
    #    reject a request carrying v3 now, so resuming a row validated against v3
    #    would be admitting exactly what create refuses.
    moved = _planned_task(tasks)
    stale = dispatcher.queue(_request(moved.task_id))  # v3
    tasks.transition(moved.task_id, TaskState.BLOCKED, actor="user", expected_version=3)
    tasks.transition(moved.task_id, TaskState.RUNNING, actor="user", expected_version=4)

    # 3. A row that records no task version at all — an older database, or one
    #    edited by hand. Unknown is reported by skipping the question it cannot
    #    answer, not by refusing every persisted row: the task is running, so it
    #    resumes.
    legacy_ok = _planned_task(tasks)
    legacy_run = _insert_queued_run(tasks, legacy_ok.task_id, role="worker")
    tasks.transition(legacy_ok.task_id, TaskState.RUNNING, actor="user", expected_version=2)

    # 4. ...but a row with no recorded version is still lifecycle-checked, which is
    #    the half of the question it *can* answer.
    legacy_dead = _planned_task(tasks)
    legacy_cancelled = _insert_queued_run(tasks, legacy_dead.task_id, role="worker")
    tasks.cancel(legacy_dead.task_id, expected_version=2)

    # 5. A queued *review* is asked the same questions. A review of a cancelled
    #    task is not evidence anyone is waiting for; the review exemption is from
    #    the worker gates, not from the task's own lifecycle.
    dead_review_task = _planned_task(tasks)
    dead_review = _insert_queued_run(tasks, dead_review_task.task_id, role="review")
    tasks.cancel(dead_review_task.task_id, expected_version=2)

    # 6. The control: unchanged task, matching version, resumed.
    fine = _planned_task(tasks)
    healthy = dispatcher.queue(_request(fine.task_id))

    refused = dispatcher.resume_queued()

    by_run = {entry["run_id"]: entry["reason"] for entry in refused}
    assert set(by_run) == {doomed.run_id, stale.run_id, legacy_cancelled, dead_review}
    assert set(scheduled) == {legacy_run, healthy.run_id}
    assert "its task is cancelled" in by_run[doomed.run_id]
    assert "version 3 to 5" in by_run[stale.run_id]
    assert "its task is cancelled" in by_run[legacy_cancelled]
    assert "its task is cancelled" in by_run[dead_review]
    for run_id in by_run:
        settled = store.require(run_id)
        assert settled.state == RunState.CANCELLED, run_id
        assert settled.error_message.startswith("Not resumed after restart: ")


def test_a_queued_run_whose_task_moved_is_not_launched(tmp_path):
    """The launch half of the same question.

    The resume sweep closes the gap across a restart. Inside a running process a
    row can wait in QUEUED for as long as its worktree and the concurrency limits
    make it wait, and ``execute`` re-read only the *run*'s state before starting a
    process. So a task cancelled — or moved to a new version — while its run waited
    still got an agent launched against it, and the tokens spent were spent on work
    nobody wanted.
    """
    for scenario in ("cancelled", "version"):
        tmp = tmp_path / scenario
        tasks, store, dispatcher = _system(
            tmp, [sys.executable, "-c", "open('launched.txt', 'w').write('x')"]
        )
        task = _planned_task(tasks)
        run = dispatcher.queue(_request(task.task_id))  # planned -> running, v3
        if scenario == "cancelled":
            tasks.cancel(task.task_id, expected_version=3)
            expected = "its task is cancelled"
        else:
            tasks.transition(task.task_id, TaskState.BLOCKED, actor="user", expected_version=3)
            tasks.transition(task.task_id, TaskState.RUNNING, actor="user", expected_version=4)
            expected = "version 3 to 5"

        settled = asyncio.run(dispatcher.execute(run.run_id))

        # CANCELLED with a stated reason, not FAILED: no process was started, and
        # FAILED would claim an agent ran and lost.
        assert settled.state == RunState.CANCELLED, scenario
        assert settled.error_message.startswith("Not launched: "), scenario
        assert expected in settled.error_message, scenario
        assert settled.exit_code is None
        workspace = dispatcher.projects.get("alpha").workspaces["codex"]
        assert not (workspace / "launched.txt").exists(), scenario
        # The refusal is on the task's timeline, not only in the run row.
        assert "run.cancelled" in [event.event_type for event in tasks.events(task.task_id)]


def test_a_gate_opened_while_a_run_waited_is_asked_again_at_launch(tmp_path):
    """The launch re-check asks the *gate* questions too, not only the task ones.

    Reproduced race: a review run settles, a worker run is queued in the window
    before ``_raise_review_attention`` writes the approval, and the approval then
    appears while that worker sits behind its worktree or waits for capacity. At
    launch the task is still running and still at the version the run was validated
    against, so the only two questions asked let the worker start — with an open
    approval on its task, which is the one outcome the Run -> Gate order exists to
    prevent. ``create`` refuses the identical request at that moment.

    The reason first given for leaving the gate questions out was that a bridge
    permission prompt raised meanwhile would cancel a waiting run. It does not:
    ``_resume_refusal`` excludes items scoped to the run being judged, which is
    exactly that transient, run-scoped fact — case 2 holds that behaviour.
    """
    launched = [sys.executable, "-c", "open('launched.txt', 'w').write('x')"]

    # 1. An approval raised after the run was queued refuses it at launch.
    tasks, store, dispatcher = _system(tmp_path / "gated", launched)
    attention = AttentionStore(tasks)
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))
    gate = attention.create(CreateAttentionRequest(
        task_id=task.task_id, kind=AttentionKind.APPROVAL,
        title="Approve the review", requester=REVIEW_REQUESTER,
    ))

    settled = asyncio.run(dispatcher.execute(run.run_id))

    assert settled.state == RunState.CANCELLED
    assert settled.error_message.startswith("Not launched: ")
    assert gate.item_id in settled.error_message
    assert settled.exit_code is None
    assert not (dispatcher.projects.get("alpha").workspaces["codex"] / "launched.txt").exists()
    # On the task's timeline, not only in the run row.
    assert "run.cancelled" in [event.event_type for event in tasks.events(task.task_id)]
    # A delay, not a one-way door: settle the gate and the next run launches.
    attention.respond(gate.item_id, RespondAttentionRequest(
        action=ResponseAction.APPROVE, expected_version=gate.version,
    ))
    retry = dispatcher.queue(_request(task.task_id, version=3))
    assert asyncio.run(dispatcher.execute(retry.run_id)).state == RunState.SUCCEEDED

    # 2. The run's OWN permission prompt is not a gate on it. This is the case the
    #    original omission was justified by, and the exclusion — not the omission —
    #    is what handles it.
    tasks, store, dispatcher = _system(tmp_path / "own-prompt", launched)
    attention = AttentionStore(tasks)
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))
    _bridge_prompt(attention, task.task_id, run.run_id, rpc_id=7)

    assert store.revalidate_before_launch(run.run_id) is None
    assert asyncio.run(dispatcher.execute(run.run_id)).state == RunState.SUCCEEDED

    # 3. A review run still launches with a gate open: it is how the gate gets
    #    something to decide, so gating it would deadlock the review cycle.
    tasks, store, dispatcher = _system(tmp_path / "review", launched)
    attention = AttentionStore(tasks)
    task = _planned_task(tasks)
    attention.create(CreateAttentionRequest(
        task_id=task.task_id, kind=AttentionKind.BLOCKER,
        title="Review said no", requester=REVIEW_REQUESTER,
    ))
    review = dispatcher.queue(
        CreateRunRequest(task_id=task.task_id, adapter="codex", prompt="review",
                         timeout_seconds=10, expected_task_version=2),
        role=ROLE_REVIEW,
    )

    assert store.revalidate_before_launch(review.run_id) is None
    assert asyncio.run(dispatcher.execute(review.run_id)).state == RunState.SUCCEEDED

    # 4. An unsettled review run refuses a queued worker at launch, the same way
    #    ``create`` does — the review is the evidence the gate decides on.
    tasks, store, dispatcher = _system(tmp_path / "under-review", launched)
    task = _planned_task(tasks)
    worker = _insert_queued_run(tasks, task.task_id, role="worker")
    review = dispatcher.queue(
        CreateRunRequest(task_id=task.task_id, adapter="codex", prompt="review",
                         timeout_seconds=10, expected_task_version=2),
        role=ROLE_REVIEW,
    )
    store.start(review.run_id, expected_version=review.version)

    reason = store.revalidate_before_launch(worker)

    assert reason is not None and review.run_id in reason
    assert store.require(worker).state == RunState.CANCELLED


def test_a_queued_run_whose_task_row_is_gone_fails_closed(tmp_path):
    """The task row itself missing is unreadable state, not permission to launch.

    A foreign key normally makes this impossible, so it is reached here the only way
    it can be reached in the field: a database edited outside Agora. The point is
    that the answer is a refusal rather than a launch — the run's task, its version
    and its gates are all unanswerable without that row.
    """
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))
    orphaned = sqlite3.connect(str(tasks.db_path), isolation_level=None)
    try:
        orphaned.execute("PRAGMA foreign_keys = OFF")
        orphaned.execute("DELETE FROM tasks WHERE task_id = ?", (task.task_id,))
    finally:
        orphaned.close()

    assert store.revalidate_before_launch(run.run_id) == "its task no longer exists"
    assert store.require(run.run_id).state == RunState.CANCELLED
    # And the launch path reaches the same answer rather than starting a process.
    assert asyncio.run(dispatcher.execute(run.run_id)).state == RunState.CANCELLED


def test_a_queued_review_outranks_an_older_queued_worker(tmp_path):
    """Which run holds authority must not depend on which row happens to be older.

    The sweep walked the queue oldest-first, so an older queued worker was admitted
    first; by the time it reached the newer queued review, ``_resume_refusal``
    returned None without consulting the worker slot, because a review bypasses the
    worker gates by design. Both were scheduled, and which one held authority came
    down to insertion order — while the method's own docstring claimed the review
    cycle outranks a queued worker.

    Existing coverage only had a RUNNING review blocking a queued worker, which the
    ``held`` map answers without any ordering rule at all.
    """
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    scheduled: list[str] = []
    dispatcher.schedule = lambda run_id: scheduled.append(run_id)  # type: ignore[method-assign]

    # Worker row inserted first, so it is unambiguously the older one — the case
    # the old walk got wrong.
    worker_first = _planned_task(tasks)
    older_worker = _insert_queued_run(tasks, worker_first.task_id, role="worker")
    newer_review = _insert_queued_run(tasks, worker_first.task_id, role="review")

    # And the mirror image, to show the answer is the policy rather than the order
    # the rows were written in.
    review_first = _planned_task(tasks)
    older_review = _insert_queued_run(tasks, review_first.task_id, role="review")
    newer_worker = _insert_queued_run(tasks, review_first.task_id, role="worker")

    # A third task where the review sits between two workers: both workers wait.
    sandwiched = _planned_task(tasks)
    first_worker = _insert_queued_run(tasks, sandwiched.task_id, role="worker")
    middle_review = _insert_queued_run(tasks, sandwiched.task_id, role="review")
    last_worker = _insert_queued_run(tasks, sandwiched.task_id, role="worker")

    refused = dispatcher.resume_queued()

    by_run = {entry["run_id"]: entry["reason"] for entry in refused}
    assert set(scheduled) == {newer_review, older_review, middle_review}
    assert set(by_run) == {older_worker, newer_worker, first_worker, last_worker}
    # And the refusal names the review that outranked it, so the record says which
    # run holds authority rather than only that this one does not.
    assert newer_review in by_run[older_worker]
    assert "review run" in by_run[older_worker]
    assert older_review in by_run[newer_worker]
    assert middle_review in by_run[first_worker] and middle_review in by_run[last_worker]
    for run_id in by_run:
        assert store.require(run_id).state == RunState.CANCELLED, run_id


def test_a_missing_reviewer_runtime_blocks_the_task_instead_of_erasing_the_review(tmp_path):
    """An unavailable reviewer used to end as "this work needs no review".

    ``_dispatch_review`` returned silently when the project's reviewer agent was not
    in the adapter registry, and ``_after_run`` swallowed everything else, so the
    review stage simply did not exist — and both the Continue action and
    ``ExecutionStore.create`` then accepted another worker run on top of unreviewed
    work. AGENTS.md is explicit that a pinned runtime is never silently
    substituted, and replacing an independent review with none is a substitution.

    The bounded fix: record a durable BLOCKER naming what failed. ``blocker`` is a
    gating kind, so the failure now blocks the task instead of vanishing. (The
    atomic "a review is owed" marker is a separate, larger change and is recorded as
    an open residual in docs/architecture/run-gate-ordering-v1.md.)
    """
    # alpha's default reviewer_agent is "claude", and claude is disabled in this
    # registry — the ordinary "the reviewer is not installed / not configured" shape.
    tasks, store, dispatcher = _system(
        tmp_path, [sys.executable, "-c", "print('ok')"], auto_review=True,
    )
    attention = AttentionStore(tasks)
    task = _planned_task(tasks)
    worker = dispatcher.queue(_request(task.task_id))

    finished = asyncio.run(dispatcher.execute(worker.run_id))

    assert finished.state == RunState.SUCCEEDED  # the worker run's outcome is untouched
    assert [run.run_id for run in store.list(task_id=task.task_id)] == [worker.run_id]
    items = attention.list(task_id=task.task_id, state=AttentionState.OPEN)
    assert len(items) == 1
    blocker = items[0]
    assert blocker.kind == AttentionKind.BLOCKER
    assert blocker.requester == REVIEW_REQUESTER
    # Scoped to the run whose review is missing, so answering it binds to that run
    # rather than to the task at large.
    assert blocker.run_id == worker.run_id
    assert "claude" in blocker.body  # it names the runtime that was unavailable

    # It gates: the next worker run is refused rather than piled onto unreviewed work.
    with pytest.raises(RunGateError, match="open gate item") as gated:
        dispatcher.queue(_request(task.task_id, version=3))
    assert gated.value.code == GATE_CODE_ATTENTION_OPEN
    assert gated.value.items[0]["item_id"] == blocker.item_id

    # Idempotent. Re-entering the orchestration for the same run — a retry, or a
    # redelivered failure — must not pile up blockers.
    asyncio.run(dispatcher._after_run(finished, ROLE_WORKER, None))
    assert [item.item_id for item in attention.list(
        task_id=task.task_id, state=AttentionState.OPEN)] == [blocker.item_id]
    assert attention.require(blocker.item_id).version == blocker.version  # not rewritten

    # And it is a delay, not a one-way door: clearing it reopens the task.
    attention.cancel(blocker.item_id, CancelAttentionRequest(expected_version=blocker.version))
    assert dispatcher.queue(_request(task.task_id, version=3)).state == RunState.QUEUED


def test_the_review_owed_blocker_is_not_duplicated_behind_a_crowd_of_gates(tmp_path):
    """Its idempotency test used to be a scan of the first 50 open blockers.

    Attention ordering puts critical items ahead of this high-urgency one, so with
    50 critical blockers scoped to the same run the existing item fell outside the
    page the check read, it concluded "absent", and a redelivery of the same failure
    created a second item with the identical (task, run, requester, title) key —
    two blockers saying the same thing, both to be settled by hand. A cap cannot
    answer an existence question, so the check is now an exact query inside the
    insert's own transaction (``AttentionStore.create_once``).
    """
    tasks, store, dispatcher = _system(
        tmp_path, [sys.executable, "-c", "print('ok')"], auto_review=True,
    )
    attention = AttentionStore(tasks)
    task = _planned_task(tasks)
    worker = dispatcher.queue(_request(task.task_id))

    finished = asyncio.run(dispatcher.execute(worker.run_id))  # claude is disabled
    owed = [item for item in attention.list(task_id=task.task_id, state=AttentionState.OPEN)
            if item.title == dispatcher.REVIEW_FAILED_TITLE]
    assert len(owed) == 1

    # The crowd: same task, same run, same kind, higher urgency — so they sort ahead
    # of the item the check is looking for.
    for index in range(50):
        attention.create(CreateAttentionRequest(
            task_id=task.task_id, run_id=worker.run_id, kind=AttentionKind.BLOCKER,
            urgency=AttentionUrgency.CRITICAL, title=f"Unrelated blocker {index}",
            requester="codex",
        ))

    asyncio.run(dispatcher._after_run(finished, ROLE_WORKER, None))

    still = [item for item in attention.list(
        task_id=task.task_id, state=AttentionState.OPEN, limit=200,
    ) if item.title == dispatcher.REVIEW_FAILED_TITLE]
    assert [item.item_id for item in still] == [owed[0].item_id]


def test_every_cross_review_dispatch_failure_is_recorded_as_a_blocker(tmp_path):
    """The other ways the review stage disappeared, one arrangement each.

    A failed prompt build, a request the control plane rejects, a queue conflict, a
    project that left the registry, and a failed gate write all used to end at
    ``except Exception: pass`` with no review and no trace. Each now records the
    same durable blocker, naming what failed in its body.
    """
    class _GoneRegistry:
        def get(self, project_id: str):
            raise KeyError(project_id)

    def _prompt_raises(dispatcher):
        def boom(worker_run):
            raise OSError("git exited 128")
        dispatcher._review_prompt = boom
        return "OSError"

    def _prompt_is_invalid(dispatcher):
        # A prompt build that produces nothing is a *rejected request*, not a
        # crash: CreateRunRequest requires a non-blank prompt.
        dispatcher._review_prompt = lambda worker_run: ""
        return "ValidationError"

    def _queue_conflicts(dispatcher):
        def conflict(*args, **kwargs):
            raise RunConflictError("Task already has an unfinished review run (run_x)")
        dispatcher.queue = conflict
        return "RunConflictError"

    def _project_gone(dispatcher):
        dispatcher.projects = _GoneRegistry()
        return "alpha"

    for name, inject in (
        ("prompt-build", _prompt_raises),
        ("invalid-request", _prompt_is_invalid),
        ("queue-conflict", _queue_conflicts),
        ("project-gone", _project_gone),
    ):
        tasks, store, dispatcher = _system(
            tmp_path / name, [sys.executable, "-c", "print('ok')"], auto_review=True,
        )
        # Make the reviewer resolvable, so the failure under test is the one
        # reached rather than the missing-runtime one.
        dispatcher.adapters["claude"] = dispatcher.adapters["codex"]
        attention = AttentionStore(tasks)
        task = _planned_task(tasks)
        worker = dispatcher.queue(_request(task.task_id))
        finished = asyncio.run(dispatcher.execute(worker.run_id))
        assert finished.state == RunState.SUCCEEDED, name

        expect = inject(dispatcher)
        asyncio.run(dispatcher._after_run(finished, ROLE_WORKER, None))

        items = attention.list(task_id=task.task_id, state=AttentionState.OPEN)
        assert len(items) == 1, name
        assert items[0].kind == AttentionKind.BLOCKER, name
        assert items[0].requester == REVIEW_REQUESTER, name
        assert items[0].run_id == worker.run_id, name
        assert expect in items[0].body, (name, items[0].body)

    # The last one: the review run itself completed, but writing its gate failed.
    # The review existed and its verdict is still lost, so that is recorded too —
    # otherwise a successful review with no gate reads as "nothing to approve".
    tasks, store, dispatcher = _system(
        tmp_path / "gate-write", [sys.executable, "-c", "print('ok')"], auto_review=True,
    )
    attention = AttentionStore(tasks)
    task = _planned_task(tasks)
    worker = dispatcher.queue(_request(task.task_id))
    review = dispatcher.queue(
        CreateRunRequest(task_id=task.task_id, adapter="codex", prompt="review it",
                         timeout_seconds=10, expected_task_version=3),
        role=ROLE_REVIEW,
    )
    review_running = store.start(review.run_id, expected_version=review.version)
    review_done = store.finish(review.run_id, RunState.SUCCEEDED,
                               expected_version=review_running.version, exit_code=0,
                               stdout_tail="two findings", stderr_tail="")

    def _gate_write_fails(*args, **kwargs):
        raise RuntimeError("attention store is unavailable")
    dispatcher._raise_review_attention = _gate_write_fails

    asyncio.run(dispatcher._after_run(review_done, ROLE_REVIEW, worker.run_id))

    items = attention.list(task_id=task.task_id, state=AttentionState.OPEN)
    assert len(items) == 1
    assert items[0].kind == AttentionKind.BLOCKER
    assert items[0].run_id == worker.run_id  # the run whose review is unaccounted for
    assert "attention store is unavailable" in items[0].body


def test_run_gate_conflict_codes_mirrored_by_the_frontend():
    """frontend/src/lib/continue-run.ts hardcodes these two 409 codes in
    RUN_GATE_CODES, because a client cannot ask the server what its own refusal
    means. Nothing at runtime keeps the two in step, so this test is the alarm: if
    it fails, that file is what has to be updated."""
    mirror = (
        Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "continue-run.ts"
    )
    source = mirror.read_text(encoding="utf-8")
    assert f'attentionOpen: "{GATE_CODE_ATTENTION_OPEN}"' in source
    assert f'reviewActive: "{GATE_CODE_REVIEW_ACTIVE}"' in source


def test_run_contract_constants_mirrored_by_the_frontend(tmp_path):
    """frontend/src/lib/continue-run.ts hardcodes these three facts:
    CONTINUE_PROMPT_LIMIT = 16_000, CONTINUE_TIMEOUT_SECONDS = 600, and
    CONTINUABLE_TASK_STATES = ["planned", "running"]. Nothing at runtime tells it
    when they change, so this test is the alarm: if it fails, that file is what
    has to be updated. Deliberately not solved by shipping the values over the
    API — a UI that must ask the server what its own button says is worse than a
    test that names the file."""
    prompt = CreateRunRequest.model_fields["prompt"]
    assert [meta.max_length for meta in prompt.metadata if hasattr(meta, "max_length")] == [16_000]
    assert CreateRunRequest.model_fields["timeout_seconds"].default == 600

    # The state guard itself, not a copy of it: queue a run against a task in
    # every state and see which states are accepted. One task per state, so the
    # same-role overlap guard never confounds the answer.
    tasks, _, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    accepted: set[str] = set()
    for state in TaskState:
        task = tasks.create(CreateTaskRequest(project_id="alpha", title=f"In {state.value}"))
        with tasks._transaction() as db:
            db.execute("UPDATE tasks SET state = ?, version = 2 WHERE task_id = ?",
                       (state.value, task.task_id))
        try:
            dispatcher.queue(_request(task.task_id))
            accepted.add(state.value)
        except RunConflictError:
            pass
    assert accepted == {"planned", "running"}


def test_deliverable_endpoints_gate_on_provisioned_worktree(tmp_path):
    """The list/content/raw HTTP endpoints serve a run's deliverables only from
    its provisioned worktree, and fail closed once it is torn down."""
    from agora.tasks.router import get_project_registry

    tasks, store, dispatcher = _system(
        tmp_path, [sys.executable, "-c", "open('out.txt', 'w').write('hi there')"]
    )
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))
    asyncio.run(dispatcher.execute(run.run_id))

    app.dependency_overrides[get_execution_store] = lambda: store
    app.dependency_overrides[get_execution_dispatcher] = lambda: dispatcher
    app.dependency_overrides[get_project_registry] = lambda: dispatcher.projects
    client = TestClient(app)
    try:
        listing = client.get(f"/api/runs/{run.run_id}/deliverables")
        assert listing.status_code == 200
        assert any(f["path"] == "out.txt" for f in listing.json()["files"])
        content = client.get(f"/api/runs/{run.run_id}/deliverables/content", params={"path": "out.txt"})
        assert content.status_code == 200 and "hi there" in content.json()["content"]
        assert client.get(f"/api/runs/{run.run_id}/deliverables/raw", params={"path": "out.txt"}).status_code == 200

        # Tear down the worktree -> a plain dir holding a *different* out.txt.
        # Every path that READS the filesystem must still fail closed.
        workspace = dispatcher.projects.get("alpha").workspaces["codex"]
        shutil.rmtree(workspace)
        workspace.mkdir(parents=True)
        (workspace / "out.txt").write_text("resurrected", encoding="utf-8")
        assert client.get(f"/api/runs/{run.run_id}/deliverables/content", params={"path": "out.txt"}).status_code == 404
        assert client.get(f"/api/runs/{run.run_id}/deliverables/raw", params={"path": "out.txt"}).status_code == 404

        # The listing, however, is no longer a directory listing: it is the
        # snapshot frozen when the run ended, so it keeps reporting what this run
        # delivered even though the worktree is gone. That is stored data, not a
        # filesystem read, so it discloses nothing the run did not already own —
        # and `content_available` tells the UI the files can no longer be opened.
        relisting = client.get(f"/api/runs/{run.run_id}/deliverables").json()
        assert relisting["source"] == "snapshot"
        assert [f["path"] for f in relisting["files"]] == ["out.txt"]
        assert relisting["content_available"] is False
    finally:
        app.dependency_overrides.clear()


def test_review_prompt_ignores_non_ready_worker_workspace(tmp_path):
    """The review prompt reads the worker's deliverables only from its own
    provisioned worktree; on a wrong-branch worktree it reads nothing."""
    tasks, store, dispatcher = _system(
        tmp_path, [sys.executable, "-c", "open('d.txt', 'w').write('x')"]
    )
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))
    asyncio.run(dispatcher.execute(run.run_id))
    worker = store.require(run.run_id)

    assert "d.txt" in dispatcher._review_prompt(worker)  # ready worktree

    workspace = dispatcher.projects.get("alpha").workspaces["codex"]
    subprocess.run([GIT, "-C", str(workspace), "checkout", "-q", "-b", "other"],
                   check=True, timeout=15)
    assert "No changed files" in dispatcher._review_prompt(worker)  # not ready -> nothing


def test_ready_worktree_for_run_gates_deliverable_endpoints(tmp_path):
    """The provenance gate the content/raw/list endpoints call accepts a run's
    own worktree and rejects a torn-down (plain-dir) workspace."""
    from agora.execution.router import _ready_worktree_for_run

    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))
    worker = store.require(run.run_id)
    assert _ready_worktree_for_run(dispatcher.projects, worker) is True

    workspace = dispatcher.projects.get("alpha").workspaces["codex"]
    shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    assert _ready_worktree_for_run(dispatcher.projects, store.require(run.run_id)) is False


def test_run_refused_when_workspace_is_not_a_provisioned_worktree(tmp_path):
    """An agent is never launched unless its workspace is the provisioned
    worktree; here we replace it with a plain directory."""
    tasks, store, dispatcher = _system(
        tmp_path, [sys.executable, "-c", "open('leak.txt', 'w').write('should not run')"]
    )
    workspace = dispatcher.projects.get("alpha").workspaces["codex"]
    # Tear down the worktree and leave a plain (non-worktree) directory behind.
    shutil.rmtree(workspace)
    workspace.mkdir(parents=True)

    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))
    finished = asyncio.run(dispatcher.execute(run.run_id))

    assert finished.state == RunState.FAILED
    assert "provisioned worktree" in (finished.error_message or "")
    assert not (workspace / "leak.txt").exists()  # process never launched


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
    shutil.rmtree(workspace)  # a provisioned worktree is non-empty (holds .git)
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
    # Two tasks, not two runs on one task: a task holds at most one unfinished
    # worker run, and what this test is about is the project-level slot.
    task = _planned_task(tasks)
    other = _planned_task(tasks)
    first = dispatcher.queue(CreateRunRequest(
        task_id=task.task_id, adapter="codex", prompt="one", timeout_seconds=10,
        expected_task_version=2,
    ))
    second = dispatcher.queue(CreateRunRequest(
        task_id=other.task_id, adapter="codex", prompt="two", timeout_seconds=10,
        expected_task_version=2,
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
