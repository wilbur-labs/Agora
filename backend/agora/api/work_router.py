"""The work page's routes (workspace CR-75 slice 1). Every one — reads included — needs
the install's terminal token: the list names this machine's projects and the others
start agents. The dependency is on the router, not repeated per route (design R11).
"""
from __future__ import annotations

import asyncio
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from agora.api.terminal_router import _data_dir, get_terminal_manager, require_terminal_session
from agora.config.settings import get_config, resolve_control_plane_db_path
from agora.projects.registry import ProjectRegistry
from agora.tasks.router import get_project_registry
from agora.projects.workflow_selection import WorkflowChanged, WorkflowRefused
from agora.work.shelf import ShelfRefused
from agora.work.service import WorkProjectBusy, WorkProjectNotFound, WorkService
from agora.work.store import WorkSessionStore
from agora.work import usage

router = APIRouter(prefix="/work", dependencies=[Depends(require_terminal_session)])


@lru_cache(maxsize=1)
def get_work_service() -> WorkService:
    return WorkService(
        store=WorkSessionStore(resolve_control_plane_db_path(get_config())),
        manager=get_terminal_manager(),
        data_dir=_data_dir(),
        sources=lambda: [],
    )


async def _service(registry: ProjectRegistry = Depends(get_project_registry)) -> WorkService:
    # ``async``: FastAPI runs a plain ``def`` dependency in its thread pool, where two first
    # requests could each build a service — and two services are two locks (kiro review).
    service = get_work_service()
    service.sources = lambda: [Path(p) for p in registry.workspace_sources()]
    return service


@router.get("/usage")
async def work_usage(service: WorkService = Depends(_service)) -> dict[str, Any]:
    """How much of each agent's limits is used (CR-87): claude from its own ``/usage``
    (the status-line record when that fails), codex from its app-server, kiro from
    ``kiro-cli acp``."""
    folder = service.data_dir / "work-usage"
    return await asyncio.to_thread(usage.read_all, folder if folder.is_dir() else None)


@router.get("/projects")
async def list_work_projects(service: WorkService = Depends(_service)) -> dict[str, Any]:
    return await asyncio.to_thread(service.view)


@router.post("/projects/{key}/open")
async def open_work_project(key: str, service: WorkService = Depends(_service)) -> dict[str, Any]:
    try:
        return await service.open(key)
    except WorkProjectNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such active work project.") from None


@router.post("/projects/{key}/close")
async def close_work_project(key: str, service: WorkService = Depends(_service)) -> dict[str, Any]:
    still_running = await service.close(key)
    return {"key": key, "closed": not still_running, "still_running": still_running}


@router.post("/restore")
async def restore_work_projects(service: WorkService = Depends(_service)) -> dict[str, Any]:
    return {"restore": await service.restore()}


class ShelveRequest(BaseModel):
    action: Literal["pause", "archive"]


@router.post("/projects/{key}/shelve")
async def shelve_work_project(key: str, body: ShelveRequest,
                              service: WorkService = Depends(_service)) -> dict[str, Any]:
    """Pause or archive: written into the project's own task.yaml (CR-80). Not committed."""
    try:
        await service.shelve(key, body.action)
    except WorkProjectNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such active work project.") from None
    except WorkProjectBusy:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            {"code": "terminal_open", "message": "Close this project's terminal first."}) from None
    except ShelfRefused as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, {"code": "refused", "message": str(exc)}) from None
    return {"key": key, "action": body.action}


class WorkflowRequest(BaseModel):
    features: dict[str, bool]
    #: sha256 of the task.yaml text the page showed (the read's ``workflow.base``).
    base: str


@router.post("/projects/{key}/workflow")
async def set_work_workflow(key: str, body: WorkflowRequest,
                            service: WorkService = Depends(_service)) -> dict[str, Any]:
    """The workflow selection, written into the project's own task.yaml (CR-75 slice 4).
    Not committed; takes effect at the next session start or /clear."""
    try:
        base = await service.set_workflow(key, body.features, body.base)
    except WorkProjectNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such active work project.") from None
    except WorkflowChanged as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, {"code": "changed", "message": str(exc)}) from None
    except WorkflowRefused as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, {"code": "refused", "message": str(exc)}) from None
    return {"key": key, "base": base}


class NameRequest(BaseModel):
    #: Blank or null removes the name.
    name: str | None = None


@router.post("/projects/{key}/name")
async def set_work_name(key: str, body: NameRequest, service: WorkService = Depends(_service)) -> dict[str, Any]:
    """The card's name (CR-85): Agora's own, not written into the task record."""
    try:
        name = await service.set_name(key, body.name)
    except WorkProjectNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such active work project.") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return {"key": key, "name": name}


class StageRequest(BaseModel):
    stage: Literal["todo", "doing"]


@router.post("/projects/{key}/stage")
async def set_work_stage(key: str, body: StageRequest, service: WorkService = Depends(_service)) -> dict[str, Any]:
    """Todo or doing (CR-81): Agora's own, not written into the task record."""
    try:
        await service.set_stage(key, body.stage)
    except WorkProjectNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such active work project.") from None
    except WorkProjectBusy:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            {"code": "terminal_open", "message": "Close this project's terminal first."}) from None
    return {"key": key, "stage": body.stage}


class OrderRequest(BaseModel):
    keys: list[str]


@router.put("/order")
async def set_work_order(body: OrderRequest, service: WorkService = Depends(_service)) -> dict[str, Any]:
    """The order the person dragged the projects into (CR-82)."""
    try:
        await service.set_order(body.keys)
    except WorkProjectNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such active work project.") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return {"keys": body.keys}


@router.post("/shelved/{key}/restore")
async def restore_shelved_project(key: str, service: WorkService = Depends(_service)) -> dict[str, Any]:
    try:
        await service.unshelve(key)
    except WorkProjectNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such paused or archived project.") from None
    except ShelfRefused as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, {"code": "refused", "message": str(exc)}) from None
    return {"key": key, "restored": True}
