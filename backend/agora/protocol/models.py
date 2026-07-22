"""Machine-verifiable Agora protocol and domain contracts."""
from __future__ import annotations

import hashlib
import math
import ntpath
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from .hashing import (
    _HASH_VALIDATION_BYPASS,
    canonical_json_bytes,
    canonical_sha256,
    native_snapshot_id,
)
from .paths import canonical_repository_path

MAX_EVIDENCE_DETAILS_BYTES = 64 * 1024
MAX_EVIDENCE_DETAILS_DEPTH = 12
MAX_EVIDENCE_DETAILS_NODES = 2_000
MAX_CONTEXT_PACK_BYTES = 4 * 1024 * 1024
MAX_HANDOFF_PACK_BYTES = 8 * 1024 * 1024
MAX_NATIVE_SNAPSHOT_BYTES = 16 * 1024 * 1024


def _lower(value: Any) -> str:
    return str(value).lower()


StableId = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"),
]
ProtocolVersion = Annotated[str, Field(pattern=r"^1\.[0-9]+$")]
Sha256Hex = Annotated[
    str,
    BeforeValidator(_lower),
    Field(pattern=r"^[0-9a-f]{64}$"),
]
GitCommit = Annotated[
    str,
    BeforeValidator(_lower),
    Field(pattern=r"^[0-9a-f]{7,64}$"),
]
NonBlank = Annotated[str, Field(min_length=1, max_length=20_000)]


class ProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HashSealedModel(ProtocolModel):
    content_sha256: Sha256Hex

    @model_validator(mode="after")
    def verify_content_hash(self, info: ValidationInfo):
        if (
            info.context
            and info.context.get("hash_validation_token") is _HASH_VALIDATION_BYPASS
        ):
            return self
        expected = canonical_sha256(
            self,
            exclude_top_level=frozenset({"content_sha256"}),
        )
        if self.content_sha256 != expected:
            raise ValueError("content_sha256 does not match canonical protocol content")
        return self


