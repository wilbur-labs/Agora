"""REST API for delivery control-plane tasks."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool

from . import clarify

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


@router.post("/{task_id}/clarifications/generate", response_model=TaskManifest)
async def generate_clarifications(
    task_id: str,
    store: TaskStore = Depends(get_task_store),
    projects: ProjectRegistry = Depends(get_project_registry),
):
    """Ask the agent to propose requirement-clarifying questions for this task."""
    task = store.get(task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    try:
        project = projects.get(task.project_id)
    except KeyError:
        project = None
    cwd = project.root if project is not None else Path(".")
    try:
        questions = await run_in_threadpool(
            clarify.generate_questions,
            title=task.title, description=task.description, cwd=cwd,
        )
    except clarify.ClarifyError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None
    existing = task.clarifications or {}
    return store.set_clarifications(task_id, {
        "status": "ready",
        "questions": questions,
        "answers": existing.get("answers", {}),
    })


@router.post("/{task_id}/clarifications/answers", response_model=TaskManifest)
def save_clarification_answers(
    task_id: str,
    payload: dict = Body(...),
    store: TaskStore = Depends(get_task_store),
):
    """Persist the user's answers to the clarifying questions."""
    task = store.get(task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    current = dict(task.clarifications or {})
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "answers must be an object")
    current["answers"] = {str(k): str(v) for k, v in answers.items()}
    current.setdefault("questions", [])
    current["status"] = "answered"
    return store.set_clarifications(task_id, current)


@router.post("/{task_id}/archive", response_model=TaskManifest)
def archive_task(
    task_id: str,
    expected_version: int | None = Query(default=None, ge=1),
    store: TaskStore = Depends(get_task_store),
):
    """Hide a task from the active board (display-only; lifecycle state is unchanged)."""
    try:
        return store.set_archived(
            task_id, archived=True, actor="user", expected_version=expected_version
        )
    except TaskNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found") from None
    except StaleTaskVersionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None


@router.post("/{task_id}/unarchive", response_model=TaskManifest)
def unarchive_task(
    task_id: str,
    expected_version: int | None = Query(default=None, ge=1),
    store: TaskStore = Depends(get_task_store),
):
    """Restore an archived task to the active board (display-only)."""
    try:
        return store.set_archived(
            task_id, archived=False, actor="user", expected_version=expected_version
        )
    except TaskNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found") from None
    except StaleTaskVersionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
