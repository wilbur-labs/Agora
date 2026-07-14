from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from agora.api.app import app
from agora.attention.models import (
    AttentionKind, AttentionState, CancelAttentionRequest, CreateAttentionRequest,
    RespondAttentionRequest, ResponseAction,
)
from agora.attention.router import get_attention_store
from agora.attention.store import AttentionConflictError, AttentionNotFoundError, AttentionStore, AttentionValidationError
from agora.attention.bridges.models import BridgeEventRequest, BridgeVendor, DeliveryMode
from agora.attention.bridges.normalize import normalize_hook_event
from agora.attention.bridges.hook_cli import main as hook_cli_main
from agora.tasks.models import AppendEventRequest, CreateTaskRequest
from agora.tasks.store import TaskStore


def _system(tmp_path):
    tasks = TaskStore(tmp_path / "agora.db")
    task = tasks.create(CreateTaskRequest(project_id="alpha", title="Ship feature"))
    return tasks, AttentionStore(tasks), task


def _request(task_id: str, **overrides) -> CreateAttentionRequest:
    values = dict(task_id=task_id, kind="question", title="Choose deployment region",
                  body="The agent needs a human decision.", options=["Tokyo", "Osaka"],
                  requester="codex", context={"token": "secret-value", "reason": "latency"})
    values.update(overrides)
    return CreateAttentionRequest(**values)


def test_create_list_get_and_audit_event(tmp_path):
    tasks, store, task = _system(tmp_path)
    item = store.create(_request(task.task_id, urgency="critical"))

    assert item.project_id == "alpha"
    assert item.state == AttentionState.OPEN
    assert item.context == {"token": "[REDACTED]", "reason": "latency"}
    assert store.require(item.item_id).options == ["Tokyo", "Osaka"]
    assert store.list(project_id="alpha", kind=AttentionKind.QUESTION)[0].item_id == item.item_id
    assert store.open_count() == 1
    assert tasks.events(task.task_id)[-1].event_type == "attention.created"
    with pytest.raises(ValueError, match="reserved"):
        AppendEventRequest(event_type="attention.created")

    secret = store.create(_request(
        task.task_id, title="access_token=title-secret", body="password=body-secret",
    ))
    assert secret.title == "access_token=[REDACTED]"
    assert secret.body == "password=[REDACTED]"


def test_create_validates_task_and_run_relationship(tmp_path):
    tasks, store, task = _system(tmp_path)
    other = tasks.create(CreateTaskRequest(project_id="beta", title="Other"))
    now = datetime.now(timezone.utc).isoformat()
    with tasks._transaction() as db:
        db.execute(
            """INSERT INTO execution_runs
               (run_id, task_id, project_id, adapter, state, prompt, workspace, timeout_seconds, queued_at, actor)
               VALUES ('run_other', ?, 'beta', 'codex', 'queued', 'x', '.', 60, ?, 'user')""",
            (other.task_id, now),
        )
    with pytest.raises(AttentionNotFoundError, match="Task"):
        store.create(_request("missing"))
    with pytest.raises(AttentionNotFoundError, match="Run"):
        store.create(_request(task.task_id, run_id="missing"))
    with pytest.raises(AttentionValidationError, match="does not belong"):
        store.create(_request(task.task_id, run_id="run_other"))


def test_respond_and_cancel_use_optimistic_concurrency(tmp_path):
    tasks, store, task = _system(tmp_path)
    item = store.create(_request(task.task_id))
    responded = store.respond(item.item_id, RespondAttentionRequest(
        action=ResponseAction.ANSWER, response="Tokyo access_token=response-secret", actor="wilbur", expected_version=1,
    ))
    assert responded.state == AttentionState.RESPONDED
    assert responded.version == 2 and responded.responded_by == "wilbur"
    assert responded.response == "Tokyo access_token=[REDACTED]"
    assert tasks.events(task.task_id)[-1].event_type == "attention.responded"
    with pytest.raises(AttentionConflictError, match="already responded"):
        store.respond(item.item_id, RespondAttentionRequest(
            action="answer", response="Osaka", expected_version=1,
        ))

    second = store.create(_request(task.task_id, title="Approve migration", kind="approval"))
    with pytest.raises(AttentionConflictError, match="Expected version"):
        store.cancel(second.item_id, CancelAttentionRequest(expected_version=9))
    cancelled = store.cancel(second.item_id, CancelAttentionRequest(expected_version=1, reason="superseded password=cancel-secret"))
    assert cancelled.state == AttentionState.CANCELLED
    assert cancelled.cancellation_reason == "superseded password=[REDACTED]"
    assert tasks.events(task.task_id)[-1].payload["reason"] == "superseded password=[REDACTED]"


