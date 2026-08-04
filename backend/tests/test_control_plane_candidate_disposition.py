from __future__ import annotations

import json
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agora.attention.models import AttentionKind, CreateAttentionRequest
from agora.attention.store import AttentionStore
from agora.control_plane.auth import (
    ControlPrincipal,
    authenticate_control_plane,
    authenticate_control_plane_token,
)
from agora.control_plane.router import get_control_plane_store, router
from agora.orchestration.runtime import RuntimeCommand
from agora.orchestration.service import TaskOrchestrationService
from agora.projects import ProjectRegistry
from agora.tasks.models import utc_now
from agora.tasks.store import TaskStore


def _principal(*permissions: str, projects=("agora",)) -> ControlPrincipal:
    return ControlPrincipal(
        "principal-candidate-owner",
        frozenset(permissions),
        frozenset(projects),
    )


def _app(store, principal: ControlPrincipal) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_control_plane_store] = lambda: store
    app.dependency_overrides[authenticate_control_plane] = lambda: principal
    return app


def _base(task_id: str, project_id: str = "agora") -> str:
    return f"/api/control-plane/projects/{project_id}/tasks/{task_id}"


def _system(tmp_path):
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    projects = ProjectRegistry(
        {
            "projects": {
                "registry_path": str(tmp_path / "projects.yaml"),
                "default": "agora",
                "projects": {
                    "agora": {
                        "name": "Agora",
                        "root": str(root),
                        "workspaces": {
                            runtime: str(root / ".agora" / "workspaces" / runtime)
                            for runtime in ("codex", "claude", "kiro")
                        },
                    }
                },
            }
        },
        project_root=tmp_path,
    )
    tasks = TaskStore(tmp_path / "agora.db")
    runtimes = {
        runtime: RuntimeCommand(
            adapter=runtime,
            command_template=(sys.executable, "{prompt}"),
        )
        for runtime in ("codex", "claude", "kiro")
    }
    service = TaskOrchestrationService(tasks, projects, runtimes)
    task = service.create(
        project_id="agora",
        title="Human disposition of one advisory candidate",
        description="Keep consultation advisory until an explicit owner action.",
        total_token_budget=30_000,
        total_cost_budget_usd=12,
    )
    return tasks, service, task


def _candidate(service, task, *, suffix="one"):
    version = service.status(task.task_id).plan.version
    candidate = service.register_consultation_candidate(
        task.task_id,
        consultation_id=f"consultation:http:{suffix}",
        runtime="codex",
        title=f"Candidate {suffix}",
        decision_key=f"candidate_policy_{suffix}",
        decision_value=f"Use bounded option {suffix}",
        analysis=f"The {suffix} option stays advisory pending an owner action.",
        source_refs=[f"requirement:{suffix}"],
        expected_plan_version=version,
        operation_key=f"candidate-source:{suffix}",
    )
    return candidate, version


def _request(candidate, version, *, action="adopt", operation_key="dispose:one", reason="Owner reviewed the bounded advice."):
    return {
        "action": action,
        "reason": reason,
        "expected_candidate_sha256": candidate.content_sha256,
        "expected_plan_version": version,
        "operation_key": operation_key,
    }


def _path(task_id: str, candidate_id: str, project_id: str = "agora") -> str:
    return (
        f"{_base(task_id, project_id)}/consultation-candidates/"
        f"{candidate_id}/dispositions"
    )


