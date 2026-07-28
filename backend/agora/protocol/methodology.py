"""Source-bound methodology graph contracts.

These contracts freeze an external methodology source without granting it
Agora routing or dispatch authority. A separate reviewed activation/migration
contract is required before a source graph can affect Task execution.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .models import HashSealedModel, ProtocolModel, Sha256Hex, StableId


SourcePath = Annotated[str, Field(min_length=1, max_length=512)]
SourceArtifactRole = Literal[
    "specification",
    "stage_definition",
    "rework_protocol",
    "compiled_graph",
    "scope_grid",
    "stage",
    "scope",
]
ExternalSourceArtifactRole = Literal["method_definition"]


def _validate_source_path(value: str) -> str:
    if "\\" in value or value.startswith("/"):
        raise ValueError("source path must be a relative POSIX path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("source path contains an empty or traversal segment")
    return value


class MethodologySourceArtifact(ProtocolModel):
    role: SourceArtifactRole
    path: SourcePath
    content_sha256: Sha256Hex

    _source_path = field_validator("path")(_validate_source_path)


class MethodologyExternalSourceArtifact(ProtocolModel):
    role: ExternalSourceArtifactRole
    url: Annotated[
        str,
        Field(
            min_length=1,
            max_length=512,
            pattern=r"^https://[^\s]+$",
        ),
    ]
    content_sha256: Sha256Hex


class MethodologySourcePin(ProtocolModel):
    repository_url: Annotated[
        str,
        Field(
            min_length=1,
            max_length=512,
            pattern=r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$",
        ),
    ]
    release_tag: Annotated[
        str,
        Field(
            min_length=1,
            max_length=64,
            pattern=r"^v\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$",
        ),
    ]
    commit_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    license_spdx: StableId
    artifacts: list[MethodologySourceArtifact] = Field(min_length=1, max_length=100)
    external_artifacts: list[MethodologyExternalSourceArtifact] = Field(
        min_length=1,
        max_length=10,
    )

    @model_validator(mode="after")
    def validate_artifact_manifest(self):
        paths = [artifact.path for artifact in self.artifacts]
        if len(paths) != len(set(paths)):
            raise ValueError("methodology source artifact paths must be unique")
        urls = [artifact.url for artifact in self.external_artifacts]
        if len(urls) != len(set(urls)):
            raise ValueError("methodology external source artifact URLs must be unique")
        return self


class MethodologyStageNode(ProtocolModel):
    source_number: Annotated[str, Field(pattern=r"^\d+\.\d+$")]
    stage_key: StableId
    title: Annotated[str, Field(min_length=1, max_length=200)]
    phase: StableId
    execution: Literal["always", "conditional"]
    mode: Literal["inline", "subagent", "agent-team"]
    for_each_artifact: StableId | None = None
    requires_stage: list[StableId] = Field(default_factory=list, max_length=50)
    scopes: list[StableId] = Field(min_length=1, max_length=32)
    source_path: SourcePath

    _source_path = field_validator("source_path")(_validate_source_path)


class MethodologyScopeProfile(ProtocolModel):
    scope_key: StableId
    depth: Literal["minimal", "standard", "comprehensive"]
    test_strategy: Literal["minimal", "standard", "comprehensive"] | None = None
    source_path: SourcePath

    _source_path = field_validator("source_path")(_validate_source_path)


class MethodologySourceGraph(HashSealedModel):
    """Immutable source graph that is not yet an executable Agora method."""

    schema_version: Literal["1.0"] = "1.0"
    methodology_id: StableId
    methodology_version: Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
    authority_basis: Literal["user_confirmed_official_source"]
    provisional: Literal[False] = False
    source: MethodologySourcePin
    phases: list[StableId] = Field(min_length=1, max_length=16)
    stages: list[MethodologyStageNode] = Field(min_length=1, max_length=200)
    scopes: list[MethodologyScopeProfile] = Field(min_length=1, max_length=32)
    structured_rework_edges: Literal[False] = False
    routing_authority: Literal[False] = False
    dispatch_authority: Literal[False] = False
    unresolved_execution_requirements: list[StableId] = Field(
        min_length=1,
        max_length=32,
    )

    @model_validator(mode="after")
    def validate_source_graph(self):
        artifact_by_path = {artifact.path: artifact for artifact in self.source.artifacts}
        role_paths: dict[str, set[str]] = {}
        for artifact in self.source.artifacts:
            role_paths.setdefault(artifact.role, set()).add(artifact.path)

        for role in {
            "specification",
            "stage_definition",
            "rework_protocol",
            "compiled_graph",
            "scope_grid",
        }:
            if len(role_paths.get(role, set())) != 1:
                raise ValueError(f"methodology source requires exactly one {role} artifact")

        external_roles = [
            artifact.role for artifact in self.source.external_artifacts
        ]
        if external_roles.count("method_definition") != 1:
            raise ValueError(
                "methodology source requires exactly one external method_definition artifact"
            )

        if len(self.phases) != len(set(self.phases)):
            raise ValueError("methodology phases must be unique")
        phase_keys = set(self.phases)

        stage_keys = [stage.stage_key for stage in self.stages]
        if len(stage_keys) != len(set(stage_keys)):
            raise ValueError("methodology stage keys must be unique")
        stage_key_set = set(stage_keys)

        source_numbers = [stage.source_number for stage in self.stages]
        if len(source_numbers) != len(set(source_numbers)):
            raise ValueError("methodology stage source numbers must be unique")
        ordered_numbers = sorted(
            source_numbers,
            key=lambda value: tuple(int(part) for part in value.split(".")),
        )
        if source_numbers != ordered_numbers:
            raise ValueError("methodology stages must be ordered by source number")

        scope_keys = [scope.scope_key for scope in self.scopes]
        if len(scope_keys) != len(set(scope_keys)):
            raise ValueError("methodology scope keys must be unique")
        scope_key_set = set(scope_keys)

        stage_source_paths: set[str] = set()
        stage_positions = {stage_key: index for index, stage_key in enumerate(stage_keys)}
        for stage in self.stages:
            if stage.phase not in phase_keys:
                raise ValueError(f"stage {stage.stage_key} references an unknown phase")
            if len(stage.requires_stage) != len(set(stage.requires_stage)):
                raise ValueError(f"stage {stage.stage_key} has duplicate dependencies")
            if len(stage.scopes) != len(set(stage.scopes)):
                raise ValueError(f"stage {stage.stage_key} has duplicate scopes")
            unknown_dependencies = set(stage.requires_stage) - stage_key_set
            if unknown_dependencies:
                raise ValueError(
                    f"stage {stage.stage_key} references unknown dependencies"
                )
            if stage.stage_key in stage.requires_stage:
                raise ValueError(f"stage {stage.stage_key} cannot depend on itself")
            for dependency in stage.requires_stage:
                if stage_positions[dependency] >= stage_positions[stage.stage_key]:
                    raise ValueError(
                        f"stage {stage.stage_key} dependency {dependency} is not upstream"
                    )
            if set(stage.scopes) - scope_key_set:
                raise ValueError(f"stage {stage.stage_key} references an unknown scope")
            source_artifact = artifact_by_path.get(stage.source_path)
            if source_artifact is None or source_artifact.role != "stage":
                raise ValueError(
                    f"stage {stage.stage_key} lacks a pinned stage source artifact"
                )
            stage_source_paths.add(stage.source_path)

        scope_source_paths: set[str] = set()
        for scope in self.scopes:
            source_artifact = artifact_by_path.get(scope.source_path)
            if source_artifact is None or source_artifact.role != "scope":
                raise ValueError(
                    f"scope {scope.scope_key} lacks a pinned scope source artifact"
                )
            scope_source_paths.add(scope.source_path)

        if stage_source_paths != role_paths.get("stage", set()):
            raise ValueError("stage source manifest and graph nodes do not match")
        if scope_source_paths != role_paths.get("scope", set()):
            raise ValueError("scope source manifest and scope profiles do not match")

        if len(self.unresolved_execution_requirements) != len(
            set(self.unresolved_execution_requirements)
        ):
            raise ValueError("unresolved execution requirements must be unique")
        return self
