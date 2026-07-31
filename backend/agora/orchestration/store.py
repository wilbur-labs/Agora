"""Transactional persistence for methodology plans, stages, runs, and usage."""
from __future__ import annotations

import json
import hashlib
import math
import re
import sqlite3
import uuid
from contextlib import closing, contextmanager
from typing import Any, Callable, Iterator

from agora.control_plane.auth import ControlPrincipal
from agora.control_plane.models import (
    RunSettlementReceipt,
    StageRouteDecision,
    TaskRecord,
)
from agora.control_plane.store import ControlPlaneConflictError, ControlPlaneStore
from agora.execution.security import redact_text, sanitize_data
from agora.protocol.agent_adapter import AgentAdapterResult
from agora.protocol.hashing import canonical_sha256, seal_model_payload
from agora.protocol.methodology_execution import MethodologyExecutionContract
from agora.protocol.models import (
    ConsultationCandidate,
    ConsultationCandidateDisposition,
    ContextPack,
    GateRequirement,
    PinnedRuntimePreflightDecision,
    ProcessStatus,
    ProviderUsageObservation,
    SchemaStatus,
    SemanticStageResult,
    StageInventory,
)
from agora.protocol.methodology_migration import (
    AuthenticatedMethodologyMigrationGate,
    MethodologyMigrationActivationReceipt,
    MethodologyMigrationPreviewRequest,
)
from agora.protocol.methodology_route_activation import (
    MethodologyRouteActivationReceipt,
    MethodologyRouteActivationRequest,
    MethodologySeedArtifactRegistration,
)
from agora.protocol.methodology_run_claim import (
    MethodologyRunClaimReceipt,
    MethodologyRunClaimRequest,
)
from agora.protocol.methodology_run_dispatch import (
    MethodologyRunDispatchClaim,
    MethodologyRunDispatchPolicyDecision,
    MethodologyRunDispatchReceipt,
)
from agora.protocol.methodology_stage_gate import (
    MethodologyStageGateReceipt,
    MethodologyStageGateRequest,
)
from agora.protocol.state_machines import GateStatus, StageStatus
from agora.tasks.models import TaskBudget, TaskState, utc_now
from agora.tasks.store import TaskNotFoundError, TaskStore

