from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

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
from agora.orchestration.protocol_context import RepositoryRevision
from agora.orchestration.runtime import RuntimeCommand
from agora.orchestration.runtime_capabilities import (
    runtime_command_sha256,
    runtime_registry_sha256,
)
from agora.orchestration.service import TaskOrchestrationService
from agora.projects import ProjectRegistry
from agora.protocol.hashing import seal_model_payload
from agora.protocol.methodology_migration import (
    MethodologyMigrationGateAssertion,
    MethodologyMigrationPreviewDecision,
    MethodologyMigrationPreviewRequest,
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
