"""Task-scoped executable contracts for migrated methodology successors.

These contracts bind one already sealed successor inventory to the frozen
ContextPack, HandoffPack, Artifact, Evidence, and GateRequirement protocols.
They do not activate a route, create a Run, or grant dispatch authority.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, model_validator

from .methodology_activation import (
    ActivationApprovalPolicy,
    ActivationRoleProfile,
    ActivationSensorRequirement,
)
from .methodology_migration import (
    MigrationRepositoryBinding,
    MigrationRuntimePin,
)
from .models import (
    ArtifactVersionRef,
    GateRequirement,
    HashSealedModel,
    NonBlank,
    ProtocolModel,
    RunBudget,
    StableId,
    StageContract,
)


MethodologyInputResolution = Literal[
    "selected_stage_output",
    "hash_bound_task_seed",
    "optional_absent",
]
MethodologyInstanceBinding = Literal[
    "single",
    "matching_unit",
    "all_units",
    "task_seed",
    "optional_absent",
]
MethodologyEvidenceSource = Literal[
    "contract",
    "output",
    "sensor",
    "source_review",
    "completion_review",
]
MethodologyEvidenceResponsibility = Literal[
    "production_execution",
    "independent_correctness",
    "methodology_stewardship",
]


class MethodologyStageInputContract(ProtocolModel):
    source_artifact_id: StableId
    kind: StableId
    required: bool
    condition: StableId | None = None
    resolution: MethodologyInputResolution
    instance_binding: MethodologyInstanceBinding
    source_producer_stage_key: StableId | None = None
    producer_stage_keys: list[StableId] = Field(default_factory=list, max_length=100)
    seed_artifact: ArtifactVersionRef | None = None

    @model_validator(mode="after")
    def validate_resolution(self):
        if self.kind != self.source_artifact_id:
            raise ValueError("methodology input kind must retain its source Artifact id")
        if self.resolution == "selected_stage_output":
            if (
                self.source_producer_stage_key is None
                or not self.producer_stage_keys
                or self.seed_artifact is not None
                or self.instance_binding
                not in {"single", "matching_unit", "all_units"}
            ):
                raise ValueError(
                    "selected Stage output inputs require producer Stages only"
                )
        elif self.resolution == "hash_bound_task_seed":
            if (
                self.source_producer_stage_key is None
                or self.producer_stage_keys
                or self.seed_artifact is None
                or self.instance_binding != "task_seed"
            ):
                raise ValueError(
                    "Task seed inputs require one exact seed Artifact reference"
                )
            if (
                self.seed_artifact.kind != self.kind
                or self.seed_artifact.location is None
            ):
                raise ValueError(
                    "Task seed input kind and repository location must be exact"
                )
        else:
            if (
                self.required
                or self.source_producer_stage_key is not None
                or self.producer_stage_keys
                or self.seed_artifact is not None
                or self.instance_binding != "optional_absent"
            ):
                raise ValueError(
                    "Only an optional unbound input may use optional_absent"
                )
        return self


class MethodologyStageOutputContract(ProtocolModel):
    source_output_id: StableId
    kind: StableId
    required: bool
    applicable_unit_kinds: list[StableId] = Field(default_factory=list, max_length=16)
    artifact_identity_strategy: Literal["task_stage_run_template_sha256_v1"] = (
        "task_stage_run_template_sha256_v1"
    )

    @model_validator(mode="after")
    def validate_output(self):
        if self.kind != self.source_output_id:
            raise ValueError("methodology output kind must retain its source output id")
        if len(self.applicable_unit_kinds) != len(set(self.applicable_unit_kinds)):
            raise ValueError("methodology output unit kinds must be unique")
        return self


class MethodologyStageEvidenceContract(ProtocolModel):
    requirement: GateRequirement
    source: MethodologyEvidenceSource
    subject_id: StableId
    producer_responsibility: MethodologyEvidenceResponsibility
    producer_runtime: StableId
    source_reviewer_role: StableId | None = None
    source_reviewer_max_iterations: int = Field(default=0, ge=0, le=10)

    @model_validator(mode="after")
    def validate_producer_boundary(self):
        if self.source == "source_review":
            if (
                self.producer_responsibility != "production_execution"
                or self.source_reviewer_role is None
                or self.source_reviewer_max_iterations < 1
            ):
                raise ValueError(
                    "source review Evidence requires its bounded source role profile"
                )
        elif self.source == "completion_review":
            if (
                self.producer_responsibility
                not in {"independent_correctness", "methodology_stewardship"}
                or self.source_reviewer_role is not None
                or self.source_reviewer_max_iterations != 0
            ):
                raise ValueError(
                    "completion review Evidence requires an independent Agora responsibility"
                )
        elif (
            self.producer_responsibility != "production_execution"
            or self.source_reviewer_role is not None
            or self.source_reviewer_max_iterations != 0
        ):
            raise ValueError(
                "Stage production Evidence must retain production responsibility"
            )
        return self


class MethodologyStageContextContract(ProtocolModel):
    context_pack_schema_version: Literal["1.0"] = "1.0"
    stage_contract: StageContract
    source_inputs_text: NonBlank
    input_contracts: list[MethodologyStageInputContract] = Field(
        default_factory=list,
        max_length=100,
    )
    output_contracts: list[MethodologyStageOutputContract] = Field(
        default_factory=list,
        max_length=100,
    )
    sensors: list[ActivationSensorRequirement] = Field(
        default_factory=list,
        max_length=32,
    )
    forbidden_constraints: list[NonBlank] = Field(min_length=1, max_length=30)
    budget: RunBudget

    @model_validator(mode="after")
    def validate_context_templates(self):
        input_ids = [item.source_artifact_id for item in self.input_contracts]
        output_ids = [item.source_output_id for item in self.output_contracts]
        sensor_ids = [item.sensor_id for item in self.sensors]
        if len(input_ids) != len(set(input_ids)):
            raise ValueError("methodology Context input templates must be unique")
        if len(output_ids) != len(set(output_ids)):
            raise ValueError("methodology Context output templates must be unique")
        if len(sensor_ids) != len(set(sensor_ids)):
            raise ValueError("methodology Context sensor templates must be unique")
        return self


class MethodologyStageHandoffContract(ProtocolModel):
    handoff_pack_schema_version: Literal["1.0"] = "1.0"
    producer_runtime: StableId
    allowed_output_kinds: list[StableId] = Field(default_factory=list, max_length=100)
    required_output_kinds: list[StableId] = Field(default_factory=list, max_length=100)
    evidence_contracts: list[MethodologyStageEvidenceContract] = Field(
        min_length=1,
        max_length=200,
    )
    exact_context_echo_required: Literal[True] = True
    unbound_output_allowed: Literal[False] = False
    native_state_authority: Literal[False] = False
    suggested_next_action_authority: Literal[False] = False
    format_only_repair_attempts: Literal[1] = 1

    @model_validator(mode="after")
    def validate_handoff_templates(self):
        if len(self.allowed_output_kinds) != len(set(self.allowed_output_kinds)):
            raise ValueError("methodology Handoff output kinds must be unique")
        if len(self.required_output_kinds) != len(set(self.required_output_kinds)):
            raise ValueError("methodology required Handoff output kinds must be unique")
        if not set(self.required_output_kinds).issubset(self.allowed_output_kinds):
            raise ValueError(
                "methodology required Handoff outputs must be allowed outputs"
            )
        requirement_ids = [
            item.requirement.requirement_id for item in self.evidence_contracts
        ]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError(
                "methodology Handoff Evidence requirement ids must be unique"
            )
        if any(
            item.producer_runtime != self.producer_runtime
            or item.producer_responsibility != "production_execution"
            or item.source == "completion_review"
            for item in self.evidence_contracts
        ):
            raise ValueError(
                "methodology Handoff may require only its production Run Evidence"
            )
        return self


class MethodologyStageGateContract(ProtocolModel):
    gate_key: StableId
    evidence_contracts: list[MethodologyStageEvidenceContract] = Field(
        min_length=1,
        max_length=210,
    )

    @model_validator(mode="after")
    def validate_gate_requirements(self):
        requirement_ids = [
            item.requirement.requirement_id for item in self.evidence_contracts
        ]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError(
                "methodology Gate Evidence requirement ids must be unique"
            )
        return self


class MethodologyStageExecutionContract(ProtocolModel):
    stage_key: StableId
    source_stage_key: StableId
    gate_key: StableId
    sequence: int = Field(ge=1, le=200)
    instance_index: int = Field(ge=1, le=100)
    instance_count: int = Field(ge=1, le=100)
    title: Annotated[str, Field(min_length=1, max_length=300)]
    role: Literal["production_execution"] = "production_execution"
    runtime: StableId
    source_role_profile: ActivationRoleProfile
    context: MethodologyStageContextContract
    handoff: MethodologyStageHandoffContract
    gate: MethodologyStageGateContract

    @model_validator(mode="after")
    def validate_stage_binding(self):
        if self.instance_index > self.instance_count:
            raise ValueError("methodology Stage instance index exceeds its count")
        if self.context.stage_contract.contract_id != self.source_stage_key:
            raise ValueError(
                "methodology Context Stage Contract must retain its source Stage id"
            )
        if self.handoff.producer_runtime != self.runtime:
            raise ValueError(
                "methodology Handoff producer runtime must match the Stage runtime"
            )
        if self.gate.gate_key != self.gate_key:
            raise ValueError(
                "methodology Gate contract key must match the inventory Gate"
            )
        output_kinds = [
            item.kind for item in self.context.output_contracts
        ]
        required_output_kinds = [
            item.kind
            for item in self.context.output_contracts
            if item.required
        ]
        if (
            self.handoff.allowed_output_kinds != output_kinds
            or self.handoff.required_output_kinds != required_output_kinds
        ):
            raise ValueError(
                "methodology Context and Handoff output templates must match"
            )
        handoff_requirement_ids = [
            item.requirement.requirement_id
            for item in self.handoff.evidence_contracts
        ]
        production_gate_requirement_ids = [
            item.requirement.requirement_id
            for item in self.gate.evidence_contracts
            if item.producer_responsibility == "production_execution"
        ]
        if handoff_requirement_ids != production_gate_requirement_ids:
            raise ValueError(
                "methodology Handoff Evidence must exactly match production Gate Evidence"
            )
        return self


class MethodologyExecutionContract(HashSealedModel):
    """One exact, non-dispatching execution contract for a sealed successor."""

    schema_version: Literal["1.0"] = "1.0"
    contract_id: StableId
    materialized_at: AwareDatetime
    authenticated_principal_id: Annotated[
        str,
        Field(min_length=1, max_length=256),
    ]
    authenticated_permission: Literal["control_plane.approve"] = (
        "control_plane.approve"
    )
    project_id: StableId
    task_id: StableId
    task_version: int = Field(ge=1)
    control_task_version: int = Field(ge=1)
    plan_id: StableId
    plan_version: int = Field(ge=1)
    inventory_id: StableId
    inventory_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    migration_request_id: StableId
    migration_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    migration_gate_id: StableId
    migration_gate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    migration_receipt_id: StableId
    migration_receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository: MigrationRepositoryBinding
    activation_id: StableId
    methodology_id: StableId
    methodology_version: Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
    source_graph_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    activation_definition_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_scope: StableId
    runtime_pins: list[MigrationRuntimePin] = Field(min_length=3, max_length=3)
    stages: list[MethodologyStageExecutionContract] = Field(
        min_length=1,
        max_length=200,
    )
    completion_stage_key: StableId
    approval_policy: ActivationApprovalPolicy
    source_profiles_are_routing_authority: Literal[False] = False
    route_activated: Literal[False] = False
    runtime_spawned: Literal[False] = False
    routing_authority: Literal[False] = False
    dispatch_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_execution_graph(self):
        responsibilities = [item.responsibility for item in self.runtime_pins]
        runtimes = [item.runtime for item in self.runtime_pins]
        expected_responsibilities = [
            "production_execution",
            "independent_correctness",
            "methodology_stewardship",
        ]
        if responsibilities != expected_responsibilities:
            raise ValueError(
                "methodology execution runtime responsibilities must be complete and ordered"
            )
        if len(runtimes) != len(set(runtimes)):
            raise ValueError(
                "methodology execution runtimes must remain pairwise distinct"
            )
        production_runtime = self.runtime_pins[0].runtime
        sequences = [item.sequence for item in self.stages]
        if sequences != list(range(1, len(self.stages) + 1)):
            raise ValueError(
                "methodology execution Stage sequences must be contiguous and ordered"
            )
        stage_keys = [item.stage_key for item in self.stages]
        gate_keys = [item.gate_key for item in self.stages]
        if len(stage_keys) != len(set(stage_keys)):
            raise ValueError("methodology execution Stage keys must be unique")
        if len(gate_keys) != len(set(gate_keys)):
            raise ValueError("methodology execution Gate keys must be unique")
        known_stage_keys = set(stage_keys)
        for stage in self.stages:
            for input_contract in stage.context.input_contracts:
                if (
                    input_contract.resolution == "selected_stage_output"
                    and not set(input_contract.producer_stage_keys).issubset(
                        known_stage_keys
                    )
                ):
                    raise ValueError(
                        "methodology input references an unknown producer Stage"
                    )
        if any(item.runtime != production_runtime for item in self.stages):
            raise ValueError(
                "methodology execution Stages must retain the production runtime pin"
            )
        if self.completion_stage_key != self.stages[-1].stage_key:
            raise ValueError(
                "methodology completion reviews must bind the final selected Stage"
            )
        completion_responsibilities = [
            evidence.producer_responsibility
            for evidence in self.stages[-1].gate.evidence_contracts
            if evidence.source == "completion_review"
        ]
        if completion_responsibilities != [
            "independent_correctness",
            "methodology_stewardship",
        ]:
            raise ValueError(
                "final methodology Gate must require both completion reviews"
            )
        for stage in self.stages[:-1]:
            if any(
                evidence.source == "completion_review"
                for evidence in stage.gate.evidence_contracts
            ):
                raise ValueError(
                    "only the final methodology Stage may carry completion reviews"
                )
        return self
