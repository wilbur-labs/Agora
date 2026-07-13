"""REST API for Requirements Studio."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from agora.tasks.router import get_task_store
from agora.tasks.store import TaskNotFoundError, TaskStore

from .models import (
    CreateSpecRequest,
    RejectSpecRequest,
    RequirementChangeRequest,
    RequirementSpec,
    ReviewChangeRequest,
    ReviewSpecRequest,
    SubmitChangeRequest,
    UpdateSpecRequest,
)
from .store import (
    RequirementConflictError,
    RequirementNotFoundError,
    RequirementStore,
    RequirementValidationError,
)

router = APIRouter(tags=["requirements"])


def get_requirement_store(task_store: TaskStore = Depends(get_task_store)) -> RequirementStore:
    return RequirementStore(task_store)


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"Not found: {exc}")


def _conflict(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


@router.post(
    "/tasks/{task_id}/specs",
    response_model=RequirementSpec,
    status_code=status.HTTP_201_CREATED,
)
def create_spec(
    task_id: str,
    request: CreateSpecRequest,
    store: RequirementStore = Depends(get_requirement_store),
):
    try:
        return store.create(task_id, request)
    except (TaskNotFoundError, RequirementNotFoundError) as exc:
        raise _not_found(exc) from None
    except RequirementConflictError as exc:
        raise _conflict(exc) from None


@router.get("/tasks/{task_id}/specs", response_model=list[RequirementSpec])
def list_specs(task_id: str, store: RequirementStore = Depends(get_requirement_store)):
    try:
        return store.list_for_task(task_id)
    except TaskNotFoundError as exc:
        raise _not_found(exc) from None


@router.get("/tasks/{task_id}/specs/current", response_model=RequirementSpec)
def current_spec(task_id: str, store: RequirementStore = Depends(get_requirement_store)):
    try:
        spec = store.current(task_id)
    except TaskNotFoundError as exc:
        raise _not_found(exc) from None
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task has no active requirement spec")
    return spec


@router.get("/specs/{spec_id}", response_model=RequirementSpec)
def get_spec(spec_id: str, store: RequirementStore = Depends(get_requirement_store)):
    try:
        return store.require(spec_id)
    except RequirementNotFoundError as exc:
        raise _not_found(exc) from None


@router.patch("/specs/{spec_id}", response_model=RequirementSpec)
def update_spec(
    spec_id: str,
    request: UpdateSpecRequest,
    store: RequirementStore = Depends(get_requirement_store),
):
    try:
        return store.update(spec_id, request)
    except RequirementNotFoundError as exc:
        raise _not_found(exc) from None
    except RequirementConflictError as exc:
        raise _conflict(exc) from None
    except RequirementValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


@router.post("/specs/{spec_id}/approve", response_model=RequirementSpec)
def approve_spec(
    spec_id: str,
    request: ReviewSpecRequest,
    store: RequirementStore = Depends(get_requirement_store),
):
    try:
        return store.approve(
            spec_id,
            actor=request.actor,
            expected_revision=request.expected_revision,
            reason=request.reason,
        )
    except RequirementNotFoundError as exc:
        raise _not_found(exc) from None
    except RequirementConflictError as exc:
        raise _conflict(exc) from None


@router.post("/specs/{spec_id}/reject", response_model=RequirementSpec)
def reject_spec(
    spec_id: str,
    request: RejectSpecRequest,
    store: RequirementStore = Depends(get_requirement_store),
):
    try:
        return store.reject(
            spec_id,
            actor=request.actor,
            expected_revision=request.expected_revision,
            reason=request.reason,
        )
    except RequirementNotFoundError as exc:
        raise _not_found(exc) from None
    except RequirementConflictError as exc:
        raise _conflict(exc) from None


@router.post(
    "/specs/{spec_id}/change-requests",
    response_model=RequirementChangeRequest,
    status_code=status.HTTP_201_CREATED,
)
def submit_change_request(
    spec_id: str,
    request: SubmitChangeRequest,
    store: RequirementStore = Depends(get_requirement_store),
):
    try:
        return store.submit_change_request(spec_id, request)
    except RequirementNotFoundError as exc:
        raise _not_found(exc) from None
    except RequirementConflictError as exc:
        raise _conflict(exc) from None


@router.get(
    "/specs/{spec_id}/change-requests",
    response_model=list[RequirementChangeRequest],
)
def list_change_requests(
    spec_id: str,
    store: RequirementStore = Depends(get_requirement_store),
):
    try:
        return store.list_change_requests(spec_id)
    except RequirementNotFoundError as exc:
        raise _not_found(exc) from None


@router.post("/change-requests/{cr_id}/review", response_model=RequirementChangeRequest)
def review_change_request(
    cr_id: str,
    request: ReviewChangeRequest,
    store: RequirementStore = Depends(get_requirement_store),
):
    try:
        return store.review_change_request(cr_id, request)
    except RequirementNotFoundError as exc:
        raise _not_found(exc) from None
    except RequirementConflictError as exc:
        raise _conflict(exc) from None
