"""Bounded async dispatch of durable execution runs."""
from __future__ import annotations

import asyncio
import inspect
import os
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

from agora.projects import ProjectRegistry
from agora.environments import ProcessHandle
from agora.fsprobe import PathProbe, probe_path
from agora.orchestration.runtime import RuntimeLaunchError, resolve_runtime_command
from agora.orchestration.provider_usage import RuntimeResultFormat, normalize_native_output
from agora.tasks.store import TaskNotFoundError, TaskStore
from agora.attention.store import AttentionStore
from agora.attention.models import AttentionKind, AttentionUrgency, CreateAttentionRequest, REVIEW_REQUESTER
from agora.attention.bridges.codex_app_server import CodexAppServerError, CodexAppServerRunner
from agora.attention.bridges.codex_broker import CodexApprovalBroker

from agora.workspaces.naming import worktree_branch

from .adapters import (
    EVENT_FORMAT_CLAUDE_STREAM_JSON,
    EVENT_FORMAT_CODEX_JSONL,
    EVENT_FORMAT_KIRO_STREAM_JSON,
    SESSION_ID_CALLER,
    ExecutionAdapter,
)
from .remote_wiring import (
    adapter_for_recorded_target, describe_target, is_remote, remote_components_for,
    remote_target_of, remote_target_record, remote_workspace_of, same_remote_host,
)
from .runlog import RunLogStore
from .sessions import (
    MODE_RESUME,
    AgentSessionStore,
)
from .transcript import TranscriptReader, reader_for
from .deliverables import (
    MAX_FILES,
    baseline_state,
    is_ready_worktree,
    list_changes,
    read_file,
    snapshot_changes,
)
from .models import (
    REMOTE_TARGET_KEY,
    CancelRunRequest,
    CreateRunRequest,
    ExecutionRun,
    RunState,
    OUTPUT_TAIL_LIMIT,
    # Role tags for cross-review orchestration. Defined in .models because
    # ExecutionStore.create enforces one unfinished run per role and importing
    # the dispatcher from the store would be a cycle; re-exported from here,
    # which is where every existing caller imports them from.
    ROLE_KEY,
    ROLE_REVIEW,
    ROLE_WORKER,
    REVIEWS_RUN_KEY,
    REVIEW_OWED_BLOCKER_TITLE,
    role_of,
)
from .store import (
    ExecutionStore,
    REVIEW_OWED_NO_GATE_ITEM,
    REVIEW_OWED_NO_REVIEW_RUN,
    REVIEW_OWED_REVIEW_CANCELLED,
    RunConflictError,
    RunNotFoundError,
    RunValidationError,
)
from .security import (
    longest_environment_secret,
    redact_text,
    safe_cut,
)


#: Most bytes buffered while waiting for a newline. Larger than any settlement
#: event these CLIs emit in practice, and small enough that a runaway stream
#: cannot exhaust the process. Reached, the buffer is flushed as one fragment
#: rather than grown.
_MAX_PENDING_LINE = 4 * 1024 * 1024

_REVIEW_PROMPT_BUDGET = 12_000
_REVIEW_FILE_CHARS = 1_500


def workspace_unavailable_reason(probe: PathProbe) -> str:
    """Why a workspace cannot be used, distinguishing gone from unobservable.

    "workspace not found" is a factual claim, so only a definite absence may carry
    it. A stat that could not be completed is unknown and says so, because the two
    call for opposite responses: reprovision, versus fix the permission.
    """
    if probe.absent:
        return "workspace not found"
    if probe.unknown:
        return f"workspace could not be inspected ({probe.reason()})"
    return "workspace is not a directory"


#: Stored in ``_session_noted`` instead of a session id once a turn has been
#: failed closed for naming two sessions. Not one of the ids: which one is on
#: record is exactly what is in doubt, and using either would let the next line
#: re-mark the run as though the conflict had just appeared.
_CONFLICT_NOTED = "__conflict__"

#: How long the output drain may take **after** the process has ended and its
#: job has been terminated. Unbounded, this was a hang rather than a delay: a
#: background process that inherited the pipes keeps them open, `proc.wait()`
#: returns, and nothing ever reaches EOF — so the run never settled and its
#: worktree lock was never released, long after `timeout_seconds` had passed.
_DRAIN_TIMEOUT_SECONDS = 20

class _ReaderGate:
    """Whether a run's stdout reader may still write anything.

    Held **by the reader itself**, not in a map on the dispatcher. The first
    version kept a bounded set of closed run ids, which is a cache pretending to
    be a guard: after 256 more such runs the oldest id was evicted, and a reader
    still attached to a long-lived pipe could write again — including a session
    event that would put an old id back over the one a later turn had
    established. A flag the reader closes over cannot be evicted, and it costs
    nothing to keep for as long as the reader lives.
    """

    __slots__ = ("open",)

    def __init__(self) -> None:
        self.open = True

    def close(self) -> None:
        self.open = False

