"""Common accessors for first-Run and later-Stage dispatch receipts."""
from __future__ import annotations

from typing import TypeAlias

from agora.protocol.methodology_run_dispatch import MethodologyRunDispatchReceipt
from agora.protocol.methodology_stage_run_dispatch import (
    MethodologyStageRunDispatchReceipt,
)


MethodologyPredecessorDispatchReceipt: TypeAlias = (
    MethodologyRunDispatchReceipt | MethodologyStageRunDispatchReceipt
)


def methodology_dispatch_sequence(
    receipt: MethodologyPredecessorDispatchReceipt,
) -> int:
    return (
        1
        if isinstance(receipt, MethodologyRunDispatchReceipt)
        else receipt.dispatch_claim.stage_sequence
    )


def methodology_dispatch_stage_key(
    receipt: MethodologyPredecessorDispatchReceipt,
) -> str:
    claim = receipt.dispatch_claim
    return (
        claim.first_stage_key
        if isinstance(receipt, MethodologyRunDispatchReceipt)
        else claim.stage_key
    )


def methodology_dispatch_gate_key(
    receipt: MethodologyPredecessorDispatchReceipt,
) -> str:
    claim = receipt.dispatch_claim
    return (
        claim.first_gate_key
        if isinstance(receipt, MethodologyRunDispatchReceipt)
        else claim.gate_key
    )


def methodology_dispatch_run_claim_receipt_id(
    receipt: MethodologyPredecessorDispatchReceipt,
) -> str:
    claim = receipt.dispatch_claim
    return (
        claim.run_claim_receipt_id
        if isinstance(receipt, MethodologyRunDispatchReceipt)
        else claim.stage_run_claim_receipt_id
    )


def methodology_dispatch_run_claim_receipt_sha256(
    receipt: MethodologyPredecessorDispatchReceipt,
) -> str:
    claim = receipt.dispatch_claim
    return (
        claim.run_claim_receipt_sha256
        if isinstance(receipt, MethodologyRunDispatchReceipt)
        else claim.stage_run_claim_receipt_sha256
    )
