"""Definition-only activation mapping for the pinned AWS AI-DLC source graph."""
from __future__ import annotations

import hashlib
import json
from importlib.resources import files

from agora.protocol.hashing import seal_model_payload
from agora.protocol.methodology_activation import (
    MethodologyActivationDefinition,
    validate_activation_source_binding,
)

from .aws_aidlc import AWS_AIDLC_V2_3_SOURCE_GRAPH


_ACTIVATION_MANIFEST_SHA256 = (
    "219d863f92b9162bef04f133623d020fb9c0ff48676d68521b5ddab47c2ede12"
)


def _load_activation_manifest() -> tuple[dict, str]:
    raw = (
        files("agora.orchestration.activation_manifests")
        .joinpath("aws_aidlc_v2_3_activation.json")
        .read_bytes()
    )
    content_sha256 = hashlib.sha256(raw).hexdigest()
    if content_sha256 != _ACTIVATION_MANIFEST_SHA256:
        raise ValueError("AWS AI-DLC activation manifest hash does not match")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("AWS AI-DLC activation manifest is invalid") from exc
    if payload.get("schema_version") != "1.0":
        raise ValueError("AWS AI-DLC activation manifest schema is unsupported")
    return payload, content_sha256


def _gate_requirements(row: dict) -> list[dict]:
    requirement_prefix = f"{row['source_stage_key']}-"
    requirements = [
        {
            "requirement_id": f"{requirement_prefix}contract-completion",
            "evidence_kind": "stage-contract-completion",
            "source": "contract",
            "subject_id": row["source_stage_key"],
        }
    ]
    requirements.extend(
        {
            "requirement_id": f"{requirement_prefix}output-{output_id}",
            "evidence_kind": "artifact-registration",
            "source": "output",
            "subject_id": output_id,
        }
        for output_id in row["required_outputs"]
    )
    requirements.extend(
        {
            "requirement_id": f"{requirement_prefix}sensor-{sensor['id']}",
            "evidence_kind": sensor["id"],
            "source": "sensor",
            "subject_id": sensor["id"],
        }
        for sensor in row["sensors"]
    )
    if row["source_reviewer_role"] is not None:
        requirements.append(
            {
                "requirement_id": f"{requirement_prefix}source-review",
                "evidence_kind": "source-review",
                "source": "source_review",
                "subject_id": row["source_reviewer_role"],
            }
        )
    return requirements


def _completion_conditions(row: dict) -> list[str]:
    conditions = [row["outputs_text"]]
    if row["required_outputs"]:
        conditions.append(
            "Register every required output as an immutable hash-bound Agora Artifact."
        )
    if row["sensors"]:
        conditions.append(
            "Attach passing Evidence for every required source sensor before "
            "Gate evaluation."
        )
    if row["source_reviewer_role"] is not None:
        conditions.append(
            "Record passing source-review Evidence within the authored reviewer "
            "iteration bound."
        )
    return conditions


def _stage_payload(row: dict, title: str) -> dict:
    required_outputs = [
        {
            "output_id": output_id,
            "required": True,
            "applicable_unit_kinds": row["produces_kinds"].get(output_id, []),
        }
        for output_id in row["required_outputs"]
    ]
    optional_outputs = [
        {
            "output_id": output_id,
            "required": False,
            "applicable_unit_kinds": row["produces_kinds"].get(output_id, []),
        }
        for output_id in row["optional_outputs"]
    ]
    return {
        "source_stage_key": row["source_stage_key"],
        "stage_contract": {
            "contract_id": row["source_stage_key"],
            "title": title,
            "objective": row["condition"],
            "completion_conditions": _completion_conditions(row),
        },
        "source_inputs_text": row["inputs_text"],
        "role_profile": {
            "lead_role": row["lead_role"],
            "support_roles": row["support_roles"],
            "source_reviewer_role": row["source_reviewer_role"],
            "source_reviewer_max_iterations": row[
                "source_reviewer_max_iterations"
            ],
        },
        "input_artifacts": [
            {
                "artifact_id": artifact["artifact"],
                "required": artifact["required"],
                "condition": artifact.get("conditional_on"),
            }
            for artifact in row["input_artifacts"]
        ],
        "output_artifacts": required_outputs + optional_outputs,
        "sensors": [
            {
                "sensor_id": sensor["id"],
                "source_path": sensor["source_path"],
                "runtime_path": sensor["runtime_path"],
                "source_sha256": sensor["content_sha256"],
                "match_pattern": sensor["matches"],
            }
            for sensor in row["sensors"]
        ],
        "gate_requirements": _gate_requirements(row),
        "for_each_artifact": row["for_each_artifact"],
        "workspace_required": row["workspace_required"],
    }


