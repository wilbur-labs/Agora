"""Additive SQLite schema for the Agora Control Plane v2 registry."""
from __future__ import annotations

import sqlite3


def initialize_control_plane_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS control_stages (
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            project_id TEXT NOT NULL,
            stage_key TEXT NOT NULL,
            gate_key TEXT NOT NULL,
            status TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (task_id, stage_key),
            UNIQUE (task_id, gate_key)
        );
        CREATE INDEX IF NOT EXISTS idx_control_stages_project_status
            ON control_stages(project_id, status, updated_at DESC);

        CREATE TABLE IF NOT EXISTS control_gates (
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            project_id TEXT NOT NULL,
            gate_key TEXT NOT NULL,
            stage_key TEXT NOT NULL,
            status TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            last_evaluation TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (task_id, gate_key),
            FOREIGN KEY (task_id, stage_key)
                REFERENCES control_stages(task_id, stage_key)
        );
        CREATE INDEX IF NOT EXISTS idx_control_gates_project_status
            ON control_gates(project_id, status, updated_at DESC);

        CREATE TABLE IF NOT EXISTS control_gate_requirements (
            task_id TEXT NOT NULL,
            gate_key TEXT NOT NULL,
            requirement_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (task_id, gate_key, requirement_id),
            FOREIGN KEY (task_id, gate_key)
                REFERENCES control_gates(task_id, gate_key)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS protocol_artifacts (
            artifact_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            project_id TEXT NOT NULL,
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            stage_key TEXT NOT NULL,
            run_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            storage TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            repository_id TEXT,
            ref TEXT,
            commit_sha TEXT,
            path TEXT,
            payload TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (artifact_id, version)
        );
        CREATE INDEX IF NOT EXISTS idx_protocol_artifacts_task_stage
            ON protocol_artifacts(task_id, stage_key, created_at);
        CREATE INDEX IF NOT EXISTS idx_protocol_artifacts_location
            ON protocol_artifacts(repository_id, ref, commit_sha, path)
            WHERE repository_id IS NOT NULL;

        CREATE TABLE IF NOT EXISTS protocol_evidence (
            evidence_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            stage_key TEXT NOT NULL,
            run_id TEXT NOT NULL,
            repository_id TEXT NOT NULL,
            ref TEXT NOT NULL,
            commit_sha TEXT NOT NULL,
            requirement_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            payload TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            UNIQUE (task_id, evidence_id)
        );
        CREATE INDEX IF NOT EXISTS idx_protocol_evidence_scope
            ON protocol_evidence(
                task_id, repository_id, ref, commit_sha, requirement_id, kind
            );

        CREATE TABLE IF NOT EXISTS control_gate_evidence (
            task_id TEXT NOT NULL,
            gate_key TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            requirement_id TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            activated_at TEXT NOT NULL,
            deactivated_at TEXT,
            PRIMARY KEY (task_id, gate_key, evidence_id),
            FOREIGN KEY (task_id, gate_key)
                REFERENCES control_gates(task_id, gate_key)
                ON DELETE CASCADE,
            FOREIGN KEY (task_id, evidence_id)
                REFERENCES protocol_evidence(task_id, evidence_id)
        );
        CREATE INDEX IF NOT EXISTS idx_control_gate_evidence_active
            ON control_gate_evidence(task_id, gate_key, active, requirement_id);

        CREATE TABLE IF NOT EXISTS protocol_approvals (
            approval_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            stage_key TEXT NOT NULL,
            gate_key TEXT NOT NULL,
            repository_id TEXT NOT NULL,
            ref TEXT NOT NULL,
            commit_sha TEXT NOT NULL,
            status TEXT NOT NULL,
            payload TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            approved_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (task_id, gate_key)
                REFERENCES control_gates(task_id, gate_key)
        );
        CREATE INDEX IF NOT EXISTS idx_protocol_approvals_scope_status
            ON protocol_approvals(repository_id, ref, status, task_id);

        CREATE TABLE IF NOT EXISTS protocol_approval_artifacts (
            approval_id TEXT NOT NULL REFERENCES protocol_approvals(approval_id)
                ON DELETE CASCADE,
            repository_id TEXT NOT NULL,
            ref TEXT NOT NULL,
            commit_sha TEXT NOT NULL,
            path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            PRIMARY KEY (approval_id, path)
        );
        CREATE INDEX IF NOT EXISTS idx_protocol_approval_artifacts_scope
            ON protocol_approval_artifacts(repository_id, ref, path, sha256);

        CREATE TABLE IF NOT EXISTS control_events (
            event_id TEXT PRIMARY KEY,
            event_key TEXT NOT NULL UNIQUE,
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            project_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            actor TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_control_events_task_created
            ON control_events(task_id, created_at, event_id);

        CREATE TABLE IF NOT EXISTS control_operations (
            operation_key TEXT PRIMARY KEY,
            fingerprint TEXT NOT NULL,
            result TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
