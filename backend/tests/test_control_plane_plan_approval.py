from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agora.attention.models import CreateAttentionRequest
from agora.attention.store import AttentionStore
from agora.control_plane.auth import (
    ControlPrincipal,
    authenticate_control_plane,
    authenticate_control_plane_token,
)
from agora.control_plane.router import (
    get_control_plane_store,
    router,
)
from agora.control_plane.store import ControlPlaneStore
from agora.orchestration.methodology import FOUNDATION_METHODOLOGY
from agora.orchestration.models import PlanState
from agora.orchestration.projection import TaskProjectionStore
from agora.orchestration.store import OrchestrationStore
from agora.execution.security import redact_text
from agora.protocol.hashing import canonical_sha256, seal_model_payload
from agora.protocol.models import StageInventory
from agora.protocol.state_machines import GateStatus, StageStatus, TaskStatus
from agora.tasks.models import CreateTaskRequest, utc_now
from agora.tasks.store import TaskStore


def _principal(*permissions: str, projects=("agora",)) -> ControlPrincipal:
    return ControlPrincipal(
        "principal-plan-owner",
        frozenset(permissions),
        frozenset(projects),
    )


def _app(store: ControlPlaneStore, principal: ControlPrincipal) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_control_plane_store] = lambda: store
    app.dependency_overrides[authenticate_control_plane] = lambda: principal
    return app


def _base(task_id: str, project_id: str = "agora") -> str:
    return f"/api/control-plane/projects/{project_id}/tasks/{task_id}"


def _request(
    task_version: int,
    plan_version: int,
    *,
    operation_key: str = "plan-approval-1",
    reason: str = "The formal Stage and Gate evidence is complete.",
) -> dict:
    return {
        "reason": reason,
        "expected_task_version": task_version,
        "expected_plan_version": plan_version,
        "operation_key": operation_key,
    }


def _review_ready_system(tmp_path):
    tasks = TaskStore(tmp_path / "agora.db")
    task = tasks.create(
        CreateTaskRequest(
            project_id="agora",
            title="Approve a reviewed formal Task",
            kind="implementation",
        )
    )
    control = ControlPlaneStore(tasks)
    control.ensure_task_state(task.task_id, actor="test")
    orchestration = OrchestrationStore(tasks)
    plan = orchestration.create_plan(
        task.task_id,
        FOUNDATION_METHODOLOGY,
        total_token_budget=60_000,
        total_cost_budget_usd=10.0,
        actor="test",
    )
    runtimes = ("codex", "claude", "kiro")
    inventory = StageInventory.model_validate(
        seal_model_payload(
            StageInventory,
            {
                "schema_version": "1.0",
                "inventory_id": f"inventory:{task.task_id}",
                "task_id": task.task_id,
                "project_id": task.project_id,
                "plan_id": plan.plan_id,
                "methodology_id": plan.methodology_id,
                "methodology_version": plan.methodology_version,
                "methodology_sha256": plan.methodology_sha256,
                "provisional": plan.provisional,
                "contract": None,
                "groups": [
                    {
                        "group_key": "delivery",
                        "sequence": 1,
                        "title": "Delivery",
                        "stages": [
                            {
                                "stage_key": stage.stage_key,
                                "gate_key": f"gate:{stage.stage_key}",
                                "sequence": index,
                                "title": stage.title,
                                "role": stage.role,
                                "runtime": runtimes[(index - 1) % len(runtimes)],
                            }
                            for index, stage in enumerate(
                                FOUNDATION_METHODOLOGY.stages,
                                start=1,
                            )
                        ],
                    }
                ],
            },
        )
    )
    control.ensure_stage_inventory(inventory, actor="test")
    now = utc_now()
    with tasks._transaction() as db:
        db.execute(
            """
            UPDATE orchestration_plans
            SET state = ?, version = version + 1, updated_at = ?
            WHERE plan_id = ?
            """,
            (PlanState.AWAITING_APPROVAL.value, now, plan.plan_id),
        )
        db.execute(
            """
            UPDATE orchestration_stages
            SET state = 'passed', updated_at = ? WHERE plan_id = ?
            """,
            (now, plan.plan_id),
        )
        db.execute("DELETE FROM control_gates WHERE task_id = ?", (task.task_id,))
        db.execute("DELETE FROM control_stages WHERE task_id = ?", (task.task_id,))
        for item in inventory.groups[0].stages:
            db.execute(
                """
                INSERT INTO control_stages (
                    task_id, project_id, stage_key, gate_key, status,
                    version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    task.task_id,
                    task.project_id,
                    item.stage_key,
                    item.gate_key,
                    StageStatus.COMPLETED.value,
                    now,
                    now,
                ),
            )
            db.execute(
                """
                INSERT INTO control_gates (
                    task_id, project_id, gate_key, stage_key, status,
                    version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    task.task_id,
                    task.project_id,
                    item.gate_key,
                    item.stage_key,
                    GateStatus.PASSED.value,
                    now,
                    now,
                ),
            )
    lifecycle = control.reconcile_task_lifecycle(task.task_id, actor="test")
    assert lifecycle.task.status == TaskStatus.NEEDS_REVIEW
    return tasks, control, orchestration, task, lifecycle.task, orchestration.require_plan(
        task.task_id
    )


