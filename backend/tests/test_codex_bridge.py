from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agora.attention.bridges.codex_broker import CodexApprovalBroker
from agora.attention.bridges.codex_protocol import decode_approval, encode_approval_response
from agora.attention.bridges.models import BridgeVendor
from agora.attention.models import CreateAttentionRequest, RespondAttentionRequest
from agora.attention.models import CancelAttentionRequest
from agora.attention.store import AttentionStore, AttentionValidationError
from agora.tasks.models import CreateTaskRequest
from agora.tasks.store import TaskStore


def _system(tmp_path):
    tasks = TaskStore(tmp_path / "agora.db")
    task = tasks.create(CreateTaskRequest(project_id="alpha", title="Codex delivery"))
    run_id = "run_codex_bridge"
    with tasks._transaction() as db:
        db.execute(
            """INSERT INTO execution_runs
               (run_id, task_id, project_id, adapter, state, prompt, workspace, timeout_seconds, queued_at, actor)
               VALUES (?, ?, 'alpha', 'codex', 'running', 'x', '.', 60, ?, 'user')""",
            (run_id, task.task_id, datetime.now(timezone.utc).isoformat()),
        )
    return tasks, AttentionStore(tasks), task, run_id


def _approval(rpc_id=7):
    return {
        "id": rpc_id,
        "method": "item/commandExecution/requestApproval",
        "params": {
            "threadId": "thread-1", "turnId": "turn-1", "itemId": "item-1",
            "startedAtMs": 1, "command": "git push", "reason": "network access",
        },
    }


def test_codex_schema_bounded_approval_codec():
    event = decode_approval(_approval(), task_id="task-1", run_id="run-1")
    assert event is not None
    assert event.delivery_mode.value == "bidirectional"
    assert event.vendor_event_id == "thread-1:turn-1:item-1:7"
    assert decode_approval({"method": "turn/completed"}, task_id="task-1", run_id="run-1") is None
    with pytest.raises(ValueError, match="identity"):
        decode_approval({"id": 1, "method": _approval()["method"], "params": {}}, task_id="t", run_id="r")


@pytest.mark.asyncio
async def test_codex_broker_captures_answers_and_delivers_once(tmp_path):
    tasks, store, task, run_id = _system(tmp_path)
    broker = CodexApprovalBroker(store, task_id=task.task_id, run_id=run_id)
    receipt = broker.capture(_approval())
    duplicate = broker.capture(_approval())
    assert receipt and receipt.created is True
    assert duplicate and duplicate.created is False

    item = store.require(receipt.item_id)
    answered = store.respond(item.item_id, RespondAttentionRequest(
        action="approve", response="approved by owner", expected_version=item.version,
    ))
    sent = []

    async def send(message): sent.append(message)

    assert await broker.deliver_ready(send) is True
    assert sent == [{"id": 7, "result": {"decision": "accept"}}]
    assert await broker.deliver_ready(send) is False
    events = [event.event_type for event in tasks.events(task.task_id)]
    assert events[-1] == "attention.delivery_delivered"
    assert answered.state.value == "responded"


@pytest.mark.asyncio
async def test_codex_delivery_failure_is_durable_and_redacted(tmp_path):
    tasks, store, task, run_id = _system(tmp_path)
    broker = CodexApprovalBroker(store, task_id=task.task_id, run_id=run_id)
    receipt = broker.capture(_approval(9))
    item = store.require(receipt.item_id)
    store.respond(item.item_id, RespondAttentionRequest(action="reject", expected_version=1))

    async def fail(_message):
        raise RuntimeError("password=delivery-secret")

    assert await broker.deliver_ready(fail) is False
    event = tasks.events(task.task_id)[-1]
    assert event.event_type == "attention.delivery_failed"
    assert event.payload["error"] == "RuntimeError: password=[REDACTED]"


def test_stale_delivery_is_failed_on_recovery(tmp_path):
    tasks, store, task, run_id = _system(tmp_path)
    broker = CodexApprovalBroker(store, task_id=task.task_id, run_id=run_id)
    receipt = broker.capture(_approval())
    store.respond(receipt.item_id, RespondAttentionRequest(action="approve", expected_version=1))
    claimed = store.claim_ready_delivery(run_id, BridgeVendor.CODEX)
    assert claimed is not None
    with tasks._transaction() as db:
        db.execute(
            "UPDATE attention_bridge_events SET claimed_at = '2000-01-01T00:00:00+00:00' WHERE item_id = ?",
            (receipt.item_id,),
        )
    assert store.recover_stale_deliveries(max_age_seconds=1) == 1
    assert tasks.events(task.task_id)[-1].event_type == "attention.delivery_failed"


def test_bridge_open_item_cap_is_enforced(tmp_path):
    _, store, task, run_id = _system(tmp_path)
    store.MAX_OPEN_BRIDGE_ITEMS_PER_RUN = 2
    for rpc_id in (1, 2):
        event = decode_approval(_approval(rpc_id), task_id=task.task_id, run_id=run_id)
        # Item id is part of identity; vary it as a real sequence would.
        event.vendor_event_id += f":{rpc_id}"
        store.create_bridge_event(event, trusted_bidirectional=True)
    third = decode_approval(_approval(3), task_id=task.task_id, run_id=run_id)
    with pytest.raises(AttentionValidationError, match="too many"):
        store.create_bridge_event(third, trusted_bidirectional=True)


@pytest.mark.parametrize("terminal", ["cancelled", "expired"])
def test_terminal_attention_fails_undelivered_codex_approval(tmp_path, terminal):
    tasks, store, task, run_id = _system(tmp_path)
    broker = CodexApprovalBroker(store, task_id=task.task_id, run_id=run_id)
    receipt = broker.capture(_approval())
    if terminal == "cancelled":
        store.cancel(receipt.item_id, CancelAttentionRequest(expected_version=1))
    else:
        with tasks._transaction() as db:
            db.execute(
                "UPDATE attention_items SET expires_at = '2000-01-01T00:00:00+00:00' WHERE item_id = ?",
                (receipt.item_id,),
            )
        assert store.require(receipt.item_id).state.value == "expired"
    with tasks._connect() as db:
        row = db.execute(
            "SELECT delivery_state, delivery_error FROM attention_bridge_events WHERE item_id = ?",
            (receipt.item_id,),
        ).fetchone()
    assert tuple(row) == ("failed", f"attention item {terminal}")
    assert tasks.events(task.task_id)[-1].event_type == "attention.delivery_failed"


def test_public_ingress_cannot_claim_bidirectional_and_answer_is_invalid(tmp_path):
    _, store, task, run_id = _system(tmp_path)
    event = decode_approval(_approval(), task_id=task.task_id, run_id=run_id)
    with pytest.raises(AttentionValidationError, match="capture_only"):
        store.create_bridge_event(event)
    receipt = store.create_bridge_event(event, trusted_bidirectional=True)
    with pytest.raises(AttentionValidationError, match="approve or reject"):
        store.respond(receipt.item_id, RespondAttentionRequest(
            action="answer", response="yes", expected_version=1,
        ))
