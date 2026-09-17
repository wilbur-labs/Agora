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

import asyncio

from dataclasses import dataclass, field
from pathlib import Path

from agora.environments import CommandResult, Environment, LocalEnvironment

from agora.tasks.models import AppendEventRequest, TaskManifest, TaskState
from agora.tasks.store import TaskStore

from agora.projects import ProjectRegistry
from agora.workspaces.naming import worktree_branch

from .deliverables import _GIT, list_changes, provisioned_worktree_check, ready_worktree_check
from .dispatcher import ROLE_KEY, ROLE_WORKER
from .remote_wiring import RemoteTargetMismatch, adapter_for_recorded_target, remote_components_for, remote_target_of
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


def _run_git(env: Environment, workspace: Path, *args: str) -> CommandResult:
    return env.run([_GIT, "-C", str(workspace), *args], timeout=_GIT_TIMEOUT)


async def _remote_handoff(adapter, projects: ProjectRegistry, worker, message: str):
    """Commit a remote worker's worktree on its host: (commit, committed, files, note).
    Nothing is committed unless the worktree is confirmed to be this run's provisioned
    worktree there, and a worktree with nothing to commit says so rather than binding
    an empty commit."""
    components = remote_components_for(adapter, projects)
    try:
        ws_path = Path(worker.workspace)
        expected_branch = worktree_branch(worker.project_id, worker.adapter)
        try:
            ready = await components.provisioner.is_ready_worktree(
                ws_path, Path(components.config.remote_project_root), expected_branch,
            )
        except Exception as exc:  # noqa: BLE001 - reported, not raised: the task still moves on
            return None, False, [], f"could not check the remote worker workspace: {type(exc).__name__}; nothing committed"
        if not ready:
            return None, False, [], (
                "remote worker workspace could not be confirmed as this run's provisioned "
                "worktree; nothing committed"
            )
        status = await components.deliverables.status_entries(ws_path)
        if status is None:
            # "Could not read" is not "clean": nothing is committed and nothing is bound.
            return None, False, [], "could not read the remote worktree's status; nothing committed"
        pending, truncated = status
        if not pending:
            # Clean — the run committed its own work, or changed nothing. The commit the
            # worktree already has is what the task is bound to, as the local path does.
            head = await components.deliverables.head_commit(ws_path)
            note = "remote worktree was clean; bound the commit it already had" if head else (
                "remote worktree was clean and its HEAD could not be read; nothing bound"
            )
            commit, committed, files = head, False, []
        elif truncated:
            return None, False, [], (
                f"remote worktree has more than {MAX_FILES} changes; refusing to bind a handoff "
                "whose file list would be incomplete"
            )
        else:
            try:
                result = await components.deliverables.handoff_commit(ws_path, message)
            except Exception as exc:  # noqa: BLE001
                return None, False, pending, f"remote commit failed: {type(exc).__name__}: {exc}"
            files = [{"path": path, "status": "committed", "size": None} for path in result.get("files", [])]
            commit, committed, note = result.get("commit"), True, None
        if components.config.cleanup_worktree:
            # Q5=C: the adapter asked for the worktree to go once handed off. Ownership is
            # verified inside teardown; a failure is said in the note, never raised — the
            # handoff itself has happened.
            try:
                await components.provisioner.teardown_worktree(worker.project_id, worker.adapter)
            except Exception as exc:  # noqa: BLE001
                note = f"{note + '; ' if note else ''}worktree cleanup failed: {type(exc).__name__}"
            else:
                note = f"{note + '; ' if note else ''}worktree removed after handoff (cleanup_worktree)"
        return commit, committed, files, note
    finally:
        await components.aclose()