def test_plan_approval_is_atomic_audited_redacted_and_exactly_replayable(tmp_path):
    tasks, control, orchestration, task, control_task, plan = _review_ready_system(
        tmp_path
    )
    client = TestClient(_app(control, _principal("control_plane.plan.approve")))
    secret = "sk-abcdefghijklmnopqrst"
    payload = _request(
        control_task.version,
        plan.version,
        reason=f"Approved after review; credential {secret}",
    )

    response = client.post(f"{_base(task.task_id)}/plan-approvals", json=payload)
    replay = client.post(f"{_base(task.task_id)}/plan-approvals", json=payload)

    assert response.status_code == 200
    assert replay.status_code == 200
    receipt = response.json()
    assert receipt["task"]["status"] == "completed"
    assert receipt["plan"]["state"] == "ready_for_implementation"
    assert receipt["plan"]["version"] == plan.version + 1
    assert receipt["plan"]["approved_by"] == "principal-plan-owner"
    assert receipt["previous_task_status"] == "needs_review"
    assert receipt["previous_plan_state"] == "awaiting_approval"
    assert receipt["task_completed"] is True
    assert receipt["formal_approval_created"] is False
    assert receipt["methodology_completion_approval_created"] is False
    assert receipt["replayed"] is False
    assert replay.json() == {**receipt, "replayed": True}
    assert control.get_task_state(task.task_id).status == TaskStatus.COMPLETED
    assert orchestration.require_plan(task.task_id).state == (
        PlanState.READY_FOR_IMPLEMENTATION
    )
    projection = TaskProjectionStore(tasks, orchestration, control).get(task.task_id)
    assert projection.task_state == TaskStatus.COMPLETED
    assert all(
        action.kind != "plan_approval"
        for action in projection.required_human_actions
    )

    with tasks._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM protocol_approvals").fetchone()[0] == 0
        assert db.execute(
            """
            SELECT COUNT(*) FROM control_events
            WHERE event_key = 'plan-approval-1:orchestration.plan_approved'
            """
        ).fetchone()[0] == 1
        operation = db.execute(
            "SELECT fingerprint FROM control_operations WHERE operation_key = ?",
            ("plan-approval-1",),
        ).fetchone()
        assert operation is not None
        assert operation["fingerprint"] == canonical_sha256(
            {
                "operation": "control_plane.plan.approve@1.0",
                "project_id": task.project_id,
                "task_id": task.task_id,
                "expected_task_version": control_task.version,
                "expected_plan_version": plan.version,
                "actor": "principal-plan-owner",
                "reason": redact_text(payload["reason"]),
            }
        )
        assert db.execute(
            "SELECT COUNT(*) FROM control_operations WHERE operation_key = ?",
            ("plan-approval-1",),
        ).fetchone()[0] == 1
        persisted = json.dumps(
            [
                row[0]
                for row in db.execute(
                    """
                    SELECT payload FROM task_events WHERE task_id = ?
                    UNION ALL
                    SELECT payload FROM control_events WHERE task_id = ?
                    UNION ALL
                    SELECT result FROM control_operations WHERE operation_key = ?
                    """,
                    (task.task_id, task.task_id, "plan-approval-1"),
                )
            ]
        )
        assert secret not in persisted
        assert "[REDACTED]" in persisted
        assert {
            row["status"]
            for row in db.execute(
                "SELECT status FROM control_gates WHERE task_id = ?",
                (task.task_id,),
            )
        } == {GateStatus.PASSED.value}