def test_authenticated_adoption_is_atomic_redacted_and_exactly_replayable(tmp_path):
    tasks, service, task = _system(tmp_path)
    candidate, version = _candidate(service, task)
    control_before = service.control_plane.get_task_state(task.task_id)
    route_before = service.control_plane.get_stage_route(task.task_id)
    secret = "sk-abcdefghijklmnopqrst"
    payload = _request(
        candidate,
        version,
        reason=f"Adopt after review; token={secret}",
    )
    client = TestClient(
        _app(service.control_plane, _principal("control_plane.candidate.dispose"))
    )

    response = client.post(_path(task.task_id, candidate.candidate_id), json=payload)
    replay = client.post(_path(task.task_id, candidate.candidate_id), json=payload)

    assert response.status_code == 200
    assert replay.status_code == 200
    receipt = response.json()
    replay_receipt = replay.json()
    assert receipt["operation_key"] == payload["operation_key"]
    assert receipt["disposition"]["action"] == "adopted"
    assert receipt["disposition"]["candidate_sha256"] == candidate.content_sha256
    assert receipt["disposition"]["actor"] == "principal-candidate-owner"
    assert receipt["candidate_authority"] is False
    assert receipt["task_decision_bound"] is True
    assert receipt["plan_version_changed"] is True
    assert receipt["task_state_mutated"] is False
    assert receipt["stage_state_mutated"] is False
    assert receipt["gate_state_mutated"] is False
    assert receipt["formal_approval_created"] is False
    assert receipt["runtime_called"] is False
    assert receipt["replayed"] is False
    assert replay_receipt["replayed"] is True
    assert replay_receipt["disposition"] == receipt["disposition"]
    assert secret not in json.dumps(receipt)
    assert "[REDACTED]" in receipt["disposition"]["reason"]

    status = service.status(task.task_id)
    assert status.plan.version == version + 1
    assert len(status.decisions) == 1
    assert status.decisions[0].decision_id == receipt["disposition"]["decision_id"]
    assert service.control_plane.get_task_state(task.task_id) == control_before
    assert service.control_plane.get_stage_route(task.task_id) == route_before
    projection = service.unified_status(task.task_id)
    assert all(
        action.source_id != candidate.candidate_id
        for action in projection.required_human_actions
    )
    assert projection.approvals == []
    with tasks._connect() as db:
        persisted = json.dumps(
            [
                dict(row)
                for row in db.execute(
                    """
                    SELECT payload FROM orchestration_candidate_dispositions
                    UNION ALL
                    SELECT result AS payload FROM control_operations
                    WHERE operation_key = ?
                    """,
                    (payload["operation_key"],),
                ).fetchall()
            ]
        )
    assert secret not in persisted


def test_authenticated_rejection_creates_no_decision_or_lifecycle_mutation(tmp_path):
    _, service, task = _system(tmp_path)
    candidate, version = _candidate(service, task, suffix="reject")
    control_before = service.control_plane.get_task_state(task.task_id)
    route_before = service.control_plane.get_stage_route(task.task_id)
    client = TestClient(
        _app(service.control_plane, _principal("control_plane.candidate.dispose"))
    )

    response = client.post(
        _path(task.task_id, candidate.candidate_id),
        json=_request(
            candidate,
            version,
            action="reject",
            operation_key="dispose:reject",
            reason="The candidate conflicts with the Task contract.",
        ),
    )

    assert response.status_code == 200
    receipt = response.json()
    assert receipt["disposition"]["action"] == "rejected"
    assert receipt["disposition"]["decision_id"] is None
    assert receipt["task_decision_bound"] is False
    assert receipt["plan_version_changed"] is False
    assert service.status(task.task_id).plan.version == version
    assert service.status(task.task_id).decisions == []
    assert service.control_plane.get_task_state(task.task_id) == control_before
    assert service.control_plane.get_stage_route(task.task_id) == route_before


def test_authentication_accepts_the_frozen_candidate_disposition_permission(
    monkeypatch,
):
    monkeypatch.setenv("AGORA_CANDIDATE_DISPOSITION_TOKEN", "candidate-secret")
    monkeypatch.setattr(
        "agora.control_plane.auth.get_config",
        lambda: {
            "control_plane": {
                "auth": {
                    "credentials": [
                        {
                            "secret_ref": "AGORA_CANDIDATE_DISPOSITION_TOKEN",
                            "principal": "principal-candidate-owner",
                            "permissions": ["control_plane.candidate.dispose"],
                            "projects": ["agora"],
                        }
                    ]
                }
            }
        },
    )

    principal = authenticate_control_plane_token("candidate-secret")

    assert principal.permissions == frozenset(
        {"control_plane.candidate.dispose"}
    )


