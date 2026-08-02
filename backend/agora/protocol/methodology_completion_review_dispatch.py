"""One-shot dispatch for an authenticated final methodology reviewer Run."""
from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, model_validator

from .methodology_completion_review_claim import (
    MethodologyCompletionReviewResponsibility,
    methodology_completion_review_run_id,
)
from .methodology_migration import MigrationRepositoryBinding
from .models import (
    HashSealedModel,
    PinnedRuntimePreflightDecision,
    ProcessStatus,
    ProtocolModel,
    ProviderUsageObservation,
    RunProtocolState,
    SchemaStatus,
    SemanticStageResult,
    Sha256Hex,
    StableId,
)
from .state_machines import GateStatus, StageStatus, TaskStatus


def methodology_completion_review_evidence_id(review_run_id: str) -> str:
    """Derive the only Evidence identity eligible for one reviewer Run."""

    digest = hashlib.sha256(review_run_id.encode("utf-8")).hexdigest()
    return f"methodology-completion-review-evidence:{digest[:32]}"


class MethodologyCompletionReviewDispatchPolicyCheck(ProtocolModel):
    check: Literal[
        "claimed_review_run",
        "completion_context",
        "current_final_stage",
        "repository_binding",
        "reviewer_independence",
        "runtime_binding",
        "usage_reservation",
    ]
    satisfied: bool
    detail: Annotated[str, Field(min_length=1, max_length=1000)]


class MethodologyCompletionReviewDispatchPolicyDecision(HashSealedModel):
    """Explain why one exact claimed reviewer Run may or may not spawn."""

    schema_version: Literal["1.0"] = "1.0"
    decision_id: StableId
    evaluated_at: AwareDatetime
    policy_id: StableId
    policy_version: Literal["1.0"] = "1.0"
    policy_sha256: Sha256Hex
    project_id: StableId
    task_id: StableId
    plan_id: StableId
    inventory_id: StableId
    inventory_sha256: Sha256Hex
    execution_contract_id: StableId
    execution_contract_sha256: Sha256Hex
    completion_review_claim_receipt_id: StableId
    completion_review_claim_receipt_sha256: Sha256Hex
    final_dispatch_receipt_id: StableId
    final_dispatch_receipt_sha256: Sha256Hex
    repository: MigrationRepositoryBinding
    stage_sequence: int = Field(ge=1, le=200)
    stage_key: StableId
    gate_key: StableId
    responsibility: MethodologyCompletionReviewResponsibility
    pinned_runtime: StableId
    result_format: Literal[
        "plain_text",
        "codex_jsonl_v1",
        "claude_json_v1",
        "kiro_chat_v1",
    ]
    review_run_id: StableId
    context_pack_id: StableId
    context_pack_sha256: Sha256Hex
    runtime_capabilities: list[StableId] = Field(max_length=50)
    token_reservation: int = Field(ge=1, le=10_000_000)
    cost_reservation_usd: float | None = Field(default=None, ge=0)
    checks: list[MethodologyCompletionReviewDispatchPolicyCheck] = Field(
        min_length=7,
        max_length=7,
    )
    dispatchable: bool
    blockers: list[
        Annotated[str, Field(min_length=1, max_length=1000)]
    ] = Field(default_factory=list, max_length=7)
    route_selection_authority: Literal[False] = False
    runtime_substitution_allowed: Literal[False] = False
    provider_serviceability_verified: Literal[False] = False

    @property
    def role(self) -> MethodologyCompletionReviewResponsibility:
        """Expose the pinned responsibility through the generic preflight shape."""

        return self.responsibility

    @model_validator(mode="after")
    def validate_policy_decision(self):
        expected_run_id = methodology_completion_review_run_id(
            task_id=self.task_id,
            execution_contract_sha256=self.execution_contract_sha256,
            final_dispatch_receipt_sha256=self.final_dispatch_receipt_sha256,
            responsibility=self.responsibility,
        )
        if self.review_run_id != expected_run_id:
            raise ValueError(
                "methodology completion-review policy Run identity differs"
            )
        expected_id = (
            "methodology-completion-review-dispatch-policy:"
            + hashlib.sha256(self.review_run_id.encode("utf-8")).hexdigest()[:32]
        )
        if self.decision_id != expected_id:
            raise ValueError(
                "methodology completion-review dispatch policy identity differs"
            )
        expected_checks = {
            "claimed_review_run",
            "completion_context",
            "current_final_stage",
            "repository_binding",
            "reviewer_independence",
            "runtime_binding",
            "usage_reservation",
        }
        names = [item.check for item in self.checks]
        if (
            set(names) != expected_checks
            or len(names) != len(expected_checks)
            or self.checks != sorted(self.checks, key=lambda item: item.check)
        ):
            raise ValueError(
                "methodology completion-review dispatch policy checks differ"
            )
        if self.dispatchable != all(item.satisfied for item in self.checks):
            raise ValueError(
                "methodology completion-review dispatch result differs from checks"
            )
        if self.blockers != [
            item.detail for item in self.checks if not item.satisfied
        ]:
            raise ValueError(
                "methodology completion-review dispatch blockers differ"
            )
        if self.runtime_capabilities != sorted(set(self.runtime_capabilities)):
            raise ValueError(
                "methodology completion-review runtime capabilities are not canonical"
            )
        if self.context_pack_id != f"context:{self.review_run_id}":
            raise ValueError(
                "methodology completion-review Context identity differs"
            )
        return self


