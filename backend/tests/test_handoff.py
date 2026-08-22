"""Box 8 handoff: commit the deliverable, bind the commit, mark the task done."""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agora.execution.adapters import build_adapter_registry
from agora.execution.dispatcher import ExecutionDispatcher
from agora.execution.handoff import HandoffError, hand_off
from agora.execution.models import CreateRunRequest, RunState
from agora.execution.store import ExecutionStore
from agora.projects import ProjectRegistry
from agora.tasks.models import CreateTaskRequest, TaskState
from agora.tasks.store import TaskStore
from agora.workspaces.naming import worktree_branch

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git is required for handoff")


def _git(*args, cwd) -> None:
    subprocess.run([GIT, "-C", str(cwd), *args], check=True, capture_output=True, timeout=15)


def _config(tmp_path: Path, command: list[str]) -> dict:
    # The project root is a real repo; the codex workspace is a linked worktree of
    # it — the shape the provisioner produces and that handoff/deliverables require.
    root = tmp_path / "alpha"
    root.mkdir(parents=True)
    _git("init", "-q", cwd=root)
    (root / "seed.txt").write_text("seed", encoding="utf-8")
    _git("-c", "user.email=t@t", "-c", "user.name=t", "add", "-A", cwd=root)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "seed", cwd=root)
    workspace = tmp_path / "workspaces" / "codex"
    _git("worktree", "add", "-q", "-b", worktree_branch("alpha", "codex"), str(workspace), "HEAD", cwd=root)
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
            "allowed_workspace_roots": [str(tmp_path / "workspaces")],
            "adapters": {
                "codex": {"command": [*command, "{prompt}"], "workspace_key": "codex"},
                "claude": {"enabled": False},
                "kiro": {"enabled": False},
            },
        },
    }


def _system(tmp_path: Path, command: list[str]):
    config = _config(tmp_path, command)
    tasks = TaskStore(tmp_path / "agora.db")
    store = ExecutionStore(tasks)
    projects = ProjectRegistry(config)
    dispatcher = ExecutionDispatcher(
        store, projects, build_adapter_registry(config),
        allowed_workspace_roots=[tmp_path / "workspaces"],
    )
    return tasks, store, dispatcher


def _planned_task(tasks: TaskStore):
    task = tasks.create(CreateTaskRequest(project_id="alpha", title="Ship the widget"))
    with tasks._transaction() as db:
        db.execute(
            "UPDATE tasks SET state = ?, version = 2 WHERE task_id = ?",
            (TaskState.PLANNED.value, task.task_id),
        )
    return tasks.get(task.task_id)


def _verified_task_with_worker(tmp_path: Path):
    """A verified task whose succeeded worker run left a file in the workspace."""
    tasks, store, dispatcher = _system(
        tmp_path,
        [sys.executable, "-c", "open('deliver.txt', 'w').write('hello')"],
    )
    task = _planned_task(tasks)
    run = dispatcher.queue(CreateRunRequest(
        task_id=task.task_id, adapter="codex", prompt="build it",
        timeout_seconds=30, expected_task_version=2,
    ))
    finished = asyncio.run(dispatcher.execute(run.run_id))
    assert finished.state == RunState.SUCCEEDED
    # queue moved planned -> running; walk running -> review -> verified.
    current = tasks.get(task.task_id)
    current = tasks.transition(task.task_id, TaskState.REVIEW, actor="t", expected_version=current.version)
    current = tasks.transition(task.task_id, TaskState.VERIFIED, actor="t", expected_version=current.version)
    return tasks, store, dispatcher.projects, current