def test_authentication_accepts_only_the_frozen_plan_approval_permission(
    monkeypatch,
):
    monkeypatch.setenv("AGORA_PLAN_APPROVAL_TOKEN", "plan-approval-secret")
    monkeypatch.setattr(
        "agora.control_plane.auth.get_config",
        lambda: {
            "control_plane": {
                "auth": {
                    "credentials": [
                        {
                            "secret_ref": "AGORA_PLAN_APPROVAL_TOKEN",
                            "principal": "principal-plan-owner",
                            "permissions": ["control_plane.plan.approve"],
                            "projects": ["agora"],
                        }
                    ]
                }
            }
        },
    )

    principal = authenticate_control_plane_token("plan-approval-secret")

    assert principal.permissions == frozenset({"control_plane.plan.approve"})


def test_plan_approval_has_distinct_permission_and_non_enumerating_scope(tmp_path):
    _, control, _, task, control_task, plan = _review_ready_system(tmp_path)
    payload = _request(control_task.version, plan.version)

    formal_approval_only = TestClient(
        _app(control, _principal("control_plane.approve"))
    ).post(f"{_base(task.task_id)}/plan-approvals", json=payload)
    wrong_membership = TestClient(
        _app(
            control,
            _principal("control_plane.plan.approve", projects=("other",)),
        )
    ).post(f"{_base(task.task_id)}/plan-approvals", json=payload)
    cross_project = TestClient(
        _app(
            control,
            _principal("control_plane.plan.approve", projects=("other",)),
        )
    ).post(f"{_base(task.task_id, 'other')}/plan-approvals", json=payload)

    assert formal_approval_only.status_code == 403
    assert wrong_membership.status_code == 403
    assert cross_project.status_code == 404


def test_plan_approval_rejects_spoofed_actor_blank_reason_and_stale_versions(tmp_path):
    _, control, _, task, control_task, plan = _review_ready_system(tmp_path)
    client = TestClient(_app(control, _principal("control_plane.plan.approve")))
    base = f"{_base(task.task_id)}/plan-approvals"

    spoofed = client.post(
        base,
        json={
            **_request(control_task.version, plan.version),
            "actor": "spoofed-principal",
        },
    )
    blank = client.post(
        base,
        json=_request(control_task.version, plan.version, reason="   "),
    )
    stale_task = client.post(
        base,
        json=_request(control_task.version - 1, plan.version),
    )
    stale_plan = client.post(
        base,
        json=_request(control_task.version, plan.version - 1),
    )

    assert spoofed.status_code == 422
    assert blank.status_code == 422
    assert stale_task.status_code == 409
    assert stale_plan.status_code == 409


def test_plan_approval_fails_when_authoritative_gate_no_longer_passes(tmp_path):
    tasks, control, orchestration, task, control_task, plan = _review_ready_system(
        tmp_path
    )
    with tasks._transaction() as db:
        db.execute(
            """
            UPDATE control_gates SET status = 'blocked', version = version + 1
            WHERE task_id = ? AND gate_key = (
                SELECT gate_key FROM control_gates
                WHERE task_id = ? ORDER BY gate_key LIMIT 1
            )
            """,
            (task.task_id, task.task_id),
        )
    response = TestClient(
        _app(control, _principal("control_plane.plan.approve"))
    ).post(
        f"{_base(task.task_id)}/plan-approvals",
        json=_request(control_task.version, plan.version),
    )

    assert response.status_code == 409
    assert control.get_task_state(task.task_id).status == TaskStatus.NEEDS_REVIEW
    assert orchestration.require_plan(task.task_id).state == PlanState.AWAITING_APPROVAL


