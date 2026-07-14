"""SQLite schema for delivery execution runs."""
from __future__ import annotations

import sqlite3


def initialize_execution_schema(db: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE IF NOT EXISTS execution_runs (
            run_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            project_id TEXT NOT NULL,
            adapter TEXT NOT NULL,
            state TEXT NOT NULL,
            prompt TEXT NOT NULL,
            workspace TEXT NOT NULL,
            command TEXT NOT NULL DEFAULT '[]',
            timeout_seconds INTEGER NOT NULL,
            pid INTEGER,
            exit_code INTEGER,
            stdout_tail TEXT NOT NULL DEFAULT '',
            stderr_tail TEXT NOT NULL DEFAULT '',
            result_metadata TEXT NOT NULL DEFAULT '{}',
            error_message TEXT,
            version INTEGER NOT NULL DEFAULT 1,
            queued_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            actor TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_execution_runs_task
            ON execution_runs(task_id, queued_at DESC)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_execution_runs_project_state
            ON execution_runs(project_id, state)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_execution_runs_state
            ON execution_runs(state)
        """,
    )
    for statement in statements:
        db.execute(statement)
