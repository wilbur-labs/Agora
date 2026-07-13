"""SQLite persistence for task manifests and append-only events."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

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
        db = sqlite3.connect(str(self.db_path), timeout=10)
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
                    updated_at TEXT NOT NULL
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
            db.commit()

    def create(self, request: CreateTaskRequest) -> TaskManifest:
        task_id = self._new_id("task")
        now = utc_now()
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
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
                    TaskState.BACKLOG.value,
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
                payload={"state": TaskState.BACKLOG.value, "version": 1},
                created_at=now,
            )
            db.commit()
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
        kind: str | None = None,
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
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
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
            db.commit()
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

    def append_event(self, task_id: str, request: AppendEventRequest) -> TaskEvent:
        if self.get(task_id) is None:
            raise TaskNotFoundError(task_id)
        now = utc_now()
        with closing(self._connect()) as db:
            event = self._insert_event(
                db,
                task_id=task_id,
                event_type=request.event_type,
                actor=request.actor,
                payload=request.payload,
                created_at=now,
            )
            db.commit()
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
