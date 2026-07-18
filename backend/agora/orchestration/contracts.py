"""Versioned concrete Task contracts for CLI-first orchestration."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .models import StrictModel


CONTRACT_FILE_LIMIT = 64 * 1024
CONTRACT_PROMPT_LIMIT = 8_000


class RoleAssignment(StrictModel):
    role_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    runtime: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    responsibilities: list[str] = Field(min_length=1, max_length=20)
    independent_from: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_role_lists(self):
        if any(not item.strip() for item in self.responsibilities):
            raise ValueError("role responsibilities may not be blank")
        if len(self.independent_from) != len(set(self.independent_from)):
            raise ValueError("independent role references must be unique")
        return self


class ArtifactRequirement(StrictModel):
    artifact_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    kind: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    description: str = Field(min_length=1, max_length=1000)


class EvidenceRequirement(StrictModel):
    requirement_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    kind: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    description: str = Field(min_length=1, max_length=1000)


class GateRequirementTemplate(StrictModel):
    requirement_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    severity: Literal["blocker", "warning"] = "blocker"
    priority: int = Field(default=100, ge=0, le=10_000)
    failure_action: str = Field(min_length=1, max_length=1000)


class StageTaskContract(StrictModel):
    stage_key: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=300)
    role_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    objective: str = Field(min_length=1, max_length=2000)
    completion_conditions: list[str] = Field(min_length=1, max_length=30)
    context_expectations: list[str] = Field(min_length=1, max_length=30)
    handoff_expectations: list[str] = Field(min_length=1, max_length=30)
    required_artifacts: list[ArtifactRequirement] = Field(min_length=1, max_length=30)
    required_evidence: list[EvidenceRequirement] = Field(min_length=1, max_length=30)
    gate_requirements: list[GateRequirementTemplate] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def gate_requirements_reference_evidence(self):
        text_lists = (
            self.completion_conditions,
            self.context_expectations,
            self.handoff_expectations,
        )
        if any(not item.strip() for values in text_lists for item in values):
            raise ValueError("stage contract expectations may not be blank")
        evidence_ids = [item.requirement_id for item in self.required_evidence]
        gate_ids = [item.requirement_id for item in self.gate_requirements]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("required evidence ids must be unique within a stage")
        if len(gate_ids) != len(set(gate_ids)):
            raise ValueError("gate requirement ids must be unique within a stage")
        if set(gate_ids) != set(evidence_ids):
            raise ValueError("gate requirements must exactly reference required evidence")
        artifact_ids = [item.artifact_id for item in self.required_artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("required artifact ids must be unique within a stage")
        return self


class TaskContract(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    contract_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=300)
    goal: str = Field(min_length=1, max_length=4000)
    roles: list[RoleAssignment] = Field(min_length=1, max_length=10)
    workflow: list[StageTaskContract] = Field(min_length=1, max_length=20)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=50)
    forbidden_constraints: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_references_and_uniqueness(self):
        if any(not item.strip() for item in self.acceptance_criteria):
            raise ValueError("acceptance criteria may not be blank")
        if any(not item.strip() for item in self.forbidden_constraints):
            raise ValueError("forbidden constraints may not be blank")
        role_ids = [item.role_id for item in self.roles]
        if len(role_ids) != len(set(role_ids)):
            raise ValueError("role ids must be unique")
        stage_keys = [item.stage_key for item in self.workflow]
        if len(stage_keys) != len(set(stage_keys)):
            raise ValueError("stage keys must be unique")
        known_roles = set(role_ids)
        for role in self.roles:
            unknown = set(role.independent_from) - known_roles
            if unknown:
                raise ValueError(f"role {role.role_id} references unknown independent roles")
            if role.role_id in role.independent_from:
                raise ValueError(f"role {role.role_id} cannot be independent from itself")
        unknown_stage_roles = {item.role_id for item in self.workflow} - known_roles
        if unknown_stage_roles:
            raise ValueError("workflow references unknown roles")
        requirement_ids = [
            requirement.requirement_id
            for stage in self.workflow
            for requirement in stage.gate_requirements
        ]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("gate requirement ids must be unique across the Task contract")
        return self


def canonical_contract_json(contract: TaskContract) -> str:
    value = json.dumps(
        contract.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(value.encode("utf-8")) > CONTRACT_PROMPT_LIMIT:
        raise ValueError("Task contract exceeds the bounded orchestration context")
    return value


def contract_sha256(contract: TaskContract) -> str:
    return hashlib.sha256(canonical_contract_json(contract).encode("utf-8")).hexdigest()


def load_task_contract(path: Path) -> TaskContract:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ValueError(f"Task contract is unavailable: {exc}") from exc
    if size > CONTRACT_FILE_LIMIT:
        raise ValueError("Task contract file exceeds 64 KiB")
    try:
        raw = path.read_text(encoding="utf-8")
        contract = TaskContract.model_validate_json(raw)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"Invalid Task contract: {exc}") from exc
    canonical_contract_json(contract)
    return contract
