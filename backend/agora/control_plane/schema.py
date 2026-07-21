"""Additive SQLite schema for the Agora Control Plane v2 registry."""
from __future__ import annotations

import sqlite3


def initialize_control_plane_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS control_tasks (
            task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
            project_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN (
                    'backlog', 'ready', 'active', 'blocked',
                    'needs_review', 'completed', 'failed', 'cancelled'
                )
            ),
            version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_control_tasks_project_status
            ON control_tasks(project_id, status, updated_at DESC);

        CREATE TABLE IF NOT EXISTS control_stage_inventories (
            task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
            project_id TEXT NOT NULL,
            inventory_id TEXT NOT NULL UNIQUE,
            payload TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_control_stage_inventories_project
            ON control_stage_inventories(project_id, created_at DESC);

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

        CREATE TABLE IF NOT EXISTS protocol_runs (
            run_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            stage_key TEXT NOT NULL,
            gate_key TEXT NOT NULL,
            context_pack_id TEXT NOT NULL UNIQUE,
            context_payload TEXT NOT NULL,
            context_sha256 TEXT NOT NULL,
            protocol_state_payload TEXT,
            handoff_pack_id TEXT UNIQUE,
            handoff_payload TEXT,
            handoff_sha256 TEXT,
            adapter_error_code TEXT,
            attention_required INTEGER NOT NULL DEFAULT 0
                CHECK (attention_required IN (0, 1)),
            attention_item_id TEXT REFERENCES attention_items(item_id),
            created_at TEXT NOT NULL,
            settled_at TEXT,
            FOREIGN KEY (task_id, stage_key)
                REFERENCES control_stages(task_id, stage_key),
            FOREIGN KEY (task_id, gate_key)
                REFERENCES control_gates(task_id, gate_key),
            CHECK (
                (settled_at IS NULL AND protocol_state_payload IS NULL)
                OR
                (settled_at IS NOT NULL AND protocol_state_payload IS NOT NULL)
            ),
            CHECK (
                (handoff_pack_id IS NULL AND handoff_payload IS NULL
                    AND handoff_sha256 IS NULL)
                OR
                (handoff_pack_id IS NOT NULL AND handoff_payload IS NOT NULL
                    AND handoff_sha256 IS NOT NULL)
            )
        );
        CREATE INDEX IF NOT EXISTS idx_protocol_runs_task_stage_created
            ON protocol_runs(task_id, stage_key, created_at, run_id);

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
