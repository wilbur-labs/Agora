from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from agora.control_plane.models import StageRouteDecision
from agora.orchestration.models import (
    RoutingConstraintCheck,
    RoutingPolicyDecision,
)
from agora.orchestration.routing_policy import (
    ROUTING_POLICY_ID,
    ROUTING_POLICY_SHA256,
    ROUTING_POLICY_VERSION,
    RUNTIME_CAPABILITIES,
)
from agora.orchestration.runtime import (
    RuntimeCommand,
    resolve_runtime_command,
)
from agora.orchestration.runtime_capabilities import (
    collect_native_runtime_capabilities,
)
from agora.orchestration.runtime_preflight import (
    RuntimePreflightError,
    derive_pinned_runtime_preflight,
    recheck_pinned_runtime_preflight,
)
from agora.protocol.hashing import seal_model_payload


NOW = datetime(2026, 7, 24, 1, 0, tzinfo=timezone.utc)
SHA = "a" * 64


def _runtimes(*, missing_codex: bool = False) -> dict[str, RuntimeCommand]:
    return {
        name: RuntimeCommand(
            adapter=name,
            command_template=(
                "agora-preflight-missing-runtime"
                if missing_codex and name == "codex"
                else sys.executable,
                "{prompt}",
            ),
        )
        for name in ("codex", "claude", "kiro")
    }


def _route() -> StageRouteDecision:
    return StageRouteDecision(
        task_id="task_alpha",
        project_id="alpha",
        inventory_id="inventory_alpha",
        inventory_sha256=SHA,
        group_key="planning",
        group_sequence=1,
        stage_key="solution_design",
        gate_key="gate:solution_design",
        stage_sequence=1,
        inventory_sequence=1,
        title="Solution design",
        role="engineering_planner",
        runtime="codex",
        stage_status="ready",
        gate_status="pending",
        runnable=True,
    )


def _routing_policy() -> RoutingPolicyDecision:
    constraints = [
        "stage_assignment",
        "runtime_capability",
        "reviewer_coverage",
        "risk_coverage",
        "protected_budget",
    ]
    payload = {
        "schema_version": "1.0",
        "decision_id": "routing_policy_run_alpha",
        "policy_id": ROUTING_POLICY_ID,
        "policy_version": ROUTING_POLICY_VERSION,
        "policy_sha256": ROUTING_POLICY_SHA256,
        "task_id": "task_alpha",
        "project_id": "alpha",
        "plan_id": "plan_alpha",
        "inventory_id": "inventory_alpha",
        "inventory_sha256": SHA,
        "methodology_id": "agora-aidlc-foundation",
        "methodology_version": "0.1",
        "methodology_sha256": "b" * 64,
        "stage_key": "solution_design",
        "role": "engineering_planner",
        "pinned_runtime": "codex",
        "task_risk": "medium",
        "required_capabilities": [
            "implementation_planning",
            "verification_planning",
        ],
        "runtime_capabilities": sorted(RUNTIME_CAPABILITIES["codex"]),
        "required_reviewers": [],
        "reviewer_assignments": [],
        "task_token_budget": 30_000,
        "settled_token_debit": 0,
        "active_token_reservations": 0,
        "available_tokens_before_dispatch": 30_000,
        "current_run_token_reservation": 10_000,
        "protected_future_reviewer_tokens": 12_000,
        "task_cost_budget_usd": 12.0,
        "settled_cost_debit_usd": 0.0,
        "active_cost_reservations_usd": 0.0,
        "available_cost_before_dispatch_usd": 12.0,
        "current_run_cost_reservation_usd": 4.0,
        "protected_future_reviewer_cost_usd": 4.8,
        "checks": [
            RoutingConstraintCheck(
                constraint=constraint,
                satisfied=True,
                detail=f"{constraint} passed",
            ).model_dump(mode="json")
            for constraint in constraints
        ],
        "dispatchable": True,
        "blockers": [],
        "rationale": [f"rationale {index}" for index in range(1, 6)],
    }
    return RoutingPolicyDecision.model_validate(
        seal_model_payload(RoutingPolicyDecision, payload)
    )


@pytest.mark.asyncio
async def test_preflight_allows_only_the_installed_pinned_runtime():
    runtimes = _runtimes()
    observation = await collect_native_runtime_capabilities(
        runtimes,
        collected_at=NOW,
    )
    decision = derive_pinned_runtime_preflight(
        observation=observation,
        runtimes=runtimes,
        route=_route(),
        routing_policy=_routing_policy(),
        run_id="run_alpha",
        evaluated_at=NOW + timedelta(seconds=1),
    )

    assert decision.allowed is True
    assert decision.pinned_runtime == "codex"
    assert decision.capability_observation_sha256 == observation.content_sha256
    assert decision.runtime_substitution_allowed is False
    assert decision.route_selection_authority is False
    assert decision.provider_serviceability_verified is False
    assert decision.version_status == "unavailable"
    assert all(item.satisfied for item in decision.checks)

    runtime = runtimes["codex"]
    recheck_pinned_runtime_preflight(
        decision=decision,
        observation=observation,
        runtimes=runtimes,
        runtime=runtime,
        resolved_command=resolve_runtime_command(runtime.build("prompt")),
        checked_at=NOW + timedelta(seconds=2),
    )