def test_expiry_is_durable_and_emits_event(tmp_path):
    tasks, store, task = _system(tmp_path)
    item = store.create(_request(
        task.task_id, expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    ))
    with tasks._transaction() as db:
        db.execute("UPDATE attention_items SET expires_at = ? WHERE item_id = ?",
                   ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), item.item_id))
    assert store.require(item.item_id).state == AttentionState.EXPIRED
    assert store.open_count() == 0
    assert tasks.events(task.task_id)[-1].event_type == "attention.expired"


@pytest.mark.parametrize("operation", ["respond", "cancel"])
def test_expired_item_cannot_be_mutated_without_a_prior_read(tmp_path, operation):
    tasks, store, task = _system(tmp_path)
    item = store.create(_request(
        task.task_id, expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    ))
    with tasks._transaction() as db:
        db.execute("UPDATE attention_items SET expires_at = ? WHERE item_id = ?",
                   ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), item.item_id))
    with pytest.raises(AttentionConflictError, match="already expired"):
        if operation == "respond":
            store.respond(item.item_id, RespondAttentionRequest(
                action="answer", response="too late", expected_version=1,
            ))
        else:
            store.cancel(item.item_id, CancelAttentionRequest(expected_version=1))
    with tasks._connect() as db:
        row = db.execute("SELECT state, version FROM attention_items WHERE item_id = ?", (item.item_id,)).fetchone()
    assert tuple(row) == ("expired", 2)
    assert tasks.events(task.task_id)[-1].event_type == "attention.expired"


def test_attention_api_lifecycle_and_conflicts(tmp_path):
    tasks, store, task = _system(tmp_path)
    app.dependency_overrides[get_attention_store] = lambda: store
    try:
        with TestClient(app) as client:
            created = client.post("/api/attention", json=_request(task.task_id).model_dump(mode="json"))
            assert created.status_code == 201
            item = created.json()
            assert client.get("/api/attention", params={"state": "open"}).json()[0]["item_id"] == item["item_id"]
            assert client.get("/api/attention/count").json() == {"open": 1}
            response = client.post(f"/api/attention/{item['item_id']}/respond", json={
                "action": "answer", "response": "Tokyo", "actor": "user", "expected_version": 1,
            })
            assert response.status_code == 200 and response.json()["state"] == "responded"
            stale = client.post(f"/api/attention/{item['item_id']}/cancel", json={
                "actor": "user", "expected_version": 1,
            })
            assert stale.status_code == 409
            assert client.get("/api/attention/missing").status_code == 404
    finally:
        app.dependency_overrides.clear()


def _insert_active_run(tasks: TaskStore, task_id: str, project_id: str = "alpha") -> str:
    now = datetime.now(timezone.utc).isoformat()
    run_id = f"run_{task_id[-8:]}"
    with tasks._transaction() as db:
        db.execute(
            """INSERT INTO execution_runs
               (run_id, task_id, project_id, adapter, state, prompt, workspace, timeout_seconds, queued_at, actor)
               VALUES (?, ?, ?, 'codex', 'running', 'x', '.', 60, ?, 'user')""",
            (run_id, task_id, project_id, now),
        )
    return run_id