def test_handoff_commits_deliverable_and_completes(tmp_path):
    tasks, store, projects, task = _verified_task_with_worker(tmp_path)

    result = hand_off(tasks, store, projects, task.task_id, actor="wilbur")

    assert result.task.state == TaskState.DONE
    assert result.committed is True
    assert result.commit and len(result.commit) == 40
    assert [f["path"] for f in result.files] == ["deliver.txt"]

    # The commit really landed and the worktree is clean.
    workspace = Path(result.workspace)
    head = subprocess.run([GIT, "-C", str(workspace), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True)
    assert head.stdout.strip() == result.commit
    status = subprocess.run([GIT, "-C", str(workspace), "status", "--porcelain"],
                            capture_output=True, text=True, check=True)
    assert status.stdout.strip() == ""

    # An append-only handoff record binds the commit to the task.
    events = [e for e in tasks.events(task.task_id) if e.event_type == "handoff.completed"]
    assert len(events) == 1
    assert events[0].payload["commit"] == result.commit
    assert events[0].payload["files"] == ["deliver.txt"]


def test_handoff_requires_verified_task(tmp_path):
    tasks, store, projects, task = _verified_task_with_worker(tmp_path)
    hand_off(tasks, store, projects, task.task_id, actor="wilbur")  # now DONE

    with pytest.raises(HandoffError, match="verified"):
        hand_off(tasks, store, projects, task.task_id, actor="wilbur")


def test_handoff_without_worker_run_still_completes(tmp_path):
    tasks, store, dispatcher = _system(tmp_path, [sys.executable, "-c", "pass"])
    task = tasks.create(CreateTaskRequest(project_id="alpha", title="No runs"))
    # Force straight to verified without any run.
    with tasks._transaction() as db:
        db.execute("UPDATE tasks SET state = ?, version = 2 WHERE task_id = ?",
                   (TaskState.VERIFIED.value, task.task_id))

    result = hand_off(tasks, store, dispatcher.projects, task.task_id, actor="wilbur")

    assert result.task.state == TaskState.DONE
    assert result.commit is None and result.committed is False
    assert result.note and "no succeeded worker run" in result.note


def test_handoff_skips_commit_when_worker_worktree_is_wrong_branch(tmp_path):
    """If the worker's workspace is not this project/adapter's provisioned
    worktree (here: right repo, wrong branch), handoff commits nothing."""
    tasks, store, projects, task = _verified_task_with_worker(tmp_path)
    workspace = projects.get("alpha").workspaces["codex"]
    _git("checkout", "-q", "-b", "some/wrong-branch", cwd=workspace)

    result = hand_off(tasks, store, projects, task.task_id, actor="wilbur")

    assert result.task.state == TaskState.DONE
    assert result.commit is None and result.committed is False
    assert result.note and "provisioned worktree" in result.note


def test_commit_workspace_never_touches_a_repo_root(tmp_path):
    """Safety: a workspace that is a repo root / project root (not a linked
    worktree) must never be staged or committed, so an outer repository can
    never be swept into a handoff commit."""
    from agora.execution.handoff import _commit_workspace

    repo = tmp_path / "outer"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    (repo / "seed.txt").write_text("seed", encoding="utf-8")
    _git("-c", "user.email=t@t", "-c", "user.name=t", "add", "-A", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "seed", cwd=repo)
    (repo / "unmanaged.txt").write_text("must not be committed", encoding="utf-8")
    before = subprocess.run([GIT, "-C", str(repo), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=True).stdout.strip()

    commit, committed, note = _commit_workspace(repo, "should not happen", "wilbur")

    assert commit is None and committed is False
    assert note and "worktree" in note
    after = subprocess.run([GIT, "-C", str(repo), "rev-parse", "HEAD"],
                           capture_output=True, text=True, check=True).stdout.strip()
    assert after == before  # outer repo HEAD untouched
    # The index is empty — nothing was staged — and the file is still untracked.
    assert subprocess.run([GIT, "-C", str(repo), "diff", "--cached", "--quiet"]).returncode == 0
    status = subprocess.run([GIT, "-C", str(repo), "status", "--porcelain"],
                            capture_output=True, text=True, check=True).stdout.strip()
    assert status == "?? unmanaged.txt"