@pytest.mark.asyncio
async def test_preflight_blocks_missing_runtime_without_substitution():
    runtimes = _runtimes(missing_codex=True)
    observation = await collect_native_runtime_capabilities(
        runtimes,
        collected_at=NOW,
    )
    decision = derive_pinned_runtime_preflight(
        observation=observation,
        runtimes=runtimes,
        route=_route(),
        routing_policy=_routing_policy(),
        run_id="run_alpha",
        evaluated_at=NOW + timedelta(seconds=1),
    )

    assert decision.allowed is False
    assert decision.installation_status == "not_found"
    assert decision.pinned_runtime == "codex"
    assert any(
        item.check == "runtime_installation" and not item.satisfied
        for item in decision.checks
    )
    assert "unavailable or uninspectable" in decision.blockers[0]


@pytest.mark.asyncio
async def test_preflight_structurally_blocks_missing_adapter_observation():
    runtimes = _runtimes()
    incomplete_observation = await collect_native_runtime_capabilities(
        {
            name: runtime
            for name, runtime in runtimes.items()
            if name != "codex"
        },
        collected_at=NOW,
    )

    decision = derive_pinned_runtime_preflight(
        observation=incomplete_observation,
        runtimes=runtimes,
        route=_route(),
        routing_policy=_routing_policy(),
        run_id="run_missing_observation",
        evaluated_at=NOW + timedelta(seconds=1),
    )

    assert decision.allowed is False
    assert decision.installation_status == "uninspectable"
    assert decision.resolved_runtime_command_sha256 is None
    assert decision.declared_capabilities == []
    assert any(
        item.check == "observation_integrity" and not item.satisfied
        for item in decision.checks
    )


@pytest.mark.asyncio
async def test_pre_spawn_recheck_rejects_expiry_and_command_change():
    runtimes = _runtimes()
    observation = await collect_native_runtime_capabilities(
        runtimes,
        collected_at=NOW,
    )
    decision = derive_pinned_runtime_preflight(
        observation=observation,
        runtimes=runtimes,
        route=_route(),
        routing_policy=_routing_policy(),
        run_id="run_alpha",
        evaluated_at=NOW + timedelta(seconds=1),
    )
    runtime = runtimes["codex"]
    resolved = resolve_runtime_command(runtime.build("prompt"))

    stale_decision = derive_pinned_runtime_preflight(
        observation=observation,
        runtimes=runtimes,
        route=_route(),
        routing_policy=_routing_policy(),
        run_id="run_stale",
        evaluated_at=NOW + timedelta(seconds=61),
    )
    assert stale_decision.allowed is False
    assert any(
        item.check == "observation_freshness" and not item.satisfied
        for item in stale_decision.checks
    )

    with pytest.raises(RuntimePreflightError, match="expired"):
        recheck_pinned_runtime_preflight(
            decision=decision,
            observation=observation,
            runtimes=runtimes,
            runtime=runtime,
            resolved_command=resolved,
            checked_at=NOW + timedelta(seconds=61),
        )

    changed = dict(runtimes)
    changed["codex"] = RuntimeCommand(
        adapter="codex",
        command_template=(sys.executable, "-I", "{prompt}"),
    )
    with pytest.raises(RuntimePreflightError, match="bindings changed"):
        recheck_pinned_runtime_preflight(
            decision=decision,
            observation=observation,
            runtimes=changed,
            runtime=changed["codex"],
            resolved_command=resolve_runtime_command(
                changed["codex"].build("prompt")
            ),
            checked_at=NOW + timedelta(seconds=2),
        )


@pytest.mark.asyncio
async def test_preflight_hash_tampering_fails_closed():
    runtimes = _runtimes()
    observation = await collect_native_runtime_capabilities(
        runtimes,
        collected_at=NOW,
    )
    decision = derive_pinned_runtime_preflight(
        observation=observation,
        runtimes=runtimes,
        route=_route(),
        routing_policy=_routing_policy(),
        run_id="run_alpha",
        evaluated_at=NOW + timedelta(seconds=1),
    )
    payload = decision.model_dump(mode="json")
    payload["provider_serviceability_verified"] = True

    with pytest.raises(ValidationError, match="Input should be False"):
        type(decision).model_validate(payload)