def test_bridge_event_is_normalized_and_atomically_deduplicated(tmp_path):
    tasks, store, task = _system(tmp_path)
    run_id = _insert_active_run(tasks, task.task_id)
    event = normalize_hook_event(BridgeVendor.CODEX, {
        "hook_event_name": "PermissionRequest", "session_id": "session-1",
        "tool_use_id": "tool-1", "tool_name": "Bash",
        "tool_input": {"command": "echo access_token=bridge-secret"},
    }, task_id=task.task_id, run_id=run_id)

    first = store.create_bridge_event(event)
    second = store.create_bridge_event(event)
    assert first.created is True and second.created is False
    assert first.item_id == second.item_id
    item = store.require(first.item_id)
    assert item.context["bridge"]["delivery_mode"] == "capture_only"
    assert "bridge-secret" not in item.body
    assert store.open_count() == 1
    assert tasks.events(task.task_id)[-1].event_type == "attention.bridge_captured"

    retry = normalize_hook_event(BridgeVendor.CODEX, {
        "hook_event_name": "PermissionRequest", "session_id": "session-1",
        "tool_name": "Bash", "tool_input": {"command": "echo stable"}, "timestamp": "first",
    }, task_id=task.task_id, run_id=run_id)
    retried = normalize_hook_event(BridgeVendor.CODEX, {
        "hook_event_name": "PermissionRequest", "session_id": "session-1",
        "tool_name": "Bash", "tool_input": {"command": "echo stable"}, "timestamp": "second",
    }, task_id=task.task_id, run_id=run_id)
    assert retry.vendor_event_id == retried.vendor_event_id


def test_bridge_ingress_rejects_unverified_delivery_and_terminal_runs(tmp_path):
    tasks, store, task = _system(tmp_path)
    run_id = _insert_active_run(tasks, task.task_id)
    base = dict(vendor="claude", vendor_event_id="event-1", task_id=task.task_id,
                run_id=run_id, kind="approval", title="Permission", requester="claude-bridge")
    with pytest.raises(AttentionValidationError, match="capture_only"):
        store.create_bridge_event(BridgeEventRequest(**base, delivery_mode=DeliveryMode.BIDIRECTIONAL))
    with tasks._transaction() as db:
        db.execute("UPDATE execution_runs SET state = 'failed' WHERE run_id = ?", (run_id,))
    with pytest.raises(AttentionValidationError, match="active run"):
        store.create_bridge_event(BridgeEventRequest(**base))

    with pytest.raises(ValueError, match="unique"):
        BridgeEventRequest(**base, options=["same", "same"])
    with pytest.raises(ValueError, match="16384"):
        BridgeEventRequest(**base, correlation={"payload": "x" * 17_000})


def test_hook_cli_configuration_failure_is_never_a_blocking_exit(monkeypatch):
    monkeypatch.delenv("AGORA_TASK_ID", raising=False)
    monkeypatch.delenv("AGORA_RUN_ID", raising=False)
    assert hook_cli_main(["claude"]) == 1
    assert hook_cli_main(["not-a-vendor"]) == 1
    assert hook_cli_main([]) == 1


def test_hook_cli_malformed_success_response_fails_cleanly(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): return None
        def read(self): return b"{}"

    monkeypatch.setenv("AGORA_TASK_ID", "task_1")
    monkeypatch.setenv("AGORA_RUN_ID", "run_1")
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO('{"event":"notification"}'))
    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: Response())
    assert hook_cli_main(["codex"]) == 1


def test_bridge_ingress_api_returns_idempotent_receipt(tmp_path):
    tasks, store, task = _system(tmp_path)
    run_id = _insert_active_run(tasks, task.task_id)
    app.dependency_overrides[get_attention_store] = lambda: store
    payload = {
        "vendor": "kiro", "vendor_event_id": "wait-1", "task_id": task.task_id,
        "run_id": run_id, "kind": "question", "title": "Kiro needs input",
        "requester": "kiro-bridge", "delivery_mode": "capture_only",
    }
    try:
        with TestClient(app) as client:
            first = client.post("/api/attention/bridge-events", json=payload)
            second = client.post("/api/attention/bridge-events", json=payload)
        assert first.status_code == 200 and first.json()["created"] is True
        assert second.status_code == 200 and second.json() == {
            **first.json(), "created": False,
        }
    finally:
        app.dependency_overrides.clear()
