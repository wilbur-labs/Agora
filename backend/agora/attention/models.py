"""Tool-neutral contracts for questions, approvals, and blockers."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


# Requester tag stamped on the approval/blocker raised by the auto cross-review.
# Shared so the dispatcher (which raises it) and the review gate (which advances
# the task when a human settles it) agree on how to recognise a review item.
REVIEW_REQUESTER = "agora-review"

#: Requesters the control plane owns. It stamps them on the items *it* raises, and
#: it then reads them back as **its own authority**:
#:
#: - ``ExecutionStore._has_review_gate_item`` treats a ``REVIEW_REQUESTER`` blocker
#:   or approval scoped to a worker run as the review gate for that run, so its
#:   existence stands down the derived "this task owes a review" refusal.
#: - ``agora.attention.review_gate.settle_review_approval`` advances a task to
#:   ``verified`` when a human approves a ``REVIEW_REQUESTER`` approval.
#: - ``agora.attention.run_scope`` exempts ``REVIEW_REQUESTER`` items from being
#:   settled with their run.
#:
#: A requester a client can set is therefore a client-settable authority marker,
#: which is the third one this slice has found: ``agora_role`` in run metadata was
#: the first (``RESERVED_METADATA_KEYS``), forgeable through ``POST /api/runs``. The
#: same shape closes this one — the value is refused in public input and only
#: Agora's internal paths may write it (``AttentionStore.create(...,
#: internal=True)``). Refused rather than rewritten, for the reason
#: ``RESERVED_METADATA_KEYS`` gives: a caller sending one is confused or hostile,
#: and quietly substituting a different requester would hide which.
RESERVED_REQUESTERS = frozenset({REVIEW_REQUESTER})


class AttentionKind(str, Enum):
    QUESTION = "question"
    APPROVAL = "approval"
    BLOCKER = "blocker"


class AttentionState(str, Enum):
    OPEN = "open"
    RESPONDED = "responded"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class AttentionUrgency(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class ResponseAction(str, Enum):
    ANSWER = "answer"
    APPROVE = "approve"
    REJECT = "reject"


class CreateAttentionRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=128)
    run_id: str | None = Field(default=None, max_length=128)
    kind: AttentionKind
    urgency: AttentionUrgency = AttentionUrgency.NORMAL
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(default="", max_length=32_000)
    options: list[str] = Field(default_factory=list, max_length=20)
    context: dict[str, Any] = Field(default_factory=dict)
    requester: str = Field(min_length=1, max_length=128)
    assignee: str | None = Field(default=None, max_length=128)
    expires_at: str | None = None

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title may not be blank")
        return value

    @field_validator("options")
    @classmethod
    def valid_options(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 500 for value in values):
            raise ValueError("options must be non-blank and at most 500 characters")
        if len(set(values)) != len(values):
            raise ValueError("options must be unique")
        return values

    @field_validator("expires_at")
    @classmethod
    def valid_expiry(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("expires_at must include a timezone")
        if parsed <= datetime.now(timezone.utc):
            raise ValueError("expires_at must be in the future")
        return parsed.astimezone(timezone.utc).isoformat()


class RespondAttentionRequest(BaseModel):
    action: ResponseAction
    response: str = Field(default="", max_length=32_000)
    actor: str = Field(default="user", min_length=1, max_length=128)
    expected_version: int = Field(ge=1)

    @model_validator(mode="after")
    def answer_requires_text(self):
        if self.action == ResponseAction.ANSWER and not self.response.strip():
            raise ValueError("answer responses may not be blank")
        return self


class CancelAttentionRequest(BaseModel):
    actor: str = Field(default="user", min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=4000)
    expected_version: int = Field(ge=1)


class AttentionItem(BaseModel):
    item_id: str
    project_id: str
    task_id: str
    run_id: str | None
    kind: AttentionKind
    state: AttentionState
    urgency: AttentionUrgency
    title: str
    body: str
    options: list[str]
    context: dict[str, Any]
    requester: str
    assignee: str | None
    response: str | None
    response_action: ResponseAction | None
    responded_by: str | None
    cancellation_reason: str | None
    version: int
    expires_at: str | None
    created_at: str
    responded_at: str | None
    updated_at: str


class AttentionCount(BaseModel):
    open: int
