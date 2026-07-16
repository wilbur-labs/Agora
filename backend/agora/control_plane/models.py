"""Internal records returned by Control Plane v2 persistence."""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field, field_validator, model_validator

from agora.protocol.invalidation import ArtifactChange
from agora.protocol.models import (
    GateEvaluation,
    GateRequirement,
    GitCommit,
    NonBlank,
    ProtocolModel,
    StableId,
)
from agora.protocol.state_machines import GateStatus, StageStatus


class RegistrationReceipt(ProtocolModel):
    entity_id: StableId
    version: int | None = Field(default=None, ge=1)
    created: bool


class StageRecord(ProtocolModel):
    task_id: StableId
    project_id: StableId
    stage_key: StableId
    gate_key: StableId
    status: StageStatus
    version: int = Field(ge=1)
    created_at: str
    updated_at: str


class GateRecord(ProtocolModel):
    task_id: StableId
    project_id: StableId
    gate_key: StableId
    stage_key: StableId
    status: GateStatus
    version: int = Field(ge=1)
    requirements: list[GateRequirement]
    active_evidence_ids: list[StableId]
    last_evaluation: GateEvaluation | None
    created_at: str
    updated_at: str


class ArtifactInventory(ProtocolModel):
    repository_id: StableId
    ref: NonBlank
    commit_sha: GitCommit
    artifacts: list[ArtifactChange] = Field(default_factory=list, max_length=10_000)

    @field_validator("artifacts", mode="before")
    @classmethod
    def canonical_artifacts(cls, value):
        values = list(value or [])

        def key(item):
            if isinstance(item, dict):
                return item["path"], item["sha256"]
            return item.path, item.sha256

        keys = [key(item) for item in values]
        paths = [item[0] for item in keys]
        if len(paths) != len(set(paths)):
            raise ValueError("artifact inventory paths must be unique")
        return [
            item
            for _, item in sorted(
                zip(keys, values, strict=True),
                key=lambda pair: pair[0],
            )
        ]

    @model_validator(mode="after")
    def inventory_scope_matches_artifacts(self):
        for artifact in self.artifacts:
            if (
                artifact.repository_id != self.repository_id
                or artifact.ref != self.ref
                or artifact.commit_sha != self.commit_sha
            ):
                raise ValueError("artifact inventory entries must match its repository, ref, and commit")
        return self


class InvalidationReceipt(ProtocolModel):
    operation_key: Annotated[
        str,
        Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"),
    ]
    stale_approval_ids: list[StableId]
    stale_gate_keys: list[StableId]
    reopened_stage_keys: list[StableId]
    reconciliation_stage_keys: list[StableId]
    event_ids: list[StableId]
    replayed: bool = False


class ControlEvent(ProtocolModel):
    event_id: StableId
    event_key: Annotated[str, Field(min_length=1, max_length=500)]
    task_id: StableId
    project_id: StableId
    event_type: Annotated[
        str,
        Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.-]*$"),
    ]
    actor: Annotated[str, Field(min_length=1, max_length=128)]
    payload: dict[str, Any]
    created_at: str
