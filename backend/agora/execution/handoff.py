"""Box 8: hand off a verified task's deliverable and mark it done.

Completing a task is more than a status flip. This commits the worker run's
deliverables inside its isolated workspace repository, binds the resulting
commit hash and file list to the task as an append-only handoff record, and
advances ``verified -> done``. Binding delivery to an immutable commit — rather
than a mutable worktree or an ambiguous cross-repo copy — matches the isolated
per-workspace git design.

A handoff binds a commit or it does not happen (CR-91, 2026-09-19: a completion
that committed nothing was a defect, not a semantic). When the workspace cannot be
checked, is not this run's provisioned worktree, or the commit fails — locally or on
the remote host — the attempt is recorded as ``handoff.refused`` with the reason, the
task stays ``verified`` so it can be retried once the cause is fixed, and the caller
gets :class:`HandoffNotCommitted`. A clean worktree is not a refusal: the run committed
its own work, and the commit it already has is what the task is bound to.
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

from .deliverables import MAX_FILES, _GIT, _read_changes, digest_of, provisioned_worktree_check, ready_worktree_check
from .dispatcher import ROLE_KEY, ROLE_WORKER
from .remote_wiring import RemoteTargetMismatch, adapter_for_recorded_target, remote_components_for, remote_target_of
from .models import RunState
from .store import ExecutionStore

_GIT_TIMEOUT = 30
_COMMIT_EMAIL = "agora@localhost"
_MAX_RECORDED_FILES = 200


class HandoffError(Exception):
    """A handoff precondition failed (e.g. the task is not verified)."""


class HandoffNotCommitted(HandoffError):
    """The handoff bound no commit, so the task was not completed (CR-91).

    ``note`` is the reason in the words the record carries; the task is still
    ``verified`` and the same call can be made again once the cause is fixed.
    """

    code = "handoff_not_committed"

    def __init__(self, note: str, *, run_id: str | None, workspace: str | None) -> None:
        super().__init__(note)
        self.note = note
        self.run_id = run_id
        self.workspace = workspace


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
            note = (
                "remote worktree was clean; bound the commit it already had (not checked "
                "against the run's delivery snapshot on the remote host — CR-94)"
            ) if head else (
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
        return commit, committed, files, note
    finally:
        await components.aclose()


async def _remote_cleanup(adapter, projects: ProjectRegistry, worker) -> str | None:
    """Q5=C: the adapter asked for the worktree to go once handed off. Called only after
    the task is DONE — before that the worktree is the retry: a handoff whose record or
    transition failed after a teardown left a verified task with nothing to hand off
    again (review, 2026-09-19). Ownership is verified inside teardown; a failure is said
    in the note, never raised, because the handoff itself has happened."""
    components = remote_components_for(adapter, projects)
    try:
        if not components.config.cleanup_worktree:
            return None
        try:
            await components.provisioner.teardown_worktree(worker.project_id, worker.adapter)
        except Exception as exc:  # noqa: BLE001
            return f"worktree cleanup failed: {type(exc).__name__}"
        return "worktree removed after handoff (cleanup_worktree)"
    finally:
        await components.aclose()


def _no_delivery_reason(exec_store: ExecutionStore, run_id: str) -> str | None:
    """Why binding the worktree's existing commit would bind nothing of this run's.

    A clean worktree has two stories: the run committed its own work, or the run
    changed nothing and HEAD is still the commit provisioning left there. The commit
    alone cannot tell them apart; the run's frozen delivery snapshot can. None means
    the run is known to have delivered something (or more than could be listed); a
    string is the refusal.
    """
    snapshot = exec_store.deliverable_snapshot(run_id)
    if snapshot is None:
        return (
            "the worktree is clean but the run has no delivery snapshot, so whether it "
            "changed anything could not be established; nothing bound — run the step "
            "again to produce a fresh snapshot"
        )
    if snapshot.get("files") or snapshot.get("truncated"):
        return None
    if snapshot.get("commits_unread"):
        # The snapshot could not compare commit history, so "no files" is not "changed
        # nothing": the run may have committed work the list does not show. Unknown,
        # said as unknown — and the snapshot is frozen, so the way out is a new run.
        return (
            "the worktree is clean and the run's snapshot lists no files, but its commit "
            "history could not be read when it ended, so whether it delivered anything "
            "is unknown; nothing bound — run the step again to produce a fresh snapshot"
        )
    return "the run changed nothing (its delivery snapshot is empty); nothing to hand off"


def _delivery_gone_reason(workspace: Path, snapshot: dict) -> str | None:
    """Why the clean worktree no longer holds what the run delivered, or None.

    A non-empty snapshot says what the run delivered *when it ended*; the worktree may
    have been reset or reverted since (review, 2026-09-19), and binding its HEAD then
    binds a commit without the work. Each snapshot row is checked as far as the row
    allows: a row with a digest must hash to it now; a row the run deleted must still
    be absent; a row the run committed itself (no digest recorded) must at least still
    be there. What this does not catch is said in CR-94: a reset to some *other* commit
    that happens to hold the same paths.
    """
    for row in snapshot.get("files") or []:
        path = str(row.get("path") or "")
        if not path:
            continue
        expected = row.get("sha256")
        if expected:
            if digest_of(workspace, path) != expected:
                return f"the worktree no longer holds what the run delivered ({path} differs or is gone); nothing bound"
            continue
        # Two ways a row says "the run removed this": the baseline path that stopped
        # being reported (`gone_since_baseline`), and a deletion the run committed
        # itself (status "deleted", no digest — review round 3: demanding presence
        # there refused a legitimate handoff on every retry).
        gone_by_run = (
            (bool(row.get("gone_since_baseline")) and row.get("exists") is False)
            or str(row.get("status") or "") == "deleted"
        )
        try:
            present = (workspace / path).lstat() is not None
        except FileNotFoundError:
            present = False
        except OSError:
            return f"the worktree could not be checked against the run's delivery ({path} unreadable); nothing bound"
        if gone_by_run and present:
            return f"the worktree no longer matches the run's delivery ({path} is back); nothing bound"
        if not gone_by_run and not present:
            return f"the worktree no longer holds what the run delivered ({path} is gone); nothing bound"
    return None


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
    environments=None,
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
    pinned = None
    if worker is not None and remote_target is not None:
        # The worker ran on another host (W3-a U5): the worktree to commit is there —
        # the place the run recorded, not where its adapter points today. A fresh remote
        # environment on a fresh loop: this function is synchronous and runs in a worker
        # thread, and the remote collaborators hold one connection on one loop (CR-4).
        run_id = worker.run_id
        workspace_str = worker.workspace
        try:
            pinned = adapter_for_recorded_target(
                (adapters or {}).get(worker.adapter), remote_target, environments,
            )
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
            # The record's file list is read with its failure kept apart from "no
            # changes" (review round 3: the best-effort reader returned [] on a
            # transient error and the record then said the commit held no files).
            read = _read_changes(workspace, LocalEnvironment())
            files = read[0] if read is not None else []
            message = f"Deliver: {task.title}"[:500]
            commit, committed, note = _commit_workspace(workspace, message, actor)
            if read is None and commit is not None:
                note = (
                    f"{note + '; ' if note else ''}the file list could not be read before "
                    "committing, so this record lists no files; the commit itself holds them"
                )
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

    if commit is not None and not committed and worker is not None:
        # Binding a commit that was already there is a delivery only if this run put
        # something in it (or committed it itself). A run that changed nothing would
        # otherwise be handed off on provisioning's seed commit — DONE with nothing
        # delivered, the very thing CR-91 refuses.
        reason = _no_delivery_reason(exec_store, worker.run_id)
        if reason is None and remote_target is None:
            # And the delivery must still be *in* the worktree whose HEAD is bound.
            # Local only: the remote host has no digest reader yet (CR-94, `defer`).
            reason = _delivery_gone_reason(
                Path(workspace_str), exec_store.deliverable_snapshot(worker.run_id) or {},
            )
        if reason is not None:
            commit, note = None, reason

    payload = {
        "run_id": run_id,
        "workspace": workspace_str,
        "commit": commit,
        "committed": committed,
        "file_count": len(files),
        "files": [f["path"] for f in files][:_MAX_RECORDED_FILES],
        "note": note,
    }
    if commit is None:
        # Nothing bound, so nothing completed (CR-91). The attempt and its reason go on
        # the record — a person reading the task later must see that a handoff was
        # tried and why it did not take — but the task stays verified, and the caller
        # is told rather than handed a DONE that delivered nothing.
        task_store.append_event(task_id, AppendEventRequest(
            event_type="handoff.refused", actor=actor, payload=payload,
        ))
        raise HandoffNotCommitted(
            note or "nothing committed", run_id=run_id, workspace=workspace_str,
        )

    # The transition first, the record second. They are two writes, and the one that
    # can be refused is the transition (a version moved under us): with the record
    # written first, that refusal left a `handoff.completed` on a task still verified,
    # and a retry appended a second one. Written this way round, a refused transition
    # writes nothing; and should the record's insert fail after DONE, the transition's
    # own reason still names the commit (review, 2026-09-19).
    reason = f"Handoff by {actor}"
    if commit:
        reason += f" (commit {commit[:12]})"
    done = task_store.transition(
        task_id, TaskState.DONE, actor=actor, reason=reason,
        expected_version=task.version,
    )
    task_store.append_event(task_id, AppendEventRequest(
        event_type="handoff.completed", actor=actor, payload=payload,
    ))

    if pinned is not None:
        # Only now, with the task DONE and the record written, may the remote worktree
        # go. The outcome travels in the result, not the record: the record is of the
        # delivery, and the worktree's absence is observable on its own. Nothing from
        # here may fail the request — the handoff has happened — so the connection's
        # own errors (opening it, closing it) are caught along with the teardown's.
        try:
            cleanup = asyncio.run(_remote_cleanup(pinned, projects, worker))
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            cleanup = f"worktree cleanup could not run: {type(exc).__name__}"
        if cleanup:
            note = f"{note + '; ' if note else ''}{cleanup}"

    return HandoffResult(
        task=done, run_id=run_id, workspace=workspace_str, commit=commit,
        committed=committed, files=files, note=note,
    )