def test_plan_approval_operation_key_is_shared_with_attention_commands(tmp_path):
    tasks, control, _, task, control_task, plan = _review_ready_system(tmp_path)
    attention = AttentionStore(tasks).create(
        CreateAttentionRequest(
            task_id=task.task_id,
            kind="question",
            title="Confirm the operator is present.",
            requester="test",
        )
    )
    client = TestClient(
        _app(
            control,
            _principal(
                "control_plane.attention.respond",
                "control_plane.plan.approve",
            ),
        )
    )
    operation_key = "shared-attention-plan-operation"
    attention_response = client.post(
        f"{_base(task.task_id)}/attention/{attention.item_id}/responses",
        json={
            "action": "answer",
            "response": "Confirmed.",
            "expected_version": attention.version,
            "operation_key": operation_key,
        },
    )
    collision = client.post(
        f"{_base(task.task_id)}/plan-approvals",
        json=_request(
            control_task.version,
            plan.version,
            operation_key=operation_key,
        ),
    )

    assert attention_response.status_code == 200
    assert collision.status_code == 409
    assert control.get_task_state(task.task_id).status == TaskStatus.NEEDS_REVIEW

    (
        reverse_tasks,
        reverse_control,
        _,
        reverse_task,
        reverse_control_task,
        reverse_plan,
    ) = _review_ready_system(tmp_path / "reverse")
    reverse_client = TestClient(
        _app(
            reverse_control,
            _principal(
                "control_plane.attention.respond",
                "control_plane.plan.approve",
            ),
        )
    )
    reverse_key = "shared-plan-attention-operation"
    approved = reverse_client.post(
        f"{_base(reverse_task.task_id)}/plan-approvals",
        json=_request(
            reverse_control_task.version,
            reverse_plan.version,
            operation_key=reverse_key,
        ),
    )
    reverse_attention = AttentionStore(reverse_tasks).create(
        CreateAttentionRequest(
            task_id=reverse_task.task_id,
            kind="question",
            title="This retry key must remain globally owned.",
            requester="test",
        )
    )
    reverse_collision = reverse_client.post(
        f"{_base(reverse_task.task_id)}/attention/{reverse_attention.item_id}/responses",
        json={
            "action": "answer",
            "response": "Do not accept the collision.",
            "expected_version": reverse_attention.version,
            "operation_key": reverse_key,
        },
    )

    assert approved.status_code == 200
    assert reverse_collision.status_code == 409
    assert AttentionStore(reverse_tasks).get(reverse_attention.item_id).state.value == (
        "open"
    )


