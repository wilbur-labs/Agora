"""Bounded request and response models for the Control Plane API."""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agora.attention.models import AttentionItem, ResponseAction
from agora.control_plane.models import ControlEvent, GateRecord, StageRecord
from agora.protocol.models import Approval, Artifact, Evidence, GateRequirement, StableId
from agora.tasks.models import TaskManifest
from agora.tasks.models import TaskBudget


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfigureGateRequest(ApiModel):
    stage_key: StableId
    requirements: list[GateRequirement] = Field(min_length=1, max_length=100)


class SetActiveEvidenceRequest(ApiModel):
    evidence_ids: list[StableId] = Field(max_length=500)
    expected_gate_version: int = Field(ge=1)
    operation_key: str = Field(
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )


class EvaluateGateRequest(ApiModel):
    expected_gate_version: int = Field(ge=1)
    operation_key: str = Field(
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )


class ControlPlaneAttentionResponseRequest(ApiModel):
    action: ResponseAction
    response: str = Field(default="", max_length=32_000)
    expected_version: int = Field(ge=1)
    operation_key: str = Field(
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )

    @model_validator(mode="after")
    def answer_requires_text(self):
        if self.action == ResponseAction.ANSWER and not self.response.strip():
            raise ValueError("answer responses may not be blank")
        return self


class AttentionResponseEffect(str, Enum):
    LOCAL_RECORDED = "local_recorded"
    CAPTURE_ONLY_RECORDED = "capture_only_recorded"
    DELIVERY_READY = "delivery_ready"


class ControlPlaneAttentionResponseReceipt(ApiModel):
    schema_version: Literal["1.0"] = "1.0"
    operation_key: str
    attention: AttentionItem
    response_effect: AttentionResponseEffect
    task_state_mutated: Literal[False] = False
    formal_approval_created: Literal[False] = False


class EventPage(ApiModel):
    limit: int
    offset: int
    total: int


class ControlEventPage(ApiModel):
    events: list[ControlEvent]
    page: EventPage


class NextSafeAction(ApiModel):
    value: str | None
    source_gate_key: StableId | None
    unavailable_reason: str | None


class ControlPlaneProjection(ApiModel):
    task: TaskManifest
    budget: TaskBudget
    stages: list[StageRecord]
    gates: list[GateRecord]
    artifacts: list[Artifact]
    evidence: list[Evidence]
    approvals: list[Approval]
    attention: list[AttentionItem]
    next_safe_action: NextSafeAction
    events: list[ControlEvent]
    event_page: EventPage
    collection_totals: dict[str, int]
    collection_pages: dict[str, EventPage]
