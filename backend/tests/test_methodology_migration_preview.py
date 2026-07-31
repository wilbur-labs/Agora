from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier

import pytest
from pydantic import ValidationError

from agora.control_plane.auth import ControlPrincipal
from agora.orchestration import cli as orchestration_cli
from agora.orchestration.aws_aidlc import AWS_AIDLC_V2_3_SOURCE_GRAPH
from agora.orchestration.aws_aidlc_activation import (
    AWS_AIDLC_V2_3_ACTIVATION_DEFINITION,
)
from agora.orchestration.contracts import load_task_contract
from agora.orchestration.methodology_migration import (
    load_methodology_migration_request,
    migration_budget_sha256,
    migration_seed_artifacts_sha256,
)
from agora.orchestration.methodology_execution_contract import (
    MethodologyExecutionSnapshot,
    _bind_selected_producer_instances,
    build_methodology_execution_contract,
)
from agora.orchestration.methodology_route_activation import (
    load_methodology_route_activation_request,
)
from agora.orchestration.methodology_run_claim import (
    _context_entry,
    load_methodology_run_claim_request,
)
from agora.orchestration.models import PlanState
from agora.orchestration.protocol_context import RepositoryRevision
from agora.orchestration.runtime import RuntimeCommand
from agora.orchestration.runtime_capabilities import (
    runtime_command_sha256,
    runtime_registry_sha256,
)
from agora.orchestration.service import TaskOrchestrationService
from agora.orchestration.store import (
    OrchestrationConflictError,
    OrchestrationValidationError,
)
from agora.projects import ProjectRegistry
from agora.protocol.hashing import seal_model_payload
from agora.protocol.methodology_execution import MethodologyExecutionContract
from agora.protocol.methodology_migration import (
    AuthenticatedMethodologyMigrationGate,
    MethodologyMigrationActivationReceipt,
    MethodologyMigrationGateAssertion,
    MethodologyMigrationPreviewDecision,
    MethodologyMigrationPreviewRequest,
)
from agora.protocol.methodology_route_activation import (
    MethodologyRouteActivationReceipt,
    MethodologyRouteActivationRequest,
)
from agora.protocol.methodology_run_claim import (
    MethodologyRunClaimReceipt,
    MethodologyRunClaimRequest,
)
from agora.protocol.schema_registry import SCHEMA_MODELS
from agora.tasks.store import TaskStore


CONTRACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "examples"
    / "bounded-control-plane-api-task-contract.json"
)
REVISION = RepositoryRevision(
    repository_id="alpha",
    ref="refs/heads/main",
    commit_sha="a" * 40,
)
FIXED_TIME = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


def _database_dump(tasks: TaskStore) -> str:
    with sqlite3.connect(tasks.db_path) as db:
        return "\n".join(db.iterdump())


def _system(tmp_path, *, total_cost_budget_usd=12):
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    proposal_path = root / "migration" / "proposal.json"
    proposal_path.parent.mkdir()
    proposal_path.write_text(
        json.dumps({"proposal": "migrate to AWS AI-DLC v2.3.0"}),
        encoding="utf-8",
    )
    projects = ProjectRegistry(
        {
            "projects": {
                "registry_path": str(tmp_path / "projects.yaml"),
                "default": "alpha",
                "projects": {
                    "alpha": {
                        "name": "Alpha",
                        "root": str(root),
                        "workspaces": {},
                    }
                },
            }
        },
        project_root=tmp_path,
    )
    tasks = TaskStore(tmp_path / "agora.db")
    runtimes = {
        name: RuntimeCommand(
            adapter=name,
            command_template=(sys.executable, name, "{prompt}"),
        )
        for name in ("codex", "claude", "kiro")
    }
    service = TaskOrchestrationService(
        tasks,
        projects,
        runtimes,
        revision_resolver=lambda _root, _repository_id: REVISION,
    )
    contract = load_task_contract(CONTRACT_PATH)
    task = service.create(
        project_id="alpha",
        title=contract.title,
        description=contract.goal,
        total_token_budget=30_000,
        total_cost_budget_usd=total_cost_budget_usd,
        contract=contract,
    )
    return tasks, service, task, root, proposal_path


def _request(
    service: TaskOrchestrationService,
    task_id: str,
    root: Path,
    proposal_path: Path,
    *,
    scope: str = "enterprise",
    include_human_gate: bool = True,
    include_scope_seeds: bool = True,
) -> MethodologyMigrationPreviewRequest:
    snapshot = service.store.methodology_migration_snapshot(task_id)
    activation = AWS_AIDLC_V2_3_ACTIVATION_DEFINITION
    seed_payloads = []
    if include_scope_seeds:
        for index, seed in enumerate(
            item
            for item in activation.scope_seed_requirements
            if item.scope_key == scope
        ):
            relative_path = (
                Path("migration")
                / "seeds"
                / f"{index:03d}-{seed.consumer_stage_key}-{seed.artifact_id}.json"
            )
            absolute_path = root / relative_path
            absolute_path.parent.mkdir(parents=True, exist_ok=True)
            content = json.dumps(
                {
                    "consumer": seed.consumer_stage_key,
                    "artifact": seed.artifact_id,
                    "producer": seed.source_producer_stage_key,
                },
                sort_keys=True,
            )
            absolute_path.write_text(content, encoding="utf-8")
            seed_payloads.append(
                {
                    "repository_id": REVISION.repository_id,
                    "ref": REVISION.ref,
                    "commit_sha": REVISION.commit_sha,
                    "path": relative_path.as_posix(),
                    "sha256": hashlib.sha256(
                        content.encode("utf-8")
                    ).hexdigest(),
                    "consumer_stage_key": seed.consumer_stage_key,
                    "artifact_id": seed.artifact_id,
                    "source_producer_stage_key": (
                        seed.source_producer_stage_key
                    ),
                }
            )

    registry_sha256 = runtime_registry_sha256(service.runtimes)
    has_cost_budget = snapshot.plan.total_cost_budget_usd is not None
    unit_of_work_count = 2
    selected_stages = [
        stage
        for stage in AWS_AIDLC_V2_3_SOURCE_GRAPH.stages
        if scope in stage.scopes
    ]
    payload = {
        "schema_version": "1.0",
        "request_id": "migration-request-1",
        "migration_strategy": "successor_task",
        "project_id": snapshot.task.project_id,
        "task_id": snapshot.task.task_id,
        "expected_task_version": snapshot.task.version,
        "expected_control_task_version": snapshot.control_task.version,
        "expected_task_status": snapshot.control_task.status.value,
        "plan_id": snapshot.plan.plan_id,
        "expected_plan_version": snapshot.plan.version,
        "current_methodology_id": snapshot.plan.methodology_id,
        "current_methodology_version": snapshot.plan.methodology_version,
        "current_methodology_sha256": snapshot.plan.methodology_sha256,
        "repository": REVISION.model_dump(mode="json"),
        "target_activation_id": activation.activation_id,
        "target_methodology_id": activation.methodology_id,
        "target_methodology_version": activation.methodology_version,
        "target_source_graph_sha256": activation.source_graph_sha256,
        "target_activation_definition_sha256": activation.content_sha256,
        "selected_scope": scope,
        "seed_artifacts": seed_payloads,
        "runtime_registry_sha256": registry_sha256,
        "runtime_pins": [
            {
                "responsibility": responsibility,
                "runtime": runtime,
                "runtime_command_sha256": runtime_command_sha256(
                    service.runtimes[runtime]
                ),
            }
            for responsibility, runtime in (
                ("production_execution", "codex"),
                ("independent_correctness", "claude"),
                ("methodology_stewardship", "kiro"),
            )
        ],
        "budget": {
            "task_token_budget": snapshot.plan.total_token_budget,
            "task_cost_budget_usd": snapshot.plan.total_cost_budget_usd,
            "unit_of_work_count": unit_of_work_count,
            "stage_allocations": [
                {
                    "source_stage_key": stage.stage_key,
                    "instance_count": (
                        unit_of_work_count
                        if stage.for_each_artifact == "unit-of-work"
                        else 1
                    ),
                    "token_allocation_per_instance": 500,
                    "max_run_token_reservation_per_instance": 250,
                    "cost_allocation_per_instance_usd": (
                        0.2 if has_cost_budget else None
                    ),
                    "max_run_cost_reservation_per_instance_usd": (
                        0.1 if has_cost_budget else None
                    ),
                }
                for stage in selected_stages
            ],
            "protected_runtime_reservations": [
                {
                    "runtime": runtime,
                    "token_reservation": 1_000,
                    "cost_reservation_usd": 1 if has_cost_budget else None,
                }
                for runtime in ("codex", "claude", "kiro")
            ],
        },
        "human_gate": None,
    }
    without_gate = MethodologyMigrationPreviewRequest.model_validate(
        seal_model_payload(MethodologyMigrationPreviewRequest, payload)
    )
    if include_human_gate:
        gate_payload = {
            "schema_version": "1.0",
            "assertion_id": "migration-gate-assertion-1",
            "gate_key": "methodology-migration",
            "migration_strategy": payload["migration_strategy"],
            "human_approved": True,
            "approved_by": "user",
            "approved_at": FIXED_TIME.isoformat(),
            "project_id": payload["project_id"],
            "task_id": payload["task_id"],
            "expected_task_version": payload["expected_task_version"],
            "expected_control_task_version": payload[
                "expected_control_task_version"
            ],
            "expected_task_status": payload["expected_task_status"],
            "plan_id": payload["plan_id"],
            "plan_version": payload["expected_plan_version"],
            "current_methodology_id": payload["current_methodology_id"],
            "current_methodology_version": payload[
                "current_methodology_version"
            ],
            "current_methodology_sha256": payload[
                "current_methodology_sha256"
            ],
            "repository": payload["repository"],
            "migration_artifact": {
                **payload["repository"],
                "path": proposal_path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(
                    proposal_path.read_bytes()
                ).hexdigest(),
            },
            "target_activation_id": payload["target_activation_id"],
            "target_methodology_id": payload["target_methodology_id"],
            "target_methodology_version": payload[
                "target_methodology_version"
            ],
            "target_source_graph_sha256": payload[
                "target_source_graph_sha256"
            ],
            "target_activation_definition_sha256": payload[
                "target_activation_definition_sha256"
            ],
            "selected_scope": payload["selected_scope"],
            "runtime_registry_sha256": registry_sha256,
            "budget_sha256": migration_budget_sha256(without_gate),
            "seed_artifacts_sha256": migration_seed_artifacts_sha256(
                without_gate
            ),
        }
        gate = MethodologyMigrationGateAssertion.model_validate(
            seal_model_payload(MethodologyMigrationGateAssertion, gate_payload)
        )
        payload["human_gate"] = gate.model_dump(mode="json")
    return MethodologyMigrationPreviewRequest.model_validate(
        seal_model_payload(MethodologyMigrationPreviewRequest, payload)
    )


