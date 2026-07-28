from __future__ import annotations

import pytest
from pydantic import ValidationError

from agora.orchestration.aws_aidlc import (
    AWS_AIDLC_V2_3_SOURCE_GRAPH,
    build_aws_aidlc_v2_3_source_graph,
)
from agora.orchestration.methodology import (
    FOUNDATION_METHODOLOGY,
    methodology_sha256,
)
from agora.protocol.hashing import seal_model_payload
from agora.protocol.methodology import MethodologySourceGraph
from agora.protocol.schema_registry import SCHEMA_MODELS


def _validate_resealed(payload: dict) -> MethodologySourceGraph:
    return MethodologySourceGraph.model_validate(
        seal_model_payload(MethodologySourceGraph, payload)
    )


def test_aws_aidlc_source_graph_is_complete_pinned_and_non_dispatching():
    graph = AWS_AIDLC_V2_3_SOURCE_GRAPH

    assert graph.schema_version == "1.0"
    assert graph.methodology_id == "aws-aidlc-workflows"
    assert graph.methodology_version == "2.3.0"
    assert graph.authority_basis == "user_confirmed_official_source"
    assert graph.source.repository_url == "https://github.com/awslabs/aidlc-workflows"
    assert graph.source.release_tag == "v2.3.0"
    assert (
        graph.source.commit_sha
        == "29a31f7899731b53f2b8d7f76cd223f9a8a25859"
    )
    assert graph.source.license_spdx == "Apache-2.0"
    assert [
        artifact.model_dump(mode="json")
        for artifact in graph.source.external_artifacts
    ] == [
        {
            "role": "method_definition",
            "url": "https://prod.d13rzhkk8cj2z0.amplifyapp.com/aidlc.pdf",
            "content_sha256": (
                "6fdd881f6a56a4d1bed3605ca9c167011f92ef6430588679311430ce95fc692f"
            ),
        }
    ]
    assert graph.phases == [
        "initialization",
        "ideation",
        "inception",
        "construction",
        "operation",
    ]
    assert len(graph.stages) == 32
    assert len(graph.scopes) == 9
    assert len(graph.source.artifacts) == 46
    assert graph.structured_rework_edges is False
    assert graph.routing_authority is False
    assert graph.dispatch_authority is False
    assert "bounded-rework-limits" in graph.unresolved_execution_requirements
    assert graph.content_sha256 == (
        "668a379e4b6ecbed1aaf47e0823b43df147b7c239a8a4ab03ba43b71030e057d"
    )


def test_aws_aidlc_source_graph_preserves_dag_scope_and_unit_expansion_facts():
    graph = AWS_AIDLC_V2_3_SOURCE_GRAPH
    by_stage = {stage.stage_key: stage for stage in graph.stages}
    scope_counts = {
        scope.scope_key: sum(
            scope.scope_key in stage.scopes for stage in graph.stages
        )
        for scope in graph.scopes
    }

    assert scope_counts["enterprise"] == 32
    assert scope_counts["feature"] == 32
    assert scope_counts["bugfix"] == 7
    assert by_stage["requirements-analysis"].requires_stage == [
        "approval-handoff",
        "reverse-engineering",
    ]
    assert by_stage["code-generation"].requires_stage == [
        "units-generation",
        "functional-design",
        "nfr-requirements",
        "nfr-design",
        "infrastructure-design",
    ]
    assert {
        stage.stage_key
        for stage in graph.stages
        if stage.for_each_artifact == "unit-of-work"
    } == {
        "functional-design",
        "nfr-requirements",
        "nfr-design",
        "infrastructure-design",
        "code-generation",
    }


def test_aws_aidlc_source_graph_is_deterministic_and_keeps_foundation_unchanged():
    rebuilt = build_aws_aidlc_v2_3_source_graph()

    assert rebuilt == AWS_AIDLC_V2_3_SOURCE_GRAPH
    assert rebuilt.content_sha256 == AWS_AIDLC_V2_3_SOURCE_GRAPH.content_sha256
    assert FOUNDATION_METHODOLOGY.provisional is True
    assert FOUNDATION_METHODOLOGY.version == "0.1"
    assert (
        methodology_sha256(FOUNDATION_METHODOLOGY)
        == "fc991d78608ec88356888e070c5e1327ffc4f215b5bbc72036a04bb92bab2928"
    )


def test_methodology_source_graph_fails_closed_for_unknown_dependency():
    payload = AWS_AIDLC_V2_3_SOURCE_GRAPH.model_dump(mode="json")
    payload["stages"][5]["requires_stage"].append("missing-stage")

    with pytest.raises(ValidationError, match="unknown dependencies"):
        _validate_resealed(payload)


def test_methodology_source_graph_fails_closed_for_non_upstream_dependency():
    payload = AWS_AIDLC_V2_3_SOURCE_GRAPH.model_dump(mode="json")
    payload["stages"][0]["requires_stage"] = ["state-init"]

    with pytest.raises(ValidationError, match="is not upstream"):
        _validate_resealed(payload)


def test_methodology_source_graph_fails_closed_for_source_manifest_drift():
    payload = AWS_AIDLC_V2_3_SOURCE_GRAPH.model_dump(mode="json")
    payload["source"]["artifacts"] = [
        artifact
        for artifact in payload["source"]["artifacts"]
        if artifact["path"]
        != "core/aidlc-common/stages/operation/feedback-optimization.md"
    ]

    with pytest.raises(ValidationError, match="lacks a pinned stage source"):
        _validate_resealed(payload)


def test_methodology_source_graph_requires_external_method_definition():
    payload = AWS_AIDLC_V2_3_SOURCE_GRAPH.model_dump(mode="json")
    payload["source"]["external_artifacts"] = []

    with pytest.raises(ValidationError, match="at least 1 item"):
        _validate_resealed(payload)


def test_methodology_source_graph_rejects_forged_content_hash_and_unknown_fields():
    payload = AWS_AIDLC_V2_3_SOURCE_GRAPH.model_dump(mode="json")
    payload["content_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="content_sha256"):
        MethodologySourceGraph.model_validate(payload)

    payload = AWS_AIDLC_V2_3_SOURCE_GRAPH.model_dump(mode="json")
    payload["unexpected"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        MethodologySourceGraph.model_validate(payload)


def test_methodology_source_graph_schema_is_registered():
    assert SCHEMA_MODELS["methodology-source-graph"] is MethodologySourceGraph
