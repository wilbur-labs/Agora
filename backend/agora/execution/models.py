"""Tool-neutral contracts for delivery execution runs."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class RunState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"


TERMINAL_RUN_STATES = {
    RunState.SUCCEEDED,
    RunState.FAILED,
    RunState.TIMED_OUT,
    RunState.CANCELLED,
    RunState.ABANDONED,
}

OUTPUT_TAIL_LIMIT = 64 * 1024


class CreateRunRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=128)
    adapter: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_-]*$")
    # Prompts are passed as one argv element; stay below Windows' process command-line limit.
    prompt: str = Field(min_length=1, max_length=16_000)
    timeout_seconds: int = Field(default=600, ge=1, le=7200)
    actor: str = Field(default="user", min_length=1, max_length=128)
    expected_task_version: int = Field(ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("prompt")
    @classmethod
    def prompt_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt may not be blank")
        return value


class CancelRunRequest(BaseModel):
    actor: str = Field(default="user", min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=4000)
    expected_version: int = Field(ge=1)


class ExecutionRun(BaseModel):
    run_id: str
    task_id: str
    project_id: str
    adapter: str
    state: RunState
    prompt: str
    workspace: str
    command: list[str]
    timeout_seconds: int
    pid: int | None
    exit_code: int | None
    stdout_tail: str
    stderr_tail: str
    result_metadata: dict[str, Any]
    error_message: str | None
    version: int
    queued_at: str
    started_at: str | None
    finished_at: str | None
    actor: str


class RunSummary(BaseModel):
    run_id: str
    task_id: str
    project_id: str
    adapter: str
    state: RunState
    version: int
    queued_at: str
    started_at: str | None
    finished_at: str | None
    exit_code: int | None


class AdapterCapability(BaseModel):
    name: str
    execution_mode: str
    attention_mode: str
    supports_tool_approval: bool
    supports_user_questions: bool
    detail: str
