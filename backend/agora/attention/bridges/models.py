from __future__ import annotations

from enum import Enum
import json
from typing import Any

from pydantic import BaseModel, Field, field_validator

from ..models import AttentionKind, AttentionUrgency


class BridgeVendor(str, Enum):
    CLAUDE = "claude"
    CODEX = "codex"
    KIRO = "kiro"


class DeliveryMode(str, Enum):
    CAPTURE_ONLY = "capture_only"
    BIDIRECTIONAL = "bidirectional"


class BridgeEventRequest(BaseModel):
    vendor: BridgeVendor
    vendor_event_id: str = Field(min_length=1, max_length=256)
    task_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    kind: AttentionKind
    urgency: AttentionUrgency = AttentionUrgency.NORMAL
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(default="", max_length=32_000)
    options: list[str] = Field(default_factory=list, max_length=20)
    requester: str = Field(min_length=1, max_length=128)
    delivery_mode: DeliveryMode = DeliveryMode.CAPTURE_ONLY
    correlation: dict[str, Any] = Field(default_factory=dict)

    @field_validator("vendor_event_id", "title")
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value may not be blank")
        return value

    @field_validator("options")
    @classmethod
    def valid_options(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 500 for value in values):
            raise ValueError("options must be non-blank and at most 500 characters")
        if len(set(values)) != len(values):
            raise ValueError("options must be unique")
        return values

    @field_validator("correlation")
    @classmethod
    def bounded_correlation(cls, value: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(value, ensure_ascii=False, default=str)
        if len(encoded.encode("utf-8")) > 16_384:
            raise ValueError("correlation must be at most 16384 UTF-8 bytes")
        return value


class BridgeEventReceipt(BaseModel):
    item_id: str
    created: bool
    delivery_mode: DeliveryMode
