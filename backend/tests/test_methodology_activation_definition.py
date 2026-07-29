from __future__ import annotations

import pytest
from pydantic import ValidationError

from agora.orchestration.aws_aidlc import AWS_AIDLC_V2_3_SOURCE_GRAPH
from agora.orchestration.aws_aidlc_activation import (
    AWS_AIDLC_V2_3_ACTIVATION_DEFINITION,
    build_aws_aidlc_v2_3_activation_definition,
)
from agora.orchestration.methodology import (
    FOUNDATION_METHODOLOGY,
    methodology_sha256,
)
from agora.protocol.hashing import seal_model_payload
from agora.protocol.methodology_activation import (
    MethodologyActivationDefinition,
    validate_activation_source_binding,
)
from agora.protocol.schema_registry import SCHEMA_MODELS


def _validate_resealed(payload: dict) -> MethodologyActivationDefinition:
    return MethodologyActivationDefinition.model_validate(
        seal_model_payload(MethodologyActivationDefinition, payload)
    )


def test_aws_aidlc_activation_definition_is_complete_pinned_and_non_authoritative():
    definition = AWS_AIDLC_V2_3_ACTIVATION_DEFINITION

    assert definition.schema_version == "1.0"
    assert definition.activation_id == "aws-aidlc-v2-3-activation-definition"
    assert definition.methodology_id == "aws-aidlc-workflows"
    assert definition.methodology_version == "2.3.0"
    assert (
        definition.source_graph_sha256
        == "668a379e4b6ecbed1aaf47e0823b43df147b7c239a8a4ab03ba43b71030e057d"
    )
    assert (
        definition.source_compiled_graph_sha256
        == "9de074e882c18bcc1285a953366a7793149d05a349657a7989f0e54b2fdd1430"
    )
    assert (
        definition.source_activation_manifest_sha256
        == "219d863f92b9162bef04f133623d020fb9c0ff48676d68521b5ddab47c2ede12"
    )
    assert definition.content_sha256 == (
        "c9d9b075a5219292d94e1fa3aff2383dc1e98bb5518cd486227f85b20b45af6d"
    )
    assert len(definition.stages) == 32
    assert sum(len(stage.input_artifacts) for stage in definition.stages) == 132
    assert sum(len(stage.output_artifacts) for stage in definition.stages) == 122
    assert sum(len(stage.gate_requirements) for stage in definition.stages) == 232
    assert len(
        {
            requirement.requirement_id
            for stage in definition.stages
            for requirement in stage.gate_requirements
        }
    ) == 232
    assert definition.routing_authority is False
    assert definition.dispatch_authority is False
    assert definition.migration_authority is False


def test_aws_aidlc_activation_definition_preserves_execution_metadata():
    definition = AWS_AIDLC_V2_3_ACTIVATION_DEFINITION
    by_stage = {stage.source_stage_key: stage for stage in definition.stages}

    assert {
        stage.source_stage_key for stage in definition.stages if stage.workspace_required
    } == {"code-generation"}
    assert {
        stage.source_stage_key
        for stage in definition.stages
        if stage.for_each_artifact == "unit-of-work"
    } == {
        "functional-design",
        "nfr-requirements",
        "nfr-design",
        "infrastructure-design",
        "code-generation",
    }
    assert by_stage["rough-mockups"].role_profile.source_reviewer_role == (
        "aidlc-product-lead-agent"
    )
    assert (
        by_stage["rough-mockups"].role_profile.source_reviewer_max_iterations == 2
    )
    assert by_stage["practices-discovery"].input_artifacts[0].condition == "brownfield"

    functional_outputs = {
        output.output_id: output for output in by_stage["functional-design"].output_artifacts
    }
    assert functional_outputs["frontend-components"].required is False
    assert functional_outputs["frontend-components"].applicable_unit_kinds == ["ui"]


