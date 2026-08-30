"""Bounded async dispatch of durable execution runs."""
from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

from agora.projects import ProjectRegistry
from agora.orchestration.runtime import RuntimeLaunchError, resolve_runtime_command
from agora.orchestration.provider_usage import RuntimeResultFormat, normalize_native_output
from agora.tasks.store import TaskNotFoundError, TaskStore
from agora.attention.store import AttentionStore
from agora.attention.models import AttentionKind, AttentionUrgency, CreateAttentionRequest, REVIEW_REQUESTER
from agora.attention.bridges.codex_app_server import CodexAppServerError, CodexAppServerRunner
from agora.attention.bridges.codex_broker import CodexApprovalBroker

from agora.workspaces.naming import worktree_branch

from .adapters import ExecutionAdapter
from .deliverables import (
    MAX_FILES,
    baseline_state,
    is_ready_worktree,
    list_changes,
    read_file,
    snapshot_changes,
)
from .models import (
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
from .security import redact_text


_REVIEW_PROMPT_BUDGET = 12_000
_REVIEW_FILE_CHARS = 1_500


class ExecutionDispatcher:
    # Only claude's CLI print mode yields parseable per-run token usage today.
    # codex runs through the app-server bridge and kiro emits plain text, so
    # neither surfaces usage on this path (see settings token capture notes).
    _USAGE_RESULT_FORMATS = {"claude": RuntimeResultFormat.CLAUDE_JSON_V1}

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
    ):
        if max_concurrent_global < 1 or max_concurrent_per_project < 1:
            raise ValueError("execution concurrency limits must be positive")
        self.store = store
        self.projects = projects
        self.adapters = adapters
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
        # Per-run pre-launch worktree state, so an abandoned run can still record
        # what it delivered (the CancelledError path lives outside execute()).
        # A missing or None entry means the baseline was never established, which
        # snapshot_changes reads as "attribution unknown" — the correct answer.
        self._baselines: dict[str, tuple[dict[str, dict], bool] | None] = {}
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
            workspace = adapter.workspace(project.workspaces)
        except KeyError as exc:
            raise RunValidationError(str(exc)) from None
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

    async def _execute_guarded(self, run_id: str) -> ExecutionRun:
        try:
            return await self.execute(run_id)
        except asyncio.CancelledError:
            proc = self._active.get(run_id)
            if proc is not None:
                await self._stop(proc)
            current = self.store.require(run_id)
            if current.state == RunState.RUNNING:
                # A run stopped mid-flight may already have written files. Freeze
                # them before the terminal transition, or its record would read
                # "unknown" for work that is sitting right there on disk. The
                # process is already stopped, so nothing else is writing.
                delivered = await asyncio.to_thread(
                    snapshot_changes,
                    Path(current.workspace).expanduser().resolve(),
                    self._baselines.get(run_id),
                )
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
            if current.state in {RunState.QUEUED, RunState.RUNNING}:
                return self.store.finish(
                    run_id,
                    RunState.FAILED,
                    expected_version=current.version,
                    exit_code=None,
                    stdout_tail=current.stdout_tail,
                    stderr_tail=current.stderr_tail,
                    error_message=redact_text(f"dispatcher error: {exc}"),
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
            if not workspace.is_dir():
                return self.store.finish(
                    run_id, RunState.FAILED, expected_version=run.version, exit_code=None,
                    stdout_tail="", stderr_tail="", error_message="workspace not found",
                )

            try:
                running = self.store.start(run_id, expected_version=run.version)
            except RunConflictError:
                return self.store.require(run_id)

            # Resolve again immediately before launch. A local writer can still swap a
            # component after this check on platforms without directory-handle cwd APIs.
            workspace = workspace.resolve()
            if not self._workspace_is_allowed(workspace, project.root) or not workspace.is_dir():
                return self.store.finish(
                    run_id, RunState.FAILED, expected_version=running.version, exit_code=None,
                    stdout_tail="", stderr_tail="", error_message="workspace changed before process start",
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
                return await self._execute_codex_app_server(
                    running, adapter, workspace, role, reviews_run_id, baseline,
                )
            # CLI adapters only: resolve the executable via PATHEXT and unwrap
            # Windows .cmd/.bat shims (npm's codex.cmd) into a CreateProcess-
            # launchable form. Only the command that will actually run is resolved.
            try:
                command = resolve_runtime_command(list(adapter.build_command(run.prompt)))
            except RuntimeLaunchError as exc:
                return self.store.finish(
                    run_id, RunState.FAILED, expected_version=running.version, exit_code=None,
                    stdout_tail="", stderr_tail="", error_message=redact_text(f"process start failed: {exc}"),
                )
            try:
                proc = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=str(workspace),
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
                return self.store.finish(
                    run_id, RunState.FAILED, expected_version=running.version, exit_code=None,
                    stdout_tail="", stderr_tail="",
                    error_message=f"process start failed: {type(exc).__name__}",
                )

            self._active[run_id] = proc
            try:
                try:
                    running = self.store.attach_pid(
                        run_id, expected_version=running.version, pid=proc.pid
                    )
                except RunConflictError:
                    await self._stop(proc)
                    return self.store.require(run_id)

                timed_out = False
                stdout_reader = asyncio.create_task(self._read_tail(proc.stdout))
                stderr_reader = asyncio.create_task(self._read_tail(proc.stderr))
                try:
                    await asyncio.wait_for(proc.wait(), timeout=running.timeout_seconds)
                except (TimeoutError, asyncio.TimeoutError):
                    timed_out = True
                    await self._stop(proc)
                except asyncio.CancelledError:
                    await self._stop(proc)
                    await asyncio.gather(stdout_reader, stderr_reader, return_exceptions=True)
                    raise

                stdout, stderr = await asyncio.gather(stdout_reader, stderr_reader)

                semantic, usage_metadata = self._observe_usage(
                    run.adapter, stdout.decode("utf-8", errors="replace"), run_id, run.prompt
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
            finally:
                self._active.pop(run_id, None)

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
            self._active[running.run_id] = proc
            try:
                attached = self.store.attach_pid(
                    running.run_id, expected_version=attached.version, pid=proc.pid,
                )
            except RunConflictError:
                await self._stop(proc)
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
            await self._stop(proc)
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
        self, adapter: str, raw_stdout: str, run_id: str, prompt: str,
    ) -> tuple[str, dict[str, Any]]:
        """Return clean semantic text and token usage; parsing never breaks a run."""
        result_format = self._USAGE_RESULT_FORMATS.get(adapter)
        if result_format is None:
            return raw_stdout, {}
        try:
            semantic, observation = normalize_native_output(
                adapter=adapter,
                result_format=result_format,
                stdout=raw_stdout,
                run_id=run_id,
                prompt=prompt,
            )
        except Exception:
            # A malformed or oversized payload must not fail the run; fall back
            # to the raw output with no usage rather than raising.
            return raw_stdout, {}
        return semantic, self._usage_metadata(observation)

    @staticmethod
    def _usage_metadata(observation: Any) -> dict[str, Any]:
        if observation is None or observation.token_measurement == "unavailable":
            return {}
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
                if self.auto_review:
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
        try:
            # The prompt build shells out to git (readiness + list_changes + reads);
            # keep it off the event loop.
            prompt = await asyncio.to_thread(self._review_prompt, worker_run)
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
        files = list_changes(workspace) if ready else []
        if not files:
            parts.append("\n## Changes\n(No changed files detected in the workspace.)")
        for entry in files:
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
    async def _stop(proc: asyncio.subprocess.Process) -> None:
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

    @staticmethod
    async def _read_tail(stream: asyncio.StreamReader | None) -> bytes:
        if stream is None:
            return b""
        tail = bytearray()
        while True:
            chunk = await stream.read(8192)
            if not chunk:
                break
            tail.extend(chunk)
            if len(tail) > OUTPUT_TAIL_LIMIT * 2:
                del tail[:-OUTPUT_TAIL_LIMIT]
        return bytes(tail[-OUTPUT_TAIL_LIMIT:])


def redact_output(value: str) -> str:
    """Compatibility wrapper for callers that imported the original helper."""
    return redact_text(value)
