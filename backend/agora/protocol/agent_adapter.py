"""Fail-closed adapter from terminal runner facts to frozen Run protocol state."""
from __future__ import annotations

import json
from enum import Enum
from typing import Any

from pydantic import model_validator

from .models import (
    ContextPack,
    HandoffPack,
    MAX_HANDOFF_PACK_BYTES,
    ProcessStatus,
    ProtocolModel,
    SchemaStatus,
    SemanticStageResult,
    StableId,
    StageResult,
    TransportStatus,
    RunProtocolState,
)


_FORMAT_OVERHEAD_BYTES = 4 * 1024


class AdapterErrorCode(str, Enum):
    PROCESS_LAUNCH_FAILED = "process_launch_failed"
    PROCESS_TIMED_OUT = "process_timed_out"
    PROCESS_CANCELLED = "process_cancelled"
    PROCESS_INTERRUPTED = "process_interrupted"
    TRANSPORT_FAILED = "transport_failed"
    HANDOFF_MISSING = "handoff_missing"
    HANDOFF_TOO_LARGE = "handoff_too_large"
    HANDOFF_ENCODING_INVALID = "handoff_encoding_invalid"
    HANDOFF_JSON_INVALID = "handoff_json_invalid"
    HANDOFF_SCHEMA_INVALID = "handoff_schema_invalid"
    HANDOFF_CONTEXT_MISMATCH = "handoff_context_mismatch"
    HANDOFF_PROCESS_MISMATCH = "handoff_process_mismatch"


class TerminalRunnerObservation(ProtocolModel):
    """Terminal facts supplied by a runner without semantic interpretation."""

    run_id: StableId
    process_started: bool
    exit_code: int | None = None
    timed_out: bool = False
    cancelled: bool = False
    interrupted: bool = False
    transport_status: TransportStatus

    @model_validator(mode="after")
    def validate_terminal_facts(self):
        abnormal = sum((self.timed_out, self.cancelled, self.interrupted))
        if abnormal > 1:
            raise ValueError("terminal process outcomes are mutually exclusive")
        if not self.process_started:
            if self.exit_code is not None or abnormal:
                raise ValueError("an unstarted process cannot have a terminal process outcome")
            if self.transport_status != TransportStatus.FAILED:
                raise ValueError("an unstarted process requires failed transport")
            return self
        if abnormal:
            if self.transport_status != TransportStatus.FAILED:
                raise ValueError("an abnormal process outcome requires failed transport")
            return self
        if self.exit_code is None:
            raise ValueError("a normally exited process requires an exit code")
        if self.transport_status not in {TransportStatus.COMPLETED, TransportStatus.FAILED}:
            raise ValueError("a terminal process requires completed or failed transport")
        return self


class AgentAdapterResult(ProtocolModel):
    """Normalized protocol state plus the only Handoff eligible for registration."""

    protocol_state: RunProtocolState
    handoff_pack: HandoffPack | None = None
    error_code: AdapterErrorCode | None = None
    attention_required: bool = False

    @model_validator(mode="after")
    def validate_result_shape(self):
        schema_accepted = self.protocol_state.schema_status in {
            SchemaStatus.VALID,
            SchemaStatus.REPAIRED,
        }
        if schema_accepted != (self.handoff_pack is not None):
            raise ValueError("only schema-valid adapter results may expose a Handoff Pack")
        if schema_accepted and self.error_code is not None:
            raise ValueError("accepted Handoff Packs cannot carry adapter errors")
        if schema_accepted and self.attention_required:
            raise ValueError("accepted Handoff Packs cannot require protocol Attention")
        if not schema_accepted and self.error_code is None:
            raise ValueError("non-accepted adapter results require a stable error code")
        if (
            self.protocol_state.schema_status == SchemaStatus.PROTOCOL_FAILED
            and not self.attention_required
        ):
            raise ValueError("protocol failure requires Attention")
        return self


