"""W3-a U5: the dispatcher runs a remote adapter's runs on the remote host.

Everything below is exercised through fakes standing in for the SSH layer: a fake
environment whose ``launch`` hands back a scripted :class:`ProcessHandle`, a fake
provisioner that answers "ready", a fake deliverables reader with a baseline and a
snapshot. What is real is the dispatcher: the branch that picks remote over local,
the streaming, the timeout, the settle, the review host check, and ``_stop`` taking
a handle. The real-host round trip lives in tests/test_environments_remote.py's
integration tests (run on the machine that can reach the host).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path, PurePosixPath

import pytest

from agora.execution import remote_wiring
from agora.execution.adapters import ExecutionAdapter, RemoteAdapterConfig, build_adapter_registry
from agora.execution.dispatcher import ExecutionDispatcher
from agora.execution.models import CreateRunRequest, REMOTE_TARGET_KEY, ROLE_CHAT, ROLE_REVIEW, ROLE_WORKER, RunState
from agora.execution.store import ExecutionStore, RunValidationError
from agora.projects import ProjectRegistry
from agora.tasks.models import CreateTaskRequest, TaskState
from agora.tasks.store import TaskStore
from agora.attention.store import AttentionStore


REMOTE_ROOT = "/srv/agora/projects/alpha"
REMOTE_WS = "/srv/agora/projects/alpha/.agora/workspaces/claude"


def _config(tmp_path, *, reviewer: str = "codex", reviewer_remote: bool = False) -> dict:
    ssh = {
        "ssh_host": "example.invalid", "ssh_user": "agora", "secret_ref": "AGORA_TEST_SSH",
        "remote_workspace": REMOTE_WS, "remote_project_root": REMOTE_ROOT,
    }
    adapters = {
        "claude": {"command": ["claude", "-p", "{prompt}"], "workspace_key": "claude", "bridge_mode": "ssh", "ssh": ssh},
        "codex": {"command": [sys.executable, "-c", "print('local review')", "{prompt}"], "workspace_key": "codex"},
        "kiro": {"enabled": False},
    }
    if reviewer_remote:
        adapters["codex"] = {**adapters["codex"], "bridge_mode": "ssh", "ssh": {**ssh, "remote_workspace": REMOTE_ROOT + "/.agora/workspaces/codex"}}
    return {
        "projects": {
            "registry_path": str(tmp_path / "projects.yaml"),
            "default": "alpha",
            "projects": {
                "alpha": {
                    "name": "Alpha",
                    "worker_agent": "claude",
                    "reviewer_agent": reviewer,
                    # The registry resolves every path on THIS machine and creates
                    # `.agora/` under the root — a POSIX root would land on the local drive
                    # (see CR-89 in the task record). The remote root the dispatcher uses
                    # is the adapter's `remote_project_root`, a string the loader never
                    # resolves; the registry root here is only the local placeholder.
                    "root": str(tmp_path / "alpha"),
                    "workspaces": {
                        # Same reason as `root`: the registry creates these locally. The
                        # dispatcher takes the remote worktree from the adapter config.
                        "claude": str(tmp_path / "workspaces" / "claude"),
                        "codex": str(tmp_path / "workspaces" / "codex"),
                    },
                }
            },
        },
        "execution": {
            "max_concurrent_global": 2,
            "max_concurrent_per_project": 2,
            "allowed_workspace_roots": [str(tmp_path / "workspaces")],
            "adapters": adapters,
        },
    }


def _system(tmp_path, **kw):
    config = _config(tmp_path, **kw)
    tasks = TaskStore(tmp_path / "agora.db")
    store = ExecutionStore(tasks)
    projects = ProjectRegistry(config)
    adapters = build_adapter_registry(config)
    dispatcher = ExecutionDispatcher(
        store, projects, adapters, max_concurrent_global=2, max_concurrent_per_project=2,
        allowed_workspace_roots=[tmp_path / "workspaces"], auto_review=True,
    )
    return tasks, store, dispatcher


def _planned_task(tasks: TaskStore):
    task = tasks.create(CreateTaskRequest(project_id="alpha", title="Implement remotely"))
    with tasks._transaction() as db:
        db.execute("UPDATE tasks SET state = ?, version = 2 WHERE task_id = ?", (TaskState.PLANNED.value, task.task_id))
    return tasks.get(task.task_id)


def _request(task_id: str, adapter: str = "claude", *, version: int = 2, timeout: int = 10) -> CreateRunRequest:
    return CreateRunRequest(task_id=task_id, adapter=adapter, prompt="build it", timeout_seconds=timeout, expected_task_version=version)


# --- fakes standing in for asyncssh -------------------------------------------------

class _Reader:
    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)

    async def read(self, n: int = -1) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


class _Handle:
    """A scripted remote process: emits its output, then exits after ``delay``."""

    def __init__(self, *, stdout: list[bytes], code: int = 0, delay: float = 0.0):
        self._stdout = _Reader(stdout)
        self._stderr = _Reader([])
        self._code = code
        self._delay = delay
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    pid = None

    @property
    def stdout(self):
        return self._stdout

    @property
    def stderr(self):
        return self._stderr

    async def wait(self, timeout: float | None = None) -> int | None:
        if self.returncode is not None:
            return self.returncode
        if timeout is not None and self._delay > timeout:
            await asyncio.sleep(timeout)
            return None
        await asyncio.sleep(self._delay)
        self.returncode = self._code
        return self.returncode

    async def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    async def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class _Env:
    def __init__(self, handle: _Handle):
        self.handle = handle
        self.launched: list[dict] = []
        self.closed = False

    async def launch(self, argv, *, cwd=None, env=None):
        self.launched.append({"argv": list(argv), "cwd": cwd, "env": dict(env or {})})
        return self.handle

    async def aclose(self) -> None:
        self.closed = True


class _Provisioner:
    def __init__(self, ready: bool = True):
        self.ready = ready
        self.asked: list[tuple] = []

    async def is_ready_worktree(self, workspace, project_root, expected_branch) -> bool:
        self.asked.append((workspace, project_root, expected_branch))
        return self.ready


class _Deliverables:
    def __init__(self, files: list[dict] | None = None, *, baseline_ok: bool = True):
        self.files = files or []
        self.baseline_ok = baseline_ok

    async def baseline_state(self, workspace):
        return ({}, True, "abc123") if self.baseline_ok else None

    async def status_entries(self, workspace):
        return (list(self.files), False)

    async def head_commit(self, workspace):
        return "abc123"

    async def snapshot_changes(self, workspace, baseline):
        if baseline is None:
            return None
        return {"files": list(self.files), "truncated": False}

    async def list_changes(self, workspace):
        return list(self.files)

    async def read_file(self, workspace, path):
        return {"path": path, "content": f"contents of {path}", "truncated": False}


def _fake_components(monkeypatch, *, handle: _Handle, ready: bool = True, files=None, baseline_ok=True):
    env = _Env(handle)
    prov = _Provisioner(ready)
    deliv = _Deliverables(files, baseline_ok=baseline_ok)
    made: list = []

    def factory(adapter, projects, *, allowed_workspace_roots=None):
        comp = remote_wiring.RemoteComponents(
            config=adapter.remote, environment=env, filesystem=None, provisioner=prov, deliverables=deliv,  # type: ignore[arg-type]
        )
        made.append(comp)
        return comp

    monkeypatch.setattr("agora.execution.dispatcher.remote_components_for", factory)
    return env, prov, deliv, made


# --- queue -----------------------------------------------------------------------------

def test_a_remote_adapter_s_run_is_queued_against_the_remote_worktree_untouched(tmp_path):
    """The workspace is the path on the host — no local resolve, no local root check,
    forward slashes intact even on Windows."""
    tasks, store, dispatcher = _system(tmp_path)
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))
    assert run.workspace == REMOTE_WS
    assert dispatcher.adapters["claude"].remote is not None


def test_remote_workspace_of_keeps_posix_paths():
    class P:
        workspaces = {"claude": Path(REMOTE_WS)}
    adapter = ExecutionAdapter(
        name="claude", command_template=("claude",), workspace_key="claude",
        remote=RemoteAdapterConfig(ssh_host="h", ssh_user="u", secret_ref="S", remote_workspace=REMOTE_WS, remote_project_root=REMOTE_ROOT),
    )
    assert remote_wiring.remote_workspace_of(adapter, P()) == PurePosixPath(REMOTE_WS)
    assert str(remote_wiring.remote_workspace_of(adapter, P())) == REMOTE_WS


# --- execute -----------------------------------------------------------------------------

def test_a_remote_run_executes_on_the_host_and_settles_with_its_snapshot(tmp_path, monkeypatch):
    tasks, store, dispatcher = _system(tmp_path, reviewer="codex")
    dispatcher.auto_review = False
    handle = _Handle(stdout=[b"hello from ", b"the host\n"])
    env, prov, deliv, made = _fake_components(monkeypatch, handle=handle, files=[{"path": "a.py", "status": "modified"}])
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))

    finished = asyncio.run(dispatcher.execute(run.run_id))

    assert finished.state == RunState.SUCCEEDED
    assert "hello from the host" in finished.stdout_tail
    # Launched where the run belongs, with only the AGORA_* the agent needs asked for.
    assert env.launched and env.launched[0]["cwd"].as_posix() == REMOTE_WS
    assert set(env.launched[0]["env"]) == {"AGORA_TASK_ID", "AGORA_RUN_ID", "AGORA_PROJECT_ID"}
    # The command was not resolved against this machine's PATH.
    assert env.launched[0]["argv"][0] == "claude"
    # Readiness was asked of the remote provisioner, against the configured remote root.
    assert prov.asked and prov.asked[0][1].as_posix() == REMOTE_ROOT
    # The snapshot is the remote one, and the connection was closed.
    assert store.deliverable_snapshot(run.run_id) == {"files": [{"path": "a.py", "status": "modified"}], "truncated": False}
    assert made and env.closed
    assert tasks.get(task.task_id).state == TaskState.RUNNING


def test_a_remote_run_that_outlives_its_timeout_is_stopped_over_the_handle(tmp_path, monkeypatch):
    tasks, store, dispatcher = _system(tmp_path)
    dispatcher.auto_review = False
    handle = _Handle(stdout=[b"working..."], delay=30)
    _fake_components(monkeypatch, handle=handle)
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id, timeout=1))

    finished = asyncio.run(dispatcher.execute(run.run_id))

    assert finished.state == RunState.TIMED_OUT
    assert handle.terminated  # a signal over the channel, not a local kill
    assert "timeout" in (finished.error_message or "")


def test_a_remote_worktree_that_is_not_ready_fails_the_run_without_launching(tmp_path, monkeypatch):
    tasks, store, dispatcher = _system(tmp_path)
    dispatcher.auto_review = False
    handle = _Handle(stdout=[b"never"])
    env, prov, deliv, made = _fake_components(monkeypatch, handle=handle, ready=False)
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))

    finished = asyncio.run(dispatcher.execute(run.run_id))

    assert finished.state == RunState.FAILED
    assert "provision" in (finished.error_message or "")
    assert env.launched == []


def test_an_unreadable_remote_baseline_leaves_the_delivery_unestablished_not_empty(tmp_path, monkeypatch):
    tasks, store, dispatcher = _system(tmp_path)
    dispatcher.auto_review = False
    handle = _Handle(stdout=[b"done"])
    _fake_components(monkeypatch, handle=handle, files=[{"path": "x", "status": "added"}], baseline_ok=False)
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))

    finished = asyncio.run(dispatcher.execute(run.run_id))

    assert finished.state == RunState.SUCCEEDED
    # None, not [] — the run's files could not be told from what was already there.
    assert store.deliverable_snapshot(run.run_id) is None


# --- review across hosts ----------------------------------------------------------------

def test_a_local_reviewer_cannot_review_a_remote_worker_and_the_task_is_blocked_saying_so(tmp_path, monkeypatch):
    tasks, store, dispatcher = _system(tmp_path, reviewer="codex")  # codex is local here
    handle = _Handle(stdout=[b"built\n"])
    _fake_components(monkeypatch, handle=handle, files=[{"path": "a.py", "status": "modified"}])
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))

    finished = asyncio.run(dispatcher.execute(run.run_id))

    assert finished.state == RunState.SUCCEEDED
    # No review run was queued; the review owed is recorded as a blocker naming the hosts.
    assert [r.run_id for r in store.list(task_id=task.task_id)] == [run.run_id]
    items = AttentionStore(tasks).list(task_id=task.task_id)
    assert any("主机" in item.body or "host" in item.body.lower() for item in items), [i.body for i in items]


def test_a_reviewer_on_the_same_host_is_queued_with_a_prompt_read_remotely(tmp_path, monkeypatch):
    tasks, store, dispatcher = _system(tmp_path, reviewer="codex", reviewer_remote=True)
    handle = _Handle(stdout=[b"built\n"])
    env, prov, deliv, made = _fake_components(monkeypatch, handle=handle, files=[{"path": "a.py", "status": "modified"}])
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))

    async def scenario():
        finished = await dispatcher.execute(run.run_id)
        # The review run is scheduled as a task on the loop; let it be queued, then cancel it
        # before it would try to execute (its fake handle is the same scripted one).
        await asyncio.sleep(0)
        for t in list(dispatcher._scheduled):
            t.cancel()
        await asyncio.sleep(0)
        return finished

    asyncio.run(scenario())
    runs = store.list(task_id=task.task_id)
    review = [r for r in runs if r.role == ROLE_REVIEW]
    assert review, [r.role for r in runs]
    full = store.require(review[0].run_id)
    assert "a.py" in full.prompt and "contents of a.py" in full.prompt


# --- _stop takes a handle ---------------------------------------------------------------

def test_stop_accepts_a_process_handle_and_signals_over_it():
    handle = _Handle(stdout=[], delay=100)
    asyncio.run(ExecutionDispatcher._stop(handle))
    assert handle.terminated


def test_a_question_on_a_remote_adapter_is_a_chat_run_there_too(tmp_path, monkeypatch):
    tasks, store, dispatcher = _system(tmp_path)
    handle = _Handle(stdout=[b"the answer\n"])
    env, *_ = _fake_components(monkeypatch, handle=handle)
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id), role=ROLE_CHAT)
    finished = asyncio.run(dispatcher.execute(run.run_id))
    assert finished.state == RunState.SUCCEEDED
    assert tasks.get(task.task_id).state == TaskState.PLANNED  # a question moves nothing
    assert [r.run_id for r in store.list(task_id=task.task_id)] == [run.run_id]  # and is not reviewed


# --- the place a run executed is part of its record ------------------------------------

def test_queue_records_where_a_remote_run_executes_and_later_reads_route_by_it(tmp_path):
    tasks, store, dispatcher = _system(tmp_path)
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))
    target = remote_wiring.remote_target_of(run)
    assert target == {"host": "example.invalid", "port": 22, "user": "agora", "workspace": REMOTE_WS, "project_root": REMOTE_ROOT}
    # Reading the run through an adapter that now points elsewhere is refused.
    moved = dispatcher.adapters["claude"]
    moved = remote_wiring.replace(moved, remote=moved.remote.model_copy(update={"ssh_host": "other.invalid"}))
    with pytest.raises(remote_wiring.RemoteTargetMismatch):
        remote_wiring.adapter_for_recorded_target(moved, target)
    with pytest.raises(remote_wiring.RemoteTargetMismatch):
        remote_wiring.adapter_for_recorded_target(dispatcher.adapters["codex"], target)  # local now
    # The same host with a changed path reads at the RECORDED path.
    repathed = remote_wiring.replace(dispatcher.adapters["claude"], remote=dispatcher.adapters["claude"].remote.model_copy(update={"remote_workspace": "/elsewhere"}))
    pinned = remote_wiring.adapter_for_recorded_target(repathed, target)
    assert pinned.remote.remote_workspace == REMOTE_WS


def test_a_client_cannot_forge_where_a_run_executed():
    with pytest.raises(Exception):
        CreateRunRequest(task_id="t", adapter="claude", prompt="x", timeout_seconds=10, expected_task_version=1,
                         metadata={REMOTE_TARGET_KEY: {"host": "evil"}})


def test_the_provisioner_sees_the_remote_paths_not_the_registry_s_resolved_ones(tmp_path):
    tasks, store, dispatcher = _system(tmp_path)
    adapter = dispatcher.adapters["claude"]
    view = remote_wiring._RemoteProjectView(dispatcher.projects, adapter, adapter.remote)
    project = view.get("alpha")
    assert project.root.as_posix() == REMOTE_ROOT
    assert project.workspaces["claude"].as_posix() == REMOTE_WS
    # The registry itself still holds this machine's resolved placeholder.
    assert dispatcher.projects.get("alpha").root != project.root


def test_a_run_recorded_remote_is_refused_when_its_adapter_no_longer_reaches_that_host(tmp_path, monkeypatch):
    tasks, store, dispatcher = _system(tmp_path)
    dispatcher.auto_review = False
    handle = _Handle(stdout=[b"never"])
    env, *_ = _fake_components(monkeypatch, handle=handle)
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))
    # The adapter moves hosts after the run was queued.
    moved = dispatcher.adapters["claude"]
    dispatcher.adapters["claude"] = remote_wiring.replace(moved, remote=moved.remote.model_copy(update={"ssh_host": "other.invalid"}))

    finished = asyncio.run(dispatcher.execute(run.run_id))

    assert finished.state == RunState.FAILED
    assert "other.invalid" in (finished.error_message or "") or "no longer" in (finished.error_message or "")
    assert env.launched == []


def test_a_run_summary_says_where_it_ran(tmp_path):
    tasks, store, dispatcher = _system(tmp_path)
    task = _planned_task(tasks)
    dispatcher.queue(_request(task.task_id))
    summary = store.list(task_id=task.task_id)[0]
    assert summary.remote is not None
    assert (summary.remote.host, summary.remote.user, summary.remote.workspace) == ("example.invalid", "agora", REMOTE_WS)


def test_review_reads_the_snapshot_so_committed_work_is_reviewed(tmp_path, monkeypatch):
    """A worker that commits during its run leaves a clean tree; the review must still
    see the files — they are in the run's snapshot, not in a live status."""
    tasks, store, dispatcher = _system(tmp_path, reviewer="codex", reviewer_remote=True)
    handle = _Handle(stdout=[b"committed it\n"])
    env, prov, deliv, made = _fake_components(monkeypatch, handle=handle, files=[])

    async def committed_snapshot(workspace, baseline):
        return {"files": [{"path": "committed.py", "status": "modified", "sha256": None, "committed_during_run": True}], "truncated": False}

    deliv.snapshot_changes = committed_snapshot  # type: ignore[method-assign]
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))

    async def scenario():
        finished = await dispatcher.execute(run.run_id)
        await asyncio.sleep(0)
        for t in list(dispatcher._scheduled):
            t.cancel()
        await asyncio.sleep(0)
        return finished

    asyncio.run(scenario())
    review = [r for r in store.list(task_id=task.task_id) if r.role == ROLE_REVIEW]
    assert review
    assert "committed.py" in store.require(review[0].run_id).prompt


def test_a_deletion_only_run_is_reviewed_as_a_deletion_not_as_no_changes(tmp_path, monkeypatch):
    tasks, store, dispatcher = _system(tmp_path, reviewer="codex", reviewer_remote=True)
    handle = _Handle(stdout=[b"removed it\n"])
    env, prov, deliv, made = _fake_components(monkeypatch, handle=handle, files=[])

    async def deleted_snapshot(workspace, baseline):
        return {"files": [{"path": "config/prod.yaml", "status": "deleted", "sha256": None, "committed_during_run": True}], "truncated": False}

    deliv.snapshot_changes = deleted_snapshot  # type: ignore[method-assign]
    task = _planned_task(tasks)
    run = dispatcher.queue(_request(task.task_id))

    async def scenario():
        finished = await dispatcher.execute(run.run_id)
        await asyncio.sleep(0)
        for t in list(dispatcher._scheduled):
            t.cancel()
        await asyncio.sleep(0)
        return finished

    asyncio.run(scenario())
    review = [r for r in store.list(task_id=task.task_id) if r.role == ROLE_REVIEW]
    assert review
    prompt = store.require(review[0].run_id).prompt
    assert "config/prod.yaml (deleted)" in prompt
    assert "No changed files" not in prompt
