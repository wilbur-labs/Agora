"""SQLite persistence and lifecycle invariants for execution runs."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from agora.attention.models import AttentionKind, AttentionState
from agora.attention.run_scope import settle_bridge_attention_for_run
from agora.tasks.localday import local_day_bounds, local_day_of
from agora.tasks.models import TaskState, utc_now
from agora.tasks.store import TaskStore

from .models import (
    ACTIVE_RUN_STATES,
    CancelRunRequest,
    CreateRunRequest,
    ExecutionRun,
    REVIEWS_RUN_KEY,
    ROLE_KEY,
    ROLE_REVIEW,
    ROLE_WORKER,
    RunState,
    RunSummary,
    TASK_VERSION_KEY,
    TERMINAL_RUN_STATES,
    OUTPUT_TAIL_LIMIT,
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

# Attention kinds that gate a new worker run, and why, per kind:
#
# - ``approval``: it *is* the gate. AGENTS.md binds an approval to repo, ref,
#   commit, stage, artifact path and hash; building the next run on top of work
#   whose approval is still open makes that binding meaningless, and is the
#   Run -> Artifact/Evidence -> Gate -> Handoff line running backwards.
# - ``blocker``: the task is stuck on something only a human can clear. Another
#   worker run does not clear it — it spends tokens on the same wall — and the
#   auto-review failure path raises exactly this kind, so it is the "the review
#   did not pass" signal that must not be built on.
# - ``question`` deliberately does NOT gate here. It binds to no artifact and
#   settles no decision: it is the agent asking for information, and answering it
#   later (or by starting a better-prompted run, which in practice *is* the
#   answer) is legitimate. The client still declines to offer Continue while a
#   question is open — that is a helpful default the human can override by
#   answering or cancelling the item — but it is not a control-plane contract.
#
# Note what excluding ``question`` does NOT exempt: a vendor permission prompt is
# raised as ``approval`` (bridges/codex_protocol.decode_approval, and
# bridges/normalize for a hook ``permissionrequest``), because it is a decision
# bound to a command or a file change. So Codex permission prompts DO gate. What
# keeps that from becoming a permanent lockout is that a bridge prompt is settled
# with its run — see _settle_orphaned_prompts and agora.attention.run_scope — not
# that it fails to gate.
GATING_ATTENTION_KINDS: tuple[str, ...] = (
    AttentionKind.APPROVAL.value,
    AttentionKind.BLOCKER.value,
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
    ) -> ExecutionRun:
        """Record a queued run.

        ``role`` is a server-side argument, never a field of ``request``: it
        decides which unfinished run blocks this one, so a client able to set it
        could name a role nothing matches and open a second concurrent run on the
        same worktree. ``CreateRunRequest`` refuses the reserved keys outright, and
        only the dispatcher's own review dispatch passes ``ROLE_REVIEW``.
        """
        if role not in {ROLE_WORKER, ROLE_REVIEW}:
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
            # The task version this run is validated against, recorded on the row so
            # the question can be re-asked later (see TASK_VERSION_KEY,
            # _task_refusal). Set to the version this transaction is about to write
            # when it bumps planned -> running, not the one it read: that bump is
            # part of creating this run, so it is not a change the run must be
            # refused for.
            validated_task_version = current_version
            if state == TaskState.PLANNED:
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

        This is the half of Run -> Gate ordering that IS enforceable atomically:
        the review run is a row, in this database, readable in this transaction.
        The other half (the gap between a worker run settling and its review being
        queued) is not, and is documented in
        docs/architecture/run-gate-ordering-v1.md.

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
    def _open_gate_items(
        db: sqlite3.Connection, *, task_id: str, now: str, exclude_run_id: str | None = None,
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

        ``exclude_run_id`` drops items scoped to that run. Used by the two
        re-validations of an already-inserted row (``revalidate_queued`` and
        ``revalidate_before_launch``): an item a run raised itself (a bridge
        permission prompt) is not a gate on prior work that run must wait behind,
        and at create time a brand-new run has raised nothing, so the create path
        passes nothing here.
        """
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'attention_items'"
        ).fetchone() is None:
            return []
        clause = "" if exclude_run_id is None else " AND (run_id IS NULL OR run_id <> ?)"
        values: tuple[Any, ...] = (
            (task_id, AttentionState.OPEN.value, *GATING_ATTENTION_KINDS, now)
            if exclude_run_id is None
            else (task_id, AttentionState.OPEN.value, *GATING_ATTENTION_KINDS, now, exclude_run_id)
        )
        return db.execute(
            f"""
            SELECT item_id, kind, title FROM attention_items
            WHERE task_id = ?
              AND state = ?
              AND kind IN ({",".join("?" for _ in GATING_ATTENTION_KINDS)})
              AND (expires_at IS NULL OR expires_at > ?){clause}
            ORDER BY created_at, rowid
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

    def attach_pid(self, run_id: str, *, expected_version: int, pid: int) -> ExecutionRun:
        now = utc_now()
        with self._transaction() as db:
            row = db.execute("SELECT * FROM execution_runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                raise RunNotFoundError(run_id)
            cursor = db.execute(
                """
                UPDATE execution_runs SET pid = ?, version = version + 1
                WHERE run_id = ? AND state = ? AND version = ?
                """,
                (pid, run_id, RunState.RUNNING.value, expected_version),
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
        snapshot was taken and reads must say so instead of guessing."""
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
            result_metadata=sanitize_data(result_metadata or {}),
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

    def revalidate_queued(self) -> tuple[list[str], list[dict[str, Any]]]:
        """Re-apply the create-time gates to persisted QUEUED runs before resuming.

        Returns ``(admitted_run_ids, refused)`` — the runs that may still be
        launched, oldest first, and one ``{run_id, task_id, role, reason}`` record
        per run settled instead.

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
                    metadata=row["result_metadata"],
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

    def revalidate_before_launch(self, run_id: str) -> str | None:
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
        the meantime. It would not: ``_resume_refusal`` excludes items scoped to the
        run being judged (``exclude_run_id=run_id``), which is exactly the
        transient, run-scoped fact that worry was about, and it keeps every gate
        raised by *prior* work. Leaving the gate questions out left a real hole with
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
        db: sqlite3.Connection, *, task_id: str, metadata: str | None
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
          dispatching again, and stated in docs/architecture/run-gate-ordering-v1.md
          rather than hidden.

        A row that does not record a task version (written before
        ``TASK_VERSION_KEY`` existed, or by hand) can only be lifecycle-checked.
        Unknown is reported as unknown by skipping the check it cannot answer, not
        by refusing every persisted row from an older database — that would settle
        runs that are very likely fine, which is a larger harm than the window it
        would close. Recorded as a residual in the ADR.
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
        if recorded is not None and recorded != current:
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
    ) -> str | None:
        """Why this QUEUED run may not be resumed, or None if it may.

        The same questions ``create`` asks, against the state that exists now.
        Prose rather than a ``RunGateError``: this is not a client refusal, it is
        the reason recorded on a run nobody will ever launch.
        """
        if role not in {ROLE_WORKER, ROLE_REVIEW}:
            # A role this build does not know cannot be gate-checked, and admitting
            # it would walk past every check above. Fail closed.
            return f"its recorded role ({role!r}) is not one this build can schedule"
        # The task questions come before the run-vs-run ones, and apply to a review
        # run as much as to a worker run: a review of a cancelled task is not
        # evidence anybody is waiting for, and resuming it would spend a reviewer's
        # tokens on a decision nobody will make.
        task_reason = cls._task_refusal(db, task_id=task_id, metadata=metadata)
        if task_reason is not None:
            return task_reason
        if (task_id, role) in held:
            return f"another {role} run ({held[(task_id, role)]}) on this task is unsettled"
        if role == ROLE_REVIEW:
            return None  # a review run is how a gate gets something to decide
        if (task_id, ROLE_REVIEW) in held:
            return f"this task's review run ({held[(task_id, ROLE_REVIEW)]}) has not settled"
        rows = cls._open_gate_items(db, task_id=task_id, now=now, exclude_run_id=run_id)
        if rows:
            _, listed = cls._describe_gate_items(rows)
            return f"{len(rows)} gate item(s) on this task are still open: {listed}"
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
                "result_metadata": self._json(result_metadata) if result_metadata is not None else None,
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
            assignments = ", ".join(f"{column} = ?" for column in updates)
            cursor = db.execute(
                f"UPDATE execution_runs SET {assignments} WHERE run_id = ? AND version = ?",
                [*updates.values(), run_id, expected_version],
            )
            if cursor.rowcount != 1:
                raise RunConflictError("Run changed during transition")
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
            version=row["version"], queued_at=row["queued_at"], started_at=row["started_at"],
            finished_at=row["finished_at"], actor=row["actor"],
        )

    @staticmethod
    def _summary(row: sqlite3.Row) -> RunSummary:
        return RunSummary(
            run_id=row["run_id"], task_id=row["task_id"], project_id=row["project_id"],
            adapter=row["adapter"], state=RunState(row["state"]), version=row["version"],
            queued_at=row["queued_at"], started_at=row["started_at"],
            finished_at=row["finished_at"], exit_code=row["exit_code"],
        )