class ProviderUsageObservation(HashSealedModel):
    """Read-only, Run-bound usage facts observed at a native CLI boundary."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: StableId
    adapter: StableId
    provider: Literal["openai", "anthropic", "kiro", "unknown"]
    source: Literal[
        "codex_exec_jsonl",
        "claude_print_json",
        "kiro_cli_text",
        "custom_text",
        "process_not_started",
        "runtime_boundary",
    ]
    source_payload_sha256: Sha256Hex | None = None
    model: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cache_read_input_tokens: int | None = Field(default=None, ge=0)
    cache_creation_input_tokens: int | None = Field(default=None, ge=0)
    reasoning_output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    token_measurement: Literal["exact", "estimated", "unavailable"]
    token_method: Literal[
        "provider_input_plus_output",
        "provider_input_output_and_cache",
        "utf8_bytes_divided_by_four_ceil",
        "process_not_started",
        "unavailable",
    ]
    cost_usd: float | None = Field(default=None, ge=0)
    cost_measurement: Literal["exact", "estimated", "unavailable"]
    cost_method: Literal[
        "provider_reported_total_cost_usd",
        "provider_rate_card_estimate",
        "process_not_started",
        "unavailable",
    ]
    native_credits: float | None = Field(default=None, ge=0)
    native_credit_measurement: Literal[
        "exact", "estimated", "unavailable"
    ] = "unavailable"
    native_credit_method: Literal[
        "provider_reported_native_credits",
        "provider_rate_card_estimate",
        "process_not_started",
        "unavailable",
    ] = "unavailable"
    duration_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_measurements(self):
        expected_provider = {
            "codex": "openai",
            "claude": "anthropic",
            "kiro": "kiro",
        }.get(self.adapter)
        if expected_provider is not None and self.provider != expected_provider:
            raise ValueError("provider does not match adapter provenance")

        token_parts = (
            self.input_tokens,
            self.output_tokens,
            self.cache_read_input_tokens,
            self.cache_creation_input_tokens,
            self.reasoning_output_tokens,
        )
        if self.token_measurement == "unavailable":
            if self.total_tokens is not None or any(item is not None for item in token_parts):
                raise ValueError("unavailable Token measurement may not carry values")
            if self.token_method != "unavailable":
                raise ValueError("unavailable Token measurement requires unavailable method")
        elif self.total_tokens is None:
            raise ValueError("measured Token usage requires a total")

        if self.token_method == "provider_input_plus_output":
            if self.token_measurement != "exact":
                raise ValueError("provider Token totals must be exact")
            if self.input_tokens is None or self.output_tokens is None:
                raise ValueError("provider Token total requires input and output values")
            if self.total_tokens != self.input_tokens + self.output_tokens:
                raise ValueError("provider Token total does not match input plus output")
        elif self.token_method == "provider_input_output_and_cache":
            required = (
                self.input_tokens,
                self.output_tokens,
                self.cache_read_input_tokens,
                self.cache_creation_input_tokens,
            )
            if self.token_measurement != "exact" or any(item is None for item in required):
                raise ValueError("provider cache Token total requires exact component values")
            if self.total_tokens != sum(item for item in required if item is not None):
                raise ValueError("provider Token total does not match its components")
        elif self.token_method == "utf8_bytes_divided_by_four_ceil":
            if self.token_measurement != "estimated" or self.total_tokens is None:
                raise ValueError("Agora Token estimate requires an estimated total")
            if any(item is not None for item in token_parts):
                raise ValueError("Agora Token estimate may not invent provider components")
        elif self.token_method == "process_not_started":
            if self.token_measurement != "exact" or self.total_tokens != 0:
                raise ValueError("a process that did not start has exact zero total Tokens")
            if any(item not in {None, 0} for item in token_parts):
                raise ValueError("a process that did not start may only carry zero components")
        elif self.token_method == "unavailable" and self.token_measurement != "unavailable":
            raise ValueError("measured Token usage requires a measurement method")

        if self.cost_measurement == "unavailable":
            if self.cost_usd is not None or self.cost_method != "unavailable":
                raise ValueError("unavailable cost measurement may not carry a value")
        else:
            if self.cost_usd is None or not math.isfinite(self.cost_usd):
                raise ValueError("measured cost must be finite")
        if self.cost_method == "provider_reported_total_cost_usd":
            if self.cost_measurement != "exact":
                raise ValueError("provider-reported cost must be exact")
        elif self.cost_method == "provider_rate_card_estimate":
            if self.cost_measurement != "estimated":
                raise ValueError("rate-card cost must be estimated")
        elif self.cost_method == "process_not_started":
            if self.cost_measurement != "exact" or self.cost_usd != 0:
                raise ValueError("a process that did not start has exact zero cost")
        elif self.cost_measurement != "unavailable":
            raise ValueError("measured cost requires a measurement method")

        if self.native_credit_measurement == "unavailable":
            if self.native_credits is not None or self.native_credit_method != "unavailable":
                raise ValueError("unavailable native credits may not carry a value")
        else:
            if (
                self.native_credits is None
                or not math.isfinite(self.native_credits)
            ):
                raise ValueError("measured native credits must be finite")
        if self.native_credit_method == "provider_reported_native_credits":
            if self.native_credit_measurement != "exact":
                raise ValueError("provider-reported native credits must be exact")
        elif self.native_credit_method == "provider_rate_card_estimate":
            if self.native_credit_measurement != "estimated":
                raise ValueError("rate-card native credits must be estimated")
        elif self.native_credit_method == "process_not_started":
            if self.native_credit_measurement != "exact" or self.native_credits != 0:
                raise ValueError("a process that did not start has exact zero credits")
        elif self.native_credit_measurement != "unavailable":
            raise ValueError("measured native credits require a measurement method")

        native_source = self.source in {"codex_exec_jsonl", "claude_print_json"}
        if native_source != (self.source_payload_sha256 is not None):
            raise ValueError("structured native sources require a source payload hash")
        if self.source == "codex_exec_jsonl" and self.adapter != "codex":
            raise ValueError("Codex JSONL source requires the codex adapter")
        if self.source == "claude_print_json" and self.adapter != "claude":
            raise ValueError("Claude JSON source requires the claude adapter")
        return self


class StageInventoryContractBinding(ProtocolModel):
    contract_id: StableId
    schema_version: ProtocolVersion
    sha256: Sha256Hex


class StageInventoryItem(ProtocolModel):
    stage_key: StableId
    gate_key: StableId
    sequence: int = Field(ge=1, le=200)
    title: Annotated[str, Field(min_length=1, max_length=300)]
    role: Annotated[str, Field(min_length=1, max_length=128)]
    runtime: StableId


class StageInventoryGroup(ProtocolModel):
    group_key: StableId
    sequence: int = Field(ge=1, le=20)
    title: Annotated[str, Field(min_length=1, max_length=300)]
    stages: list[StageInventoryItem] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_stage_order_and_identity(self):
        sequences = [item.sequence for item in self.stages]
        if sequences != list(range(1, len(self.stages) + 1)):
            raise ValueError("Stage inventory sequences must be contiguous and ordered")
        stage_keys = [item.stage_key for item in self.stages]
        gate_keys = [item.gate_key for item in self.stages]
        if len(stage_keys) != len(set(stage_keys)):
            raise ValueError("Stage inventory stage keys must be unique within a group")
        if len(gate_keys) != len(set(gate_keys)):
            raise ValueError("Stage inventory gate keys must be unique within a group")
        return self


class StageInventory(HashSealedModel):
    schema_version: Literal["1.0"] = "1.0"
    inventory_id: StableId
    task_id: StableId
    project_id: StableId
    plan_id: StableId
    methodology_id: StableId
    methodology_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")]
    methodology_sha256: Sha256Hex
    provisional: bool
    contract: StageInventoryContractBinding | None = None
    groups: list[StageInventoryGroup] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_complete_grouped_inventory(self):
        sequences = [item.sequence for item in self.groups]
        if sequences != list(range(1, len(self.groups) + 1)):
            raise ValueError("Stage group sequences must be contiguous and ordered")
        group_keys = [item.group_key for item in self.groups]
        if len(group_keys) != len(set(group_keys)):
            raise ValueError("Stage inventory group keys must be unique")
        stages = [stage for group in self.groups for stage in group.stages]
        if len(stages) > 200:
            raise ValueError("Stage inventory may contain at most 200 Stages")
        stage_keys = [item.stage_key for item in stages]
        gate_keys = [item.gate_key for item in stages]
        if len(stage_keys) != len(set(stage_keys)):
            raise ValueError("Stage inventory stage keys must be unique")
        if len(gate_keys) != len(set(gate_keys)):
            raise ValueError("Stage inventory gate keys must be unique")
        return self


class RuntimeName(str, Enum):
    AGORA = "agora"
    CODEX = "codex"
    CLAUDE = "claude"
    KIRO = "kiro"


class ArtifactStorage(str, Enum):
    MANAGED = "managed"
    REFERENCED = "referenced"


class EvidenceStatus(str, Enum):
    PASSED = "passed"
    FAILED_PRODUCT = "failed_product"
    FAILED_EXTERNAL = "failed_external"
    MISSING = "missing"
    STALE = "stale"


class ApprovalStatus(str, Enum):
    ACTIVE = "active"
    STALE = "stale"
    REVOKED = "revoked"


class GateDecision(str, Enum):
    PASS = "pass"
    BLOCK = "block"


class RequirementSeverity(str, Enum):
    BLOCKER = "blocker"
    WARNING = "warning"


class StageResult(str, Enum):
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProcessStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    EXITED = "exited"
    LAUNCH_FAILED = "launch_failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class TransportStatus(str, Enum):
    PENDING = "pending"
    CONNECTED = "connected"
    COMPLETED = "completed"
    FAILED = "failed"


class SchemaStatus(str, Enum):
    PENDING = "pending"
    VALID = "valid"
    REPAIRED = "repaired"
    PROTOCOL_FAILED = "protocol_failed"


class SemanticStageResult(str, Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReconciliationStatus(str, Enum):
    VERIFIED = "verified"
    CONFLICTED = "conflicted"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class NativeConflictCode(str, Enum):
    STATE_STALE = "state_stale"
    AUDIT_STALE = "audit_stale"
    INTERNAL_CONTRADICTION = "internal_contradiction"
    REQUIRED_EVIDENCE_MISSING = "required_evidence_missing"
    APPROVAL_MISSING_OR_STALE = "approval_missing_or_stale"
    BRANCH_DIVERGENCE = "branch_divergence"
    POLICY_REASSESSMENT_REQUIRED = "policy_reassessment_required"
    LOCATION_STALE = "location_stale"


class ProducerRef(ProtocolModel):
    runtime: RuntimeName
    run_id: StableId
    stage_key: StableId


class ArtifactLocation(ProtocolModel):
    repository_id: StableId
    ref: NonBlank
    commit_sha: GitCommit
    path: Annotated[str, Field(min_length=1, max_length=4000)]

    @field_validator("path")
    @classmethod
    def canonical_relative_path(cls, value: str) -> str:
        return canonical_repository_path(value)


class ArtifactVersionRef(ProtocolModel):
    artifact_id: StableId
    version: int = Field(ge=1)
    sha256: Sha256Hex
    kind: StableId
    location: ArtifactLocation | None = None


class Artifact(ProtocolModel):
    schema_version: ProtocolVersion = "1.0"
    artifact_id: StableId
    project_id: StableId
    task_id: StableId
    stage_key: StableId
    producer: ProducerRef
    kind: StableId
    storage: ArtifactStorage
    version: int = Field(ge=1)
    sha256: Sha256Hex
    media_type: Annotated[str, Field(min_length=1, max_length=255)] = "text/plain"
    content: str | None = Field(default=None, max_length=2_000_000)
    location: ArtifactLocation | None = None
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_storage_contract(self):
        if self.storage == ArtifactStorage.MANAGED:
            if self.content is None:
                raise ValueError("managed artifacts require content")
            if self.location is not None:
                raise ValueError("managed artifacts must not define a repository location")
            actual = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
            if self.sha256 != actual:
                raise ValueError("managed artifact sha256 does not match UTF-8 content")
        else:
            if self.location is None:
                raise ValueError("referenced artifacts require a repository location")
            if self.content is not None:
                raise ValueError("referenced artifacts must not embed content")
        return self

    def version_ref(self) -> ArtifactVersionRef:
        return ArtifactVersionRef(
            artifact_id=self.artifact_id,
            version=self.version,
            sha256=self.sha256,
            kind=self.kind,
            location=self.location,
        )


class Evidence(ProtocolModel):
    schema_version: ProtocolVersion = "1.0"
    evidence_id: StableId
    project_id: StableId
    task_id: StableId
    stage_key: StableId
    producer: ProducerRef
    repository_id: StableId
    ref: NonBlank
    commit_sha: GitCommit
    requirement_id: StableId
    kind: StableId
    status: EvidenceStatus
    artifact_versions: list[ArtifactVersionRef] = Field(default_factory=list, max_length=100)
    summary: Annotated[str, Field(min_length=1, max_length=10_000)]
    observed_at: AwareDatetime
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("details")
    @classmethod
    def bounded_json_details(cls, value: dict[str, Any]) -> dict[str, Any]:
        nodes = 0

        def visit(item: Any, depth: int) -> None:
            nonlocal nodes
            nodes += 1
            if nodes > MAX_EVIDENCE_DETAILS_NODES:
                raise ValueError("evidence details exceed the node limit")
            if depth > MAX_EVIDENCE_DETAILS_DEPTH:
                raise ValueError("evidence details exceed the nesting limit")
            if isinstance(item, dict):
                for key, child in item.items():
                    if not isinstance(key, str):
                        raise ValueError("evidence detail keys must be strings")
                    visit(child, depth + 1)
            elif isinstance(item, list):
                for child in item:
                    visit(child, depth + 1)
            elif item is not None and not isinstance(item, (str, int, float, bool)):
                raise ValueError("evidence details must contain JSON values")

        visit(value, 0)
        try:
            encoded = canonical_json_bytes(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("evidence details must be canonical JSON") from exc
        if len(encoded) > MAX_EVIDENCE_DETAILS_BYTES:
            raise ValueError("evidence details exceed 64 KiB")
        return value


class ApprovalArtifactBinding(ProtocolModel):
    repository_id: StableId
    ref: NonBlank
    commit_sha: GitCommit
    path: Annotated[str, Field(min_length=1, max_length=4000)]
    sha256: Sha256Hex

    @field_validator("path")
    @classmethod
    def canonical_relative_path(cls, value: str) -> str:
        return canonical_repository_path(value)


class Approval(ProtocolModel):
    schema_version: ProtocolVersion = "1.0"
    approval_id: StableId
    project_id: StableId
    task_id: StableId
    stage_key: StableId
    gate_key: StableId
    repository_id: StableId
    ref: NonBlank
    commit_sha: GitCommit
    artifact_versions: list[ApprovalArtifactBinding] = Field(min_length=1, max_length=100)
    status: ApprovalStatus = ApprovalStatus.ACTIVE
    approved_by: Annotated[str, Field(min_length=1, max_length=256)]
    approved_at: AwareDatetime
    stale_reason: Annotated[str, Field(min_length=1, max_length=4000)] | None = None

    @model_validator(mode="after")
    def validate_binding_and_status(self):
        for artifact in self.artifact_versions:
            if (
                artifact.repository_id != self.repository_id
                or artifact.ref != self.ref
                or artifact.commit_sha != self.commit_sha
            ):
                raise ValueError(
                    "approval artifacts must match the approval repository, ref, and commit"
                )
        if self.status == ApprovalStatus.STALE and not self.stale_reason:
            raise ValueError("stale approvals require stale_reason")
        if self.status == ApprovalStatus.ACTIVE and self.stale_reason:
            raise ValueError("active approvals must not define stale_reason")
        return self


class StageContract(ProtocolModel):
    contract_id: StableId
    title: Annotated[str, Field(min_length=1, max_length=300)]
    objective: NonBlank
    completion_conditions: list[NonBlank] = Field(min_length=1, max_length=100)


class RequiredOutput(ProtocolModel):
    output_id: StableId
    kind: StableId
    schema_uri: Annotated[str, Field(min_length=1, max_length=2000)] | None = None
    required: bool = True


class ContextEntry(ProtocolModel):
    entry_id: StableId
    version: int = Field(ge=1)
    sha256: Sha256Hex
    title: Annotated[str, Field(min_length=1, max_length=300)]
    content: NonBlank
    source_ref: Annotated[str, Field(min_length=1, max_length=2000)]


class RunBudget(ProtocolModel):
    max_seconds: int = Field(ge=1, le=86_400)
    max_output_bytes: int = Field(default=1_000_000, ge=1024, le=50_000_000)
    max_model_tokens: int | None = Field(default=None, ge=1)
    max_cost_usd: float | None = Field(default=None, ge=0)


class ContextPack(HashSealedModel):
    schema_version: ProtocolVersion = "1.0"
    pack_id: StableId
    project_id: StableId
    task_id: StableId
    stage_key: StableId
    run_id: StableId
    generated_at: AwareDatetime
    stage_contract: StageContract
    input_artifacts: list[ArtifactVersionRef] = Field(default_factory=list, max_length=200)
    required_outputs: list[RequiredOutput] = Field(default_factory=list, max_length=100)
    forbidden_constraints: list[NonBlank] = Field(default_factory=list, max_length=100)
    policies: list[ContextEntry] = Field(default_factory=list, max_length=100)
    task_memory: list[ContextEntry] = Field(default_factory=list, max_length=100)
    project_knowledge: list[ContextEntry] = Field(default_factory=list, max_length=100)
    user_preferences: list[ContextEntry] = Field(default_factory=list, max_length=50)
    budget: RunBudget

    @model_validator(mode="after")
    def unique_context_identifiers(self):
        artifact_ids = [item.artifact_id for item in self.input_artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("input artifact ids must be unique")
        output_ids = [item.output_id for item in self.required_outputs]
        if len(output_ids) != len(set(output_ids)):
            raise ValueError("required output ids must be unique")
        entry_ids = [
            item.entry_id
            for group in (
                self.policies,
                self.task_memory,
                self.project_knowledge,
                self.user_preferences,
            )
            for item in group
        ]
        if len(entry_ids) != len(set(entry_ids)):
            raise ValueError("context entry ids must be unique across the pack")
        if len(canonical_json_bytes(self)) > MAX_CONTEXT_PACK_BYTES:
            raise ValueError("context pack exceeds 4 MiB")
        return self


class UnresolvedQuestion(ProtocolModel):
    question_id: StableId
    question: NonBlank
    blocking: bool = True


class MemoryCandidate(ProtocolModel):
    candidate_id: StableId
    kind: StableId
    title: Annotated[str, Field(min_length=1, max_length=300)]
    content: NonBlank
    source_refs: list[StableId] = Field(min_length=1, max_length=100)


class NativeConflict(ProtocolModel):
    code: NativeConflictCode
    severity: RequirementSeverity
    detail: NonBlank
    source_refs: list[NonBlank] = Field(default_factory=list, max_length=100)

    @field_validator("source_refs", mode="before")
    @classmethod
    def canonical_source_refs(cls, value):
        values = list(value or [])
        if len(values) != len(set(values)):
            raise ValueError("native conflict source refs must be unique")
        return sorted(values)


class GateRecommendation(ProtocolModel):
    decision: GateDecision
    reasons: list[NativeConflictCode] = Field(default_factory=list, max_length=100)

    @field_validator("reasons", mode="before")
    @classmethod
    def canonical_reasons(cls, value):
        values = list(value or [])
        normalized = [NativeConflictCode(item) for item in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("gate recommendation reasons must be unique")
        return sorted(normalized, key=lambda item: item.value)

    @model_validator(mode="after")
    def blocked_recommendations_need_reasons(self):
        if self.decision == GateDecision.BLOCK and not self.reasons:
            raise ValueError("blocked gate recommendations require reasons")
        if self.decision == GateDecision.PASS and self.reasons:
            raise ValueError("passing gate recommendations must not contain reasons")
        return self


class NativeStateSnapshot(HashSealedModel):
    schema_version: ProtocolVersion = "1.0"
    snapshot_id: StableId
    project_id: StableId
    repository_id: StableId
    canonical_ref: NonBlank
    commit_sha: GitCommit
    native_state_sha256: Sha256Hex
    reconciliation_rule_version: Annotated[
        str,
        Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"),
    ]
    methodology: StableId
    declared_native_stage: NonBlank
    verified_native_stage: NonBlank
    reconciliation_status: ReconciliationStatus
    artifacts: list[ArtifactVersionRef] = Field(default_factory=list, max_length=1000)
    approval_ids: list[StableId] = Field(default_factory=list, max_length=1000)
    conflicts: list[NativeConflict] = Field(default_factory=list, max_length=1000)
    gate_recommendation: GateRecommendation

    @field_validator("artifacts", mode="before")
    @classmethod
    def canonical_artifacts(cls, value):
        values = list(value or [])
        keys = [
            (
                item.get("artifact_id") if isinstance(item, dict) else item.artifact_id,
                item.get("version") if isinstance(item, dict) else item.version,
                item.get("sha256") if isinstance(item, dict) else item.sha256,
            )
            for item in values
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("native snapshot artifacts must be unique")
        return [
            item
            for _, item in sorted(
                zip(keys, values, strict=True),
                key=lambda pair: pair[0],
            )
        ]

    @field_validator("approval_ids", mode="before")
    @classmethod
    def canonical_approval_ids(cls, value):
        values = list(value or [])
        if len(values) != len(set(values)):
            raise ValueError("native snapshot approval ids must be unique")
        return sorted(values)

    @field_validator("conflicts", mode="before")
    @classmethod
    def canonical_conflicts(cls, value):
        values = list(value or [])

        def key(item):
            if isinstance(item, dict):
                code = str(item["code"])
                severity = str(item["severity"])
                detail = str(item["detail"])
            else:
                code = item.code.value
                severity = item.severity.value
                detail = item.detail
            return code, severity, detail

        keys = [key(item) for item in values]
        if len(keys) != len(set(keys)):
            raise ValueError("native snapshot conflicts must be unique")
        return [
            item
            for _, item in sorted(
                zip(keys, values, strict=True),
                key=lambda pair: pair[0],
            )
        ]

    @model_validator(mode="after")
    def validate_snapshot_identity_and_conflicts(self):
        identity = {
            "project_id": self.project_id,
            "repository_id": self.repository_id,
            "canonical_ref": self.canonical_ref,
            "commit_sha": self.commit_sha,
            "native_state_sha256": self.native_state_sha256,
            "reconciliation_rule_version": self.reconciliation_rule_version,
            "methodology": self.methodology,
        }
        if self.snapshot_id != native_snapshot_id(identity):
            raise ValueError("snapshot_id does not match the deterministic snapshot identity")
        blocker_codes = {
            conflict.code
            for conflict in self.conflicts
            if conflict.severity == RequirementSeverity.BLOCKER
        }
        if self.gate_recommendation.decision == GateDecision.BLOCK:
            if set(self.gate_recommendation.reasons) != blocker_codes:
                raise ValueError("gate recommendation reasons must equal blocker conflict codes")
        elif blocker_codes:
            raise ValueError("a snapshot with blocker conflicts cannot recommend pass")
        if (
            self.reconciliation_status == ReconciliationStatus.VERIFIED
            and self.gate_recommendation.decision != GateDecision.PASS
        ):
            raise ValueError("verified snapshots must recommend gate pass")
        if len(canonical_json_bytes(self)) > MAX_NATIVE_SNAPSHOT_BYTES:
            raise ValueError("native state snapshot exceeds 16 MiB")
        return self


class HandoffPack(HashSealedModel):
    schema_version: ProtocolVersion = "1.0"
    pack_id: StableId
    project_id: StableId
    task_id: StableId
    stage_key: StableId
    run_id: StableId
    producer: ProducerRef
    input_artifacts: list[ArtifactVersionRef] = Field(default_factory=list, max_length=200)
    required_outputs: list[RequiredOutput] = Field(default_factory=list, max_length=100)
    forbidden_constraints: list[NonBlank] = Field(default_factory=list, max_length=100)
    stage_result: StageResult
    output_artifacts: list[Artifact] = Field(default_factory=list, max_length=200)
    evidence: list[Evidence] = Field(default_factory=list, max_length=500)
    unresolved_questions: list[UnresolvedQuestion] = Field(default_factory=list, max_length=100)
    native_state_snapshot: NativeStateSnapshot | None = None
    memory_candidates: list[MemoryCandidate] = Field(default_factory=list, max_length=100)
    blocker_requirement_ids: list[StableId] = Field(default_factory=list, max_length=100)
    suggested_next_action: NonBlank | None = None

    @model_validator(mode="after")
    def validate_handoff_semantics(self):
        if self.producer.run_id != self.run_id or self.producer.stage_key != self.stage_key:
            raise ValueError("handoff producer must match the handoff run and stage")
        output_ids = [artifact.artifact_id for artifact in self.output_artifacts]
        if len(output_ids) != len(set(output_ids)):
            raise ValueError("output artifact ids must be unique")
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence ids must be unique")
        has_blocking_question = any(item.blocking for item in self.unresolved_questions)
        if self.stage_result == StageResult.SUCCEEDED and (
            has_blocking_question or self.blocker_requirement_ids
        ):
            raise ValueError("succeeded handoffs cannot contain blockers")
        if self.stage_result == StageResult.BLOCKED and not (
            has_blocking_question
            or self.blocker_requirement_ids
            or any(item.status != EvidenceStatus.PASSED for item in self.evidence)
        ):
            raise ValueError("blocked handoffs must identify a blocker")
        if len(canonical_json_bytes(self)) > MAX_HANDOFF_PACK_BYTES:
            raise ValueError("handoff pack exceeds 8 MiB")
        return self


class RunProtocolState(ProtocolModel):
    schema_version: ProtocolVersion = "1.0"
    run_id: StableId
    process_status: ProcessStatus
    transport_status: TransportStatus
    schema_status: SchemaStatus
    semantic_stage_result: SemanticStageResult
    process_exit_code: int | None = None
    repair_attempts: int = Field(default=0, ge=0, le=1)

    @model_validator(mode="after")
    def validate_protocol_dimensions(self):
        if self.process_status == ProcessStatus.EXITED and self.process_exit_code is None:
            raise ValueError("exited processes require process_exit_code")
        if self.process_status != ProcessStatus.EXITED and self.process_exit_code is not None:
            raise ValueError("only exited processes may define process_exit_code")
        if self.schema_status == SchemaStatus.REPAIRED and self.repair_attempts != 1:
            raise ValueError("repaired schemas require exactly one repair attempt")
        if self.schema_status == SchemaStatus.PROTOCOL_FAILED and (
            self.semantic_stage_result != SemanticStageResult.BLOCKED
        ):
            raise ValueError("protocol failure must block semantic stage completion")
        if self.semantic_stage_result == SemanticStageResult.SUCCEEDED:
            if self.process_status != ProcessStatus.EXITED:
                raise ValueError("semantic success requires an exited process")
            if self.transport_status != TransportStatus.COMPLETED:
                raise ValueError("semantic success requires completed transport")
            if self.schema_status not in {SchemaStatus.VALID, SchemaStatus.REPAIRED}:
                raise ValueError("semantic success requires a valid protocol schema")
        if (
            self.semantic_stage_result == SemanticStageResult.CANCELLED
            and self.process_status != ProcessStatus.CANCELLED
        ):
            raise ValueError("semantic cancellation requires a cancelled process")
        return self


class GateRequirement(ProtocolModel):
    schema_version: ProtocolVersion = "1.0"
    requirement_id: StableId
    title: Annotated[str, Field(min_length=1, max_length=300)]
    repository_id: StableId
    ref: NonBlank
    commit_sha: GitCommit
    evidence_kind: StableId
    severity: RequirementSeverity = RequirementSeverity.BLOCKER
    priority: int = Field(default=100, ge=0, le=10_000)
    failure_action: NonBlank


class RequirementEvaluation(ProtocolModel):
    requirement_id: StableId
    status: EvidenceStatus
    evidence_ids: list[StableId] = Field(default_factory=list)
    satisfied: bool


class GateEvaluation(ProtocolModel):
    decision: GateDecision
    requirements: list[RequirementEvaluation]
    blocker_requirement_ids: list[StableId]
    warning_requirement_ids: list[StableId]
    next_safe_action: str | None


class RunnerIsolationContract(ProtocolModel):
    schema_version: ProtocolVersion = "1.0"
    platform: Literal["windows"] = "windows"
    run_id: StableId
    run_root: Annotated[str, Field(min_length=3, max_length=4000)]
    workspace: Annotated[str, Field(min_length=3, max_length=4000)]
    allowed_workspace_roots: list[Annotated[str, Field(min_length=3, max_length=4000)]] = Field(
        min_length=1,
        max_length=50,
    )
    home_dir: Annotated[str, Field(min_length=3, max_length=4000)]
    temp_dir: Annotated[str, Field(min_length=3, max_length=4000)]
    cache_dir: Annotated[str, Field(min_length=3, max_length=4000)]
    config_dir: Annotated[str, Field(min_length=3, max_length=4000)]
    credential_refs: list[
        Annotated[
            str,
            Field(
                min_length=14,
                max_length=1000,
                pattern=r"^credential://[A-Za-z0-9][A-Za-z0-9_./:-]*$",
            ),
        ]
    ] = Field(default_factory=list, max_length=50)
    serialized_global_operations: list[NonBlank] = Field(default_factory=list, max_length=50)
    recovery_marker: Annotated[str, Field(min_length=3, max_length=4000)]

    @field_validator("recovery_marker")
    @classmethod
    def marker_is_file_path(cls, value: str) -> str:
        if not value.lower().endswith((".json", ".marker")):
            raise ValueError("recovery_marker must end with .json or .marker")
        return value

    @field_validator("credential_refs")
    @classmethod
    def credential_refs_are_opaque_and_canonical(cls, values: list[str]) -> list[str]:
        for value in values:
            path = value.removeprefix("credential://")
            if any(part in {"", ".", ".."} for part in path.split("/")):
                raise ValueError("credential references must not contain path traversal")
        return values

    @staticmethod
    def _canonical(path: str) -> str:
        normalized = ntpath.normcase(ntpath.normpath(path))
        if not ntpath.isabs(normalized) or not ntpath.splitdrive(normalized)[0]:
            raise ValueError("runner paths must be absolute Windows paths")
        return normalized

    @classmethod
    def _is_within(cls, child: str, parent: str) -> bool:
        try:
            return ntpath.commonpath([cls._canonical(child), cls._canonical(parent)]) == cls._canonical(
                parent
            )
        except ValueError:
            return False

    @model_validator(mode="after")
    def validate_isolation_boundaries(self):
        run_root = self._canonical(self.run_root)
        writable = {
            self._canonical(self.home_dir),
            self._canonical(self.temp_dir),
            self._canonical(self.cache_dir),
            self._canonical(self.config_dir),
            self._canonical(self.recovery_marker),
        }
        if len(writable) != 5:
            raise ValueError("runner writable directories and recovery marker must be distinct")
        if any(not self._is_within(path, run_root) for path in writable):
            raise ValueError("runner writable paths must stay within run_root")
        if not any(
            self._is_within(self.workspace, root) for root in self.allowed_workspace_roots
        ):
            raise ValueError("workspace must stay within an allowed workspace root")
        return self
