"""SQLite persistence and lifecycle rules for human-attention items."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Iterator

from agora.execution.security import redact_text, sanitize_data
from agora.tasks.models import utc_now
from agora.tasks.store import TaskStore

from .models import (
    AttentionItem, AttentionKind, AttentionState, AttentionUrgency,
    CancelAttentionRequest, CreateAttentionRequest, RespondAttentionRequest,
)
from .schema import initialize_attention_schema


class AttentionNotFoundError(LookupError):
    pass


class AttentionConflictError(RuntimeError):
    pass


class AttentionValidationError(ValueError):
    pass


class AttentionStore:
    def __init__(self, task_store: TaskStore):
        self.tasks = task_store
        self.db_path = Path(task_store.db_path)
        with closing(self._connect()) as db:
            initialize_attention_schema(db)
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

    def create(self, request: CreateAttentionRequest) -> AttentionItem:
        item_id = f"attn_{uuid.uuid4().hex}"
        now = utc_now()
        with self._transaction() as db:
            task = db.execute("SELECT project_id FROM tasks WHERE task_id = ?", (request.task_id,)).fetchone()
            if task is None:
                raise AttentionNotFoundError("Task not found")
            if request.run_id:
                run = db.execute(
                    "SELECT task_id, project_id FROM execution_runs WHERE run_id = ?", (request.run_id,)
                ).fetchone()
                if run is None:
                    raise AttentionNotFoundError("Run not found")
                if run["task_id"] != request.task_id or run["project_id"] != task["project_id"]:
                    raise AttentionValidationError("Run does not belong to the requested task")
            db.execute(
                """INSERT INTO attention_items (
                    item_id, project_id, task_id, run_id, kind, state, urgency, title, body,
                    options, context, requester, assignee, version, expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                (item_id, task["project_id"], request.task_id, request.run_id, request.kind.value,
                 AttentionState.OPEN.value, request.urgency.value, redact_text(request.title.strip()), redact_text(request.body),
                 self._json(request.options), self._json(sanitize_data(request.context)), request.requester,
                 request.assignee, request.expires_at, now, now),
            )
            self._event(db, request.task_id, "attention.created", request.requester,
                        {"item_id": item_id, "kind": request.kind.value, "urgency": request.urgency.value}, now)
        return self.require(item_id)

    def get(self, item_id: str) -> AttentionItem | None:
        self.expire_overdue()
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM attention_items WHERE item_id = ?", (item_id,)).fetchone()
        return self._item(row) if row else None

    def require(self, item_id: str) -> AttentionItem:
        item = self.get(item_id)
        if item is None:
            raise AttentionNotFoundError("Attention item not found")
        return item

    def list(self, *, project_id: str | None = None, task_id: str | None = None,
             run_id: str | None = None, state: AttentionState | None = None,
             kind: AttentionKind | None = None, limit: int = 100, offset: int = 0) -> list[AttentionItem]:
        self.expire_overdue()
        clauses, values = [], []
        for column, value in (("project_id", project_id), ("task_id", task_id), ("run_id", run_id)):
            if value is not None:
                clauses.append(f"{column} = ?")
                values.append(value)
        if state is not None:
            clauses.append("state = ?"); values.append(state.value)
        if kind is not None:
            clauses.append("kind = ?"); values.append(kind.value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        urgency_order = "CASE urgency WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END"
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT * FROM attention_items{where} ORDER BY CASE state WHEN 'open' THEN 0 ELSE 1 END, {urgency_order}, created_at DESC LIMIT ? OFFSET ?",
                (*values, limit, offset),
            ).fetchall()
        return [self._item(row) for row in rows]

    def open_count(self, *, project_id: str | None = None) -> int:
        self.expire_overdue()
        sql, values = "SELECT COUNT(*) FROM attention_items WHERE state = 'open'", []
        if project_id is not None:
            sql += " AND project_id = ?"; values.append(project_id)
        with closing(self._connect()) as db:
            return int(db.execute(sql, values).fetchone()[0])

    def respond(self, item_id: str, request: RespondAttentionRequest) -> AttentionItem:
        now = utc_now()
        expired = False
        with self._transaction() as db:
            row = self._locked(db, item_id)
            expired = self._expire_locked(db, row, now)
            if not expired:
                self._assert_open_version(row, request.expected_version)
                cursor = db.execute(
                    """UPDATE attention_items SET state = ?, response = ?, response_action = ?, responded_by = ?,
                       responded_at = ?, updated_at = ?, version = version + 1
                       WHERE item_id = ? AND state = ? AND version = ?""",
                    (AttentionState.RESPONDED.value, redact_text(request.response), request.action.value, request.actor,
                     now, now, item_id, AttentionState.OPEN.value, request.expected_version),
                )
                if cursor.rowcount != 1:
                    raise AttentionConflictError("Attention item changed while responding")
                self._event(db, row["task_id"], "attention.responded", request.actor,
                            {"item_id": item_id, "action": request.action.value}, now)
        if expired:
            raise AttentionConflictError("Attention item is already expired")
        return self.require(item_id)

    def cancel(self, item_id: str, request: CancelAttentionRequest) -> AttentionItem:
        now = utc_now()
        expired = False
        with self._transaction() as db:
            row = self._locked(db, item_id)
            expired = self._expire_locked(db, row, now)
            if not expired:
                self._assert_open_version(row, request.expected_version)
                reason = redact_text(request.reason) if request.reason is not None else None
                cursor = db.execute(
                    """UPDATE attention_items SET state = ?, cancellation_reason = ?, updated_at = ?, version = version + 1
                       WHERE item_id = ? AND state = ? AND version = ?""",
                    (AttentionState.CANCELLED.value, reason, now, item_id,
                     AttentionState.OPEN.value, request.expected_version),
                )
                if cursor.rowcount != 1:
                    raise AttentionConflictError("Attention item changed while cancelling")
                self._event(db, row["task_id"], "attention.cancelled", request.actor,
                            {"item_id": item_id, "reason": reason}, now)
        if expired:
            raise AttentionConflictError("Attention item is already expired")
        return self.require(item_id)

    def expire_overdue(self) -> int:
        now = utc_now()
        with closing(self._connect()) as db:
            overdue = db.execute(
                "SELECT 1 FROM attention_items WHERE state = 'open' AND expires_at IS NOT NULL AND expires_at <= ? LIMIT 1",
                (now,),
            ).fetchone()
        if overdue is None:
            return 0
        with self._transaction() as db:
            rows = db.execute(
                "SELECT item_id, task_id, state, version, expires_at FROM attention_items WHERE state = 'open' AND expires_at IS NOT NULL AND expires_at <= ?",
                (now,),
            ).fetchall()
            return sum(1 for row in rows if self._expire_locked(db, row, now))

    def _expire_locked(self, db: sqlite3.Connection, row: sqlite3.Row, now: str) -> bool:
        if row["state"] != AttentionState.OPEN.value or row["expires_at"] is None or row["expires_at"] > now:
            return False
        cursor = db.execute(
            "UPDATE attention_items SET state = 'expired', updated_at = ?, version = version + 1 WHERE item_id = ? AND state = 'open' AND version = ?",
            (now, row["item_id"], row["version"]),
        )
        if not cursor.rowcount:
            return False
        self._event(db, row["task_id"], "attention.expired", "system", {"item_id": row["item_id"]}, now)
        return True

    @staticmethod
    def _locked(db: sqlite3.Connection, item_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM attention_items WHERE item_id = ?", (item_id,)).fetchone()
        if row is None:
            raise AttentionNotFoundError("Attention item not found")
        return row

    @staticmethod
    def _assert_open_version(row: sqlite3.Row, expected: int) -> None:
        if row["state"] != AttentionState.OPEN.value:
            raise AttentionConflictError(f"Attention item is already {row['state']}")
        if int(row["version"]) != expected:
            raise AttentionConflictError(f"Expected version {expected}, current version is {row['version']}")

    def _event(self, db: sqlite3.Connection, task_id: str, event_type: str, actor: str, payload: dict, now: str) -> None:
        self.tasks._insert_event(db, task_id=task_id, event_type=event_type, actor=actor, payload=payload, created_at=now)

    @staticmethod
    def _json(value) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _item(row: sqlite3.Row) -> AttentionItem:
        return AttentionItem(
            item_id=row["item_id"], project_id=row["project_id"], task_id=row["task_id"], run_id=row["run_id"],
            kind=AttentionKind(row["kind"]), state=AttentionState(row["state"]), urgency=AttentionUrgency(row["urgency"]),
            title=row["title"], body=row["body"], options=json.loads(row["options"]), context=json.loads(row["context"]),
            requester=row["requester"], assignee=row["assignee"], response=row["response"],
            response_action=row["response_action"], responded_by=row["responded_by"],
            cancellation_reason=row["cancellation_reason"], version=int(row["version"]), expires_at=row["expires_at"],
            created_at=row["created_at"], responded_at=row["responded_at"], updated_at=row["updated_at"],
        )
