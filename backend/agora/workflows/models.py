"""Tool-neutral contracts for durable workflow DAG plans."""
from __future__ import annotations

from enum import Enum
import json
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class WorkflowState(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowStepState(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CreateWorkflowStep(BaseModel):
    key: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=300)
    project_id: str = Field(min_length=1, max_length=128)
    task_id: str | None = Field(default=None, max_length=128)
    adapter: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_-]*$")
    prompt: str = Field(min_length=1, max_length=16_000)
    depends_on: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("depends_on")
    @classmethod
    def unique_dependencies(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("depends_on entries must be unique")
        return values


class CreateWorkflowRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=20_000)
    steps: list[CreateWorkflowStep] = Field(min_length=1, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_by: str = Field(default="user", min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_graph_references(self):
        keys = [step.key for step in self.steps]
        if len(keys) != len(set(keys)):
            raise ValueError("workflow step keys must be unique")
        known = set(keys)
        for step in self.steps:
            if step.key in step.depends_on:
                raise ValueError(f"step {step.key} may not depend on itself")
            unknown = set(step.depends_on) - known
            if unknown:
                raise ValueError(f"step {step.key} has unknown dependencies: {sorted(unknown)}")
        return self

    @field_validator("metadata")
    @classmethod
    def bounded_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > 64 * 1024:
            raise ValueError("workflow metadata may not exceed 64 KiB")
        return value


class WorkflowActionRequest(BaseModel):
    expected_version: int = Field(ge=1)
    actor: str = Field(default="user", min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=4000)


class TransitionWorkflowStepRequest(BaseModel):
    target_state: WorkflowStepState
    expected_version: int = Field(ge=1)
    actor: str = Field(default="system", min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=4000)


class WorkflowStep(BaseModel):
    step_id: str
    workflow_id: str
    key: str
    title: str
    project_id: str
    task_id: str | None
    adapter: str
    prompt: str
    depends_on: list[str]
    state: WorkflowStepState
    version: int
    created_at: str
    updated_at: str
    run_id: str | None = None
    dispatch_token: str | None = None
    dispatch_error: str | None = None


class WorkflowDispatchBlocker(BaseModel):
    step_id: str
    reason: str


class WorkflowDispatchResult(BaseModel):
    workflow_id: str
    dispatched_run_ids: list[str]
    blockers: list[WorkflowDispatchBlocker]


class WorkflowManifest(BaseModel):
    workflow_id: str
    title: str
    description: str
    state: WorkflowState
    steps: list[WorkflowStep]
    metadata: dict[str, Any]
    version: int
    created_by: str
    created_at: str
    updated_at: str


class WorkflowSummary(BaseModel):
    workflow_id: str
    title: str
    state: WorkflowState
    step_count: int
    ready_count: int = Field(description="Ready steps across the entire workflow, not only a project filter")
    version: int
    created_at: str
    updated_at: str


class WorkflowEvent(BaseModel):
    event_id: str
    workflow_id: str
    event_type: str
    actor: str
    payload: dict[str, Any]
    created_at: str
