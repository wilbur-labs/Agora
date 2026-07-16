"""Bounded protocol-schema repair decisions."""
from __future__ import annotations

from enum import Enum

from pydantic import Field

from .models import ProtocolModel


class RepairAction(str, Enum):
    ACCEPT = "accept"
    REQUEST_FORMAT_REPAIR = "request_format_repair"
    PROTOCOL_FAILED = "protocol_failed"


class RepairDecision(ProtocolModel):
    action: RepairAction
    repair_attempts: int = Field(ge=0, le=1)
    attention_required: bool
    semantic_changes_allowed: bool = False


def decide_schema_repair(
    *,
    schema_valid: bool,
    repair_attempts: int,
) -> RepairDecision:
    if repair_attempts not in {0, 1}:
        raise ValueError("repair_attempts must be 0 or 1")
    if schema_valid:
        return RepairDecision(
            action=RepairAction.ACCEPT,
            repair_attempts=repair_attempts,
            attention_required=False,
        )
    if repair_attempts == 0:
        return RepairDecision(
            action=RepairAction.REQUEST_FORMAT_REPAIR,
            repair_attempts=1,
            attention_required=False,
        )
    return RepairDecision(
        action=RepairAction.PROTOCOL_FAILED,
        repair_attempts=1,
        attention_required=True,
    )
