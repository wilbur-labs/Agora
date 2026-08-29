"""A run's delivered file list is frozen when the run ends (D-1).

`list_changes` reads the worktree live, which answers "what is changed now" —
correct only while a run is active. Serving a *finished* run from it is wrong in
three ordinary situations: a handoff commit empties the list, a later run in the
same worktree is attributed to the earlier one, and two runs show an identical
list. These tests pin the frozen-snapshot behavior and, crucially, that a
terminal run with no snapshot reports that rather than falling back to the
worktree.
"""
from __future__ import annotations

import shutil
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from agora.api.app import app
from agora.execution.adapters import build_adapter_registry
from agora.execution.deliverables import (
    CLEAN_BASELINE,
    baseline_state,
    list_changes,
    snapshot_changes,
)
from agora.execution.dispatcher import ExecutionDispatcher
from agora.execution.models import CreateRunRequest, RunState
from agora.execution.router import get_execution_dispatcher, get_execution_store
from agora.execution.store import ExecutionStore, RunNotFoundError
from agora.projects import ProjectRegistry
from agora.tasks.models import CreateTaskRequest, TaskState
from agora.tasks.router import get_project_registry, get_task_store
from agora.tasks.store import TaskStore
from agora.workspaces.naming import worktree_branch

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git is required to provision run worktrees")

_GIT_ID = ("-c", "user.email=t@t", "-c", "user.name=t")


def _git(cwd, *args):
    subprocess.run([GIT, "-C", str(cwd), *args], check=True, timeout=15,
                   capture_output=True)


def _provision(tmp_path):
    root = tmp_path / "alpha"
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    (root / "seed.txt").write_text("seed", encoding="utf-8")
    _git(root, *_GIT_ID, "add", "-A")
    _git(root, *_GIT_ID, "commit", "-q", "-m", "seed")
    (tmp_path / "workspaces").mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspaces" / "codex"
    _git(root, "worktree", "add", "-q", "-b", worktree_branch("alpha", "codex"),
         str(workspace), "HEAD")
    return workspace


def _config(tmp_path, command: list[str]) -> dict:
    workspace = _provision(tmp_path)
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


def _system(tmp_path, command: list[str]):
    config = _config(tmp_path, command)
    tasks = TaskStore(tmp_path / "agora.db")
    store = ExecutionStore(tasks)
    projects = ProjectRegistry(config)
    dispatcher = ExecutionDispatcher(
        store, projects, build_adapter_registry(config),
        max_concurrent_global=2, max_concurrent_per_project=1,
        allowed_workspace_roots=[tmp_path / "workspaces"],
    )
    return tasks, store, dispatcher, projects


def _planned_task(tasks: TaskStore):
    task = tasks.create(CreateTaskRequest(project_id="alpha", title="Deliver something"))
    with tasks._transaction() as db:
        db.execute("UPDATE tasks SET state = ?, version = 2 WHERE task_id = ?",
                   (TaskState.PLANNED.value, task.task_id))
    return tasks.get(task.task_id)


def _writer(rel_path: str, body: str) -> list[str]:
    """A command that writes one file into its cwd, so the run really delivers."""
    return [sys.executable, "-c",
            f"open({rel_path!r}, 'w', encoding='utf-8').write({body!r})\nprint('done')"]


# ── the snapshot itself ────────────────────────────────────────────────

def test_snapshot_changes_adds_a_digest_per_file(tmp_path):
    workspace = _provision(tmp_path)
    (workspace / "made.txt").write_text("hello", encoding="utf-8")
    snap = snapshot_changes(workspace, CLEAN_BASELINE)
    assert snap["truncated"] is False
    files = snap["files"]
    assert [f["path"] for f in files] == ["made.txt"]
    entry = files[0]
    assert entry["status"] == "untracked"
    # sha256("hello")
    assert entry["sha256"] == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


def test_snapshot_of_a_clean_worktree_is_empty_not_unknown(tmp_path):
    workspace = _provision(tmp_path)
    assert snapshot_changes(workspace, CLEAN_BASELINE) == {"files": [], "truncated": False}


def test_collection_failure_is_unknown_not_empty(tmp_path):
    """A worktree that cannot be read must yield None. Freezing `[]` here would
    permanently record "this run changed nothing" for a `git status` that simply
    failed, and no later read could recover."""
    plain = tmp_path / "not-a-worktree"
    plain.mkdir()
    (plain / "file.txt").write_text("x", encoding="utf-8")
    assert snapshot_changes(plain, CLEAN_BASELINE) is None
    assert baseline_state(plain) is None
    # list_changes stays best-effort for the live path.
    assert list_changes(plain) == []


