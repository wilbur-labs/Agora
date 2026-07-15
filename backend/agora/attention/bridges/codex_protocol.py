"""Version-bounded codec for stable Codex 0.144 app-server approvals."""
from __future__ import annotations

import json
from typing import Any

from .models import (
    BridgeDelivery, BridgeEventRequest, BridgeVendor, CodexApprovalCorrelation, DeliveryMode,
)


SUPPORTED_REQUESTS = {
    "item/commandExecution/requestApproval": "Command execution approval",
    "item/fileChange/requestApproval": "File change approval",
}


def decode_approval(message: dict[str, Any], *, task_id: str, run_id: str) -> BridgeEventRequest | None:
    method = message.get("method")
    rpc_id = message.get("id")
    params = message.get("params")
    if method not in SUPPORTED_REQUESTS or rpc_id is None or not isinstance(params, dict):
        return None
    item_id = params.get("itemId")
    thread_id = params.get("threadId")
    turn_id = params.get("turnId")
    if not all(isinstance(value, str) and value for value in (item_id, thread_id, turn_id)):
        raise ValueError("Codex approval is missing item/thread/turn identity")
    command = params.get("command")
    reason = params.get("reason")
    body = "\n".join(part for part in (
        f"Command: {command}" if command else None,
        f"Reason: {reason}" if reason else None,
    ) if part) or json.dumps(params, ensure_ascii=False, sort_keys=True)
    return BridgeEventRequest(
        vendor=BridgeVendor.CODEX,
        vendor_event_id=f"{thread_id}:{turn_id}:{item_id}:{rpc_id}",
        task_id=task_id,
        run_id=run_id,
        kind="approval",
        title=SUPPORTED_REQUESTS[method],
        body=body,
        requester="codex-app-server",
        delivery_mode=DeliveryMode.BIDIRECTIONAL,
        correlation=CodexApprovalCorrelation(
            rpc_id=rpc_id, method=method, thread_id=thread_id,
            turn_id=turn_id, item_id=item_id,
        ).model_dump(),
    )


def encode_approval_response(delivery: BridgeDelivery) -> dict[str, Any]:
    correlation = CodexApprovalCorrelation.model_validate(delivery.correlation)
    if correlation.method not in SUPPORTED_REQUESTS:
        raise ValueError("Unsupported or incomplete Codex approval correlation")
    decisions = {"approve": "accept", "reject": "decline"}
    try:
        decision = decisions[delivery.response_action]
    except KeyError:
        raise ValueError("Codex approvals require approve or reject") from None
    return {"id": correlation.rpc_id, "result": {"decision": decision}}
