"""Bounded HTTP surface for the provisional task orchestration demo."""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Callable, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi.concurrency import run_in_threadpool

from agora.tasks.models import TaskManifest
from agora.tasks.router import get_project_registry, get_task_store
from agora.tasks.store import TaskNotFoundError

from .models import (
    ApprovalRequest,
    AttachOrchestrationRequest,
    CreateOrchestratedTaskRequest,
    OrchestrationRun,
    TaskOrchestrationStatus,
)
from .runtime import build_runtime_registry
from .service import TaskOrchestrationService
from .store import (
    OrchestrationConflictError,
    OrchestrationNotFoundError,
    OrchestrationValidationError,
)
from agora.config.settings import get_config


router = APIRouter(tags=["task-orchestration"])
TaskId = Annotated[str, Path(max_length=128, pattern=r"^task_[a-f0-9]{32}$")]
StageKey = Annotated[str, Path(max_length=128, pattern=r"^[a-z][a-z0-9_-]*$")]
T = TypeVar("T")


@lru_cache(maxsize=1)
def get_task_orchestration_service() -> TaskOrchestrationService:
    config = get_config()
    settings = config.get("orchestration", {})
    return TaskOrchestrationService(
        get_task_store(),
        get_project_registry(),
        build_runtime_registry(config),
        timeout_seconds=int(settings.get("timeout_seconds", 600)),
    )


async def _store_action(action: Callable[[], T]) -> T:
    try:
        return await run_in_threadpool(action)
    except (TaskNotFoundError, OrchestrationNotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task orchestration not found") from None
    except OrchestrationConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except OrchestrationValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    except KeyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


@router.post(
    "/orchestrations",
    response_model=TaskManifest,
    status_code=status.HTTP_201_CREATED,
)
async def create_orchestrated_task(
    request: CreateOrchestratedTaskRequest,
    service: TaskOrchestrationService = Depends(get_task_orchestration_service),
):
    return await _store_action(lambda: service.create(
        project_id=request.project_id,
        title=request.title,
        description=request.description,
        total_token_budget=request.total_token_budget,
        total_cost_budget_usd=request.total_cost_budget_usd,
        risk=request.risk,
    ))


@router.post(
    "/tasks/{task_id}/orchestration",
    response_model=TaskOrchestrationStatus,
    status_code=status.HTTP_201_CREATED,
)
async def attach_orchestration(
    task_id: TaskId,
    request: AttachOrchestrationRequest,
    service: TaskOrchestrationService = Depends(get_task_orchestration_service),
):
    await _store_action(lambda: service.attach(
        task_id,
        total_token_budget=request.total_token_budget,
        total_cost_budget_usd=request.total_cost_budget_usd,
    ))
    return await _store_action(lambda: service.status(task_id))


@router.get("/tasks/{task_id}/orchestration", response_model=TaskOrchestrationStatus)
async def get_orchestration(
    task_id: TaskId,
    service: TaskOrchestrationService = Depends(get_task_orchestration_service),
):
    return await _store_action(lambda: service.status(task_id))


@router.post("/tasks/{task_id}/orchestration/next", response_model=OrchestrationRun)
async def run_next_stage(
    task_id: TaskId,
    service: TaskOrchestrationService = Depends(get_task_orchestration_service),
):
    try:
        return await service.run_next(task_id)
    except (TaskNotFoundError, OrchestrationNotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task orchestration not found") from None
    except OrchestrationConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except OrchestrationValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


@router.post("/tasks/{task_id}/orchestration/resume", response_model=TaskOrchestrationStatus)
async def resume_orchestration(
    task_id: TaskId,
    service: TaskOrchestrationService = Depends(get_task_orchestration_service),
):
    return await _store_action(lambda: service.resume(task_id))


@router.post(
    "/tasks/{task_id}/orchestration/stages/{stage_key}/retry",
    response_model=TaskOrchestrationStatus,
)
async def retry_stage(
    task_id: TaskId,
    stage_key: StageKey,
    service: TaskOrchestrationService = Depends(get_task_orchestration_service),
):
    await _store_action(lambda: service.retry(task_id, stage_key))
    return await _store_action(lambda: service.status(task_id))


@router.post("/tasks/{task_id}/orchestration/approve", response_model=TaskOrchestrationStatus)
async def approve_orchestration(
    task_id: TaskId,
    request: ApprovalRequest,
    service: TaskOrchestrationService = Depends(get_task_orchestration_service),
):
    await _store_action(lambda: service.approve(
        task_id,
        actor=request.actor,
        reason=request.reason,
    ))
    return await _store_action(lambda: service.status(task_id))
