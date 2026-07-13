"""SQLite lifecycle and audit store for versioned requirement specs."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agora.tasks.models import TaskState, utc_now
from agora.tasks.store import TaskNotFoundError, TaskStore

from .models import (
    ChangeRequestState,
    CreateSpecRequest,
    RequirementChangeRequest,
    RequirementSpec,
    ReviewChangeRequest,
    SpecContent,
    SpecState,
    SubmitChangeRequest,
    UpdateSpecRequest,
)
from .schema import initialize_requirement_schema


class RequirementNotFoundError(LookupError):
    pass


class RequirementConflictError(ValueError):
    pass


class RequirementValidationError(ValueError):
    pass


class RequirementStore:
    def __init__(self, task_store: TaskStore):
        self.task_store = task_store
        self.db_path = Path(task_store.db_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA journal_mode = WAL")
        return db

    def _initialize(self) -> None:
        with closing(self._connect()) as db:
            initialize_requirement_schema(db)
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

    def create(self, task_id: str, request: CreateSpecRequest) -> RequirementSpec:
        now = utc_now()
        with self._transaction() as db:
            task = self._task_row(db, task_id)
            if TaskState(task["state"]) != TaskState.REQUIREMENTS:
                raise RequirementConflictError("Task must be in requirements state")
            if self._active_row(db, task_id):
                raise RequirementConflictError("Task already has an active requirement spec")
            version = db.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM requirement_specs WHERE task_id = ?",
                (task_id,),
            ).fetchone()[0]
            spec_id = self._new_id("spec")
            content = self._content_from_create(request)
            db.execute(
                """
                INSERT INTO requirement_specs (
                    spec_id, task_id, version, state, title, summary, content,
                    created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    spec_id,
                    task_id,
                    version,
                    SpecState.DRAFT.value,
                    request.title,
                    request.summary,
                    self._json(content),
                    request.created_by,
                    now,
                    now,
                ),
            )
            self._event(
                db,
                task_id,
                "spec.created",
                request.created_by,
                {"spec_id": spec_id, "version": version},
                now,
            )
        return self.require(spec_id)

    def get(self, spec_id: str) -> RequirementSpec | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM requirement_specs WHERE spec_id = ?", (spec_id,)).fetchone()
        return self._spec(row) if row else None

    def require(self, spec_id: str) -> RequirementSpec:
        spec = self.get(spec_id)
        if spec is None:
            raise RequirementNotFoundError(spec_id)
        return spec

    def list_for_task(self, task_id: str) -> list[RequirementSpec]:
        if self.task_store.get(task_id) is None:
            raise TaskNotFoundError(task_id)
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM requirement_specs WHERE task_id = ? ORDER BY version DESC",
                (task_id,),
            ).fetchall()
        return [self._spec(row) for row in rows]

    def current(self, task_id: str) -> RequirementSpec | None:
        if self.task_store.get(task_id) is None:
            raise TaskNotFoundError(task_id)
        with closing(self._connect()) as db:
            row = db.execute(
                """
                SELECT * FROM requirement_specs
                WHERE task_id = ? AND state IN ('draft', 'approved')
                ORDER BY version DESC LIMIT 1
                """,
                (task_id,),
            ).fetchone()
        return self._spec(row) if row else None

    def update(self, spec_id: str, request: UpdateSpecRequest) -> RequirementSpec:
        changes = request.model_dump(exclude_unset=True, mode="json")
        actor = changes.pop("actor", request.actor)
        expected_revision = changes.pop("expected_revision")
        if not changes:
            raise RequirementConflictError("No spec fields were provided")
        now = utc_now()
        with self._transaction() as db:
            row = self._spec_row(db, spec_id)
            if SpecState(row["state"]) != SpecState.DRAFT:
                raise RequirementConflictError("Only draft specs can be updated")
            if row["revision"] != expected_revision:
                raise RequirementConflictError(
                    f"Expected revision {expected_revision}, current revision is {row['revision']}"
                )
            existing = self._spec(row)
            merged = existing.model_dump(
                include={
                    "title",
                    "summary",
                    "functional",
                    "non_functional",
                    "constraints",
                    "acceptance_scenarios",
                    "out_of_scope",
                    "glossary",
                    "assumptions",
                    "open_questions",
                    "links",
                },
                mode="json",
            )
            merged.update(changes)
            try:
                content = SpecContent.model_validate(merged)
            except ValidationError as exc:
                raise RequirementValidationError(str(exc)) from exc
            db.execute(
                """
                UPDATE requirement_specs
                SET title = ?, summary = ?, content = ?, revision = ?, updated_at = ?
                WHERE spec_id = ? AND revision = ?
                """,
                (
                    content.title,
                    content.summary,
                    self._json(self._content_dict(content)),
                    expected_revision + 1,
                    now,
                    spec_id,
                    expected_revision,
                ),
            )
            self._event(
                db,
                row["task_id"],
                "spec.updated",
                actor,
                {"spec_id": spec_id, "fields_changed": sorted(changes)},
                now,
            )
        return self.require(spec_id)

    def approve(
        self,
        spec_id: str,
        *,
        actor: str,
        expected_revision: int,
        reason: str | None = None,
    ) -> RequirementSpec:
        now = utc_now()
        with self._transaction() as db:
            row = self._spec_row(db, spec_id)
            spec = self._spec(row)
            if spec.state != SpecState.DRAFT:
                raise RequirementConflictError("Only draft specs can be approved")
            if spec.revision != expected_revision:
                raise RequirementConflictError(
                    f"Expected revision {expected_revision}, current revision is {spec.revision}"
                )
            task = self._task_row(db, spec.task_id)
            if TaskState(task["state"]) != TaskState.REQUIREMENTS:
                raise RequirementConflictError("Task must be in requirements state for spec approval")
            if not spec.functional and not spec.non_functional:
                raise RequirementConflictError("Spec must contain at least one requirement")
            if not spec.acceptance_scenarios:
                raise RequirementConflictError("Spec must contain at least one acceptance scenario")
            unresolved = [q.question_id for q in spec.open_questions if not (q.resolution or "").strip()]
            if unresolved:
                raise RequirementConflictError(
                    f"Resolve open questions before approval: {', '.join(unresolved)}"
                )
            db.execute(
                """
                UPDATE requirement_specs
                SET state = ?, approved_by = ?, approval_reason = ?, revision = ?, updated_at = ?
                WHERE spec_id = ? AND revision = ?
                """,
                (
                    SpecState.APPROVED.value,
                    actor,
                    reason,
                    expected_revision + 1,
                    now,
                    spec_id,
                    expected_revision,
                ),
            )
            self._event(
                db,
                spec.task_id,
                "spec.approved",
                actor,
                {"spec_id": spec_id, "version": spec.version, "reason": reason},
                now,
            )
        return self.require(spec_id)

    def reject(
        self,
        spec_id: str,
        *,
        actor: str,
        expected_revision: int,
        reason: str,
    ) -> RequirementSpec:
        now = utc_now()
        with self._transaction() as db:
            row = self._spec_row(db, spec_id)
            if SpecState(row["state"]) != SpecState.DRAFT:
                raise RequirementConflictError("Only draft specs can be rejected")
            if row["revision"] != expected_revision:
                raise RequirementConflictError(
                    f"Expected revision {expected_revision}, current revision is {row['revision']}"
                )
            db.execute(
                """
                UPDATE requirement_specs
                SET state = ?, rejected_by = ?, rejection_reason = ?, revision = ?, updated_at = ?
                WHERE spec_id = ? AND revision = ?
                """,
                (
                    SpecState.REJECTED.value,
                    actor,
                    reason,
                    expected_revision + 1,
                    now,
                    spec_id,
                    expected_revision,
                ),
            )
            self._event(
                db,
                row["task_id"],
                "spec.rejected",
                actor,
                {"spec_id": spec_id, "version": row["version"], "reason": reason},
                now,
            )
        return self.require(spec_id)

    def submit_change_request(
        self,
        spec_id: str,
        request: SubmitChangeRequest,
    ) -> RequirementChangeRequest:
        now = utc_now()
        with self._transaction() as db:
            spec = self._spec(self._spec_row(db, spec_id))
            if spec.state != SpecState.APPROVED:
                raise RequirementConflictError("Change requests require an approved spec")
            cr_id = self._new_id("cr")
            db.execute(
                """
                INSERT INTO requirement_change_requests (
                    cr_id, spec_id, task_id, state, title, description, impact_notes,
                    affected_targets, submitted_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cr_id,
                    spec_id,
                    spec.task_id,
                    ChangeRequestState.OPEN.value,
                    request.title,
                    request.description,
                    request.impact_notes,
                    self._json(request.affected_targets),
                    request.submitted_by,
                    now,
                ),
            )
            self._event(
                db,
                spec.task_id,
                "cr.submitted",
                request.submitted_by,
                {"cr_id": cr_id, "spec_id": spec_id},
                now,
            )
        return self.require_change_request(cr_id)

    def review_change_request(
        self,
        cr_id: str,
        request: ReviewChangeRequest,
    ) -> RequirementChangeRequest:
        now = utc_now()
        with self._transaction() as db:
            cr_row = self._cr_row(db, cr_id)
            cr = self._change_request(cr_row)
            if cr.state != ChangeRequestState.OPEN:
                raise RequirementConflictError("Change request is already resolved")
            if request.action == "decline":
                if not (request.reason or "").strip():
                    raise RequirementConflictError("Declining a change request requires a reason")
                db.execute(
                    """
                    UPDATE requirement_change_requests
                    SET state = ?, reviewed_by = ?, review_reason = ?, resolved_at = ?
                    WHERE cr_id = ?
                    """,
                    (ChangeRequestState.DECLINED.value, request.actor, request.reason, now, cr_id),
                )
                self._event(
                    db,
                    cr.task_id,
                    "cr.declined",
                    request.actor,
                    {"cr_id": cr_id, "reason": request.reason},
                    now,
                )
            else:
                spec = self._spec(self._spec_row(db, cr.spec_id))
                if spec.state != SpecState.APPROVED:
                    raise RequirementConflictError("Change request source spec is no longer approved")
                task = self._task_row(db, cr.task_id)
                if TaskState(task["state"]) != TaskState.REQUIREMENTS:
                    raise RequirementConflictError(
                        "Return task to requirements state before accepting a change request"
                    )
                new_spec_id = self._new_id("spec")
                next_version = db.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 FROM requirement_specs WHERE task_id = ?",
                    (cr.task_id,),
                ).fetchone()[0]
                db.execute(
                    "UPDATE requirement_specs SET state = ?, updated_at = ? WHERE spec_id = ?",
                    (SpecState.SUPERSEDED.value, now, spec.spec_id),
                )
                db.execute(
                    """
                    INSERT INTO requirement_specs (
                        spec_id, task_id, version, state, title, summary, content,
                        created_by, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_spec_id,
                        cr.task_id,
                        next_version,
                        SpecState.DRAFT.value,
                        spec.title,
                        spec.summary,
                        self._json(self._content_dict(spec)),
                        request.actor,
                        now,
                        now,
                    ),
                )
                db.execute(
                    """
                    UPDATE requirement_change_requests
                    SET state = ?, reviewed_by = ?, review_reason = ?,
                        resulting_spec_id = ?, resolved_at = ?
                    WHERE cr_id = ?
                    """,
                    (
                        ChangeRequestState.ACCEPTED.value,
                        request.actor,
                        request.reason,
                        new_spec_id,
                        now,
                        cr_id,
                    ),
                )
                self._event(
                    db,
                    cr.task_id,
                    "spec.superseded",
                    request.actor,
                    {"old_spec_id": spec.spec_id, "new_spec_id": new_spec_id, "cr_id": cr_id},
                    now,
                )
                self._event(
                    db,
                    cr.task_id,
                    "cr.accepted",
                    request.actor,
                    {"cr_id": cr_id, "new_spec_id": new_spec_id},
                    now,
                )
        return self.require_change_request(cr_id)

    def require_change_request(self, cr_id: str) -> RequirementChangeRequest:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM requirement_change_requests WHERE cr_id = ?",
                (cr_id,),
            ).fetchone()
        if not row:
            raise RequirementNotFoundError(cr_id)
        return self._change_request(row)

    def list_change_requests(self, spec_id: str) -> list[RequirementChangeRequest]:
        self.require(spec_id)
        with closing(self._connect()) as db:
            rows = db.execute(
                """
                SELECT * FROM requirement_change_requests
                WHERE spec_id = ? ORDER BY created_at, rowid
                """,
                (spec_id,),
            ).fetchall()
        return [self._change_request(row) for row in rows]

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex}"

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _content_dict(content: SpecContent | RequirementSpec) -> dict[str, Any]:
        return content.model_dump(
            include={
                "functional",
                "non_functional",
                "constraints",
                "acceptance_scenarios",
                "out_of_scope",
                "glossary",
                "assumptions",
                "open_questions",
                "links",
            },
            mode="json",
        )

    def _content_from_create(self, request: CreateSpecRequest) -> dict[str, Any]:
        return self._content_dict(request)

    @staticmethod
    def _task_row(db: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            raise TaskNotFoundError(task_id)
        return row

    @staticmethod
    def _spec_row(db: sqlite3.Connection, spec_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM requirement_specs WHERE spec_id = ?", (spec_id,)).fetchone()
        if not row:
            raise RequirementNotFoundError(spec_id)
        return row

    @staticmethod
    def _cr_row(db: sqlite3.Connection, cr_id: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM requirement_change_requests WHERE cr_id = ?", (cr_id,)
        ).fetchone()
        if not row:
            raise RequirementNotFoundError(cr_id)
        return row

    @staticmethod
    def _active_row(db: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
        return db.execute(
            """
            SELECT * FROM requirement_specs
            WHERE task_id = ? AND state IN ('draft', 'approved') LIMIT 1
            """,
            (task_id,),
        ).fetchone()

    @staticmethod
    def _event(
        db: sqlite3.Connection,
        task_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
        created_at: str,
    ) -> None:
        db.execute(
            """
            INSERT INTO task_events (event_id, task_id, event_type, actor, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"evt_{uuid.uuid4().hex}",
                task_id,
                event_type,
                actor,
                RequirementStore._json(payload),
                created_at,
            ),
        )

    @staticmethod
    def _spec(row: sqlite3.Row) -> RequirementSpec:
        content = json.loads(row["content"])
        return RequirementSpec(
            spec_id=row["spec_id"],
            task_id=row["task_id"],
            version=row["version"],
            revision=row["revision"],
            state=SpecState(row["state"]),
            title=row["title"],
            summary=row["summary"],
            created_by=row["created_by"],
            approved_by=row["approved_by"],
            approval_reason=row["approval_reason"],
            rejected_by=row["rejected_by"],
            rejection_reason=row["rejection_reason"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            **content,
        )

    @staticmethod
    def _change_request(row: sqlite3.Row) -> RequirementChangeRequest:
        return RequirementChangeRequest(
            cr_id=row["cr_id"],
            spec_id=row["spec_id"],
            task_id=row["task_id"],
            state=ChangeRequestState(row["state"]),
            title=row["title"],
            description=row["description"],
            impact_notes=row["impact_notes"],
            affected_targets=json.loads(row["affected_targets"]),
            submitted_by=row["submitted_by"],
            reviewed_by=row["reviewed_by"],
            review_reason=row["review_reason"],
            resulting_spec_id=row["resulting_spec_id"],
            created_at=row["created_at"],
            resolved_at=row["resolved_at"],
        )