def test_plan_approval_rejects_methodology_execution_contract(tmp_path):
    tasks, control, orchestration, task, control_task, plan = _review_ready_system(
        tmp_path
    )
    db = tasks._connect()
    try:
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute(
            """
            INSERT INTO orchestration_methodology_execution_contracts (
                contract_id, task_id, plan_id, inventory_id, inventory_sha256,
                migration_request_id, migration_receipt_sha256, contract_sha256,
                contract_payload, authenticated_principal_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "methodology-contract-test",
                task.task_id,
                plan.plan_id,
                f"inventory:{task.task_id}",
                "a" * 64,
                "migration-request-test",
                "b" * 64,
                "c" * 64,
                "{}",
                "principal-plan-owner",
                utc_now(),
            ),
        )
        db.commit()
    finally:
        db.close()

    response = TestClient(
        _app(control, _principal("control_plane.plan.approve"))
    ).post(
        f"{_base(task.task_id)}/plan-approvals",
        json=_request(control_task.version, plan.version),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Control Plane state conflict"
    assert control.get_task_state(task.task_id).status == TaskStatus.NEEDS_REVIEW
    assert orchestration.require_plan(task.task_id).state == PlanState.AWAITING_APPROVAL


def test_plan_approval_rejects_active_work_and_pending_candidate(tmp_path):
    tasks, control, orchestration, task, control_task, plan = _review_ready_system(
        tmp_path
    )
    now = utc_now()
    with tasks._transaction() as db:
        db.execute(
            """
            INSERT INTO orchestration_runs (
                run_id, plan_id, task_id, stage_key, adapter, state,
                operation_key, prompt_sha256, token_reserved,
                token_measurement, cost_measurement, attempt, started_at
            ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?, 1, 'estimated',
                      'unavailable', 1, ?)
            """,
            (
                "active-run-test",
                plan.plan_id,
                task.task_id,
                FOUNDATION_METHODOLOGY.stages[0].stage_key,
                "codex",
                "active-run-operation",
                "a" * 64,
                now,
            ),
        )
        db.execute(
            """
            INSERT INTO orchestration_consultations (
                consultation_id, operation_key, project_id, task_id, plan_id,
                plan_version_observed, inventory_id, inventory_sha256,
                stage_key, role, runtime, repository_id, repository_ref,
                repository_commit, decision_key, state, prompt_sha256,
                schema_status, token_reserved, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running',
                      ?, 'not_observed', 1, ?)
            """,
            (
                "active-consultation-test",
                "active-consultation-operation",
                task.project_id,
                task.task_id,
                plan.plan_id,
                plan.version,
                f"inventory:{task.task_id}",
                "b" * 64,
                FOUNDATION_METHODOLOGY.stages[0].stage_key,
                "advisor",
                "codex",
                "repo",
                "refs/heads/main",
                "c" * 40,
                "consultation-decision",
                "d" * 64,
                now,
            ),
        )
    client = TestClient(_app(control, _principal("control_plane.plan.approve")))
    base = f"{_base(task.task_id)}/plan-approvals"

    active_run = client.post(
        base,
        json=_request(
            control_task.version,
            plan.version,
            operation_key="approve-with-active-run",
        ),
    )
    with tasks._transaction() as db:
        db.execute("DELETE FROM orchestration_runs WHERE run_id = 'active-run-test'")
    active_consultation = client.post(
        base,
        json=_request(
            control_task.version,
            plan.version,
            operation_key="approve-with-active-consultation",
        ),
    )
    with tasks._transaction() as db:
        db.execute(
            "DELETE FROM orchestration_consultations WHERE consultation_id = ?",
            ("active-consultation-test",),
        )
        first_stage = FOUNDATION_METHODOLOGY.stages[0].stage_key
        db.execute(
            """
            INSERT INTO protocol_runs (
                run_id, project_id, task_id, stage_key, gate_key,
                context_pack_id, context_payload, context_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, '{}', ?, ?)
            """,
            (
                "unsettled-protocol-run-test",
                task.project_id,
                task.task_id,
                first_stage,
                f"gate:{first_stage}",
                "unsettled-context-pack-test",
                "e" * 64,
                now,
            ),
        )
    unsettled_protocol_run = client.post(
        base,
        json=_request(
            control_task.version,
            plan.version,
            operation_key="approve-with-unsettled-protocol-run",
        ),
    )
    with tasks._transaction() as db:
        db.execute(
            "DELETE FROM protocol_runs WHERE run_id = ?",
            ("unsettled-protocol-run-test",),
        )
        db.execute(
            """
            INSERT INTO orchestration_consultation_candidates (
                candidate_id, plan_id, task_id, stage_key,
                operation_key, payload, created_at
            ) VALUES (?, ?, ?, ?, ?, '{}', ?)
            """,
            (
                "pending-candidate-test",
                plan.plan_id,
                task.task_id,
                FOUNDATION_METHODOLOGY.stages[0].stage_key,
                "pending-candidate-operation",
                now,
            ),
        )
    pending_candidate = client.post(
        base,
        json=_request(
            control_task.version,
            plan.version,
            operation_key="approve-with-pending-candidate",
        ),
    )

    assert active_run.status_code == 409
    assert active_consultation.status_code == 409
    assert unsettled_protocol_run.status_code == 409
    assert pending_candidate.status_code == 409
    assert control.get_task_state(task.task_id).status == TaskStatus.NEEDS_REVIEW
    assert orchestration.require_plan(task.task_id).state == PlanState.AWAITING_APPROVAL


def test_plan_approval_rolls_back_task_plan_audit_and_receipt(tmp_path):
    tasks, control, orchestration, task, control_task, plan = _review_ready_system(
        tmp_path
    )
    operation_key = "rollback-plan-approval"
    with tasks._transaction() as db:
        event_count = db.execute(
            "SELECT COUNT(*) FROM task_events WHERE task_id = ?",
            (task.task_id,),
        ).fetchone()[0]
        db.execute(
            f"""
            CREATE TRIGGER fail_plan_approval_receipt
            AFTER INSERT ON control_operations
            WHEN NEW.operation_key = '{operation_key}'
            BEGIN
                SELECT RAISE(ABORT, 'injected Plan approval receipt failure');
            END
            """
        )

    response = TestClient(
        _app(control, _principal("control_plane.plan.approve"))
    ).post(
        f"{_base(task.task_id)}/plan-approvals",
        json=_request(
            control_task.version,
            plan.version,
            operation_key=operation_key,
        ),
    )

    assert response.status_code == 500
    assert control.get_task_state(task.task_id).status == TaskStatus.NEEDS_REVIEW
    assert orchestration.require_plan(task.task_id).state == PlanState.AWAITING_APPROVAL
    with tasks._connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM task_events WHERE task_id = ?",
            (task.task_id,),
        ).fetchone()[0] == event_count
        assert db.execute(
            "SELECT COUNT(*) FROM control_operations WHERE operation_key = ?",
            (operation_key,),
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM control_events WHERE event_key LIKE ?",
            (f"{operation_key}:%",),
        ).fetchone()[0] == 0