def adapt_agent_output(
    context_pack: ContextPack,
    observation: TerminalRunnerObservation,
    raw_handoff: str | bytes | None,
) -> AgentAdapterResult:
    """Normalize one terminal runner observation without writing workflow state.

    The adapter accepts exact JSON or one whole-document Markdown-fence removal.
    It never extracts an object from prose and never modifies Handoff semantics.
    """

    if context_pack.run_id != observation.run_id:
        raise ValueError("runner observation must match the Context Pack run")

    process_status = _process_status(observation)
    if process_status == ProcessStatus.LAUNCH_FAILED:
        return _non_protocol_result(
            observation,
            process_status,
            SemanticStageResult.FAILED,
            AdapterErrorCode.PROCESS_LAUNCH_FAILED,
        )
    if process_status == ProcessStatus.TIMED_OUT:
        return _non_protocol_result(
            observation,
            process_status,
            SemanticStageResult.FAILED,
            AdapterErrorCode.PROCESS_TIMED_OUT,
        )
    if process_status == ProcessStatus.CANCELLED:
        return _non_protocol_result(
            observation,
            process_status,
            SemanticStageResult.CANCELLED,
            AdapterErrorCode.PROCESS_CANCELLED,
        )
    if process_status == ProcessStatus.INTERRUPTED:
        return _non_protocol_result(
            observation,
            process_status,
            SemanticStageResult.FAILED,
            AdapterErrorCode.PROCESS_INTERRUPTED,
        )
    if observation.transport_status != TransportStatus.COMPLETED:
        return _non_protocol_result(
            observation,
            process_status,
            SemanticStageResult.FAILED,
            AdapterErrorCode.TRANSPORT_FAILED,
        )

    parsed, schema_status, repair_attempts, error_code = _parse_handoff(
        context_pack,
        raw_handoff,
    )
    if parsed is None:
        return AgentAdapterResult(
            protocol_state=RunProtocolState(
                run_id=observation.run_id,
                process_status=process_status,
                transport_status=observation.transport_status,
                schema_status=SchemaStatus.PROTOCOL_FAILED,
                semantic_stage_result=SemanticStageResult.BLOCKED,
                process_exit_code=observation.exit_code,
                repair_attempts=repair_attempts,
            ),
            error_code=error_code,
            attention_required=True,
        )

    semantic_result = _semantic_result(parsed.stage_result)
    if semantic_result == SemanticStageResult.CANCELLED:
        return AgentAdapterResult(
            protocol_state=RunProtocolState(
                run_id=observation.run_id,
                process_status=process_status,
                transport_status=observation.transport_status,
                schema_status=SchemaStatus.PROTOCOL_FAILED,
                semantic_stage_result=SemanticStageResult.BLOCKED,
                process_exit_code=observation.exit_code,
                repair_attempts=repair_attempts,
            ),
            error_code=AdapterErrorCode.HANDOFF_PROCESS_MISMATCH,
            attention_required=True,
        )
    return AgentAdapterResult(
        protocol_state=RunProtocolState(
            run_id=observation.run_id,
            process_status=process_status,
            transport_status=observation.transport_status,
            schema_status=schema_status,
            semantic_stage_result=semantic_result,
            process_exit_code=observation.exit_code,
            repair_attempts=repair_attempts,
        ),
        handoff_pack=parsed,
    )


def _process_status(observation: TerminalRunnerObservation) -> ProcessStatus:
    if not observation.process_started:
        return ProcessStatus.LAUNCH_FAILED
    if observation.timed_out:
        return ProcessStatus.TIMED_OUT
    if observation.cancelled:
        return ProcessStatus.CANCELLED
    if observation.interrupted:
        return ProcessStatus.INTERRUPTED
    return ProcessStatus.EXITED


def _non_protocol_result(
    observation: TerminalRunnerObservation,
    process_status: ProcessStatus,
    semantic_result: SemanticStageResult,
    error_code: AdapterErrorCode,
) -> AgentAdapterResult:
    # Infrastructure failure Attention/retry policy belongs to the orchestrator;
    # this pure adapter mandates Attention only for protocol failure.
    return AgentAdapterResult(
        protocol_state=RunProtocolState(
            run_id=observation.run_id,
            process_status=process_status,
            transport_status=observation.transport_status,
            schema_status=SchemaStatus.PENDING,
            semantic_stage_result=semantic_result,
            process_exit_code=(
                observation.exit_code if process_status == ProcessStatus.EXITED else None
            ),
            repair_attempts=0,
        ),
        error_code=error_code,
    )


