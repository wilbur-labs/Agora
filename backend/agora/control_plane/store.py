"""Transactional Control Plane v2 registry on the existing Agora SQLite database."""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections import defaultdict
from contextlib import closing
from typing import Any

from agora.attention.schema import initialize_attention_schema
from agora.attention.store import AttentionStore
from agora.protocol.agent_adapter import AgentAdapterResult
from agora.protocol.gates import evaluate_gate
from agora.protocol.hashing import canonical_json_bytes, canonical_sha256
from agora.protocol.invalidation import invalidate_approvals
from agora.protocol.models import (
    Approval,
    ApprovalStatus,
    Artifact,
    ContextPack,
    Evidence,
    GateEvaluation,
    GateRequirement,
    HandoffPack,
    RunProtocolState,
    SemanticStageResult,
    StageInventory,
)
from agora.protocol.state_machines import (
    GateStatus,
    StageStatus,
    TaskStatus,
    TransitionError,
    transition_gate,
    transition_stage,
    transition_task as validate_task_transition,
)
from agora.tasks.models import utc_now
from agora.tasks.store import TaskStore

from .models import (
    ArtifactInventory,
    ControlEvent,
    GateRecord,
    InvalidationReceipt,
    ProtocolRunRecord,
    RegistrationReceipt,
    RunSettlementReceipt,
    StageRecord,
    TaskRecord,
    TaskTransitionCause,
    TaskTransitionReceipt,
)
from .schema import initialize_control_plane_schema


class ControlPlaneNotFoundError(LookupError):
    pass


class ControlPlaneConflictError(RuntimeError):
    pass


class ControlPlaneValidationError(ValueError):
    pass


PROJECTION_COLLECTION_LIMIT = 200


