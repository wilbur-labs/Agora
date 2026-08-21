"""Box 8: hand off a verified task's deliverable and mark it done.

Completing a task is more than a status flip. This commits the worker run's
deliverables inside its isolated workspace repository, binds the resulting
commit hash and file list to the task as an append-only handoff record, and
advances ``verified -> done``. Binding delivery to an immutable commit — rather
than a mutable worktree or an ambiguous cross-repo copy — matches the isolated
per-workspace git design.

Best-effort on the git side: a missing git binary, a non-repo workspace, or an
empty worktree never blocks completion; the handoff is recorded with whatever
commit (if any) the workspace already has.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from agora.tasks.models import AppendEventRequest, TaskManifest, TaskState
from agora.tasks.store import TaskStore

from .deliverables import list_changes
from .dispatcher import ROLE_KEY, ROLE_WORKER
from .models import RunState
from .store import ExecutionStore

_GIT_TIMEOUT = 30
_COMMIT_EMAIL = "agora@localhost"
_MAX_RECORDED_FILES = 200


class HandoffError(Exception):
    """A handoff precondition failed (e.g. the task is not verified)."""


@dataclass
class HandoffResult:
    task: TaskManifest
    run_id: str | None = None
    workspace: str | None = None
    commit: str | None = None
    committed: bool = False
    files: list[dict] = field(default_factory=list)
    note: str | None = None


def _latest_worker_run(exec_store: ExecutionStore, task_id: str):
    """The most recent succeeded worker run for the task (its deliverables)."""
    for summary in exec_store.list(task_id=task_id, limit=500):
        run = exec_store.require(summary.run_id)
        role = str((run.result_metadata or {}).get(ROLE_KEY, ROLE_WORKER))
        if role == ROLE_WORKER and run.state == RunState.SUCCEEDED:
            return run
    return None


def _run_git(git: str, workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [git, "-C", str(workspace), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=_GIT_TIMEOUT, check=False,
    )


def _commit_workspace(workspace: Path, message: str, actor: str) -> tuple[str | None, bool, str | None]:
    """Commit everything in the workspace repo. Returns (commit, committed, note)."""
    git = shutil.which("git")
    if git is None:
        return None, False, "git is not available"
    if not (workspace / ".git").exists():
        return None, False, "workspace is not a git repository"
    try:
        _run_git(git, workspace, "add", "-A")
        status = _run_git(git, workspace, "status", "--porcelain")
        committed = False
        if status.stdout.strip():
            commit_proc = _run_git(
                git, workspace,
                "-c", f"user.name={actor}", "-c", f"user.email={_COMMIT_EMAIL}",
                "commit", "-m", message,
            )
            committed = commit_proc.returncode == 0
            if not committed:
                return None, False, f"git commit failed: {commit_proc.stderr.strip()[:200]}"
        head = _run_git(git, workspace, "rev-parse", "HEAD")
        commit = head.stdout.strip() if head.returncode == 0 else None
        note = None if commit else "workspace has no commits"
        return commit, committed, note
    except (OSError, subprocess.SubprocessError) as exc:
        return None, False, f"git error: {exc}"


def hand_off(
    task_store: TaskStore,
    exec_store: ExecutionStore,
    task_id: str,
    *,
    actor: str,
) -> HandoffResult:
    """Commit the deliverable, bind the commit to the task, and mark it done."""
    task = task_store.get(task_id)
    if task is None:
        raise HandoffError(f"Task not found: {task_id}")
    if task.state != TaskState.VERIFIED:
        raise HandoffError(
            f"Handoff requires a verified task; task is {task.state.value}"
        )

    worker = _latest_worker_run(exec_store, task_id)
    run_id = workspace_str = commit = note = None
    committed = False
    files: list[dict] = []
    if worker is not None:
        run_id = worker.run_id
        workspace = Path(worker.workspace).expanduser().resolve()
        workspace_str = str(workspace)
        files = list_changes(workspace)
        message = f"Deliver: {task.title}"[:500]
        commit, committed, note = _commit_workspace(workspace, message, actor)
    else:
        note = "no succeeded worker run; nothing to commit"

    task_store.append_event(task_id, AppendEventRequest(
        event_type="handoff.completed",
        actor=actor,
        payload={
            "run_id": run_id,
            "workspace": workspace_str,
            "commit": commit,
            "committed": committed,
            "file_count": len(files),
            "files": [f["path"] for f in files][:_MAX_RECORDED_FILES],
            "note": note,
        },
    ))

    reason = f"Handoff by {actor}"
    if commit:
        reason += f" (commit {commit[:12]})"
    done = task_store.transition(
        task_id, TaskState.DONE, actor=actor, reason=reason,
        expected_version=task.version,
    )

    return HandoffResult(
        task=done, run_id=run_id, workspace=workspace_str, commit=commit,
        committed=committed, files=files, note=note,
    )
