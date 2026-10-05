"""REST API for explicit workspace provisioning."""
from __future__ import annotations

import asyncio

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Path as PathParam, Query, status

from agora.config.settings import get_config, resolve_control_plane_db_path
from agora.execution.adapters import build_adapter_registry
from agora.execution.environment_registry_store import RemoteEnvironmentStore
from agora.execution.remote_wiring import (
    initialise_if_empty, is_remote, on_registered_environment, remote_components_for,
)
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


def get_remote_environments() -> RemoteEnvironmentStore:
    """Registered remote environments (CR-4), on the one control-plane database."""
    return RemoteEnvironmentStore(resolve_control_plane_db_path(get_config()))


def _adapter_on(adapter_name: str, environment_id: str | None, project_id: str):
    """The adapter as a request names it: its own configured place, or — when the
    request names a registered environment — that environment. Unknown is a KeyError
    (a 404), never a quiet fall back to the adapter's own place."""
    adapter = get_execution_adapters().get(adapter_name)
    if environment_id is None:
        return adapter
    if adapter is None:
        # Not "local": the request named a remote place for an executor that does not
        # exist; answering from this machine would be the quiet fall back refused below.
        raise KeyError(f"Unknown or disabled execution adapter: {adapter_name}")
    record = get_remote_environments().get(environment_id)
    if record is None:
        raise KeyError(f"Unknown remote environment: {environment_id}")
    if record.project_id != project_id:
        # Another project's root: provisioning here would build this project's
        # worktree inside that repository (CR-4, option A).
        raise WorkspaceValidationError(
            f"Remote environment {environment_id} belongs to project "
            f"{record.project_id or '(none)'}, not {project_id}"
        )
    return on_registered_environment(adapter, record)


def _task_place(task_id: str | None, environment_id: str | None) -> str | None:
    """The environment a worktree check for ``task_id`` is about: the one named, or —
    when none is — the one the task's work is on, exactly as its next run would inherit."""
    if task_id is None or environment_id is not None:
        return environment_id
    # Lazy: the execution router imports workspace modules at load.
    from agora.execution.router import get_execution_dispatcher

    return get_execution_dispatcher().task_environment(task_id)


def _remote_status(
    project_id: str, adapter_name: str, projects: ProjectRegistry,
    environment_id: str | None = None,
) -> WorkspaceStatus | None:
    """The worktree's state on the adapter's host (W3-a U5), or None for a local adapter.
    A fresh remote environment per request, on its own loop: these endpoints are
    synchronous and run in a worker thread."""
    adapter = _adapter_on(adapter_name, environment_id, project_id)
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
    environment_id: Optional[str] = Query(
        default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    ),
    task_id: Optional[str] = Query(default=None, min_length=1, max_length=128),
):
    try:
        environment_id = _task_place(task_id, environment_id)
        if task_id is not None:
            from agora.execution.router import get_execution_dispatcher

            refusal = get_execution_dispatcher().placement_refusal(task_id, environment_id, adapter)
            if refusal is not None:
                raise HTTPException(status.HTTP_409_CONFLICT, refusal)
        return (
            _remote_status(project_id, adapter, provisioner.projects, environment_id)
            or provisioner.status(project_id, adapter)
        )
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
        if request.task_id is not None:
            # Lazy: the execution router imports workspace modules at load.
            from agora.execution.router import get_execution_dispatcher

            # The place the task's next run will use, so the preflight builds there.
            request = request.model_copy(update={
                "environment_id": _task_place(request.task_id, request.environment_id),
            })
            refusal = get_execution_dispatcher().placement_refusal(
                request.task_id, request.environment_id, request.adapter,
            )
            if refusal is not None:
                raise HTTPException(status.HTTP_409_CONFLICT, refusal)
        remote_adapter = _adapter_on(request.adapter, request.environment_id, request.project_id)
        if is_remote(remote_adapter):
            components = remote_components_for(remote_adapter, provisioner.projects)
            try:
                if request.environment_id is not None:
                    # A registered environment's root may be a pre-created empty
                    # directory (D2 form-2): make it a repository first, as project
                    # creation does locally. Idempotent.
                    await initialise_if_empty(components, request.project_id)
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
