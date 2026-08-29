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

#: A run that has not settled. The complement of TERMINAL_RUN_STATES, spelled out
#: rather than derived so it can be used directly in a SQL ``IN`` clause.
ACTIVE_RUN_STATES = {RunState.QUEUED, RunState.RUNNING}

OUTPUT_TAIL_LIMIT = 64 * 1024

# Role tags carried in a run's metadata so the dispatcher can tell a developer
# run (which triggers cross-review when it succeeds) from a review run (which
# raises an approval for the human) — and never review a review (no loops).
#
# They live here, not in the dispatcher, because ExecutionStore.create enforces
# one active run per role and importing the dispatcher from the store would be a
# cycle. agora.execution.dispatcher re-exports them for existing callers.
ROLE_KEY = "agora_role"
ROLE_WORKER = "worker"
ROLE_REVIEW = "review"
REVIEWS_RUN_KEY = "reviews_run_id"

#: The task version this run was validated against, recorded by
#: ``ExecutionStore.create`` in the transaction that inserts the run — after the
#: planned -> running bump that same transaction may perform, so it is the version
#: that should still be current when the run launches.
#:
#: It exists because a QUEUED row outlives the state it was checked against.
#: ``create`` refuses a request whose ``expected_task_version`` is stale, and
#: nothing re-asked that question of a row already inserted: a run queued while
#: the task was planned could still launch after the task had been edited,
#: cancelled or completed. ``_task_refusal`` compares this against the task's
#: current version at resume and again immediately before launch.
#:
#: A row without it (a database written before this key existed, or one edited by
#: hand) can only be lifecycle-checked; see ``_task_refusal``.
TASK_VERSION_KEY = "task_version"

#: Metadata keys the control plane owns. They decide which run gates which other
#: run (see ExecutionStore._reject_second_active_run), so a client that could set
#: them could name a role that matches nothing and slip a second concurrent paid
#: run past the same-role guard — or, for ``task_version``, claim a version the
#: task never had and walk past the staleness check. Refused on input, exactly as
#: AppendEventRequest refuses reserved event types: a client sending one is
#: confused or hostile, and silently rewriting its request would hide which of the
#: two happened.
RESERVED_METADATA_KEYS = frozenset({ROLE_KEY, REVIEWS_RUN_KEY, TASK_VERSION_KEY})


def role_of(metadata: dict[str, Any] | None) -> str:
    """The role a *stored* run's metadata declares, defaulting to ``worker``.

    A run recorded before roles existed carries no tag, and the honest reading of
    an untagged run is the one that gates: it was somebody's implementation work,
    so it counts as a worker run.

    Not for request metadata: a request never carries a role (see
    RESERVED_METADATA_KEYS). The role is a server-side argument to
    ``ExecutionStore.create``.
    """
    return str((metadata or {}).get(ROLE_KEY, ROLE_WORKER))


def task_version_of(metadata: dict[str, Any] | None) -> int | None:
    """The task version a *stored* run was validated against, or None if unknown.

    None is not "any version is fine" — it is "this row does not record the
    answer", which is the honest reading of a run inserted before
    ``TASK_VERSION_KEY`` existed or by hand. Callers must treat it as unknown and
    fall back to the checks they can still make, rather than inventing a baseline.

    A non-integer value is unknown too, for the same reason: a metadata blob is
    JSON that some other writer may have shaped, and guessing at it would be
    exactly the silent substitution this project forbids.
    """
    raw = (metadata or {}).get(TASK_VERSION_KEY)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


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

    @field_validator("metadata")
    @classmethod
    def reserved_metadata_keys(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Refuse control-plane keys in client metadata.

        The role decides which unfinished run blocks this one, so accepting it
        from a client would let ``{"agora_role": "anything"}`` match no existing
        run and start a second concurrent paid run on the same worktree. Refused
        rather than forced to ``worker``: quietly rewriting a field the caller
        explicitly set is the silent substitution this project forbids elsewhere,
        and a 422 naming the key is what tells an honest caller it is not theirs.
        Every run created from a request is a worker run; only the internal review
        dispatch passes ``role=review``.
        """
        present = sorted(RESERVED_METADATA_KEYS & value.keys())
        if present:
            raise ValueError(
                f"metadata key(s) {', '.join(present)} are reserved for the control plane"
            )
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