class MethodologyCompletionReviewDispatchClaim(HashSealedModel):
    """Durable single-use process authority attached to one review claim."""

    schema_version: Literal["1.0"] = "1.0"
    dispatch_id: StableId
    claimed_at: AwareDatetime
    project_id: StableId
    task_id: StableId
    plan_id: StableId
    inventory_id: StableId
    inventory_sha256: Sha256Hex
    execution_contract_id: StableId
    execution_contract_sha256: Sha256Hex
    completion_review_claim_receipt_id: StableId
    completion_review_claim_receipt_sha256: Sha256Hex
    final_dispatch_receipt_id: StableId
    final_dispatch_receipt_sha256: Sha256Hex
    production_run_id: StableId
    production_handoff_pack_id: StableId
    production_handoff_pack_sha256: Sha256Hex
    repository: MigrationRepositoryBinding
    stage_sequence: int = Field(ge=1, le=200)
    stage_key: StableId
    gate_key: StableId
    responsibility: MethodologyCompletionReviewResponsibility
    runtime: StableId
    result_format: Literal[
        "plain_text",
        "codex_jsonl_v1",
        "claude_json_v1",
        "kiro_chat_v1",
    ]
    review_run_id: StableId
    context_pack_id: StableId
    context_pack_sha256: Sha256Hex
    prompt_sha256: Sha256Hex
    spawn_owner_id: StableId
    recovery_not_before: AwareDatetime
    dispatch_policy: MethodologyCompletionReviewDispatchPolicyDecision
    runtime_preflight: PinnedRuntimePreflightDecision
    unbounded_native_usage_acknowledged: Literal[True] = True
    formal_review_run_created: Literal[True] = True
    review_context_pack_created: Literal[True] = True
    compatibility_run_created: Literal[False] = False
    process_started: Literal[False] = False
    process_spawn_authority: Literal[True] = True
    route_selection_authority: Literal[False] = False
    runtime_substitution_allowed: Literal[False] = False
    provider_serviceability_verified: Literal[False] = False

    @model_validator(mode="after")
    def validate_dispatch_binding(self):
        policy = self.dispatch_policy
        preflight = self.runtime_preflight
        expected_run_id = methodology_completion_review_run_id(
            task_id=self.task_id,
            execution_contract_sha256=self.execution_contract_sha256,
            final_dispatch_receipt_sha256=self.final_dispatch_receipt_sha256,
            responsibility=self.responsibility,
        )
        if self.review_run_id != expected_run_id:
            raise ValueError(
                "methodology completion-review dispatch Run identity differs"
            )
        expected_id = (
            "methodology-completion-review-dispatch:"
            + hashlib.sha256(self.review_run_id.encode("utf-8")).hexdigest()[:32]
        )
        if self.dispatch_id != expected_id:
            raise ValueError(
                "methodology completion-review dispatch identity differs"
            )
        if self.context_pack_id != f"context:{self.review_run_id}":
            raise ValueError(
                "methodology completion-review dispatch Context identity differs"
            )
        if (
            not policy.dispatchable
            or policy.project_id != self.project_id
            or policy.task_id != self.task_id
            or policy.plan_id != self.plan_id
            or policy.inventory_id != self.inventory_id
            or policy.inventory_sha256 != self.inventory_sha256
            or policy.execution_contract_id != self.execution_contract_id
            or policy.execution_contract_sha256
            != self.execution_contract_sha256
            or policy.completion_review_claim_receipt_id
            != self.completion_review_claim_receipt_id
            or policy.completion_review_claim_receipt_sha256
            != self.completion_review_claim_receipt_sha256
            or policy.final_dispatch_receipt_id
            != self.final_dispatch_receipt_id
            or policy.final_dispatch_receipt_sha256
            != self.final_dispatch_receipt_sha256
            or policy.repository != self.repository
            or policy.stage_sequence != self.stage_sequence
            or policy.stage_key != self.stage_key
            or policy.gate_key != self.gate_key
            or policy.responsibility != self.responsibility
            or policy.pinned_runtime != self.runtime
            or policy.result_format != self.result_format
            or policy.review_run_id != self.review_run_id
            or policy.context_pack_id != self.context_pack_id
            or policy.context_pack_sha256 != self.context_pack_sha256
            or not preflight.allowed
            or preflight.project_id != self.project_id
            or preflight.task_id != self.task_id
            or preflight.run_id != self.review_run_id
            or preflight.inventory_id != self.inventory_id
            or preflight.inventory_sha256 != self.inventory_sha256
            or preflight.stage_key != self.stage_key
            or preflight.role != self.responsibility
            or preflight.pinned_runtime != self.runtime
            or preflight.routing_policy_decision_id != policy.decision_id
            or preflight.routing_policy_decision_sha256
            != policy.content_sha256
            or preflight.routing_policy_declaration_sha256
            != policy.policy_sha256
            or policy.evaluated_at > preflight.evaluated_at
            or preflight.evaluated_at > self.claimed_at
        ):
            raise ValueError(
                "methodology completion-review preflight differs from dispatch"
            )
        if self.recovery_not_before <= self.claimed_at:
            raise ValueError(
                "methodology completion-review recovery lease must follow claim"
            )
        if self.review_run_id == self.production_run_id:
            raise ValueError(
                "methodology completion reviewer cannot reuse production Run"
            )
        return self


