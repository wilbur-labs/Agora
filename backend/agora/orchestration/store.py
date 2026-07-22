"""Transactional persistence for methodology plans, stages, runs, and usage."""
from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import uuid
from contextlib import closing, contextmanager
from typing import Any, Iterator

from agora.control_plane.models import RunSettlementReceipt
from agora.execution.security import redact_text, sanitize_data
from agora.protocol.agent_adapter import AgentAdapterResult
from agora.protocol.models import SemanticStageResult
from agora.protocol.state_machines import GateStatus, StageStatus
from agora.tasks.models import utc_now
from agora.tasks.store import TaskNotFoundError, TaskStore

from .methodology import MethodologyDefinition, methodology_sha256
from .models import (
    LedgerEntryType,
    Measurement,
    OrchestrationPlan,
    OrchestrationRun,
    OrchestrationStage,
    PlanState,
    RunState,
    SemanticResult,
    StageState,
    TaskDecision,
    TaskOrchestrationStatus,
    UsageLedgerEntry,
)


DECISION_CONTEXT_LIMIT = 2_000


class OrchestrationNotFoundError(LookupError):
    pass


class OrchestrationConflictError(RuntimeError):
    pass


class OrchestrationValidationError(ValueError):
    pass


class OrchestrationStore:
    def __init__(self, tasks: TaskStore):
        self.tasks = tasks

    def _connect(self) -> sqlite3.Connection:
        return self.tasks._connect()

    @classmethod
    def new_run_id(cls) -> str:
        return cls._id("orun")

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

    def create_plan(
        self,
        task_id: str,
        methodology: MethodologyDefinition,
        *,
        total_token_budget: int,
        total_cost_budget_usd: float | None,
        actor: str = "user",
    ) -> OrchestrationPlan:
        self.validate_plan_inputs(
            methodology,
            total_token_budget=total_token_budget,
            total_cost_budget_usd=total_cost_budget_usd,
        )
        task = self.tasks.get(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)

        plan_id = self._id("plan")
        now = utc_now()
        digest = methodology_sha256(methodology)
        token_allocations = self._allocate_int(total_token_budget, [s.token_weight for s in methodology.stages])
        cost_allocations = (
            self._allocate_float(total_cost_budget_usd, [s.token_weight for s in methodology.stages])
            if total_cost_budget_usd is not None else [None] * len(methodology.stages)
        )
        first_stage = methodology.stages[0].stage_key
        with self._transaction() as db:
            try:
                db.execute(
                    """
                    INSERT INTO orchestration_plans (
                        plan_id, task_id, project_id, methodology_id, methodology_version,
                        methodology_sha256, methodology_payload, provisional, state,
                        total_token_budget, total_cost_budget_usd, current_stage_key,
                        version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        plan_id, task_id, task.project_id, methodology.methodology_id,
                        methodology.version, digest, self._json(methodology.model_dump(mode="json")),
                        int(methodology.provisional), PlanState.ACTIVE.value, total_token_budget,
                        total_cost_budget_usd, first_stage, now, now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                if "task_id" in str(exc) or "UNIQUE" in str(exc):
                    raise OrchestrationConflictError("Task already has an orchestration plan") from None
                raise
            for sequence, (stage, tokens, cost) in enumerate(
                zip(methodology.stages, token_allocations, cost_allocations), start=1,
            ):
                db.execute(
                    """
                    INSERT INTO orchestration_stages (
                        stage_id, plan_id, stage_key, sequence, title, role, adapter,
                        state, token_budget, cost_budget_usd, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self._id("stage"), plan_id, stage.stage_key, sequence, stage.title,
                        stage.role, stage.adapter, StageState.PENDING.value, tokens, cost, now,
                    ),
                )
            self.tasks._insert_event(
                db, task_id=task_id, event_type="orchestration.plan_created", actor=actor,
                payload={
                    "plan_id": plan_id,
                    "methodology": f"{methodology.methodology_id}@{methodology.version}",
                    "methodology_sha256": digest,
                    "provisional": methodology.provisional,
                    "total_token_budget": total_token_budget,
                    "total_cost_budget_usd": total_cost_budget_usd,
                },
                created_at=now,
            )
        return self.require_plan(task_id)

    @staticmethod
    def validate_plan_inputs(
        methodology: MethodologyDefinition,
        *,
        total_token_budget: int,
        total_cost_budget_usd: float | None,
    ) -> None:
        if total_token_budget < 3_000 or total_token_budget > 10_000_000:
            raise OrchestrationValidationError("total_token_budget must be between 3000 and 10000000")
        if total_cost_budget_usd is not None and total_cost_budget_usd < 0:
            raise OrchestrationValidationError("total_cost_budget_usd may not be negative")
        if sum(stage.token_weight for stage in methodology.stages) != 100:
            raise OrchestrationValidationError("methodology stage token weights must sum to 100")

    def get_plan(self, task_id: str) -> OrchestrationPlan | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM orchestration_plans WHERE task_id = ?", (task_id,),
            ).fetchone()
        return self._plan(row) if row else None

    def require_plan(self, task_id: str) -> OrchestrationPlan:
        plan = self.get_plan(task_id)
        if plan is None:
            raise OrchestrationNotFoundError(task_id)
        return plan

    def methodology(self, plan_id: str) -> MethodologyDefinition:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT methodology_payload FROM orchestration_plans WHERE plan_id = ?", (plan_id,),
            ).fetchone()
        if not row:
            raise OrchestrationNotFoundError(plan_id)
        return MethodologyDefinition.model_validate_json(row["methodology_payload"])

    def stages(self, plan_id: str) -> list[OrchestrationStage]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM orchestration_stages WHERE plan_id = ? ORDER BY sequence", (plan_id,),
            ).fetchall()
        return [self._stage(row) for row in rows]

    def runs(self, plan_id: str) -> list[OrchestrationRun]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM orchestration_runs WHERE plan_id = ? ORDER BY rowid", (plan_id,),
            ).fetchall()
        return [self._run(row) for row in rows]

    def usage(self, plan_id: str) -> list[UsageLedgerEntry]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM orchestration_usage_ledger WHERE plan_id = ? ORDER BY rowid", (plan_id,),
            ).fetchall()
        return [self._usage(row) for row in rows]

    def decisions(self, plan_id: str) -> list[TaskDecision]:
        with closing(self._connect()) as db:
            rows = db.execute(
                """SELECT * FROM orchestration_decisions
                   WHERE plan_id = ? ORDER BY decision_key, version""",
                (plan_id,),
            ).fetchall()
        return [self._decision(row) for row in rows]

    def latest_decisions(self, plan_id: str) -> list[TaskDecision]:
        with closing(self._connect()) as db:
            rows = db.execute(
                """SELECT decision.* FROM orchestration_decisions AS decision
                   JOIN (
                       SELECT decision_key, MAX(version) AS version
                       FROM orchestration_decisions WHERE plan_id = ?
                       GROUP BY decision_key
                   ) AS latest
                   ON latest.decision_key = decision.decision_key
                   AND latest.version = decision.version
                   WHERE decision.plan_id = ?
                   ORDER BY decision.decision_key""",
                (plan_id, plan_id),
            ).fetchall()
        return [self._decision(row) for row in rows]

    def record_decision(
        self,
        task_id: str,
        *,
        decision_key: str,
        decision_value: str,
        rationale: str,
        actor: str = "user",
    ) -> TaskDecision:
        if not re.fullmatch(r"[a-z][a-z0-9_.-]*", decision_key):
            raise OrchestrationValidationError("Invalid decision key")
        safe_value = redact_text(decision_value.strip())
        safe_rationale = redact_text(rationale.strip())
        if not safe_value or len(safe_value) > 1_000:
            raise OrchestrationValidationError("Decision value must contain 1 to 1000 characters")
        if not safe_rationale or len(safe_rationale) > 500:
            raise OrchestrationValidationError("Decision rationale must contain 1 to 500 characters")
        actor = actor.strip()
        if not actor or len(actor) > 128:
            raise OrchestrationValidationError("Decision actor must contain 1 to 128 characters")
        digest = hashlib.sha256(self._json({
            "decision_key": decision_key,
            "decision_value": safe_value,
            "rationale": safe_rationale,
        }).encode("utf-8")).hexdigest()
        now = utc_now()
        with self._transaction() as db:
            plan = db.execute(
                "SELECT * FROM orchestration_plans WHERE task_id = ?", (task_id,),
            ).fetchone()
            if not plan:
                raise OrchestrationNotFoundError(task_id)
            if plan["state"] != PlanState.BLOCKED.value:
                raise OrchestrationConflictError(
                    "Task decisions may be recorded only while the plan is blocked"
                )
            stage = db.execute(
                """SELECT state FROM orchestration_stages
                   WHERE plan_id = ? AND stage_key = ?""",
                (plan["plan_id"], plan["current_stage_key"]),
            ).fetchone()
            if not stage or stage["state"] != StageState.BLOCKED.value:
                raise OrchestrationConflictError("Current stage is not blocked")
            latest = db.execute(
                """SELECT * FROM orchestration_decisions
                   WHERE plan_id = ? AND decision_key = ?
                   ORDER BY version DESC LIMIT 1""",
                (plan["plan_id"], decision_key),
            ).fetchone()
            if latest and latest["decision_sha256"] == digest:
                return self._decision(latest)
            version = int(latest["version"]) + 1 if latest else 1
            latest_rows = db.execute(
                """SELECT decision_key, decision_value, rationale, version, actor
                   FROM orchestration_decisions AS decision
                   WHERE plan_id = ? AND version = (
                       SELECT MAX(version) FROM orchestration_decisions
                       WHERE plan_id = decision.plan_id
                       AND decision_key = decision.decision_key
                   ) AND decision_key != ? ORDER BY decision_key""",
                (plan["plan_id"], decision_key),
            ).fetchall()
            decision_context = [dict(row) for row in latest_rows]
            decision_context.append({
                "decision_key": decision_key,
                "decision_value": safe_value,
                "rationale": safe_rationale,
                "version": version,
                "actor": actor,
            })
            decision_context.sort(key=lambda item: item["decision_key"])
            if len(self._json(decision_context)) > DECISION_CONTEXT_LIMIT:
                raise OrchestrationValidationError(
                    "Active Task decisions exceed the bounded prompt allocation"
                )
            decision_id = self._id("decision")
            db.execute(
                """INSERT INTO orchestration_decisions (
                       decision_id, plan_id, task_id, decision_key, decision_value,
                       rationale, decision_sha256, version, actor, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision_id, plan["plan_id"], task_id, decision_key, safe_value,
                    safe_rationale, digest, version, actor, now,
                ),
            )
            cursor = db.execute(
                """UPDATE orchestration_plans
                   SET version = version + 1, updated_at = ?
                   WHERE plan_id = ? AND version = ?""",
                (now, plan["plan_id"], plan["version"]),
            )
            if cursor.rowcount != 1:
                raise OrchestrationConflictError("Plan changed while recording the decision")
            self.tasks._insert_event(
                db,
                task_id=task_id,
                event_type="orchestration.decision_recorded",
                actor=actor,
                payload={
                    "plan_id": plan["plan_id"],
                    "decision_id": decision_id,
                    "decision_key": decision_key,
                    "decision_sha256": digest,
                    "version": version,
                },
                created_at=now,
            )
        return self.require_decision(decision_id)

    def require_decision(self, decision_id: str) -> TaskDecision:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM orchestration_decisions WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
        if not row:
            raise OrchestrationNotFoundError(decision_id)
        return self._decision(row)

    def claim_current_stage(
        self,
        task_id: str,
        *,
        prompt_sha256: str,
        operation_key: str,
        run_id: str | None = None,
        expected_stage_key: str | None = None,
        expected_adapter: str | None = None,
        actor: str = "orchestrator",
    ) -> OrchestrationRun:
        if run_id is not None and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", run_id
        ):
            raise OrchestrationValidationError("Run id is not a stable protocol identity")
        now = utc_now()
        with self._transaction() as db:
            plan = db.execute(
                "SELECT * FROM orchestration_plans WHERE task_id = ?", (task_id,),
            ).fetchone()
            if not plan:
                raise OrchestrationNotFoundError(task_id)
            existing = db.execute(
                "SELECT * FROM orchestration_runs WHERE operation_key = ?", (operation_key,),
            ).fetchone()
            if existing:
                raise OrchestrationConflictError(
                    f"Operation {operation_key} already claimed run {existing['run_id']}"
                )
            if plan["state"] != PlanState.ACTIVE.value:
                raise OrchestrationConflictError(f"Plan is {plan['state']}, not active")
            if (
                expected_stage_key is not None
                and plan["current_stage_key"] != expected_stage_key
            ):
                raise OrchestrationConflictError(
                    "Compatibility Plan route changed before the authoritative claim"
                )
            stage = db.execute(
                "SELECT * FROM orchestration_stages WHERE plan_id = ? AND stage_key = ?",
                (plan["plan_id"], plan["current_stage_key"]),
            ).fetchone()
            if not stage or stage["state"] != StageState.PENDING.value:
                raise OrchestrationConflictError("Current stage is not pending")
            if expected_adapter is not None and stage["adapter"] != expected_adapter:
                raise OrchestrationConflictError(
                    "Compatibility Stage adapter does not match the authoritative route"
                )
            previous_incomplete = db.execute(
                """SELECT 1 FROM orchestration_stages
                   WHERE plan_id = ? AND sequence < ? AND state != ? LIMIT 1""",
                (plan["plan_id"], stage["sequence"], StageState.PASSED.value),
            ).fetchone()
            if previous_incomplete:
                raise OrchestrationConflictError("A previous methodology stage has not passed")
            settled = db.execute(
                """SELECT COALESCE(SUM(
                           CASE
                               WHEN ledger.token_measurement = ? OR ledger.tokens IS NULL
                                   THEN runs.token_reserved
                               ELSE ledger.tokens
                           END
                       ), 0) AS total
                   FROM orchestration_usage_ledger AS ledger
                   JOIN orchestration_runs AS runs ON runs.run_id = ledger.run_id
                   WHERE ledger.plan_id = ? AND ledger.entry_type = ?""",
                (
                    Measurement.UNAVAILABLE.value,
                    plan["plan_id"],
                    LedgerEntryType.SETTLEMENT.value,
                ),
            ).fetchone()["total"]
            active_reserved = db.execute(
                """SELECT COALESCE(SUM(token_reserved), 0) AS total
                   FROM orchestration_runs WHERE plan_id = ? AND state = ?""",
                (plan["plan_id"], RunState.RUNNING.value),
            ).fetchone()["total"]
            if settled + active_reserved + stage["token_budget"] > plan["total_token_budget"]:
                raise OrchestrationConflictError("Token budget is exhausted; increase it before retrying")

            run_id = run_id or self._id("orun")
            attempt = int(stage["attempt_count"]) + 1
            db.execute(
                """
                INSERT INTO orchestration_runs (
                    run_id, plan_id, task_id, stage_key, adapter, state, operation_key,
                    prompt_sha256, findings, token_reserved, token_measurement,
                    cost_reserved_usd, cost_measurement, attempt, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, plan["plan_id"], task_id, stage["stage_key"], stage["adapter"],
                    RunState.RUNNING.value, operation_key, prompt_sha256, stage["token_budget"],
                    Measurement.UNAVAILABLE.value, stage["cost_budget_usd"],
                    Measurement.UNAVAILABLE.value, attempt, now,
                ),
            )
            db.execute(
                """UPDATE orchestration_stages
                   SET state = ?, attempt_count = ?, latest_run_id = ?, blockers = '[]', updated_at = ?
                   WHERE stage_id = ?""",
                (StageState.RUNNING.value, attempt, run_id, now, stage["stage_id"]),
            )
            db.execute(
                """UPDATE orchestration_plans SET version = version + 1, updated_at = ?
                   WHERE plan_id = ?""",
                (now, plan["plan_id"]),
            )
            db.execute(
                """
                INSERT INTO orchestration_usage_ledger (
                    entry_id, task_id, plan_id, stage_key, run_id, entry_type,
                    tokens, token_measurement, cost_usd, cost_measurement, adapter, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._id("usage"), task_id, plan["plan_id"], stage["stage_key"], run_id,
                    LedgerEntryType.RESERVATION.value, stage["token_budget"],
                    Measurement.UNAVAILABLE.value, stage["cost_budget_usd"],
                    Measurement.UNAVAILABLE.value, stage["adapter"], now,
                ),
            )
            self.tasks._insert_event(
                db, task_id=task_id, event_type="orchestration.run_started", actor=actor,
                payload={
                    "plan_id": plan["plan_id"], "run_id": run_id,
                    "stage_key": stage["stage_key"], "adapter": stage["adapter"],
                    "token_reserved": stage["token_budget"],
                    "cost_reserved_usd": stage["cost_budget_usd"],
                },
                created_at=now,
            )
            row = db.execute("SELECT * FROM orchestration_runs WHERE run_id = ?", (run_id,)).fetchone()
        assert row is not None
        return self._run(row)

    def attach_pid(self, run_id: str, pid: int) -> OrchestrationRun:
        with self._transaction() as db:
            cursor = db.execute(
                "UPDATE orchestration_runs SET pid = ? WHERE run_id = ? AND state = ? AND pid IS NULL",
                (pid, run_id, RunState.RUNNING.value),
            )
            if cursor.rowcount != 1:
                raise OrchestrationConflictError("Run is not attachable")
        return self.require_run(run_id)

    def finish_protocol_run(
        self,
        run_id: str,
        *,
        receipt: RunSettlementReceipt,
        adapter_result: AgentAdapterResult,
        exit_code: int | None,
        timed_out: bool,
        output: str,
        error_message: str | None,
        token_used: int | None,
        token_measurement: Measurement,
        actor: str = "orchestrator",
    ) -> OrchestrationRun:
        """Project an authoritative protocol settlement into the 0.5 ledger.

        This compatibility projection records dispatch/usage and advances the
        provisional Plan only after the frozen Control Plane Stage completed.
        It never parses runtime prose or makes an independent Gate decision.
        """

        if (
            receipt.run.run_id != run_id
            or adapter_result.protocol_state.run_id != run_id
            or receipt.run.protocol_state != adapter_result.protocol_state
        ):
            raise OrchestrationValidationError(
                "Protocol settlement does not match the operational Run"
            )
        now = utc_now()
        safe_output = redact_text(output)[-64 * 1024:]
        safe_error = redact_text(error_message) if error_message else None
        semantic_result = adapter_result.protocol_state.semantic_stage_result
        summary = (
            f"Formal protocol semantic={semantic_result.value}; "
            f"gate={receipt.gate.status.value}; stage={receipt.stage.status.value}."
        )
        findings = self._protocol_findings(receipt, adapter_result)
        blockers = self._protocol_blockers(receipt, adapter_result, safe_error)
        stage_completed = receipt.stage.status == StageStatus.COMPLETED
        if stage_completed:
            run_state = RunState.PASSED
        elif receipt.stage.status == StageStatus.FAILED:
            run_state = RunState.FAILED
        elif receipt.stage.status == StageStatus.CANCELLED:
            run_state = RunState.CANCELLED
        else:
            run_state = RunState.BLOCKED

        with self._transaction() as db:
            run = db.execute(
                "SELECT * FROM orchestration_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if not run:
                raise OrchestrationNotFoundError(run_id)
            if run["task_id"] != receipt.run.task_id or run["stage_key"] != receipt.run.stage_key:
                raise OrchestrationValidationError(
                    "Protocol settlement crosses the operational Task or Stage scope"
                )
            if run["state"] != RunState.RUNNING.value:
                settled = db.execute(
                    """SELECT 1 FROM orchestration_usage_ledger
                       WHERE run_id = ? AND entry_type = ?""",
                    (run_id, LedgerEntryType.SETTLEMENT.value),
                ).fetchone()
                if settled:
                    return self._run(run)
                raise OrchestrationConflictError(
                    "Terminal protocol projection is missing its usage settlement"
                )
            stage = db.execute(
                "SELECT * FROM orchestration_stages WHERE plan_id = ? AND stage_key = ?",
                (run["plan_id"], run["stage_key"]),
            ).fetchone()
            assert stage is not None
            if stage["state"] != StageState.RUNNING.value:
                raise OrchestrationConflictError(
                    "Operational Stage is not running during protocol projection"
                )
            db.execute(
                """
                UPDATE orchestration_runs
                SET state = ?, exit_code = ?, timed_out = ?, output = ?, error_message = ?,
                    semantic_status = ?, semantic_summary = ?, findings = ?,
                    token_used = ?, token_measurement = ?, cost_used_usd = NULL,
                    cost_measurement = ?, finished_at = ?
                WHERE run_id = ?
                """,
                (
                    run_state.value,
                    exit_code,
                    int(timed_out),
                    safe_output,
                    safe_error,
                    ("pass" if stage_completed else "blocked"),
                    summary,
                    self._json(sanitize_data(findings)),
                    token_used,
                    token_measurement.value,
                    Measurement.UNAVAILABLE.value,
                    now,
                    run_id,
                ),
            )
            db.execute(
                """
                INSERT INTO orchestration_usage_ledger (
                    entry_id, task_id, plan_id, stage_key, run_id, entry_type,
                    tokens, token_measurement, cost_usd, cost_measurement, adapter, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    self._id("usage"),
                    run["task_id"],
                    run["plan_id"],
                    run["stage_key"],
                    run_id,
                    LedgerEntryType.SETTLEMENT.value,
                    token_used,
                    token_measurement.value,
                    Measurement.UNAVAILABLE.value,
                    run["adapter"],
                    now,
                ),
            )
            db.execute(
                """UPDATE orchestration_stages
                   SET state = ?, semantic_summary = ?, blockers = ?, updated_at = ?
                   WHERE stage_id = ?""",
                (
                    (StageState.PASSED if stage_completed else StageState.BLOCKED).value,
                    summary,
                    self._json(sanitize_data(blockers)),
                    now,
                    stage["stage_id"],
                ),
            )
            if stage_completed:
                route = receipt.next_stage_route
                if route is None:
                    plan_state = PlanState.AWAITING_APPROVAL
                    next_key = None
                else:
                    next_stage = db.execute(
                        """SELECT * FROM orchestration_stages
                           WHERE plan_id = ? AND stage_key = ?""",
                        (run["plan_id"], route.stage_key),
                    ).fetchone()
                    if next_stage is None or next_stage["sequence"] <= stage["sequence"]:
                        raise OrchestrationValidationError(
                            "Authoritative next Stage route is absent or out of order in "
                            "the compatibility ledger"
                        )
                    if (
                        next_stage["adapter"] != route.runtime
                        or next_stage["role"] != route.role
                        or next_stage["title"] != route.title
                    ):
                        raise OrchestrationValidationError(
                            "Authoritative next Stage route does not match compatibility "
                            "Stage metadata"
                        )
                    plan_state = (
                        PlanState.ACTIVE
                        if route.stage_status in {StageStatus.READY, StageStatus.RUNNING}
                        else PlanState.BLOCKED
                    )
                    next_key = route.stage_key
            else:
                plan_state = PlanState.BLOCKED
                next_key = stage["stage_key"]
            db.execute(
                """UPDATE orchestration_plans
                   SET state = ?, current_stage_key = ?, version = version + 1, updated_at = ?
                   WHERE plan_id = ?""",
                (plan_state.value, next_key, now, run["plan_id"]),
            )
            self.tasks._insert_event(
                db,
                task_id=run["task_id"],
                event_type="orchestration.protocol_run_projected",
                actor=actor,
                payload={
                    "plan_id": run["plan_id"],
                    "run_id": run_id,
                    "stage_key": run["stage_key"],
                    "protocol_semantic_result": semantic_result.value,
                    "gate_status": receipt.gate.status.value,
                    "control_stage_status": receipt.stage.status.value,
                    "operational_run_state": run_state.value,
                    "token_used": token_used,
                    "token_measurement": token_measurement.value,
                    "cost_measurement": Measurement.UNAVAILABLE.value,
                },
                created_at=now,
            )
        return self.require_run(run_id)

    def finish_run(
        self,
        run_id: str,
        *,
        exit_code: int | None,
        timed_out: bool,
        output: str,
        error_message: str | None,
        semantic: SemanticResult | None,
        token_used: int | None,
        token_measurement: Measurement = Measurement.ESTIMATED,
        actor: str = "orchestrator",
    ) -> OrchestrationRun:
        now = utc_now()
        safe_output = redact_text(output)[-64 * 1024:]
        safe_error = redact_text(error_message) if error_message else None
        safe_summary = redact_text(semantic.summary) if semantic else None
        safe_findings = [redact_text(item) for item in semantic.findings] if semantic else []
        safe_next_action = (
            redact_text(semantic.recommended_next_action) if semantic else None
        )
        with self._transaction() as db:
            run = db.execute("SELECT * FROM orchestration_runs WHERE run_id = ?", (run_id,)).fetchone()
            if not run:
                raise OrchestrationNotFoundError(run_id)
            if run["state"] != RunState.RUNNING.value:
                raise OrchestrationConflictError("Run is already terminal")
            stage = db.execute(
                "SELECT * FROM orchestration_stages WHERE plan_id = ? AND stage_key = ?",
                (run["plan_id"], run["stage_key"]),
            ).fetchone()
            assert stage is not None
            blockers: list[str] = []
            if timed_out:
                blockers.append(safe_error or "Runtime process timed out")
            if exit_code != 0:
                process_error = safe_error or "Runtime process did not exit successfully"
                if process_error not in blockers:
                    blockers.append(process_error)
            if semantic is None:
                blockers.append("Runtime output did not match the required semantic result schema")
            elif semantic.status.value != "pass":
                blockers.extend(safe_findings or [safe_next_action or "Runtime requested review"])
            if token_used is not None and token_used > run["token_reserved"]:
                blockers.append(
                    f"Estimated token use {token_used} exceeded the reserved {run['token_reserved']} tokens"
                )
            passed = not blockers
            run_state = RunState.PASSED if passed else (
                RunState.FAILED if timed_out or exit_code != 0 else RunState.BLOCKED
            )
            db.execute(
                """
                UPDATE orchestration_runs
                SET state = ?, exit_code = ?, timed_out = ?, output = ?, error_message = ?,
                    semantic_status = ?, semantic_summary = ?, findings = ?,
                    token_used = ?, token_measurement = ?, cost_used_usd = NULL,
                    cost_measurement = ?, finished_at = ?
                WHERE run_id = ?
                """,
                (
                    run_state.value, exit_code, int(timed_out), safe_output, safe_error,
                    semantic.status.value if semantic else None, safe_summary,
                    self._json(sanitize_data(safe_findings)), token_used,
                    token_measurement.value, Measurement.UNAVAILABLE.value, now, run_id,
                ),
            )
            db.execute(
                """
                INSERT INTO orchestration_usage_ledger (
                    entry_id, task_id, plan_id, stage_key, run_id, entry_type,
                    tokens, token_measurement, cost_usd, cost_measurement, adapter, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    self._id("usage"), run["task_id"], run["plan_id"], run["stage_key"],
                    run_id, LedgerEntryType.SETTLEMENT.value, token_used,
                    token_measurement.value, Measurement.UNAVAILABLE.value,
                    run["adapter"], now,
                ),
            )
            stage_state = StageState.PASSED if passed else StageState.BLOCKED
            db.execute(
                """UPDATE orchestration_stages
                   SET state = ?, semantic_summary = ?, blockers = ?, updated_at = ?
                   WHERE stage_id = ?""",
                (
                    stage_state.value, safe_summary,
                    self._json(sanitize_data(blockers)), now, stage["stage_id"],
                ),
            )
            if passed:
                next_stage = db.execute(
                    """SELECT stage_key FROM orchestration_stages
                       WHERE plan_id = ? AND sequence > ? ORDER BY sequence LIMIT 1""",
                    (run["plan_id"], stage["sequence"]),
                ).fetchone()
                plan_state = PlanState.ACTIVE if next_stage else PlanState.AWAITING_APPROVAL
                next_key = next_stage["stage_key"] if next_stage else None
            else:
                plan_state = PlanState.BLOCKED
                next_key = stage["stage_key"]
            db.execute(
                """UPDATE orchestration_plans
                   SET state = ?, current_stage_key = ?, version = version + 1, updated_at = ?
                   WHERE plan_id = ?""",
                (plan_state.value, next_key, now, run["plan_id"]),
            )
            self.tasks._insert_event(
                db, task_id=run["task_id"], event_type="orchestration.run_finished", actor=actor,
                payload={
                    "plan_id": run["plan_id"], "run_id": run_id,
                    "stage_key": run["stage_key"], "process_exit_code": exit_code,
                    "timed_out": timed_out,
                    "semantic_status": semantic.status.value if semantic else None,
                    "stage_state": stage_state.value, "token_used": token_used,
                    "token_measurement": token_measurement.value,
                    "cost_measurement": Measurement.UNAVAILABLE.value,
                    "blockers": sanitize_data(blockers),
                },
                created_at=now,
            )
        return self.require_run(run_id)

    def mark_interrupted(self, run_id: str, *, reason: str) -> OrchestrationRun:
        now = utc_now()
        safe_reason = redact_text(reason)
        with self._transaction() as db:
            run = db.execute("SELECT * FROM orchestration_runs WHERE run_id = ?", (run_id,)).fetchone()
            if not run:
                raise OrchestrationNotFoundError(run_id)
            if run["state"] != RunState.RUNNING.value:
                return self._run(run)
            db.execute(
                """UPDATE orchestration_runs
                   SET state = ?, error_message = ?, token_used = NULL,
                       token_measurement = ?, cost_used_usd = NULL,
                       cost_measurement = ?, finished_at = ?
                   WHERE run_id = ?""",
                (
                    RunState.INTERRUPTED.value, safe_reason,
                    Measurement.UNAVAILABLE.value, Measurement.UNAVAILABLE.value,
                    now, run_id,
                ),
            )
            db.execute(
                """INSERT INTO orchestration_usage_ledger (
                       entry_id, task_id, plan_id, stage_key, run_id, entry_type,
                       tokens, token_measurement, cost_usd, cost_measurement,
                       adapter, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?, ?, ?)""",
                (
                    self._id("usage"), run["task_id"], run["plan_id"],
                    run["stage_key"], run_id, LedgerEntryType.SETTLEMENT.value,
                    Measurement.UNAVAILABLE.value, Measurement.UNAVAILABLE.value,
                    run["adapter"], now,
                ),
            )
            db.execute(
                """UPDATE orchestration_stages SET state = ?, blockers = ?, updated_at = ?
                   WHERE plan_id = ? AND stage_key = ?""",
                (
                    StageState.BLOCKED.value, self._json([safe_reason]), now,
                    run["plan_id"], run["stage_key"],
                ),
            )
            db.execute(
                """UPDATE orchestration_plans SET state = ?, current_stage_key = ?,
                   version = version + 1, updated_at = ? WHERE plan_id = ?""",
                (PlanState.BLOCKED.value, run["stage_key"], now, run["plan_id"]),
            )
            self.tasks._insert_event(
                db, task_id=run["task_id"], event_type="orchestration.run_interrupted",
                actor="orchestrator",
                payload={
                    "plan_id": run["plan_id"], "run_id": run_id,
                    "stage_key": run["stage_key"], "reason": safe_reason,
                    "token_used": None,
                    "token_measurement": Measurement.UNAVAILABLE.value,
                    "cost_measurement": Measurement.UNAVAILABLE.value,
                },
                created_at=now,
            )
        return self.require_run(run_id)

    @staticmethod
    def _protocol_findings(
        receipt: RunSettlementReceipt,
        adapter_result: AgentAdapterResult,
    ) -> list[str]:
        findings: list[str] = []
        if adapter_result.error_code is not None:
            findings.append(f"Protocol adapter error: {adapter_result.error_code.value}")
        handoff = adapter_result.handoff_pack
        if handoff is not None:
            findings.extend(
                f"Unresolved question {item.question_id}: {item.question}"
                for item in handoff.unresolved_questions
            )
            if handoff.blocker_requirement_ids:
                findings.append(
                    "Handoff blocker requirements: "
                    + ", ".join(sorted(handoff.blocker_requirement_ids))
                )
        evaluation = receipt.gate.last_evaluation
        if evaluation and evaluation.blocker_requirement_ids:
            findings.append(
                "Formal Gate blockers: "
                + ", ".join(sorted(evaluation.blocker_requirement_ids))
            )
        return findings

    @staticmethod
    def _protocol_blockers(
        receipt: RunSettlementReceipt,
        adapter_result: AgentAdapterResult,
        safe_error: str | None,
    ) -> list[str]:
        blockers: list[str] = []
        if safe_error:
            blockers.append(safe_error)
        if adapter_result.error_code is not None:
            blockers.append(f"Protocol adapter error: {adapter_result.error_code.value}")
        semantic = adapter_result.protocol_state.semantic_stage_result
        if semantic != SemanticStageResult.SUCCEEDED:
            blockers.append(f"Formal semantic result is {semantic.value}")
        if receipt.gate.status != GateStatus.PASSED:
            evaluation = receipt.gate.last_evaluation
            requirement_by_id = {
                item.requirement_id: item for item in receipt.gate.requirements
            }
            actions = [
                requirement_by_id[item].failure_action
                for item in sorted(evaluation.blocker_requirement_ids if evaluation else [])
                if item in requirement_by_id
            ]
            blockers.extend(actions or [f"Formal Gate is {receipt.gate.status.value}"])
        if receipt.stage.status != StageStatus.COMPLETED and not blockers:
            blockers.append(f"Authoritative Stage is {receipt.stage.status.value}")
        return list(dict.fromkeys(blockers))

    def retry(self, task_id: str, stage_key: str, *, actor: str = "user") -> OrchestrationPlan:
        now = utc_now()
        with self._transaction() as db:
            plan = db.execute(
                "SELECT * FROM orchestration_plans WHERE task_id = ?", (task_id,),
            ).fetchone()
            if not plan:
                raise OrchestrationNotFoundError(task_id)
            stage = db.execute(
                "SELECT * FROM orchestration_stages WHERE plan_id = ? AND stage_key = ?",
                (plan["plan_id"], stage_key),
            ).fetchone()
            if not stage:
                raise OrchestrationValidationError(f"Unknown stage: {stage_key}")
            if stage["state"] != StageState.BLOCKED.value:
                raise OrchestrationConflictError("Only a blocked stage may be retried")
            db.execute(
                """UPDATE orchestration_stages
                   SET state = ?, semantic_summary = NULL, blockers = '[]', updated_at = ?
                   WHERE plan_id = ? AND sequence >= ?""",
                (StageState.PENDING.value, now, plan["plan_id"], stage["sequence"]),
            )
            db.execute(
                """UPDATE orchestration_plans SET state = ?, current_stage_key = ?,
                   version = version + 1, updated_at = ? WHERE plan_id = ?""",
                (PlanState.ACTIVE.value, stage_key, now, plan["plan_id"]),
            )
            self.tasks._insert_event(
                db, task_id=task_id, event_type="orchestration.stage_retry_requested", actor=actor,
                payload={"plan_id": plan["plan_id"], "stage_key": stage_key}, created_at=now,
            )
        return self.require_plan(task_id)

    def approve(self, task_id: str, *, actor: str, reason: str) -> OrchestrationPlan:
        if not reason.strip():
            raise OrchestrationValidationError("Approval reason may not be blank")
        now = utc_now()
        with self._transaction() as db:
            plan = db.execute(
                "SELECT * FROM orchestration_plans WHERE task_id = ?", (task_id,),
            ).fetchone()
            if not plan:
                raise OrchestrationNotFoundError(task_id)
            if plan["state"] == PlanState.READY_FOR_IMPLEMENTATION.value:
                return self._plan(plan)
            if plan["state"] != PlanState.AWAITING_APPROVAL.value:
                raise OrchestrationConflictError("Plan is not awaiting human approval")
            db.execute(
                """UPDATE orchestration_plans
                   SET state = ?, approved_at = ?, approved_by = ?, version = version + 1, updated_at = ?
                   WHERE plan_id = ?""",
                (PlanState.READY_FOR_IMPLEMENTATION.value, now, actor, now, plan["plan_id"]),
            )
            self.tasks._insert_event(
                db, task_id=task_id, event_type="orchestration.plan_approved", actor=actor,
                payload={"plan_id": plan["plan_id"], "reason": redact_text(reason)}, created_at=now,
            )
        return self.require_plan(task_id)

    def require_run(self, run_id: str) -> OrchestrationRun:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM orchestration_runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            raise OrchestrationNotFoundError(run_id)
        return self._run(row)

    def status(self, task_id: str) -> TaskOrchestrationStatus:
        with closing(self._connect()) as db:
            db.execute("BEGIN")
            try:
                return self._status_snapshot(db, task_id)
            finally:
                db.rollback()

    def _status_snapshot(
        self,
        db: sqlite3.Connection,
        task_id: str,
    ) -> TaskOrchestrationStatus:
        """Read the compatibility projection from one caller-owned snapshot."""

        plan_row = db.execute(
            "SELECT * FROM orchestration_plans WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if plan_row is None:
            raise OrchestrationNotFoundError(task_id)
        plan = self._plan(plan_row)
        stages = [
            self._stage(row)
            for row in db.execute(
                "SELECT * FROM orchestration_stages WHERE plan_id = ? ORDER BY sequence",
                (plan.plan_id,),
            ).fetchall()
        ]
        runs = [
            self._run(row)
            for row in db.execute(
                "SELECT * FROM orchestration_runs WHERE plan_id = ? ORDER BY rowid",
                (plan.plan_id,),
            ).fetchall()
        ]
        usage = [
            self._usage(row)
            for row in db.execute(
                """SELECT * FROM orchestration_usage_ledger
                   WHERE plan_id = ? ORDER BY rowid""",
                (plan.plan_id,),
            ).fetchall()
        ]
        decisions = [
            self._decision(row)
            for row in db.execute(
                """SELECT * FROM orchestration_decisions
                   WHERE plan_id = ? ORDER BY decision_key, version""",
                (plan.plan_id,),
            ).fetchall()
        ]
        return self._status_from_records(plan, stages, runs, usage, decisions)

    @classmethod
    def _status_from_records(
        cls,
        plan: OrchestrationPlan,
        stages: list[OrchestrationStage],
        runs: list[OrchestrationRun],
        usage: list[UsageLedgerEntry],
        decisions: list[TaskDecision],
    ) -> TaskOrchestrationStatus:
        reservations = sum(run.token_reserved for run in runs if run.state == RunState.RUNNING)
        settlement_entries = [
            item for item in usage if item.entry_type == LedgerEntryType.SETTLEMENT
        ]
        token_measurement = (
            Measurement.UNAVAILABLE
            if any(item.token_measurement == Measurement.UNAVAILABLE for item in settlement_entries)
            else Measurement.ESTIMATED
            if any(item.token_measurement == Measurement.ESTIMATED for item in settlement_entries)
            else Measurement.EXACT
        )
        known_used = sum(
            item.tokens or 0 for item in usage if item.entry_type == LedgerEntryType.SETTLEMENT
        )
        used = None if token_measurement == Measurement.UNAVAILABLE else known_used
        cost_entries = [
            item for item in usage
            if item.entry_type == LedgerEntryType.SETTLEMENT and item.cost_usd is not None
        ]
        cost_used = sum(item.cost_usd or 0 for item in cost_entries) if cost_entries else None
        cost_measurement = (
            Measurement.EXACT if cost_entries and all(item.cost_measurement == Measurement.EXACT for item in cost_entries)
            else Measurement.ESTIMATED if cost_entries else Measurement.UNAVAILABLE
        )
        return TaskOrchestrationStatus(
            plan=plan, stages=stages, runs=runs, usage=usage, decisions=decisions,
            tokens_reserved=reservations,
            tokens_used=used,
            token_measurement=token_measurement,
            tokens_remaining=(
                None
                if used is None
                else max(0, plan.total_token_budget - reservations - used)
            ),
            cost_used_usd=cost_used, cost_measurement=cost_measurement,
            next_safe_action=cls._next_action(plan, stages),
        )

    @staticmethod
    def _next_action(plan: OrchestrationPlan, stages: list[OrchestrationStage]) -> str:
        if plan.state == PlanState.READY_FOR_IMPLEMENTATION:
            return "The reviewed plan is ready for a later implementation workflow."
        if plan.state == PlanState.AWAITING_APPROVAL:
            return "Review the three results and explicitly approve or reject the plan."
        if plan.state == PlanState.BLOCKED:
            blocked = next((stage for stage in stages if stage.state == StageState.BLOCKED), None)
            return f"Resolve blockers and retry stage {blocked.stage_key}." if blocked else "Resolve plan blockers."
        running = next((stage for stage in stages if stage.state == StageState.RUNNING), None)
        if running:
            return f"Wait for {running.adapter} to finish stage {running.stage_key}."
        pending = next((stage for stage in stages if stage.state == StageState.PENDING), None)
        return f"Run stage {pending.stage_key} with {pending.adapter}." if pending else "Inspect plan state."

    @staticmethod
    def _allocate_int(total: int, weights: list[int]) -> list[int]:
        values = [(total * weight) // 100 for weight in weights]
        values[-1] += total - sum(values)
        return values

    @staticmethod
    def _allocate_float(total: float, weights: list[int]) -> list[float]:
        values = [round(total * weight / 100, 6) for weight in weights]
        values[-1] = round(values[-1] + total - sum(values), 6)
        return values

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex}"

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _plan(row: sqlite3.Row) -> OrchestrationPlan:
        return OrchestrationPlan(
            plan_id=row["plan_id"], task_id=row["task_id"], project_id=row["project_id"],
            methodology_id=row["methodology_id"], methodology_version=row["methodology_version"],
            methodology_sha256=row["methodology_sha256"], provisional=bool(row["provisional"]),
            state=row["state"], total_token_budget=row["total_token_budget"],
            total_cost_budget_usd=row["total_cost_budget_usd"],
            current_stage_key=row["current_stage_key"], version=row["version"],
            created_at=row["created_at"], updated_at=row["updated_at"],
            approved_at=row["approved_at"], approved_by=row["approved_by"],
        )

    @staticmethod
    def _stage(row: sqlite3.Row) -> OrchestrationStage:
        return OrchestrationStage(
            stage_id=row["stage_id"], plan_id=row["plan_id"], stage_key=row["stage_key"],
            sequence=row["sequence"], title=row["title"], role=row["role"], adapter=row["adapter"],
            state=row["state"], token_budget=row["token_budget"],
            cost_budget_usd=row["cost_budget_usd"], attempt_count=row["attempt_count"],
            latest_run_id=row["latest_run_id"], semantic_summary=row["semantic_summary"],
            blockers=json.loads(row["blockers"]), updated_at=row["updated_at"],
        )

    @staticmethod
    def _run(row: sqlite3.Row) -> OrchestrationRun:
        return OrchestrationRun(
            run_id=row["run_id"], plan_id=row["plan_id"], task_id=row["task_id"],
            stage_key=row["stage_key"], adapter=row["adapter"], state=row["state"],
            operation_key=row["operation_key"], prompt_sha256=row["prompt_sha256"], pid=row["pid"],
            exit_code=row["exit_code"], timed_out=bool(row["timed_out"]),
            output=row["output"], error_message=row["error_message"],
            semantic_status=row["semantic_status"], semantic_summary=row["semantic_summary"],
            findings=json.loads(row["findings"]), token_reserved=row["token_reserved"],
            token_used=row["token_used"], token_measurement=row["token_measurement"],
            cost_reserved_usd=row["cost_reserved_usd"], cost_used_usd=row["cost_used_usd"],
            cost_measurement=row["cost_measurement"], attempt=row["attempt"],
            started_at=row["started_at"], finished_at=row["finished_at"],
        )

    @staticmethod
    def _usage(row: sqlite3.Row) -> UsageLedgerEntry:
        return UsageLedgerEntry(
            entry_id=row["entry_id"], task_id=row["task_id"], plan_id=row["plan_id"],
            stage_key=row["stage_key"], run_id=row["run_id"], entry_type=row["entry_type"],
            tokens=row["tokens"], token_measurement=row["token_measurement"],
            cost_usd=row["cost_usd"], cost_measurement=row["cost_measurement"],
            adapter=row["adapter"], created_at=row["created_at"],
        )

    @staticmethod
    def _decision(row: sqlite3.Row) -> TaskDecision:
        return TaskDecision(
            decision_id=row["decision_id"], plan_id=row["plan_id"],
            task_id=row["task_id"], decision_key=row["decision_key"],
            decision_value=row["decision_value"], rationale=row["rationale"],
            decision_sha256=row["decision_sha256"], version=row["version"],
            actor=row["actor"], created_at=row["created_at"],
        )
