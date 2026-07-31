"""One-shot process-dispatch contracts for an already claimed methodology Run."""
from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, model_validator

from .methodology_migration import MigrationRepositoryBinding
from .models import (
    HashSealedModel,
    PinnedRuntimePreflightDecision,
    ProcessStatus,
    ProtocolModel,
    ProviderUsageObservation,
    RunProtocolState,
    Sha256Hex,
    StableId,
)
from .state_machines import GateStatus, StageStatus


class MethodologyRunDispatchPolicyCheck(ProtocolModel):
    check: Literal[
        "claimed_formal_run",
        "context_binding",
        "current_route",
        "repository_binding",
        "runtime_binding",
        "usage_reservation",
    ]
    satisfied: bool
    detail: Annotated[str, Field(min_length=1, max_length=1000)]


class MethodologyRunDispatchPolicyDecision(HashSealedModel):
    """Per-Run allow/block policy before native capability preflight."""

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
    route_activation_receipt_id: StableId
    route_activation_receipt_sha256: Sha256Hex
    run_claim_receipt_id: StableId
    run_claim_receipt_sha256: Sha256Hex
    repository: MigrationRepositoryBinding
    stage_key: StableId
    gate_key: StableId
    role: Literal["production_execution"] = "production_execution"
    pinned_runtime: StableId
    run_id: StableId
    context_pack_id: StableId
    context_pack_sha256: Sha256Hex
    runtime_capabilities: list[StableId] = Field(max_length=50)
    token_reservation: int = Field(ge=0)
    cost_reservation_usd: float | None = Field(default=None, ge=0)
    checks: list[MethodologyRunDispatchPolicyCheck] = Field(
        min_length=6,
        max_length=6,
    )
    dispatchable: bool
    blockers: list[
        Annotated[str, Field(min_length=1, max_length=1000)]
    ] = Field(default_factory=list, max_length=6)
    route_selection_authority: Literal[False] = False
    runtime_substitution_allowed: Literal[False] = False
    provider_serviceability_verified: Literal[False] = False

    @model_validator(mode="after")
    def validate_policy_decision(self):
        expected_decision_id = (
            "methodology-dispatch-policy:"
            + hashlib.sha256(self.run_id.encode("utf-8")).hexdigest()[:32]
        )
        if self.decision_id != expected_decision_id:
            raise ValueError(
                "methodology dispatch policy identity must match its Run"
            )
        expected = {
            "claimed_formal_run",
            "context_binding",
            "current_route",
            "repository_binding",
            "runtime_binding",
            "usage_reservation",
        }
        names = [item.check for item in self.checks]
        if (
            set(names) != expected
            or len(names) != len(expected)
            or self.checks != sorted(self.checks, key=lambda item: item.check)
        ):
            raise ValueError(
                "methodology dispatch policy must contain every canonical check"
            )
        if self.dispatchable != all(item.satisfied for item in self.checks):
            raise ValueError(
                "methodology dispatch policy result must match all checks"
            )
        usage_check = next(
            item for item in self.checks if item.check == "usage_reservation"
        )
        if usage_check.satisfied != (self.token_reservation > 0):
            raise ValueError(
                "methodology dispatch usage check differs from its reservation"
            )
        expected_blockers = [
            item.detail for item in self.checks if not item.satisfied
        ]
        if self.blockers != expected_blockers:
            raise ValueError(
                "methodology dispatch policy blockers differ from its result"
            )
        if self.runtime_capabilities != sorted(set(self.runtime_capabilities)):
            raise ValueError(
                "methodology dispatch runtime capabilities must be canonical"
            )
        if self.context_pack_id != f"context:{self.run_id}":
            raise ValueError(
                "methodology dispatch policy Context identity must match its Run"
            )
        return self


