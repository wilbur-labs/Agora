"""Read-only AWS AI-DLC methodology migration preview derivation."""
from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path

from agora.protocol.hashing import canonical_sha256, seal_model_payload
from agora.protocol.methodology_migration import (
    MIGRATION_PREVIEW_CONSTRAINTS,
    MethodologyMigrationPreviewCheck,
    MethodologyMigrationPreviewDecision,
    MethodologyMigrationPreviewRequest,
    MigrationArtifactBinding,
    MigrationPreviewConstraint,
)
from agora.protocol.state_machines import TaskStatus

from .aws_aidlc import AWS_AIDLC_V2_3_SOURCE_GRAPH
from .aws_aidlc_activation import AWS_AIDLC_V2_3_ACTIVATION_DEFINITION
from .methodology import methodology_sha256
from .models import MethodologyMigrationStateSnapshot
from .protocol_context import RepositoryRevision
from .runtime import RuntimeCommand
from .runtime_capabilities import (
    runtime_command_sha256,
    runtime_registry_sha256,
)


MIGRATION_REQUEST_LIMIT = 1_000_000
MIGRATION_ARTIFACT_LIMIT = 16_000_000
MIGRATION_ARTIFACT_TOTAL_LIMIT = 64_000_000


def load_methodology_migration_request(
    path: Path,
) -> MethodologyMigrationPreviewRequest:
    """Load one strict bounded migration request without changing Task state."""

    try:
        if not path.is_file():
            raise ValueError("Methodology migration request must be a file")
        if path.stat().st_size > MIGRATION_REQUEST_LIMIT:
            raise ValueError("Methodology migration request exceeds 1 MiB")
        with path.open("rb") as handle:
            payload = handle.read(MIGRATION_REQUEST_LIMIT + 1)
        if len(payload) > MIGRATION_REQUEST_LIMIT:
            raise ValueError("Methodology migration request exceeds 1 MiB")
    except OSError as exc:
        raise ValueError("Methodology migration request is unavailable") from exc
    return MethodologyMigrationPreviewRequest.model_validate_json(payload)


def migration_budget_sha256(request: MethodologyMigrationPreviewRequest) -> str:
    return canonical_sha256(request.budget)


def migration_seed_artifacts_sha256(
    request: MethodologyMigrationPreviewRequest,
) -> str:
    payload = sorted(
        (
            seed.model_dump(mode="json")
            for seed in request.seed_artifacts
        ),
        key=lambda item: (
            item["consumer_stage_key"],
            item["artifact_id"],
            item["source_producer_stage_key"],
            item["path"],
        ),
    )
    return canonical_sha256(payload)


def observe_migration_artifacts(
    root: Path,
    artifacts: Iterable[MigrationArtifactBinding],
) -> dict[str, str | None]:
    """Hash bounded repository files without following paths outside the root."""

    resolved_root = root.resolve()
    observed: dict[str, str | None] = {}
    total_observed_bytes = 0
    for artifact in artifacts:
        if artifact.path in observed:
            continue
        try:
            candidate = (resolved_root / artifact.path).resolve(strict=True)
            candidate.relative_to(resolved_root)
            if not candidate.is_file():
                observed[artifact.path] = None
                continue
            before = candidate.stat()
            if before.st_size > MIGRATION_ARTIFACT_LIMIT:
                observed[artifact.path] = None
                continue
            total_observed_bytes += before.st_size
            if total_observed_bytes > MIGRATION_ARTIFACT_TOTAL_LIMIT:
                observed[artifact.path] = None
                continue
            digest = hashlib.sha256()
            with candidate.open("rb") as handle:
                while chunk := handle.read(64 * 1024):
                    digest.update(chunk)
            after = candidate.stat()
            if (
                after.st_size != before.st_size
                or after.st_mtime_ns != before.st_mtime_ns
            ):
                observed[artifact.path] = None
            else:
                observed[artifact.path] = digest.hexdigest()
        except (OSError, ValueError):
            observed[artifact.path] = None
    return observed