from .contracts import TaskContract, contract_sha256
from .consultation import ConsultationAdapterResult
from .methodology import MethodologyDefinition, methodology_sha256
from .methodology_migration_activation import (
    MethodologySuccessorMaterialization,
)
from .methodology_execution_contract import MethodologyExecutionSnapshot
from .methodology_route_activation import MethodologyRouteActivationSnapshot
from .methodology_run_claim import MethodologyRunClaimSnapshot
from .methodology_run_dispatch import MethodologyRunDispatchSnapshot
from .methodology_stage_gate import MethodologyStageGateSnapshot
from .models import (
    BudgetAmendment,
    ConsultationRun,
    ConsultationState,
    LedgerEntryType,
    Measurement,
    MethodologyDispatchState,
    MethodologyMigrationStateSnapshot,
    MethodologyRunDispatchState,
    OrchestrationPlan,
    OrchestrationRun,
    OrchestrationStage,
    PlanState,
    RoutingPolicyDecision,
    RunState,
    SemanticResult,
    StageState,
    TaskDecision,
    TaskOrchestrationStatus,
    UsageLedgerEntry,
)
from .routing_policy import (
    REVIEWER_ROLES,
    RoutingStageBudget,
    derive_routing_policy_decision,
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

    def consultations(self, plan_id: str) -> list[ConsultationRun]:
        with closing(self._connect()) as db:
            rows = db.execute(
                """SELECT * FROM orchestration_consultations
                   WHERE plan_id = ? ORDER BY started_at, consultation_id""",
                (plan_id,),
            ).fetchall()
        return [self._consultation(row) for row in rows]

    def consultation_candidates(self, plan_id: str) -> list[ConsultationCandidate]:
        with closing(self._connect()) as db:
            rows = db.execute(
                """SELECT * FROM orchestration_consultation_candidates
                   WHERE plan_id = ? ORDER BY created_at, candidate_id""",
                (plan_id,),
            ).fetchall()
        return [self._consultation_candidate(row) for row in rows]

    def consultation_candidate_dispositions(
        self,
        plan_id: str,
    ) -> list[ConsultationCandidateDisposition]:
        with closing(self._connect()) as db:
            rows = db.execute(
                """SELECT * FROM orchestration_candidate_dispositions
                   WHERE plan_id = ? ORDER BY created_at, disposition_id""",
                (plan_id,),
            ).fetchall()
        return [self._candidate_disposition(row) for row in rows]

    def budget_amendments(self, plan_id: str) -> list[BudgetAmendment]:
        with closing(self._connect()) as db:
            rows = db.execute(
                """SELECT * FROM orchestration_budget_amendments
                   WHERE plan_id = ? ORDER BY version""",
                (plan_id,),
            ).fetchall()
        return [self._budget_amendment(row) for row in rows]

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

    def claim_consultation(
        self,
        task_id: str,
        *,
        route: StageRouteDecision,
        repository_id: str,
        repository_ref: str,
        repository_commit: str,
        expected_plan_version: int,
        decision_key: str,
        prompt_sha256: str,
        token_reserved: int,
        cost_reserved_usd: float | None,
        operation_key: str,
        actor: str = "orchestrator",
    ) -> tuple[ConsultationRun, bool]:
        """Reserve one advisory native consultation without claiming a Stage."""

        operation_key = operation_key.strip()
        actor = actor.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", operation_key):
            raise OrchestrationValidationError(
                "Consultation operation_key must be a stable identifier"
            )
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,127}", decision_key):
            raise OrchestrationValidationError("Invalid consultation decision key")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", repository_id):
            raise OrchestrationValidationError(
                "Consultation repository ID must be a stable identifier"
            )
        if not repository_ref or len(repository_ref) > 20_000:
            raise OrchestrationValidationError(
                "Consultation repository ref must be bounded and non-empty"
            )
        if not re.fullmatch(r"[0-9a-f]{7,64}", repository_commit):
            raise OrchestrationValidationError(
                "Consultation repository commit must be lowercase Git hex"
            )
        if not re.fullmatch(r"[0-9a-f]{64}", prompt_sha256):
            raise OrchestrationValidationError(
                "Consultation prompt hash must be lowercase SHA-256"
            )
        if expected_plan_version < 1:
            raise OrchestrationValidationError(
                "Consultation expected Plan version must be positive"
            )
        if not 1 <= token_reserved <= 1_000_000:
            raise OrchestrationValidationError(
                "Consultation Token reservation must be between 1 and 1000000"
            )
        if cost_reserved_usd is not None and (
            not math.isfinite(cost_reserved_usd) or cost_reserved_usd < 0
        ):
            raise OrchestrationValidationError(
                "Consultation cost reservation must be finite and nonnegative"
            )
        if not actor or len(actor) > 128:
            raise OrchestrationValidationError(
                "Consultation actor must contain 1 to 128 characters"
            )

        consultation_id = self._id("consultation")
        now = utc_now()
        with self._transaction() as db:
            replay_row = db.execute(
                """SELECT * FROM orchestration_consultations
                   WHERE operation_key = ?""",
                (operation_key,),
            ).fetchone()
            if replay_row is not None:
                replay = self._consultation(replay_row)
                if not (
                    replay.task_id == task_id
                    and replay.plan_version_observed == expected_plan_version
                    and replay.inventory_id == route.inventory_id
                    and replay.inventory_sha256 == route.inventory_sha256
                    and replay.stage_key == route.stage_key
                    and replay.role == route.role
                    and replay.runtime == route.runtime
                    and replay.repository_id == repository_id
                    and replay.repository_ref == repository_ref
                    and replay.repository_commit == repository_commit
                    and replay.decision_key == decision_key
                    and replay.prompt_sha256 == prompt_sha256
                    and replay.token_reserved == token_reserved
                    and replay.cost_reserved_usd == cost_reserved_usd
                ):
                    raise OrchestrationConflictError(
                        "Consultation operation_key was already used for different inputs"
                    )
                return replay, True

            plan = db.execute(
                "SELECT * FROM orchestration_plans WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if plan is None:
                raise OrchestrationNotFoundError(task_id)
            if plan["version"] != expected_plan_version:
                raise OrchestrationConflictError(
                    "Plan version changed before consultation claim"
                )
            if plan["state"] not in {
                PlanState.ACTIVE.value,
                PlanState.BLOCKED.value,
            }:
                raise OrchestrationConflictError(
                    "Consultation requires an active or blocked Plan"
                )
            try:
                current_route = ControlPlaneStore(self.tasks).stage_route_snapshot(
                    db,
                    task_id,
                )
            except ControlPlaneConflictError as exc:
                raise OrchestrationConflictError(str(exc)) from exc
            if current_route != route:
                raise OrchestrationConflictError(
                    "Authoritative Stage route changed before consultation claim"
                )
            active_formal = db.execute(
                """SELECT run_id FROM orchestration_runs
                   WHERE plan_id = ? AND state = ? LIMIT 1""",
                (plan["plan_id"], RunState.RUNNING.value),
            ).fetchone()
            active_consultation = db.execute(
                """SELECT consultation_id FROM orchestration_consultations
                   WHERE plan_id = ? AND state = ? LIMIT 1""",
                (plan["plan_id"], ConsultationState.RUNNING.value),
            ).fetchone()
            if active_formal is not None or active_consultation is not None:
                raise OrchestrationConflictError(
                    "A formal Run or consultation is already active for this Plan"
                )

            stage = db.execute(
                """SELECT * FROM orchestration_stages
                   WHERE plan_id = ? AND stage_key = ?""",
                (plan["plan_id"], plan["current_stage_key"]),
            ).fetchone()
            valid_stage = stage is not None and (
                (
                    plan["state"] == PlanState.ACTIVE.value
                    and stage["state"] == StageState.PENDING.value
                )
                or (
                    plan["state"] == PlanState.BLOCKED.value
                    and stage["state"] == StageState.BLOCKED.value
                )
            )
            if not valid_stage or (
                stage["stage_key"] != route.stage_key
                or stage["role"] != route.role
                or stage["adapter"] != route.runtime
            ):
                raise OrchestrationConflictError(
                    "Compatibility Stage does not match the consultation route"
                )
            budget = self._provider_budget_snapshot(db, plan["plan_id"])
            protected_rows = db.execute(
                """SELECT token_budget, cost_budget_usd
                   FROM orchestration_stages
                   WHERE plan_id = ? AND state != ? AND role IN (?, ?)""",
                (
                    plan["plan_id"],
                    StageState.PASSED.value,
                    *sorted(REVIEWER_ROLES),
                ),
            ).fetchall()
            protected_tokens = sum(row["token_budget"] for row in protected_rows)
            available_tokens = (
                plan["total_token_budget"]
                - budget["settled_tokens"]
                - budget["active_tokens"]
                - protected_tokens
            )
            if token_reserved > available_tokens:
                raise OrchestrationConflictError(
                    "Consultation reservation would consume protected reviewer Tokens"
                )
            if plan["total_cost_budget_usd"] is None:
                if cost_reserved_usd is not None:
                    raise OrchestrationConflictError(
                        "Unbounded Task cost may not claim a bounded consultation cost"
                    )
            else:
                if cost_reserved_usd is None:
                    raise OrchestrationConflictError(
                        "A cost-bounded Task requires a consultation cost reservation"
                    )
                if any(row["cost_budget_usd"] is None for row in protected_rows):
                    raise OrchestrationConflictError(
                        "Protected reviewer cost allocation is unavailable"
                    )
                protected_cost = sum(
                    row["cost_budget_usd"] for row in protected_rows
                )
                available_cost = (
                    plan["total_cost_budget_usd"]
                    - budget["settled_cost"]
                    - budget["active_cost"]
                    - protected_cost
                )
                if cost_reserved_usd > available_cost + 1e-9:
                    raise OrchestrationConflictError(
                        "Consultation reservation would consume protected reviewer cost"
                    )

            db.execute(
                """INSERT INTO orchestration_consultations (
                       consultation_id, operation_key, project_id, task_id,
                       plan_id, plan_version_observed, inventory_id,
                       inventory_sha256, stage_key, role, runtime, decision_key,
                       repository_id, repository_ref, repository_commit, state,
                       prompt_sha256, schema_status, token_reserved,
                       cost_reserved_usd, started_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    consultation_id,
                    operation_key,
                    route.project_id,
                    task_id,
                    plan["plan_id"],
                    expected_plan_version,
                    route.inventory_id,
                    route.inventory_sha256,
                    route.stage_key,
                    route.role,
                    route.runtime,
                    decision_key,
                    repository_id,
                    repository_ref,
                    repository_commit,
                    ConsultationState.RUNNING.value,
                    prompt_sha256,
                    "pending",
                    token_reserved,
                    cost_reserved_usd,
                    now,
                ),
            )
            self.tasks._insert_event(
                db,
                task_id=task_id,
                event_type="orchestration.consultation_started",
                actor=actor,
                payload={
                    "consultation_id": consultation_id,
                    "plan_id": plan["plan_id"],
                    "plan_version_observed": expected_plan_version,
                    "inventory_id": route.inventory_id,
                    "inventory_sha256": route.inventory_sha256,
                    "stage_key": route.stage_key,
                    "runtime": route.runtime,
                    "repository_id": repository_id,
                    "repository_ref": repository_ref,
                    "repository_commit": repository_commit,
                    "decision_key": decision_key,
                    "prompt_sha256": prompt_sha256,
                    "token_reserved": token_reserved,
                    "cost_reserved_usd": cost_reserved_usd,
                },
                created_at=now,
            )
        return self.require_consultation(consultation_id), False

    def attach_consultation_pid(
        self,
        consultation_id: str,
        pid: int,
    ) -> ConsultationRun:
        if pid <= 0:
            raise OrchestrationValidationError("Consultation PID must be positive")
        with self._transaction() as db:
            row = db.execute(
                """SELECT * FROM orchestration_consultations
                   WHERE consultation_id = ?""",
                (consultation_id,),
            ).fetchone()
            if row is None:
                raise OrchestrationNotFoundError(consultation_id)
            if row["state"] != ConsultationState.RUNNING.value:
                raise OrchestrationConflictError("Consultation is not running")
            if row["pid"] is not None and row["pid"] != pid:
                raise OrchestrationConflictError(
                    "Consultation already has a different process"
                )
            db.execute(
                """UPDATE orchestration_consultations SET pid = ?
                   WHERE consultation_id = ?""",
                (pid, consultation_id),
            )
        return self.require_consultation(consultation_id)

    def settle_consultation(
        self,
        consultation_id: str,
        *,
        adapted: ConsultationAdapterResult,
        output_sha256: str,
        error_message: str | None,
        usage_observation: ProviderUsageObservation,
        actor: str = "orchestrator",
    ) -> ConsultationRun:
        """Atomically settle usage and register only a schema-valid candidate."""

        if not re.fullmatch(r"[0-9a-f]{64}", output_sha256):
            raise OrchestrationValidationError(
                "Consultation output hash must be lowercase SHA-256"
            )
        safe_error = (
            redact_text(error_message.strip())[-4_000:]
            if error_message and error_message.strip()
            else None
        )
        now = utc_now()
        with self._transaction() as db:
            row = db.execute(
                """SELECT * FROM orchestration_consultations
                   WHERE consultation_id = ?""",
                (consultation_id,),
            ).fetchone()
            if row is None:
                raise OrchestrationNotFoundError(consultation_id)
            if row["state"] != ConsultationState.RUNNING.value:
                raise OrchestrationConflictError("Consultation is already terminal")
            if (
                usage_observation.run_id != consultation_id
                or usage_observation.adapter != row["runtime"]
            ):
                raise OrchestrationConflictError(
                    "Consultation usage does not match its runtime binding"
                )

            draft = adapted.draft
            error_code = adapted.error_code
            schema_status = adapted.schema_status
            candidate = None
            if draft is not None:
                plan = db.execute(
                    "SELECT * FROM orchestration_plans WHERE plan_id = ?",
                    (row["plan_id"],),
                ).fetchone()
                try:
                    route = ControlPlaneStore(self.tasks).stage_route_snapshot(
                        db,
                        row["task_id"],
                    )
                except ControlPlaneConflictError:
                    route = None
                route_matches = bool(
                    plan is not None
                    and plan["version"] == row["plan_version_observed"]
                    and route is not None
                    and route.project_id == row["project_id"]
                    and route.inventory_id == row["inventory_id"]
                    and route.inventory_sha256 == row["inventory_sha256"]
                    and route.stage_key == row["stage_key"]
                    and route.role == row["role"]
                    and route.runtime == row["runtime"]
                )
                safe_refs = list(draft.source_refs)
                if not route_matches:
                    draft = None
                    schema_status = SchemaStatus.PROTOCOL_FAILED
                    error_code = "candidate_context_stale"
                elif any(redact_text(item) != item for item in safe_refs):
                    draft = None
                    schema_status = SchemaStatus.PROTOCOL_FAILED
                    error_code = "candidate_source_ref_sensitive"
                else:
                    candidate_id = self._id("candidate")
                    candidate_operation_key = f"candidate-from:{consultation_id}"
                    candidate_payload = seal_model_payload(
                        ConsultationCandidate,
                        {
                            "schema_version": "1.0",
                            "candidate_id": candidate_id,
                            "consultation_id": consultation_id,
                            "operation_key": candidate_operation_key,
                            "project_id": row["project_id"],
                            "task_id": row["task_id"],
                            "plan_id": row["plan_id"],
                            "plan_version_observed": row["plan_version_observed"],
                            "inventory_id": row["inventory_id"],
                            "inventory_sha256": row["inventory_sha256"],
                            "stage_key": row["stage_key"],
                            "role": row["role"],
                            "runtime": row["runtime"],
                            "title": redact_text(draft.title),
                            "decision_key": draft.decision_key,
                            "decision_value": redact_text(draft.decision_value),
                            "analysis": redact_text(draft.analysis),
                            "source_refs": safe_refs,
                            "registered_by": f"runtime:{row['runtime']}",
                            "advisory_authority": False,
                            "formal_artifact": False,
                            "created_at": now,
                        },
                    )
                    candidate = ConsultationCandidate.model_validate(
                        candidate_payload
                    )
                    db.execute(
                        """INSERT INTO orchestration_consultation_candidates (
                               candidate_id, plan_id, task_id, stage_key,
                               operation_key, payload, created_at
                           ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            candidate.candidate_id,
                            candidate.plan_id,
                            candidate.task_id,
                            candidate.stage_key,
                            candidate.operation_key,
                            self._json(candidate.model_dump(mode="json")),
                            candidate.created_at.isoformat(),
                        ),
                    )
                    self.tasks._insert_event(
                        db,
                        task_id=row["task_id"],
                        event_type=(
                            "orchestration.consultation_candidate_registered"
                        ),
                        actor=f"runtime:{row['runtime']}",
                        payload={
                            "plan_id": candidate.plan_id,
                            "stage_key": candidate.stage_key,
                            "candidate_id": candidate.candidate_id,
                            "consultation_id": candidate.consultation_id,
                            "candidate_sha256": candidate.content_sha256,
                            "inventory_id": candidate.inventory_id,
                            "inventory_sha256": candidate.inventory_sha256,
                            "advisory_authority": False,
                            "formal_artifact": False,
                        },
                        created_at=now,
                    )

            if candidate is not None:
                state = ConsultationState.COMPLETED
            elif adapted.process_status == ProcessStatus.INTERRUPTED:
                state = ConsultationState.INTERRUPTED
            elif schema_status == SchemaStatus.PROTOCOL_FAILED:
                state = ConsultationState.PROTOCOL_FAILED
            else:
                state = ConsultationState.FAILED
            schema_value = schema_status.value
            db.execute(
                """UPDATE orchestration_consultations
                   SET state = ?, process_status = ?, transport_status = ?,
                       schema_status = ?, repair_attempts = ?, candidate_id = ?,
                       output_sha256 = ?, error_code = ?, error_message = ?,
                       token_used = ?, token_measurement = ?,
                       cost_used_usd = ?, cost_measurement = ?,
                       usage_observation_payload = ?, finished_at = ?
                   WHERE consultation_id = ?""",
                (
                    state.value,
                    adapted.process_status.value,
                    adapted.transport_status.value,
                    schema_value,
                    adapted.repair_attempts,
                    candidate.candidate_id if candidate else None,
                    output_sha256,
                    error_code,
                    safe_error,
                    usage_observation.total_tokens,
                    usage_observation.token_measurement,
                    usage_observation.cost_usd,
                    usage_observation.cost_measurement,
                    self._json(usage_observation.model_dump(mode="json")),
                    now,
                    consultation_id,
                ),
            )
            self.tasks._insert_event(
                db,
                task_id=row["task_id"],
                event_type="orchestration.consultation_settled",
                actor=actor,
                payload={
                    "consultation_id": consultation_id,
                    "state": state.value,
                    "process_status": adapted.process_status.value,
                    "transport_status": adapted.transport_status.value,
                    "schema_status": schema_value,
                    "repair_attempts": adapted.repair_attempts,
                    "candidate_id": candidate.candidate_id if candidate else None,
                    "output_sha256": output_sha256,
                    "error_code": error_code,
                    "usage_observation_sha256": (
                        usage_observation.content_sha256
                    ),
                },
                created_at=now,
            )
        return self.require_consultation(consultation_id)

    def require_consultation(self, consultation_id: str) -> ConsultationRun:
        with closing(self._connect()) as db:
            row = db.execute(
                """SELECT * FROM orchestration_consultations
                   WHERE consultation_id = ?""",
                (consultation_id,),
            ).fetchone()
        if row is None:
            raise OrchestrationNotFoundError(consultation_id)
        return self._consultation(row)

    def register_consultation_candidate(
        self,
        task_id: str,
        *,
        consultation_id: str,
        runtime: str,
        title: str,
        decision_key: str,
        decision_value: str,
        analysis: str,
        source_refs: list[str],
        expected_plan_version: int,
        operation_key: str | None,
        actor: str = "agora",
    ) -> ConsultationCandidate:
        """Persist advisory output without changing Plan, Stage, Gate, or Artifact state."""

        consultation_id = consultation_id.strip()
        runtime = runtime.strip()
        safe_title = redact_text(title.strip())
        safe_value = redact_text(decision_value.strip())
        safe_analysis = redact_text(analysis.strip())
        safe_source_refs = [item.strip() for item in source_refs]
        actor = actor.strip()
        operation_key = (
            operation_key.strip()
            if operation_key is not None
            else "candidate-register:"
            + canonical_sha256(
                {
                    "task_id": task_id,
                    "consultation_id": consultation_id,
                    "runtime": runtime,
                    "title": safe_title,
                    "decision_key": decision_key,
                    "decision_value": safe_value,
                    "analysis": safe_analysis,
                    "source_refs": safe_source_refs,
                    "expected_plan_version": expected_plan_version,
                    "actor": actor,
                }
            )
        )
        stable_id = r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}"
        if not re.fullmatch(stable_id, operation_key):
            raise OrchestrationValidationError(
                "Consultation candidate operation_key must be a stable identifier"
            )
        if not re.fullmatch(stable_id, consultation_id):
            raise OrchestrationValidationError(
                "Consultation ID must be a stable identifier"
            )
        if not re.fullmatch(stable_id, runtime):
            raise OrchestrationValidationError(
                "Consultation runtime must be a stable identifier"
            )
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,127}", decision_key):
            raise OrchestrationValidationError("Invalid candidate decision key")
        if not safe_title or len(safe_title) > 200:
            raise OrchestrationValidationError(
                "Candidate title must contain 1 to 200 characters"
            )
        if not safe_value or len(safe_value) > 1_000:
            raise OrchestrationValidationError(
                "Candidate decision value must contain 1 to 1000 characters"
            )
        if not safe_analysis or len(safe_analysis) > 8_000:
            raise OrchestrationValidationError(
                "Candidate analysis must contain 1 to 8000 characters"
            )
        if len(safe_source_refs) > 20 or len(safe_source_refs) != len(
            set(safe_source_refs)
        ):
            raise OrchestrationValidationError(
                "Candidate source refs must be unique and contain at most 20 items"
            )
        if any(not re.fullmatch(stable_id, item) for item in safe_source_refs):
            raise OrchestrationValidationError(
                "Candidate source refs must be stable identifiers"
            )
        if any(redact_text(item) != item for item in safe_source_refs):
            raise OrchestrationValidationError(
                "Candidate source refs may not contain credential-like values"
            )
        if expected_plan_version < 1:
            raise OrchestrationValidationError(
                "Candidate expected Plan version must be positive"
            )
        if not actor or len(actor) > 128:
            raise OrchestrationValidationError(
                "Candidate actor must contain 1 to 128 characters"
            )

        candidate_id = self._id("candidate")
        now = utc_now()
        task = self.tasks.get(task_id)
        if task is None:
            raise OrchestrationNotFoundError(task_id)
        with self._transaction() as db:
            replay_row = db.execute(
                """SELECT * FROM orchestration_consultation_candidates
                   WHERE operation_key = ?""",
                (operation_key,),
            ).fetchone()
            if replay_row is not None:
                replay = self._consultation_candidate(replay_row)
                if not (
                    replay.task_id == task_id
                    and replay.consultation_id == consultation_id
                    and replay.runtime == runtime
                    and replay.title == safe_title
                    and replay.decision_key == decision_key
                    and replay.decision_value == safe_value
                    and replay.analysis == safe_analysis
                    and replay.source_refs == safe_source_refs
                    and replay.plan_version_observed == expected_plan_version
                    and replay.registered_by == actor
                ):
                    raise OrchestrationConflictError(
                        "Candidate operation_key was already used for different inputs"
                    )
                return replay

            plan = db.execute(
                "SELECT * FROM orchestration_plans WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if plan is None:
                raise OrchestrationNotFoundError(task_id)
            if plan["version"] != expected_plan_version:
                raise OrchestrationConflictError(
                    "Plan version changed before candidate registration"
                )
            try:
                route = ControlPlaneStore(self.tasks).stage_route_snapshot(
                    db,
                    task_id,
                )
            except ControlPlaneConflictError as exc:
                raise OrchestrationConflictError(str(exc)) from exc
            if route is None:
                raise OrchestrationConflictError(
                    "Authoritative Stage route is unavailable for consultation"
                )
            running = db.execute(
                """SELECT run_id FROM orchestration_runs
                   WHERE plan_id = ? AND state = ? LIMIT 1""",
                (plan["plan_id"], RunState.RUNNING.value),
            ).fetchone()
            if running is not None:
                raise OrchestrationConflictError(
                    "Consultation candidate cannot be registered while a Run is active"
                )
            stage = db.execute(
                """SELECT * FROM orchestration_stages
                   WHERE plan_id = ? AND stage_key = ?""",
                (plan["plan_id"], plan["current_stage_key"]),
            ).fetchone()
            valid_state = stage is not None and (
                (
                    plan["state"] == PlanState.ACTIVE.value
                    and stage["state"] == StageState.PENDING.value
                )
                or (
                    plan["state"] == PlanState.BLOCKED.value
                    and stage["state"] == StageState.BLOCKED.value
                )
            )
            if not valid_state:
                raise OrchestrationConflictError(
                    "Consultation candidates require the current pending or blocked Stage"
                )
            if (
                route.task_id != task_id
                or route.project_id != task.project_id
                or route.stage_key != stage["stage_key"]
                or route.runtime != stage["adapter"]
            ):
                raise OrchestrationConflictError(
                    "Compatibility Plan route does not match the authoritative "
                    "Control Plane Stage route"
                )
            if route.runtime != runtime:
                raise OrchestrationConflictError(
                    "Consultation runtime does not match the pinned current Stage runtime"
                )
            payload = seal_model_payload(
                ConsultationCandidate,
                {
                    "schema_version": "1.0",
                    "candidate_id": candidate_id,
                    "consultation_id": consultation_id,
                    "operation_key": operation_key,
                    "project_id": task.project_id,
                    "task_id": task_id,
                    "plan_id": plan["plan_id"],
                    "plan_version_observed": expected_plan_version,
                    "inventory_id": route.inventory_id,
                    "inventory_sha256": route.inventory_sha256,
                    "stage_key": stage["stage_key"],
                    "role": route.role,
                    "runtime": runtime,
                    "title": safe_title,
                    "decision_key": decision_key,
                    "decision_value": safe_value,
                    "analysis": safe_analysis,
                    "source_refs": safe_source_refs,
                    "registered_by": actor,
                    "advisory_authority": False,
                    "formal_artifact": False,
                    "created_at": now,
                },
            )
            candidate = ConsultationCandidate.model_validate(payload)
            db.execute(
                """INSERT INTO orchestration_consultation_candidates (
                       candidate_id, plan_id, task_id, stage_key, operation_key,
                       payload, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    candidate.candidate_id,
                    candidate.plan_id,
                    candidate.task_id,
                    candidate.stage_key,
                    candidate.operation_key,
                    self._json(candidate.model_dump(mode="json")),
                    candidate.created_at.isoformat(),
                ),
            )
            self.tasks._insert_event(
                db,
                task_id=task_id,
                event_type="orchestration.consultation_candidate_registered",
                actor=actor,
                payload={
                    "plan_id": candidate.plan_id,
                    "stage_key": candidate.stage_key,
                    "candidate_id": candidate.candidate_id,
                    "consultation_id": candidate.consultation_id,
                    "candidate_sha256": candidate.content_sha256,
                    "inventory_id": candidate.inventory_id,
                    "inventory_sha256": candidate.inventory_sha256,
                    "advisory_authority": False,
                    "formal_artifact": False,
                },
                created_at=now,
            )
        return self.require_consultation_candidate(candidate_id)

    def dispose_consultation_candidate(
        self,
        task_id: str,
        candidate_id: str,
        *,
        action: str,
        expected_plan_version: int,
        reason: str,
        actor: str,
        operation_key: str | None,
    ) -> ConsultationCandidateDisposition:
        """Adopt a candidate into Task decisions or reject it without formal mutation."""

        if action not in {"adopted", "rejected"}:
            raise OrchestrationValidationError(
                "Candidate disposition must be adopted or rejected"
            )
        actor = actor.strip()
        safe_reason = redact_text(reason.strip())
        operation_key = (
            operation_key.strip()
            if operation_key is not None
            else f"candidate-{action}:"
            + canonical_sha256(
                {
                    "task_id": task_id,
                    "candidate_id": candidate_id,
                    "action": action,
                    "expected_plan_version": expected_plan_version,
                    "reason": safe_reason,
                    "actor": actor,
                }
            )
        )
        stable_id = r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}"
        if not re.fullmatch(stable_id, operation_key):
            raise OrchestrationValidationError(
                "Candidate disposition operation_key must be a stable identifier"
            )
        if not re.fullmatch(stable_id, candidate_id):
            raise OrchestrationValidationError("Invalid consultation candidate ID")
        if expected_plan_version < 1:
            raise OrchestrationValidationError(
                "Candidate disposition expected Plan version must be positive"
            )
        if not actor or len(actor) > 128:
            raise OrchestrationValidationError(
                "Candidate disposition actor must contain 1 to 128 characters"
            )
        if not safe_reason or len(safe_reason) > 500:
            raise OrchestrationValidationError(
                "Candidate disposition reason must contain 1 to 500 characters"
            )

        disposition_id = self._id("disposition")
        now = utc_now()
        with self._transaction() as db:
            replay_row = db.execute(
                """SELECT * FROM orchestration_candidate_dispositions
                   WHERE operation_key = ?""",
                (operation_key,),
            ).fetchone()
            if replay_row is not None:
                replay = self._candidate_disposition(replay_row)
                if not (
                    replay.task_id == task_id
                    and replay.candidate_id == candidate_id
                    and replay.action == action
                    and replay.plan_version_before == expected_plan_version
                    and replay.actor == actor
                    and replay.reason == safe_reason
                ):
                    raise OrchestrationConflictError(
                        "Candidate disposition operation_key was already used "
                        "for different inputs"
                    )
                return replay

            candidate_row = db.execute(
                """SELECT * FROM orchestration_consultation_candidates
                   WHERE candidate_id = ?""",
                (candidate_id,),
            ).fetchone()
            if candidate_row is None:
                raise OrchestrationNotFoundError(candidate_id)
            candidate = self._consultation_candidate(candidate_row)
            if candidate.task_id != task_id:
                raise OrchestrationConflictError(
                    "Consultation candidate belongs to a different Task"
                )
            prior_disposition = db.execute(
                """SELECT disposition_id
                   FROM orchestration_candidate_dispositions
                   WHERE candidate_id = ?""",
                (candidate_id,),
            ).fetchone()
            if prior_disposition is not None:
                raise OrchestrationConflictError(
                    "Consultation candidate already has an explicit disposition"
                )
            plan = db.execute(
                "SELECT * FROM orchestration_plans WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if plan is None:
                raise OrchestrationNotFoundError(task_id)
            running = db.execute(
                """SELECT run_id FROM orchestration_runs
                   WHERE plan_id = ? AND state = ? LIMIT 1""",
                (plan["plan_id"], RunState.RUNNING.value),
            ).fetchone()
            if running is not None:
                raise OrchestrationConflictError(
                    "Candidate disposition cannot race an active Run"
                )
            if (
                plan["plan_id"] != candidate.plan_id
                or plan["version"] != expected_plan_version
                or candidate.plan_version_observed != expected_plan_version
            ):
                raise OrchestrationConflictError(
                    "Consultation candidate is stale for the current Plan version"
                )
            try:
                route = ControlPlaneStore(self.tasks).stage_route_snapshot(
                    db,
                    task_id,
                )
            except ControlPlaneConflictError as exc:
                raise OrchestrationConflictError(str(exc)) from exc
            if (
                route is None
                or route.project_id != candidate.project_id
                or route.inventory_id != candidate.inventory_id
                or route.inventory_sha256 != candidate.inventory_sha256
                or route.stage_key != candidate.stage_key
                or route.role != candidate.role
                or route.runtime != candidate.runtime
            ):
                raise OrchestrationConflictError(
                    "Consultation candidate is stale for the authoritative Stage route"
                )
            stage = db.execute(
                """SELECT * FROM orchestration_stages
                   WHERE plan_id = ? AND stage_key = ?""",
                (plan["plan_id"], plan["current_stage_key"]),
            ).fetchone()
            valid_state = (
                stage is not None
                and stage["stage_key"] == candidate.stage_key
                and (
                    (
                        plan["state"] == PlanState.ACTIVE.value
                        and stage["state"] == StageState.PENDING.value
                    )
                    or (
                        plan["state"] == PlanState.BLOCKED.value
                        and stage["state"] == StageState.BLOCKED.value
                    )
                )
            )
            if not valid_state:
                raise OrchestrationConflictError(
                    "Candidate disposition requires its current pending or blocked Stage"
                )
            decision = None
            if action == "adopted":
                digest = hashlib.sha256(
                    self._json(
                        {
                            "decision_key": candidate.decision_key,
                            "decision_value": candidate.decision_value,
                            "rationale": safe_reason,
                        }
                    ).encode("utf-8")
                ).hexdigest()
                latest = db.execute(
                    """SELECT * FROM orchestration_decisions
                       WHERE plan_id = ? AND decision_key = ?
                       ORDER BY version DESC LIMIT 1""",
                    (plan["plan_id"], candidate.decision_key),
                ).fetchone()
                if latest is not None and latest["decision_sha256"] == digest:
                    decision = self._decision(latest)
                else:
                    version = int(latest["version"]) + 1 if latest else 1
                    latest_rows = db.execute(
                        """SELECT decision_key, decision_value, rationale, version, actor
                           FROM orchestration_decisions AS decision
                           WHERE plan_id = ? AND version = (
                               SELECT MAX(version) FROM orchestration_decisions
                               WHERE plan_id = decision.plan_id
                               AND decision_key = decision.decision_key
                           ) AND decision_key != ? ORDER BY decision_key""",
                        (plan["plan_id"], candidate.decision_key),
                    ).fetchall()
                    decision_context = [dict(row) for row in latest_rows]
                    decision_context.append(
                        {
                            "decision_key": candidate.decision_key,
                            "decision_value": candidate.decision_value,
                            "rationale": safe_reason,
                            "version": version,
                            "actor": actor,
                        }
                    )
                    decision_context.sort(key=lambda item: item["decision_key"])
                    if len(self._json(decision_context)) > DECISION_CONTEXT_LIMIT:
                        raise OrchestrationValidationError(
                            "Active Task decisions exceed the bounded prompt allocation"
                        )
                    decision_id = self._id("decision")
                    db.execute(
                        """INSERT INTO orchestration_decisions (
                               decision_id, plan_id, task_id, decision_key,
                               decision_value, rationale, decision_sha256,
                               version, actor, created_at
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            decision_id,
                            plan["plan_id"],
                            task_id,
                            candidate.decision_key,
                            candidate.decision_value,
                            safe_reason,
                            digest,
                            version,
                            actor,
                            now,
                        ),
                    )
                    decision = self._decision(
                        db.execute(
                            """SELECT * FROM orchestration_decisions
                               WHERE decision_id = ?""",
                            (decision_id,),
                        ).fetchone()
                    )
                    self.tasks._insert_event(
                        db,
                        task_id=task_id,
                        event_type="orchestration.decision_recorded",
                        actor=actor,
                        payload={
                            "plan_id": plan["plan_id"],
                            "decision_id": decision.decision_id,
                            "decision_key": decision.decision_key,
                            "decision_sha256": decision.decision_sha256,
                            "version": decision.version,
                            "candidate_id": candidate.candidate_id,
                        },
                        created_at=now,
                    )

            next_plan_version = (
                expected_plan_version + 1
                if action == "adopted"
                else expected_plan_version
            )
            payload = seal_model_payload(
                ConsultationCandidateDisposition,
                {
                    "schema_version": "1.0",
                    "disposition_id": disposition_id,
                    "operation_key": operation_key,
                    "candidate_id": candidate.candidate_id,
                    "candidate_sha256": candidate.content_sha256,
                    "project_id": candidate.project_id,
                    "task_id": task_id,
                    "plan_id": plan["plan_id"],
                    "stage_key": candidate.stage_key,
                    "action": action,
                    "plan_version_before": expected_plan_version,
                    "plan_version_after": next_plan_version,
                    "claim_invalidated": action == "adopted",
                    "decision_id": decision.decision_id if decision else None,
                    "decision_sha256": (
                        decision.decision_sha256 if decision else None
                    ),
                    "decision_version": decision.version if decision else None,
                    "actor": actor,
                    "reason": safe_reason,
                    "created_at": now,
                },
            )
            disposition = ConsultationCandidateDisposition.model_validate(payload)
            db.execute(
                """INSERT INTO orchestration_candidate_dispositions (
                       disposition_id, plan_id, task_id, candidate_id,
                       operation_key, action, payload, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    disposition.disposition_id,
                    disposition.plan_id,
                    disposition.task_id,
                    disposition.candidate_id,
                    disposition.operation_key,
                    disposition.action,
                    self._json(disposition.model_dump(mode="json")),
                    disposition.created_at.isoformat(),
                ),
            )
            if action == "adopted":
                cursor = db.execute(
                    """UPDATE orchestration_plans
                       SET version = version + 1, updated_at = ?
                       WHERE plan_id = ? AND version = ?""",
                    (now, plan["plan_id"], expected_plan_version),
                )
                if cursor.rowcount != 1:
                    raise OrchestrationConflictError(
                        "Plan changed while adopting the consultation candidate"
                    )
            self.tasks._insert_event(
                db,
                task_id=task_id,
                event_type="orchestration.consultation_candidate_disposed",
                actor=actor,
                payload={
                    "plan_id": plan["plan_id"],
                    "stage_key": candidate.stage_key,
                    "candidate_id": candidate.candidate_id,
                    "candidate_sha256": candidate.content_sha256,
                    "disposition_id": disposition.disposition_id,
                    "disposition_sha256": disposition.content_sha256,
                    "action": action,
                    "decision_id": decision.decision_id if decision else None,
                },
                created_at=now,
            )
        return self.require_consultation_candidate_disposition(disposition_id)

    def require_consultation_candidate(
        self,
        candidate_id: str,
    ) -> ConsultationCandidate:
        with closing(self._connect()) as db:
            row = db.execute(
                """SELECT * FROM orchestration_consultation_candidates
                   WHERE candidate_id = ?""",
                (candidate_id,),
            ).fetchone()
        if row is None:
            raise OrchestrationNotFoundError(candidate_id)
        return self._consultation_candidate(row)

    def require_consultation_candidate_disposition(
        self,
        disposition_id: str,
    ) -> ConsultationCandidateDisposition:
        with closing(self._connect()) as db:
            row = db.execute(
                """SELECT * FROM orchestration_candidate_dispositions
                   WHERE disposition_id = ?""",
                (disposition_id,),
            ).fetchone()
        if row is None:
            raise OrchestrationNotFoundError(disposition_id)
        return self._candidate_disposition(row)

    def amend_budget(
        self,
        task_id: str,
        *,
        amended_total_token_budget: int,
        amended_total_cost_budget_usd: float | None,
        expected_task_version: int,
        expected_plan_version: int,
        operation_key: str,
        route: StageRouteDecision | None,
        contract: TaskContract | None,
        actor: str,
        reason: str,
    ) -> BudgetAmendment:
        """Increase one Task envelope without changing Stage allocations or history."""

        operation_key = operation_key.strip()
        actor = actor.strip()
        safe_reason = redact_text(reason.strip())
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", operation_key):
            raise OrchestrationValidationError(
                "Budget amendment operation_key must be a stable identifier"
            )
        if not actor or len(actor) > 128:
            raise OrchestrationValidationError(
                "Budget amendment actor must contain 1 to 128 characters"
            )
        if not safe_reason or len(safe_reason) > 1_000:
            raise OrchestrationValidationError(
                "Budget amendment reason must contain 1 to 1000 characters"
            )
        if expected_task_version < 1 or expected_plan_version < 1:
            raise OrchestrationValidationError(
                "Budget amendment expected versions must be positive"
            )
        if (
            amended_total_cost_budget_usd is not None
            and not math.isfinite(amended_total_cost_budget_usd)
        ):
            raise OrchestrationValidationError(
                "Budget amendment cost must be finite"
            )

        amendment_id = self._id("budget")
        now = utc_now()
        with self._transaction() as db:
            replay_row = db.execute(
                """SELECT * FROM orchestration_budget_amendments
                   WHERE operation_key = ?""",
                (operation_key,),
            ).fetchone()
            if replay_row is not None:
                replay = self._budget_amendment(replay_row)
                if not self._budget_amendment_matches_request(
                    replay,
                    task_id=task_id,
                    amended_total_token_budget=amended_total_token_budget,
                    amended_total_cost_budget_usd=amended_total_cost_budget_usd,
                    expected_task_version=expected_task_version,
                    expected_plan_version=expected_plan_version,
                    route=route,
                    contract=contract,
                    actor=actor,
                    reason=safe_reason,
                ):
                    raise OrchestrationConflictError(
                        "Budget amendment operation_key was already used for different inputs"
                    )
                return replay

            task_row = db.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            plan = db.execute(
                "SELECT * FROM orchestration_plans WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            inventory_row = db.execute(
                "SELECT * FROM control_stage_inventories WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if task_row is None or plan is None or inventory_row is None:
                raise OrchestrationNotFoundError(task_id)
            if int(task_row["version"]) != expected_task_version:
                raise OrchestrationConflictError(
                    f"Expected Task version {expected_task_version}, current version is "
                    f"{task_row['version']}"
                )
            if int(plan["version"]) != expected_plan_version:
                raise OrchestrationConflictError(
                    f"Expected Plan version {expected_plan_version}, current version is "
                    f"{plan['version']}"
                )
            if route is None or contract is None:
                raise OrchestrationConflictError(
                    "Budget amendment requires a current formal route and pinned Task contract"
                )
            try:
                current_route = ControlPlaneStore(self.tasks).stage_route_snapshot(
                    db,
                    task_id,
                )
            except ControlPlaneConflictError as exc:
                raise OrchestrationConflictError(str(exc)) from exc
            if current_route != route or not current_route.runnable:
                raise OrchestrationConflictError(
                    "Budget amendment requires the exact currently runnable formal route"
                )
            if (
                plan["state"] != PlanState.ACTIVE.value
                or plan["current_stage_key"] != route.stage_key
            ):
                raise OrchestrationConflictError(
                    "Budget amendment requires the active Plan at the routed Stage"
                )
            current_stage = db.execute(
                """SELECT * FROM orchestration_stages
                   WHERE plan_id = ? AND stage_key = ?""",
                (plan["plan_id"], route.stage_key),
            ).fetchone()
            if current_stage is None or current_stage["state"] != StageState.PENDING.value:
                raise OrchestrationConflictError(
                    "Budget amendment requires the routed compatibility Stage to be pending"
                )
            # Operational and formal Run lifecycles are separate dimensions.
            # Both guards execute under the same BEGIN IMMEDIATE writer lock.
            if db.execute(
                """SELECT 1 FROM orchestration_runs
                   WHERE plan_id = ? AND state = ? LIMIT 1""",
                (plan["plan_id"], RunState.RUNNING.value),
            ).fetchone():
                raise OrchestrationConflictError(
                    "Budget amendment is forbidden while an operational Run is active"
                )
            if db.execute(
                """SELECT 1 FROM protocol_runs
                   WHERE task_id = ? AND settled_at IS NULL LIMIT 1""",
                (task_id,),
            ).fetchone():
                raise OrchestrationConflictError(
                    "Budget amendment is forbidden while a formal Run is unsettled"
                )

            task = self.tasks._manifest(task_row)
            methodology = MethodologyDefinition.model_validate_json(
                plan["methodology_payload"]
            )
            inventory = StageInventory.model_validate_json(inventory_row["payload"])
            previous_tokens = int(plan["total_token_budget"])
            previous_cost = plan["total_cost_budget_usd"]
            task_cost = task.budget.max_cost_usd
            if not self._same_optional_money(task_cost, previous_cost):
                raise OrchestrationConflictError(
                    "Task and Plan cost envelopes do not agree"
                )
            self.validate_plan_inputs(
                methodology,
                total_token_budget=amended_total_token_budget,
                total_cost_budget_usd=amended_total_cost_budget_usd,
            )
            if amended_total_token_budget < previous_tokens:
                raise OrchestrationValidationError(
                    "Budget amendment may not reduce the Task Token envelope"
                )
            if previous_cost is None and amended_total_cost_budget_usd is not None:
                raise OrchestrationValidationError(
                    "Budget amendment may not replace an unbounded cost envelope"
                )
            if previous_cost is not None and amended_total_cost_budget_usd is None:
                raise OrchestrationValidationError(
                    "Budget amendment may not remove the configured cost envelope"
                )
            if (
                previous_cost is not None
                and amended_total_cost_budget_usd is not None
                and amended_total_cost_budget_usd < previous_cost
            ):
                raise OrchestrationValidationError(
                    "Budget amendment may not reduce the Task cost envelope"
                )
            token_increased = amended_total_token_budget > previous_tokens
            cost_increased = bool(
                previous_cost is not None
                and amended_total_cost_budget_usd is not None
                and amended_total_cost_budget_usd > previous_cost
            )
            if not token_increased and not cost_increased:
                raise OrchestrationValidationError(
                    "Budget amendment must increase Tokens or configured cost"
                )

            prior_policy = self._routing_policy_decision(
                db,
                task_id=task_id,
                route=route,
                contract=contract,
                run_id=f"budget-before:{amendment_id}",
            )
            non_budget_failures = [
                check
                for check in prior_policy.checks
                if check.constraint != "protected_budget" and not check.satisfied
            ]
            protected_check = next(
                check
                for check in prior_policy.checks
                if check.constraint == "protected_budget"
            )
            if non_budget_failures:
                raise OrchestrationConflictError(
                    "Budget amendment cannot repair non-budget routing-policy blockers"
                )
            if protected_check.satisfied:
                raise OrchestrationConflictError(
                    "Current route already satisfies the protected budget policy"
                )

            # BEGIN IMMEDIATE owns SQLite's only writer slot throughout this
            # transaction. These before/after hashes therefore prove that this
            # mutation itself did not rewrite Stage allocations; no concurrent
            # writer can enter between the two reads.
            stage_rows = db.execute(
                """SELECT stage_key, sequence, token_budget, cost_budget_usd
                   FROM orchestration_stages WHERE plan_id = ? ORDER BY sequence""",
                (plan["plan_id"],),
            ).fetchall()
            stage_allocations_sha256 = canonical_sha256(
                [dict(row) for row in stage_rows]
            )
            next_task_version = expected_task_version + 1
            next_plan_version = expected_plan_version + 1
            amended_task_budget = TaskBudget(
                max_cost_usd=amended_total_cost_budget_usd,
                max_minutes=task.budget.max_minutes,
            )
            task_cursor = db.execute(
                """UPDATE tasks SET budget = ?, version = ?, updated_at = ?
                   WHERE task_id = ? AND version = ?""",
                (
                    self._json(amended_task_budget.model_dump(exclude_none=True)),
                    next_task_version,
                    now,
                    task_id,
                    expected_task_version,
                ),
            )
            plan_cursor = db.execute(
                """UPDATE orchestration_plans
                   SET total_token_budget = ?, total_cost_budget_usd = ?,
                       version = ?, updated_at = ?
                   WHERE plan_id = ? AND version = ?""",
                (
                    amended_total_token_budget,
                    amended_total_cost_budget_usd,
                    next_plan_version,
                    now,
                    plan["plan_id"],
                    expected_plan_version,
                ),
            )
            if task_cursor.rowcount != 1 or plan_cursor.rowcount != 1:
                raise OrchestrationConflictError(
                    "Task or Plan changed while amending the budget"
                )

            resulting_policy = self._routing_policy_decision(
                db,
                task_id=task_id,
                route=route,
                contract=contract,
                run_id=f"budget-after:{amendment_id}",
            )
            if not resulting_policy.dispatchable:
                resulting_budget_check = next(
                    check
                    for check in resulting_policy.checks
                    if check.constraint == "protected_budget"
                )
                raise OrchestrationValidationError(
                    "Amended envelope still does not satisfy protected budget: "
                    + resulting_budget_check.detail
                )
            if (
                prior_policy.policy_sha256 != resulting_policy.policy_sha256
                or prior_policy.inventory_sha256 != resulting_policy.inventory_sha256
                or prior_policy.methodology_sha256
                != resulting_policy.methodology_sha256
                or prior_policy.stage_key != resulting_policy.stage_key
                or prior_policy.role != resulting_policy.role
                or prior_policy.pinned_runtime != resulting_policy.pinned_runtime
                or prior_policy.task_risk != resulting_policy.task_risk
                or prior_policy.required_capabilities
                != resulting_policy.required_capabilities
                or prior_policy.runtime_capabilities
                != resulting_policy.runtime_capabilities
                or prior_policy.required_reviewers
                != resulting_policy.required_reviewers
                or prior_policy.reviewer_assignments
                != resulting_policy.reviewer_assignments
            ):
                raise OrchestrationConflictError(
                    "Budget amendment changed non-budget routing-policy inputs"
                )
            stage_rows_after = db.execute(
                """SELECT stage_key, sequence, token_budget, cost_budget_usd
                   FROM orchestration_stages WHERE plan_id = ? ORDER BY sequence""",
                (plan["plan_id"],),
            ).fetchall()
            if canonical_sha256([dict(row) for row in stage_rows_after]) != (
                stage_allocations_sha256
            ):
                raise OrchestrationConflictError(
                    "Budget amendment changed sealed Stage allocations"
                )

            version = int(db.execute(
                """SELECT COALESCE(MAX(version), 0) + 1 AS version
                   FROM orchestration_budget_amendments WHERE plan_id = ?""",
                (plan["plan_id"],),
            ).fetchone()["version"])
            payload = seal_model_payload(
                BudgetAmendment,
                {
                    "schema_version": "1.0",
                    "amendment_id": amendment_id,
                    "amendment_version": version,
                    "operation_key": operation_key,
                    "task_id": task_id,
                    "project_id": task.project_id,
                    "plan_id": plan["plan_id"],
                    "task_version_before": expected_task_version,
                    "task_version_after": next_task_version,
                    "plan_version_before": expected_plan_version,
                    "plan_version_after": next_plan_version,
                    "inventory_id": inventory.inventory_id,
                    "inventory_sha256": inventory.content_sha256,
                    "methodology_id": methodology.methodology_id,
                    "methodology_version": methodology.version,
                    "methodology_sha256": methodology_sha256(methodology),
                    "contract_id": contract.contract_id,
                    "contract_schema_version": contract.schema_version,
                    "contract_sha256": contract_sha256(contract),
                    "stage_key": route.stage_key,
                    "stage_allocations_sha256": stage_allocations_sha256,
                    "previous_total_token_budget": previous_tokens,
                    "amended_total_token_budget": amended_total_token_budget,
                    "previous_total_cost_budget_usd": previous_cost,
                    "amended_total_cost_budget_usd": amended_total_cost_budget_usd,
                    "prior_policy": prior_policy,
                    "resulting_policy": resulting_policy,
                    "claim_requires_policy_rederivation": True,
                    "actor": actor,
                    "reason": safe_reason,
                    "created_at": now,
                },
            )
            amendment = BudgetAmendment.model_validate(payload)
            db.execute(
                """INSERT INTO orchestration_budget_amendments (
                       amendment_id, plan_id, task_id, version, operation_key,
                       payload, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    amendment_id,
                    plan["plan_id"],
                    task_id,
                    version,
                    operation_key,
                    self._json(amendment.model_dump(mode="json")),
                    now,
                ),
            )
            self.tasks._insert_event(
                db,
                task_id=task_id,
                event_type="orchestration.budget_amended",
                actor=actor,
                payload={
                    "amendment_id": amendment_id,
                    "amendment_version": version,
                    "plan_id": plan["plan_id"],
                    "stage_key": route.stage_key,
                    "previous_total_token_budget": previous_tokens,
                    "amended_total_token_budget": amended_total_token_budget,
                    "previous_total_cost_budget_usd": previous_cost,
                    "amended_total_cost_budget_usd": amended_total_cost_budget_usd,
                    "prior_policy_sha256": prior_policy.content_sha256,
                    "resulting_policy_sha256": resulting_policy.content_sha256,
                    "stage_allocations_sha256": stage_allocations_sha256,
                    "task_version": next_task_version,
                    "plan_version": next_plan_version,
                    "reason": safe_reason,
                },
                created_at=now,
            )
        return self.require_budget_amendment(amendment_id)

    def require_budget_amendment(self, amendment_id: str) -> BudgetAmendment:
        with closing(self._connect()) as db:
            row = db.execute(
                """SELECT * FROM orchestration_budget_amendments
                   WHERE amendment_id = ?""",
                (amendment_id,),
            ).fetchone()
        if row is None:
            raise OrchestrationNotFoundError(amendment_id)
        return self._budget_amendment(row)

    def preview_routing_policy(
        self,
        task_id: str,
        *,
        route: StageRouteDecision,
        contract: TaskContract,
        run_id: str,
    ) -> RoutingPolicyDecision:
        """Derive a read-only policy snapshot that claim revalidates atomically."""

        with closing(self._connect()) as db:
            db.execute("BEGIN")
            try:
                return self._routing_policy_decision(
                    db,
                    task_id=task_id,
                    route=route,
                    contract=contract,
                    run_id=run_id,
                )
            finally:
                db.rollback()

    def _routing_policy_decision(
        self,
        db: sqlite3.Connection,
        *,
        task_id: str,
        route: StageRouteDecision,
        contract: TaskContract,
        run_id: str,
    ) -> RoutingPolicyDecision:
        task_row = db.execute(
            "SELECT * FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        plan = db.execute(
            "SELECT * FROM orchestration_plans WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        inventory_row = db.execute(
            "SELECT * FROM control_stage_inventories WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if task_row is None or plan is None or inventory_row is None:
            raise OrchestrationNotFoundError(task_id)
        task = self.tasks._manifest(task_row)
        inventory = StageInventory.model_validate_json(inventory_row["payload"])
        methodology = MethodologyDefinition.model_validate_json(
            plan["methodology_payload"]
        )
        methodology_digest = methodology_sha256(methodology)
        ordered_inventory = [
            (inventory_sequence, group, stage)
            for inventory_sequence, (group, stage) in enumerate(
                (
                    (group, stage)
                    for group in inventory.groups
                    for stage in group.stages
                ),
                start=1,
            )
        ]
        inventory_route = next(
            (
                item
                for item in ordered_inventory
                if item[1].group_key == route.group_key
                and item[2].stage_key == route.stage_key
            ),
            None,
        )
        if (
            route.task_id != task.task_id
            or route.project_id != task.project_id
            or route.inventory_id != inventory.inventory_id
            or route.inventory_sha256 != inventory.content_sha256
            or inventory.plan_id != plan["plan_id"]
            or inventory.methodology_id != methodology.methodology_id
            or inventory.methodology_version != methodology.version
            or inventory.methodology_sha256 != methodology_digest
            or plan["methodology_sha256"] != methodology_digest
            or inventory_route is None
            or inventory_route[0] != route.inventory_sequence
            or inventory_route[1].sequence != route.group_sequence
            or inventory_route[2].gate_key != route.gate_key
            or inventory_route[2].sequence != route.stage_sequence
            or inventory_route[2].title != route.title
            or inventory_route[2].role != route.role
            or inventory_route[2].runtime != route.runtime
        ):
            raise OrchestrationValidationError(
                "Authoritative route does not match its sealed inventory and methodology"
            )
        stored_contract = task.metadata.get("task_contract")
        contract_digest = contract_sha256(contract)
        if (
            stored_contract is None
            or TaskContract.model_validate(stored_contract) != contract
            or task.metadata.get("task_contract_id") != contract.contract_id
            or task.metadata.get("task_contract_schema_version")
            != contract.schema_version
            or task.metadata.get("task_contract_sha256") != contract_digest
            or inventory.contract is None
            or inventory.contract.contract_id != contract.contract_id
            or inventory.contract.schema_version != contract.schema_version
            or inventory.contract.sha256 != contract_digest
        ):
            raise OrchestrationValidationError(
                "Routing policy requires the exact pinned Task contract"
            )

        stage_rows = db.execute(
            "SELECT * FROM orchestration_stages WHERE plan_id = ? ORDER BY sequence",
            (plan["plan_id"],),
        ).fetchall()
        stages = [
            RoutingStageBudget(
                stage_key=row["stage_key"],
                sequence=int(row["sequence"]),
                title=row["title"],
                role=row["role"],
                runtime=row["adapter"],
                state=StageState(row["state"]),
                token_budget=int(row["token_budget"]),
                cost_budget_usd=row["cost_budget_usd"],
            )
            for row in stage_rows
        ]
        budget = self._provider_budget_snapshot(db, plan["plan_id"])
        settled_tokens = int(budget["settled_tokens"])
        active_tokens = int(budget["active_tokens"])

        if plan["total_cost_budget_usd"] is None:
            settled_cost = None
            active_cost = None
        else:
            settled_cost = float(budget["settled_cost"])
            active_cost = float(budget["active_cost"])

        return derive_routing_policy_decision(
            decision_id=f"routing-policy:{hashlib.sha256(run_id.encode('utf-8')).hexdigest()[:32]}",
            task=task,
            contract=contract,
            methodology=methodology,
            methodology_sha256=methodology_digest,
            plan_id=plan["plan_id"],
            route=route,
            stages=stages,
            task_token_budget=int(plan["total_token_budget"]),
            settled_token_debit=settled_tokens,
            active_token_reservations=active_tokens,
            task_cost_budget_usd=plan["total_cost_budget_usd"],
            settled_cost_debit_usd=settled_cost,
            active_cost_reservations_usd=active_cost,
        )

    def claim_current_stage(
        self,
        task_id: str,
        *,
        prompt_sha256: str,
        operation_key: str,
        run_id: str | None = None,
        expected_stage_key: str | None = None,
        expected_adapter: str | None = None,
        route: StageRouteDecision | None = None,
        contract: TaskContract | None = None,
        routing_policy: RoutingPolicyDecision | None = None,
        runtime_preflight: PinnedRuntimePreflightDecision | None = None,
        actor: str = "orchestrator",
    ) -> OrchestrationRun:
        policy_inputs = (route, contract, routing_policy, runtime_preflight)
        if any(item is not None for item in policy_inputs) and not all(
            item is not None for item in policy_inputs
        ):
            raise OrchestrationValidationError(
                "Formal routing and runtime preflight inputs must be supplied together"
            )
        if routing_policy is not None and run_id is None:
            raise OrchestrationValidationError(
                "A policy-bound formal Run requires a stable Run id"
            )
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
            active_consultation = db.execute(
                """SELECT consultation_id FROM orchestration_consultations
                   WHERE plan_id = ? AND state = ? LIMIT 1""",
                (plan["plan_id"], ConsultationState.RUNNING.value),
            ).fetchone()
            if active_consultation is not None:
                raise OrchestrationConflictError(
                    "An advisory consultation is already active for this Plan"
                )
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
            budget = self._provider_budget_snapshot(db, plan["plan_id"])
            settled = budget["settled_tokens"]
            active_reserved = budget["active_tokens"]
            if routing_policy is not None:
                assert route is not None and contract is not None and run_id is not None
                current_policy = self._routing_policy_decision(
                    db,
                    task_id=task_id,
                    route=route,
                    contract=contract,
                    run_id=run_id,
                )
                if current_policy != routing_policy:
                    raise OrchestrationConflictError(
                        "Routing policy inputs changed before the authoritative claim"
                    )
                if not current_policy.dispatchable:
                    raise OrchestrationConflictError(current_policy.blockers[0])
                assert runtime_preflight is not None
                if (
                    not runtime_preflight.allowed
                    or runtime_preflight.task_id != task_id
                    or runtime_preflight.project_id != route.project_id
                    or runtime_preflight.run_id != run_id
                    or runtime_preflight.inventory_id != route.inventory_id
                    or runtime_preflight.inventory_sha256 != route.inventory_sha256
                    or runtime_preflight.stage_key != route.stage_key
                    or runtime_preflight.role != route.role
                    or runtime_preflight.pinned_runtime != route.runtime
                    or runtime_preflight.routing_policy_decision_id
                    != current_policy.decision_id
                    or runtime_preflight.routing_policy_decision_sha256
                    != current_policy.content_sha256
                    or runtime_preflight.routing_policy_declaration_sha256
                    != current_policy.policy_sha256
                ):
                    raise OrchestrationConflictError(
                        "Runtime preflight does not match the authoritative Run claim"
                    )
                token_reservation = current_policy.current_run_token_reservation
                cost_reservation = current_policy.current_run_cost_reservation_usd
                routing_policy_payload = self._json(current_policy.model_dump(mode="json"))
                runtime_preflight_payload = self._json(
                    runtime_preflight.model_dump(mode="json")
                )
            else:
                if (
                    settled + active_reserved + stage["token_budget"]
                    > plan["total_token_budget"]
                ):
                    raise OrchestrationConflictError(
                        "Token budget is exhausted; increase it before retrying"
                    )
                if plan["total_cost_budget_usd"] is not None:
                    if stage["cost_budget_usd"] is None:
                        raise OrchestrationConflictError(
                            "Current Stage cost reservation is unavailable"
                        )
                    if (
                        budget["settled_cost"]
                        + budget["active_cost"]
                        + stage["cost_budget_usd"]
                        > plan["total_cost_budget_usd"] + 1e-9
                    ):
                        raise OrchestrationConflictError(
                            "Cost budget is exhausted; increase it before retrying"
                        )
                token_reservation = stage["token_budget"]
                cost_reservation = stage["cost_budget_usd"]
                routing_policy_payload = None
                runtime_preflight_payload = None

            run_id = run_id or self._id("orun")
            attempt = int(stage["attempt_count"]) + 1
            db.execute(
                """
                INSERT INTO orchestration_runs (
                    run_id, plan_id, task_id, stage_key, adapter, state, operation_key,
                    prompt_sha256, findings, token_reserved, token_measurement,
                    cost_reserved_usd, cost_measurement, attempt,
                    routing_policy_payload, runtime_preflight_payload, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, plan["plan_id"], task_id, stage["stage_key"], stage["adapter"],
                    RunState.RUNNING.value, operation_key, prompt_sha256, token_reservation,
                    Measurement.UNAVAILABLE.value, cost_reservation,
                    Measurement.UNAVAILABLE.value, attempt, routing_policy_payload,
                    runtime_preflight_payload, now,
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
                    LedgerEntryType.RESERVATION.value, token_reservation,
                    Measurement.UNAVAILABLE.value, cost_reservation,
                    Measurement.UNAVAILABLE.value, stage["adapter"], now,
                ),
            )
            self.tasks._insert_event(
                db, task_id=task_id, event_type="orchestration.run_started", actor=actor,
                payload={
                    "plan_id": plan["plan_id"], "run_id": run_id,
                    "stage_key": stage["stage_key"], "adapter": stage["adapter"],
                    "token_reserved": token_reservation,
                    "cost_reserved_usd": cost_reservation,
                    "routing_policy_id": (
                        routing_policy.decision_id if routing_policy is not None else None
                    ),
                    "routing_policy_sha256": (
                        routing_policy.content_sha256
                        if routing_policy is not None
                        else None
                    ),
                    "runtime_preflight_id": (
                        runtime_preflight.decision_id
                        if runtime_preflight is not None
                        else None
                    ),
                    "runtime_preflight_sha256": (
                        runtime_preflight.content_sha256
                        if runtime_preflight is not None
                        else None
                    ),
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
        cost_used_usd: float | None,
        cost_measurement: Measurement,
        usage_observation: ProviderUsageObservation | None = None,
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
        self._validate_measured_cost(cost_used_usd, cost_measurement)
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
            self._validate_usage_observation(
                run,
                usage_observation,
                token_used=token_used,
                token_measurement=token_measurement,
                cost_used_usd=cost_used_usd,
                cost_measurement=cost_measurement,
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
                    token_used = ?, token_measurement = ?, cost_used_usd = ?,
                    cost_measurement = ?, usage_observation_payload = ?, finished_at = ?
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
                    cost_used_usd,
                    cost_measurement.value,
                    (
                        self._json(usage_observation.model_dump(mode="json"))
                        if usage_observation is not None
                        else None
                    ),
                    now,
                    run_id,
                ),
            )
            db.execute(
                """
                INSERT INTO orchestration_usage_ledger (
                    entry_id, task_id, plan_id, stage_key, run_id, entry_type,
                    tokens, token_measurement, cost_usd, cost_measurement, adapter, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    cost_used_usd,
                    cost_measurement.value,
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
                    "cost_used_usd": cost_used_usd,
                    "cost_measurement": cost_measurement.value,
                    "usage_observation_sha256": (
                        usage_observation.content_sha256
                        if usage_observation is not None
                        else None
                    ),
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
        cost_used_usd: float | None = None,
        cost_measurement: Measurement = Measurement.UNAVAILABLE,
        usage_observation: ProviderUsageObservation | None = None,
        actor: str = "orchestrator",
    ) -> OrchestrationRun:
        self._validate_measured_cost(cost_used_usd, cost_measurement)
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
            self._validate_usage_observation(
                run,
                usage_observation,
                token_used=token_used,
                token_measurement=token_measurement,
                cost_used_usd=cost_used_usd,
                cost_measurement=cost_measurement,
            )
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
            if (
                cost_used_usd is not None
                and run["cost_reserved_usd"] is not None
                and cost_used_usd > run["cost_reserved_usd"]
            ):
                blockers.append(
                    f"Measured cost {cost_used_usd} exceeded the reserved "
                    f"{run['cost_reserved_usd']} USD"
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
                    token_used = ?, token_measurement = ?, cost_used_usd = ?,
                    cost_measurement = ?, usage_observation_payload = ?, finished_at = ?
                WHERE run_id = ?
                """,
                (
                    run_state.value, exit_code, int(timed_out), safe_output, safe_error,
                    semantic.status.value if semantic else None, safe_summary,
                    self._json(sanitize_data(safe_findings)), token_used,
                    token_measurement.value, cost_used_usd,
                    cost_measurement.value,
                    (
                        self._json(usage_observation.model_dump(mode="json"))
                        if usage_observation is not None
                        else None
                    ),
                    now, run_id,
                ),
            )
            db.execute(
                """
                INSERT INTO orchestration_usage_ledger (
                    entry_id, task_id, plan_id, stage_key, run_id, entry_type,
                    tokens, token_measurement, cost_usd, cost_measurement, adapter, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._id("usage"), run["task_id"], run["plan_id"], run["stage_key"],
                    run_id, LedgerEntryType.SETTLEMENT.value, token_used,
                    token_measurement.value, cost_used_usd, cost_measurement.value,
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
                    "cost_used_usd": cost_used_usd,
                    "cost_measurement": cost_measurement.value,
                    "usage_observation_sha256": (
                        usage_observation.content_sha256
                        if usage_observation is not None
                        else None
                    ),
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

    def activate_methodology_successor(
        self,
        task_id: str,
        request: MethodologyMigrationPreviewRequest,
        *,
        principal: ControlPrincipal,
        control_plane: ControlPlaneStore,
        recheck: Callable[
            [MethodologyMigrationStateSnapshot, str, str, str],
            MethodologySuccessorMaterialization,
        ],
    ) -> MethodologyMigrationActivationReceipt:
        """Atomically recheck and create one authenticated successor Task."""

        actor = principal.principal_id
        now = utc_now()
        with self._transaction() as db:
            existing = db.execute(
                """
                SELECT * FROM orchestration_methodology_migrations
                WHERE request_id = ?
                """,
                (request.request_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["request_sha256"] != request.content_sha256
                    or existing["source_task_id"] != task_id
                    or existing["authenticated_principal_id"] != actor
                ):
                    raise OrchestrationConflictError(
                        "Methodology migration request id already has different bindings"
                    )
                return MethodologyMigrationActivationReceipt.model_validate_json(
                    existing["receipt_payload"]
                )
            prior = db.execute(
                """
                SELECT request_id FROM orchestration_methodology_migrations
                WHERE source_task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if prior is not None:
                raise OrchestrationConflictError(
                    "Source Task already has a methodology migration successor"
                )

            snapshot = self._methodology_migration_snapshot_tx(db, task_id)
            successor_task_id = self._id("task")
            successor_plan_id = self._id("plan")
            materialization = recheck(
                snapshot,
                successor_task_id,
                successor_plan_id,
                now,
            )
            assertion = materialization.authenticated_gate.assertion
            if (
                assertion.task_id != task_id
                or assertion.project_id != snapshot.task.project_id
            ):
                raise OrchestrationValidationError(
                    "Authenticated migration Gate does not match the source Task"
                )
            inventory = materialization.inventory
            if (
                inventory.task_id != successor_task_id
                or inventory.plan_id != successor_plan_id
                or inventory.project_id != snapshot.task.project_id
                or inventory.methodology_id != request.target_methodology_id
                or inventory.methodology_version
                != request.target_methodology_version
                or inventory.methodology_sha256
                != request.target_activation_definition_sha256
            ):
                raise OrchestrationValidationError(
                    "Successor inventory does not match the migration request"
                )
            if not materialization.stages:
                raise OrchestrationValidationError(
                    "Methodology successor requires at least one Stage"
                )
            inventory_stage_keys = [
                stage.stage_key
                for group in inventory.groups
                for stage in group.stages
            ]
            planned_stage_keys = [
                stage.stage_key for stage in materialization.stages
            ]
            if inventory_stage_keys != planned_stage_keys:
                raise OrchestrationValidationError(
                    "Successor Plan Stages do not match the sealed inventory"
                )

            db.execute(
                """
                INSERT INTO tasks (
                    task_id, project_id, title, description, kind, state, risk,
                    priority, primary_agent, reviewers, acceptance, budget,
                    metadata, version, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    successor_task_id,
                    snapshot.task.project_id,
                    materialization.task_title,
                    materialization.task_description,
                    materialization.task_kind,
                    TaskState.BACKLOG.value,
                    materialization.task_risk,
                    materialization.task_priority,
                    materialization.task_primary_agent,
                    self._json(list(materialization.task_reviewers)),
                    self._json(list(materialization.task_acceptance)),
                    self._json(
                        TaskBudget(
                            max_cost_usd=request.budget.task_cost_budget_usd
                        ).model_dump(exclude_none=True)
                    ),
                    self._json(materialization.task_metadata),
                    actor,
                    now,
                    now,
                ),
            )
            self.tasks._insert_event(
                db,
                task_id=successor_task_id,
                event_type="task_created",
                actor=actor,
                payload={"state": TaskState.BACKLOG.value, "version": 1},
                created_at=now,
            )
            db.execute(
                """
                INSERT INTO orchestration_plans (
                    plan_id, task_id, project_id, methodology_id,
                    methodology_version, methodology_sha256,
                    methodology_payload, provisional, state,
                    total_token_budget, total_cost_budget_usd,
                    current_stage_key, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    successor_plan_id,
                    successor_task_id,
                    snapshot.task.project_id,
                    request.target_methodology_id,
                    request.target_methodology_version,
                    request.target_activation_definition_sha256,
                    self._json(materialization.methodology_payload),
                    PlanState.READY_FOR_IMPLEMENTATION.value,
                    request.budget.task_token_budget,
                    request.budget.task_cost_budget_usd,
                    materialization.stages[0].stage_key,
                    now,
                    now,
                ),
            )
            for sequence, stage in enumerate(materialization.stages, start=1):
                db.execute(
                    """
                    INSERT INTO orchestration_stages (
                        stage_id, plan_id, stage_key, sequence, title, role,
                        adapter, state, token_budget, cost_budget_usd, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self._id("stage"),
                        successor_plan_id,
                        stage.stage_key,
                        sequence,
                        stage.title,
                        stage.role,
                        stage.runtime,
                        StageState.PENDING.value,
                        stage.token_budget,
                        stage.cost_budget_usd,
                        now,
                    ),
                )
            self.tasks._insert_event(
                db,
                task_id=successor_task_id,
                event_type="orchestration.plan_created",
                actor=actor,
                payload={
                    "plan_id": successor_plan_id,
                    "methodology": (
                        f"{request.target_methodology_id}@"
                        f"{request.target_methodology_version}"
                    ),
                    "methodology_sha256": (
                        request.target_activation_definition_sha256
                    ),
                    "provisional": False,
                    "total_token_budget": request.budget.task_token_budget,
                    "total_cost_budget_usd": (
                        request.budget.task_cost_budget_usd
                    ),
                    "dispatch_authority": False,
                },
                created_at=now,
            )
            successor_control_task = (
                control_plane.initialize_migrated_successor_tx(
                    db,
                    inventory,
                    actor=actor,
                    now=now,
                )
            )

            recheck_decision = materialization.recheck_decision
            source_control_version = (
                recheck_decision.observed_control_task_version
            )
            if source_control_version is None:
                raise OrchestrationValidationError(
                    "Eligible migration recheck omitted the source Control Task"
                )
            gate = materialization.authenticated_gate
            receipt_payload = {
                "schema_version": "1.0",
                "receipt_id": (
                    f"migration-receipt-{request.content_sha256[:20]}"
                ),
                "activated_at": now,
                "request_id": request.request_id,
                "request_sha256": request.content_sha256,
                "recheck_decision": recheck_decision.model_dump(mode="json"),
                "authenticated_gate": gate.model_dump(mode="json"),
                "authenticated_gate_id": gate.gate_id,
                "authenticated_gate_sha256": gate.content_sha256,
                "project_id": snapshot.task.project_id,
                "source_task_id": snapshot.task.task_id,
                "source_task_version": snapshot.task.version,
                "source_control_task_version": source_control_version,
                "successor_task_id": successor_task_id,
                "successor_task_version": 1,
                "successor_control_task_status": (
                    successor_control_task.status.value
                ),
                "successor_control_task_version": (
                    successor_control_task.version
                ),
                "successor_plan_id": successor_plan_id,
                "successor_plan_version": 1,
                "successor_inventory_id": inventory.inventory_id,
                "successor_inventory_sha256": inventory.content_sha256,
                "target_activation_id": request.target_activation_id,
                "target_methodology_id": request.target_methodology_id,
                "target_methodology_version": (
                    request.target_methodology_version
                ),
                "target_source_graph_sha256": (
                    request.target_source_graph_sha256
                ),
                "target_activation_definition_sha256": (
                    request.target_activation_definition_sha256
                ),
                "selected_scope": request.selected_scope,
                "migration_strategy": "successor_task",
                "source_task_preserved": True,
                "migration_gate_persisted": True,
                "successor_task_created": True,
                "successor_plan_sealed": True,
                "successor_inventory_sealed": True,
                "route_activated": False,
                "runtime_spawned": False,
                "dispatch_authority": False,
            }
            receipt = MethodologyMigrationActivationReceipt.model_validate(
                seal_model_payload(
                    MethodologyMigrationActivationReceipt,
                    receipt_payload,
                )
            )
            db.execute(
                """
                INSERT INTO orchestration_methodology_migrations (
                    request_id, request_sha256, request_payload,
                    source_task_id, successor_task_id, successor_plan_id,
                    gate_id, gate_sha256, gate_payload,
                    recheck_decision_sha256, recheck_decision_payload,
                    receipt_sha256, receipt_payload,
                    authenticated_principal_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.request_id,
                    request.content_sha256,
                    self._json(request.model_dump(mode="json")),
                    task_id,
                    successor_task_id,
                    successor_plan_id,
                    gate.gate_id,
                    gate.content_sha256,
                    self._json(gate.model_dump(mode="json")),
                    recheck_decision.content_sha256,
                    self._json(recheck_decision.model_dump(mode="json")),
                    receipt.content_sha256,
                    self._json(receipt.model_dump(mode="json")),
                    actor,
                    now,
                ),
            )
            control_plane._event(
                db,
                event_key=f"methodology.migration.activate:{request.request_id}",
                task_id=successor_task_id,
                project_id=snapshot.task.project_id,
                event_type="methodology.migration_activated",
                actor=actor,
                payload={
                    "source_task_id": task_id,
                    "request_id": request.request_id,
                    "request_sha256": request.content_sha256,
                    "gate_id": gate.gate_id,
                    "gate_sha256": gate.content_sha256,
                    "receipt_id": receipt.receipt_id,
                    "receipt_sha256": receipt.content_sha256,
                    "plan_id": successor_plan_id,
                    "inventory_id": inventory.inventory_id,
                    "inventory_sha256": inventory.content_sha256,
                    "route_activated": False,
                    "dispatch_authority": False,
                },
                now=now,
            )
            return receipt

    def get_methodology_execution_contract(
        self,
        task_id: str,
    ) -> MethodologyExecutionContract | None:
        """Return one sealed successor execution contract without mutation."""

        with closing(self._connect()) as db:
            row = db.execute(
                """
                SELECT * FROM orchestration_methodology_execution_contracts
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        contract = MethodologyExecutionContract.model_validate_json(
            row["contract_payload"]
        )
        if (
            contract.contract_id != row["contract_id"]
            or contract.task_id != row["task_id"]
            or contract.plan_id != row["plan_id"]
            or contract.inventory_id != row["inventory_id"]
            or contract.inventory_sha256 != row["inventory_sha256"]
            or contract.migration_request_id != row["migration_request_id"]
            or contract.migration_receipt_sha256
            != row["migration_receipt_sha256"]
            or contract.content_sha256 != row["contract_sha256"]
            or contract.authenticated_principal_id
            != row["authenticated_principal_id"]
        ):
            raise OrchestrationValidationError(
                "Persisted methodology execution contract binding drifted"
            )
        return contract

    def materialize_methodology_execution_contract(
        self,
        task_id: str,
        *,
        principal: ControlPrincipal,
        control_plane: ControlPlaneStore,
        materialize: Callable[
            [MethodologyExecutionSnapshot, str],
            MethodologyExecutionContract,
        ],
    ) -> MethodologyExecutionContract:
        """Atomically seal one inert execution contract for a successor Task."""

        now = utc_now()
        with self._transaction() as db:
            existing = db.execute(
                """
                SELECT * FROM orchestration_methodology_execution_contracts
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if existing is not None:
                contract = MethodologyExecutionContract.model_validate_json(
                    existing["contract_payload"]
                )
                if (
                    contract.content_sha256 != existing["contract_sha256"]
                    or contract.contract_id != existing["contract_id"]
                    or contract.task_id != task_id
                ):
                    raise OrchestrationValidationError(
                        "Persisted methodology execution contract binding drifted"
                    )
                if (
                    "control_plane.approve" not in principal.permissions
                    or contract.project_id not in principal.projects
                    or existing["authenticated_principal_id"]
                    != principal.principal_id
                ):
                    raise OrchestrationValidationError(
                        "Methodology execution contract replay requires the "
                        "original currently authorized principal"
                    )
                return contract

            migration = db.execute(
                """
                SELECT * FROM orchestration_methodology_migrations
                WHERE successor_task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if migration is None:
                raise OrchestrationConflictError(
                    "Task is not a migrated methodology successor"
                )
            request = MethodologyMigrationPreviewRequest.model_validate_json(
                migration["request_payload"]
            )
            gate = AuthenticatedMethodologyMigrationGate.model_validate_json(
                migration["gate_payload"]
            )
            receipt = MethodologyMigrationActivationReceipt.model_validate_json(
                migration["receipt_payload"]
            )
            if (
                request.request_id != migration["request_id"]
                or request.content_sha256 != migration["request_sha256"]
                or gate.gate_id != migration["gate_id"]
                or gate.content_sha256 != migration["gate_sha256"]
                or receipt.content_sha256 != migration["receipt_sha256"]
                or receipt.request_id != request.request_id
                or receipt.request_sha256 != request.content_sha256
                or receipt.authenticated_gate_id != gate.gate_id
                or receipt.authenticated_gate_sha256 != gate.content_sha256
                or migration["authenticated_principal_id"]
                != gate.authenticated_principal_id
            ):
                raise OrchestrationValidationError(
                    "Methodology migration provenance is unavailable or drifted"
                )
            if "control_plane.approve" not in principal.permissions:
                raise OrchestrationValidationError(
                    "Authenticated principal lacks control_plane.approve permission"
                )
            if request.project_id not in principal.projects:
                raise OrchestrationValidationError(
                    "Authenticated principal is not authorized for the successor project"
                )
            if (
                principal.principal_id
                != migration["authenticated_principal_id"]
            ):
                raise OrchestrationValidationError(
                    "Execution contract principal does not match the migration Gate"
                )

            task_row = db.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if task_row is None:
                raise OrchestrationNotFoundError(task_id)
            task = self.tasks._manifest(task_row)
            plan_row = db.execute(
                """
                SELECT * FROM orchestration_plans
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if plan_row is None:
                raise OrchestrationValidationError(
                    "Methodology successor Plan is unavailable"
                )
            plan = self._plan(plan_row)
            stage_rows = db.execute(
                """
                SELECT * FROM orchestration_stages
                WHERE plan_id = ?
                ORDER BY sequence
                """,
                (plan.plan_id,),
            ).fetchall()
            plan_stages = tuple(self._stage(row) for row in stage_rows)
            control_task_row = db.execute(
                "SELECT * FROM control_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if control_task_row is None:
                raise OrchestrationValidationError(
                    "Methodology successor Control Task is unavailable"
                )
            control_task = control_plane._task_record(control_task_row)
            inventory_row = db.execute(
                """
                SELECT * FROM control_stage_inventories
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if inventory_row is None:
                raise OrchestrationValidationError(
                    "Methodology successor Stage inventory is unavailable"
                )
            inventory = StageInventory.model_validate_json(
                inventory_row["payload"]
            )
            if (
                inventory.content_sha256 != inventory_row["content_sha256"]
                or inventory.inventory_id != inventory_row["inventory_id"]
                or inventory.task_id != task_id
            ):
                raise OrchestrationValidationError(
                    "Methodology successor inventory binding drifted"
                )
            if (
                control_task.status.value
                != receipt.successor_control_task_status.value
                or control_task.version
                != receipt.successor_control_task_version
            ):
                raise OrchestrationConflictError(
                    "Methodology successor lifecycle changed before contract materialization"
                )
            for table in (
                "orchestration_runs",
                "orchestration_consultations",
                "control_stages",
                "control_gates",
                "protocol_runs",
            ):
                if db.execute(
                    f"SELECT 1 FROM {table} WHERE task_id = ? LIMIT 1",
                    (task_id,),
                ).fetchone() is not None:
                    raise OrchestrationConflictError(
                        "Methodology successor execution state already exists"
                    )
            if (
                task.metadata.get("methodology_route_activated") is not False
                or task.metadata.get("methodology_dispatch_authority") is not False
                or plan.state != PlanState.READY_FOR_IMPLEMENTATION
                or any(stage.state != StageState.PENDING for stage in plan_stages)
            ):
                raise OrchestrationConflictError(
                    "Methodology successor is no longer inert"
                )

            snapshot = MethodologyExecutionSnapshot(
                task=task,
                control_task=control_task,
                plan=plan,
                plan_stages=plan_stages,
                inventory=inventory,
                request=request,
                gate=gate,
                receipt=receipt,
            )
            contract = materialize(snapshot, now)
            if (
                contract.task_id != task_id
                or contract.project_id != task.project_id
                or contract.plan_id != plan.plan_id
                or contract.inventory_id != inventory.inventory_id
                or contract.inventory_sha256 != inventory.content_sha256
                or contract.migration_request_id != request.request_id
                or contract.migration_request_sha256 != request.content_sha256
                or contract.migration_gate_id != gate.gate_id
                or contract.migration_gate_sha256 != gate.content_sha256
                or contract.migration_receipt_id != receipt.receipt_id
                or contract.migration_receipt_sha256 != receipt.content_sha256
                or contract.authenticated_principal_id
                != principal.principal_id
                or contract.route_activated
                or contract.runtime_spawned
                or contract.routing_authority
                or contract.dispatch_authority
            ):
                raise OrchestrationValidationError(
                    "Materialized methodology execution contract binding differs"
                )
            db.execute(
                """
                INSERT INTO orchestration_methodology_execution_contracts (
                    contract_id, task_id, plan_id, inventory_id,
                    inventory_sha256, migration_request_id,
                    migration_receipt_sha256, contract_sha256,
                    contract_payload, authenticated_principal_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    contract.contract_id,
                    task_id,
                    plan.plan_id,
                    inventory.inventory_id,
                    inventory.content_sha256,
                    request.request_id,
                    receipt.content_sha256,
                    contract.content_sha256,
                    self._json(contract.model_dump(mode="json")),
                    principal.principal_id,
                    now,
                ),
            )
            control_plane._event(
                db,
                event_key=f"methodology.execution_contract:{task_id}",
                task_id=task_id,
                project_id=task.project_id,
                event_type="methodology.execution_contract_materialized",
                actor=principal.principal_id,
                payload={
                    "contract_id": contract.contract_id,
                    "contract_sha256": contract.content_sha256,
                    "plan_id": plan.plan_id,
                    "inventory_id": inventory.inventory_id,
                    "inventory_sha256": inventory.content_sha256,
                    "migration_receipt_id": receipt.receipt_id,
                    "migration_receipt_sha256": receipt.content_sha256,
                    "stage_count": len(contract.stages),
                    "route_activated": False,
                    "dispatch_authority": False,
                },
                now=now,
            )
            return contract

    def get_methodology_route_activation(
        self,
        task_id: str,
    ) -> MethodologyRouteActivationReceipt | None:
        """Return one immutable first-route activation receipt."""

        with closing(self._connect()) as db:
            row = db.execute(
                """
                SELECT * FROM orchestration_methodology_route_activations
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        receipt = MethodologyRouteActivationReceipt.model_validate_json(
            row["receipt_payload"]
        )
        if (
            receipt.receipt_id != row["receipt_id"]
            or receipt.content_sha256 != row["receipt_sha256"]
            or receipt.request_id != row["request_id"]
            or receipt.request_sha256 != row["request_sha256"]
            or receipt.task_id != row["task_id"]
            or receipt.execution_contract_id
            != row["execution_contract_id"]
            or receipt.execution_contract_sha256
            != row["execution_contract_sha256"]
            or receipt.authenticated_principal_id
            != row["authenticated_principal_id"]
        ):
            raise OrchestrationValidationError(
                "Persisted methodology route activation binding drifted"
            )
        return receipt

    def activate_methodology_first_route(
        self,
        task_id: str,
        request: MethodologyRouteActivationRequest,
        *,
        principal: ControlPrincipal,
        control_plane: ControlPlaneStore,
        recheck: Callable[
            [MethodologyRouteActivationSnapshot, str],
            tuple[MethodologySeedArtifactRegistration, ...],
        ],
    ) -> MethodologyRouteActivationReceipt:
        """Atomically configure and activate one contract-bound first route."""

        if request.task_id != task_id:
            raise OrchestrationValidationError(
                "Methodology route activation Task argument differs from request"
            )
        now = utc_now()
        with self._transaction() as db:
            existing = db.execute(
                """
                SELECT * FROM orchestration_methodology_route_activations
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if existing is not None:
                receipt = MethodologyRouteActivationReceipt.model_validate_json(
                    existing["receipt_payload"]
                )
                if (
                    request.request_id != existing["request_id"]
                    or request.content_sha256 != existing["request_sha256"]
                    or receipt.content_sha256 != existing["receipt_sha256"]
                    or receipt.receipt_id != existing["receipt_id"]
                    or receipt.request_id != existing["request_id"]
                    or receipt.request_sha256 != existing["request_sha256"]
                    or receipt.task_id != task_id
                    or receipt.execution_contract_id
                    != existing["execution_contract_id"]
                    or receipt.execution_contract_sha256
                    != existing["execution_contract_sha256"]
                    or receipt.authenticated_principal_id
                    != existing["authenticated_principal_id"]
                ):
                    raise OrchestrationConflictError(
                        "Methodology route activation replay binding differs"
                    )
                if (
                    "control_plane.approve" not in principal.permissions
                    or receipt.project_id not in principal.projects
                    or principal.principal_id
                    != existing["authenticated_principal_id"]
                ):
                    raise OrchestrationValidationError(
                        "Methodology route activation replay requires the "
                        "original currently authorized principal"
                    )
                return receipt

            contract_row = db.execute(
                """
                SELECT * FROM orchestration_methodology_execution_contracts
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if contract_row is None:
                raise OrchestrationConflictError(
                    "Methodology successor execution contract is unavailable"
                )
            contract = MethodologyExecutionContract.model_validate_json(
                contract_row["contract_payload"]
            )
            if (
                contract.contract_id != contract_row["contract_id"]
                or contract.content_sha256 != contract_row["contract_sha256"]
                or contract.task_id != task_id
                or request.execution_contract_id != contract.contract_id
                or request.execution_contract_sha256
                != contract.content_sha256
            ):
                raise OrchestrationValidationError(
                    "Methodology execution contract binding drifted"
                )
            migration = db.execute(
                """
                SELECT * FROM orchestration_methodology_migrations
                WHERE successor_task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if migration is None:
                raise OrchestrationConflictError(
                    "Task is not a migrated methodology successor"
                )
            migration_request = (
                MethodologyMigrationPreviewRequest.model_validate_json(
                    migration["request_payload"]
                )
            )
            migration_gate = (
                AuthenticatedMethodologyMigrationGate.model_validate_json(
                    migration["gate_payload"]
                )
            )
            migration_receipt = (
                MethodologyMigrationActivationReceipt.model_validate_json(
                    migration["receipt_payload"]
                )
            )
            if (
                migration_request.content_sha256
                != migration["request_sha256"]
                or migration_gate.content_sha256 != migration["gate_sha256"]
                or migration_receipt.content_sha256
                != migration["receipt_sha256"]
                or contract.migration_request_sha256
                != migration_request.content_sha256
                or contract.migration_gate_sha256
                != migration_gate.content_sha256
                or contract.migration_receipt_sha256
                != migration_receipt.content_sha256
            ):
                raise OrchestrationValidationError(
                    "Methodology route activation migration provenance drifted"
                )
            if "control_plane.approve" not in principal.permissions:
                raise OrchestrationValidationError(
                    "Authenticated principal lacks control_plane.approve permission"
                )
            if contract.project_id not in principal.projects:
                raise OrchestrationValidationError(
                    "Authenticated principal is not authorized for the successor project"
                )
            if (
                principal.principal_id
                != contract_row["authenticated_principal_id"]
                or principal.principal_id
                != migration["authenticated_principal_id"]
            ):
                raise OrchestrationValidationError(
                    "Route activation principal does not match the migration Gate"
                )

            task_row = db.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if task_row is None:
                raise OrchestrationNotFoundError(task_id)
            task = self.tasks._manifest(task_row)
            plan_row = db.execute(
                """
                SELECT * FROM orchestration_plans
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if plan_row is None:
                raise OrchestrationValidationError(
                    "Methodology successor Plan is unavailable"
                )
            plan = self._plan(plan_row)
            plan_stage_rows = db.execute(
                """
                SELECT * FROM orchestration_stages
                WHERE plan_id = ? ORDER BY sequence
                """,
                (plan.plan_id,),
            ).fetchall()
            plan_stages = tuple(
                self._stage(row) for row in plan_stage_rows
            )
            control_task_row = db.execute(
                "SELECT * FROM control_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if control_task_row is None:
                raise OrchestrationValidationError(
                    "Methodology successor Control Task is unavailable"
                )
            control_task = control_plane._task_record(control_task_row)
            inventory_row = db.execute(
                """
                SELECT * FROM control_stage_inventories
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if inventory_row is None:
                raise OrchestrationValidationError(
                    "Methodology successor Stage inventory is unavailable"
                )
            inventory = StageInventory.model_validate_json(
                inventory_row["payload"]
            )
            if (
                inventory.content_sha256 != inventory_row["content_sha256"]
                or inventory.inventory_id != inventory_row["inventory_id"]
                or inventory.task_id != task_id
            ):
                raise OrchestrationValidationError(
                    "Methodology successor inventory binding drifted"
                )
            route = control_plane.stage_route_snapshot(db, task_id)
            if route is None:
                raise OrchestrationConflictError(
                    "Methodology successor first route is unavailable"
                )
            for table in (
                "orchestration_runs",
                "orchestration_consultations",
                "control_stages",
                "control_gates",
                "protocol_runs",
                "protocol_artifacts",
                "protocol_evidence",
            ):
                if db.execute(
                    f"SELECT 1 FROM {table} WHERE task_id = ? LIMIT 1",
                    (task_id,),
                ).fetchone() is not None:
                    raise OrchestrationConflictError(
                        "Methodology successor execution state already exists"
                    )

            snapshot = MethodologyRouteActivationSnapshot(
                task=task,
                control_task=control_task,
                plan=plan,
                plan_stages=plan_stages,
                inventory=inventory,
                migration_request=migration_request,
                migration_gate=migration_gate,
                migration_receipt=migration_receipt,
                execution_contract=contract,
                route=route,
            )
            try:
                seed_artifacts = recheck(snapshot, now)
            except ValueError as exc:
                raise OrchestrationConflictError(
                    f"Methodology route activation blocked: {exc}"
                ) from exc

            first_stage = contract.stages[0]
            expected_seed_artifacts = tuple(
                MethodologySeedArtifactRegistration(
                    consumer_stage_key=first_stage.stage_key,
                    source_artifact_id=item.source_artifact_id,
                    artifact=item.seed_artifact,
                )
                for item in first_stage.context.input_contracts
                if item.resolution == "hash_bound_task_seed"
                and item.required
                and item.seed_artifact is not None
            )
            if seed_artifacts != expected_seed_artifacts:
                raise OrchestrationValidationError(
                    "Methodology route activation seed references differ "
                    "from the first Stage execution contract"
                )
            requirements = [
                item.requirement
                for item in first_stage.gate.evidence_contracts
            ]
            activation, gate, control_task_after = (
                control_plane.activate_methodology_first_route_tx(
                    db,
                    task_id=task_id,
                    expected_inventory_id=inventory.inventory_id,
                    expected_inventory_sha256=inventory.content_sha256,
                    expected_stage_key=first_stage.stage_key,
                    expected_gate_key=first_stage.gate_key,
                    requirements=requirements,
                    actor=principal.principal_id,
                    event_key_prefix=(
                        f"methodology.route.activate:{request.request_id}"
                    ),
                    now=now,
                )
            )
            stage_row = db.execute(
                """
                SELECT * FROM control_stages
                WHERE task_id = ? AND stage_key = ?
                """,
                (task_id, first_stage.stage_key),
            ).fetchone()
            assert stage_row is not None
            formal_stage = control_plane._stage(stage_row)

            metadata = dict(task.metadata)
            metadata.update(
                {
                    "methodology_route_activated": True,
                    "methodology_dispatch_authority": False,
                    "methodology_route_activation_request_id": (
                        request.request_id
                    ),
                    "methodology_route_activation_request_sha256": (
                        request.content_sha256
                    ),
                    "methodology_execution_contract_id": (
                        contract.contract_id
                    ),
                    "methodology_execution_contract_sha256": (
                        contract.content_sha256
                    ),
                    "methodology_first_stage_key": first_stage.stage_key,
                    "methodology_first_gate_key": first_stage.gate_key,
                    "methodology_route_activated_by": principal.principal_id,
                    "methodology_route_activated_at": now,
                }
            )
            task_version_after = task.version + 1
            updated = db.execute(
                """
                UPDATE tasks
                SET metadata = ?, version = ?, updated_at = ?
                WHERE task_id = ? AND version = ?
                """,
                (
                    self._json(metadata),
                    task_version_after,
                    now,
                    task_id,
                    task.version,
                ),
            )
            if updated.rowcount != 1:
                raise OrchestrationConflictError(
                    "Methodology successor Task changed during route activation"
                )

            receipt_payload = {
                "schema_version": "1.0",
                "receipt_id": (
                    f"route-activation-{request.content_sha256[:20]}"
                ),
                "activated_at": now,
                "request_id": request.request_id,
                "request_sha256": request.content_sha256,
                "authenticated_principal_id": principal.principal_id,
                "authenticated_permission": "control_plane.approve",
                "project_id": task.project_id,
                "task_id": task_id,
                "task_version_before": task.version,
                "task_version_after": task_version_after,
                "control_task_status_before": control_task.status.value,
                "control_task_version_before": control_task.version,
                "control_task_status_after": (
                    control_task_after.status.value
                ),
                "control_task_version_after": control_task_after.version,
                "plan_id": plan.plan_id,
                "plan_version": plan.version,
                "inventory_id": inventory.inventory_id,
                "inventory_sha256": inventory.content_sha256,
                "execution_contract_id": contract.contract_id,
                "execution_contract_sha256": contract.content_sha256,
                "migration_receipt_id": migration_receipt.receipt_id,
                "migration_receipt_sha256": (
                    migration_receipt.content_sha256
                ),
                "repository": request.repository.model_dump(mode="json"),
                "first_stage_key": first_stage.stage_key,
                "first_stage_version": formal_stage.version,
                "first_stage_status": formal_stage.status.value,
                "first_gate_key": first_stage.gate_key,
                "first_gate_version": gate.version,
                "first_gate_status": gate.status.value,
                "seed_artifacts": [
                    item.model_dump(mode="json")
                    for item in seed_artifacts
                ],
                "request_authenticated": True,
                "first_stage_configured": True,
                "first_gate_configured": True,
                "seed_artifact_references_registered": True,
                "route_activated": True,
                "protocol_artifacts_created": False,
                "run_created": False,
                "runtime_spawned": False,
                "dispatch_authority": False,
                "provider_substitution": False,
            }
            receipt = MethodologyRouteActivationReceipt.model_validate(
                seal_model_payload(
                    MethodologyRouteActivationReceipt,
                    receipt_payload,
                )
            )
            if (
                activation.route.stage_key != receipt.first_stage_key
                or activation.route.gate_key != receipt.first_gate_key
                or activation.route.stage_status != StageStatus.READY
                or activation.route.gate_status != GateStatus.PENDING
                or not activation.activated
            ):
                raise OrchestrationValidationError(
                    "Methodology route activation receipt differs from Control Plane state"
                )

            db.execute(
                """
                INSERT INTO orchestration_methodology_route_activations (
                    request_id, request_sha256, request_payload,
                    receipt_id, receipt_sha256, receipt_payload,
                    task_id, execution_contract_id,
                    execution_contract_sha256,
                    authenticated_principal_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.request_id,
                    request.content_sha256,
                    self._json(request.model_dump(mode="json")),
                    receipt.receipt_id,
                    receipt.content_sha256,
                    self._json(receipt.model_dump(mode="json")),
                    task_id,
                    contract.contract_id,
                    contract.content_sha256,
                    principal.principal_id,
                    now,
                ),
            )
            for seed in seed_artifacts:
                location = seed.artifact.location
                assert location is not None
                payload = self._json(seed.model_dump(mode="json"))
                db.execute(
                    """
                    INSERT INTO orchestration_methodology_seed_artifact_refs (
                        task_id, consumer_stage_key, source_artifact_id,
                        artifact_id, artifact_version, artifact_sha256,
                        artifact_kind, repository_id, ref, commit_sha, path,
                        payload, payload_sha256, execution_contract_id,
                        activation_request_id, registered_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        seed.consumer_stage_key,
                        seed.source_artifact_id,
                        seed.artifact.artifact_id,
                        seed.artifact.version,
                        seed.artifact.sha256,
                        seed.artifact.kind,
                        location.repository_id,
                        location.ref,
                        location.commit_sha,
                        location.path,
                        payload,
                        canonical_sha256(seed),
                        contract.contract_id,
                        request.request_id,
                        now,
                    ),
                )
            self.tasks._insert_event(
                db,
                task_id=task_id,
                event_type="methodology_route_activated",
                actor=principal.principal_id,
                payload={
                    "request_id": request.request_id,
                    "request_sha256": request.content_sha256,
                    "receipt_id": receipt.receipt_id,
                    "receipt_sha256": receipt.content_sha256,
                    "execution_contract_id": contract.contract_id,
                    "execution_contract_sha256": contract.content_sha256,
                    "first_stage_key": first_stage.stage_key,
                    "first_gate_key": first_stage.gate_key,
                    "version": task_version_after,
                    "dispatch_authority": False,
                },
                created_at=now,
            )
            control_plane._event(
                db,
                event_key=(
                    f"methodology.route.activated:{request.request_id}"
                ),
                task_id=task_id,
                project_id=task.project_id,
                event_type="methodology.route_activated",
                actor=principal.principal_id,
                payload={
                    "request_id": request.request_id,
                    "request_sha256": request.content_sha256,
                    "receipt_id": receipt.receipt_id,
                    "receipt_sha256": receipt.content_sha256,
                    "execution_contract_id": contract.contract_id,
                    "execution_contract_sha256": contract.content_sha256,
                    "first_stage_key": first_stage.stage_key,
                    "first_gate_key": first_stage.gate_key,
                    "seed_artifact_reference_count": len(seed_artifacts),
                    "runtime_spawned": False,
                    "dispatch_authority": False,
                },
                now=now,
            )
            return receipt

    def get_methodology_run_claim(
        self,
        task_id: str,
    ) -> MethodologyRunClaimReceipt | None:
        """Return one immutable first methodology Run-claim receipt."""

        with closing(self._connect()) as db:
            row = db.execute(
                """
                SELECT c.*, r.context_pack_id AS protocol_context_pack_id,
                       r.context_sha256 AS protocol_context_sha256
                FROM orchestration_methodology_run_claims c
                LEFT JOIN protocol_runs r ON r.run_id = c.run_id
                WHERE c.task_id = ?
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        receipt = MethodologyRunClaimReceipt.model_validate_json(
            row["receipt_payload"]
        )
        persisted_request = MethodologyRunClaimRequest.model_validate_json(
            row["request_payload"]
        )
        if (
            persisted_request.request_id != row["request_id"]
            or persisted_request.content_sha256 != row["request_sha256"]
            or receipt.receipt_id != row["receipt_id"]
            or receipt.content_sha256 != row["receipt_sha256"]
            or receipt.request_id != row["request_id"]
            or receipt.request_sha256 != row["request_sha256"]
            or receipt.task_id != row["task_id"]
            or receipt.plan_id != row["plan_id"]
            or receipt.execution_contract_id
            != row["execution_contract_id"]
            or receipt.route_activation_receipt_id
            != row["route_activation_receipt_id"]
            or receipt.run_id != row["run_id"]
            or receipt.first_stage_key != row["stage_key"]
            or receipt.runtime != row["runtime"]
            or receipt.context_pack_id != row["context_pack_id"]
            or receipt.context_pack_sha256 != row["context_pack_sha256"]
            or receipt.context_pack_id != row["protocol_context_pack_id"]
            or receipt.context_pack_sha256 != row["protocol_context_sha256"]
            or receipt.budget.max_model_tokens != row["token_reserved"]
            or receipt.budget.max_cost_usd != row["cost_reserved_usd"]
            or receipt.authenticated_principal_id
            != row["authenticated_principal_id"]
            or bool(row["process_started"])
        ):
            raise OrchestrationValidationError(
                "Persisted methodology Run claim binding drifted"
            )
        return receipt

    def claim_methodology_first_run(
        self,
        task_id: str,
        request: MethodologyRunClaimRequest,
        *,
        principal: ControlPrincipal,
        control_plane: ControlPlaneStore,
        recheck: Callable[
            [MethodologyRunClaimSnapshot, str],
            ContextPack,
        ],
    ) -> MethodologyRunClaimReceipt:
        """Atomically materialize and claim one formal first Run."""

        if request.task_id != task_id:
            raise OrchestrationValidationError(
                "Methodology Run claim Task argument differs from request"
            )
        now = utc_now()
        with self._transaction() as db:
            existing = db.execute(
                """
                SELECT c.*, r.context_pack_id AS protocol_context_pack_id,
                       r.context_sha256 AS protocol_context_sha256
                FROM orchestration_methodology_run_claims c
                LEFT JOIN protocol_runs r ON r.run_id = c.run_id
                WHERE c.task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if existing is not None:
                receipt = MethodologyRunClaimReceipt.model_validate_json(
                    existing["receipt_payload"]
                )
                persisted_request = MethodologyRunClaimRequest.model_validate_json(
                    existing["request_payload"]
                )
                if (
                    request.request_id != existing["request_id"]
                    or request.content_sha256 != existing["request_sha256"]
                    or persisted_request != request
                    or persisted_request.content_sha256
                    != existing["request_sha256"]
                    or receipt.content_sha256 != existing["receipt_sha256"]
                    or receipt.receipt_id != existing["receipt_id"]
                    or receipt.request_id != existing["request_id"]
                    or receipt.request_sha256 != existing["request_sha256"]
                    or receipt.task_id != task_id
                    or receipt.execution_contract_id
                    != existing["execution_contract_id"]
                    or receipt.run_id != existing["run_id"]
                    or receipt.context_pack_id
                    != existing["protocol_context_pack_id"]
                    or receipt.context_pack_sha256
                    != existing["protocol_context_sha256"]
                    or receipt.authenticated_principal_id
                    != existing["authenticated_principal_id"]
                ):
                    raise OrchestrationConflictError(
                        "Methodology Run claim replay binding differs"
                    )
                if (
                    "control_plane.approve" not in principal.permissions
                    or receipt.project_id not in principal.projects
                    or principal.principal_id
                    != existing["authenticated_principal_id"]
                ):
                    raise OrchestrationValidationError(
                        "Methodology Run claim replay requires the original "
                        "currently authorized principal"
                    )
                return receipt

            contract_row = db.execute(
                """
                SELECT * FROM orchestration_methodology_execution_contracts
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if contract_row is None:
                raise OrchestrationConflictError(
                    "Methodology successor execution contract is unavailable"
                )
            contract = MethodologyExecutionContract.model_validate_json(
                contract_row["contract_payload"]
            )
            if (
                contract.contract_id != contract_row["contract_id"]
                or contract.content_sha256 != contract_row["contract_sha256"]
                or contract.task_id != task_id
                or request.execution_contract_id != contract.contract_id
                or request.execution_contract_sha256
                != contract.content_sha256
            ):
                raise OrchestrationValidationError(
                    "Methodology execution contract binding drifted"
                )
            activation_row = db.execute(
                """
                SELECT * FROM orchestration_methodology_route_activations
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if activation_row is None:
                raise OrchestrationConflictError(
                    "Methodology successor first route is not activated"
                )
            activation_request = (
                MethodologyRouteActivationRequest.model_validate_json(
                    activation_row["request_payload"]
                )
            )
            activation_receipt = (
                MethodologyRouteActivationReceipt.model_validate_json(
                    activation_row["receipt_payload"]
                )
            )
            if (
                activation_request.request_id != activation_row["request_id"]
                or activation_request.content_sha256
                != activation_row["request_sha256"]
                or activation_receipt.receipt_id
                != activation_row["receipt_id"]
                or activation_receipt.content_sha256
                != activation_row["receipt_sha256"]
                or activation_receipt.request_id
                != activation_request.request_id
                or activation_receipt.request_sha256
                != activation_request.content_sha256
                or request.route_activation_receipt_id
                != activation_receipt.receipt_id
                or request.route_activation_receipt_sha256
                != activation_receipt.content_sha256
            ):
                raise OrchestrationValidationError(
                    "Methodology route activation binding drifted"
                )
            migration = db.execute(
                """
                SELECT * FROM orchestration_methodology_migrations
                WHERE successor_task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if migration is None:
                raise OrchestrationConflictError(
                    "Task is not a migrated methodology successor"
                )
            migration_request = (
                MethodologyMigrationPreviewRequest.model_validate_json(
                    migration["request_payload"]
                )
            )
            migration_gate = (
                AuthenticatedMethodologyMigrationGate.model_validate_json(
                    migration["gate_payload"]
                )
            )
            migration_receipt = (
                MethodologyMigrationActivationReceipt.model_validate_json(
                    migration["receipt_payload"]
                )
            )
            if (
                migration_request.content_sha256
                != migration["request_sha256"]
                or migration_gate.content_sha256 != migration["gate_sha256"]
                or migration_receipt.content_sha256
                != migration["receipt_sha256"]
                or contract.migration_request_sha256
                != migration_request.content_sha256
                or contract.migration_gate_sha256
                != migration_gate.content_sha256
                or contract.migration_receipt_sha256
                != migration_receipt.content_sha256
            ):
                raise OrchestrationValidationError(
                    "Methodology Run claim migration provenance drifted"
                )
            if "control_plane.approve" not in principal.permissions:
                raise OrchestrationValidationError(
                    "Authenticated principal lacks control_plane.approve permission"
                )
            if contract.project_id not in principal.projects:
                raise OrchestrationValidationError(
                    "Authenticated principal is not authorized for the successor project"
                )
            if (
                principal.principal_id
                != contract_row["authenticated_principal_id"]
                or principal.principal_id
                != activation_row["authenticated_principal_id"]
                or principal.principal_id
                != migration["authenticated_principal_id"]
            ):
                raise OrchestrationValidationError(
                    "Run claim principal does not match the migration Gate"
                )

            task_row = db.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if task_row is None:
                raise OrchestrationNotFoundError(task_id)
            task = self.tasks._manifest(task_row)
            plan_row = db.execute(
                "SELECT * FROM orchestration_plans WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if plan_row is None:
                raise OrchestrationValidationError(
                    "Methodology successor Plan is unavailable"
                )
            plan = self._plan(plan_row)
            plan_stages = tuple(
                self._stage(row)
                for row in db.execute(
                    """
                    SELECT * FROM orchestration_stages
                    WHERE plan_id = ? ORDER BY sequence
                    """,
                    (plan.plan_id,),
                ).fetchall()
            )
            control_task_row = db.execute(
                "SELECT * FROM control_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if control_task_row is None:
                raise OrchestrationValidationError(
                    "Methodology successor Control Task is unavailable"
                )
            control_task = control_plane._task_record(control_task_row)
            inventory_row = db.execute(
                """
                SELECT * FROM control_stage_inventories
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if inventory_row is None:
                raise OrchestrationValidationError(
                    "Methodology successor Stage inventory is unavailable"
                )
            inventory = StageInventory.model_validate_json(
                inventory_row["payload"]
            )
            if (
                inventory.content_sha256 != inventory_row["content_sha256"]
                or inventory.inventory_id != inventory_row["inventory_id"]
                or inventory.task_id != task_id
            ):
                raise OrchestrationValidationError(
                    "Methodology successor inventory binding drifted"
                )
            route = control_plane.stage_route_snapshot(db, task_id)
            if route is None:
                raise OrchestrationConflictError(
                    "Methodology successor first route is unavailable"
                )
            first_stage = contract.stages[0]
            formal_stage_row = db.execute(
                """
                SELECT * FROM control_stages
                WHERE task_id = ? AND stage_key = ?
                """,
                (task_id, first_stage.stage_key),
            ).fetchone()
            formal_gate_row = db.execute(
                """
                SELECT * FROM control_gates
                WHERE task_id = ? AND gate_key = ?
                """,
                (task_id, first_stage.gate_key),
            ).fetchone()
            if formal_stage_row is None or formal_gate_row is None:
                raise OrchestrationConflictError(
                    "Methodology first Stage or Gate is unavailable"
                )
            formal_stage = control_plane._stage(formal_stage_row)
            formal_gate = control_plane._gate_record(db, formal_gate_row)

            seed_rows = db.execute(
                """
                SELECT * FROM orchestration_methodology_seed_artifact_refs
                WHERE task_id = ? AND consumer_stage_key = ?
                ORDER BY source_artifact_id
                """,
                (task_id, first_stage.stage_key),
            ).fetchall()
            seed_by_source: dict[str, MethodologySeedArtifactRegistration] = {}
            for row in seed_rows:
                seed = MethodologySeedArtifactRegistration.model_validate_json(
                    row["payload"]
                )
                location = seed.artifact.location
                if (
                    location is None
                    or seed.consumer_stage_key != row["consumer_stage_key"]
                    or seed.source_artifact_id != row["source_artifact_id"]
                    or seed.artifact.artifact_id != row["artifact_id"]
                    or seed.artifact.version != row["artifact_version"]
                    or seed.artifact.sha256 != row["artifact_sha256"]
                    or seed.artifact.kind != row["artifact_kind"]
                    or location.repository_id != row["repository_id"]
                    or location.ref != row["ref"]
                    or location.commit_sha != row["commit_sha"]
                    or location.path != row["path"]
                    or canonical_sha256(seed) != row["payload_sha256"]
                    or row["execution_contract_id"] != contract.contract_id
                    or row["activation_request_id"]
                    != activation_request.request_id
                ):
                    raise OrchestrationValidationError(
                        "Registered methodology seed Artifact binding drifted"
                    )
                seed_by_source[seed.source_artifact_id] = seed
            if len(seed_by_source) != len(seed_rows):
                raise OrchestrationValidationError(
                    "Registered methodology seed Artifact identities drifted"
                )
            try:
                seed_artifacts = tuple(
                    seed_by_source[item.source_artifact_id]
                    for item in activation_receipt.seed_artifacts
                )
            except KeyError as exc:
                raise OrchestrationValidationError(
                    "Registered methodology seed Artifact set is incomplete"
                ) from exc
            if (
                len(seed_artifacts) != len(seed_rows)
                or seed_artifacts != tuple(activation_receipt.seed_artifacts)
            ):
                raise OrchestrationValidationError(
                    "Registered methodology seed Artifact set drifted"
                )

            for table in (
                "orchestration_runs",
                "orchestration_consultations",
                "protocol_runs",
                "protocol_artifacts",
                "protocol_evidence",
            ):
                if db.execute(
                    f"SELECT 1 FROM {table} WHERE task_id = ? LIMIT 1",
                    (task_id,),
                ).fetchone() is not None:
                    raise OrchestrationConflictError(
                        "Methodology successor first Run is already claimed "
                        "or execution state already exists"
                    )

            snapshot = MethodologyRunClaimSnapshot(
                task=task,
                control_task=control_task,
                plan=plan,
                plan_stages=plan_stages,
                inventory=inventory,
                migration_request=migration_request,
                migration_gate=migration_gate,
                migration_receipt=migration_receipt,
                execution_contract=contract,
                route_activation_request=activation_request,
                route_activation_receipt=activation_receipt,
                route=route,
                formal_stage=formal_stage,
                formal_gate=formal_gate,
                seed_artifacts=seed_artifacts,
            )
            try:
                context_pack = recheck(snapshot, now)
            except ValueError as exc:
                raise OrchestrationConflictError(
                    f"Methodology Run claim blocked: {exc}"
                ) from exc
            if (
                context_pack.project_id != task.project_id
                or context_pack.task_id != task_id
                or context_pack.stage_key != first_stage.stage_key
                or context_pack.run_id != request.run_id
                or context_pack.pack_id != request.context_pack_id
                or context_pack.budget != first_stage.context.budget
                or context_pack.input_artifacts
                != [item.artifact for item in seed_artifacts]
            ):
                raise OrchestrationValidationError(
                    "Methodology Context Pack differs from the first Stage claim"
                )
            if context_pack.budget.max_model_tokens is None:
                raise OrchestrationValidationError(
                    "Methodology Run claim has no Token reservation"
                )

            protocol_run = control_plane._start_protocol_run_tx(
                db,
                context_pack=context_pack,
                gate_key=first_stage.gate_key,
                actor=principal.principal_id,
                event_key_prefix=f"methodology.run.claim:{request.request_id}",
                now=now,
                registered_external_inputs=[
                    item.artifact for item in seed_artifacts
                ],
            )
            stage_after_row = db.execute(
                """
                SELECT * FROM control_stages
                WHERE task_id = ? AND stage_key = ?
                """,
                (task_id, first_stage.stage_key),
            ).fetchone()
            control_task_after_row = db.execute(
                "SELECT * FROM control_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            assert stage_after_row is not None
            assert control_task_after_row is not None
            formal_stage_after = control_plane._stage(stage_after_row)
            control_task_after = control_plane._task_record(
                control_task_after_row
            )

            metadata = dict(task.metadata)
            metadata.update(
                {
                    "methodology_run_claimed": True,
                    "methodology_run_claim_request_id": request.request_id,
                    "methodology_run_claim_request_sha256": (
                        request.content_sha256
                    ),
                    "methodology_run_id": context_pack.run_id,
                    "methodology_context_pack_id": context_pack.pack_id,
                    "methodology_context_pack_sha256": (
                        context_pack.content_sha256
                    ),
                    "methodology_run_claimed_by": principal.principal_id,
                    "methodology_run_claimed_at": now,
                    "methodology_dispatch_authority": False,
                }
            )
            task_version_after = task.version + 1
            updated = db.execute(
                """
                UPDATE tasks
                SET metadata = ?, version = ?, updated_at = ?
                WHERE task_id = ? AND version = ?
                """,
                (
                    self._json(metadata),
                    task_version_after,
                    now,
                    task_id,
                    task.version,
                ),
            )
            if updated.rowcount != 1:
                raise OrchestrationConflictError(
                    "Methodology successor Task changed during Run claim"
                )

            receipt_payload = {
                "schema_version": "1.0",
                "receipt_id": f"run-claim-{request.content_sha256[:20]}",
                "claimed_at": now,
                "request_id": request.request_id,
                "request_sha256": request.content_sha256,
                "authenticated_principal_id": principal.principal_id,
                "authenticated_permission": "control_plane.approve",
                "project_id": task.project_id,
                "task_id": task_id,
                "task_version_before": task.version,
                "task_version_after": task_version_after,
                "control_task_status_before": control_task.status.value,
                "control_task_version_before": control_task.version,
                "control_task_status_after": control_task_after.status.value,
                "control_task_version_after": control_task_after.version,
                "plan_id": plan.plan_id,
                "plan_version": plan.version,
                "inventory_id": inventory.inventory_id,
                "inventory_sha256": inventory.content_sha256,
                "execution_contract_id": contract.contract_id,
                "execution_contract_sha256": contract.content_sha256,
                "route_activation_receipt_id": activation_receipt.receipt_id,
                "route_activation_receipt_sha256": (
                    activation_receipt.content_sha256
                ),
                "repository": request.repository.model_dump(mode="json"),
                "first_stage_key": first_stage.stage_key,
                "first_stage_version_before": formal_stage.version,
                "first_stage_status_before": formal_stage.status.value,
                "first_stage_version_after": formal_stage_after.version,
                "first_stage_status_after": formal_stage_after.status.value,
                "first_gate_key": first_stage.gate_key,
                "first_gate_version": formal_gate.version,
                "first_gate_status": formal_gate.status.value,
                "runtime": first_stage.runtime,
                "run_id": context_pack.run_id,
                "context_pack_id": context_pack.pack_id,
                "context_pack_sha256": context_pack.content_sha256,
                "seed_artifacts": [
                    item.model_dump(mode="json") for item in seed_artifacts
                ],
                "required_outputs": [
                    item.model_dump(mode="json")
                    for item in context_pack.required_outputs
                ],
                "budget": context_pack.budget.model_dump(mode="json"),
                "request_authenticated": True,
                "context_pack_materialized": True,
                "formal_run_created": True,
                "usage_reservation_recorded": True,
                "compatibility_run_created": False,
                "protocol_artifacts_created": False,
                "runtime_preflight_created": False,
                "process_started": False,
                "runtime_spawned": False,
                "process_spawn_authority": False,
                "provider_substitution": False,
            }
            receipt = MethodologyRunClaimReceipt.model_validate(
                seal_model_payload(
                    MethodologyRunClaimReceipt,
                    receipt_payload,
                )
            )
            if (
                protocol_run.run_id != receipt.run_id
                or protocol_run.context_pack.pack_id
                != receipt.context_pack_id
                or protocol_run.context_pack.content_sha256
                != receipt.context_pack_sha256
                or formal_stage_after.status != StageStatus.RUNNING
                or formal_gate.status != GateStatus.PENDING
            ):
                raise OrchestrationValidationError(
                    "Methodology Run claim receipt differs from Control Plane state"
                )

            db.execute(
                """
                INSERT INTO orchestration_methodology_run_claims (
                    request_id, request_sha256, request_payload,
                    receipt_id, receipt_sha256, receipt_payload,
                    task_id, plan_id, execution_contract_id,
                    route_activation_request_id, route_activation_receipt_id,
                    run_id, stage_key, runtime,
                    context_pack_id, context_pack_sha256,
                    token_reserved, cost_reserved_usd,
                    authenticated_principal_id, process_started, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?
                )
                """,
                (
                    request.request_id,
                    request.content_sha256,
                    self._json(request.model_dump(mode="json")),
                    receipt.receipt_id,
                    receipt.content_sha256,
                    self._json(receipt.model_dump(mode="json")),
                    task_id,
                    plan.plan_id,
                    contract.contract_id,
                    activation_request.request_id,
                    activation_receipt.receipt_id,
                    context_pack.run_id,
                    first_stage.stage_key,
                    first_stage.runtime,
                    context_pack.pack_id,
                    context_pack.content_sha256,
                    context_pack.budget.max_model_tokens,
                    context_pack.budget.max_cost_usd,
                    principal.principal_id,
                    now,
                ),
            )
            self.tasks._insert_event(
                db,
                task_id=task_id,
                event_type="methodology_run_claimed",
                actor=principal.principal_id,
                payload={
                    "request_id": request.request_id,
                    "request_sha256": request.content_sha256,
                    "receipt_id": receipt.receipt_id,
                    "receipt_sha256": receipt.content_sha256,
                    "execution_contract_id": contract.contract_id,
                    "execution_contract_sha256": contract.content_sha256,
                    "run_id": context_pack.run_id,
                    "context_pack_id": context_pack.pack_id,
                    "context_pack_sha256": context_pack.content_sha256,
                    "first_stage_key": first_stage.stage_key,
                    "version": task_version_after,
                    "process_started": False,
                },
                created_at=now,
            )
            control_plane._event(
                db,
                event_key=f"methodology.run.claimed:{request.request_id}",
                task_id=task_id,
                project_id=task.project_id,
                event_type="methodology.run_claimed",
                actor=principal.principal_id,
                payload={
                    "request_id": request.request_id,
                    "request_sha256": request.content_sha256,
                    "receipt_id": receipt.receipt_id,
                    "receipt_sha256": receipt.content_sha256,
                    "run_id": context_pack.run_id,
                    "context_pack_id": context_pack.pack_id,
                    "context_pack_sha256": context_pack.content_sha256,
                    "first_stage_key": first_stage.stage_key,
                    "runtime": first_stage.runtime,
                    "token_reserved": context_pack.budget.max_model_tokens,
                    "cost_reserved_usd": context_pack.budget.max_cost_usd,
                    "process_started": False,
                    "process_spawn_authority": False,
                },
                now=now,
            )
            return receipt

    def methodology_run_dispatch_snapshot(
        self,
        task_id: str,
        *,
        control_plane: ControlPlaneStore,
    ) -> MethodologyRunDispatchSnapshot:
        """Read all bindings for the one already claimed methodology Run."""

        with closing(self._connect()) as db:
            db.execute("BEGIN")
            try:
                return self._methodology_run_dispatch_snapshot_tx(
                    db,
                    task_id,
                    control_plane=control_plane,
                )
            finally:
                db.rollback()

    def _methodology_run_dispatch_snapshot_tx(
        self,
        db: sqlite3.Connection,
        task_id: str,
        *,
        control_plane: ControlPlaneStore,
    ) -> MethodologyRunDispatchSnapshot:
        task_row = db.execute(
            "SELECT * FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        control_task_row = db.execute(
            "SELECT * FROM control_tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        plan_row = db.execute(
            "SELECT * FROM orchestration_plans WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        contract_row = db.execute(
            """
            SELECT * FROM orchestration_methodology_execution_contracts
            WHERE task_id = ?
            """,
            (task_id,),
        ).fetchone()
        activation_row = db.execute(
            """
            SELECT * FROM orchestration_methodology_route_activations
            WHERE task_id = ?
            """,
            (task_id,),
        ).fetchone()
        claim_row = db.execute(
            """
            SELECT * FROM orchestration_methodology_run_claims
            WHERE task_id = ?
            """,
            (task_id,),
        ).fetchone()
        if (
            task_row is None
            or control_task_row is None
            or plan_row is None
            or contract_row is None
            or activation_row is None
            or claim_row is None
        ):
            raise OrchestrationConflictError(
                "Methodology Run dispatch requires the complete claimed ledger"
            )
        task = self.tasks._manifest(task_row)
        control_task = control_plane._task_record(control_task_row)
        plan = self._plan(plan_row)
        inventory = control_plane.stage_inventory_snapshot(db, task_id)
        if inventory is None:
            raise OrchestrationConflictError(
                "Methodology Run dispatch Stage inventory is unavailable"
            )
        contract = MethodologyExecutionContract.model_validate_json(
            contract_row["contract_payload"]
        )
        activation = MethodologyRouteActivationReceipt.model_validate_json(
            activation_row["receipt_payload"]
        )
        claim = MethodologyRunClaimReceipt.model_validate_json(
            claim_row["receipt_payload"]
        )
        protocol_row = db.execute(
            "SELECT * FROM protocol_runs WHERE run_id = ?",
            (claim.run_id,),
        ).fetchone()
        stage_row = db.execute(
            """
            SELECT * FROM control_stages
            WHERE task_id = ? AND stage_key = ?
            """,
            (task_id, claim.first_stage_key),
        ).fetchone()
        gate_row = db.execute(
            """
            SELECT * FROM control_gates
            WHERE task_id = ? AND gate_key = ?
            """,
            (task_id, claim.first_gate_key),
        ).fetchone()
        if protocol_row is None or stage_row is None or gate_row is None:
            raise OrchestrationConflictError(
                "Methodology Run dispatch formal Run, Stage, or Gate is unavailable"
            )
        if (
            contract.contract_id != contract_row["contract_id"]
            or contract.content_sha256 != contract_row["contract_sha256"]
            or activation.receipt_id != activation_row["receipt_id"]
            or activation.content_sha256 != activation_row["receipt_sha256"]
            or claim.receipt_id != claim_row["receipt_id"]
            or claim.content_sha256 != claim_row["receipt_sha256"]
            or claim.run_id != claim_row["run_id"]
            or claim.context_pack_id != claim_row["context_pack_id"]
            or claim.context_pack_sha256 != claim_row["context_pack_sha256"]
            or bool(claim_row["process_started"])
        ):
            raise OrchestrationValidationError(
                "Methodology Run dispatch persisted provenance drifted"
            )
        route = control_plane._stage_route_decision_tx(
            db,
            task_id,
            required=True,
        )
        if route is None:
            raise OrchestrationConflictError(
                "Methodology Run dispatch authoritative route is unavailable"
            )
        return MethodologyRunDispatchSnapshot(
            task=task,
            control_task=control_task,
            plan=plan,
            inventory=inventory,
            execution_contract=contract,
            route_activation_receipt=activation,
            run_claim_receipt=claim,
            protocol_run=control_plane._protocol_run(protocol_row),
            route=route,
            formal_stage=control_plane._stage(stage_row),
            formal_gate=control_plane._gate_record(db, gate_row),
        )

    def get_methodology_run_dispatch(
        self,
        task_id: str,
    ) -> MethodologyRunDispatchState | None:
        with closing(self._connect()) as db:
            row = db.execute(
                """
                SELECT * FROM orchestration_methodology_run_dispatches
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
        return self._methodology_dispatch(row) if row is not None else None

    def claim_methodology_run_dispatch(
        self,
        task_id: str,
        *,
        control_plane: ControlPlaneStore,
        recheck: Callable[
            [MethodologyRunDispatchSnapshot, str],
            MethodologyRunDispatchClaim,
        ],
    ) -> MethodologyRunDispatchState:
        """Atomically consume the sole spawn attachment for an existing Run."""

        now = utc_now()
        with self._transaction() as db:
            existing = db.execute(
                """
                SELECT * FROM orchestration_methodology_run_dispatches
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if existing is not None:
                raise OrchestrationConflictError(
                    "Methodology Run already has a process dispatch attachment"
                )
            snapshot = self._methodology_run_dispatch_snapshot_tx(
                db,
                task_id,
                control_plane=control_plane,
            )
            try:
                claim = recheck(snapshot, now)
            except (
                ControlPlaneConflictError,
                OrchestrationConflictError,
                OrchestrationValidationError,
                ValueError,
            ) as exc:
                if isinstance(
                    exc,
                    (OrchestrationConflictError, OrchestrationValidationError),
                ):
                    raise
                raise OrchestrationConflictError(str(exc)) from exc
            if claim.task_id != task_id:
                raise OrchestrationValidationError(
                    "Methodology dispatch claim crosses its Task scope"
                )
            compatibility = db.execute(
                "SELECT 1 FROM orchestration_runs WHERE run_id = ? OR task_id = ?",
                (claim.run_id, task_id),
            ).fetchone()
            if compatibility is not None:
                raise OrchestrationConflictError(
                    "Methodology dispatch cannot create or reuse a compatibility Run"
                )
            db.execute(
                """
                INSERT INTO orchestration_methodology_run_dispatches (
                    dispatch_id, claim_sha256, claim_payload,
                    task_id, plan_id, run_id, stage_key, runtime,
                    prompt_sha256, dispatch_policy_id,
                    dispatch_policy_sha256, dispatch_policy_payload,
                    preflight_id, preflight_sha256, preflight_payload,
                    state, claimed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    claim.dispatch_id,
                    claim.content_sha256,
                    self._json(claim.model_dump(mode="json")),
                    task_id,
                    claim.plan_id,
                    claim.run_id,
                    claim.first_stage_key,
                    claim.runtime,
                    claim.prompt_sha256,
                    claim.dispatch_policy.decision_id,
                    claim.dispatch_policy.content_sha256,
                    self._json(
                        claim.dispatch_policy.model_dump(mode="json")
                    ),
                    claim.runtime_preflight.decision_id,
                    claim.runtime_preflight.content_sha256,
                    self._json(
                        claim.runtime_preflight.model_dump(mode="json")
                    ),
                    MethodologyDispatchState.CLAIMED.value,
                    now,
                ),
            )
            self.tasks._insert_event(
                db,
                task_id=task_id,
                event_type="methodology_run_dispatch_claimed",
                actor="orchestrator",
                payload={
                    "dispatch_id": claim.dispatch_id,
                    "dispatch_sha256": claim.content_sha256,
                    "run_id": claim.run_id,
                    "context_pack_id": claim.context_pack_id,
                    "runtime": claim.runtime,
                    "dispatch_policy_id": claim.dispatch_policy.decision_id,
                    "dispatch_policy_sha256": (
                        claim.dispatch_policy.content_sha256
                    ),
                    "preflight_id": claim.runtime_preflight.decision_id,
                    "preflight_sha256": (
                        claim.runtime_preflight.content_sha256
                    ),
                    "process_started": False,
                    "provider_substitution": False,
                },
                created_at=now,
            )
            control_plane._event(
                db,
                event_key=f"methodology.run.dispatch.claimed:{claim.dispatch_id}",
                task_id=task_id,
                project_id=claim.project_id,
                event_type="methodology.run_dispatch_claimed",
                actor="orchestrator",
                payload={
                    "dispatch_id": claim.dispatch_id,
                    "dispatch_sha256": claim.content_sha256,
                    "run_id": claim.run_id,
                    "stage_key": claim.first_stage_key,
                    "runtime": claim.runtime,
                    "dispatch_policy_sha256": (
                        claim.dispatch_policy.content_sha256
                    ),
                    "preflight_sha256": (
                        claim.runtime_preflight.content_sha256
                    ),
                    "existing_formal_run_reused": True,
                    "existing_context_pack_reused": True,
                    "compatibility_run_created": False,
                    "process_spawn_authority": True,
                },
                now=now,
            )
            row = db.execute(
                """
                SELECT * FROM orchestration_methodology_run_dispatches
                WHERE dispatch_id = ?
                """,
                (claim.dispatch_id,),
            ).fetchone()
        assert row is not None
        return self._methodology_dispatch(row)

    def attach_methodology_run_pid(
        self,
        dispatch_id: str,
        pid: int,
    ) -> MethodologyRunDispatchState:
        if pid < 1:
            raise OrchestrationValidationError(
                "Methodology dispatch PID must be positive"
            )
        now = utc_now()
        with self._transaction() as db:
            row = db.execute(
                """
                SELECT * FROM orchestration_methodology_run_dispatches
                WHERE dispatch_id = ?
                """,
                (dispatch_id,),
            ).fetchone()
            if row is None:
                raise OrchestrationNotFoundError(dispatch_id)
            if (
                row["state"] == MethodologyDispatchState.RUNNING.value
                and row["pid"] == pid
                and bool(row["process_started"])
            ):
                return self._methodology_dispatch(row)
            if (
                row["state"] != MethodologyDispatchState.CLAIMED.value
                or row["pid"] is not None
                or bool(row["process_started"])
            ):
                raise OrchestrationConflictError(
                    "Methodology Run dispatch is not attachable"
                )
            cursor = db.execute(
                """
                UPDATE orchestration_methodology_run_dispatches
                SET state = ?, pid = ?, process_started = 1,
                    process_attached_at = ?
                WHERE dispatch_id = ? AND state = ? AND pid IS NULL
                  AND process_started = 0
                """,
                (
                    MethodologyDispatchState.RUNNING.value,
                    pid,
                    now,
                    dispatch_id,
                    MethodologyDispatchState.CLAIMED.value,
                ),
            )
            if cursor.rowcount != 1:
                raise OrchestrationConflictError(
                    "Methodology Run dispatch changed before PID attachment"
                )
            claim = MethodologyRunDispatchClaim.model_validate_json(
                row["claim_payload"]
            )
            self.tasks._insert_event(
                db,
                task_id=row["task_id"],
                event_type="methodology_run_process_attached",
                actor="orchestrator",
                payload={
                    "dispatch_id": dispatch_id,
                    "run_id": row["run_id"],
                    "pid": pid,
                    "process_started": True,
                },
                created_at=now,
            )
            updated = db.execute(
                """
                SELECT * FROM orchestration_methodology_run_dispatches
                WHERE dispatch_id = ?
                """,
                (claim.dispatch_id,),
            ).fetchone()
        assert updated is not None
        return self._methodology_dispatch(updated)

    def observe_methodology_run_terminal(
        self,
        dispatch_id: str,
        *,
        process_started: bool,
        exit_code: int | None,
        timed_out: bool,
        output: str,
        error_message: str | None,
        repository_unchanged: bool,
        adapter_result: AgentAdapterResult,
        usage_observation: ProviderUsageObservation,
    ) -> MethodologyRunDispatchState:
        """Persist terminal runner facts before authoritative protocol settlement."""

        now = utc_now()
        safe_output = redact_text(output)[-64 * 1024:]
        safe_error = (
            redact_text(error_message)[-4_000:] if error_message else None
        )
        with self._transaction() as db:
            row = db.execute(
                """
                SELECT * FROM orchestration_methodology_run_dispatches
                WHERE dispatch_id = ?
                """,
                (dispatch_id,),
            ).fetchone()
            if row is None:
                raise OrchestrationNotFoundError(dispatch_id)
            existing = self._methodology_dispatch(row)
            if existing.state in {
                MethodologyDispatchState.TERMINAL_OBSERVED,
                MethodologyDispatchState.SETTLED,
            }:
                if (
                    existing.process_started != process_started
                    or existing.exit_code != exit_code
                    or existing.timed_out != timed_out
                    or existing.output != safe_output
                    or existing.error_message != safe_error
                    or existing.repository_unchanged != repository_unchanged
                    or existing.adapter_result != adapter_result
                    or existing.usage_observation != usage_observation
                ):
                    raise OrchestrationConflictError(
                        "Methodology terminal observation replay differs"
                    )
                return existing
            if existing.state not in {
                MethodologyDispatchState.CLAIMED,
                MethodologyDispatchState.RUNNING,
            }:
                raise OrchestrationConflictError(
                    "Methodology dispatch cannot accept terminal facts"
                )
            if process_started != existing.process_started:
                raise OrchestrationValidationError(
                    "Methodology terminal process fact differs from its attachment"
                )
            if process_started and existing.pid is None:
                raise OrchestrationValidationError(
                    "Attached methodology process must settle as started"
                )
            if (
                adapter_result.protocol_state.run_id != existing.claim.run_id
                or usage_observation.run_id != existing.claim.run_id
                or usage_observation.adapter != existing.claim.runtime
            ):
                raise OrchestrationValidationError(
                    "Methodology terminal observation crosses its Run binding"
                )
            cursor = db.execute(
                """
                UPDATE orchestration_methodology_run_dispatches
                SET state = ?, process_started = ?, exit_code = ?,
                    timed_out = ?, output = ?, error_message = ?,
                    repository_unchanged = ?, adapter_result_payload = ?,
                    usage_observation_payload = ?, terminal_observed_at = ?
                WHERE dispatch_id = ? AND state IN (?, ?)
                """,
                (
                    MethodologyDispatchState.TERMINAL_OBSERVED.value,
                    int(process_started),
                    exit_code,
                    int(timed_out),
                    safe_output,
                    safe_error,
                    int(repository_unchanged),
                    self._json(adapter_result.model_dump(mode="json")),
                    self._json(usage_observation.model_dump(mode="json")),
                    now,
                    dispatch_id,
                    MethodologyDispatchState.CLAIMED.value,
                    MethodologyDispatchState.RUNNING.value,
                ),
            )
            if cursor.rowcount != 1:
                raise OrchestrationConflictError(
                    "Methodology dispatch changed before terminal observation"
                )
            self.tasks._insert_event(
                db,
                task_id=row["task_id"],
                event_type="methodology_run_terminal_observed",
                actor="orchestrator",
                payload={
                    "dispatch_id": dispatch_id,
                    "run_id": row["run_id"],
                    "process_started": process_started,
                    "exit_code": exit_code,
                    "timed_out": timed_out,
                    "repository_unchanged": repository_unchanged,
                    "protocol_state": adapter_result.protocol_state.model_dump(
                        mode="json"
                    ),
                    "usage_observation_sha256": (
                        usage_observation.content_sha256
                    ),
                },
                created_at=now,
            )
            updated = db.execute(
                """
                SELECT * FROM orchestration_methodology_run_dispatches
                WHERE dispatch_id = ?
                """,
                (dispatch_id,),
            ).fetchone()
        assert updated is not None
        return self._methodology_dispatch(updated)

    def finish_methodology_run_dispatch(
        self,
        dispatch_id: str,
        *,
        settlement: RunSettlementReceipt,
        control_plane: ControlPlaneStore,
    ) -> MethodologyRunDispatchReceipt:
        """Finalize the process attachment and its separate usage ledger exactly once."""

        now = utc_now()
        with self._transaction() as db:
            row = db.execute(
                """
                SELECT * FROM orchestration_methodology_run_dispatches
                WHERE dispatch_id = ?
                """,
                (dispatch_id,),
            ).fetchone()
            if row is None:
                raise OrchestrationNotFoundError(dispatch_id)
            state = self._methodology_dispatch(row)
            if state.state == MethodologyDispatchState.SETTLED:
                assert state.receipt is not None
                return state.receipt
            if (
                state.state != MethodologyDispatchState.TERMINAL_OBSERVED
                or state.adapter_result is None
                or state.usage_observation is None
                or state.repository_unchanged is None
            ):
                raise OrchestrationConflictError(
                    "Methodology dispatch has no complete terminal observation"
                )
            protocol_row = db.execute(
                "SELECT * FROM protocol_runs WHERE run_id = ?",
                (state.claim.run_id,),
            ).fetchone()
            if protocol_row is None:
                raise OrchestrationConflictError(
                    "Settled methodology protocol Run is unavailable"
                )
            protocol_run = control_plane._protocol_run(protocol_row)
            normalized = settlement.model_copy(update={"replayed": False})
            if (
                normalized.run != protocol_run
                or normalized.run.protocol_state
                != state.adapter_result.protocol_state
                or normalized.run.run_id != state.claim.run_id
                or normalized.stage.task_id != state.claim.task_id
                or normalized.stage.stage_key != state.claim.first_stage_key
                or normalized.gate.task_id != state.claim.task_id
                or normalized.gate.gate_key != state.claim.first_gate_key
            ):
                raise OrchestrationValidationError(
                    "Methodology dispatch settlement differs from terminal facts"
                )
            receipt_payload = {
                "schema_version": "1.0",
                "receipt_id": f"methodology-dispatch-receipt:{state.dispatch_id}",
                "settled_at": now,
                "dispatch_claim": state.claim,
                "pid": state.pid,
                "process_started": state.process_started,
                "exit_code": state.exit_code,
                "timed_out": state.timed_out,
                "output_sha256": hashlib.sha256(
                    state.output.encode("utf-8")
                ).hexdigest(),
                "error_sha256": hashlib.sha256(
                    (state.error_message or "").encode("utf-8")
                ).hexdigest(),
                "repository_unchanged": state.repository_unchanged,
                "usage_observation": state.usage_observation,
                "protocol_state": state.adapter_result.protocol_state,
                "handoff_pack_id": (
                    protocol_run.handoff_pack.pack_id
                    if protocol_run.handoff_pack is not None
                    else None
                ),
                "handoff_pack_sha256": (
                    protocol_run.handoff_pack.content_sha256
                    if protocol_run.handoff_pack is not None
                    else None
                ),
                "stage_status": normalized.stage.status,
                "gate_status": normalized.gate.status,
                "artifact_ids": sorted(normalized.artifact_ids),
                "evidence_ids": sorted(normalized.evidence_ids),
                "active_evidence_ids": sorted(normalized.active_evidence_ids),
                "next_stage_key": (
                    normalized.next_stage_route.stage_key
                    if normalized.next_stage_route is not None
                    else None
                ),
                "existing_formal_run_reused": True,
                "existing_context_pack_reused": True,
                "compatibility_run_created": False,
                "protocol_settled": True,
                "process_spawn_authority_consumed": True,
                "provider_substitution": False,
            }
            receipt = MethodologyRunDispatchReceipt.model_validate(
                seal_model_payload(
                    MethodologyRunDispatchReceipt,
                    receipt_payload,
                )
            )
            usage = state.usage_observation
            db.execute(
                """
                INSERT INTO orchestration_methodology_usage_ledger (
                    entry_id, dispatch_id, task_id, plan_id, stage_key,
                    run_id, tokens, token_measurement, cost_usd,
                    cost_measurement, adapter, usage_observation_sha256,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._id("methodology-usage"),
                    dispatch_id,
                    state.claim.task_id,
                    state.claim.plan_id,
                    state.claim.first_stage_key,
                    state.claim.run_id,
                    usage.total_tokens,
                    usage.token_measurement,
                    usage.cost_usd,
                    usage.cost_measurement,
                    state.claim.runtime,
                    usage.content_sha256,
                    now,
                ),
            )
            cursor = db.execute(
                """
                UPDATE orchestration_methodology_run_dispatches
                SET state = ?, receipt_sha256 = ?, receipt_payload = ?,
                    settled_at = ?
                WHERE dispatch_id = ? AND state = ?
                """,
                (
                    MethodologyDispatchState.SETTLED.value,
                    receipt.content_sha256,
                    self._json(receipt.model_dump(mode="json")),
                    now,
                    dispatch_id,
                    MethodologyDispatchState.TERMINAL_OBSERVED.value,
                ),
            )
            if cursor.rowcount != 1:
                raise OrchestrationConflictError(
                    "Methodology dispatch changed before finalization"
                )
            self.tasks._insert_event(
                db,
                task_id=state.claim.task_id,
                event_type="methodology_run_dispatch_settled",
                actor="orchestrator",
                payload={
                    "dispatch_id": dispatch_id,
                    "receipt_id": receipt.receipt_id,
                    "receipt_sha256": receipt.content_sha256,
                    "run_id": state.claim.run_id,
                    "process_started": state.process_started,
                    "protocol_state": receipt.protocol_state.model_dump(
                        mode="json"
                    ),
                    "gate_status": receipt.gate_status.value,
                    "stage_status": receipt.stage_status.value,
                    "usage_observation_sha256": usage.content_sha256,
                },
                created_at=now,
            )
            control_plane._event(
                db,
                event_key=(
                    f"methodology.run.dispatch.settled:{state.claim.dispatch_id}"
                ),
                task_id=state.claim.task_id,
                project_id=state.claim.project_id,
                event_type="methodology.run_dispatch_settled",
                actor="orchestrator",
                payload={
                    "dispatch_id": dispatch_id,
                    "receipt_id": receipt.receipt_id,
                    "receipt_sha256": receipt.content_sha256,
                    "run_id": state.claim.run_id,
                    "protocol_settled": True,
                    "provider_substitution": False,
                },
                now=now,
            )
        return receipt

    def get_methodology_stage_gate(
        self,
        task_id: str,
        stage_key: str,
    ) -> MethodologyStageGateReceipt | None:
        """Return one immutable next-Stage Gate configuration receipt."""

        with closing(self._connect()) as db:
            row = db.execute(
                """
                SELECT * FROM orchestration_methodology_stage_gates
                WHERE task_id = ? AND stage_key = ?
                """,
                (task_id, stage_key),
            ).fetchone()
        if row is None:
            return None
        request = MethodologyStageGateRequest.model_validate_json(
            row["request_payload"]
        )
        receipt = MethodologyStageGateReceipt.model_validate_json(
            row["receipt_payload"]
        )
        if (
            request.request_id != row["request_id"]
            or request.content_sha256 != row["request_sha256"]
            or receipt.receipt_id != row["receipt_id"]
            or receipt.content_sha256 != row["receipt_sha256"]
            or receipt.request_id != row["request_id"]
            or receipt.request_sha256 != row["request_sha256"]
            or receipt.task_id != row["task_id"]
            or receipt.plan_id != row["plan_id"]
            or receipt.execution_contract_id
            != row["execution_contract_id"]
            or receipt.execution_contract_sha256
            != request.execution_contract_sha256
            or receipt.predecessor_dispatch_receipt_id
            != request.predecessor_dispatch_receipt_id
            or receipt.predecessor_dispatch_receipt_sha256
            != request.predecessor_dispatch_receipt_sha256
            or receipt.predecessor_run_id != request.predecessor_run_id
            or receipt.predecessor_stage_key
            != request.predecessor_stage_key
            or receipt.predecessor_gate_key != request.predecessor_gate_key
            or receipt.predecessor_handoff_pack_id
            != request.predecessor_handoff_pack_id
            or receipt.predecessor_handoff_pack_sha256
            != request.predecessor_handoff_pack_sha256
            or receipt.predecessor_run_id != row["predecessor_run_id"]
            or receipt.stage_sequence != row["stage_sequence"]
            or receipt.stage_sequence != request.stage_sequence
            or receipt.stage_key != row["stage_key"]
            or receipt.stage_key != request.stage_key
            or receipt.gate_key != row["gate_key"]
            or receipt.gate_key != request.gate_key
            or receipt.runtime != request.runtime
            or receipt.authenticated_principal_id
            != row["authenticated_principal_id"]
        ):
            raise OrchestrationValidationError(
                "Persisted methodology Stage Gate binding drifted"
            )
        return receipt

    def configure_methodology_next_stage_gate(
        self,
        task_id: str,
        request: MethodologyStageGateRequest,
        *,
        principal: ControlPrincipal,
        control_plane: ControlPlaneStore,
        recheck: Callable[
            [MethodologyStageGateSnapshot, str],
            tuple[GateRequirement, ...],
        ],
    ) -> MethodologyStageGateReceipt:
        """Atomically configure only the exact current next methodology Gate."""

        if request.task_id != task_id:
            raise OrchestrationValidationError(
                "Methodology Stage Gate Task argument differs from request"
            )
        now = utc_now()
        with self._transaction() as db:
            identity_row = db.execute(
                """
                SELECT * FROM orchestration_methodology_stage_gates
                WHERE request_id = ?
                """,
                (request.request_id,),
            ).fetchone()
            if identity_row is not None and (
                identity_row["task_id"] != task_id
                or identity_row["stage_key"] != request.stage_key
            ):
                raise OrchestrationConflictError(
                    "Methodology Stage Gate request identity is already bound "
                    "to another Task or Stage"
                )
            existing = db.execute(
                """
                SELECT * FROM orchestration_methodology_stage_gates
                WHERE task_id = ? AND stage_key = ?
                """,
                (task_id, request.stage_key),
            ).fetchone()
            if existing is not None:
                persisted_request = (
                    MethodologyStageGateRequest.model_validate_json(
                        existing["request_payload"]
                    )
                )
                receipt = MethodologyStageGateReceipt.model_validate_json(
                    existing["receipt_payload"]
                )
                if (
                    request.request_id != existing["request_id"]
                    or request.content_sha256 != existing["request_sha256"]
                    or persisted_request != request
                    or receipt.receipt_id != existing["receipt_id"]
                    or receipt.content_sha256 != existing["receipt_sha256"]
                    or receipt.request_id != existing["request_id"]
                    or receipt.request_sha256 != existing["request_sha256"]
                    or receipt.task_id != task_id
                    or receipt.stage_key != request.stage_key
                    or receipt.gate_key != request.gate_key
                    or receipt.authenticated_principal_id
                    != existing["authenticated_principal_id"]
                ):
                    raise OrchestrationConflictError(
                        "Methodology Stage Gate replay binding differs"
                    )
                if (
                    "control_plane.approve" not in principal.permissions
                    or receipt.project_id not in principal.projects
                    or principal.principal_id
                    != existing["authenticated_principal_id"]
                ):
                    raise OrchestrationValidationError(
                        "Methodology Stage Gate replay requires the original "
                        "currently authorized principal"
                    )
                return receipt

            sequence_row = db.execute(
                """
                SELECT request_id, stage_key, gate_key
                FROM orchestration_methodology_stage_gates
                WHERE task_id = ?
                  AND (stage_sequence = ? OR gate_key = ?)
                """,
                (task_id, request.stage_sequence, request.gate_key),
            ).fetchone()
            if sequence_row is not None:
                raise OrchestrationConflictError(
                    "Methodology Stage Gate sequence or Gate is already bound "
                    "to a different request"
                )

            task_row = db.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if task_row is None:
                raise OrchestrationNotFoundError(task_id)
            task = self.tasks._manifest(task_row)
            control_task_row = db.execute(
                "SELECT * FROM control_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if control_task_row is None:
                raise OrchestrationConflictError(
                    "Methodology successor Control Task is unavailable"
                )
            control_task = control_plane._task_record(control_task_row)
            plan_row = db.execute(
                """
                SELECT * FROM orchestration_plans
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if plan_row is None:
                raise OrchestrationConflictError(
                    "Methodology successor Plan is unavailable"
                )
            plan = self._plan(plan_row)
            inventory_row = db.execute(
                """
                SELECT * FROM control_stage_inventories
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if inventory_row is None:
                raise OrchestrationConflictError(
                    "Methodology successor Stage inventory is unavailable"
                )
            inventory = StageInventory.model_validate_json(
                inventory_row["payload"]
            )
            if (
                inventory.inventory_id != inventory_row["inventory_id"]
                or inventory.content_sha256
                != inventory_row["content_sha256"]
                or inventory.task_id != task_id
            ):
                raise OrchestrationValidationError(
                    "Methodology successor Stage inventory binding drifted"
                )
            contract_row = db.execute(
                """
                SELECT * FROM orchestration_methodology_execution_contracts
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if contract_row is None:
                raise OrchestrationConflictError(
                    "Methodology successor execution contract is unavailable"
                )
            contract = MethodologyExecutionContract.model_validate_json(
                contract_row["contract_payload"]
            )
            if (
                contract.contract_id != contract_row["contract_id"]
                or contract.content_sha256 != contract_row["contract_sha256"]
                or contract.task_id != task_id
            ):
                raise OrchestrationValidationError(
                    "Methodology execution contract binding drifted"
                )
            migration_row = db.execute(
                """
                SELECT * FROM orchestration_methodology_migrations
                WHERE successor_task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if migration_row is None:
                raise OrchestrationConflictError(
                    "Task is not a migrated methodology successor"
                )
            migration_request = (
                MethodologyMigrationPreviewRequest.model_validate_json(
                    migration_row["request_payload"]
                )
            )
            migration_gate = (
                AuthenticatedMethodologyMigrationGate.model_validate_json(
                    migration_row["gate_payload"]
                )
            )
            migration_receipt = (
                MethodologyMigrationActivationReceipt.model_validate_json(
                    migration_row["receipt_payload"]
                )
            )
            if (
                migration_request.content_sha256
                != migration_row["request_sha256"]
                or migration_gate.content_sha256
                != migration_row["gate_sha256"]
                or migration_receipt.content_sha256
                != migration_row["receipt_sha256"]
                or migration_row["authenticated_principal_id"]
                != contract_row["authenticated_principal_id"]
            ):
                raise OrchestrationValidationError(
                    "Methodology Stage Gate migration provenance drifted"
                )

            dispatch_row = db.execute(
                """
                SELECT * FROM orchestration_methodology_run_dispatches
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if dispatch_row is None:
                raise OrchestrationConflictError(
                    "Settled predecessor methodology dispatch is unavailable"
                )
            dispatch_state = self._methodology_dispatch(dispatch_row)
            if (
                dispatch_state.state != MethodologyDispatchState.SETTLED
                or dispatch_state.receipt is None
            ):
                raise OrchestrationConflictError(
                    "Predecessor methodology dispatch is not settled"
                )
            dispatch_receipt = dispatch_state.receipt
            claim_row = db.execute(
                """
                SELECT * FROM orchestration_methodology_run_claims
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if claim_row is None or (
                claim_row["receipt_id"]
                != dispatch_receipt.dispatch_claim.run_claim_receipt_id
                or claim_row["receipt_sha256"]
                != dispatch_receipt.dispatch_claim.run_claim_receipt_sha256
                or claim_row["run_id"]
                != dispatch_receipt.dispatch_claim.run_id
                or claim_row["authenticated_principal_id"]
                != contract_row["authenticated_principal_id"]
            ):
                raise OrchestrationValidationError(
                    "Methodology Stage Gate Run-claim provenance drifted"
                )
            protocol_row = db.execute(
                "SELECT * FROM protocol_runs WHERE run_id = ?",
                (dispatch_receipt.dispatch_claim.run_id,),
            ).fetchone()
            predecessor_stage_row = db.execute(
                """
                SELECT * FROM control_stages
                WHERE task_id = ? AND stage_key = ?
                """,
                (
                    task_id,
                    dispatch_receipt.dispatch_claim.first_stage_key,
                ),
            ).fetchone()
            predecessor_gate_row = db.execute(
                """
                SELECT * FROM control_gates
                WHERE task_id = ? AND gate_key = ?
                """,
                (
                    task_id,
                    dispatch_receipt.dispatch_claim.first_gate_key,
                ),
            ).fetchone()
            route = control_plane._stage_route_decision_tx(
                db,
                task_id,
                required=True,
            )
            if route is None:
                raise OrchestrationConflictError(
                    "Methodology successor route is unavailable"
                )
            stage_row = db.execute(
                """
                SELECT * FROM control_stages
                WHERE task_id = ? AND stage_key = ?
                """,
                (task_id, route.stage_key),
            ).fetchone()
            gate_row = db.execute(
                """
                SELECT * FROM control_gates
                WHERE task_id = ? AND gate_key = ?
                """,
                (task_id, route.gate_key),
            ).fetchone()
            if (
                protocol_row is None
                or predecessor_stage_row is None
                or predecessor_gate_row is None
                or stage_row is None
            ):
                raise OrchestrationConflictError(
                    "Methodology Stage Gate formal predecessor or route is unavailable"
                )
            for table in (
                "orchestration_runs",
                "orchestration_consultations",
                "protocol_runs",
                "protocol_artifacts",
                "protocol_evidence",
            ):
                if db.execute(
                    f"""
                    SELECT 1 FROM {table}
                    WHERE task_id = ? AND stage_key = ? LIMIT 1
                    """,
                    (task_id, route.stage_key),
                ).fetchone() is not None:
                    raise OrchestrationConflictError(
                        "Next methodology Stage execution state already exists"
                    )

            snapshot = MethodologyStageGateSnapshot(
                task=task,
                control_task=control_task,
                plan=plan,
                inventory=inventory,
                migration_request=migration_request,
                migration_gate=migration_gate,
                migration_receipt=migration_receipt,
                execution_contract=contract,
                authenticated_principal_id=(
                    claim_row["authenticated_principal_id"]
                ),
                predecessor_dispatch_receipt=dispatch_receipt,
                predecessor_protocol_run=control_plane._protocol_run(
                    protocol_row
                ),
                predecessor_stage=control_plane._stage(
                    predecessor_stage_row
                ),
                predecessor_gate=control_plane._gate_record(
                    db,
                    predecessor_gate_row,
                ),
                route=route,
                formal_stage=control_plane._stage(stage_row),
                formal_gate=(
                    control_plane._gate_record(db, gate_row)
                    if gate_row is not None
                    else None
                ),
            )
            try:
                requirements = list(recheck(snapshot, now))
            except (
                ControlPlaneConflictError,
                OrchestrationConflictError,
                OrchestrationValidationError,
                ValueError,
            ) as exc:
                if isinstance(
                    exc,
                    (
                        OrchestrationConflictError,
                        OrchestrationValidationError,
                    ),
                ):
                    raise
                raise OrchestrationConflictError(
                    f"Methodology Stage Gate configuration blocked: {exc}"
                ) from exc

            stage_before = snapshot.formal_stage
            gate = control_plane._configure_gate_tx(
                db,
                task=control_task_row,
                gate_key=request.gate_key,
                stage_key=request.stage_key,
                requirements=requirements,
                actor=principal.principal_id,
                now=now,
            )
            route_after = control_plane._stage_route_decision_tx(
                db,
                task_id,
                required=True,
            )
            stage_after_row = db.execute(
                """
                SELECT * FROM control_stages
                WHERE task_id = ? AND stage_key = ?
                """,
                (task_id, request.stage_key),
            ).fetchone()
            task_after_row = db.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            control_after_row = db.execute(
                "SELECT * FROM control_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            plan_after_row = db.execute(
                """
                SELECT * FROM orchestration_plans WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            assert (
                route_after is not None
                and stage_after_row is not None
                and task_after_row is not None
                and control_after_row is not None
                and plan_after_row is not None
            )
            stage_after = control_plane._stage(stage_after_row)
            task_after = self.tasks._manifest(task_after_row)
            control_after = control_plane._task_record(control_after_row)
            plan_after = self._plan(plan_after_row)
            if (
                route_after.stage_key != request.stage_key
                or route_after.gate_key != request.gate_key
                or route_after.stage_status != StageStatus.READY
                or route_after.gate_status != GateStatus.PENDING
                or not route_after.runnable
                or gate.version != 1
                or gate.status != GateStatus.PENDING
                or stage_after != stage_before
                or task_after != task
                or control_after != control_task
                or plan_after != plan
            ):
                raise OrchestrationValidationError(
                    "Methodology Stage Gate mutation exceeded its authority"
                )

            receipt_payload = {
                "schema_version": "1.0",
                "receipt_id": (
                    "methodology-stage-gate:"
                    f"{request.content_sha256[:20]}"
                ),
                "configured_at": now,
                "request_id": request.request_id,
                "request_sha256": request.content_sha256,
                "authenticated_principal_id": principal.principal_id,
                "authenticated_permission": "control_plane.approve",
                "project_id": task.project_id,
                "task_id": task_id,
                "task_version_before": task.version,
                "task_version_after": task_after.version,
                "control_task_status_before": control_task.status.value,
                "control_task_version_before": control_task.version,
                "control_task_status_after": control_after.status.value,
                "control_task_version_after": control_after.version,
                "plan_id": plan.plan_id,
                "plan_version_before": plan.version,
                "plan_version_after": plan_after.version,
                "inventory_id": inventory.inventory_id,
                "inventory_sha256": inventory.content_sha256,
                "execution_contract_id": contract.contract_id,
                "execution_contract_sha256": contract.content_sha256,
                "predecessor_dispatch_receipt_id": (
                    dispatch_receipt.receipt_id
                ),
                "predecessor_dispatch_receipt_sha256": (
                    dispatch_receipt.content_sha256
                ),
                "predecessor_run_id": (
                    dispatch_receipt.dispatch_claim.run_id
                ),
                "predecessor_stage_key": (
                    dispatch_receipt.dispatch_claim.first_stage_key
                ),
                "predecessor_stage_status": (
                    snapshot.predecessor_stage.status.value
                ),
                "predecessor_gate_key": (
                    dispatch_receipt.dispatch_claim.first_gate_key
                ),
                "predecessor_gate_status": (
                    snapshot.predecessor_gate.status.value
                ),
                "predecessor_handoff_pack_id": (
                    dispatch_receipt.handoff_pack_id
                ),
                "predecessor_handoff_pack_sha256": (
                    dispatch_receipt.handoff_pack_sha256
                ),
                "repository": request.repository.model_dump(mode="json"),
                "stage_sequence": request.stage_sequence,
                "stage_key": request.stage_key,
                "stage_version_before": stage_before.version,
                "stage_status_before": stage_before.status.value,
                "stage_version_after": stage_after.version,
                "stage_status_after": stage_after.status.value,
                "gate_key": gate.gate_key,
                "gate_version": gate.version,
                "gate_status": gate.status.value,
                "runtime": request.runtime,
                "requirements": [
                    item.model_dump(mode="json")
                    for item in requirements
                ],
                "request_authenticated": True,
                "predecessor_protocol_settled": True,
                "formal_gate_configured": True,
                "route_runnable_before": True,
                "route_runnable_after": True,
                "formal_run_claimable_before": False,
                "formal_run_claimable_after": True,
                "task_mutated": False,
                "control_task_mutated": False,
                "plan_mutated": False,
                "stage_mutated": False,
                "protocol_artifacts_created": False,
                "protocol_evidence_created": False,
                "context_pack_created": False,
                "run_created": False,
                "process_started": False,
                "dispatch_authority": False,
                "provider_substitution": False,
            }
            receipt = MethodologyStageGateReceipt.model_validate(
                seal_model_payload(
                    MethodologyStageGateReceipt,
                    receipt_payload,
                )
            )
            db.execute(
                """
                INSERT INTO orchestration_methodology_stage_gates (
                    request_id, request_sha256, request_payload,
                    receipt_id, receipt_sha256, receipt_payload,
                    task_id, plan_id, execution_contract_id,
                    predecessor_dispatch_id, predecessor_run_id,
                    stage_sequence, stage_key, gate_key,
                    authenticated_principal_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.request_id,
                    request.content_sha256,
                    self._json(request.model_dump(mode="json")),
                    receipt.receipt_id,
                    receipt.content_sha256,
                    self._json(receipt.model_dump(mode="json")),
                    task_id,
                    plan.plan_id,
                    contract.contract_id,
                    dispatch_receipt.dispatch_claim.dispatch_id,
                    dispatch_receipt.dispatch_claim.run_id,
                    request.stage_sequence,
                    request.stage_key,
                    request.gate_key,
                    principal.principal_id,
                    now,
                ),
            )
            self.tasks._insert_event(
                db,
                task_id=task_id,
                event_type="methodology_stage_gate_configured",
                actor=principal.principal_id,
                payload={
                    "request_id": request.request_id,
                    "request_sha256": request.content_sha256,
                    "receipt_id": receipt.receipt_id,
                    "receipt_sha256": receipt.content_sha256,
                    "predecessor_run_id": receipt.predecessor_run_id,
                    "stage_sequence": receipt.stage_sequence,
                    "stage_key": receipt.stage_key,
                    "gate_key": receipt.gate_key,
                    "run_created": False,
                    "process_started": False,
                },
                created_at=now,
            )
            control_plane._event(
                db,
                event_key=(
                    "methodology.stage.gate.configured:"
                    f"{request.request_id}"
                ),
                task_id=task_id,
                project_id=task.project_id,
                event_type="methodology.stage_gate_configured",
                actor=principal.principal_id,
                payload={
                    "request_id": request.request_id,
                    "request_sha256": request.content_sha256,
                    "receipt_id": receipt.receipt_id,
                    "receipt_sha256": receipt.content_sha256,
                    "predecessor_dispatch_receipt_id": (
                        receipt.predecessor_dispatch_receipt_id
                    ),
                    "stage_sequence": receipt.stage_sequence,
                    "stage_key": receipt.stage_key,
                    "gate_key": receipt.gate_key,
                    "route_runnable": True,
                    "dispatch_authority": False,
                },
                now=now,
            )
            return receipt

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

    def methodology_migration_snapshot(
        self,
        task_id: str,
    ) -> MethodologyMigrationStateSnapshot:
        """Read exact migration-preview inputs through one rollback snapshot."""

        with closing(self._connect()) as db:
            db.execute("BEGIN")
            try:
                return self._methodology_migration_snapshot_tx(db, task_id)
            finally:
                db.rollback()

    def _methodology_migration_snapshot_tx(
        self,
        db: sqlite3.Connection,
        task_id: str,
    ) -> MethodologyMigrationStateSnapshot:
        """Read exact migration inputs from one caller-owned transaction."""

        task_row = db.execute(
            "SELECT * FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if task_row is None:
            raise OrchestrationNotFoundError(task_id)
        plan_row = db.execute(
            "SELECT * FROM orchestration_plans WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if plan_row is None:
            raise OrchestrationNotFoundError(task_id)
        control_task_row = db.execute(
            "SELECT * FROM control_tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        inventory_row = db.execute(
            "SELECT payload FROM control_stage_inventories WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        active_runs = int(
            db.execute(
                """
                SELECT COUNT(*) AS value
                FROM orchestration_runs
                WHERE task_id = ? AND state = ?
                """,
                (task_id, RunState.RUNNING.value),
            ).fetchone()["value"]
        )
        active_consultations = int(
            db.execute(
                """
                SELECT COUNT(*) AS value
                FROM orchestration_consultations
                WHERE task_id = ? AND state = ?
                """,
                (task_id, ConsultationState.RUNNING.value),
            ).fetchone()["value"]
        )
        unsettled_protocol_runs = int(
            db.execute(
                """
                SELECT COUNT(*) AS value
                FROM protocol_runs
                WHERE task_id = ? AND settled_at IS NULL
                """,
                (task_id,),
            ).fetchone()["value"]
        )
        return MethodologyMigrationStateSnapshot(
            task=self.tasks._manifest(task_row),
            control_task=(
                TaskRecord(
                    task_id=control_task_row["task_id"],
                    project_id=control_task_row["project_id"],
                    status=control_task_row["status"],
                    version=control_task_row["version"],
                    created_at=control_task_row["created_at"],
                    updated_at=control_task_row["updated_at"],
                )
                if control_task_row is not None
                else None
            ),
            plan=self._plan(plan_row),
            current_methodology=MethodologyDefinition.model_validate_json(
                plan_row["methodology_payload"]
            ),
            stage_inventory=(
                StageInventory.model_validate_json(inventory_row["payload"])
                if inventory_row is not None
                else None
            ),
            active_runs=active_runs,
            active_consultations=active_consultations,
            unsettled_protocol_runs=unsettled_protocol_runs,
        )

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
        if not settlement_entries or any(
            item.cost_measurement == Measurement.UNAVAILABLE
            or item.cost_usd is None
            for item in settlement_entries
        ):
            cost_used = None
            cost_measurement = Measurement.UNAVAILABLE
        else:
            cost_used = sum(item.cost_usd or 0 for item in settlement_entries)
            cost_measurement = (
                Measurement.ESTIMATED
                if any(
                    item.cost_measurement == Measurement.ESTIMATED
                    for item in settlement_entries
                )
                else Measurement.EXACT
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
    def _validate_measured_cost(
        cost_used_usd: float | None,
        cost_measurement: Measurement,
    ) -> None:
        if cost_measurement == Measurement.UNAVAILABLE:
            if cost_used_usd is not None:
                raise OrchestrationValidationError(
                    "Unavailable cost measurement may not carry a value"
                )
        elif (
            cost_used_usd is None
            or not math.isfinite(cost_used_usd)
            or cost_used_usd < 0
        ):
            raise OrchestrationValidationError(
                "Measured cost must be a finite non-negative value"
            )

    @staticmethod
    def _validate_usage_observation(
        run: sqlite3.Row,
        observation: ProviderUsageObservation | None,
        *,
        token_used: int | None,
        token_measurement: Measurement,
        cost_used_usd: float | None,
        cost_measurement: Measurement,
    ) -> None:
        if observation is None:
            return
        if observation.run_id != run["run_id"] or observation.adapter != run["adapter"]:
            raise OrchestrationValidationError(
                "Usage observation crosses its Run or adapter scope"
            )
        if (
            observation.total_tokens != token_used
            or observation.token_measurement != token_measurement.value
            or observation.cost_usd != cost_used_usd
            or observation.cost_measurement != cost_measurement.value
        ):
            raise OrchestrationValidationError(
                "Usage observation does not match the compatibility settlement"
            )

    @staticmethod
    def _same_optional_money(left: float | None, right: float | None) -> bool:
        return left == right

    @classmethod
    def _budget_amendment_matches_request(
        cls,
        amendment: BudgetAmendment,
        *,
        task_id: str,
        amended_total_token_budget: int,
        amended_total_cost_budget_usd: float | None,
        expected_task_version: int,
        expected_plan_version: int,
        route: StageRouteDecision | None,
        contract: TaskContract | None,
        actor: str,
        reason: str,
    ) -> bool:
        # The durable request identity intentionally uses the canonical redacted
        # reason. Raw secrets are never part of a replay fingerprint or receipt.
        if reason != redact_text(reason):
            return False
        return bool(
            amendment.task_id == task_id
            and amendment.amended_total_token_budget
            == amended_total_token_budget
            and cls._same_optional_money(
                amendment.amended_total_cost_budget_usd,
                amended_total_cost_budget_usd,
            )
            and amendment.task_version_before == expected_task_version
            and amendment.plan_version_before == expected_plan_version
            and route is not None
            and amendment.project_id == route.project_id
            and amendment.inventory_id == route.inventory_id
            and amendment.inventory_sha256 == route.inventory_sha256
            and amendment.stage_key == route.stage_key
            and amendment.prior_policy.role == route.role
            and amendment.prior_policy.pinned_runtime == route.runtime
            and contract is not None
            and amendment.contract_id == contract.contract_id
            and amendment.contract_schema_version == contract.schema_version
            and amendment.contract_sha256 == contract_sha256(contract)
            and amendment.actor == actor
            and amendment.reason == reason
        )

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
        runtime_preflight = (
            PinnedRuntimePreflightDecision.model_validate_json(
                row["runtime_preflight_payload"]
            )
            if row["runtime_preflight_payload"]
            else None
        )
        usage_observation = (
            ProviderUsageObservation.model_validate_json(
                row["usage_observation_payload"]
            )
            if row["usage_observation_payload"]
            else None
        )
        run = OrchestrationRun(
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
            routing_policy=(
                RoutingPolicyDecision.model_validate_json(row["routing_policy_payload"])
                if row["routing_policy_payload"]
                else None
            ),
            runtime_preflight=runtime_preflight,
            usage_observation=usage_observation,
            started_at=row["started_at"], finished_at=row["finished_at"],
        )
        if usage_observation is not None and (
            usage_observation.run_id != run.run_id
            or usage_observation.adapter != run.adapter
            or usage_observation.total_tokens != run.token_used
            or usage_observation.token_measurement != run.token_measurement.value
            or usage_observation.cost_usd != run.cost_used_usd
            or usage_observation.cost_measurement != run.cost_measurement.value
        ):
            raise OrchestrationConflictError(
                "Persisted usage observation does not match its Run settlement"
            )
        if runtime_preflight is not None and (
            run.routing_policy is None
            or runtime_preflight.run_id != run.run_id
            or runtime_preflight.task_id != run.task_id
            or runtime_preflight.stage_key != run.stage_key
            or runtime_preflight.pinned_runtime != run.adapter
            or runtime_preflight.routing_policy_decision_id
            != run.routing_policy.decision_id
            or runtime_preflight.routing_policy_decision_sha256
            != run.routing_policy.content_sha256
        ):
            raise OrchestrationConflictError(
                "Persisted runtime preflight does not match its Run bindings"
            )
        return run

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

    @staticmethod
    def _consultation(row: sqlite3.Row) -> ConsultationRun:
        observation = (
            ProviderUsageObservation.model_validate_json(
                row["usage_observation_payload"]
            )
            if row["usage_observation_payload"]
            else None
        )
        consultation = ConsultationRun(
            consultation_id=row["consultation_id"],
            operation_key=row["operation_key"],
            project_id=row["project_id"],
            task_id=row["task_id"],
            plan_id=row["plan_id"],
            plan_version_observed=row["plan_version_observed"],
            inventory_id=row["inventory_id"],
            inventory_sha256=row["inventory_sha256"],
            stage_key=row["stage_key"],
            role=row["role"],
            runtime=row["runtime"],
            repository_id=row["repository_id"],
            repository_ref=row["repository_ref"],
            repository_commit=row["repository_commit"],
            decision_key=row["decision_key"],
            state=row["state"],
            prompt_sha256=row["prompt_sha256"],
            pid=row["pid"],
            process_status=row["process_status"],
            transport_status=row["transport_status"],
            schema_status=row["schema_status"],
            repair_attempts=row["repair_attempts"],
            candidate_id=row["candidate_id"],
            output_sha256=row["output_sha256"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            token_reserved=row["token_reserved"],
            cost_reserved_usd=row["cost_reserved_usd"],
            usage_observation=observation,
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )
        if observation is not None and (
            observation.run_id != consultation.consultation_id
            or observation.adapter != consultation.runtime
            or observation.total_tokens != row["token_used"]
            or observation.token_measurement != row["token_measurement"]
            or observation.cost_usd != row["cost_used_usd"]
            or observation.cost_measurement != row["cost_measurement"]
        ):
            raise OrchestrationConflictError(
                "Persisted consultation usage does not match its settlement"
            )
        return consultation

    @staticmethod
    def _consultation_candidate(row: sqlite3.Row) -> ConsultationCandidate:
        candidate = ConsultationCandidate.model_validate_json(row["payload"])
        if (
            candidate.candidate_id != row["candidate_id"]
            or candidate.plan_id != row["plan_id"]
            or candidate.task_id != row["task_id"]
            or candidate.stage_key != row["stage_key"]
            or candidate.operation_key != row["operation_key"]
            or candidate.created_at.isoformat() != row["created_at"]
        ):
            raise OrchestrationConflictError(
                "Consultation candidate row does not match its hash-sealed payload"
            )
        return candidate

    @staticmethod
    def _candidate_disposition(
        row: sqlite3.Row,
    ) -> ConsultationCandidateDisposition:
        disposition = ConsultationCandidateDisposition.model_validate_json(
            row["payload"]
        )
        if (
            disposition.disposition_id != row["disposition_id"]
            or disposition.plan_id != row["plan_id"]
            or disposition.task_id != row["task_id"]
            or disposition.candidate_id != row["candidate_id"]
            or disposition.operation_key != row["operation_key"]
            or disposition.action != row["action"]
            or disposition.created_at.isoformat() != row["created_at"]
        ):
            raise OrchestrationConflictError(
                "Candidate disposition row does not match its hash-sealed payload"
            )
        return disposition

    @staticmethod
    def _methodology_dispatch(
        row: sqlite3.Row,
    ) -> MethodologyRunDispatchState:
        claim = MethodologyRunDispatchClaim.model_validate_json(
            row["claim_payload"]
        )
        dispatch_policy = MethodologyRunDispatchPolicyDecision.model_validate_json(
            row["dispatch_policy_payload"]
        )
        preflight = PinnedRuntimePreflightDecision.model_validate_json(
            row["preflight_payload"]
        )
        receipt = (
            MethodologyRunDispatchReceipt.model_validate_json(
                row["receipt_payload"]
            )
            if row["receipt_payload"] is not None
            else None
        )
        adapter_result = (
            AgentAdapterResult.model_validate_json(
                row["adapter_result_payload"]
            )
            if row["adapter_result_payload"] is not None
            else None
        )
        usage_observation = (
            ProviderUsageObservation.model_validate_json(
                row["usage_observation_payload"]
            )
            if row["usage_observation_payload"] is not None
            else None
        )
        if (
            claim.dispatch_id != row["dispatch_id"]
            or claim.content_sha256 != row["claim_sha256"]
            or claim.task_id != row["task_id"]
            or claim.plan_id != row["plan_id"]
            or claim.run_id != row["run_id"]
            or claim.first_stage_key != row["stage_key"]
            or claim.runtime != row["runtime"]
            or claim.prompt_sha256 != row["prompt_sha256"]
            or claim.dispatch_policy != dispatch_policy
            or dispatch_policy.decision_id != row["dispatch_policy_id"]
            or dispatch_policy.content_sha256
            != row["dispatch_policy_sha256"]
            or claim.runtime_preflight != preflight
            or preflight.decision_id != row["preflight_id"]
            or preflight.content_sha256 != row["preflight_sha256"]
            or (receipt is None) != (row["receipt_sha256"] is None)
            or (
                receipt is not None
                and (
                    receipt.content_sha256 != row["receipt_sha256"]
                    or receipt.dispatch_claim != claim
                )
            )
        ):
            raise OrchestrationValidationError(
                "Persisted methodology dispatch binding drifted"
            )
        return MethodologyRunDispatchState(
            dispatch_id=row["dispatch_id"],
            state=MethodologyDispatchState(row["state"]),
            claim=claim,
            receipt=receipt,
            pid=row["pid"],
            process_started=bool(row["process_started"]),
            exit_code=row["exit_code"],
            timed_out=bool(row["timed_out"]),
            output=row["output"],
            error_message=row["error_message"],
            repository_unchanged=(
                bool(row["repository_unchanged"])
                if row["repository_unchanged"] is not None
                else None
            ),
            adapter_result=adapter_result,
            usage_observation=usage_observation,
            claimed_at=row["claimed_at"],
            process_attached_at=row["process_attached_at"],
            terminal_observed_at=row["terminal_observed_at"],
            settled_at=row["settled_at"],
        )

    @staticmethod
    def _provider_budget_snapshot(db, plan_id: str) -> dict[str, float | int]:
        formal = db.execute(
            """SELECT
                   COALESCE(SUM(CASE
                       WHEN ledger.token_measurement = 'unavailable'
                            OR ledger.tokens IS NULL
                           THEN runs.token_reserved
                       ELSE ledger.tokens END), 0) AS settled_tokens,
                   COALESCE(SUM(CASE
                       WHEN ledger.cost_measurement = 'unavailable'
                            OR ledger.cost_usd IS NULL
                           THEN COALESCE(runs.cost_reserved_usd, 0)
                       ELSE ledger.cost_usd END), 0) AS settled_cost
               FROM orchestration_usage_ledger AS ledger
               JOIN orchestration_runs AS runs ON runs.run_id = ledger.run_id
               WHERE ledger.plan_id = ? AND ledger.entry_type = 'settlement'""",
            (plan_id,),
        ).fetchone()
        formal_active = db.execute(
            """SELECT COALESCE(SUM(token_reserved), 0) AS tokens,
                      COALESCE(SUM(cost_reserved_usd), 0) AS cost
               FROM orchestration_runs WHERE plan_id = ? AND state = ?""",
            (plan_id, RunState.RUNNING.value),
        ).fetchone()
        consultations = db.execute(
            """SELECT
                   COALESCE(SUM(CASE
                       WHEN token_measurement = 'unavailable'
                            OR token_used IS NULL
                           THEN token_reserved
                       ELSE token_used END), 0) AS settled_tokens,
                   COALESCE(SUM(CASE
                       WHEN cost_measurement = 'unavailable'
                            OR cost_used_usd IS NULL
                           THEN COALESCE(cost_reserved_usd, 0)
                       ELSE cost_used_usd END), 0) AS settled_cost
               FROM orchestration_consultations
               WHERE plan_id = ? AND state != ?""",
            (plan_id, ConsultationState.RUNNING.value),
        ).fetchone()
        consultation_active = db.execute(
            """SELECT COALESCE(SUM(token_reserved), 0) AS tokens,
                      COALESCE(SUM(cost_reserved_usd), 0) AS cost
               FROM orchestration_consultations
               WHERE plan_id = ? AND state = ?""",
            (plan_id, ConsultationState.RUNNING.value),
        ).fetchone()
        methodology = db.execute(
            """SELECT
                   COALESCE(SUM(CASE
                       WHEN usage.token_measurement = 'unavailable'
                            OR usage.tokens IS NULL
                           THEN claims.token_reserved
                       ELSE usage.tokens END), 0) AS settled_tokens,
                   COALESCE(SUM(CASE
                       WHEN usage.cost_measurement = 'unavailable'
                            OR usage.cost_usd IS NULL
                           THEN COALESCE(claims.cost_reserved_usd, 0)
                       ELSE usage.cost_usd END), 0) AS settled_cost
               FROM orchestration_methodology_usage_ledger AS usage
               JOIN orchestration_methodology_run_claims AS claims
                 ON claims.run_id = usage.run_id
               WHERE usage.plan_id = ?""",
            (plan_id,),
        ).fetchone()
        methodology_active = db.execute(
            """SELECT COALESCE(SUM(claims.token_reserved), 0) AS tokens,
                      COALESCE(SUM(claims.cost_reserved_usd), 0) AS cost
               FROM orchestration_methodology_run_claims AS claims
               LEFT JOIN orchestration_methodology_usage_ledger AS usage
                 ON usage.run_id = claims.run_id
               WHERE claims.plan_id = ? AND usage.run_id IS NULL""",
            (plan_id,),
        ).fetchone()
        return {
            "settled_tokens": int(formal["settled_tokens"])
            + int(consultations["settled_tokens"])
            + int(methodology["settled_tokens"]),
            "settled_cost": float(formal["settled_cost"])
            + float(consultations["settled_cost"])
            + float(methodology["settled_cost"]),
            "active_tokens": int(formal_active["tokens"])
            + int(consultation_active["tokens"])
            + int(methodology_active["tokens"]),
            "active_cost": float(formal_active["cost"])
            + float(consultation_active["cost"])
            + float(methodology_active["cost"]),
        }

    @staticmethod
    def _budget_amendment(row: sqlite3.Row) -> BudgetAmendment:
        amendment = BudgetAmendment.model_validate_json(row["payload"])
        if (
            amendment.amendment_id != row["amendment_id"]
            or amendment.plan_id != row["plan_id"]
            or amendment.task_id != row["task_id"]
            or amendment.amendment_version != row["version"]
            or amendment.operation_key != row["operation_key"]
            or amendment.created_at.isoformat() != row["created_at"]
        ):
            raise OrchestrationConflictError(
                "Budget amendment row does not match its hash-sealed payload"
            )
        return amendment