def _reseal_request(payload: dict) -> MethodologyMigrationPreviewRequest:
    return MethodologyMigrationPreviewRequest.model_validate(
        seal_model_payload(MethodologyMigrationPreviewRequest, payload)
    )


def test_methodology_migration_preview_is_eligible_read_only_and_non_authoritative(
    tmp_path,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    request = _request(service, task.task_id, root, proposal_path)
    before = _database_dump(tasks)

    decision = service.preview_methodology_migration(task.task_id, request)

    assert decision.eligible is True
    assert decision.blockers == []
    assert decision.migration_strategy == "successor_task"
    assert decision.observed_task_version == request.expected_task_version
    assert (
        decision.observed_control_task_version
        == request.expected_control_task_version
    )
    assert decision.observed_plan_version == request.expected_plan_version
    assert decision.preview_only is True
    assert decision.state_mutated is False
    assert decision.plan_mutated is False
    assert decision.inventory_mutated is False
    assert decision.runtime_spawned is False
    assert decision.migration_executed is False
    assert decision.routing_authority is False
    assert decision.dispatch_authority is False
    assert decision.migration_authority is False
    assert _database_dump(tasks) == before


def test_methodology_migration_preview_blocks_missing_human_gate(tmp_path):
    _, service, task, root, proposal_path = _system(tmp_path)
    request = _request(
        service,
        task.task_id,
        root,
        proposal_path,
        include_human_gate=False,
    )

    decision = service.preview_methodology_migration(task.task_id, request)

    assert decision.eligible is False
    assert decision.blockers == ["human_gate"]


def test_methodology_migration_preview_accepts_explicit_token_only_budget(
    tmp_path,
):
    _, service, task, root, proposal_path = _system(
        tmp_path,
        total_cost_budget_usd=None,
    )
    request = _request(service, task.task_id, root, proposal_path)

    decision = service.preview_methodology_migration(task.task_id, request)

    assert decision.eligible is True
    assert all(
        allocation.cost_allocation_per_instance_usd is None
        and allocation.max_run_cost_reservation_per_instance_usd is None
        for allocation in request.budget.stage_allocations
    )
    assert all(
        reservation.cost_reservation_usd is None
        for reservation in request.budget.protected_runtime_reservations
    )


def test_methodology_migration_preview_blocks_stale_task_and_plan_binding(tmp_path):
    _, service, task, root, proposal_path = _system(tmp_path)
    request = _request(service, task.task_id, root, proposal_path)
    payload = request.model_dump(mode="json")
    payload["expected_task_version"] += 1
    payload["expected_plan_version"] += 1
    request = _reseal_request(payload)

    decision = service.preview_methodology_migration(task.task_id, request)

    assert decision.eligible is False
    assert "task_binding" in decision.blockers
    assert "human_gate" in decision.blockers


def test_methodology_migration_preview_blocks_control_task_lock_drift(tmp_path):
    _, service, task, root, proposal_path = _system(tmp_path)
    request = _request(service, task.task_id, root, proposal_path)
    payload = request.model_dump(mode="json")
    payload["expected_control_task_version"] += 1
    payload["expected_task_status"] = "completed"
    request = _reseal_request(payload)

    decision = service.preview_methodology_migration(task.task_id, request)

    assert "task_binding" in decision.blockers
    assert "human_gate" in decision.blockers


def test_methodology_migration_preview_blocks_current_methodology_drift(tmp_path):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            UPDATE orchestration_plans
            SET methodology_sha256 = ?
            WHERE task_id = ?
            """,
            ("0" * 64, task.task_id),
        )
    request = _request(service, task.task_id, root, proposal_path)

    decision = service.preview_methodology_migration(task.task_id, request)

    assert decision.blockers == ["current_methodology_binding"]


def test_methodology_migration_preview_blocks_unavailable_repository(tmp_path):
    _, service, task, root, proposal_path = _system(tmp_path)
    request = _request(service, task.task_id, root, proposal_path)

    def unavailable(_root, _repository_id):
        raise ValueError("unavailable")

    service.revision_resolver = unavailable
    decision = service.preview_methodology_migration(task.task_id, request)

    assert decision.eligible is False
    assert decision.observed_repository is None
    assert "repository_binding" in decision.blockers


def test_methodology_migration_preview_blocks_different_repository_revision(
    tmp_path,
):
    _, service, task, root, proposal_path = _system(tmp_path)
    request = _request(service, task.task_id, root, proposal_path)
    service.revision_resolver = lambda _root, _repository_id: RepositoryRevision(
        repository_id=REVISION.repository_id,
        ref=REVISION.ref,
        commit_sha="b" * 40,
    )

    decision = service.preview_methodology_migration(task.task_id, request)

    assert decision.blockers == ["repository_binding"]


def test_methodology_migration_preview_requires_exact_scope_seed_files(tmp_path):
    _, service, task, root, proposal_path = _system(tmp_path)
    missing = _request(
        service,
        task.task_id,
        root,
        proposal_path,
        scope="bugfix",
        include_scope_seeds=False,
    )
    missing_decision = service.preview_methodology_migration(
        task.task_id,
        missing,
    )
    assert missing_decision.blockers == ["scope_seed_artifacts"]

    request = _request(
        service,
        task.task_id,
        root,
        proposal_path,
        scope="bugfix",
    )
    seed_path = root / request.seed_artifacts[0].path
    seed_path.write_text("tampered", encoding="utf-8")
    tampered = service.preview_methodology_migration(task.task_id, request)
    assert tampered.blockers == ["scope_seed_artifacts"]


def test_methodology_migration_preview_blocks_runtime_and_budget_drift(tmp_path):
    _, service, task, root, proposal_path = _system(tmp_path)
    request = _request(service, task.task_id, root, proposal_path)
    payload = request.model_dump(mode="json")
    payload["runtime_pins"][0]["runtime_command_sha256"] = "0" * 64
    for reservation in payload["budget"]["protected_runtime_reservations"]:
        reservation["token_reservation"] = 15_000
    request = _reseal_request(payload)

    decision = service.preview_methodology_migration(task.task_id, request)

    assert "runtime_pins" in decision.blockers
    assert "budget" in decision.blockers
    assert "human_gate" in decision.blockers


def test_methodology_migration_preview_requires_exact_stage_instance_budgets(
    tmp_path,
):
    _, service, task, root, proposal_path = _system(tmp_path)
    request = _request(service, task.task_id, root, proposal_path)
    payload = request.model_dump(mode="json")
    payload["budget"]["stage_allocations"].pop()
    payload["budget"]["stage_allocations"][0]["instance_count"] += 1
    request = _reseal_request(payload)

    decision = service.preview_methodology_migration(task.task_id, request)

    assert "budget" in decision.blockers
    assert "human_gate" in decision.blockers


def test_methodology_migration_preview_blocks_target_scope_and_quiescence_drift(
    tmp_path,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            UPDATE control_tasks
            SET status = 'cancelled', version = version + 1
            WHERE task_id = ?
            """,
            (task.task_id,),
        )
    request = _request(service, task.task_id, root, proposal_path)
    payload = request.model_dump(mode="json")
    payload["target_source_graph_sha256"] = "0" * 64
    payload["selected_scope"] = "unknown-scope"
    request = _reseal_request(payload)

    decision = service.preview_methodology_migration(task.task_id, request)

    assert "target_source_binding" in decision.blockers
    assert "scope_selection" in decision.blockers
    assert "scope_seed_artifacts" in decision.blockers
    assert "budget" in decision.blockers
    assert "human_gate" in decision.blockers
    assert "task_quiescence" in decision.blockers


def test_methodology_migration_preview_blocks_stale_gate_artifact(tmp_path):
    _, service, task, root, proposal_path = _system(tmp_path)
    request = _request(service, task.task_id, root, proposal_path)
    proposal_path.write_text("changed after approval", encoding="utf-8")

    decision = service.preview_methodology_migration(task.task_id, request)

    assert decision.eligible is False
    assert decision.blockers == ["human_gate"]


