"""Recovery planning for Windows Embedded Runner cleanup failures."""
from __future__ import annotations

from pydantic import Field

from .models import NonBlank, ProtocolModel, StableId, RunnerIsolationContract


class CleanupFailurePlan(ProtocolModel):
    run_id: StableId
    recovery_marker: NonBlank
    attention_code: StableId
    preserve_workspace: bool = True
    error_summary: str = Field(min_length=1, max_length=4000)


def plan_cleanup_failure(
    contract: RunnerIsolationContract,
    error_summary: str,
) -> CleanupFailurePlan:
    return CleanupFailurePlan(
        run_id=contract.run_id,
        recovery_marker=contract.recovery_marker,
        attention_code=f"runner_cleanup_failed:{contract.run_id}",
        error_summary=error_summary,
    )
