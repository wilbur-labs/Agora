"""Additive SQLite schema shared by task and requirement stores."""
from __future__ import annotations

import sqlite3


def initialize_requirement_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS requirement_specs (
            spec_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            version INTEGER NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1,
            state TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '{}',
            created_by TEXT NOT NULL,
            approved_by TEXT,
            approval_reason TEXT,
            rejected_by TEXT,
            rejection_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(task_id, version)
        );
        CREATE INDEX IF NOT EXISTS idx_requirement_specs_task_version
            ON requirement_specs(task_id, version DESC);
        CREATE INDEX IF NOT EXISTS idx_requirement_specs_state
            ON requirement_specs(state);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_requirement_specs_one_active
            ON requirement_specs(task_id)
            WHERE state IN ('draft', 'approved');

        CREATE TABLE IF NOT EXISTS requirement_change_requests (
            cr_id TEXT PRIMARY KEY,
            spec_id TEXT NOT NULL REFERENCES requirement_specs(spec_id),
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            state TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            impact_notes TEXT NOT NULL DEFAULT '',
            affected_targets TEXT NOT NULL DEFAULT '[]',
            submitted_by TEXT NOT NULL,
            reviewed_by TEXT,
            review_reason TEXT,
            resulting_spec_id TEXT REFERENCES requirement_specs(spec_id),
            created_at TEXT NOT NULL,
            resolved_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_requirement_cr_spec
            ON requirement_change_requests(spec_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_requirement_cr_task
            ON requirement_change_requests(task_id, created_at);
        """
    )
