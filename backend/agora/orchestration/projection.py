"""Read-only unified Task projection across formal and compatibility ledgers."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime

from agora.attention.models import AttentionState
from agora.attention.store import AttentionStore
from agora.control_plane.models import ProtocolRunRecord
from agora.control_plane.store import ControlPlaneConflictError, ControlPlaneStore
from agora.protocol.models import Approval, Artifact, Evidence
from agora.protocol.state_machines import StageStatus
from agora.tasks.models import utc_now
from agora.tasks.store import TaskStore

from .models import (
    ArtifactSummary,
    ConsultationState,
    GateDerivedNextSafeAction,
    Measurement,
    MethodologyDispatchState,
    PlanState,
    ProjectionPage,
    RequiredHumanAction,
    RunState,
    RunWaitState,
    UnifiedAuditEvent,
    UnifiedBudgetProjection,
    UnifiedRunProjection,
    UnifiedStageGroupProgress,
    UnifiedStageProjection,
    UnifiedTaskProgress,
    UnifiedTaskProjection,
)
from .store import (
    OrchestrationConflictError,
    OrchestrationNotFoundError,
    OrchestrationStore,
    OrchestrationValidationError,
)


MAX_PROJECTION_PAGE = 200
MAX_CURRENT_RECORDS = 200


class TaskProjectionStore:
    """Compose one bounded projection from a single SQLite read snapshot."""

    def __init__(
        self,
        tasks: TaskStore,
        orchestration: OrchestrationStore,
        control_plane: ControlPlaneStore,
    ):
        self.tasks = tasks
        self.orchestration = orchestration
        self.control_plane = control_plane

    def get(
        self,
        task_id: str,
        *,
        history_limit: int = 100,
        history_offset: int = 0,
    ) -> UnifiedTaskProjection:
        if not 1 <= history_limit <= MAX_PROJECTION_PAGE:
            raise OrchestrationValidationError(
                "history_limit must be between 1 and 200"
            )
        if not 0 <= history_offset <= 1_000_000:
            raise OrchestrationValidationError(
                "history_offset must be between 0 and 1000000"
            )

        snapshot_at = utc_now()
        with closing(self.tasks._connect()) as db:
            db.execute("BEGIN")
            try:
                return self._snapshot(
                    db,
                    task_id,
                    snapshot_at=snapshot_at,
                    history_limit=history_limit,
                    history_offset=history_offset,
                )
            finally:
                db.rollback()

    def _snapshot(
        self,
        db: sqlite3.Connection,
        task_id: str,
        *,
        snapshot_at: str,
        history_limit: int,
        history_offset: int,
    ) -> UnifiedTaskProjection:
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
        stage_inventory = self.control_plane.stage_inventory_snapshot(db, task_id)
        task_state = (
            self.control_plane._task_record(control_task_row)
            if control_task_row is not None
            else None
        )
        try:
            lifecycle_decision = self.control_plane.task_lifecycle_decision_snapshot(
                db,
                task_id,
            )
            stage_route = self.control_plane.stage_route_snapshot(db, task_id)
        except ControlPlaneConflictError as exc:
            raise OrchestrationConflictError(str(exc)) from exc
        if task_state is None or lifecycle_decision is None:
            task_state_lifecycle = "unavailable"
        elif task_state.status == lifecycle_decision.target_status:
            task_state_lifecycle = "control_plane_managed"
        else:
            task_state_lifecycle = "reconciliation_required"
        plan_row = db.execute(
            "SELECT * FROM orchestration_plans WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if plan_row is None:
            raise OrchestrationNotFoundError(task_id)
        plan = self.orchestration._plan(plan_row)
        operational_stages = [
            self.orchestration._stage(row)
            for row in db.execute(
                """SELECT * FROM orchestration_stages
                   WHERE plan_id = ? ORDER BY sequence""",
                (plan.plan_id,),
            ).fetchall()
        ]

        control_stage_rows = db.execute(
            """SELECT * FROM control_stages
               WHERE task_id = ? ORDER BY stage_key LIMIT ?""",
            (task_id, MAX_CURRENT_RECORDS + 1),
        ).fetchall()
        gate_rows = db.execute(
            """SELECT * FROM control_gates
               WHERE task_id = ? ORDER BY gate_key LIMIT ?""",
            (task_id, MAX_CURRENT_RECORDS + 1),
        ).fetchall()
        if (
            len(control_stage_rows) > MAX_CURRENT_RECORDS
            or len(gate_rows) > MAX_CURRENT_RECORDS
        ):
            raise OrchestrationConflictError(
                "Formal Stage or Gate inventory exceeds the bounded Task projection"
            )
        control_stages = [
            self.control_plane._stage(row) for row in control_stage_rows
        ]
        gates = [self.control_plane._gate_record(db, row) for row in gate_rows]
        stage_by_key = {item.stage_key: item for item in control_stages}
        gate_by_stage = {}
        for gate in gates:
            if gate.stage_key in gate_by_stage:
                raise OrchestrationConflictError(
                    "Multiple formal Gates are bound to one inventory Stage"
                )
            gate_by_stage[gate.stage_key] = gate
        operational_by_key = {item.stage_key: item for item in operational_stages}
        inventory_by_key = {}
        group_by_stage = {}
        inventory_position = {}
        if stage_inventory is not None:
            position = 0
            for group in stage_inventory.groups:
                for item in group.stages:
                    position += 1
                    inventory_by_key[item.stage_key] = item
                    group_by_stage[item.stage_key] = group.group_key
                    inventory_position[item.stage_key] = position
            stage_keys = list(inventory_by_key)
            inventory_keys = set(stage_keys)
            if set(operational_by_key) != inventory_keys:
                raise OrchestrationConflictError(
                    "Pinned Stage inventory does not match the Plan Stage ledger"
                )
            if not set(stage_by_key).issubset(inventory_keys):
                raise OrchestrationConflictError(
                    "Formal Stage exists outside the immutable Task Stage inventory"
                )
            for stage_key, gate in gate_by_stage.items():
                item = inventory_by_key.get(stage_key)
                if item is None or item.gate_key != gate.gate_key:
                    raise OrchestrationConflictError(
                        "Formal Gate does not match the immutable Task Stage inventory"
                    )
            if (
                plan.current_stage_key is not None
                and plan.current_stage_key not in inventory_keys
            ):
                raise OrchestrationConflictError(
                    "Current Plan Stage is outside the immutable Task Stage inventory"
                )
        else:
            stage_keys = sorted(
                set(operational_by_key) | set(stage_by_key),
                key=lambda key: (
                    operational_by_key[key].sequence
                    if key in operational_by_key
                    else 1_000_000,
                    key,
                ),
            )
        current_stage_key = (
            stage_route.stage_key
            if stage_inventory is not None and stage_route is not None
            else (None if stage_inventory is not None else plan.current_stage_key)
        )
        if len(stage_keys) > MAX_CURRENT_RECORDS:
            raise OrchestrationConflictError(
                "Unified Stage inventory exceeds the bounded Task projection"
            )
        stages = [
            self._stage_projection(
                key,
                operational_by_key.get(key),
                stage_by_key.get(key),
                gate_by_stage.get(key),
                current_stage_key,
                inventory_by_key.get(key),
                group_by_stage.get(key),
                inventory_position.get(key),
            )
            for key in stage_keys
        ]

        run_ids, run_total = self._run_page(
            db,
            task_id,
            limit=history_limit,
            offset=history_offset,
        )
        operational_runs = self._operational_runs(db, run_ids)
        protocol_runs = self._protocol_runs(db, run_ids)
        methodology_claims = self._methodology_run_claims(db, run_ids)
        methodology_dispatches = self._methodology_run_dispatches(db, run_ids)
        runs = [
            self._run_projection(
                run_id,
                operational_runs.get(run_id),
                protocol_runs.get(run_id),
                methodology_claims.get(run_id),
                methodology_dispatches.get(run_id),
                snapshot_at,
            )
            for run_id in run_ids
        ]

        artifacts, artifact_total = self._artifacts(
            db, task_id, history_limit, history_offset
        )
        evidence, evidence_total = self._evidence(
            db, task_id, history_limit, history_offset
        )
        approvals, approval_total = self._approvals(
            db, task_id, history_limit, history_offset
        )
        decisions, decision_total = self._decisions(
            db,
            plan.plan_id,
            history_limit,
            history_offset,
        )
        consultation_runs, consultation_run_total = self._consultations(
            db,
            plan.plan_id,
            history_limit,
            history_offset,
        )
        consultation_candidates, consultation_candidate_total = (
            self._consultation_candidates(
                db,
                plan.plan_id,
                history_limit,
                history_offset,
            )
        )
        candidate_dispositions, candidate_disposition_total = (
            self._consultation_candidate_dispositions(
                db,
                plan.plan_id,
                history_limit,
                history_offset,
            )
        )
        budget_amendments, budget_amendment_total = self._budget_amendments(
            db,
            plan.plan_id,
            history_limit,
            history_offset,
        )
        usage, usage_total = self._usage(
            db,
            plan.plan_id,
            history_limit,
            history_offset,
        )
        audit_events, audit_total = self._audit_events(
            db, task_id, history_limit, history_offset
        )
        attention = AttentionStore.list_snapshot(
            db,
            task_id=task_id,
            limit=MAX_CURRENT_RECORDS,
            offset=0,
        )
        attention_total = db.execute(
            "SELECT COUNT(*) AS count FROM attention_items WHERE task_id = ?",
            (task_id,),
        ).fetchone()["count"]
        pending_candidate_rows = db.execute(
            """SELECT candidate.*
               FROM orchestration_consultation_candidates AS candidate
               LEFT JOIN orchestration_candidate_dispositions AS disposition
                 ON disposition.candidate_id = candidate.candidate_id
               WHERE candidate.plan_id = ? AND disposition.candidate_id IS NULL
               ORDER BY candidate.created_at, candidate.candidate_id
               LIMIT ?""",
            (plan.plan_id, MAX_CURRENT_RECORDS + 1),
        ).fetchall()
        if len(pending_candidate_rows) > MAX_CURRENT_RECORDS:
            raise OrchestrationConflictError(
                "Pending consultation candidates exceed the bounded Task projection"
            )
        pending_candidates = [
            self.orchestration._consultation_candidate(row)
            for row in pending_candidate_rows
        ]

        completed_stage_keys = [
            item.stage_key
            for item in stages
            if item.authoritative_stage is not None
            and item.authoritative_stage.status == StageStatus.COMPLETED
        ]
        if stage_inventory is not None:
            completed = set(completed_stage_keys)
            progress = UnifiedTaskProgress(
                inventory_complete=True,
                total_stages=len(stage_keys),
                completed_stages=len([key for key in stage_keys if key in completed]),
                current_stage_key=current_stage_key,
                current_stage_source=(
                    "control_plane_route" if current_stage_key is not None else None
                ),
                completed_stage_keys=[key for key in stage_keys if key in completed],
                remaining_stage_keys=[key for key in stage_keys if key not in completed],
                groups=[
                    UnifiedStageGroupProgress(
                        group_key=group.group_key,
                        sequence=group.sequence,
                        title=group.title,
                        total_stages=len(group.stages),
                        completed_stages=len(
                            [
                                item
                                for item in group.stages
                                if item.stage_key in completed
                            ]
                        ),
                        remaining_stage_keys=[
                            item.stage_key
                            for item in group.stages
                            if item.stage_key not in completed
                        ],
                    )
                    for group in stage_inventory.groups
                ],
            )
        else:
            progress = UnifiedTaskProgress(
                source="unavailable",
                inventory_complete=False,
                inventory_unavailable_reason=(
                    "Grouped Stage inventory has not been initialized; run task resume "
                    "to perform explicit recovery."
                ),
                current_stage_key=plan.current_stage_key,
                current_stage_source=(
                    "compatibility_plan" if plan.current_stage_key is not None else None
                ),
                completed_stage_keys=[],
                remaining_stage_keys=[],
                groups=[],
            )
        required_actions = self._required_human_actions(
            attention,
            plan.state,
            plan.plan_id,
            pending_candidates,
        )
        budget = self._budget_projection(db, plan)
        next_action = GateDerivedNextSafeAction.model_validate(
            self.control_plane._next_safe_action(gates)
        )

        totals = {
            "stages": len(stage_keys),
            "gates": len(gates),
            "runs": run_total,
            "artifacts": artifact_total,
            "evidence": evidence_total,
            "approvals": approval_total,
            "attention": attention_total,
            "decisions": decision_total,
            "consultation_runs": consultation_run_total,
            "consultation_candidates": consultation_candidate_total,
            "consultation_candidate_dispositions": candidate_disposition_total,
            "budget_amendments": budget_amendment_total,
            "usage": usage_total,
            "audit_events": audit_total,
        }
        pages = {
            "stages": ProjectionPage(
                limit=MAX_CURRENT_RECORDS, offset=0, total=totals["stages"]
            ),
            "gates": ProjectionPage(
                limit=MAX_CURRENT_RECORDS, offset=0, total=totals["gates"]
            ),
            "attention": ProjectionPage(
                limit=MAX_CURRENT_RECORDS, offset=0, total=attention_total
            ),
        }
        pages.update(
            {
                name: ProjectionPage(
                    limit=history_limit,
                    offset=history_offset,
                    total=totals[name],
                )
                for name in (
                    "runs",
                    "artifacts",
                    "evidence",
                    "approvals",
                    "decisions",
                    "consultation_runs",
                    "consultation_candidates",
                    "consultation_candidate_dispositions",
                    "budget_amendments",
                    "usage",
                    "audit_events",
                )
            }
        )
        return UnifiedTaskProjection(
            snapshot_at=snapshot_at,
            task=task,
            task_state=task_state.status if task_state is not None else None,
            task_state_version=(
                control_task_row["version"] if control_task_row is not None else None
            ),
            task_state_unavailable_reason=(
                None
                if control_task_row is not None
                else (
                    "Frozen Task state has not been initialized; run task resume "
                    "to perform explicit recovery."
                )
            ),
            task_state_lifecycle=task_state_lifecycle,
            task_lifecycle_decision=lifecycle_decision,
            stage_inventory=stage_inventory,
            stage_inventory_unavailable_reason=(
                None
                if stage_inventory is not None
                else (
                    "Grouped Stage inventory has not been initialized; run task resume "
                    "to perform explicit recovery."
                )
            ),
            stage_route=stage_route,
            stage_route_unavailable_reason=(
                (
                    "Authoritative Stage routing requires the grouped Stage inventory; "
                    "run task resume to perform explicit recovery."
                )
                if stage_inventory is None
                else (
                    (
                        "Authoritative Stage routing requires frozen Task state; "
                        "run task resume to perform explicit recovery."
                    )
                    if task_state is None or lifecycle_decision is None
                    else None
                )
            ),
            plan=plan,
            progress=progress,
            stages=stages,
            runs=runs,
            artifacts=artifacts,
            evidence=evidence,
            approvals=approvals,
            attention=attention,
            required_human_actions=required_actions,
            decisions=decisions,
            consultation_runs=consultation_runs,
            consultation_candidates=consultation_candidates,
            consultation_candidate_dispositions=candidate_dispositions,
            budget_amendments=budget_amendments,
            usage=usage,
            audit_events=audit_events,
            budget=budget,
            next_safe_action=next_action,
            compatibility_next_action=self.orchestration._next_action(
                plan,
                operational_stages,
            ),
            collection_totals=totals,
            collection_pages=pages,
        )

    @staticmethod
    def _stage_projection(
        stage_key,
        operational,
        authoritative,
        gate,
        current_stage_key,
        inventory_stage,
        group_key,
        inventory_position,
    ) -> UnifiedStageProjection:
        return UnifiedStageProjection(
            stage_key=stage_key,
            group_key=group_key,
            sequence=(
                inventory_position
                if inventory_position is not None
                else (operational.sequence if operational else None)
            ),
            title=(
                inventory_stage.title
                if inventory_stage is not None
                else (operational.title if operational else None)
            ),
            runtime=(
                inventory_stage.runtime
                if inventory_stage is not None
                else (operational.adapter if operational else None)
            ),
            inventory_stage=inventory_stage,
            current=stage_key == current_stage_key,
            operational_state=operational.state if operational else None,
            authoritative_stage=authoritative,
            gate=gate,
            attempt_count=operational.attempt_count if operational else 0,
            latest_run_id=operational.latest_run_id if operational else None,
            semantic_summary=operational.semantic_summary if operational else None,
            blockers=(
                [item[-4_000:] for item in operational.blockers[:100]]
                if operational
                else []
            ),
        )

    def _run_page(
        self,
        db: sqlite3.Connection,
        task_id: str,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[str], int]:
        rows = db.execute(
            """WITH candidates(run_id, sort_at) AS (
                   SELECT run_id, started_at FROM orchestration_runs WHERE task_id = ?
                   UNION ALL
                   SELECT run_id, created_at FROM protocol_runs WHERE task_id = ?
               )
               SELECT run_id, MIN(sort_at) AS sort_at
               FROM candidates GROUP BY run_id
               ORDER BY sort_at, run_id LIMIT ? OFFSET ?""",
            (task_id, task_id, limit, offset),
        ).fetchall()
        total = db.execute(
            """SELECT COUNT(*) AS count FROM (
                   SELECT run_id FROM orchestration_runs WHERE task_id = ?
                   UNION
                   SELECT run_id FROM protocol_runs WHERE task_id = ?
               )""",
            (task_id, task_id),
        ).fetchone()["count"]
        return [row["run_id"] for row in rows], total

    def _operational_runs(self, db, run_ids):
        if not run_ids:
            return {}
        placeholders = ",".join("?" for _ in run_ids)
        rows = db.execute(
            f"SELECT * FROM orchestration_runs WHERE run_id IN ({placeholders})",
            tuple(run_ids),
        ).fetchall()
        return {row["run_id"]: self.orchestration._run(row) for row in rows}

    def _protocol_runs(self, db, run_ids):
        if not run_ids:
            return {}
        placeholders = ",".join("?" for _ in run_ids)
        rows = db.execute(
            f"SELECT * FROM protocol_runs WHERE run_id IN ({placeholders})",
            tuple(run_ids),
        ).fetchall()
        return {row["run_id"]: self.control_plane._protocol_run(row) for row in rows}

    @staticmethod
    def _methodology_run_claims(db, run_ids):
        if not run_ids:
            return {}
        placeholders = ",".join("?" for _ in run_ids)
        rows = db.execute(
            f"""
            SELECT run_id, runtime, token_reserved, cost_reserved_usd
            FROM orchestration_methodology_run_claims
            WHERE run_id IN ({placeholders})
            UNION ALL
            SELECT run_id, runtime, token_reserved, cost_reserved_usd
            FROM orchestration_methodology_stage_run_claims
            WHERE run_id IN ({placeholders})
            """,
            (*run_ids, *run_ids),
        ).fetchall()
        return {row["run_id"]: row for row in rows}

    def _methodology_run_dispatches(self, db, run_ids):
        if not run_ids:
            return {}
        placeholders = ",".join("?" for _ in run_ids)
        first_rows = db.execute(
            f"""
            SELECT * FROM orchestration_methodology_run_dispatches
            WHERE run_id IN ({placeholders})
            """,
            tuple(run_ids),
        ).fetchall()
        stage_rows = db.execute(
            f"""
            SELECT * FROM orchestration_methodology_stage_run_dispatches
            WHERE run_id IN ({placeholders})
            """,
            tuple(run_ids),
        ).fetchall()
        dispatches = {
            row["run_id"]: self.orchestration._methodology_dispatch(row)
            for row in first_rows
        }
        dispatches.update(
            {
                row["run_id"]: (
                    self.orchestration._methodology_stage_dispatch(row)
                )
                for row in stage_rows
            }
        )
        return dispatches

    def _run_projection(
        self,
        run_id: str,
        operational,
        protocol: ProtocolRunRecord | None,
        methodology_claim,
        methodology_dispatch,
        snapshot_at: str,
    ) -> UnifiedRunProjection:
        protocol_state = protocol.protocol_state if protocol else None
        observed_protocol_state = (
            methodology_dispatch.adapter_result.protocol_state
            if methodology_dispatch is not None
            and methodology_dispatch.adapter_result is not None
            else None
        )
        projected_protocol_state = protocol_state or observed_protocol_state
        handoff = protocol.handoff_pack if protocol else None
        if projected_protocol_state is not None:
            semantic_result = projected_protocol_state.semantic_stage_result
            semantic_source = "protocol"
        elif operational is not None and operational.semantic_status is not None:
            semantic_result = operational.semantic_status
            semantic_source = "compatibility"
        else:
            semantic_result = None
            semantic_source = "unavailable"
        started_at = (
            operational.started_at
            if operational is not None
            else methodology_dispatch.claimed_at
            if methodology_dispatch is not None
            else protocol.created_at
        )
        finished_at = (
            operational.finished_at
            if operational is not None and operational.finished_at is not None
            else methodology_dispatch.settled_at
            if methodology_dispatch is not None
            and methodology_dispatch.settled_at is not None
            else protocol.settled_at if protocol else None
        )
        runtime = operational.adapter if operational is not None else None
        if runtime is None and handoff is not None:
            runtime = handoff.producer.runtime.value
        if runtime is None and methodology_claim is not None:
            runtime = methodology_claim["runtime"]
        dispatch_operational_state = None
        if methodology_dispatch is not None:
            if methodology_dispatch.state != MethodologyDispatchState.SETTLED:
                dispatch_operational_state = RunState.RUNNING
            elif protocol is not None and protocol.protocol_state is not None:
                dispatch_operational_state = (
                    RunState.PASSED
                    if methodology_dispatch.receipt is not None
                    and methodology_dispatch.receipt.stage_status
                    == StageStatus.COMPLETED
                    else RunState.CANCELLED
                    if methodology_dispatch.receipt is not None
                    and methodology_dispatch.receipt.stage_status
                    == StageStatus.CANCELLED
                    else RunState.FAILED
                    if methodology_dispatch.receipt is not None
                    and methodology_dispatch.receipt.stage_status
                    == StageStatus.FAILED
                    else RunState.BLOCKED
                )
        return UnifiedRunProjection(
            run_id=run_id,
            stage_key=(
                operational.stage_key if operational is not None else protocol.stage_key
            ),
            runtime=runtime,
            attempt=operational.attempt if operational is not None else None,
            operational_state=(
                operational.state
                if operational is not None
                else dispatch_operational_state
            ),
            wait_state=self._wait_state(
                operational,
                protocol,
                methodology_dispatch,
            ),
            process_status=(
                projected_protocol_state.process_status
                if projected_protocol_state
                else None
            ),
            transport_status=(
                projected_protocol_state.transport_status
                if projected_protocol_state
                else None
            ),
            schema_status=(
                projected_protocol_state.schema_status
                if projected_protocol_state
                else None
            ),
            semantic_result=semantic_result,
            semantic_source=semantic_source,
            process_exit_code=(
                projected_protocol_state.process_exit_code
                if projected_protocol_state is not None
                else operational.exit_code if operational is not None else None
            ),
            timed_out=(
                operational.timed_out
                if operational is not None
                else methodology_dispatch.timed_out
                if methodology_dispatch is not None
                else False
            ),
            semantic_summary=(
                operational.semantic_summary[-4_000:]
                if operational is not None and operational.semantic_summary
                else None
            ),
            findings=(
                [item[-4_000:] for item in operational.findings[:100]]
                if operational
                else []
            ),
            failure=(
                operational.error_message[-4_000:]
                if operational is not None and operational.error_message
                else methodology_dispatch.error_message[-4_000:]
                if methodology_dispatch is not None
                and methodology_dispatch.error_message
                else None
            ),
            context_pack_id=protocol.context_pack.pack_id if protocol else None,
            context_sha256=(
                protocol.context_pack.content_sha256 if protocol else None
            ),
            handoff_pack_id=handoff.pack_id if handoff else None,
            handoff_sha256=handoff.content_sha256 if handoff else None,
            adapter_error_code=(
                protocol.adapter_error_code.value
                if protocol and protocol.adapter_error_code
                else None
            ),
            attention_required=protocol.attention_required if protocol else False,
            attention_item_id=protocol.attention_item_id if protocol else None,
            token_reserved=(
                operational.token_reserved
                if operational
                else methodology_claim["token_reserved"]
                if methodology_claim is not None
                else None
            ),
            token_settled=(
                operational.token_used
                if operational
                else methodology_dispatch.usage_observation.total_tokens
                if methodology_dispatch is not None
                and methodology_dispatch.usage_observation is not None
                else None
            ),
            token_measurement=(
                operational.token_measurement
                if operational
                else Measurement(
                    methodology_dispatch.usage_observation.token_measurement
                )
                if methodology_dispatch is not None
                and methodology_dispatch.usage_observation is not None
                else None
            ),
            cost_reserved_usd=(
                operational.cost_reserved_usd
                if operational
                else methodology_claim["cost_reserved_usd"]
                if methodology_claim is not None
                else None
            ),
            cost_settled_usd=(
                operational.cost_used_usd
                if operational
                else methodology_dispatch.usage_observation.cost_usd
                if methodology_dispatch is not None
                and methodology_dispatch.usage_observation is not None
                else None
            ),
            cost_measurement=(
                operational.cost_measurement
                if operational
                else Measurement(
                    methodology_dispatch.usage_observation.cost_measurement
                )
                if methodology_dispatch is not None
                and methodology_dispatch.usage_observation is not None
                else None
            ),
            routing_policy=(operational.routing_policy if operational else None),
            runtime_preflight=(
                operational.runtime_preflight
                if operational is not None
                else methodology_dispatch.claim.runtime_preflight
                if methodology_dispatch is not None
                else None
            ),
            usage_observation=(
                operational.usage_observation
                if operational
                else methodology_dispatch.usage_observation
                if methodology_dispatch is not None
                else None
            ),
            started_at=started_at,
            finished_at=finished_at,
            elapsed_seconds=self._elapsed_seconds(
                started_at,
                finished_at or snapshot_at,
            ),
        )

    @staticmethod
    def _wait_state(
        operational,
        protocol: ProtocolRunRecord | None,
        methodology_dispatch=None,
    ) -> RunWaitState:
        if methodology_dispatch is not None:
            return (
                RunWaitState.SETTLED
                if methodology_dispatch.state == MethodologyDispatchState.SETTLED
                else RunWaitState.RUNTIME_OR_SETTLEMENT_PENDING
            )
        if operational is None:
            return (
                RunWaitState.SETTLED
                if protocol is not None and protocol.settled_at is not None
                else RunWaitState.RUNTIME_OR_SETTLEMENT_PENDING
            )
        if operational.state != RunState.RUNNING:
            return RunWaitState.SETTLED
        if protocol is None:
            return (
                RunWaitState.PROTOCOL_START_PENDING
                if ":protocol:" in operational.operation_key
                else RunWaitState.OPERATIONAL_RUNTIME_PENDING
            )
        if protocol.settled_at is not None:
            return RunWaitState.COMPATIBILITY_PROJECTION_PENDING
        if operational.pid is None:
            return RunWaitState.PROTOCOL_START_PENDING
        return RunWaitState.RUNTIME_OR_SETTLEMENT_PENDING

    @staticmethod
    def _elapsed_seconds(started_at: str, ended_at: str) -> float:
        try:
            started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            ended = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise OrchestrationValidationError(
                "Persisted Run timestamps are invalid"
            ) from exc
        if started.tzinfo is None or ended.tzinfo is None:
            raise OrchestrationValidationError(
                "Persisted Run timestamps must include a timezone"
            )
        return max(0.0, (ended - started).total_seconds())

    def _artifacts(self, db, task_id, limit, offset):
        rows = db.execute(
            """SELECT payload FROM protocol_artifacts WHERE task_id = ?
               ORDER BY created_at, artifact_id, version LIMIT ? OFFSET ?""",
            (task_id, limit, offset),
        ).fetchall()
        values = []
        for row in rows:
            artifact = Artifact.model_validate_json(row["payload"])
            values.append(
                ArtifactSummary(
                    version_ref=artifact.version_ref(),
                    project_id=artifact.project_id,
                    task_id=artifact.task_id,
                    stage_key=artifact.stage_key,
                    producer_runtime=artifact.producer.runtime.value,
                    producer_run_id=artifact.producer.run_id,
                    media_type=artifact.media_type,
                    created_at=artifact.created_at.isoformat(),
                )
            )
        return values, self._count(db, "protocol_artifacts", task_id)

    def _evidence(self, db, task_id, limit, offset):
        rows = db.execute(
            """SELECT payload FROM protocol_evidence WHERE task_id = ?
               ORDER BY observed_at, evidence_id LIMIT ? OFFSET ?""",
            (task_id, limit, offset),
        ).fetchall()
        return (
            [Evidence.model_validate_json(row["payload"]) for row in rows],
            self._count(db, "protocol_evidence", task_id),
        )

    def _approvals(self, db, task_id, limit, offset):
        rows = db.execute(
            """SELECT payload FROM protocol_approvals WHERE task_id = ?
               ORDER BY approved_at, approval_id LIMIT ? OFFSET ?""",
            (task_id, limit, offset),
        ).fetchall()
        return (
            [Approval.model_validate_json(row["payload"]) for row in rows],
            self._count(db, "protocol_approvals", task_id),
        )

    def _decisions(self, db, plan_id, limit, offset):
        rows = db.execute(
            """SELECT * FROM orchestration_decisions WHERE plan_id = ?
               ORDER BY decision_key, version LIMIT ? OFFSET ?""",
            (plan_id, limit, offset),
        ).fetchall()
        total = db.execute(
            """SELECT COUNT(*) AS count FROM orchestration_decisions
               WHERE plan_id = ?""",
            (plan_id,),
        ).fetchone()["count"]
        return [self.orchestration._decision(row) for row in rows], total

    def _consultation_candidates(self, db, plan_id, limit, offset):
        rows = db.execute(
            """SELECT * FROM orchestration_consultation_candidates
               WHERE plan_id = ?
               ORDER BY created_at, candidate_id LIMIT ? OFFSET ?""",
            (plan_id, limit, offset),
        ).fetchall()
        total = db.execute(
            """SELECT COUNT(*) AS count
               FROM orchestration_consultation_candidates WHERE plan_id = ?""",
            (plan_id,),
        ).fetchone()["count"]
        return [
            self.orchestration._consultation_candidate(row) for row in rows
        ], total

    def _consultations(self, db, plan_id, limit, offset):
        rows = db.execute(
            """SELECT * FROM orchestration_consultations
               WHERE plan_id = ?
               ORDER BY started_at, consultation_id LIMIT ? OFFSET ?""",
            (plan_id, limit, offset),
        ).fetchall()
        total = db.execute(
            """SELECT COUNT(*) AS count FROM orchestration_consultations
               WHERE plan_id = ?""",
            (plan_id,),
        ).fetchone()["count"]
        return [self.orchestration._consultation(row) for row in rows], total

    def _consultation_candidate_dispositions(self, db, plan_id, limit, offset):
        rows = db.execute(
            """SELECT * FROM orchestration_candidate_dispositions
               WHERE plan_id = ?
               ORDER BY created_at, disposition_id LIMIT ? OFFSET ?""",
            (plan_id, limit, offset),
        ).fetchall()
        total = db.execute(
            """SELECT COUNT(*) AS count
               FROM orchestration_candidate_dispositions WHERE plan_id = ?""",
            (plan_id,),
        ).fetchone()["count"]
        return [
            self.orchestration._candidate_disposition(row) for row in rows
        ], total

    def _usage(self, db, plan_id, limit, offset):
        self.orchestration._validate_methodology_stage_usage_ledgers(
            db,
            plan_id,
        )
        rows = db.execute(
            """SELECT * FROM (
                   SELECT entry_id, task_id, plan_id, stage_key, run_id,
                          entry_type, tokens, token_measurement, cost_usd,
                          cost_measurement, adapter, created_at
                   FROM orchestration_usage_ledger WHERE plan_id = ?
                   UNION ALL
                   SELECT entry_id, task_id, plan_id, stage_key, run_id,
                          'settlement' AS entry_type, tokens,
                          token_measurement, cost_usd, cost_measurement,
                          adapter, created_at
                   FROM orchestration_methodology_usage_ledger
                   WHERE plan_id = ?
                   UNION ALL
                   SELECT entry_id, task_id, plan_id, stage_key, run_id,
                          'settlement' AS entry_type, tokens,
                          token_measurement, cost_usd, cost_measurement,
                          adapter, created_at
                   FROM orchestration_methodology_stage_usage_ledger
                   WHERE plan_id = ?
               )
               ORDER BY created_at, entry_id LIMIT ? OFFSET ?""",
            (plan_id, plan_id, plan_id, limit, offset),
        ).fetchall()
        total = db.execute(
            """SELECT
                   (SELECT COUNT(*) FROM orchestration_usage_ledger
                    WHERE plan_id = ?) +
                   (SELECT COUNT(*) FROM orchestration_methodology_usage_ledger
                    WHERE plan_id = ?) +
                   (SELECT COUNT(*)
                    FROM orchestration_methodology_stage_usage_ledger
                    WHERE plan_id = ?) AS count""",
            (plan_id, plan_id, plan_id),
        ).fetchone()["count"]
        return [self.orchestration._usage(row) for row in rows], total

    def _budget_amendments(self, db, plan_id, limit, offset):
        rows = db.execute(
            """SELECT * FROM orchestration_budget_amendments WHERE plan_id = ?
               ORDER BY version LIMIT ? OFFSET ?""",
            (plan_id, limit, offset),
        ).fetchall()
        total = db.execute(
            """SELECT COUNT(*) AS count FROM orchestration_budget_amendments
               WHERE plan_id = ?""",
            (plan_id,),
        ).fetchone()["count"]
        return [self.orchestration._budget_amendment(row) for row in rows], total

    def _audit_events(self, db, task_id, limit, offset):
        rows = db.execute(
            """SELECT event_id, 'task' AS source, NULL AS event_key,
                      event_type, actor, payload, created_at
               FROM task_events WHERE task_id = ?
               UNION ALL
               SELECT event_id, 'control_plane' AS source, event_key,
                      event_type, actor, payload, created_at
               FROM control_events WHERE task_id = ?
               ORDER BY created_at, event_id, source LIMIT ? OFFSET ?""",
            (task_id, task_id, limit, offset),
        ).fetchall()
        total = db.execute(
            """SELECT
                   (SELECT COUNT(*) FROM task_events WHERE task_id = ?) +
                   (SELECT COUNT(*) FROM control_events WHERE task_id = ?)
                   AS count""",
            (task_id, task_id),
        ).fetchone()["count"]
        events = []
        for row in rows:
            payload, digest, truncated = self._bounded_event_payload(row["payload"])
            events.append(
                UnifiedAuditEvent(
                    event_id=row["event_id"],
                    source=row["source"],
                    event_key=row["event_key"],
                    event_type=row["event_type"],
                    actor=row["actor"],
                    payload=payload,
                    payload_sha256=digest,
                    payload_truncated=truncated,
                    created_at=row["created_at"],
                )
            )
        return events, total

    @staticmethod
    def _required_human_actions(attention, plan_state, plan_id, pending_candidates):
        actions = [
            RequiredHumanAction(
                action_id=f"attention:{item.item_id}",
                kind="attention",
                title=item.title,
                source_id=item.item_id,
            )
            for item in attention
            if item.state == AttentionState.OPEN
        ]
        if plan_state == PlanState.AWAITING_APPROVAL:
            actions.append(
                RequiredHumanAction(
                    action_id=f"plan-approval:{plan_id}",
                    kind="plan_approval",
                    title="Review the formal Stage results and explicitly approve the Plan.",
                    source_id=plan_id,
                )
            )
        actions.extend(
            RequiredHumanAction(
                action_id=f"candidate-disposition:{candidate.candidate_id}",
                kind="candidate_disposition",
                title=f"Adopt or reject consultation candidate: {candidate.title}",
                source_id=candidate.candidate_id,
            )
            for candidate in pending_candidates
        )
        return actions

    def _budget_projection(self, db, plan):
        self.orchestration._validate_methodology_stage_usage_ledgers(
            db,
            plan.plan_id,
        )
        active_runs = db.execute(
            """SELECT COUNT(*) AS count,
                      COALESCE(SUM(token_reserved), 0) AS token_reserved,
                      SUM(CASE WHEN cost_reserved_usd IS NULL THEN 1 ELSE 0 END)
                          AS unavailable_costs,
                      COALESCE(SUM(cost_reserved_usd), 0) AS cost_reserved
               FROM orchestration_runs WHERE plan_id = ? AND state = ?""",
            (plan.plan_id, RunState.RUNNING.value),
        ).fetchone()
        active_consultations = db.execute(
            """SELECT COUNT(*) AS count,
                      COALESCE(SUM(token_reserved), 0) AS token_reserved,
                      SUM(CASE WHEN cost_reserved_usd IS NULL THEN 1 ELSE 0 END)
                          AS unavailable_costs,
                      COALESCE(SUM(cost_reserved_usd), 0) AS cost_reserved
               FROM orchestration_consultations
               WHERE plan_id = ? AND state = ?""",
            (plan.plan_id, ConsultationState.RUNNING.value),
        ).fetchone()
        active_methodology_claims = db.execute(
            """SELECT COUNT(*) AS count,
                      COALESCE(SUM(c.token_reserved), 0) AS token_reserved,
                      SUM(CASE WHEN c.cost_reserved_usd IS NULL THEN 1 ELSE 0 END)
                          AS unavailable_costs,
                      COALESCE(SUM(c.cost_reserved_usd), 0) AS cost_reserved
               FROM (
                   SELECT first_claim.plan_id, first_claim.token_reserved,
                          first_claim.cost_reserved_usd
                   FROM orchestration_methodology_run_claims first_claim
                   LEFT JOIN orchestration_methodology_usage_ledger usage
                     ON usage.run_id = first_claim.run_id
                   WHERE first_claim.plan_id = ? AND usage.run_id IS NULL
                   UNION ALL
                   SELECT stage_claim.plan_id, stage_claim.token_reserved,
                          stage_claim.cost_reserved_usd
                   FROM orchestration_methodology_stage_run_claims stage_claim
                   LEFT JOIN orchestration_methodology_stage_usage_ledger usage
                     ON usage.run_id = stage_claim.run_id
                   WHERE stage_claim.plan_id = ? AND usage.run_id IS NULL
               ) c""",
            (plan.plan_id, plan.plan_id),
        ).fetchone()
        settled_runs = db.execute(
            """SELECT COUNT(*) AS count,
                      COALESCE(SUM(CASE WHEN tokens IS NOT NULL THEN tokens ELSE 0 END), 0)
                          AS known_tokens,
                      SUM(CASE WHEN token_measurement = 'unavailable' THEN 1 ELSE 0 END)
                          AS unavailable_tokens,
                      SUM(CASE WHEN token_measurement = 'estimated' THEN 1 ELSE 0 END)
                          AS estimated_tokens,
                      SUM(CASE WHEN cost_usd IS NOT NULL THEN 1 ELSE 0 END)
                          AS cost_count,
                      COALESCE(SUM(cost_usd), 0) AS known_cost,
                      SUM(CASE WHEN cost_usd IS NOT NULL
                               AND cost_measurement != 'exact' THEN 1 ELSE 0 END)
                          AS inexact_costs
               FROM orchestration_usage_ledger
               WHERE plan_id = ? AND entry_type = 'settlement'""",
            (plan.plan_id,),
        ).fetchone()
        settled_consultations = db.execute(
            """SELECT COUNT(*) AS count,
                      COALESCE(SUM(CASE WHEN token_used IS NOT NULL
                                        THEN token_used ELSE 0 END), 0)
                          AS known_tokens,
                      SUM(CASE WHEN token_measurement = 'unavailable'
                               THEN 1 ELSE 0 END) AS unavailable_tokens,
                      SUM(CASE WHEN token_measurement = 'estimated'
                               THEN 1 ELSE 0 END) AS estimated_tokens,
                      SUM(CASE WHEN cost_used_usd IS NOT NULL
                               THEN 1 ELSE 0 END) AS cost_count,
                      COALESCE(SUM(cost_used_usd), 0) AS known_cost,
                      SUM(CASE WHEN cost_used_usd IS NOT NULL
                               AND cost_measurement != 'exact'
                               THEN 1 ELSE 0 END) AS inexact_costs
               FROM orchestration_consultations
               WHERE plan_id = ? AND state != ?""",
            (plan.plan_id, ConsultationState.RUNNING.value),
        ).fetchone()
        settled_methodology = db.execute(
            """SELECT COUNT(*) AS count,
                      COALESCE(SUM(CASE WHEN tokens IS NOT NULL
                                        THEN tokens ELSE 0 END), 0)
                          AS known_tokens,
                      SUM(CASE WHEN token_measurement = 'unavailable'
                               THEN 1 ELSE 0 END) AS unavailable_tokens,
                      SUM(CASE WHEN token_measurement = 'estimated'
                               THEN 1 ELSE 0 END) AS estimated_tokens,
                      SUM(CASE WHEN cost_usd IS NOT NULL
                               THEN 1 ELSE 0 END) AS cost_count,
                      COALESCE(SUM(cost_usd), 0) AS known_cost,
                      SUM(CASE WHEN cost_usd IS NOT NULL
                               AND cost_measurement != 'exact'
                               THEN 1 ELSE 0 END) AS inexact_costs
               FROM orchestration_methodology_usage_ledger
               WHERE plan_id = ?""",
            (plan.plan_id,),
        ).fetchone()
        settled_methodology_stage = db.execute(
            """SELECT COUNT(*) AS count,
                      COALESCE(SUM(CASE WHEN tokens IS NOT NULL
                                        THEN tokens ELSE 0 END), 0)
                          AS known_tokens,
                      SUM(CASE WHEN token_measurement = 'unavailable'
                               THEN 1 ELSE 0 END) AS unavailable_tokens,
                      SUM(CASE WHEN token_measurement = 'estimated'
                               THEN 1 ELSE 0 END) AS estimated_tokens,
                      SUM(CASE WHEN cost_usd IS NOT NULL
                               THEN 1 ELSE 0 END) AS cost_count,
                      COALESCE(SUM(cost_usd), 0) AS known_cost,
                      SUM(CASE WHEN cost_usd IS NOT NULL
                               AND cost_measurement != 'exact'
                               THEN 1 ELSE 0 END) AS inexact_costs
               FROM orchestration_methodology_stage_usage_ledger
               WHERE plan_id = ?""",
            (plan.plan_id,),
        ).fetchone()
        active_token_reserved = (
            active_runs["token_reserved"]
            + active_consultations["token_reserved"]
            + active_methodology_claims["token_reserved"]
        )
        active_unavailable_costs = (
            (active_runs["unavailable_costs"] or 0)
            + (active_consultations["unavailable_costs"] or 0)
            + (active_methodology_claims["unavailable_costs"] or 0)
        )
        active_cost_reserved = (
            active_runs["cost_reserved"]
            + active_consultations["cost_reserved"]
            + active_methodology_claims["cost_reserved"]
        )
        settled_count = (
            settled_runs["count"]
            + settled_consultations["count"]
            + settled_methodology["count"]
            + settled_methodology_stage["count"]
        )
        known_tokens = (
            settled_runs["known_tokens"]
            + settled_consultations["known_tokens"]
            + settled_methodology["known_tokens"]
            + settled_methodology_stage["known_tokens"]
        )
        unavailable_tokens = (
            (settled_runs["unavailable_tokens"] or 0)
            + (settled_consultations["unavailable_tokens"] or 0)
            + (settled_methodology["unavailable_tokens"] or 0)
            + (settled_methodology_stage["unavailable_tokens"] or 0)
        )
        estimated_tokens = (
            (settled_runs["estimated_tokens"] or 0)
            + (settled_consultations["estimated_tokens"] or 0)
            + (settled_methodology["estimated_tokens"] or 0)
            + (settled_methodology_stage["estimated_tokens"] or 0)
        )
        if unavailable_tokens:
            token_measurement = "unavailable"
            token_settled = None
        elif estimated_tokens:
            token_measurement = "estimated"
            token_settled = known_tokens
        else:
            token_measurement = "exact"
            token_settled = known_tokens
        token_remaining = (
            None
            if token_settled is None
            else max(
                0,
                plan.total_token_budget
                - active_token_reserved
                - token_settled,
            )
        )
        cost_count = (
            (settled_runs["cost_count"] or 0)
            + (settled_consultations["cost_count"] or 0)
            + (settled_methodology["cost_count"] or 0)
            + (settled_methodology_stage["cost_count"] or 0)
        )
        known_cost = (
            settled_runs["known_cost"] + settled_consultations["known_cost"]
            + settled_methodology["known_cost"]
            + settled_methodology_stage["known_cost"]
        )
        inexact_costs = (
            (settled_runs["inexact_costs"] or 0)
            + (settled_consultations["inexact_costs"] or 0)
            + (settled_methodology["inexact_costs"] or 0)
            + (settled_methodology_stage["inexact_costs"] or 0)
        )
        if settled_count == 0:
            cost_settled = 0.0
            cost_measurement = "exact"
        elif cost_count != settled_count:
            cost_settled = None
            cost_measurement = "unavailable"
        else:
            cost_settled = known_cost
            cost_measurement = (
                "estimated" if inexact_costs else "exact"
            )
        if plan.total_cost_budget_usd is None:
            cost_reserved = None
        elif active_unavailable_costs:
            cost_reserved = None
        else:
            cost_reserved = active_cost_reserved
        if (
            plan.total_cost_budget_usd is None
            or cost_settled is None
            or cost_reserved is None
        ):
            cost_remaining = None
        else:
            cost_remaining = max(
                0.0,
                plan.total_cost_budget_usd
                - cost_settled
                - cost_reserved,
            )
        return UnifiedBudgetProjection(
            token_allocated=plan.total_token_budget,
            token_reserved=active_token_reserved,
            token_settled=token_settled,
            token_measurement=token_measurement,
            token_remaining=token_remaining,
            cost_allocated_usd=plan.total_cost_budget_usd,
            cost_reserved_usd=cost_reserved,
            cost_settled_usd=cost_settled,
            cost_measurement=cost_measurement,
            cost_remaining_usd=cost_remaining,
        )

    @staticmethod
    def _bounded_event_payload(raw_payload: str):
        encoded = raw_payload.encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        if len(encoded) <= 16_384:
            return json.loads(raw_payload), digest, False
        return {
            "projection_truncated": True,
            "payload_sha256": digest,
            "original_utf8_bytes": len(encoded),
        }, digest, True

    @staticmethod
    def _count(db, table: str, task_id: str) -> int:
        return db.execute(
            f"SELECT COUNT(*) AS count FROM {table} WHERE task_id = ?",
            (task_id,),
        ).fetchone()["count"]