def _commit_workspace(
    workspace: Path, message: str, actor: str, *, env: Environment | None = None
) -> tuple[str | None, bool, str | None]:
    """Commit everything in the workspace worktree. Returns (commit, committed,
    note). The caller must have already confirmed the workspace is this run's
    provisioned worktree (see hand_off), so `git -C` here operates only on that
    isolated worktree, never a surrounding repository."""
    env = env or LocalEnvironment()
    # Defense in depth: hand_off has already confirmed full provenance, but this
    # shape guard ensures we never `git add -A` a repo root / project root even if
    # called directly on an unexpected workspace.
    #
    # Two answers, not one. This note is written into the handoff record and read by
    # a person: "workspace is not a provisioned worktree" is a verdict about their
    # workspace, and reporting a missing git that way would send them to fix
    # something that is not broken.
    provisioned, unavailable = provisioned_worktree_check(env, workspace)
    if unavailable is not None:
        return None, False, unavailable
    if not provisioned:
        # Same wording as hand_off's, and for the same reason: git ran and the check
        # did not pass, which is not the same as having established that this is not
        # a worktree (exit 128 also covers safe.directory and an unreadable .git).
        return None, False, (
            "workspace could not be confirmed as a provisioned worktree"
        )
    added = _run_git(env, workspace, "add", "-A")
    if not added.ok:
        # `git add -A` exits zero even with nothing to add, so a non-zero exit (or a
        # timeout) means staging did not finish. Carrying on from here would commit
        # whatever the index happened to hold and record it as this run's delivery.
        if not added.ran:
            return None, False, f"git is not available ({added.reason()})"
        return None, False, f"git add failed ({added.reason()})"
    status = _run_git(env, workspace, "status", "--porcelain")
    if not status.ok:
        # "could not read the worktree" is not "the worktree is clean"; the second
        # would silently record a handoff that committed nothing.
        return None, False, f"git status failed ({status.reason()})"
    committed = False
    if status.stdout.strip():
        commit_proc = _run_git(
            env, workspace,
            "-c", f"user.name={actor}", "-c", f"user.email={_COMMIT_EMAIL}",
            "commit", "-m", message,
        )
        committed = commit_proc.ok
        if not committed:
            detail = commit_proc.stderr.strip()[:200] or commit_proc.reason()
            return None, False, f"git commit failed: {detail}"
    head = _run_git(env, workspace, "rev-parse", "HEAD")
    sha = head.stdout.strip() if head.ok else ""
    if sha:
        return sha, committed, None
    if committed:
        # A commit was just made, so a HEAD exists whatever this read says.
        return None, True, (
            f"committed, but the resulting HEAD could not be read ({head.reason()})"
        )
    # No sha, and nothing established about why. Every attempt to narrow this down
    # failed on the same ambiguity: exit 128 covers an unborn branch, a corrupted or
    # unreadable ref, and a revoked .git. Reading HEAD with `symbolic-ref` was tried
    # and does not settle it either -- it succeeds whenever HEAD names a branch,
    # including one whose ref is broken, so it would have reported "no commits" for a
    # repository that has them. Nothing cheap distinguishes the cases, an Agora
    # worktree always has a HEAD anyway (`ensure_source_repo` seeds one before
    # provisioning), and this note is permanent -- so it says what was observed.
    return None, False, f"could not read the workspace HEAD ({head.reason()})"


def hand_off(
    task_store: TaskStore,
    exec_store: ExecutionStore,
    projects: ProjectRegistry,
    task_id: str,
    *,
    actor: str,
    adapters: dict | None = None,
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
    remote_target = remote_target_of(worker) if worker is not None else None
    if worker is not None and remote_target is not None:
        # The worker ran on another host (W3-a U5): the worktree to commit is there —
        # the place the run recorded, not where its adapter points today. A fresh remote
        # environment on a fresh loop: this function is synchronous and runs in a worker
        # thread, and the remote collaborators hold one connection on one loop (CR-4).
        run_id = worker.run_id
        workspace_str = worker.workspace
        try:
            pinned = adapter_for_recorded_target((adapters or {}).get(worker.adapter), remote_target)
        except RemoteTargetMismatch as exc:
            commit, committed, files, note = None, False, [], f"{exc}; nothing committed"
        else:
            commit, committed, files, note = asyncio.run(_remote_handoff(
                pinned, projects, worker, f"Deliver: {task.title}"[:500],
            ))
    elif worker is not None:
        run_id = worker.run_id
        workspace = Path(worker.workspace).expanduser().resolve()
        workspace_str = str(workspace)
        # Fail closed: only read/commit the worker run's own provisioned worktree
        # (same repo as its project, expected branch), never a surrounding or
        # unrelated repository.
        expected_branch = worktree_branch(worker.project_id, worker.adapter)
        try:
            project_root = projects.get(worker.project_id).root
        except (KeyError, LookupError):
            project_root = None
        ready, unavailable = (
            ready_worktree_check(workspace, project_root, expected_branch)
            if project_root is not None else (False, None)
        )
        if project_root is None:
            # A fourth state, and the only one whose cause is already known: the run's
            # project is gone from the registry, so no worktree check was even
            # attempted. Folding it into "could not be confirmed" points the operator
            # at a workspace that may be perfectly fine.
            note = (
                "worker project is no longer registered, so its workspace could not "
                "be checked; nothing committed"
            )
        elif ready:
            files = list_changes(workspace)
            message = f"Deliver: {task.title}"[:500]
            commit, committed, note = _commit_workspace(workspace, message, actor)
        elif unavailable is not None:
            # git could not run or could not finish. The old wording here said the
            # workspace is not a provisioned worktree, which is a verdict about the
            # operator's workspace drawn from a fact about our own tooling -- and it
            # went into a permanent handoff record on a task moving to DONE.
            note = f"could not check the worker workspace: {unavailable}; nothing committed"
        else:
            note = (
                "worker workspace could not be confirmed as this run's provisioned "
                "worktree; nothing committed"
            )
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
