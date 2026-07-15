"""REST API for durable delivery execution runs."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path as PathParam, Query, status
from fastapi.concurrency import run_in_threadpool

from agora.config.settings import get_config
from agora.projects import ProjectRegistry
from agora.tasks.router import get_project_registry, get_task_store
from agora.tasks.store import TaskNotFoundError

from .adapters import build_adapter_registry
from .dispatcher import ExecutionDispatcher
from .models import AdapterCapability, CancelRunRequest, CreateRunRequest, ExecutionRun, RunState, RunSummary
from .store import ExecutionStore, RunConflictError, RunNotFoundError, RunValidationError


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
