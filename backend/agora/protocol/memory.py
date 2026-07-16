"""Deterministic M2 publication decisions."""
from __future__ import annotations

from enum import Enum

from .models import GateDecision


class RunOutcome(str, Enum):
    RUNNING = "running"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PROTOCOL_FAILED = "protocol_failed"
    SUCCEEDED = "succeeded"


class M2UpdateAction(str, Enum):
    CANDIDATE_ONLY = "candidate_only"
    PRESERVE_VERIFIED_APPEND_ATTEMPT = "preserve_verified_append_attempt"
    PUBLISH_UNVERIFIED_DRAFT = "publish_unverified_draft"
    PUBLISH_VERIFIED_ATOMIC = "publish_verified_atomic"


def decide_m2_update(
    run_outcome: RunOutcome,
    gate_decision: GateDecision | None = None,
) -> M2UpdateAction:
    if run_outcome == RunOutcome.RUNNING:
        if gate_decision is not None:
            raise ValueError("running runs cannot have a Gate decision")
        return M2UpdateAction.CANDIDATE_ONLY
    if run_outcome in {
        RunOutcome.FAILED,
        RunOutcome.CANCELLED,
        RunOutcome.PROTOCOL_FAILED,
    }:
        if gate_decision == GateDecision.PASS:
            raise ValueError("non-success runs cannot publish a passing Gate")
        return M2UpdateAction.PRESERVE_VERIFIED_APPEND_ATTEMPT
    if gate_decision is None:
        raise ValueError("successful runs require a Gate decision")
    if gate_decision == GateDecision.PASS:
        return M2UpdateAction.PUBLISH_VERIFIED_ATOMIC
    return M2UpdateAction.PUBLISH_UNVERIFIED_DRAFT