def _scope_seed_requirements(
    stage_payloads: list[dict],
    source_graph,
) -> list[dict]:
    output_producers = {
        output["output_id"]: stage["source_stage_key"]
        for stage in stage_payloads
        for output in stage["output_artifacts"]
    }
    stages_by_key = {
        stage["source_stage_key"]: stage for stage in stage_payloads
    }
    requirements = []
    for scope in source_graph.scopes:
        selected_stage_keys = {
            stage.stage_key
            for stage in source_graph.stages
            if scope.scope_key in stage.scopes
        }
        for source_stage in source_graph.stages:
            if source_stage.stage_key not in selected_stage_keys:
                continue
            for input_artifact in stages_by_key[
                source_stage.stage_key
            ]["input_artifacts"]:
                producer = output_producers[input_artifact["artifact_id"]]
                if input_artifact["required"] and producer not in selected_stage_keys:
                    requirements.append(
                        {
                            "scope_key": scope.scope_key,
                            "consumer_stage_key": source_stage.stage_key,
                            "artifact_id": input_artifact["artifact_id"],
                            "source_producer_stage_key": producer,
                        }
                    )
    return requirements


def build_aws_aidlc_v2_3_activation_definition() -> MethodologyActivationDefinition:
    manifest, manifest_sha256 = _load_activation_manifest()
    source_graph = AWS_AIDLC_V2_3_SOURCE_GRAPH
    title_by_stage = {stage.stage_key: stage.title for stage in source_graph.stages}
    stage_payloads = [
        _stage_payload(row, title_by_stage[row["source_stage_key"]])
        for row in manifest["stages"]
    ]
    payload = {
        "schema_version": "1.0",
        "activation_id": "aws-aidlc-v2-3-activation-definition",
        "methodology_id": source_graph.methodology_id,
        "methodology_version": source_graph.methodology_version,
        "source_graph_sha256": source_graph.content_sha256,
        "source_compiled_graph_sha256": manifest[
            "source_compiled_graph_sha256"
        ],
        "source_activation_manifest_sha256": manifest_sha256,
        "stages": stage_payloads,
        "runtime_policy": {
            "production_runtime": "codex",
            "independent_correctness_runtime": "claude",
            "methodology_steward_runtime": "kiro",
        },
        "budget_policy": {
            "usage_settlement": "exact_or_conservative_reservation",
        },
        "quality_policy": {},
        "rework_policy": {
            "exhaustion_action": "block_and_escalate",
        },
        "approval_policy": {
            "binding_fields": [
                "repository_id",
                "ref",
                "commit_sha",
                "task_id",
                "stage_key",
                "artifact_path",
                "artifact_hash",
                "source_graph_hash",
                "activation_definition_hash",
            ],
        },
        "migration_policy": {},
        "scope_input_policy": {
            "required_input_binding": (
                "selected_upstream_stage_or_hash_bound_task_seed"
            ),
            "missing_required_input_action": "block_activation",
        },
        "scope_seed_requirements": _scope_seed_requirements(
            stage_payloads,
            source_graph,
        ),
        "routing_authority": False,
        "dispatch_authority": False,
        "migration_authority": False,
    }
    definition = MethodologyActivationDefinition.model_validate(
        seal_model_payload(MethodologyActivationDefinition, payload)
    )
    return validate_activation_source_binding(definition, source_graph)


AWS_AIDLC_V2_3_ACTIVATION_DEFINITION = (
    build_aws_aidlc_v2_3_activation_definition()
)
