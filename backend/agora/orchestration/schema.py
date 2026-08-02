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
            runtime_preflight_payload TEXT,
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

        CREATE TABLE IF NOT EXISTS orchestration_consultations (
            consultation_id TEXT PRIMARY KEY,
            operation_key TEXT NOT NULL UNIQUE,
            project_id TEXT NOT NULL,
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            plan_id TEXT NOT NULL REFERENCES orchestration_plans(plan_id),
            plan_version_observed INTEGER NOT NULL,
            inventory_id TEXT NOT NULL,
            inventory_sha256 TEXT NOT NULL,
            stage_key TEXT NOT NULL,
            role TEXT NOT NULL,
            runtime TEXT NOT NULL,
            repository_id TEXT NOT NULL,
            repository_ref TEXT NOT NULL,
            repository_commit TEXT NOT NULL,
            decision_key TEXT NOT NULL,
            state TEXT NOT NULL,
            prompt_sha256 TEXT NOT NULL,
            pid INTEGER,
            process_status TEXT,
            transport_status TEXT,
            schema_status TEXT NOT NULL,
            repair_attempts INTEGER NOT NULL DEFAULT 0,
            candidate_id TEXT UNIQUE,
            output_sha256 TEXT,
            error_code TEXT,
            error_message TEXT,
            token_reserved INTEGER NOT NULL,
            cost_reserved_usd REAL,
            token_used INTEGER,
            token_measurement TEXT,
            cost_used_usd REAL,
            cost_measurement TEXT,
            usage_observation_payload TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_orchestration_consultations_plan_started
            ON orchestration_consultations(plan_id, started_at, consultation_id);
        CREATE INDEX IF NOT EXISTS idx_orchestration_consultations_task_state
            ON orchestration_consultations(task_id, state, started_at);

        CREATE TABLE IF NOT EXISTS orchestration_consultation_candidates (
            candidate_id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL REFERENCES orchestration_plans(plan_id),
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            stage_key TEXT NOT NULL,
            operation_key TEXT NOT NULL UNIQUE,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_orchestration_candidates_plan_created
            ON orchestration_consultation_candidates(plan_id, created_at, candidate_id);

        CREATE TABLE IF NOT EXISTS orchestration_candidate_dispositions (
            disposition_id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL REFERENCES orchestration_plans(plan_id),
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            candidate_id TEXT NOT NULL UNIQUE
                REFERENCES orchestration_consultation_candidates(candidate_id),
            operation_key TEXT NOT NULL UNIQUE,
            action TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_orchestration_dispositions_plan_created
            ON orchestration_candidate_dispositions(
                plan_id, created_at, disposition_id
            );

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

        CREATE TABLE IF NOT EXISTS orchestration_methodology_migrations (
            request_id TEXT PRIMARY KEY,
            request_sha256 TEXT NOT NULL,
            request_payload TEXT NOT NULL,
            source_task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
            successor_task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
            successor_plan_id TEXT NOT NULL UNIQUE
                REFERENCES orchestration_plans(plan_id),
            gate_id TEXT NOT NULL UNIQUE,
            gate_sha256 TEXT NOT NULL,
            gate_payload TEXT NOT NULL,
            recheck_decision_sha256 TEXT NOT NULL,
            recheck_decision_payload TEXT NOT NULL,
            receipt_sha256 TEXT NOT NULL,
            receipt_payload TEXT NOT NULL,
            authenticated_principal_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_orchestration_methodology_migrations_successor
            ON orchestration_methodology_migrations(
                successor_task_id, created_at
            );

        CREATE TABLE IF NOT EXISTS orchestration_methodology_execution_contracts (
            contract_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
            plan_id TEXT NOT NULL UNIQUE REFERENCES orchestration_plans(plan_id),
            inventory_id TEXT NOT NULL UNIQUE,
            inventory_sha256 TEXT NOT NULL,
            migration_request_id TEXT NOT NULL UNIQUE
                REFERENCES orchestration_methodology_migrations(request_id),
            migration_receipt_sha256 TEXT NOT NULL,
            contract_sha256 TEXT NOT NULL,
            contract_payload TEXT NOT NULL,
            authenticated_principal_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_methodology_execution_contracts_task
            ON orchestration_methodology_execution_contracts(task_id, created_at);

        CREATE TABLE IF NOT EXISTS orchestration_methodology_route_activations (
            request_id TEXT PRIMARY KEY,
            request_sha256 TEXT NOT NULL,
            request_payload TEXT NOT NULL,
            receipt_id TEXT NOT NULL UNIQUE,
            receipt_sha256 TEXT NOT NULL,
            receipt_payload TEXT NOT NULL,
            task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
            execution_contract_id TEXT NOT NULL UNIQUE
                REFERENCES orchestration_methodology_execution_contracts(contract_id),
            execution_contract_sha256 TEXT NOT NULL,
            authenticated_principal_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_methodology_route_activations_task
            ON orchestration_methodology_route_activations(task_id, created_at);

        CREATE TABLE IF NOT EXISTS orchestration_methodology_seed_artifact_refs (
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            consumer_stage_key TEXT NOT NULL,
            source_artifact_id TEXT NOT NULL,
            artifact_id TEXT NOT NULL,
            artifact_version INTEGER NOT NULL,
            artifact_sha256 TEXT NOT NULL,
            artifact_kind TEXT NOT NULL,
            repository_id TEXT NOT NULL,
            ref TEXT NOT NULL,
            commit_sha TEXT NOT NULL,
            path TEXT NOT NULL,
            payload TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            execution_contract_id TEXT NOT NULL
                REFERENCES orchestration_methodology_execution_contracts(contract_id),
            activation_request_id TEXT NOT NULL
                REFERENCES orchestration_methodology_route_activations(request_id),
            registered_at TEXT NOT NULL,
            PRIMARY KEY (task_id, consumer_stage_key, source_artifact_id)
        );
        CREATE INDEX IF NOT EXISTS idx_methodology_seed_artifact_refs_contract
            ON orchestration_methodology_seed_artifact_refs(
                execution_contract_id, consumer_stage_key
            );

        CREATE TABLE IF NOT EXISTS orchestration_methodology_run_claims (
            request_id TEXT PRIMARY KEY,
            request_sha256 TEXT NOT NULL,
            request_payload TEXT NOT NULL,
            receipt_id TEXT NOT NULL UNIQUE,
            receipt_sha256 TEXT NOT NULL,
            receipt_payload TEXT NOT NULL,
            task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
            plan_id TEXT NOT NULL REFERENCES orchestration_plans(plan_id),
            execution_contract_id TEXT NOT NULL UNIQUE
                REFERENCES orchestration_methodology_execution_contracts(contract_id),
            route_activation_request_id TEXT NOT NULL UNIQUE
                REFERENCES orchestration_methodology_route_activations(request_id),
            route_activation_receipt_id TEXT NOT NULL UNIQUE,
            run_id TEXT NOT NULL UNIQUE REFERENCES protocol_runs(run_id),
            stage_key TEXT NOT NULL,
            runtime TEXT NOT NULL,
            context_pack_id TEXT NOT NULL UNIQUE,
            context_pack_sha256 TEXT NOT NULL,
            token_reserved INTEGER NOT NULL CHECK (token_reserved >= 0),
            cost_reserved_usd REAL CHECK (
                cost_reserved_usd IS NULL OR cost_reserved_usd >= 0
            ),
            authenticated_principal_id TEXT NOT NULL,
            process_started INTEGER NOT NULL DEFAULT 0
                CHECK (process_started = 0),
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_methodology_run_claims_plan_created
            ON orchestration_methodology_run_claims(plan_id, created_at, run_id);

        CREATE TABLE IF NOT EXISTS orchestration_methodology_run_dispatches (
            dispatch_id TEXT PRIMARY KEY,
            claim_sha256 TEXT NOT NULL,
            claim_payload TEXT NOT NULL,
            task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
            plan_id TEXT NOT NULL REFERENCES orchestration_plans(plan_id),
            run_id TEXT NOT NULL UNIQUE REFERENCES protocol_runs(run_id),
            stage_key TEXT NOT NULL,
            runtime TEXT NOT NULL,
            prompt_sha256 TEXT NOT NULL,
            dispatch_policy_id TEXT NOT NULL UNIQUE,
            dispatch_policy_sha256 TEXT NOT NULL,
            dispatch_policy_payload TEXT NOT NULL,
            preflight_id TEXT NOT NULL UNIQUE,
            preflight_sha256 TEXT NOT NULL,
            preflight_payload TEXT NOT NULL,
            state TEXT NOT NULL CHECK (
                state IN ('claimed', 'running', 'terminal_observed', 'settled')
            ),
            pid INTEGER,
            process_started INTEGER NOT NULL DEFAULT 0
                CHECK (process_started IN (0, 1)),
            exit_code INTEGER,
            timed_out INTEGER NOT NULL DEFAULT 0
                CHECK (timed_out IN (0, 1)),
            output TEXT NOT NULL DEFAULT '',
            error_message TEXT,
            repository_unchanged INTEGER
                CHECK (repository_unchanged IN (0, 1)),
            adapter_result_payload TEXT,
            usage_observation_payload TEXT,
            receipt_sha256 TEXT,
            receipt_payload TEXT,
            claimed_at TEXT NOT NULL,
            process_attached_at TEXT,
            terminal_observed_at TEXT,
            settled_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_methodology_dispatches_plan_claimed
            ON orchestration_methodology_run_dispatches(
                plan_id, claimed_at, run_id
            );

        CREATE TABLE IF NOT EXISTS orchestration_methodology_usage_ledger (
            entry_id TEXT PRIMARY KEY,
            dispatch_id TEXT NOT NULL UNIQUE
                REFERENCES orchestration_methodology_run_dispatches(dispatch_id),
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            plan_id TEXT NOT NULL REFERENCES orchestration_plans(plan_id),
            stage_key TEXT NOT NULL,
            run_id TEXT NOT NULL UNIQUE REFERENCES protocol_runs(run_id),
            tokens INTEGER,
            token_measurement TEXT NOT NULL,
            cost_usd REAL,
            cost_measurement TEXT NOT NULL,
            adapter TEXT NOT NULL,
            usage_observation_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_methodology_usage_task_created
            ON orchestration_methodology_usage_ledger(
                task_id, created_at, entry_id
            );

        CREATE TABLE IF NOT EXISTS orchestration_methodology_stage_gates (
            request_id TEXT PRIMARY KEY,
            request_sha256 TEXT NOT NULL,
            request_payload TEXT NOT NULL,
            receipt_id TEXT NOT NULL UNIQUE,
            receipt_sha256 TEXT NOT NULL,
            receipt_payload TEXT NOT NULL,
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            plan_id TEXT NOT NULL REFERENCES orchestration_plans(plan_id),
            execution_contract_id TEXT NOT NULL
                REFERENCES orchestration_methodology_execution_contracts(contract_id),
            predecessor_dispatch_id TEXT NOT NULL
                REFERENCES orchestration_methodology_run_dispatches(dispatch_id),
            predecessor_run_id TEXT NOT NULL REFERENCES protocol_runs(run_id),
            stage_sequence INTEGER NOT NULL CHECK (
                stage_sequence >= 2 AND stage_sequence <= 200
            ),
            stage_key TEXT NOT NULL,
            gate_key TEXT NOT NULL,
            authenticated_principal_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(task_id, stage_key),
            UNIQUE(task_id, gate_key),
            UNIQUE(task_id, stage_sequence)
        );
        CREATE INDEX IF NOT EXISTS idx_methodology_stage_gates_task_sequence
            ON orchestration_methodology_stage_gates(
                task_id, stage_sequence, created_at
            );

        CREATE TABLE IF NOT EXISTS
            orchestration_methodology_successor_predecessors (
            stage_gate_request_id TEXT PRIMARY KEY
                REFERENCES orchestration_methodology_stage_gates(request_id),
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            plan_id TEXT NOT NULL REFERENCES orchestration_plans(plan_id),
            stage_sequence INTEGER NOT NULL CHECK (
                stage_sequence >= 3 AND stage_sequence <= 200
            ),
            predecessor_dispatch_id TEXT NOT NULL UNIQUE
                REFERENCES orchestration_methodology_stage_run_dispatches(dispatch_id),
            predecessor_dispatch_receipt_id TEXT NOT NULL UNIQUE,
            predecessor_dispatch_receipt_sha256 TEXT NOT NULL,
            predecessor_run_id TEXT NOT NULL UNIQUE REFERENCES protocol_runs(run_id),
            predecessor_stage_sequence INTEGER NOT NULL CHECK (
                predecessor_stage_sequence >= 2
                AND predecessor_stage_sequence <= 199
                AND predecessor_stage_sequence + 1 = stage_sequence
            ),
            predecessor_stage_key TEXT NOT NULL,
            predecessor_gate_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(task_id, stage_sequence)
        );
        CREATE INDEX IF NOT EXISTS idx_methodology_successor_predecessors_plan
            ON orchestration_methodology_successor_predecessors(
                plan_id, stage_sequence, created_at
            );

        CREATE TABLE IF NOT EXISTS orchestration_methodology_stage_run_claims (
            request_id TEXT PRIMARY KEY,
            request_sha256 TEXT NOT NULL,
            request_payload TEXT NOT NULL,
            receipt_id TEXT NOT NULL UNIQUE,
            receipt_sha256 TEXT NOT NULL,
            receipt_payload TEXT NOT NULL,
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            plan_id TEXT NOT NULL REFERENCES orchestration_plans(plan_id),
            execution_contract_id TEXT NOT NULL
                REFERENCES orchestration_methodology_execution_contracts(contract_id),
            stage_gate_request_id TEXT NOT NULL UNIQUE
                REFERENCES orchestration_methodology_stage_gates(request_id),
            stage_gate_receipt_id TEXT NOT NULL UNIQUE,
            predecessor_dispatch_id TEXT NOT NULL
                REFERENCES orchestration_methodology_run_dispatches(dispatch_id),
            predecessor_dispatch_receipt_id TEXT NOT NULL,
            stage_sequence INTEGER NOT NULL CHECK (
                stage_sequence >= 2 AND stage_sequence <= 200
            ),
            stage_key TEXT NOT NULL,
            gate_key TEXT NOT NULL,
            runtime TEXT NOT NULL,
            run_id TEXT NOT NULL UNIQUE REFERENCES protocol_runs(run_id),
            context_pack_id TEXT NOT NULL UNIQUE,
            context_pack_sha256 TEXT NOT NULL,
            token_reserved INTEGER NOT NULL CHECK (token_reserved > 0),
            cost_reserved_usd REAL CHECK (
                cost_reserved_usd IS NULL OR cost_reserved_usd >= 0
            ),
            authenticated_principal_id TEXT NOT NULL,
            process_started INTEGER NOT NULL DEFAULT 0
                CHECK (process_started = 0),
            created_at TEXT NOT NULL,
            UNIQUE(task_id, stage_key),
            UNIQUE(task_id, gate_key),
            UNIQUE(task_id, stage_sequence)
        );
        CREATE INDEX IF NOT EXISTS idx_methodology_stage_run_claims_plan
            ON orchestration_methodology_stage_run_claims(
                plan_id, stage_sequence, created_at, run_id
            );

        CREATE TABLE IF NOT EXISTS orchestration_methodology_stage_run_dispatches (
            dispatch_id TEXT PRIMARY KEY,
            claim_sha256 TEXT NOT NULL,
            claim_payload TEXT NOT NULL,
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            plan_id TEXT NOT NULL REFERENCES orchestration_plans(plan_id),
            stage_run_claim_request_id TEXT NOT NULL UNIQUE
                REFERENCES orchestration_methodology_stage_run_claims(request_id),
            stage_run_claim_receipt_id TEXT NOT NULL UNIQUE,
            predecessor_dispatch_id TEXT NOT NULL
                REFERENCES orchestration_methodology_run_dispatches(dispatch_id),
            run_id TEXT NOT NULL UNIQUE REFERENCES protocol_runs(run_id),
            stage_sequence INTEGER NOT NULL CHECK (
                stage_sequence >= 2 AND stage_sequence <= 200
            ),
            stage_key TEXT NOT NULL,
            runtime TEXT NOT NULL,
            prompt_sha256 TEXT NOT NULL,
            spawn_owner_id TEXT NOT NULL,
            recovery_not_before TEXT NOT NULL,
            dispatch_policy_id TEXT NOT NULL UNIQUE,
            dispatch_policy_sha256 TEXT NOT NULL,
            dispatch_policy_payload TEXT NOT NULL,
            preflight_id TEXT NOT NULL UNIQUE,
            preflight_sha256 TEXT NOT NULL,
            preflight_payload TEXT NOT NULL,
            state TEXT NOT NULL CHECK (
                state IN ('claimed', 'running', 'terminal_observed', 'settled')
            ),
            pid INTEGER,
            process_started INTEGER NOT NULL DEFAULT 0
                CHECK (process_started IN (0, 1)),
            exit_code INTEGER,
            timed_out INTEGER NOT NULL DEFAULT 0
                CHECK (timed_out IN (0, 1)),
            output TEXT NOT NULL DEFAULT '',
            error_message TEXT,
            repository_unchanged INTEGER
                CHECK (repository_unchanged IN (0, 1)),
            adapter_result_payload TEXT,
            usage_observation_payload TEXT,
            receipt_sha256 TEXT,
            receipt_payload TEXT,
            claimed_at TEXT NOT NULL,
            process_attached_at TEXT,
            terminal_observed_at TEXT,
            settled_at TEXT,
            UNIQUE(task_id, stage_key),
            UNIQUE(task_id, stage_sequence)
        );
        CREATE INDEX IF NOT EXISTS idx_methodology_stage_dispatches_plan
            ON orchestration_methodology_stage_run_dispatches(
                plan_id, stage_sequence, claimed_at, run_id
            );

        CREATE TABLE IF NOT EXISTS orchestration_methodology_stage_usage_ledger (
            entry_id TEXT PRIMARY KEY,
            dispatch_id TEXT NOT NULL UNIQUE
                REFERENCES orchestration_methodology_stage_run_dispatches(dispatch_id),
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            plan_id TEXT NOT NULL REFERENCES orchestration_plans(plan_id),
            stage_key TEXT NOT NULL,
            run_id TEXT NOT NULL UNIQUE REFERENCES protocol_runs(run_id),
            tokens INTEGER,
            token_measurement TEXT NOT NULL,
            cost_usd REAL,
            cost_measurement TEXT NOT NULL,
            adapter TEXT NOT NULL,
            usage_observation_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_methodology_stage_usage_task
            ON orchestration_methodology_stage_usage_ledger(
                task_id, created_at, entry_id
            );

        CREATE TABLE IF NOT EXISTS
            orchestration_methodology_completion_review_claims (
            request_id TEXT PRIMARY KEY,
            request_sha256 TEXT NOT NULL,
            request_payload TEXT NOT NULL,
            receipt_id TEXT NOT NULL UNIQUE,
            receipt_sha256 TEXT NOT NULL,
            receipt_payload TEXT NOT NULL,
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            plan_id TEXT NOT NULL REFERENCES orchestration_plans(plan_id),
            execution_contract_id TEXT NOT NULL
                REFERENCES orchestration_methodology_execution_contracts(contract_id),
            final_dispatch_id TEXT NOT NULL
                REFERENCES orchestration_methodology_stage_run_dispatches(dispatch_id),
            final_dispatch_receipt_id TEXT NOT NULL,
            responsibility TEXT NOT NULL CHECK (
                responsibility IN (
                    'independent_correctness',
                    'methodology_stewardship'
                )
            ),
            runtime TEXT NOT NULL,
            review_run_id TEXT NOT NULL UNIQUE,
            stage_key TEXT NOT NULL,
            gate_key TEXT NOT NULL,
            authenticated_principal_id TEXT NOT NULL,
            claimed_at TEXT NOT NULL,
            UNIQUE(task_id, responsibility)
        );
        CREATE INDEX IF NOT EXISTS idx_methodology_completion_review_claims_task
            ON orchestration_methodology_completion_review_claims(
                task_id, claimed_at, responsibility
            );

        CREATE TABLE IF NOT EXISTS
            orchestration_methodology_completion_review_dispatches (
            dispatch_id TEXT PRIMARY KEY,
            claim_sha256 TEXT NOT NULL,
            claim_payload TEXT NOT NULL,
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            plan_id TEXT NOT NULL REFERENCES orchestration_plans(plan_id),
            completion_review_claim_request_id TEXT NOT NULL UNIQUE
                REFERENCES orchestration_methodology_completion_review_claims(request_id),
            completion_review_claim_receipt_id TEXT NOT NULL UNIQUE,
            responsibility TEXT NOT NULL CHECK (
                responsibility IN (
                    'independent_correctness',
                    'methodology_stewardship'
                )
            ),
            runtime TEXT NOT NULL,
            review_run_id TEXT NOT NULL UNIQUE REFERENCES protocol_runs(run_id),
            stage_key TEXT NOT NULL,
            gate_key TEXT NOT NULL,
            context_pack_id TEXT NOT NULL UNIQUE,
            context_pack_sha256 TEXT NOT NULL,
            prompt_sha256 TEXT NOT NULL,
            token_reserved INTEGER NOT NULL CHECK (token_reserved > 0),
            cost_reserved_usd REAL CHECK (
                cost_reserved_usd IS NULL OR cost_reserved_usd >= 0
            ),
            spawn_owner_id TEXT NOT NULL,
            recovery_not_before TEXT NOT NULL,
            dispatch_policy_id TEXT NOT NULL UNIQUE,
            dispatch_policy_sha256 TEXT NOT NULL,
            dispatch_policy_payload TEXT NOT NULL,
            preflight_id TEXT NOT NULL UNIQUE,
            preflight_sha256 TEXT NOT NULL,
            preflight_payload TEXT NOT NULL,
            state TEXT NOT NULL CHECK (
                state IN ('claimed', 'running', 'terminal_observed', 'settled')
            ),
            pid INTEGER,
            process_started INTEGER NOT NULL DEFAULT 0
                CHECK (process_started IN (0, 1)),
            exit_code INTEGER,
            timed_out INTEGER NOT NULL DEFAULT 0
                CHECK (timed_out IN (0, 1)),
            output TEXT NOT NULL DEFAULT '',
            error_message TEXT,
            repository_unchanged INTEGER
                CHECK (repository_unchanged IN (0, 1)),
            adapter_result_payload TEXT,
            usage_observation_payload TEXT,
            receipt_sha256 TEXT,
            receipt_payload TEXT,
            claimed_at TEXT NOT NULL,
            process_attached_at TEXT,
            terminal_observed_at TEXT,
            settled_at TEXT,
            UNIQUE(task_id, responsibility)
        );
        CREATE INDEX IF NOT EXISTS idx_methodology_completion_dispatches_task
            ON orchestration_methodology_completion_review_dispatches(
                task_id, claimed_at, responsibility, review_run_id
            );

        CREATE TABLE IF NOT EXISTS
            orchestration_methodology_completion_review_usage_ledger (
            entry_id TEXT PRIMARY KEY,
            dispatch_id TEXT NOT NULL UNIQUE
                REFERENCES orchestration_methodology_completion_review_dispatches(dispatch_id),
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            plan_id TEXT NOT NULL REFERENCES orchestration_plans(plan_id),
            responsibility TEXT NOT NULL,
            review_run_id TEXT NOT NULL UNIQUE REFERENCES protocol_runs(run_id),
            tokens INTEGER,
            token_measurement TEXT NOT NULL,
            cost_usd REAL,
            cost_measurement TEXT NOT NULL,
            adapter TEXT NOT NULL,
            usage_observation_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_methodology_completion_usage_task
            ON orchestration_methodology_completion_review_usage_ledger(
                task_id, created_at, entry_id
            );

        CREATE TABLE IF NOT EXISTS
            orchestration_methodology_completion_approvals (
            approval_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL UNIQUE,
            request_sha256 TEXT NOT NULL,
            request_payload TEXT NOT NULL,
            authenticated_approval_sha256 TEXT NOT NULL,
            authenticated_approval_payload TEXT NOT NULL,
            approval_sha256 TEXT NOT NULL,
            receipt_id TEXT NOT NULL UNIQUE,
            receipt_sha256 TEXT NOT NULL,
            receipt_payload TEXT NOT NULL,
            task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
            plan_id TEXT NOT NULL REFERENCES orchestration_plans(plan_id),
            execution_contract_id TEXT NOT NULL UNIQUE
                REFERENCES orchestration_methodology_execution_contracts(contract_id),
            stage_key TEXT NOT NULL,
            gate_key TEXT NOT NULL,
            authenticated_principal_id TEXT NOT NULL,
            completed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_methodology_completion_approvals_task
            ON orchestration_methodology_completion_approvals(
                task_id, completed_at, approval_id
            );
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
    if "runtime_preflight_payload" not in columns:
        db.execute(
            "ALTER TABLE orchestration_runs "
            "ADD COLUMN runtime_preflight_payload TEXT"
        )
    if "usage_observation_payload" not in columns:
        db.execute(
            "ALTER TABLE orchestration_runs "
            "ADD COLUMN usage_observation_payload TEXT"
        )
