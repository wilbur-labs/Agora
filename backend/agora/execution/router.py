"""REST API for durable delivery execution runs."""
from __future__ import annotations

import mimetypes

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path as PathParam, Query, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response

from pydantic import BaseModel

from agora.config.settings import get_config
from agora.projects import ProjectRegistry
from agora.tasks.models import TaskManifest
from agora.tasks.router import get_project_registry, get_task_store
from agora.tasks.store import (
    InvalidTransitionError,
    StaleTaskVersionError,
    TaskNotFoundError,
    TaskStore,
)

from agora.workspaces.naming import worktree_branch

from .adapters import build_adapter_registry
from .deliverables import (
    is_ready_worktree,
    list_changes,
    preview_of,
    read_bytes_verified,
    read_file,
    resolve_confined,
)
from .dispatcher import ExecutionDispatcher
from .handoff import HandoffError, hand_off
from .models import (
    AdapterCapability,
    CancelRunRequest,
    CreateRunRequest,
    ExecutionRun,
    RunState,
    RunSummary,
    TERMINAL_RUN_STATES,
)
from .store import ExecutionStore, RunConflictError, RunNotFoundError, RunValidationError


class HandoffResponse(BaseModel):
    task: TaskManifest
    run_id: str | None = None
    workspace: str | None = None
    commit: str | None = None
    committed: bool = False
    files: list[dict] = []
    note: str | None = None


router = APIRouter(tags=["execution"])


def _ready_worktree_for_run(registry: ProjectRegistry, run: ExecutionRun) -> bool:
    """Whether a run's workspace is exactly its project/adapter's provisioned
    worktree (own top-level, linked, same repo as the project, expected branch,
    HEAD). Fail closed on anything else."""
    try:
        project = registry.get(run.project_id)
    except (KeyError, LookupError):
        return False
    workspace = Path(run.workspace).expanduser().resolve()
    return is_ready_worktree(workspace, project.root, worktree_branch(run.project_id, run.adapter))


def _require_ready_worktree(registry: ProjectRegistry, run: ExecutionRun) -> None:
    """Fail closed: only serve deliverable content from a run's own provisioned
    worktree, never from a repo root, an unrelated worktree, or a plain dir."""
    if not _ready_worktree_for_run(registry, run):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deliverable not found")


def _delivered_entry(store: ExecutionStore, run: ExecutionRun, path: str) -> dict | None:
    """The snapshot entry proving this run delivered ``path``.

    Returns None only when the run has no snapshot at all — runs recorded before
    snapshots existed keep the previous behavior (the worktree gate alone).
    Otherwise it either returns a verifiable entry or refuses: a later run in the
    same worktree can rewrite or recreate a path, and serving that under the
    earlier run's card is exactly the untruth the snapshot exists to remove.
    """
    snapshot = store.deliverable_snapshot(run.run_id)
    if snapshot is None:
        if run.state in TERMINAL_RUN_STATES:
            # The list endpoint already says this run's files are unrecoverable.
            # Reading from the live worktree here would contradict it, and would
            # hand back a later run's file under this run's id.
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This run ended without a deliverable record, so no file can be "
                "attributed to it",
            )
        return None  # active run: the live worktree is the answer
    files = snapshot.get("files") or []
    entry = next(
        (item for item in files if isinstance(item, dict) and item.get("path") == path),
        None,
    )
    if entry is None:
        if snapshot.get("truncated"):
            # The recorded list stopped at MAX_FILES, so absence from it is not
            # proof the run did not deliver this file — it is simply unknown.
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This run changed more files than were recorded, so this one cannot be "
                "attributed to it",
            )
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This run did not deliver that file")
    if entry.get("sha256") is None:
        # No digest means no verifiable identity — deleted, not a regular file, or
        # above the hash limit. A later run can recreate the same path, so serving
        # whatever is there now would present its bytes as this run's delivery.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This run recorded no verifiable content for that file, so it cannot be opened",
        )
    return entry


def _verified_bytes(workspace: Path, path: str, entry: dict) -> bytes:
    """The file's bytes, read once and proven to be what the run delivered.

    Digesting one read and then serving a second (or handing a path to a response
    that opens it later) leaves a window for the file to change in between, so
    the caller must build its response from exactly these bytes.
    """
    try:
        raw = read_bytes_verified(workspace, path, str(entry["sha256"]))
    except (FileNotFoundError, PermissionError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deliverable not found") from None
    if raw is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This file changed after the run ended, so it no longer holds what the run delivered",
        )
    return raw


