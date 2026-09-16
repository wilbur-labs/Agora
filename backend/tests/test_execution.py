from __future__ import annotations

import asyncio
import ast
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import agora.api.app as app_module
from agora.api.app import app
from agora.fsprobe import PathProbe
from agora.execution.adapters import ExecutionAdapter, RemoteAdapterConfig, build_adapter_registry
from agora.execution.dispatcher import ExecutionDispatcher, redact_output
from agora.execution.models import (
    CancelRunRequest,
    CreateRunRequest,
    REVIEWS_RUN_KEY,
    ROLE_KEY,
    ROLE_REVIEW,
    ROLE_WORKER,
    RunState,
)
from agora.execution.router import get_execution_dispatcher, get_execution_store
from agora.execution.store import (
    ExecutionStore,
    GATE_CODE_ATTENTION_OPEN,
    GATE_CODE_REVIEW_ACTIVE,
    GATE_CODE_REVIEW_OWED,
    REVIEW_OWED_NO_GATE_ITEM,
    REVIEW_OWED_NO_REVIEW_RUN,
    REVIEW_OWED_REVIEW_CANCELLED,
    RunConflictError,
    RunGateError,
    RunValidationError,
)
from agora.projects import ProjectRegistry
from agora.tasks.models import AppendEventRequest, CreateTaskRequest, TaskState
from agora.tasks.store import TaskStore
from agora.attention.store import AttentionStore, AttentionValidationError
from agora.attention.router import get_attention_store
from agora.attention.run_scope import ORPHANED_PROMPT_REASON
from agora.attention.bridges.models import BridgeEventRequest, BridgeVendor, DeliveryMode
from agora.attention.bridges.normalize import normalize_hook_event
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
                    # Explicit, not the registry default: these tests were written against
                    # a codex worker reviewed by claude, and the product default moved to
                    # claude reviewed by codex (2026-09-17). A fixture that leaned on the
                    # default changed meaning the day the default did.
                    "worker_agent": "codex",
                    "reviewer_agent": "claude",
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


def _ssh_adapter_config(**overrides):
    """A minimal valid ssh adapter config section. Host is a placeholder — no real
    IP/hostname is committed (public repo, SECURITY-12)."""
    ssh = {
        "ssh_host": "example.test",
        "ssh_user": "runner",
        "remote_workspace": "/srv/agora/wt",
        "remote_project_root": "/srv/agora/repo",
        "secret_ref": "AGORA_SSH_KEY_CODEX",
    }
    ssh.update(overrides)
    return {"execution": {"adapters": {"codex": {"bridge_mode": "ssh", "ssh": ssh}}}}


def test_ssh_adapter_config_parsed_onto_adapter():
    registry = build_adapter_registry(_ssh_adapter_config())
    remote = registry["codex"].remote
    assert isinstance(remote, RemoteAdapterConfig)
    assert remote.ssh_host == "example.test"
    assert remote.ssh_user == "runner"
    assert remote.remote_project_root == "/srv/agora/repo"
    assert remote.secret_ref == "AGORA_SSH_KEY_CODEX"
    # Defaults from the frozen contract.
    assert remote.ssh_port == 22
    assert remote.host_key_mode == "strict"       # NFR-1 default
    assert remote.known_hosts_path is None
    assert remote.cleanup_worktree is False       # Q5=C default keep


def test_local_adapter_has_no_remote_config():
    registry = build_adapter_registry({"execution": {"adapters": {}}})
    assert registry["codex"].remote is None


def test_ssh_bridge_mode_requires_ssh_section():
    cfg = {"execution": {"adapters": {"codex": {"bridge_mode": "ssh"}}}}
    with pytest.raises(ValueError, match="needs an 'ssh' config section"):
        build_adapter_registry(cfg)


def test_ssh_section_forbidden_without_ssh_bridge_mode():
    # A local adapter carrying ssh config is a config error, not silently ignored:
    # it signals the operator expected remote execution that will not happen.
    cfg = {"execution": {"adapters": {"codex": {"ssh": {"ssh_host": "example.test"}}}}}
    with pytest.raises(ValueError, match="bridge_mode is not 'ssh'"):
        build_adapter_registry(cfg)


def test_explicit_ssh_null_without_ssh_bridge_mode_is_a_config_error():
    # 'ssh' present as an explicit null is still a present 'ssh' section on a local
    # adapter — keyed on membership, not truthiness, so it fails closed like {...}.
    cfg = {"execution": {"adapters": {"codex": {"bridge_mode": "cli", "ssh": None}}}}
    with pytest.raises(ValueError, match="bridge_mode is not 'ssh'"):
        build_adapter_registry(cfg)


def test_disabled_adapter_skips_ssh_validation_on_purpose():
    # A disabled adapter never enters the registry; its (here invalid) ssh section
    # is inert and deliberately not validated. Locks that intentional behavior so a
    # later "validate everything" change is a conscious decision, not a silent one.
    cfg = {"execution": {"adapters": {"codex": {"enabled": False, "bridge_mode": "ssh"}}}}
    registry = build_adapter_registry(cfg)
    assert "codex" not in registry


def test_ssh_config_rejects_unknown_key_fail_closed():
    # extra='forbid': a misspelled/unsupported field fails at load, never ignored.
    with pytest.raises(ValueError, match="ssh config invalid"):
        build_adapter_registry(_ssh_adapter_config(ssh_hostt="typo.test"))


def test_ssh_config_requires_mandatory_fields():
    cfg = {"execution": {"adapters": {"codex": {
        "bridge_mode": "ssh", "ssh": {"ssh_host": "example.test"}  # missing user/workspace/root/secret_ref
    }}}}
    with pytest.raises(ValueError, match="ssh config invalid"):
        build_adapter_registry(cfg)