def test_methodology_migration_preview_cli_returns_json_without_writes(
    tmp_path,
    monkeypatch,
    capsys,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    request = _request(service, task.task_id, root, proposal_path)
    request_path = tmp_path / "migration-request.json"
    request_path.write_text(
        request.model_dump_json(indent=2),
        encoding="utf-8",
    )
    before = _database_dump(tasks)
    monkeypatch.setattr(orchestration_cli, "build_service", lambda: service)

    result = orchestration_cli.main(
        [
            "migration-preview",
            task.task_id,
            "--request",
            str(request_path),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["eligible"] is True
    assert payload["migration_executed"] is False
    assert _database_dump(tasks) == before


def test_methodology_migration_preview_cli_returns_two_for_blocked_decision(
    tmp_path,
    monkeypatch,
    capsys,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    request = _request(
        service,
        task.task_id,
        root,
        proposal_path,
        include_human_gate=False,
    )
    request_path = tmp_path / "blocked-migration-request.json"
    request_path.write_text(
        request.model_dump_json(indent=2),
        encoding="utf-8",
    )
    before = _database_dump(tasks)
    monkeypatch.setattr(orchestration_cli, "build_service", lambda: service)

    result = orchestration_cli.main(
        [
            "migration-preview",
            task.task_id,
            "--request",
            str(request_path),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 2
    assert payload["eligible"] is False
    assert payload["blockers"] == ["human_gate"]
    assert _database_dump(tasks) == before


def test_methodology_migration_request_loader_and_hash_fail_closed(tmp_path):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    del tasks
    request = _request(service, task.task_id, root, proposal_path)
    request_path = tmp_path / "request.json"
    request_path.write_text(request.model_dump_json(), encoding="utf-8")

    assert load_methodology_migration_request(request_path) == request

    payload = request.model_dump(mode="json")
    payload["content_sha256"] = "0" * 64
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValidationError, match="content_sha256"):
        load_methodology_migration_request(request_path)


def test_methodology_migration_preview_decision_rejects_inconsistent_blockers(
    tmp_path,
):
    _, service, task, root, proposal_path = _system(tmp_path)
    request = _request(service, task.task_id, root, proposal_path)
    decision = service.preview_methodology_migration(task.task_id, request)
    payload = decision.model_dump(mode="json")
    payload["blockers"] = ["budget"]

    with pytest.raises(ValidationError, match="blockers"):
        MethodologyMigrationPreviewDecision.model_validate(
            seal_model_payload(MethodologyMigrationPreviewDecision, payload)
        )


def test_methodology_migration_preview_schemas_are_registered():
    assert (
        SCHEMA_MODELS["methodology-migration-preview-request"]
        is MethodologyMigrationPreviewRequest
    )
    assert (
        SCHEMA_MODELS["methodology-migration-preview-decision"]
        is MethodologyMigrationPreviewDecision
    )
    assert len(AWS_AIDLC_V2_3_SOURCE_GRAPH.scopes) == 9


def _migration_principal(
    *,
    principal_id: str = "user",
    permissions: frozenset[str] = frozenset({"control_plane.approve"}),
    projects: frozenset[str] = frozenset({"alpha"}),
) -> ControlPrincipal:
    return ControlPrincipal(
        principal_id=principal_id,
        permissions=permissions,
        projects=projects,
    )


def _migration_row_count(tasks: TaskStore) -> int:
    with sqlite3.connect(tasks.db_path) as db:
        return int(
            db.execute(
                "SELECT COUNT(*) FROM orchestration_methodology_migrations"
            ).fetchone()[0]
        )


def test_methodology_migration_activation_atomically_creates_sealed_successor(
    tmp_path,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    request = _request(service, task.task_id, root, proposal_path)
    source_manifest = tasks.get(task.task_id)
    source_events = tasks.events(task.task_id)

    receipt = service.activate_methodology_migration(
        task.task_id,
        request,
        principal=_migration_principal(),
    )

    assert isinstance(receipt, MethodologyMigrationActivationReceipt)
    assert receipt.recheck_decision.eligible is True
    assert receipt.authenticated_gate.authenticated_principal_id == "user"
    assert receipt.source_task_id == task.task_id
    assert receipt.successor_task_id != task.task_id
    assert receipt.source_task_preserved is True
    assert receipt.migration_gate_persisted is True
    assert receipt.successor_plan_sealed is True
    assert receipt.successor_inventory_sealed is True
    assert receipt.route_activated is False
    assert receipt.runtime_spawned is False
    assert receipt.dispatch_authority is False
    assert tasks.get(task.task_id) == source_manifest
    assert tasks.events(task.task_id) == source_events

    successor = tasks.get(receipt.successor_task_id)
    assert successor is not None
    assert successor.kind == "aws_aidlc_successor"
    assert successor.primary_agent == "codex"
    assert successor.reviewers == ["claude", "kiro"]
    assert successor.metadata["methodology_predecessor_task_id"] == task.task_id
    assert successor.metadata["methodology_dispatch_authority"] is False
    assert (
        successor.metadata["methodology_activation_sha256"]
        == AWS_AIDLC_V2_3_ACTIVATION_DEFINITION.content_sha256
    )

    status = service.status(successor.task_id)
    inventory = service.control_plane.get_stage_inventory(successor.task_id)
    route = service.control_plane.get_stage_route(successor.task_id)
    expected_stage_count = sum(
        allocation.instance_count
        for allocation in request.budget.stage_allocations
    )
    assert status.plan.state == PlanState.READY_FOR_IMPLEMENTATION
    assert status.plan.methodology_sha256 == request.target_activation_definition_sha256
    assert len(status.stages) == expected_stage_count
    assert inventory is not None
    assert inventory.content_sha256 == receipt.successor_inventory_sha256
    assert inventory.methodology_version == "2.3.0"
    assert sum(len(group.stages) for group in inventory.groups) == expected_stage_count
    assert route is not None
    assert route.stage_status is None
    assert route.runnable is False
    assert service.store.runs(status.plan.plan_id) == []
    resumed = service.resume(successor.task_id)
    resumed_route = service.control_plane.get_stage_route(successor.task_id)
    assert resumed.plan.state == PlanState.READY_FOR_IMPLEMENTATION
    assert resumed_route is not None
    assert resumed_route.stage_status is None
    with sqlite3.connect(tasks.db_path) as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM control_stages WHERE task_id = ?",
                (successor.task_id,),
            ).fetchone()[0]
            == 0
        )
        assert (
            db.execute(
                "SELECT COUNT(*) FROM control_gates WHERE task_id = ?",
                (successor.task_id,),
            ).fetchone()[0]
            == 0
        )

    allocation_by_source = {
        allocation.source_stage_key: allocation
        for allocation in request.budget.stage_allocations
    }
    for stage in status.stages:
        source_key = stage.stage_key.partition("-unit-")[0]
        assert stage.role == "production_execution"
        assert stage.adapter == "codex"
        assert (
            stage.token_budget
            == allocation_by_source[source_key].token_allocation_per_instance
        )
    assert _migration_row_count(tasks) == 1
    with sqlite3.connect(tasks.db_path) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            """
            SELECT * FROM orchestration_methodology_migrations
            WHERE request_id = ?
            """,
            (request.request_id,),
        ).fetchone()
        assert row is not None
        gate = AuthenticatedMethodologyMigrationGate.model_validate_json(
            row["gate_payload"]
        )
        assert gate.authenticated_principal_id == "user"
        assert gate.assertion == request.human_gate
        assert row["receipt_sha256"] == receipt.content_sha256


def test_methodology_migration_activation_preserves_token_only_budget(tmp_path):
    _, service, task, root, proposal_path = _system(
        tmp_path,
        total_cost_budget_usd=None,
    )
    request = _request(service, task.task_id, root, proposal_path)

    receipt = service.activate_methodology_migration(
        task.task_id,
        request,
        principal=_migration_principal(),
    )
    status = service.status(receipt.successor_task_id)

    assert status.plan.total_cost_budget_usd is None
    assert all(stage.cost_budget_usd is None for stage in status.stages)


def test_methodology_migration_successor_rejects_legacy_budget_amendment(
    tmp_path,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    request = _request(service, task.task_id, root, proposal_path)
    receipt = service.activate_methodology_migration(
        task.task_id,
        request,
        principal=_migration_principal(),
    )
    successor = tasks.get(receipt.successor_task_id)
    status = service.status(receipt.successor_task_id)
    assert successor is not None
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationConflictError,
        match="budget amendment is deferred",
    ):
        service.amend_budget(
            successor.task_id,
            amended_total_token_budget=status.plan.total_token_budget + 1_000,
            expected_task_version=successor.version,
            expected_plan_version=status.plan.version,
            reason="must remain inert",
        )

    assert _database_dump(tasks) == before


def test_methodology_migration_activation_is_exactly_idempotent(tmp_path):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    request = _request(service, task.task_id, root, proposal_path)
    principal = _migration_principal()
    first = service.activate_methodology_migration(
        task.task_id,
        request,
        principal=principal,
    )
    successor_events = tasks.events(first.successor_task_id)

    second = service.activate_methodology_migration(
        task.task_id,
        request,
        principal=principal,
    )

    assert second == first
    assert tasks.events(first.successor_task_id) == successor_events
    assert len(tasks.list()) == 2
    assert _migration_row_count(tasks) == 1


@pytest.mark.parametrize(
    "principal",
    [
        _migration_principal(permissions=frozenset()),
        _migration_principal(projects=frozenset()),
        _migration_principal(principal_id="different-user"),
    ],
)
def test_methodology_migration_activation_requires_exact_authenticated_approver(
    tmp_path,
    principal,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    request = _request(service, task.task_id, root, proposal_path)
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationValidationError,
        match="principal|permission|authorized",
    ):
        service.activate_methodology_migration(
            task.task_id,
            request,
            principal=principal,
        )

    assert _database_dump(tasks) == before


def test_methodology_migration_activation_requires_registered_project(
    tmp_path,
    monkeypatch,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    request = _request(service, task.task_id, root, proposal_path)
    before = _database_dump(tasks)

    def project_missing(_project_id):
        raise KeyError("unregistered")

    monkeypatch.setattr(service.projects, "get", project_missing)

    with pytest.raises(
        OrchestrationConflictError,
        match="Migration project is not registered",
    ):
        service.activate_methodology_migration(
            task.task_id,
            request,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before


@pytest.mark.parametrize("drift", ["task_version", "proposal_artifact"])
def test_methodology_migration_activation_rechecks_live_bindings_before_writes(
    tmp_path,
    drift,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    request = _request(service, task.task_id, root, proposal_path)
    if drift == "task_version":
        with sqlite3.connect(tasks.db_path) as db:
            db.execute(
                "UPDATE tasks SET version = version + 1 WHERE task_id = ?",
                (task.task_id,),
            )
    else:
        proposal_path.write_text("changed after approval", encoding="utf-8")
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationConflictError,
        match="atomic recheck blocked",
    ):
        service.activate_methodology_migration(
            task.task_id,
            request,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before


def test_methodology_migration_activation_rechecks_repository_after_hashing(
    tmp_path,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    request = _request(service, task.task_id, root, proposal_path)
    revisions = iter(
        [
            REVISION,
            RepositoryRevision(
                repository_id=REVISION.repository_id,
                ref=REVISION.ref,
                commit_sha="b" * 40,
            ),
        ]
    )
    service.revision_resolver = lambda _root, _repository_id: next(revisions)
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationConflictError,
        match="repository_binding",
    ):
        service.activate_methodology_migration(
            task.task_id,
            request,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before


def test_methodology_migration_activation_rolls_back_partial_successor(
    tmp_path,
    monkeypatch,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    request = _request(service, task.task_id, root, proposal_path)
    before = _database_dump(tasks)

    def fail_after_plan(*_args, **_kwargs):
        raise RuntimeError("injected successor initialization failure")

    monkeypatch.setattr(
        service.control_plane,
        "initialize_migrated_successor_tx",
        fail_after_plan,
    )
    with pytest.raises(RuntimeError, match="injected successor"):
        service.activate_methodology_migration(
            task.task_id,
            request,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before


def test_methodology_migration_activation_blocks_a_second_successor(tmp_path):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    request = _request(service, task.task_id, root, proposal_path)
    service.activate_methodology_migration(
        task.task_id,
        request,
        principal=_migration_principal(),
    )
    payload = request.model_dump(mode="json")
    payload["request_id"] = "migration-request-2"
    second_request = _reseal_request(payload)
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationConflictError,
        match="already has a methodology migration successor",
    ):
        service.activate_methodology_migration(
            task.task_id,
            second_request,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before


def test_methodology_migration_activation_cli_uses_env_credential_without_leak(
    tmp_path,
    monkeypatch,
    capsys,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    request = _request(service, task.task_id, root, proposal_path)
    request_path = tmp_path / "migration-request.json"
    request_path.write_text(request.model_dump_json(), encoding="utf-8")
    secret = "migration-control-secret"
    monkeypatch.setenv("AGORA_TEST_MIGRATION_TOKEN", secret)
    monkeypatch.setattr(orchestration_cli, "build_service", lambda: service)
    monkeypatch.setattr(
        "agora.control_plane.auth.get_config",
        lambda: {
            "control_plane": {
                "auth": {
                    "credentials": [
                        {
                            "secret_ref": "AGORA_TEST_MIGRATION_TOKEN",
                            "principal": "user",
                            "permissions": ["control_plane.approve"],
                            "projects": ["alpha"],
                        }
                    ]
                }
            }
        },
    )

    code = orchestration_cli.main(
        [
            "migration-activate",
            task.task_id,
            "--request",
            str(request_path),
            "--credential-env",
            "AGORA_TEST_MIGRATION_TOKEN",
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert code == 0
    assert payload["successor_task_id"] != task.task_id
    assert payload["dispatch_authority"] is False
    assert secret not in output
    assert secret not in _database_dump(tasks)


def test_methodology_migration_activation_cli_authenticates_before_store_init(
    tmp_path,
    monkeypatch,
    capsys,
):
    request_path = tmp_path / "request.json"
    request_path.write_text("{}", encoding="utf-8")
    monkeypatch.delenv("AGORA_MISSING_MIGRATION_TOKEN", raising=False)
    built = False

    def build_forbidden():
        nonlocal built
        built = True
        raise AssertionError("service must not be built before authentication")

    monkeypatch.setattr(orchestration_cli, "build_service", build_forbidden)

    code = orchestration_cli.main(
        [
            "migration-activate",
            "task-source",
            "--request",
            str(request_path),
            "--credential-env",
            "AGORA_MISSING_MIGRATION_TOKEN",
        ]
    )

    assert code == 2
    assert built is False
    assert "credential environment variable is absent" in capsys.readouterr().out


def test_methodology_migration_activation_schemas_are_registered():
    assert (
        SCHEMA_MODELS["authenticated-methodology-migration-gate"]
        is AuthenticatedMethodologyMigrationGate
    )
    assert (
        SCHEMA_MODELS["methodology-migration-activation-receipt"]
        is MethodologyMigrationActivationReceipt
    )


def _activated_successor(
    service: TaskOrchestrationService,
    task,
    root: Path,
    proposal_path: Path,
    *,
    scope: str = "enterprise",
):
    request = _request(
        service,
        task.task_id,
        root,
        proposal_path,
        scope=scope,
    )
    receipt = service.activate_methodology_migration(
        task.task_id,
        request,
        principal=_migration_principal(),
    )
    return request, receipt


def _execution_snapshot(
    tasks: TaskStore,
    service: TaskOrchestrationService,
    task_id: str,
) -> MethodologyExecutionSnapshot:
    task = tasks.get(task_id)
    control_task = service.control_plane.get_task_state(task_id)
    inventory = service.control_plane.get_stage_inventory(task_id)
    status = service.status(task_id)
    assert task is not None
    assert control_task is not None
    assert inventory is not None
    with sqlite3.connect(tasks.db_path) as db:
        db.row_factory = sqlite3.Row
        migration = db.execute(
            """
            SELECT * FROM orchestration_methodology_migrations
            WHERE successor_task_id = ?
            """,
            (task_id,),
        ).fetchone()
    assert migration is not None
    return MethodologyExecutionSnapshot(
        task=task,
        control_task=control_task,
        plan=status.plan,
        plan_stages=tuple(status.stages),
        inventory=inventory,
        request=MethodologyMigrationPreviewRequest.model_validate_json(
            migration["request_payload"]
        ),
        gate=AuthenticatedMethodologyMigrationGate.model_validate_json(
            migration["gate_payload"]
        ),
        receipt=MethodologyMigrationActivationReceipt.model_validate_json(
            migration["receipt_payload"]
        ),
    )


def _snapshot_with_request(
    snapshot: MethodologyExecutionSnapshot,
    request: MethodologyMigrationPreviewRequest,
) -> MethodologyExecutionSnapshot:
    decision_payload = snapshot.receipt.recheck_decision.model_dump(mode="json")
    decision_payload["request_sha256"] = request.content_sha256
    decision = MethodologyMigrationPreviewDecision.model_validate(
        seal_model_payload(
            MethodologyMigrationPreviewDecision,
            decision_payload,
        )
    )
    receipt_payload = snapshot.receipt.model_dump(mode="json")
    receipt_payload["request_sha256"] = request.content_sha256
    receipt_payload["recheck_decision"] = decision.model_dump(mode="json")
    receipt = MethodologyMigrationActivationReceipt.model_validate(
        seal_model_payload(
            MethodologyMigrationActivationReceipt,
            receipt_payload,
        )
    )
    metadata = dict(snapshot.task.metadata)
    metadata["methodology_migration_request_sha256"] = request.content_sha256
    task = snapshot.task.model_copy(update={"metadata": metadata})
    return replace(
        snapshot,
        task=task,
        request=request,
        receipt=receipt,
    )


def _build_execution_contract_direct(
    service: TaskOrchestrationService,
    snapshot: MethodologyExecutionSnapshot,
) -> MethodologyExecutionContract:
    artifacts = [
        *snapshot.request.seed_artifacts,
        snapshot.gate.assertion.migration_artifact,
    ]
    return build_methodology_execution_contract(
        snapshot=snapshot,
        principal=_migration_principal(),
        repository=REVISION,
        observed_artifact_sha256s={
            artifact.path: artifact.sha256 for artifact in artifacts
        },
        runtimes=service.runtimes,
        timeout_seconds=service.timeout_seconds,
        max_output_bytes=1_000_000,
        materialized_at=FIXED_TIME,
    )


def test_methodology_execution_contract_seals_stage_context_handoff_and_gates(
    tmp_path,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    request, receipt = _activated_successor(
        service,
        task,
        root,
        proposal_path,
    )
    successor_before = tasks.get(receipt.successor_task_id)
    status_before = service.status(receipt.successor_task_id)
    inventory_before = service.control_plane.get_stage_inventory(
        receipt.successor_task_id
    )
    route_before = service.control_plane.get_stage_route(receipt.successor_task_id)

    contract = service.materialize_methodology_execution_contract(
        receipt.successor_task_id,
        principal=_migration_principal(),
    )

    assert isinstance(contract, MethodologyExecutionContract)
    assert contract.task_id == receipt.successor_task_id
    assert contract.inventory_sha256 == receipt.successor_inventory_sha256
    assert contract.migration_request_sha256 == request.content_sha256
    assert contract.migration_gate_sha256 == receipt.authenticated_gate_sha256
    assert contract.migration_receipt_sha256 == receipt.content_sha256
    assert contract.route_activated is False
    assert contract.runtime_spawned is False
    assert contract.routing_authority is False
    assert contract.dispatch_authority is False
    assert contract.authenticated_principal_id == "user"
    assert [pin.responsibility for pin in contract.runtime_pins] == [
        "production_execution",
        "independent_correctness",
        "methodology_stewardship",
    ]
    assert [pin.runtime for pin in contract.runtime_pins] == [
        "codex",
        "claude",
        "kiro",
    ]
    assert len(contract.stages) == len(status_before.stages)
    assert [stage.stage_key for stage in contract.stages] == [
        stage.stage_key for stage in status_before.stages
    ]
    assert all(stage.runtime == "codex" for stage in contract.stages)
    assert all(
        stage.context.context_pack_schema_version == "1.0"
        and stage.handoff.handoff_pack_schema_version == "1.0"
        and stage.handoff.exact_context_echo_required
        and not stage.handoff.unbound_output_allowed
        and not stage.handoff.native_state_authority
        and not stage.handoff.suggested_next_action_authority
        and stage.handoff.format_only_repair_attempts == 1
        for stage in contract.stages
    )
    completion_reviews = [
        evidence
        for evidence in contract.stages[-1].gate.evidence_contracts
        if evidence.source == "completion_review"
    ]
    assert [
        (item.producer_responsibility, item.producer_runtime)
        for item in completion_reviews
    ] == [
        ("independent_correctness", "claude"),
        ("methodology_stewardship", "kiro"),
    ]
    assert not any(
        evidence.source == "completion_review"
        for stage in contract.stages[:-1]
        for evidence in stage.gate.evidence_contracts
    )
    assert not any(
        evidence.source == "completion_review"
        for evidence in contract.stages[-1].handoff.evidence_contracts
    )

    matching_unit = next(
        item
        for item in next(
            stage
            for stage in contract.stages
            if stage.stage_key == "nfr-requirements-unit-001"
        ).context.input_contracts
        if item.source_artifact_id == "business-logic-model"
    )
    assert matching_unit.instance_binding == "matching_unit"
    assert matching_unit.producer_stage_keys == ["functional-design-unit-001"]
    all_units = next(
        item
        for item in next(
            stage
            for stage in contract.stages
            if stage.stage_key == "build-and-test"
        ).context.input_contracts
        if item.source_artifact_id == "code-summary"
    )
    assert all_units.instance_binding == "all_units"
    assert all_units.producer_stage_keys == [
        "code-generation-unit-001",
        "code-generation-unit-002",
    ]
    single = next(
        item
        for item in next(
            stage
            for stage in contract.stages
            if stage.stage_key == "nfr-requirements-unit-001"
        ).context.input_contracts
        if item.source_artifact_id == "requirements"
    )
    assert single.instance_binding == "single"
    assert single.producer_stage_keys == ["requirements-analysis"]

    assert tasks.get(receipt.successor_task_id) == successor_before
    assert service.status(receipt.successor_task_id) == status_before
    assert (
        service.control_plane.get_stage_inventory(receipt.successor_task_id)
        == inventory_before
    )
    assert service.control_plane.get_stage_route(receipt.successor_task_id) == route_before
    assert service.store.get_methodology_execution_contract(
        receipt.successor_task_id
    ) == contract
    with sqlite3.connect(tasks.db_path) as db:
        assert db.execute(
            """
            SELECT COUNT(*) FROM orchestration_methodology_execution_contracts
            WHERE task_id = ?
            """,
            (receipt.successor_task_id,),
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM control_stages WHERE task_id = ?",
            (receipt.successor_task_id,),
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM control_gates WHERE task_id = ?",
            (receipt.successor_task_id,),
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM protocol_runs WHERE task_id = ?",
            (receipt.successor_task_id,),
        ).fetchone()[0] == 0


def test_methodology_execution_contract_binds_scope_seed_artifacts(tmp_path):
    _, service, task, root, proposal_path = _system(tmp_path)
    request, receipt = _activated_successor(
        service,
        task,
        root,
        proposal_path,
        scope="bugfix",
    )

    contract = service.materialize_methodology_execution_contract(
        receipt.successor_task_id,
        principal=_migration_principal(),
    )

    seed_inputs = [
        item
        for stage in contract.stages
        for item in stage.context.input_contracts
        if item.resolution == "hash_bound_task_seed"
    ]
    assert seed_inputs
    optional_absent_inputs = [
        item
        for stage in contract.stages
        for item in stage.context.input_contracts
        if item.resolution == "optional_absent"
    ]
    assert optional_absent_inputs
    assert all(
        not item.required
        and item.instance_binding == "optional_absent"
        and not item.producer_stage_keys
        and item.seed_artifact is None
        for item in optional_absent_inputs
    )
    expected = {
        (seed.consumer_stage_key, seed.artifact_id): seed
        for seed in request.seed_artifacts
    }
    for stage in contract.stages:
        for item in stage.context.input_contracts:
            if item.resolution != "hash_bound_task_seed":
                continue
            seed = expected[(stage.source_stage_key, item.source_artifact_id)]
            assert item.instance_binding == "task_seed"
            assert item.seed_artifact is not None
            assert item.seed_artifact.sha256 == seed.sha256
            assert item.seed_artifact.location is not None
            assert item.seed_artifact.location.path == seed.path


def test_methodology_execution_contract_rejects_unused_scope_seed(tmp_path):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    request, receipt = _activated_successor(
        service,
        task,
        root,
        proposal_path,
        scope="bugfix",
    )
    snapshot = _execution_snapshot(tasks, service, receipt.successor_task_id)
    payload = request.model_dump(mode="json")
    extra = dict(payload["seed_artifacts"][0])
    extra["consumer_stage_key"] = "unused-consumer"
    payload["seed_artifacts"].append(extra)
    request_with_orphan = _reseal_request(payload)
    snapshot = _snapshot_with_request(snapshot, request_with_orphan)

    with pytest.raises(
        ValueError,
        match="unused or missing bindings",
    ):
        _build_execution_contract_direct(service, snapshot)


def test_methodology_execution_contract_rejects_missing_required_seed(tmp_path):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    request, receipt = _activated_successor(
        service,
        task,
        root,
        proposal_path,
        scope="bugfix",
    )
    snapshot = _execution_snapshot(tasks, service, receipt.successor_task_id)
    payload = request.model_dump(mode="json")
    payload["seed_artifacts"] = []
    request_without_seed = _reseal_request(payload)
    snapshot = _snapshot_with_request(snapshot, request_without_seed)

    with pytest.raises(
        ValueError,
        match="Required successor Stage input lacks its Task seed",
    ):
        _build_execution_contract_direct(service, snapshot)


def test_methodology_execution_contract_rejects_scope_budget_drift(tmp_path):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    request, receipt = _activated_successor(
        service,
        task,
        root,
        proposal_path,
    )
    snapshot = _execution_snapshot(tasks, service, receipt.successor_task_id)
    payload = request.model_dump(mode="json")
    payload["budget"]["stage_allocations"] = payload["budget"][
        "stage_allocations"
    ][:-1]
    request_without_stage_budget = _reseal_request(payload)
    snapshot = _snapshot_with_request(snapshot, request_without_stage_budget)

    with pytest.raises(
        ValueError,
        match="Stage allocations differ from scope",
    ):
        _build_execution_contract_direct(service, snapshot)


def test_methodology_execution_contract_rejects_plan_stage_order_drift(tmp_path):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    _, receipt = _activated_successor(
        service,
        task,
        root,
        proposal_path,
    )
    snapshot = _execution_snapshot(tasks, service, receipt.successor_task_id)
    snapshot = replace(
        snapshot,
        plan_stages=tuple(reversed(snapshot.plan_stages)),
    )

    with pytest.raises(
        ValueError,
        match="Stage order differs",
    ):
        _build_execution_contract_direct(service, snapshot)


def test_methodology_execution_contract_rejects_dangling_producer_stage(
    tmp_path,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    _, receipt = _activated_successor(
        service,
        task,
        root,
        proposal_path,
    )
    snapshot = _execution_snapshot(tasks, service, receipt.successor_task_id)
    contract = _build_execution_contract_direct(service, snapshot)
    payload = contract.model_dump(mode="json")
    selected_input = next(
        item
        for stage in payload["stages"]
        for item in stage["context"]["input_contracts"]
        if item["resolution"] == "selected_stage_output"
    )
    selected_input["producer_stage_keys"] = ["unknown-stage"]

    with pytest.raises(
        ValidationError,
        match="unknown producer Stage",
    ):
        MethodologyExecutionContract.model_validate(
            seal_model_payload(MethodologyExecutionContract, payload)
        )


def test_methodology_execution_contract_rejects_expanded_count_mismatch():
    with pytest.raises(
        ValueError,
        match="matching unit counts",
    ):
        _bind_selected_producer_instances(
            producer_stage_keys=["producer-unit-001", "producer-unit-002"],
            consumer_stage_keys=[
                "consumer-unit-001",
                "consumer-unit-002",
                "consumer-unit-003",
            ],
            instance_index=1,
        )


def test_methodology_execution_contract_is_exactly_idempotent(tmp_path):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    _, receipt = _activated_successor(
        service,
        task,
        root,
        proposal_path,
    )
    principal = _migration_principal()
    first = service.materialize_methodology_execution_contract(
        receipt.successor_task_id,
        principal=principal,
    )
    after_first = _database_dump(tasks)

    second = service.materialize_methodology_execution_contract(
        receipt.successor_task_id,
        principal=principal,
    )

    assert second == first
    assert _database_dump(tasks) == after_first


def test_methodology_execution_contract_replay_rechecks_authorization(tmp_path):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    _, receipt = _activated_successor(
        service,
        task,
        root,
        proposal_path,
    )
    service.materialize_methodology_execution_contract(
        receipt.successor_task_id,
        principal=_migration_principal(),
    )
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationValidationError,
        match="currently authorized principal",
    ):
        service.materialize_methodology_execution_contract(
            receipt.successor_task_id,
            principal=_migration_principal(permissions=frozenset()),
        )

    assert _database_dump(tasks) == before


@pytest.mark.parametrize(
    "principal",
    [
        _migration_principal(permissions=frozenset()),
        _migration_principal(projects=frozenset()),
        _migration_principal(principal_id="different-user"),
    ],
)
def test_methodology_execution_contract_requires_original_gate_principal(
    tmp_path,
    principal,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    _, receipt = _activated_successor(
        service,
        task,
        root,
        proposal_path,
    )
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationValidationError,
        match="permission|authorized|migration Gate",
    ):
        service.materialize_methodology_execution_contract(
            receipt.successor_task_id,
            principal=principal,
        )

    assert _database_dump(tasks) == before


def test_methodology_execution_contract_rechecks_migration_artifacts(tmp_path):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    _, receipt = _activated_successor(
        service,
        task,
        root,
        proposal_path,
    )
    proposal_path.write_text("changed after migration", encoding="utf-8")
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationConflictError,
        match="Artifact binding is stale",
    ):
        service.materialize_methodology_execution_contract(
            receipt.successor_task_id,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before


def test_methodology_execution_contract_rechecks_runtime_commands(tmp_path):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    _, receipt = _activated_successor(
        service,
        task,
        root,
        proposal_path,
    )
    service.runtimes["codex"] = RuntimeCommand(
        adapter="codex",
        command_template=(sys.executable, "changed", "{prompt}"),
    )
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationConflictError,
        match="runtime registry binding is stale",
    ):
        service.materialize_methodology_execution_contract(
            receipt.successor_task_id,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before


def test_methodology_execution_contract_requires_inert_successor(tmp_path):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    _, receipt = _activated_successor(
        service,
        task,
        root,
        proposal_path,
    )
    with sqlite3.connect(tasks.db_path) as db:
        row = db.execute(
            "SELECT metadata FROM tasks WHERE task_id = ?",
            (receipt.successor_task_id,),
        ).fetchone()
        metadata = json.loads(row[0])
        metadata["methodology_route_activated"] = True
        db.execute(
            "UPDATE tasks SET metadata = ? WHERE task_id = ?",
            (
                json.dumps(metadata, sort_keys=True, separators=(",", ":")),
                receipt.successor_task_id,
            ),
        )
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationConflictError,
        match="no longer inert",
    ):
        service.materialize_methodology_execution_contract(
            receipt.successor_task_id,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before


def test_methodology_execution_contract_rolls_back_event_failure(
    tmp_path,
    monkeypatch,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    _, receipt = _activated_successor(
        service,
        task,
        root,
        proposal_path,
    )
    before = _database_dump(tasks)

    def fail_event(*_args, **_kwargs):
        raise RuntimeError("injected execution contract event failure")

    monkeypatch.setattr(service.control_plane, "_event", fail_event)

    with pytest.raises(RuntimeError, match="injected execution contract"):
        service.materialize_methodology_execution_contract(
            receipt.successor_task_id,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before


def test_methodology_execution_contract_cli_authenticates_without_secret_leak(
    tmp_path,
    monkeypatch,
    capsys,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    _, receipt = _activated_successor(
        service,
        task,
        root,
        proposal_path,
    )
    secret = "execution-contract-control-secret"
    monkeypatch.setenv("AGORA_TEST_EXECUTION_CONTRACT_TOKEN", secret)
    monkeypatch.setattr(orchestration_cli, "build_service", lambda: service)
    monkeypatch.setattr(
        "agora.control_plane.auth.get_config",
        lambda: {
            "control_plane": {
                "auth": {
                    "credentials": [
                        {
                            "secret_ref": "AGORA_TEST_EXECUTION_CONTRACT_TOKEN",
                            "principal": "user",
                            "permissions": ["control_plane.approve"],
                            "projects": ["alpha"],
                        }
                    ]
                }
            }
        },
    )

    code = orchestration_cli.main(
        [
            "migration-contract",
            receipt.successor_task_id,
            "--credential-env",
            "AGORA_TEST_EXECUTION_CONTRACT_TOKEN",
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert code == 0
    assert payload["task_id"] == receipt.successor_task_id
    assert payload["dispatch_authority"] is False
    assert secret not in output
    assert secret not in _database_dump(tasks)


def test_methodology_execution_contract_cli_authenticates_before_store_init(
    tmp_path,
    monkeypatch,
    capsys,
):
    monkeypatch.delenv("AGORA_MISSING_EXECUTION_CONTRACT_TOKEN", raising=False)
    built = False

    def build_forbidden():
        nonlocal built
        built = True
        raise AssertionError("service must not be built before authentication")

    monkeypatch.setattr(orchestration_cli, "build_service", build_forbidden)

    code = orchestration_cli.main(
        [
            "migration-contract",
            "task-successor",
            "--credential-env",
            "AGORA_MISSING_EXECUTION_CONTRACT_TOKEN",
        ]
    )

    assert code == 2
    assert built is False
    assert "credential environment variable is absent" in capsys.readouterr().out


def test_methodology_execution_contract_schema_is_registered():
    assert (
        SCHEMA_MODELS["methodology-execution-contract"]
        is MethodologyExecutionContract
    )


def _route_activation_request(
    tasks: TaskStore,
    service: TaskOrchestrationService,
    contract: MethodologyExecutionContract,
) -> MethodologyRouteActivationRequest:
    task = tasks.get(contract.task_id)
    control_task = service.control_plane.get_task_state(contract.task_id)
    status = service.status(contract.task_id)
    inventory = service.control_plane.get_stage_inventory(contract.task_id)
    assert task is not None
    assert control_task is not None
    assert inventory is not None
    first_stage = contract.stages[0]
    payload = {
        "schema_version": "1.0",
        "request_id": f"route-request-{contract.content_sha256[:20]}",
        "requested_at": FIXED_TIME,
        "project_id": task.project_id,
        "task_id": task.task_id,
        "expected_task_version": task.version,
        "expected_control_task_version": control_task.version,
        "expected_control_task_status": control_task.status.value,
        "plan_id": status.plan.plan_id,
        "expected_plan_version": status.plan.version,
        "inventory_id": inventory.inventory_id,
        "inventory_sha256": inventory.content_sha256,
        "execution_contract_id": contract.contract_id,
        "execution_contract_sha256": contract.content_sha256,
        "repository": contract.repository.model_dump(mode="json"),
        "first_stage_key": first_stage.stage_key,
        "first_gate_key": first_stage.gate_key,
        "activate_first_route": True,
        "dispatch_runtime": False,
    }
    return MethodologyRouteActivationRequest.model_validate(
        seal_model_payload(MethodologyRouteActivationRequest, payload)
    )


def _contracted_successor(
    tasks: TaskStore,
    service: TaskOrchestrationService,
    task,
    root: Path,
    proposal_path: Path,
    *,
    scope: str = "enterprise",
):
    _, migration_receipt = _activated_successor(
        service,
        task,
        root,
        proposal_path,
        scope=scope,
    )
    contract = service.materialize_methodology_execution_contract(
        migration_receipt.successor_task_id,
        principal=_migration_principal(),
    )
    request = _route_activation_request(tasks, service, contract)
    return migration_receipt, contract, request


def test_methodology_first_route_activation_is_atomic_and_non_dispatching(
    tmp_path,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    _, contract, request = _contracted_successor(
        tasks,
        service,
        task,
        root,
        proposal_path,
    )
    task_before = tasks.get(contract.task_id)
    control_before = service.control_plane.get_task_state(contract.task_id)
    status_before = service.status(contract.task_id)
    inventory_before = service.control_plane.get_stage_inventory(
        contract.task_id
    )
    assert task_before is not None
    assert control_before is not None
    assert inventory_before is not None

    receipt = service.activate_methodology_first_route(
        contract.task_id,
        request,
        principal=_migration_principal(),
    )

    assert isinstance(receipt, MethodologyRouteActivationReceipt)
    assert receipt.request_id == request.request_id
    assert receipt.request_sha256 == request.content_sha256
    assert receipt.execution_contract_id == contract.contract_id
    assert receipt.execution_contract_sha256 == contract.content_sha256
    assert receipt.task_version_before == task_before.version
    assert receipt.task_version_after == task_before.version + 1
    assert receipt.control_task_version_before == control_before.version
    assert receipt.first_stage_key == contract.stages[0].stage_key
    assert receipt.first_gate_key == contract.stages[0].gate_key
    assert receipt.first_stage_status.value == "ready"
    assert receipt.first_gate_status.value == "pending"
    assert receipt.route_activated is True
    assert receipt.run_created is False
    assert receipt.runtime_spawned is False
    assert receipt.dispatch_authority is False
    assert receipt.protocol_artifacts_created is False

    task_after = tasks.get(contract.task_id)
    assert task_after is not None
    assert task_after.version == task_before.version + 1
    assert task_after.metadata["methodology_route_activated"] is True
    assert task_after.metadata["methodology_dispatch_authority"] is False
    assert (
        task_after.metadata["methodology_execution_contract_sha256"]
        == contract.content_sha256
    )
    route = service.control_plane.get_stage_route(contract.task_id)
    stage = service.control_plane.get_stage(
        contract.task_id,
        contract.stages[0].stage_key,
    )
    gate = service.control_plane.get_gate(
        contract.task_id,
        contract.stages[0].gate_key,
    )
    assert route is not None
    assert route.stage_key == contract.stages[0].stage_key
    assert route.stage_status.value == "ready"
    assert route.gate_status.value == "pending"
    assert stage is not None
    assert stage.version == receipt.first_stage_version
    assert gate is not None
    assert gate.requirements == sorted(
        [
            item.requirement
            for item in contract.stages[0].gate.evidence_contracts
        ],
        key=lambda item: item.requirement_id,
    )
    assert service.status(contract.task_id) == status_before
    assert (
        service.control_plane.get_stage_inventory(contract.task_id)
        == inventory_before
    )
    assert (
        service.store.get_methodology_route_activation(contract.task_id)
        == receipt
    )
    with sqlite3.connect(tasks.db_path) as db:
        assert db.execute(
            """
            SELECT COUNT(*)
            FROM orchestration_methodology_route_activations
            WHERE task_id = ?
            """,
            (contract.task_id,),
        ).fetchone()[0] == 1
        for table in (
            "orchestration_runs",
            "orchestration_consultations",
            "protocol_runs",
            "protocol_artifacts",
            "protocol_evidence",
        ):
            assert db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE task_id = ?",
                (contract.task_id,),
            ).fetchone()[0] == 0


def test_methodology_first_route_activation_registers_only_first_stage_seeds(
    tmp_path,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    _, contract, request = _contracted_successor(
        tasks,
        service,
        task,
        root,
        proposal_path,
        scope="bugfix",
    )
    expected = [
        item.seed_artifact
        for item in contract.stages[0].context.input_contracts
        if item.resolution == "hash_bound_task_seed"
    ]

    receipt = service.activate_methodology_first_route(
        contract.task_id,
        request,
        principal=_migration_principal(),
    )

    assert len(receipt.seed_artifacts) == len(expected)
    assert [item.artifact for item in receipt.seed_artifacts] == expected
    assert all(
        item.consumer_stage_key == contract.stages[0].stage_key
        for item in receipt.seed_artifacts
    )
    with sqlite3.connect(tasks.db_path) as db:
        rows = db.execute(
            """
            SELECT consumer_stage_key, artifact_id, artifact_version,
                   artifact_sha256, path
            FROM orchestration_methodology_seed_artifact_refs
            WHERE task_id = ?
            ORDER BY consumer_stage_key, source_artifact_id
            """,
            (contract.task_id,),
        ).fetchall()
        assert len(rows) == len(expected)
        assert db.execute(
            "SELECT COUNT(*) FROM protocol_artifacts WHERE task_id = ?",
            (contract.task_id,),
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    "principal",
    [
        _migration_principal(permissions=frozenset()),
        _migration_principal(projects=frozenset()),
        _migration_principal(principal_id="different-user"),
    ],
)
def test_methodology_first_route_activation_requires_gate_principal(
    tmp_path,
    principal,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    _, contract, request = _contracted_successor(
        tasks,
        service,
        task,
        root,
        proposal_path,
    )
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationValidationError,
        match="permission|authorized|migration Gate",
    ):
        service.activate_methodology_first_route(
            contract.task_id,
            request,
            principal=principal,
        )

    assert _database_dump(tasks) == before


def test_methodology_first_route_activation_rejects_nonfirst_request(
    tmp_path,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    _, contract, request = _contracted_successor(
        tasks,
        service,
        task,
        root,
        proposal_path,
    )
    payload = request.model_dump(mode="json")
    payload["first_stage_key"] = contract.stages[1].stage_key
    payload["first_gate_key"] = contract.stages[1].gate_key
    changed = MethodologyRouteActivationRequest.model_validate(
        seal_model_payload(MethodologyRouteActivationRequest, payload)
    )
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationConflictError,
        match="exact unconfigured first",
    ):
        service.activate_methodology_first_route(
            contract.task_id,
            changed,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before


def test_methodology_first_route_activation_rechecks_artifacts_and_runtime(
    tmp_path,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    _, contract, request = _contracted_successor(
        tasks,
        service,
        task,
        root,
        proposal_path,
    )
    proposal_path.write_text("changed before route activation", encoding="utf-8")
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationConflictError,
        match="Artifact binding is stale",
    ):
        service.activate_methodology_first_route(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )
    assert _database_dump(tasks) == before

    proposal_path.write_text(
        json.dumps({"proposal": "migrate to AWS AI-DLC v2.3.0"}),
        encoding="utf-8",
    )
    service.runtimes["codex"] = RuntimeCommand(
        adapter="codex",
        command_template=(sys.executable, "changed", "{prompt}"),
    )
    with pytest.raises(
        OrchestrationConflictError,
        match="runtime registry binding is stale",
    ):
        service.activate_methodology_first_route(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )
    assert _database_dump(tasks) == before


def test_methodology_first_route_activation_is_exactly_idempotent(tmp_path):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    _, contract, request = _contracted_successor(
        tasks,
        service,
        task,
        root,
        proposal_path,
    )
    principal = _migration_principal()
    first = service.activate_methodology_first_route(
        contract.task_id,
        request,
        principal=principal,
    )
    after_first = _database_dump(tasks)

    second = service.activate_methodology_first_route(
        contract.task_id,
        request,
        principal=principal,
    )

    assert second == first
    assert _database_dump(tasks) == after_first


def test_methodology_first_route_activation_serializes_concurrent_replay(
    tmp_path,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    _, contract, request = _contracted_successor(
        tasks,
        service,
        task,
        root,
        proposal_path,
    )
    barrier = Barrier(2)

    def activate():
        barrier.wait(timeout=10)
        return service.activate_methodology_first_route(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(lambda _index: activate(), range(2)))

    assert receipts[0] == receipts[1]
    with sqlite3.connect(tasks.db_path) as db:
        assert db.execute(
            """
            SELECT COUNT(*)
            FROM orchestration_methodology_route_activations
            WHERE task_id = ?
            """,
            (contract.task_id,),
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM control_stages WHERE task_id = ?",
            (contract.task_id,),
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM control_gates WHERE task_id = ?",
            (contract.task_id,),
        ).fetchone()[0] == 1


def test_methodology_first_route_activation_replay_rechecks_authorization(
    tmp_path,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    _, contract, request = _contracted_successor(
        tasks,
        service,
        task,
        root,
        proposal_path,
    )
    service.activate_methodology_first_route(
        contract.task_id,
        request,
        principal=_migration_principal(),
    )
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationValidationError,
        match="currently authorized principal",
    ):
        service.activate_methodology_first_route(
            contract.task_id,
            request,
            principal=_migration_principal(permissions=frozenset()),
        )

    assert _database_dump(tasks) == before


def test_methodology_first_route_activation_rolls_back_event_failure(
    tmp_path,
    monkeypatch,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    _, contract, request = _contracted_successor(
        tasks,
        service,
        task,
        root,
        proposal_path,
    )
    before = _database_dump(tasks)

    def fail_event(*_args, **_kwargs):
        raise RuntimeError("injected route activation event failure")

    monkeypatch.setattr(service.control_plane, "_event", fail_event)
    with pytest.raises(RuntimeError, match="injected route activation"):
        service.activate_methodology_first_route(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before


def test_methodology_route_activation_request_loader_is_strict(tmp_path):
    path = tmp_path / "route-activation.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_methodology_route_activation_request(path)


def test_methodology_first_route_activation_cli_authenticates_without_secret(
    tmp_path,
    monkeypatch,
    capsys,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    _, contract, request = _contracted_successor(
        tasks,
        service,
        task,
        root,
        proposal_path,
    )
    request_path = tmp_path / "route-activation.json"
    request_path.write_text(request.model_dump_json(), encoding="utf-8")
    secret = "route-activation-control-secret"
    monkeypatch.setenv("AGORA_TEST_ROUTE_ACTIVATION_TOKEN", secret)
    monkeypatch.setattr(orchestration_cli, "build_service", lambda: service)
    monkeypatch.setattr(
        "agora.control_plane.auth.get_config",
        lambda: {
            "control_plane": {
                "auth": {
                    "credentials": [
                        {
                            "secret_ref": "AGORA_TEST_ROUTE_ACTIVATION_TOKEN",
                            "principal": "user",
                            "permissions": ["control_plane.approve"],
                            "projects": ["alpha"],
                        }
                    ]
                }
            }
        },
    )

    code = orchestration_cli.main(
        [
            "migration-route-activate",
            contract.task_id,
            "--request",
            str(request_path),
            "--credential-env",
            "AGORA_TEST_ROUTE_ACTIVATION_TOKEN",
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert code == 0
    assert payload["task_id"] == contract.task_id
    assert payload["route_activated"] is True
    assert payload["dispatch_authority"] is False
    assert secret not in output
    assert secret not in _database_dump(tasks)


def test_methodology_first_route_activation_cli_authenticates_before_store(
    tmp_path,
    monkeypatch,
    capsys,
):
    monkeypatch.delenv("AGORA_MISSING_ROUTE_ACTIVATION_TOKEN", raising=False)
    built = False

    def build_forbidden():
        nonlocal built
        built = True
        raise AssertionError("service must not be built before authentication")

    monkeypatch.setattr(orchestration_cli, "build_service", build_forbidden)
    code = orchestration_cli.main(
        [
            "migration-route-activate",
            "task-successor",
            "--request",
            str(tmp_path / "missing.json"),
            "--credential-env",
            "AGORA_MISSING_ROUTE_ACTIVATION_TOKEN",
        ]
    )

    assert code == 2
    assert built is False
    assert "credential environment variable is absent" in capsys.readouterr().out


def test_methodology_route_activation_schemas_are_registered():
    assert (
        SCHEMA_MODELS["methodology-route-activation-request"]
        is MethodologyRouteActivationRequest
    )
    assert (
        SCHEMA_MODELS["methodology-route-activation-receipt"]
        is MethodologyRouteActivationReceipt
    )


def _run_claim_request(
    tasks: TaskStore,
    service: TaskOrchestrationService,
    contract: MethodologyExecutionContract,
    activation: MethodologyRouteActivationReceipt,
) -> MethodologyRunClaimRequest:
    task = tasks.get(contract.task_id)
    control_task = service.control_plane.get_task_state(contract.task_id)
    status = service.status(contract.task_id)
    inventory = service.control_plane.get_stage_inventory(contract.task_id)
    assert task is not None
    assert control_task is not None
    assert inventory is not None
    first_stage = contract.stages[0]
    run_id = f"methodology-run-{contract.content_sha256[:20]}"
    payload = {
        "schema_version": "1.0",
        "request_id": f"run-claim-request-{contract.content_sha256[:20]}",
        "requested_at": FIXED_TIME,
        "project_id": task.project_id,
        "task_id": task.task_id,
        "expected_task_version": task.version,
        "expected_control_task_version": control_task.version,
        "expected_control_task_status": control_task.status.value,
        "plan_id": status.plan.plan_id,
        "expected_plan_version": status.plan.version,
        "inventory_id": inventory.inventory_id,
        "inventory_sha256": inventory.content_sha256,
        "execution_contract_id": contract.contract_id,
        "execution_contract_sha256": contract.content_sha256,
        "route_activation_receipt_id": activation.receipt_id,
        "route_activation_receipt_sha256": activation.content_sha256,
        "repository": contract.repository.model_dump(mode="json"),
        "first_stage_key": first_stage.stage_key,
        "first_gate_key": first_stage.gate_key,
        "runtime": first_stage.runtime,
        "run_id": run_id,
        "context_pack_id": f"context:{run_id}",
        "context_pack_schema_version": "1.0",
        "claim_formal_run": True,
        "start_runtime_process": False,
    }
    return MethodologyRunClaimRequest.model_validate(
        seal_model_payload(MethodologyRunClaimRequest, payload)
    )


def _activated_route(
    tasks: TaskStore,
    service: TaskOrchestrationService,
    task,
    root: Path,
    proposal_path: Path,
    *,
    scope: str = "bugfix",
):
    _, contract, route_request = _contracted_successor(
        tasks,
        service,
        task,
        root,
        proposal_path,
        scope=scope,
    )
    activation = service.activate_methodology_first_route(
        contract.task_id,
        route_request,
        principal=_migration_principal(),
    )
    claim_request = _run_claim_request(
        tasks,
        service,
        contract,
        activation,
    )
    return contract, activation, claim_request


def test_methodology_first_run_claim_is_atomic_and_non_spawning(tmp_path):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    contract, activation, request = _activated_route(
        tasks,
        service,
        task,
        root,
        proposal_path,
    )
    task_before = tasks.get(contract.task_id)
    control_before = service.control_plane.get_task_state(contract.task_id)
    stage_before = service.control_plane.get_stage(
        contract.task_id,
        contract.stages[0].stage_key,
    )
    assert task_before is not None
    assert control_before is not None
    assert stage_before is not None

    receipt = service.claim_methodology_first_run(
        contract.task_id,
        request,
        principal=_migration_principal(),
    )

    assert isinstance(receipt, MethodologyRunClaimReceipt)
    assert receipt.request_id == request.request_id
    assert receipt.request_sha256 == request.content_sha256
    assert receipt.route_activation_receipt_id == activation.receipt_id
    assert receipt.route_activation_receipt_sha256 == activation.content_sha256
    assert receipt.task_version_before == task_before.version
    assert receipt.task_version_after == task_before.version + 1
    assert receipt.control_task_version_before == control_before.version
    assert receipt.first_stage_version_before == stage_before.version
    assert receipt.first_stage_version_after == stage_before.version + 1
    assert receipt.first_stage_status_before.value == "ready"
    assert receipt.first_stage_status_after.value == "running"
    assert receipt.context_pack_materialized is True
    assert receipt.formal_run_created is True
    assert receipt.usage_reservation_recorded is True
    assert receipt.compatibility_run_created is False
    assert receipt.protocol_artifacts_created is False
    assert receipt.runtime_preflight_created is False
    assert receipt.process_started is False
    assert receipt.runtime_spawned is False
    assert receipt.process_spawn_authority is False
    assert receipt.provider_substitution is False

    protocol_run = service.control_plane.get_protocol_run(receipt.run_id)
    assert protocol_run is not None
    context = protocol_run.context_pack
    first_stage = contract.stages[0]
    assert context.pack_id == receipt.context_pack_id
    assert context.content_sha256 == receipt.context_pack_sha256
    assert context.stage_contract == first_stage.context.stage_contract
    assert context.input_artifacts == [
        item.artifact for item in activation.seed_artifacts
    ]
    assert context.required_outputs == receipt.required_outputs
    assert [item.kind for item in context.required_outputs] == [
        item.kind for item in first_stage.context.output_contracts
    ]
    assert [item.required for item in context.required_outputs] == [
        item.required for item in first_stage.context.output_contracts
    ]
    assert context.forbidden_constraints == (
        first_stage.context.forbidden_constraints
    )
    assert context.budget == first_stage.context.budget
    assert context.task_memory == []
    assert context.project_knowledge == []
    assert context.user_preferences == []
    with sqlite3.connect(tasks.db_path) as db:
        migration_payload = db.execute(
            """
            SELECT request_payload
            FROM orchestration_methodology_migrations
            WHERE successor_task_id = ?
            """,
            (contract.task_id,),
        ).fetchone()[0]
    migration_request = MethodologyMigrationPreviewRequest.model_validate_json(
        migration_payload
    )
    migration_seed_sha256_by_path = {
        item.path: item.sha256 for item in migration_request.seed_artifacts
    }
    assert all(
        item.location is not None
        and migration_seed_sha256_by_path.get(item.location.path) == item.sha256
        for item in context.input_artifacts
    )

    task_after = tasks.get(contract.task_id)
    stage_after = service.control_plane.get_stage(
        contract.task_id,
        first_stage.stage_key,
    )
    control_after = service.control_plane.get_task_state(contract.task_id)
    assert task_after is not None
    assert stage_after is not None
    assert control_after is not None
    assert task_after.version == task_before.version + 1
    assert task_after.metadata["methodology_run_claimed"] is True
    assert task_after.metadata["methodology_run_id"] == receipt.run_id
    assert task_after.metadata["methodology_dispatch_authority"] is False
    assert stage_after.status.value == "running"
    assert control_after.status.value == "active"
    assert service.store.get_methodology_run_claim(contract.task_id) == receipt

    compatibility = service.status(contract.task_id)
    assert compatibility.plan.state == PlanState.READY_FOR_IMPLEMENTATION
    assert all(item.state.value == "pending" for item in compatibility.stages)
    assert compatibility.runs == []
    unified = service.unified_status(contract.task_id)
    assert len(unified.runs) == 1
    assert unified.runs[0].run_id == receipt.run_id
    assert unified.runs[0].runtime == first_stage.runtime
    assert (
        unified.runs[0].token_reserved
        == first_stage.context.budget.max_model_tokens
    )
    assert (
        unified.budget.token_reserved
        == first_stage.context.budget.max_model_tokens
    )
    with sqlite3.connect(tasks.db_path) as db:
        assert db.execute(
            """
            SELECT COUNT(*) FROM orchestration_methodology_run_claims
            WHERE task_id = ? AND process_started = 0
            """,
            (contract.task_id,),
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM protocol_runs WHERE task_id = ?",
            (contract.task_id,),
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM orchestration_runs WHERE task_id = ?",
            (contract.task_id,),
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM protocol_artifacts WHERE task_id = ?",
            (contract.task_id,),
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    "principal",
    [
        _migration_principal(permissions=frozenset()),
        _migration_principal(projects=frozenset()),
        _migration_principal(principal_id="different-user"),
    ],
)
def test_methodology_first_run_claim_requires_migration_principal(
    tmp_path,
    principal,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    contract, _, request = _activated_route(
        tasks,
        service,
        task,
        root,
        proposal_path,
    )
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationValidationError,
        match="permission|authorized|migration Gate",
    ):
        service.claim_methodology_first_run(
            contract.task_id,
            request,
            principal=principal,
        )

    assert _database_dump(tasks) == before


def test_methodology_first_run_claim_rechecks_artifacts_and_runtime(tmp_path):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    contract, _, request = _activated_route(
        tasks,
        service,
        task,
        root,
        proposal_path,
    )
    proposal_path.write_text("changed before Run claim", encoding="utf-8")
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationConflictError,
        match="Artifact binding is stale",
    ):
        service.claim_methodology_first_run(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )
    assert _database_dump(tasks) == before

    proposal_path.write_text(
        json.dumps({"proposal": "migrate to AWS AI-DLC v2.3.0"}),
        encoding="utf-8",
    )
    service.runtimes["codex"] = RuntimeCommand(
        adapter="codex",
        command_template=(sys.executable, "changed", "{prompt}"),
    )
    with pytest.raises(
        OrchestrationConflictError,
        match="runtime registry binding is stale",
    ):
        service.claim_methodology_first_run(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )
    assert _database_dump(tasks) == before


def test_methodology_first_run_claim_rejects_stale_request_without_mutation(
    tmp_path,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    contract, _, request = _activated_route(
        tasks,
        service,
        task,
        root,
        proposal_path,
    )
    payload = request.model_dump(mode="json")
    payload["expected_task_version"] += 1
    stale = MethodologyRunClaimRequest.model_validate(
        seal_model_payload(MethodologyRunClaimRequest, payload)
    )
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationConflictError,
        match="request binding is stale or differs",
    ):
        service.claim_methodology_first_run(
            contract.task_id,
            stale,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before


def test_methodology_first_run_claim_is_exactly_idempotent_and_serialized(
    tmp_path,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    contract, _, request = _activated_route(
        tasks,
        service,
        task,
        root,
        proposal_path,
    )
    barrier = Barrier(2)

    def claim():
        barrier.wait(timeout=10)
        return service.claim_methodology_first_run(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(lambda _index: claim(), range(2)))

    assert receipts[0] == receipts[1]
    after = _database_dump(tasks)
    assert (
        service.claim_methodology_first_run(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )
        == receipts[0]
    )
    assert _database_dump(tasks) == after
    with sqlite3.connect(tasks.db_path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM orchestration_methodology_run_claims"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM protocol_runs WHERE task_id = ?",
            (contract.task_id,),
        ).fetchone()[0] == 1


def test_methodology_first_run_claim_rejects_different_replay_request(
    tmp_path,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    contract, _, request = _activated_route(
        tasks,
        service,
        task,
        root,
        proposal_path,
    )
    service.claim_methodology_first_run(
        contract.task_id,
        request,
        principal=_migration_principal(),
    )
    payload = request.model_dump(mode="json")
    payload["request_id"] = f"{request.request_id}-different"
    changed = MethodologyRunClaimRequest.model_validate(
        seal_model_payload(MethodologyRunClaimRequest, payload)
    )
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationConflictError,
        match="replay binding differs",
    ):
        service.claim_methodology_first_run(
            contract.task_id,
            changed,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before


def test_methodology_first_run_claim_replay_rechecks_authorization(tmp_path):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    contract, _, request = _activated_route(
        tasks,
        service,
        task,
        root,
        proposal_path,
    )
    service.claim_methodology_first_run(
        contract.task_id,
        request,
        principal=_migration_principal(),
    )
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationValidationError,
        match="currently authorized principal",
    ):
        service.claim_methodology_first_run(
            contract.task_id,
            request,
            principal=_migration_principal(permissions=frozenset()),
        )

    assert _database_dump(tasks) == before


def test_methodology_first_run_claim_rolls_back_event_failure(
    tmp_path,
    monkeypatch,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    contract, _, request = _activated_route(
        tasks,
        service,
        task,
        root,
        proposal_path,
    )
    before = _database_dump(tasks)
    original_event = service.control_plane._event

    def fail_last_event(*args, **kwargs):
        if kwargs.get("event_type") == "methodology.run_claimed":
            raise RuntimeError("injected Run claim event failure")
        return original_event(*args, **kwargs)

    monkeypatch.setattr(service.control_plane, "_event", fail_last_event)
    with pytest.raises(RuntimeError, match="injected Run claim"):
        service.claim_methodology_first_run(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before


def test_methodology_first_run_claim_rejects_oversized_context_entry_atomically(
    tmp_path,
    monkeypatch,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    contract, _, request = _activated_route(
        tasks,
        service,
        task,
        root,
        proposal_path,
    )
    before = _database_dump(tasks)

    def oversized_context(**_kwargs):
        return _context_entry(
            prefix="oversized",
            title="Oversized methodology Context entry",
            content="x" * 20_001,
            source_ref="test:oversized-context-entry",
        )

    monkeypatch.setattr(
        "agora.orchestration.service.build_methodology_run_claim_context",
        oversized_context,
    )
    with pytest.raises(
        OrchestrationConflictError,
        match="exceeds 20,000 characters",
    ):
        service.claim_methodology_first_run(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before


def test_methodology_run_claim_request_loader_is_strict(tmp_path):
    path = tmp_path / "run-claim.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_methodology_run_claim_request(path)


def test_methodology_first_run_claim_cli_authenticates_without_secret(
    tmp_path,
    monkeypatch,
    capsys,
):
    tasks, service, task, root, proposal_path = _system(tmp_path)
    contract, _, request = _activated_route(
        tasks,
        service,
        task,
        root,
        proposal_path,
    )
    request_path = tmp_path / "run-claim.json"
    request_path.write_text(request.model_dump_json(), encoding="utf-8")
    secret = "run-claim-control-secret"
    monkeypatch.setenv("AGORA_TEST_RUN_CLAIM_TOKEN", secret)
    monkeypatch.setattr(orchestration_cli, "build_service", lambda: service)
    monkeypatch.setattr(
        "agora.control_plane.auth.get_config",
        lambda: {
            "control_plane": {
                "auth": {
                    "credentials": [
                        {
                            "secret_ref": "AGORA_TEST_RUN_CLAIM_TOKEN",
                            "principal": "user",
                            "permissions": ["control_plane.approve"],
                            "projects": ["alpha"],
                        }
                    ]
                }
            }
        },
    )

    code = orchestration_cli.main(
        [
            "migration-run-claim",
            contract.task_id,
            "--request",
            str(request_path),
            "--credential-env",
            "AGORA_TEST_RUN_CLAIM_TOKEN",
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert code == 0
    assert payload["task_id"] == contract.task_id
    assert payload["formal_run_created"] is True
    assert payload["process_started"] is False
    assert secret not in output
    assert secret not in _database_dump(tasks)


def test_methodology_first_run_claim_cli_authenticates_before_store(
    tmp_path,
    monkeypatch,
    capsys,
):
    monkeypatch.delenv("AGORA_MISSING_RUN_CLAIM_TOKEN", raising=False)
    built = False

    def build_forbidden():
        nonlocal built
        built = True
        raise AssertionError("service must not be built before authentication")

    monkeypatch.setattr(orchestration_cli, "build_service", build_forbidden)
    code = orchestration_cli.main(
        [
            "migration-run-claim",
            "task-successor",
            "--request",
            str(tmp_path / "missing.json"),
            "--credential-env",
            "AGORA_MISSING_RUN_CLAIM_TOKEN",
        ]
    )

    assert code == 2
    assert built is False
    assert "credential environment variable is absent" in capsys.readouterr().out


def test_methodology_run_claim_schemas_are_registered():
    assert (
        SCHEMA_MODELS["methodology-run-claim-request"]
        is MethodologyRunClaimRequest
    )
    assert (
        SCHEMA_MODELS["methodology-run-claim-receipt"]
        is MethodologyRunClaimReceipt
    )
