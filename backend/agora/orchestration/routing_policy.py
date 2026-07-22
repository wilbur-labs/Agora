"""Explainable policy checks for one pinned authoritative Stage route."""
from __future__ import annotations

from dataclasses import dataclass

from agora.control_plane.models import StageRouteDecision
from agora.protocol.hashing import canonical_sha256, seal_model_payload
from agora.tasks.models import TaskManifest, TaskRisk

from .contracts import TaskContract
from .models import (
    MethodologyDefinition,
    RoutingConstraintCheck,
    RoutingPolicyDecision,
    RoutingReviewerAssignment,
    StageState,
)


ROUTING_POLICY_ID = "agora-foundation-routing-policy"
ROUTING_POLICY_VERSION = "1.0"

ROLE_REQUIRED_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "engineering_planner": (
        "implementation_planning",
        "verification_planning",
    ),
    "independent_reviewer": (
        "correctness_review",
        "safety_review",
        "regression_review",
    ),
    "methodology_steward": (
        "methodology_review",
        "protocol_review",
        "lifecycle_review",
        "delivery_boundary_review",
    ),
}

RUNTIME_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "codex": (
        "implementation_planning",
        "verification_planning",
        "code_change",
        "test_execution",
    ),
    "claude": (
        "correctness_review",
        "safety_review",
        "regression_review",
    ),
    "kiro": (
        "methodology_review",
        "protocol_review",
        "lifecycle_review",
        "delivery_boundary_review",
    ),
}

REVIEWER_ROLES = frozenset({"independent_reviewer", "methodology_steward"})
MINIMUM_INDEPENDENT_REVIEWERS = {
    TaskRisk.LOW: 1,
    TaskRisk.MEDIUM: 1,
    TaskRisk.HIGH: 2,
    TaskRisk.CRITICAL: 2,
}

ROUTING_POLICY_SHA256 = canonical_sha256(
    {
        "policy_id": ROUTING_POLICY_ID,
        "version": ROUTING_POLICY_VERSION,
        "role_required_capabilities": ROLE_REQUIRED_CAPABILITIES,
        "runtime_capabilities": RUNTIME_CAPABILITIES,
        "reviewer_roles": sorted(REVIEWER_ROLES),
        "minimum_independent_reviewers": {
            risk.value: count
            for risk, count in MINIMUM_INDEPENDENT_REVIEWERS.items()
        },
        "protect_unfinished_reviewer_stage_allocations": True,
        "runtime_substitution": False,
    }
)


@dataclass(frozen=True)
class RoutingStageBudget:
    stage_key: str
    sequence: int
    title: str
    role: str
    runtime: str
    state: StageState
    token_budget: int
    cost_budget_usd: float | None


def _money(value: float) -> float:
    return round(value, 9)