def test_ssh_config_carries_no_plaintext_secret_field():
    # Credentials travel by env-var name (secret_ref), never inline. extra='forbid'
    # means an attempt to put a raw key/password field in config fails closed.
    with pytest.raises(ValueError, match="ssh config invalid"):
        build_adapter_registry(_ssh_adapter_config(ssh_password="hunter2"))


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
        ), internal=True)

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

    # A question gates too, as of 2026-08-30. It used to be exempt on the ground
    # that it binds to no artifact and settles no decision — but a new run started
    # while the question is unanswered proceeds on an unanswered assumption, which
    # makes asking pointless. Answering it is the release (see
    # test_an_open_question_gates_the_next_worker_run for the full set).
    question = raise_item(AttentionKind.QUESTION, "Which database should this use?")
    with pytest.raises(RunGateError, match="open gate item") as asked:
        dispatcher.queue(_request(task.task_id, version=3))
    assert asked.value.items[0]["kind"] == "question"
    attention.respond(question.item_id, RespondAttentionRequest(
        action=ResponseAction.ANSWER, response="postgres", expected_version=question.version,
    ))
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
    """The half of Run -> Gate ordering enforceable from a row that already exists.

    A review run is a row in this database, readable in the create transaction.
    Without this check a worker run created while its predecessor's review executes
    is refused by nothing: the same-role guard only compares worker against worker.
    The gap between a worker run settling and its review being *queued* has no such
    row, and is refused by the derived predicate instead — see
    test_a_worker_run_is_refused_while_its_predecessor_owes_a_review.
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
    ), internal=True)
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
    ), internal=True)
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
    ), internal=True)

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


def _insert_queued_run(
    tasks: TaskStore, task_id: str, *, role: str | None, adapter="codex",
    task_version: int | None = -1,
):
    """A persisted QUEUED row of the shape an older database — or a crash — leaves
    behind: inserted without passing through ``create``'s gates.

    ``task_version`` defaults to the task's version *at insert time*, which is what
    ``create`` would have recorded, so a row is only missing that key when a test
    asks for it (``task_version=None``). Since N2 a row with no recorded version is
    refused rather than lifecycle-checked only, so the two cases have to be
    distinguishable here; before N2 every row this helper made was version-less and
    the difference did not show.
    """
    run_id = f"run_persisted_{uuid.uuid4().hex[:8]}"
    if task_version == -1:
        current = tasks.get(task_id)
        task_version = None if current is None else current.version
    metadata: dict = {}
    if role is not None:
        metadata["agora_role"] = role
    if task_version is not None:
        metadata["task_version"] = task_version
    with tasks._transaction() as db:
        db.execute(
            """INSERT INTO execution_runs
               (run_id, task_id, project_id, adapter, state, prompt, workspace, command,
                timeout_seconds, result_metadata, version, queued_at, actor)
               VALUES (?, ?, 'alpha', ?, 'queued', 'go', '.', '[]', 10, ?, 1, ?, 'user')""",
            (run_id, task_id, adapter, json.dumps(metadata),
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
    ), internal=True)
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
    ), internal=True)
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

    **Case 3 pins the opposite of what it used to (N2, 2026-08-30).** It previously
    asserted that a row recording *no* task version is **admitted** — that
    ``_task_refusal`` skips the check it cannot answer and schedules the run. That
    expectation was wrong, and this test was protecting the defect: unknown authority
    was being read as permission. A version-less queued row is reachable without
    anyone editing a database — an older Agora persisted it — and the task can then
    be edited or moved ``running -> blocked -> running``, bumping its version, with
    nothing able to say which version the run was validated against. Admitting it
    launches an agent against a prompt and a worktree built for state that no longer
    exists, which is exactly what the recorded-version check exists to prevent, and
    "the row is probably fine" is not something this transaction can read. So unknown
    now **refuses**, with a reason naming the version it could not check against, and
    the run is settled CANCELLED like every other refusal here. Every run ``create``
    makes records the version, so no run this build creates reaches that branch.
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
    #    edited by hand. Unknown authority fails closed: it cannot be checked, so it
    #    is refused rather than admitted. The task here is perfectly healthy, which
    #    is the point — nothing about the *task* is wrong, and the run is still
    #    refused because nothing can say what it was validated against.
    legacy = _planned_task(tasks)
    legacy_run = _insert_queued_run(tasks, legacy.task_id, role="worker", task_version=None)

    # 4. ...and the lifecycle question still comes first, so a version-less row on a
    #    task that has left planned/running is refused for the task, not the version.
    legacy_dead = _planned_task(tasks)
    legacy_cancelled = _insert_queued_run(
        tasks, legacy_dead.task_id, role="worker", task_version=None
    )
    tasks.cancel(legacy_dead.task_id, expected_version=2)

    # 5. A queued *review* is asked the same questions. A review of a cancelled
    #    task is not evidence anyone is waiting for; the review exemption is from
    #    the worker gates, not from the task's own lifecycle.
    dead_review_task = _planned_task(tasks)
    dead_review = _insert_queued_run(tasks, dead_review_task.task_id, role="review")
    tasks.cancel(dead_review_task.task_id, expected_version=2)

    # 6. The control: unchanged task, matching version, resumed. A persisted row that
    #    *does* record the current version resumes too — the refusal in case 3 is
    #    about the version being unknown, not about the row being hand-inserted.
    fine = _planned_task(tasks)
    healthy = dispatcher.queue(_request(fine.task_id))
    persisted_ok = _planned_task(tasks)
    persisted_run = _insert_queued_run(tasks, persisted_ok.task_id, role="worker")

    refused = dispatcher.resume_queued()

    by_run = {entry["run_id"]: entry["reason"] for entry in refused}
    assert set(by_run) == {
        doomed.run_id, stale.run_id, legacy_run, legacy_cancelled, dead_review
    }
    assert set(scheduled) == {healthy.run_id, persisted_run}
    assert "its task is cancelled" in by_run[doomed.run_id]
    assert "version 3 to 5" in by_run[stale.run_id]
    assert "records no task version" in by_run[legacy_run]
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
    ), internal=True)

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
    ), internal=True)
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
    review = dispatcher.queue(
        CreateRunRequest(task_id=task.task_id, adapter="codex", prompt="review",
                         timeout_seconds=10, expected_task_version=2),
        role=ROLE_REVIEW,
    )
    # Inserted after the review, so it records the version the review's
    # planned -> running bump left behind. Before N2 a version-less row skipped the
    # version question; now it would be refused for that instead of for the review,
    # and this case is about the review.
    worker = _insert_queued_run(tasks, task.task_id, role="worker")
    store.start(review.run_id, expected_version=review.version)

    reason = store.revalidate_before_launch(worker)

    assert reason is not None and review.run_id in reason
    assert store.require(worker).state == RunState.CANCELLED


def test_a_generic_question_scoped_to_a_queued_run_still_gates_it(tmp_path):
    """The re-validation exclusion is cut by provable **origin**, not by ``run_id`` (M2).

    ``_open_gate_items`` drops the items a queued run raised *itself*, so a run does
    not wait behind its own vendor permission prompt. That was implemented as
    ``run_id <> ?``, which is only equivalent while every item carrying a run's id
    came from that run's bridge — and since D1 made ``kind=question`` gate, it does
    not. ``POST /api/attention`` takes ``kind``, ``requester`` and ``run_id`` from the
    client, so a *generic* question about the queued run was excluded by the same
    rule: the run launched with an unanswered question about it and proceeded on a
    guess, which is exactly what D1 exists to prevent.

    The fix is the distinction ``agora.attention.run_scope`` already makes: exclude
    only items with a row in ``attention_bridge_events`` for this run. Both halves are
    asserted here, at both re-validations — the generic question gates, the run's own
    bridge prompt still does not.
    """
    launched = [sys.executable, "-c", "open('launched.txt', 'w').write('x')"]

    # 1. At launch: a generic question naming the queued run refuses it.
    tasks, store, dispatcher = _system(tmp_path / "launch", launched)
    attention = AttentionStore(tasks)
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))
    asked = attention.create(CreateAttentionRequest(
        task_id=task.task_id, run_id=run.run_id, kind=AttentionKind.QUESTION,
        title="Which database should this run migrate?", requester="operator",
    ))

    settled = asyncio.run(dispatcher.execute(run.run_id))

    assert settled.state == RunState.CANCELLED
    assert settled.error_message.startswith("Not launched: ")
    assert asked.item_id in settled.error_message
    assert not (dispatcher.projects.get("alpha").workspaces["codex"] / "launched.txt").exists()
    # Answering it is the exit, as it is for every gate item.
    attention.respond(asked.item_id, RespondAttentionRequest(
        action=ResponseAction.APPROVE, expected_version=asked.version,
    ))
    retry = dispatcher.queue(_request(task.task_id, version=3))
    assert asyncio.run(dispatcher.execute(retry.run_id)).state == RunState.SUCCEEDED

    # 2. At launch: the run's OWN bridge prompt is still not a gate on it. Same kind
    #    of row, same run_id — only the origin differs, which is the whole point.
    tasks, store, dispatcher = _system(tmp_path / "own-bridge", launched)
    attention = AttentionStore(tasks)
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))
    _bridge_prompt(attention, task.task_id, run.run_id, rpc_id=21)

    assert store.revalidate_before_launch(run.run_id) is None
    assert asyncio.run(dispatcher.execute(run.run_id)).state == RunState.SUCCEEDED

    # 3. The resume sweep reads the same helper, so both halves hold there too.
    tasks, store, dispatcher = _system(tmp_path / "resume", launched)
    attention = AttentionStore(tasks)

    bridged = _planned_task(tasks)
    admitted = _insert_queued_run(tasks, bridged.task_id, role="worker")
    _bridge_prompt(attention, bridged.task_id, admitted, rpc_id=22)

    questioned = _planned_task(tasks)
    refused_run = _insert_queued_run(tasks, questioned.task_id, role="worker")
    question = attention.create(CreateAttentionRequest(
        task_id=questioned.task_id, run_id=refused_run, kind=AttentionKind.QUESTION,
        title="Which schema does this run target?", requester="operator",
    ))

    scheduled: list[str] = []
    dispatcher.schedule = lambda run_id: scheduled.append(run_id)  # type: ignore[method-assign]
    refused = dispatcher.resume_queued()

    assert scheduled == [admitted]
    assert store.require(admitted).state == RunState.QUEUED
    assert [entry["run_id"] for entry in refused] == [refused_run]
    assert question.item_id in refused[0]["reason"]
    assert store.require(refused_run).state == RunState.CANCELLED


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


# --- D1: an unanswered question gates the next worker run --------------------


def test_an_open_question_gates_the_next_worker_run(tmp_path):
    """``question`` joins ``GATING_ATTENTION_KINDS``.

    The rule it replaces argued that a question binds to no artifact and settles no
    decision, so answering it later is legitimate. The answer to "later" turned out
    to be "by a run that already guessed": an agent that asked the human something,
    got no answer, and is followed by a new run proceeds on an *unanswered
    assumption*, which makes asking pointless.

    Release conditions asserted here: answering it, and cancelling it. Together
    they are what actually makes gating this kind safe — a ``question`` is
    **always human-clearable** — and they hold for an item from any producer,
    including the generic ``POST /api/attention`` used here, which writes no
    bridge row and is therefore never settled with any run. (The *additional*
    release that only bridge-origin questions have — the run that asked settling —
    is ``test_only_a_bridge_question_is_settled_with_its_run``.)
    """
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    attention = AttentionStore(tasks)
    task = _planned_task(tasks)
    first = dispatcher.queue(_request(task.task_id))
    running = store.start(first.run_id, expected_version=first.version)
    store.finish(first.run_id, RunState.SUCCEEDED, expected_version=running.version,
                 exit_code=0, stdout_tail="asked a question", stderr_tail="")

    question = attention.create(CreateAttentionRequest(
        task_id=task.task_id, kind=AttentionKind.QUESTION,
        title="Which cache backend should this use?", requester="codex",
    ))

    with pytest.raises(RunGateError, match="open gate item") as gated:
        dispatcher.queue(_request(task.task_id, version=3))
    assert gated.value.code == GATE_CODE_ATTENTION_OPEN
    assert gated.value.items == [{
        "item_id": question.item_id, "kind": "question",
        "title": "Which cache backend should this use?",
    }]
    # The refusal says how to clear it, which is the whole point of gating a
    # question rather than only refusing to offer the button.
    assert "answer" in str(gated.value)

    # Release 1: answering it.
    attention.respond(question.item_id, RespondAttentionRequest(
        action=ResponseAction.ANSWER, response="redis", expected_version=question.version,
    ))
    assert dispatcher.queue(_request(task.task_id, version=3)).state == RunState.QUEUED

    # Release 2: cancelling it, on a second task so the run above does not confound
    # the answer.
    other = _planned_task(tasks)
    dropped = attention.create(CreateAttentionRequest(
        task_id=other.task_id, kind=AttentionKind.QUESTION, title="Which region?",
        requester="codex",
    ))
    with pytest.raises(RunGateError):
        dispatcher.queue(_request(other.task_id))
    attention.cancel(dropped.item_id, CancelAttentionRequest(expected_version=dropped.version))
    assert dispatcher.queue(_request(other.task_id)).state == RunState.QUEUED


def test_only_a_bridge_question_is_settled_with_its_run(tmp_path):
    """The release a bridge-origin ``question`` has, and the one a generic one does
    not.

    ``bridges/normalize.normalize_hook_event`` is the only *runtime* producer of
    ``kind=question`` — a non-permission hook event delivered during a run — and an
    item it raises goes in through ``create_bridge_event``, which writes the
    ``attention_bridge_events`` row that ``settle_bridge_attention_for_run`` joins
    on. So that item is cancelled in the same transaction that settles its run,
    exactly as a permission ``approval`` is (section 1a of
    docs/architecture/run-gate-ordering-v1.md).

    **This is not true of every question, and the safety of gating the kind does not
    rest on it.** The generic ``POST /api/attention`` -> ``AttentionStore.create``
    takes ``kind`` and ``requester`` from the client and writes no bridge row, so
    the run-scoped sweep never touches such an item — asserted below alongside the
    bridge one, because the earlier docstring here claimed the absolute ("every
    runtime question is bridge-origin, so every question settles with its run") and
    a false safety argument is what the next person relaxing this rule would rely
    on. What makes gating safe is that a question is always human-clearable:
    answerable or cancellable through the Attention page for as long as it exists
    (``test_an_open_question_gates_the_next_worker_run``).
    """
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    attention = AttentionStore(tasks)
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))

    # The producer itself, not a hand-built item: a hook event that is not a
    # permission request normalizes to kind=question.
    event = normalize_hook_event(
        BridgeVendor.KIRO, {"hook_event_name": "notification", "tool_name": "fs_write",
                            "message": "Which directory should this go in?"},
        task_id=task.task_id, run_id=run.run_id,
    )
    assert event.kind == AttentionKind.QUESTION
    item_id = attention.create_bridge_event(event).item_id
    assert attention.require(item_id).expires_at is None  # nothing sweeps it later

    # The other producer, scoped to the same run: no bridge row, so the sweep below
    # must leave it exactly as it is.
    generic = attention.create(CreateAttentionRequest(
        task_id=task.task_id, run_id=run.run_id, kind=AttentionKind.QUESTION,
        title="Which region should this deploy to?", requester="operator",
    ))

    running = store.start(run.run_id, expected_version=run.version)
    store.finish(run.run_id, RunState.SUCCEEDED, expected_version=running.version,
                 exit_code=0, stdout_tail="done", stderr_tail="")

    settled = attention.require(item_id)
    assert settled.state == AttentionState.CANCELLED
    assert settled.cancellation_reason == ORPHANED_PROMPT_REASON
    # The generic question is still open, and still gating: it is human-clearable,
    # which is the actual safety property, but nothing settles it with a run.
    assert attention.require(generic.item_id).state == AttentionState.OPEN
    with pytest.raises(RunGateError, match="open gate item") as gated:
        dispatcher.queue(_request(task.task_id, version=3))
    assert gated.value.items == [{
        "item_id": generic.item_id, "kind": "question",
        "title": "Which region should this deploy to?",
    }]
    # And cancelling it — the human exit that every question has — releases the gate,
    # so this is not the section 1a lockout.
    attention.cancel(generic.item_id, CancelAttentionRequest(expected_version=generic.version))
    assert dispatcher.queue(_request(task.task_id, version=3)).state == RunState.QUEUED


# --- D2: the handoff window between a worker settling and its review ---------


def _settled_worker_run(dispatcher, store, task_id: str, *, state=RunState.SUCCEEDED, version: int = 2):
    """A terminal worker run with no review anywhere near it: the handoff gap.

    Built through the store rather than by awaiting ``execute``, because the gap *is*
    the moment after ``finish`` and before ``_dispatch_review``: going through the
    dispatcher would run the dispatch (or record its blocker) and close the window
    the test is about.
    """
    run = dispatcher.queue(_request(task_id, version=version))
    running = store.start(run.run_id, expected_version=run.version)
    store.finish(run.run_id, state, expected_version=running.version,
                 exit_code=0 if state == RunState.SUCCEEDED else 1,
                 stdout_tail="implemented it", stderr_tail="")
    return run


def test_a_worker_run_is_refused_while_its_predecessor_owes_a_review(tmp_path):
    """The gap a reviewer reproduced through the API.

    ``_reject_active_review`` reads a review *row*, so it says nothing in the window
    where the worker run is terminal and ``_dispatch_review`` has not inserted one
    yet: no run is active, no gate is open, and the create transaction accepted
    another worker run on unreviewed work. The fact is now *derived* inside that
    transaction — a terminal SUCCEEDED worker run that no review run references,
    with auto-review on — rather than written as a marker that could leak.

    Release asserted here: the review run appearing. Nothing is written, so nothing
    has to be swept.
    """
    tasks, store, dispatcher = _system(
        tmp_path, [sys.executable, "-c", "print('ok')"], auto_review=True,
    )
    task = _planned_task(tasks)
    worker = _settled_worker_run(dispatcher, store, task.task_id)

    with pytest.raises(RunGateError, match="owes a review") as gated:
        dispatcher.queue(_request(task.task_id, version=3))
    assert gated.value.code == GATE_CODE_REVIEW_OWED
    assert gated.value.items == [{"run_id": worker.run_id, "state": "succeeded"}]
    assert "cancelling that blocker" in str(gated.value)  # it says how to clear it

    # The review run appears. The predicate goes false on its own; from here the
    # unsettled-review refusal takes over, which is a different answer.
    review = dispatcher.queue(
        CreateRunRequest(task_id=task.task_id, adapter="codex", prompt="review it",
                         timeout_seconds=10, expected_task_version=3),
        role=ROLE_REVIEW, reviews_run_id=worker.run_id,
    )
    with pytest.raises(RunGateError) as under_review:
        dispatcher.queue(_request(task.task_id, version=3))
    assert under_review.value.code == GATE_CODE_REVIEW_ACTIVE

    # And once that review has settled *and its gate item exists*, nothing is owed.
    # The gate item is the release, not the review run's state: a review run that
    # succeeded is evidence a review happened, and the approval is the human gate
    # its outcome raises — see the L2 regression below for what the run's state
    # alone used to be allowed to claim.
    review_running = store.start(review.run_id, expected_version=review.version)
    settled = store.finish(review.run_id, RunState.SUCCEEDED,
                           expected_version=review_running.version,
                           exit_code=0, stdout_tail="no findings", stderr_tail="")
    asyncio.run(dispatcher._after_run(settled, ROLE_REVIEW, worker.run_id))
    approval = AttentionStore(tasks).list(task_id=task.task_id, state=AttentionState.OPEN)[0]
    assert approval.kind == AttentionKind.APPROVAL and approval.run_id == worker.run_id
    assert store.reviews_owed() == []
    # That approval is itself an open gate, so settle it the way a human would
    # before asking for the next worker run.
    AttentionStore(tasks).respond(approval.item_id, RespondAttentionRequest(
        action=ResponseAction.APPROVE, expected_version=approval.version,
    ))
    assert dispatcher.queue(_request(task.task_id, version=3)).state == RunState.QUEUED


def test_no_review_is_owed_when_auto_review_is_off(tmp_path):
    """``auto_review`` is dispatcher configuration the store cannot read, so the
    flag is passed into the create transaction and the derived question is asked
    there. With the flag off no review would ever have been dispatched, so claiming
    one is owed would refuse a run for a review that could never arrive."""
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    assert dispatcher.auto_review is False
    task = _planned_task(tasks)
    _settled_worker_run(dispatcher, store, task.task_id)

    assert dispatcher.queue(_request(task.task_id, version=3)).state == RunState.QUEUED
    # And the reconciliation matches the predicate exactly: with the flag off it
    # records nothing, rather than blocking tasks for a review nobody asked for.
    assert dispatcher.reconcile_reviews_owed() == []
    assert AttentionStore(tasks).list(task_id=task.task_id) == []


def test_an_unsuccessful_worker_run_owes_no_review(tmp_path):
    """Matched to ``_dispatch_review``'s own trigger: ``_after_run`` dispatches a
    review only for a worker run that SUCCEEDED. A predicate that claimed a review
    was owed for a failed, timed-out or cancelled run would refuse every retry of
    exactly the run a human needs to retry."""
    for name, state in (
        ("failed", RunState.FAILED),
        ("timed-out", RunState.TIMED_OUT),
    ):
        tasks, store, dispatcher = _system(
            tmp_path / name, [sys.executable, "-c", "print('ok')"], auto_review=True,
        )
        task = _planned_task(tasks)
        _settled_worker_run(dispatcher, store, task.task_id, state=state)
        assert dispatcher.queue(_request(task.task_id, version=3)).state == RunState.QUEUED, name
        assert dispatcher.reconcile_reviews_owed() == [], name

    # Cancelled, which does not go through finish() at all.
    tasks, store, dispatcher = _system(
        tmp_path / "cancelled", [sys.executable, "-c", "print('ok')"], auto_review=True,
    )
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))
    store.cancel(run.run_id, CancelRunRequest(expected_version=run.version))
    assert dispatcher.queue(_request(task.task_id, version=3)).state == RunState.QUEUED
    assert dispatcher.reconcile_reviews_owed() == []


def test_only_the_latest_terminal_worker_run_can_owe_a_review(tmp_path):
    """An older unreviewed run is not one a review would be dispatched for.

    ``_after_run`` dispatches for the run that just settled, so the predicate reads
    the task's most recent terminal worker run and no other. This also bounds what
    turning ``auto_review`` on over an existing database can raise: one item per
    task, not one per historical run.
    """
    tasks, store, dispatcher = _system(
        tmp_path, [sys.executable, "-c", "print('ok')"], auto_review=True,
    )
    task = _planned_task(tasks)
    first = _settled_worker_run(dispatcher, store, task.task_id)
    review = dispatcher.queue(
        CreateRunRequest(task_id=task.task_id, adapter="codex", prompt="review it",
                         timeout_seconds=10, expected_task_version=3),
        role=ROLE_REVIEW, reviews_run_id=first.run_id,
    )
    review_running = store.start(review.run_id, expected_version=review.version)
    reviewed = store.finish(review.run_id, RunState.SUCCEEDED,
                            expected_version=review_running.version,
                            exit_code=0, stdout_tail="no findings", stderr_tail="",
                            result_metadata={"usage": {"total": 11}})
    # The gate item that review raises, without which the review is owed again (L2).
    asyncio.run(dispatcher._after_run(reviewed, ROLE_REVIEW, first.run_id))
    approval = AttentionStore(tasks).list(task_id=task.task_id, state=AttentionState.OPEN)[0]
    AttentionStore(tasks).respond(approval.item_id, RespondAttentionRequest(
        action=ResponseAction.APPROVE, expected_version=approval.version,
    ))

    # A second worker run that FAILED is now the latest: nothing is owed, even
    # though `first` is still succeeded and reviewed.
    _settled_worker_run(dispatcher, store, task.task_id, state=RunState.FAILED, version=3)
    assert dispatcher.reconcile_reviews_owed() == []
    assert dispatcher.queue(_request(task.task_id, version=3)).state == RunState.QUEUED


def test_a_task_that_can_no_longer_run_owes_no_review(tmp_path):
    """``create`` refuses any run — worker or review — for a task outside
    planned/running, so a review could not have been dispatched for one that has
    left them: claiming a review is owed there would claim one that would never
    have happened. It also keeps the startup reconciliation from writing blockers
    onto tasks that are already done, which is inbox noise a human clears for
    nothing."""
    tasks, store, dispatcher = _system(
        tmp_path, [sys.executable, "-c", "print('ok')"], auto_review=True,
    )
    task = _planned_task(tasks)
    _settled_worker_run(dispatcher, store, task.task_id)
    with tasks._transaction() as db:
        db.execute("UPDATE tasks SET state = ? WHERE task_id = ?",
                   (TaskState.VERIFIED.value, task.task_id))

    assert dispatcher.reconcile_reviews_owed() == []
    assert AttentionStore(tasks).list(task_id=task.task_id) == []


def test_a_review_lost_to_a_crash_is_reconciled_into_a_blocker_at_startup(tmp_path):
    """The one release condition the derived refusal cannot read for itself.

    If the process dies between the worker run settling and ``_dispatch_review``
    running, nothing will ever insert the review and nothing recorded a blocker — so
    the predicate stays true forever and no worker run could start on that task
    again. That is the lockout this slice keeps producing, so it is reconciled at
    startup: ``reconcile_reviews_owed`` records the blocker that says what happened
    (chosen over re-dispatching, because ``_review_prompt`` reads the *live*
    worktree and after a restart that directory is no longer guaranteed to hold what
    the run delivered).

    The blocker is the exit: it gates by itself, it names the two ways out, and once
    it exists the derived refusal stands down for that run — so clearing it clears
    everything rather than one of two gates.
    """
    tasks, store, dispatcher = _system(
        tmp_path, [sys.executable, "-c", "print('ok')"], auto_review=True,
    )
    attention = AttentionStore(tasks)
    task = _planned_task(tasks)
    worker = _settled_worker_run(dispatcher, store, task.task_id)
    with pytest.raises(RunGateError) as before:
        dispatcher.queue(_request(task.task_id, version=3))
    assert before.value.code == GATE_CODE_REVIEW_OWED

    # Restart.
    assert dispatcher.reconcile_reviews_owed() == [
        {"task_id": task.task_id, "run_id": worker.run_id,
         "cause": REVIEW_OWED_NO_REVIEW_RUN}
    ]
    items = attention.list(task_id=task.task_id, state=AttentionState.OPEN)
    assert len(items) == 1
    blocker = items[0]
    assert blocker.kind == AttentionKind.BLOCKER
    assert blocker.requester == REVIEW_REQUESTER
    assert blocker.run_id == worker.run_id  # scoped to the run whose review is missing
    assert blocker.title == dispatcher.REVIEW_FAILED_TITLE
    assert dispatcher.REVIEW_OWED_AFTER_RESTART_REASON in blocker.body

    # The refusal is now the blocker, which names itself and how to clear it.
    with pytest.raises(RunGateError, match="open gate item") as after:
        dispatcher.queue(_request(task.task_id, version=3))
    assert after.value.code == GATE_CODE_ATTENTION_OPEN
    assert after.value.items[0]["item_id"] == blocker.item_id

    # A second restart records nothing more: the recorded blocker is what the
    # predicate reads, so the two cannot pile up.
    assert dispatcher.reconcile_reviews_owed() == []
    assert [item.item_id for item in attention.list(
        task_id=task.task_id, state=AttentionState.OPEN)] == [blocker.item_id]

    # Cancelling it releases BOTH gates — the blocker because it is no longer open,
    # and the derived refusal because the review path has now been accounted for.
    attention.cancel(blocker.item_id, CancelAttentionRequest(expected_version=blocker.version))
    assert dispatcher.queue(_request(task.task_id, version=3)).state == RunState.QUEUED


def test_review_owed_text_claims_only_what_holds_for_every_cause(tmp_path):
    """The strings, checked as claims. This is the check rounds O3/P1-P4 lacked.

    Every earlier round of this slice shipped a human-facing sentence that was false,
    and no test caught one, because the tests assert that a constant *appears* in an
    item's body and never what the sentence says. Three properties are pinned here,
    each of them a defect that actually shipped:

    1. **No exit nobody has.** ``ROLE_REVIEW`` is a server-side argument to
       ``ExecutionStore.create`` and ``_dispatch_review`` is its only production
       caller, so no string may tell a human to dispatch a review.
    2. **Nothing attached to every cause may claim what only some causes support.**
       The title and the generic body are shared by all five causes, and for
       ``REVIEW_OWED_NO_GATE_ITEM`` the review started, ran and reached a conclusion
       whose gate write failed. So "the cross-review failed to start" and "no review
       has seen this work" are both out; what holds everywhere is that no review
       decision is on record.
    3. **A stated limit comes from the constant that enforces it.** The body's
       description of what a later review can cover is built from
       ``_REVIEW_FILE_CHARS``/``_REVIEW_PROMPT_BUDGET``/``MAX_FILES``, so editing a
       bound edits the sentence.
    """
    from agora.execution.dispatcher import (
        _REVIEW_FILE_CHARS,
        _REVIEW_PROMPT_BUDGET,
    )
    from agora.execution.deliverables import MAX_FILES as _MAX_FILES

    tasks, store, dispatcher = _system(
        tmp_path, [sys.executable, "-c", "print('ok')"], auto_review=True,
    )
    attention = AttentionStore(tasks)
    task = _planned_task(tasks)
    worker = _settled_worker_run(dispatcher, store, task.task_id)
    # The cause where a review *did* run: the title and the generic body are shared
    # with it, so it is the one that decides what they may say.
    review = dispatcher.queue(
        CreateRunRequest(task_id=task.task_id, adapter="codex", prompt="review it",
                         timeout_seconds=10, expected_task_version=3),
        role=ROLE_REVIEW, reviews_run_id=worker.run_id,
    )
    running = store.start(review.run_id, expected_version=review.version)
    store.finish(review.run_id, RunState.SUCCEEDED, expected_version=running.version,
                 exit_code=0, stdout_tail="one finding", stderr_tail="")
    assert store.reviews_owed(task_id=task.task_id)[0]["cause"] == REVIEW_OWED_NO_GATE_ITEM
    assert dispatcher.reconcile_reviews_owed() != []
    blocker = attention.list(task_id=task.task_id, state=AttentionState.OPEN)[0]

    reasons = list(dispatcher.REVIEW_OWED_REASONS.values())

    # (1) No sentence anywhere offers dispatching a review. The one place the phrase
    # may appear is where the body says it is *not* an exit.
    for text in [blocker.title, *reasons]:
        assert "派发一次评审" not in text and "派发评审" not in text
    assert blocker.body.count("再派发一次评审") == 1
    assert "因此“再派发一次评审”不是一个出口" in blocker.body

    # (2) Neither the shared title nor the shared body claims the review never
    # started or that nobody looked at the work.
    assert "未能启动" not in blocker.title and "未能启动" not in blocker.body
    assert "没有任何评审看过" not in blocker.body
    assert "没有任何独立交叉评审的结论被记录下来" in blocker.body
    # ...and the cause-specific reason is where that review's own fate is stated.
    assert dispatcher.REVIEW_OWED_WITHOUT_GATE_REASON in blocker.body

    # (3) The stated coverage limit is the bound the code enforces.
    for bound in (_REVIEW_FILE_CHARS, _REVIEW_PROMPT_BUDGET, _MAX_FILES):
        assert str(bound) in blocker.body
    assert "有上限的采样" in blocker.body


def test_a_failed_review_dispatch_does_not_double_gate_with_the_derived_refusal(tmp_path):
    """The two guards must not add up to a task with no exit.

    ``_block_review_owed`` records a blocker whose body promises that cancelling it
    lets the task accept another implementation run. If the derived refusal also
    fired, cancelling would clear one gate and leave the other standing with nothing
    left to act on — the shape of every lockout this slice has produced. So a run
    the review path has already accounted for is not owed a review, whatever state
    that item is in.
    """
    tasks, store, dispatcher = _system(
        tmp_path, [sys.executable, "-c", "print('ok')"], auto_review=True,
    )
    attention = AttentionStore(tasks)
    task = _planned_task(tasks)
    worker = dispatcher.queue(_request(task.task_id))
    # claude is alpha's reviewer and is disabled in this registry, so the real
    # dispatch failure path runs: no review run, one durable blocker.
    finished = asyncio.run(dispatcher.execute(worker.run_id))
    assert finished.state == RunState.SUCCEEDED
    blocker = attention.list(task_id=task.task_id, state=AttentionState.OPEN)[0]

    with pytest.raises(RunGateError) as gated:
        dispatcher.queue(_request(task.task_id, version=3))
    assert gated.value.code == GATE_CODE_ATTENTION_OPEN  # the blocker, not the predicate

    attention.cancel(blocker.item_id, CancelAttentionRequest(expected_version=blocker.version))
    assert dispatcher.queue(_request(task.task_id, version=3)).state == RunState.QUEUED


# --- K1: a run's identity is not a result, and survives every terminal path ---


def _identity_of(store, run_id: str) -> dict:
    """The three server-set identity keys a stored run carries, as stored."""
    metadata = store.require(run_id).result_metadata
    return {
        key: metadata[key] for key in ("agora_role", "reviews_run_id", "task_version")
        if key in metadata
    }


def test_a_review_that_fails_before_it_starts_still_owes_a_review(tmp_path):
    """The scenario that defeated the whole review gate through one cleared field.

    A QUEUED review run can end before it ever starts: the adapter is no longer
    configured, the workspace is gone, the pre-launch checks refuse it. Every one of
    those paths in ``ExecutionDispatcher.execute`` settles the run with ``finish``
    and no result at all — and ``finish`` used to write ``result_metadata or {}``,
    which replaced the identity ``create`` had written with ``{}``. The failed review
    then had no ``agora_role``, so ``_reviews_owed``'s
    ``COALESCE(json_extract(..., '$.agora_role'), 'worker')`` read it as the task's
    most recent *worker* run — state ``failed``, therefore nothing owed — and the
    next worker run walked past the review gate onto unreviewed work.

    Two things are asserted, because the fix needed both. The identity survives the
    settle (K1), *and* a review run that ended without reviewing anything does not
    discharge the obligation by existing (``_has_accounting_review_run``): with the
    identity restored but the old "a row in any state counts" rule, the preserved
    link would have been read as "a review exists" and the gate would still have
    stood open.
    """
    tasks, store, dispatcher = _system(
        tmp_path, [sys.executable, "-c", "print('ok')"], auto_review=True,
    )
    attention = AttentionStore(tasks)
    task = _planned_task(tasks)
    worker = _settled_worker_run(dispatcher, store, task.task_id)
    review = dispatcher.queue(
        CreateRunRequest(task_id=task.task_id, adapter="codex", prompt="review it",
                         timeout_seconds=10, expected_task_version=3),
        role=ROLE_REVIEW, reviews_run_id=worker.run_id,
    )
    assert store.reviews_owed() == []  # the queued review accounts for it

    # The pre-launch failure, exactly as execute() records it: still QUEUED, no
    # process, no result, no deliverables.
    failed = store.finish(review.run_id, RunState.FAILED, expected_version=review.version,
                          exit_code=None, stdout_tail="", stderr_tail="",
                          error_message="workspace not found")
    assert failed.state == RunState.FAILED
    # The identity is still there: this is a review run, and it is still linked to
    # the worker run it was created to review.
    assert failed.result_metadata[ROLE_KEY] == ROLE_REVIEW
    assert failed.result_metadata[REVIEWS_RUN_KEY] == worker.run_id
    assert failed.result_metadata["task_version"] == 3

    # So a review is still owed for the worker run — not for the failed review,
    # which is not a worker run and never was. A review run *is* linked to it, and
    # left no gate item, which is the cause the reconciliation reports.
    assert store.reviews_owed() == [
        {"task_id": task.task_id, "run_id": worker.run_id,
         "cause": REVIEW_OWED_NO_GATE_ITEM}
    ]

    # And the next worker run is refused, in the create transaction.
    with pytest.raises(RunGateError, match="owes a review") as owed:
        dispatcher.queue(_request(task.task_id, version=3))
    assert owed.value.code == GATE_CODE_REVIEW_OWED
    assert owed.value.items == [{"run_id": worker.run_id, "state": "succeeded"}]

    # Not a one-way door: the startup reconciliation turns it into the blocker that
    # says what happened, the predicate stands down for that run so the two never
    # gate at once, and cancelling the blocker releases everything.
    assert dispatcher.reconcile_reviews_owed() == [
        {"task_id": task.task_id, "run_id": worker.run_id,
         "cause": REVIEW_OWED_NO_GATE_ITEM}
    ]
    blocker = attention.list(task_id=task.task_id, state=AttentionState.OPEN)[0]
    assert blocker.kind == AttentionKind.BLOCKER and blocker.run_id == worker.run_id
    # The reason names the arrangement actually found — a review run that ended and
    # recorded nothing — rather than the crash window, which is a different fact.
    assert dispatcher.REVIEW_OWED_WITHOUT_GATE_REASON in blocker.body
    assert store.reviews_owed() == []
    with pytest.raises(RunGateError) as after:
        dispatcher.queue(_request(task.task_id, version=3))
    assert after.value.code == GATE_CODE_ATTENTION_OPEN
    attention.cancel(blocker.item_id, CancelAttentionRequest(expected_version=blocker.version))
    assert dispatcher.queue(_request(task.task_id, version=3)).state == RunState.QUEUED


def test_a_shutdown_cancelled_review_still_owes_a_review(tmp_path):
    """N1 (2026-08-30): a cancelled review used to satisfy the obligation by itself.

    ``_has_accounting_review_run`` counted ``cancelled`` alongside the unsettled
    states, on the reasoning that cancelling a review is a human's decision the
    control plane does not override — and that telling a human's cancel from a
    sweep's would mean parsing a recorded reason string. The premise assumed a human.
    ``ExecutionDispatcher.shutdown`` cancels every scheduled run through
    ``_settle_unstarted``, so an ordinary restart in the window between a worker run
    succeeding and its review starting cancelled that review automatically, with no
    approval and no blocker written anywhere. The next start then read
    ``reviews_owed() == []`` and admitted a new worker run: no review had run, no gate
    item existed, and nothing said so.

    Reproduced here end to end — succeed a worker, dispatch the auto-review, shut the
    dispatcher down before it starts, then ask the questions a restart asks. And the
    exit does not need a reason string: what a cancelled review has in common with a
    failed one is that it produced no gate item, which this database records directly.
    """
    tasks, store, dispatcher = _system(
        tmp_path, [sys.executable, "-c", "print('ok')"], auto_review=True,
    )
    attention = AttentionStore(tasks)
    task = _planned_task(tasks)
    worker = _settled_worker_run(dispatcher, store, task.task_id)
    review = dispatcher.queue(
        CreateRunRequest(task_id=task.task_id, adapter="codex", prompt="review it",
                         timeout_seconds=10, expected_task_version=3),
        role=ROLE_REVIEW, reviews_run_id=worker.run_id,
    )
    assert store.reviews_owed() == []  # the queued review accounts for it

    # Shutdown, with the review still QUEUED. This is _settle_unstarted's sweep, not
    # a human: no approval, no blocker, nobody asked anything.
    async def stop():
        dispatcher.schedule(review.run_id)
        await dispatcher.shutdown()

    asyncio.run(stop())
    assert store.require(review.run_id).state == RunState.CANCELLED

    # The reported symptom was that a restart here saw ``reviews_owed() == []`` **and**
    # no gate item anywhere, and admitted a new worker run. Both halves are now false.
    # The sweep recorded the gate item at the moment it cancelled, so the human hears
    # about it now rather than at the next start — and the blocker says the review was
    # stopped rather than that its gate writes failed, which is a different fact
    # pointing at a different thing to do.
    blocker = attention.list(task_id=task.task_id, state=AttentionState.OPEN)[0]
    assert blocker.kind == AttentionKind.BLOCKER and blocker.run_id == worker.run_id
    assert dispatcher.REVIEW_OWED_CANCELLED_REASON in blocker.body

    # With that item recorded the derived predicate stands down, so the two never gate
    # at once: what refuses the next worker run is the blocker, with its two exits.
    # (``reviews_owed`` reporting the cancelled cause is pinned by the next test,
    # which cancels through the store alone — the shape a hard kill leaves.)
    assert store.reviews_owed() == []
    with pytest.raises(RunGateError) as gated:
        dispatcher.queue(_request(task.task_id, version=3))
    assert gated.value.code == GATE_CODE_ATTENTION_OPEN
    assert store.list(task_id=task.task_id, state=RunState.QUEUED) == []
    attention.cancel(blocker.item_id, CancelAttentionRequest(expected_version=blocker.version))
    assert dispatcher.queue(_request(task.task_id, version=3)).state == RunState.QUEUED


def test_the_startup_reconciliation_notices_a_cancelled_review(tmp_path):
    """The other half of N1: the shape has to survive a hard kill too.

    ``_settle_unstarted`` writes the blocker for the cancellation it performs, but a
    process that is killed outright — or one whose write fails — leaves the cancelled
    review with no gate item and nothing pending. That arrangement cannot resolve
    itself: no later event writes anything, and every reading of the run table says a
    review exists. So ``reconcile_reviews_owed`` has to see it at the next start, and
    it has to say which arrangement it found: ``REVIEW_OWED_REVIEW_CANCELLED``, not
    the "both gate writes failed" sentence, which describes something else.
    """
    tasks, store, dispatcher = _system(
        tmp_path, [sys.executable, "-c", "print('ok')"], auto_review=True,
    )
    attention = AttentionStore(tasks)
    task = _planned_task(tasks)
    worker = _settled_worker_run(dispatcher, store, task.task_id)
    review = dispatcher.queue(
        CreateRunRequest(task_id=task.task_id, adapter="codex", prompt="review it",
                         timeout_seconds=10, expected_task_version=3),
        role=ROLE_REVIEW, reviews_run_id=worker.run_id,
    )
    # Cancelled through the store alone: the dispatcher never got to record anything,
    # which is what a kill -9 in that moment leaves behind.
    store.cancel(review.run_id, CancelRunRequest(expected_version=review.version))
    assert attention.list(task_id=task.task_id) == []

    recorded = dispatcher.reconcile_reviews_owed()

    assert recorded == [
        {"task_id": task.task_id, "run_id": worker.run_id,
         "cause": REVIEW_OWED_REVIEW_CANCELLED}
    ]
    blocker = attention.list(task_id=task.task_id, state=AttentionState.OPEN)[0]
    assert blocker.run_id == worker.run_id
    assert dispatcher.REVIEW_OWED_CANCELLED_REASON in blocker.body
    assert dispatcher.REVIEW_OWED_WITHOUT_GATE_REASON not in blocker.body
    # Idempotent with the live path: recording it twice leaves one item.
    assert dispatcher.reconcile_reviews_owed() == []
    assert len(attention.list(task_id=task.task_id)) == 1


def test_a_human_cancelling_a_review_gets_the_blocker_that_releases_it(tmp_path):
    """A human's cancel is still honoured — by being *recorded*, not by vanishing.

    N1 removed ``cancelled`` from the states that account for the review, and the
    release for the obligation is a gate item, which cancelling a run writes none of.
    Left there, a human who cancelled a review inside a live process would have a
    task refusing worker runs with ``review_owed`` and nothing in their inbox to act
    on until the next restart's reconciliation — a worse door than the one N1 closed.
    So ``ExecutionDispatcher.cancel`` records the blocker at the moment of the cancel,
    and the human has the two exits that exist immediately: clear the item and accept
    the work with no review decision on record, or clear it and dispatch another
    *worker* run, whose own automatic cross-review follows. No endpoint dispatches a
    review run on its own, so "dispatch another review" is not one of them (O3/P3).
    """
    tasks, store, dispatcher = _system(
        tmp_path, [sys.executable, "-c", "print('ok')"], auto_review=True,
    )
    attention = AttentionStore(tasks)
    task = _planned_task(tasks)
    worker = _settled_worker_run(dispatcher, store, task.task_id)
    review = dispatcher.queue(
        CreateRunRequest(task_id=task.task_id, adapter="codex", prompt="review it",
                         timeout_seconds=10, expected_task_version=3),
        role=ROLE_REVIEW, reviews_run_id=worker.run_id,
    )

    cancelled = asyncio.run(dispatcher.cancel(
        review.run_id,
        CancelRunRequest(expected_version=review.version, reason="I read it myself"),
    ))

    assert cancelled.state == RunState.CANCELLED
    blocker = attention.list(task_id=task.task_id, state=AttentionState.OPEN)[0]
    assert blocker.kind == AttentionKind.BLOCKER and blocker.run_id == worker.run_id
    assert dispatcher.REVIEW_OWED_CANCELLED_REASON in blocker.body
    # One item, one exit, no restart needed.
    assert store.reviews_owed() == []
    attention.cancel(blocker.item_id, CancelAttentionRequest(
        expected_version=blocker.version, reason="taking it unreviewed",
    ))
    assert dispatcher.queue(_request(task.task_id, version=3)).state == RunState.QUEUED


def test_cancelling_a_worker_run_raises_no_review_blocker(tmp_path):
    """The scope of the cancel-time write: review runs only.

    A cancelled *worker* run owes nothing — the obligation belongs to a worker run
    that succeeded — so raising a blocker when one is cancelled would put an item in
    front of a human at a moment that has nothing to do with it, and would gate the
    retry of the very run they just stopped.
    """
    tasks, store, dispatcher = _system(
        tmp_path, [sys.executable, "-c", "print('ok')"], auto_review=True,
    )
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))

    cancelled = asyncio.run(dispatcher.cancel(
        run.run_id, CancelRunRequest(expected_version=run.version, reason="wrong prompt"),
    ))

    assert cancelled.state == RunState.CANCELLED
    assert AttentionStore(tasks).list(task_id=task.task_id) == []
    assert store.reviews_owed() == []
    assert dispatcher.queue(_request(task.task_id, version=3)).state == RunState.QUEUED


def test_terminal_worker_runs_that_cannot_be_ordered_fail_closed(tmp_path):
    """N3 (2026-08-30): an unknown ``finished_at`` was manufactured into an ordering.

    ``_reviews_owed`` ranked a task's terminal worker runs with
    ``ORDER BY COALESCE(finished_at, '')``, which does not mean "unknown" — it means
    "older than every real timestamp". So an older **failed** worker run with a real
    timestamp outranked a newer **succeeded** one whose timestamp was missing, became
    the task's "latest terminal worker", and the not-succeeded branch concluded no
    review was owed. Another worker run was then admitted on unreviewed work, out of
    a sort order chosen because it happened to look safe.

    Being unable to order the runs now fails closed instead: a row with no usable
    ``finished_at`` has no rank at all, and when one exists this method stops claiming
    to know which run is latest and nominates the newest succeeded run it can still
    *identify* — the last one in ``rowid`` order — rather than letting a row in another
    state displace it. Exactly one run is nominated either way; the lifetime bound on
    that nomination is ``test_unorderable_terminal_runs_owe_one_review_for_the_task_
    lifetime`` (O1).
    """
    tasks, store, dispatcher = _system(
        tmp_path, [sys.executable, "-c", "print('ok')"], auto_review=True,
    )
    task = _planned_task(tasks)
    # The succeeded run comes first and is the one that owes a review...
    succeeded = _settled_worker_run(dispatcher, store, task.task_id)
    # ...and a later failed worker run keeps a real timestamp. Retries are allowed
    # once the predecessor settled, so this needs the gate item out of the way; the
    # arrangement being tested is the *ordering*, so insert the row directly.
    failed = _insert_queued_run(tasks, task.task_id, role="worker")
    with tasks._transaction() as db:
        db.execute(
            "UPDATE execution_runs SET state = 'failed', finished_at = ? WHERE run_id = ?",
            ("2099-01-01T00:00:00+00:00", failed),
        )
        # The succeeded run's timestamp is gone: an older Agora, or a hand edit.
        db.execute(
            "UPDATE execution_runs SET finished_at = NULL WHERE run_id = ?",
            (succeeded.run_id,),
        )

    # The failed row is the only orderable one, and under the old rule it was the
    # "latest terminal worker", so nothing was owed.
    assert store.reviews_owed() == [
        {"task_id": task.task_id, "run_id": succeeded.run_id,
         "cause": REVIEW_OWED_NO_REVIEW_RUN}
    ]
    with pytest.raises(RunGateError, match="owes a review") as gated:
        dispatcher.queue(_request(task.task_id, version=3))
    assert gated.value.code == GATE_CODE_REVIEW_OWED
    assert gated.value.items == [{"run_id": succeeded.run_id, "state": "succeeded"}]

    # A readable chronology still means exactly one run is examined: give the
    # succeeded run a timestamp *older* than the failed one and nothing is owed,
    # because the latest terminal worker run really did not succeed.
    with tasks._transaction() as db:
        db.execute(
            "UPDATE execution_runs SET finished_at = ? WHERE run_id = ?",
            ("2000-01-01T00:00:00+00:00", succeeded.run_id),
        )
    assert store.reviews_owed() == []
    assert dispatcher.queue(_request(task.task_id, version=3)).state == RunState.QUEUED


def test_unorderable_terminal_runs_owe_one_review_for_the_task_lifetime(tmp_path):
    """O1 (round 5, 2026-08-30): N3's fix bounded one call, not the task.

    The first version of the unusable-chronology branch made **every** succeeded
    terminal worker run a candidate and returned the first one that was still owed,
    with a ``break`` after it. That bounds a single invocation and nothing longer,
    which is not the bound the ADR claimed. Reproduced across successive starts:

        INITIAL [run A] / RECONCILE_1 [run A] / AFTER_1 [run B] /
        RECONCILE_2 [run B] / AFTER_2 []

    Once the reconciliation had recorded the blocker for A, the next start skipped A —
    it now had a gate item — and walked back to B, so N historical successes produced
    N blockers over N starts with several open at once. It also falsified "one cancel
    clears it": cancelling A's item released the obligation for A only, and the next
    worker attempt was refused for B, which had no item yet.

    The nominee is now chosen from the rows *before* any release question is asked, so
    it does not move: the newest succeeded row that can still be identified, and no
    fallback to an older one. One item, one cancel, for as long as no new terminal
    worker row is recorded.
    """
    tasks, store, dispatcher = _system(
        tmp_path, [sys.executable, "-c", "print('ok')"], auto_review=True,
    )
    attention = AttentionStore(tasks)
    task = _planned_task(tasks)
    # Two succeeded worker runs, and no usable chronology: the newer row has no
    # ``finished_at`` at all (an older Agora, or a hand edit), which is the branch.
    older = _settled_worker_run(dispatcher, store, task.task_id)
    newer = _insert_queued_run(tasks, task.task_id, role="worker")
    with tasks._transaction() as db:
        db.execute("UPDATE execution_runs SET state = 'succeeded' WHERE run_id = ?", (newer,))

    assert store.reviews_owed() == [
        {"task_id": task.task_id, "run_id": newer, "cause": REVIEW_OWED_NO_REVIEW_RUN}
    ]
    assert [owed["run_id"] for owed in dispatcher.reconcile_reviews_owed()] == [newer]
    # The second start records nothing: under the old rule it walked back to ``older``
    # and raised a second blocker for the same arrangement.
    assert dispatcher.reconcile_reviews_owed() == []
    assert store.reviews_owed() == []
    blockers = attention.list(task_id=task.task_id, state=AttentionState.OPEN)
    assert [item.run_id for item in blockers] == [newer]
    assert older.run_id != newer

    # One cancel is the whole exit, which is what the ADR now claims and what the
    # walk-back made false.
    only = blockers[0]
    attention.cancel(only.item_id, CancelAttentionRequest(
        expected_version=only.version, reason="taking it unreviewed",
    ))
    assert dispatcher.queue(_request(task.task_id, version=3)).state == RunState.QUEUED


def test_a_failed_review_that_raised_its_blocker_does_not_gate_twice(tmp_path):
    """The other side of the narrowed rule: a review that failed *after* launch.

    ``_after_run`` raises the "review did not complete" blocker for it, scoped to the
    worker run, and ``_has_review_gate_item`` stands the derived predicate down —
    so the human sees one gate with one exit, exactly as before this change. The
    narrowing only bites where nothing was recorded at all.
    """
    tasks, store, dispatcher = _system(
        tmp_path, [sys.executable, "-c", "print('ok')"], auto_review=True,
    )
    attention = AttentionStore(tasks)
    task = _planned_task(tasks)
    worker = _settled_worker_run(dispatcher, store, task.task_id)
    review = dispatcher.queue(
        CreateRunRequest(task_id=task.task_id, adapter="codex", prompt="review it",
                         timeout_seconds=10, expected_task_version=3),
        role=ROLE_REVIEW, reviews_run_id=worker.run_id,
    )
    running = store.start(review.run_id, expected_version=review.version)
    failed = store.finish(review.run_id, RunState.FAILED, expected_version=running.version,
                          exit_code=1, stdout_tail="", stderr_tail="reviewer crashed",
                          error_message="process exited with a non-zero status")
    asyncio.run(dispatcher._after_run(failed, ROLE_REVIEW, worker.run_id))

    blocker = attention.list(task_id=task.task_id, state=AttentionState.OPEN)[0]
    assert blocker.kind == AttentionKind.BLOCKER and blocker.run_id == worker.run_id
    assert store.reviews_owed() == []  # the blocker accounts for it, so not both
    with pytest.raises(RunGateError) as gated_again:
        dispatcher.queue(_request(task.task_id, version=3))
    assert gated_again.value.code == GATE_CODE_ATTENTION_OPEN
    attention.cancel(blocker.item_id, CancelAttentionRequest(expected_version=blocker.version))
    assert dispatcher.queue(_request(task.task_id, version=3)).state == RunState.QUEUED


# --- L2: a review run is evidence; the human gate is a separate fact ----------


def test_a_succeeded_review_that_recorded_no_gate_item_still_owes_a_review(tmp_path, monkeypatch):
    """A review that ran and left nothing to decide is not a review that happened.

    ``_after_run`` writes the approval when a review run succeeds, and
    ``_record_review_failure`` writes a blocker if that fails. Both writes can fail —
    the Attention store is a different transaction on the same database, and the
    fallback is reached precisely when something is already wrong. The accounting
    then had a SUCCEEDED review run, no approval and no blocker: it concluded the
    review was satisfied *from the run's state alone*, nothing was pending for a
    human anywhere, and the next worker run built on work nobody had been asked to
    accept. Evidence (a run happened) is not the gate (a decision is pending), and
    the Run -> Artifact/Evidence -> Gate line depends on the two staying apart.

    So ``succeeded`` no longer accounts for the obligation by itself; the gate item
    the outcome raises does. A review run that produced no gate item is worse than no
    review at all, because it looks finished — and after a restart the run table is
    the only thing left saying so, which is why the startup reconciliation has to
    cover this case too.
    """
    tasks, store, dispatcher = _system(
        tmp_path, [sys.executable, "-c", "print('ok')"], auto_review=True,
    )
    attention = AttentionStore(tasks)
    task = _planned_task(tasks)
    worker = _settled_worker_run(dispatcher, store, task.task_id)
    review = dispatcher.queue(
        CreateRunRequest(task_id=task.task_id, adapter="codex", prompt="review it",
                         timeout_seconds=10, expected_task_version=3),
        role=ROLE_REVIEW, reviews_run_id=worker.run_id,
    )
    running = store.start(review.run_id, expected_version=review.version)
    reviewed = store.finish(review.run_id, RunState.SUCCEEDED, expected_version=running.version,
                            exit_code=0, stdout_tail="two findings", stderr_tail="")

    # Both gate writes fail: the approval, and then the fallback blocker. This is the
    # real orchestration path, not a hand-built database — ``_after_run`` swallows
    # the first failure into ``_record_review_failure``, which swallows the second.
    def _unwritable(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(AttentionStore, "create", _unwritable)
    monkeypatch.setattr(AttentionStore, "create_once", _unwritable)
    asyncio.run(dispatcher._after_run(reviewed, ROLE_REVIEW, worker.run_id))
    monkeypatch.undo()

    # The run row says the review succeeded. Nothing anywhere asks a human anything.
    assert store.require(review.run_id).state == RunState.SUCCEEDED
    assert attention.list(task_id=task.task_id) == []

    # The review is therefore owed again, and the next worker run is refused.
    assert store.reviews_owed() == [
        {"task_id": task.task_id, "run_id": worker.run_id,
         "cause": REVIEW_OWED_NO_GATE_ITEM}
    ]
    with pytest.raises(RunGateError, match="owes a review") as owed_again:
        dispatcher.queue(_request(task.task_id, version=3))
    assert owed_again.value.code == GATE_CODE_REVIEW_OWED

    # Restart: the only thing that can notice this arrangement records the blocker,
    # with the reason that describes *this* cause rather than the crash window.
    assert dispatcher.reconcile_reviews_owed() == [
        {"task_id": task.task_id, "run_id": worker.run_id,
         "cause": REVIEW_OWED_NO_GATE_ITEM}
    ]
    raised = attention.list(task_id=task.task_id, state=AttentionState.OPEN)[0]
    assert raised.kind == AttentionKind.BLOCKER and raised.run_id == worker.run_id
    assert dispatcher.REVIEW_OWED_WITHOUT_GATE_REASON in raised.body
    assert dispatcher.REVIEW_OWED_AFTER_RESTART_REASON not in raised.body

    # And it is the one exit, not one of two: the predicate stands down for that run.
    assert store.reviews_owed() == []
    attention.cancel(raised.item_id, CancelAttentionRequest(expected_version=raised.version))
    assert dispatcher.queue(_request(task.task_id, version=3)).state == RunState.QUEUED


def test_an_incomplete_reconciliation_does_not_resume_a_worker_onto_unreviewed_work(
    tmp_path, monkeypatch
):
    """``start_recovery`` continued into the resume sweep after a reconciliation that
    did not finish (M1).

    The order was right and that was not enough. ``reconcile_reviews_owed`` swallows
    its own failures by design — the ``reviews_owed`` read can fail outright, and a
    per-task blocker write that fails is skipped so one unrecordable task does not
    cost the rest theirs — and ``start_recovery`` then called ``resume_queued``
    anyway. The sweep judged the queue against blockers that were never written, and
    ``revalidate_queued`` did not compute ``review_owed`` at all, so a persisted
    QUEUED worker was scheduled onto work that succeeded and was never reviewed.

    **Chosen fix: the resume path derives the refusal itself**, rather than refusing
    to resume when a bookkeeping flag says the reconciliation was incomplete. It asks
    the hazard instead of a proxy for it, so it needs no knowledge of *which* tasks a
    failed reconciliation would have covered, and every unaffected queued run still
    resumes. In the ordinary path it changes nothing: the blocker is a gate item, and
    once that item exists the derived question stands down
    (``_has_review_gate_item``), so the gate answers first — case 3 asserts that the
    two answers stay distinguishable.

    **What the operator sees**: the queued run is settled CANCELLED with the reason
    on its own row and on the task's timeline, it is named in
    ``start_recovery``'s ``queued_runs_refused``, and the reconciliation's own
    incompleteness is reported per task in ``reviews_owed_unrecorded`` and printed by
    ``lifespan``. "The queue just did not resume" is not one of the outcomes.
    """
    command = [sys.executable, "-c", "print('ok')"]

    # 1. The blocker write fails. The run is still refused, and the failure is named.
    tasks, store, dispatcher = _system(tmp_path / "unwritable", command, auto_review=True)
    task = _planned_task(tasks)
    worker = _settled_worker_run(dispatcher, store, task.task_id)
    queued = _insert_queued_run(tasks, task.task_id, role="worker")
    scheduled: list[str] = []
    dispatcher.schedule = lambda run_id: scheduled.append(run_id)  # type: ignore[method-assign]

    def _unwritable(**kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(dispatcher, "_block_review_owed", _unwritable)
    receipt = dispatcher.start_recovery()

    assert receipt["reviews_owed_reconciled"] == []
    assert AttentionStore(tasks).list(task_id=task.task_id) == []  # no gate was raised
    assert [
        (entry["run_id"], entry["cause"]) for entry in receipt["reviews_owed_unrecorded"]
    ] == [(worker.run_id, REVIEW_OWED_NO_REVIEW_RUN)]
    assert "OperationalError" in receipt["reviews_owed_unrecorded"][0]["error"]
    # The defect: this used to be scheduled.
    assert scheduled == []
    assert [entry["run_id"] for entry in receipt["queued_runs_refused"]] == [queued]
    assert "owes a review" in receipt["queued_runs_refused"][0]["reason"]
    refused_run = store.require(queued)
    assert refused_run.state == RunState.CANCELLED
    assert worker.run_id in refused_run.error_message
    assert "run.cancelled" in [event.event_type for event in tasks.events(task.task_id)]

    # 2. The other half of an incomplete reconciliation: the read itself fails, so
    #    nothing is even known to be owed. The refusal is derived inside the sweep's
    #    own transaction, so it does not depend on that read having succeeded.
    tasks, store, dispatcher = _system(tmp_path / "unreadable", command, auto_review=True)
    task = _planned_task(tasks)
    worker = _settled_worker_run(dispatcher, store, task.task_id)
    queued = _insert_queued_run(tasks, task.task_id, role="worker")
    scheduled = []
    dispatcher.schedule = lambda run_id: scheduled.append(run_id)  # type: ignore[method-assign]

    def _unreadable():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "reviews_owed", _unreadable)
    receipt = dispatcher.start_recovery()

    assert "OperationalError" in receipt["reviews_owed_unreadable"]
    assert scheduled == []
    assert store.require(queued).state == RunState.CANCELLED

    # 3. The reconciliation succeeding is unchanged, and the two answers stay
    #    distinguishable: the run is refused by the *blocker* the reconciliation
    #    raised, named by item id, not by the derived question — which stands down
    #    once that item exists so a human has one gate with one exit.
    tasks, store, dispatcher = _system(tmp_path / "recorded", command, auto_review=True)
    task = _planned_task(tasks)
    worker = _settled_worker_run(dispatcher, store, task.task_id)
    queued = _insert_queued_run(tasks, task.task_id, role="worker")
    scheduled = []
    dispatcher.schedule = lambda run_id: scheduled.append(run_id)  # type: ignore[method-assign]

    receipt = dispatcher.start_recovery()

    assert [entry["run_id"] for entry in receipt["reviews_owed_reconciled"]] == [worker.run_id]
    assert receipt["reviews_owed_unrecorded"] == []
    blocker = AttentionStore(tasks).list(task_id=task.task_id, state=AttentionState.OPEN)[0]
    assert scheduled == []
    reason = receipt["queued_runs_refused"][0]["reason"]
    assert blocker.item_id in reason and "owes a review" not in reason

    # 4. With auto_review off nothing can be owed, so the question is not asked at
    #    all and a persisted queued worker on a succeeded run still resumes.
    tasks, store, dispatcher = _system(tmp_path / "off", command)
    task = _planned_task(tasks)
    _settled_worker_run(dispatcher, store, task.task_id)
    queued = _insert_queued_run(tasks, task.task_id, role="worker")
    scheduled = []
    dispatcher.schedule = lambda run_id: scheduled.append(run_id)  # type: ignore[method-assign]

    assert dispatcher.resume_queued() == []
    assert scheduled == [queued]


def test_a_cause_with_no_recorded_reason_is_skipped_rather_than_mislabelled(tmp_path):
    """The reconciliation has one true sentence per cause, and no default.

    A cause nobody has decided what to say about would otherwise be recorded with
    whichever reason happened to be the fallback — a blocker describing something
    that did not happen, which is the class of defect L4 was. Skipping leaves the
    derived refusal standing, which is the visible failure this path exists to
    produce.
    """
    tasks, store, dispatcher = _system(
        tmp_path, [sys.executable, "-c", "print('ok')"], auto_review=True,
    )
    task = _planned_task(tasks)
    worker = _settled_worker_run(dispatcher, store, task.task_id)
    store.reviews_owed = lambda: [  # type: ignore[method-assign]
        {"task_id": task.task_id, "run_id": worker.run_id, "cause": "something_new"}
    ]

    assert dispatcher.reconcile_reviews_owed() == []
    assert AttentionStore(tasks).list(task_id=task.task_id) == []
    assert set(dispatcher.REVIEW_OWED_REASONS) == {
        REVIEW_OWED_NO_REVIEW_RUN, REVIEW_OWED_NO_GATE_ITEM, REVIEW_OWED_REVIEW_CANCELLED
    }


# --- L1: the control plane's requester is not a field a client may set --------


def test_the_control_plane_requester_cannot_be_claimed_through_the_public_api(tmp_path):
    """A forged Attention item must not be able to release the review obligation.

    ``requester`` is an authority marker: ``_has_review_gate_item`` reads a
    ``REVIEW_REQUESTER`` blocker or approval scoped to a worker run as *Agora's own*
    review gate, so its existence in any state stands the derived refusal down. But
    ``POST /api/attention`` took ``requester`` from the client, so a caller could
    write that row itself, cancel it, and start a worker run whose predecessor was
    never reviewed and never gated — the third client-settable authority marker in
    this slice, after ``agora_role`` in run metadata.

    The fix has the same shape as that one: the value is refused in public input and
    only Agora's internal paths may write it. Refused, not rewritten — a caller
    sending it is confused or hostile, and quietly substituting a requester would
    hide which.

    Residual, stated because it cannot be closed here: a row **already stored** by a
    build that predates this check is indistinguishable from a genuine one. Every
    column a client could set (``requester``, ``kind``, ``run_id``, ``title``,
    ``body``, ``context``) is one the internal path sets the same way, and the item's
    ``attention.created`` event records that same requester as its actor. So this test
    pins the property the fix actually delivers — no sequence of public API calls can
    produce a releasing row — and not a claim about databases written before it.
    """
    tasks, store, dispatcher = _system(
        tmp_path, [sys.executable, "-c", "print('ok')"], auto_review=True,
    )
    attention = AttentionStore(tasks)
    task = _planned_task(tasks)
    worker = _settled_worker_run(dispatcher, store, task.task_id)
    owed = [{"task_id": task.task_id, "run_id": worker.run_id,
             "cause": REVIEW_OWED_NO_REVIEW_RUN}]
    assert store.reviews_owed() == owed

    forged = CreateAttentionRequest(
        task_id=task.task_id, run_id=worker.run_id, kind=AttentionKind.BLOCKER,
        urgency=AttentionUrgency.HIGH, title="Cross-review did not start",
        requester=REVIEW_REQUESTER,
    )

    # 1. The public API refuses it, and says which value it refused.
    app.dependency_overrides[get_attention_store] = lambda: attention
    try:
        with TestClient(app) as client:
            refused = client.post("/api/attention", json=forged.model_dump(mode="json"))
            assert refused.status_code == 422
            assert "reserved for the control plane" in refused.json()["detail"]
            # An approval is refused for the same reason: settling one advances the
            # task to verified (agora.attention.review_gate).
            approval = client.post("/api/attention", json={
                **forged.model_dump(mode="json"), "kind": "approval",
                "title": "Review finished, approve it",
            })
            assert approval.status_code == 422
    finally:
        app.dependency_overrides.clear()

    # 2. So does the store itself, for any caller that has not said it is internal —
    #    the default is closed, so a new caller cannot forget the argument into a
    #    forgery. A vendor bridge event is never the control plane and has no opt-out
    #    at all.
    with pytest.raises(AttentionValidationError, match="reserved"):
        attention.create(forged)
    with pytest.raises(AttentionValidationError, match="reserved"):
        attention.create_once(forged)
    with pytest.raises(AttentionValidationError, match="reserved"):
        attention.create_bridge_event(BridgeEventRequest(
            vendor=BridgeVendor.CLAUDE, task_id=task.task_id, run_id=worker.run_id,
            vendor_event_id="evt-1", kind=AttentionKind.BLOCKER,
            title="Cross-review did not start", requester=REVIEW_REQUESTER,
            delivery_mode=DeliveryMode.CAPTURE_ONLY,
        ))

    # 3. Nothing was written, so the obligation still stands and the worker run the
    #    forgery was for is still refused.
    assert attention.list(task_id=task.task_id) == []
    assert store.reviews_owed() == owed
    with pytest.raises(RunGateError) as still_gated:
        dispatcher.queue(_request(task.task_id, version=3))
    assert still_gated.value.code == GATE_CODE_REVIEW_OWED

    # 4. The internal path still can, and its item is what releases the obligation —
    #    the marker still means something, which is the point of refusing it above.
    genuine, created = attention.create_once(forged, internal=True)
    assert created is True and genuine.requester == REVIEW_REQUESTER
    assert store.reviews_owed() == []


def test_every_terminal_path_preserves_a_runs_identity(tmp_path):
    """Identity is set by ``create`` and survives every way a run can end.

    One case per terminal path, because the bug was a single path clearing a field
    every other path reads: ``finish`` with a result and without one, ``cancel``,
    ``record_cancelled_output`` (which writes a settled row again), ``abandon``, and
    the queued-cancel settle inside ``revalidate_queued``.
    """
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    dispatcher.schedule = lambda run_id: None  # type: ignore[method-assign]

    def _review(task_id, worker_run_id, *, version=3):
        return dispatcher.queue(
            CreateRunRequest(task_id=task_id, adapter="codex", prompt="review it",
                             timeout_seconds=10, expected_task_version=version),
            role=ROLE_REVIEW, reviews_run_id=worker_run_id,
        )

    # 1. finish() carrying a result: the result is recorded, identity is not touched.
    first = _planned_task(tasks)
    worker_one = dispatcher.queue(_request(first.task_id))
    review_one = _review(first.task_id, worker_one.run_id)
    running = store.start(review_one.run_id, expected_version=review_one.version)
    settled = store.finish(review_one.run_id, RunState.SUCCEEDED,
                           expected_version=running.version, exit_code=0,
                           stdout_tail="no findings", stderr_tail="",
                           result_metadata={"usage": {"total": 12}})
    assert settled.result_metadata["usage"] == {"total": 12}
    assert _identity_of(store, review_one.run_id) == {
        "agora_role": ROLE_REVIEW, "reviews_run_id": worker_one.run_id, "task_version": 3,
    }

    # 2. finish() with no result at all — the pre-launch failure shape.
    second = _planned_task(tasks)
    worker_two = dispatcher.queue(_request(second.task_id))
    review_two = _review(second.task_id, worker_two.run_id)
    store.finish(review_two.run_id, RunState.FAILED, expected_version=review_two.version,
                 exit_code=None, stdout_tail="", stderr_tail="",
                 error_message="adapter is no longer configured")
    assert _identity_of(store, review_two.run_id) == {
        "agora_role": ROLE_REVIEW, "reviews_run_id": worker_two.run_id, "task_version": 3,
    }

    # 3. cancel(), and 4. record_cancelled_output() writing that settled row again.
    third = _planned_task(tasks)
    worker_three = dispatcher.queue(_request(third.task_id))
    review_three = _review(third.task_id, worker_three.run_id)
    cancelled = store.cancel(review_three.run_id,
                             CancelRunRequest(expected_version=review_three.version,
                                              reason="human changed their mind"))
    assert _identity_of(store, review_three.run_id) == {
        "agora_role": ROLE_REVIEW, "reviews_run_id": worker_three.run_id, "task_version": 3,
    }
    store.record_cancelled_output(review_three.run_id, expected_version=cancelled.version,
                                  stdout_tail="partial", stderr_tail="", exit_code=None,
                                  deliverables={"files": [], "truncated": False})
    assert _identity_of(store, review_three.run_id) == {
        "agora_role": ROLE_REVIEW, "reviews_run_id": worker_three.run_id, "task_version": 3,
    }

    # 5. abandon(), the restart path for a run whose process we lost.
    fourth = _planned_task(tasks)
    worker_four = dispatcher.queue(_request(fourth.task_id))
    review_four = _review(fourth.task_id, worker_four.run_id)
    started = store.start(review_four.run_id, expected_version=review_four.version)
    store.abandon(review_four.run_id, expected_version=started.version,
                  reason="Agora restarted while the process was running")
    assert _identity_of(store, review_four.run_id) == {
        "agora_role": ROLE_REVIEW, "reviews_run_id": worker_four.run_id, "task_version": 3,
    }

    # 6. The queued-cancel settle: the sweep refuses a queued run and cancels it
    #    inside its own transaction, without going through _transition at all.
    fifth = _planned_task(tasks)
    worker_five = dispatcher.queue(_request(fifth.task_id))
    review_five = _review(fifth.task_id, worker_five.run_id)
    tasks.cancel(fifth.task_id, expected_version=3)  # so the sweep refuses both rows
    refused = {entry["run_id"] for entry in dispatcher.resume_queued()}
    assert {worker_five.run_id, review_five.run_id} <= refused
    assert store.require(review_five.run_id).state == RunState.CANCELLED
    assert _identity_of(store, review_five.run_id) == {
        "agora_role": ROLE_REVIEW, "reviews_run_id": worker_five.run_id, "task_version": 3,
    }
    assert _identity_of(store, worker_five.run_id) == {
        "agora_role": ROLE_WORKER, "task_version": 3,
    }


def test_a_result_write_may_not_carry_identity(tmp_path):
    """A caller that sends identity with a result is refused, loudly.

    Not merged over and not ignored: the dispatcher used to re-send ``agora_role``
    with every successful settle, which is what made identity look like something a
    result write carries — and so made the paths that had *no* result look like they
    were merely omitting an optional field rather than erasing the field the gates
    read. The run is left exactly as it was, because a refused write writes nothing.
    """
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))
    running = store.start(run.run_id, expected_version=run.version)

    for offered in ({ROLE_KEY: ROLE_REVIEW}, {ROLE_KEY: ROLE_WORKER},
                    {REVIEWS_RUN_KEY: "run_whatever"}, {"task_version": 99}):
        with pytest.raises(RunValidationError, match="identity key"):
            store.finish(run.run_id, RunState.SUCCEEDED, expected_version=running.version,
                         exit_code=0, stdout_tail="done", stderr_tail="",
                         result_metadata=offered)
    still = store.require(run.run_id)
    assert still.state == RunState.RUNNING
    assert still.version == running.version
    assert _identity_of(store, run.run_id) == {"agora_role": ROLE_WORKER, "task_version": 3}


#: Stands in for an f-string's interpolated part when the SQL of a statement is
#: reassembled from the AST. It is not valid SQL, which is the point: it marks a
#: place the check cannot read rather than pretending the text is complete.
_SQL_INTERPOLATION = "\x00{}\x00"

#: Any *mention* of the column anywhere in a statement, which is what the check
#: below keys on — not "an assignment", and not scoped to a clause. Note the
#: leading ``\b``: it does not match inside ``_write_result_metadata``, so the
#: writer's own name is never a false positive.
_NAMES_RESULT_METADATA = re.compile(r"\bresult_metadata\b", re.IGNORECASE)
_UPDATES_RUNS = re.compile(r"\bUPDATE\s+execution_runs\b", re.IGNORECASE)

#: The one function in ``agora/execution/store.py`` whose UPDATE may name
#: ``result_metadata``. Structural, not stylistic: the column has a single writer,
#: which is where ``_identity_preserved`` is called.
_RESULT_METADATA_WRITER = "_write_result_metadata"


def _sql_literals(tree: ast.AST) -> list[tuple[str, str]]:
    """``(enclosing function, SQL text)`` for every string literal in a module.

    Reading literals from the **AST** rather than from the file text is what makes
    the guard below unfussy about how a string is written: adjacent string literals
    are concatenated by the parser itself, so a statement split as
    ``"UPDATE ... SET result_" "metadata = ?"`` arrives here as one value. An
    f-string is one node too, with its interpolated parts marked (they are the parts
    a static reader cannot resolve).
    """
    found: list[tuple[str, str]] = []

    def walk(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, child.name)
                continue
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                found.append((scope, child.value))
                continue
            if isinstance(child, ast.JoinedStr):
                # Handled whole, and NOT descended into: its literal fragments on
                # their own would each read as a complete statement and the
                # interpolation between them would vanish.
                found.append((scope, "".join(
                    part.value if isinstance(part, ast.Constant) and isinstance(part.value, str)
                    else _SQL_INTERPOLATION
                    for part in child.values
                )))
                continue
            walk(child, scope)

    walk(tree, "<module>")
    return found


def test_no_raw_sql_write_of_result_metadata():
    """``result_metadata`` has exactly one writer in ``store.py``, and no other
    UPDATE of ``execution_runs`` there so much as names the column.

    ``result_metadata`` carries a run's identity, which every gate in ``store.py``
    reads, and the column is written wholesale — so a second statement assigning it
    can erase the identity without touching a line of gate logic, which is how a
    QUEUED review run silently lost its ``agora_role`` (K1).

    **This check is a backstop, not the guarantee.** The guarantee is structural:
    ``ExecutionStore._write_result_metadata`` owns the column and is where
    ``_identity_preserved`` is called; ``_transition`` delegates to it. Three earlier
    revisions of this test tried instead to decide whether a SET clause *assigned*
    the column, and each was defeated by a form of SQL its parser could not read —
    the last one (M3) cut the statement at the first ``WHERE``, so

        UPDATE execution_runs SET x = (SELECT ... WHERE ...), result_metadata = ? WHERE ...

    hid the assignment behind a subquery's own ``WHERE`` and passed. Removing the
    parser removes that whole class of hole.

    **What it actually checks**, stated exactly:

    - String literals come from the module's **AST**, so adjacent-literal splitting
      cannot hide a statement, and an f-string is read as one value with its
      interpolations marked.
    - Every literal containing ``UPDATE execution_runs`` is examined. If it *mentions*
      ``result_metadata`` anywhere at all — assignment or not, before or after any
      ``WHERE``, at any nesting depth — it must be in ``_write_result_metadata``.
      Being position-independent is the whole improvement: there is no clause to
      truncate and no nesting to respect.
    - The writer's own statement must be a plain literal (no interpolation), there
      must be exactly one of them, and the writer must call ``_identity_preserved``.
    - A statement whose text is built from an interpolation cannot be read at all, so
      it is refused in any function that names ``result_metadata`` — **with no
      exemption** since N4 (2026-08-30). The previous revision exempted such a
      statement in a function that delegates to the writer, and ``_transition`` was
      exactly that: it built its SET list from an update dict *and* called the writer,
      so a reviewer added ``updates["result_metadata"] = ...`` there and this test
      reported ``offenders=[]`` with every structural assertion below still satisfied.
      The fix was not a smarter guard (this is its fifth revision). ``_transition``'s
      dynamic ``UPDATE`` moved to ``ExecutionStore._write_transition_columns``, whose
      body never names the column and whose vocabulary is the fixed
      ``_TRANSITION_COLUMNS`` list; a key outside that list raises. So the exemption
      had nothing left to cover and is gone, and the two remaining interpolated
      statements (that one and ``record_cancelled_output``'s ``{snapshot_sql}``) pass
      because neither function mentions the column at all.

    **What it does not check**, so nobody relies on more than is here:

    - SQL assembled with ``+``, from a module constant, or by a helper returning
      fragments — anything other than adjacent literals and f-strings.
    - Writes to the column from outside ``store.py``, and writes issued by a caller
      holding a raw connection.
    - Whether ``_identity_preserved`` is called *for* the statement rather than
      merely in the same function.

    Verified by mutation on 2026-08-30, in five styles: a ``SET result_metadata =
    :meta`` sweep (named parameter); the same statement split across adjacent
    literals as ``"... result_" "metadata=COALESCE(json(:meta), ...)"`` (no spaces,
    wrapped value); a dynamic ``SET {assignments}`` whose column dict included the
    column; the M3 style the fourth revision missed — an assignment placed
    after a subquery's own ``WHERE`` inside the SET list of ``_cancel_locked``; and the
    N4 style the fifth revision missed — ``updates["result_metadata"] = ...`` inside
    ``_transition``, which now cannot be written at all
    (``test_a_transition_may_not_write_result_metadata`` is the assertion that it
    raises, and this test's ``_TRANSITION_COLUMNS`` check is why the SET list cannot
    name it). Each one makes a test fail, naming the function or the column;
    reverting it makes the suite pass.

    The N4 style is the one to be careful about when re-verifying: until O2 (round 5,
    2026-08-30) it failed *only* in unrelated tests that happen to perform a
    transition, because neither named guard exercised ``_transition`` itself — the
    companion test called ``_write_transition_columns`` with its own dict. That test
    now performs one real transition, so the mutation fails a test that names the
    property rather than 57 that do not.
    """
    module = Path(__file__).resolve().parents[1] / "agora" / "execution" / "store.py"
    source = module.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {
        node.name: ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    offenders: list[str] = []
    checked = 0
    writer_statements = 0
    for scope, sql in _sql_literals(tree):
        if not _UPDATES_RUNS.search(sql):
            continue
        checked += 1
        body = functions.get(scope, "")
        unreadable = _SQL_INTERPOLATION in sql
        if _NAMES_RESULT_METADATA.search(sql):
            if scope != _RESULT_METADATA_WRITER:
                offenders.append(
                    f"{scope}: an UPDATE of execution_runs names result_metadata; only "
                    f"{_RESULT_METADATA_WRITER} may write that column"
                )
            elif unreadable:
                offenders.append(
                    f"{scope}: the writer's own statement is built from an interpolation, "
                    "so this check can no longer read which column it writes"
                )
            else:
                writer_statements += 1
            continue
        if unreadable and _NAMES_RESULT_METADATA.search(body):
            offenders.append(
                f"{scope}: SET clause is built dynamically in a function that names "
                "result_metadata, so this check cannot read which column it writes"
            )
    assert not offenders, "raw result_metadata write(s): " + "; ".join(offenders)
    # The structure itself, asserted rather than assumed: one writer, one statement,
    # and the identity check inside it.
    assert writer_statements == 1, (
        f"expected exactly one UPDATE naming result_metadata (in {_RESULT_METADATA_WRITER}), "
        f"saw {writer_statements}"
    )
    assert "_identity_preserved" in functions.get(_RESULT_METADATA_WRITER, "")
    # _transition must still reach the column through the writer; if it stopped
    # doing so, every terminal path would silently stop recording a result.
    assert _RESULT_METADATA_WRITER in functions.get("_transition", "")
    # ...and the dynamic statement it delegates *from* must not be able to name the
    # column. This is what replaced the exemption above (N4): the vocabulary of the
    # only interpolated UPDATE in the module is this fixed list.
    assert "result_metadata" not in ExecutionStore._TRANSITION_COLUMNS
    # The guard is worthless if it stopped finding the statements it reasons about.
    assert checked >= 4, f"expected the module's UPDATE execution_runs statements, saw {checked}"


def test_a_transition_may_not_write_result_metadata(tmp_path):
    """The structural half of N4, asserted behaviourally rather than by reading SQL.

    ``_transition`` builds its SET list dynamically, so ``test_no_raw_sql_write_of_
    result_metadata`` above cannot read which columns it writes — and its previous
    revision therefore *exempted* it, on the ground that it delegates to
    ``_write_result_metadata``. A reviewer added one line, ``updates["result_metadata"]
    = ...``, and that guard reported no offenders: a second writer of the identity
    column, admitted by the exemption.

    The exemption is gone because the hole is gone. ``ExecutionStore._TRANSITION_
    COLUMNS`` is the whole vocabulary of that statement and does not contain the
    column, and a key outside the list raises instead of being dropped — dropping it
    would silently discard a write the caller believed it made. This test is the
    assertion that it raises, so the reviewer's mutation now fails here rather than
    passing everywhere.

    **Second half, added by O2 in round 5 (2026-08-30): it goes through
    ``_transition``.** The first assertion below calls ``_write_transition_columns``
    directly with a dict this test wrote, which proves the refusal exists but not that
    the production path is subject to it — so the N4 mutation
    (``updates["result_metadata"] = "{}"`` inside ``_transition``) left this test and
    ``test_no_raw_sql_write_of_result_metadata`` both passing, and was caught only by
    the 57 unrelated tests that happen to perform a transition. A loud failure in
    tests about something else is a coincidence, not a guard: nothing named the
    property, so nothing would have kept it. The cancel below is a real
    ``_transition``, so the mutation now fails a test that says what it protects, and
    the identity assertion after it covers the other shape of the same mistake — the
    column being *added* to ``_TRANSITION_COLUMNS`` so the write is admitted and
    erases ``agora_role`` wholesale.
    """
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))

    with pytest.raises(RunValidationError) as refused:
        with store._transaction() as db:
            store._write_transition_columns(
                db,
                run_id=run.run_id,
                expected_version=run.version,
                updates={"state": RunState.CANCELLED.value, "result_metadata": "{}"},
            )
    assert "result_metadata" in str(refused.value)
    assert _RESULT_METADATA_WRITER in str(refused.value)
    # Refused, not partially applied: the transaction rolled back with the run intact.
    assert store.require(run.run_id).state == RunState.QUEUED
    assert _identity_of(store, run.run_id) == {"agora_role": ROLE_WORKER, "task_version": 3}

    # And the production path is subject to the same refusal: one real transition
    # through ``_transition``, which must settle the run and leave its identity
    # exactly as ``create`` wrote it.
    settled = store.cancel(run.run_id, CancelRunRequest(
        expected_version=run.version, reason="one real transition, on purpose",
    ))
    assert settled.state == RunState.CANCELLED
    assert _identity_of(store, run.run_id) == {"agora_role": ROLE_WORKER, "task_version": 3}


def test_startup_reconciles_reviews_owed_before_resuming_queued_runs(monkeypatch):
    """The reconciliation is only a fix if it runs, and it has to run first.

    Before the API serves, so no client can observe the unreleasable state, and
    before ``resume_queued``, so a blocker it raises is visible to the sweep that
    refuses queued worker runs on a gated task.

    ``lifespan`` makes **one** call, and the order lives in the real
    ``ExecutionDispatcher.start_recovery`` — borrowed unbound by the stub below, so
    this test exercises the production sequencing function rather than a copy of its
    order. Two statements in ``lifespan`` pinned by a test caught a reordering in
    that one file and nothing else; ``resume_queued``'s own guard is what makes a
    reordering anywhere fail loudly
    (``test_resume_queued_refuses_to_run_before_the_reconciliation``).
    """
    calls: list[str] = []

    class _Dispatcher:
        auto_review = True
        reviews_owed_unreadable = None
        reviews_owed_unrecorded: list = []
        start_recovery = ExecutionDispatcher.start_recovery

        def reconcile_reviews_owed(self):
            calls.append("reconcile")
            return []

        def resume_queued(self):
            calls.append("resume")
            return []

        async def shutdown(self):
            calls.append("shutdown")

    class _Supervisor:
        def start(self):
            calls.append("supervisor")

        async def shutdown(self):
            pass

    factory = lru_cache(maxsize=1)(lambda: _Dispatcher())
    monkeypatch.setattr(app_module, "get_execution_dispatcher", factory)
    monkeypatch.setattr(app_module, "get_workflow_supervisor", lambda: _Supervisor())
    monkeypatch.setattr(app_module, "initialize_control_plane_store", lambda: None)

    async def drive():
        async with app_module.lifespan(app_module.app):
            pass

    asyncio.run(drive())
    assert calls[:2] == ["reconcile", "resume"]


def test_resume_queued_refuses_to_run_before_the_reconciliation(tmp_path):
    """The ordering is enforced in the code, not only by statement order.

    ``reconcile_reviews_owed`` raises the gates ``resume_queued`` judges the
    persisted queue against, so going first means admitting a queued worker run on a
    task that is about to be gated. Reaching the sweep first is a bug in the caller,
    and it now says so instead of quietly admitting runs. Scoped to ``auto_review``,
    which is exactly when the reconciliation has anything to raise.
    """
    tasks, store, dispatcher = _system(
        tmp_path, [sys.executable, "-c", "print('ok')"], auto_review=True,
    )
    dispatcher.schedule = lambda run_id: None  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="before reconcile_reviews_owed"):
        dispatcher.resume_queued()

    # start_recovery is the sequence, and it returns the receipt for both steps.
    receipt = dispatcher.start_recovery()
    assert receipt == {
        "reviews_owed_reconciled": [],
        "reviews_owed_unreadable": None,
        "reviews_owed_unrecorded": [],
        "queued_runs_refused": [],
    }
    assert dispatcher.resume_queued() == []  # allowed now, and idempotent

    # With auto_review off nothing can be owed, so there is no order to get wrong
    # and the sweep is callable on its own (as several tests here do).
    _, _, no_review = _system(tmp_path / "off", [sys.executable, "-c", "print('ok')"])
    no_review.schedule = lambda run_id: None  # type: ignore[method-assign]
    assert no_review.resume_queued() == []


def test_an_unreadable_reviews_owed_read_is_recorded_and_does_not_stop_startup(tmp_path, monkeypatch):
    """The read was the one unwrapped call on a path that promises to keep serving.

    ``reconcile_reviews_owed`` wraps the per-task blocker write so one unrecordable
    task cannot cost the others theirs — but the read that finds those tasks was
    bare, so a database it could not read propagated out of ``lifespan``: the API
    never served and ``resume_queued`` never ran, which is the opposite of what the
    method's own docstring promised. It is guarded now, and the failure is *recorded*
    rather than skipped: an unreadable reconciliation is a fact about this process,
    because every task it would have recorded a blocker for stays refused with
    ``review_owed`` until a later start reads it.
    """
    tasks, store, dispatcher = _system(
        tmp_path, [sys.executable, "-c", "print('ok')"], auto_review=True,
    )
    dispatcher.schedule = lambda run_id: None  # type: ignore[method-assign]
    task = _planned_task(tasks)
    queued = dispatcher.queue(_request(task.task_id))

    def _unreadable():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "reviews_owed", _unreadable)
    receipt = dispatcher.start_recovery()

    assert receipt["reviews_owed_reconciled"] == []
    assert "OperationalError" in receipt["reviews_owed_unreadable"]
    assert dispatcher.reviews_owed_unreadable == receipt["reviews_owed_unreadable"]
    # The rest of startup still happened: the sweep ran and the queued run survives.
    assert receipt["queued_runs_refused"] == []
    assert store.require(queued.run_id).state == RunState.QUEUED
    # And no Attention item was invented for a read that produced nothing.
    assert AttentionStore(tasks).list(task_id=task.task_id) == []


def test_lifespan_reports_an_unreadable_reconciliation_to_the_operator(monkeypatch, capsys):
    """A reconciliation that could not run is surfaced, not swallowed.

    stderr is the only channel available here — the failure *is* a database read, so
    there is nowhere durable to write it — and it is the operator's channel at
    startup. Serving continues either way; the point is that nobody has to infer
    from a later ``review_owed`` refusal that the reconciliation never ran.
    """
    class _Dispatcher:
        auto_review = True
        reviews_owed_unreadable = "OperationalError: database is locked"
        reviews_owed_unrecorded: list = []
        start_recovery = ExecutionDispatcher.start_recovery

        def reconcile_reviews_owed(self):
            return []

        def resume_queued(self):
            return []

        async def shutdown(self):
            pass

    class _Supervisor:
        def start(self):
            pass

        async def shutdown(self):
            pass

    factory = lru_cache(maxsize=1)(lambda: _Dispatcher())
    monkeypatch.setattr(app_module, "get_execution_dispatcher", factory)
    monkeypatch.setattr(app_module, "get_workflow_supervisor", lambda: _Supervisor())
    monkeypatch.setattr(app_module, "initialize_control_plane_store", lambda: None)

    async def drive():
        async with app_module.lifespan(app_module.app):
            pass

    asyncio.run(drive())

    reported = capsys.readouterr().err
    assert "reconciliation could not run" in reported
    assert "database is locked" in reported


def test_run_gate_conflict_codes_mirrored_by_the_frontend():
    """frontend/src/lib/continue-run.ts hardcodes these 409 codes in
    RUN_GATE_CODES, because a client cannot ask the server what its own refusal
    means. Nothing at runtime keeps the two in step, so this test is the alarm: if
    it fails, that file is what has to be updated."""
    mirror = (
        Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "continue-run.ts"
    )
    source = mirror.read_text(encoding="utf-8")
    assert f'attentionOpen: "{GATE_CODE_ATTENTION_OPEN}"' in source
    assert f'reviewActive: "{GATE_CODE_REVIEW_ACTIVE}"' in source
    assert f'reviewOwed: "{GATE_CODE_REVIEW_OWED}"' in source


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


def test_an_unreadable_workspace_fails_the_run_without_calling_it_missing(tmp_path, monkeypatch):
    """Fail closed either way, but record the reason that is true.

    `workspace.is_dir()` answered False both for a worktree that is gone and for one
    this process could not stat, so an ACL-denied workspace was recorded as "workspace
    not found" — sending the operator to reprovision a directory that is still there
    instead of to fix a permission.
    """
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('never runs')"])
    task = _planned_task(tasks)
    queued = dispatcher.queue(_request(task.task_id))
    monkeypatch.setattr(
        "agora.execution.dispatcher.probe_path",
        lambda path, **kwargs: PathProbe("unknown", error=PermissionError(13, "denied")),
    )
    failed = asyncio.run(dispatcher.execute(queued.run_id))
    assert failed.state == RunState.FAILED
    assert failed.error_message == "workspace could not be inspected (PermissionError)"


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


def test_a_cancel_racing_the_pid_binding_still_records_what_the_run_wrote(tmp_path):
    """A cancel between the launch and ``attach_pid`` must not lose the files.

    That branch used to stop the process and return the row as it found it: no
    deliverable snapshot, no output record. The changes the agent had already
    written were then absorbed into the *next* run's baseline, so they were
    attributable to nothing — the one thing the whole deliverables machinery
    exists to prevent.
    """
    workspace = tmp_path / "workspaces" / "codex"
    workspace.mkdir(parents=True, exist_ok=True)
    # The process writes a file immediately, then lingers so the cancel window
    # is real rather than simulated by timing.
    program = (
        "import pathlib, sys, time; "
        "pathlib.Path('written-before-cancel.txt').write_text('x', encoding='utf-8'); "
        # A real codex first event, so the drained output names a session.
        "print('{\"type\":\"thread.started\",\"thread_id\":\"sid-before-cancel\"}', flush=True); "
        "print('printed-before-cancel', flush=True); "
        "time.sleep(30)"
    )
    # codex's event format, declared: an overridden command does not inherit it,
    # and the point of this test is what the drained output *said*.
    config = _config(tmp_path, [sys.executable, "-c", program])
    config["execution"]["adapters"]["codex"]["event_format"] = "codex_jsonl"
    tasks = TaskStore(tmp_path / "agora.db")
    store = ExecutionStore(tasks)
    dispatcher = ExecutionDispatcher(
        store, ProjectRegistry(config), build_adapter_registry(config),
        allowed_workspace_roots=[tmp_path / "workspaces"],
    )
    task = _planned_task(tasks)
    queued = dispatcher.queue(_request(task.task_id, timeout=30))

    real_attach = store.attach_pid

    written = workspace / "written-before-cancel.txt"

    def cancel_then_conflict(run_id, *, expected_version, pid, command=None):
        # Waited for deliberately: the race is "a cancel lands a moment after the
        # process started", and the interesting case is the one where it already
        # wrote something. Relying on scheduling instead would have tested an
        # empty workspace most runs — which is exactly what the first version of
        # this test did, and it passed against a snapshot of nothing.
        for _ in range(200):
            if written.exists():
                break
            time.sleep(0.05)
        # The row is cancelled by someone else, so the optimistic write this call
        # was about to make no longer applies.
        current = store.require(run_id)
        store.cancel(run_id, CancelRunRequest(expected_version=current.version))
        raise RunConflictError("cancelled while binding the pid")

    store.attach_pid = cancel_then_conflict
    try:
        result = asyncio.run(dispatcher.execute(queued.run_id))
    finally:
        store.attach_pid = real_attach

    assert result.state == RunState.CANCELLED
    assert written.exists(), "the child must have written before the cancel landed"
    snapshot = store.deliverable_snapshot(queued.run_id)
    assert snapshot is not None, "a cancelled run that already wrote files must say so"
    recorded = json.dumps(snapshot, ensure_ascii=False)
    assert "written-before-cancel.txt" in recorded
    # And what it had already printed. Recording two empty tails here left the
    # person with their own prompt and nothing else, while the branch's own
    # comment claimed it fixed exactly that.
    settled = store.require(queued.run_id)
    assert "printed-before-cancel" in settled.stdout_tail
    # And the session it announced. Saving the raw tail kept the text and threw
    # away what it said: the session table stayed empty, so the next message
    # opened a brand-new conversation over work that existed.
    recorded = dispatcher.sessions.get(task.task_id, "codex", ROLE_WORKER)
    assert recorded.session_id == "sid-before-cancel"


def test_a_cancel_during_the_error_path_snapshot_still_records_the_deliverables(tmp_path, monkeypatch):
    """The exception path re-reads the run after snapshotting, like the settle path.

    Snapshotting a large worktree takes long enough for a cancel to land inside
    it. The version captured before then made `finish` conflict, so the snapshot
    was computed and thrown away — and the files it described were absorbed by
    the next run's baseline, attributable to nobody.
    """
    from agora.execution import dispatcher as dispatcher_module

    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    task = _planned_task(tasks)
    queued = dispatcher.queue(_request(task.task_id))

    async def explode(*args, **kwargs):
        raise RuntimeError("dispatcher blew up mid-turn")

    def snapshot_and_cancel(workspace, baseline):
        # The cancel lands *while* the snapshot is being taken.
        current = store.require(queued.run_id)
        store.cancel(queued.run_id, CancelRunRequest(expected_version=current.version))
        return {"files": [{"path": "left-behind.txt", "change": "added"}], "truncated": False}

    monkeypatch.setattr(dispatcher, "_read_streaming", explode)
    monkeypatch.setattr(dispatcher_module, "snapshot_changes", snapshot_and_cancel)

    # `_execute_guarded` is the wrapper `schedule()` runs a turn through, and the
    # branch under test is its `except Exception`. `execute()` on its own lets the
    # error out, which is what a caller awaiting it should see.
    result = asyncio.run(dispatcher._execute_guarded(queued.run_id))

    assert result.state == RunState.CANCELLED
    snapshot = store.deliverable_snapshot(queued.run_id)
    assert snapshot is not None, "the snapshot must not be computed and dropped"
    assert "left-behind.txt" in json.dumps(snapshot, ensure_ascii=False)


def test_a_failure_after_a_cancel_still_freezes_what_the_run_wrote(tmp_path, monkeypatch):
    """The error path covers an already-cancelled row too.

    The person cancels; then something on the way out fails — a locked database,
    a full disk, anything the settle path can raise. Returning the row there
    dropped both the snapshot and the baseline, so the files the agent had
    already written were absorbed by the next run's baseline and attributed to
    nobody.
    """
    from agora.execution import dispatcher as dispatcher_module

    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "print('ok')"])
    task = _planned_task(tasks)
    queued = dispatcher.queue(_request(task.task_id))

    def cancel_then_explode(*args, **kwargs):
        current = store.require(queued.run_id)
        if current.state == RunState.RUNNING:
            store.cancel(queued.run_id, CancelRunRequest(expected_version=current.version))
        raise RuntimeError("settling blew up after the cancel")

    def snapshot(workspace, baseline):
        return {"files": [{"path": "written-then-cancelled.txt", "change": "added"}],
                "truncated": False}

    monkeypatch.setattr(dispatcher, "_settle_session", cancel_then_explode)
    monkeypatch.setattr(dispatcher_module, "snapshot_changes", snapshot)

    result = asyncio.run(dispatcher._execute_guarded(queued.run_id))

    assert result.state == RunState.CANCELLED
    delivered = store.deliverable_snapshot(queued.run_id)
    assert delivered is not None, "a cancelled run's files must not be dropped by a later failure"
    assert "written-then-cancelled.txt" in json.dumps(delivered, ensure_ascii=False)
