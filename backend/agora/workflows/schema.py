"""SQLite schema for durable workflow DAG plans."""
from __future__ import annotations

import sqlite3


def initialize_workflow_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS workflows (
            workflow_id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
            state TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}', version INTEGER NOT NULL,
            created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            auto_dispatch INTEGER NOT NULL DEFAULT 0,
            max_concurrent_runs INTEGER NOT NULL DEFAULT 4
        );
        CREATE INDEX IF NOT EXISTS idx_workflows_state_created ON workflows(state, created_at DESC);
        CREATE TABLE IF NOT EXISTS workflow_steps (
            step_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id),
            step_key TEXT NOT NULL, title TEXT NOT NULL, project_id TEXT NOT NULL,
            task_id TEXT REFERENCES tasks(task_id), adapter TEXT NOT NULL, prompt TEXT NOT NULL,
            depends_on TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL, version INTEGER NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            run_id TEXT, dispatch_token TEXT, dispatch_error TEXT,
            UNIQUE(workflow_id, step_key)
        );
        CREATE INDEX IF NOT EXISTS idx_workflow_steps_workflow_state ON workflow_steps(workflow_id, state);
        CREATE TABLE IF NOT EXISTS workflow_events (
            event_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id),
            event_type TEXT NOT NULL, actor TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_workflow_events_workflow ON workflow_events(workflow_id, created_at, event_id);
        """
    )
    columns = {row[1] for row in db.execute("PRAGMA table_info(workflow_steps)")}
    for name in ("run_id", "dispatch_token", "dispatch_error"):
        if name not in columns:
            db.execute(f"ALTER TABLE workflow_steps ADD COLUMN {name} TEXT")
    workflow_columns = {row[1] for row in db.execute("PRAGMA table_info(workflows)")}
    if "auto_dispatch" not in workflow_columns:
        db.execute("ALTER TABLE workflows ADD COLUMN auto_dispatch INTEGER NOT NULL DEFAULT 0")
    if "max_concurrent_runs" not in workflow_columns:
        db.execute("ALTER TABLE workflows ADD COLUMN max_concurrent_runs INTEGER NOT NULL DEFAULT 4")
