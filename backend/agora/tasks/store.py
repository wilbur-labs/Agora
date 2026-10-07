"""SQLite persistence for task manifests and append-only events."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import timezone
from pathlib import Path
from typing import Any, Collection, Iterable

from .models import (
    AppendEventRequest,
    CreateTaskRequest,
    TaskBudget,
    TaskEvent,
    TaskManifest,
    TaskRisk,
    TaskState,
    utc_now,
)
from .localday import local_day_bounds, local_day_of
from .transitions import can_transition


class TaskNotFoundError(LookupError):
    pass


class InvalidTransitionError(ValueError):
    pass


class StaleTaskVersionError(RuntimeError):
    pass


class TaskStore:
    """Small synchronous store suitable for Agora's current single-node API."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        # 60 s, not 10: one methodology completion-review claim re-validates its whole
        # history inside the write transaction and holds the lock ~9-10 s on a work PC,
        # so a 10 s wait failed with "database is locked" (CR-33). Waiting is the right
        # behaviour for a local single-user store; shortening that transaction is the
        # real fix and stays open as CR-33.
        db = sqlite3.connect(str(self.db_path), timeout=60)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA journal_mode = WAL")
        return db

    def _initialize(self) -> None:
        with closing(self._connect()) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    primary_agent TEXT,
                    reviewers TEXT NOT NULL DEFAULT '[]',
                    acceptance TEXT NOT NULL DEFAULT '[]',
                    budget TEXT NOT NULL DEFAULT '{}',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    version INTEGER NOT NULL DEFAULT 1,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived_at TEXT,
                    clarifications TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_project_created
                    ON tasks(project_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_tasks_state
                    ON tasks(state);

                CREATE TABLE IF NOT EXISTS task_events (
                    event_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_task_events_task_created
                    ON task_events(task_id, created_at, event_id);
                """
            )
            # Additive migrations for columns added after initial release.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(tasks)")}
            if "archived_at" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN archived_at TEXT")
            if "clarifications" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN clarifications TEXT")

            from agora.requirements.schema import initialize_requirement_schema
            from agora.execution.schema import initialize_execution_schema
            from agora.attention.schema import initialize_attention_schema
            from agora.workflows.schema import initialize_workflow_schema
            from agora.control_plane.schema import initialize_control_plane_schema
            from agora.orchestration.schema import initialize_orchestration_schema

            initialize_requirement_schema(db)
            initialize_execution_schema(db)
            initialize_attention_schema(db)
            initialize_workflow_schema(db)
            initialize_control_plane_schema(db)
            initialize_orchestration_schema(db)
            db.commit()

    @contextmanager
    def _transaction(self):
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

    #: The states `create` may be asked for. `backlog` is where the lifecycle starts and
    #: stays the default. `planned` is for a task whose requirements-and-design step
    #: happened *outside* the board — the project kickoff, where the owner approved a
    #: charter with success criteria and a card that says what done looks like, and the
    #: project conversation that proposes a card against that charter afterwards. Walking
    #: such a task up the chain instead would demand an approved requirement spec it has
    #: no reason to have (`transition` gates `design` on one), and leaving it in `backlog`
    #: is what made every kickoff task undispatchable.
    #:
    #: It is a keyword of *this method*, deliberately not a field of `CreateTaskRequest`:
    #: that model is the body of the public `POST /api/tasks`, and a state on it would let
    #: any client create a dispatchable task and skip the lifecycle entirely.
    CREATABLE_STATES = frozenset({TaskState.BACKLOG, TaskState.PLANNED})

    def create(self, request: CreateTaskRequest, *, state: TaskState = TaskState.BACKLOG) -> TaskManifest:
        if state not in self.CREATABLE_STATES:
            raise ValueError(
                f"a task may not be created in {state.value}; "
                f"create it in {' or '.join(sorted(s.value for s in self.CREATABLE_STATES))} and transition it"
            )
        task_id = self._new_id("task")
        now = utc_now()
        with self._transaction() as db:
            db.execute(
                """
                INSERT INTO tasks (
                    task_id, project_id, title, description, kind, state, risk,
                    priority, primary_agent, reviewers, acceptance, budget,
                    metadata, version, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    task_id,
                    request.project_id,
                    request.title,
                    request.description,
                    request.kind,
                    state.value,
                    request.risk.value,
                    request.priority,
                    request.primary_agent,
                    self._json(request.reviewers),
                    self._json(request.acceptance),
                    self._json(request.budget.model_dump(exclude_none=True)),
                    self._json(request.metadata),
                    request.created_by,
                    now,
                    now,
                ),
            )
            self._insert_event(
                db,
                task_id=task_id,
                event_type="task_created",
                actor=request.created_by,
                # The state it was actually created in. A task that appears already
                # planned has to say so in its own timeline; a hard-coded "backlog" here
                # would make the board and the history disagree from the first event.
                payload={"state": state.value, "version": 1},
                created_at=now,
            )
        task = self.get(task_id)
        assert task is not None
        return task

    def get(self, task_id: str) -> TaskManifest | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return self._manifest(row) if row else None

    def list(
        self,
        *,
        project_id: str | None = None,
        state: TaskState | None = None,
        #: Keep only these states. Applied in the query, not after it: a caller that
        #: filtered a page in Python would be filtering the *first* `limit` rows by
        #: priority, and a project whose top rows are all finished would come back empty
        #: while live tasks sat below the cut.
        states: Collection[TaskState] | None = None,
        kind: str | None = None,
        include_archived: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TaskManifest]:
        clauses: list[str] = []
        values: list[Any] = []
        if project_id:
            clauses.append("project_id = ?")
            values.append(project_id)
        if state:
            clauses.append("state = ?")
            values.append(state.value)
        if states is not None:
            wanted = sorted({s.value for s in states})
            if not wanted:
                return []
            clauses.append(f"state IN ({', '.join('?' for _ in wanted)})")
            values.extend(wanted)
        if not include_archived:
            clauses.append("archived_at IS NULL")
        if kind:
            clauses.append("kind = ?")
            values.append(kind)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        values.extend([limit, offset])
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT * FROM tasks{where} ORDER BY priority DESC, created_at DESC LIMIT ? OFFSET ?",
                values,
            ).fetchall()
        return [self._manifest(row) for row in rows]

    def transition(
        self,
        task_id: str,
        target: TaskState,
        *,
        actor: str,
        reason: str | None = None,
        expected_version: int | None = None,
    ) -> TaskManifest:
        now = utc_now()
        with self._transaction() as db:
            row = db.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if not row:
                raise TaskNotFoundError(task_id)
            current = TaskState(row["state"])
            current_version = int(row["version"])
            if expected_version is not None and expected_version != current_version:
                raise StaleTaskVersionError(
                    f"Expected version {expected_version}, current version is {current_version}"
                )
            if not can_transition(current, target):
                raise InvalidTransitionError(f"Cannot transition {current.value} to {target.value}")
            if target == TaskState.DESIGN:
                approved = db.execute(
                    """
                    SELECT 1 FROM requirement_specs
                    WHERE task_id = ? AND state = 'approved' LIMIT 1
                    """,
                    (task_id,),
                ).fetchone()
                if not approved:
                    raise InvalidTransitionError(
                        "Task requires an approved spec to advance to design"
                    )
            next_version = current_version + 1
            cursor = db.execute(
                """
                UPDATE tasks SET state = ?, version = ?, updated_at = ?
                WHERE task_id = ? AND version = ?
                """,
                (target.value, next_version, now, task_id, current_version),
            )
            if cursor.rowcount != 1:
                raise StaleTaskVersionError("Task changed during transition")
            self._insert_event(
                db,
                task_id=task_id,
                event_type="state_changed",
                actor=actor,
                payload={
                    "from": current.value,
                    "to": target.value,
                    "reason": reason,
                    "version": next_version,
                },
                created_at=now,
            )
        task = self.get(task_id)
        assert task is not None
        return task

    def cancel(
        self,
        task_id: str,
        *,
        actor: str = "user",
        reason: str | None = None,
        expected_version: int | None = None,
    ) -> TaskManifest:
        return self.transition(
            task_id,
            TaskState.CANCELLED,
            actor=actor,
            reason=reason,
            expected_version=expected_version,
        )

    def set_archived(
        self,
        task_id: str,
        *,
        archived: bool,
        actor: str = "user",
        expected_version: int | None = None,
    ) -> TaskManifest:
        """Set the display-only archived flag. Does not change lifecycle state.

        Fails closed on a version conflict and is idempotent: setting the flag
        to its current value is a no-op that returns the task unchanged.
        """
        now = utc_now()
        with self._transaction() as db:
            row = db.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if not row:
                raise TaskNotFoundError(task_id)
            current_version = int(row["version"])
            if expected_version is not None and expected_version != current_version:
                raise StaleTaskVersionError(
                    f"Expected version {expected_version}, current version is {current_version}"
                )
            currently_archived = row["archived_at"] is not None
            if currently_archived == archived:
                task = self.get(task_id)
                assert task is not None
                return task
            next_version = current_version + 1
            new_archived_at = now if archived else None
            cursor = db.execute(
                """
                UPDATE tasks SET archived_at = ?, version = ?, updated_at = ?
                WHERE task_id = ? AND version = ?
                """,
                (new_archived_at, next_version, now, task_id, current_version),
            )
            if cursor.rowcount != 1:
                raise StaleTaskVersionError("Task changed during archive")
            self._insert_event(
                db,
                task_id=task_id,
                event_type="archived" if archived else "unarchived",
                actor=actor,
                payload={"archived": archived, "version": next_version},
                created_at=now,
            )
        task = self.get(task_id)
        assert task is not None
        return task

    def set_clarifications(self, task_id: str, data: dict[str, Any]) -> TaskManifest:
        """Store the requirements-interview questions/answers (working aux data).

        Does not change lifecycle state or bump the task version.
        """
        now = utc_now()
        with self._transaction() as db:
            row = db.execute("SELECT task_id FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if not row:
                raise TaskNotFoundError(task_id)
            db.execute(
                "UPDATE tasks SET clarifications = ?, updated_at = ? WHERE task_id = ?",
                (self._json(data), now, task_id),
            )
        task = self.get(task_id)
        assert task is not None
        return task

    def append_event(self, task_id: str, request: AppendEventRequest) -> TaskEvent:
        if self.get(task_id) is None:
            raise TaskNotFoundError(task_id)
        now = utc_now()
        with self._transaction() as db:
            event = self._insert_event(
                db,
                task_id=task_id,
                event_type=request.event_type,
                actor=request.actor,
                payload=request.payload,
                created_at=now,
            )
        return event

    def events(self, task_id: str) -> list[TaskEvent]:
        if self.get(task_id) is None:
            raise TaskNotFoundError(task_id)
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM task_events WHERE task_id = ? ORDER BY rowid",
                (task_id,),
            ).fetchall()
        return [self._event(row) for row in rows]

    def latest_event_day(
        self, task_id: str, event_types: Iterable[str], *, zone: timezone | None = None
    ) -> str | None:
        """The most recent **local** calendar day on which one of ``event_types``
        was recorded, or None if there is none.

        Exists so a recap can find its day without reading a task's whole event
        history: a long-lived task accumulates thousands of events, and paying for
        all of them on every open makes the cost grow without bound. The newest
        matching row is enough to name the day, so this reads exactly one.

        ``zone`` overrides the configured one, so a caller making several day
        derivations resolves the zone once and cannot straddle a config change.
        """
        types = list(dict.fromkeys(event_types))
        if not types:
            return None
        placeholders = ", ".join("?" for _ in types)
        with closing(self._connect()) as db:
            row = db.execute(
                f"""
                SELECT created_at FROM task_events
                WHERE task_id = ? AND event_type IN ({placeholders})
                ORDER BY created_at DESC LIMIT 1
                """,
                (task_id, *types),
            ).fetchone()
        return local_day_of(row["created_at"], zone=zone) if row else None

    def events_on_day(
        self, task_id: str, day: str, *, limit: int, zone: timezone | None = None
    ) -> tuple[list[TaskEvent], bool]:
        """``(events, truncated)`` for this task's events recorded on the **local**
        ``day``, oldest first.

        ``truncated`` is True when more exist than ``limit``, established by selecting
        ``limit + 1`` rows and returning ``limit`` of them -- the same shape as its
        sibling ``list_finished_on_day``. It used to return the bare list, and the
        caller inferred truncation from ``len(events) >= limit``, which cannot tell a
        truncated day from a day holding exactly that many events: the recap then said
        "more happened that day than is shown" about a day it had read whole, and that
        sentence also reaches the seed prompt handed to an agent.

        A UTC range, not a string prefix: timestamps are stored in UTC, so one local
        day spans two UTC dates (in UTC+9, local 2026-08-27 runs from
        2026-08-26T15:00Z). Prefix matching would silently drop a late night's work.
        """
        start, end = local_day_bounds(day, zone=zone)
        with closing(self._connect()) as db:
            rows = db.execute(
                """
                SELECT * FROM task_events
                WHERE task_id = ? AND created_at >= ? AND created_at < ?
                ORDER BY created_at, rowid LIMIT ?
                """,
                (task_id, start, end, limit + 1),
            ).fetchall()
        truncated = len(rows) > limit
        return [self._event(row) for row in rows[:limit]], truncated

    def _insert_event(
        self,
        db: sqlite3.Connection,
        *,
        task_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
        created_at: str,
    ) -> TaskEvent:
        event_id = self._new_id("evt")
        db.execute(
            """
            INSERT INTO task_events (event_id, task_id, event_type, actor, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (event_id, task_id, event_type, actor, self._json(payload), created_at),
        )
        return TaskEvent(
            event_id=event_id,
            task_id=task_id,
            event_type=event_type,
            actor=actor,
            payload=payload,
            created_at=created_at,
        )

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex}"

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _manifest(row: sqlite3.Row) -> TaskManifest:
        return TaskManifest(
            task_id=row["task_id"],
            project_id=row["project_id"],
            title=row["title"],
            description=row["description"],
            kind=row["kind"],
            state=TaskState(row["state"]),
            risk=TaskRisk(row["risk"]),
            priority=row["priority"],
            primary_agent=row["primary_agent"],
            reviewers=json.loads(row["reviewers"]),
            acceptance=json.loads(row["acceptance"]),
            budget=TaskBudget.model_validate(json.loads(row["budget"])),
            metadata=json.loads(row["metadata"]),
            version=row["version"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            archived_at=row["archived_at"] if "archived_at" in row.keys() else None,
            clarifications=(
                json.loads(row["clarifications"])
                if "clarifications" in row.keys() and row["clarifications"]
                else None
            ),
        )

    @staticmethod
    def _event(row: sqlite3.Row) -> TaskEvent:
        return TaskEvent(
            event_id=row["event_id"],
            task_id=row["task_id"],
            event_type=row["event_type"],
            actor=row["actor"],
            payload=json.loads(row["payload"]),
            created_at=row["created_at"],
        )
