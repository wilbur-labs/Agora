from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agora.attention.bridges.models import BridgeEventRequest, DeliveryMode
from agora.attention.models import CreateAttentionRequest
from agora.attention.store import AttentionStore
from agora.control_plane.auth import ControlPrincipal, authenticate_control_plane
from agora.control_plane.router import (
    get_control_plane_attention_store,
    get_control_plane_store,
    initialize_control_plane_store,
    router,
    task_discovery_router,
)
from agora.control_plane.store import ControlPlaneStore
from agora.orchestration.methodology import FOUNDATION_METHODOLOGY
from agora.orchestration.store import OrchestrationStore
from agora.protocol.models import Evidence
from agora.tasks.models import CreateTaskRequest
from agora.tasks.router import get_task_store
from agora.tasks.store import TaskStore


NOW = datetime(2026, 7, 18, 9, 0, tzinfo=timezone.utc)
COMMIT = "1" * 40


def _app(
    store: ControlPlaneStore,
    principal: ControlPrincipal | None,
    attention_store: AttentionStore | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.include_router(task_discovery_router, prefix="/api")
    app.dependency_overrides[get_control_plane_store] = lambda: store
    if attention_store is not None:
        app.dependency_overrides[get_control_plane_attention_store] = (
            lambda: attention_store
        )
    if principal is not None:
        app.dependency_overrides[authenticate_control_plane] = lambda: principal
    return app


def _store(tmp_path) -> tuple[ControlPlaneStore, str]:
    tasks = TaskStore(tmp_path / "agora.db")
    task = tasks.create(CreateTaskRequest(project_id="agora", title="API", kind="implementation"))
    return ControlPlaneStore(tasks), task.task_id


def _prepare_unified_projection(
    store: ControlPlaneStore,
    task_id: str,
) -> None:
    store.ensure_task_state(task_id, actor="test")
    OrchestrationStore(store.tasks).create_plan(
        task_id,
        FOUNDATION_METHODOLOGY,
        total_token_budget=60_000,
        total_cost_budget_usd=10.0,
        actor="test",
    )


def _principal(*permissions: str, projects=("agora",)) -> ControlPrincipal:
    return ControlPrincipal("principal-api", frozenset(permissions), frozenset(projects))


def _base(task_id: str) -> str:
    return f"/api/control-plane/projects/agora/tasks/{task_id}"


def _index_base(project_id: str = "agora") -> str:
    return f"/api/control-plane/projects/{project_id}/tasks"


def _attention_response(
    item_id: str,
    *,
    action: str = "answer",
    response: str = "Proceed",
    expected_version: int = 1,
    operation_key: str = "attention-response-1",
) -> dict:
    return {
        "action": action,
        "response": response,
        "expected_version": expected_version,
        "operation_key": operation_key,
    }


def _requirement() -> dict:
    return {
        "requirement_id": "review",
        "title": "Review passes",
        "repository_id": "repo",
        "ref": "refs/heads/main",
        "commit_sha": COMMIT,
        "evidence_kind": "review",
        "priority": 1,
        "failure_action": "Run review.",
    }


def _evidence(task_id: str, evidence_id: str = "evidence-review") -> dict:
    return {
        "schema_version": "1.0",
        "evidence_id": evidence_id,
        "project_id": "agora",
        "task_id": task_id,
        "stage_key": "review-stage",
        "producer": {
            "runtime": "claude",
            "run_id": f"run-{evidence_id}",
            "stage_key": "review-stage",
        },
        "repository_id": "repo",
        "ref": "refs/heads/main",
        "commit_sha": COMMIT,
        "requirement_id": "review",
        "kind": "review",
        "status": "passed",
        "artifact_versions": [],
        "summary": "Review approved.",
        "observed_at": NOW.isoformat(),
        "details": {},
    }


def test_api_is_fail_closed_without_auth_configuration(tmp_path, monkeypatch):
    store, task_id = _store(tmp_path)
    monkeypatch.setattr("agora.control_plane.auth.get_config", lambda: {})
    response = TestClient(_app(store, None)).get(f"{_base(task_id)}/projection")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_bearer_secret_ref_resolves_to_a_stable_principal(tmp_path, monkeypatch):
    store, task_id = _store(tmp_path)
    monkeypatch.setenv("AGORA_TEST_CONTROL_TOKEN", "secret-token")
    monkeypatch.setattr(
        "agora.control_plane.auth.get_config",
        lambda: {
            "control_plane": {
                "auth": {
                    "credentials": [
                        {
                            "secret_ref": "AGORA_TEST_CONTROL_TOKEN",
                            "principal": "principal-api",
                            "permissions": ["control_plane.read"],
                            "projects": ["agora"],
                        }
                    ]
                }
            }
        },
    )
    client = TestClient(_app(store, None))
    assert client.get(
        f"{_base(task_id)}/projection",
        headers={"Authorization": "Bearer secret-token"},
    ).status_code == 200
    assert client.get(
        f"{_base(task_id)}/projection",
        headers={"Authorization": "Bearer wrong-token"},
    ).status_code == 401


def test_malformed_or_ambiguous_bearer_configuration_fails_closed(
    tmp_path, monkeypatch
):
    store, task_id = _store(tmp_path)
    monkeypatch.setenv("AGORA_TEST_CONTROL_TOKEN", "secret-token")
    client = TestClient(_app(store, None))
    base_entry = {
        "secret_ref": "AGORA_TEST_CONTROL_TOKEN",
        "principal": "principal-api",
        "permissions": ["control_plane.read"],
        "projects": ["agora"],
    }

    for malformed in (
        {**base_entry, "permissions": "control_plane.read"},
        {**base_entry, "permissions": [1]},
        {**base_entry, "permissions": ["control_plane.unknown"]},
        {**base_entry, "projects": ["../agora"]},
    ):
        monkeypatch.setattr(
            "agora.control_plane.auth.get_config",
            lambda malformed=malformed: {
                "control_plane": {"auth": {"credentials": [malformed]}}
            },
        )
        assert client.get(
            f"{_base(task_id)}/projection",
            headers={"Authorization": "Bearer secret-token"},
        ).status_code == 401

    monkeypatch.setattr(
        "agora.control_plane.auth.get_config",
        lambda: {
            "control_plane": {
                "auth": {
                    "credentials": [
                        base_entry,
                        {**base_entry, "principal": "other-principal"},
                    ]
                }
            }
        },
    )
    assert client.get(
        f"{_base(task_id)}/projection",
        headers={"Authorization": "Bearer secret-token"},
    ).status_code == 401


def test_permissions_and_project_membership_are_enforced(tmp_path):
    store, task_id = _store(tmp_path)
    client = TestClient(_app(store, _principal("control_plane.read", projects=("other",))))
    assert client.get(f"{_base(task_id)}/projection").status_code == 403


def test_cross_project_task_lookup_is_non_enumerating(tmp_path):
    store, task_id = _store(tmp_path)
    principal = _principal("control_plane.read", projects=("agora", "other"))
    response = TestClient(_app(store, principal)).get(
        f"/api/control-plane/projects/other/tasks/{task_id}/projection"
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Control Plane resource not found"


def test_attention_response_is_authenticated_scoped_and_exactly_replayable(
    tmp_path,
):
    store, task_id = _store(tmp_path)
    store.ensure_task_state(task_id, actor="test")
    attention_store = AttentionStore(store.tasks)
    item = attention_store.create(
        CreateAttentionRequest(
            task_id=task_id,
            kind="question",
            title="Choose the deployment region.",
            requester="runtime",
            assignee="principal-api",
        )
    )
    principal = _principal("control_plane.attention.respond")
    client = TestClient(_app(store, principal, attention_store))
    path = f"{_base(task_id)}/attention/{item.item_id}/responses"
    payload = _attention_response(
        item.item_id,
        response="Tokyo access_token=response-secret",
    )
    before_state = store.get_task_state(task_id)

    first = client.post(path, json=payload)
    second = client.post(path, json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    receipt = first.json()
    assert receipt["schema_version"] == "1.0"
    assert receipt["operation_key"] == "attention-response-1"
    assert receipt["response_effect"] == "local_recorded"
    assert receipt["task_state_mutated"] is False
    assert receipt["formal_approval_created"] is False
    assert receipt["attention"]["state"] == "responded"
    assert receipt["attention"]["responded_by"] == "principal-api"
    assert receipt["attention"]["response"] == "Tokyo access_token=[REDACTED]"
    assert store.get_task_state(task_id) == before_state
    responded_events = [
        event
        for event in store.tasks.events(task_id)
        if event.event_type == "attention.responded"
    ]
    assert len(responded_events) == 1
    assert responded_events[0].actor == "principal-api"
    assert responded_events[0].payload == {
        "item_id": item.item_id,
        "action": "answer",
        "operation_key": "attention-response-1",
        "response_effect": "local_recorded",
    }
    with store.tasks._connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM protocol_approvals WHERE task_id = ?",
            (task_id,),
        ).fetchone()[0] == 0

    collision = client.post(
        path,
        json={**payload, "response": "Osaka"},
    )
    assert collision.status_code == 409
    spoof = client.post(path, json={**payload, "actor": "spoofed"})
    assert spoof.status_code == 422


def test_attention_response_enforces_permission_scope_assignee_and_kind(tmp_path):
    store, task_id = _store(tmp_path)
    attention_store = AttentionStore(store.tasks)
    item = attention_store.create(
        CreateAttentionRequest(
            task_id=task_id,
            kind="approval",
            title="Approve the proposed local action.",
            requester="runtime",
            assignee="assigned-principal",
        )
    )
    path = f"{_base(task_id)}/attention/{item.item_id}/responses"

    assert TestClient(_app(store, _principal("control_plane.read"))).post(
        path,
        json=_attention_response(item.item_id, action="approve"),
    ).status_code == 403
    assert TestClient(
        _app(store, _principal("control_plane.attention.respond"), attention_store)
    ).post(
        path,
        json=_attention_response(item.item_id, action="approve"),
    ).status_code == 403

    assigned = ControlPrincipal(
        "assigned-principal",
        frozenset({"control_plane.attention.respond"}),
        frozenset({"agora"}),
    )
    client = TestClient(_app(store, assigned, attention_store))
    mismatch = client.post(
        path,
        json=_attention_response(item.item_id, action="answer"),
    )
    assert mismatch.status_code == 422

    other = store.tasks.create(
        CreateTaskRequest(project_id="agora", title="Other Task")
    )
    cross_task = client.post(
        f"{_base(other.task_id)}/attention/{item.item_id}/responses",
        json=_attention_response(item.item_id, action="approve"),
    )
    assert cross_task.status_code == 404
    cross_project = TestClient(
        _app(
            store,
            ControlPrincipal(
                "assigned-principal",
                frozenset({"control_plane.attention.respond"}),
                frozenset({"agora", "other"}),
            ),
            attention_store,
        )
    ).post(
        f"/api/control-plane/projects/other/tasks/{task_id}/attention/{item.item_id}/responses",
        json=_attention_response(item.item_id, action="approve"),
    )
    assert cross_project.status_code == 404


def test_attention_response_operation_keys_share_the_control_plane_namespace(
    tmp_path,
):
    store, task_id = _store(tmp_path)
    attention_store = AttentionStore(store.tasks)
    principal = _principal(
        "control_plane.attention.respond",
        "control_plane.register",
    )
    client = TestClient(_app(store, principal, attention_store))
    base = _base(task_id)
    assert client.put(
        f"{base}/gates/review-gate",
        json={"stage_key": "review-stage", "requirements": [_requirement()]},
    ).status_code == 200
    control_first = client.put(
        f"{base}/gates/review-gate/active-evidence",
        json={
            "evidence_ids": [],
            "expected_gate_version": 1,
            "operation_key": "shared-control-first",
        },
    )
    assert control_first.status_code == 200

    blocked_item = attention_store.create(
        CreateAttentionRequest(
            task_id=task_id,
            kind="question",
            title="Control key already used.",
            requester="runtime",
        )
    )
    control_collision = client.post(
        f"{base}/attention/{blocked_item.item_id}/responses",
        json=_attention_response(
            blocked_item.item_id,
            operation_key="shared-control-first",
        ),
    )
    assert control_collision.status_code == 409
    assert attention_store.get(blocked_item.item_id).state.value == "open"

    responded_item = attention_store.create(
        CreateAttentionRequest(
            task_id=task_id,
            kind="question",
            title="Attention key is reserved globally.",
            requester="runtime",
        )
    )
    attention_first = client.post(
        f"{base}/attention/{responded_item.item_id}/responses",
        json=_attention_response(
            responded_item.item_id,
            operation_key="shared-attention-first",
        ),
    )
    assert attention_first.status_code == 200
    attention_collision = client.put(
        f"{base}/gates/review-gate/active-evidence",
        json={
            "evidence_ids": [],
            "expected_gate_version": control_first.json()["version"],
            "operation_key": "shared-attention-first",
        },
    )
    assert attention_collision.status_code == 409


def test_attention_response_settles_expiry_without_an_operation_receipt(tmp_path):
    store, task_id = _store(tmp_path)
    attention_store = AttentionStore(store.tasks)
    item = attention_store.create(
        CreateAttentionRequest(
            task_id=task_id,
            kind="blocker",
            title="Expired decision.",
            requester="runtime",
        )
    )
    with store.tasks._transaction() as db:
        db.execute(
            "UPDATE attention_items SET expires_at = ? WHERE item_id = ?",
            ("2020-01-01T00:00:00+00:00", item.item_id),
        )
    response = TestClient(
        _app(
            store,
            _principal("control_plane.attention.respond"),
            attention_store,
        )
    ).post(
        f"{_base(task_id)}/attention/{item.item_id}/responses",
        json=_attention_response(item.item_id, operation_key="expired-response"),
    )

    assert response.status_code == 409
    assert attention_store.get(item.item_id).state.value == "expired"
    with store.tasks._connect() as db:
        assert db.execute(
            """SELECT COUNT(*) FROM control_plane_attention_response_operations
               WHERE operation_key = 'expired-response'"""
        ).fetchone()[0] == 0
        assert db.execute(
            """SELECT COUNT(*) FROM control_operations
               WHERE operation_key = 'expired-response'"""
        ).fetchone()[0] == 0


def test_attention_response_preserves_capture_only_and_queues_bidirectional(
    tmp_path,
):
    store, task_id = _store(tmp_path)
    attention_store = AttentionStore(store.tasks)
    now = datetime.now(timezone.utc).isoformat()
    with store.tasks._transaction() as db:
        db.execute(
            """INSERT INTO execution_runs
               (run_id, task_id, project_id, adapter, state, prompt, workspace,
                timeout_seconds, queued_at, actor)
               VALUES ('run-attention', ?, 'agora', 'codex', 'running', 'x',
                       '.', 60, ?, 'runtime')""",
            (task_id, now),
        )
    capture = attention_store.create_bridge_event(
        BridgeEventRequest(
            vendor="codex",
            vendor_event_id="capture-1",
            task_id=task_id,
            run_id="run-attention",
            kind="question",
            title="Captured question",
            requester="codex-bridge",
            delivery_mode=DeliveryMode.CAPTURE_ONLY,
        )
    )
    bidirectional = attention_store.create_bridge_event(
        BridgeEventRequest(
            vendor="codex",
            vendor_event_id="bidirectional-1",
            task_id=task_id,
            run_id="run-attention",
            kind="question",
            title="Bidirectional question",
            requester="codex-bridge",
            delivery_mode=DeliveryMode.BIDIRECTIONAL,
        ),
        trusted_bidirectional=True,
    )
    client = TestClient(
        _app(
            store,
            _principal("control_plane.attention.respond"),
            attention_store,
        )
    )
    capture_response = client.post(
        f"{_base(task_id)}/attention/{capture.item_id}/responses",
        json=_attention_response(
            capture.item_id,
            operation_key="capture-response",
        ),
    )
    delivery_response = client.post(
        f"{_base(task_id)}/attention/{bidirectional.item_id}/responses",
        json=_attention_response(
            bidirectional.item_id,
            operation_key="bidirectional-response",
        ),
    )

    assert capture_response.json()["response_effect"] == "capture_only_recorded"
    assert delivery_response.json()["response_effect"] == "delivery_ready"
    with store.tasks._connect() as db:
        states = {
            row["item_id"]: row["delivery_state"]
            for row in db.execute(
                """SELECT item_id, delivery_state FROM attention_bridge_events
                   WHERE item_id IN (?, ?)""",
                (capture.item_id, bidirectional.item_id),
            )
        }
    assert states == {
        capture.item_id: "pending",
        bidirectional.item_id: "ready",
    }


def test_attention_response_rolls_back_item_event_delivery_and_receipt(
    tmp_path,
):
    store, task_id = _store(tmp_path)
    attention_store = AttentionStore(store.tasks)
    item = attention_store.create(
        CreateAttentionRequest(
            task_id=task_id,
            kind="question",
            title="Rollback response.",
            requester="runtime",
        )
    )
    event_count = len(store.tasks.events(task_id))
    with store.tasks._transaction() as db:
        db.execute(
            """CREATE TRIGGER fail_attention_shared_operation
               AFTER INSERT ON control_operations
               WHEN NEW.operation_key = 'rollback-response'
               BEGIN
                   SELECT RAISE(ABORT, 'injected shared receipt failure');
               END"""
        )
    response = TestClient(
        _app(
            store,
            _principal("control_plane.attention.respond"),
            attention_store,
        )
    ).post(
        f"{_base(task_id)}/attention/{item.item_id}/responses",
        json=_attention_response(item.item_id, operation_key="rollback-response"),
    )

    assert response.status_code == 500
    assert attention_store.get(item.item_id).state.value == "open"
    assert len(store.tasks.events(task_id)) == event_count
    with store.tasks._connect() as db:
        assert db.execute(
            """SELECT COUNT(*) FROM control_plane_attention_response_operations
               WHERE operation_key = 'rollback-response'"""
        ).fetchone()[0] == 0
        assert db.execute(
            """SELECT COUNT(*) FROM control_operations
               WHERE operation_key = 'rollback-response'"""
        ).fetchone()[0] == 0


def test_task_discovery_is_authoritative_bounded_and_plan_scoped(tmp_path):
    store, task_id = _store(tmp_path)
    _prepare_unified_projection(store, task_id)
    undiscoverable = store.tasks.create(
        CreateTaskRequest(
            project_id="agora",
            title="No authority or plan",
            kind="implementation",
        )
    )
    authority_only = store.tasks.create(
        CreateTaskRequest(
            project_id="agora",
            title="Authority without plan",
            kind="implementation",
        )
    )
    store.ensure_task_state(authority_only.task_id, actor="test")
    plan_only = store.tasks.create(
        CreateTaskRequest(
            project_id="agora",
            title="Plan without authority",
            kind="implementation",
        )
    )
    OrchestrationStore(store.tasks).create_plan(
        plan_only.task_id,
        FOUNDATION_METHODOLOGY,
        total_token_budget=60_000,
        total_cost_budget_usd=10.0,
        actor="test",
    )
    with store.tasks._transaction() as db:
        db.execute(
            "UPDATE tasks SET state = 'done' WHERE task_id = ?",
            (task_id,),
        )

    response = TestClient(_app(store, _principal("control_plane.read"))).get(
        f"{_index_base()}?limit=1&offset=0"
    )

    assert response.status_code == 200
    index = response.json()
    assert index["schema_version"] == "1.0"
    assert index["project_id"] == "agora"
    assert index["page"] == {"limit": 1, "offset": 0, "total": 1}
    assert [item["task_id"] for item in index["tasks"]] == [task_id]
    discovered_ids = {item["task_id"] for item in index["tasks"]}
    assert undiscoverable.task_id not in discovered_ids
    assert authority_only.task_id not in discovered_ids
    assert plan_only.task_id not in discovered_ids
    assert index["tasks"][0]["task_state"] == "backlog"
    assert index["tasks"][0]["task_state_source"] == "control_plane"
    assert index["tasks"][0]["compatibility_state"] == "done"


def test_task_discovery_paginates_one_stable_total_in_deterministic_order(tmp_path):
    store, first_id = _store(tmp_path)
    _prepare_unified_projection(store, first_id)
    task_ids = [first_id]
    for title in ("Second", "Third"):
        task = store.tasks.create(
            CreateTaskRequest(
                project_id="agora",
                title=title,
                kind="implementation",
            )
        )
        _prepare_unified_projection(store, task.task_id)
        task_ids.append(task.task_id)
    with store.tasks._transaction() as db:
        db.execute(
            "UPDATE tasks SET updated_at = ? WHERE task_id = ?",
            ("2026-08-01T00:00:00+00:00", task_ids[0]),
        )
        for task_id in task_ids[1:]:
            db.execute(
                "UPDATE tasks SET updated_at = ? WHERE task_id = ?",
                ("2026-08-02T00:00:00+00:00", task_id),
            )
    expected = sorted(task_ids[1:]) + [task_ids[0]]
    client = TestClient(_app(store, _principal("control_plane.read")))

    first_page = client.get(f"{_index_base()}?limit=2&offset=0").json()
    second_page = client.get(f"{_index_base()}?limit=2&offset=2").json()

    assert first_page["page"] == {"limit": 2, "offset": 0, "total": 3}
    assert [item["task_id"] for item in first_page["tasks"]] == expected[:2]
    assert second_page["page"] == {"limit": 2, "offset": 2, "total": 3}
    assert [item["task_id"] for item in second_page["tasks"]] == expected[2:]


def test_task_discovery_accepts_the_minimum_legal_priority(tmp_path):
    store, _ = _store(tmp_path)
    task = store.tasks.create(
        CreateTaskRequest(
            project_id="agora",
            title="Priority zero",
            kind="implementation",
            priority=0,
        )
    )
    _prepare_unified_projection(store, task.task_id)

    response = TestClient(_app(store, _principal("control_plane.read"))).get(
        _index_base()
    )

    assert response.status_code == 200
    item = next(
        item for item in response.json()["tasks"] if item["task_id"] == task.task_id
    )
    assert item["priority"] == 0


def test_task_discovery_enforces_project_membership(tmp_path):
    store, _ = _store(tmp_path)
    response = TestClient(
        _app(store, _principal("control_plane.read", projects=("other",)))
    ).get(_index_base())

    assert response.status_code == 403


def test_unified_projection_exposes_authoritative_state_not_legacy_state(tmp_path):
    store, task_id = _store(tmp_path)
    _prepare_unified_projection(store, task_id)
    with store.tasks._transaction() as db:
        db.execute(
            "UPDATE tasks SET state = 'done' WHERE task_id = ?",
            (task_id,),
        )

    response = TestClient(_app(store, _principal("control_plane.read"))).get(
        f"{_base(task_id)}/unified-projection"
    )

    assert response.status_code == 200
    projection = response.json()
    assert projection["schema_version"] == "12.0"
    assert projection["task"]["state"] == "done"
    assert projection["task_state"] == "backlog"
    assert projection["task_state_source"] == "control_plane"
    assert projection["task_state_version"] == 1
    assert projection["task_state_lifecycle"] == "unavailable"
    assert projection["task_lifecycle_decision"] is None
    assert projection["plan"]["task_id"] == task_id
    assert projection["collection_pages"]["runs"] == {
        "limit": 100,
        "offset": 0,
        "total": 0,
    }


def test_unified_projection_is_read_only_and_bounded(tmp_path):
    store, task_id = _store(tmp_path)
    _prepare_unified_projection(store, task_id)

    with store.tasks._connect() as db:
        before = "\n".join(db.iterdump())

    response = TestClient(_app(store, _principal("control_plane.read"))).get(
        f"{_base(task_id)}/unified-projection?limit=1&offset=0"
    )

    assert response.status_code == 200
    assert response.json()["collection_pages"]["audit_events"]["limit"] == 1
    with store.tasks._connect() as db:
        after = "\n".join(db.iterdump())
    assert after == before


def test_unified_projection_production_dependency_is_initialized_before_get(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "production-dependency.db"
    monkeypatch.setattr(
        "agora.tasks.router.get_config",
        lambda: {"control_plane": {"db_path": str(db_path)}},
    )
    get_task_store.cache_clear()
    initialize_control_plane_store.cache_clear()
    try:
        store = initialize_control_plane_store()
        task = store.tasks.create(
            CreateTaskRequest(
                project_id="agora",
                title="Production dependency",
                kind="implementation",
            )
        )
        _prepare_unified_projection(store, task.task_id)
        with store.tasks._connect() as db:
            before = "\n".join(db.iterdump())

        app = FastAPI()
        app.include_router(router, prefix="/api")
        app.include_router(task_discovery_router, prefix="/api")
        app.dependency_overrides[authenticate_control_plane] = lambda: _principal(
            "control_plane.read"
        )
        response = TestClient(app).get(
            f"{_base(task.task_id)}/unified-projection"
        )
        discovery = TestClient(app).get(_index_base())

        assert response.status_code == 200
        assert discovery.status_code == 200
        with store.tasks._connect() as db:
            after = "\n".join(db.iterdump())
        assert after == before
    finally:
        initialize_control_plane_store.cache_clear()
        get_task_store.cache_clear()


def test_unified_projection_requires_an_orchestration_plan(tmp_path):
    store, task_id = _store(tmp_path)
    response = TestClient(_app(store, _principal("control_plane.read"))).get(
        f"{_base(task_id)}/unified-projection"
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Control Plane resource not found"


def test_gate_flow_and_projection_are_task_scoped_and_audited(tmp_path):
    store, task_id = _store(tmp_path)
    principal = _principal("control_plane.read", "control_plane.register", "control_plane.evaluate")
    client = TestClient(_app(store, principal))
    base = _base(task_id)
    AttentionStore(store.tasks).create(
        CreateAttentionRequest(
            task_id=task_id,
            kind="question",
            title="Confirm the review evidence.",
            requester="runtime",
        )
    )

    configured = client.put(
        f"{base}/gates/review-gate",
        json={"stage_key": "review-stage", "requirements": [_requirement()]},
    )
    assert configured.status_code == 200

    evidence = _evidence(task_id)
    assert client.post(f"{base}/evidence", json=evidence).status_code == 200
    selected = client.put(
        f"{base}/gates/review-gate/active-evidence",
        json={"evidence_ids": ["evidence-review"], "expected_gate_version": 1, "operation_key": "select-review"},
    )
    assert selected.status_code == 200
    evaluated = client.post(
        f"{base}/gates/review-gate/evaluations",
        json={"expected_gate_version": selected.json()["version"], "operation_key": "evaluate-review"},
    )
    assert evaluated.status_code == 200
    assert evaluated.json()["status"] == "passed"

    projection = client.get(f"{base}/projection?limit=2").json()
    assert projection["task"]["task_id"] == task_id
    assert projection["gates"][0]["status"] == "passed"
    assert projection["collection_totals"]["evidence"] == 1
    assert projection["collection_totals"]["attention"] == 1
    assert projection["collection_pages"]["evidence"] == {
        "limit": 200,
        "offset": 0,
        "total": 1,
    }
    assert projection["attention"][0]["task_id"] == task_id
    assert projection["budget"] == projection["task"]["budget"]
    assert projection["next_safe_action"]["value"] is None
    assert projection["next_safe_action"]["unavailable_reason"]
    assert projection["event_page"]["total"] >= len(projection["events"])
    assert {event["actor"] for event in projection["events"]} == {"principal-api"}
    assert client.get(f"{base}/stages/review-stage").status_code == 200
    assert client.get(f"{base}/gates/review-gate").status_code == 200
    event_page = client.get(f"{base}/events?limit=1").json()
    assert len(event_page["events"]) == 1
    assert event_page["page"]["total"] >= 3


def test_payload_scope_and_approval_actor_cannot_be_spoofed(tmp_path):
    store, task_id = _store(tmp_path)
    principal = _principal("control_plane.register", "control_plane.approve")
    client = TestClient(_app(store, principal))
    base = _base(task_id)
    payload = {
        "schema_version": "1.0",
        "artifact_id": "artifact-x",
        "project_id": "other",
        "task_id": task_id,
        "stage_key": "review-stage",
        "producer": {"runtime": "codex", "run_id": "run-x", "stage_key": "review-stage"},
        "kind": "review",
        "storage": "managed",
        "version": 1,
        "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "content": "",
        "created_at": NOW.isoformat(),
    }
    assert client.post(f"{base}/artifacts", json=payload).status_code == 422

    approval = {
        "schema_version": "1.0",
        "approval_id": "approval-x",
        "project_id": "agora",
        "task_id": task_id,
        "stage_key": "review-stage",
        "gate_key": "review-gate",
        "repository_id": "repo",
        "ref": "refs/heads/main",
        "commit_sha": COMMIT,
        "artifact_versions": [
            {
                "repository_id": "repo",
                "ref": "refs/heads/main",
                "commit_sha": COMMIT,
                "path": "docs/review.md",
                "sha256": "a" * 64,
            }
        ],
        "status": "active",
        "approved_by": "spoofed-principal",
        "approved_at": NOW.isoformat(),
        "stale_reason": None,
    }
    response = client.post(f"{base}/approvals", json=approval)
    assert response.status_code == 422
    assert response.json()["detail"] == (
        "approved_by must match the authenticated principal"
    )


def test_projection_read_does_not_expire_attention(tmp_path):
    store, task_id = _store(tmp_path)
    attention = AttentionStore(store.tasks).create(
        CreateAttentionRequest(
            task_id=task_id,
            kind="question",
            title="Remain a read-only projection.",
            requester="runtime",
        )
    )
    with store.tasks._transaction() as db:
        db.execute(
            "UPDATE attention_items SET expires_at = ? WHERE item_id = ?",
            ("2020-01-01T00:00:00+00:00", attention.item_id),
        )
        event_count = db.execute(
            "SELECT COUNT(*) FROM task_events WHERE task_id = ?",
            (task_id,),
        ).fetchone()[0]

    client = TestClient(_app(store, _principal("control_plane.read")))
    response = client.get(f"{_base(task_id)}/projection")
    assert response.status_code == 200
    assert response.json()["attention"][0]["state"] == "open"

    with store.tasks._transaction() as db:
        row = db.execute(
            "SELECT state FROM attention_items WHERE item_id = ?",
            (attention.item_id,),
        ).fetchone()
        assert row["state"] == "open"
        assert db.execute(
            "SELECT COUNT(*) FROM task_events WHERE task_id = ?",
            (task_id,),
        ).fetchone()[0] == event_count


def test_projection_rows_and_totals_share_one_snapshot(tmp_path, monkeypatch):
    store, task_id = _store(tmp_path)
    principal = _principal("control_plane.read", "control_plane.register")
    client = TestClient(_app(store, principal))
    base = _base(task_id)
    assert client.put(
        f"{base}/gates/review-gate",
        json={"stage_key": "review-stage", "requirements": [_requirement()]},
    ).status_code == 200
    assert client.post(f"{base}/evidence", json=_evidence(task_id)).status_code == 200

    original = AttentionStore.list_snapshot.__func__

    def insert_during_snapshot(cls, db, **kwargs):
        store.register_evidence(
            Evidence.model_validate(_evidence(task_id, "evidence-concurrent"))
        )
        return original(cls, db, **kwargs)

    monkeypatch.setattr(
        AttentionStore,
        "list_snapshot",
        classmethod(insert_during_snapshot),
    )
    projection = client.get(f"{base}/projection").json()
    assert [item["evidence_id"] for item in projection["evidence"]] == [
        "evidence-review"
    ]
    assert projection["collection_totals"]["evidence"] == 1
    assert projection["collection_pages"]["evidence"]["total"] == 1
    assert store.get_evidence("evidence-concurrent") is not None


def test_projection_selects_the_highest_priority_gate_action(tmp_path):
    store, task_id = _store(tmp_path)
    principal = _principal(
        "control_plane.read",
        "control_plane.register",
        "control_plane.evaluate",
    )
    client = TestClient(_app(store, principal))
    base = _base(task_id)
    low = {
        **_requirement(),
        "requirement_id": "low-priority",
        "priority": 100,
        "failure_action": "Handle the lower-priority blocker.",
    }
    high = {
        **_requirement(),
        "requirement_id": "high-priority",
        "priority": 1,
        "failure_action": "Handle the urgent blocker.",
    }
    assert client.put(
        f"{base}/gates/a-low-gate",
        json={"stage_key": "low-stage", "requirements": [low]},
    ).status_code == 200
    assert client.put(
        f"{base}/gates/z-high-gate",
        json={"stage_key": "high-stage", "requirements": [high]},
    ).status_code == 200
    for gate_key in ("a-low-gate", "z-high-gate"):
        assert client.post(
            f"{base}/gates/{gate_key}/evaluations",
            json={
                "expected_gate_version": 1,
                "operation_key": f"evaluate-{gate_key}",
            },
        ).status_code == 200

    action = client.get(f"{base}/projection").json()["next_safe_action"]
    assert action == {
        "value": "Handle the urgent blocker.",
        "source_gate_key": "z-high-gate",
        "unavailable_reason": None,
    }


def test_sqlite_lock_is_sanitized_as_retryable_unavailable(tmp_path, monkeypatch):
    store, task_id = _store(tmp_path)

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked: private detail")

    monkeypatch.setattr(store, "projection", locked)
    response = TestClient(_app(store, _principal("control_plane.read"))).get(
        f"{_base(task_id)}/projection"
    )
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.json()["detail"] == "Control Plane is temporarily unavailable"