class MethodologyCompletionReviewDispatchReceipt(HashSealedModel):
    """Terminal reviewer process, Evidence, Gate, Stage, and Task receipt."""

    schema_version: Literal["1.0"] = "1.0"
    receipt_id: StableId
    settled_at: AwareDatetime
    dispatch_claim: MethodologyCompletionReviewDispatchClaim
    pid: int | None = Field(default=None, ge=1)
    process_started: bool
    exit_code: int | None = None
    timed_out: bool = False
    output_sha256: Sha256Hex
    error_sha256: Sha256Hex
    repository_unchanged: bool
    usage_observation: ProviderUsageObservation
    protocol_state: RunProtocolState
    handoff_pack_id: StableId | None = None
    handoff_pack_sha256: Sha256Hex | None = None
    stage_status: StageStatus
    gate_status: GateStatus
    task_status: TaskStatus
    evidence_ids: list[StableId] = Field(default_factory=list, max_length=1)
    active_evidence_ids: list[StableId] = Field(
        default_factory=list,
        max_length=500,
    )
    next_stage_key: Literal[None] = None
    formal_review_run_created: Literal[True] = True
    review_context_pack_created: Literal[True] = True
    compatibility_run_created: Literal[False] = False
    protocol_settled: Literal[True] = True
    process_spawn_authority_consumed: Literal[True] = True
    provider_substitution: Literal[False] = False

    @model_validator(mode="after")
    def validate_terminal_binding(self):
        claim = self.dispatch_claim
        state = self.protocol_state
        if self.receipt_id != (
            f"methodology-completion-review-dispatch-receipt:{claim.dispatch_id}"
        ):
            raise ValueError(
                "methodology completion-review receipt identity differs"
            )
        if self.settled_at < claim.claimed_at:
            raise ValueError(
                "methodology completion-review receipt predates its claim"
            )
        if (
            state.run_id != claim.review_run_id
            or self.usage_observation.run_id != claim.review_run_id
            or self.usage_observation.adapter != claim.runtime
        ):
            raise ValueError(
                "methodology completion-review terminal facts cross Run binding"
            )
        launch_failed = state.process_status == ProcessStatus.LAUNCH_FAILED
        if launch_failed != (not self.process_started):
            raise ValueError(
                "methodology completion-review process fact differs from state"
            )
        if (
            self.exit_code != state.process_exit_code
            or self.timed_out != (state.process_status == ProcessStatus.TIMED_OUT)
        ):
            raise ValueError(
                "methodology completion-review terminal process result differs"
            )
        if self.process_started != (self.pid is not None):
            raise ValueError(
                "methodology completion-review PID differs from process fact"
            )
        if not self.process_started and (
            self.pid is not None
            or self.exit_code is not None
            or self.timed_out
            or self.usage_observation.total_tokens != 0
            or self.usage_observation.cost_usd != 0
        ):
            raise ValueError(
                "unstarted completion reviewer requires exact-zero usage"
            )
        if (self.handoff_pack_id is None) != (self.handoff_pack_sha256 is None):
            raise ValueError(
                "completion-review Handoff identity and hash must appear together"
            )
        if (
            self.evidence_ids != sorted(set(self.evidence_ids))
            or self.active_evidence_ids
            != sorted(set(self.active_evidence_ids))
            or not set(self.evidence_ids).issubset(self.active_evidence_ids)
        ):
            raise ValueError(
                "completion-review Evidence identities must be canonical"
            )
        handoff_present = self.handoff_pack_id is not None
        accepted_schema = state.schema_status in {
            SchemaStatus.VALID,
            SchemaStatus.REPAIRED,
        }
        if accepted_schema != handoff_present:
            raise ValueError(
                "completion-review accepted schema and Handoff presence differ"
            )
        if state.semantic_stage_result not in {
            SemanticStageResult.SUCCEEDED,
            SemanticStageResult.BLOCKED,
        }:
            raise ValueError(
                "completion-review receipt requires succeeded or blocked semantics"
            )
        if handoff_present:
            if (
                not accepted_schema
                or not self.repository_unchanged
                or self.evidence_ids
                != [methodology_completion_review_evidence_id(claim.review_run_id)]
            ):
                raise ValueError(
                    "completion-review Handoff requires one exact accepted Evidence"
                )
        elif (
            self.evidence_ids
            or state.semantic_stage_result != SemanticStageResult.BLOCKED
        ):
            raise ValueError(
                "completion-review without a Handoff must settle blocked with no "
                "new Evidence"
            )
        if self.gate_status == GateStatus.PASSED:
            if (
                self.stage_status != StageStatus.COMPLETED
                or self.task_status != TaskStatus.NEEDS_REVIEW
                or state.semantic_stage_result
                != SemanticStageResult.SUCCEEDED
                or not handoff_present
            ):
                raise ValueError(
                    "passed final Gate requires completed Stage and human Task review"
                )
        elif (
            self.gate_status != GateStatus.BLOCKED
            or self.stage_status != StageStatus.BLOCKED
            or self.task_status != TaskStatus.BLOCKED
        ):
            raise ValueError(
                "non-passed completion review must leave Gate, Stage, and Task "
                "blocked"
            )
        return self
