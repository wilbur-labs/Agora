"""Transactional persistence and readiness projection for workflow DAGs."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from typing import Any

from agora.tasks.models import utc_now
from agora.tasks.store import TaskStore
from agora.execution.security import redact_text, sanitize_data

from .models import (
    CreateWorkflowRequest, TransitionWorkflowStepRequest, WorkflowActionRequest, WorkflowEvent,
    WorkflowManifest, WorkflowState, WorkflowStep, WorkflowStepState, WorkflowSummary,
)
from .schema import initialize_workflow_schema


class WorkflowNotFoundError(LookupError): pass
class WorkflowValidationError(ValueError): pass
class WorkflowConflictError(RuntimeError): pass


class WorkflowStore:
    def __init__(self, tasks: TaskStore):
        self.tasks = tasks
        with closing(tasks._connect()) as db:
            initialize_workflow_schema(db)
            db.commit()

    def create(self, request: CreateWorkflowRequest) -> WorkflowManifest:
        self._assert_acyclic(request)
        workflow_id, now = self._id("wf"), utc_now()
        step_ids = {step.key: self._id("step") for step in request.steps}
        with self.tasks._transaction() as db:
            for step in request.steps:
                if step.task_id:
                    task = db.execute("SELECT project_id FROM tasks WHERE task_id = ?", (step.task_id,)).fetchone()
                    if not task:
                        raise WorkflowValidationError(f"Unknown task_id for step {step.key}")
                    if task["project_id"] != step.project_id:
                        raise WorkflowValidationError(f"Task project does not match step {step.key}")
            db.execute(
                "INSERT INTO workflows VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
                (workflow_id, redact_text(request.title), redact_text(request.description), WorkflowState.DRAFT.value,
                 self._json(sanitize_data(request.metadata)), request.created_by, now, now),
            )
            for step in request.steps:
                db.execute(
                    """INSERT INTO workflow_steps (
                        step_id, workflow_id, step_key, title, project_id, task_id, adapter, prompt,
                        depends_on, state, version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                    (step_ids[step.key], workflow_id, step.key, redact_text(step.title), step.project_id,
                     step.task_id, step.adapter, redact_text(step.prompt),
                     self._json([step_ids[key] for key in step.depends_on]),
                     WorkflowStepState.PENDING.value, now, now),
                )
            self._event(db, workflow_id, "workflow.created", request.created_by,
                        {"step_count": len(request.steps)}, now)
        return self.require(workflow_id)

    def get(self, workflow_id: str) -> WorkflowManifest | None:
        with closing(self.tasks._connect()) as db:
            row = db.execute("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)).fetchone()
            if not row: return None
            steps = db.execute("SELECT * FROM workflow_steps WHERE workflow_id = ? ORDER BY rowid", (workflow_id,)).fetchall()
        return self._manifest(row, steps)

    def require(self, workflow_id: str) -> WorkflowManifest:
        item = self.get(workflow_id)
        if not item: raise WorkflowNotFoundError(workflow_id)
        return item

    def list(self, *, state: WorkflowState | None = None, project_id: str | None = None,
             limit: int = 100, offset: int = 0) -> list[WorkflowSummary]:
        clauses, values = [], []
        if state: clauses.append("w.state = ?"); values.append(state.value)
        if project_id:
            clauses.append("EXISTS (SELECT 1 FROM workflow_steps p WHERE p.workflow_id=w.workflow_id AND p.project_id=?)")
            values.append(project_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.extend([limit, offset])
        with closing(self.tasks._connect()) as db:
            rows = db.execute(f"""SELECT w.*, COUNT(s.step_id) step_count,
                SUM(CASE WHEN s.state='ready' THEN 1 ELSE 0 END) ready_count
                FROM workflows w JOIN workflow_steps s ON s.workflow_id=w.workflow_id {where}
                GROUP BY w.workflow_id ORDER BY w.created_at DESC LIMIT ? OFFSET ?""", values).fetchall()
        return [WorkflowSummary(workflow_id=r["workflow_id"], title=r["title"], state=r["state"],
            step_count=r["step_count"], ready_count=r["ready_count"], version=r["version"],
            created_at=r["created_at"], updated_at=r["updated_at"]) for r in rows]

    def activate(self, workflow_id: str, request: WorkflowActionRequest) -> WorkflowManifest:
        now = utc_now()
        with self.tasks._transaction() as db:
            row = self._locked(db, workflow_id, request.expected_version)
            if row["state"] != WorkflowState.DRAFT.value: raise WorkflowConflictError("Only draft workflows may be activated")
            roots = db.execute("SELECT step_id FROM workflow_steps WHERE workflow_id=? AND depends_on='[]'", (workflow_id,)).fetchall()
            root_ids = [r["step_id"] for r in roots]
            if root_ids:
                db.executemany("UPDATE workflow_steps SET state='ready', version=version+1, updated_at=? WHERE step_id=?", [(now, i) for i in root_ids])
            db.execute("UPDATE workflows SET state='active', version=version+1, updated_at=? WHERE workflow_id=?", (now, workflow_id))
            self._event(db, workflow_id, "workflow.activated", request.actor, {"ready_steps": root_ids}, now)
        return self.require(workflow_id)

    def transition_step(self, workflow_id: str, step_id: str, request: TransitionWorkflowStepRequest) -> WorkflowManifest:
        allowed = {WorkflowStepState.READY: {WorkflowStepState.RUNNING},
                   WorkflowStepState.RUNNING: {WorkflowStepState.SUCCEEDED, WorkflowStepState.FAILED}}
        now = utc_now()
        with self.tasks._transaction() as db:
            workflow = db.execute("SELECT * FROM workflows WHERE workflow_id=?", (workflow_id,)).fetchone()
            if not workflow: raise WorkflowNotFoundError(workflow_id)
            if workflow["state"] != WorkflowState.ACTIVE.value: raise WorkflowConflictError("Workflow is not active")
            step = db.execute("SELECT * FROM workflow_steps WHERE workflow_id=? AND step_id=?", (workflow_id, step_id)).fetchone()
            if not step: raise WorkflowNotFoundError(step_id)
            if step["version"] != request.expected_version: raise WorkflowConflictError("Step version changed")
            current = WorkflowStepState(step["state"])
            if request.target_state not in allowed.get(current, set()):
                raise WorkflowConflictError(f"Cannot transition step from {current.value} to {request.target_state.value}")
            db.execute("UPDATE workflow_steps SET state=?, version=version+1, updated_at=? WHERE step_id=?",
                       (request.target_state.value, now, step_id))
            promoted: list[str] = []
            if request.target_state == WorkflowStepState.SUCCEEDED:
                pending = db.execute("SELECT step_id, depends_on FROM workflow_steps WHERE workflow_id=? AND state='pending'", (workflow_id,)).fetchall()
                succeeded = {r["step_id"] for r in db.execute("SELECT step_id FROM workflow_steps WHERE workflow_id=? AND state='succeeded'", (workflow_id,))}
                for candidate in pending:
                    if set(json.loads(candidate["depends_on"])).issubset(succeeded): promoted.append(candidate["step_id"])
                db.executemany("UPDATE workflow_steps SET state='ready', version=version+1, updated_at=? WHERE step_id=?", [(now, i) for i in promoted])
            terminal = None
            if request.target_state == WorkflowStepState.FAILED:
                terminal = WorkflowState.FAILED
                db.execute(
                    """UPDATE workflow_steps SET state='cancelled', version=version+1, updated_at=?
                       WHERE workflow_id=? AND step_id!=? AND state IN ('pending','ready','running')""",
                    (now, workflow_id, step_id),
                )
            nonterminal = db.execute(
                "SELECT COUNT(*) FROM workflow_steps WHERE workflow_id=? AND state IN ('pending','ready','running')",
                (workflow_id,),
            ).fetchone()[0]
            failed = db.execute(
                "SELECT COUNT(*) FROM workflow_steps WHERE workflow_id=? AND state='failed'", (workflow_id,)
            ).fetchone()[0]
            if nonterminal == 0 and failed == 0: terminal = WorkflowState.COMPLETED
            db.execute("UPDATE workflows SET state=COALESCE(?, state), version=version+1, updated_at=? WHERE workflow_id=?",
                       (terminal.value if terminal else None, now, workflow_id))
            self._event(db, workflow_id, "workflow.step_transitioned", request.actor,
                        {"step_id": step_id, "from": current.value, "to": request.target_state.value,
                         "promoted": promoted, "reason": redact_text(request.reason) if request.reason else None}, now)
            if terminal: self._event(db, workflow_id, f"workflow.{terminal.value}", request.actor, {}, now)
        return self.require(workflow_id)

    def cancel(self, workflow_id: str, request: WorkflowActionRequest) -> WorkflowManifest:
        now = utc_now()
        with self.tasks._transaction() as db:
            row = self._locked(db, workflow_id, request.expected_version)
            if row["state"] in {s.value for s in (WorkflowState.COMPLETED, WorkflowState.FAILED, WorkflowState.CANCELLED)}:
                raise WorkflowConflictError("Workflow is terminal")
            db.execute("UPDATE workflows SET state='cancelled', version=version+1, updated_at=? WHERE workflow_id=?", (now, workflow_id))
            db.execute("UPDATE workflow_steps SET state='cancelled', version=version+1, updated_at=? WHERE workflow_id=? AND state IN ('pending','ready','running')", (now, workflow_id))
            self._event(db, workflow_id, "workflow.cancelled", request.actor,
                        {"reason": redact_text(request.reason) if request.reason else None}, now)
        return self.require(workflow_id)

    def claim_dispatch(self, workflow_id: str, step_id: str, *, expected_version: int, token: str) -> WorkflowStep:
        now = utc_now()
        with self.tasks._transaction() as db:
            workflow = db.execute("SELECT state FROM workflows WHERE workflow_id=?", (workflow_id,)).fetchone()
            if not workflow: raise WorkflowNotFoundError(workflow_id)
            if workflow["state"] != WorkflowState.ACTIVE.value: raise WorkflowConflictError("Workflow is not active")
            cursor = db.execute(
                """UPDATE workflow_steps SET state='running', dispatch_token=?, dispatch_error=NULL,
                   version=version+1, updated_at=?
                   WHERE workflow_id=? AND step_id=? AND state='ready' AND version=? AND run_id IS NULL""",
                (token, now, workflow_id, step_id, expected_version),
            )
            if cursor.rowcount != 1: raise WorkflowConflictError("Workflow step is no longer dispatchable")
            db.execute("UPDATE workflows SET version=version+1, updated_at=? WHERE workflow_id=?", (now, workflow_id))
            self._event(db, workflow_id, "workflow.step_claimed", "scheduler", {"step_id": step_id}, now)
        return next(step for step in self.require(workflow_id).steps if step.step_id == step_id)

    def bind_run(self, workflow_id: str, step_id: str, *, token: str, run_id: str) -> WorkflowStep:
        now = utc_now()
        with self.tasks._transaction() as db:
            cursor = db.execute(
                """UPDATE workflow_steps SET run_id=?, dispatch_token=NULL, version=version+1, updated_at=?
                   WHERE workflow_id=? AND step_id=? AND state='running' AND dispatch_token=? AND run_id IS NULL""",
                (run_id, now, workflow_id, step_id, token),
            )
            if cursor.rowcount != 1: raise WorkflowConflictError("Workflow dispatch claim was lost")
            db.execute("UPDATE workflows SET version=version+1, updated_at=? WHERE workflow_id=?", (now, workflow_id))
            self._event(db, workflow_id, "workflow.run_bound", "scheduler", {"step_id": step_id, "run_id": run_id}, now)
        return next(step for step in self.require(workflow_id).steps if step.step_id == step_id)

    def release_dispatch(self, workflow_id: str, step_id: str, *, token: str, error: str) -> WorkflowStep:
        now, safe_error = utc_now(), redact_text(error)[:1000]
        with self.tasks._transaction() as db:
            cursor = db.execute(
                """UPDATE workflow_steps SET state='ready', dispatch_token=NULL, dispatch_error=?,
                   version=version+1, updated_at=?
                   WHERE workflow_id=? AND step_id=? AND state='running' AND dispatch_token=? AND run_id IS NULL""",
                (safe_error, now, workflow_id, step_id, token),
            )
            if cursor.rowcount != 1: raise WorkflowConflictError("Workflow dispatch claim was lost")
            db.execute("UPDATE workflows SET version=version+1, updated_at=? WHERE workflow_id=?", (now, workflow_id))
            self._event(db, workflow_id, "workflow.dispatch_blocked", "scheduler",
                        {"step_id": step_id, "reason": safe_error}, now)
        return next(step for step in self.require(workflow_id).steps if step.step_id == step_id)

    def record_blocker(self, workflow_id: str, step_id: str, *, expected_version: int, error: str) -> WorkflowStep:
        now, safe_error = utc_now(), redact_text(error)[:1000]
        with self.tasks._transaction() as db:
            cursor = db.execute(
                """UPDATE workflow_steps SET dispatch_error=?, version=version+1, updated_at=?
                   WHERE workflow_id=? AND step_id=? AND state='ready' AND version=?
                     AND COALESCE(dispatch_error, '') != ?""",
                (safe_error, now, workflow_id, step_id, expected_version, safe_error),
            )
            if cursor.rowcount:
                db.execute("UPDATE workflows SET version=version+1, updated_at=? WHERE workflow_id=?", (now, workflow_id))
                self._event(db, workflow_id, "workflow.dispatch_blocked", "scheduler",
                            {"step_id": step_id, "reason": safe_error}, now)
        return next(step for step in self.require(workflow_id).steps if step.step_id == step_id)

    def record_binding_error(self, workflow_id: str, step_id: str, *, token: str, error: str) -> WorkflowStep:
        now, safe_error = utc_now(), redact_text(error)[:1000]
        with self.tasks._transaction() as db:
            cursor = db.execute(
                """UPDATE workflow_steps SET dispatch_error=?, version=version+1, updated_at=?
                   WHERE workflow_id=? AND step_id=? AND state='running' AND dispatch_token=? AND run_id IS NULL""",
                (safe_error, now, workflow_id, step_id, token),
            )
            if cursor.rowcount:
                db.execute("UPDATE workflows SET version=version+1, updated_at=? WHERE workflow_id=?", (now, workflow_id))
                self._event(db, workflow_id, "workflow.run_binding_failed", "scheduler",
                            {"step_id": step_id, "reason": safe_error}, now)
        return next(step for step in self.require(workflow_id).steps if step.step_id == step_id)

    def record_scheduler_error(self, workflow_id: str, step_id: str, *, error: str) -> None:
        now, safe_error = utc_now(), redact_text(error)[:1000]
        with self.tasks._transaction() as db:
            if not db.execute("SELECT 1 FROM workflows WHERE workflow_id=?", (workflow_id,)).fetchone():
                raise WorkflowNotFoundError(workflow_id)
            self._event(db, workflow_id, "workflow.scheduler_error", "scheduler",
                        {"step_id": step_id, "reason": safe_error}, now)

    def events(self, workflow_id: str) -> list[WorkflowEvent]:
        self.require(workflow_id)
        with closing(self.tasks._connect()) as db:
            rows = db.execute("SELECT * FROM workflow_events WHERE workflow_id=? ORDER BY rowid", (workflow_id,)).fetchall()
        return [WorkflowEvent(event_id=r["event_id"], workflow_id=r["workflow_id"], event_type=r["event_type"], actor=r["actor"], payload=json.loads(r["payload"]), created_at=r["created_at"]) for r in rows]

    def _locked(self, db: sqlite3.Connection, workflow_id: str, expected: int):
        row = db.execute("SELECT * FROM workflows WHERE workflow_id=?", (workflow_id,)).fetchone()
        if not row: raise WorkflowNotFoundError(workflow_id)
        if row["version"] != expected: raise WorkflowConflictError("Workflow version changed")
        return row

    @staticmethod
    def _assert_acyclic(request: CreateWorkflowRequest) -> None:
        graph = {s.key: set(s.depends_on) for s in request.steps}
        ready = [key for key, deps in graph.items() if not deps]
        visited = 0
        while ready:
            done = ready.pop(); visited += 1
            for key, deps in graph.items():
                if done in deps:
                    deps.remove(done)
                    if not deps: ready.append(key)
        if visited != len(graph): raise WorkflowValidationError("Workflow dependencies contain a cycle")

    @staticmethod
    def _event(db, workflow_id, event_type, actor, payload, now):
        db.execute("INSERT INTO workflow_events VALUES (?, ?, ?, ?, ?, ?)",
                   (WorkflowStore._id("wevt"), workflow_id, event_type, actor, WorkflowStore._json(payload), now))

    @staticmethod
    def _manifest(row, steps) -> WorkflowManifest:
        return WorkflowManifest(workflow_id=row["workflow_id"], title=row["title"], description=row["description"],
            state=row["state"], steps=[WorkflowStep(step_id=s["step_id"], workflow_id=s["workflow_id"], key=s["step_key"],
                title=s["title"], project_id=s["project_id"], task_id=s["task_id"], adapter=s["adapter"], prompt=s["prompt"],
                depends_on=json.loads(s["depends_on"]), state=s["state"], version=s["version"], created_at=s["created_at"], updated_at=s["updated_at"],
                run_id=s["run_id"], dispatch_token=s["dispatch_token"], dispatch_error=s["dispatch_error"]) for s in steps],
            metadata=json.loads(row["metadata"]), version=row["version"], created_by=row["created_by"], created_at=row["created_at"], updated_at=row["updated_at"])

    @staticmethod
    def _id(prefix): return f"{prefix}_{uuid.uuid4().hex}"
    @staticmethod
    def _json(value): return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
