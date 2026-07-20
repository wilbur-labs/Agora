"""Bridge provisional CLI runner results into the frozen Agent adapter boundary."""
from __future__ import annotations

from agora.protocol.agent_adapter import (
    AgentAdapterResult,
    TerminalRunnerObservation,
    adapt_agent_output,
)
from agora.protocol.models import ContextPack, TransportStatus

from .runtime import RuntimeResult


def adapt_runtime_result(
    context_pack: ContextPack,
    result: RuntimeResult,
) -> AgentAdapterResult:
    """Preserve terminal runner facts and validate stdout as a Handoff Pack.

    A normally exited subprocess has completed its local stdout transport even
    when it exits non-zero. Schema and semantic validity are decided only by
    the protocol adapter.
    """

    interrupted = (
        result.process_started and not result.timed_out and result.exit_code is None
    )
    transport_status = (
        TransportStatus.COMPLETED
        if result.process_started and not result.timed_out and not interrupted
        else TransportStatus.FAILED
    )
    observation = TerminalRunnerObservation(
        run_id=context_pack.run_id,
        process_started=result.process_started,
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        interrupted=interrupted,
        transport_status=transport_status,
    )
    return adapt_agent_output(context_pack, observation, result.stdout)
