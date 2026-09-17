"""REST API for explicit workspace provisioning."""
from __future__ import annotations

import asyncio

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path as PathParam, status

from agora.config.settings import get_config
from agora.execution.adapters import build_adapter_registry
from agora.execution.remote_wiring import is_remote, remote_components_for
from agora.projects import ProjectRegistry

from .models import ProvisionRequest, ProvisionResult, WorkspaceStatus
from .provisioner import (
    WorkspaceConflictError,
    WorkspaceProvisioner,
    WorkspaceUnavailableError,
    WorkspaceValidationError,
)


router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@lru_cache(maxsize=1)
def get_workspace_provisioner() -> WorkspaceProvisioner:
    config = get_config()
    projects = ProjectRegistry(config)
    execution = config.get("execution", {})
    roots = []
    for value in execution.get("allowed_workspace_roots", []):
        root = Path(value).expanduser()
        roots.append((root if root.is_absolute() else projects.project_root / root).resolve())
    return WorkspaceProvisioner(projects, allowed_workspace_roots=roots)


@lru_cache(maxsize=1)
def get_execution_adapters() -> dict:
    return build_adapter_registry(get_config())


def _remote_status(project_id: str, adapter_name: str, projects: ProjectRegistry) -> WorkspaceStatus | None:
    """The worktree's state on the adapter's host (W3-a U5), or None for a local adapter.
    A fresh remote environment per request, on its own loop: these endpoints are
    synchronous and run in a worker thread."""
    adapter = get_execution_adapters().get(adapter_name)
    if not is_remote(adapter):
        return None

    async def go() -> WorkspaceStatus:
        components = remote_components_for(adapter, projects)
        try:
            return await components.provisioner.status(project_id, adapter_name)
        finally:
            await components.aclose()

    return asyncio.run(go())


@router.get("/{project_id}", response_model=list[WorkspaceStatus])
def list_workspace_status(
    project_id: Annotated[str, PathParam(max_length=128)],
    provisioner: WorkspaceProvisioner = Depends(get_workspace_provisioner),
):
    try:
        project = provisioner.projects.get(project_id)
        return [
            _remote_status(project_id, adapter, provisioner.projects) or provisioner.status(project_id, adapter)
            for adapter in sorted(project.workspaces)
        ]
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found") from None
    except WorkspaceValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


@router.get("/{project_id}/{adapter}", response_model=WorkspaceStatus)
def get_workspace_status(
    project_id: Annotated[str, PathParam(max_length=128)],
    adapter: Annotated[str, PathParam(max_length=128)],
    provisioner: WorkspaceProvisioner = Depends(get_workspace_provisioner),
):
    try:
        return _remote_status(project_id, adapter, provisioner.projects) or provisioner.status(project_id, adapter)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project or adapter not found") from None
    except WorkspaceValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


@router.post("/provision", response_model=ProvisionResult)
async def provision_workspace(
    request: ProvisionRequest,
    provisioner: WorkspaceProvisioner = Depends(get_workspace_provisioner),
):
    try:
        remote_adapter = get_execution_adapters().get(request.adapter)
        if is_remote(remote_adapter):
            components = remote_components_for(remote_adapter, provisioner.projects)
            try:
                return await components.provisioner.provision(request)
            finally:
                await components.aclose()
        return await provisioner.provision(request)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project or adapter not found") from None
    except WorkspaceConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except WorkspaceValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    except WorkspaceUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None
