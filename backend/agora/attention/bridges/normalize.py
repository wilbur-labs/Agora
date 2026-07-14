"""Pure vendor payload parsers; no process or network side effects."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .models import BridgeEventRequest, BridgeVendor, DeliveryMode


def normalize_hook_event(vendor: BridgeVendor, payload: dict[str, Any], *, task_id: str, run_id: str) -> BridgeEventRequest:
    """Map supported vendor hook payloads to the neutral capture-only contract."""
    event_name = str(payload.get("hook_event_name") or payload.get("event") or "notification")
    session_id = str(payload.get("session_id") or payload.get("conversation_id") or "unknown")
    tool = str(payload.get("tool_name") or payload.get("tool") or "agent")
    raw_id = payload.get("event_id") or payload.get("tool_use_id") or payload.get("request_id")
    if raw_id is None:
        # Exclude timestamps, nonces, and other per-delivery envelope fields so
        # vendor retries map to one logical event.
        stable = json.dumps({
            "event_name": event_name,
            "session_id": session_id,
            "tool": tool,
            "tool_input": payload.get("tool_input"),
            "message": payload.get("message") or payload.get("notification"),
        }, sort_keys=True, ensure_ascii=False, default=str)
        raw_id = hashlib.sha256(stable.encode("utf-8")).hexdigest()[:32]
    details = payload.get("message") or payload.get("notification") or payload.get("tool_input") or payload
    if not isinstance(details, str):
        details = json.dumps(details, ensure_ascii=False, sort_keys=True, default=str)
    kind = "approval" if event_name.lower() in {"permissionrequest", "permission_request"} else "question"
    options = ["approve", "reject"] if kind == "approval" else []
    return BridgeEventRequest(
        vendor=vendor,
        vendor_event_id=f"{session_id}:{raw_id}",
        task_id=task_id,
        run_id=run_id,
        kind=kind,
        title=f"{vendor.value} {event_name}: {tool}",
        body=details,
        options=options,
        requester=f"{vendor.value}-bridge",
        delivery_mode=DeliveryMode.CAPTURE_ONLY,
        correlation={"event_name": event_name, "session_id": session_id, "tool": tool},
    )