def test_candidate_disposition_permission_scope_and_request_shape_fail_closed(tmp_path):
    _, service, task = _system(tmp_path)
    candidate, version = _candidate(service, task, suffix="scope")
    path = _path(task.task_id, candidate.candidate_id)
    payload = _request(candidate, version, operation_key="dispose:scope")

    assert TestClient(_app(service.control_plane, _principal("control_plane.read"))).post(
        path, json=payload
    ).status_code == 403
    assert TestClient(
        _app(
            service.control_plane,
            _principal("control_plane.candidate.dispose", projects=("other",)),
        )
    ).post(path, json=payload).status_code == 403

    other = service.create(
        project_id="agora",
        title="Other Task",
        description="Must not enumerate the first Task candidate.",
        total_token_budget=30_000,
        total_cost_budget_usd=12,
    )
    client = TestClient(
        _app(service.control_plane, _principal("control_plane.candidate.dispose"))
    )
    assert client.post(
        _path(other.task_id, candidate.candidate_id), json=payload
    ).status_code == 404
    assert client.post(
        _path(task.task_id, "candidate-does-not-exist"), json=payload
    ).status_code == 404
    assert client.post(path, json={**payload, "actor": "spoofed"}).status_code == 422
    assert client.post(path, json={**payload, "reason": "   "}).status_code == 422
    assert client.post(
        path, json={**payload, "expected_candidate_sha256": "g" * 64}
    ).status_code == 422


def test_candidate_hash_plan_version_active_run_and_second_action_conflict(tmp_path):
    _, service, task = _system(tmp_path)
    candidate, version = _candidate(service, task, suffix="conflict")
    client = TestClient(
        _app(service.control_plane, _principal("control_plane.candidate.dispose"))
    )
    path = _path(task.task_id, candidate.candidate_id)

    wrong_hash = _request(
        candidate,
        version,
        operation_key="dispose:wrong-hash",
    )
    wrong_hash["expected_candidate_sha256"] = "0" * 64
    assert client.post(path, json=wrong_hash).status_code == 409
    assert client.post(
        path,
        json=_request(
            candidate,
            version + 1,
            operation_key="dispose:stale-plan",
        ),
    ).status_code == 409

    first = _request(
        candidate,
        version,
        action="reject",
        operation_key="dispose:first",
    )
    assert client.post(path, json=first).status_code == 200
    assert client.post(
        path,
        json=_request(
            candidate,
            version,
            action="reject",
            operation_key="dispose:second",
        ),
    ).status_code == 409
    assert client.post(path, json={**first, "reason": "Changed input"}).status_code == 409

    active_task = service.create(
        project_id="agora",
        title="Active Run conflict",
        description="Candidate disposition may not race the formal Run.",
        total_token_budget=30_000,
        total_cost_budget_usd=12,
    )
    active_candidate, active_version = _candidate(
        service,
        active_task,
        suffix="active-run",
    )
    run = service.store.claim_current_stage(
        active_task.task_id,
        prompt_sha256="f" * 64,
        operation_key="candidate:http:active-run",
    )
    assert client.post(
        _path(active_task.task_id, active_candidate.candidate_id),
        json=_request(
            active_candidate,
            active_version,
            operation_key="dispose:active-run",
        ),
    ).status_code == 409
    service.store.mark_interrupted(run.run_id, reason="finish API conflict test")

    formal_task = service.create(
        project_id="agora",
        title="Unsettled formal Run conflict",
        description="Candidate disposition may not race formal protocol settlement.",
        total_token_budget=30_000,
        total_cost_budget_usd=12,
    )
    formal_candidate, formal_version = _candidate(
        service,
        formal_task,
        suffix="formal-run",
    )
    route = service.control_plane.get_stage_route(formal_task.task_id)
    assert route is not None
    with service.tasks._transaction() as db:
        now = utc_now()
        db.execute(
            """
            INSERT OR IGNORE INTO control_gates (
                task_id, project_id, gate_key, stage_key, status,
                version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'pending', 1, ?, ?)
            """,
            (
                formal_task.task_id,
                formal_task.project_id,
                route.gate_key,
                route.stage_key,
                now,
                now,
            ),
        )
        db.execute(
            """
            INSERT INTO protocol_runs (
                run_id, project_id, task_id, stage_key, gate_key,
                context_pack_id, context_payload, context_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, '{}', ?, ?)
            """,
            (
                "candidate-unsettled-protocol-run",
                formal_task.project_id,
                formal_task.task_id,
                route.stage_key,
                route.gate_key,
                "candidate-unsettled-context",
                "e" * 64,
                now,
            ),
        )
    assert client.post(
        _path(formal_task.task_id, formal_candidate.candidate_id),
        json=_request(
            formal_candidate,
            formal_version,
            operation_key="dispose:formal-run",
        ),
    ).status_code == 409


