from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agora.attention.models import CreateAttentionRequest
from agora.attention.store import AttentionStore
from agora.control_plane.auth import ControlPrincipal, authenticate_control_plane
from agora.control_plane.router import get_control_plane_store, router
from agora.control_plane.store import ControlPlaneStore
from agora.protocol.models import Evidence
from agora.tasks.models import CreateTaskRequest
from agora.tasks.store import TaskStore


NOW = datetime(2026, 7, 18, 9, 0, tzinfo=timezone.utc)
COMMIT = "1" * 40


def _app(store: ControlPlaneStore, principal: ControlPrincipal | None) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_control_plane_store] = lambda: store
    if principal is not None:
        app.dependency_overrides[authenticate_control_plane] = lambda: principal
    return app


def _store(tmp_path) -> tuple[ControlPlaneStore, str]:
    tasks = TaskStore(tmp_path / "agora.db")
    task = tasks.create(CreateTaskRequest(project_id="agora", title="API", kind="implementation"))
    return ControlPlaneStore(tasks), task.task_id


def _principal(*permissions: str, projects=("agora",)) -> ControlPrincipal:
    return ControlPrincipal("principal-api", frozenset(permissions), frozenset(projects))


def _base(task_id: str) -> str:
    return f"/api/control-plane/projects/agora/tasks/{task_id}"


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