def _parse_handoff(
    context_pack: ContextPack,
    raw_handoff: str | bytes | None,
) -> tuple[HandoffPack | None, SchemaStatus, int, AdapterErrorCode | None]:
    if raw_handoff is None:
        return None, SchemaStatus.PROTOCOL_FAILED, 0, AdapterErrorCode.HANDOFF_MISSING
    try:
        if isinstance(raw_handoff, bytes):
            encoded = raw_handoff
            value = raw_handoff.decode("utf-8", errors="strict")
        else:
            value = raw_handoff
            encoded = raw_handoff.encode("utf-8", errors="strict")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return (
            None,
            SchemaStatus.PROTOCOL_FAILED,
            0,
            AdapterErrorCode.HANDOFF_ENCODING_INVALID,
        )

    limit = min(
        context_pack.budget.max_output_bytes,
        MAX_HANDOFF_PACK_BYTES + _FORMAT_OVERHEAD_BYTES,
    )
    if len(encoded) > limit:
        return None, SchemaStatus.PROTOCOL_FAILED, 0, AdapterErrorCode.HANDOFF_TOO_LARGE
    if not value.strip():
        return None, SchemaStatus.PROTOCOL_FAILED, 0, AdapterErrorCode.HANDOFF_MISSING

    try:
        candidate = _load_json_object(value)
    except (json.JSONDecodeError, ValueError):
        repaired = _unwrap_json_fence(value)
        if repaired is None:
            # Invalid output consumes the one bounded repair opportunity even
            # when no permitted whole-document transformation applies.
            return (
                None,
                SchemaStatus.PROTOCOL_FAILED,
                1,
                AdapterErrorCode.HANDOFF_JSON_INVALID,
            )
        try:
            candidate = _load_json_object(repaired)
        except (json.JSONDecodeError, ValueError):
            return (
                None,
                SchemaStatus.PROTOCOL_FAILED,
                1,
                AdapterErrorCode.HANDOFF_JSON_INVALID,
            )
        schema_status = SchemaStatus.REPAIRED
        repair_attempts = 1
    else:
        schema_status = SchemaStatus.VALID
        repair_attempts = 0

    try:
        handoff = HandoffPack.model_validate(candidate)
    except ValueError:
        return (
            None,
            SchemaStatus.PROTOCOL_FAILED,
            repair_attempts,
            AdapterErrorCode.HANDOFF_SCHEMA_INVALID,
        )
    if not _handoff_matches_context(context_pack, handoff):
        return (
            None,
            SchemaStatus.PROTOCOL_FAILED,
            repair_attempts,
            AdapterErrorCode.HANDOFF_CONTEXT_MISMATCH,
        )
    return handoff, schema_status, repair_attempts, None


def _load_json_object(value: str) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = item
        return result

    def reject_non_finite(_: str) -> None:
        raise ValueError("non-finite JSON number")

    parsed = json.loads(
        value,
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_non_finite,
    )
    if not isinstance(parsed, dict):
        raise ValueError("Handoff Pack JSON must be an object")
    return parsed


def _unwrap_json_fence(value: str) -> str | None:
    lines = value.strip().splitlines()
    if len(lines) < 3 or lines[-1].strip() != "```":
        return None
    opening = lines[0].strip().lower()
    if opening not in {"```", "```json"}:
        return None
    return "\n".join(lines[1:-1]).strip()


def _handoff_matches_context(context: ContextPack, handoff: HandoffPack) -> bool:
    # Echoed Context lists are order-sensitive by design: the sealed Handoff
    # must preserve the exact versioned input contract, not merely its members.
    if (
        handoff.project_id != context.project_id
        or handoff.task_id != context.task_id
        or handoff.stage_key != context.stage_key
        or handoff.run_id != context.run_id
        or handoff.input_artifacts != context.input_artifacts
        or handoff.required_outputs != context.required_outputs
        or handoff.forbidden_constraints != context.forbidden_constraints
    ):
        return False
    producer = handoff.producer
    if handoff.stage_result == StageResult.SUCCEEDED:
        outputs = {
            (artifact.artifact_id, artifact.kind)
            for artifact in handoff.output_artifacts
        }
        if any(
            required.required
            and (required.output_id, required.kind) not in outputs
            for required in context.required_outputs
        ):
            return False
    for artifact in handoff.output_artifacts:
        if (
            artifact.project_id != context.project_id
            or artifact.task_id != context.task_id
            or artifact.stage_key != context.stage_key
            or artifact.producer != producer
        ):
            return False
    for evidence in handoff.evidence:
        if (
            evidence.project_id != context.project_id
            or evidence.task_id != context.task_id
            or evidence.stage_key != context.stage_key
            or evidence.producer != producer
        ):
            return False
    # Reconciliation and Gate evaluation, not this scope-binding adapter, own
    # the meaning of a Native State recommendation.
    return (
        handoff.native_state_snapshot is None
        or handoff.native_state_snapshot.project_id == context.project_id
    )


def _semantic_result(stage_result: StageResult) -> SemanticStageResult:
    return SemanticStageResult(stage_result.value)