def test_baseline_keeps_an_earlier_runs_files_out_of_a_later_snapshot(tmp_path):
    """The queue allows another run on an already-RUNNING task, and a retry
    reuses the worktree, so a worktree is not clean at launch. Without the
    baseline the second run would claim the first run's file."""
    workspace = _provision(tmp_path)
    (workspace / "from-run-1.txt").write_text("earlier", encoding="utf-8")

    baseline = baseline_state(workspace)
    assert baseline is not None
    identities, complete = baseline
    assert set(identities) == {"from-run-1.txt"} and complete is True

    (workspace / "from-run-2.txt").write_text("later", encoding="utf-8")
    delivered = snapshot_changes(workspace, baseline)["files"]
    assert [f["path"] for f in delivered] == ["from-run-2.txt"]

    # A baseline file this run *did* rewrite is its delivery again.
    (workspace / "from-run-1.txt").write_text("rewritten by run 2", encoding="utf-8")
    delivered = snapshot_changes(workspace, baseline)["files"]
    assert sorted(f["path"] for f in delivered) == ["from-run-1.txt", "from-run-2.txt"]


def test_incomplete_baseline_makes_attribution_unknown(tmp_path):
    """If the worktree already held more changes than can be recorded, the
    baseline is only a prefix and subtracting it cannot separate this run's work
    from what was already there. Say "unknown" rather than guess."""
    from agora.execution.deliverables import MAX_FILES

    workspace = _provision(tmp_path)
    for index in range(MAX_FILES + 3):
        (workspace / f"pre{index:04d}.txt").write_text(str(index), encoding="utf-8")

    baseline = baseline_state(workspace)
    assert baseline is not None and baseline[1] is False  # incomplete
    (workspace / "mine.txt").write_text("this run", encoding="utf-8")
    assert snapshot_changes(workspace, baseline) is None


def test_unreadable_baseline_makes_attribution_unknown(tmp_path):
    """A transient git error leaves `baseline_state` None. A reused worktree may
    already hold an earlier run's files, so None at this parameter must mean
    "unknown" — not "there was nothing before", which would freeze the earlier
    run's work as this run's delivery."""
    workspace = _provision(tmp_path)
    (workspace / "left-by-someone-else.txt").write_text("earlier", encoding="utf-8")
    assert snapshot_changes(workspace, None) is None
    # The explicit "nothing was there" answer is a different value, and it works.
    assert snapshot_changes(workspace, CLEAN_BASELINE) is not None


def test_truncated_snapshot_says_at_least_these(tmp_path):
    """Past MAX_FILES the list stops. It is then "at least these", so a path
    absent from it is unknown — not proof the run did not deliver it."""
    from agora.execution.deliverables import MAX_FILES

    workspace = _provision(tmp_path)
    for index in range(MAX_FILES + 5):
        (workspace / f"f{index:04d}.txt").write_text(str(index), encoding="utf-8")

    snap = snapshot_changes(workspace, CLEAN_BASELINE)
    assert snap["truncated"] is True
    assert len(snap["files"]) == MAX_FILES


# ── store: None and [] must not collapse ──────────────────────────────

def test_store_keeps_empty_and_missing_snapshots_distinct(tmp_path):
    tasks, store, dispatcher, _ = _system(tmp_path, [sys.executable, "-c", "print(1)"])
    task = _planned_task(tasks)

    def queue_and_start(prompt: str):
        # Only the first queue moves the task PLANNED -> RUNNING, so read the
        # live version each time rather than assuming it keeps incrementing.
        current = tasks.get(task.task_id)
        run = dispatcher.queue(CreateRunRequest(
            task_id=task.task_id, adapter="codex", prompt=prompt,
            expected_task_version=current.version))
        return store.start(run.run_id, expected_version=run.version)

    delivered = queue_and_start("delivers a file")
    store.finish(delivered.run_id, RunState.SUCCEEDED, expected_version=delivered.version,
                 exit_code=0, stdout_tail="", stderr_tail="",
                 deliverables={"files": [{"path": "a.txt", "status": "added",
                                          "size": 3, "sha256": None}],
                               "truncated": False})
    assert store.deliverable_snapshot(delivered.run_id) == {
        "files": [{"path": "a.txt", "status": "added", "size": 3, "sha256": None}],
        "truncated": False,
    }

    nothing = queue_and_start("changes nothing")
    store.finish(nothing.run_id, RunState.SUCCEEDED, expected_version=nothing.version,
                 exit_code=0, stdout_tail="", stderr_tail="",
                 deliverables={"files": [], "truncated": False})
    # "changed nothing" is knowledge, and must survive as empty rather than None.
    assert store.deliverable_snapshot(nothing.run_id) == {"files": [], "truncated": False}

    legacy = queue_and_start("no snapshot taken")
    store.finish(legacy.run_id, RunState.SUCCEEDED, expected_version=legacy.version,
                 exit_code=0, stdout_tail="", stderr_tail="")
    assert store.deliverable_snapshot(legacy.run_id) is None

    with pytest.raises(RunNotFoundError):
        store.deliverable_snapshot("run_does_not_exist")


