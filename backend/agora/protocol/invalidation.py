"""Approval hash invalidation and downstream Stage reopen propagation."""
from __future__ import annotations

from collections import deque
from typing import Annotated

from pydantic import Field, field_validator

from .models import (
    Approval,
    ApprovalStatus,
    GitCommit,
    NonBlank,
    ProtocolModel,
    Sha256Hex,
    StableId,
)
from .paths import canonical_repository_path


class ArtifactChange(ProtocolModel):
    repository_id: StableId
    ref: NonBlank
    commit_sha: GitCommit
    path: Annotated[str, Field(min_length=1, max_length=4000)]
    sha256: Sha256Hex

    @field_validator("path")
    @classmethod
    def _normalize_path(cls, value: str) -> str:
        return canonical_repository_path(value)


class InvalidationPlan(ProtocolModel):
    approvals: list[Approval]
    stale_approval_ids: list[StableId]
    stale_gate_keys: list[StableId]
    reopen_stage_keys: list[StableId]
    attention_codes: list[StableId]


def _downstream_stages(
    seeds: set[str],
    stage_dependents: dict[str, set[str]],
) -> list[str]:
    seen = set(seeds)
    queue = deque(sorted(seeds))
    ordered: list[str] = []
    while queue:
        stage = queue.popleft()
        ordered.append(stage)
        for dependent in sorted(stage_dependents.get(stage, set())):
            if dependent not in seen:
                seen.add(dependent)
                queue.append(dependent)
    return ordered


def invalidate_approvals(
    approvals: list[Approval],
    current_artifact_inventory: list[ArtifactChange],
    *,
    stage_dependents: dict[str, set[str]] | None = None,
) -> InvalidationPlan:
    """Invalidate against a complete current artifact inventory for covered refs."""
    current = {
        (
            item.repository_id,
            item.ref,
            item.path.replace("\\", "/"),
        ): item
        for item in current_artifact_inventory
    }
    updated: list[Approval] = []
    stale_ids: list[str] = []
    stale_gates: set[str] = set()
    stale_stages: set[str] = set()

    for approval in sorted(approvals, key=lambda item: item.approval_id):
        if approval.status != ApprovalStatus.ACTIVE:
            updated.append(approval)
            continue
        changed = []
        for binding in approval.artifact_versions:
            item = current.get(
                (
                    binding.repository_id,
                    binding.ref,
                    binding.path.replace("\\", "/"),
                )
            )
            if (
                item is None
                or item.sha256 != binding.sha256
                or item.commit_sha != binding.commit_sha
            ):
                changed.append(binding.path.replace("\\", "/"))
        if not changed:
            updated.append(approval)
            continue
        reason = f"artifact_changed:{','.join(sorted(changed))}"
        updated.append(
            approval.model_copy(
                update={
                    "status": ApprovalStatus.STALE,
                    "stale_reason": reason,
                }
            )
        )
        stale_ids.append(approval.approval_id)
        stale_gates.add(approval.gate_key)
        stale_stages.add(approval.stage_key)

    reopen = _downstream_stages(stale_stages, stage_dependents or {})
    return InvalidationPlan(
        approvals=updated,
        stale_approval_ids=sorted(stale_ids),
        stale_gate_keys=sorted(stale_gates),
        reopen_stage_keys=reopen,
        attention_codes=[
            f"approval_impact_analysis:{approval_id}" for approval_id in sorted(stale_ids)
        ],
    )
