"""REST API for delivery control-plane tasks."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status

from agora.config.settings import get_config
from agora.projects import ProjectRegistry

from .models import (
    AppendEventRequest,
    CreateTaskRequest,
    TaskEvent,
    TaskManifest,
    TaskState,
    TransitionTaskRequest,
)
from .store import (
    InvalidTransitionError,
    StaleTaskVersionError,
    TaskNotFoundError,
    TaskStore,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])


@lru_cache(maxsize=1)
def get_task_store() -> TaskStore:
    cfg = get_config()
    data_dir = Path(cfg.get("memory", {}).get("data_dir", "./data"))
    db_path = cfg.get("control_plane", {}).get("db_path", data_dir / "agora.db")
    return TaskStore(db_path)


def get_project_registry() -> ProjectRegistry:
    return ProjectRegistry(get_config())


@router.post("", response_model=TaskManifest, status_code=status.HTTP_201_CREATED)
def create_task(
    request: CreateTaskRequest,
    store: TaskStore = Depends(get_task_store),
    projects: ProjectRegistry = Depends(get_project_registry),
):
    if request.project_id not in projects.list_projects():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown project_id")
    return store.create(request)


@router.get("", response_model=list[TaskManifest])
def list_tasks(
    project_id: str | None = None,
    state_filter: TaskState | None = Query(default=None, alias="state"),
    kind: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    store: TaskStore = Depends(get_task_store),
):
    return store.list(
        project_id=project_id,
        state=state_filter,
        kind=kind,
        limit=limit,
        offset=offset,
    )


@router.get("/{task_id}", response_model=TaskManifest)
def get_task(task_id: str, store: TaskStore = Depends(get_task_store)):
    task = store.get(task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    return task


@router.patch("/{task_id}/state", response_model=TaskManifest)
def transition_task(
    task_id: str,
    request: TransitionTaskRequest,
    store: TaskStore = Depends(get_task_store),
):
    try:
        return store.transition(
            task_id,
            request.target_state,
            actor=request.actor,
            reason=request.reason,
            expected_version=request.expected_version,
        )
    except TaskNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found") from None
    except (InvalidTransitionError, StaleTaskVersionError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None


@router.get("/{task_id}/events", response_model=list[TaskEvent])
def list_task_events(task_id: str, store: TaskStore = Depends(get_task_store)):
    try:
        return store.events(task_id)
    except TaskNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found") from None


@router.post("/{task_id}/events", response_model=TaskEvent, status_code=status.HTTP_201_CREATED)
def append_task_event(
    task_id: str,
    request: AppendEventRequest,
    store: TaskStore = Depends(get_task_store),
):
    try:
        return store.append_event(task_id, request)
    except TaskNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found") from None


@router.delete("/{task_id}", response_model=TaskManifest)
def cancel_task(
    task_id: str,
    reason: str | None = Query(default=None, max_length=4000),
    expected_version: int | None = Query(default=None, ge=1),
    store: TaskStore = Depends(get_task_store),
):
    try:
        return store.cancel(
            task_id,
            actor="user",
            reason=reason,
            expected_version=expected_version,
        )
    except TaskNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found") from None
    except (InvalidTransitionError, StaleTaskVersionError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