class MethodologyRunDispatchClaim(HashSealedModel):
    """Durable single-use spawn attachment for one existing formal Run."""

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
    route_activation_receipt_id: StableId
    route_activation_receipt_sha256: Sha256Hex
    run_claim_receipt_id: StableId
    run_claim_receipt_sha256: Sha256Hex
    repository: MigrationRepositoryBinding
    first_stage_key: StableId
    first_gate_key: StableId
    role: Literal["production_execution"] = "production_execution"
    runtime: StableId
    run_id: StableId
    context_pack_id: StableId
    context_pack_sha256: Sha256Hex
    prompt_sha256: Sha256Hex
    dispatch_policy: MethodologyRunDispatchPolicyDecision
    runtime_preflight: PinnedRuntimePreflightDecision
    unbounded_native_usage_acknowledged: Literal[True] = True
    existing_formal_run_reused: Literal[True] = True
    existing_context_pack_reused: Literal[True] = True
    compatibility_run_created: Literal[False] = False
    process_started: Literal[False] = False
    process_spawn_authority: Literal[True] = True
    route_selection_authority: Literal[False] = False
    runtime_substitution_allowed: Literal[False] = False
    provider_serviceability_verified: Literal[False] = False

    @model_validator(mode="after")
    def validate_dispatch_binding(self):
        preflight = self.runtime_preflight
        policy = self.dispatch_policy
        expected_dispatch_id = (
            "methodology-dispatch:"
            + hashlib.sha256(self.run_id.encode("utf-8")).hexdigest()[:32]
        )
        if self.dispatch_id != expected_dispatch_id:
            raise ValueError(
                "methodology dispatch identity must match its Run"
            )
        if self.context_pack_id != f"context:{self.run_id}":
            raise ValueError(
                "methodology dispatch Context Pack identity must match its Run"
            )
        if (
            not policy.dispatchable
            or policy.task_id != self.task_id
            or policy.project_id != self.project_id
            or policy.plan_id != self.plan_id
            or policy.inventory_id != self.inventory_id
            or policy.inventory_sha256 != self.inventory_sha256
            or policy.execution_contract_id != self.execution_contract_id
            or policy.execution_contract_sha256
            != self.execution_contract_sha256
            or policy.route_activation_receipt_id
            != self.route_activation_receipt_id
            or policy.route_activation_receipt_sha256
            != self.route_activation_receipt_sha256
            or policy.run_claim_receipt_id != self.run_claim_receipt_id
            or policy.run_claim_receipt_sha256 != self.run_claim_receipt_sha256
            or policy.repository != self.repository
            or policy.stage_key != self.first_stage_key
            or policy.gate_key != self.first_gate_key
            or policy.role != self.role
            or policy.pinned_runtime != self.runtime
            or policy.run_id != self.run_id
            or policy.context_pack_id != self.context_pack_id
            or policy.context_pack_sha256 != self.context_pack_sha256
            or not preflight.allowed
            or preflight.task_id != self.task_id
            or preflight.project_id != self.project_id
            or preflight.run_id != self.run_id
            or preflight.inventory_id != self.inventory_id
            or preflight.inventory_sha256 != self.inventory_sha256
            or preflight.stage_key != self.first_stage_key
            or preflight.role != self.role
            or preflight.pinned_runtime != self.runtime
            or preflight.routing_policy_decision_id
            != policy.decision_id
            or preflight.routing_policy_decision_sha256
            != policy.content_sha256
            or preflight.routing_policy_declaration_sha256
            != policy.policy_sha256
            or policy.evaluated_at > preflight.evaluated_at
            or preflight.evaluated_at > self.claimed_at
        ):
            raise ValueError(
                "methodology dispatch preflight does not match the claimed route"
            )
        return self


class MethodologyRunDispatchReceipt(HashSealedModel):
    """Terminal receipt after the attached process and formal Run settle."""

    schema_version: Literal["1.0"] = "1.0"
    receipt_id: StableId
    settled_at: AwareDatetime
    dispatch_claim: MethodologyRunDispatchClaim
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
    artifact_ids: list[StableId] = Field(default_factory=list, max_length=200)
    evidence_ids: list[StableId] = Field(default_factory=list, max_length=500)
    active_evidence_ids: list[StableId] = Field(
        default_factory=list,
        max_length=500,
    )
    next_stage_key: StableId | None = None
    existing_formal_run_reused: Literal[True] = True
    existing_context_pack_reused: Literal[True] = True
    compatibility_run_created: Literal[False] = False
    protocol_settled: Literal[True] = True
    process_spawn_authority_consumed: Literal[True] = True
    provider_substitution: Literal[False] = False

    @model_validator(mode="after")
    def validate_terminal_binding(self):
        claim = self.dispatch_claim
        protocol_state = self.protocol_state
        if self.receipt_id != (
            f"methodology-dispatch-receipt:{claim.dispatch_id}"
        ):
            raise ValueError(
                "methodology dispatch receipt identity must match its claim"
            )
        if self.settled_at < claim.claimed_at:
            raise ValueError(
                "methodology dispatch receipt predates its claim"
            )
        if (
            protocol_state.run_id != claim.run_id
            or self.usage_observation.run_id != claim.run_id
            or self.usage_observation.adapter != claim.runtime
        ):
            raise ValueError(
                "methodology dispatch terminal facts cross their claimed Run binding"
            )
        launch_failed = protocol_state.process_status == ProcessStatus.LAUNCH_FAILED
        if launch_failed != (not self.process_started):
            raise ValueError(
                "methodology dispatch process-start fact differs from protocol state"
            )
        if self.process_started != (self.pid is not None):
            raise ValueError(
                "methodology dispatch PID presence differs from process start"
            )
        if not self.process_started and (
            self.pid is not None
            or self.exit_code is not None
            or self.timed_out
            or self.usage_observation.total_tokens != 0
            or self.usage_observation.cost_usd != 0
        ):
            raise ValueError(
                "an unstarted methodology process requires exact-zero terminal usage"
            )
        if (self.handoff_pack_id is None) != (self.handoff_pack_sha256 is None):
            raise ValueError(
                "methodology dispatch Handoff identity and hash must appear together"
            )
        if (
            self.active_evidence_ids != sorted(set(self.active_evidence_ids))
            or not set(self.active_evidence_ids).issubset(self.evidence_ids)
            or self.artifact_ids != sorted(set(self.artifact_ids))
            or self.evidence_ids != sorted(set(self.evidence_ids))
        ):
            raise ValueError(
                "methodology dispatch Artifact and Evidence ids must be canonical"
            )
        return self