def test_schema_migration_adds_the_column_to_an_existing_database(tmp_path):
    import sqlite3
    from agora.execution.schema import initialize_execution_schema

    db_path = tmp_path / "legacy.db"
    TaskStore(db_path)  # creates the tasks table, and today's execution_runs
    with sqlite3.connect(str(db_path)) as db:
        # Rebuild execution_runs as it looked before the snapshot column, so the
        # migration runs against a real pre-existing table.
        db.execute("DROP TABLE execution_runs")
        db.execute("""
            CREATE TABLE execution_runs (
                run_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(task_id),
                project_id TEXT NOT NULL, adapter TEXT NOT NULL, state TEXT NOT NULL,
                prompt TEXT NOT NULL, workspace TEXT NOT NULL,
                command TEXT NOT NULL DEFAULT '[]', timeout_seconds INTEGER NOT NULL,
                pid INTEGER, exit_code INTEGER,
                stdout_tail TEXT NOT NULL DEFAULT '', stderr_tail TEXT NOT NULL DEFAULT '',
                result_metadata TEXT NOT NULL DEFAULT '{}', error_message TEXT,
                version INTEGER NOT NULL DEFAULT 1, queued_at TEXT NOT NULL,
                started_at TEXT, finished_at TEXT, actor TEXT NOT NULL
            )
        """)
        assert "deliverable_snapshot" not in {
            row[1] for row in db.execute("PRAGMA table_info(execution_runs)")
        }
        initialize_execution_schema(db)
        assert "deliverable_snapshot" in {
            row[1] for row in db.execute("PRAGMA table_info(execution_runs)")
        }
        initialize_execution_schema(db)  # idempotent


# ── the endpoint: the actual D-1 regression ───────────────────────────

def _client(tmp_path, command):
    tasks, store, dispatcher, projects = _system(tmp_path, command)
    app.dependency_overrides[get_task_store] = lambda: tasks
    app.dependency_overrides[get_execution_store] = lambda: store
    app.dependency_overrides[get_execution_dispatcher] = lambda: dispatcher
    app.dependency_overrides[get_project_registry] = lambda: projects
    return tasks, store, dispatcher, TestClient(app)


def test_finished_run_keeps_its_files_after_the_worktree_moves_on(tmp_path):
    """The D-1 regression: commit the run's work, then have a later run change
    something else. The first run's card must still show what *it* delivered."""
    import asyncio

    tasks, store, dispatcher, client = _client(tmp_path, _writer("first.txt", "one"))
    try:
        task = _planned_task(tasks)
        run = dispatcher.queue(CreateRunRequest(
            task_id=task.task_id, adapter="codex", prompt="write first",
            timeout_seconds=20, expected_task_version=2))
        finished = asyncio.run(dispatcher._execute_guarded(run.run_id))
        assert finished.state == RunState.SUCCEEDED

        first = client.get(f"/api/runs/{run.run_id}/deliverables").json()
        assert first["source"] == "snapshot"
        assert [f["path"] for f in first["files"]] == ["first.txt"]
        assert first["captured_at"] == finished.finished_at

        # A handoff commit empties the worktree, and a later change lands in it.
        workspace = tmp_path / "workspaces" / "codex"
        _git(workspace, *_GIT_ID, "add", "-A")
        _git(workspace, *_GIT_ID, "commit", "-q", "-m", "handoff")
        (workspace / "later.txt").write_text("unrelated", encoding="utf-8")

        again = client.get(f"/api/runs/{run.run_id}/deliverables").json()
        assert again["source"] == "snapshot"
        assert [f["path"] for f in again["files"]] == ["first.txt"], (
            "a finished run must not be re-answered from the current worktree"
        )
    finally:
        app.dependency_overrides.clear()


