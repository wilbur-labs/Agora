"""Tool-neutral contracts for delivery execution runs."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from agora.messages import MESSAGES


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
#: A question to the agent about the task (CR-75): answered in the task's worktree,
#: never cross-reviewed, and the task's state is untouched by it. Untagged rows never
#: mean this — ``role_of`` still reads absence as ``worker``, the role that gates.
ROLE_CHAT = "chat"
REVIEWS_RUN_KEY = "reviews_run_id"
#: Where a remote run executed — host, account, worktree, project root — written by
#: ``queue`` from the adapter's configuration at that moment and never again (W3-a U5).
#: Every later read of the run (deliverables, handoff) routes by THIS, not by whatever
#: the adapter is configured to today: a host or path changed after the fact must not
#: make an old run's files be read from a new place, or a remote path be read locally.
REMOTE_TARGET_KEY = "agora_remote"

#: Fixed title of the "no cross-review conclusion is on record" blocker raised by
#: ``ExecutionDispatcher._block_review_owed``. Fixed on purpose: it is the
#: idempotency key (with the task, the run it is scoped to, and the requester), so
#: a retry updates nothing and piles up nothing, and the varying detail lives in
#: the body.
#:
#: It says "no conclusion", not "did not start" (P1, 2026-08-30). It used to read
#: 跨评审未能启动 ("the cross-review failed to start"), which was true of the only
#: causes that existed when it was written — the three ``_dispatch_review`` failures
#: — and became false when this slice attached the same item to
#: ``REVIEW_OWED_NO_GATE_ITEM``, where the review *did* start, ran, and lost only its
#: gate write, and to ``REVIEW_OWED_REVIEW_CANCELLED``, where it may have started and
#: been stopped partway. What holds for every cause is that no review decision is on
#: record, which is also the fact ``ExecutionStore._has_review_gate_item`` releases
#: on. Which review, if any, ever ran is said in the cause-specific reason.
#:
#: It lives here rather than on the dispatcher because ``ExecutionStore`` has to
#: know that this blocker exists — the derived "a review is owed" refusal stands
#: down once the review path has recorded a human-clearable blocker for that run
#: (see ``ExecutionStore._reviews_owed``) — and importing the dispatcher from the
#: store would be a cycle. The store matches on
#: ``(task_id, run_id, requester, kind)`` rather than on this string, because a
#: title is redacted on insert and a comparison against a redacted copy is a
#: second thing to keep in step; the constant is shared so both sides still name
#: one item rather than two.
#:
#: CR-71: the title is now written in the reader's display language
#: (``messages.say("review.owed_blocker_title")``). This constant is that message's
#: ``zh`` text, kept for the callers and tests that name it; the idempotency lookup
#: matches every language's variant (``AttentionStore.create_once``'s
#: ``equivalent_titles``), so a switch between two attempts does not pile up a second
#: blocker.
REVIEW_OWED_BLOCKER_TITLE_KEY = "review.owed_blocker_title"
REVIEW_OWED_BLOCKER_TITLE = MESSAGES[REVIEW_OWED_BLOCKER_TITLE_KEY]["zh"]

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

#: The run's **identity**: what the control plane decided this run *is*, written
#: by ``ExecutionStore.create`` in the transaction that inserts the row and never
#: again. It is not the run's result, and the two live in the same
#: ``result_metadata`` JSON column only because that column is where a run's
#: non-tabular facts are kept.
#:
#: Everything in this slice reads identity to decide which run gates which other
#: run: the same-role overlap guard, the "this task is under review" refusal, the
#: derived "this task owes a review" predicate, the resume sweep's role ordering,
#: and ``_task_refusal``'s staleness check. So identity has two protections, one
#: per direction:
#:
#: - a client may not **set** it (``RESERVED_METADATA_KEYS`` below, refused by
#:   ``CreateRunRequest``): a settable role names a role nothing matches and slips
#:   a second concurrent paid run past the same-role guard, and a settable
#:   ``task_version`` claims a version the task never had.
#: - a *result* write may not **clear or change** it
#:   (``ExecutionStore._identity_preserved``): the column is written wholesale, so
#:   a settle that carried only a result — the whole of a pre-launch failure, where
#:   there is no result at all — used to overwrite the identity with ``{}``. A
#:   review run that failed before it started then read as the task's latest
#:   *worker* run, the derived predicate concluded no review was owed, and the next
#:   worker run walked straight past the review gate. A gate defeated by a field
#:   being cleared somewhere else entirely.
IDENTITY_METADATA_KEYS = frozenset({ROLE_KEY, REVIEWS_RUN_KEY, TASK_VERSION_KEY, REMOTE_TARGET_KEY})

#: Metadata keys the control plane owns, refused in client input. The same set as
#: ``IDENTITY_METADATA_KEYS`` **by construction, not by coincidence**: a key is
#: refused from a client exactly because the server owns it, so one list cannot
#: drift from the other. Refused rather than silently rewritten, exactly as
#: AppendEventRequest refuses reserved event types: a client sending one is
#: confused or hostile, and quietly fixing its request would hide which of the two
#: happened.
RESERVED_METADATA_KEYS = IDENTITY_METADATA_KEYS


def identity_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """The server-owned identity keys a *stored* run's metadata carries.

    Only the keys that are actually present: a row written before a key existed
    (or by hand) records no answer for it, and inventing a default here would
    manufacture identity for a run the control plane never gave one. ``role_of``
    and ``task_version_of`` are where the "what does absence mean?" question is
    answered, each for its own key.
    """
    return {
        key: value for key, value in (metadata or {}).items()
        if key in IDENTITY_METADATA_KEYS
    }


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
    #: ``work`` (default) is a worker run; ``chat`` is a question (CR-75) — the server
    #: files it under ``ROLE_CHAT``: no cross-review, no hand-off, the task not moved.
    kind: Literal["work", "chat"] = "work"
    #: Run on this registered remote environment (CR-4) instead of where the adapter's
    #: own config points. ``None`` keeps the adapter's configured place.
    environment_id: Optional[str] = Field(
        default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"
    )

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
    #: The same identity the summary of this row carries. Populated from the
    #: stored metadata by one shared reader: left to the defaults, a record
    #: fetched by id contradicted the list it appeared in — the list said
    #: `review`, the detail said `worker`.
    role: str = ROLE_WORKER
    reviews_run_id: str | None = None


class RemoteTarget(BaseModel):
    """Where a remote adapter runs, or where a run ran, as the UI may show it: the host,
    the account, the project root and (for a run) the worktree there. Never the
    credential reference or the known-hosts path."""
    host: str
    port: int
    user: str
    project_root: str
    workspace: str | None = None
    #: The registered environment a run was dispatched to (CR-4), when it was; a
    #: follow-up run (a review fix) goes back to the same one.
    environment_id: str | None = None


def remote_target_public(metadata: dict[str, Any] | None) -> "RemoteTarget | None":
    """The place a stored run executed, from its identity metadata (``REMOTE_TARGET_KEY``),
    in the public shape; ``None`` for a local run or a record this build cannot read."""
    raw = (metadata or {}).get(REMOTE_TARGET_KEY)
    if not isinstance(raw, dict):
        return None
    try:
        return RemoteTarget(
            host=str(raw["host"]), port=int(raw.get("port", 22)), user=str(raw["user"]),
            project_root=str(raw["project_root"]), workspace=str(raw.get("workspace")) if raw.get("workspace") else None,
            environment_id=str(raw["environment_id"]) if raw.get("environment_id") else None,
        )
    except (KeyError, TypeError, ValueError):
        return None


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
    #: The run's **identity**, carried on the summary because consumers place
    #: runs by it. A summary without them is not a smaller answer, it is a wrong
    #: one: every view that nests a review under the run it reviews reads these
    #: two keys, and their absence reads as "this is a worker run" — so a list of
    #: summaries drew every review as a top-level turn of its own.
    #:
    #: Defaults match ``role_of``: an untagged run counts as somebody's
    #: implementation work, and no reviewed-run link means it reviews nothing.
    role: str = ROLE_WORKER
    reviews_run_id: str | None = None
    #: Where the run executed, from its own record (W3-a U5); ``None`` for a run on this
    #: machine. Carried on the summary so a card states the host the run actually ran
    #: on, not the host its adapter is configured for today.
    remote: RemoteTarget | None = None


class AdapterCapability(BaseModel):
    name: str
    execution_mode: str
    attention_mode: str
    supports_tool_approval: bool
    supports_user_questions: bool
    detail: str
    #: ``None`` for an adapter that runs on this machine (W3-a U5).
    remote: RemoteTarget | None = None
