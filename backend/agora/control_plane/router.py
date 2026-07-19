"""Authenticated, Task-scoped HTTP boundary for the Control Plane registry."""
from __future__ import annotations

import sqlite3
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool

from agora.protocol.models import Approval, Artifact, Evidence
from agora.tasks.router import get_task_store
from agora.tasks.store import TaskStore

from .api_models import (
    ConfigureGateRequest,
    ControlEventPage,
    ControlPlaneProjection,
    EvaluateGateRequest,
    SetActiveEvidenceRequest,
)
from .auth import ControlPrincipal, authenticate_control_plane, authorize
from .models import GateRecord, RegistrationReceipt, StageRecord
from .store import (
    ControlPlaneConflictError,
    ControlPlaneNotFoundError,
    ControlPlaneStore,
    ControlPlaneValidationError,
)

router = APIRouter(
    prefix="/control-plane/projects/{project_id}/tasks/{task_id}",
    tags=["control-plane"],
)


@lru_cache(maxsize=1)
def _cached_store(db_path: str) -> ControlPlaneStore:
    return ControlPlaneStore(TaskStore(db_path))


def get_control_plane_store(
    tasks: TaskStore = Depends(get_task_store),
) -> ControlPlaneStore:
    return _cached_store(str(tasks.db_path))


def _scope(
    project_id: str,
    task_id: str,
    permission: str,
    principal: ControlPrincipal,
    store: ControlPlaneStore,
) -> None:
    authorize(principal, project_id, permission)
    task = store.tasks.get(task_id)
    if task is None or task.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Control Plane resource not found")


def _bound(entity, project_id: str, task_id: str) -> None:
    if entity.project_id != project_id or entity.task_id != task_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Payload scope does not match the request path",
        )


def _read_bound(entity, project_id: str, task_id: str):
    if entity is None or entity.project_id != project_id or entity.task_id != task_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Control Plane resource not found")
    return entity


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, ControlPlaneNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, "Control Plane resource not found")
    if isinstance(exc, (ControlPlaneConflictError,)):
        return HTTPException(status.HTTP_409_CONFLICT, "Control Plane state conflict")
    if isinstance(exc, ControlPlaneValidationError):
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Control Plane validation failed",
        )
    if isinstance(exc, sqlite3.OperationalError) and any(
        marker in str(exc).lower() for marker in ("busy", "locked")
    ):
        return HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Control Plane is temporarily unavailable",
            headers={"Retry-After": "1"},
        )
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Control Plane operation failed")


async def _call(method, /, *args, **kwargs):
    try:
        return await run_in_threadpool(method, *args, **kwargs)
    except HTTPException:
        raise
    except Exception as exc:
        raise _translate(exc) from None


@router.put("/gates/{gate_key}", response_model=GateRecord)
async def configure_gate(
    project_id: str,
    task_id: str,
    gate_key: str,
    request: ConfigureGateRequest,
    principal: ControlPrincipal = Depends(authenticate_control_plane),
    store: ControlPlaneStore = Depends(get_control_plane_store),
):
    await _call(_scope, project_id, task_id, "control_plane.register", principal, store)
    return await _call(
        store.configure_gate,
        task_id=task_id,
        gate_key=gate_key,
        stage_key=request.stage_key,
        requirements=request.requirements,
        actor=principal.principal_id,
    )


@router.post("/artifacts", response_model=RegistrationReceipt)
async def register_artifact(
    project_id: str,
    task_id: str,
    artifact: Artifact,
    principal: ControlPrincipal = Depends(authenticate_control_plane),
    store: ControlPlaneStore = Depends(get_control_plane_store),
):
    await _call(_scope, project_id, task_id, "control_plane.register", principal, store)
    _bound(artifact, project_id, task_id)
    return await _call(store.register_artifact, artifact, actor=principal.principal_id)


@router.post("/evidence", response_model=RegistrationReceipt)
async def register_evidence(
    project_id: str,
    task_id: str,
    evidence: Evidence,
    principal: ControlPrincipal = Depends(authenticate_control_plane),
    store: ControlPlaneStore = Depends(get_control_plane_store),
):
    await _call(_scope, project_id, task_id, "control_plane.register", principal, store)
    _bound(evidence, project_id, task_id)
    return await _call(store.register_evidence, evidence, actor=principal.principal_id)


@router.post("/approvals", response_model=RegistrationReceipt)
async def register_approval(
    project_id: str,
    task_id: str,
    approval: Approval,
    principal: ControlPrincipal = Depends(authenticate_control_plane),
    store: ControlPlaneStore = Depends(get_control_plane_store),
):
    await _call(_scope, project_id, task_id, "control_plane.approve", principal, store)
    _bound(approval, project_id, task_id)
    if approval.approved_by != principal.principal_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "approved_by must match the authenticated principal",
        )
    return await _call(store.register_approval, approval, actor=principal.principal_id)


