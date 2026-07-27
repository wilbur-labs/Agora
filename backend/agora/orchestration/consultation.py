"""Fail-closed parsing for native consultation candidate drafts."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from pydantic import ValidationError

from agora.protocol.models import (
    ConsultationCandidateDraft,
    ProcessStatus,
    SchemaStatus,
    TransportStatus,
)

from .runtime import RuntimeResult


MAX_CONSULTATION_DRAFT_BYTES = 16 * 1024
_FENCE = re.compile(
    r"\A\s*```(?:json)?\s*\r?\n(?P<body>[\s\S]*?)\r?\n```\s*\Z",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ConsultationAdapterResult:
    process_status: ProcessStatus
    transport_status: TransportStatus
    schema_status: SchemaStatus
    repair_attempts: int
    draft: ConsultationCandidateDraft | None
    error_code: str | None


def adapt_consultation_output(
    result: RuntimeResult,
    *,
    expected_decision_key: str,
) -> ConsultationAdapterResult:
    """Accept exact draft JSON or one whole-document fence removal."""

    if not result.process_started:
        return _failure(
            ProcessStatus.LAUNCH_FAILED,
            TransportStatus.FAILED,
            SchemaStatus.PENDING,
            "process_launch_failed",
        )
    if result.timed_out:
        return _failure(
            ProcessStatus.TIMED_OUT,
            TransportStatus.FAILED,
            SchemaStatus.PENDING,
            "process_timed_out",
        )
    if result.exit_code is None:
        return _failure(
            ProcessStatus.INTERRUPTED,
            TransportStatus.FAILED,
            SchemaStatus.PENDING,
            "process_interrupted",
        )
    if result.exit_code != 0:
        return _failure(
            ProcessStatus.EXITED,
            TransportStatus.COMPLETED,
            SchemaStatus.PENDING,
            "process_nonzero_exit",
        )

    try:
        raw = result.stdout.encode("utf-8", errors="strict")
    except UnicodeError:
        return _failure(
            ProcessStatus.EXITED,
            TransportStatus.COMPLETED,
            SchemaStatus.PROTOCOL_FAILED,
            "candidate_encoding_invalid",
        )
    if len(raw) > MAX_CONSULTATION_DRAFT_BYTES:
        return _failure(
            ProcessStatus.EXITED,
            TransportStatus.COMPLETED,
            SchemaStatus.PROTOCOL_FAILED,
            "candidate_too_large",
        )

    parsed, repaired = _parse_json(result.stdout)
    if parsed is None:
        return _failure(
            ProcessStatus.EXITED,
            TransportStatus.COMPLETED,
            SchemaStatus.PROTOCOL_FAILED,
            "candidate_json_invalid",
            repair_attempts=int(repaired),
        )
    try:
        draft = ConsultationCandidateDraft.model_validate(parsed)
    except ValidationError:
        return _failure(
            ProcessStatus.EXITED,
            TransportStatus.COMPLETED,
            SchemaStatus.PROTOCOL_FAILED,
            "candidate_schema_invalid",
            repair_attempts=int(repaired),
        )
    if draft.decision_key != expected_decision_key:
        return _failure(
            ProcessStatus.EXITED,
            TransportStatus.COMPLETED,
            SchemaStatus.PROTOCOL_FAILED,
            "candidate_decision_key_mismatch",
            repair_attempts=int(repaired),
        )
    return ConsultationAdapterResult(
        process_status=ProcessStatus.EXITED,
        transport_status=TransportStatus.COMPLETED,
        schema_status=(
            SchemaStatus.REPAIRED if repaired else SchemaStatus.VALID
        ),
        repair_attempts=int(repaired),
        draft=draft,
        error_code=None,
    )


def _parse_json(value: str) -> tuple[object | None, bool]:
    try:
        return json.loads(value), False
    except json.JSONDecodeError:
        match = _FENCE.fullmatch(value)
        if match is None:
            return None, False
        try:
            return json.loads(match.group("body")), True
        except json.JSONDecodeError:
            return None, True


def _failure(
    process_status: ProcessStatus,
    transport_status: TransportStatus,
    schema_status: SchemaStatus,
    error_code: str,
    *,
    repair_attempts: int = 0,
) -> ConsultationAdapterResult:
    return ConsultationAdapterResult(
        process_status=process_status,
        transport_status=transport_status,
        schema_status=schema_status,
        repair_attempts=repair_attempts,
        draft=None,
        error_code=error_code,
    )