def test_activation_runtime_budget_quality_rework_and_migration_policies_are_closed():
    definition = AWS_AIDLC_V2_3_ACTIVATION_DEFINITION

    assert definition.runtime_policy.model_dump(mode="json") == {
        "production_runtime": "codex",
        "independent_correctness_runtime": "claude",
        "methodology_steward_runtime": "kiro",
        "pairwise_distinct_runtime_families": True,
        "task_runtime_pins_required": True,
        "source_agent_roles_are_profiles_only": True,
    }
    assert definition.budget_policy.static_stage_token_limits_authored is False
    assert definition.budget_policy.activation_task_must_supply_limits is True
    assert definition.quality_policy.format_only_repair_attempts == 1
    assert definition.rework_policy.automatic_cross_stage_rework is False
    assert definition.rework_policy.structured_cross_stage_rework_edges is False
    assert definition.rework_policy.exhaustion_action == "block_and_escalate"
    assert definition.migration_policy.existing_tasks_preserved is True
    assert definition.migration_policy.in_place_methodology_mutation is False
    assert definition.migration_policy.explicit_migration_gate_required is True
    assert definition.migration_policy.automatic_reroute is False
    assert definition.migration_policy.activation_command_exposed is False
    assert definition.scope_input_policy.required_input_binding == (
        "selected_upstream_stage_or_hash_bound_task_seed"
    )
    assert (
        definition.scope_input_policy.missing_required_input_action
        == "block_activation"
    )
    assert len(definition.scope_seed_requirements) == 27
    assert {
        (
            item.scope_key,
            item.consumer_stage_key,
            item.artifact_id,
            item.source_producer_stage_key,
        )
        for item in definition.scope_seed_requirements
    } >= {
        ("bugfix", "code-generation", "unit-of-work", "units-generation"),
        ("infra", "ci-pipeline", "build-test-results", "build-and-test"),
        ("workshop", "refined-mockups", "wireframes", "rough-mockups"),
    }


def test_activation_stage_gates_cover_contract_outputs_sensors_and_source_review():
    by_stage = {
        stage.source_stage_key: stage
        for stage in AWS_AIDLC_V2_3_ACTIVATION_DEFINITION.stages
    }

    assert {
        requirement.requirement_id
        for requirement in by_stage["workspace-scaffold"].gate_requirements
    } == {"workspace-scaffold-contract-completion"}
    assert {
        requirement.requirement_id
        for requirement in by_stage["requirements-analysis"].gate_requirements
    } == {
        "requirements-analysis-contract-completion",
        "requirements-analysis-output-requirements",
        "requirements-analysis-output-requirements-analysis-questions",
        "requirements-analysis-sensor-required-sections",
        "requirements-analysis-sensor-upstream-coverage",
        "requirements-analysis-source-review",
    }
    assert {
        requirement.requirement_id
        for requirement in by_stage["functional-design"].gate_requirements
    } >= {
        "functional-design-output-business-logic-model",
        "functional-design-output-business-rules",
        "functional-design-output-domain-entities",
        "functional-design-sensor-linter",
        "functional-design-sensor-type-check",
        "functional-design-source-review",
    }
    assert "functional-design-output-frontend-components" not in {
        requirement.requirement_id
        for requirement in by_stage["functional-design"].gate_requirements
    }
    assert {
        sensor.sensor_id: sensor.source_sha256
        for sensor in by_stage["functional-design"].sensors
    } == {
        "required-sections": (
            "52b9631c830eb383166173037a922ec0ccd0ef3171c1f52796864302bf2acf08"
        ),
        "upstream-coverage": (
            "223b8a8a644bab117d8a6afbde049f721c678eacdbe61847a542ec91e1a94ed8"
        ),
        "linter": (
            "11a082c26e181c79fb2107fdd750e5b58dfe79bc377deadaeb955e5abed67262"
        ),
        "type-check": (
            "765688e25fd50054761f59ddf5cd68a898fe25e64b4c0a5a3c6e3e009f0adc1e"
        ),
    }
    required_sections = by_stage["functional-design"].sensors[0]
    assert required_sections.source_path == (
        "dist/codex/.codex/sensors/aidlc-required-sections.md"
    )
    assert required_sections.runtime_path == (
        ".codex/sensors/aidlc-required-sections.md"
    )


