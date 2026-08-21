"""REST API for durable delivery execution runs."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path as PathParam, Query, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

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

from .adapters import build_adapter_registry
from .deliverables import list_changes, read_file, resolve_confined
from .dispatcher import ExecutionDispatcher
from .handoff import HandoffError, hand_off
from .models import AdapterCapability, CancelRunRequest, CreateRunRequest, ExecutionRun, RunState, RunSummary
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
):
    """List the files this run produced in its Git worktree (read-only)."""
    try:
        run = store.require(run_id)
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found") from None
    workspace = Path(run.workspace).expanduser().resolve()
    if not dispatcher.is_workspace_readable(run.project_id, workspace):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Workspace is not readable")
    if not workspace.is_dir():
        return {"run_id": run_id, "workspace": str(workspace), "files": [], "unavailable": "workspace not found"}
    return {"run_id": run_id, "workspace": str(workspace), "files": list_changes(workspace)}


@router.get("/runs/{run_id}/deliverables/content")
def run_deliverable_content(
    run_id: Annotated[str, PathParam(max_length=128)],
    path: str = Query(..., max_length=1024),
    store: ExecutionStore = Depends(get_execution_store),
    dispatcher: ExecutionDispatcher = Depends(get_execution_dispatcher),
):
    """Return a bounded, redacted text preview of one deliverable file."""
    try:
        run = store.require(run_id)
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found") from None
    workspace = Path(run.workspace).expanduser().resolve()
    if not dispatcher.is_workspace_readable(run.project_id, workspace):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Workspace is not readable")
    try:
        return read_file(workspace, path)
    except (FileNotFoundError, PermissionError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deliverable not found") from None


@router.get("/runs/{run_id}/deliverables/raw")
def run_deliverable_raw(
    run_id: Annotated[str, PathParam(max_length=128)],
    path: str = Query(..., max_length=1024),
    store: ExecutionStore = Depends(get_execution_store),
    dispatcher: ExecutionDispatcher = Depends(get_execution_dispatcher),
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
    try:
        target = resolve_confined(workspace, path)
    except (FileNotFoundError, PermissionError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deliverable not found") from None
    return FileResponse(
        str(target),
        headers={
            "Content-Security-Policy": "sandbox allow-scripts allow-forms allow-popups allow-modals;",
            "X-Content-Type-Options": "nosniff",
        },
    )


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
):
    """Commit a verified task's deliverable, bind the commit, and mark it done."""
    try:
        result = await run_in_threadpool(
            hand_off, task_store, store, task_id, actor=actor,
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