def test_terminal_run_without_a_snapshot_says_so_instead_of_guessing(tmp_path):
    tasks, store, dispatcher, client = _client(tmp_path, [sys.executable, "-c", "print(1)"])
    try:
        task = _planned_task(tasks)
        run = dispatcher.queue(CreateRunRequest(
            task_id=task.task_id, adapter="codex", prompt="p", expected_task_version=2))
        run = store.start(run.run_id, expected_version=run.version)
        store.finish(run.run_id, RunState.SUCCEEDED, expected_version=run.version,
                     exit_code=0, stdout_tail="", stderr_tail="")  # legacy: no snapshot

        # Put something in the worktree that a live listing would have reported.
        (tmp_path / "workspaces" / "codex" / "decoy.txt").write_text("x", encoding="utf-8")

        body = client.get(f"/api/runs/{run.run_id}/deliverables").json()
        assert body["source"] == "none"
        assert body["files"] == []
        assert "without a deliverable snapshot" in body["unavailable"]
        assert "decoy.txt" not in str(body), "must not leak the live worktree listing"
    finally:
        app.dependency_overrides.clear()


def test_a_file_rewritten_after_the_run_cannot_be_opened_under_it(tmp_path):
    """The digest is recorded so it can be checked. A later run rewriting the
    same path must not make the earlier run's card serve the new bytes."""
    import asyncio

    tasks, store, dispatcher, client = _client(tmp_path, _writer("shared.txt", "from run one"))
    try:
        task = _planned_task(tasks)
        run = dispatcher.queue(CreateRunRequest(
            task_id=task.task_id, adapter="codex", prompt="write shared",
            timeout_seconds=20, expected_task_version=2))
        asyncio.run(dispatcher._execute_guarded(run.run_id))

        ok = client.get(f"/api/runs/{run.run_id}/deliverables/content",
                        params={"path": "shared.txt"})
        assert ok.status_code == 200 and "from run one" in ok.json()["content"]

        # A later run rewrites the same path; the worktree stays a valid worktree.
        (tmp_path / "workspaces" / "codex" / "shared.txt").write_text(
            "from run two", encoding="utf-8")

        stale = client.get(f"/api/runs/{run.run_id}/deliverables/content",
                           params={"path": "shared.txt"})
        assert stale.status_code == 409
        assert "no longer holds" in stale.json()["detail"]
        raw = client.get(f"/api/runs/{run.run_id}/deliverables/raw",
                         params={"path": "shared.txt"})
        assert raw.status_code == 409

        # A path this run never delivered is not openable under it either.
        (tmp_path / "workspaces" / "codex" / "someone-else.txt").write_text("z", encoding="utf-8")
        other = client.get(f"/api/runs/{run.run_id}/deliverables/content",
                           params={"path": "someone-else.txt"})
        assert other.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_cancelled_run_keeps_what_it_already_delivered(tmp_path):
    """A run cancelled after it wrote files still delivered them; its record
    must not read "unknown"."""
    import asyncio

    tasks, store, dispatcher, client = _client(tmp_path, _writer("partial.txt", "half done"))
    try:
        task = _planned_task(tasks)
        run = dispatcher.queue(CreateRunRequest(
            task_id=task.task_id, adapter="codex", prompt="write then get cancelled",
            timeout_seconds=20, expected_task_version=2))
        started = store.start(run.run_id, expected_version=run.version)
        # Cancel while "running", then let the dispatcher settle the output. The
        # cancelled branch is what previously skipped the snapshot entirely.
        from agora.execution.models import CancelRunRequest
        store.cancel(started.run_id, CancelRunRequest(expected_version=started.version))
        (tmp_path / "workspaces" / "codex" / "partial.txt").write_text("half done", encoding="utf-8")
        store.record_cancelled_output(
            started.run_id, expected_version=store.require(started.run_id).version,
            stdout_tail="", stderr_tail="", exit_code=None,
            deliverables=snapshot_changes(tmp_path / "workspaces" / "codex", CLEAN_BASELINE),
        )

        body = client.get(f"/api/runs/{run.run_id}/deliverables").json()
        assert body["source"] == "snapshot"
        assert [f["path"] for f in body["files"]] == ["partial.txt"]
    finally:
        app.dependency_overrides.clear()


