"""Fresh allow/block preflight for one already pinned native runtime."""
from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timedelta, timezone
from typing import Sequence

from agora.control_plane.models import StageRouteDecision
from agora.protocol.hashing import seal_model_payload
from agora.protocol.methodology_run_dispatch import (
    MethodologyRunDispatchPolicyDecision,
)
from agora.protocol.methodology_completion_review_dispatch import (
    MethodologyCompletionReviewDispatchPolicyDecision,
)
from agora.protocol.methodology_stage_run_dispatch import (
    MethodologyStageRunDispatchPolicyDecision,
)
from agora.protocol.models import (
    NativeRuntimeCapabilityObservation,
    PinnedRuntimePreflightCheck,
    PinnedRuntimePreflightDecision,
)

from .models import RoutingPolicyDecision
from .routing_policy import (
    ROUTING_POLICY_ID,
    ROUTING_POLICY_SHA256,
    ROUTING_POLICY_VERSION,
)
from .runtime import RuntimeCommand
from .runtime_capabilities import (
    resolve_runtime_launch_binding,
    runtime_command_sha256,
    runtime_registry_sha256,
)


PREFLIGHT_MAX_OBSERVATION_AGE_SECONDS = 60


class RuntimePreflightError(RuntimeError):
    """The pinned runtime no longer matches its fresh preflight decision."""