@router.put("/gates/{gate_key}/active-evidence", response_model=GateRecord)
async def set_active_evidence(
    project_id: str,
    task_id: str,
    gate_key: str,
    request: SetActiveEvidenceRequest,
    principal: ControlPrincipal = Depends(authenticate_control_plane),
    store: ControlPlaneStore = Depends(get_control_plane_store),
):
    await _call(_scope, project_id, task_id, "control_plane.register", principal, store)
    return await _call(
        store.set_active_evidence,
        task_id=task_id,
        gate_key=gate_key,
        evidence_ids=request.evidence_ids,
        expected_gate_version=request.expected_gate_version,
        actor=principal.principal_id,
        operation_key=request.operation_key,
    )


@router.post("/gates/{gate_key}/evaluations", response_model=GateRecord)
async def evaluate_gate(
    project_id: str,
    task_id: str,
    gate_key: str,
    request: EvaluateGateRequest,
    principal: ControlPrincipal = Depends(authenticate_control_plane),
    store: ControlPlaneStore = Depends(get_control_plane_store),
):
    await _call(_scope, project_id, task_id, "control_plane.evaluate", principal, store)
    return await _call(
        store.evaluate,
        task_id=task_id,
        gate_key=gate_key,
        expected_gate_version=request.expected_gate_version,
        actor=principal.principal_id,
        operation_key=request.operation_key,
    )


@router.get("/projection", response_model=ControlPlaneProjection)
async def get_projection(
    project_id: str,
    task_id: str,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1_000_000),
    principal: ControlPrincipal = Depends(authenticate_control_plane),
    store: ControlPlaneStore = Depends(get_control_plane_store),
):
    await _call(_scope, project_id, task_id, "control_plane.read", principal, store)
    return await _call(store.projection, task_id, event_limit=limit, event_offset=offset)


@router.get("/stages/{stage_key}", response_model=StageRecord)
async def get_stage(
    project_id: str,
    task_id: str,
    stage_key: str,
    principal: ControlPrincipal = Depends(authenticate_control_plane),
    store: ControlPlaneStore = Depends(get_control_plane_store),
):
    await _call(_scope, project_id, task_id, "control_plane.read", principal, store)
    return _read_bound(
        await _call(store.get_stage, task_id, stage_key), project_id, task_id
    )


@router.get("/gates/{gate_key}", response_model=GateRecord)
async def get_gate(
    project_id: str,
    task_id: str,
    gate_key: str,
    principal: ControlPrincipal = Depends(authenticate_control_plane),
    store: ControlPlaneStore = Depends(get_control_plane_store),
):
    await _call(_scope, project_id, task_id, "control_plane.read", principal, store)
    return _read_bound(await _call(store.get_gate, task_id, gate_key), project_id, task_id)


@router.get("/artifacts/{artifact_id}/versions/{version}", response_model=Artifact)
async def get_artifact(
    project_id: str,
    task_id: str,
    artifact_id: str,
    version: int,
    principal: ControlPrincipal = Depends(authenticate_control_plane),
    store: ControlPlaneStore = Depends(get_control_plane_store),
):
    await _call(_scope, project_id, task_id, "control_plane.read", principal, store)
    return _read_bound(
        await _call(store.get_artifact, artifact_id, version), project_id, task_id
    )


@router.get("/evidence/{evidence_id}", response_model=Evidence)
async def get_evidence(
    project_id: str,
    task_id: str,
    evidence_id: str,
    principal: ControlPrincipal = Depends(authenticate_control_plane),
    store: ControlPlaneStore = Depends(get_control_plane_store),
):
    await _call(_scope, project_id, task_id, "control_plane.read", principal, store)
    return _read_bound(
        await _call(store.get_evidence, evidence_id), project_id, task_id
    )


@router.get("/approvals/{approval_id}", response_model=Approval)
async def get_approval(
    project_id: str,
    task_id: str,
    approval_id: str,
    principal: ControlPrincipal = Depends(authenticate_control_plane),
    store: ControlPlaneStore = Depends(get_control_plane_store),
):
    await _call(_scope, project_id, task_id, "control_plane.read", principal, store)
    return _read_bound(
        await _call(store.get_approval, approval_id), project_id, task_id
    )


@router.get("/events", response_model=ControlEventPage)
async def get_events(
    project_id: str,
    task_id: str,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1_000_000),
    principal: ControlPrincipal = Depends(authenticate_control_plane),
    store: ControlPlaneStore = Depends(get_control_plane_store),
):
    await _call(_scope, project_id, task_id, "control_plane.read", principal, store)
    result = await _call(store.event_page, task_id, limit=limit, offset=offset)
    return {"events": result["events"], "page": result["event_page"]}
