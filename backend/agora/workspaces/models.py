"""Workspace provisioning API contracts."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class WorkspaceState(str, Enum):
    MISSING = "missing"
    PROVISIONING = "provisioning"
    READY = "ready"
    FOREIGN = "foreign"
    ERROR = "error"


class WorkspaceStatus(BaseModel):
    project_id: str
    adapter: str
    state: WorkspaceState
    path: str
    branch: str | None = None
    head_sha: str | None = None
    error: str | None = None
    source_is_git: bool


class ProvisionRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    adapter: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_-]*$")
    #: Provision on this registered remote environment (CR-4) instead of where the
    #: adapter's own config points — the same choice a run request makes.
    environment_id: Optional[str] = Field(
        default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"
    )
    #: The task this worktree is being prepared for. When given, the same placement
    #: rule a run obeys is checked BEFORE anything is built — so a run that would be
    #: refused never leaves a worktree (or an initialised repository) in the wrong place.
    task_id: Optional[str] = Field(default=None, min_length=1, max_length=128)


class ProvisionResult(BaseModel):
    status: WorkspaceStatus
    created: bool