def derive_pinned_runtime_preflight(
    *,
    observation: NativeRuntimeCapabilityObservation,
    runtimes: dict[str, RuntimeCommand],
    route: StageRouteDecision,
    routing_policy: (
        RoutingPolicyDecision
        | MethodologyRunDispatchPolicyDecision
        | MethodologyStageRunDispatchPolicyDecision
        | MethodologyCompletionReviewDispatchPolicyDecision
    ),
    run_id: str,
    evaluated_at: datetime | None = None,
    platform: str | None = None,
) -> PinnedRuntimePreflightDecision:
    """Derive a sealed decision without selecting or substituting a runtime."""
    evaluated = evaluated_at or datetime.now(timezone.utc)
    if evaluated.tzinfo is None or evaluated.utcoffset() is None:
        raise ValueError("evaluated_at must include a timezone")
    valid_until = observation.collected_at + timedelta(
        seconds=PREFLIGHT_MAX_OBSERVATION_AGE_SECONDS
    )
    runtime = runtimes.get(route.runtime)
    adapter = next(
        (item for item in observation.adapters if item.adapter == route.runtime),
        None,
    )
    current_registry_sha256 = runtime_registry_sha256(runtimes)
    launch = (
        resolve_runtime_launch_binding(runtime, platform=platform)
        if runtime is not None
        else None
    )

    route_binding = bool(
        routing_policy.dispatchable
        and routing_policy.task_id == route.task_id
        and routing_policy.project_id == route.project_id
        and routing_policy.inventory_id == route.inventory_id
        and routing_policy.inventory_sha256 == route.inventory_sha256
        and routing_policy.stage_key == route.stage_key
        and routing_policy.role == route.role
        and routing_policy.pinned_runtime == route.runtime
    )
    freshness = bool(
        observation.collected_at <= evaluated <= valid_until
    )
    observation_integrity = bool(
        observation.capability_declaration_id == ROUTING_POLICY_ID
        and observation.capability_declaration_version == ROUTING_POLICY_VERSION
        and observation.capability_declaration_sha256 == ROUTING_POLICY_SHA256
        and observation.routing_authority is False
        and adapter is not None
    )
    command_binding = bool(
        runtime is not None
        and adapter is not None
        and launch is not None
        and observation.runtime_registry_sha256 == current_registry_sha256
        and adapter.runtime_command_sha256 == runtime_command_sha256(runtime)
        and adapter.resolved_runtime_command_sha256 == launch.content_sha256
    )
    runtime_installation = bool(
        adapter is not None
        and launch is not None
        and adapter.installation_status == "installed"
        and launch.installation_status == "installed"
        and adapter.resolved_runtime_command_sha256 is not None
    )
    declaration_binding = bool(
        adapter is not None
        and routing_policy.policy_id == ROUTING_POLICY_ID
        and routing_policy.policy_version == ROUTING_POLICY_VERSION
        and routing_policy.policy_sha256 == ROUTING_POLICY_SHA256
        and observation.capability_declaration_sha256 == routing_policy.policy_sha256
        and adapter.declared_capabilities
        == sorted(routing_policy.runtime_capabilities)
    )

    checks = sorted(
        [
            PinnedRuntimePreflightCheck(
                check="route_binding",
                satisfied=route_binding,
                detail=(
                    "The decision is bound to the exact sealed route and dispatchable "
                    "per-Run routing policy."
                    if route_binding
                    else "The sealed route and per-Run routing policy do not match."
                ),
            ),
            PinnedRuntimePreflightCheck(
                check="observation_freshness",
                satisfied=freshness,
                detail=(
                    "The native observation is within the 60-second preflight window."
                    if freshness
                    else "The native observation is future-dated or older than 60 seconds."
                ),
            ),
            PinnedRuntimePreflightCheck(
                check="observation_integrity",
                satisfied=observation_integrity,
                detail=(
                    "The sealed observation contains the pinned adapter and reviewed "
                    "capability declaration provenance."
                    if observation_integrity
                    else "The observation or capability declaration provenance is invalid."
                ),
            ),
            PinnedRuntimePreflightCheck(
                check="runtime_command_binding",
                satisfied=command_binding,
                detail=(
                    "The configured registry, command template, and audited resolved "
                    "launch target match the observation."
                    if command_binding
                    else "The configured or resolved runtime command changed."
                ),
            ),
            PinnedRuntimePreflightCheck(
                check="runtime_installation",
                satisfied=runtime_installation,
                detail=(
                    "The already pinned native runtime is locally installed."
                    if runtime_installation
                    else "The already pinned native runtime is unavailable or uninspectable."
                ),
            ),
            PinnedRuntimePreflightCheck(
                check="capability_declaration_binding",
                satisfied=declaration_binding,
                detail=(
                    "Declared capabilities match the reviewed routing-policy declaration."
                    if declaration_binding
                    else "Capability declarations differ from the reviewed routing policy."
                ),
            ),
        ],
        key=lambda item: item.check,
    )
    blockers = [
        item.detail for item in checks if not item.satisfied
    ]
    allowed = not blockers
    decision_id = (
        "runtime-preflight:"
        + hashlib.sha256(
            f"{run_id}:{observation.content_sha256}".encode("utf-8")
        ).hexdigest()[:32]
    )
    adapter_runtime_command_sha256 = (
        adapter.runtime_command_sha256
        if adapter is not None
        else (
            runtime_command_sha256(runtime)
            if runtime is not None
            else "0" * 64
        )
    )
    payload = {
        "schema_version": "1.0",
        "decision_id": decision_id,
        "evaluated_at": evaluated,
        "valid_until": valid_until,
        "max_observation_age_seconds": PREFLIGHT_MAX_OBSERVATION_AGE_SECONDS,
        "task_id": route.task_id,
        "project_id": route.project_id,
        "run_id": run_id,
        "inventory_id": route.inventory_id,
        "inventory_sha256": route.inventory_sha256,
        "stage_key": route.stage_key,
        "role": route.role,
        "pinned_runtime": route.runtime,
        "routing_policy_decision_id": routing_policy.decision_id,
        "routing_policy_decision_sha256": routing_policy.content_sha256,
        "routing_policy_declaration_sha256": routing_policy.policy_sha256,
        "capability_observation_sha256": observation.content_sha256,
        "capability_observation": observation.model_dump(mode="json"),
        "observation_collected_at": observation.collected_at,
        "runtime_registry_sha256": observation.runtime_registry_sha256,
        "runtime_command_sha256": adapter_runtime_command_sha256,
        "resolved_runtime_command_sha256": (
            adapter.resolved_runtime_command_sha256
            if adapter is not None
            else None
        ),
        "capability_declaration_id": observation.capability_declaration_id,
        "capability_declaration_version": observation.capability_declaration_version,
        "capability_declaration_sha256": observation.capability_declaration_sha256,
        "installation_status": (
            adapter.installation_status if adapter is not None else "uninspectable"
        ),
        "version": adapter.version if adapter is not None else None,
        "version_status": (
            adapter.version_status if adapter is not None else "unavailable"
        ),
        "model_availability": (
            adapter.model_availability if adapter is not None else "unavailable"
        ),
        "declared_models": adapter.declared_models if adapter is not None else [],
        "declared_capabilities": (
            adapter.declared_capabilities
            if adapter is not None
            else []
        ),
        "checks": [item.model_dump(mode="json") for item in checks],
        "allowed": allowed,
        "blockers": blockers,
        "rationale": [
            "This preflight may only allow or block the already sealed Stage route.",
            "Runtime and model substitution remain disabled.",
            (
                "Declared models and capabilities are provenance only; this decision "
                "does not verify provider authentication or serviceability."
            ),
            (
                "An unavailable native version is informational because no reviewed "
                "version constraint exists for this policy."
            ),
        ],
        "route_selection_authority": False,
        "runtime_substitution_allowed": False,
        "provider_serviceability_verified": False,
    }
    return PinnedRuntimePreflightDecision.model_validate(
        seal_model_payload(PinnedRuntimePreflightDecision, payload)
    )


