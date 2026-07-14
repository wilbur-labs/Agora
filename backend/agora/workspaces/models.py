"""Workspace provisioning API contracts."""
from __future__ import annotations

from enum import Enum

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


class ProvisionResult(BaseModel):
    status: WorkspaceStatus
    created: bool
