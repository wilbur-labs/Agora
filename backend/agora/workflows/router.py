"""REST API for durable workflow DAG plans."""
from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query, status

from agora.projects import ProjectRegistry
from agora.tasks.router import get_project_registry, get_task_store
from agora.execution.router import get_execution_dispatcher

from .models import (CreateWorkflowRequest, TransitionWorkflowStepRequest, WorkflowActionRequest,
                     WorkflowDispatchResult, WorkflowEvent, WorkflowManifest, WorkflowState, WorkflowSummary)
from .orchestrator import WorkflowOrchestrator
from .supervisor import WorkflowSupervisor
from agora.config.settings import get_config
from .store import WorkflowConflictError, WorkflowNotFoundError, WorkflowStore, WorkflowValidationError

router = APIRouter(prefix="/workflows", tags=["workflows"])


@lru_cache(maxsize=1)
def get_workflow_store() -> WorkflowStore: return WorkflowStore(get_task_store())


@lru_cache(maxsize=1)
def get_workflow_orchestrator() -> WorkflowOrchestrator:
    return WorkflowOrchestrator(get_workflow_store(), get_execution_dispatcher())


@lru_cache(maxsize=1)
def get_workflow_supervisor() -> WorkflowSupervisor:
    settings = get_config().get("workflow_scheduler", {})
    return WorkflowSupervisor(
        get_workflow_store(), get_workflow_orchestrator(),
        interval_seconds=float(settings.get("interval_seconds", 5)),
    )


@router.post("", response_model=WorkflowManifest, status_code=status.HTTP_201_CREATED)
def create_workflow(request: CreateWorkflowRequest, store: WorkflowStore = Depends(get_workflow_store),
                    projects: ProjectRegistry = Depends(get_project_registry)):
    known = set(projects.list_projects())
    unknown = {step.project_id for step in request.steps} - known
    if unknown: raise HTTPException(422, f"Unknown project_id: {sorted(unknown)}")
    try: return store.create(request)
    except WorkflowValidationError as exc: raise HTTPException(422, str(exc)) from None


@router.get("", response_model=list[WorkflowSummary])
def list_workflows(state_filter: WorkflowState | None = Query(default=None, alias="state"), project_id: str | None = None,
                   limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                   store: WorkflowStore = Depends(get_workflow_store)):
    return store.list(state=state_filter, project_id=project_id, limit=limit, offset=offset)


@router.get("/{workflow_id}", response_model=WorkflowManifest)
def get_workflow(workflow_id: str, store: WorkflowStore = Depends(get_workflow_store)):
    try: return store.require(workflow_id)
    except WorkflowNotFoundError: raise HTTPException(404, "Workflow not found") from None


def _action(method, workflow_id, request, store):
    try: return method(workflow_id, request)
    except WorkflowNotFoundError: raise HTTPException(404, "Workflow not found") from None
    except WorkflowConflictError as exc: raise HTTPException(409, str(exc)) from None


@router.post("/{workflow_id}/activate", response_model=WorkflowManifest)
def activate_workflow(workflow_id: str, request: WorkflowActionRequest, store: WorkflowStore = Depends(get_workflow_store)):
    return _action(store.activate, workflow_id, request, store)


@router.post("/{workflow_id}/cancel", response_model=WorkflowManifest)
def cancel_workflow(workflow_id: str, request: WorkflowActionRequest, store: WorkflowStore = Depends(get_workflow_store)):
    return _action(store.cancel, workflow_id, request, store)


@router.patch("/{workflow_id}/steps/{step_id}/state", response_model=WorkflowManifest)
def transition_step(workflow_id: str, step_id: str, request: TransitionWorkflowStepRequest,
                    store: WorkflowStore = Depends(get_workflow_store)):
    return _action(lambda wid, req: store.transition_step(wid, step_id, req), workflow_id, request, store)


@router.get("/{workflow_id}/events", response_model=list[WorkflowEvent])
def workflow_events(workflow_id: str, store: WorkflowStore = Depends(get_workflow_store)):
    try: return store.events(workflow_id)
    except WorkflowNotFoundError: raise HTTPException(404, "Workflow not found") from None


@router.post("/{workflow_id}/dispatch", response_model=WorkflowDispatchResult)
async def dispatch_workflow(
    workflow_id: str,
    orchestrator: WorkflowOrchestrator = Depends(get_workflow_orchestrator),
):
    try: return await orchestrator.dispatch(workflow_id)
    except WorkflowNotFoundError: raise HTTPException(404, "Workflow not found") from None
    except WorkflowConflictError as exc: raise HTTPException(409, str(exc)) from None