def test_candidate_disposition_uses_shared_control_operation_registry(tmp_path):
    _, service, task = _system(tmp_path)
    candidate_one, version = _candidate(service, task, suffix="collision-one")
    candidate_two, _ = _candidate(service, task, suffix="collision-two")
    attention_store = AttentionStore(service.tasks, initialize_schema=False)
    item_one = attention_store.create(
        CreateAttentionRequest(
            task_id=task.task_id,
            kind=AttentionKind.QUESTION,
            title="First collision",
            body="Answer before candidate disposition.",
            requester="test",
        )
    )
    item_two = attention_store.create(
        CreateAttentionRequest(
            task_id=task.task_id,
            kind=AttentionKind.QUESTION,
            title="Reverse collision",
            body="Candidate disposition claims the key first.",
            requester="test",
        )
    )
    client = TestClient(
        _app(
            service.control_plane,
            _principal(
                "control_plane.candidate.dispose",
                "control_plane.attention.respond",
            ),
        )
    )
    collision_key = "shared:collision"
    attention_payload = {
        "action": "answer",
        "response": "Human answer",
        "expected_version": item_one.version,
        "operation_key": collision_key,
    }
    assert client.post(
        f"{_base(task.task_id)}/attention/{item_one.item_id}/responses",
        json=attention_payload,
    ).status_code == 200
    assert client.post(
        _path(task.task_id, candidate_one.candidate_id),
        json=_request(
            candidate_one,
            version,
            action="reject",
            operation_key=collision_key,
        ),
    ).status_code == 409

    reverse_key = "shared:reverse"
    assert client.post(
        _path(task.task_id, candidate_two.candidate_id),
        json=_request(
            candidate_two,
            version,
            action="reject",
            operation_key=reverse_key,
        ),
    ).status_code == 200
    assert client.post(
        f"{_base(task.task_id)}/attention/{item_two.item_id}/responses",
        json={
            "action": "answer",
            "response": "Changed command",
            "expected_version": item_two.version,
            "operation_key": reverse_key,
        },
    ).status_code == 409


def test_candidate_disposition_rolls_back_when_shared_receipt_write_fails(tmp_path):
    _, service, task = _system(tmp_path)
    candidate, version = _candidate(service, task, suffix="rollback")
    operation_key = "dispose:rollback"
    with service.tasks._transaction() as db:
        db.execute(
            f"""
            CREATE TRIGGER reject_candidate_control_receipt
            BEFORE INSERT ON control_operations
            WHEN NEW.operation_key = '{operation_key}'
            BEGIN
                SELECT RAISE(ABORT, 'injected candidate receipt failure');
            END
            """
        )
    client = TestClient(
        _app(service.control_plane, _principal("control_plane.candidate.dispose"))
    )

    response = client.post(
        _path(task.task_id, candidate.candidate_id),
        json=_request(candidate, version, operation_key=operation_key),
    )

    assert response.status_code == 500
    assert service.status(task.task_id).plan.version == version
    assert service.status(task.task_id).decisions == []
    with service.tasks._connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM orchestration_candidate_dispositions WHERE candidate_id = ?",
            (candidate.candidate_id,),
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM control_operations WHERE operation_key = ?",
            (operation_key,),
        ).fetchone()[0] == 0


def test_default_projection_keeps_pending_candidate_body_with_long_history(tmp_path):
    _, service, task = _system(tmp_path)
    version = service.status(task.task_id).plan.version
    for index in range(100):
        suffix = f"history-{index:03d}"
        candidate, _ = _candidate(service, task, suffix=suffix)
        service.reject_candidate(
            task.task_id,
            candidate.candidate_id,
            expected_plan_version=version,
            reason="Historical candidate rejected.",
            actor="owner",
            operation_key=f"candidate-history-reject:{index:03d}",
        )
    pending, _ = _candidate(service, task, suffix="pending-after-history")

    projection = service.unified_status(task.task_id, history_limit=100)

    assert len(projection.consultation_candidates) == 100
    assert projection.collection_totals["consultation_candidates"] == 101
    assert pending.candidate_id in {
        candidate.candidate_id for candidate in projection.consultation_candidates
    }
    assert any(
        action.kind == "candidate_disposition"
        and action.source_id == pending.candidate_id
        for action in projection.required_human_actions
    )
