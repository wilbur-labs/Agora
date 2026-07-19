"""SQLite persistence and lifecycle rules for human-attention items."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
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
from .bridges.models import (
    BridgeDelivery, BridgeEventReceipt, BridgeEventRequest, BridgeVendor, DeliveryMode, DeliveryState,
)


class AttentionNotFoundError(LookupError):
    pass


class AttentionConflictError(RuntimeError):
    pass


class AttentionValidationError(ValueError):
    pass


class AttentionStore:
    MAX_OPEN_BRIDGE_ITEMS_PER_RUN = 50
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

    def create_bridge_event(
        self, request: BridgeEventRequest, *, trusted_bidirectional: bool = False,
    ) -> BridgeEventReceipt:
        """Atomically deduplicate a vendor event and create its attention item."""
        if request.delivery_mode == DeliveryMode.BIDIRECTIONAL and not trusted_bidirectional:
            raise AttentionValidationError("Public hook ingestion supports capture_only events")
        now = utc_now()
        item_id = f"attn_{uuid.uuid4().hex}"
        with self._transaction() as db:
            existing = db.execute(
                """SELECT item_id, delivery_mode FROM attention_bridge_events
                   WHERE vendor = ? AND run_id = ? AND vendor_event_id = ?""",
                (request.vendor.value, request.run_id, request.vendor_event_id),
            ).fetchone()
            if existing:
                return BridgeEventReceipt(
                    item_id=existing["item_id"], created=False, delivery_mode=existing["delivery_mode"]
                )
            run = db.execute(
                "SELECT task_id, project_id, state FROM execution_runs WHERE run_id = ?", (request.run_id,)
            ).fetchone()
            if run is None:
                raise AttentionNotFoundError("Run not found")
            if run["task_id"] != request.task_id:
                raise AttentionValidationError("Run does not belong to the requested task")
            if run["state"] not in {"queued", "running"}:
                raise AttentionValidationError("Bridge events require an active run")
            open_count = db.execute(
                "SELECT COUNT(*) FROM attention_items WHERE run_id = ? AND state = 'open'",
                (request.run_id,),
            ).fetchone()[0]
            if int(open_count) >= self.MAX_OPEN_BRIDGE_ITEMS_PER_RUN:
                raise AttentionValidationError("Run has too many open attention items")
            context = sanitize_data({
                "bridge": {
                    "vendor": request.vendor.value,
                    "vendor_event_id": request.vendor_event_id,
                    "delivery_mode": request.delivery_mode.value,
                    "correlation": request.correlation,
                }
            })
            db.execute(
                """INSERT INTO attention_items (
                    item_id, project_id, task_id, run_id, kind, state, urgency, title, body,
                    options, context, requester, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (item_id, run["project_id"], request.task_id, request.run_id, request.kind.value,
                 request.urgency.value, redact_text(request.title.strip()), redact_text(request.body),
                 self._json(request.options), self._json(context), request.requester, now, now),
            )
            db.execute(
                """INSERT INTO attention_bridge_events
                   (vendor, run_id, vendor_event_id, item_id, delivery_mode, received_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (request.vendor.value, request.run_id, request.vendor_event_id, item_id,
                 request.delivery_mode.value, now),
            )
            self._event(db, request.task_id, "attention.bridge_captured", request.requester,
                        {"item_id": item_id, "vendor": request.vendor.value,
                         "delivery_mode": request.delivery_mode.value}, now)
        return BridgeEventReceipt(item_id=item_id, created=True, delivery_mode=request.delivery_mode)

    def claim_ready_delivery(self, run_id: str, vendor: BridgeVendor) -> BridgeDelivery | None:
        """Claim one answered bridge item exactly once for structured delivery."""
        with self._transaction() as db:
            row = db.execute(
                """SELECT b.*, a.response_action, a.response, a.context
                   FROM attention_bridge_events b JOIN attention_items a ON a.item_id = b.item_id
                   WHERE b.run_id = ? AND b.vendor = ? AND b.delivery_mode = 'bidirectional'
                     AND b.delivery_state = 'ready'
                   ORDER BY b.received_at LIMIT 1""",
                (run_id, vendor.value),
            ).fetchone()
            if row is None:
                return None
            cursor = db.execute(
                """UPDATE attention_bridge_events SET delivery_state = 'delivering', delivery_error = NULL,
                   claimed_at = ?
                   WHERE item_id = ? AND delivery_state = 'ready'""",
                (utc_now(), row["item_id"]),
            )
            if cursor.rowcount != 1:
                return None
            context = json.loads(row["context"])
            return BridgeDelivery(
                item_id=row["item_id"], run_id=row["run_id"], vendor=BridgeVendor(row["vendor"]),
                vendor_event_id=row["vendor_event_id"], delivery_state=DeliveryState.DELIVERING,
                response_action=row["response_action"], response=row["response"] or "",
                correlation=context["bridge"]["correlation"],
            )

    def finish_delivery(self, item_id: str, *, delivered: bool, error: str | None = None) -> None:
        now = utc_now()
        state = DeliveryState.DELIVERED if delivered else DeliveryState.FAILED
        safe_error = redact_text(error) if error else None
        with self._transaction() as db:
            row = db.execute(
                """SELECT b.delivery_state, a.task_id FROM attention_bridge_events b
                   JOIN attention_items a ON a.item_id = b.item_id WHERE b.item_id = ?""",
                (item_id,),
            ).fetchone()
            if row is None:
                raise AttentionNotFoundError("Bridge delivery not found")
            if row["delivery_state"] != DeliveryState.DELIVERING.value:
                raise AttentionConflictError("Bridge delivery is not claimed")
            db.execute(
                """UPDATE attention_bridge_events SET delivery_state = ?, delivery_error = ?, delivered_at = ?
                   WHERE item_id = ? AND delivery_state = 'delivering'""",
                (state.value, safe_error, now if delivered else None, item_id),
            )
            self._event(db, row["task_id"], f"attention.delivery_{state.value}", "bridge",
                        {"item_id": item_id, "error": safe_error}, now)

    def recover_stale_deliveries(self, *, max_age_seconds: int = 60) -> int:
        if max_age_seconds < 1:
            raise ValueError("max_age_seconds must be positive")
        now = utc_now()
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)).isoformat()
        with self._transaction() as db:
            rows = db.execute(
                """SELECT b.item_id, a.task_id FROM attention_bridge_events b
                   JOIN attention_items a ON a.item_id = b.item_id
                   WHERE b.delivery_state = 'delivering' AND b.claimed_at IS NOT NULL AND b.claimed_at <= ?""",
                (cutoff,),
            ).fetchall()
            for row in rows:
                db.execute(
                    """UPDATE attention_bridge_events SET delivery_state = 'failed',
                       delivery_error = 'delivery interrupted before acknowledgement'
                       WHERE item_id = ? AND delivery_state = 'delivering'""",
                    (row["item_id"],),
                )
                self._event(db, row["task_id"], "attention.delivery_failed", "system",
                            {"item_id": row["item_id"], "error": "delivery interrupted before acknowledgement"}, now)
            return len(rows)

    def delivery_failure_for_run(self, run_id: str, vendor: BridgeVendor) -> str | None:
        with closing(self._connect()) as db:
            row = db.execute(
                """SELECT delivery_error FROM attention_bridge_events
                   WHERE run_id = ? AND vendor = ? AND delivery_mode = 'bidirectional'
                     AND delivery_state = 'failed'
                   ORDER BY received_at DESC LIMIT 1""",
                (run_id, vendor.value),
            ).fetchone()
        return row["delivery_error"] if row else None

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
        with closing(self._connect()) as db:
            return self.list_snapshot(
                db,
                project_id=project_id,
                task_id=task_id,
                run_id=run_id,
                state=state,
                kind=kind,
                limit=limit,
                offset=offset,
            )

    @classmethod
    def list_snapshot(
        cls,
        db: sqlite3.Connection,
        *,
        project_id: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
        state: AttentionState | None = None,
        kind: AttentionKind | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AttentionItem]:
        """List Attention from a caller-owned read snapshot without mutation."""
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
        rows = db.execute(
            f"SELECT * FROM attention_items{where} ORDER BY CASE state WHEN 'open' THEN 0 ELSE 1 END, {urgency_order}, created_at DESC LIMIT ? OFFSET ?",
            (*values, limit, offset),
        ).fetchall()
        return [cls._item(row) for row in rows]

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
                bridge = db.execute(
                    "SELECT delivery_mode FROM attention_bridge_events WHERE item_id = ?", (item_id,)
                ).fetchone()
                if (bridge and bridge["delivery_mode"] == DeliveryMode.BIDIRECTIONAL.value
                        and row["kind"] == "approval"
                        and request.action.value not in {"approve", "reject"}):
                    raise AttentionValidationError("Bidirectional approvals require approve or reject")
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
                db.execute(
                    """UPDATE attention_bridge_events SET delivery_state = 'ready'
                       WHERE item_id = ? AND delivery_mode = 'bidirectional' AND delivery_state = 'pending'""",
                    (item_id,),
                )
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
                self._fail_undelivered_bridge(db, row, now, "attention item cancelled")
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
        self._fail_undelivered_bridge(db, row, now, "attention item expired")
        return True

    def _fail_undelivered_bridge(
        self, db: sqlite3.Connection, row: sqlite3.Row, now: str, reason: str,
    ) -> None:
        cursor = db.execute(
            """UPDATE attention_bridge_events SET delivery_state = 'failed', delivery_error = ?
               WHERE item_id = ? AND delivery_mode = 'bidirectional'
                 AND delivery_state IN ('pending', 'ready')""",
            (reason, row["item_id"]),
        )
        if cursor.rowcount:
            self._event(db, row["task_id"], "attention.delivery_failed", "system",
                        {"item_id": row["item_id"], "error": reason}, now)

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
