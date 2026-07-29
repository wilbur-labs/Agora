"""Non-authoritative methodology activation-definition contracts.

An activation definition materializes source-bound execution requirements but
does not itself migrate a Task or acquire routing, dispatch, or migration
authority.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .methodology import MethodologySourceGraph, SourcePath
from .models import (
    HashSealedModel,
    NonBlank,
    ProtocolModel,
    Sha256Hex,
    StableId,
    StageContract,
)


class ActivationArtifactInput(ProtocolModel):
    artifact_id: StableId
    required: bool
    condition: StableId | None = None


class ActivationArtifactOutput(ProtocolModel):
    output_id: StableId
    required: bool
    applicable_unit_kinds: list[StableId] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def validate_unit_kinds(self):
        if len(self.applicable_unit_kinds) != len(set(self.applicable_unit_kinds)):
            raise ValueError("activation output unit kinds must be unique")
        return self


class ActivationSensorRequirement(ProtocolModel):
    sensor_id: StableId
    source_path: SourcePath
    runtime_path: SourcePath
    source_sha256: Sha256Hex
    match_pattern: Annotated[str, Field(min_length=1, max_length=1000)]


class ActivationRoleProfile(ProtocolModel):
    lead_role: StableId
    support_roles: list[StableId] = Field(default_factory=list, max_length=32)
    source_reviewer_role: StableId | None = None
    source_reviewer_max_iterations: int = Field(default=0, ge=0, le=10)

    @model_validator(mode="after")
    def validate_role_separation(self):
        if len(self.support_roles) != len(set(self.support_roles)):
            raise ValueError("activation support roles must be unique")
        if self.lead_role in self.support_roles:
            raise ValueError("activation lead role cannot also be a support role")
        if self.source_reviewer_role is None:
            if self.source_reviewer_max_iterations != 0:
                raise ValueError("review iterations require a source reviewer role")
        else:
            if self.source_reviewer_max_iterations < 1:
                raise ValueError("source reviewer role requires a positive iteration bound")
            if self.source_reviewer_role == self.lead_role:
                raise ValueError("source reviewer role must differ from the lead role")
            if self.source_reviewer_role in self.support_roles:
                raise ValueError("source reviewer role must differ from support roles")
        return self


class ActivationGateRequirement(ProtocolModel):
    requirement_id: StableId
    evidence_kind: StableId
    source: Literal["contract", "output", "sensor", "source_review"]
    subject_id: StableId
    severity: Literal["blocker"] = "blocker"
    failure_action: Literal["block_stage"] = "block_stage"


class ActivationStageDefinition(ProtocolModel):
    source_stage_key: StableId
    stage_contract: StageContract
    source_inputs_text: NonBlank
    role_profile: ActivationRoleProfile
    input_artifacts: list[ActivationArtifactInput] = Field(
        default_factory=list,
        max_length=100,
    )
    output_artifacts: list[ActivationArtifactOutput] = Field(
        default_factory=list,
        max_length=100,
    )
    sensors: list[ActivationSensorRequirement] = Field(
        default_factory=list,
        max_length=32,
    )
    gate_requirements: list[ActivationGateRequirement] = Field(
        min_length=1,
        max_length=200,
    )
    for_each_artifact: StableId | None = None
    workspace_required: bool = False

    @model_validator(mode="after")
    def validate_stage_definition(self):
        if self.stage_contract.contract_id != self.source_stage_key:
            raise ValueError("activation Stage Contract id must match its source Stage")

        input_ids = [item.artifact_id for item in self.input_artifacts]
        if len(input_ids) != len(set(input_ids)):
            raise ValueError("activation Stage input artifacts must be unique")
        output_ids = [item.output_id for item in self.output_artifacts]
        if len(output_ids) != len(set(output_ids)):
            raise ValueError("activation Stage output artifacts must be unique")
        sensor_ids = [item.sensor_id for item in self.sensors]
        if len(sensor_ids) != len(set(sensor_ids)):
            raise ValueError("activation Stage sensors must be unique")

        requirement_prefix = f"{self.source_stage_key}-"
        expected_requirements = {
            f"{requirement_prefix}contract-completion": (
                "stage-contract-completion",
                "contract",
                self.source_stage_key,
            )
        }
        for output in self.output_artifacts:
            if output.required:
                expected_requirements[
                    f"{requirement_prefix}output-{output.output_id}"
                ] = (
                    "artifact-registration",
                    "output",
                    output.output_id,
                )
        for sensor in self.sensors:
            expected_requirements[
                f"{requirement_prefix}sensor-{sensor.sensor_id}"
            ] = (
                sensor.sensor_id,
                "sensor",
                sensor.sensor_id,
            )
        if self.role_profile.source_reviewer_role is not None:
            expected_requirements[f"{requirement_prefix}source-review"] = (
                "source-review",
                "source_review",
                self.role_profile.source_reviewer_role,
            )

        requirement_ids = [item.requirement_id for item in self.gate_requirements]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("activation Stage Gate requirement ids must be unique")
        actual_requirements = {
            item.requirement_id: (
                item.evidence_kind,
                item.source,
                item.subject_id,
            )
            for item in self.gate_requirements
        }
        if actual_requirements != expected_requirements:
            raise ValueError(
                "activation Stage Gate requirements must exactly cover the "
                "contract, required outputs, sensors, and source review"
            )
        return self


class ActivationRuntimePolicy(ProtocolModel):
    production_runtime: Literal["codex"] = "codex"
    independent_correctness_runtime: Literal["claude"] = "claude"
    methodology_steward_runtime: Literal["kiro"] = "kiro"
    pairwise_distinct_runtime_families: Literal[True] = True
    task_runtime_pins_required: Literal[True] = True
    source_agent_roles_are_profiles_only: Literal[True] = True


class ActivationBudgetPolicy(ProtocolModel):
    task_envelope_required: Literal[True] = True
    per_run_reservation_required: Literal[True] = True
    unbounded_native_usage_ack_required: Literal[True] = True
    usage_settlement: Literal["exact_or_conservative_reservation"]
    static_stage_token_limits_authored: Literal[False] = False
    activation_task_must_supply_limits: Literal[True] = True


class ActivationQualityPolicy(ProtocolModel):
    independent_run_dimensions_required: Literal[True] = True
    format_only_repair_attempts: Literal[1] = 1
    stage_contract_evidence_required: Literal[True] = True
    source_sensor_evidence_required: Literal[True] = True
    independent_correctness_completion_gate: Literal[True] = True
    methodology_completion_gate: Literal[True] = True
    human_completion_approval_required: Literal[True] = True


class ActivationReworkPolicy(ProtocolModel):
    source_review_iteration_bounds_enforced: Literal[True] = True
    automatic_cross_stage_rework: Literal[False] = False
    structured_cross_stage_rework_edges: Literal[False] = False
    exhaustion_action: Literal["block_and_escalate"]
    keep_modify_redo_requires_task_decision: Literal[True] = True


ApprovalBindingField = Literal[
    "repository_id",
    "ref",
    "commit_sha",
    "task_id",
    "stage_key",
    "artifact_path",
    "artifact_hash",
    "source_graph_hash",
    "activation_definition_hash",
]


class ActivationApprovalPolicy(ProtocolModel):
    migration_human_approval_required: Literal[True] = True
    completion_human_approval_required: Literal[True] = True
    binding_fields: list[ApprovalBindingField] = Field(min_length=9, max_length=9)

    @model_validator(mode="after")
    def validate_binding_fields(self):
        expected = [
            "repository_id",
            "ref",
            "commit_sha",
            "task_id",
            "stage_key",
            "artifact_path",
            "artifact_hash",
            "source_graph_hash",
            "activation_definition_hash",
        ]
        if self.binding_fields != expected:
            raise ValueError("activation approval binding fields must be complete and ordered")
        return self


class ActivationMigrationPolicy(ProtocolModel):
    existing_tasks_preserved: Literal[True] = True
    in_place_methodology_mutation: Literal[False] = False
    explicit_migration_gate_required: Literal[True] = True
    automatic_reroute: Literal[False] = False
    activation_command_exposed: Literal[False] = False


class ActivationScopeInputPolicy(ProtocolModel):
    required_input_binding: Literal[
        "selected_upstream_stage_or_hash_bound_task_seed"
    ]
    missing_required_input_action: Literal["block_activation"]
    optional_input_absence_allowed: Literal[True] = True
    conditional_input_requires_matching_task_condition: Literal[True] = True


class ActivationScopeSeedRequirement(ProtocolModel):
    scope_key: StableId
    consumer_stage_key: StableId
    artifact_id: StableId
    source_producer_stage_key: StableId
    binding: Literal["hash_bound_task_seed"] = "hash_bound_task_seed"


class MethodologyActivationDefinition(HashSealedModel):
    """Sealed execution definition with no Task mutation authority."""

    schema_version: Literal["1.0"] = "1.0"
    activation_id: StableId
    methodology_id: StableId
    methodology_version: Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
    source_graph_sha256: Sha256Hex
    source_compiled_graph_sha256: Sha256Hex
    source_activation_manifest_sha256: Sha256Hex
    stages: list[ActivationStageDefinition] = Field(min_length=1, max_length=200)
    runtime_policy: ActivationRuntimePolicy
    budget_policy: ActivationBudgetPolicy
    quality_policy: ActivationQualityPolicy
    rework_policy: ActivationReworkPolicy
    approval_policy: ActivationApprovalPolicy
    migration_policy: ActivationMigrationPolicy
    scope_input_policy: ActivationScopeInputPolicy
    scope_seed_requirements: list[ActivationScopeSeedRequirement] = Field(
        min_length=1,
        max_length=500,
    )
    routing_authority: Literal[False] = False
    dispatch_authority: Literal[False] = False
    migration_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_definition_graph(self):
        stage_keys = [stage.source_stage_key for stage in self.stages]
        if len(stage_keys) != len(set(stage_keys)):
            raise ValueError("activation definition Stage keys must be unique")
        stage_positions = {
            stage_key: position for position, stage_key in enumerate(stage_keys)
        }

        output_producers: dict[str, str] = {}
        gate_requirement_ids: list[str] = []
        for stage in self.stages:
            gate_requirement_ids.extend(
                requirement.requirement_id
                for requirement in stage.gate_requirements
            )
            for output in stage.output_artifacts:
                if output.output_id in output_producers:
                    raise ValueError(
                        "activation output artifacts must have one source producer"
                    )
                output_producers[output.output_id] = stage.source_stage_key
        if len(gate_requirement_ids) != len(set(gate_requirement_ids)):
            raise ValueError(
                "activation Gate requirement ids must be unique across Stages"
            )

        seed_keys = [
            (
                item.scope_key,
                item.consumer_stage_key,
                item.artifact_id,
                item.source_producer_stage_key,
            )
            for item in self.scope_seed_requirements
        ]
        if len(seed_keys) != len(set(seed_keys)):
            raise ValueError("activation scope seed requirements must be unique")
        for item in self.scope_seed_requirements:
            if item.consumer_stage_key not in stage_positions:
                raise ValueError("activation scope seed references an unknown consumer")
            producer = output_producers.get(item.artifact_id)
            if producer is None:
                raise ValueError("activation scope seed references an unknown artifact")
            if producer != item.source_producer_stage_key:
                raise ValueError("activation scope seed producer does not match")
            if stage_positions[producer] >= stage_positions[item.consumer_stage_key]:
                raise ValueError("activation scope seed producer must be upstream")

        for stage in self.stages:
            for input_artifact in stage.input_artifacts:
                producer = output_producers.get(input_artifact.artifact_id)
                if producer is None:
                    raise ValueError(
                        f"activation Stage {stage.source_stage_key} references "
                        "an unknown input artifact"
                    )
                if stage_positions[producer] >= stage_positions[stage.source_stage_key]:
                    raise ValueError(
                        f"activation Stage {stage.source_stage_key} input artifact "
                        "does not come from an upstream Stage"
                    )
            if stage.for_each_artifact is not None:
                producer = output_producers.get(stage.for_each_artifact)
                if producer is None:
                    raise ValueError(
                        f"activation Stage {stage.source_stage_key} references "
                        "an unknown for-each artifact"
                    )
                if stage_positions[producer] >= stage_positions[stage.source_stage_key]:
                    raise ValueError(
                        f"activation Stage {stage.source_stage_key} for-each artifact "
                        "does not come from an upstream Stage"
                    )
        return self


def validate_activation_source_binding(
    definition: MethodologyActivationDefinition,
    source_graph: MethodologySourceGraph,
) -> MethodologyActivationDefinition:
    """Fail closed when an activation definition drifts from its source graph."""

    if definition.methodology_id != source_graph.methodology_id:
        raise ValueError("activation methodology id does not match its source graph")
    if definition.methodology_version != source_graph.methodology_version:
        raise ValueError("activation methodology version does not match its source graph")
    if definition.source_graph_sha256 != source_graph.content_sha256:
        raise ValueError("activation source graph hash does not match")

    compiled_graph_hashes = [
        artifact.content_sha256
        for artifact in source_graph.source.artifacts
        if artifact.role == "compiled_graph"
    ]
    if compiled_graph_hashes != [definition.source_compiled_graph_sha256]:
        raise ValueError("activation compiled source graph hash does not match")

    source_stages = {stage.stage_key: stage for stage in source_graph.stages}
    activation_keys = [stage.source_stage_key for stage in definition.stages]
    if activation_keys != [stage.stage_key for stage in source_graph.stages]:
        raise ValueError("activation Stage order must exactly match its source graph")
    for stage in definition.stages:
        source_stage = source_stages[stage.source_stage_key]
        if stage.stage_contract.title != source_stage.title:
            raise ValueError(
                f"activation Stage {stage.source_stage_key} title does not match"
            )
        if stage.for_each_artifact != source_stage.for_each_artifact:
            raise ValueError(
                f"activation Stage {stage.source_stage_key} for-each binding does not match"
            )

    output_producers = {
        output.output_id: stage.source_stage_key
        for stage in definition.stages
        for output in stage.output_artifacts
    }
    activation_stages = {
        stage.source_stage_key: stage for stage in definition.stages
    }
    expected_scope_seeds = []
    for scope in source_graph.scopes:
        selected_stage_keys = {
            stage.stage_key
            for stage in source_graph.stages
            if scope.scope_key in stage.scopes
        }
        for source_stage in source_graph.stages:
            if source_stage.stage_key not in selected_stage_keys:
                continue
            activation_stage = activation_stages[source_stage.stage_key]
            for input_artifact in activation_stage.input_artifacts:
                producer = output_producers[input_artifact.artifact_id]
                if input_artifact.required and producer not in selected_stage_keys:
                    expected_scope_seeds.append(
                        (
                            scope.scope_key,
                            source_stage.stage_key,
                            input_artifact.artifact_id,
                            producer,
                        )
                    )
    actual_scope_seeds = [
        (
            item.scope_key,
            item.consumer_stage_key,
            item.artifact_id,
            item.source_producer_stage_key,
        )
        for item in definition.scope_seed_requirements
    ]
    if actual_scope_seeds != expected_scope_seeds:
        raise ValueError(
            "activation scope seed requirements do not match source scope closure"
        )
    return definition
