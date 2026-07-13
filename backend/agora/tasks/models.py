"""Tool-neutral task and event contracts for the delivery control plane."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskState(str, Enum):
    BACKLOG = "backlog"
    REQUIREMENTS = "requirements"
    DESIGN = "design"
    PLANNED = "planned"
    RUNNING = "running"
    BLOCKED = "blocked"
    REVIEW = "review"
    VERIFIED = "verified"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TaskBudget(BaseModel):
    max_cost_usd: float | None = Field(default=None, ge=0)
    max_minutes: int | None = Field(default=None, ge=1)


class CreateTaskRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=20_000)
    kind: str = Field(default="custom", min_length=1, max_length=128)
    risk: TaskRisk = TaskRisk.MEDIUM
    priority: int = Field(default=50, ge=0, le=100)
    primary_agent: str | None = Field(default=None, max_length=128)
    reviewers: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)
    budget: TaskBudget = Field(default_factory=TaskBudget)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_by: str = Field(default="user", min_length=1, max_length=128)

    @field_validator("project_id")
    @classmethod
    def valid_project_id(cls, value: str) -> str:
        if not value.replace("-", "").replace("_", "").isalnum():
            raise ValueError("project_id may contain only letters, numbers, dashes, or underscores")
        return value

    @field_validator("reviewers", "acceptance")
    @classmethod
    def no_blank_items(cls, values: list[str]) -> list[str]:
        if any(not item.strip() for item in values):
            raise ValueError("list items may not be blank")
        return values


class TransitionTaskRequest(BaseModel):
    target_state: TaskState
    reason: str | None = Field(default=None, max_length=4000)
    actor: str = Field(default="user", min_length=1, max_length=128)
    expected_version: int | None = Field(default=None, ge=1)


class AppendEventRequest(BaseModel):
    event_type: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.-]*$")
    payload: dict[str, Any] = Field(default_factory=dict)
    actor: str = Field(default="user", min_length=1, max_length=128)

    @field_validator("event_type")
    @classmethod
    def reserved_event_type(cls, value: str) -> str:
        if value in {"task_created", "state_changed"} or value.startswith(("spec.", "cr.")):
            raise ValueError(f"{value} is reserved for the control plane")
        return value


class TaskManifest(BaseModel):
    task_id: str
    project_id: str
    title: str
    description: str
    kind: str
    state: TaskState
    risk: TaskRisk
    priority: int
    primary_agent: str | None
    reviewers: list[str]
    acceptance: list[str]
    budget: TaskBudget
    metadata: dict[str, Any]
    version: int
    created_by: str
    created_at: str
    updated_at: str


class TaskEvent(BaseModel):
    event_id: str
    task_id: str
    event_type: str
    actor: str
    payload: dict[str, Any]
    created_at: str