class ControlPlaneStore:
    """Additive v2 persistence that leaves the 0.5 Task/Run rows intact."""

    _OPERATION_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")

    def __init__(self, tasks: TaskStore):
        self.tasks = tasks
        with closing(tasks._connect()) as db:
            initialize_attention_schema(db)
            initialize_control_plane_schema(db)
            db.commit()

    def ensure_task_state(
        self,
        task_id: str,
        *,
        actor: str = "system",
    ) -> TaskRecord:
        """Create the frozen Task state at backlog without reading legacy state."""

        self._validate_actor(actor)
        now = utc_now()
        with self.tasks._transaction() as db:
            task = self._task_row(db, task_id)
            existing = db.execute(
                "SELECT * FROM control_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if existing is not None:
                return self._task_record(existing)
            db.execute(
                """
                INSERT INTO control_tasks (
                    task_id, project_id, status, version, created_at, updated_at
                ) VALUES (?, ?, ?, 1, ?, ?)
                """,
                (
                    task_id,
                    task["project_id"],
                    TaskStatus.BACKLOG.value,
                    now,
                    now,
                ),
            )
            self._event(
                db,
                event_key=f"task.state.initialize:{task_id}",
                task_id=task_id,
                project_id=task["project_id"],
                event_type="task.state_initialized",
                actor=actor,
                payload={"status": TaskStatus.BACKLOG.value, "version": 1},
                now=now,
            )
            row = db.execute(
                "SELECT * FROM control_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            assert row is not None
            return self._task_record(row)

    def transition_task_state(
        self,
        task_id: str,
        target: TaskStatus,
        *,
        expected_version: int,
        cause: TaskTransitionCause,
        actor: str,
        reason: str,
        operation_key: str,
    ) -> TaskTransitionReceipt:
        """Persist one explicit, optimistic, replay-safe frozen Task transition."""

        self._validate_operation_key(operation_key)
        self._validate_actor(actor)
        reason = reason.strip()
        if not reason or len(reason) > 4_000:
            raise ControlPlaneValidationError(
                "Task transition reason must contain between 1 and 4000 characters"
            )
        fingerprint = canonical_sha256(
            {
                "action": "transition_task_state",
                "task_id": task_id,
                "target": target.value,
                "expected_version": expected_version,
                "cause": cause.value,
                "actor": actor,
                "reason": reason,
            }
        )
        now = utc_now()
        with self.tasks._transaction() as db:
            replay = self._operation_result(db, operation_key, fingerprint)
            if replay is not None:
                receipt = TaskTransitionReceipt.model_validate(replay["receipt"])
                return receipt.model_copy(update={"replayed": True})
            row = db.execute(
                "SELECT * FROM control_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                if db.execute(
                    "SELECT 1 FROM tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone() is None:
                    raise ControlPlaneNotFoundError(f"Task not found: {task_id}")
                raise ControlPlaneConflictError(
                    "Frozen Task state is not initialized"
                )
            if row["version"] != expected_version:
                raise ControlPlaneConflictError(
                    f"Expected Task version {expected_version}, current version is "
                    f"{row['version']}"
                )
            current = TaskStatus(row["status"])
            if (
                current == TaskStatus.COMPLETED
                and target == TaskStatus.ACTIVE
                and cause not in {
                    TaskTransitionCause.INVALIDATION,
                    TaskTransitionCause.RECONCILIATION,
                }
            ):
                raise ControlPlaneConflictError(
                    "A completed Task may reopen only through invalidation or reconciliation"
                )
            try:
                next_status = validate_task_transition(current, target)
            except TransitionError as exc:
                raise ControlPlaneConflictError(str(exc)) from exc
            next_version = expected_version + 1
            cursor = db.execute(
                """
                UPDATE control_tasks
                SET status = ?, version = ?, updated_at = ?
                WHERE task_id = ? AND status = ? AND version = ?
                """,
                (
                    next_status.value,
                    next_version,
                    now,
                    task_id,
                    current.value,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise ControlPlaneConflictError("Task changed during transition")
            self._event(
                db,
                event_key=f"{operation_key}:task.state_changed",
                task_id=task_id,
                project_id=row["project_id"],
                event_type="task.state_changed",
                actor=actor,
                payload={
                    "from": current.value,
                    "to": next_status.value,
                    "cause": cause.value,
                    "reason": reason,
                    "version": next_version,
                },
                now=now,
            )
            updated = db.execute(
                "SELECT * FROM control_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            assert updated is not None
            receipt = TaskTransitionReceipt(
                task=self._task_record(updated),
                previous_status=current,
                cause=cause,
            )
            self._complete_operation(
                db,
                operation_key,
                fingerprint,
                {"receipt": receipt.model_dump(mode="json")},
                now,
            )
            return receipt

    def ensure_stage_inventory(
        self,
        inventory: StageInventory,
        *,
        actor: str = "system",
    ) -> StageInventory:
        """Persist one immutable, hash-sealed grouped Stage inventory per Task."""

        self._validate_actor(actor)
        now = utc_now()
        with self.tasks._transaction() as db:
            task = self._task_row(db, inventory.task_id)
            self._assert_project(task, inventory.project_id)
            if db.execute(
                "SELECT 1 FROM control_tasks WHERE task_id = ?",
                (inventory.task_id,),
            ).fetchone() is None:
                raise ControlPlaneConflictError(
                    "Frozen Task state must be initialized before its Stage inventory"
                )
            existing = db.execute(
                "SELECT * FROM control_stage_inventories WHERE task_id = ?",
                (inventory.task_id,),
            ).fetchone()
            if existing is not None:
                current = self._stage_inventory(existing)
                if current.content_sha256 != inventory.content_sha256:
                    raise ControlPlaneConflictError(
                        "Task already has a different immutable Stage inventory"
                    )
                return current
            db.execute(
                """
                INSERT INTO control_stage_inventories (
                    task_id, project_id, inventory_id, payload,
                    content_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    inventory.task_id,
                    inventory.project_id,
                    inventory.inventory_id,
                    self._json(inventory),
                    inventory.content_sha256,
                    now,
                ),
            )
            stage_count = sum(len(group.stages) for group in inventory.groups)
            self._event(
                db,
                event_key=f"stage.inventory.initialize:{inventory.task_id}",
                task_id=inventory.task_id,
                project_id=inventory.project_id,
                event_type="stage.inventory_initialized",
                actor=actor,
                payload={
                    "inventory_id": inventory.inventory_id,
                    "content_sha256": inventory.content_sha256,
                    "methodology_id": inventory.methodology_id,
                    "methodology_version": inventory.methodology_version,
                    "methodology_sha256": inventory.methodology_sha256,
                    "provisional": inventory.provisional,
                    "group_count": len(inventory.groups),
                    "stage_count": stage_count,
                },
                now=now,
            )
            row = db.execute(
                "SELECT * FROM control_stage_inventories WHERE task_id = ?",
                (inventory.task_id,),
            ).fetchone()
            assert row is not None
            return self._stage_inventory(row)

    def ensure_stage(
        self,
        *,
        task_id: str,
        stage_key: str,
        gate_key: str,
        status: StageStatus = StageStatus.READY,
        actor: str = "system",
    ) -> StageRecord:
        now = utc_now()
        with self.tasks._transaction() as db:
            task = self._task_row(db, task_id)
            row = self._ensure_stage_row(
                db,
                task=task,
                stage_key=stage_key,
                gate_key=gate_key,
                status=status,
                actor=actor,
                now=now,
            )
            return self._stage(row)

    def configure_gate(
        self,
        *,
        task_id: str,
        gate_key: str,
        stage_key: str,
        requirements: list[GateRequirement],
        actor: str = "system",
    ) -> GateRecord:
        if not requirements:
            raise ControlPlaneValidationError("Gate requires at least one requirement")
        requirement_ids = sorted(item.requirement_id for item in requirements)
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ControlPlaneValidationError("Gate requirement ids must be unique")
        scopes = {
            (item.repository_id, item.ref, item.commit_sha)
            for item in requirements
        }
        if len(scopes) != 1:
            raise ControlPlaneValidationError(
                "Agora 1.0 gates require one repository/ref/commit scope"
            )
        now = utc_now()
        canonical_requirements = sorted(requirements, key=lambda item: item.requirement_id)
        fingerprint = canonical_sha256(canonical_requirements)
        with self.tasks._transaction() as db:
            task = self._task_row(db, task_id)
            self._ensure_stage_row(
                db,
                task=task,
                stage_key=stage_key,
                gate_key=gate_key,
                status=StageStatus.READY,
                actor=actor,
                now=now,
            )
            existing = db.execute(
                "SELECT * FROM control_gates WHERE task_id = ? AND gate_key = ?",
                (task_id, gate_key),
            ).fetchone()
            if existing:
                rows = db.execute(
                    """
                    SELECT payload_sha256 FROM control_gate_requirements
                    WHERE task_id = ? AND gate_key = ?
                    ORDER BY requirement_id
                    """,
                    (task_id, gate_key),
                ).fetchall()
                existing_fingerprint = canonical_sha256([row["payload_sha256"] for row in rows])
                expected_fingerprint = canonical_sha256(
                    [canonical_sha256(item) for item in canonical_requirements]
                )
                if (
                    existing["stage_key"] != stage_key
                    or existing_fingerprint != expected_fingerprint
                ):
                    raise ControlPlaneConflictError(
                        "Gate already exists with a different Stage or requirements"
                    )
                return self._gate_record(db, existing)
            db.execute(
                """
                INSERT INTO control_gates (
                    task_id, project_id, gate_key, stage_key, status,
                    version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    task_id,
                    task["project_id"],
                    gate_key,
                    stage_key,
                    GateStatus.PENDING.value,
                    now,
                    now,
                ),
            )
            for requirement in canonical_requirements:
                payload = self._json(requirement)
                db.execute(
                    """
                    INSERT INTO control_gate_requirements (
                        task_id, gate_key, requirement_id, payload,
                        payload_sha256, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        gate_key,
                        requirement.requirement_id,
                        payload,
                        canonical_sha256(requirement),
                        now,
                    ),
                )
            self._event(
                db,
                event_key=f"gate.configure:{task_id}:{gate_key}:{fingerprint}",
                task_id=task_id,
                project_id=task["project_id"],
                event_type="gate.configured",
                actor=actor,
                payload={
                    "gate_key": gate_key,
                    "stage_key": stage_key,
                    "requirement_ids": requirement_ids,
                },
                now=now,
            )
            row = db.execute(
                "SELECT * FROM control_gates WHERE task_id = ? AND gate_key = ?",
                (task_id, gate_key),
            ).fetchone()
            assert row is not None
            return self._gate_record(db, row)

    def start_protocol_run(
        self,
        context_pack: ContextPack,
        *,
        gate_key: str,
        actor: str,
        operation_key: str,
    ) -> ProtocolRunRecord:
        """Persist one sealed Context Pack and authoritatively start its Stage."""

        self._validate_operation_key(operation_key)
        fingerprint = canonical_sha256(
            {
                "action": "start_protocol_run",
                "context_pack": context_pack,
                "gate_key": gate_key,
            }
        )
        now = utc_now()
        with self.tasks._transaction() as db:
            replay = self._operation_result(db, operation_key, fingerprint)
            if replay is not None:
                return ProtocolRunRecord.model_validate(replay["run"])

            task = self._task_row(db, context_pack.task_id)
            self._assert_project(task, context_pack.project_id)
            stage = db.execute(
                """
                SELECT * FROM control_stages
                WHERE task_id = ? AND stage_key = ?
                """,
                (context_pack.task_id, context_pack.stage_key),
            ).fetchone()
            if stage is None:
                raise ControlPlaneNotFoundError(
                    f"Stage not found: {context_pack.stage_key}"
                )
            gate = self._gate_row(db, context_pack.task_id, gate_key)
            if stage["gate_key"] != gate_key or gate["stage_key"] != context_pack.stage_key:
                raise ControlPlaneValidationError(
                    "Context Pack Stage does not match its configured Gate"
                )
            current_stage = StageStatus(stage["status"])
            if current_stage != StageStatus.READY:
                raise ControlPlaneConflictError(
                    f"Protocol Run requires a ready Stage, current status is "
                    f"{current_stage.value}"
                )
            self._assert_context_inputs(db, context_pack)
            existing = db.execute(
                """
                SELECT run_id, context_pack_id FROM protocol_runs
                WHERE run_id = ? OR context_pack_id = ?
                """,
                (context_pack.run_id, context_pack.pack_id),
            ).fetchone()
            if existing is not None:
                raise ControlPlaneConflictError(
                    "Protocol Run or Context Pack identity already exists"
                )

            db.execute(
                """
                INSERT INTO protocol_runs (
                    run_id, project_id, task_id, stage_key, gate_key,
                    context_pack_id, context_payload, context_sha256,
                    attention_required, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    context_pack.run_id,
                    context_pack.project_id,
                    context_pack.task_id,
                    context_pack.stage_key,
                    gate_key,
                    context_pack.pack_id,
                    self._json(context_pack),
                    context_pack.content_sha256,
                    now,
                ),
            )
            next_stage = transition_stage(current_stage, StageStatus.RUNNING)
            cursor = db.execute(
                """
                UPDATE control_stages
                SET status = ?, version = version + 1, updated_at = ?
                WHERE task_id = ? AND stage_key = ? AND version = ?
                """,
                (
                    next_stage.value,
                    now,
                    context_pack.task_id,
                    context_pack.stage_key,
                    stage["version"],
                ),
            )
            if cursor.rowcount != 1:
                raise ControlPlaneConflictError("Stage changed before Run start")
            self._event(
                db,
                event_key=f"{operation_key}:run.context_sealed",
                task_id=context_pack.task_id,
                project_id=context_pack.project_id,
                event_type="run.context_sealed",
                actor=actor,
                payload={
                    "run_id": context_pack.run_id,
                    "stage_key": context_pack.stage_key,
                    "gate_key": gate_key,
                    "context_pack_id": context_pack.pack_id,
                    "context_sha256": context_pack.content_sha256,
                },
                now=now,
            )
            self._event(
                db,
                event_key=f"{operation_key}:stage.started",
                task_id=context_pack.task_id,
                project_id=context_pack.project_id,
                event_type="stage.started",
                actor=actor,
                payload={
                    "run_id": context_pack.run_id,
                    "stage_key": context_pack.stage_key,
                    "from": current_stage.value,
                    "to": next_stage.value,
                },
                now=now,
            )
            run = self._protocol_run(
                db.execute(
                    "SELECT * FROM protocol_runs WHERE run_id = ?",
                    (context_pack.run_id,),
                ).fetchone()
            )
            self._complete_operation(
                db,
                operation_key,
                fingerprint,
                {"run": run.model_dump(mode="json")},
                now,
            )
            return run

    def settle_protocol_run(
        self,
        result: AgentAdapterResult,
        *,
        actor: str,
        operation_key: str,
    ) -> RunSettlementReceipt:
        """Atomically persist a result, evaluate its Gate, and settle its Stage."""

        self._validate_operation_key(operation_key)
        fingerprint = canonical_sha256(
            {"action": "settle_protocol_run", "result": result}
        )
        now = utc_now()
        with self.tasks._transaction() as db:
            replay = self._operation_result(db, operation_key, fingerprint)
            if replay is not None:
                return RunSettlementReceipt.model_validate(
                    {**replay["receipt"], "replayed": True}
                )

            row = db.execute(
                "SELECT * FROM protocol_runs WHERE run_id = ?",
                (result.protocol_state.run_id,),
            ).fetchone()
            if row is None:
                raise ControlPlaneNotFoundError(
                    f"Protocol Run not found: {result.protocol_state.run_id}"
                )
            if row["settled_at"] is not None:
                raise ControlPlaneConflictError("Protocol Run is already settled")
            persisted_run = self._protocol_run(row)
            context_pack = persisted_run.context_pack
            handoff = result.handoff_pack
            if handoff is not None:
                self._assert_handoff_matches_context(context_pack, handoff)
                if handoff.stage_result.value != result.protocol_state.semantic_stage_result.value:
                    raise ControlPlaneValidationError(
                        "Handoff semantic result does not match Run protocol state"
                    )

            stage = db.execute(
                """
                SELECT * FROM control_stages
                WHERE task_id = ? AND stage_key = ?
                """,
                (row["task_id"], row["stage_key"]),
            ).fetchone()
            assert stage is not None
            current_stage = StageStatus(stage["status"])
            if current_stage != StageStatus.RUNNING:
                raise ControlPlaneConflictError(
                    f"Protocol Run settlement requires a running Stage, current status is "
                    f"{current_stage.value}"
                )

            artifact_ids: list[str] = []
            evidence_ids: list[str] = []
            active_evidence_ids: list[str] = []
            if handoff is not None:
                for artifact in handoff.output_artifacts:
                    self._register_artifact_tx(db, artifact, actor=actor)
                    artifact_ids.append(artifact.artifact_id)
                for evidence in handoff.evidence:
                    self._register_evidence_tx(db, evidence, actor=actor)
                    evidence_ids.append(evidence.evidence_id)
                if result.protocol_state.semantic_stage_result in {
                    SemanticStageResult.SUCCEEDED,
                    SemanticStageResult.BLOCKED,
                }:
                    active_evidence_ids = self._activate_handoff_evidence_tx(
                        db,
                        task_id=row["task_id"],
                        gate_key=row["gate_key"],
                        evidence=handoff.evidence,
                        actor=actor,
                        event_key=f"{operation_key}:gate.evidence_set",
                        now=now,
                    )
                    gate = self._evaluate_gate_tx(
                        db,
                        task_id=row["task_id"],
                        gate_key=row["gate_key"],
                        actor=actor,
                        event_key_prefix=operation_key,
                        now=now,
                    )
                else:
                    gate = self._gate_record(
                        db,
                        self._gate_row(db, row["task_id"], row["gate_key"]),
                    )
            else:
                gate = self._gate_record(
                    db,
                    self._gate_row(db, row["task_id"], row["gate_key"]),
                )

            target_stage = self._settled_stage_status(
                result.protocol_state.semantic_stage_result,
                gate.status,
            )
            next_stage = transition_stage(current_stage, target_stage)
            cursor = db.execute(
                """
                UPDATE control_stages
                SET status = ?, version = version + 1, updated_at = ?
                WHERE task_id = ? AND stage_key = ? AND version = ?
                """,
                (
                    next_stage.value,
                    now,
                    row["task_id"],
                    row["stage_key"],
                    stage["version"],
                ),
            )
            if cursor.rowcount != 1:
                raise ControlPlaneConflictError("Stage changed during Run settlement")

            attention_item_id = None
            if result.attention_required:
                attention_item_id = self._create_protocol_attention_tx(
                    db,
                    run_id=row["run_id"],
                    task_id=row["task_id"],
                    project_id=row["project_id"],
                    stage_key=row["stage_key"],
                    error_code=(
                        result.error_code.value if result.error_code else None
                    ),
                    actor=actor,
                    now=now,
                )

            cursor = db.execute(
                """
                UPDATE protocol_runs
                SET protocol_state_payload = ?, handoff_pack_id = ?,
                    handoff_payload = ?, handoff_sha256 = ?,
                    adapter_error_code = ?, attention_required = ?,
                    attention_item_id = ?, settled_at = ?
                WHERE run_id = ? AND settled_at IS NULL
                """,
                (
                    self._json(result.protocol_state),
                    handoff.pack_id if handoff else None,
                    self._json(handoff) if handoff else None,
                    handoff.content_sha256 if handoff else None,
                    result.error_code.value if result.error_code else None,
                    int(result.attention_required),
                    attention_item_id,
                    now,
                    row["run_id"],
                ),
            )
            if cursor.rowcount != 1:
                raise ControlPlaneConflictError("Protocol Run changed during settlement")

            self._event(
                db,
                event_key=f"{operation_key}:run.settled",
                task_id=row["task_id"],
                project_id=row["project_id"],
                event_type="run.settled",
                actor=actor,
                payload={
                    "run_id": row["run_id"],
                    "stage_key": row["stage_key"],
                    "gate_key": row["gate_key"],
                    "semantic_stage_result": (
                        result.protocol_state.semantic_stage_result.value
                    ),
                    "gate_status": gate.status.value,
                    "handoff_pack_id": handoff.pack_id if handoff else None,
                    "attention_required": result.attention_required,
                },
                now=now,
            )
            self._event(
                db,
                event_key=f"{operation_key}:stage.settled",
                task_id=row["task_id"],
                project_id=row["project_id"],
                event_type="stage.settled",
                actor=actor,
                payload={
                    "run_id": row["run_id"],
                    "stage_key": row["stage_key"],
                    "from": current_stage.value,
                    "to": next_stage.value,
                    "gate_status": gate.status.value,
                },
                now=now,
            )
            run = self._protocol_run(
                db.execute(
                    "SELECT * FROM protocol_runs WHERE run_id = ?",
                    (row["run_id"],),
                ).fetchone()
            )
            updated_stage = self._stage(
                db.execute(
                    """
                    SELECT * FROM control_stages
                    WHERE task_id = ? AND stage_key = ?
                    """,
                    (row["task_id"], row["stage_key"]),
                ).fetchone()
            )
            receipt = RunSettlementReceipt(
                run=run,
                stage=updated_stage,
                gate=gate,
                artifact_ids=sorted(artifact_ids),
                evidence_ids=sorted(evidence_ids),
                active_evidence_ids=active_evidence_ids,
            )
            self._complete_operation(
                db,
                operation_key,
                fingerprint,
                {"receipt": receipt.model_dump(mode="json")},
                now,
            )
            return receipt

    def prepare_protocol_retry(
        self,
        *,
        task_id: str,
        stage_key: str,
        actor: str,
        operation_key: str,
    ) -> StageRecord:
        """Move a settled retryable Stage back to ready without dispatching."""

        self._validate_operation_key(operation_key)
        fingerprint = canonical_sha256(
            {
                "action": "prepare_protocol_retry",
                "task_id": task_id,
                "stage_key": stage_key,
            }
        )
        now = utc_now()
        with self.tasks._transaction() as db:
            replay = self._operation_result(db, operation_key, fingerprint)
            if replay is not None:
                return StageRecord.model_validate(replay["stage"])
            task = self._task_row(db, task_id)
            stage = db.execute(
                """SELECT * FROM control_stages
                   WHERE task_id = ? AND stage_key = ?""",
                (task_id, stage_key),
            ).fetchone()
            if stage is None:
                raise ControlPlaneNotFoundError(f"Stage not found: {stage_key}")
            current = StageStatus(stage["status"])
            if current not in {StageStatus.BLOCKED, StageStatus.FAILED}:
                raise ControlPlaneConflictError(
                    f"Protocol retry requires a blocked or failed Stage, current status is "
                    f"{current.value}"
                )
            target = transition_stage(current, StageStatus.READY)
            cursor = db.execute(
                """UPDATE control_stages
                   SET status = ?, version = version + 1, updated_at = ?
                   WHERE task_id = ? AND stage_key = ? AND version = ?""",
                (target.value, now, task_id, stage_key, stage["version"]),
            )
            if cursor.rowcount != 1:
                raise ControlPlaneConflictError("Stage changed during protocol retry")
            gate = self._gate_row(db, task_id, stage["gate_key"])
            gate_status = GateStatus(gate["status"])
            if gate_status == GateStatus.PASSED:
                stale = transition_gate(gate_status, GateStatus.STALE)
                cursor = db.execute(
                    """UPDATE control_gates
                       SET status = ?, version = version + 1, updated_at = ?
                       WHERE task_id = ? AND gate_key = ? AND version = ?""",
                    (
                        stale.value,
                        now,
                        task_id,
                        gate["gate_key"],
                        gate["version"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise ControlPlaneConflictError(
                        "Gate changed during protocol retry"
                    )
            self._event(
                db,
                event_key=f"{operation_key}:stage.retry_ready",
                task_id=task_id,
                project_id=task["project_id"],
                event_type="stage.retry_ready",
                actor=actor,
                payload={
                    "stage_key": stage_key,
                    "gate_key": stage["gate_key"],
                    "from": current.value,
                    "to": target.value,
                    "gate_staled": gate_status == GateStatus.PASSED,
                },
                now=now,
            )
            updated = self._stage(
                db.execute(
                    """SELECT * FROM control_stages
                       WHERE task_id = ? AND stage_key = ?""",
                    (task_id, stage_key),
                ).fetchone()
            )
            self._complete_operation(
                db,
                operation_key,
                fingerprint,
                {"stage": updated.model_dump(mode="json")},
                now,
            )
            return updated

    def register_artifact(
        self,
        artifact: Artifact,
        *,
        actor: str = "runtime",
    ) -> RegistrationReceipt:
        with self.tasks._transaction() as db:
            return self._register_artifact_tx(db, artifact, actor=actor)

    def register_evidence(
        self,
        evidence: Evidence,
        *,
        actor: str = "runtime",
    ) -> RegistrationReceipt:
        with self.tasks._transaction() as db:
            return self._register_evidence_tx(db, evidence, actor=actor)

    def register_approval(
        self,
        approval: Approval,
        *,
        actor: str | None = None,
    ) -> RegistrationReceipt:
        payload = self._json(approval)
        payload_sha256 = canonical_sha256(approval)
        now = approval.approved_at.isoformat()
        with self.tasks._transaction() as db:
            task = self._task_row(db, approval.task_id)
            self._assert_project(task, approval.project_id)
            gate = self._gate_row(db, approval.task_id, approval.gate_key)
            if gate["stage_key"] != approval.stage_key:
                raise ControlPlaneValidationError(
                    "Approval Stage does not match its configured Gate"
                )
            existing = db.execute(
                "SELECT payload_sha256 FROM protocol_approvals WHERE approval_id = ?",
                (approval.approval_id,),
            ).fetchone()
            if existing:
                if existing["payload_sha256"] != payload_sha256:
                    raise ControlPlaneConflictError(
                        "Approval id already exists with different content"
                    )
                return RegistrationReceipt(
                    entity_id=approval.approval_id,
                    created=False,
                )
            for binding in approval.artifact_versions:
                registered = db.execute(
                    """
                    SELECT 1 FROM protocol_artifacts
                    WHERE repository_id = ? AND ref = ? AND commit_sha = ?
                      AND path = ? AND sha256 = ?
                      AND task_id = ? AND project_id = ?
                    LIMIT 1
                    """,
                    (
                        binding.repository_id,
                        binding.ref,
                        binding.commit_sha,
                        binding.path,
                        binding.sha256,
                        approval.task_id,
                        approval.project_id,
                    ),
                ).fetchone()
                if not registered:
                    raise ControlPlaneValidationError(
                        f"Approval artifact is not registered: {binding.path}"
                    )
            db.execute(
                """
                INSERT INTO protocol_approvals (
                    approval_id, project_id, task_id, stage_key, gate_key,
                    repository_id, ref, commit_sha, status, payload,
                    payload_sha256, approved_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval.approval_id,
                    approval.project_id,
                    approval.task_id,
                    approval.stage_key,
                    approval.gate_key,
                    approval.repository_id,
                    approval.ref,
                    approval.commit_sha,
                    approval.status.value,
                    payload,
                    payload_sha256,
                    now,
                    now,
                ),
            )
            db.executemany(
                """
                INSERT INTO protocol_approval_artifacts (
                    approval_id, repository_id, ref, commit_sha, path, sha256
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        approval.approval_id,
                        binding.repository_id,
                        binding.ref,
                        binding.commit_sha,
                        binding.path,
                        binding.sha256,
                    )
                    for binding in approval.artifact_versions
                ],
            )
            self._event(
                db,
                event_key=f"approval.register:{approval.approval_id}:{payload_sha256}",
                task_id=approval.task_id,
                project_id=approval.project_id,
                event_type="approval.registered",
                actor=actor or approval.approved_by,
                payload={
                    "approval_id": approval.approval_id,
                    "gate_key": approval.gate_key,
                    "artifact_count": len(approval.artifact_versions),
                },
                now=now,
            )
        return RegistrationReceipt(entity_id=approval.approval_id, created=True)

    def set_active_evidence(
        self,
        *,
        task_id: str,
        gate_key: str,
        evidence_ids: list[str],
        expected_gate_version: int,
        actor: str,
        operation_key: str,
    ) -> GateRecord:
        self._validate_operation_key(operation_key)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ControlPlaneValidationError("Active evidence ids must be unique")
        canonical_ids = sorted(evidence_ids)
        fingerprint = canonical_sha256(
            {
                "action": "set_active_evidence",
                "task_id": task_id,
                "gate_key": gate_key,
                "evidence_ids": canonical_ids,
                "expected_gate_version": expected_gate_version,
            }
        )
        now = utc_now()
        with self.tasks._transaction() as db:
            replay = self._operation_result(db, operation_key, fingerprint)
            if replay is not None:
                return GateRecord.model_validate(replay["gate"])
            gate = self._gate_row(db, task_id, gate_key)
            if gate["version"] != expected_gate_version:
                raise ControlPlaneConflictError(
                    f"Expected Gate version {expected_gate_version}, "
                    f"current version is {gate['version']}"
                )
            requirements = {
                item.requirement_id: item
                for item in self._gate_requirements(db, task_id, gate_key)
            }
            evidence_items: list[Evidence] = []
            for evidence_id in canonical_ids:
                item = self._evidence_row(db, evidence_id)
                evidence = Evidence.model_validate_json(item["payload"])
                if (
                    evidence.task_id != task_id
                    or evidence.project_id != gate["project_id"]
                ):
                    raise ControlPlaneValidationError(
                        f"Evidence {evidence_id} does not belong to the Gate Task"
                    )
                requirement = requirements.get(evidence.requirement_id)
                if requirement is None:
                    raise ControlPlaneValidationError(
                        f"Evidence {evidence_id} targets an unknown Gate requirement"
                    )
                if (
                    evidence.repository_id != requirement.repository_id
                    or evidence.ref != requirement.ref
                    or evidence.commit_sha != requirement.commit_sha
                    or evidence.kind != requirement.evidence_kind
                ):
                    raise ControlPlaneValidationError(
                        f"Evidence {evidence_id} does not match the Gate requirement scope"
                    )
                evidence_items.append(evidence)
            db.execute(
                """
                UPDATE control_gate_evidence
                SET active = 0, deactivated_at = ?
                WHERE task_id = ? AND gate_key = ? AND active = 1
                """,
                (now, task_id, gate_key),
            )
            for evidence in evidence_items:
                db.execute(
                    """
                    INSERT INTO control_gate_evidence (
                        task_id, gate_key, evidence_id, requirement_id,
                        active, activated_at, deactivated_at
                    ) VALUES (?, ?, ?, ?, 1, ?, NULL)
                    ON CONFLICT(task_id, gate_key, evidence_id) DO UPDATE SET
                        requirement_id = excluded.requirement_id,
                        active = 1,
                        activated_at = excluded.activated_at,
                        deactivated_at = NULL
                    """,
                    (
                        task_id,
                        gate_key,
                        evidence.evidence_id,
                        evidence.requirement_id,
                        now,
                    ),
                )
            current_status = GateStatus(gate["status"])
            if current_status == GateStatus.EVALUATING:
                raise ControlPlaneConflictError(
                    "Cannot replace Evidence while the Gate is evaluating"
                )
            if current_status == GateStatus.PASSED:
                next_status = transition_gate(current_status, GateStatus.STALE)
            else:
                next_status = current_status
            cursor = db.execute(
                """
                UPDATE control_gates
                SET status = ?, version = version + 1,
                    last_evaluation = NULL, updated_at = ?
                WHERE task_id = ? AND gate_key = ? AND version = ?
                """,
                (
                    next_status.value,
                    now,
                    task_id,
                    gate_key,
                    expected_gate_version,
                ),
            )
            if cursor.rowcount != 1:
                raise ControlPlaneConflictError(
                    "Gate changed during Evidence selection"
                )
            event = self._event(
                db,
                event_key=f"{operation_key}:gate.evidence_set",
                task_id=task_id,
                project_id=gate["project_id"],
                event_type="gate.evidence_set",
                actor=actor,
                payload={
                    "gate_key": gate_key,
                    "evidence_ids": canonical_ids,
                    "status": next_status.value,
                },
                now=now,
            )
            updated_gate = self._gate_record(
                db,
                self._gate_row(db, task_id, gate_key),
            )
            self._complete_operation(
                db,
                operation_key,
                fingerprint,
                {
                    "event_id": event.event_id,
                    "gate": updated_gate.model_dump(mode="json"),
                },
                now,
            )
            return updated_gate

    def evaluate(
        self,
        *,
        task_id: str,
        gate_key: str,
        expected_gate_version: int,
        actor: str,
        operation_key: str,
    ) -> GateRecord:
        self._validate_operation_key(operation_key)
        fingerprint = canonical_sha256(
            {
                "action": "evaluate_gate",
                "task_id": task_id,
                "gate_key": gate_key,
                "expected_gate_version": expected_gate_version,
            }
        )
        now = utc_now()
        with self.tasks._transaction() as db:
            replay = self._operation_result(db, operation_key, fingerprint)
            if replay is not None:
                return GateRecord.model_validate(replay["gate"])
            gate = self._gate_row(db, task_id, gate_key)
            if gate["version"] != expected_gate_version:
                raise ControlPlaneConflictError(
                    f"Expected Gate version {expected_gate_version}, "
                    f"current version is {gate['version']}"
                )
            requirements = self._gate_requirements(db, task_id, gate_key)
            evidence = self._active_evidence(db, task_id, gate_key)
            current_status = GateStatus(gate["status"])
            try:
                evaluating_status = transition_gate(
                    current_status,
                    GateStatus.EVALUATING,
                )
            except TransitionError as exc:
                raise ControlPlaneConflictError(
                    f"Gate cannot be evaluated from {current_status.value}"
                ) from exc
            cursor = db.execute(
                """
                UPDATE control_gates
                SET status = ?, version = version + 1, updated_at = ?
                WHERE task_id = ? AND gate_key = ? AND version = ?
                """,
                (
                    evaluating_status.value,
                    now,
                    task_id,
                    gate_key,
                    expected_gate_version,
                ),
            )
            if cursor.rowcount != 1:
                raise ControlPlaneConflictError(
                    "Gate changed before evaluation started"
                )
            started_event = self._event(
                db,
                event_key=f"{operation_key}:gate.evaluation_started",
                task_id=task_id,
                project_id=gate["project_id"],
                event_type="gate.evaluation_started",
                actor=actor,
                payload={
                    "gate_key": gate_key,
                    "from": current_status.value,
                    "to": evaluating_status.value,
                },
                now=now,
            )
            result = evaluate_gate(requirements, evidence)
            next_status = (
                GateStatus.PASSED
                if result.decision.value == "pass"
                else GateStatus.BLOCKED
            )
            next_status = transition_gate(evaluating_status, next_status)
            cursor = db.execute(
                """
                UPDATE control_gates
                SET status = ?, version = version + 1,
                    last_evaluation = ?, updated_at = ?
                WHERE task_id = ? AND gate_key = ?
                  AND status = ? AND version = ?
                """,
                (
                    next_status.value,
                    self._json(result),
                    now,
                    task_id,
                    gate_key,
                    evaluating_status.value,
                    expected_gate_version + 1,
                ),
            )
            if cursor.rowcount != 1:
                raise ControlPlaneConflictError("Gate changed during evaluation")
            event = self._event(
                db,
                event_key=f"{operation_key}:gate.evaluated",
                task_id=task_id,
                project_id=gate["project_id"],
                event_type="gate.evaluated",
                actor=actor,
                payload={
                    "gate_key": gate_key,
                    "status": next_status.value,
                    "decision": result.decision.value,
                    "blocker_requirement_ids": result.blocker_requirement_ids,
                    "next_safe_action": result.next_safe_action,
                },
                now=now,
            )
            updated_gate = self._gate_record(
                db,
                self._gate_row(db, task_id, gate_key),
            )
            self._complete_operation(
                db,
                operation_key,
                fingerprint,
                {
                    "started_event_id": started_event.event_id,
                    "event_id": event.event_id,
                    "gate": updated_gate.model_dump(mode="json"),
                },
                now,
            )
            return updated_gate

    def invalidate_inventory(
        self,
        inventory: ArtifactInventory,
        *,
        stage_dependents: dict[str, set[str]],
        actor: str,
        operation_key: str,
    ) -> InvalidationReceipt:
        self._validate_operation_key(operation_key)
        canonical_dependents = {
            key: sorted(values)
            for key, values in sorted(stage_dependents.items())
        }
        fingerprint = canonical_sha256(
            {
                "action": "invalidate_inventory",
                "inventory": inventory,
                "stage_dependents": canonical_dependents,
            }
        )
        now = utc_now()
        with self.tasks._transaction() as db:
            replay = self._operation_result(db, operation_key, fingerprint)
            if replay is not None:
                return InvalidationReceipt.model_validate(
                    {**replay, "replayed": True}
                )
            rows = db.execute(
                """
                SELECT * FROM protocol_approvals
                WHERE repository_id = ? AND ref = ? AND status = 'active'
                ORDER BY task_id, approval_id
                """,
                (inventory.repository_id, inventory.ref),
            ).fetchall()
            approvals_by_task: dict[str, list[Approval]] = defaultdict(list)
            for row in rows:
                approvals_by_task[row["task_id"]].append(
                    Approval.model_validate_json(row["payload"])
                )

            stale_approval_ids: list[str] = []
            stale_gate_keys: list[str] = []
            reopened_stage_keys: list[str] = []
            reconciliation_stage_keys: list[str] = []
            attention_item_ids: list[str] = []
            event_ids: list[str] = []

            for task_id in sorted(approvals_by_task):
                approvals = approvals_by_task[task_id]
                task = self._task_row(db, task_id)
                plan = invalidate_approvals(
                    approvals,
                    inventory.artifacts,
                    stage_dependents=stage_dependents,
                )
                updated = {item.approval_id: item for item in plan.approvals}
                for approval_id in plan.stale_approval_ids:
                    approval = updated[approval_id]
                    cursor = db.execute(
                        """
                        UPDATE protocol_approvals
                        SET status = ?, payload = ?, payload_sha256 = ?, updated_at = ?
                        WHERE approval_id = ? AND status = 'active'
                        """,
                        (
                            approval.status.value,
                            self._json(approval),
                            canonical_sha256(approval),
                            now,
                            approval_id,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise ControlPlaneConflictError(
                            f"Approval changed during invalidation: {approval_id}"
                        )
                    stale_approval_ids.append(approval_id)
                    event_ids.append(
                        self._event(
                            db,
                            event_key=f"{operation_key}:approval:{approval_id}",
                            task_id=task_id,
                            project_id=task["project_id"],
                            event_type="approval.stale",
                            actor=actor,
                            payload={
                                "approval_id": approval_id,
                                "reason": approval.stale_reason,
                                "inventory_commit_sha": inventory.commit_sha,
                            },
                            now=now,
                        ).event_id
                    )

                for gate_key in plan.stale_gate_keys:
                    gate = self._gate_row(db, task_id, gate_key)
                    if gate["status"] == GateStatus.PASSED.value:
                        stale_status = transition_gate(
                            GateStatus.PASSED,
                            GateStatus.STALE,
                        )
                        cursor = db.execute(
                            """
                            UPDATE control_gates
                            SET status = ?, version = version + 1,
                                updated_at = ?
                            WHERE task_id = ? AND gate_key = ?
                              AND status = ? AND version = ?
                            """,
                            (
                                stale_status.value,
                                now,
                                task_id,
                                gate_key,
                                GateStatus.PASSED.value,
                                gate["version"],
                            ),
                        )
                        if cursor.rowcount != 1:
                            raise ControlPlaneConflictError(
                                f"Gate changed during invalidation: {gate_key}"
                            )
                        stale_gate_keys.append(f"{task_id}:{gate_key}")
                    event_ids.append(
                        self._event(
                            db,
                            event_key=f"{operation_key}:gate:{task_id}:{gate_key}",
                            task_id=task_id,
                            project_id=task["project_id"],
                            event_type="gate.approval_invalidated",
                            actor=actor,
                            payload={
                                "gate_key": gate_key,
                                "previous_status": gate["status"],
                                "status": (
                                    GateStatus.STALE.value
                                    if gate["status"] == GateStatus.PASSED.value
                                    else gate["status"]
                                ),
                            },
                            now=now,
                        ).event_id
                    )

                for stage_key in plan.reopen_stage_keys:
                    row = db.execute(
                        """
                        SELECT * FROM control_stages
                        WHERE task_id = ? AND stage_key = ?
                        """,
                        (task_id, stage_key),
                    ).fetchone()
                    if row is None:
                        continue
                    current = StageStatus(row["status"])
                    path = self._invalidation_stage_path(current)
                    if not path:
                        continue
                    from_status = current
                    version = int(row["version"])
                    for index, target in enumerate(path):
                        transition_stage(from_status, target)
                        cursor = db.execute(
                            """
                            UPDATE control_stages
                            SET status = ?, version = version + 1, updated_at = ?
                            WHERE task_id = ? AND stage_key = ?
                              AND status = ? AND version = ?
                            """,
                            (
                                target.value,
                                now,
                                task_id,
                                stage_key,
                                from_status.value,
                                version,
                            ),
                        )
                        if cursor.rowcount != 1:
                            raise ControlPlaneConflictError(
                                f"Stage changed during invalidation: {stage_key}"
                            )
                        event_type = (
                            "stage.reconciliation_required"
                            if target == StageStatus.RECONCILIATION_REQUIRED
                            else (
                                "stage.reopened"
                                if target == StageStatus.READY
                                else "stage.invalidation_blocked"
                            )
                        )
                        event_ids.append(
                            self._event(
                                db,
                                event_key=(
                                    f"{operation_key}:stage:{task_id}:"
                                    f"{stage_key}:{index}"
                                ),
                                task_id=task_id,
                                project_id=task["project_id"],
                                event_type=event_type,
                                actor=actor,
                                payload={
                                    "stage_key": stage_key,
                                    "from": from_status.value,
                                    "to": target.value,
                                    "reason": "approval_invalidated",
                                },
                                now=now,
                            ).event_id
                        )
                        from_status = target
                        version += 1
                    target = path[-1]
                    qualified = f"{task_id}:{stage_key}"
                    if target == StageStatus.RECONCILIATION_REQUIRED:
                        reconciliation_stage_keys.append(qualified)
                    else:
                        reopened_stage_keys.append(qualified)

                if plan.stale_approval_ids:
                    attention_item_id, attention_event_id = (
                        self._create_invalidation_attention_tx(
                            db,
                            operation_key=operation_key,
                            task_id=task_id,
                            project_id=task["project_id"],
                            inventory=inventory,
                            stale_approval_ids=plan.stale_approval_ids,
                            stale_gate_keys=plan.stale_gate_keys,
                            reopen_stage_keys=plan.reopen_stage_keys,
                            actor=actor,
                            now=now,
                        )
                    )
                    attention_item_ids.append(attention_item_id)
                    event_ids.append(attention_event_id)

            result = InvalidationReceipt(
                operation_key=operation_key,
                stale_approval_ids=sorted(stale_approval_ids),
                stale_gate_keys=sorted(stale_gate_keys),
                reopened_stage_keys=sorted(reopened_stage_keys),
                reconciliation_stage_keys=sorted(reconciliation_stage_keys),
                attention_item_ids=sorted(attention_item_ids),
                event_ids=event_ids,
            )
            self._complete_operation(
                db,
                operation_key,
                fingerprint,
                result.model_dump(mode="json"),
                now,
            )
            return result

    def get_artifact(self, artifact_id: str, version: int) -> Artifact | None:
        with closing(self.tasks._connect()) as db:
            row = db.execute(
                """
                SELECT payload FROM protocol_artifacts
                WHERE artifact_id = ? AND version = ?
                """,
                (artifact_id, version),
            ).fetchone()
        return Artifact.model_validate_json(row["payload"]) if row else None

    def get_task_state(self, task_id: str) -> TaskRecord | None:
        with closing(self.tasks._connect()) as db:
            row = db.execute(
                "SELECT * FROM control_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return self._task_record(row) if row else None

    def get_stage_inventory(self, task_id: str) -> StageInventory | None:
        with closing(self.tasks._connect()) as db:
            return self.stage_inventory_snapshot(db, task_id)

    def stage_inventory_snapshot(
        self,
        db: sqlite3.Connection,
        task_id: str,
    ) -> StageInventory | None:
        """Read and revalidate an inventory inside a caller-owned snapshot."""

        row = db.execute(
            "SELECT * FROM control_stage_inventories WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return self._stage_inventory(row) if row else None

    def get_evidence(self, evidence_id: str) -> Evidence | None:
        with closing(self.tasks._connect()) as db:
            row = db.execute(
                "SELECT payload FROM protocol_evidence WHERE evidence_id = ?",
                (evidence_id,),
            ).fetchone()
        return Evidence.model_validate_json(row["payload"]) if row else None

    def get_approval(self, approval_id: str) -> Approval | None:
        with closing(self.tasks._connect()) as db:
            row = db.execute(
                "SELECT payload FROM protocol_approvals WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
        return Approval.model_validate_json(row["payload"]) if row else None

    def get_protocol_run(self, run_id: str) -> ProtocolRunRecord | None:
        with closing(self.tasks._connect()) as db:
            row = db.execute(
                "SELECT * FROM protocol_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return self._protocol_run(row) if row else None

    def get_stage(self, task_id: str, stage_key: str) -> StageRecord | None:
        with closing(self.tasks._connect()) as db:
            row = db.execute(
                "SELECT * FROM control_stages WHERE task_id = ? AND stage_key = ?",
                (task_id, stage_key),
            ).fetchone()
        return self._stage(row) if row else None

    def get_gate(self, task_id: str, gate_key: str) -> GateRecord | None:
        with closing(self.tasks._connect()) as db:
            row = db.execute(
                "SELECT * FROM control_gates WHERE task_id = ? AND gate_key = ?",
                (task_id, gate_key),
            ).fetchone()
            return self._gate_record(db, row) if row else None

    def events(self, task_id: str) -> list[ControlEvent]:
        if self.tasks.get(task_id) is None:
            raise ControlPlaneNotFoundError(task_id)
        with closing(self.tasks._connect()) as db:
            rows = db.execute(
                "SELECT * FROM control_events WHERE task_id = ? ORDER BY rowid",
                (task_id,),
            ).fetchall()
        return [self._control_event(row) for row in rows]

    def projection(
        self,
        task_id: str,
        *,
        event_limit: int = 100,
        event_offset: int = 0,
    ) -> dict[str, Any]:
        """Return a bounded, Task-scoped registry projection."""
        with closing(self.tasks._connect()) as db:
            db.execute("BEGIN")
            try:
                task_row = db.execute(
                    "SELECT * FROM tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                if task_row is None:
                    raise ControlPlaneNotFoundError(task_id)
                task = self.tasks._manifest(task_row)
                stage_rows = db.execute(
                    """
                    SELECT * FROM control_stages
                    WHERE task_id = ? ORDER BY stage_key LIMIT ?
                    """,
                    (task_id, PROJECTION_COLLECTION_LIMIT),
                ).fetchall()
                gate_rows = db.execute(
                    """
                    SELECT * FROM control_gates
                    WHERE task_id = ? ORDER BY gate_key LIMIT ?
                    """,
                    (task_id, PROJECTION_COLLECTION_LIMIT),
                ).fetchall()
                artifact_rows = db.execute(
                    """
                    SELECT payload FROM protocol_artifacts
                    WHERE task_id = ? ORDER BY created_at, artifact_id, version
                    LIMIT ?
                    """,
                    (task_id, PROJECTION_COLLECTION_LIMIT),
                ).fetchall()
                evidence_rows = db.execute(
                    """
                    SELECT payload FROM protocol_evidence
                    WHERE task_id = ? ORDER BY observed_at, evidence_id LIMIT ?
                    """,
                    (task_id, PROJECTION_COLLECTION_LIMIT),
                ).fetchall()
                approval_rows = db.execute(
                    """
                    SELECT payload FROM protocol_approvals
                    WHERE task_id = ? ORDER BY approved_at, approval_id LIMIT ?
                    """,
                    (task_id, PROJECTION_COLLECTION_LIMIT),
                ).fetchall()
                attention = AttentionStore.list_snapshot(
                    db,
                    task_id=task_id,
                    limit=PROJECTION_COLLECTION_LIMIT,
                )
                event_page = self._event_page(db, task_id, event_limit, event_offset)
                totals = {}
                for name, table in (
                    ("stages", "control_stages"),
                    ("gates", "control_gates"),
                    ("artifacts", "protocol_artifacts"),
                    ("evidence", "protocol_evidence"),
                    ("approvals", "protocol_approvals"),
                ):
                    totals[name] = db.execute(
                        f"SELECT COUNT(*) AS count FROM {table} WHERE task_id = ?",
                        (task_id,),
                    ).fetchone()["count"]
                totals["attention"] = db.execute(
                    "SELECT COUNT(*) AS count FROM attention_items WHERE task_id = ?",
                    (task_id,),
                ).fetchone()["count"]
                gates = [self._gate_record(db, row) for row in gate_rows]
                return {
                    "task": task.model_dump(mode="json"),
                    "budget": task.budget.model_dump(mode="json"),
                    "stages": [self._stage(row) for row in stage_rows],
                    "gates": gates,
                    "artifacts": [
                        Artifact.model_validate_json(row["payload"])
                        for row in artifact_rows
                    ],
                    "evidence": [
                        Evidence.model_validate_json(row["payload"])
                        for row in evidence_rows
                    ],
                    "approvals": [
                        Approval.model_validate_json(row["payload"])
                        for row in approval_rows
                    ],
                    "attention": attention,
                    "next_safe_action": self._next_safe_action(gates),
                    **event_page,
                    "collection_totals": totals,
                    "collection_pages": {
                        name: {
                            "limit": PROJECTION_COLLECTION_LIMIT,
                            "offset": 0,
                            "total": total,
                        }
                        for name, total in totals.items()
                    },
                }
            finally:
                db.rollback()

    def event_page(
        self,
        task_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        if self.tasks.get(task_id) is None:
            raise ControlPlaneNotFoundError(task_id)
        with closing(self.tasks._connect()) as db:
            return self._event_page(db, task_id, limit, offset)

    def _event_page(
        self,
        db: sqlite3.Connection,
        task_id: str,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        rows = db.execute(
            """
            SELECT * FROM control_events WHERE task_id = ?
            ORDER BY rowid LIMIT ? OFFSET ?
            """,
            (task_id, limit, offset),
        ).fetchall()
        total = db.execute(
            "SELECT COUNT(*) AS count FROM control_events WHERE task_id = ?",
            (task_id,),
        ).fetchone()["count"]
        return {
            "events": [self._control_event(row) for row in rows],
            "event_page": {"limit": limit, "offset": offset, "total": total},
        }

    @staticmethod
    def _next_safe_action(gates: list[GateRecord]) -> dict[str, str | None]:
        if not gates:
            return {
                "value": None,
                "source_gate_key": None,
                "unavailable_reason": "No Control Plane Gate is configured.",
            }
        candidates: list[tuple[int, str, str, str]] = []
        for gate in gates:
            evaluation = gate.last_evaluation
            if gate.status != GateStatus.BLOCKED or evaluation is None:
                continue
            value = evaluation.next_safe_action
            if value is None:
                continue
            blocker_ids = set(evaluation.blocker_requirement_ids)
            priority, requirement_id = min(
                (
                    (requirement.priority, requirement.requirement_id)
                    for requirement in gate.requirements
                    if requirement.requirement_id in blocker_ids
                ),
                default=(10_001, ""),
            )
            candidates.append((priority, requirement_id, gate.gate_key, value))
        if candidates:
            _, _, gate_key, value = min(candidates)
            return {
                "value": value,
                "source_gate_key": gate_key,
                "unavailable_reason": None,
            }
        if any(gate.status != GateStatus.PASSED for gate in gates):
            return {
                "value": None,
                "source_gate_key": None,
                "unavailable_reason": (
                    "No current blocked Gate evaluation has produced a "
                    "next-safe-action."
                ),
            }
        return {
            "value": None,
            "source_gate_key": None,
            "unavailable_reason": (
                "All configured Gates are passed; no subsequent authoritative "
                "Stage is configured."
            ),
        }

    def _ensure_stage_row(
        self,
        db: sqlite3.Connection,
        *,
        task: sqlite3.Row,
        stage_key: str,
        gate_key: str,
        status: StageStatus,
        actor: str,
        now: str,
    ) -> sqlite3.Row:
        task_id = task["task_id"]
        self._assert_inventory_stage(
            db,
            task_id=task_id,
            stage_key=stage_key,
            gate_key=gate_key,
        )
        existing = db.execute(
            "SELECT * FROM control_stages WHERE task_id = ? AND stage_key = ?",
            (task_id, stage_key),
        ).fetchone()
        if existing:
            if existing["gate_key"] != gate_key:
                raise ControlPlaneConflictError(
                    "Stage is already bound to a different gate"
                )
            return existing
        try:
            db.execute(
                """
                INSERT INTO control_stages (
                    task_id, project_id, stage_key, gate_key, status,
                    version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    task_id,
                    task["project_id"],
                    stage_key,
                    gate_key,
                    status.value,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ControlPlaneConflictError(
                "Gate key is already bound to another Stage"
            ) from exc
        self._event(
            db,
            event_key=f"stage.ensure:{task_id}:{stage_key}",
            task_id=task_id,
            project_id=task["project_id"],
            event_type="stage.created",
            actor=actor,
            payload={
                "stage_key": stage_key,
                "gate_key": gate_key,
                "status": status.value,
            },
            now=now,
        )
        row = db.execute(
            "SELECT * FROM control_stages WHERE task_id = ? AND stage_key = ?",
            (task_id, stage_key),
        ).fetchone()
        assert row is not None
        return row

    def _assert_inventory_stage(
        self,
        db: sqlite3.Connection,
        *,
        task_id: str,
        stage_key: str,
        gate_key: str,
    ) -> None:
        row = db.execute(
            "SELECT * FROM control_stage_inventories WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            return
        inventory = self._stage_inventory(row)
        stages = {
            stage.stage_key: stage
            for group in inventory.groups
            for stage in group.stages
        }
        stage = stages.get(stage_key)
        if stage is None:
            raise ControlPlaneConflictError(
                "Stage is not part of the immutable Task Stage inventory"
            )
        if stage.gate_key != gate_key:
            raise ControlPlaneConflictError(
                "Stage inventory binds the Stage to a different gate"
            )

    @staticmethod
    def _invalidation_stage_path(current: StageStatus) -> tuple[StageStatus, ...]:
        if current == StageStatus.RUNNING:
            return (StageStatus.RECONCILIATION_REQUIRED,)
        if current == StageStatus.NEEDS_REVIEW:
            return (StageStatus.BLOCKED, StageStatus.READY)
        if current in {
            StageStatus.PENDING,
            StageStatus.BLOCKED,
            StageStatus.RECONCILIATION_REQUIRED,
            StageStatus.COMPLETED,
            StageStatus.FAILED,
        }:
            return (StageStatus.READY,)
        return ()

    def _register_artifact_tx(
        self,
        db: sqlite3.Connection,
        artifact: Artifact,
        *,
        actor: str,
    ) -> RegistrationReceipt:
        payload = self._json(artifact)
        payload_sha256 = canonical_sha256(artifact)
        task = self._task_row(db, artifact.task_id)
        self._assert_project(task, artifact.project_id)
        self._assert_protocol_stage(
            db,
            task_id=artifact.task_id,
            stage_key=artifact.stage_key,
            producer_stage_key=artifact.producer.stage_key,
        )
        existing = db.execute(
            """
            SELECT payload_sha256 FROM protocol_artifacts
            WHERE artifact_id = ? AND version = ?
            """,
            (artifact.artifact_id, artifact.version),
        ).fetchone()
        if existing:
            if existing["payload_sha256"] != payload_sha256:
                raise ControlPlaneConflictError(
                    "Artifact id/version already exists with different content"
                )
            return RegistrationReceipt(
                entity_id=artifact.artifact_id,
                version=artifact.version,
                created=False,
            )
        location = artifact.location
        db.execute(
            """
            INSERT INTO protocol_artifacts (
                artifact_id, version, project_id, task_id, stage_key, run_id,
                kind, storage, sha256, repository_id, ref, commit_sha, path,
                payload, payload_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact.artifact_id,
                artifact.version,
                artifact.project_id,
                artifact.task_id,
                artifact.stage_key,
                artifact.producer.run_id,
                artifact.kind,
                artifact.storage.value,
                artifact.sha256,
                location.repository_id if location else None,
                location.ref if location else None,
                location.commit_sha if location else None,
                location.path if location else None,
                payload,
                payload_sha256,
                artifact.created_at.isoformat(),
            ),
        )
        self._event(
            db,
            event_key=(
                f"artifact.register:{artifact.artifact_id}:{artifact.version}:"
                f"{payload_sha256}"
            ),
            task_id=artifact.task_id,
            project_id=artifact.project_id,
            event_type="artifact.registered",
            actor=actor,
            payload={
                "artifact_id": artifact.artifact_id,
                "version": artifact.version,
                "sha256": artifact.sha256,
                "kind": artifact.kind,
            },
            now=artifact.created_at.isoformat(),
        )
        return RegistrationReceipt(
            entity_id=artifact.artifact_id,
            version=artifact.version,
            created=True,
        )

    def _register_evidence_tx(
        self,
        db: sqlite3.Connection,
        evidence: Evidence,
        *,
        actor: str,
    ) -> RegistrationReceipt:
        payload = self._json(evidence)
        payload_sha256 = canonical_sha256(evidence)
        task = self._task_row(db, evidence.task_id)
        self._assert_project(task, evidence.project_id)
        self._assert_protocol_stage(
            db,
            task_id=evidence.task_id,
            stage_key=evidence.stage_key,
            producer_stage_key=evidence.producer.stage_key,
        )
        existing = db.execute(
            "SELECT payload_sha256 FROM protocol_evidence WHERE evidence_id = ?",
            (evidence.evidence_id,),
        ).fetchone()
        if existing:
            if existing["payload_sha256"] != payload_sha256:
                raise ControlPlaneConflictError(
                    "Evidence id already exists with different content"
                )
            return RegistrationReceipt(entity_id=evidence.evidence_id, created=False)
        db.execute(
            """
            INSERT INTO protocol_evidence (
                evidence_id, project_id, task_id, stage_key, run_id,
                repository_id, ref, commit_sha, requirement_id, kind,
                status, payload, payload_sha256, observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence.evidence_id,
                evidence.project_id,
                evidence.task_id,
                evidence.stage_key,
                evidence.producer.run_id,
                evidence.repository_id,
                evidence.ref,
                evidence.commit_sha,
                evidence.requirement_id,
                evidence.kind,
                evidence.status.value,
                payload,
                payload_sha256,
                evidence.observed_at.isoformat(),
            ),
        )
        self._event(
            db,
            event_key=f"evidence.register:{evidence.evidence_id}:{payload_sha256}",
            task_id=evidence.task_id,
            project_id=evidence.project_id,
            event_type="evidence.registered",
            actor=actor,
            payload={
                "evidence_id": evidence.evidence_id,
                "requirement_id": evidence.requirement_id,
                "status": evidence.status.value,
            },
            now=evidence.observed_at.isoformat(),
        )
        return RegistrationReceipt(entity_id=evidence.evidence_id, created=True)

    @staticmethod
    def _assert_context_inputs(db: sqlite3.Connection, context_pack: ContextPack) -> None:
        for binding in context_pack.input_artifacts:
            row = db.execute(
                """
                SELECT payload FROM protocol_artifacts
                WHERE artifact_id = ? AND version = ?
                """,
                (binding.artifact_id, binding.version),
            ).fetchone()
            if row is None:
                raise ControlPlaneValidationError(
                    f"Context input Artifact is not registered: {binding.artifact_id}"
                )
            artifact = Artifact.model_validate_json(row["payload"])
            if (
                artifact.project_id != context_pack.project_id
                or artifact.task_id != context_pack.task_id
                or artifact.version_ref() != binding
            ):
                raise ControlPlaneValidationError(
                    f"Context input Artifact binding does not match: {binding.artifact_id}"
                )

    @staticmethod
    def _assert_handoff_matches_context(
        context_pack: ContextPack,
        handoff: HandoffPack,
    ) -> None:
        if (
            handoff.project_id != context_pack.project_id
            or handoff.task_id != context_pack.task_id
            or handoff.stage_key != context_pack.stage_key
            or handoff.run_id != context_pack.run_id
            or handoff.input_artifacts != context_pack.input_artifacts
            or handoff.required_outputs != context_pack.required_outputs
            or handoff.forbidden_constraints != context_pack.forbidden_constraints
        ):
            raise ControlPlaneValidationError(
                "Handoff Pack does not match the persisted Context Pack"
            )
        for artifact in handoff.output_artifacts:
            if (
                artifact.project_id != context_pack.project_id
                or artifact.task_id != context_pack.task_id
                or artifact.stage_key != context_pack.stage_key
                or artifact.producer != handoff.producer
            ):
                raise ControlPlaneValidationError(
                    "Handoff Artifact producer or scope does not match its Context"
                )
        for evidence in handoff.evidence:
            if (
                evidence.project_id != context_pack.project_id
                or evidence.task_id != context_pack.task_id
                or evidence.stage_key != context_pack.stage_key
                or evidence.producer != handoff.producer
            ):
                raise ControlPlaneValidationError(
                    "Handoff Evidence producer or scope does not match its Context"
                )

    def _activate_handoff_evidence_tx(
        self,
        db: sqlite3.Connection,
        *,
        task_id: str,
        gate_key: str,
        evidence: list[Evidence],
        actor: str,
        event_key: str,
        now: str,
    ) -> list[str]:
        gate = self._gate_row(db, task_id, gate_key)
        requirements = {
            item.requirement_id: item
            for item in self._gate_requirements(db, task_id, gate_key)
        }
        active_ids: list[str] = []
        replaced_requirement_ids: set[str] = set()
        for item in evidence:
            requirement = requirements.get(item.requirement_id)
            if requirement is None:
                continue
            if (
                item.repository_id != requirement.repository_id
                or item.ref != requirement.ref
                or item.commit_sha != requirement.commit_sha
                or item.kind != requirement.evidence_kind
            ):
                raise ControlPlaneValidationError(
                    f"Evidence {item.evidence_id} does not match its Gate requirement scope"
                )
            active_ids.append(item.evidence_id)
            replaced_requirement_ids.add(item.requirement_id)
        preserved = db.execute(
            """
            SELECT evidence_id, requirement_id FROM control_gate_evidence
            WHERE task_id = ? AND gate_key = ? AND active = 1
            ORDER BY evidence_id
            """,
            (task_id, gate_key),
        ).fetchall()
        active_ids.extend(
            item["evidence_id"]
            for item in preserved
            if item["requirement_id"] not in replaced_requirement_ids
        )
        active_ids.sort()

        db.execute(
            """
            UPDATE control_gate_evidence
            SET active = 0, deactivated_at = ?
            WHERE task_id = ? AND gate_key = ? AND active = 1
            """,
            (now, task_id, gate_key),
        )
        for evidence_id in active_ids:
            item = self._evidence_row(db, evidence_id)
            db.execute(
                """
                INSERT INTO control_gate_evidence (
                    task_id, gate_key, evidence_id, requirement_id,
                    active, activated_at, deactivated_at
                ) VALUES (?, ?, ?, ?, 1, ?, NULL)
                ON CONFLICT(task_id, gate_key, evidence_id) DO UPDATE SET
                    active = 1,
                    requirement_id = excluded.requirement_id,
                    activated_at = excluded.activated_at,
                    deactivated_at = NULL
                """,
                (
                    task_id,
                    gate_key,
                    evidence_id,
                    item["requirement_id"],
                    now,
                ),
            )
        current = GateStatus(gate["status"])
        if current == GateStatus.EVALUATING:
            raise ControlPlaneConflictError(
                "Cannot replace Evidence while the Gate is evaluating"
            )
        next_status = (
            transition_gate(current, GateStatus.STALE)
            if current == GateStatus.PASSED
            else current
        )
        cursor = db.execute(
            """
            UPDATE control_gates
            SET status = ?, version = version + 1,
                last_evaluation = NULL, updated_at = ?
            WHERE task_id = ? AND gate_key = ? AND version = ?
            """,
            (next_status.value, now, task_id, gate_key, gate["version"]),
        )
        if cursor.rowcount != 1:
            raise ControlPlaneConflictError("Gate changed during Handoff registration")
        self._event(
            db,
            event_key=event_key,
            task_id=task_id,
            project_id=gate["project_id"],
            event_type="gate.evidence_set",
            actor=actor,
            payload={
                "gate_key": gate_key,
                "evidence_ids": active_ids,
                "status": next_status.value,
            },
            now=now,
        )
        return active_ids

    def _evaluate_gate_tx(
        self,
        db: sqlite3.Connection,
        *,
        task_id: str,
        gate_key: str,
        actor: str,
        event_key_prefix: str,
        now: str,
    ) -> GateRecord:
        gate = self._gate_row(db, task_id, gate_key)
        requirements = self._gate_requirements(db, task_id, gate_key)
        evidence = self._active_evidence(db, task_id, gate_key)
        current = GateStatus(gate["status"])
        try:
            evaluating = transition_gate(current, GateStatus.EVALUATING)
        except TransitionError as exc:
            raise ControlPlaneConflictError(
                f"Gate cannot be evaluated from {current.value}"
            ) from exc
        cursor = db.execute(
            """
            UPDATE control_gates
            SET status = ?, version = version + 1, updated_at = ?
            WHERE task_id = ? AND gate_key = ? AND version = ?
            """,
            (evaluating.value, now, task_id, gate_key, gate["version"]),
        )
        if cursor.rowcount != 1:
            raise ControlPlaneConflictError("Gate changed before evaluation started")
        self._event(
            db,
            event_key=f"{event_key_prefix}:gate.evaluation_started",
            task_id=task_id,
            project_id=gate["project_id"],
            event_type="gate.evaluation_started",
            actor=actor,
            payload={
                "gate_key": gate_key,
                "from": current.value,
                "to": evaluating.value,
            },
            now=now,
        )
        result = evaluate_gate(requirements, evidence)
        final = transition_gate(
            evaluating,
            GateStatus.PASSED
            if result.decision.value == "pass"
            else GateStatus.BLOCKED,
        )
        cursor = db.execute(
            """
            UPDATE control_gates
            SET status = ?, version = version + 1,
                last_evaluation = ?, updated_at = ?
            WHERE task_id = ? AND gate_key = ?
              AND status = ? AND version = ?
            """,
            (
                final.value,
                self._json(result),
                now,
                task_id,
                gate_key,
                evaluating.value,
                int(gate["version"]) + 1,
            ),
        )
        if cursor.rowcount != 1:
            raise ControlPlaneConflictError("Gate changed during evaluation")
        self._event(
            db,
            event_key=f"{event_key_prefix}:gate.evaluated",
            task_id=task_id,
            project_id=gate["project_id"],
            event_type="gate.evaluated",
            actor=actor,
            payload={
                "gate_key": gate_key,
                "status": final.value,
                "decision": result.decision.value,
                "blocker_requirement_ids": result.blocker_requirement_ids,
                "next_safe_action": result.next_safe_action,
            },
            now=now,
        )
        return self._gate_record(db, self._gate_row(db, task_id, gate_key))

    @staticmethod
    def _settled_stage_status(
        semantic_result: SemanticStageResult,
        gate_status: GateStatus,
    ) -> StageStatus:
        if semantic_result == SemanticStageResult.SUCCEEDED:
            return (
                StageStatus.COMPLETED
                if gate_status == GateStatus.PASSED
                else StageStatus.BLOCKED
            )
        if semantic_result == SemanticStageResult.BLOCKED:
            return StageStatus.BLOCKED
        if semantic_result == SemanticStageResult.FAILED:
            return StageStatus.FAILED
        if semantic_result == SemanticStageResult.CANCELLED:
            return StageStatus.CANCELLED
        raise ControlPlaneValidationError("A pending Run cannot be settled")

    def _create_protocol_attention_tx(
        self,
        db: sqlite3.Connection,
        *,
        run_id: str,
        task_id: str,
        project_id: str,
        stage_key: str,
        error_code: str | None,
        actor: str,
        now: str,
    ) -> str:
        item_id = (
            "attn_protocol_"
            + canonical_sha256(
                {
                    "run_id": run_id,
                    "stage_key": stage_key,
                    "error_code": error_code,
                }
            )[:32]
        )
        db.execute(
            """
            INSERT INTO attention_items (
                item_id, project_id, task_id, run_id, kind, state, urgency,
                title, body, options, context, requester, version,
                created_at, updated_at
            ) VALUES (?, ?, ?, NULL, 'blocker', 'open', 'high', ?, ?, '[]', ?, ?, 1, ?, ?)
            """,
            (
                item_id,
                project_id,
                task_id,
                "Protocol Run requires attention",
                (
                    "The runtime result failed the frozen Handoff protocol. "
                    "Inspect the durable error code and retry from the sealed Context Pack."
                ),
                json.dumps(
                    {
                        "protocol_run_id": run_id,
                        "stage_key": stage_key,
                        "adapter_error_code": error_code,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                actor,
                now,
                now,
            ),
        )
        self.tasks._insert_event(
            db,
            task_id=task_id,
            event_type="attention.created",
            actor=actor,
            payload={
                "item_id": item_id,
                "kind": "blocker",
                "urgency": "high",
                "protocol_run_id": run_id,
            },
            created_at=now,
        )
        return item_id

    def _create_invalidation_attention_tx(
        self,
        db: sqlite3.Connection,
        *,
        operation_key: str,
        task_id: str,
        project_id: str,
        inventory: ArtifactInventory,
        stale_approval_ids: list[str],
        stale_gate_keys: list[str],
        reopen_stage_keys: list[str],
        actor: str,
        now: str,
    ) -> tuple[str, str]:
        item_id = (
            "attn_invalidation_"
            + canonical_sha256(
                {"operation_key": operation_key, "task_id": task_id}
            )[:32]
        )
        detail_limit = 200
        context = {
            "operation_key": operation_key,
            "repository_id": inventory.repository_id,
            "ref": inventory.ref,
            "commit_sha": inventory.commit_sha,
            "stale_approval_ids": sorted(stale_approval_ids)[:detail_limit],
            "stale_gate_keys": sorted(stale_gate_keys)[:detail_limit],
            "affected_stage_keys": sorted(reopen_stage_keys)[:detail_limit],
            "stale_approval_count": len(stale_approval_ids),
            "stale_gate_count": len(stale_gate_keys),
            "affected_stage_count": len(reopen_stage_keys),
            "details_truncated": any(
                len(items) > detail_limit
                for items in (stale_approval_ids, stale_gate_keys, reopen_stage_keys)
            ),
        }
        db.execute(
            """
            INSERT INTO attention_items (
                item_id, project_id, task_id, run_id, kind, state, urgency,
                title, body, options, context, requester, version,
                created_at, updated_at
            ) VALUES (?, ?, ?, NULL, 'blocker', 'open', 'high', ?, ?, '[]', ?, ?, 1, ?, ?)
            """,
            (
                item_id,
                project_id,
                task_id,
                "Approval invalidation requires impact analysis",
                (
                    "Repository Artifact changes made one or more Approvals stale. "
                    "Review the affected Gates and Stages before resuming work."
                ),
                json.dumps(
                    context,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                actor,
                now,
                now,
            ),
        )
        event = self._event(
            db,
            event_key=f"{operation_key}:attention:{task_id}",
            task_id=task_id,
            project_id=project_id,
            event_type="attention.created",
            actor=actor,
            payload={
                "item_id": item_id,
                "kind": "blocker",
                "urgency": "high",
                "reason": "approval_invalidated",
            },
            now=now,
        )
        return item_id, event.event_id

    @staticmethod
    def _protocol_run(row: sqlite3.Row) -> ProtocolRunRecord:
        context_pack = ContextPack.model_validate_json(row["context_payload"])
        if (
            context_pack.content_sha256 != row["context_sha256"]
            or context_pack.run_id != row["run_id"]
            or context_pack.project_id != row["project_id"]
            or context_pack.task_id != row["task_id"]
            or context_pack.stage_key != row["stage_key"]
        ):
            raise ControlPlaneValidationError(
                "Persisted Context Pack does not match its Run ledger binding"
            )
        protocol_state = (
            RunProtocolState.model_validate_json(row["protocol_state_payload"])
            if row["protocol_state_payload"]
            else None
        )
        if protocol_state is not None and protocol_state.run_id != row["run_id"]:
            raise ControlPlaneValidationError(
                "Persisted Run protocol state does not match its Run ledger binding"
            )
        handoff_pack = (
            HandoffPack.model_validate_json(row["handoff_payload"])
            if row["handoff_payload"]
            else None
        )
        if handoff_pack is not None and (
            handoff_pack.content_sha256 != row["handoff_sha256"]
            or handoff_pack.pack_id != row["handoff_pack_id"]
            or handoff_pack.run_id != row["run_id"]
        ):
            raise ControlPlaneValidationError(
                "Persisted Handoff Pack does not match its Run ledger binding"
            )
        return ProtocolRunRecord(
            run_id=row["run_id"],
            project_id=row["project_id"],
            task_id=row["task_id"],
            stage_key=row["stage_key"],
            gate_key=row["gate_key"],
            context_pack=context_pack,
            protocol_state=protocol_state,
            handoff_pack=handoff_pack,
            adapter_error_code=row["adapter_error_code"],
            attention_required=bool(row["attention_required"]),
            attention_item_id=row["attention_item_id"],
            created_at=row["created_at"],
            settled_at=row["settled_at"],
        )

    def _gate_record(self, db: sqlite3.Connection, row: sqlite3.Row) -> GateRecord:
        return GateRecord(
            task_id=row["task_id"],
            project_id=row["project_id"],
            gate_key=row["gate_key"],
            stage_key=row["stage_key"],
            status=GateStatus(row["status"]),
            version=int(row["version"]),
            requirements=self._gate_requirements(
                db, row["task_id"], row["gate_key"]
            ),
            active_evidence_ids=[
                item["evidence_id"]
                for item in db.execute(
                    """
                    SELECT evidence_id FROM control_gate_evidence
                    WHERE task_id = ? AND gate_key = ? AND active = 1
                    ORDER BY evidence_id
                    """,
                    (row["task_id"], row["gate_key"]),
                ).fetchall()
            ],
            last_evaluation=(
                GateEvaluation.model_validate_json(row["last_evaluation"])
                if row["last_evaluation"]
                else None
            ),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _task_record(row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            task_id=row["task_id"],
            project_id=row["project_id"],
            status=TaskStatus(row["status"]),
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _stage_inventory(row: sqlite3.Row) -> StageInventory:
        inventory = StageInventory.model_validate_json(row["payload"])
        if (
            inventory.task_id != row["task_id"]
            or inventory.project_id != row["project_id"]
            or inventory.inventory_id != row["inventory_id"]
            or inventory.content_sha256 != row["content_sha256"]
        ):
            raise ControlPlaneValidationError(
                "Persisted Stage inventory does not match its ledger binding"
            )
        return inventory

    @staticmethod
    def _stage(row: sqlite3.Row) -> StageRecord:
        return StageRecord(
            task_id=row["task_id"],
            project_id=row["project_id"],
            stage_key=row["stage_key"],
            gate_key=row["gate_key"],
            status=StageStatus(row["status"]),
            version=int(row["version"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _control_event(row: sqlite3.Row) -> ControlEvent:
        return ControlEvent(
            event_id=row["event_id"],
            event_key=row["event_key"],
            task_id=row["task_id"],
            project_id=row["project_id"],
            event_type=row["event_type"],
            actor=row["actor"],
            payload=json.loads(row["payload"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _assert_project(task: sqlite3.Row, project_id: str) -> None:
        if task["project_id"] != project_id:
            raise ControlPlaneValidationError("Protocol object project does not match its Task")

    @staticmethod
    def _assert_protocol_stage(
        db: sqlite3.Connection,
        *,
        task_id: str,
        stage_key: str,
        producer_stage_key: str,
    ) -> None:
        if producer_stage_key != stage_key:
            raise ControlPlaneValidationError(
                "Protocol producer Stage does not match the object Stage"
            )
        row = db.execute(
            """
            SELECT 1 FROM control_stages
            WHERE task_id = ? AND stage_key = ?
            """,
            (task_id, stage_key),
        ).fetchone()
        if row is None:
            raise ControlPlaneNotFoundError(f"Stage not found: {stage_key}")

    @staticmethod
    def _task_row(db: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT task_id, project_id FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise ControlPlaneNotFoundError(f"Task not found: {task_id}")
        return row

    @staticmethod
    def _gate_row(db: sqlite3.Connection, task_id: str, gate_key: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM control_gates WHERE task_id = ? AND gate_key = ?",
            (task_id, gate_key),
        ).fetchone()
        if row is None:
            raise ControlPlaneNotFoundError(f"Gate not found: {gate_key}")
        return row

    @staticmethod
    def _evidence_row(db: sqlite3.Connection, evidence_id: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM protocol_evidence WHERE evidence_id = ?",
            (evidence_id,),
        ).fetchone()
        if row is None:
            raise ControlPlaneNotFoundError(f"Evidence not found: {evidence_id}")
        return row

    @staticmethod
    def _gate_requirements(
        db: sqlite3.Connection,
        task_id: str,
        gate_key: str,
    ) -> list[GateRequirement]:
        rows = db.execute(
            """
            SELECT payload FROM control_gate_requirements
            WHERE task_id = ? AND gate_key = ?
            ORDER BY requirement_id
            """,
            (task_id, gate_key),
        ).fetchall()
        return [GateRequirement.model_validate_json(row["payload"]) for row in rows]

    @staticmethod
    def _active_evidence(
        db: sqlite3.Connection,
        task_id: str,
        gate_key: str,
    ) -> list[Evidence]:
        rows = db.execute(
            """
            SELECT e.payload FROM control_gate_evidence b
            JOIN protocol_evidence e ON e.evidence_id = b.evidence_id
            WHERE b.task_id = ? AND b.gate_key = ? AND b.active = 1
            ORDER BY e.evidence_id
            """,
            (task_id, gate_key),
        ).fetchall()
        return [Evidence.model_validate_json(row["payload"]) for row in rows]

    def _operation_result(
        self,
        db: sqlite3.Connection,
        operation_key: str,
        fingerprint: str,
    ) -> dict[str, Any] | None:
        row = db.execute(
            "SELECT fingerprint, result FROM control_operations WHERE operation_key = ?",
            (operation_key,),
        ).fetchone()
        if row is None:
            return None
        if row["fingerprint"] != fingerprint:
            raise ControlPlaneConflictError(
                "Operation key was already used with different input"
            )
        return json.loads(row["result"])

    @staticmethod
    def _complete_operation(
        db: sqlite3.Connection,
        operation_key: str,
        fingerprint: str,
        result: dict[str, Any],
        now: str,
    ) -> None:
        db.execute(
            """
            INSERT INTO control_operations (
                operation_key, fingerprint, result, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                operation_key,
                fingerprint,
                json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                now,
            ),
        )

    def _event(
        self,
        db: sqlite3.Connection,
        *,
        event_key: str,
        task_id: str,
        project_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
        now: str,
    ) -> ControlEvent:
        event_id = f"cevt_{uuid.uuid4().hex}"
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        db.execute(
            """
            INSERT INTO control_events (
                event_id, event_key, task_id, project_id,
                event_type, actor, payload, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                event_key,
                task_id,
                project_id,
                event_type,
                actor,
                serialized,
                now,
            ),
        )
        self.tasks._insert_event(
            db,
            task_id=task_id,
            event_type=event_type,
            actor=actor,
            payload=payload,
            created_at=now,
        )
        return ControlEvent(
            event_id=event_id,
            event_key=event_key,
            task_id=task_id,
            project_id=project_id,
            event_type=event_type,
            actor=actor,
            payload=payload,
            created_at=now,
        )

    @staticmethod
    def _json(value: Any) -> str:
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        return canonical_json_bytes(value).decode("utf-8")

    @classmethod
    def _validate_operation_key(cls, value: str) -> None:
        if not cls._OPERATION_KEY.fullmatch(value):
            raise ControlPlaneValidationError("Invalid operation_key")

    @staticmethod
    def _validate_actor(value: str) -> None:
        if not value.strip() or len(value) > 128:
            raise ControlPlaneValidationError(
                "actor must contain between 1 and 128 characters"
            )