def test_activation_definition_is_deterministic_and_preserves_prior_baselines():
    rebuilt = build_aws_aidlc_v2_3_activation_definition()

    assert rebuilt == AWS_AIDLC_V2_3_ACTIVATION_DEFINITION
    assert AWS_AIDLC_V2_3_SOURCE_GRAPH.content_sha256 == (
        "668a379e4b6ecbed1aaf47e0823b43df147b7c239a8a4ab03ba43b71030e057d"
    )
    assert FOUNDATION_METHODOLOGY.provisional is True
    assert (
        methodology_sha256(FOUNDATION_METHODOLOGY)
        == "fc991d78608ec88356888e070c5e1327ffc4f215b5bbc72036a04bb92bab2928"
    )


def test_activation_definition_fails_closed_for_unknown_input_artifact():
    payload = AWS_AIDLC_V2_3_ACTIVATION_DEFINITION.model_dump(mode="json")
    payload["stages"][5]["input_artifacts"].append(
        {"artifact_id": "missing-artifact", "required": True, "condition": None}
    )

    with pytest.raises(ValidationError, match="unknown input artifact"):
        _validate_resealed(payload)


def test_activation_definition_fails_closed_for_incomplete_stage_gate():
    payload = AWS_AIDLC_V2_3_ACTIVATION_DEFINITION.model_dump(mode="json")
    payload["stages"][12]["gate_requirements"] = [
        requirement
        for requirement in payload["stages"][12]["gate_requirements"]
        if requirement["requirement_id"] != "requirements-analysis-source-review"
    ]

    with pytest.raises(ValidationError, match="must exactly cover"):
        _validate_resealed(payload)


def test_activation_definition_fails_closed_for_reviewer_bound_drift():
    payload = AWS_AIDLC_V2_3_ACTIVATION_DEFINITION.model_dump(mode="json")
    payload["stages"][12]["role_profile"]["source_reviewer_max_iterations"] = 0

    with pytest.raises(ValidationError, match="positive iteration bound"):
        _validate_resealed(payload)


def test_activation_source_binding_rejects_missing_or_reordered_stage():
    payload = AWS_AIDLC_V2_3_ACTIVATION_DEFINITION.model_dump(mode="json")
    payload["stages"] = payload["stages"][:-1]
    definition = _validate_resealed(payload)

    with pytest.raises(ValueError, match="Stage order"):
        validate_activation_source_binding(definition, AWS_AIDLC_V2_3_SOURCE_GRAPH)


def test_activation_source_binding_rejects_scope_seed_drift():
    payload = AWS_AIDLC_V2_3_ACTIVATION_DEFINITION.model_dump(mode="json")
    payload["scope_seed_requirements"] = payload["scope_seed_requirements"][:-1]
    definition = _validate_resealed(payload)

    with pytest.raises(ValueError, match="scope seed requirements"):
        validate_activation_source_binding(definition, AWS_AIDLC_V2_3_SOURCE_GRAPH)


def test_activation_definition_rejects_forged_hash_and_unknown_fields():
    payload = AWS_AIDLC_V2_3_ACTIVATION_DEFINITION.model_dump(mode="json")
    payload["content_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="content_sha256"):
        MethodologyActivationDefinition.model_validate(payload)

    payload = AWS_AIDLC_V2_3_ACTIVATION_DEFINITION.model_dump(mode="json")
    payload["unexpected"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        MethodologyActivationDefinition.model_validate(payload)


def test_methodology_activation_definition_schema_is_registered():
    assert (
        SCHEMA_MODELS["methodology-activation-definition"]
        is MethodologyActivationDefinition
    )
