"""Structured, versioned requirement specification contracts."""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class SpecState(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class ChangeRequestState(str, Enum):
    OPEN = "open"
    ACCEPTED = "accepted"
    DECLINED = "declined"


class RequirementItem(BaseModel):
    requirement_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    statement: str = Field(min_length=1, max_length=10_000)
    rationale: str | None = Field(default=None, max_length=10_000)

    @field_validator("statement")
    @classmethod
    def statement_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("statement may not be blank")
        return value.strip()


class AcceptanceScenario(BaseModel):
    scenario_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    requirement_ids: list[str] = Field(default_factory=list)
    given: str = Field(min_length=1, max_length=10_000)
    when: str = Field(min_length=1, max_length=10_000)
    then: str = Field(min_length=1, max_length=10_000)

    @field_validator("given", "when", "then")
    @classmethod
    def scenario_text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("acceptance scenario text may not be blank")
        return value.strip()


class OpenQuestion(BaseModel):
    question_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    question: str = Field(min_length=1, max_length=10_000)
    resolution: str | None = Field(default=None, max_length=10_000)

    @field_validator("question")
    @classmethod
    def question_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question may not be blank")
        return value.strip()


class TraceabilityLink(BaseModel):
    requirement_id: str = Field(min_length=1, max_length=128)
    target_type: Literal["design", "task", "test"]
    target_id: str = Field(min_length=1, max_length=1000)
    label: str | None = Field(default=None, max_length=1000)


class SpecContent(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(default="", max_length=10_000)
    functional: list[RequirementItem] = Field(default_factory=list)
    non_functional: list[RequirementItem] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    acceptance_scenarios: list[AcceptanceScenario] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    links: list[TraceabilityLink] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title may not be blank")
        return value.strip()

    @field_validator("constraints", "out_of_scope", "assumptions")
    @classmethod
    def no_blank_items(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("list items may not be blank")
        return values

    @field_validator("glossary")
    @classmethod
    def valid_glossary(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not term.strip() or not definition.strip() for term, definition in value.items()):
            raise ValueError("glossary terms and definitions may not be blank")
        return value

    @model_validator(mode="after")
    def validate_references(self):
        requirement_ids = [item.requirement_id for item in [*self.functional, *self.non_functional]]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("requirement_id values must be unique")
        known = set(requirement_ids)
        scenario_ids = [item.scenario_id for item in self.acceptance_scenarios]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("scenario_id values must be unique")
        question_ids = [item.question_id for item in self.open_questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("question_id values must be unique")
        referenced = {
            requirement_id
            for scenario in self.acceptance_scenarios
            for requirement_id in scenario.requirement_ids
        } | {link.requirement_id for link in self.links}
        unknown = sorted(referenced - known)
        if unknown:
            raise ValueError(f"unknown requirement references: {', '.join(unknown)}")
        return self


class CreateSpecRequest(SpecContent):
    created_by: str = Field(default="user", min_length=1, max_length=128)


class UpdateSpecRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    summary: str | None = Field(default=None, max_length=10_000)
    functional: list[RequirementItem] | None = None
    non_functional: list[RequirementItem] | None = None
    constraints: list[str] | None = None
    acceptance_scenarios: list[AcceptanceScenario] | None = None
    out_of_scope: list[str] | None = None
    glossary: dict[str, str] | None = None
    assumptions: list[str] | None = None
    open_questions: list[OpenQuestion] | None = None
    links: list[TraceabilityLink] | None = None
    actor: str = Field(default="user", min_length=1, max_length=128)


class ReviewSpecRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    actor: str = Field(default="user", min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=4000)


class RejectSpecRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    actor: str = Field(default="user", min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=4000)


class SubmitChangeRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=10_000)
    impact_notes: str = Field(default="", max_length=10_000)
    affected_targets: list[str] = Field(default_factory=list)
    submitted_by: str = Field(default="user", min_length=1, max_length=128)


class ReviewChangeRequest(BaseModel):
    action: Literal["accept", "decline"]
    actor: str = Field(default="user", min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=4000)


class RequirementSpec(SpecContent):
    spec_id: str
    task_id: str
    version: int
    revision: int
    state: SpecState
    created_by: str
    approved_by: str | None
    approval_reason: str | None
    rejected_by: str | None
    rejection_reason: str | None
    created_at: str
    updated_at: str


class RequirementChangeRequest(BaseModel):
    cr_id: str
    spec_id: str
    task_id: str
    state: ChangeRequestState
    title: str
    description: str
    impact_notes: str
    affected_targets: list[str]
    submitted_by: str
    reviewed_by: str | None
    review_reason: str | None
    resulting_spec_id: str | None
    created_at: str
    resolved_at: str | None
