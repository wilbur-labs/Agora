"""Bridge provisional CLI runner results into the frozen Agent adapter boundary."""
from __future__ import annotations

from agora.protocol.agent_adapter import (
    AdapterErrorCode,
    AgentAdapterResult,
    TerminalRunnerObservation,
    adapt_agent_output,
)
from agora.protocol.models import (
    ContextPack,
    GateRequirement,
    RunProtocolState,
    SchemaStatus,
    SemanticStageResult,
    TransportStatus,
)

from .runtime import RuntimeResult


def adapt_runtime_result(
    context_pack: ContextPack,
    result: RuntimeResult,
    *,
    gate_requirements: list[GateRequirement] | None = None,
    cancelled: bool = False,
) -> AgentAdapterResult:
    """Preserve terminal runner facts and validate stdout as a Handoff Pack.

    A normally exited subprocess has completed its local stdout transport even
    when it exits non-zero. Schema and semantic validity are decided only by
    the protocol adapter.
    """

    interrupted = (
        result.process_started
        and not result.timed_out
        and not cancelled
        and result.exit_code is None
    )
    transport_status = (
        TransportStatus.COMPLETED
        if (
            result.process_started
            and not result.timed_out
            and not interrupted
            and not cancelled
        )
        else TransportStatus.FAILED
    )
    observation = TerminalRunnerObservation(
        run_id=context_pack.run_id,
        process_started=result.process_started,
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        cancelled=cancelled,
        interrupted=interrupted,
        transport_status=transport_status,
    )
    adapted = adapt_agent_output(context_pack, observation, result.stdout)
    if adapted.handoff_pack is None or gate_requirements is None:
        return adapted
    requirements = {item.requirement_id: item for item in gate_requirements}
    for evidence in adapted.handoff_pack.evidence:
        requirement = requirements.get(evidence.requirement_id)
        if requirement is None:
            continue
        if (
            evidence.repository_id != requirement.repository_id
            or evidence.ref != requirement.ref
            or evidence.commit_sha != requirement.commit_sha
            or evidence.kind != requirement.evidence_kind
        ):
            return AgentAdapterResult(
                protocol_state=RunProtocolState(
                    run_id=adapted.protocol_state.run_id,
                    process_status=adapted.protocol_state.process_status,
                    transport_status=adapted.protocol_state.transport_status,
                    schema_status=SchemaStatus.PROTOCOL_FAILED,
                    semantic_stage_result=SemanticStageResult.BLOCKED,
                    process_exit_code=adapted.protocol_state.process_exit_code,
                    repair_attempts=adapted.protocol_state.repair_attempts,
                ),
                error_code=AdapterErrorCode.HANDOFF_CONTEXT_MISMATCH,
                attention_required=True,
            )
    return adapted
