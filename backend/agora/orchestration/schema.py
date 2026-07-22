"""Additive SQLite schema for task orchestration plans and usage accounting."""
from __future__ import annotations

import sqlite3


def initialize_orchestration_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS orchestration_plans (
            plan_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
            project_id TEXT NOT NULL,
            methodology_id TEXT NOT NULL,
            methodology_version TEXT NOT NULL,
            methodology_sha256 TEXT NOT NULL,
            methodology_payload TEXT NOT NULL,
            provisional INTEGER NOT NULL,
            state TEXT NOT NULL,
            total_token_budget INTEGER NOT NULL,
            total_cost_budget_usd REAL,
            current_stage_key TEXT,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            approved_at TEXT,
            approved_by TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_orchestration_plans_project_state
            ON orchestration_plans(project_id, state, updated_at DESC);

        CREATE TABLE IF NOT EXISTS orchestration_stages (
            stage_id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL REFERENCES orchestration_plans(plan_id),
            stage_key TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            title TEXT NOT NULL,
            role TEXT NOT NULL,
            adapter TEXT NOT NULL,
            state TEXT NOT NULL,
            token_budget INTEGER NOT NULL,
            cost_budget_usd REAL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            latest_run_id TEXT,
            semantic_summary TEXT,
            blockers TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL,
            UNIQUE(plan_id, stage_key),
            UNIQUE(plan_id, sequence)
        );
        CREATE INDEX IF NOT EXISTS idx_orchestration_stages_plan_sequence
            ON orchestration_stages(plan_id, sequence);

        CREATE TABLE IF NOT EXISTS orchestration_runs (
            run_id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL REFERENCES orchestration_plans(plan_id),
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            stage_key TEXT NOT NULL,
            adapter TEXT NOT NULL,
            state TEXT NOT NULL,
            operation_key TEXT NOT NULL UNIQUE,
            prompt_sha256 TEXT NOT NULL,
            pid INTEGER,
            exit_code INTEGER,
            timed_out INTEGER NOT NULL DEFAULT 0,
            output TEXT NOT NULL DEFAULT '',
            error_message TEXT,
            semantic_status TEXT,
            semantic_summary TEXT,
            findings TEXT NOT NULL DEFAULT '[]',
            token_reserved INTEGER NOT NULL,
            token_used INTEGER,
            token_measurement TEXT NOT NULL,
            cost_reserved_usd REAL,
            cost_used_usd REAL,
            cost_measurement TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            routing_policy_payload TEXT,
            usage_observation_payload TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_orchestration_runs_plan_started
            ON orchestration_runs(plan_id, started_at, run_id);

        CREATE TABLE IF NOT EXISTS orchestration_usage_ledger (
            entry_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            plan_id TEXT NOT NULL REFERENCES orchestration_plans(plan_id),
            stage_key TEXT NOT NULL,
            run_id TEXT NOT NULL REFERENCES orchestration_runs(run_id),
            entry_type TEXT NOT NULL,
            tokens INTEGER,
            token_measurement TEXT NOT NULL,
            cost_usd REAL,
            cost_measurement TEXT NOT NULL,
            adapter TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(run_id, entry_type)
        );
        CREATE INDEX IF NOT EXISTS idx_orchestration_usage_task_created
            ON orchestration_usage_ledger(task_id, created_at, entry_id);

        CREATE TABLE IF NOT EXISTS orchestration_decisions (
            decision_id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL REFERENCES orchestration_plans(plan_id),
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            decision_key TEXT NOT NULL,
            decision_value TEXT NOT NULL,
            rationale TEXT NOT NULL,
            decision_sha256 TEXT NOT NULL,
            version INTEGER NOT NULL,
            actor TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(plan_id, decision_key, version)
        );
        CREATE INDEX IF NOT EXISTS idx_orchestration_decisions_plan_key_version
            ON orchestration_decisions(plan_id, decision_key, version DESC);

        CREATE TABLE IF NOT EXISTS orchestration_budget_amendments (
            amendment_id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL REFERENCES orchestration_plans(plan_id),
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            version INTEGER NOT NULL,
            operation_key TEXT NOT NULL UNIQUE,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(plan_id, version)
        );
        CREATE INDEX IF NOT EXISTS idx_orchestration_budget_amendments_plan_version
            ON orchestration_budget_amendments(plan_id, version);
        """
    )
    columns = {row[1] for row in db.execute("PRAGMA table_info(orchestration_runs)")}
    if "timed_out" not in columns:
        db.execute(
            "ALTER TABLE orchestration_runs "
            "ADD COLUMN timed_out INTEGER NOT NULL DEFAULT 0"
        )
    if "routing_policy_payload" not in columns:
        db.execute(
            "ALTER TABLE orchestration_runs "
            "ADD COLUMN routing_policy_payload TEXT"
        )
    if "usage_observation_payload" not in columns:
        db.execute(
            "ALTER TABLE orchestration_runs "
            "ADD COLUMN usage_observation_payload TEXT"
        )