@lru_cache(maxsize=1)
def get_execution_store() -> ExecutionStore:
    store = ExecutionStore(get_task_store())
    store.recover_abandoned()
    return store


@lru_cache(maxsize=1)
def get_execution_dispatcher() -> ExecutionDispatcher:
    config = get_config()
    limits = config.get("execution", {})
    projects = ProjectRegistry(config)
    allowed_roots = []
    for value in limits.get("allowed_workspace_roots", []):
        root = Path(value).expanduser()
        if not root.is_absolute():
            root = projects.project_root / root
        allowed_roots.append(root.resolve())
    return ExecutionDispatcher(
        get_execution_store(),
        projects,
        build_adapter_registry(config),
        max_concurrent_global=int(limits.get("max_concurrent_global", 4)),
        max_concurrent_per_project=int(limits.get("max_concurrent_per_project", 2)),
        allowed_workspace_roots=allowed_roots,
        auto_review=bool(limits.get("auto_review", False)),
    )


@router.get("/execution-adapters", response_model=list[AdapterCapability])
def list_execution_adapters(
    dispatcher: ExecutionDispatcher = Depends(get_execution_dispatcher),
):
    return [adapter.capability() for adapter in dispatcher.adapters.values()]


@router.post("/runs", response_model=ExecutionRun, status_code=status.HTTP_201_CREATED)
async def create_run(
    request: CreateRunRequest,
    dispatcher: ExecutionDispatcher = Depends(get_execution_dispatcher),
):
    try:
        run = await run_in_threadpool(dispatcher.queue, request)
        dispatcher.schedule(run.run_id)
        return run
    except (TaskNotFoundError, RunNotFoundError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    except RunConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except RunValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


@router.get("/runs", response_model=list[RunSummary])
def list_runs(
    task_id: str | None = Query(default=None, max_length=128),
    project_id: str | None = Query(default=None, max_length=128),
    state_filter: RunState | None = Query(default=None, alias="state"),
    adapter: str | None = Query(default=None, max_length=128, pattern=r"^[a-z][a-z0-9_-]*$"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    store: ExecutionStore = Depends(get_execution_store),
):
    return store.list(
        task_id=task_id, project_id=project_id, state=state_filter,
        adapter=adapter, limit=limit, offset=offset,
    )


@router.get("/runs/{run_id}", response_model=ExecutionRun)
def get_run(
    run_id: Annotated[str, PathParam(max_length=128)],
    store: ExecutionStore = Depends(get_execution_store),
):
    try:
        return store.require(run_id)
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found") from None


@router.get("/runs/{run_id}/deliverables")
def run_deliverables(
    run_id: Annotated[str, PathParam(max_length=128)],
    store: ExecutionStore = Depends(get_execution_store),
    dispatcher: ExecutionDispatcher = Depends(get_execution_dispatcher),
    registry: ProjectRegistry = Depends(get_project_registry),
):
    """What files this run delivered (read-only).

    A terminal run is answered from the snapshot frozen when it ended, so its
    card keeps showing that run's files after a handoff commit or a later run in
    the same worktree. A terminal run with no snapshot says so — it never falls
    back to the live worktree, which is what made historical file lists wrong.
    An active run has no snapshot yet, and there the live worktree *is* the
    answer.
    """
    try:
        run = store.require(run_id)
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found") from None
    workspace = Path(run.workspace).expanduser().resolve()
    if not dispatcher.is_workspace_readable(run.project_id, workspace):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Workspace is not readable")

    base = {"run_id": run_id, "workspace": str(workspace)}
    snapshot = store.deliverable_snapshot(run_id)
    if snapshot is not None:
        # Serving stored data, so the worktree-shape checks below do not gate this
        # list — they gate filesystem reads, and there is no filesystem read here.
        # The project-scoped readability check above still gates access, and the
        # content/raw endpoints keep their own gate.
        #
        # The worktree can be gone or replaced by now, in which case the file list
        # is still the truth about this run but no file can be opened. Report that
        # instead of leaving every "view" to fail with a 404.
        return {
            **base,
            "files": snapshot.get("files") or [],
            "truncated": bool(snapshot.get("truncated")),
            "source": "snapshot",
            "captured_at": run.finished_at,
            "content_available": workspace.is_dir() and _ready_worktree_for_run(registry, run),
        }
    if run.state in TERMINAL_RUN_STATES:
        return {
            **base, "files": [], "source": "none",
            "unavailable": "this run ended without a deliverable snapshot; its files are not recoverable",
        }

    if not workspace.is_dir():
        return {**base, "files": [], "source": "live", "unavailable": "workspace not found"}
    if not _ready_worktree_for_run(registry, run):
        return {**base, "files": [], "source": "live", "unavailable": "workspace is not a provisioned worktree"}
    return {**base, "files": list_changes(workspace), "source": "live"}


@router.get("/runs/{run_id}/deliverables/content")
def run_deliverable_content(
    run_id: Annotated[str, PathParam(max_length=128)],
    path: str = Query(..., max_length=1024),
    store: ExecutionStore = Depends(get_execution_store),
    dispatcher: ExecutionDispatcher = Depends(get_execution_dispatcher),
    registry: ProjectRegistry = Depends(get_project_registry),
):
    """Return a bounded, redacted text preview of one deliverable file."""
    try:
        run = store.require(run_id)
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found") from None
    workspace = Path(run.workspace).expanduser().resolve()
    if not dispatcher.is_workspace_readable(run.project_id, workspace):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Workspace is not readable")
    _require_ready_worktree(registry, run)
    entry = _delivered_entry(store, run, path)
    if entry is None:  # pre-snapshot run: nothing recorded to verify against
        try:
            return read_file(workspace, path)
        except (FileNotFoundError, PermissionError):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Deliverable not found") from None
    return preview_of(path, _verified_bytes(workspace, path, entry))


@router.get("/runs/{run_id}/deliverables/raw")
def run_deliverable_raw(
    run_id: Annotated[str, PathParam(max_length=128)],
    path: str = Query(..., max_length=1024),
    store: ExecutionStore = Depends(get_execution_store),
    dispatcher: ExecutionDispatcher = Depends(get_execution_dispatcher),
    registry: ProjectRegistry = Depends(get_project_registry),
):
    """Serve a deliverable file so the UI can open/render it (e.g. an HTML page).

    Sandboxed via CSP so a rendered deliverable runs in an opaque origin and
    cannot touch the app's own origin, cookies, or storage.
    """
    try:
        run = store.require(run_id)
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found") from None
    workspace = Path(run.workspace).expanduser().resolve()
    if not dispatcher.is_workspace_readable(run.project_id, workspace):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Workspace is not readable")
    _require_ready_worktree(registry, run)
    sandbox_headers = {
        "Content-Security-Policy": "sandbox allow-scripts allow-forms allow-popups allow-modals;",
        "X-Content-Type-Options": "nosniff",
    }
    entry = _delivered_entry(store, run, path)
    if entry is None:  # pre-snapshot run: keep streaming from disk
        try:
            target = resolve_confined(workspace, path)
        except (FileNotFoundError, PermissionError):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Deliverable not found") from None
        return FileResponse(str(target), headers=sandbox_headers)
    # Serve the very bytes that were verified. FileResponse opens the path after
    # this function returns, which would reopen a file a later run may have
    # rewritten in between. Bounded by SNAPSHOT_HASH_LIMIT, since anything larger
    # has no digest and was already refused above.
    raw = _verified_bytes(workspace, path, entry)
    media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return Response(content=raw, media_type=media_type, headers=sandbox_headers)


@router.post("/runs/{run_id}/cancel", response_model=ExecutionRun)
async def cancel_run(
    run_id: Annotated[str, PathParam(max_length=128)],
    request: CancelRunRequest,
    dispatcher: ExecutionDispatcher = Depends(get_execution_dispatcher),
):
    try:
        return await dispatcher.cancel(run_id, request)
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found") from None
    except RunConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None


@router.get("/tasks/{task_id}/runs", response_model=list[RunSummary])
def list_task_runs(
    task_id: Annotated[str, PathParam(max_length=128)],
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    store: ExecutionStore = Depends(get_execution_store),
):
    if store.tasks.get(task_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    return store.list(task_id=task_id, limit=limit, offset=offset)


@router.post("/tasks/{task_id}/handoff", response_model=HandoffResponse)
async def handoff_task(
    task_id: Annotated[str, PathParam(max_length=128)],
    actor: str = Query(default="user", min_length=1, max_length=128),
    store: ExecutionStore = Depends(get_execution_store),
    task_store: TaskStore = Depends(get_task_store),
    registry: ProjectRegistry = Depends(get_project_registry),
):
    """Commit a verified task's deliverable, bind the commit, and mark it done."""
    try:
        result = await run_in_threadpool(
            hand_off, task_store, store, registry, task_id, actor=actor,
        )
    except HandoffError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except TaskNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found") from None
    except (InvalidTransitionError, StaleTaskVersionError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return HandoffResponse(
        task=result.task, run_id=result.run_id, workspace=result.workspace,
        commit=result.commit, committed=result.committed, files=result.files,
        note=result.note,
    )
