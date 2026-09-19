"""SQLite persistence and lifecycle invariants for execution runs."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from agora.attention.models import AttentionKind, AttentionState, REVIEW_REQUESTER
from agora.attention.run_scope import settle_bridge_attention_for_run
from agora.tasks.localday import local_day_bounds, local_day_of
from agora.tasks.models import TaskState, utc_now
from agora.tasks.store import TaskStore

from .models import (
    ACTIVE_RUN_STATES,
    CancelRunRequest,
    CreateRunRequest,
    ExecutionRun,
    IDENTITY_METADATA_KEYS,
    REVIEWS_RUN_KEY,
    ROLE_CHAT,
    ROLE_KEY,
    remote_target_public,
    ROLE_REVIEW,
    ROLE_WORKER,
    RunState,
    RunSummary,
    TASK_VERSION_KEY,
    TERMINAL_RUN_STATES,
    OUTPUT_TAIL_LIMIT,
    identity_metadata,
    role_of,
    task_version_of,
)
from .schema import initialize_execution_schema
from .security import redact_text, sanitize_data


class RunNotFoundError(LookupError):
    pass


class RunConflictError(RuntimeError):
    pass


class RunGateError(RunConflictError):
    """A worker run refused because the Run -> Gate order is not satisfied yet.

    A subclass of ``RunConflictError`` so every existing caller keeps mapping it
    to 409, and its own type so the API can *label* the refusal: "wait for the
    run that is already going" and "settle the gate that is waiting for you" call
    for different words and a different action from the human, and the panel
    cannot tell them apart by sniffing prose.

    ``code`` is the machine-readable discriminator carried in the 409 body;
    ``items`` names exactly what has to be settled, so the message can say how to
    clear the refusal instead of only that there is one.
    """

    def __init__(self, code: str, message: str, *, items: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.code = code
        self.items = items or []


class RunValidationError(ValueError):
    pass


# 409 discriminators for RunGateError. Mirrored by
# frontend/src/lib/continue-run.ts (RUN_GATE_CODES); the contract test
# ``test_run_gate_conflict_codes_mirrored_by_the_frontend`` is the alarm when
# these change.
GATE_CODE_ATTENTION_OPEN = "attention_gate_open"
GATE_CODE_REVIEW_ACTIVE = "review_run_active"
GATE_CODE_REVIEW_OWED = "review_owed"

# Why a worker run is owed a cross-review. Carried in each derived record so the
# startup reconciliation's blocker can say which of the arrangements it found
# instead of asserting the most common one. They are different facts about a
# database and a human reads the difference: nothing ever reviewed this run, versus
# a review ran and left nothing for you to decide, versus a review was created and
# then cancelled before it produced anything.
REVIEW_OWED_NO_REVIEW_RUN = "no_review_run"
REVIEW_OWED_NO_GATE_ITEM = "no_gate_item"
REVIEW_OWED_REVIEW_CANCELLED = "review_cancelled"

# Attention kinds that gate a new worker run. All three of them do.
#
# Every guard added to this slice has produced a lockout, so each kind states
# three things: what RAISES the gate, what RELEASES it, and what happens when the
# release condition CANNOT BE READ. A kind whose release condition cannot be named
# does not belong in this tuple.
#
# - ``approval``: it *is* the gate. AGENTS.md binds an approval to repo, ref,
#   commit, stage, artifact path and hash; building the next run on top of work
#   whose approval is still open makes that binding meaningless, and is the
#   Run -> Artifact/Evidence -> Gate -> Handoff line running backwards.
#   Raised by: ExecutionDispatcher._raise_review_attention on a successful review
#   run, and by a vendor permission prompt (bridges/codex_protocol.decode_approval,
#   bridges/normalize for a hook ``permissionrequest``).
#   Released by: a human responding (approve/reject) or cancelling it; its
#   ``expires_at`` passing; or — for a bridge-raised prompt, which dies with the
#   process that asked — the run settling, via
#   agora.attention.run_scope.settle_bridge_attention_for_run in the same
#   transaction that settles the run.
# - ``blocker``: the task is stuck on something only a human can clear. Another
#   worker run does not clear it — it spends tokens on the same wall — and the
#   auto-review failure path raises exactly this kind, so it is the "the review
#   did not pass" / "the review never started" signal that must not be built on.
#   Raised by: ExecutionDispatcher._block_review_owed and _raise_review_attention.
#   Released by: a human answering or cancelling it (the item's own body says so);
#   expiry; and, for a bridge-origin blocker, its run settling.
# - ``question``: an agent asked the human something. It gates because a new run
#   created while the question is unanswered proceeds on an *unanswered
#   assumption* — which makes asking pointless. This is a reversal of the earlier
#   rule here, which argued that a question binds to no artifact so it may be
#   answered later; the answer to "later" turned out to be "by a run that already
#   guessed".
#   Raised by: bridges/normalize.normalize_hook_event, for a non-permission hook
#   event delivered during a run — the only *runtime* producer
#   (agora.tasks.clarify raises no Attention at all) — and the generic
#   ``POST /api/attention``, which takes ``kind`` and ``requester`` from the
#   client, so an operator or script can create one too. (Any requester except the
#   control plane's own: ``RESERVED_REQUESTERS`` is refused in public input, because
#   the gates read those back as Agora's own authority — see
#   ``_has_review_gate_item``.)
#   Released by: a human answering or cancelling it; expiry; and, for a
#   bridge-origin question ONLY, the run that asked settling —
#   ``settle_bridge_attention_for_run`` joins ``attention_bridge_events``, and an
#   item created through ``AttentionStore.create`` has no row there, so nothing
#   settles it with any run.
#   What makes gating this kind safe is therefore NOT that every question settles
#   with its run. It is that every question is **always human-clearable**: a
#   bridge-origin one is answerable while its run lives and is cancelled with it
#   when it dies, and a generically created one has no run to depend on at all —
#   it is answerable or cancellable through the Attention page for as long as it
#   exists. The stale-lockout path of section 1a of
#   docs/architecture/run-gate-ordering-v1.md is the item that gates and can no
#   longer be *acted on*; neither shape of ``question`` is that. (This comment
#   claimed the absolute — "every question is bridge-origin, so every question
#   auto-settles" — until 2026-08-30. It was false, and the false version is the
#   dangerous kind: the next person relaxing something here would have relied on
#   it. If a new caller raises a ``question`` that is genuinely run-scoped and
#   unanswerable, it *does* reintroduce the section 1a lockout, and nothing here
#   detects that — the residual already recorded for ``approval``.)
#
# When the release condition cannot be read: a database with no
# ``attention_items`` table holds no items at all, which is a fact about the schema
# and not an unreadable state (see _open_gate_items). An item past its
# ``expires_at`` counts as released whether or not ``AttentionStore.expire_overdue``
# has swept it, so the refusal never depends on when that sweep last ran. Nothing
# else about a gate item is derived from outside this database.
GATING_ATTENTION_KINDS: tuple[str, ...] = (
    AttentionKind.APPROVAL.value,
    AttentionKind.BLOCKER.value,
    AttentionKind.QUESTION.value,
)

# How many gate items the refusal names. The rest are counted, not listed: the
# message has to be readable, and one named item is enough to act on.
GATE_ITEM_REPORT_LIMIT = 3
_GATE_TITLE_CHARS = 120


_ALLOWED: dict[RunState, set[RunState]] = {
    RunState.QUEUED: {RunState.RUNNING, RunState.FAILED, RunState.CANCELLED},
    RunState.RUNNING: {
        RunState.SUCCEEDED,
        RunState.FAILED,
        RunState.TIMED_OUT,
        RunState.CANCELLED,
        RunState.ABANDONED,
    },
    RunState.SUCCEEDED: set(),
    RunState.FAILED: set(),
    RunState.TIMED_OUT: set(),
    RunState.CANCELLED: set(),
    RunState.ABANDONED: set(),
}


def _reviews_link(raw: str | None) -> str | None:
    """The reviewed-run id a stored run names, or None.

    One reader for both the summary and the full record, so the two cannot
    disagree about the same row — which is exactly what happened when only the
    summary populated it.
    """
    metadata = json.loads(raw) if raw else {}
    value = metadata.get(REVIEWS_RUN_KEY)
    return value if isinstance(value, str) and value else None


class ExecutionStore:
    def __init__(self, task_store: TaskStore):
        self.tasks = task_store
        self.db_path = Path(task_store.db_path)
        with closing(self._connect()) as db:
            initialize_execution_schema(db)
            db.commit()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA journal_mode = WAL")
        return db

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def create(
        self,
        request: CreateRunRequest,
        *,
        project_id: str,
        workspace: Path,
        stored_command: list[str],
        role: str = ROLE_WORKER,
        reviews_run_id: str | None = None,
        auto_review: bool = False,
    ) -> ExecutionRun:
        """Record a queued run.

        ``role`` is a server-side argument, never a field of ``request``: it
        decides which unfinished run blocks this one, so a client able to set it
        could name a role nothing matches and open a second concurrent run on the
        same worktree. ``CreateRunRequest`` refuses the reserved keys outright, and
        only the dispatcher's own review dispatch passes ``ROLE_REVIEW``.

        ``auto_review`` is dispatcher-level configuration this store cannot read, so
        the caller that knows it passes the *flag* — not the answer. The derived
        "does this task owe a review?" question is then asked inside this
        transaction (``_reject_review_owed``): a caller that evaluated it beforehand
        and handed over a verdict would reintroduce exactly the TOCTOU window the
        check exists to close. It defaults to False because the flag itself defaults
        off in configuration and a caller that never dispatches reviews can never
        owe one; ``ExecutionDispatcher.queue`` is the only production caller and
        always passes its own value.
        """
        if role not in {ROLE_WORKER, ROLE_REVIEW, ROLE_CHAT}:
            raise RunValidationError(f"Unknown run role: {role}")
        now = utc_now()
        run_id = f"run_{uuid.uuid4().hex}"
        # The role is recorded explicitly rather than left to role_of's default, so
        # the stored run says what it is. reviews_run_id is only meaningful for a
        # review run and is omitted otherwise.
        metadata: dict[str, Any] = {**request.metadata, ROLE_KEY: role}
        if reviews_run_id is not None:
            metadata[REVIEWS_RUN_KEY] = reviews_run_id
        with self._transaction() as db:
            task = db.execute("SELECT * FROM tasks WHERE task_id = ?", (request.task_id,)).fetchone()
            if task is None:
                raise RunNotFoundError(f"Task not found: {request.task_id}")
            if task["project_id"] != project_id:
                raise RunValidationError("Task project does not match resolved project")
            current_version = int(task["version"])
            if current_version != request.expected_task_version:
                raise RunConflictError(
                    f"Expected task version {request.expected_task_version}, current version is {current_version}"
                )
            state = TaskState(task["state"])
            if state not in {TaskState.PLANNED, TaskState.RUNNING}:
                raise RunConflictError("Execution runs require a planned or running task")
            self._reject_second_active_run(db, task_id=request.task_id, role=role)
            if role == ROLE_WORKER:
                # Run -> Gate ordering, enforced in the transaction that creates the
                # run rather than by the client (AGENTS.md: the mainline is
                # Run -> Artifact/Evidence -> Gate -> Handoff, and Agora is the only
                # writer of that order). Only worker runs: a review run *is* how a
                # gate gets something to decide, so gating it would deadlock the
                # cycle it belongs to.
                self._reject_active_review(db, task_id=request.task_id)
                self._reject_open_gate(db, task_id=request.task_id, now=now)
                if auto_review:
                    # Last of the three, so a gate that already exists is reported
                    # as the gate it is: the review-owed blocker raised by a failed
                    # dispatch is an ``attention_gate_open`` refusal naming the item
                    # and how to clear it, which is more useful than the derived
                    # answer. (The derived predicate stands down for that run
                    # anyway — see _has_review_gate_item — so the two cannot
                    # gate at once and leave no exit.)
                    self._reject_review_owed(db, task_id=request.task_id)
            # The task version this run is validated against, recorded on the row so
            # the question can be re-asked later (see TASK_VERSION_KEY,
            # _task_refusal). Set to the version this transaction is about to write
            # when it bumps planned -> running, not the one it read: that bump is
            # part of creating this run, so it is not a change the run must be
            # refused for.
            validated_task_version = current_version
            # A question (ROLE_CHAT) does not start the work: the task stays where it is,
            # its version untouched, so the worker run that follows still matches (CR-75).
            if role != ROLE_CHAT and state == TaskState.PLANNED:
                next_version = current_version + 1
                validated_task_version = next_version
                cursor = db.execute(
                    "UPDATE tasks SET state = ?, version = ?, updated_at = ? WHERE task_id = ? AND version = ?",
                    (TaskState.RUNNING.value, next_version, now, request.task_id, current_version),
                )
                if cursor.rowcount != 1:
                    raise RunConflictError("Task changed while queueing the run")
                self.tasks._insert_event(
                    db,
                    task_id=request.task_id,
                    event_type="state_changed",
                    actor=request.actor,
                    payload={
                        "from": TaskState.PLANNED.value,
                        "to": TaskState.RUNNING.value,
                        "reason": f"Queued {request.adapter} execution",
                        "version": next_version,
                    },
                    created_at=now,
                )
            db.execute(
                """
                INSERT INTO execution_runs (
                    run_id, task_id, project_id, adapter, state, prompt, workspace,
                    command, timeout_seconds, result_metadata, version, queued_at, actor
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    run_id,
                    request.task_id,
                    project_id,
                    request.adapter,
                    RunState.QUEUED.value,
                    request.prompt,
                    str(workspace),
                    self._json(sanitize_data(stored_command)),
                    request.timeout_seconds,
                    self._json(
                        {
                            **sanitize_data(metadata),
                            # After sanitize_data, not through it: the value is an
                            # integer this transaction just computed, and a key
                            # named by the control plane must not be reshaped by a
                            # redactor that only inspects client input.
                            TASK_VERSION_KEY: validated_task_version,
                        }
                    ),
                    now,
                    request.actor,
                ),
            )
            self._event(
                db,
                request.task_id,
                "run.queued",
                request.actor,
                {"run_id": run_id, "adapter": request.adapter},
                now,
            )
        return self.require(run_id)

    @staticmethod
    def _reject_second_active_run(db: sqlite3.Connection, *, task_id: str, role: str) -> None:
        """Refuse a second unsettled run of the same role on one task.

        Two clients can both see "no run is active" and POST with the same
        ``expected_task_version``: the version check passes for both, because a
        task that is already RUNNING is not bumped by queueing a run. Only a check
        inside this transaction can close that, so the refusal lives here rather
        than in a caller or in the UI.

        Same *role*, not same task: cross-review is the ordinary case of two runs
        on one task, and it must keep working. It never overlaps a worker run
        anyway — the review is dispatched after the worker run has settled — so
        scoping the rule to the role blocks the duplicate dispatch without
        touching the review path. An untagged run counts as ``worker``.

        ``role`` comes from ``create``'s server-side argument, not from client
        metadata: a client-settable role would name something no run matches and
        walk straight past this check.

        The dispatcher's per-worktree semaphore still stands; it serializes what
        was already created, which is a different guarantee from not creating it.
        """
        states = sorted(state.value for state in ACTIVE_RUN_STATES)
        row = db.execute(
            f"""
            SELECT run_id FROM execution_runs
            WHERE task_id = ?
              AND state IN ({",".join("?" for _ in states)})
              AND COALESCE(json_extract(result_metadata, ?), ?) = ?
            LIMIT 1
            """,
            (task_id, *states, f"$.{ROLE_KEY}", ROLE_WORKER, role),
        ).fetchone()
        if row is not None:
            raise RunConflictError(
                f"Task already has an unfinished {role} run ({row['run_id']}); "
                "wait for it to finish or cancel it"
            )

    @staticmethod
    def _reject_active_review(db: sqlite3.Connection, *, task_id: str) -> None:
        """Refuse a worker run while this task's cross-review has not settled.

        The review run is the evidence the human gate decides on. A worker run
        created while it is still queued or running builds the next change on top
        of work whose review is not in yet, and — because a review run does not
        block a *worker* run under the same-role rule — nothing else refuses it.

        This is the half of Run -> Gate ordering that is enforceable by a row that
        already exists: the review run is in this database, readable in this
        transaction. The *other* half — the gap between a worker run settling and
        its review being queued, where no such row exists yet — is closed by
        ``_reject_review_owed``, which derives the same fact from the worker run
        instead of reading a review row.

        Reversible by design: the message names the review run, and cancelling it
        clears the refusal. A review run cannot outlive the process silently —
        ``recover_abandoned`` settles a RUNNING one on restart and the dispatcher
        settles a QUEUED one on shutdown — so this cannot become a permanent lock.
        """
        states = sorted(state.value for state in ACTIVE_RUN_STATES)
        row = db.execute(
            f"""
            SELECT run_id, state FROM execution_runs
            WHERE task_id = ?
              AND state IN ({",".join("?" for _ in states)})
              AND COALESCE(json_extract(result_metadata, ?), ?) = ?
            ORDER BY queued_at, rowid
            LIMIT 1
            """,
            (task_id, *states, f"$.{ROLE_KEY}", ROLE_WORKER, ROLE_REVIEW),
        ).fetchone()
        if row is None:
            return
        raise RunGateError(
            GATE_CODE_REVIEW_ACTIVE,
            f"Task is under review: review run {row['run_id']} is {row['state']}. "
            "Wait for the review to finish, or cancel that run, before starting "
            "another worker run.",
            items=[{"run_id": row["run_id"], "state": row["state"]}],
        )

    @staticmethod
    def _has_accounting_review_run(db: sqlite3.Connection, *, run_id: str) -> bool:
        """Whether a review run that *accounts for* reviewing ``run_id`` exists.

        Existence of the row is not enough, and this is the correction to the
        earlier rule (``_has_review_run``, "any state, deliberately"). That rule
        rested on each state accounting for itself: unsettled is held by
        ``_reject_active_review``, and a settled one raises the approval or blocker
        its outcome produces. The second half is only true of a review run that
        reached ``ExecutionDispatcher._after_run``. A review run that ends **before
        it starts** — adapter no longer configured, workspace gone, the launch
        refused — is settled by an early return in ``execute`` that never calls
        ``_after_run``, so nothing is raised, nothing is recorded, and the row alone
        discharged the obligation. The worker run was then unreviewed with no gate
        anywhere.

        So the states that account for the review are:

        - ``queued``/``running``: ``_reject_active_review`` holds the task.

        **``cancelled`` is not one of them**, and that is N1 (2026-08-30) reversing a
        residual this docstring recorded as accepted twice. The old rule read a
        cancelled review as "a human's decision not to review, which the control plane
        does not override", and dismissed telling a human's cancel from a sweep's as
        "parsing a recorded reason string". Both halves were wrong. The premise assumed
        a human was involved: ``ExecutionDispatcher.shutdown`` cancels every queued run
        through ``_settle_unstarted``, so an ordinary restart between a worker run
        settling and its review starting cancelled the review automatically, and this
        method then reported the obligation discharged with no approval and no blocker
        anywhere. And the rule never needed a reason string, because the exit does not
        depend on *who* cancelled: what a cancelled review has in common with a failed
        one is that **it produced no gate item**, and that is a fact this database
        records directly.

        So a cancelled review accounts for nothing here either, and the gate item is
        again what releases the obligation (``_has_review_gate_item``). A human
        cancelling a review run is still a decision the control plane does not
        override — it is honoured by *recording* it: every path that cancels a review
        run raises the review-owed blocker for the worker run it was going to review
        (``ExecutionDispatcher._block_reviews_owed_for``), so the human has the item
        and the two exits that exist — clear it and accept the work with no review
        decision on record, or clear it and dispatch another *worker* run, whose own
        automatic cross-review follows — instead of the obligation vanishing silently.
        No endpoint dispatches a review run on its own, so "dispatch another review" is
        not one of them (O3/P3).

        **``succeeded`` is not one of them either**, and that is L2 (2026-08-30). A
        review run that succeeded is *evidence that a review happened*; the thing that
        accounts for the obligation is the **gate** its outcome raises. Those are
        different facts, and the Run -> Artifact/Evidence -> Gate line only means
        something while they stay different: when
        ``_raise_review_attention`` failed to write the approval *and*
        ``_record_review_failure``'s fallback blocker write failed too, this method
        concluded the review was satisfied from the run's state alone, with no
        approval and no blocker anywhere. Nothing was pending for the human, the
        startup reconciliation saw nothing owed, and the next worker run built on
        work whose review nobody had ever been asked to accept. A review run that
        produced no gate item is worse than no review at all, because it *looks*
        finished — so it counts for nothing here, and the gate item is what releases
        the obligation (``_has_review_gate_item``).

        ``failed``/``timed_out``/``abandoned`` do not account for it either, for the
        same reason under a different cause: they reviewed nothing. For all of those,
        for ``cancelled`` and for ``succeeded``, what discharges the obligation is the
        item ``_raise_review_attention``/``_block_review_owed`` records against the
        worker run — checked separately by ``_has_review_gate_item``, so the two never
        gate at once, and a review whose gate item is missing everywhere is owed again
        rather than silently forgiven.
        """
        accounted = sorted(state.value for state in ACTIVE_RUN_STATES)
        return db.execute(
            f"""SELECT 1 FROM execution_runs
               WHERE json_extract(result_metadata, ?) = ?
                 AND state IN ({",".join("?" for _ in accounted)}) LIMIT 1""",
            (f"$.{REVIEWS_RUN_KEY}", run_id, *accounted),
        ).fetchone() is not None

    #: The Attention kinds the review path raises against a worker run, either of
    #: which *is* the human gate for that run's review: ``approval`` when the review
    #: ran and produced a verdict to accept, ``blocker`` when it did not happen or
    #: did not finish. Both are in ``GATING_ATTENTION_KINDS``, so either one also
    #: refuses the next worker run by itself while it is open.
    REVIEW_GATE_KINDS: tuple[str, ...] = (
        AttentionKind.APPROVAL.value, AttentionKind.BLOCKER.value,
    )

    @classmethod
    def _has_review_gate_item(
        cls, db: sqlite3.Connection, *, task_id: str, run_id: str
    ) -> bool:
        """Whether the review path already raised its human gate for this worker run.

        **This is the release valve on the derived refusal below, and the reason it
        cannot become a one-way door.** Every review outcome ends in one of two items
        scoped to the reviewed worker run: the ``approval`` a successful review run
        raises (``_raise_review_attention``), or the ``blocker`` every path that
        produced no review records (``_block_review_owed``, and
        ``_raise_review_attention`` for a review run that failed). Both gate by
        themselves — they are in ``GATING_ATTENTION_KINDS`` — and each states the exits
        that **exist**: for the approval, approve or reject the review; for the
        blocker, clear the item and accept the work with no review decision on record,
        or clear it and start another worker run whose own automatic cross-review
        follows. There is no
        endpoint that dispatches a review run on its own (``ROLE_REVIEW`` is
        server-only and ``_dispatch_review`` is its only production caller), so
        "dispatch the review again" is not among them — see O3 in
        docs/architecture/run-gate-ordering-v1.md. If the
        derived refusal *also* fired, settling that item would clear one gate and
        leave the other standing forever, with nothing left to act on. So a run
        whose review gate has been raised is not owed a review here, whatever state
        that item is in — ``cancelled`` and ``responded`` included, because the
        human's disposition of it is the decision.

        Matched on ``(task_id, run_id, requester, kind)``, not on the item's title:
        titles are redacted on insert, so comparing against a redacted copy of
        ``REVIEW_OWED_BLOCKER_TITLE`` would be a second thing to keep in step.

        ``requester = REVIEW_REQUESTER`` is what makes this a reading of *Agora's
        own* gate rather than of any item that happens to be shaped like one, and
        since L1 (2026-08-30) that requester is refused in public input and writable
        only by the internal paths (``RESERVED_REQUESTERS``,
        ``AttentionStore._reject_reserved_requester``). Before that fix a client
        could POST this exact row for a worker run, cancel it, and release the
        review obligation for a review that was never created — an authority marker
        the API let the caller forge. The fix is at that boundary, deliberately, and
        not here: a gate that has to guess which of its own markers are genuine has
        no marker at all.

        A database with no ``attention_items`` table has no items at all, so the
        answer is False: the same schema fact ``_open_gate_items`` relies on, and it
        fails *closed* here (no recorded gate item means the derived refusal still
        stands).
        """
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'attention_items'"
        ).fetchone() is None:
            return False
        return db.execute(
            f"""SELECT 1 FROM attention_items
               WHERE task_id = ? AND run_id = ? AND requester = ?
                 AND kind IN ({",".join("?" for _ in cls.REVIEW_GATE_KINDS)}) LIMIT 1""",
            (task_id, run_id, REVIEW_REQUESTER, *cls.REVIEW_GATE_KINDS),
        ).fetchone() is not None

    @classmethod
    def _reviews_owed(
        cls, db: sqlite3.Connection, *, task_id: str | None = None,
    ) -> list[dict[str, str]]:
        """Worker runs that owe an independent cross-review, derived and not stored.

        At most one ``{"task_id", "run_id", "cause"}`` per *task*, for one task or for
        every task. ``cause`` is one of ``REVIEW_OWED_NO_REVIEW_RUN`` (no review run
        is linked to this worker run at all), ``REVIEW_OWED_REVIEW_CANCELLED`` (one
        is, it was cancelled, and it left no gate item) or
        ``REVIEW_OWED_NO_GATE_ITEM`` (one is, it settled some other way, and it left
        no gate item behind) — see ``_review_owed_cause``, which is the only thing
        that decides between them.

        **Why derived.** The window this closes is between a worker run settling and
        ``ExecutionDispatcher._dispatch_review`` inserting the review row: in it no
        run is active, no gate exists, and both other create-time checks pass, so a
        worker run was accepted on unreviewed work through the API. A durable
        "review owed" marker written by the settle transaction would close it too,
        and was rejected: it leaks on a crash and needs its own reconciliation. A
        derived fact cannot leak, because nothing is written.

        **What raises it.** The task's most recent *terminal worker* run — the single
        run this method nominates, see the two chronology paragraphs at the end — is
        ``succeeded``, and no review run accounts for reviewing it
        (``_has_accounting_review_run``), and the review path has raised no gate item
        for it (``_has_review_gate_item``), and the task is still ``planned`` or
        ``running``.

        **What releases it**, in the order the paths actually occur:

        - the review run is inserted — the predicate goes false on its own, with no
          write and no sweep;
        - the review settled and its gate item exists — the approval a successful
          review raises, or the blocker every failed path records; that item gates
          instead, and the human clears it by responding or cancelling;
        - a later worker run settles in any non-``succeeded`` state — only the one
          nominated terminal worker run is examined, and when the chronology is
          readable the newest terminal row is the nominee whatever state it is in.
          When it is *not* readable a non-succeeded row cannot displace a succeeded
          one (that displacement is N3), so this release does not apply there;
        - the task leaves ``planned``/``running`` — a task that cannot accept a run
          cannot be owed a review either;
        - ``auto_review`` is off — the caller does not ask this question at all (see
          ``create``).

        A review run that *finished* is not itself a release, whatever its outcome,
        and since N1 that includes one that was **cancelled**. One that ended
        ``failed``, ``timed_out`` or ``abandoned`` reviewed nothing. One that was
        cancelled — by a human, or automatically by the shutdown sweep before it ever
        started — reviewed nothing either, and used to release this predicate on the
        theory that a human had decided; a restart in the dispatch window is enough to
        reach it with nobody deciding anything. One that ``succeeded`` is evidence a
        review happened, which is not the same fact as the human gate being open, and
        when both writes of that gate failed the run's state was the only thing left
        saying "reviewed" — see ``_has_accounting_review_run`` for every path that used
        to end here with no gate at all.

        **The release conditions that cannot resolve themselves** are the ones where
        no later event will ever write anything: the process dying between the settle
        and the dispatch (no review run, no blocker), and a review run that settled
        while every attempt to record its gate item failed (a review run, still no
        gate). In both, the predicate stays true forever, which is why
        ``ExecutionDispatcher.reconcile_reviews_owed`` calls this at startup and
        converts each answer into the blocker that says what happened. ``cause``
        distinguishes them (``REVIEW_OWED_NO_REVIEW_RUN`` /
        ``REVIEW_OWED_NO_GATE_ITEM``) so that blocker states the arrangement it
        actually found. A refusal carrying ``GATE_CODE_REVIEW_OWED`` therefore means
        the live, seconds-long window: the API can only refuse while the process is
        up, and every start of that process reconciles first.

        **Only tasks that could still accept a run.** ``create`` refuses *any* run —
        worker or review — for a task outside ``planned``/``running``, so a review
        could not have been dispatched for a task that has left them, and claiming
        one is owed there would be claiming a review that would never have been
        dispatched. It also keeps the startup reconciliation from writing blockers
        onto tasks that are already done, which would be inbox noise a human has to
        clear for nothing.

        **Only the most recent terminal worker run**, for two reasons. It matches
        ``_dispatch_review``'s trigger — ``_after_run`` dispatches for the run that
        just settled, so an older unreviewed run is not one a review "would have
        been dispatched" for. And it bounds the blast radius of turning
        ``auto_review`` on over an existing database to one item per task rather
        than one per historical run.

        **"Most recent" needs a chronology, and when there is none this fails closed**
        (N3, 2026-08-30). Runs are ranked by ``finished_at`` then ``rowid``. A terminal
        row whose ``finished_at`` is missing or empty has **no rank at all**: it used to
        be coalesced to ``''``, which is not "unknown" but "older than every real
        timestamp", so an older *failed* worker run with a real timestamp outranked a
        newer *succeeded* one whose timestamp was missing, became the "latest terminal
        worker", and no review was owed — another worker run admitted on unreviewed
        work, from a sort order chosen for convenience. Manufacturing an order out of
        an absent value is what made an unknown into a permission.

        So when any of a task's terminal worker rows lacks a usable ``finished_at``,
        this method does not claim to know which one is latest. It nominates the
        newest run it can still *identify*: the **last ``succeeded`` terminal worker
        row in ``rowid`` order**, and no other. A row in any other state cannot
        displace it — that displacement is the defect above — and no older succeeded
        row is fallen back to, which is the bound O1 corrected (see below).

        **The bound in that branch is one run per task, not one per invocation**
        (O1, 2026-08-30). The first version of the fix made *every* succeeded row a
        candidate and returned the first one that had neither an accounting review run
        nor a gate item, with a ``break`` after it. That bounded a single call and
        nothing else: once the reconciliation had recorded the blocker for the oldest
        such row, the next start skipped it — it now had a gate item — and nominated
        the *next* historical success, so N successes produced N blockers across N
        starts, several open at once, and "one cancel clears it" was false because
        cancelling the first item released the obligation for that run only while the
        next worker attempt was refused for a run that had no item yet. The nominee is
        therefore chosen *before* any release question is asked, and it does not depend
        on the answer: the same row is nominated on every call until a new terminal
        worker row is recorded, so one gate item settles it for good.

        Why ``rowid`` is the tie-breaker used to identify that row, stated as the weak
        claim it is: ``_reject_second_active_run`` refuses a second unsettled worker
        run on a task, so under Agora's own writes worker runs on one task are strictly
        serialized and insertion order *is* their settle order. This branch is only
        reached when a row was written by something that is not Agora, so insertion
        order is treated as the best available identification of "newest", not as proof
        of chronology — which is exactly why the branch nominates one run and refuses
        to walk any further back.

        The cost, unchanged in direction and now bounded in size: a hand-edited
        database can raise one blocker for a run that was in fact reviewed before the
        row was edited, which a human clears with one cancel. That is the direction to
        be wrong in, and one item is the whole of it.
        """
        states = sorted(state.value for state in TERMINAL_RUN_STATES)
        values: list[Any] = [
            TaskState.PLANNED.value, TaskState.RUNNING.value,
            f"$.{ROLE_KEY}", ROLE_WORKER, ROLE_WORKER,
            *states,
        ]
        clause = "" if task_id is None else " AND r.task_id = ?"
        if task_id is not None:
            values.append(task_id)
        # Ordered by rowid only. The chronological ranking is done below, where a
        # missing timestamp can be treated as unknown instead of being coalesced
        # into a value that compares.
        terminal: dict[str, list[sqlite3.Row]] = {}
        for row in db.execute(
            f"""SELECT r.task_id AS task_id, r.run_id AS run_id, r.state AS state,
                       r.finished_at AS finished_at
                FROM execution_runs r
                JOIN tasks t ON t.task_id = r.task_id
                WHERE t.state IN (?, ?)
                  AND COALESCE(json_extract(r.result_metadata, ?), ?) = ?
                  AND r.state IN ({",".join("?" for _ in states)}){clause}
                ORDER BY r.rowid""",
            values,
        ).fetchall():
            terminal.setdefault(str(row["task_id"]), []).append(row)
        owed: list[dict[str, str]] = []
        for owner, rows in terminal.items():
            # The nominee is chosen from the rows alone, before any release question
            # is asked, and exactly one row is ever nominated. That ordering is the
            # bound (O1): a nominee picked by "the first candidate that is still owed"
            # moves on to the next historical run as soon as the previous one gets its
            # gate item, which bounds one invocation and nothing longer.
            if any(not row["finished_at"] for row in rows):
                # No usable chronology for this task: nominate the newest succeeded
                # run that can still be *identified* (last in rowid order) rather
                # than ranking rows that cannot be ranked. A row in another state
                # does not displace it (N3), and no older succeeded row is fallen
                # back to (O1).
                succeeded = [
                    row for row in rows if str(row["state"]) == RunState.SUCCEEDED.value
                ]
                nominee = succeeded[-1] if succeeded else None
            else:
                # Stable sort over the rowid order the query returned, so equal
                # timestamps still resolve by rowid and the last row wins.
                newest = sorted(rows, key=lambda row: str(row["finished_at"]))[-1]
                nominee = (
                    newest if str(newest["state"]) == RunState.SUCCEEDED.value else None
                )
            if nominee is None:
                continue
            run_id = str(nominee["run_id"])
            if cls._has_accounting_review_run(db, run_id=run_id):
                continue
            if cls._has_review_gate_item(db, task_id=owner, run_id=run_id):
                continue
            owed.append({
                "task_id": owner,
                "run_id": run_id,
                # One extra query, and only for a run already known to be owed:
                # the reconciliation's blocker has to name the arrangement it
                # found, and only this transaction can see which one it is.
                "cause": cls._review_owed_cause(db, run_id=run_id),
            })
        return owed

    @staticmethod
    def _review_owed_cause(db: sqlite3.Connection, *, run_id: str) -> str:
        """Which arrangement left this worker run owing a review.

        Only used to label *why* a review is owed, never to decide whether one is:
        "a row exists" is precisely the rule ``_has_accounting_review_run``
        replaced. The label matters because
        ``ExecutionDispatcher.REVIEW_OWED_REASONS`` turns it into the sentence a
        human reads, and a blocker that misdescribes its own cause points at the
        wrong thing to check.

        ``REVIEW_OWED_REVIEW_CANCELLED`` is checked before
        ``REVIEW_OWED_NO_GATE_ITEM`` because it is the more specific reading of the
        same shape: both mean "a review run is linked and left no gate item", but a
        cancelled one was stopped, whereas the no-gate-item sentence says both gate
        writes failed. If several review runs are linked and any of them is
        cancelled, cancelled is the answer — the others left no gate item either, so
        the sentence that names a stop is the true one.
        """
        linked = [
            str(row["state"])
            for row in db.execute(
                "SELECT state FROM execution_runs WHERE json_extract(result_metadata, ?) = ?",
                (f"$.{REVIEWS_RUN_KEY}", run_id),
            ).fetchall()
        ]
        if not linked:
            return REVIEW_OWED_NO_REVIEW_RUN
        if RunState.CANCELLED.value in linked:
            return REVIEW_OWED_REVIEW_CANCELLED
        return REVIEW_OWED_NO_GATE_ITEM

    @classmethod
    def _reject_review_owed(cls, db: sqlite3.Connection, *, task_id: str) -> None:
        """Refuse a worker run while this task's last successful worker run is
        unreviewed and no review run accounts for reviewing it.

        Asked *inside* the create transaction, and the ``auto_review`` answer is
        passed in rather than computed by the caller and handed over as a verdict:
        a caller that decided "no review is owed" before this transaction opened is
        the TOCTOU hole this exists to close.
        """
        rows = cls._reviews_owed(db, task_id=task_id)
        if not rows:
            return
        run_id = rows[0]["run_id"]
        raise RunGateError(
            GATE_CODE_REVIEW_OWED,
            f"Worker run {run_id} succeeded and no independent cross-review accounts "
            "for it yet, so this task owes a review. Either the review run is still "
            "being dispatched — wait a few seconds for it to appear and start this "
            "run again — or the one that was created ended without reviewing "
            "anything, or finished and left no approval for you to decide on. If "
            "Agora stopped inside that window, restarting it records a blocker "
            "naming what happened, and answering or cancelling that blocker releases "
            "this refusal.",
            items=[{"run_id": run_id, "state": RunState.SUCCEEDED.value}],
        )

    def reviews_owed(self, *, task_id: str | None = None) -> list[dict[str, str]]:
        """Every worker run that owes a cross-review — across all tasks, or one.

        The startup read behind ``ExecutionDispatcher.reconcile_reviews_owed``, and
        the same read for one task behind
        ``ExecutionDispatcher._block_reviews_owed_for``, which is how a review run
        cancelled inside a live process gets its blocker written at the time rather
        than at the next start (N1). Its own connection, because the caller then
        writes an Attention item through ``AttentionStore``, which opens its own
        transaction — holding ``BEGIN IMMEDIATE`` here would block on itself.

        Read-then-write is safe for *these callers specifically*, which is not a
        general licence. At startup it runs before the API serves and before
        ``resume_queued`` schedules anything, so no review run can appear between the
        read and the blocker. For the single-task caller the run it is reporting on has
        already been settled CANCELLED in a committed transaction, so the fact it reads
        cannot become false underneath it; a review run appearing in between would only
        mean the blocker is written for a run that is being reviewed again, and
        ``_has_review_gate_item`` makes that item the release rather than a second
        gate. The create-time refusal asks the same question inside its own
        transaction, which is where the race actually lives.
        """
        with closing(self._connect()) as db:
            return self._reviews_owed(db, task_id=task_id)

    @staticmethod
    def _open_gate_items(
        db: sqlite3.Connection, *, task_id: str, now: str, exclude_bridge_run_id: str | None = None,
    ) -> list[sqlite3.Row]:
        """The task's Attention items that gate a worker run right now.

        Only genuinely open items count. ``responded``, ``cancelled`` and
        ``expired`` are gates already dealt with, and an item whose ``expires_at``
        has passed but whose row has not been swept yet is *expired* — deciding
        otherwise would make the refusal depend on when
        ``AttentionStore.expire_overdue`` last ran.

        A database with no ``attention_items`` table has no Attention items at all,
        so there is nothing to gate on. That is a fact about the schema, not an
        unreadable state: ``ExecutionStore`` initializes only the execution schema,
        and a deployment or test that never constructs an ``AttentionStore`` cannot
        have raised an item.

        ``exclude_bridge_run_id`` drops the items that run's **own vendor bridge**
        raised — and nothing else. Used by the two re-validations of an
        already-inserted row (``revalidate_queued`` and
        ``revalidate_before_launch``): a permission prompt a run raised *itself* is
        not prior work that run must wait behind, and at create time a brand-new run
        has raised nothing, so the create path passes nothing here.

        **The cut is by provable origin, not by ``run_id``** — the same distinction
        ``agora.attention.run_scope`` is built on, and it was wrong here until
        2026-08-30 (M2). The exclusion used to be ``run_id <> ?``, which was correct
        only while every item carrying a queued run's id came from that run's bridge.
        Since D1 made ``question`` gate, it does not: ``POST /api/attention`` takes
        ``kind`` and ``run_id`` from the client, so a *generic* question scoped to the
        queued run — a human asking that run's operator something — was excluded by
        the same rule, and the run launched with an unanswered question about it and
        proceeded on a guess. Provable origin is a row in ``attention_bridge_events``
        for this ``run_id``, which exists if and only if the item came from
        ``AttentionStore.create_bridge_event``. Everything else keeps gating.

        ``requester <> REVIEW_REQUESTER`` is checked inside the exclusion for the
        reason ``run_scope`` gives: redundant by rule (``create_bridge_event`` refuses
        the reserved requesters, so a bridge item cannot carry this one) and checked
        anyway, because the cost of excluding a review gate here is admitting a worker
        run onto work a human is still holding.

        **When the origin cannot be read**: a database with no
        ``attention_bridge_events`` table cannot prove any item is bridge-origin, so
        nothing is excluded and every open item gates. That is the fail-closed
        direction — it can cost a queued run that was waiting behind its own prompt,
        never admit one that should have waited.
        """
        tables = {
            str(row["name"])
            for row in db.execute(
                """SELECT name FROM sqlite_master WHERE type = 'table'
                   AND name IN ('attention_items', 'attention_bridge_events')"""
            ).fetchall()
        }
        if "attention_items" not in tables:
            return []
        clause = ""
        values: list[Any] = [task_id, AttentionState.OPEN.value, *GATING_ATTENTION_KINDS, now]
        if exclude_bridge_run_id is not None and "attention_bridge_events" in tables:
            clause = """
              AND NOT (
                a.requester <> ?
                AND EXISTS (
                  SELECT 1 FROM attention_bridge_events b
                  WHERE b.item_id = a.item_id AND b.run_id = ?
                )
              )"""
            values.extend([REVIEW_REQUESTER, exclude_bridge_run_id])
        return db.execute(
            f"""
            SELECT a.item_id AS item_id, a.kind AS kind, a.title AS title
            FROM attention_items a
            WHERE a.task_id = ?
              AND a.state = ?
              AND a.kind IN ({",".join("?" for _ in GATING_ATTENTION_KINDS)})
              AND (a.expires_at IS NULL OR a.expires_at > ?){clause}
            ORDER BY a.created_at, a.rowid
            """,
            values,
        ).fetchall()

    @staticmethod
    def _describe_gate_items(rows: list[sqlite3.Row]) -> tuple[list[dict[str, Any]], str]:
        """``(named, listed)`` for a refusal message: what to report, and its prose.

        The rest are counted, not listed: the message has to be readable, and one
        named item is enough to act on.
        """
        named = [
            {"item_id": row["item_id"], "kind": row["kind"], "title": row["title"]}
            for row in rows[:GATE_ITEM_REPORT_LIMIT]
        ]
        listed = "; ".join(
            f"{item['kind']} {item['item_id']} \"{item['title'][:_GATE_TITLE_CHARS]}\""
            for item in named
        )
        remainder = len(rows) - len(named)
        return named, f"{listed}{f' (and {remainder} more)' if remainder > 0 else ''}"

    @classmethod
    def _reject_open_gate(cls, db: sqlite3.Connection, *, task_id: str, now: str) -> None:
        """Refuse a worker run while a human gate on this task is still open.

        ``expected_task_version`` cannot cover this: raising an Attention item does
        not bump the task's version, so a page holding a current manifest passes
        the optimistic check while a gate waits. Only a read inside this
        transaction closes it.

        Which kinds gate, and why, is recorded on ``GATING_ATTENTION_KINDS``; which
        items count is recorded on ``_open_gate_items``.
        """
        rows = cls._open_gate_items(db, task_id=task_id, now=now)
        if not rows:
            return
        named, listed = cls._describe_gate_items(rows)
        raise RunGateError(
            GATE_CODE_ATTENTION_OPEN,
            f"Task has {len(rows)} open gate item(s) waiting for you: {listed}. "
            "Respond to each one (approve/reject, or answer) or cancel it, then "
            "start the run again.",
            items=named,
        )

    def get(self, run_id: str) -> ExecutionRun | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM execution_runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._run(row) if row else None

    def require(self, run_id: str) -> ExecutionRun:
        run = self.get(run_id)
        if run is None:
            raise RunNotFoundError(run_id)
        return run

    def list(
        self,
        *,
        task_id: str | None = None,
        project_id: str | None = None,
        state: RunState | None = None,
        states: Sequence[RunState] | None = None,
        adapter: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RunSummary]:
        """Runs matching the filters, newest first.

        ``states`` asks for several states in **one** query, which is a different
        fact from two queries: "are any runs of this task unsettled?" answered as
        ``queued`` then ``running`` can miss a run that moves between the two
        reads, and a mirror that can drift is worse than no mirror. ``state`` is
        the single-state form and still works; passing both is refused rather than
        guessed at. An empty ``states`` sequence is no state filter — there is no
        way to ask for "no states" over the wire, so it cannot mean that.
        """
        if state is not None and states:
            raise RunValidationError("Pass either state or states, not both")
        selected = [state] if state is not None else list(states or [])
        clauses: list[str] = []
        values: list[Any] = []
        for column, value in (("task_id", task_id), ("project_id", project_id), ("adapter", adapter)):
            if value:
                clauses.append(f"{column} = ?")
                values.append(value)
        if selected:
            wanted = sorted({item.value for item in selected})
            clauses.append(f"state IN ({','.join('?' for _ in wanted)})")
            values.extend(wanted)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        values.extend([limit, offset])
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT * FROM execution_runs{where} ORDER BY queued_at DESC, rowid DESC LIMIT ? OFFSET ?",
                values,
            ).fetchall()
        return [self._summary(row) for row in rows]

    def token_usage_by_adapter(self, *, project_id: str | None = None) -> dict[str, dict[str, int]]:
        """Sum captured per-run token usage per adapter (0 where none was recorded)."""
        clauses: list[str] = []
        values: list[Any] = []
        if project_id:
            clauses.append("project_id = ?")
            values.append(project_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with closing(self._connect()) as db:
            rows = db.execute(
                f"""
                SELECT adapter,
                       SUM(COALESCE(json_extract(result_metadata, '$.usage.total'), 0)) AS total,
                       SUM(CASE WHEN json_extract(result_metadata, '$.usage.total') IS NOT NULL
                                THEN 1 ELSE 0 END) AS runs_with_usage
                FROM execution_runs{where}
                GROUP BY adapter
                """,
                values,
            ).fetchall()
        return {
            row["adapter"]: {
                "total_tokens": int(row["total"] or 0),
                "runs_with_usage": int(row["runs_with_usage"] or 0),
            }
            for row in rows
        }

    def start(self, run_id: str, *, expected_version: int, pid: int | None = None) -> ExecutionRun:
        return self._transition(
            run_id,
            RunState.RUNNING,
            expected_version=expected_version,
            pid=pid,
            event_payload={"pid": pid},
        )

    def attach_pid(
        self,
        run_id: str,
        *,
        expected_version: int,
        pid: int | None,
        command: list[str] | None = None,
    ) -> ExecutionRun:
        """Record the process behind a RUNNING run.

        ``command`` corrects what ``create`` had to guess. Queue time stores the
        adapter's *start* template because that is the only one that exists yet;
        the dispatcher decides at launch whether this turn starts a session or
        resumes one, and the resume argv is a different shape. Passing it here —
        the one write that already happens at launch, under the same optimistic
        version — keeps the audit record naming the command that actually ran.
        Omitted, the stored command is left alone.
        """
        now = utc_now()
        with self._transaction() as db:
            row = db.execute("SELECT * FROM execution_runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                raise RunNotFoundError(run_id)
            if command is None:
                cursor = db.execute(
                    """
                    UPDATE execution_runs SET pid = ?, version = version + 1
                    WHERE run_id = ? AND state = ? AND version = ?
                    """,
                    (pid, run_id, RunState.RUNNING.value, expected_version),
                )
            else:
                cursor = db.execute(
                    """
                    UPDATE execution_runs SET pid = ?, command = ?, version = version + 1
                    WHERE run_id = ? AND state = ? AND version = ?
                    """,
                    (pid, self._json(sanitize_data(command)), run_id, RunState.RUNNING.value, expected_version),
                )
            if cursor.rowcount != 1:
                raise RunConflictError("Run changed while attaching the process")
            self._event(
                db,
                row["task_id"],
                "run.process_started",
                row["actor"],
                {"run_id": run_id, "adapter": row["adapter"], "pid": pid},
                now,
            )
        return self.require(run_id)

    def finish(
        self,
        run_id: str,
        target: RunState,
        *,
        expected_version: int,
        exit_code: int | None,
        stdout_tail: str,
        stderr_tail: str,
        error_message: str | None = None,
        result_metadata: dict[str, Any] | None = None,
        deliverables: dict[str, Any] | None = None,
    ) -> ExecutionRun:
        """Settle a run. ``deliverables`` is the frozen delivery record captured
        from the worktree as this run ended (see ``deliverables.snapshot_changes``);
        empty ``files`` means "this run changed nothing", while None means no
        snapshot was taken and reads must say so instead of guessing.

        ``result_metadata`` is this run's *result* and nothing else. It may not
        carry an identity key (``IDENTITY_METADATA_KEYS``) — ``_transition``
        refuses one — and an empty or absent one leaves the stored metadata alone
        rather than replacing it with ``{}``. That replacement is what erased a
        pre-launch failure's identity: every early return on this path (adapter
        disabled, workspace gone, launch failure) settles with no result at all,
        and it used to clear ``agora_role`` and ``reviews_run_id`` with it.
        """
        if target not in {RunState.SUCCEEDED, RunState.FAILED, RunState.TIMED_OUT}:
            raise RunValidationError(f"Invalid dispatcher terminal state: {target.value}")
        return self._transition(
            run_id,
            target,
            expected_version=expected_version,
            exit_code=exit_code,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
            error_message=error_message,
            # Nothing to record is None, not {}: an empty dict is a value, and a
            # value gets written. See _transition's result_metadata handling.
            result_metadata=sanitize_data(result_metadata) if result_metadata else None,
            deliverables=deliverables,
            event_payload={"exit_code": exit_code, "error_message": error_message},
        )

    def latest_finished_day(self, task_id: str, *, zone: timezone | None = None) -> str | None:
        """The most recent **local** calendar day a run of this task finished.

        Ordered by ``finished_at``, not ``queued_at`` like ``list``: a long run
        queued earlier can finish later, so a ``queued_at``-ordered page can miss
        the newest completion entirely and point a recap at the wrong day.

        ``zone`` overrides the configured one, so a caller making several day
        derivations resolves the zone once and cannot straddle a config change.
        """
        with closing(self._connect()) as db:
            row = db.execute(
                """
                SELECT finished_at FROM execution_runs
                WHERE task_id = ? AND finished_at IS NOT NULL
                ORDER BY finished_at DESC LIMIT 1
                """,
                (task_id,),
            ).fetchone()
        return local_day_of(row["finished_at"], zone=zone) if row else None

    def list_finished_on_day(
        self, task_id: str, day: str, *, limit: int, zone: timezone | None = None
    ) -> tuple[list[ExecutionRun], bool]:
        """``(runs, truncated)`` for this task's runs that finished on the **local**
        ``day``, oldest first. ``truncated`` is True when more exist than ``limit``,
        so a caller reports "at least these" instead of implying completeness.

        A UTC range, not a string prefix: one local day spans two UTC dates, so
        prefix matching would drop the hours either side of midnight.
        """
        start, end = local_day_bounds(day, zone=zone)
        with closing(self._connect()) as db:
            rows = db.execute(
                """
                SELECT * FROM execution_runs
                WHERE task_id = ? AND finished_at >= ? AND finished_at < ?
                ORDER BY finished_at, rowid LIMIT ?
                """,
                (task_id, start, end, limit + 1),
            ).fetchall()
        truncated = len(rows) > limit
        return [self._run(row) for row in rows[:limit]], truncated

    def deliverable_snapshot(self, run_id: str) -> dict[str, Any] | None:
        """What this run delivered, frozen when it ended, or None if never
        captured: ``{"files": [...], "truncated": bool}``.

        None and an empty ``files`` mean different things: None is "we do not
        know what this run delivered", empty is "this run delivered no file
        changes". Callers must not collapse them. ``truncated`` marks a list that
        hit MAX_FILES, so an absent path is unknown rather than not-delivered.
        """
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT deliverable_snapshot FROM execution_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise RunNotFoundError(run_id)
        raw = row["deliverable_snapshot"]
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(value, dict) or not isinstance(value.get("files"), list):
            return None
        return value

    def cancel(self, run_id: str, request: CancelRunRequest) -> ExecutionRun:
        return self._transition(
            run_id,
            RunState.CANCELLED,
            expected_version=request.expected_version,
            actor=request.actor,
            error_message=request.reason,
            event_payload={"reason": request.reason},
        )

    def record_cancelled_output(
        self,
        run_id: str,
        *,
        expected_version: int,
        stdout_tail: str,
        stderr_tail: str,
        exit_code: int | None,
        deliverables: dict[str, Any] | None = None,
    ) -> ExecutionRun:
        """Record a cancelled run's final output. ``deliverables`` is its frozen
        delivery record: a run cancelled after it already wrote files still
        delivered them, and its record should say so rather than "unknown"."""
        with self._transaction() as db:
            row = db.execute("SELECT * FROM execution_runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                raise RunNotFoundError(run_id)
            snapshot_sql = ", deliverable_snapshot = ?" if deliverables is not None else ""
            snapshot_args = (self._json(deliverables),) if deliverables is not None else ()
            cursor = db.execute(
                f"""
                UPDATE execution_runs
                SET stdout_tail = ?, stderr_tail = ?, exit_code = ?{snapshot_sql},
                    version = version + 1
                WHERE run_id = ? AND state = ? AND version = ?
                """,
                (
                    redact_text(stdout_tail)[-OUTPUT_TAIL_LIMIT:],
                    redact_text(stderr_tail)[-OUTPUT_TAIL_LIMIT:],
                    exit_code,
                    *snapshot_args,
                    run_id, RunState.CANCELLED.value, expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise RunConflictError("Cancelled run changed while recording final output")
            self._event(
                db,
                row["task_id"],
                "run.cancelled_output",
                row["actor"],
                {"run_id": run_id, "adapter": row["adapter"], "exit_code": exit_code},
                utc_now(),
            )
        return self.require(run_id)

    def recover_abandoned(self) -> list[ExecutionRun]:
        recovered: list[ExecutionRun] = []
        for summary in self.list(state=RunState.RUNNING, limit=500):
            try:
                recovered.append(
                    self._transition(
                        summary.run_id,
                        RunState.ABANDONED,
                        expected_version=summary.version,
                        error_message="Agora restarted while the process was running",
                        event_payload={"reason": "process ownership lost after restart"},
                    )
                )
            except RunConflictError:
                continue
        return recovered

    def revalidate_queued(self, *, auto_review: bool = False) -> tuple[list[str], list[dict[str, Any]]]:
        """Re-apply the create-time gates to persisted QUEUED runs before resuming.

        Returns ``(admitted_run_ids, refused)`` — the runs that may still be
        launched, oldest first, and one ``{run_id, task_id, role, reason}`` record
        per run settled instead.

        ``auto_review`` is the dispatcher's flag, passed exactly as ``create`` takes
        it, and it enables the derived "does this task owe a cross-review?" question
        in ``_resume_refusal``. That question used to be omitted here; see the comment
        on it for why an incomplete startup reconciliation made the omission unsafe.

        Why this exists: every gate in ``create`` is checked in the transaction
        that inserts the run, and nothing rechecks a row that was already inserted.
        A QUEUED row therefore outlives the state it was validated against — across
        an upgrade from a database written before these gates existed, and across a
        crash that left the row behind. ``recover_abandoned`` settles RUNNING rows
        and says nothing about QUEUED ones, so without this a worker run persisted
        earlier is rescheduled unconditionally and starts while its predecessor's
        review is queued or running, or while a human gate waits — exactly what the
        create-time checks refuse.

        **Settled here, not at launch.** Two reasons. First, the record: a run
        refused at launch would have to end FAILED, and a FAILED run reads as "the
        agent ran and did not succeed" — a lie about a process that was never
        started. CANCELLED with a reason is what actually happened, and it is the
        only terminal state the store allows from QUEUED. Second, determinism: this
        is one sweep, oldest first, in one transaction, at startup before the API
        serves, so which run survives a conflict is decided once by queue order
        rather than by whichever coroutine the event loop happens to reach first.

        (``revalidate_before_launch`` re-asks the same questions again at launch,
        because their answers can change after this sweep in a running process. It
        settles CANCELLED too, for the same reason.)

        Conflicts are resolved in favour of the run that is already unsettled and
        of the review cycle: a RUNNING run outranks a QUEUED one, an older QUEUED
        run outranks a newer one of the same role, and an unsettled review run
        outranks a queued worker run because the review is the evidence the human
        gate decides on.

        **The review-outranks-worker rule needs the queue walked in role order,
        not purely in queue order.** A queued worker that happens to be older was
        admitted first, and by the time the sweep reached the newer queued review
        ``_resume_refusal`` returned None without consulting the worker slot —
        reviews bypass worker gates by design — so both were admitted and which one
        held authority came down to insertion order. So QUEUED reviews are settled
        first and admitted ones recorded in ``held``; the workers are then judged
        against a review slot that is already decided. Queue order still decides
        *within* a role, because the sort is stable over the SQL ordering.
        """
        now = utc_now()
        admitted: list[str] = []
        refused: list[dict[str, Any]] = []
        role_path = f"$.{ROLE_KEY}"
        with self._transaction() as db:
            # Runs already in flight hold their role's slot outright: nothing in a
            # QUEUED row may displace a process that exists.
            held: dict[tuple[str, str], str] = {}
            for row in db.execute(
                """SELECT run_id, task_id, COALESCE(json_extract(result_metadata, ?), ?) AS role
                   FROM execution_runs WHERE state = ? ORDER BY queued_at, rowid""",
                (role_path, ROLE_WORKER, RunState.RUNNING.value),
            ).fetchall():
                held.setdefault((row["task_id"], row["role"]), row["run_id"])
            queued = db.execute(
                """SELECT run_id, task_id, version, actor, adapter, result_metadata,
                          COALESCE(json_extract(result_metadata, ?), ?) AS role
                   FROM execution_runs WHERE state = ? ORDER BY queued_at, rowid""",
                (role_path, ROLE_WORKER, RunState.QUEUED.value),
            ).fetchall()
            # Reviews first, queue order preserved within each rank (Python's sort
            # is stable, and the SQL above already ordered by queued_at, rowid).
            # An unknown role sorts with the workers; it is refused outright either
            # way, and grouping it with the runs it cannot outrank keeps the rule
            # here to one sentence.
            for row in sorted(queued, key=lambda r: 0 if r["role"] == ROLE_REVIEW else 1):
                task_id, role = row["task_id"], row["role"]
                reason = self._resume_refusal(
                    db, run_id=row["run_id"], task_id=task_id, role=role, held=held, now=now,
                    metadata=row["result_metadata"], auto_review=auto_review,
                )
                if reason is None:
                    held.setdefault((task_id, role), row["run_id"])
                    admitted.append(row["run_id"])
                    continue
                if self._cancel_locked(db, row, reason=reason, now=now):
                    refused.append(
                        {"run_id": row["run_id"], "task_id": task_id, "role": role, "reason": reason}
                    )
        # Oldest first, as the docstring promises. The sweep walked reviews before
        # workers to decide authority; the caller schedules in queue order.
        order = {row["run_id"]: index for index, row in enumerate(queued)}
        admitted.sort(key=lambda run_id: order.get(run_id, 0))
        return admitted, refused

    def revalidate_before_launch(self, run_id: str, *, auto_review: bool = False) -> str | None:
        """Re-ask the create-time questions immediately before a QUEUED run launches.

        Returns the refusal reason — with the run already settled CANCELLED — or
        None if it may launch.

        ``revalidate_queued`` closes the gap across a restart. This closes the gap
        inside a running process, which is a different window: a run can sit QUEUED
        for as long as its worktree and the concurrency limits make it wait, and in
        that time a human can cancel the task, the review gate can carry it to
        ``verified``, an edit can move its version, *or a gate can be raised on work
        that came before it*. ``create`` would refuse the identical request at that
        point; nothing re-asked before this change, so the run launched anyway and
        spent tokens against a task that no longer wanted it.

        **The whole question set, not only the task questions.** This asked only
        ``_task_refusal`` at first, on the theory that re-asking the gate questions
        here would cancel a run waiting behind a bridge permission prompt raised in
        the meantime. It would not: ``_resume_refusal`` excludes the items this run's
        own *bridge* raised (``exclude_bridge_run_id=run_id``), which is exactly the
        transient, run-scoped fact that worry was about, and it keeps every gate
        raised by *prior* work — and, since M2, every generically created item that
        merely names this run. Leaving the gate questions out left a real hole with
        no exclusion needed to close it: a review run settles, a worker run is
        queued in the window before ``_raise_review_attention`` writes the approval,
        the approval then appears while that worker waits for capacity, and at
        launch the lifecycle and version are both still valid — so the worker
        started with an open approval on its task, which is the one thing the
        Run -> Gate order exists to prevent.

        A review run is still exempt from the *worker* gates (that exemption lives
        in ``_resume_refusal``, and without it the review a gate is waiting on could
        never run), and it is still subject to the task questions.

        CANCELLED, not FAILED, for the reason recorded on ``revalidate_queued``: no
        process was started, and FAILED would claim one ran and lost.
        """
        now = utc_now()
        with self._transaction() as db:
            row = db.execute("SELECT * FROM execution_runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                raise RunNotFoundError(run_id)
            if RunState(row["state"]) != RunState.QUEUED:
                # Not ours to judge: a run already started, or already settled, is
                # past the question this method asks.
                return None
            task_id = str(row["task_id"])
            try:
                metadata = json.loads(row["result_metadata"]) if row["result_metadata"] else None
            except (TypeError, ValueError):
                metadata = None
            reason = self._resume_refusal(
                db,
                run_id=run_id,
                task_id=task_id,
                role=role_of(metadata if isinstance(metadata, dict) else None),
                held=self._unsettled_slots(db, task_id=task_id, exclude_run_id=run_id),
                now=now,
                metadata=row["result_metadata"],
                auto_review=auto_review,
            )
            if reason is None:
                return None
            if not self._cancel_locked(
                db, row, reason=reason, now=now, context="Not launched"
            ):
                # It settled underneath us; whoever won owns its own record.
                return None
        return reason

    @staticmethod
    def _unsettled_slots(
        db: sqlite3.Connection, *, task_id: str, exclude_run_id: str
    ) -> dict[tuple[str, str], str]:
        """Which role slots on this task another unsettled run already holds.

        The ``held`` map ``_resume_refusal`` expects, for the launch re-check of one
        run. QUEUED counts as well as RUNNING, because that is what ``create``
        counts (``ACTIVE_RUN_STATES`` in ``_reject_second_active_run`` and
        ``_reject_active_review``) and this re-asks ``create``'s questions. The run
        being judged is excluded: it is itself QUEUED, and a run cannot be the
        reason it may not launch.

        Oldest first, so the run named in a refusal is the one that has been waiting
        longest rather than whichever row SQLite returned first.
        """
        states = sorted(state.value for state in ACTIVE_RUN_STATES)
        held: dict[tuple[str, str], str] = {}
        for row in db.execute(
            f"""SELECT run_id, COALESCE(json_extract(result_metadata, ?), ?) AS role
                FROM execution_runs
                WHERE task_id = ? AND run_id <> ?
                  AND state IN ({",".join("?" for _ in states)})
                ORDER BY queued_at, rowid""",
            (f"$.{ROLE_KEY}", ROLE_WORKER, task_id, exclude_run_id, *states),
        ).fetchall():
            held.setdefault((task_id, str(row["role"])), str(row["run_id"]))
        return held

    @staticmethod
    def _task_refusal(
        db: sqlite3.Connection, *, task_id: str, metadata: str | None, role: str
    ) -> str | None:
        """Why this queued run's *task* no longer permits it, or None if it does.

        The two questions ``create`` asks of the task, re-asked against the state
        that exists now:

        - Is it still planned or running? A run queued while the task was running
          would otherwise still launch after the task was cancelled, failed, or
          carried to ``verified``/``done`` by the review gate.
        - Is it still at the version this run was validated against? That is the
          task's one optimistic-concurrency token, and ``create`` refuses a request
          carrying a stale one. Deliberately the same test rather than a narrower
          "material change" predicate: the version is what the control plane
          actually tracks, and inventing a second notion of materiality here would
          be an untested rule that disagrees with ``create``. It does mean a queued
          run is refused after, say, the task is archived — recoverable by
          dispatching a *worker* run again, and stated in
          docs/architecture/run-gate-ordering-v1.md rather than hidden.

        ``role`` is here only to word the remediation truthfully (O3, 2026-08-30). A
        worker run has an endpoint a human can POST to, so "dispatch it again" is a
        real instruction. A **review** run has none — ``ROLE_REVIEW`` is a server-side
        argument to ``create`` and ``ExecutionDispatcher._dispatch_review`` is its only
        production caller — so telling a human to dispatch one again names an exit
        nobody has. This method's text is recorded on the run and shown on the task
        timeline, so it says instead what the review path actually does: the cancel
        that this refusal produces raises the review-owed blocker for the worker run
        the review was going to look at (``_block_reviews_owed_for``), and that item is
        what a human acts on. This is the same defect as the one N1 named and the fix
        for N1 then re-wrote into a human-facing message; it is corrected in every
        place that offered it.

        A row that does not record a task version (written before
        ``TASK_VERSION_KEY`` existed, or by hand) is **refused**, and that is the
        reversal of this method's original rule (N2, 2026-08-30). It used to skip the
        check it could not answer, on the reasoning that refusing every persisted row
        from an older database would settle runs that are very likely fine. That
        traded a definite harm for a speculative one in the wrong direction: unknown
        authority was being read as *permission*. The reachable path is ordinary — a
        queued run persisted by an older Agora records no version, its task is then
        edited or moved ``running -> blocked -> running`` and its version bumps, and
        nothing can tell which version that run was validated against, so it launched
        an agent against a prompt and a worktree built for state that no longer
        exists. "Very likely fine" is not a property this transaction can read.

        So unknown fails closed, with a reason a human can act on: the run is settled
        CANCELLED naming the missing version. For a worker run, dispatching it again
        re-validates it against the task that exists now and records the version this
        time; for a review run there is nothing to dispatch, and the blocker above is
        the item that carries the decision. (``create`` has written
        ``TASK_VERSION_KEY`` on every run since it existed, so no run this build
        creates can reach this branch.)
        """
        row = db.execute(
            "SELECT state, version FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return "its task no longer exists"
        state = str(row["state"])
        if state not in {TaskState.PLANNED.value, TaskState.RUNNING.value}:
            return f"its task is {state}, and a run requires a planned or running task"
        try:
            recorded = task_version_of(json.loads(metadata) if metadata else None)
        except (TypeError, ValueError):
            recorded = None
        current = int(row["version"])
        if recorded is None:
            remediation = (
                "dispatch it again to re-validate it"
                if role == ROLE_WORKER
                else "no endpoint dispatches a review run, so the item that carries "
                "this decision is the review-owed blocker for the worker run it was "
                "going to review, recorded by this cancel or, if that write fails, by "
                "the next start's reconciliation"
            )
            return (
                "it records no task version, so it cannot be checked against its task's "
                f"current version ({current}); {remediation}"
            )
        if recorded != current:
            return (
                f"its task moved from version {recorded} to {current} after this run "
                "was validated"
            )
        return None

    @classmethod
    def _resume_refusal(
        cls,
        db: sqlite3.Connection,
        *,
        run_id: str,
        task_id: str,
        role: str,
        held: dict[tuple[str, str], str],
        now: str,
        metadata: str | None = None,
        auto_review: bool = False,
    ) -> str | None:
        """Why this QUEUED run may not be resumed, or None if it may.

        The same questions ``create`` asks, against the state that exists now.
        Prose rather than a ``RunGateError``: this is not a client refusal, it is
        the reason recorded on a run nobody will ever launch.

        ``auto_review`` is the dispatcher's flag, passed in exactly as ``create``
        takes it — the flag, never a precomputed verdict — because the last question
        below is only asked when reviews are dispatched at all.
        """
        if role not in {ROLE_WORKER, ROLE_REVIEW, ROLE_CHAT}:
            # A role this build does not know cannot be gate-checked, and admitting
            # it would walk past every check above. Fail closed.
            return f"its recorded role ({role!r}) is not one this build can schedule"
        # The task questions come before the run-vs-run ones, and apply to a review
        # run as much as to a worker run: a review of a cancelled task is not
        # evidence anybody is waiting for, and resuming it would spend a reviewer's
        # tokens on a decision nobody will make.
        task_reason = cls._task_refusal(
            db, task_id=task_id, metadata=metadata, role=role
        )
        if task_reason is not None:
            return task_reason
        if (task_id, role) in held:
            return f"another {role} run ({held[(task_id, role)]}) on this task is unsettled"
        if role in {ROLE_REVIEW, ROLE_CHAT}:
            # A review run is how a gate gets something to decide; a question (ROLE_CHAT)
            # is not work and is gated by none of the worker-only checks below.
            return None
        if (task_id, ROLE_REVIEW) in held:
            return f"this task's review run ({held[(task_id, ROLE_REVIEW)]}) has not settled"
        rows = cls._open_gate_items(
            db, task_id=task_id, now=now, exclude_bridge_run_id=run_id
        )
        if rows:
            _, listed = cls._describe_gate_items(rows)
            return f"{len(rows)} gate item(s) on this task are still open: {listed}"
        if auto_review:
            # ``create``'s fourth question, re-asked here — last, in create's own
            # order, so an existing gate item is reported as the gate it is.
            #
            # **Why this is asked at resume at all (M2/M1, 2026-08-30).** It was
            # deliberately left out, on the ground that a QUEUED worker coexisting
            # with an owed review is unreachable through the API: ``create`` would
            # have refused it. True of the API, and false of a restart. The startup
            # reconciliation that converts an unreleasable owed review into a human
            # blocker can fail *partway* — the ``reviews_owed`` read can fail
            # outright, and a per-task blocker write can fail and is skipped so one
            # unrecordable task does not cost the rest theirs. Continuing into the
            # resume sweep after that left the queue judged against gates that were
            # never raised, and a QUEUED worker was scheduled onto work that
            # succeeded and was never reviewed.
            #
            # Deriving the refusal here rather than refusing to resume on a
            # bookkeeping flag is the deliberate choice: this asks the hazard itself,
            # so it does not need to know *which* tasks an incomplete reconciliation
            # would have covered, and every unaffected queued run still resumes. In
            # the ordinary path it changes nothing — the reconciliation's blocker is
            # a gate item, and once that item exists the derived question stands down
            # (``_has_review_gate_item``), so the gate above answers first.
            owed = cls._reviews_owed(db, task_id=task_id)
            if owed:
                return (
                    f"worker run {owed[0]['run_id']} on this task succeeded and no "
                    "independent cross-review accounts for it yet, so this task owes a "
                    "review and no gate item was recorded for it; a start whose "
                    "reconciliation completes records the review-owed blocker, and "
                    "clearing that blocker — which accepts the work with no review "
                    "decision on record — lets a new worker run be dispatched"
                )
        return None

    def _cancel_locked(
        self, db: sqlite3.Connection, row: sqlite3.Row, *, reason: str, now: str,
        context: str = "Not resumed after restart",
    ) -> bool:
        """Cancel one QUEUED run inside the caller's transaction.

        ``cancel`` cannot be reused: it opens its own transaction, and a second
        ``BEGIN IMMEDIATE`` on this database while this one is held would block on
        itself. This mirrors the QUEUED -> CANCELLED half of ``_transition``,
        including settling the run's orphaned bridge prompts.

        ``context`` prefixes the recorded reason, because the same refusal reads
        differently depending on when it was reached: "Not resumed after restart"
        for the startup sweep, "Not launched" for the pre-launch re-check.
        """
        message = redact_text(f"{context}: {reason}")
        cursor = db.execute(
            """UPDATE execution_runs
               SET state = ?, version = version + 1, finished_at = ?, pid = NULL, error_message = ?
               WHERE run_id = ? AND state = ? AND version = ?""",
            (
                RunState.CANCELLED.value,
                now,
                message,
                row["run_id"],
                RunState.QUEUED.value,
                row["version"],
            ),
        )
        if cursor.rowcount != 1:
            return False
        # A task event needs a task. Normally the foreign key makes a run without
        # one impossible, but ``_task_refusal`` reports exactly that case (a
        # database edited outside Agora), and inserting a timeline row for a task
        # that does not exist raises — which would abort the whole sweep and leave
        # every other row in it unsettled. So the run is still settled, and the
        # record that cannot exist is skipped rather than faked. The run row's own
        # ``error_message`` still carries the reason. The prompt sweep is skipped
        # with it: an Attention item is scoped to a task too, so there is nothing
        # for it to find.
        if db.execute(
            "SELECT 1 FROM tasks WHERE task_id = ?", (row["task_id"],)
        ).fetchone() is None:
            return True
        self._event(
            db,
            row["task_id"],
            "run.cancelled",
            row["actor"],
            sanitize_data(
                {"run_id": row["run_id"], "adapter": row["adapter"], "reason": message}
            ),
            now,
        )
        self._settle_orphaned_prompts(db, run_id=row["run_id"], task_id=row["task_id"], now=now)
        return True

    def abandon(
        self,
        run_id: str,
        *,
        expected_version: int,
        reason: str,
        deliverables: dict[str, Any] | None = None,
    ) -> ExecutionRun:
        """Give up on a run the dispatcher can no longer follow. ``deliverables``
        is its frozen delivery record: work already written to the worktree is
        still this run's delivery, and its record should say so."""
        return self._transition(
            run_id,
            RunState.ABANDONED,
            expected_version=expected_version,
            error_message=reason,
            deliverables=deliverables,
            event_payload={"reason": reason},
        )

    @staticmethod
    def _identity_preserved(
        run_id: str, *, stored: str | None, result: dict[str, Any]
    ) -> dict[str, Any]:
        """``result`` with this run's identity carried over from the stored row.

        **The invariant.** A run's identity — ``agora_role``, ``reviews_run_id``,
        ``task_version`` (``IDENTITY_METADATA_KEYS``) — is set once, by ``create``,
        in the transaction that inserts the row. It says what the control plane
        decided this run *is*, and every gate in this module reads it to decide
        which run gates which other run. It is not part of any run's result, so no
        write that carries a result may replace it.

        **What went wrong without this.** ``result_metadata`` is one JSON column,
        written wholesale. ``finish`` passed ``result_metadata or {}``, so a settle
        with no result — every pre-launch failure: adapter disabled, workspace
        gone, launch refused — wrote ``{}`` over the identity. A QUEUED *review*
        run that failed before it started then had no ``agora_role``, and
        ``_reviews_owed``'s ``COALESCE(..., 'worker')`` read it as the task's most
        recent *worker* run: state ``failed``, so nothing was owed, so the next
        worker run passed the review gate on unreviewed work. One field cleared on
        an unrelated path defeated the whole gate.

        **How a future write is stopped from doing it again.** Three things, in
        order of how loudly they fail:

        1. This method is on the only path that writes the column, and that path is
           now a *single statement* with a single caller: ``_write_result_metadata``
           owns the column, and ``_transition`` (``finish``, ``cancel``,
           ``abandon``, ``recover_abandoned``) delegates to it. The two other paths
           that settle a run with hand-written SQL — ``record_cancelled_output`` and
           ``_cancel_locked``, the queued-cancel settle — name their columns
           explicitly and do not include this one.
        2. An identity key inside ``result`` is refused, not merged over. A caller
           passing one has confused identity for result, and that is exactly the
           confusion that produced the bug: the dispatcher used to re-send the role
           with every successful settle, which made the role look like something a
           result write is expected to carry, so the paths that had no result
           looked like they were merely omitting an optional field.
        3. ``test_no_raw_sql_write_of_result_metadata`` fails if any other UPDATE of
           ``execution_runs`` in this module so much as *names* the column, which is
           the one way 1 can silently stop being true. It is a backstop on the
           structure, not the structure itself — see ``_write_result_metadata`` for
           why that distinction was worth the refactor.

        A row whose stored metadata is absent or unparseable yields no identity —
        the honest reading of a hand-edited or pre-role row, and the same one
        ``role_of``/``task_version_of`` make for a missing key. It is not repaired
        here: this method preserves what the row says, and inventing a role for a
        row that never had one is the silent substitution this project forbids.
        """
        offered = sorted(IDENTITY_METADATA_KEYS & result.keys())
        if offered:
            raise RunValidationError(
                f"result metadata for run {run_id} may not carry the control-plane "
                f"identity key(s) {', '.join(offered)}: identity is set by create and "
                "is preserved across every settle, not written by a result"
            )
        try:
            existing = json.loads(stored) if stored else {}
        except (TypeError, ValueError):
            existing = {}
        if not isinstance(existing, dict):
            existing = {}
        # Three layers, lowest first. What the client sent at create (``display_prompt``:
        # the line the person typed, kept apart from the prompt the agent received) is
        # part of the run's record, not of its result, and used to be dropped here the
        # moment the run settled — the workbench then had no words of the person's to
        # draw and fell back to peeling the agent's preamble off the prompt (user, 2026-
        # 09-19: "我说的内容不显示"). The result overlays it; identity overlays both.
        client_sent = {k: v for k, v in existing.items() if k not in IDENTITY_METADATA_KEYS}
        return {
            **client_sent,
            **result,
            **identity_metadata(existing),
        }

    def _transition(
        self,
        run_id: str,
        target: RunState,
        *,
        expected_version: int,
        actor: str | None = None,
        pid: int | None = None,
        exit_code: int | None = None,
        stdout_tail: str | None = None,
        stderr_tail: str | None = None,
        error_message: str | None = None,
        result_metadata: dict[str, Any] | None = None,
        deliverables: dict[str, Any] | None = None,
        event_payload: dict[str, Any] | None = None,
    ) -> ExecutionRun:
        now = utc_now()
        with self._transaction() as db:
            row = db.execute("SELECT * FROM execution_runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                raise RunNotFoundError(run_id)
            current = RunState(row["state"])
            if int(row["version"]) != expected_version:
                raise RunConflictError(
                    f"Expected run version {expected_version}, current version is {row['version']}"
                )
            if target not in _ALLOWED[current]:
                if current == target or current in TERMINAL_RUN_STATES:
                    return self._run(row)
                raise RunConflictError(f"Cannot transition run {current.value} to {target.value}")
            next_version = expected_version + 1
            updates: dict[str, Any] = {"state": target.value, "version": next_version}
            if target == RunState.RUNNING:
                updates.update({"pid": pid, "started_at": now})
            if target in TERMINAL_RUN_STATES:
                updates.update({"finished_at": now, "pid": None})
            optional = {
                "exit_code": exit_code,
                "stdout_tail": (
                    redact_text(stdout_tail)[-OUTPUT_TAIL_LIMIT:] if stdout_tail is not None else None
                ),
                "stderr_tail": (
                    redact_text(stderr_tail)[-OUTPUT_TAIL_LIMIT:] if stderr_tail is not None else None
                ),
                "error_message": redact_text(error_message) if error_message is not None else None,
                # A dict always serializes to a non-empty JSON string, so a run
                # that changed nothing ({"files": [], ...}) is recorded rather
                # than dropped by the None filter below.
                #
                # Deliberately NOT run through sanitize_data: it flags any dict key
                # containing one of its markers, and "PAT" (personal access token)
                # is a substring of "path" — so every delivered file path would be
                # stored as "[REDACTED]". The values here are git-derived paths,
                # statuses, sizes and digests, and the live listing already returns
                # those same paths unredacted.
                "deliverable_snapshot": (
                    self._json(deliverables) if deliverables is not None else None
                ),
            }
            updates.update({key: value for key, value in optional.items() if value is not None})
            if self._write_transition_columns(
                db, run_id=run_id, expected_version=expected_version, updates=updates
            ) != 1:
                raise RunConflictError("Run changed during transition")
            if result_metadata is not None:
                # NOT one of the columns above, and it *cannot* be: the state UPDATE
                # is built from ``_TRANSITION_COLUMNS``, which does not contain this
                # column, and ``_write_transition_columns`` refuses any key outside
                # that list. ``result_metadata`` carries the run's identity, so it has
                # exactly one writer in this module (``_write_result_metadata``, which
                # is where ``_identity_preserved`` is called). None still means "do not
                # touch that column at all", which is the cheapest form of the same
                # guarantee.
                self._write_result_metadata(db, row, result_metadata)
            event_actor = actor or row["actor"]
            event_name = "started" if target == RunState.RUNNING else target.value
            self._event(
                db,
                row["task_id"],
                f"run.{event_name}",
                event_actor,
                sanitize_data({"run_id": run_id, "adapter": row["adapter"], **(event_payload or {})}),
                now,
            )
            if target in TERMINAL_RUN_STATES:
                self._settle_orphaned_prompts(db, run_id=run_id, task_id=row["task_id"], now=now)
        return self.require(run_id)

    #: Every column ``_transition``'s state UPDATE may set, in the order it writes
    #: them. **``result_metadata`` is deliberately absent, and that absence is the
    #: guarantee** — not a convention, and no longer something a static check has to
    #: infer from an f-string it cannot read.
    #:
    #: ``_transition`` assembles its SET list from whichever of these columns the
    #: transition actually touches, so the statement's shape is dynamic while its
    #: *vocabulary* is fixed here. Before this list existed the SET list was built
    #: by iterating the update dict, so one added ``updates["result_metadata"] = ...``
    #: line was a second writer of the identity column — invisible to
    #: ``test_no_raw_sql_write_of_result_metadata``, which had to exempt an
    #: unreadable dynamic statement in any function that delegates to the writer,
    #: and ``_transition`` is such a function. The exemption is gone because the
    #: hole it covered is gone (N4, 2026-08-30).
    _TRANSITION_COLUMNS: tuple[str, ...] = (
        "state", "version", "pid", "started_at", "finished_at",
        "exit_code", "stdout_tail", "stderr_tail", "error_message",
        "deliverable_snapshot",
    )

    @classmethod
    def _write_transition_columns(
        cls,
        db: sqlite3.Connection,
        *,
        run_id: str,
        expected_version: int,
        updates: dict[str, Any],
    ) -> int:
        # NOTE: the reasoning below is in comments, not in the docstring, on purpose.
        # ``test_no_raw_sql_write_of_result_metadata`` refuses a dynamically built
        # UPDATE of execution_runs in any function that so much as *names* the identity
        # column — with no exemption, since exempting one is what let N4 through — and
        # ``ast.unparse`` keeps docstrings while dropping comments. A function holding
        # the module's one interpolated UPDATE has to be a function that cannot be
        # talking about that column, and this is how it stays one.
        #
        # Split out of ``_transition`` for exactly that reason. ``_transition`` decides
        # *which* columns a transition touches and reaches the identity column only
        # through ``_write_result_metadata``; this function turns that decision into
        # SQL, and the set of columns it can name is ``_TRANSITION_COLUMNS``, not
        # whatever keys the caller's dict happens to have.
        """Apply one run's state-transition columns. Returns the affected row count.

        The column vocabulary is the fixed ``_TRANSITION_COLUMNS`` list; a key outside
        it raises rather than being dropped. Dropping it would silently discard a write
        the caller believed it had made, which is the class of failure this slice keeps
        finding, and a key that is not on the list is a programming error — so the
        transaction rolls back with the column named.
        """
        unknown = sorted(key for key in updates if key not in cls._TRANSITION_COLUMNS)
        if unknown:
            raise RunValidationError(
                f"run transition may not write column(s) {', '.join(unknown)}: the state "
                f"UPDATE writes only {', '.join(cls._TRANSITION_COLUMNS)}. A run's "
                "identity has exactly one writer (_write_result_metadata), which is "
                "where _identity_preserved is called."
            )
        columns = [column for column in cls._TRANSITION_COLUMNS if column in updates]
        assignments = ", ".join(f"{column} = ?" for column in columns)
        cursor = db.execute(
            f"UPDATE execution_runs SET {assignments} WHERE run_id = ? AND version = ?",
            [*(updates[column] for column in columns), run_id, expected_version],
        )
        return cursor.rowcount

    def _write_result_metadata(
        self, db: sqlite3.Connection, row: sqlite3.Row, result: dict[str, Any]
    ) -> None:
        """**The only statement in this module that writes ``result_metadata``.**

        That exclusivity is the point, and it is why this is a method rather than
        four lines inside ``_transition``. The column carries a run's identity
        (``IDENTITY_METADATA_KEYS``), every gate here reads that identity, and the
        column is written *wholesale* — so any second statement assigning it can
        erase the identity without touching a line of the gate logic. Giving the
        column one writer makes ``_identity_preserved`` unavoidable for anything
        that writes it, instead of a convention each new statement has to remember.

        ``test_no_raw_sql_write_of_result_metadata`` is now a **backstop** on that
        structure rather than the guarantee: it asserts that no other UPDATE of
        ``execution_runs`` in this module *names* the column at all. That is a
        property of a whole statement — position-independent, so nothing about
        clause order, nesting or a subquery's own ``WHERE`` can hide an assignment
        from it. Its three predecessors each tried to decide whether a SET clause
        assigned the column, and each was defeated by a form of SQL it could not
        parse; the structure is what removes the parsing problem.

        Its fifth revision needed one exemption to stay true — an unreadable, f-string
        ``UPDATE`` inside a function that delegates here — and ``_transition`` was
        exactly such a function, so an ``updates["result_metadata"] = ...`` line there
        satisfied every assertion the guard made (N4). That exemption is gone rather
        than narrowed: the dynamic statement moved to
        ``_write_transition_columns``, whose vocabulary is the fixed
        ``_TRANSITION_COLUMNS`` list and whose body never names this column.

        Runs inside the caller's transaction, immediately after the row's guarded
        state UPDATE has already succeeded, so no version predicate is needed here:
        ``BEGIN IMMEDIATE`` serializes writers, and the row was just proved to be
        at the expected version. A refusal from ``_identity_preserved`` rolls the
        whole transition back, which is the same net effect as the old ordering
        (nothing persisted) with the state write no longer able to land alone.
        """
        run_id = str(row["run_id"])
        db.execute(
            "UPDATE execution_runs SET result_metadata = ? WHERE run_id = ?",
            (
                self._json(
                    self._identity_preserved(
                        run_id, stored=row["result_metadata"], result=result
                    )
                ),
                run_id,
            ),
        )

    def _settle_orphaned_prompts(
        self, db: sqlite3.Connection, *, run_id: str, task_id: str, now: str,
    ) -> None:
        """Close this run's bridge permission prompts as it settles.

        In the same transaction as the terminal transition, so a crash cannot leave
        the run finished and its prompt open. Only bridge-raised, run-scoped items
        are touched — the review gate raised *after* its review run is terminal
        must survive; see ``agora.attention.run_scope`` for why the cut is by
        origin and not by run state.
        """
        settle_bridge_attention_for_run(
            db,
            run_id=run_id,
            now=now,
            record_event=lambda event_type, payload: self._event(
                db, task_id, event_type, "system", payload, now
            ),
        )

    def _event(
        self,
        db: sqlite3.Connection,
        task_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
        created_at: str,
    ) -> None:
        self.tasks._insert_event(
            db,
            task_id=task_id,
            event_type=event_type,
            actor=actor,
            payload=payload,
            created_at=created_at,
        )

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _run(row: sqlite3.Row) -> ExecutionRun:
        return ExecutionRun(
            run_id=row["run_id"], task_id=row["task_id"], project_id=row["project_id"],
            adapter=row["adapter"], state=RunState(row["state"]), prompt=row["prompt"],
            workspace=row["workspace"], command=json.loads(row["command"]),
            timeout_seconds=row["timeout_seconds"], pid=row["pid"], exit_code=row["exit_code"],
            stdout_tail=row["stdout_tail"], stderr_tail=row["stderr_tail"],
            result_metadata=json.loads(row["result_metadata"]), error_message=row["error_message"],
            # The same identity the summary carries. Left to the field defaults,
            # a full record fetched by id contradicted the summary of that very
            # row: the list said `review`, the detail said `worker`.
            role=role_of(json.loads(row["result_metadata"]) if row["result_metadata"] else {}),
            reviews_run_id=_reviews_link(row["result_metadata"]),
            version=row["version"], queued_at=row["queued_at"], started_at=row["started_at"],
            finished_at=row["finished_at"], actor=row["actor"],
        )

    @staticmethod
    def _summary(row: sqlite3.Row) -> RunSummary:
        # The identity keys travel with the summary. Every view that nests a
        # review under the run it reviews reads them, and their absence reads as
        # "this is a worker run" — so a page of summaries drew each review as a
        # top-level turn of its own. Read through the same helpers the full
        # record uses, so "what does an untagged run mean" has one answer.
        metadata = json.loads(row["result_metadata"]) if row["result_metadata"] else {}
        return RunSummary(
            run_id=row["run_id"], task_id=row["task_id"], project_id=row["project_id"],
            adapter=row["adapter"], state=RunState(row["state"]), version=row["version"],
            queued_at=row["queued_at"], started_at=row["started_at"],
            finished_at=row["finished_at"], exit_code=row["exit_code"],
            role=role_of(metadata),
            reviews_run_id=_reviews_link(row["result_metadata"]),
            remote=remote_target_public(metadata),
        )
