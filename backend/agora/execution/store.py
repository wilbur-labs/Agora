"""SQLite persistence and lifecycle invariants for execution runs."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator

from agora.tasks.models import TaskState, utc_now
from agora.tasks.store import TaskStore

from .models import (
    CancelRunRequest,
    CreateRunRequest,
    ExecutionRun,
    RunState,
    RunSummary,
    TERMINAL_RUN_STATES,
    OUTPUT_TAIL_LIMIT,
)
from .schema import initialize_execution_schema
from .security import redact_text, sanitize_data


class RunNotFoundError(LookupError):
    pass


class RunConflictError(RuntimeError):
    pass


class RunValidationError(ValueError):
    pass


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
    ) -> ExecutionRun:
        now = utc_now()
        run_id = f"run_{uuid.uuid4().hex}"
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
            if state == TaskState.PLANNED:
                next_version = current_version + 1
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
                    self._json(sanitize_data(request.metadata)),
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
        adapter: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RunSummary]:
        clauses: list[str] = []
        values: list[Any] = []
        for column, value in (("task_id", task_id), ("project_id", project_id), ("adapter", adapter)):
            if value:
                clauses.append(f"{column} = ?")
                values.append(value)
        if state:
            clauses.append("state = ?")
            values.append(state.value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        values.extend([limit, offset])
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT * FROM execution_runs{where} ORDER BY queued_at DESC, rowid DESC LIMIT ? OFFSET ?",
                values,
            ).fetchall()
        return [self._summary(row) for row in rows]

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
    ) -> ExecutionRun:
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
            event_payload={"exit_code": exit_code, "error_message": error_message},
        )

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
    ) -> ExecutionRun:
        with self._transaction() as db:
            row = db.execute("SELECT * FROM execution_runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                raise RunNotFoundError(run_id)
            cursor = db.execute(
                """
                UPDATE execution_runs
                SET stdout_tail = ?, stderr_tail = ?, exit_code = ?, version = version + 1
                WHERE run_id = ? AND state = ? AND version = ?
                """,
                (
                    redact_text(stdout_tail)[-OUTPUT_TAIL_LIMIT:],
                    redact_text(stderr_tail)[-OUTPUT_TAIL_LIMIT:],
                    exit_code,
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

    def abandon(self, run_id: str, *, expected_version: int, reason: str) -> ExecutionRun:
        return self._transition(
            run_id,
            RunState.ABANDONED,
            expected_version=expected_version,
            error_message=reason,
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
        return self.require(run_id)

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