def runtime_preflight_remediation(
    decision: PinnedRuntimePreflightDecision,
) -> list[str]:
    """Explain bounded repair for the pinned route without suggesting substitution."""

    if decision.allowed:
        return [
            (
                "No local launch-binding remediation is required. Provider "
                "authentication and serviceability remain unverified."
            )
        ]
    remediation_by_check = {
        "route_binding": (
            "Reconcile the authoritative Task and Stage route with `agora task "
            "resume`, then rerun this preview."
        ),
        "observation_freshness": (
            "Rerun this preview to collect a fresh native capability observation."
        ),
        "observation_integrity": (
            "Restore the reviewed routing-policy declaration and complete pinned "
            "adapter observation, then rerun this preview."
        ),
        "runtime_command_binding": (
            "Restore the configured command and audited launcher binding for the "
            "already pinned runtime, then rerun this preview."
        ),
        "runtime_installation": (
            "Install or repair the already pinned runtime executable or launcher. "
            "Changing runtimes requires a separate reviewed route or methodology "
            "change."
        ),
        "capability_declaration_binding": (
            "Restore the reviewed capability declaration for the already pinned "
            "runtime. This preview cannot select a substitute."
        ),
    }
    return [
        remediation_by_check[item.check]
        for item in decision.checks
        if not item.satisfied
    ]


def recheck_pinned_runtime_preflight(
    *,
    decision: PinnedRuntimePreflightDecision,
    observation: NativeRuntimeCapabilityObservation,
    runtimes: dict[str, RuntimeCommand],
    runtime: RuntimeCommand,
    resolved_command: Sequence[str],
    checked_at: datetime | None = None,
    platform: str | None = None,
) -> None:
    """Fail closed immediately before spawn when any bound launch fact changed."""
    checked = checked_at or datetime.now(timezone.utc)
    if checked.tzinfo is None or checked.utcoffset() is None:
        raise RuntimePreflightError("pre-spawn check time must include a timezone")
    if not decision.allowed:
        raise RuntimePreflightError("pinned runtime preflight is blocked")
    if checked > decision.valid_until:
        raise RuntimePreflightError("pinned runtime preflight observation expired")
    if (
        decision.capability_observation_sha256 != observation.content_sha256
        or decision.capability_observation != observation
        or decision.runtime_registry_sha256 != observation.runtime_registry_sha256
        or runtime.adapter != decision.pinned_runtime
        or runtime_registry_sha256(runtimes) != decision.runtime_registry_sha256
        or runtime_command_sha256(runtime) != decision.runtime_command_sha256
        or decision.capability_declaration_id != ROUTING_POLICY_ID
        or decision.capability_declaration_version != ROUTING_POLICY_VERSION
        or decision.capability_declaration_sha256 != ROUTING_POLICY_SHA256
        or decision.routing_policy_declaration_sha256 != ROUTING_POLICY_SHA256
    ):
        raise RuntimePreflightError("pinned runtime preflight bindings changed")
    adapter = next(
        (item for item in observation.adapters if item.adapter == runtime.adapter),
        None,
    )
    launch = resolve_runtime_launch_binding(runtime, platform=platform or sys.platform)
    if (
        adapter is None
        or adapter.installation_status != "installed"
        or launch.installation_status != "installed"
        or launch.resolved_command is None
        or adapter.resolved_runtime_command_sha256 != launch.content_sha256
        or decision.resolved_runtime_command_sha256 != launch.content_sha256
    ):
        raise RuntimePreflightError("pinned runtime launch target is unavailable")
    prefix = list(launch.resolved_command)
    if list(resolved_command[: len(prefix)]) != prefix:
        raise RuntimePreflightError("resolved spawn command differs from preflight")