class ExecutionDispatcher:
    # Which usage reader parses which event format. Keyed by the format an
    # adapter **emits**, not by adapter name: the argv already decides the format
    # (``--json`` / ``--output-format stream-json``), and a second adapter-keyed
    # table was a second place to keep that decision, free to disagree with the
    # first. It did disagree — the table said only claude yielded usage while
    # codex's JSONL reader had existed, unreachable, the whole time, because the
    # codex argv passed no ``--json``.
    #
    # An adapter whose format is absent here reports usage as unavailable, which
    # is the honest answer for a format with no reader.
    _USAGE_RESULT_FORMATS = {
        EVENT_FORMAT_CODEX_JSONL: RuntimeResultFormat.CODEX_JSONL_V1,
        EVENT_FORMAT_CLAUDE_STREAM_JSON: RuntimeResultFormat.CLAUDE_STREAM_JSON_V1,
        EVENT_FORMAT_KIRO_STREAM_JSON: RuntimeResultFormat.KIRO_STREAM_JSON_V1,
    }

    #: Of those, the formats whose provider actually reports **token counts**.
    #: kiro's ACP stream reports credits and no tokens at all, and its reader says
    #: so (``token_measurement="unavailable"``) — so listing it as usage-readable
    #: made the Environments page promise a token figure that path can never
    #: produce. Having a reader and yielding tokens are two different facts, and
    #: this is the second one.
    _TOKEN_CAPTURE_FORMATS = frozenset({
        EVENT_FORMAT_CODEX_JSONL,
        EVENT_FORMAT_CLAUDE_STREAM_JSON,
    })

    def __init__(
        self,
        store: ExecutionStore,
        projects: ProjectRegistry,
        adapters: dict[str, ExecutionAdapter],
        *,
        max_concurrent_global: int = 4,
        max_concurrent_per_project: int = 2,
        allowed_workspace_roots: list[Path] | None = None,
        auto_review: bool = False,
        sessions: AgentSessionStore | None = None,
        run_logs: RunLogStore | None = None,
    ):
        if max_concurrent_global < 1 or max_concurrent_per_project < 1:
            raise ValueError("execution concurrency limits must be positive")
        self.store = store
        self.projects = projects
        self.adapters = adapters
        # Both default off the run store's own database path, so "where is
        # Agora's state" keeps one answer and no caller can put the sessions on a
        # different file from the runs that use them (the invariant
        # resolve_control_plane_db_path exists to hold). Injectable for tests.
        self.sessions = sessions or AgentSessionStore(store.db_path)
        self.run_logs = run_logs or RunLogStore(Path(store.db_path).parent / "run-logs")
        # Opt-in: when true, a succeeded developer run auto-dispatches an
        # independent review run by the project's reviewer agent. Default off
        # keeps single-run behavior (and existing tests) unchanged.
        self.auto_review = auto_review
        self.allowed_workspace_roots = [root.expanduser().resolve() for root in (allowed_workspace_roots or [])]
        self._global_limit = asyncio.Semaphore(max_concurrent_global)
        self._project_limits: defaultdict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(max_concurrent_per_project)
        )
        # One run at a time per worktree. Workspaces are keyed by adapter, so two
        # runs of the same project+adapter resolve to the SAME directory, and
        # max_concurrent_per_project defaults to 2 — without this they would write
        # one git worktree concurrently (clobbered files, index.lock contention)
        # and each run's deliverable snapshot would claim the other's files.
        # Acquired BEFORE the capacity limits (see execute) so a run waiting on a
        # busy worktree does not sit on global/project capacity meanwhile.
        self._workspace_limits: defaultdict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(1)
        )
        self._active: dict[str, asyncio.subprocess.Process] = {}
        #: The job object each run's process tree belongs to, on Windows. Keyed
        #: by run so that `cancel` and the guarded wrapper — neither of which
        #: sees the spawn — can stop the tree and not just the CLI.
        self._jobs: dict[str, object] = {}

        # run_id -> the session id already written for it while streaming, so the
        # observed id is persisted once rather than on every line of output.
        # Popped when the run settles.
        self._session_noted: dict[str, str] = {}
        # Per-run pre-launch worktree state, so an abandoned run can still record
        # what it delivered (the CancelledError path lives outside execute()).
        # A missing or None entry means the baseline was never established, which
        # snapshot_changes reads as "attribution unknown" — the correct answer.
        self._baselines: dict[str, tuple[dict[str, dict], bool] | None] = {}
        #: Remote runs' pre-run state, kept apart: it is read on the host, and the
        #: cancel path must freeze the run's files there, not on this machine.
        self._remote_baselines: dict[str, tuple | None] = {}
        # Startup recovery bookkeeping, read by ``resume_queued``'s ordering guard
        # and by ``start_recovery``'s receipt. ``_reviews_owed_reconciled`` is "the
        # reconciliation step ran in this process", which is true even when it
        # could not read anything: an attempted step that recorded a failure is a
        # different fact from a step that never ran, and only the second one is a
        # sequencing bug. ``reviews_owed_unreadable`` carries the failure itself,
        # because a reconciliation that could not run is a fact about this process
        # an operator has to be told, not a silent skip.
        self._reviews_owed_reconciled = False
        self.reviews_owed_unreadable: str | None = None
        # The *partial* failures of the same step, which used to be entirely silent:
        # one ``{task_id, run_id, cause, error}`` per owed run the reconciliation read
        # but could not record a blocker for, either because the write failed or
        # because its cause has no true sentence to record. Each one leaves the
        # derived review-owed refusal standing with nothing in the human's inbox
        # saying so, which is a fact about this process for the same reason
        # ``reviews_owed_unreadable`` is. Safety no longer depends on this list —
        # ``_resume_refusal`` derives the refusal itself (M1) — but an operator still
        # has to be told the reconciliation did not finish.
        self.reviews_owed_unrecorded: list[dict[str, str]] = []
        # Scheduled task -> its run_id. The mapping (rather than a bare set) exists
        # because a cancelled task has to be settled from *outside* the coroutine:
        # a task cancelled before the loop ever ran its body has no body to raise
        # CancelledError in, so only the canceller knows which row is now orphaned.
        self._scheduled: dict[asyncio.Task[Any], str] = {}

    def draining_for(self, task_id: str, adapter: str) -> bool:
        """Whether any process still being read belongs to this conversation.

        Asked instead of paging through a task's runs and testing each one. That
        page was capped, and a cap here fails toward *allowing* a session clear:
        two hundred newer runs push an older cancelled-but-still-draining one off
        it, the clear is accepted, and that process's next session event writes
        the id straight back. This set is bounded by the concurrency limit
        instead, so there is nothing to cap.
        """
        for run_id in list(self._active):
            try:
                run = self.store.require(run_id)
            except RunNotFoundError:
                # Deleted under us. Nothing to attribute, and refusing on it
                # would block the clear forever.
                continue
            if run.task_id == task_id and run.adapter == adapter:
                return True
        return False

    def is_draining(self, run_id: str) -> bool:
        """Whether a process for this run is still being read.

        A cancel settles the row *before* the process is stopped and its stdout
        drained, so a terminal state alone is not "the transcript is finished".
        Callers that would tell a reader to stop polling have to ask this too, or
        the tail a killed agent produced is written after the last read and never
        shown.
        """
        return run_id in self._active

    @staticmethod
    def _workspace_key(workspace: str | Path) -> str:
        """Normalize a workspace path so two runs naming the same directory
        differently still contend for the same slot."""
        try:
            return str(Path(workspace).expanduser().resolve())
        except OSError:  # pragma: no cover - defensive
            return str(workspace)

    def queue(
        self,
        request: CreateRunRequest,
        *,
        role: str = ROLE_WORKER,
        reviews_run_id: str | None = None,
    ) -> ExecutionRun:
        """Create a queued run.

        ``role`` defaults to ``worker`` and is a server-side argument: anything
        arriving from a client is a worker run (``CreateRunRequest`` refuses the
        reserved metadata keys), and only ``_dispatch_review`` below passes
        ``ROLE_REVIEW``. The role decides which unfinished run blocks this one, so
        a client-settable role would defeat that guard outright.
        """
        task = self.store.tasks.get(request.task_id)
        if task is None:
            raise TaskNotFoundError(request.task_id)
        adapter = self.adapters.get(request.adapter)
        if adapter is None:
            raise RunValidationError(f"Unknown or disabled execution adapter: {request.adapter}")
        try:
            project = self.projects.get(task.project_id)
            if is_remote(adapter):
                # A remote adapter's worktree is a path on *its* host (W3-a U5). It is
                # neither resolved nor confined here — both would read this machine's
                # filesystem; the remote provisioner confines it under the configured
                # remote project root on the host when the run executes.
                workspace: Path | PurePosixPath = remote_workspace_of(adapter, project)
                # Where it executes, fixed now: every later read of this run routes by
                # this record, not by what the adapter is configured to then.
                request = request.model_copy(update={
                    "metadata": {**request.metadata, REMOTE_TARGET_KEY: remote_target_record(adapter.remote)},  # type: ignore[arg-type]
                })
            else:
                workspace = adapter.workspace(project.workspaces)
        except KeyError as exc:
            raise RunValidationError(str(exc)) from None
        if not is_remote(adapter):
            workspace = workspace.resolve()
            if not self._workspace_is_allowed(workspace, project.root):
                raise RunValidationError("Execution workspace is outside the configured allowed roots")
        return self.store.create(
            request,
            project_id=project.project_id,
            workspace=workspace,
            stored_command=adapter.stored_command(),
            role=role,
            reviews_run_id=reviews_run_id,
            # The flag, not the answer: the store asks the derived "does this task
            # owe a review?" question inside its own transaction. Computing it here
            # is the TOCTOU hole (see ExecutionStore._reject_review_owed).
            auto_review=self.auto_review,
        )

    def schedule(self, run_id: str) -> None:
        task = asyncio.create_task(self._execute_guarded(run_id), name=f"agora-run-{run_id}")
        self._scheduled[task] = run_id
        task.add_done_callback(self._scheduled_done)

    def start_recovery(self) -> dict[str, Any]:
        """The startup recovery sequence, in the one order that is correct.

        Returns a receipt: ``{"reviews_owed_reconciled": [...],
        "reviews_owed_unreadable": str | None, "reviews_owed_unrecorded": [...],
        "queued_runs_refused": [...]}``.

        **Why this is a function and not two calls in ``lifespan``.**
        ``reconcile_reviews_owed`` must run before ``resume_queued``: a blocker it
        raises is a gate, and ``revalidate_queued`` refuses a persisted queued
        worker run on a gated task (``ExecutionStore._resume_refusal``). Run the
        other way round, the sweep judges the queue against gates that are about to
        exist and admits a worker run the create path would refuse. Both must also
        finish before the API serves, so no client can observe the state that
        neither step has reconciled yet.

        That dependency used to live only in the order of two statements in
        ``agora.api.app.lifespan``, with a test to pin it — which catches a
        reordering in *that* file and nothing else, and says nothing to somebody
        adding a second startup path. It lives here now, next to the two steps it
        orders, and ``resume_queued`` refuses to run first (see its guard) so a
        reordering fails loudly instead of silently admitting runs.

        **Order alone is not enough, because the first step can fail partway
        (M1, 2026-08-30).** ``reconcile_reviews_owed`` swallows its own failures by
        design — an unreadable ``reviews_owed`` read, and a per-task blocker write
        that fails is skipped so one unrecordable task does not cost the rest theirs
        — and this function then called ``resume_queued`` anyway. Correct order,
        incomplete gates: the sweep judged the queue against blockers that were never
        written, and a QUEUED worker was scheduled onto work that succeeded and was
        never reviewed. The fix is in ``_resume_refusal``, which now derives the
        review-owed refusal itself rather than trusting that the gates it reads were
        all raised; both failure kinds are reported in this receipt so an incomplete
        reconciliation is a stated fact rather than a queue that quietly did less.
        """
        reconciled = self.reconcile_reviews_owed()
        refused = self.resume_queued()
        return {
            "reviews_owed_reconciled": reconciled,
            "reviews_owed_unreadable": self.reviews_owed_unreadable,
            "reviews_owed_unrecorded": list(self.reviews_owed_unrecorded),
            "queued_runs_refused": refused,
        }

    def resume_queued(self) -> list[dict[str, Any]]:
        """Reschedule persisted QUEUED runs — after re-checking the create-time gates.

        A QUEUED row was validated when it was inserted and never again, so on an
        upgrade from a database written before those gates existed, or after a
        crash, rescheduling it blindly starts a worker run while its predecessor's
        review is still unsettled or a human gate is still open.
        ``ExecutionStore.revalidate_queued`` re-asks those questions and settles the
        rows that cannot be launched; only what it admits is scheduled here. The
        refused rows are returned — and recorded on their task's timeline — rather
        than dropped silently.

        **Must not run before ``reconcile_reviews_owed``**, and says so rather than
        trusting a caller to keep two statements in the right order: the
        reconciliation raises the gates this sweep judges the queue against, so
        going first means admitting a queued worker run whose task is about to be
        gated. ``start_recovery`` is the sequence. The guard is scoped to
        ``auto_review`` because that is exactly when the reconciliation has anything
        to do — with the flag off no review can be owed, nothing is raised, and
        there is no order to get wrong.

        **Order is necessary and not sufficient (M1).** The reconciliation can run and
        still not finish — an unreadable read, or a blocker write that failed — and
        this sweep then judged the queue against gates that do not exist. So the
        ``auto_review`` flag is also passed *into* the sweep, where
        ``_resume_refusal`` derives the review-owed refusal itself instead of relying
        on a blocker having been written. The flag is passed, never a precomputed
        verdict, for the reason ``create`` gives.
        """
        if self.auto_review and not self._reviews_owed_reconciled:
            raise RuntimeError(
                "resume_queued() ran before reconcile_reviews_owed(): the resume sweep "
                "judges queued runs against gates the reconciliation has not raised "
                "yet, so it would admit a worker run on a task that owes a review. "
                "Call start_recovery(), which orders the two."
            )
        admitted, refused = self.store.revalidate_queued(auto_review=self.auto_review)
        for entry in refused:
            # A refused *review* run was settled CANCELLED by the sweep, which — since
            # N1 — leaves the worker run it was going to review still owing one. The
            # reconciliation ran before this sweep, so it cannot have seen these; the
            # blocker is written here or not at all until the next start.
            if entry.get("role") == ROLE_REVIEW:
                self._block_reviews_owed_for(str(entry["task_id"]))
        for run_id in admitted:
            self.schedule(run_id)
        return refused

    async def _freeze_after_stop(self, current: ExecutionRun) -> dict | None:
        """The delivery of a run stopped from outside its own path (a dispatcher stop,
        an unhandled error), frozen where the run executed. A remote run's worktree is
        on its host: asking this machine's filesystem about a POSIX path answers
        nothing, and would settle files that sit on the host as "could not establish" —
        after which the next run's baseline absorbs them and nobody owns them."""
        recorded = remote_target_of(current)
        if recorded is None:
            return await asyncio.to_thread(
                snapshot_changes,
                Path(current.workspace).expanduser().resolve(),
                self._baselines.get(current.run_id),
            )
        try:
            pinned = adapter_for_recorded_target(self.adapters.get(current.adapter), recorded)
            components = remote_components_for(pinned, self.projects)
        except Exception:  # noqa: BLE001 - the host cannot be reached as recorded: unknown, not empty
            return None
        try:
            return await components.deliverables.snapshot_changes(
                Path(current.workspace), self._remote_baselines.get(current.run_id),
            )
        except Exception:  # noqa: BLE001
            return None
        finally:
            await components.aclose()

    async def _execute_guarded(self, run_id: str) -> ExecutionRun:
        try:
            return await self.execute(run_id)
        except asyncio.CancelledError:
            proc = self._active.get(run_id)
            if proc is not None:
                await self._stop(proc, self._jobs.get(run_id))
            current = self.store.require(run_id)
            if current.state == RunState.RUNNING:
                # A run stopped mid-flight may already have written files. Freeze
                # them before the terminal transition, or its record would read
                # "unknown" for work that is sitting right there on disk. The
                # process is already stopped, so nothing else is writing.
                delivered = await self._freeze_after_stop(current)
                self.store.abandon(
                    run_id,
                    expected_version=current.version,
                    reason="dispatcher stopped before the process completed",
                    deliverables=delivered,
                )
            elif current.state == RunState.QUEUED:
                # Cancelled before launch — typically while waiting behind a busy
                # worktree or for capacity. See _settle_unstarted for why it may
                # not be left QUEUED, and note that this branch only covers a
                # cancellation the coroutine actually *received*; one that lands
                # before the loop first runs this body never reaches here, which
                # is why schedule()/shutdown() settle it from outside too.
                self._settle_unstarted(run_id)
            raise
        except Exception as exc:
            current = self.store.require(run_id)
            if current.state == RunState.CANCELLED and (
                self._baselines.get(run_id) is not None or self._remote_baselines.get(run_id) is not None
            ):
                # Cancelled, and then something failed on the way out — a locked
                # database, a full disk, anything `_settle_session` can raise.
                # Returning the row here dropped the snapshot *and* the baseline,
                # so files the agent had already written were absorbed by the
                # next run's baseline and attributed to nobody. The cancel path
                # freezes them; so does this one.
                try:
                    delivered = await self._freeze_after_stop(current)
                    current = self.store.require(run_id)
                    return self.store.record_cancelled_output(
                        run_id,
                        expected_version=current.version,
                        stdout_tail=current.stdout_tail,
                        stderr_tail=current.stderr_tail,
                        exit_code=None,
                        deliverables=delivered,
                    )
                except Exception:
                    # The row is already terminal and the original failure is the
                    # one worth reporting; this is a best effort on top of it.
                    return self.store.require(run_id)
            if current.state in {RunState.QUEUED, RunState.RUNNING}:
                # The same reasoning as the cancel branch above, and it was
                # missing here: a run whose process had started may already have
                # written files, and settling without freezing them left its
                # record reading "unknown" for work sitting on disk — which the
                # *next* run's baseline then absorbed, so those files ended up
                # attributed to nobody. Only for RUNNING: a QUEUED run never had
                # a workspace of its own to snapshot.
                delivered = None
                if current.state == RunState.RUNNING:
                    try:
                        delivered = await self._freeze_after_stop(current)
                    except Exception:
                        # A file removed between `is_file()` and `stat()`, a
                        # thread pool refusing work during shutdown. Escaping
                        # here left the run RUNNING in the database while the
                        # `finally` below dropped its baseline — so the next run
                        # in that worktree absorbed its files, and nothing ever
                        # settled the row. Settling without a delivery record is
                        # worse than one; hanging on RUNNING for ever is worse
                        # than both.
                        delivered = None
                    # Re-read AFTER the snapshot, and branch on what it says —
                    # the same discipline the settle path documents and this one
                    # was missing. Snapshotting a large worktree takes long
                    # enough for a cancel to land inside it; the version captured
                    # before then made `finish` conflict and return, so the
                    # snapshot was computed and thrown away, and the files it
                    # described were absorbed by the next run's baseline.
                    current = self.store.require(run_id)
                    if current.state == RunState.CANCELLED:
                        return self.store.record_cancelled_output(
                            run_id,
                            expected_version=current.version,
                            stdout_tail=current.stdout_tail,
                            stderr_tail=current.stderr_tail,
                            exit_code=None,
                            deliverables=delivered,
                        )
                    if current.state not in {RunState.QUEUED, RunState.RUNNING}:
                        # Settled by someone else while we snapshotted. The row is
                        # authoritative; the deliverables it holds are theirs.
                        return current
                return self.store.finish(
                    run_id,
                    RunState.FAILED,
                    expected_version=current.version,
                    exit_code=None,
                    stdout_tail=current.stdout_tail,
                    stderr_tail=current.stderr_tail,
                    error_message=redact_text(f"dispatcher error: {exc}"),
                    deliverables=delivered,
                )
            return current
        finally:
            self._baselines.pop(run_id, None)

    def _settle_unstarted(self, run_id: str) -> None:
        """Terminate a run that was cancelled before its process ever started.

        Left QUEUED it survives as a run nothing will ever execute, and the
        same-role guard in ``ExecutionStore.create`` then refuses **every** new
        worker run for that task — permanently, since no restart clears a QUEUED
        row (``recover_abandoned`` only settles RUNNING ones). A human would have
        to cancel it by hand through the API.

        CANCELLED, not ABANDONED: no process was ever started, so nothing was
        abandoned mid-flight, and ABANDONED is reserved for a run whose process we
        lost track of. It is also the only terminal state a QUEUED run may legally
        reach (see ``store._ALLOWED``).

        No deliverable snapshot: the baseline is established after the readiness
        gate, which this run never reached, and a snapshot without a baseline is
        attribution guesswork.

        Idempotent and state-checked, because it is called from three places (the
        coroutine's own CancelledError handler, the scheduled task's done callback,
        and shutdown's sweep) and at most one of them may win.

        The winner records the review-owed blocker if the run it cancelled was a
        *review* run. This is the sweep N1 was reported against: shutdown cancels
        queued reviews with no human involved, and a cancelled review no longer
        accounts for the obligation, so the gate item has to be written here or the
        next start is the first time anybody hears about it.
        """
        try:
            current = self.store.require(run_id)
        except RunNotFoundError:
            return
        if current.state != RunState.QUEUED:
            return
        try:
            cancelled = self.store.cancel(
                run_id,
                CancelRunRequest(
                    expected_version=current.version,
                    reason="dispatcher stopped before the run started",
                    actor=current.actor,
                ),
            )
        except (RunConflictError, RunNotFoundError):
            # It settled underneath us (a user cancel landing in the same moment,
            # or another of the three callers). Never let this mask the
            # cancellation being reported.
            return
        self._block_reviews_owed_after_cancel(cancelled)

    def _scheduled_done(self, task: asyncio.Task[Any]) -> None:
        run_id = self._scheduled.pop(task, None)
        if task.cancelled():
            # The task ended cancelled. If the cancellation arrived before the
            # loop first ran the coroutine, its body — and therefore its own
            # except CancelledError — never executed, and this callback is the
            # only place that still knows the row exists. Cheap and idempotent
            # when the coroutine did settle it.
            if run_id is not None:
                self._settle_unstarted(run_id)
            return
        task.exception()

    async def execute(self, run_id: str) -> ExecutionRun:
        initial = self.store.require(run_id)
        if initial.state != RunState.QUEUED:
            return initial
        # Workspace slot FIRST, then run capacity. Every run acquires in this same
        # order, so no pair can hold one another's resource — and a run queued
        # behind a busy worktree waits without occupying global/project capacity
        # that other projects could be using (two same-worktree runs would
        # otherwise starve every other project while only one process ran).
        async with (
            self._workspace_limits[self._workspace_key(initial.workspace)],
            self._global_limit,
            self._project_limits[initial.project_id],
        ):
            run = self.store.require(run_id)
            if run.state != RunState.QUEUED:
                return run
            # The create-time questions, re-asked at the last moment the run is
            # still QUEUED. This run may have waited here for a long time — behind
            # its worktree, or for capacity — and in that time its task can be
            # cancelled, carried to `verified` by the review gate, edited into a new
            # version, or have a gate raised on the work that came before it, all of
            # which would make `create` refuse the identical request now. Settles
            # the row CANCELLED with a stated reason rather than launching an agent
            # against a task that no longer wants it, or on top of work a human is
            # still holding a gate on. Items this run's own *bridge* raised are
            # excluded, so a prompt it raised itself never becomes its own refusal —
            # by provable origin, not by run_id (M2); see revalidate_before_launch
            # and ExecutionStore._open_gate_items.
            if self.store.revalidate_before_launch(run_id, auto_review=self.auto_review) is not None:
                settled = self.store.require(run_id)
                # Same as the resume sweep: a review run refused at launch was settled
                # CANCELLED, and that no longer discharges the review obligation (N1).
                await asyncio.to_thread(self._block_reviews_owed_after_cancel, settled)
                return settled
            # Role/target-run are the run's identity, written by create and
            # preserved across every settle (ExecutionStore._identity_preserved).
            # Read here because this is where the branching needs them.
            role = role_of(run.result_metadata)
            reviews_run_id = (run.result_metadata or {}).get(REVIEWS_RUN_KEY)
            adapter = self.adapters.get(run.adapter)
            if remote_target_of(run) is not None:
                # Where this run executes was fixed when it was queued (REMOTE_TARGET_KEY),
                # and that — not the adapter's configuration today — picks the path.
                # Everything below asks this machine's filesystem and process tree; a
                # remote run asks its recorded host instead, or is refused if the adapter
                # no longer reaches it (W3-a U5). A run with no record was queued as a
                # local run and runs locally, whatever the adapter has since become.
                return await self._execute_remote(run, adapter, role, reviews_run_id)
            workspace = Path(run.workspace).expanduser().resolve()
            if adapter is None:
                return self.store.finish(
                    run_id, RunState.FAILED, expected_version=run.version, exit_code=None,
                    stdout_tail="", stderr_tail="", error_message="adapter is no longer configured",
                )
            try:
                project = self.projects.get(run.project_id)
            except KeyError:
                project = None
            if project is None or not self._workspace_is_allowed(workspace, project.root):
                return self.store.finish(
                    run_id, RunState.FAILED, expected_version=run.version, exit_code=None,
                    stdout_tail="", stderr_tail="", error_message="workspace is no longer allowed",
                )
            workspace_probe = probe_path(workspace)
            if not workspace_probe.is_dir():
                # The run fails either way, but the reason it records has to be the
                # reason. `is_dir()` answered False both for a workspace that is gone
                # and for one this process could not stat, so an ACL-denied worktree
                # was recorded as "not found" and sent the operator looking for a
                # directory that is still there.
                return self.store.finish(
                    run_id, RunState.FAILED, expected_version=run.version, exit_code=None,
                    stdout_tail="", stderr_tail="",
                    error_message=workspace_unavailable_reason(workspace_probe),
                )

            try:
                running = self.store.start(run_id, expected_version=run.version)
            except RunConflictError:
                return self.store.require(run_id)

            # Resolve again immediately before launch. A local writer can still swap a
            # component after this check on platforms without directory-handle cwd APIs.
            workspace = workspace.resolve()
            recheck = probe_path(workspace)
            if not self._workspace_is_allowed(workspace, project.root):
                changed = "workspace changed before process start"
            elif recheck.is_dir():
                changed = None
            elif recheck.unknown:
                # Confirmed a directory a moment ago and now unobservable. Fail closed
                # either way, but do not report an unreadable workspace as a changed
                # one: the operator would go looking for a swap that never happened.
                changed = f"workspace became unreadable before process start ({recheck.reason()})"
            else:
                changed = "workspace changed before process start"
            if changed is not None:
                return self.store.finish(
                    run_id, RunState.FAILED, expected_version=running.version, exit_code=None,
                    stdout_tail="", stderr_tail="", error_message=changed,
                )
            # Never launch an agent unless the workspace is exactly this project/
            # adapter's provisioned linked worktree: its own top-level, a linked
            # worktree sharing the project repo, on the expected branch, with a
            # HEAD. This binds execution to a provisioned workspace, so an agent
            # can never write into a repo it does not own (a repo root, the
            # project root, the outer Agora checkout) nor an unrelated worktree.
            # Off the event loop: the check shells out to git. Fail closed.
            expected_branch = worktree_branch(run.project_id, run.adapter)
            ready = await asyncio.to_thread(
                is_ready_worktree, workspace, project.root, expected_branch
            )
            if not ready:
                return self.store.finish(
                    run_id, RunState.FAILED, expected_version=running.version, exit_code=None,
                    stdout_tail="", stderr_tail="",
                    error_message="workspace is not a provisioned worktree for this project/adapter; provision it before running",
                )
            # What this worktree already had changed before we launch. The queue
            # allows another run on an already-RUNNING task, and a retry reuses
            # the same worktree, so without this the next run's snapshot would
            # claim the previous run's files. Captured after the readiness gate
            # so it only ever reads a proven worktree.
            baseline = await asyncio.to_thread(baseline_state, workspace)
            self._baselines[run_id] = baseline
            if adapter.bridge_mode == "codex_app_server":
                # This bridge keeps its own in-process conversation over JSON-RPC
                # and has no resume path Agora has verified, so it carries no
                # provider session. Said on the task's session record rather than
                # left blank: a window that showed a continuing conversation over
                # turns that each start from nothing is the exact failure
                # adr/0007 forbids. Tracked as CR-39 (defer) — the trigger is
                # test_sessions.py::test_app_server_records_unsupported_session.
                self.sessions.mark_unsupported(
                    task_id=run.task_id, adapter=run.adapter, workspace=workspace, role=role,
                    reason=(
                        "codex app-server 桥不参与会话续接：它自己在进程内持有对话，"
                        "Agora 没有实测过它的 resume 路径。这条链路上每一轮都是独立调用。"
                    ),
                )
                return await self._execute_codex_app_server(
                    running, adapter, workspace, role, reviews_run_id, baseline,
                )
            # Which conversation this turn belongs to, decided immediately before
            # the command is built because the command *shape* depends on the
            # answer (resume templates differ from start templates; see
            # ExecutionAdapter.build_command). A refusal here settles the run with
            # its reason and never falls back to starting a new session — that
            # fallback would answer the user in a fresh context while the window
            # still showed the conversation they believe they are in.
            decision = self.sessions.decide(
                task_id=run.task_id,
                adapter=run.adapter,
                workspace=workspace,
                can_resume=adapter.can_resume(),
                caller_chooses_id=adapter.session_id_source == SESSION_ID_CALLER,
                # The role is part of the session's identity: a review turn gets
                # its own conversation rather than resuming the work it reviews.
                role=role,
            )
            if decision.refused:
                if decision.condition_based:
                    # Only a condition-based refusal writes this state. A `lost`
                    # session refused here used to be overwritten with
                    # `unreadable`, which is the self-healing one — so the next
                    # turn was let through, exactly the block this promised to
                    # hold.
                    self.sessions.mark_unreadable(
                        task_id=run.task_id, adapter=run.adapter, role=role,
                        reason=decision.refusal or "",
                    )
                return self.store.finish(
                    run_id, RunState.FAILED, expected_version=running.version, exit_code=None,
                    stdout_tail="", stderr_tail="", error_message=decision.refusal,
                )
            resuming = decision.mode == MODE_RESUME
            # CLI adapters only: resolve the executable via PATHEXT and unwrap
            # Windows .cmd/.bat shims (npm's codex.cmd) into a CreateProcess-
            # launchable form. Only the command that will actually run is resolved.
            try:
                command = resolve_runtime_command(list(adapter.build_command(
                    run.prompt, session_id=decision.session_id, resume=resuming,
                )))
            except (RuntimeLaunchError, ValueError) as exc:
                # ValueError comes from build_command refusing to expand a
                # template it cannot satisfy. Caught here with the launch failure
                # it is: letting it escape would leave the row RUNNING with no
                # process behind it, which nothing later can settle.
                return self.store.finish(
                    run_id, RunState.FAILED, expected_version=running.version, exit_code=None,
                    stdout_tail="", stderr_tail="", error_message=redact_text(f"process start failed: {exc}"),
                )
            self.run_logs.append(
                run_id, _prompt_echo(run.prompt, resuming, machine_written=role == ROLE_REVIEW),
            )
            try:
                proc = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=str(workspace),
                    # Closed, not inherited. codex reads stdin when it is not a
                    # terminal ("Reading additional input from stdin...", seen on
                    # 0.149.1), so an inherited handle that never reaches EOF is a
                    # process that never starts working — indistinguishable, in
                    # the conversation window, from an agent thinking hard.
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env={
                        **os.environ,
                        "AGORA_TASK_ID": run.task_id,
                        "AGORA_RUN_ID": run.run_id,
                        "AGORA_PROJECT_ID": run.project_id,
                    },
                )
            except (FileNotFoundError, OSError) as exc:
                # Into the transcript as well. The prompt echo has already created
                # it, so the record fallback will not fire — and the conversation
                # showed the user's message followed by "ended with no output",
                # with the actual cause (a permission, a broken executable, a
                # Windows CreateProcess error) nowhere on screen.
                reason = f"process start failed: {type(exc).__name__}"
                self.run_logs.append(run_id, _failure_note(reason, ""))
                return self.store.finish(
                    run_id, RunState.FAILED, expected_version=running.version, exit_code=None,
                    stdout_tail="", stderr_tail="",
                    error_message=reason,
                )

            # Immediately after the spawn, so the window in which a child could
            # be started outside the job is as small as two calls allow. Stated
            # rather than hidden: a grandchild started inside that window is not
            # in the job, and CR-46 carries what remains.
            self._jobs[run_id] = self._adopt_process_tree(proc)
            if os.name == "nt" and self._jobs[run_id] is None:
                # Adoption failed — a nested-job restriction, a policy, an API
                # that said no. Everything downstream (freezing the deliverables,
                # releasing the worktree) assumes this run's children stop when
                # it does, and here they may not. Said in the transcript rather
                # than assumed away: if the snapshot later disagrees with the
                # disk, this line is the explanation, and it is CR-46's case.
                self.run_logs.append(
                    run_id,
                    "· 这一轮没能把子进程收进进程组（Windows job object 接管失败）。"
                    "如果 agent 起了后台进程，它可能在这一轮结束之后还在写工作目录——"
                    "交付物快照与磁盘内容届时可能对不上。\n",
                )
            self._active[run_id] = proc
            try:
                # Bound to its session only now that a process exists. Recorded
                # *before* the launch, this used to insert an active session for a
                # caller-chosen id even when the executable was missing — an id the
                # provider had never seen, which every later message would then try
                # to resume until someone cleared it by hand. A process that starts
                # and dies a second later is a different case and still records: the
                # session file exists by then.
                #
                # Inside the try, because a write that raises here (a locked
                # database, a full disk) left the agent running with nothing
                # tracking it: the outer guard settled the row FAILED, the
                # semaphores were released, and the process kept editing the
                # worktree while the next run was free to enter it.
                self.sessions.record_launch(
                    run_id=run_id, task_id=run.task_id, adapter=run.adapter,
                    workspace=workspace, session_id=decision.session_id,
                    mode=decision.mode, role=role,
                    # A resume is continuing an id the provider has already
                    # named; a start with a caller-chosen id has not been
                    # acknowledged by anything yet.
                    confirmed=resuming,
                    # Checked against the generation this turn was decided under,
                    # so a clear that landed in between is not resurrected here.
                    expected_clear_seq=decision.clear_seq,
                )
                try:
                    running = self.store.attach_pid(
                        run_id, expected_version=running.version, pid=proc.pid,
                        # The argv that actually ran. Queue time stored the *start*
                        # template, and from turn two on the shape is different —
                        # `codex exec resume` with its sandbox override, or
                        # claude's `--resume`. An audit trail naming a command the
                        # process never ran is worse than one naming none.
                        command=adapter.stored_command(resume=resuming),
                    )
                except RunConflictError:
                    # A cancel landed between the launch and the pid binding. The
                    # process is already running, and on a fast agent it may
                    # already have written files — so this path settles the run
                    # like any other cancel instead of returning the row as it
                    # found it. Returning early recorded no deliverables and no
                    # output: the next run's baseline absorbed those changes, and
                    # they were attributable to nothing.
                    await self._stop(proc, self._jobs.get(run_id))
                    # The pipes, before the row. A fast process prints its error
                    # or its result and *then* gets stopped, and recording two
                    # empty tails here left the person with their own prompt and
                    # nothing else — while the comment above claimed this branch
                    # fixed exactly that. The reads are bounded and the process is
                    # already dead, so they return what is in the buffers.
                    # The head as well as the tail: the session id is in the
                    # first event, and a chatty run pushes it out of a bounded
                    # tail — so the turn was recorded as naming no session, and
                    # the next message opened a new conversation over the files
                    # this one had already written.
                    # Bounded, like the ordinary drain. Unbounded, a background
                    # process holding the pipes made this branch wait for ever:
                    # the cancelled run never got its output or its deliverables
                    # written, and its worktree lock was never released.
                    conflict_out_task = asyncio.create_task(
                        self._read_head_and_tail(proc.stdout, keep_head=OUTPUT_TAIL_LIMIT)
                    )
                    conflict_err_task = asyncio.create_task(self._read_tail(proc.stderr))
                    conflict_done, _conflict_pending = await asyncio.wait(
                        {conflict_out_task, conflict_err_task},
                        timeout=_DRAIN_TIMEOUT_SECONDS,
                    )
                    out_head, out_tail, out_total = (
                        conflict_out_task.result()
                        if conflict_out_task in conflict_done else (b"", b"", 0)
                    )
                    err_tail = (
                        conflict_err_task.result()
                        if conflict_err_task in conflict_done else b""
                    )
                    conflict_err = redact_text(err_tail.decode("utf-8", errors="replace"))
                    # **Through the reader**, not straight to the log. Saving the
                    # raw tail kept the text and threw away what it *said*: a
                    # first turn that had already created its session, written
                    # files and announced its id left the session table empty, so
                    # the next message opened a brand-new conversation over work
                    # that existed. Rendering it here settles the session from the
                    # same events the normal path uses.
                    conflict_transcript = reader_for(adapter.event_format)
                    conflict_writer = _RedactingWriter(self.run_logs, run_id)
                    conflict_gate = _ReaderGate()
                    # Merged on their actual overlap. `startswith` only
                    # catches the case where the whole output fit in both; when
                    # the total sits between one and two tail limits the two
                    # pieces overlap *partially*, and concatenating them wrote
                    # the middle twice — duplicated commands, file changes and
                    # replies in the conversation, and an audit record that no
                    # longer matched what the process printed.
                    rendered_bytes = _merge_overlapping(out_head, out_tail, out_total)
                    for line in rendered_bytes.split(b"\n"):
                        if line:
                            self._render_line(
                                run, workspace, conflict_transcript, role,
                                decision.session_id if resuming else None,
                                line, conflict_writer, conflict_gate,
                            )
                    conflict_writer.close()
                    self.run_logs.append(run_id, redact_text(conflict_transcript.finish()))
                    self._settle_session(
                        run_id=run_id,
                        task_id=run.task_id,
                        adapter=run.adapter,
                        workspace=workspace,
                        transcript=conflict_transcript,
                        resuming=resuming,
                        exit_code=proc.returncode,
                        role=role,
                        requested=decision.session_id if resuming else None,
                        killed=True,
                    )
                    conflict_out = redact_text(out_tail.decode("utf-8", errors="replace"))
                    delivered = await asyncio.to_thread(snapshot_changes, workspace, baseline)
                    current = self.store.require(run_id)
                    if current.state != RunState.CANCELLED:
                        # Something else moved the row. Nothing to correct here,
                        # and guessing a state would be worse than the row.
                        return current
                    try:
                        return self.store.record_cancelled_output(
                            run_id,
                            expected_version=current.version,
                            stdout_tail=conflict_out[-OUTPUT_TAIL_LIMIT:],
                            stderr_tail=conflict_err[-OUTPUT_TAIL_LIMIT:],
                            exit_code=None,
                            deliverables=delivered,
                        )
                    except RunConflictError:
                        # Raced again while snapshotting. The row is authoritative.
                        return self.store.require(run_id)

                timed_out = False
                # stdout is read twice over, from one pass: the bounded byte tail
                # the usage normaliser parses once the run settles, and a rendered
                # transcript appended as events arrive. The second is what makes
                # the conversation window show text before the process exits.
                transcript = reader_for(adapter.event_format)
                # One gate per run, held by the reader that may outlive the
                # turn. Closing it is what stops an abandoned reader from writing
                # into a conversation the window has already finished.
                gate = _ReaderGate()
                stdout_reader = asyncio.create_task(
                    self._read_streaming(
                        proc.stdout, run, workspace, transcript, role,
                        decision.session_id if resuming else None, gate,
                    )
                )
                stderr_reader = asyncio.create_task(self._read_tail(proc.stderr))
                try:
                    await asyncio.wait_for(proc.wait(), timeout=running.timeout_seconds)
                except (TimeoutError, asyncio.TimeoutError):
                    timed_out = True
                    await self._stop(proc, self._jobs.get(run_id))
                except asyncio.CancelledError:
                    await self._stop(proc, self._jobs.get(run_id))
                    # Bounded, like the ordinary drain: a background process
                    # holding the pipes made this wait for ever, so a cancel
                    # never finished cancelling and the worktree lock was never
                    # released.
                    #
                    # The gate closes **after** that wait, and only for a reader
                    # still pending. Closing it first — which is what the first
                    # version did — threw away the very output that was waiting
                    # to be read: the agent's final reply, and the session id,
                    # which the next turn then had to open a new conversation
                    # without.
                    _done, still_reading = await asyncio.wait(
                        {stdout_reader, stderr_reader}, timeout=_DRAIN_TIMEOUT_SECONDS,
                    )
                    if still_reading:
                        gate.close()
                    raise

                # The CLI has exited (or been stopped). **Everything it left
                # behind stops here**, before its output is drained and before
                # the worktree is frozen. Two failures came from doing this
                # later:
                #
                # * the snapshot ran while a background copy was still writing,
                #   so the record missed files or caught them half-written — and
                #   the kill that followed could truncate the very file it had
                #   just recorded;
                # * a background process inherits the pipes, so `proc.wait()`
                #   returned while nothing ever reached EOF, and the drain below
                #   waited for ever: the run never settled and its worktree lock
                #   was never released, with `timeout_seconds` already spent.
                self._kill_process_tree(self._jobs.get(run_id))
                # Waited for, **not** cancelled and awaited. Measured on
                # Windows: a pending pipe read does not answer a cancel until the
                # I/O itself completes, so `wait_for` — which cancels the inner
                # task and then waits for it — took the full ninety seconds of
                # the process holding the pipe, bound or no bound. Abandoning the
                # readers is what actually ends the turn: the run settles, its
                # worktree lock is released, and the task finishes on its own
                # whenever the pipe finally closes.
                done, pending = await asyncio.wait(
                    {stdout_reader, stderr_reader}, timeout=_DRAIN_TIMEOUT_SECONDS,
                )
                if pending:
                    stdout, stdout_truncated = (
                        stdout_reader.result() if stdout_reader in done else (b"", True)
                    )
                    stderr = stderr_reader.result() if stderr_reader in done else b""
                    # Said in the transcript, because the settled output below is
                    # what the usage reader parses and it will be short: the
                    # window must not present a truncated turn as a whole one.
                    self.run_logs.append(
                        run_id,
                        "\n· 这一轮的输出没能读完：进程已经结束，但还有东西占着它的管道"
                        f"（等了 {_DRAIN_TIMEOUT_SECONDS} 秒）。上面显示的是已经读到的部分。\n",
                    )
                    # The abandoned reader is still attached to that pipe. Marked
                    # closed so it writes nothing more: the window has been told
                    # the turn is complete and has stopped polling, and a late
                    # session event from this run could otherwise overwrite the
                    # session the *next* turn has already established.
                    gate.close()
                else:
                    (stdout, stdout_truncated), stderr = (
                        stdout_reader.result(), stderr_reader.result(),
                    )
                self.run_logs.append(run_id, redact_text(transcript.finish()))
                self._settle_session(
                    run_id=run_id,
                    task_id=run.task_id,
                    adapter=run.adapter,
                    workspace=workspace,
                    transcript=transcript,
                    resuming=resuming,
                    exit_code=proc.returncode,
                    role=role,
                    requested=decision.session_id,
                    # What is knowable here: Agora killed it (timeout), or a
                    # cancel has already been recorded. `target` is computed
                    # further down — and a cancelled run returns before it — so
                    # the facts travel rather than the verdict.
                    killed=timed_out or self.store.require(run_id).state == RunState.CANCELLED,
                )

                settled_stdout, prefix_truncated = _settlement_stdout(
                    stdout.decode("utf-8", errors="replace"), stdout_truncated, transcript,
                )
                semantic, usage_metadata = self._observe_usage(
                    adapter, settled_stdout, run_id, run.prompt,
                    # Whether what is being parsed begins mid-event. Only the
                    # byte tail can; a stream rebuilt from retained whole lines
                    # cannot, and saying it did would make the reader discard its
                    # first line.
                    stdout_prefix_truncated=prefix_truncated,
                )
                out = redact_text(semantic)
                err = redact_text(stderr.decode("utf-8", errors="replace"))
                # Snapshot before branching: a cancelled run that already wrote
                # files delivered them, and its record should say so rather than
                # reading "unknown".
                delivered = await asyncio.to_thread(snapshot_changes, workspace, baseline)
                # Re-read AFTER the snapshot. A cancel can land while it runs (a
                # large worktree widens that window), and a version captured
                # before the await would make both branches below fail their
                # optimistic check — losing the output and the snapshot together.
                current = self.store.require(run_id)
                if current.state == RunState.CANCELLED:
                    return self.store.record_cancelled_output(
                        run_id,
                        expected_version=current.version,
                        stdout_tail=out[-OUTPUT_TAIL_LIMIT:],
                        stderr_tail=err[-OUTPUT_TAIL_LIMIT:],
                        exit_code=proc.returncode,
                        deliverables=delivered,
                    )
                target = RunState.TIMED_OUT if timed_out else (
                    RunState.SUCCEEDED if proc.returncode == 0 else RunState.FAILED
                )
                error = (
                    f"timeout after {running.timeout_seconds}s" if timed_out
                    else (None if proc.returncode == 0 else "process exited with a non-zero status")
                )
                # Why it failed, into the transcript. The window reads the
                # conversation, and the record fallback cannot help here: the
                # prompt echo already created the file, so the run *has* a
                # transcript — one that held the user's message and nothing about
                # a CLI that refused to start. The reason was on screen nowhere.
                if target is not RunState.SUCCEEDED:
                    self.run_logs.append(run_id, _failure_note(error, err))
                finished = self.store.finish(
                    run_id,
                    target,
                    expected_version=current.version,
                    exit_code=proc.returncode,
                    stdout_tail=out[-OUTPUT_TAIL_LIMIT:],
                    stderr_tail=err[-OUTPUT_TAIL_LIMIT:],
                    error_message=error,
                    # The result only. The run's identity (role, reviewed-run link)
                    # was written by create and is preserved by the store across
                    # every settle — re-sending it here is what made a settle with
                    # no result look free to write an empty metadata blob over it
                    # (see ExecutionStore._identity_preserved), and the store now
                    # refuses an identity key on this argument.
                    result_metadata=usage_metadata,
                    deliverables=delivered,
                )
                await self._after_run(finished, role, reviews_run_id)
                return finished
            except BaseException:
                # Anything that escapes the body with a live process — including a
                # failed session write above — stops that process first. Without
                # this the run was settled and forgotten while the agent carried on
                # writing to a worktree the next run could take.
                await self._stop(proc, self._jobs.get(run_id))
                raise
            finally:
                self._active.pop(run_id, None)
                self._session_noted.pop(run_id, None)
                # Closing the job kills anything still in it. That is deliberate:
                # a process still writing after its run settled is exactly what
                # makes the frozen deliverables disagree with the disk, and the
                # next run enters that worktree.
                self._release_process_tree(self._jobs.pop(run_id, None))


    async def _execute_remote(
        self, run: ExecutionRun, adapter: ExecutionAdapter | None, role: str, reviews_run_id: str | None,
    ) -> ExecutionRun:
        """The remote counterpart of the local path (W3-a U5).

        The same questions in the same order — is the worktree ready, what was there
        before, launch, stream, wait, stop, snapshot, settle — each asked of the
        adapter's host instead of this machine. Its own path rather than branches
        threaded through the local one: that path is built on this machine's
        filesystem and process tree (probe_path, job objects, taskkill), none of
        which mean anything over SSH, and a branch at each of those points would be a
        second copy of the flow written between the lines of the first.

        What it does not do, said plainly: the cancel path in ``_execute_guarded``
        snapshots the *local* path named by the run, which for a remote run is not a
        worktree — so a run abandoned by a dispatcher stop records its delivery as
        "could not establish", never as a list.
        """
        run_id = run.run_id
        try:
            # The place recorded at queue time, if the run has one; an adapter whose host
            # changed since then is a refusal, not a run on the new host.
            recorded = remote_target_of(run)
            if recorded is None:
                raise RuntimeError("this run carries no recorded remote target")
            pinned = adapter_for_recorded_target(adapter, recorded)
            components = remote_components_for(
                pinned, self.projects, allowed_workspace_roots=self.allowed_workspace_roots,
            )
        except Exception as exc:  # a misconfigured adapter is a run that cannot start
            return self.store.finish(
                run_id, RunState.FAILED, expected_version=run.version, exit_code=None,
                stdout_tail="", stderr_tail="",
                error_message=redact_text(f"remote adapter is misconfigured: {type(exc).__name__}: {exc}"),
            )
        remote = components.config
        adapter = pinned
        workspace = PurePosixPath(run.workspace)
        # The session's identity binds the host too: the same worktree string on another
        # host is another worktree, and a provider session id recorded there must not be
        # resumed here. The session store compares this as a string.
        session_scope = PurePosixPath(f"{remote.ssh_user}@{remote.ssh_host}:{remote.ssh_port}{workspace}")
        # `Path` here is the type the remote collaborators take; on Windows it is a
        # WindowsPath whose `.as_posix()` is the remote path unchanged, and that is the
        # only way they read it.
        ws_path = Path(str(workspace))
        remote_root = Path(remote.remote_project_root)
        try:
            running = self.store.start(run_id, expected_version=run.version)
        except RunConflictError:
            await components.aclose()
            return self.store.require(run_id)
        env = components.environment
        try:
            expected_branch = worktree_branch(run.project_id, run.adapter)
            try:
                ready = await components.provisioner.is_ready_worktree(ws_path, remote_root, expected_branch)
            except Exception as exc:
                return self.store.finish(
                    run_id, RunState.FAILED, expected_version=running.version, exit_code=None,
                    stdout_tail="", stderr_tail="",
                    error_message=redact_text(
                        f"remote worktree could not be checked on {describe_target(remote)}: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                )
            if not ready:
                return self.store.finish(
                    run_id, RunState.FAILED, expected_version=running.version, exit_code=None,
                    stdout_tail="", stderr_tail="",
                    error_message=(
                        f"remote workspace on {describe_target(remote)} is not a provisioned "
                        "worktree for this project/adapter; provision it before running"
                    ),
                )
            try:
                baseline = await components.deliverables.baseline_state(ws_path)
            except Exception:
                baseline = None
            self._remote_baselines[run_id] = baseline
            decision = self.sessions.decide(
                task_id=run.task_id,
                adapter=run.adapter,
                workspace=session_scope,  # type: ignore[arg-type]
                can_resume=adapter.can_resume(),
                caller_chooses_id=adapter.session_id_source == SESSION_ID_CALLER,
                role=role,
            )
            if decision.refused:
                if decision.condition_based:
                    self.sessions.mark_unreadable(
                        task_id=run.task_id, adapter=run.adapter, role=role,
                        reason=decision.refusal or "",
                    )
                return self.store.finish(
                    run_id, RunState.FAILED, expected_version=running.version, exit_code=None,
                    stdout_tail="", stderr_tail="", error_message=decision.refusal,
                )
            resuming = decision.mode == MODE_RESUME
            # Not resolved against this machine's PATH: the CLI lives on the host.
            try:
                command = list(adapter.build_command(
                    run.prompt, session_id=decision.session_id, resume=resuming,
                ))
            except ValueError as exc:
                return self.store.finish(
                    run_id, RunState.FAILED, expected_version=running.version, exit_code=None,
                    stdout_tail="", stderr_tail="",
                    error_message=redact_text(f"process start failed: {exc}"),
                )
            self.run_logs.append(
                run_id, _prompt_echo(run.prompt, resuming, machine_written=role == ROLE_REVIEW),
            )
            self.run_logs.append(
                run_id, f"· 在远端 {describe_target(remote)} 的工作树 {workspace} 里执行。\n",
            )
            try:
                handle = await env.launch(
                    command,
                    cwd=ws_path,
                    env={
                        "AGORA_TASK_ID": run.task_id,
                        "AGORA_RUN_ID": run.run_id,
                        "AGORA_PROJECT_ID": run.project_id,
                    },
                )
            except Exception as exc:
                reason = redact_text(
                    f"remote process start failed on {describe_target(remote)}: {type(exc).__name__}: {exc}"
                )
                self.run_logs.append(run_id, _failure_note(reason, ""))
                return self.store.finish(
                    run_id, RunState.FAILED, expected_version=running.version, exit_code=None,
                    stdout_tail="", stderr_tail="", error_message=reason,
                )
            self._active[run_id] = handle  # type: ignore[assignment]  # a ProcessHandle; _stop takes both
            try:
                self.sessions.record_launch(
                    run_id=run_id, task_id=run.task_id, adapter=run.adapter,
                    workspace=session_scope, session_id=decision.session_id,  # type: ignore[arg-type]
                    mode=decision.mode, role=role,
                    confirmed=resuming,
                    expected_clear_seq=decision.clear_seq,
                )
                try:
                    running = self.store.attach_pid(
                        run_id, expected_version=running.version, pid=handle.pid,
                        command=adapter.stored_command(resume=resuming),
                    )
                except RunConflictError:
                    # Cancelled between start and attach. Stop it and record what the
                    # channel had already carried, as the local path does.
                    await self._stop(handle)
                    out_task = asyncio.create_task(self._read_tail(handle.stdout))
                    err_task = asyncio.create_task(self._read_tail(handle.stderr))
                    done, _pending = await asyncio.wait({out_task, err_task}, timeout=_DRAIN_TIMEOUT_SECONDS)
                    out_bytes = out_task.result() if out_task in done else b""
                    err_bytes = err_task.result() if err_task in done else b""
                    current = self.store.require(run_id)
                    if current.state != RunState.CANCELLED:
                        return current
                    try:
                        delivered = await components.deliverables.snapshot_changes(ws_path, baseline)
                    except Exception:
                        delivered = None
                    try:
                        return self.store.record_cancelled_output(
                            run_id,
                            expected_version=current.version,
                            stdout_tail=redact_text(out_bytes.decode("utf-8", errors="replace"))[-OUTPUT_TAIL_LIMIT:],
                            stderr_tail=redact_text(err_bytes.decode("utf-8", errors="replace"))[-OUTPUT_TAIL_LIMIT:],
                            exit_code=None,
                            deliverables=delivered,
                        )
                    except RunConflictError:
                        return self.store.require(run_id)
                timed_out = False
                transcript = reader_for(adapter.event_format)
                gate = _ReaderGate()
                stdout_reader = asyncio.create_task(
                    self._read_streaming(
                        handle.stdout, run, ws_path, transcript, role,
                        decision.session_id if resuming else None, gate,
                    )
                )
                stderr_reader = asyncio.create_task(self._read_tail(handle.stderr))
                try:
                    code = await handle.wait(timeout=running.timeout_seconds)
                    if code is None:
                        timed_out = True
                        await self._stop(handle)
                except asyncio.CancelledError:
                    await self._stop(handle)
                    _done, still_reading = await asyncio.wait(
                        {stdout_reader, stderr_reader}, timeout=_DRAIN_TIMEOUT_SECONDS,
                    )
                    if still_reading:
                        gate.close()
                    raise
                done, pending = await asyncio.wait(
                    {stdout_reader, stderr_reader}, timeout=_DRAIN_TIMEOUT_SECONDS,
                )
                if pending:
                    stdout, stdout_truncated = (
                        stdout_reader.result() if stdout_reader in done else (b"", True)
                    )
                    stderr = stderr_reader.result() if stderr_reader in done else b""
                    self.run_logs.append(
                        run_id,
                        "\n· 这一轮的输出没能读完：远端进程已经结束，但通道上还有东西没读到"
                        f"（等了 {_DRAIN_TIMEOUT_SECONDS} 秒）。上面显示的是已经读到的部分。\n",
                    )
                    gate.close()
                else:
                    (stdout, stdout_truncated), stderr = (
                        stdout_reader.result(), stderr_reader.result(),
                    )
                self.run_logs.append(run_id, redact_text(transcript.finish()))
                self._settle_session(
                    run_id=run_id,
                    task_id=run.task_id,
                    adapter=run.adapter,
                    workspace=session_scope,  # type: ignore[arg-type]
                    transcript=transcript,
                    resuming=resuming,
                    exit_code=handle.returncode,
                    role=role,
                    requested=decision.session_id,
                    killed=timed_out or self.store.require(run_id).state == RunState.CANCELLED,
                )
                settled_stdout, prefix_truncated = _settlement_stdout(
                    stdout.decode("utf-8", errors="replace"), stdout_truncated, transcript,
                )
                semantic, usage_metadata = self._observe_usage(
                    adapter, settled_stdout, run_id, run.prompt,
                    stdout_prefix_truncated=prefix_truncated,
                )
                out = redact_text(semantic)
                err = redact_text(stderr.decode("utf-8", errors="replace"))
                try:
                    delivered = await components.deliverables.snapshot_changes(ws_path, baseline)
                except Exception:
                    delivered = None  # could not establish — never an empty list
                current = self.store.require(run_id)
                if current.state == RunState.CANCELLED:
                    return self.store.record_cancelled_output(
                        run_id,
                        expected_version=current.version,
                        stdout_tail=out[-OUTPUT_TAIL_LIMIT:],
                        stderr_tail=err[-OUTPUT_TAIL_LIMIT:],
                        exit_code=handle.returncode,
                        deliverables=delivered,
                    )
                target = RunState.TIMED_OUT if timed_out else (
                    RunState.SUCCEEDED if handle.returncode == 0 else RunState.FAILED
                )
                error = (
                    f"timeout after {running.timeout_seconds}s" if timed_out
                    else (None if handle.returncode == 0 else "process exited with a non-zero status")
                )
                if target is not RunState.SUCCEEDED:
                    self.run_logs.append(run_id, _failure_note(error, err))
                finished = self.store.finish(
                    run_id,
                    target,
                    expected_version=current.version,
                    exit_code=handle.returncode,
                    stdout_tail=out[-OUTPUT_TAIL_LIMIT:],
                    stderr_tail=err[-OUTPUT_TAIL_LIMIT:],
                    error_message=error,
                    result_metadata=usage_metadata,
                    deliverables=delivered,
                )
                await self._after_run(finished, role, reviews_run_id)
                return finished
            except BaseException:
                await self._stop(handle)
                raise
            finally:
                self._active.pop(run_id, None)
                self._session_noted.pop(run_id, None)
                self._remote_baselines.pop(run_id, None)
        finally:
            await components.aclose()

    def _reviewable_files(self, worker_run: ExecutionRun, live: list[dict]) -> tuple[list[dict], str | None]:
        """The files a review must look at: the run's frozen snapshot when it has one —
        that is what the run delivered, including what it committed during the run,
        which a live ``git status`` afterwards no longer shows — else the live list.
        A cut snapshot says so, so the reviewer knows the list is "at least these"."""
        snapshot = self.store.deliverable_snapshot(worker_run.run_id)
        if snapshot is None:
            return live, None
        # A deletion is a change the reviewer must see; only the two non-answers
        # ("no longer differs", "unreadable") are left out.
        files = [
            entry for entry in (snapshot.get("files") or [])
            if entry.get("status") not in ("no longer differs", "unreadable")
        ]
        note = (
            "\n(The run's file list was cut at the limit: more changed than is shown here.)"
            if snapshot.get("truncated") else None
        )
        return files, note

    async def _review_prompt_remote(self, worker_run: ExecutionRun, adapter: ExecutionAdapter) -> str:
        """``_review_prompt``, read on the worker's host: readiness, changed files and
        their contents come from the remote provisioner and deliverables reader."""
        components = remote_components_for(
            adapter, self.projects, allowed_workspace_roots=self.allowed_workspace_roots,
        )
        try:
            ws_path = Path(worker_run.workspace)
            parts = [
                "You are an independent code reviewer. A developer agent produced the "
                "changes below. Review them for correctness, edge cases, and safety. "
                "List concrete findings (file + issue); if everything looks correct, say so clearly.",
                f"\n## Developer's own summary\n{worker_run.stdout_tail[:_REVIEW_FILE_CHARS]}",
            ]
            budget = _REVIEW_PROMPT_BUDGET
            expected_branch = worktree_branch(worker_run.project_id, worker_run.adapter)
            ready = await components.provisioner.is_ready_worktree(
                ws_path, Path(components.config.remote_project_root), expected_branch,
            )
            files, note = (
                self._reviewable_files(worker_run, await components.deliverables.list_changes(ws_path))
                if ready else ([], None)
            )
            if note:
                parts.append(note)
            if not files:
                parts.append("\n## Changes\n(No changed files detected in the workspace.)")
            for entry in files:
                if entry.get("status") == "deleted":
                    parts.append(f"\n## FILE: {entry['path']} (deleted)")
                    continue
                try:
                    content = (await components.deliverables.read_file(ws_path, entry["path"]))["content"]
                except (FileNotFoundError, PermissionError, OSError):
                    continue
                block = f"\n## FILE: {entry['path']} ({entry['status']})\n{content[:_REVIEW_FILE_CHARS]}"
                if len(block) > budget:
                    parts.append("\n[additional files omitted for length]")
                    break
                parts.append(block)
                budget -= len(block)
            return "\n".join(parts)[:15_900]
        finally:
            await components.aclose()

    async def _execute_codex_app_server(
        self, running: ExecutionRun, adapter: ExecutionAdapter, workspace: Path,
        role: str = ROLE_WORKER, reviews_run_id: str | None = None,
        baseline: dict[str, dict] | None = None,
    ) -> ExecutionRun:
        attention = AttentionStore(self.store.tasks)
        broker = CodexApprovalBroker(attention, task_id=running.task_id, run_id=running.run_id)
        runner = CodexAppServerRunner(broker)
        attached = running

        async def on_process(proc: asyncio.subprocess.Process) -> None:
            nonlocal attached
            self._jobs[running.run_id] = self._adopt_process_tree(proc)
            self._active[running.run_id] = proc
            try:
                attached = self.store.attach_pid(
                    running.run_id, expected_version=attached.version, pid=proc.pid,
                )
            except RunConflictError:
                await self._stop(proc, self._jobs.get(running.run_id))
                raise

        env = {
            **os.environ,
            "AGORA_TASK_ID": running.task_id,
            "AGORA_RUN_ID": running.run_id,
            "AGORA_PROJECT_ID": running.project_id,
        }
        out = b""
        err = b""
        timed_out = False
        failure: str | None = None
        try:
            # Resolve the executable via PATHEXT and unwrap Windows .cmd/.bat
            # shims (npm's codex.cmd) so the app-server launches on Windows.
            resolved = tuple(resolve_runtime_command(list(adapter.app_server_command)))
            result = await runner.run(
                command=resolved, prompt=running.prompt, cwd=workspace,
                timeout_seconds=running.timeout_seconds, env=env, on_process=on_process,
            )
            out, err = result.stdout, result.stderr
            timed_out = result.timed_out
            failure = (
                f"timeout after {running.timeout_seconds}s" if timed_out
                else (redact_text(f"app-server error: {result.error}") if result.error else None)
            )
        except (CodexAppServerError, RunConflictError, FileNotFoundError, OSError, RuntimeLaunchError) as exc:
            failure = redact_text(f"app-server error: {type(exc).__name__}: {exc}")
        finally:
            self._active.pop(running.run_id, None)
            # And the job, which this path assigns exactly like the CLI path and
            # was the only one that never released. `KILL_ON_JOB_CLOSE` then
            # never fired: a background process the agent left behind kept
            # writing the worktree after the snapshot was frozen and the lock
            # released, which is the whole hazard the job exists to close.
            self._release_process_tree(self._jobs.pop(running.run_id, None))

        safe_out = redact_text(out.decode("utf-8", errors="replace"))[-OUTPUT_TAIL_LIMIT:]
        safe_err = redact_text(err.decode("utf-8", errors="replace"))[-OUTPUT_TAIL_LIMIT:]
        # Same as the CLI path: freeze the delivered list (baseline-subtracted)
        # before branching, then re-read state/version after the await so a cancel
        # landing during the snapshot does not void both writes.
        delivered = await asyncio.to_thread(snapshot_changes, workspace, baseline)
        current = self.store.require(running.run_id)
        if current.state == RunState.CANCELLED:
            return self.store.record_cancelled_output(
                running.run_id, expected_version=current.version, stdout_tail=safe_out,
                stderr_tail=safe_err, exit_code=None, deliverables=delivered,
            )
        target = RunState.TIMED_OUT if timed_out else (RunState.SUCCEEDED if failure is None else RunState.FAILED)
        finished = self.store.finish(
            running.run_id, target, expected_version=current.version,
            exit_code=0 if target == RunState.SUCCEEDED else None,
            stdout_tail=safe_out, stderr_tail=safe_err, error_message=failure,
            # No result to record on this path, and no identity to re-send: the
            # store preserves the run's role and reviewed-run link across the
            # settle (ExecutionStore._identity_preserved).
            deliverables=delivered,
        )
        await self._after_run(finished, role, reviews_run_id)
        return finished

    async def cancel(self, run_id: str, request: CancelRunRequest) -> ExecutionRun:
        cancelled = await asyncio.to_thread(self.store.cancel, run_id, request)
        proc = self._active.get(run_id)
        if proc is not None and proc.returncode is None:
            await self._stop(proc, self._jobs.get(run_id))
        # Cancelling a review run leaves the work it was going to review unreviewed,
        # and since N1 that no longer discharges the obligation by itself. Record the
        # blocker now so the human has the item — and its exits — at the moment they
        # cancelled, rather than a task that refuses worker runs until the next start.
        await asyncio.to_thread(self._block_reviews_owed_after_cancel, cancelled)
        return cancelled

    async def shutdown(self) -> None:
        scheduled = list(self._scheduled.items())
        for task, _ in scheduled:
            task.cancel()
        if scheduled:
            await asyncio.gather(*(task for task, _ in scheduled), return_exceptions=True)
        # Sweep every run we just cancelled. A done callback is only *scheduled*
        # on the loop when a task completes, so "shutdown returned" must not
        # depend on it having run yet — and a task cancelled before the loop ever
        # entered its coroutine has no body that could settle its own row. Both
        # paths are idempotent; whichever gets there first wins.
        for _, run_id in scheduled:
            self._settle_unstarted(run_id)

    def _observe_usage(
        self,
        adapter: ExecutionAdapter,
        raw_stdout: str,
        run_id: str,
        prompt: str,
        *,
        stdout_prefix_truncated: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        """Return clean semantic text and token usage; parsing never breaks a run."""
        result_format = self._USAGE_RESULT_FORMATS.get(adapter.event_format)
        if result_format is None:
            return raw_stdout, {}
        try:
            semantic, observation = normalize_native_output(
                adapter=adapter.name,
                result_format=result_format,
                stdout=raw_stdout,
                run_id=run_id,
                prompt=prompt,
                stdout_prefix_truncated=stdout_prefix_truncated,
            )
        except Exception:
            # A malformed or oversized payload must not fail the run; fall back
            # to the raw output with no usage rather than raising.
            return raw_stdout, {}
        return semantic, self._usage_metadata(observation)

    @staticmethod
    def _usage_metadata(observation: Any) -> dict[str, Any]:
        if observation is None:
            return {}
        if observation.token_measurement == "unavailable":
            # Not "no usage": a provider can report a cost without reporting
            # tokens, which is exactly what kiro does (credits, no token counts).
            # Gating the whole block on tokens threw those measured credits away
            # on every run.
            credits = getattr(observation, "native_credits", None)
            if credits is None:
                return {}
            return {
                "usage": {
                    "measurement": "unavailable",
                    "credits": credits,
                    "credit_measurement": observation.native_credit_measurement,
                    "model": observation.model,
                }
            }
        # NOTE: keys must not contain "token" — sanitize_data() redacts any key
        # matching a secret marker (KEY/TOKEN/SECRET/...). These are token counts,
        # not secrets, so they live under neutral names inside the `usage` block.
        return {
            "usage": {
                "input": observation.input_tokens,
                "output": observation.output_tokens,
                "total": observation.total_tokens,
                "model": observation.model,
                "measurement": observation.token_measurement,
                "cost_usd": observation.cost_usd,
            }
        }

    async def _after_run(self, finished: ExecutionRun, role: str, reviews_run_id: str | None) -> None:
        """Cross-review orchestration. Never let it break the primary run."""
        try:
            if role == ROLE_WORKER and finished.state == RunState.SUCCEEDED:
                # CR-99: a turn that verifiably changed nothing is an answer, not work —
                # no reviewer is sent after it, and the store's review-owed predicate
                # releases it on the same reading (``ExecutionStore.delivered_nothing``).
                # "Unknown" (no snapshot, unread commits) is not "nothing": reviewed.
                if self.auto_review and not self.store.run_delivered_nothing(finished.run_id):
                    await self._dispatch_review(finished)
            elif role == ROLE_REVIEW and finished.state == RunState.SUCCEEDED:
                self._raise_review_attention(finished, reviews_run_id, AttentionKind.APPROVAL)
            elif role == ROLE_REVIEW and finished.state in {RunState.FAILED, RunState.TIMED_OUT}:
                self._raise_review_attention(finished, reviews_run_id, AttentionKind.BLOCKER)
        except Exception as exc:
            # Orchestration is best-effort for the run that just completed: its own
            # outcome is already recorded and must not change. But "best-effort"
            # used to mean the review stage vanished without trace — an unavailable
            # reviewer, a failed prompt build, a queue conflict or a failed gate
            # write all ended as "no review exists", and both the UI and the create
            # gate then accepted another worker run on unreviewed work. So the
            # failure is recorded as a durable blocker on the task instead. That
            # blocker is a gating kind, so an unavailable reviewer now blocks its
            # work rather than deleting the review stage.
            self._record_review_failure(finished, reviews_run_id, exc)

    def _record_review_failure(
        self, finished: ExecutionRun, reviews_run_id: str | None, exc: BaseException,
    ) -> None:
        """Last resort for an orchestration failure: make it visible, never raise.

        Wrapped in its own ``except`` because this is the path taken when something
        has already gone wrong, and the primary run's recorded outcome must survive
        whatever else fails here.
        """
        try:
            self._block_review_owed(
                task_id=finished.task_id,
                run_id=reviews_run_id or finished.run_id,
                reason=f"{type(exc).__name__}: {exc}",
            )
        except Exception:
            pass

    #: Fixed title of the "no cross-review conclusion is on record" blocker. Defined
    #: in .models (and re-exposed here, which is where every existing caller reads it)
    #: because ``ExecutionStore`` has to know this blocker exists: its derived
    #: review-owed refusal stands down once one has been recorded for the run. The
    #: name ``REVIEW_FAILED_TITLE`` predates the two reconciled causes and is kept
    #: only because callers import it; the string it points at is the accurate one.
    REVIEW_FAILED_TITLE = REVIEW_OWED_BLOCKER_TITLE

    #: Recorded as the blocker's reason by ``reconcile_reviews_owed`` when no review
    #: run is linked to the worker run at all: the process died between the worker
    #: run settling and the review being queued.
    REVIEW_OWED_AFTER_RESTART_REASON = (
        "Agora 在这次实现运行结束之后、独立交叉评审被创建之前停止了(进程退出或崩溃),"
        "因此评审运行从未入队,也没有留下任何记录。"
    )

    #: Recorded as the blocker's reason when a review run *is* linked to the worker
    #: run but left no gate item. The review ran; writing the approval failed and so
    #: did the fallback blocker, so nothing was ever put in front of a human. Said
    #: separately from the reason above because it is a different fact about this
    #: database and it points at a different thing to check.
    #:
    #: States what happened and what to look at, and nothing about how to get out:
    #: the exits are the same for every cause and are written once, in
    #: ``_block_review_owed``'s body. Until O3 (2026-08-30) each reason carried its
    #: own copy and two of them offered "dispatch a review again", which is not an
    #: exit that exists.
    REVIEW_OWED_WITHOUT_GATE_REASON = (
        "这次实现运行的评审运行已经结束,但没有留下任何需要你处置的条目(批准项和阻塞项"
        "都没有写成功),因此没有人被要求接受这份工作。请查看那次评审运行本身的状态与"
        "输出:它可能在真正评审之前就失败了,也可能结论已经写出来但没有保存下来。"
    )

    #: Recorded as the blocker's reason when the review run linked to this worker run
    #: was **cancelled** and left no gate item — by a human through the API, or
    #: automatically by ``shutdown``'s ``_settle_unstarted`` sweep, or by a
    #: re-validation that refused to resume or launch it. Said separately from the two
    #: reasons above because a stopped review is a different fact from a review whose
    #: gate writes both failed — see N1 in
    #: docs/architecture/run-gate-ordering-v1.md.
    #:
    #: It does **not** claim nobody looked at the work (P1, 2026-08-30). ``cancel``
    #: settles a RUNNING review too, and a reviewer stopped partway had already been
    #: given the prompt; what the cancel guarantees is that no conclusion was recorded,
    #: which is the fact this database holds.
    REVIEW_OWED_CANCELLED_REASON = (
        "这次实现运行的独立交叉评审运行已被取消(可能是你手动取消的,也可能是 Agora 在评审"
        "真正开始之前退出、由停机清理自动取消的),因此这次评审没有留下任何结论 —— 它可能"
        "还没开始,也可能在中途被停下。"
    )

    #: Which reason each derived cause gets. A cause with no entry here is a new
    #: arrangement nobody has decided what to say about, so it is not defaulted:
    #: ``reconcile_reviews_owed`` skips it (the run stays owed and refused) rather
    #: than recording a reason that does not describe it.
    REVIEW_OWED_REASONS: dict[str, str] = {
        REVIEW_OWED_NO_REVIEW_RUN: REVIEW_OWED_AFTER_RESTART_REASON,
        REVIEW_OWED_NO_GATE_ITEM: REVIEW_OWED_WITHOUT_GATE_REASON,
        REVIEW_OWED_REVIEW_CANCELLED: REVIEW_OWED_CANCELLED_REASON,
    }

    def reconcile_reviews_owed(self) -> list[dict[str, str]]:
        """Startup reconciliation for the review-owed cases that cannot resolve
        themselves. Returns the ``{"task_id", "run_id", "cause"}`` records it
        recorded a blocker for.

        The derived refusal in ``ExecutionStore._reject_review_owed`` releases on its
        own in every ordinary path: the review run appears, or it settled and its
        gate item — the approval a successful review raises, the blocker a failed
        dispatch records — is there for the human to clear. Two paths release in
        neither way. The process dying between the worker run settling and
        ``_dispatch_review`` running: nothing will ever insert that review and nothing
        recorded a blocker (``REVIEW_OWED_NO_REVIEW_RUN``). And a review run that
        settled while *both* writes of its gate item failed —
        ``_raise_review_attention`` and then ``_record_review_failure``'s fallback
        (``REVIEW_OWED_NO_GATE_ITEM``): after a restart that arrangement looks
        reviewed from the run table and has nothing pending anywhere, so this sweep is
        the only thing that can notice it. In both, the predicate would otherwise stay
        true for as long as the task exists and no worker run could ever start on the
        task again.

        **Chosen: record the blocker, do not dispatch the review.** Both were
        available. Dispatching was rejected for three reasons. ``_review_prompt``
        reads the worker's *live* worktree rather than the frozen deliverables
        snapshot, and after a crash and a restart that directory is no longer
        guaranteed to hold what the run delivered (worktrees are keyed by
        project+adapter and are reused), so an automatic review at boot can review
        the wrong content and report a verdict on it — worse than saying nothing.
        It also spends a reviewer's tokens at startup on work nobody asked to
        review again. And it can fail for the same reason it failed before, which
        lands on ``_block_review_owed`` anyway. The blocker is deterministic, costs
        nothing, gates by itself, names what happened, and leaves the human the exits
        that exist — clear it and accept the work with no review decision on record, or
        clear it and dispatch another *worker* run whose own automatic review follows.
        It does **not** offer
        "dispatch the review again": no endpoint does that, which is the correction O3
        made to this method's own wording (2026-08-30).

        Runs only when ``auto_review`` is on, exactly matching the predicate: with
        the flag off no review is owed and the create path never asks.

        **A third shape, since N1: a review run that was cancelled.** Shutdown cancels
        every queued run, so a restart in the dispatch window leaves a linked review run
        in ``cancelled`` with no gate item — and that no longer discharges the
        obligation. It reaches here as ``REVIEW_OWED_REVIEW_CANCELLED`` and gets its own
        sentence, because "the review was stopped" is a different fact from "both gate
        writes failed" and points at a different thing to do. The live cancel paths write
        the same blocker at the time they cancel
        (``_block_reviews_owed_for``); this sweep is the backstop for the ones that had
        no chance to — a hard kill, or a write that failed — and both are idempotent.

        **Nothing here stops the API from serving, and nothing here fails
        silently.** A blocker write that fails is skipped so one unrecordable task
        does not cost the rest theirs, and the read that finds the owed runs is
        guarded for the same reason — it used to be the one unwrapped call on this
        path, so a database that could not be read propagated out of
        ``lifespan``, the API never served and ``resume_queued`` never ran, which is
        the opposite of what this paragraph promised. Every failure is *recorded*
        rather than swallowed: an unreadable read is reported on
        ``reviews_owed_unreadable``, and each owed run this method read but could not
        record a blocker for — a failed write, or a cause it has no true sentence for
        — on ``reviews_owed_unrecorded``. Both are in ``start_recovery``'s receipt and
        both are printed on the operator's console at startup
        (``agora.api.app.lifespan``): a reconciliation that could not finish is a fact
        about this process, not a no-op. Those tasks stay refused with ``review_owed``
        until a later start records them (stated in
        docs/architecture/run-gate-ordering-v1.md).

        **What must not depend on this method succeeding (M1).** Until 2026-08-30 the
        resume sweep's safety did: it judged the queue against the blockers recorded
        here, so an incomplete reconciliation meant a QUEUED worker run scheduled onto
        work that succeeded and was never reviewed. ``_resume_refusal`` now derives the
        review-owed refusal itself, so a partial or failed reconciliation costs a human
        an *inbox item*, not a gate.
        """
        if not self.auto_review:
            return []
        # Set before the work, not after: this flag answers "did the reconciliation
        # step run in this process?", which is what resume_queued's ordering guard
        # asks. A step that ran and could not read is still a step that ran.
        self._reviews_owed_reconciled = True
        self.reviews_owed_unreadable = None
        self.reviews_owed_unrecorded = []
        try:
            owed_runs = self.store.reviews_owed()
        except Exception as exc:
            self.reviews_owed_unreadable = redact_text(f"{type(exc).__name__}: {exc}")
            return []
        recorded, unrecorded = self._record_owed_blockers(owed_runs)
        self.reviews_owed_unrecorded = unrecorded
        return recorded

    def _record_owed_blockers(
        self, owed_runs: list[dict[str, str]]
    ) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        """Turn derived owed-review records into blockers. Returns (recorded, not).

        Shared by ``reconcile_reviews_owed`` and ``_block_reviews_owed_for`` so the
        startup sweep and the live cancel paths cannot describe the same arrangement
        differently: one mapping from ``cause`` to sentence, one idempotent write, one
        rule for a cause with no sentence.
        """
        recorded: list[dict[str, str]] = []
        unrecorded: list[dict[str, str]] = []
        for owed in owed_runs:
            reason = self.REVIEW_OWED_REASONS.get(owed.get("cause", ""))
            if reason is None:
                # An unrecognised cause is a new arrangement this method has no true
                # sentence for. Skipping leaves the derived refusal standing, which
                # is the failure this whole path exists to make visible; inventing a
                # reason would clear the gate with a description of something else.
                unrecorded.append({
                    **{key: str(value) for key, value in owed.items()},
                    "error": "no recorded reason for this cause",
                })
                continue
            try:
                self._block_review_owed(
                    task_id=owed["task_id"], run_id=owed["run_id"], reason=reason,
                )
            except Exception as exc:
                unrecorded.append({
                    **{key: str(value) for key, value in owed.items()},
                    "error": redact_text(f"{type(exc).__name__}: {exc}"),
                })
                continue
            recorded.append(owed)
        return recorded, unrecorded

    def _block_reviews_owed_for(self, task_id: str) -> None:
        """Record the review-owed blocker for one task, now, and never raise.

        **Why this exists (N1, 2026-08-30).** A cancelled review run no longer
        accounts for the review obligation (``_has_accounting_review_run``), which is
        correct — the shutdown sweep cancels queued reviews automatically, and reading
        that as "reviewed" let a restart admit a worker run on unreviewed work. But
        the release for the obligation is a *gate item*, and cancelling a review run
        writes none. Without this, a review cancelled while the process is up would
        leave the task refusing worker runs with ``review_owed`` and nothing in the
        human's inbox to act on until the next restart's reconciliation. So every path
        that cancels a review run calls this, and the human gets the item and its two
        exits at the moment the review stopped.

        Never raises, and reports nothing: it is called from cancellation paths whose
        own outcome must not change, including one that runs during shutdown. The
        startup reconciliation is the backstop for anything that fails here — it asks
        the same derived question against the same rows, and ``_block_review_owed`` is
        idempotent, so covering the same run twice records one item.
        """
        try:
            self._record_owed_blockers(self.store.reviews_owed(task_id=task_id))
        except Exception:
            pass

    def _block_reviews_owed_after_cancel(self, run: ExecutionRun | None) -> None:
        """``_block_reviews_owed_for`` for a run that was just cancelled, if it was a
        *review* run.

        Scoped to review runs on purpose. A cancelled worker run owes nothing — the
        obligation belongs to a worker run that *succeeded* — and calling this for one
        would raise a blocker for whatever else the task happens to owe, at a moment
        that has nothing to do with it.
        """
        if run is None or run.state != RunState.CANCELLED:
            return
        if role_of(run.result_metadata) != ROLE_REVIEW:
            return
        self._block_reviews_owed_for(run.task_id)

    def _block_review_owed(self, *, task_id: str, run_id: str | None, reason: str) -> None:
        """Record a durable BLOCKER saying the cross-review did not happen.

        Raised by: every ``_dispatch_review`` path that ends without a review run,
        and by ``reconcile_reviews_owed`` for a review lost to a crash.
        Released by: a human answering or cancelling the item. ``blocker`` is one of
        ``GATING_ATTENTION_KINDS``, so while it is open ``ExecutionStore.create``
        refuses another worker run on the task and the Continue action declines to
        offer one; and once it exists in *any* state the derived review-owed refusal
        stands down for that run, so clearing this item is a real exit rather than
        one of two gates. An unavailable reviewer blocks its work; it does not
        delete the review stage.

        **Idempotent.** A retry — a second worker run that also fails to dispatch a
        review, or a redelivery of the same failure — must not pile up blockers, so
        an existing OPEN item with this title, this requester and this ``run_id`` is
        left exactly as it is. Not updated either: the first failure is the one that
        gated the task, and rewriting its body would move a record a human may
        already be reading. The task timeline still carries every attempt, because
        each attempt is a run event.

        That test is ``AttentionStore.create_once``: an exact existence query and the
        insert, in one transaction. It used to be a scan of the first 50 open
        blockers on the task from this side of the store, which is a different and
        weaker thing — attention ordering puts critical items first, so 50 critical
        blockers scoped to the same run hid the existing item and a redelivery
        created a duplicate with the identical key. A cap cannot answer an existence
        question, and the read-then-create it was part of was not one transaction.

        The item is scoped to the run whose review is missing, so answering it
        binds to that run rather than to the task at large.

        **The body states only exits that exist (O3, 2026-08-30).** It used to say
        "then dispatch the run again", and two of the three ``REVIEW_OWED_REASONS``
        said "dispatch a review again" — the exact nonexistent escape hatch N1 was
        recorded for naming, rewritten into the message a human reads. ``ROLE_REVIEW``
        is a server-side argument to ``ExecutionStore.create`` and ``_dispatch_review``
        is its only production caller, so a review cannot be started by hand at all.
        The two real exits are here, in one place.

        **The body claims only what is true of every cause (P1, 2026-08-30).** It used
        to open with "this work has not been seen by any review", and exit 1 with
        "the work stays as nothing has reviewed it". That is false for
        ``REVIEW_OWED_NO_GATE_ITEM``, whose own ``reason`` says the review *ran* and may
        well have reached a conclusion that failed to save — the body contradicted the
        reason printed two lines above it. It is not verifiable for
        ``REVIEW_OWED_REVIEW_CANCELLED`` either: ``cancel`` can stop a RUNNING review,
        and a reviewer that had begun reading has seen the work. What is true of all
        five causes — the three ``_dispatch_review`` failures and both reconciled ones —
        is that **no review decision is on record**, which is the same fact
        ``_has_review_gate_item`` releases on, so that is what the body says. Which
        review, if any, ever looked at the work is left to the cause-specific
        ``reason``, because that is the only place it can be said truthfully.

        **The coverage limit of exit 2 is stated as the bound the code enforces (P2,
        2026-08-30).** The first attempt at this caveat said "a later review reads the
        worktree as it then stands", which overstates it in the same direction the
        caveat was added to correct: ``_review_prompt`` samples. It takes only files
        ``list_changes`` reports as changed against HEAD (``[]`` on any git failure,
        indistinguishable from clean, and capped at ``MAX_FILES``), truncates each to
        ``_REVIEW_FILE_CHARS``, stops adding files once ``_REVIEW_PROMPT_BUDGET`` is
        spent, and skips a file it cannot read. So an unchanged 2,000-character file is
        covered to its first 1,500 characters and a file past the aggregate budget is
        not covered at all. The numbers are interpolated from those constants rather
        than written out, so the sentence cannot drift from the bound.
        """
        AttentionStore(self.store.tasks).create_once(
            CreateAttentionRequest(
                task_id=task_id,
                run_id=run_id,
                kind=AttentionKind.BLOCKER,
                urgency=AttentionUrgency.HIGH,
                title=self.REVIEW_FAILED_TITLE,
                body=(
                    "这次实现运行已完成,但没有任何独立交叉评审的结论被记录下来,因此没有人"
                    "被要求接受这份工作。具体是哪一种情况,见原因:\n"
                    f"{reason}\n\n"
                    "本条打开期间会挡住这个任务的新实现运行,所以两个出口都从处置本条"
                    "(回复或取消)开始:\n"
                    "1) 处置本条,不再补评审:这份工作就以“没有任何评审结论”的状态留下,"
                    "任务随后可以接受新的实现运行。选这个出口,就是在没有评审结论的情况下"
                    "接受这份工作。\n"
                    "2) 先处理上面的原因(例如配置或登录评审运行时),处置本条,然后重新派发"
                    "一次实现运行;它成功后 Agora 会自动为*那次*运行派发交叉评审。那次评审"
                    "读到的不是完整的工作区,而是一份有上限的采样:只包含新运行结束时工作区里"
                    f"相对 HEAD 有改动的文件,每个文件最多前 {_REVIEW_FILE_CHARS} 个字符,"
                    f"所有文件合计约 {_REVIEW_PROMPT_BUDGET} 个字符(超出后其余文件整个略去),"
                    f"最多 {MAX_FILES} 个文件;读不出来的文件会被跳过,git 给不出答案时按"
                    "“没有改动”处理。所以上面这次运行的产出,只有仍留在工作区里*并且*落在"
                    "这份采样之内的部分会被看到 —— 一个 2000 字符、之后没再改动的文件,只有"
                    f"前 {_REVIEW_FILE_CHARS} 个字符会被看到;排在合计上限之外的文件完全不会"
                    "被看到。\n"
                    "Agora 没有单独派发评审运行的接口(评审只会跟在一次成功的实现运行之后),"
                    "因此“再派发一次评审”不是一个出口。"
                )[:32_000],
                requester=REVIEW_REQUESTER,
            ),
            # This *is* the control plane raising its own item, which is the only
            # caller allowed to claim REVIEW_REQUESTER (AttentionStore.create).
            internal=True,
        )

    async def _dispatch_review(self, worker_run: ExecutionRun) -> None:
        """Queue an independent review run by the project's reviewer agent.

        Every path that ends without a review run records a blocker naming what
        failed (see ``_block_review_owed``). Returning quietly is what turned a
        missing reviewer into "this work needs no review", which is the one answer
        that must not be produced silently.
        """
        try:
            project = self.projects.get(worker_run.project_id)
        except KeyError:
            self._block_review_owed(
                task_id=worker_run.task_id, run_id=worker_run.run_id,
                reason=f"项目 {worker_run.project_id} 已不在配置中,无法确定评审代理。",
            )
            return
        reviewer = project.reviewer_agent
        if reviewer not in self.adapters:
            # A pinned runtime is never silently substituted, and "skip the review"
            # is a substitution: it replaces an independent review with none.
            self._block_review_owed(
                task_id=worker_run.task_id, run_id=worker_run.run_id,
                reason=(
                    f"评审运行时 {reviewer!r} 未配置或已停用(可用:"
                    f"{', '.join(sorted(self.adapters)) or '无'})。"
                ),
            )
            return
        task = self.store.tasks.get(worker_run.task_id)
        if task is None:
            # Nothing to gate and nowhere to record: an attention item requires the
            # task row it hangs on. The run's own record is the only trace left, and
            # that is the honest end of this path.
            return
        worker_adapter = self.adapters.get(worker_run.adapter)
        reviewer_adapter = self.adapters[reviewer]
        if is_remote(worker_adapter) or is_remote(reviewer_adapter):
            # A review reads the worker's worktree, so the two must be on the same
            # account of the same host (W3-a U5). Not a substitution to make quietly —
            # the task is blocked with the two places named.
            compatible = (
                is_remote(worker_adapter) and is_remote(reviewer_adapter)
                and same_remote_host(worker_adapter.remote, reviewer_adapter.remote)  # type: ignore[arg-type]
            )
            if not compatible:
                self._block_review_owed(
                    task_id=worker_run.task_id, run_id=worker_run.run_id,
                    reason=(
                        f"评审代理与开发代理不在同一台主机上：开发 {worker_run.adapter!r} 在 "
                        f"{describe_target(worker_adapter.remote if worker_adapter else None)}，评审 {reviewer!r} 在 "
                        f"{describe_target(reviewer_adapter.remote)}。复核要读开发的工作树，两者必须配置在同一台主机。"
                    ),
                )
                return
        try:
            prompt = (
                await self._review_prompt_remote(worker_run, worker_adapter)  # type: ignore[arg-type]
                if is_remote(worker_adapter)
                else await asyncio.to_thread(self._review_prompt, worker_run)
            )
            request = CreateRunRequest(
                task_id=worker_run.task_id,
                adapter=reviewer,
                prompt=prompt,
                timeout_seconds=worker_run.timeout_seconds,
                actor=REVIEW_REQUESTER,
                expected_task_version=task.version,
            )
            # The role travels as an argument, not as request metadata: the request
            # model refuses the reserved keys so no client can forge a role, and this
            # is the only place that asks for ROLE_REVIEW.
            review_run = self.queue(request, role=ROLE_REVIEW, reviews_run_id=worker_run.run_id)
        except Exception as exc:
            # A failed prompt build, a rejected request, a queue conflict, a task
            # that moved underneath us: all of them mean no review exists, and that
            # is what has to be visible.
            self._block_review_owed(
                task_id=worker_run.task_id, run_id=worker_run.run_id,
                reason=f"评审运行未能入队:{type(exc).__name__}: {exc}",
            )
            return
        self.schedule(review_run.run_id)

    def _review_prompt(self, worker_run: ExecutionRun) -> str:
        """Build a bounded review prompt from the developer run's deliverables."""
        workspace = Path(worker_run.workspace).expanduser().resolve()
        parts = [
            "You are an independent code reviewer. A developer agent produced the "
            "changes below. Review them for correctness, edge cases, and safety. "
            "List concrete findings (file + issue); if everything looks correct, say so clearly.",
            f"\n## Developer's own summary\n{worker_run.stdout_tail[:_REVIEW_FILE_CHARS]}",
        ]
        budget = _REVIEW_PROMPT_BUDGET
        # Only read the worker run's own provisioned worktree, never a surrounding
        # or unrelated repository.
        try:
            project_root = self.projects.get(worker_run.project_id).root
        except (KeyError, LookupError):
            project_root = None
        expected_branch = worktree_branch(worker_run.project_id, worker_run.adapter)
        ready = project_root is not None and is_ready_worktree(workspace, project_root, expected_branch)
        # A workspace that is not this run's provisioned worktree is not read at all —
        # not even through the snapshot's paths — so an unready one contributes nothing.
        files, note = self._reviewable_files(worker_run, list_changes(workspace)) if ready else ([], None)
        if note:
            parts.append(note)
        if not files:
            parts.append("\n## Changes\n(No changed files detected in the workspace.)")
        for entry in files:
            if entry.get("status") == "deleted":
                parts.append(f"\n## FILE: {entry['path']} (deleted)")
                continue
            try:
                content = read_file(workspace, entry["path"])["content"]
            except (FileNotFoundError, PermissionError, OSError):
                continue
            block = f"\n## FILE: {entry['path']} ({entry['status']})\n{content[:_REVIEW_FILE_CHARS]}"
            if len(block) > budget:
                parts.append("\n[additional files omitted for length]")
                break
            parts.append(block)
            budget -= len(block)
        return "\n".join(parts)[:15_900]

    def _raise_review_attention(
        self, review_run: ExecutionRun, reviews_run_id: str | None, kind: AttentionKind,
    ) -> None:
        """Turn a completed review run into a human attention item on the task."""
        if kind == AttentionKind.APPROVAL:
            title = f"评审完成,待你批准(评审:{review_run.adapter})"
            body = review_run.stdout_tail or "(评审未产出文本)"
        else:
            title = f"评审运行未完成(评审:{review_run.adapter})"
            body = review_run.error_message or review_run.stderr_tail or "(无错误详情)"
        AttentionStore(self.store.tasks).create(
            CreateAttentionRequest(
                task_id=review_run.task_id,
                run_id=reviews_run_id,
                kind=kind,
                urgency=AttentionUrgency.NORMAL,
                title=title[:500],
                body=body[:32_000],
                requester=REVIEW_REQUESTER,
            ),
            internal=True,  # the control plane's own gate item; see AttentionStore.create
        )

    def _workspace_is_allowed(self, workspace: Path, project_root: Path) -> bool:
        allowed_roots = [project_root.expanduser().resolve(), *self.allowed_workspace_roots]
        return any(workspace.is_relative_to(root) for root in allowed_roots)

    def is_workspace_readable(self, project_id: str, workspace: Path) -> bool:
        """Read-only confinement check for a run's workspace (delivery viewing)."""
        try:
            project = self.projects.get(project_id)
        except KeyError:
            return False
        if project is None:
            return False
        return self._workspace_is_allowed(workspace.expanduser().resolve(), project.root)

    @staticmethod
    def _job_api():
        """The four kernel32 calls the job object needs, with real prototypes.

        Declaring them is not optional. Left to ctypes' defaults, ``restype`` is
        a C ``int``, so a 64-bit HANDLE comes back **truncated** — and the next
        call on it raises STATUS_INVALID_HANDLE, which on Windows is a fatal
        process exception, not a Python error. Measured: it killed the test
        runner outright.
        """
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        return ctypes, wintypes, kernel32

    @staticmethod
    def _adopt_process_tree(proc: asyncio.subprocess.Process) -> object | None:
        """Put a run's process **and everything it starts** into one job object.

        Windows only, and the mechanism that covers what ``taskkill /T`` cannot:
        a job holds every descendant, so an agent that launches a background
        script and exits does not leave it running. Configured to kill its
        members when the last handle closes, which also covers Agora going away
        — a stray handle is not what should keep a worktree being written to.

        Returns the handle to hold for the run's lifetime, or ``None`` when the
        platform or the API says no. Failure is not fatal: the signal path in
        ``_stop`` still stops the run itself, which is what the caller waits on.
        """
        if os.name != "nt":
            return None
        try:
            ctypes, wintypes, kernel32 = ExecutionDispatcher._job_api()

            class _BasicLimits(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class _IoCounters(ctypes.Structure):
                _fields_ = [
                    ("ReadOperationCount", ctypes.c_uint64),
                    ("WriteOperationCount", ctypes.c_uint64),
                    ("OtherOperationCount", ctypes.c_uint64),
                    ("ReadTransferCount", ctypes.c_uint64),
                    ("WriteTransferCount", ctypes.c_uint64),
                    ("OtherTransferCount", ctypes.c_uint64),
                ]

            class _ExtendedLimits(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", _BasicLimits),
                    ("IoInfo", _IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                return None
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(
                job, 9, ctypes.byref(limits), ctypes.sizeof(limits),
            ):
                kernel32.CloseHandle(job)
                return None
            # PROCESS_SET_QUOTA is the one that matters: without it
            # AssignProcessToJobObject returns ACCESS_DENIED, which is what
            # the first version of this got — measured, not guessed.
            handle = kernel32.OpenProcess(
                0x0100 | 0x0001 | 0x0400,  # SET_QUOTA | TERMINATE | QUERY_INFORMATION
                False, proc.pid,
            )
            if not handle:
                kernel32.CloseHandle(job)
                return None
            assigned = kernel32.AssignProcessToJobObject(job, handle)
            kernel32.CloseHandle(handle)
            if not assigned:
                kernel32.CloseHandle(job)
                return None
            return job
        except Exception:
            # ctypes on an unexpected build, a policy that forbids jobs, a
            # nested-job restriction. The stop path below runs then, and CR-46
            # says what that leaves uncovered.
            return None

    @staticmethod
    def _kill_process_tree(job: object | None) -> None:
        """Terminate a job object's members — the run and everything under it.

        **Terminating only.** Closing the handle here as well was a double close:
        a stop and the run's own release both ran, the second `CloseHandle` was
        given a handle that no longer existed, and on Windows that is a *fatal
        process exception* (STATUS_INVALID_HANDLE), not an error code — measured
        as the test runner dying mid-suite. The handle has exactly one owner: the
        run, which releases it in its `finally`.
        """
        if job is None or os.name != "nt":
            return
        try:
            _, _, kernel32 = ExecutionDispatcher._job_api()
            kernel32.TerminateJobObject(job, 1)
        except Exception:
            return

    @staticmethod
    def _release_process_tree(job: object | None) -> None:
        """Kill whatever is left in the job and close it, once, at the run's end."""
        if job is None or os.name != "nt":
            return
        try:
            _, _, kernel32 = ExecutionDispatcher._job_api()
            kernel32.TerminateJobObject(job, 1)
            kernel32.CloseHandle(job)
        except Exception:
            return

    @staticmethod
    async def _stop(
        proc: "asyncio.subprocess.Process | ProcessHandle", job: object | None = None
    ) -> None:
        """Stop a run's process — and on Windows, the tree under it.

        An agent's own children are the ones holding the worktree: a shell, a
        copy, a build. Signalling only the CLI left those running while Agora had
        already snapshotted the deliverables and released the workspace lock, so
        the next run entered a worktree a live process was still writing to.

        **Exactly what this covers: Windows.** Every descendant, whether or not
        the CLI itself is still alive, because the run was assigned to a job
        object at spawn (:meth:`_adopt_process_tree`) and killing the job kills
        its members. ``taskkill /T`` remains as the path for a run started before
        a job could be created — it walks the tree from a *live* parent, which is
        why the job is the mechanism and it is the fallback.

        **POSIX is not covered, and is not claimed to be.** The signal path below
        reaches the CLI and not the shell or copy it started; closing it needs the
        children in their own process group, which changes how signals reach every
        run and cannot be measured from this machine. CR-46 carries it, with a
        skipped test naming the platform.
        """
        # First, and whether or not the parent is still alive — that is the whole
        # point of the job. A run whose CLI exited leaving a background script
        # behind used to reach the early return below and keep writing the
        # worktree while Agora froze the snapshot and released the lock.
        ExecutionDispatcher._kill_process_tree(job)
        if _is_handle(proc):
            # An environment-launched process (U1): remote, or any handle. Signals go
            # over the handle; there is no local tree to walk.
            await _stop_handle(proc)  # type: ignore[arg-type]
            return
        if proc.returncode is not None:
            return
        pid = getattr(proc, "pid", None)
        if os.name == "nt" and pid:
            # Tried first, and while the parent is alive: `taskkill /T` walks the
            # tree from it, so killing the parent first would orphan exactly the
            # children this exists to reach. Skipped without a pid — a reaped
            # process, or a stub standing in for one in a test of the signal
            # path below — since there is no tree to walk from.
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill", "/PID", str(pid), "/T", "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(killer.wait(), timeout=10)
            except (OSError, TimeoutError, asyncio.TimeoutError):
                # Not found, or too slow. The signal path below is the backstop:
                # it still stops the run itself, which is the part the caller is
                # waiting on.
                pass
            if proc.returncode is not None:
                return
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except (TimeoutError, asyncio.TimeoutError):
            try:
                proc.kill()
            except ProcessLookupError:
                return
            await proc.wait()

    def _settle_session(
        self,
        *,
        run_id: str,
        task_id: str,
        adapter: str,
        workspace: Path,
        transcript: TranscriptReader,
        resuming: bool,
        exit_code: int | None,
        role: str,
        requested: str | None,
        killed: bool,
    ) -> None:
        """Record what this turn established about the task's session.

        Two writes, and the second one is narrow on purpose.

        **The id.** If the provider announced a session id, record it. For codex
        and kiro this is the only way it is ever learned; for claude it confirms
        the id Agora handed over.

        **The failure.** A *resumed* turn that failed **without the provider ever
        naming a session** is the one shape that says the session could not be
        picked up: all three CLIs announce the session in their first event, so
        silence means the process died before it had one. That is what gets
        marked unreadable.

        What this deliberately does **not** claim: that every unreachable session
        is caught here. A resume that fails *after* announcing its session (the
        work itself failed), or a provider that stops announcing sessions in a
        future version, both leave the state ``active``. The narrower guard is
        the one that is true; the state is a report of what was observed, not a
        proof of reachability, and the window says so by showing the last turn's
        outcome next to it.
        """
        if transcript.session_conflict is not None:
            # Two sessions in one turn. Which one holds the work is not knowable
            # from here — the second is the better guess and a guess is exactly
            # what must not be recorded as a live session — so this fails closed:
            # the next turn is refused until the person starts a new conversation.
            first, second = transcript.session_conflict
            self.sessions.mark_lost(
                task_id=task_id, adapter=adapter, role=role,
                reason=(
                    f"这一轮的输出里出现了两个不同的会话 id（先 {first}，后 {second}）。"
                    "工作很可能落在后一个里，而记录指向前一个——继续续接任何一个都可能"
                    "接不上这一轮、甚至重做或覆盖它写过的文件。所以这里停下来，"
                    "要继续请显式新开一个会话。"
                ),
            )
            return
        if transcript.session_id:
            if resuming and requested and transcript.session_id != requested:
                # The provider answered in a different session than the one this
                # turn asked to continue. Recording it as a healthy active
                # session — which is what happened before — overwrote the id the
                # conversation had and left the badge reading "session live"
                # while the context was gone.
                self.sessions.mark_forked(
                    task_id=task_id, adapter=adapter, role=role,
                    observed=transcript.session_id, previous=requested,
                    run_id=run_id,
                )
                return
            self.sessions.record_observed(
                run_id=run_id, task_id=task_id, adapter=adapter,
                workspace=workspace, session_id=transcript.session_id, role=role,
            )
            return
        # Only a process that **ended on its own** is evidence about the session.
        # A cancelled turn says nothing: the person clicked Stop, often on a CLI
        # that was merely slow to print its first event — and marking the session
        # lost there refused every later message and left "start a new session",
        # which throws the context away, as the only way out. A timeout is
        # excluded for the same reason: Agora killed it.
        if not resuming:
            # A first turn that never named a session. There is no id to resume,
            # so the next message must start a new conversation — but it has to
            # say that this turn's context is not coming with it. Recording
            # nothing left the next turn reading as a clean first turn, which is
            # the silence adr/0007 exists to forbid.
            self.sessions.mark_unidentified(
                task_id=task_id, adapter=adapter, workspace=workspace, role=role,
                reason=(
                    "这一轮跑过了，但它的输出里读不到会话 id"
                    f"（退出码 {exit_code}）。可能供应商那边确实有一个会话，"
                    "只是 Agora 叫不出它的名字——所以下一条消息会开一段新的对话，"
                    "这一轮的上下文不会被带过去。"
                ),
            )
            return
        if resuming and exit_code == 0 and not killed:
            # It resumed, it succeeded, and it never named a session. The row was
            # left `active` — the badge kept saying "session live · N turns" —
            # which asserts something this turn did not show. All three CLIs
            # announce the session in their first event, so silence means either
            # the resume was ignored (the work went somewhere Agora cannot see)
            # or the reader no longer recognises the event. Either way continuity
            # is unproven, and `unreadable` is the honest state: it says so,
            # ``decide`` re-derives the conditions next turn rather than blocking,
            # and the id is kept because the conversation is probably still there.
            self.sessions.mark_unreadable(
                task_id=task_id, adapter=adapter, role=role,
                reason=(
                    f"这一轮续接跑完了（退出码 {exit_code}），"
                    "但输出里始终没有出现会话 id——所以没法确认它接上的是同一段会话。"
                    "可能是 CLI 换了事件格式（Agora 读不出来），"
                    "也可能是它忽略了 resume、在另一段会话里干了这一轮的活。"
                    "下一条消息仍会按记录里的 id 续接；如果你发现上下文接不上，请显式新开一个会话。"
                ),
            )
            return
        if resuming and exit_code != 0 and not killed:
            self.sessions.mark_lost(
                task_id=task_id, adapter=adapter, role=role,
                reason=(
                    f"上一次续接会话失败（退出码 {exit_code}），"
                    "而且这一轮里供应商始终没有报出会话 id —— 说明它没能接上这段会话。"
                    "上下文可能还在，但这次没读到。"
                ),
            )

    async def _read_streaming(
        self,
        stream: asyncio.StreamReader | None,
        run: ExecutionRun,
        workspace: Path,
        transcript: TranscriptReader,
        role: str,
        requested: str | None,
        gate: "_ReaderGate",
    ) -> tuple[bytes, bool]:
        """Read stdout once: the bounded tail, a live transcript, and the session id.

        Returns the tail and **whether anything was dropped from its front**. The
        tail is byte-identical to what ``_read_tail`` would have returned, so the
        usage normaliser sees what it always saw; the flag is what tells it the
        first line may be half an event.

        Rendering happens on whole lines only — every supported format is JSON
        Lines, and half a line is not an event.

        **Redaction spans events**, which the first version of this got wrong. It
        redacted each rendered unit on its own and claimed a secret could not be
        split across two writes — false for exactly the two adapters whose
        streaming this was built for: claude and kiro emit text token by token, so
        an API key arrives as several deltas, each individually harmless, and the
        log concatenated them back into the original. Text is now held in a
        buffer until a boundary, redacted with its neighbours in hand, and the
        API redacts again over the contiguous slice it serves.
        """
        if stream is None:
            return b"", False
        tail = bytearray()
        pending = bytearray()
        dropped = False
        writer = _RedactingWriter(self.run_logs, run.run_id)
        while True:
            chunk = await stream.read(8192)
            if not chunk:
                break
            tail.extend(chunk)
            if len(tail) > OUTPUT_TAIL_LIMIT * 2:
                del tail[:-OUTPUT_TAIL_LIMIT]
                dropped = True
            pending.extend(chunk)
            # Whole lines first, **then** the ceiling. Checking the ceiling first
            # swallowed everything in the buffer as one unparseable fragment —
            # including complete events sitting behind the newline that ended the
            # long one, in the same 8 KiB read. Those events are where the session
            # id, the final text and the usage live: losing them silently made the
            # next turn look like a new conversation.
            while True:
                newline = pending.find(b"\n")
                if newline < 0:
                    break
                line = bytes(pending[:newline])
                del pending[: newline + 1]
                self._render_line(
                    run, workspace, transcript, role, requested, line, writer, gate,
                )
            if len(pending) > _MAX_PENDING_LINE:
                # What is left holds no newline at all, so this is genuinely one
                # "line" too long to be one. A JSONL event can legitimately be
                # large — a command's aggregated output — but without a ceiling a
                # CLI that never emits a newline grew this buffer until the
                # control plane ran out of memory while its child kept editing the
                # worktree. Fed as-is: the reader shows an unparseable fragment
                # verbatim, which is the honest rendering of output that arrived
                # without structure.
                self._render_line(
                    run, workspace, transcript, role, requested, bytes(pending), writer,
                    gate,
                )
                pending.clear()
        if pending:
            # A last line with no trailing newline. Fed rather than dropped: it is
            # a whole event whenever the process was killed between writing an
            # event and its newline, and the reader handles a fragment by showing
            # it verbatim.
            self._render_line(
                run, workspace, transcript, role, requested, bytes(pending), writer, gate,
            )
        if gate.open:
            # The gate covers the *writer's* last flush too. A chunk with no
            # newline sits in its buffer until close, so a reader abandoned at
            # the drain bound wrote that text into a turn the window had already
            # been told was complete — text nobody would see without reloading.
            writer.close()
        # The final trim can drop a front the loop never counted.
        dropped = dropped or len(tail) > OUTPUT_TAIL_LIMIT
        return bytes(tail[-OUTPUT_TAIL_LIMIT:]), dropped

    def _render_line(
        self,
        run: ExecutionRun,
        workspace: Path,
        transcript: TranscriptReader,
        role: str,
        requested: str | None,
        line: bytes,
        writer: "_RedactingWriter",
        gate: "_ReaderGate",
    ) -> None:
        if not gate.open:
            # The turn settled without this reader; whatever it is holding now
            # belongs to a conversation the window has already finished drawing,
            # and its session events would overwrite what a later turn
            # established.
            return
        try:
            rendered = transcript.feed(line.decode("utf-8", errors="replace"))
        except Exception:
            # Rendering is a reading surface. A reader that trips over an event
            # shape must not be able to kill a run that is otherwise fine; the
            # cost is a gap in the transcript, and the run's own settled output is
            # unaffected because it comes from the byte tail.
            return
        if rendered:
            writer.write(rendered)
        # Persist the session id the moment the provider names it, not when the
        # run settles. codex and kiro announce it in their first event, and a
        # first turn that announced its session and then died — a crash, a
        # dispatcher shutdown, a kill — used to leave the database saying "no
        # session at all", so the *next* message opened a fresh context in
        # silence. That is the one failure adr/0007 exists to prevent, and it was
        # reachable through the ordinary crash path.
        # Checked **outside** the "a new id appeared" guard below, and before it.
        # The reader keeps the *first* id and records the conflict beside it, so
        # `session_id` does not change when the second one arrives — and keying
        # the whole persistence block on that change meant the conflict was never
        # seen while streaming. The first id stayed on record as `active`, and a
        # crash before settlement left the next turn resuming a session that does
        # not hold this turn's work, free to redo it or overwrite the files the
        # second session wrote.
        if transcript.session_conflict is not None:
            if self._session_noted.get(run.run_id) != _CONFLICT_NOTED:
                self._session_noted[run.run_id] = _CONFLICT_NOTED
                first, second = transcript.session_conflict
                try:
                    self.sessions.mark_lost(
                        task_id=run.task_id, adapter=run.adapter, role=role,
                        reason=(
                            f"这一轮的输出里出现了两个不同的会话 id（先 {first}，后 {second}）。"
                            "工作很可能落在后一个里——继续续接任何一个都可能接不上这一轮，"
                            "甚至重做或覆盖它写过的文件。要继续请显式新开一个会话。"
                        ),
                    )
                except Exception:
                    # Retried on the next line, and again at settlement.
                    self._session_noted.pop(run.run_id, None)
            return
        if transcript.session_id and self._session_noted.get(run.run_id) != transcript.session_id:
            self._session_noted[run.run_id] = transcript.session_id
            try:
                if requested and transcript.session_id != requested:
                    # A resume that landed somewhere else, recorded **here**
                    # rather than at settlement. Writing it as a healthy observed
                    # session and correcting it later meant a dispatcher that
                    # stopped in between lost both the old id and the fact that a
                    # fork happened — and the window then claimed continuity the
                    # new session does not have.
                    self.sessions.mark_forked(
                        task_id=run.task_id, adapter=run.adapter, role=role,
                        observed=transcript.session_id, previous=requested,
                        run_id=run.run_id,
                    )
                    return
                self.sessions.record_observed(
                    run_id=run.run_id, task_id=run.task_id, adapter=run.adapter,
                    workspace=workspace, session_id=transcript.session_id, role=role,
                )
            except Exception:
                # Same reasoning as the render guard: a failed write costs the
                # session record, which `_settle_session` will try again at the
                # end, and must not take the run down with it. Cleared so that
                # retry is not skipped as already-done.
                self._session_noted.pop(run.run_id, None)

    @staticmethod
    async def _read_tail(stream: asyncio.StreamReader | None) -> bytes:
        if stream is None:
            return b""
        _head, tail, _total = await ExecutionDispatcher._read_head_and_tail(
            stream, keep_head=0
        )
        return tail

    @staticmethod
    async def _read_head_and_tail(
        stream: asyncio.StreamReader | None, *, keep_head: int
    ) -> tuple[bytes, bytes, int]:
        """Drain a pipe, keeping its first ``keep_head`` bytes and its last tail.

        The head matters for one question only, and it is the important one: all
        three CLIs announce the session in their **first** event. A reader given
        only the bounded tail of a chatty run therefore reported "no session
        could be named" for a turn that had named one in its opening line, and
        the next message opened a new conversation over work that existed.
        """
        if stream is None:
            return b"", b"", 0
        head = bytearray()
        tail = bytearray()
        total = 0
        while True:
            chunk = await stream.read(8192)
            if not chunk:
                break
            if keep_head and len(head) < keep_head:
                head.extend(chunk[: keep_head - len(head)])
            total += len(chunk)
            tail.extend(chunk)
            if len(tail) > OUTPUT_TAIL_LIMIT * 2:
                del tail[:-OUTPUT_TAIL_LIMIT]
        # The total is returned because it is the only thing that says how the
        # two pieces relate. Inferring the overlap from their *bytes* — the first
        # version — found a false one wherever the output repeats: a newline, a
        # run of spaces, a duplicated log block. It then deleted real output and
        # reported no gap.
        return bytes(head), bytes(tail[-OUTPUT_TAIL_LIMIT:]), total


def redact_output(value: str) -> str:
    """Compatibility wrapper for callers that imported the original helper."""
    return redact_text(value)


class _RedactingWriter:
    """Buffers a run's rendered transcript so redaction can see across events.

    The problem it solves: claude and kiro stream assistant text token by token,
    so ``sk-…`` reaches this code as several rendered chunks. Redacting each chunk
    alone matches nothing and the log ends up holding the whole secret, which the
    output API then serves.

    So text is held until a **line boundary**, and the whole buffer is redacted
    *before* any of it is written. Secret patterns are single-line by
    construction (a key, a token, an ``api_key=value`` assignment), so a completed
    line is the right unit — and redacting the buffer as a whole is what makes the
    unit hold: a secret anywhere in it is matched while both of its halves are
    still in hand.

    The first version of this got that wrong in a way worth keeping written down.
    It redacted only the part it was about to write and kept ``_OVERLAP``
    characters back "so a pattern straddling the cut is still whole on the next
    pass" — but the cut had already been written by then, unredacted, and the
    retained half alone matches nothing. The name promised an overlap the code
    did not perform.

    A forced flush — a single line longer than ``_FLUSH_LIMIT`` — is the one cut
    the buffer cannot avoid, and two things make it safe rather than merely
    documented:

    * what is **retained** is as long as the longest secret in this process's
      environment, read from it rather than assumed, so an exact-match secret
      spanning the cut is still whole on the next pass; and
    * a redaction that ran to the very end of the buffer means a ``key=value``
      value was cut mid-flight, and whatever arrives next is its continuation:
      those characters are dropped until the value's own terminator (whitespace,
      ``,`` or ``;``) rather than written. The marker is already in the text, so
      the reader sees a redaction, not a hole.

    This used to be written down as an accepted exception, with the API's second
    redaction pass offered as the cover. It is not cover: that pass sees one
    served window, and the two halves of a long credential are in the log by
    then. What is *still* not covered is stated exactly — a secret longer than
    ``_FLUSH_LIMIT`` that redaction cannot recognise from either half alone.
    """

    #: Force a flush rather than buffer without limit — a single line of output
    #: can be megabytes, and a window showing nothing until it ends is the
    #: three-minute silence this whole surface exists to remove.
    _FLUSH_LIMIT = 8192
    #: Retained at the end of every emit, so a redaction *pattern* whose literal
    #: prefix has arrived but whose value has not (``api_`` with ``key=…`` still
    #: to come) is still whole on the next pass. Sized for those literals, not for
    #: secrets: an exact environment secret is recognised by ``safe_cut`` from its
    #: prefix, so it needs no size assumption at all — which is what the previous
    #: version made, and it was wrong in both directions (too small to matter for
    #: a straddling secret, large enough to hold the live transcript back).
    _KEEP = 64
    #: Characters that end a value, per the redaction patterns' own definition of
    #: one. Named here because the hold below has to end at exactly the same
    #: place a pattern would.
    _VALUE_END = " \t\r\n,;"
    #: Ceiling on an unfinished value the buffer is holding whole. Only output
    #: with none of the separators the patterns look for reaches it; at that
    #: point the run becomes the marker rather than being kept, because it cannot
    #: be shown to be safe and it cannot be held for ever.
    _HOLD_LIMIT = 64 * 1024

    def __init__(self, logs: RunLogStore, run_id: str) -> None:
        self._logs = logs
        self._run_id = run_id
        self._buffer = ""
        # Bounded by the flush limit: retaining more than the buffer holds would
        # mean never emitting anything, which is the silence this surface exists
        # to remove.
        self._keep = self._KEEP
        #: Set when a value grew past `_HOLD_LIMIT` and was collapsed to a
        #: marker. What arrives next is the rest of *that* value — the trigger
        #: word is gone, so redaction cannot recognise it — and it is dropped up
        #: to the value's own terminator. This is the one place a guess is not
        #: needed: the collapse only happens when a value was still growing.
        self._dropping = False

    def write(self, text: str) -> None:
        if self._dropping:
            cut = next(
                (index for index, char in enumerate(text) if char in self._VALUE_END),
                None,
            )
            if cut is None:
                # Still the same run. Nothing of it is written: the marker that
                # replaced its first 64 KiB already stands in the transcript.
                return
            text = text[cut:]
            self._dropping = False
        self._buffer += text
        # Redact the whole buffer first, every time. Idempotent on text that has
        # already been through it, so re-redacting the retained tail costs
        # nothing and guarantees no half of a secret is ever written alone.
        # Measured on what is actually held, not on the redacted copy. Redaction
        # can shrink text — an `api_key=` prefix followed by an endless value
        # collapses to one short marker — so a limit read off the redacted length
        # was never reached: the buffer grew without bound and every delta
        # re-scanned all of it.
        held_text = self._buffer
        held = len(held_text)
        cut = held_text.rfind("\n")
        if cut >= 0:
            # A completed line is the unit — but the cut still goes through
            # `safe_cut`. A multi-line environment secret (a PEM, the ordinary
            # case) matches nothing while only its first line has arrived, and
            # emitting on the newline wrote that line in clear, permanently.
            line_cut = safe_cut(held_text, min(cut + 1, held - self._keep))
            if line_cut > 0:
                self._logs.append(self._run_id, redact_text(held_text[:line_cut]))
                self._buffer = held_text[line_cut:]
            return
        if held > self._FLUSH_LIMIT:
            # Where the cut may fall is one question, and `safe_cut` is the
            # one place that answers it: never inside a redaction, and never
            # inside one that reaches the end (it may still be growing). Asking
            # it in pieces produced four wrong versions — see that function for
            # what each of them leaked or ate.
            cut_point = safe_cut(held_text, held - self._keep)
            if held - cut_point > self._HOLD_LIMIT:
                # The buffer cannot be cut anywhere near the end and has grown
                # past any length a value plausibly has. Whatever is holding it
                # open cannot be shown safe and cannot be held for ever, so it
                # becomes the marker — a redaction the reader can see, not a
                # silent hole.
                keep_from = safe_cut(held_text, held - self._HOLD_LIMIT)
                self._logs.append(self._run_id, redact_text(held_text[:keep_from]))
                self._buffer = "[REDACTED]"
                # And the rest of that value is dropped rather than appended
                # after the marker: its trigger word went with the collapse, so
                # redaction would never match it again and the tail of the
                # credential would be written in clear.
                self._dropping = True
                return
            head = redact_text(held_text[:cut_point])
            self._buffer = held_text[cut_point:]
            if head:
                self._logs.append(self._run_id, head)

    def close(self) -> None:
        # The end of the stream: nothing more is coming, so everything held back
        # for a secret that might still complete is redacted as it stands and
        # written. A half-arrived secret matches nothing and is emitted — which
        # is correct, because half of a secret that never finished arriving is
        # what the agent actually printed.
        tail = redact_text(self._buffer)
        self._buffer = ""
        if tail:
            self._logs.append(self._run_id, tail)


def token_capturable_adapters(adapters: dict[str, ExecutionAdapter]) -> set[str]:
    """Adapter names whose configured execution path actually yields token counts.

    Derived from the registry rather than listed: an adapter reconfigured to a
    different output format changes the answer, and a hand-kept list would keep
    reporting the old one.

    The **execution path** is part of that answer, not just the format. Reading
    the format alone reported codex as token-capable while it was configured on
    the app-server bridge, which produces no usage result at all — so the
    settings page promised numbers that never arrive. A configuration that can be
    switched back in one line (see config.yaml, CR-44) must not be described by
    the half of it that happens to look right.
    """
    return {
        name for name, adapter in adapters.items()
        if adapter.event_format in ExecutionDispatcher._TOKEN_CAPTURE_FORMATS
        and adapter.bridge_mode != "codex_app_server"
    }


#: Written between a kept head and a bounded tail that do not meet. The reader
#: shows it verbatim, so a turn whose middle was dropped does not read as one
#: continuous answer.
_MERGE_GAP_NOTICE = "\n· 中间的输出没有保留：这一轮打印的内容超过了缓冲上限，上下两段之间缺了一截。\n".encode()


def _merge_overlapping(head: bytes, tail: bytes, total: int) -> bytes:
    """Join a stream's kept head and its bounded tail, using how many bytes there were.

    ``total`` is what the reader counted going past it, so the relationship is
    arithmetic, not a guess: the two pieces overlap by
    ``len(head) + len(tail) - total`` when that is positive, and everything
    between them was dropped when it is not.

    The first version inferred the overlap by matching bytes — the longest suffix
    of ``head`` that is a prefix of ``tail`` — which finds a false overlap
    wherever output repeats: one shared newline is enough. It then deleted real
    output *and* reported no gap, so the audit transcript disagreed with what the
    process printed and said nothing about it.
    """
    if not head:
        return tail
    if not tail:
        return head
    overlap = len(head) + len(tail) - total
    if overlap >= len(tail):
        # Everything the tail holds is already in the head — a short run that fit
        # in both.
        return head
    if overlap > 0:
        return head + tail[overlap:]
    if overlap == 0:
        return head + tail
    # A gap: `total` exceeds what the two pieces hold. Said, not silently closed
    # with a newline — the record used to read as one continuous output,
    # complete and not lossy, with its whole middle gone.
    return head + _MERGE_GAP_NOTICE + tail


def _settlement_stdout(
    tail: str, truncated: bool, transcript: TranscriptReader,
) -> tuple[str, bool]:
    """What the usage reader should parse, and whether it starts mid-event.

    Normally the byte tail, exactly as before. But a final answer can be a single
    JSON line **larger than the whole tail** — a long claude ``result``, a long
    kiro ``runFinished``, a long codex ``agent_message`` — and once that happens
    the tail holds no complete copy of it at all: the semantic text degrades to a
    JSON fragment and the token, cost and credit figures are simply gone, on
    precisely the runs whose cost is worth knowing.

    So when the tail was truncated and the reader retained the settlement lines
    while they streamed past, those lines are what gets parsed. They are whole by
    construction, which is why the truncation flag comes back false with them.

    The tail is still preferred when nothing was dropped: it is the complete
    stream, and rebuilding from a handful of retained lines would throw away
    context the readers are entitled to see.
    """
    if not truncated or not transcript.settlement:
        return tail, truncated
    return "\n".join(transcript.settlement.values()), False


def _failure_note(error: str | None, stderr_tail: str) -> str:
    """The turn's failure, as the conversation should read it.

    Both halves matter and neither is enough alone: the error message says what
    Agora concluded ("process exited with a non-zero status"), and stderr says
    what the CLI actually complained about — a missing flag, an expired login, a
    permission. A window showing only the first tells the person their message
    failed without telling them why.
    """
    lines = [f"✖ {error}"] if error else []
    tail = (stderr_tail or "").strip()
    if tail:
        lines.extend(tail.splitlines()[-20:])
    return "".join(f"{line}\n" for line in lines) if lines else ""


def _prompt_echo(prompt: str, resuming: bool, *, machine_written: bool = False) -> str:
    """The turn that opens the transcript.

    The transcript is read as a conversation, so it has to contain both halves.
    The marker also records which half of adr/0007's mechanism ran: ``›`` opened a
    session, ``››`` continued one. That is the one place a reader can see
    continuity actually happening rather than being asserted by a badge.

    **Every line of the message carries the marker**, not just the first. The
    message box is a textarea, so a two-paragraph instruction is ordinary — and
    marking only the first line put the rest into the terminal pane, where it read
    as something the *agent* had said. The window takes the leading run of marked
    lines as the message, so the boundary is in the text rather than assumed.

    ``machine_written`` marks a prompt **Agora composed**, not one a person typed.
    The review prompt embeds whole changed files (``_review_prompt``, up to ~16k
    characters), and echoing it line by line put alembic.ini, env.py and the new
    test files into the chat window — code the person had just watched the worker
    write, replayed at them as though they had said it (CR-106).

    The distinction is **who wrote it**, not how long it is. Echoing by length
    would truncate a person's own long instruction, which is exactly the "the
    system decided what you may see" behaviour that CR-99 removed.
    """
    marker = "››" if resuming else "›"
    if machine_written:
        # One line, and it says what the turn is rather than reprinting it. The
        # content is not lost: a review's subject is the worker run whose output
        # is right above it in the same conversation.
        body = prompt.strip()
        summary = f"（Agora 自动拼的复核请求，{len(body)} 字；内容是上面那一轮改动的文件）"
        return redact_text(f"{marker} {summary}\n")
    lines = prompt.strip().splitlines() or [""]
    return redact_text("".join(f"{marker} {line}\n" for line in lines))


def _is_handle(proc: object) -> bool:
    """Whether ``proc`` is a :class:`ProcessHandle` (U1) rather than a raw asyncio
    subprocess: the handle's ``terminate``/``kill`` are coroutines, the subprocess's
    are not. Asked of the object, not of a type, so any conforming handle qualifies."""
    return inspect.iscoroutinefunction(getattr(proc, "terminate", None))


async def _stop_handle(handle: ProcessHandle) -> None:
    """Stop an environment-launched process over its handle: terminate, wait
    ``GRACE`` seconds, kill. Idempotent for an exited process, like ``_stop``."""
    if handle.returncode is not None:
        return
    await handle.terminate()
    if await handle.wait(timeout=5) is None:
        await handle.kill()
        await handle.wait(timeout=5)