def derive_methodology_migration_preview(
    *,
    request: MethodologyMigrationPreviewRequest,
    snapshot: MethodologyMigrationStateSnapshot,
    repository: RepositoryRevision | None,
    runtimes: dict[str, RuntimeCommand],
    observed_artifact_sha256s: Mapping[str, str | None],
    generated_at: datetime | str,
) -> MethodologyMigrationPreviewDecision:
    """Derive a non-authoritative eligibility decision from exact current facts."""

    activation = AWS_AIDLC_V2_3_ACTIVATION_DEFINITION
    source_graph = AWS_AIDLC_V2_3_SOURCE_GRAPH
    checks: list[MethodologyMigrationPreviewCheck] = []

    def add(
        constraint: MigrationPreviewConstraint,
        satisfied: bool,
        passed_detail: str,
        failed_detail: str,
    ) -> None:
        checks.append(
            MethodologyMigrationPreviewCheck(
                constraint=constraint,
                satisfied=satisfied,
                detail=passed_detail if satisfied else failed_detail,
            )
        )

    control_task = snapshot.control_task
    task_binding = (
        request.task_id == snapshot.task.task_id
        and request.project_id == snapshot.task.project_id
        and request.expected_task_version == snapshot.task.version
        and request.plan_id == snapshot.plan.plan_id
        and request.expected_plan_version == snapshot.plan.version
        and snapshot.plan.task_id == snapshot.task.task_id
        and snapshot.plan.project_id == snapshot.task.project_id
        and control_task is not None
        and request.expected_control_task_version == control_task.version
        and request.expected_task_status == control_task.status
        and control_task.task_id == snapshot.task.task_id
        and control_task.project_id == snapshot.task.project_id
    )
    add(
        "task_binding",
        task_binding,
        "Task, authoritative Task state, and Plan optimistic bindings match.",
        "Task, authoritative Task state, or Plan optimistic binding is stale.",
    )

    current_methodology_hash = methodology_sha256(snapshot.current_methodology)
    inventory = snapshot.stage_inventory
    current_methodology_binding = (
        request.current_methodology_id == snapshot.plan.methodology_id
        and request.current_methodology_version
        == snapshot.plan.methodology_version
        and request.current_methodology_sha256
        == snapshot.plan.methodology_sha256
        and current_methodology_hash == snapshot.plan.methodology_sha256
        and inventory is not None
        and inventory.task_id == snapshot.task.task_id
        and inventory.project_id == snapshot.task.project_id
        and inventory.plan_id == snapshot.plan.plan_id
        and inventory.methodology_id == snapshot.plan.methodology_id
        and inventory.methodology_version == snapshot.plan.methodology_version
        and inventory.methodology_sha256 == snapshot.plan.methodology_sha256
    )
    add(
        "current_methodology_binding",
        current_methodology_binding,
        "Current Plan and sealed Stage inventory retain one exact methodology.",
        "Current Plan methodology or sealed Stage inventory binding is unavailable or inconsistent.",
    )

    observed_repository = (
        None
        if repository is None
        else request.repository.__class__(
            repository_id=repository.repository_id,
            ref=repository.ref,
            commit_sha=repository.commit_sha,
        )
    )
    repository_binding = (
        observed_repository is not None
        and observed_repository == request.repository
    )
    add(
        "repository_binding",
        repository_binding,
        "Clean repository, ref, and commit match the migration request.",
        "Repository is unavailable, dirty, or differs from the migration request.",
    )

    target_source_binding = (
        request.target_activation_id == activation.activation_id
        and request.target_methodology_id == activation.methodology_id
        and request.target_methodology_version
        == activation.methodology_version
        and request.target_source_graph_sha256
        == activation.source_graph_sha256
        and request.target_source_graph_sha256 == source_graph.content_sha256
        and request.target_activation_definition_sha256
        == activation.content_sha256
    )
    add(
        "target_source_binding",
        target_source_binding,
        "Target source graph and activation definition hashes match the pinned baseline.",
        "Target source graph or activation definition binding does not match the pinned baseline.",
    )

    scope_keys = {scope.scope_key for scope in source_graph.scopes}
    scope_selection = request.selected_scope in scope_keys
    add(
        "scope_selection",
        scope_selection,
        "Selected scope is one of the nine source-bound AWS AI-DLC scopes.",
        "Selected scope is not present in the pinned AWS AI-DLC source graph.",
    )

    expected_seed_keys = {
        (
            seed.consumer_stage_key,
            seed.artifact_id,
            seed.source_producer_stage_key,
        )
        for seed in activation.scope_seed_requirements
        if seed.scope_key == request.selected_scope
    }
    actual_seed_keys = {
        (
            seed.consumer_stage_key,
            seed.artifact_id,
            seed.source_producer_stage_key,
        )
        for seed in request.seed_artifacts
    }
    seed_bindings_match = (
        scope_selection
        and len(request.seed_artifacts) == len(expected_seed_keys)
        and actual_seed_keys == expected_seed_keys
    )
    for seed in request.seed_artifacts:
        seed_bindings_match = (
            seed_bindings_match
            and seed.repository_id == request.repository.repository_id
            and seed.ref == request.repository.ref
            and seed.commit_sha == request.repository.commit_sha
            and observed_artifact_sha256s.get(seed.path) == seed.sha256
        )
    add(
        "scope_seed_artifacts",
        seed_bindings_match,
        "Hash-bound Task seed files exactly close the selected scope input gaps.",
        "Task seed set is missing, extra, stale, unreadable, or not bound to the selected scope.",
    )

    expected_runtime_pins = {
        "production_execution": activation.runtime_policy.production_runtime,
        "independent_correctness": (
            activation.runtime_policy.independent_correctness_runtime
        ),
        "methodology_stewardship": (
            activation.runtime_policy.methodology_steward_runtime
        ),
    }
    actual_runtime_pins = {
        pin.responsibility: pin for pin in request.runtime_pins
    }
    runtime_pins_match = (
        request.runtime_registry_sha256 == runtime_registry_sha256(runtimes)
        and set(actual_runtime_pins) == set(expected_runtime_pins)
    )
    for responsibility, expected_runtime in expected_runtime_pins.items():
        pin = actual_runtime_pins.get(responsibility)
        runtime = runtimes.get(expected_runtime)
        runtime_pins_match = (
            runtime_pins_match
            and pin is not None
            and runtime is not None
            and pin.runtime == expected_runtime
            and pin.runtime_command_sha256
            == runtime_command_sha256(runtime)
        )
    add(
        "runtime_pins",
        runtime_pins_match,
        "Codex, Claude, and Kiro responsibilities match the current configured command hashes.",
        "Required runtime responsibility, registry, or configured command hash does not match.",
    )

    reservations = {
        reservation.runtime: reservation
        for reservation in request.budget.protected_runtime_reservations
    }
    required_runtimes = set(expected_runtime_pins.values())
    selected_source_stages = [
        stage
        for stage in source_graph.stages
        if request.selected_scope in stage.scopes
    ]
    stage_allocations = {
        allocation.source_stage_key: allocation
        for allocation in request.budget.stage_allocations
    }
    expected_stage_keys = {
        stage.stage_key
        for stage in selected_source_stages
    }
    stage_instances_match = (
        set(stage_allocations) == expected_stage_keys
        and all(
            stage_allocations[stage.stage_key].instance_count
            == (
                request.budget.unit_of_work_count
                if stage.for_each_artifact == "unit-of-work"
                else 1
            )
            for stage in selected_source_stages
        )
    )
    allocated_stage_tokens = sum(
        allocation.token_allocation_per_instance * allocation.instance_count
        for allocation in stage_allocations.values()
    )
    protected_runtime_tokens = sum(
        reservation.token_reservation
        for reservation in reservations.values()
    )
    budget_matches = (
        request.budget.task_token_budget == snapshot.plan.total_token_budget
        and request.budget.task_cost_budget_usd
        == snapshot.plan.total_cost_budget_usd
        and set(reservations) == required_runtimes
        and stage_instances_match
        and allocated_stage_tokens + protected_runtime_tokens
        <= request.budget.task_token_budget
    )
    if request.budget.task_cost_budget_usd is None:
        budget_matches = (
            budget_matches
            and all(
                allocation.cost_allocation_per_instance_usd is None
                and allocation.max_run_cost_reservation_per_instance_usd
                is None
                for allocation in stage_allocations.values()
            )
            and all(
                reservation.cost_reservation_usd is None
                for reservation in reservations.values()
            )
        )
    else:
        stage_cost_allocations = [
            allocation.cost_allocation_per_instance_usd
            for allocation in stage_allocations.values()
        ]
        runtime_cost_reservations = [
            reservation.cost_reservation_usd
            for reservation in reservations.values()
        ]
        budget_matches = (
            budget_matches
            and all(value is not None for value in stage_cost_allocations)
            and all(value is not None for value in runtime_cost_reservations)
            and sum(
                (allocation.cost_allocation_per_instance_usd or 0)
                * allocation.instance_count
                for allocation in stage_allocations.values()
            )
            + sum(value or 0 for value in runtime_cost_reservations)
            <= request.budget.task_cost_budget_usd
        )
    add(
        "budget",
        budget_matches,
        "Every selected Stage instance and all three runtime families have explicit reservations within the current Task envelope.",
        "Task envelope, selected Stage instance allocation, or protected runtime reservation is missing, stale, or over budget.",
    )

    budget_sha256 = migration_budget_sha256(request)
    seed_artifacts_sha256 = migration_seed_artifacts_sha256(request)
    gate = request.human_gate
    human_gate_matches = (
        gate is not None
        and gate.migration_strategy == request.migration_strategy
        and gate.project_id == request.project_id
        and gate.task_id == request.task_id
        and gate.expected_task_version == request.expected_task_version
        and gate.expected_control_task_version
        == request.expected_control_task_version
        and gate.expected_task_status == request.expected_task_status
        and gate.plan_id == request.plan_id
        and gate.plan_version == request.expected_plan_version
        and gate.current_methodology_id == request.current_methodology_id
        and gate.current_methodology_version
        == request.current_methodology_version
        and gate.current_methodology_sha256
        == request.current_methodology_sha256
        and gate.repository == request.repository
        and gate.target_activation_id == request.target_activation_id
        and gate.target_methodology_id == request.target_methodology_id
        and gate.target_methodology_version
        == request.target_methodology_version
        and gate.target_source_graph_sha256
        == request.target_source_graph_sha256
        and gate.target_activation_definition_sha256
        == request.target_activation_definition_sha256
        and gate.selected_scope == request.selected_scope
        and gate.runtime_registry_sha256 == request.runtime_registry_sha256
        and gate.budget_sha256 == budget_sha256
        and gate.seed_artifacts_sha256 == seed_artifacts_sha256
        and observed_artifact_sha256s.get(gate.migration_artifact.path)
        == gate.migration_artifact.sha256
    )
    add(
        "human_gate",
        human_gate_matches,
        "Explicit human Gate assertion matches the exact migration artifact and proposal bindings.",
        "Explicit human Gate assertion is absent, stale, unreadable, or bound to different inputs.",
    )

    task_quiescence = (
        control_task is not None
        and control_task.status
        in {
            TaskStatus.READY,
            TaskStatus.BLOCKED,
            TaskStatus.NEEDS_REVIEW,
            TaskStatus.COMPLETED,
        }
        and snapshot.active_runs == 0
        and snapshot.active_consultations == 0
        and snapshot.unsettled_protocol_runs == 0
    )
    add(
        "task_quiescence",
        task_quiescence,
        "Task has no active or unsettled execution and is eligible for successor planning.",
        "Task is terminally failed/cancelled, actively executing, or has an unsettled protocol Run.",
    )

    if tuple(check.constraint for check in checks) != MIGRATION_PREVIEW_CONSTRAINTS:
        raise AssertionError("migration preview check order drifted")
    blockers = [
        check.constraint
        for check in checks
        if not check.satisfied
    ]
    payload = {
        "schema_version": "1.0",
        "decision_id": f"migration-preview-{request.content_sha256[:20]}",
        "generated_at": generated_at,
        "request_id": request.request_id,
        "request_sha256": request.content_sha256,
        "migration_strategy": request.migration_strategy,
        "project_id": snapshot.task.project_id,
        "task_id": snapshot.task.task_id,
        "observed_task_version": snapshot.task.version,
        "observed_control_task_version": (
            control_task.version
            if control_task is not None
            else None
        ),
        "observed_task_status": (
            control_task.status.value
            if control_task is not None
            else None
        ),
        "plan_id": snapshot.plan.plan_id,
        "observed_plan_version": snapshot.plan.version,
        "current_methodology_id": snapshot.plan.methodology_id,
        "current_methodology_version": snapshot.plan.methodology_version,
        "current_methodology_sha256": snapshot.plan.methodology_sha256,
        "observed_repository": (
            observed_repository.model_dump(mode="json")
            if observed_repository is not None
            else None
        ),
        "target_activation_id": activation.activation_id,
        "target_methodology_id": activation.methodology_id,
        "target_methodology_version": activation.methodology_version,
        "target_source_graph_sha256": activation.source_graph_sha256,
        "target_activation_definition_sha256": activation.content_sha256,
        "selected_scope": request.selected_scope,
        "active_runs": snapshot.active_runs,
        "active_consultations": snapshot.active_consultations,
        "unsettled_protocol_runs": snapshot.unsettled_protocol_runs,
        "checks": [check.model_dump(mode="json") for check in checks],
        "blockers": blockers,
        "eligible": not blockers,
        "preview_only": True,
        "state_mutated": False,
        "plan_mutated": False,
        "inventory_mutated": False,
        "runtime_spawned": False,
        "migration_executed": False,
        "routing_authority": False,
        "dispatch_authority": False,
        "migration_authority": False,
    }
    return MethodologyMigrationPreviewDecision.model_validate(
        seal_model_payload(MethodologyMigrationPreviewDecision, payload)
    )
