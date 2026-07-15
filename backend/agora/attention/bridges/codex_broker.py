"""Codex app-server approval broker independent from process supervision."""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from agora.attention.store import AttentionStore

from .codex_protocol import decode_approval, encode_approval_response
from .models import BridgeVendor


class CodexApprovalBroker:
    def __init__(self, store: AttentionStore, *, task_id: str, run_id: str):
        self.store = store
        self.task_id = task_id
        self.run_id = run_id

    def capture(self, message: dict[str, Any]):
        event = decode_approval(message, task_id=self.task_id, run_id=self.run_id)
        if event is None:
            return None
        return self.store.create_bridge_event(event, trusted_bidirectional=True)

    async def deliver_ready(self, send: Callable[[dict[str, Any]], Awaitable[None]]) -> bool:
        delivery = self.store.claim_ready_delivery(self.run_id, BridgeVendor.CODEX)
        if delivery is None:
            return False
        try:
            await send(encode_approval_response(delivery))
        except Exception as exc:
            self.store.finish_delivery(
                delivery.item_id, delivered=False, error=f"{type(exc).__name__}: {exc}",
            )
            return False
        self.store.finish_delivery(delivery.item_id, delivered=True)
        return True

    def capture_json_line(self, line: bytes | str):
        if isinstance(line, bytes):
            line = line.decode("utf-8")
        message = json.loads(line)
        if not isinstance(message, dict):
            raise ValueError("Codex app-server message must be an object")
        return self.capture(message)