def derive_routing_policy_decision(
    *,
    decision_id: str,
    task: TaskManifest,
    contract: TaskContract,
    methodology: MethodologyDefinition,
    methodology_sha256: str,
    plan_id: str,
    route: StageRouteDecision,
    stages: list[RoutingStageBudget],
    task_token_budget: int,
    settled_token_debit: int,
    active_token_reservations: int,
    task_cost_budget_usd: float | None,
    settled_cost_debit_usd: float | None,
    active_cost_reservations_usd: float | None,
) -> RoutingPolicyDecision:
    """Explain why the sealed assignment is safe without selecting a runtime."""

    stage_by_key = {stage.stage_key: stage for stage in stages}
    method_by_key = {stage.stage_key: stage for stage in methodology.stages}
    contract_stage_by_key = {stage.stage_key: stage for stage in contract.workflow}
    contract_role_by_key = {role.role_id: role for role in contract.roles}
    current = stage_by_key.get(route.stage_key)
    method_stage = method_by_key.get(route.stage_key)
    contract_stage = contract_stage_by_key.get(route.stage_key)
    current_role = (
        contract_role_by_key.get(contract_stage.role_id)
        if contract_stage is not None
        else None
    )

    method_and_contract_aligned = bool(
        len(stages) == len(methodology.stages) == len(contract.workflow)
        and all(
            persisted.sequence == sequence
            and persisted.stage_key == method_stage_item.stage_key
            and persisted.title == method_stage_item.title
            and persisted.role == method_stage_item.role
            and persisted.runtime == method_stage_item.adapter
            and contract_stage_item.stage_key == method_stage_item.stage_key
            and contract_stage_item.role_id == method_stage_item.role
            and contract_role_by_key.get(contract_stage_item.role_id) is not None
            and contract_role_by_key[contract_stage_item.role_id].runtime
            == method_stage_item.adapter
            for sequence, (persisted, method_stage_item, contract_stage_item)
            in enumerate(
                zip(stages, methodology.stages, contract.workflow),
                start=1,
            )
        )
    )
    compatibility_state_aligned = bool(
        current is not None
        and all(
            stage.state
            == (
                StageState.PASSED
                if stage.sequence < current.sequence
                else StageState.PENDING
            )
            for stage in stages
        )
    )
    stage_assignment_ok = bool(
        task.task_id == route.task_id
        and task.project_id == route.project_id
        and method_and_contract_aligned
        and compatibility_state_aligned
        and current is not None
        and method_stage is not None
        and contract_stage is not None
        and current_role is not None
        and current.stage_key == route.stage_key
        and current.title == route.title
        and current.role == route.role
        and current.runtime == route.runtime
        and method_stage.role == route.role
        and method_stage.adapter == route.runtime
        and contract_stage.role_id == route.role
        and current_role.runtime == route.runtime
    )
    stage_detail = (
        f"Sealed Stage {route.stage_key} pins role {route.role} to runtime "
        f"{route.runtime}; substitution is disabled."
        if stage_assignment_ok
        else "The sealed Stage, methodology, contract, and compatibility assignment do not agree."
    )

    required_capabilities = tuple(ROLE_REQUIRED_CAPABILITIES.get(route.role, ()))
    runtime_capabilities = tuple(RUNTIME_CAPABILITIES.get(route.runtime, ()))
    runtime_capability_ok = bool(
        required_capabilities
        and set(required_capabilities).issubset(runtime_capabilities)
    )
    capability_detail = (
        f"Pinned runtime {route.runtime} satisfies role {route.role} capabilities: "
        + ", ".join(required_capabilities)
        if runtime_capability_ok
        else f"Pinned runtime {route.runtime} lacks declared capabilities for role {route.role}."
    )

    sealed_reviewer_runtimes = {
        stage.adapter
        for stage in methodology.stages
        if stage.role in REVIEWER_ROLES
    }
    required_reviewers = sorted(sealed_reviewer_runtimes | set(task.reviewers))
    reviewer_assignments: list[RoutingReviewerAssignment] = []
    reviewer_errors: list[str] = []
    for runtime in required_reviewers:
        matches = [
            stage
            for stage in methodology.stages
            if stage.adapter == runtime and stage.role in REVIEWER_ROLES
        ]
        if len(matches) != 1:
            reviewer_errors.append(
                f"reviewer {runtime} is not bound to exactly one sealed reviewer Stage"
            )
            continue
        reviewer_stage = matches[0]
        contract_reviewer_stage = contract_stage_by_key.get(reviewer_stage.stage_key)
        reviewer_role = contract_role_by_key.get(reviewer_stage.role)
        reviewer_capabilities = ROLE_REQUIRED_CAPABILITIES.get(reviewer_stage.role, ())
        available_capabilities = RUNTIME_CAPABILITIES.get(runtime, ())
        independent_roles = (
            list(reviewer_role.independent_from) if reviewer_role is not None else []
        )
        independent_runtimes_differ = bool(independent_roles) and all(
            contract_role_by_key.get(role_id) is not None
            and contract_role_by_key[role_id].runtime != runtime
            for role_id in independent_roles
        )
        valid = bool(
            contract_reviewer_stage is not None
            and contract_reviewer_stage.role_id == reviewer_stage.role
            and reviewer_role is not None
            and reviewer_role.runtime == runtime
            and reviewer_capabilities
            and set(reviewer_capabilities).issubset(available_capabilities)
            and independent_runtimes_differ
        )
        if not valid:
            reviewer_errors.append(
                f"reviewer {runtime} lacks a capability-complete independent contract binding"
            )
            continue
        reviewer_assignments.append(
            RoutingReviewerAssignment(
                runtime=runtime,
                role=reviewer_stage.role,
                stage_key=reviewer_stage.stage_key,
                independent_from_roles=sorted(independent_roles),
                required_capabilities=sorted(reviewer_capabilities),
            )
        )

    reviewer_coverage_ok = bool(
        required_reviewers
        and not reviewer_errors
        and len(task.reviewers) == len(set(task.reviewers))
        and {item.runtime for item in reviewer_assignments} == set(required_reviewers)
        and set(task.reviewers) == sealed_reviewer_runtimes
    )
    reviewer_detail = (
        "Required independent reviewer set is " + ", ".join(required_reviewers)
        + "; every reviewer has a distinct contract role and capability-complete sealed Stage."
        if reviewer_coverage_ok
        else "Reviewer coverage is invalid: "
        + "; ".join(
            reviewer_errors
            or ["Task reviewer declarations differ from the sealed reviewer Stages"]
        )
    )

    minimum_reviewers = MINIMUM_INDEPENDENT_REVIEWERS[task.risk]
    independent_reviewer_count = len({item.runtime for item in reviewer_assignments})
    risk_coverage_ok = independent_reviewer_count >= minimum_reviewers
    risk_detail = (
        f"Task risk {task.risk.value} requires at least {minimum_reviewers} independent "
        f"reviewer(s); {independent_reviewer_count} are sealed."
    )

    current_sequence = current.sequence if current is not None else 0
    protected_stages = [
        stage
        for stage in stages
        if stage.sequence > current_sequence
        and stage.role in REVIEWER_ROLES
        and stage.runtime in required_reviewers
        and stage.state != StageState.PASSED
    ]
    # A missing current Stage already blocks stage_assignment. Keep the
    # explanatory budget snapshot neutral rather than inventing a reservation.
    current_token_reservation = current.token_budget if current is not None else 0
    protected_tokens = sum(stage.token_budget for stage in protected_stages)
    raw_available_tokens = (
        task_token_budget - settled_token_debit - active_token_reservations
    )
    available_tokens = max(0, raw_available_tokens)
    token_budget_ok = bool(
        current is not None
        and raw_available_tokens >= current_token_reservation + protected_tokens
    )

    task_cost_matches = (
        (task.budget.max_cost_usd is None and task_cost_budget_usd is None)
        or (
            task.budget.max_cost_usd is not None
            and task_cost_budget_usd is not None
            and abs(task.budget.max_cost_usd - task_cost_budget_usd) < 1e-9
        )
    )
    if task_cost_budget_usd is None:
        settled_cost = None
        active_cost = None
        available_cost = None
        current_cost = None
        protected_cost = None
        cost_budget_ok = bool(
            task_cost_matches
            and current is not None
            and current.cost_budget_usd is None
            and all(stage.cost_budget_usd is None for stage in stages)
        )
    else:
        settled_cost = _money(settled_cost_debit_usd or 0.0)
        active_cost = _money(active_cost_reservations_usd or 0.0)
        raw_available_cost = _money(
            task_cost_budget_usd - settled_cost - active_cost
        )
        available_cost = max(0.0, raw_available_cost)
        current_cost = (
            _money(current.cost_budget_usd)
            if current is not None and current.cost_budget_usd is not None
            else None
        )
        protected_cost_values = [stage.cost_budget_usd for stage in protected_stages]
        protected_cost = (
            _money(sum(value for value in protected_cost_values if value is not None))
            if all(value is not None for value in protected_cost_values)
            else None
        )
        cost_budget_ok = bool(
            task_cost_matches
            and current_cost is not None
            and protected_cost is not None
            and raw_available_cost + 1e-9 >= current_cost + protected_cost
        )
    protected_budget_ok = token_budget_ok and cost_budget_ok
    protected_stage_keys = [stage.stage_key for stage in protected_stages]
    budget_detail = (
        f"Dispatch reserves {current_token_reservation} tokens and protects "
        f"{protected_tokens} tokens for future required reviewer Stages "
        f"{protected_stage_keys or []}."
        if protected_budget_ok
        else "Dispatch would consume budget protected for required independent review; "
        "increase the Task budget or reduce scope without weakening the reviewer set."
    )

    checks = [
        RoutingConstraintCheck(
            constraint="stage_assignment",
            satisfied=stage_assignment_ok,
            detail=stage_detail,
        ),
        RoutingConstraintCheck(
            constraint="runtime_capability",
            satisfied=runtime_capability_ok,
            detail=capability_detail,
        ),
        RoutingConstraintCheck(
            constraint="reviewer_coverage",
            satisfied=reviewer_coverage_ok,
            detail=reviewer_detail,
        ),
        RoutingConstraintCheck(
            constraint="risk_coverage",
            satisfied=risk_coverage_ok,
            detail=risk_detail,
        ),
        RoutingConstraintCheck(
            constraint="protected_budget",
            satisfied=protected_budget_ok,
            detail=budget_detail,
        ),
    ]
    blockers = [check.detail for check in checks if not check.satisfied]
    payload = {
        "schema_version": "1.0",
        "decision_id": decision_id,
        "policy_id": ROUTING_POLICY_ID,
        "policy_version": ROUTING_POLICY_VERSION,
        "policy_sha256": ROUTING_POLICY_SHA256,
        "task_id": task.task_id,
        "project_id": task.project_id,
        "plan_id": plan_id,
        "inventory_id": route.inventory_id,
        "inventory_sha256": route.inventory_sha256,
        "methodology_id": methodology.methodology_id,
        "methodology_version": methodology.version,
        "methodology_sha256": methodology_sha256,
        "stage_key": route.stage_key,
        "role": route.role,
        "pinned_runtime": route.runtime,
        "task_risk": task.risk,
        "required_capabilities": sorted(required_capabilities),
        "runtime_capabilities": sorted(runtime_capabilities),
        "required_reviewers": required_reviewers,
        "reviewer_assignments": reviewer_assignments,
        "task_token_budget": task_token_budget,
        "settled_token_debit": settled_token_debit,
        "active_token_reservations": active_token_reservations,
        "available_tokens_before_dispatch": available_tokens,
        "current_run_token_reservation": current_token_reservation,
        "protected_future_reviewer_tokens": protected_tokens,
        "task_cost_budget_usd": task_cost_budget_usd,
        "settled_cost_debit_usd": settled_cost,
        "active_cost_reservations_usd": active_cost,
        "available_cost_before_dispatch_usd": available_cost,
        "current_run_cost_reservation_usd": current_cost,
        "protected_future_reviewer_cost_usd": protected_cost,
        "checks": checks,
        "dispatchable": not blockers,
        "blockers": blockers,
        "rationale": [check.detail for check in checks],
    }
    return RoutingPolicyDecision.model_validate(
        seal_model_payload(RoutingPolicyDecision, payload)
    )
