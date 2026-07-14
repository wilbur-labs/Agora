"""SQLite schema for durable human-attention items."""
from __future__ import annotations

import sqlite3


def initialize_attention_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS attention_items (
            item_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            run_id TEXT REFERENCES execution_runs(run_id),
            kind TEXT NOT NULL,
            state TEXT NOT NULL,
            urgency TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            options TEXT NOT NULL DEFAULT '[]',
            context TEXT NOT NULL DEFAULT '{}',
            requester TEXT NOT NULL,
            assignee TEXT,
            response TEXT,
            response_action TEXT,
            responded_by TEXT,
            cancellation_reason TEXT,
            version INTEGER NOT NULL DEFAULT 1,
            expires_at TEXT,
            created_at TEXT NOT NULL,
            responded_at TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_attention_project_state
            ON attention_items(project_id, state, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_attention_task_created
            ON attention_items(task_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_attention_run
            ON attention_items(run_id) WHERE run_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_attention_state_urgency
            ON attention_items(state, urgency, created_at DESC);

        CREATE TABLE IF NOT EXISTS attention_bridge_events (
            vendor TEXT NOT NULL,
            run_id TEXT NOT NULL REFERENCES execution_runs(run_id),
            vendor_event_id TEXT NOT NULL,
            item_id TEXT NOT NULL UNIQUE REFERENCES attention_items(item_id),
            delivery_mode TEXT NOT NULL,
            received_at TEXT NOT NULL,
            PRIMARY KEY (vendor, run_id, vendor_event_id)
        );
        CREATE INDEX IF NOT EXISTS idx_attention_bridge_item
            ON attention_bridge_events(item_id);
        """
    )