def test_runs_sharing_a_worktree_are_serialized(tmp_path):
    """Workspaces are keyed by adapter, so two runs of the same project+adapter
    resolve to one directory while max_concurrent_per_project defaults to 2.
    Without a per-worktree slot they would interleave and each snapshot would
    claim the other's files."""
    import asyncio

    tasks, store, dispatcher, _ = _system(
        tmp_path,
        [sys.executable, "-c",
         "import os,time;p=str(os.getpid());open(p+'.txt','w').write(p);time.sleep(0.35)"],
    )
    task = _planned_task(tasks)
    # Two tasks: the same project+adapter resolves to one worktree either way,
    # and a single task holds at most one unfinished worker run.
    other = _planned_task(tasks)
    first = dispatcher.queue(CreateRunRequest(
        task_id=task.task_id, adapter="codex", prompt="a",
        timeout_seconds=30, expected_task_version=2))
    second = dispatcher.queue(CreateRunRequest(
        task_id=other.task_id, adapter="codex", prompt="b",
        timeout_seconds=30, expected_task_version=2))

    async def both():
        return await asyncio.gather(
            dispatcher._execute_guarded(first.run_id),
            dispatcher._execute_guarded(second.run_id),
        )

    asyncio.run(both())

    a = (store.deliverable_snapshot(first.run_id) or {}).get("files")
    b = (store.deliverable_snapshot(second.run_id) or {}).get("files")
    assert a is not None and b is not None
    # Each run wrote exactly one file named after its own pid. Serialization plus
    # the baseline means neither claims the other's.
    assert len(a) == 1, f"first run claimed {[f['path'] for f in a]}"
    assert len(b) == 1, f"second run claimed {[f['path'] for f in b]}"
    assert a[0]["path"] != b[0]["path"]


def test_a_file_with_no_recorded_digest_cannot_be_opened(tmp_path):
    """No digest means no verifiable identity (deleted, not a regular file, over
    the hash limit). A later run can recreate the path, so fail closed."""
    tasks, store, dispatcher, client = _client(tmp_path, [sys.executable, "-c", "print(1)"])
    try:
        task = _planned_task(tasks)
        run = dispatcher.queue(CreateRunRequest(
            task_id=task.task_id, adapter="codex", prompt="p", expected_task_version=2))
        run = store.start(run.run_id, expected_version=run.version)
        store.finish(
            run.run_id, RunState.SUCCEEDED, expected_version=run.version, exit_code=0,
            stdout_tail="", stderr_tail="",
            # Recorded as delivered, but with nothing to verify against.
            deliverables={"files": [{"path": "ghost.txt", "status": "added",
                                     "size": None, "sha256": None}],
                          "truncated": False},
        )
        # Something now exists at that path — a later run recreating it.
        (tmp_path / "workspaces" / "codex" / "ghost.txt").write_text("recreated", encoding="utf-8")

        body = client.get(f"/api/runs/{run.run_id}/deliverables/content",
                          params={"path": "ghost.txt"})
        assert body.status_code == 409
        assert "no verifiable content" in body.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_abandoned_run_keeps_what_it_already_delivered(tmp_path):
    """A run the dispatcher stops mid-flight may already have written files;
    its record must not read "unknown" for work sitting on disk."""
    import asyncio

    tasks, store, dispatcher, client = _client(
        tmp_path,
        [sys.executable, "-c",
         "import time;open('written.txt','w').write('before cancel');time.sleep(30)"],
    )
    try:
        task = _planned_task(tasks)
        run = dispatcher.queue(CreateRunRequest(
            task_id=task.task_id, adapter="codex", prompt="write then hang",
            timeout_seconds=60, expected_task_version=2))

        async def start_then_cancel():
            job = asyncio.create_task(dispatcher._execute_guarded(run.run_id))
            for _ in range(80):  # wait for the file to land, then cancel
                await asyncio.sleep(0.05)
                if (tmp_path / "workspaces" / "codex" / "written.txt").exists():
                    break
            job.cancel()
            with pytest.raises(asyncio.CancelledError):
                await job

        asyncio.run(start_then_cancel())

        settled = store.require(run.run_id)
        assert settled.state == RunState.ABANDONED
        body = client.get(f"/api/runs/{run.run_id}/deliverables").json()
        assert body["source"] == "snapshot"
        assert [f["path"] for f in body["files"]] == ["written.txt"]
    finally:
        app.dependency_overrides.clear()


def test_active_run_is_answered_from_the_live_worktree(tmp_path):
    tasks, store, dispatcher, client = _client(tmp_path, [sys.executable, "-c", "print(1)"])
    try:
        task = _planned_task(tasks)
        run = dispatcher.queue(CreateRunRequest(
            task_id=task.task_id, adapter="codex", prompt="p", expected_task_version=2))
        run = store.start(run.run_id, expected_version=run.version)
        (tmp_path / "workspaces" / "codex" / "in-flight.txt").write_text("y", encoding="utf-8")

        body = client.get(f"/api/runs/{run.run_id}/deliverables").json()
        assert body["source"] == "live"
        assert [f["path"] for f in body["files"]] == ["in-flight.txt"]
    finally:
        app.dependency_overrides.clear()
