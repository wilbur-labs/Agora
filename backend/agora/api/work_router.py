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
from agora.work.shelf import ShelfRefused
from agora.work.service import WorkProjectBusy, WorkProjectNotFound, WorkService
from agora.work.store import WorkSessionStore

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


@router.post("/shelved/{key}/restore")
async def restore_shelved_project(key: str, service: WorkService = Depends(_service)) -> dict[str, Any]:
    try:
        await service.unshelve(key)
    except WorkProjectNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such paused or archived project.") from None
    except ShelfRefused as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, {"code": "refused", "message": str(exc)}) from None
    return {"key": key, "restored": True}
