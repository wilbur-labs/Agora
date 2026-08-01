from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest
from pydantic import ValidationError

from agora.control_plane.auth import ControlPrincipal
from agora.control_plane.store import ControlPlaneValidationError
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
from agora.orchestration.methodology_run_dispatch import (
    derive_methodology_run_dispatch_policy,
)
from agora.orchestration.methodology_stage_gate import (
    load_methodology_stage_gate_request,
)
from agora.orchestration.methodology_stage_run_claim import (
    load_methodology_stage_run_claim_request,
)
from agora.orchestration.methodology_stage_run_dispatch import (
    derive_methodology_stage_run_dispatch_policy,
)
from agora.orchestration.models import MethodologyDispatchState, PlanState
from agora.orchestration.processes import ProcessState
from agora.orchestration.provider_usage import RuntimeResultFormat
from agora.orchestration.protocol_context import (
    PROTOCOL_PROMPT_LIMIT,
    RepositoryRevision,
)
from agora.orchestration.runtime import (
    RuntimeCommand,
    RuntimeResult,
    resolve_runtime_command,
)
from agora.orchestration.runtime_capabilities import (
    collect_native_runtime_capabilities,
    runtime_command_sha256,
    runtime_registry_sha256,
)
from agora.orchestration.service import TaskOrchestrationService
from agora.orchestration.store import (
    OrchestrationConflictError,
    OrchestrationValidationError,
)
from agora.projects import ProjectRegistry
from agora.protocol.hashing import canonical_sha256, seal_model_payload
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
from agora.protocol.methodology_run_dispatch import (
    MethodologyRunDispatchClaim,
    MethodologyRunDispatchPolicyDecision,
    MethodologyRunDispatchReceipt,
)
from agora.protocol.methodology_stage_gate import (
    MethodologyStageGateReceipt,
    MethodologyStageGateRequest,
)
from agora.protocol.methodology_stage_run_claim import (
    MethodologyStageRunClaimReceipt,
    MethodologyStageRunClaimRequest,
    methodology_stage_run_id,
)
from agora.protocol.methodology_stage_run_dispatch import (
    MethodologyStageRunDispatchClaim,
    MethodologyStageRunDispatchPolicyDecision,
    MethodologyStageRunDispatchReceipt,
)
from agora.protocol.models import (
    ContextPack,
    HandoffPack,
    ProviderUsageObservation,
)
from agora.protocol.schema_registry import SCHEMA_MODELS
from agora.tasks.models import utc_now
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


def _context_from_prompt(prompt: str) -> ContextPack:
    value = prompt.split("SEALED CONTEXT PACK (canonical JSON):\n", 1)[1]
    value = value.split("\nEND SEALED CONTEXT PACK", 1)[0]
    return ContextPack.model_validate_json(value)


class MethodologyDispatchRunner:
    def __init__(self, *, fail_before_process: bool = False):
        self.fail_before_process = fail_before_process
        self.calls = 0
        self.pid = 515_151
        self.prompts: list[str] = []

    async def run(self, runtime, prompt, **kwargs):
        self.calls += 1
        self.prompts.append(prompt)
        if self.fail_before_process:
            raise RuntimeError("injected pre-process boundary failure")
        if kwargs.get("before_spawn") is not None:
            kwargs["before_spawn"](
                runtime,
                resolve_runtime_command(runtime.build(prompt)),
            )
        await kwargs["on_process"](self.pid)
        context = _context_from_prompt(prompt)
        artifacts = []
        for output in context.required_outputs:
            content = json.dumps(
                {
                    "output_id": output.output_id,
                    "result": "methodology stage completed",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            artifacts.append(
                {
                    "schema_version": "1.0",
                    "artifact_id": output.output_id,
                    "project_id": context.project_id,
                    "task_id": context.task_id,
                    "stage_key": context.stage_key,
                    "producer": {
                        "runtime": runtime.adapter,
                        "run_id": context.run_id,
                        "stage_key": context.stage_key,
                    },
                    "kind": output.kind,
                    "storage": "managed",
                    "version": 1,
                    "sha256": hashlib.sha256(
                        content.encode("utf-8")
                    ).hexdigest(),
                    "media_type": "application/json",
                    "content": content,
                    "location": None,
                    "created_at": utc_now(),
                }
            )
        artifact_refs = [
            {
                key: artifact[key]
                for key in ("artifact_id", "version", "sha256", "kind", "location")
            }
            for artifact in artifacts
        ]
        requirements = json.loads(
            prompt.split("FORMAL GATE REQUIREMENTS:\n", 1)[1].split(
                "\n\nSEALED CONTEXT PACK", 1
            )[0]
        )
        evidence = [
            {
                "schema_version": "1.0",
                "evidence_id": (
                    f"evidence:{context.run_id}:{item['requirement_id']}"
                ),
                "project_id": context.project_id,
                "task_id": context.task_id,
                "stage_key": context.stage_key,
                "producer": {
                    "runtime": runtime.adapter,
                    "run_id": context.run_id,
                    "stage_key": context.stage_key,
                },
                "repository_id": item["repository_id"],
                "ref": item["ref"],
                "commit_sha": item["commit_sha"],
                "requirement_id": item["requirement_id"],
                "kind": item["evidence_kind"],
                "status": "passed",
                "artifact_versions": artifact_refs,
                "summary": "Observed the exact methodology Gate requirement.",
                "observed_at": utc_now(),
                "details": {},
            }
            for item in requirements
        ]
        payload = {
            "schema_version": "1.0",
            "pack_id": f"handoff:{context.run_id}",
            "project_id": context.project_id,
            "task_id": context.task_id,
            "stage_key": context.stage_key,
            "run_id": context.run_id,
            "producer": {
                "runtime": runtime.adapter,
                "run_id": context.run_id,
                "stage_key": context.stage_key,
            },
            "input_artifacts": [
                item.model_dump(mode="json") for item in context.input_artifacts
            ],
            "required_outputs": [
                item.model_dump(mode="json") for item in context.required_outputs
            ],
            "forbidden_constraints": list(context.forbidden_constraints),
            "stage_result": "succeeded",
            "output_artifacts": artifacts,
            "evidence": evidence,
            "unresolved_questions": [],
            "native_state_snapshot": None,
            "memory_candidates": [],
            "blocker_requirement_ids": [],
            "suggested_next_action": None,
        }
        usage = ProviderUsageObservation.model_validate(
            seal_model_payload(
                ProviderUsageObservation,
                {
                    "schema_version": "1.0",
                    "run_id": context.run_id,
                    "adapter": runtime.adapter,
                    "provider": "openai",
                    "source": "codex_exec_jsonl",
                    "source_payload_sha256": canonical_sha256(
                        {
                            "runner": "methodology-test",
                            "run_id": context.run_id,
                        }
                    ),
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "total_tokens": 2,
                    "token_measurement": "exact",
                    "token_method": "provider_input_plus_output",
                    "cost_usd": None,
                    "cost_measurement": "unavailable",
                    "cost_method": "unavailable",
                },
            )
        )
        return RuntimeResult(
            0,
            json.dumps(
                seal_model_payload(HandoffPack, payload),
                ensure_ascii=False,
            ),
            "",
            usage_observation=usage,
        )


class UnboundSeedOutputMethodologyDispatchRunner(MethodologyDispatchRunner):
    async def run(self, runtime, prompt, **kwargs):
        result = await super().run(runtime, prompt, **kwargs)
        context = _context_from_prompt(prompt)
        seed = next(
            item
            for item in context.input_artifacts
            if item.location is not None
        )
        assert seed.location is not None
        payload = json.loads(result.stdout)
        payload["output_artifacts"].append(
            {
                "schema_version": "1.0",
                "artifact_id": seed.artifact_id,
                "project_id": context.project_id,
                "task_id": context.task_id,
                "stage_key": context.stage_key,
                "producer": payload["producer"],
                "kind": seed.kind,
                "storage": "referenced",
                "version": seed.version,
                "sha256": seed.sha256,
                "media_type": "application/json",
                "content": None,
                "location": seed.location.model_dump(mode="json"),
                "created_at": utc_now(),
            }
        )
        payload = seal_model_payload(
            HandoffPack,
            {key: value for key, value in payload.items() if key != "content_sha256"},
        )
        return replace(
            result,
            stdout=json.dumps(payload, ensure_ascii=False),
        )


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


def _claimed_methodology_run(
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
    run_claim = service.claim_methodology_first_run(
        contract.task_id,
        request,
        principal=_migration_principal(),
    )
    return tasks, service, contract, run_claim


async def _dispatch_methodology_run(service, task_id):
    return await service.dispatch_methodology_first_run(
        task_id,
        allow_unbounded_native_usage=True,
    )


@pytest.mark.asyncio
async def test_methodology_dispatch_service_requires_explicit_acknowledgement(
    tmp_path,
):
    tasks, service, contract, _ = _claimed_methodology_run(tmp_path)
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationValidationError,
        match="acknowledgement",
    ):
        await service.dispatch_methodology_first_run(
            contract.task_id,
            allow_unbounded_native_usage=False,
        )

    assert _database_dump(tasks) == before


@pytest.mark.asyncio
async def test_methodology_dispatch_reuses_claimed_run_and_settles_atomically(
    tmp_path,
):
    tasks, service, contract, run_claim = _claimed_methodology_run(tmp_path)
    runner = MethodologyDispatchRunner()
    service.runner = runner

    receipt = await _dispatch_methodology_run(service, contract.task_id)

    assert isinstance(receipt, MethodologyRunDispatchReceipt)
    assert isinstance(receipt.dispatch_claim, MethodologyRunDispatchClaim)
    assert receipt.dispatch_claim.run_id == run_claim.run_id
    assert receipt.dispatch_claim.context_pack_id == run_claim.context_pack_id
    assert receipt.dispatch_claim.dispatch_policy.dispatchable is True
    assert receipt.dispatch_claim.runtime_preflight.allowed is True
    assert (
        receipt.dispatch_claim.runtime_preflight.routing_policy_decision_id
        == receipt.dispatch_claim.dispatch_policy.decision_id
    )
    assert (
        receipt.dispatch_claim.runtime_preflight.routing_policy_decision_sha256
        == receipt.dispatch_claim.dispatch_policy.content_sha256
    )
    assert receipt.dispatch_claim.existing_formal_run_reused is True
    assert receipt.dispatch_claim.existing_context_pack_reused is True
    assert receipt.dispatch_claim.compatibility_run_created is False
    assert receipt.process_started is True
    assert receipt.pid == runner.pid
    assert receipt.protocol_settled is True
    assert receipt.provider_substitution is False
    assert runner.calls == 1

    dispatch = service.store.get_methodology_run_dispatch(contract.task_id)
    assert dispatch is not None
    assert dispatch.state == MethodologyDispatchState.SETTLED
    assert dispatch.receipt == receipt
    protocol_run = service.control_plane.get_protocol_run(run_claim.run_id)
    assert protocol_run is not None
    assert protocol_run.settled_at is not None
    assert protocol_run.protocol_state == receipt.protocol_state
    assert protocol_run.handoff_pack is not None
    assert protocol_run.handoff_pack.pack_id == receipt.handoff_pack_id
    unified = service.unified_status(contract.task_id)
    assert unified.schema_version == "12.0"
    assert len(unified.runs) == 1
    assert unified.runs[0].run_id == run_claim.run_id
    assert unified.runs[0].runtime_preflight == (
        receipt.dispatch_claim.runtime_preflight
    )
    assert unified.runs[0].usage_observation == receipt.usage_observation
    assert unified.runs[0].wait_state.value == "settled"
    assert unified.budget.token_reserved == 0
    assert unified.collection_totals["usage"] == 1
    assert len(unified.usage) == 1
    assert unified.usage[0].run_id == run_claim.run_id
    with sqlite3.connect(tasks.db_path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM protocol_runs WHERE task_id = ?",
            (contract.task_id,),
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM orchestration_runs WHERE task_id = ?",
            (contract.task_id,),
        ).fetchone()[0] == 0
        assert db.execute(
            """
            SELECT COUNT(*) FROM orchestration_methodology_run_dispatches
            WHERE task_id = ? AND state = 'settled'
            """,
            (contract.task_id,),
        ).fetchone()[0] == 1
        assert db.execute(
            """
            SELECT COUNT(*) FROM orchestration_methodology_usage_ledger
            WHERE task_id = ?
            """,
            (contract.task_id,),
        ).fetchone()[0] == 1
        assert db.execute(
            """
            SELECT process_started FROM orchestration_methodology_run_claims
            WHERE task_id = ?
            """,
            (contract.task_id,),
        ).fetchone()[0] == 0

    replay = await _dispatch_methodology_run(service, contract.task_id)
    assert replay == receipt
    assert runner.calls == 1


@pytest.mark.asyncio
async def test_methodology_dispatch_pre_spawn_failure_settles_exact_zero(
    tmp_path,
):
    tasks, service, contract, run_claim = _claimed_methodology_run(tmp_path)
    runner = MethodologyDispatchRunner(fail_before_process=True)
    service.runner = runner

    receipt = await _dispatch_methodology_run(service, contract.task_id)

    assert receipt.process_started is False
    assert receipt.pid is None
    assert receipt.exit_code is None
    assert receipt.usage_observation.total_tokens == 0
    assert receipt.usage_observation.token_measurement == "exact"
    assert receipt.usage_observation.cost_usd == 0
    assert receipt.usage_observation.cost_measurement == "exact"
    assert receipt.protocol_state.process_status.value == "launch_failed"
    assert receipt.stage_status.value == "failed"
    assert receipt.handoff_pack_id is None
    assert runner.calls == 1
    with sqlite3.connect(tasks.db_path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM protocol_runs WHERE run_id = ?",
            (run_claim.run_id,),
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM orchestration_runs WHERE task_id = ?",
            (contract.task_id,),
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_methodology_dispatch_immediate_repository_recheck_blocks_spawn(
    tmp_path,
):
    _, service, contract, _ = _claimed_methodology_run(tmp_path)
    runner = MethodologyDispatchRunner()
    service.runner = runner
    calls = 0

    def changing_revision(_root, _repository_id):
        nonlocal calls
        calls += 1
        if calls >= 3:
            return RepositoryRevision(
                repository_id=REVISION.repository_id,
                ref=REVISION.ref,
                commit_sha="b" * 40,
            )
        return REVISION

    service.revision_resolver = changing_revision
    receipt = await _dispatch_methodology_run(service, contract.task_id)

    assert receipt.process_started is False
    assert receipt.protocol_state.process_status.value == "launch_failed"
    assert runner.calls == 0
    assert calls == 3


@pytest.mark.asyncio
async def test_methodology_dispatch_blocked_preflight_has_zero_mutation(
    tmp_path,
):
    tasks, service, contract, _ = _claimed_methodology_run(tmp_path)
    service.runtimes["codex"] = RuntimeCommand(
        adapter="codex",
        command_template=("agora-missing-methodology-runtime", "{prompt}"),
    )
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationConflictError,
        match="unavailable|changed",
    ):
        await _dispatch_methodology_run(service, contract.task_id)

    assert _database_dump(tasks) == before
    assert service.store.get_methodology_run_dispatch(contract.task_id) is None


def test_methodology_dispatch_policy_reports_unbounded_reservation_as_blocked(
    tmp_path,
):
    _, service, contract, _ = _claimed_methodology_run(tmp_path)
    snapshot = service.store.methodology_run_dispatch_snapshot(
        contract.task_id,
        control_plane=service.control_plane,
    )
    unbounded_budget = snapshot.run_claim_receipt.budget.model_copy(
        update={"max_model_tokens": None}
    )
    unbounded_claim = snapshot.run_claim_receipt.model_copy(
        update={"budget": unbounded_budget}
    )

    decision = derive_methodology_run_dispatch_policy(
        snapshot=replace(
            snapshot,
            run_claim_receipt=unbounded_claim,
        ),
        repository=REVISION,
        runtimes=service.runtimes,
        evaluated_at=utc_now(),
    )

    assert decision.dispatchable is False
    assert decision.token_reservation == 0
    assert any(
        item.check == "usage_reservation" and not item.satisfied
        for item in decision.checks
    )


@pytest.mark.asyncio
async def test_methodology_dispatch_collection_occurs_before_write_claim(
    tmp_path,
):
    tasks, service, contract, _ = _claimed_methodology_run(tmp_path)
    runner = MethodologyDispatchRunner(fail_before_process=True)
    service.runner = runner
    observed_without_claim = False

    async def collector(runtimes):
        nonlocal observed_without_claim
        with sqlite3.connect(tasks.db_path, timeout=0.1) as db:
            db.execute("BEGIN IMMEDIATE")
            observed_without_claim = (
                db.execute(
                    """
                    SELECT COUNT(*)
                    FROM orchestration_methodology_run_dispatches
                    """
                ).fetchone()[0]
                == 0
            )
            db.rollback()
        return await collect_native_runtime_capabilities(runtimes)

    service.capability_collector = collector
    await _dispatch_methodology_run(service, contract.task_id)

    assert observed_without_claim is True


@pytest.mark.asyncio
async def test_methodology_dispatch_registry_drift_inside_runner_blocks_spawn(
    tmp_path,
):
    _, service, contract, _ = _claimed_methodology_run(tmp_path)

    class DriftingRunner:
        def __init__(self):
            self.calls = 0

        async def run(self, runtime, prompt, **kwargs):
            self.calls += 1
            service.runtimes["codex"] = RuntimeCommand(
                adapter="codex",
                command_template=(sys.executable, "changed", "{prompt}"),
            )
            kwargs["before_spawn"](
                runtime,
                resolve_runtime_command(runtime.build(prompt)),
            )
            raise AssertionError("preflight drift must reject before process")

    runner = DriftingRunner()
    service.runner = runner
    receipt = await _dispatch_methodology_run(service, contract.task_id)

    assert runner.calls == 1
    assert receipt.process_started is False
    assert receipt.protocol_state.process_status.value == "launch_failed"
    assert receipt.usage_observation.total_tokens == 0


@pytest.mark.asyncio
async def test_methodology_dispatch_concurrency_never_spawns_twice(tmp_path):
    _, service, contract, _ = _claimed_methodology_run(tmp_path)
    runner = MethodologyDispatchRunner()
    service.runner = runner

    results = await asyncio.gather(
        _dispatch_methodology_run(service, contract.task_id),
        _dispatch_methodology_run(service, contract.task_id),
        return_exceptions=True,
    )

    assert runner.calls == 1
    assert sum(isinstance(item, MethodologyRunDispatchReceipt) for item in results) == 1
    assert sum(isinstance(item, OrchestrationConflictError) for item in results) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("attach_process", "expected_process_status", "expected_stage_status"),
    [
        (False, "launch_failed", "failed"),
        (True, "cancelled", "cancelled"),
    ],
)
async def test_methodology_dispatch_cancellation_settles_before_propagating(
    tmp_path,
    attach_process,
    expected_process_status,
    expected_stage_status,
):
    _, service, contract, _ = _claimed_methodology_run(tmp_path)

    class CancellingRunner:
        async def run(self, _runtime, _prompt, **kwargs):
            if attach_process:
                await kwargs["on_process"](717_171)
            raise asyncio.CancelledError

    service.runner = CancellingRunner()
    with pytest.raises(asyncio.CancelledError):
        await _dispatch_methodology_run(service, contract.task_id)

    dispatch = service.store.get_methodology_run_dispatch(contract.task_id)
    assert dispatch is not None
    assert dispatch.state == MethodologyDispatchState.SETTLED
    assert dispatch.receipt is not None
    assert (
        dispatch.receipt.protocol_state.process_status.value
        == expected_process_status
    )
    assert dispatch.receipt.stage_status.value == expected_stage_status


@pytest.mark.asyncio
async def test_methodology_dispatch_crash_recovery_refuses_live_pid_then_settles(
    tmp_path,
):
    _, service, contract, _ = _claimed_methodology_run(tmp_path)

    class CrashingRunner:
        async def run(self, _runtime, _prompt, **kwargs):
            await kwargs["on_process"](616_161)
            raise GeneratorExit("simulated host crash")

    service.runner = CrashingRunner()
    with pytest.raises(GeneratorExit, match="simulated host crash"):
        await _dispatch_methodology_run(service, contract.task_id)
    dispatch = service.store.get_methodology_run_dispatch(contract.task_id)
    assert dispatch is not None
    assert dispatch.state == MethodologyDispatchState.RUNNING
    assert dispatch.pid == 616_161

    service.process_inspector = lambda _pid: ProcessState.ALIVE
    with pytest.raises(
        OrchestrationConflictError,
        match="refusing duplicate dispatch",
    ):
        service.resume(contract.task_id)

    service.process_inspector = lambda _pid: ProcessState.DEAD
    service.resume(contract.task_id)
    recovered = service.store.get_methodology_run_dispatch(contract.task_id)
    assert recovered is not None
    assert recovered.state == MethodologyDispatchState.SETTLED
    assert recovered.receipt is not None
    assert recovered.receipt.process_started is True
    assert recovered.receipt.protocol_state.process_status.value == "interrupted"


@pytest.mark.asyncio
async def test_methodology_dispatch_finalization_recovers_after_event_rollback(
    tmp_path,
    monkeypatch,
):
    tasks, service, contract, run_claim = _claimed_methodology_run(tmp_path)
    service.runner = MethodologyDispatchRunner()
    original_event = service.control_plane._event

    def fail_final_event(*args, **kwargs):
        if kwargs.get("event_type") == "methodology.run_dispatch_settled":
            raise RuntimeError("injected dispatch finalization event failure")
        return original_event(*args, **kwargs)

    monkeypatch.setattr(service.control_plane, "_event", fail_final_event)
    with pytest.raises(
        RuntimeError,
        match="dispatch finalization event failure",
    ):
        await _dispatch_methodology_run(service, contract.task_id)

    dispatch = service.store.get_methodology_run_dispatch(contract.task_id)
    assert dispatch is not None
    assert dispatch.state == MethodologyDispatchState.TERMINAL_OBSERVED
    assert service.control_plane.get_protocol_run(
        dispatch.claim.run_id
    ).settled_at is not None
    with sqlite3.connect(tasks.db_path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM orchestration_methodology_usage_ledger"
        ).fetchone()[0] == 0
    interrupted_projection = service.unified_status(contract.task_id)
    assert (
        interrupted_projection.budget.token_reserved
        == run_claim.budget.max_model_tokens
    )

    monkeypatch.setattr(service.control_plane, "_event", original_event)
    receipt = await _dispatch_methodology_run(service, contract.task_id)
    assert receipt.protocol_settled is True
    assert (
        service.store.get_methodology_run_dispatch(contract.task_id).state
        == MethodologyDispatchState.SETTLED
    )
    assert service.unified_status(contract.task_id).budget.token_reserved == 0


@pytest.mark.asyncio
async def test_methodology_dispatch_persisted_preflight_tamper_fails_closed(
    tmp_path,
):
    tasks, service, contract, _ = _claimed_methodology_run(tmp_path)
    service.runner = MethodologyDispatchRunner(fail_before_process=True)
    await _dispatch_methodology_run(service, contract.task_id)
    with sqlite3.connect(tasks.db_path) as db:
        payload = json.loads(
            db.execute(
                """
                SELECT preflight_payload
                FROM orchestration_methodology_run_dispatches
                WHERE task_id = ?
                """,
                (contract.task_id,),
            ).fetchone()[0]
        )
        payload["rationale"][0] = "tampered methodology preflight"
        db.execute(
            """
            UPDATE orchestration_methodology_run_dispatches
            SET preflight_payload = ? WHERE task_id = ?
            """,
            (json.dumps(payload), contract.task_id),
        )
        db.commit()

    with pytest.raises(
        (ValidationError, OrchestrationValidationError),
        match="content_sha256|binding drifted",
    ):
        service.store.get_methodology_run_dispatch(contract.task_id)


@pytest.mark.asyncio
async def test_methodology_dispatch_persisted_policy_tamper_fails_closed(
    tmp_path,
):
    tasks, service, contract, _ = _claimed_methodology_run(tmp_path)
    service.runner = MethodologyDispatchRunner(fail_before_process=True)
    await _dispatch_methodology_run(service, contract.task_id)
    with sqlite3.connect(tasks.db_path) as db:
        payload = json.loads(
            db.execute(
                """
                SELECT dispatch_policy_payload
                FROM orchestration_methodology_run_dispatches
                WHERE task_id = ?
                """,
                (contract.task_id,),
            ).fetchone()[0]
        )
        payload["checks"][0]["detail"] = "tampered methodology dispatch policy"
        db.execute(
            """
            UPDATE orchestration_methodology_run_dispatches
            SET dispatch_policy_payload = ? WHERE task_id = ?
            """,
            (json.dumps(payload), contract.task_id),
        )
        db.commit()

    with pytest.raises(
        (ValidationError, OrchestrationValidationError),
        match="content_sha256|binding drifted",
    ):
        service.store.get_methodology_run_dispatch(contract.task_id)


@pytest.mark.asyncio
async def test_methodology_dispatch_terminal_fact_tamper_fails_closed(
    tmp_path,
):
    tasks, service, contract, _ = _claimed_methodology_run(tmp_path)
    service.runner = MethodologyDispatchRunner()
    await _dispatch_methodology_run(service, contract.task_id)
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            UPDATE orchestration_methodology_run_dispatches
            SET output = ? WHERE task_id = ?
            """,
            ("tampered terminal output", contract.task_id),
        )
        db.commit()

    with pytest.raises(
        (ValidationError, OrchestrationValidationError),
        match="receipt differs|binding drifted",
    ):
        service.store.get_methodology_run_dispatch(contract.task_id)


def test_methodology_dispatch_cli_requires_acknowledgement_before_store(
    monkeypatch,
    capsys,
):
    built = False

    def build_forbidden():
        nonlocal built
        built = True
        raise AssertionError("service must not build before acknowledgement")

    monkeypatch.setattr(orchestration_cli, "build_service", build_forbidden)
    code = orchestration_cli.main(
        ["migration-run-dispatch", "task-successor"]
    )

    assert code == 2
    assert built is False
    assert "--allow-unbounded-native-usage" in capsys.readouterr().out


def test_methodology_dispatch_cli_emits_terminal_receipt(
    tmp_path,
    monkeypatch,
    capsys,
):
    _, service, contract, _ = _claimed_methodology_run(tmp_path)
    service.runner = MethodologyDispatchRunner(fail_before_process=True)
    monkeypatch.setattr(orchestration_cli, "build_service", lambda: service)

    code = orchestration_cli.main(
        [
            "migration-run-dispatch",
            contract.task_id,
            "--allow-unbounded-native-usage",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["dispatch_claim"]["task_id"] == contract.task_id
    assert payload["process_started"] is False
    assert payload["protocol_settled"] is True
    assert payload["provider_substitution"] is False


def test_methodology_run_dispatch_schemas_are_registered():
    assert (
        SCHEMA_MODELS["methodology-run-dispatch-policy-decision"]
        is MethodologyRunDispatchPolicyDecision
    )
    assert (
        SCHEMA_MODELS["methodology-run-dispatch-claim"]
        is MethodologyRunDispatchClaim
    )
    assert (
        SCHEMA_MODELS["methodology-run-dispatch-receipt"]
        is MethodologyRunDispatchReceipt
    )


async def _settled_first_methodology_run(tmp_path):
    tasks, service, contract, run_claim = _claimed_methodology_run(tmp_path)
    service.runner = MethodologyDispatchRunner()
    dispatch = await _dispatch_methodology_run(service, contract.task_id)
    assert dispatch.stage_status.value == "completed"
    assert dispatch.gate_status.value == "passed"
    assert dispatch.next_stage_key == contract.stages[1].stage_key
    return tasks, service, contract, run_claim, dispatch


def _stage_gate_request(
    tasks: TaskStore,
    service: TaskOrchestrationService,
    contract: MethodologyExecutionContract,
    dispatch: MethodologyRunDispatchReceipt,
) -> MethodologyStageGateRequest:
    task = tasks.get(contract.task_id)
    control_task = service.control_plane.get_task_state(contract.task_id)
    inventory = service.control_plane.get_stage_inventory(contract.task_id)
    route = service.control_plane.get_stage_route(contract.task_id)
    status = service.status(contract.task_id)
    assert task is not None
    assert control_task is not None
    assert inventory is not None
    assert route is not None
    formal_stage = service.control_plane.get_stage(
        contract.task_id,
        route.stage_key,
    )
    assert formal_stage is not None
    assert dispatch.handoff_pack_id is not None
    assert dispatch.handoff_pack_sha256 is not None
    stage = contract.stages[1]
    payload = {
        "schema_version": "1.0",
        "request_id": f"stage-gate-request-{contract.content_sha256[:20]}",
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
        "predecessor_dispatch_receipt_id": dispatch.receipt_id,
        "predecessor_dispatch_receipt_sha256": dispatch.content_sha256,
        "predecessor_run_id": dispatch.dispatch_claim.run_id,
        "predecessor_stage_key": contract.stages[0].stage_key,
        "predecessor_gate_key": contract.stages[0].gate_key,
        "predecessor_handoff_pack_id": dispatch.handoff_pack_id,
        "predecessor_handoff_pack_sha256": dispatch.handoff_pack_sha256,
        "repository": contract.repository.model_dump(mode="json"),
        "stage_sequence": stage.sequence,
        "stage_key": stage.stage_key,
        "gate_key": stage.gate_key,
        "runtime": stage.runtime,
        "expected_stage_version": formal_stage.version,
        "configure_formal_gate": True,
        "claim_formal_run": False,
        "start_runtime_process": False,
    }
    return MethodologyStageGateRequest.model_validate(
        seal_model_payload(MethodologyStageGateRequest, payload)
    )


def _reseal_stage_gate_request(
    request: MethodologyStageGateRequest,
    **updates,
) -> MethodologyStageGateRequest:
    payload = request.model_dump(mode="json")
    payload.pop("content_sha256")
    payload.update(updates)
    return MethodologyStageGateRequest.model_validate(
        seal_model_payload(MethodologyStageGateRequest, payload)
    )


@pytest.mark.asyncio
async def test_methodology_next_stage_gate_is_atomic_and_non_dispatching(
    tmp_path,
):
    tasks, service, contract, _, dispatch = (
        await _settled_first_methodology_run(tmp_path)
    )
    request = _stage_gate_request(tasks, service, contract, dispatch)
    task_before = tasks.get(contract.task_id)
    control_before = service.control_plane.get_task_state(contract.task_id)
    plan_before = service.status(contract.task_id).plan
    stage_before = service.control_plane.get_stage(
        contract.task_id,
        request.stage_key,
    )
    route_before = service.control_plane.get_stage_route(contract.task_id)
    with sqlite3.connect(tasks.db_path) as db:
        before_counts = tuple(
            db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE task_id = ?",
                (contract.task_id,),
            ).fetchone()[0]
            for table in (
                "protocol_runs",
                "protocol_artifacts",
                "protocol_evidence",
                "orchestration_runs",
                "orchestration_consultations",
            )
        )
    assert route_before is not None
    assert route_before.runnable is True
    assert route_before.gate_status is None

    receipt = service.configure_methodology_next_stage_gate(
        contract.task_id,
        request,
        principal=_migration_principal(),
    )

    assert isinstance(receipt, MethodologyStageGateReceipt)
    assert receipt.request_id == request.request_id
    assert receipt.request_sha256 == request.content_sha256
    assert receipt.predecessor_dispatch_receipt_id == dispatch.receipt_id
    assert receipt.predecessor_dispatch_receipt_sha256 == dispatch.content_sha256
    assert receipt.predecessor_run_id == dispatch.dispatch_claim.run_id
    assert receipt.stage_sequence == 2
    assert receipt.stage_key == contract.stages[1].stage_key
    assert receipt.gate_key == contract.stages[1].gate_key
    assert receipt.route_runnable_before is True
    assert receipt.route_runnable_after is True
    assert receipt.formal_run_claimable_before is False
    assert receipt.formal_run_claimable_after is True
    assert receipt.run_created is False
    assert receipt.context_pack_created is False
    assert receipt.process_started is False
    assert receipt.dispatch_authority is False
    assert receipt.requirements == sorted(
        [
            item.requirement
            for item in contract.stages[1].gate.evidence_contracts
        ],
        key=lambda item: item.requirement_id,
    )
    route_after = service.control_plane.get_stage_route(contract.task_id)
    gate_after = service.control_plane.get_gate(
        contract.task_id,
        request.gate_key,
    )
    assert route_after is not None
    assert route_after.stage_key == request.stage_key
    assert route_after.runnable is True
    assert route_after.gate_status.value == "pending"
    assert gate_after is not None
    assert gate_after.requirements == receipt.requirements
    assert tasks.get(contract.task_id) == task_before
    assert service.control_plane.get_task_state(contract.task_id) == control_before
    assert service.status(contract.task_id).plan == plan_before
    assert (
        service.control_plane.get_stage(contract.task_id, request.stage_key)
        == stage_before
    )
    with sqlite3.connect(tasks.db_path) as db:
        after_counts = tuple(
            db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE task_id = ?",
                (contract.task_id,),
            ).fetchone()[0]
            for table in (
                "protocol_runs",
                "protocol_artifacts",
                "protocol_evidence",
                "orchestration_runs",
                "orchestration_consultations",
            )
        )
        gate_rows = db.execute(
            """
            SELECT COUNT(*) FROM orchestration_methodology_stage_gates
            WHERE task_id = ?
            """,
            (contract.task_id,),
        ).fetchone()[0]
    assert after_counts == before_counts
    assert gate_rows == 1
    assert (
        service.store.get_methodology_stage_gate(
            contract.task_id,
            request.stage_key,
        )
        == receipt
    )


@pytest.mark.asyncio
async def test_methodology_next_stage_gate_exact_replay_and_conflict(
    tmp_path,
):
    tasks, service, contract, _, dispatch = (
        await _settled_first_methodology_run(tmp_path)
    )
    request = _stage_gate_request(tasks, service, contract, dispatch)
    receipt = service.configure_methodology_next_stage_gate(
        contract.task_id,
        request,
        principal=_migration_principal(),
    )
    before = _database_dump(tasks)

    replay = service.configure_methodology_next_stage_gate(
        contract.task_id,
        request,
        principal=_migration_principal(),
    )

    assert replay == receipt
    assert _database_dump(tasks) == before
    changed = _reseal_stage_gate_request(
        request,
        request_id=f"{request.request_id}-different",
    )
    with pytest.raises(
        OrchestrationConflictError,
        match="replay binding differs",
    ):
        service.configure_methodology_next_stage_gate(
            contract.task_id,
            changed,
            principal=_migration_principal(),
        )
    with pytest.raises(
        OrchestrationValidationError,
        match="original currently authorized principal",
    ):
        service.configure_methodology_next_stage_gate(
            contract.task_id,
            request,
            principal=_migration_principal(principal_id="other"),
        )
    assert _database_dump(tasks) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_task_version", 999),
        ("expected_control_task_version", 999),
        ("expected_plan_version", 999),
        ("inventory_sha256", "0" * 64),
        ("execution_contract_sha256", "0" * 64),
        ("predecessor_dispatch_receipt_sha256", "0" * 64),
        ("predecessor_run_id", "wrong-run"),
        ("predecessor_handoff_pack_sha256", "0" * 64),
        ("stage_key", "wrong-stage"),
        ("gate_key", "wrong-gate"),
        ("runtime", "wrong-runtime"),
        ("expected_stage_version", 999),
    ],
)
async def test_methodology_next_stage_gate_stale_bindings_are_zero_write(
    tmp_path,
    field,
    value,
):
    tasks, service, contract, _, dispatch = (
        await _settled_first_methodology_run(tmp_path)
    )
    request = _stage_gate_request(tasks, service, contract, dispatch)
    changed = _reseal_stage_gate_request(request, **{field: value})
    before = _database_dump(tasks)

    with pytest.raises(
        (OrchestrationConflictError, OrchestrationValidationError),
        match="binding|route|unavailable|blocked",
    ):
        service.configure_methodology_next_stage_gate(
            contract.task_id,
            changed,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before
    assert (
        service.control_plane.get_gate(contract.task_id, request.gate_key)
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "principal",
    [
        _migration_principal(permissions=frozenset()),
        _migration_principal(projects=frozenset()),
        _migration_principal(principal_id="other"),
    ],
)
async def test_methodology_next_stage_gate_authorization_is_zero_write(
    tmp_path,
    principal,
):
    tasks, service, contract, _, dispatch = (
        await _settled_first_methodology_run(tmp_path)
    )
    request = _stage_gate_request(tasks, service, contract, dispatch)
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationConflictError,
        match="blocked",
    ):
        service.configure_methodology_next_stage_gate(
            contract.task_id,
            request,
            principal=principal,
        )

    assert _database_dump(tasks) == before


@pytest.mark.asyncio
async def test_methodology_next_stage_gate_requires_settled_predecessor(
    tmp_path,
):
    tasks, service, contract, _ = _claimed_methodology_run(tmp_path)
    task = tasks.get(contract.task_id)
    control_task = service.control_plane.get_task_state(contract.task_id)
    assert task is not None
    assert control_task is not None
    route = service.control_plane.get_stage_route(contract.task_id)
    assert route is not None
    assert route.stage_key == contract.stages[0].stage_key
    before = _database_dump(tasks)
    request_payload = {
        "schema_version": "1.0",
        "request_id": "premature-stage-gate",
        "requested_at": FIXED_TIME,
        "project_id": contract.project_id,
        "task_id": contract.task_id,
        "expected_task_version": task.version,
        "expected_control_task_version": control_task.version,
        "expected_control_task_status": "active",
        "plan_id": contract.plan_id,
        "expected_plan_version": contract.plan_version,
        "inventory_id": contract.inventory_id,
        "inventory_sha256": contract.inventory_sha256,
        "execution_contract_id": contract.contract_id,
        "execution_contract_sha256": contract.content_sha256,
        "predecessor_dispatch_receipt_id": "missing-receipt",
        "predecessor_dispatch_receipt_sha256": "0" * 64,
        "predecessor_run_id": "missing-run",
        "predecessor_stage_key": contract.stages[0].stage_key,
        "predecessor_gate_key": contract.stages[0].gate_key,
        "predecessor_handoff_pack_id": "missing-handoff",
        "predecessor_handoff_pack_sha256": "0" * 64,
        "repository": contract.repository.model_dump(mode="json"),
        "stage_sequence": 2,
        "stage_key": contract.stages[1].stage_key,
        "gate_key": contract.stages[1].gate_key,
        "runtime": contract.stages[1].runtime,
        "expected_stage_version": 2,
        "configure_formal_gate": True,
        "claim_formal_run": False,
        "start_runtime_process": False,
    }
    request = MethodologyStageGateRequest.model_validate(
        seal_model_payload(
            MethodologyStageGateRequest,
            request_payload,
        )
    )

    with pytest.raises(
        OrchestrationConflictError,
        match="dispatch is unavailable",
    ):
        service.configure_methodology_next_stage_gate(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before


@pytest.mark.asyncio
async def test_methodology_next_stage_gate_repository_and_runtime_drift_block(
    tmp_path,
):
    tasks, service, contract, _, dispatch = (
        await _settled_first_methodology_run(tmp_path)
    )
    request = _stage_gate_request(tasks, service, contract, dispatch)
    before = _database_dump(tasks)
    service.revision_resolver = (
        lambda _root, _repository_id: replace(
            REVISION,
            commit_sha="b" * 40,
        )
    )
    with pytest.raises(OrchestrationConflictError, match="repository binding"):
        service.configure_methodology_next_stage_gate(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )
    assert _database_dump(tasks) == before

    service.revision_resolver = lambda _root, _repository_id: REVISION
    service.runtimes["codex"] = RuntimeCommand(
        adapter="codex",
        command_template=(sys.executable, "changed", "{prompt}"),
    )
    with pytest.raises(OrchestrationConflictError, match="runtime"):
        service.configure_methodology_next_stage_gate(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )
    assert _database_dump(tasks) == before


@pytest.mark.asyncio
async def test_methodology_next_stage_gate_event_failure_rolls_back(
    tmp_path,
    monkeypatch,
):
    tasks, service, contract, _, dispatch = (
        await _settled_first_methodology_run(tmp_path)
    )
    request = _stage_gate_request(tasks, service, contract, dispatch)
    before = _database_dump(tasks)

    def fail_event(*_args, **_kwargs):
        raise RuntimeError("injected Stage Gate event failure")

    monkeypatch.setattr(service.control_plane, "_event", fail_event)
    with pytest.raises(RuntimeError, match="injected Stage Gate event failure"):
        service.configure_methodology_next_stage_gate(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before
    assert (
        service.control_plane.get_gate(contract.task_id, request.gate_key)
        is None
    )


@pytest.mark.asyncio
async def test_methodology_next_stage_gate_concurrent_calls_share_receipt(
    tmp_path,
):
    tasks, service, contract, _, dispatch = (
        await _settled_first_methodology_run(tmp_path)
    )
    request = _stage_gate_request(tasks, service, contract, dispatch)
    barrier = Barrier(2)

    def configure():
        barrier.wait()
        return service.configure_methodology_next_stage_gate(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(lambda _index: configure(), range(2)))

    assert receipts[0] == receipts[1]
    with sqlite3.connect(tasks.db_path) as db:
        assert (
            db.execute(
                """
                SELECT COUNT(*) FROM orchestration_methodology_stage_gates
                WHERE task_id = ?
                """,
                (contract.task_id,),
            ).fetchone()[0]
            == 1
        )


@pytest.mark.asyncio
async def test_methodology_next_stage_gate_loader_and_cli(
    tmp_path,
    monkeypatch,
    capsys,
):
    tasks, service, contract, _, dispatch = (
        await _settled_first_methodology_run(tmp_path)
    )
    request = _stage_gate_request(tasks, service, contract, dispatch)
    request_path = tmp_path / "stage-gate.json"
    request_path.write_text(request.model_dump_json(), encoding="utf-8")
    assert load_methodology_stage_gate_request(request_path) == request
    secret = "stage-gate-control-secret"
    monkeypatch.setenv("AGORA_TEST_STAGE_GATE_TOKEN", secret)
    monkeypatch.setattr(orchestration_cli, "build_service", lambda: service)
    monkeypatch.setattr(
        "agora.control_plane.auth.get_config",
        lambda: {
            "control_plane": {
                "auth": {
                    "credentials": [
                        {
                            "secret_ref": "AGORA_TEST_STAGE_GATE_TOKEN",
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
            "migration-next-stage-gate",
            contract.task_id,
            "--request",
            str(request_path),
            "--credential-env",
            "AGORA_TEST_STAGE_GATE_TOKEN",
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert code == 0
    assert payload["stage_key"] == contract.stages[1].stage_key
    assert payload["formal_gate_configured"] is True
    assert payload["run_created"] is False
    assert payload["process_started"] is False
    assert secret not in output
    assert secret not in _database_dump(tasks)


def test_methodology_next_stage_gate_cli_authenticates_before_store(
    tmp_path,
    monkeypatch,
    capsys,
):
    monkeypatch.delenv("AGORA_MISSING_STAGE_GATE_TOKEN", raising=False)
    built = False

    def build_forbidden():
        nonlocal built
        built = True
        raise AssertionError("service must not be built before authentication")

    monkeypatch.setattr(orchestration_cli, "build_service", build_forbidden)
    code = orchestration_cli.main(
        [
            "migration-next-stage-gate",
            "task-successor",
            "--request",
            str(tmp_path / "missing.json"),
            "--credential-env",
            "AGORA_MISSING_STAGE_GATE_TOKEN",
        ]
    )

    assert code == 2
    assert built is False
    assert "credential environment variable is absent" in capsys.readouterr().out


def test_methodology_stage_gate_schemas_are_registered():
    assert (
        SCHEMA_MODELS["methodology-stage-gate-request"]
        is MethodologyStageGateRequest
    )
    assert (
        SCHEMA_MODELS["methodology-stage-gate-receipt"]
        is MethodologyStageGateReceipt
    )


async def _configured_next_methodology_stage(tmp_path):
    tasks, service, contract, first_claim, dispatch = (
        await _settled_first_methodology_run(tmp_path)
    )
    gate_request = _stage_gate_request(
        tasks,
        service,
        contract,
        dispatch,
    )
    gate_receipt = service.configure_methodology_next_stage_gate(
        contract.task_id,
        gate_request,
        principal=_migration_principal(),
    )
    return (
        tasks,
        service,
        contract,
        first_claim,
        dispatch,
        gate_receipt,
    )


def _stage_run_claim_request(
    tasks: TaskStore,
    service: TaskOrchestrationService,
    contract: MethodologyExecutionContract,
    dispatch: MethodologyRunDispatchReceipt,
    stage_gate: MethodologyStageGateReceipt,
) -> MethodologyStageRunClaimRequest:
    task = tasks.get(contract.task_id)
    control_task = service.control_plane.get_task_state(contract.task_id)
    inventory = service.control_plane.get_stage_inventory(contract.task_id)
    route = service.control_plane.get_stage_route(contract.task_id)
    status = service.status(contract.task_id)
    assert task is not None
    assert control_task is not None
    assert inventory is not None
    assert route is not None
    formal_stage = service.control_plane.get_stage(
        contract.task_id,
        route.stage_key,
    )
    formal_gate = service.control_plane.get_gate(
        contract.task_id,
        route.gate_key,
    )
    assert formal_stage is not None
    assert formal_gate is not None
    stage = contract.stages[1]
    run_id = methodology_stage_run_id(
        task_id=task.task_id,
        execution_contract_sha256=contract.content_sha256,
        stage_sequence=stage.sequence,
        stage_key=stage.stage_key,
    )
    payload = {
        "schema_version": "1.0",
        "request_id": f"stage-run-request-{contract.content_sha256[:20]}",
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
        "stage_gate_receipt_id": stage_gate.receipt_id,
        "stage_gate_receipt_sha256": stage_gate.content_sha256,
        "predecessor_dispatch_receipt_id": dispatch.receipt_id,
        "predecessor_dispatch_receipt_sha256": dispatch.content_sha256,
        "repository": contract.repository.model_dump(mode="json"),
        "stage_sequence": stage.sequence,
        "stage_key": stage.stage_key,
        "gate_key": stage.gate_key,
        "runtime": stage.runtime,
        "expected_stage_version": formal_stage.version,
        "expected_gate_version": formal_gate.version,
        "expected_gate_status": formal_gate.status.value,
        "run_id": run_id,
        "context_pack_id": f"context:{run_id}",
        "context_pack_schema_version": "1.0",
        "claim_formal_run": True,
        "start_runtime_process": False,
    }
    return MethodologyStageRunClaimRequest.model_validate(
        seal_model_payload(MethodologyStageRunClaimRequest, payload)
    )


def _reseal_stage_run_claim_request(
    request: MethodologyStageRunClaimRequest,
    **updates,
) -> MethodologyStageRunClaimRequest:
    payload = request.model_dump(mode="json")
    payload.pop("content_sha256")
    payload.update(updates)
    return MethodologyStageRunClaimRequest.model_validate(
        seal_model_payload(MethodologyStageRunClaimRequest, payload)
    )


@pytest.mark.asyncio
async def test_methodology_sequence_two_run_claim_is_atomic_non_spawning(
    tmp_path,
):
    (
        tasks,
        service,
        contract,
        first_claim,
        dispatch,
        stage_gate,
    ) = await _configured_next_methodology_stage(tmp_path)
    request = _stage_run_claim_request(
        tasks,
        service,
        contract,
        dispatch,
        stage_gate,
    )
    task_before = tasks.get(contract.task_id)
    control_before = service.control_plane.get_task_state(contract.task_id)
    stage_before = service.control_plane.get_stage(
        contract.task_id,
        request.stage_key,
    )
    gate_before = service.control_plane.get_gate(
        contract.task_id,
        request.gate_key,
    )
    assert task_before is not None
    assert control_before is not None
    assert stage_before is not None
    assert gate_before is not None
    assert contract.stages[1].context.input_contracts == []
    assert contract.stages[1].context.output_contracts == []
    with sqlite3.connect(tasks.db_path) as db:
        before_counts = {
            table: db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE task_id = ?",
                (contract.task_id,),
            ).fetchone()[0]
            for table in (
                "protocol_runs",
                "protocol_artifacts",
                "protocol_evidence",
                "orchestration_runs",
                "orchestration_consultations",
            )
        }

    receipt = service.claim_methodology_next_stage_run(
        contract.task_id,
        request,
        principal=_migration_principal(),
    )

    assert isinstance(receipt, MethodologyStageRunClaimReceipt)
    assert receipt.request_id == request.request_id
    assert receipt.request_sha256 == request.content_sha256
    assert receipt.stage_gate_receipt_id == stage_gate.receipt_id
    assert receipt.stage_gate_receipt_sha256 == stage_gate.content_sha256
    assert receipt.predecessor_dispatch_receipt_id == dispatch.receipt_id
    assert receipt.predecessor_dispatch_receipt_sha256 == dispatch.content_sha256
    assert receipt.stage_sequence == 2
    assert receipt.stage_key == contract.stages[1].stage_key
    assert receipt.gate_key == contract.stages[1].gate_key
    assert receipt.input_artifact_bindings == []
    assert receipt.required_outputs == []
    assert receipt.formal_run_created is True
    assert receipt.process_started is False
    assert receipt.process_spawn_authority is False
    task_after = tasks.get(contract.task_id)
    control_after = service.control_plane.get_task_state(contract.task_id)
    stage_after = service.control_plane.get_stage(
        contract.task_id,
        request.stage_key,
    )
    gate_after = service.control_plane.get_gate(
        contract.task_id,
        request.gate_key,
    )
    protocol_run = service.control_plane.get_protocol_run(receipt.run_id)
    assert task_after is not None
    assert task_after.version == task_before.version + 1
    assert task_after.metadata["methodology_run_id"] == first_claim.run_id
    assert task_after.metadata["methodology_current_stage_run_claimed"] is True
    assert task_after.metadata["methodology_current_stage_sequence"] == 2
    assert task_after.metadata["methodology_current_run_id"] == receipt.run_id
    assert control_after is not None
    assert control_after.status.value == "active"
    assert control_after.version >= control_before.version
    assert stage_after is not None
    assert stage_after.status.value == "running"
    assert stage_after.version == stage_before.version + 1
    assert gate_after == gate_before
    assert protocol_run is not None
    assert protocol_run.context_pack.pack_id == receipt.context_pack_id
    assert (
        protocol_run.context_pack.content_sha256
        == receipt.context_pack_sha256
    )
    assert protocol_run.context_pack.input_artifacts == []
    assert protocol_run.context_pack.required_outputs == []
    assert protocol_run.protocol_state is None
    assert protocol_run.handoff_pack is None
    assert len(protocol_run.context_pack.policies) == 5
    assert protocol_run.context_pack.task_memory == []
    assert protocol_run.context_pack.project_knowledge == []
    assert protocol_run.context_pack.user_preferences == []
    with sqlite3.connect(tasks.db_path) as db:
        after_counts = {
            table: db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE task_id = ?",
                (contract.task_id,),
            ).fetchone()[0]
            for table in before_counts
        }
        claims = db.execute(
            """
            SELECT COUNT(*) FROM orchestration_methodology_stage_run_claims
            WHERE task_id = ?
            """,
            (contract.task_id,),
        ).fetchone()[0]
    assert after_counts["protocol_runs"] == before_counts["protocol_runs"] + 1
    for table in (
        "protocol_artifacts",
        "protocol_evidence",
        "orchestration_runs",
        "orchestration_consultations",
    ):
        assert after_counts[table] == before_counts[table]
    assert claims == 1
    assert (
        service.store.get_methodology_stage_run_claim(
            contract.task_id,
            request.stage_key,
        )
        == receipt
    )
    unified = service.unified_status(contract.task_id)
    assert len(unified.runs) == 2
    projected = next(item for item in unified.runs if item.run_id == receipt.run_id)
    assert projected.stage_key == receipt.stage_key
    assert projected.runtime == receipt.runtime
    assert projected.token_reserved == receipt.budget.max_model_tokens
    assert projected.runtime_preflight is None
    assert unified.budget.token_reserved == receipt.budget.max_model_tokens


@pytest.mark.asyncio
async def test_methodology_sequence_two_run_claim_replay_and_conflict(
    tmp_path,
):
    tasks, service, contract, _, dispatch, stage_gate = (
        await _configured_next_methodology_stage(tmp_path)
    )
    request = _stage_run_claim_request(
        tasks,
        service,
        contract,
        dispatch,
        stage_gate,
    )
    receipt = service.claim_methodology_next_stage_run(
        contract.task_id,
        request,
        principal=_migration_principal(),
    )
    before = _database_dump(tasks)

    replay = service.claim_methodology_next_stage_run(
        contract.task_id,
        request,
        principal=_migration_principal(),
    )

    assert replay == receipt
    assert _database_dump(tasks) == before
    changed = _reseal_stage_run_claim_request(
        request,
        request_id=f"{request.request_id}-different",
    )
    with pytest.raises(
        OrchestrationConflictError,
        match="replay binding differs",
    ):
        service.claim_methodology_next_stage_run(
            contract.task_id,
            changed,
            principal=_migration_principal(),
        )
    with pytest.raises(
        OrchestrationValidationError,
        match="original currently authorized principal",
    ):
        service.claim_methodology_next_stage_run(
            contract.task_id,
            request,
            principal=_migration_principal(principal_id="other"),
        )
    assert _database_dump(tasks) == before


@pytest.mark.asyncio
async def test_methodology_sequence_two_run_claim_replay_rejects_ledger_drift(
    tmp_path,
):
    tasks, service, contract, _, dispatch, stage_gate = (
        await _configured_next_methodology_stage(tmp_path)
    )
    request = _stage_run_claim_request(
        tasks,
        service,
        contract,
        dispatch,
        stage_gate,
    )
    service.claim_methodology_next_stage_run(
        contract.task_id,
        request,
        principal=_migration_principal(),
    )
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            UPDATE orchestration_methodology_stage_run_claims
            SET context_pack_sha256 = ?
            WHERE task_id = ?
            """,
            ("0" * 64, contract.task_id),
        )
        db.commit()
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationConflictError,
        match="replay binding differs",
    ):
        service.claim_methodology_next_stage_run(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )
    with pytest.raises(
        OrchestrationValidationError,
        match="Persisted methodology Stage Run claim binding drifted",
    ):
        service.store.get_methodology_stage_run_claim(
            contract.task_id,
            request.stage_key,
        )

    assert _database_dump(tasks) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_task_version", 999),
        ("expected_control_task_version", 999),
        ("expected_plan_version", 999),
        ("inventory_sha256", "0" * 64),
        ("execution_contract_sha256", "0" * 64),
        ("stage_gate_receipt_sha256", "0" * 64),
        ("predecessor_dispatch_receipt_sha256", "0" * 64),
        ("stage_key", "wrong-stage"),
        ("gate_key", "wrong-gate"),
        ("runtime", "wrong-runtime"),
        ("expected_stage_version", 999),
        ("expected_gate_version", 999),
        ("run_id", "wrong-run"),
    ],
)
async def test_methodology_sequence_two_run_claim_stale_is_zero_write(
    tmp_path,
    field,
    value,
):
    tasks, service, contract, _, dispatch, stage_gate = (
        await _configured_next_methodology_stage(tmp_path)
    )
    request = _stage_run_claim_request(
        tasks,
        service,
        contract,
        dispatch,
        stage_gate,
    )
    updates = {field: value}
    if field == "run_id":
        updates["context_pack_id"] = f"context:{value}"
    changed = _reseal_stage_run_claim_request(request, **updates)
    before = _database_dump(tasks)

    with pytest.raises(
        (OrchestrationConflictError, OrchestrationValidationError),
        match="binding|route|unavailable|blocked|differs",
    ):
        service.claim_methodology_next_stage_run(
            contract.task_id,
            changed,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before
    assert (
        service.control_plane.get_protocol_run(changed.run_id)
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "principal",
    [
        _migration_principal(permissions=frozenset()),
        _migration_principal(projects=frozenset()),
        _migration_principal(principal_id="other"),
    ],
)
async def test_methodology_sequence_two_run_claim_auth_is_zero_write(
    tmp_path,
    principal,
):
    tasks, service, contract, _, dispatch, stage_gate = (
        await _configured_next_methodology_stage(tmp_path)
    )
    request = _stage_run_claim_request(
        tasks,
        service,
        contract,
        dispatch,
        stage_gate,
    )
    before = _database_dump(tasks)

    with pytest.raises(
        (OrchestrationConflictError, OrchestrationValidationError),
        match="permission|authorized|principal|Gate",
    ):
        service.claim_methodology_next_stage_run(
            contract.task_id,
            request,
            principal=principal,
        )

    assert _database_dump(tasks) == before


@pytest.mark.asyncio
async def test_methodology_sequence_two_run_claim_requires_gate_ledger(
    tmp_path,
):
    tasks, service, contract, _, dispatch = (
        await _settled_first_methodology_run(tmp_path)
    )
    task = tasks.get(contract.task_id)
    control_task = service.control_plane.get_task_state(contract.task_id)
    status = service.status(contract.task_id)
    inventory = service.control_plane.get_stage_inventory(contract.task_id)
    route = service.control_plane.get_stage_route(contract.task_id)
    assert task is not None
    assert control_task is not None
    assert inventory is not None
    assert route is not None
    run_id = "premature-stage-run"
    payload = {
        "schema_version": "1.0",
        "request_id": "premature-stage-run-request",
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
        "stage_gate_receipt_id": "missing-stage-gate",
        "stage_gate_receipt_sha256": "0" * 64,
        "predecessor_dispatch_receipt_id": dispatch.receipt_id,
        "predecessor_dispatch_receipt_sha256": dispatch.content_sha256,
        "repository": contract.repository.model_dump(mode="json"),
        "stage_sequence": 2,
        "stage_key": contract.stages[1].stage_key,
        "gate_key": contract.stages[1].gate_key,
        "runtime": contract.stages[1].runtime,
        "expected_stage_version": 2,
        "expected_gate_version": 1,
        "expected_gate_status": "pending",
        "run_id": run_id,
        "context_pack_id": f"context:{run_id}",
        "context_pack_schema_version": "1.0",
        "claim_formal_run": True,
        "start_runtime_process": False,
    }
    request = MethodologyStageRunClaimRequest.model_validate(
        seal_model_payload(MethodologyStageRunClaimRequest, payload)
    )
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationConflictError,
        match="Gate configuration is unavailable",
    ):
        service.claim_methodology_next_stage_run(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before


@pytest.mark.asyncio
async def test_methodology_sequence_two_run_claim_repository_runtime_drift(
    tmp_path,
):
    tasks, service, contract, _, dispatch, stage_gate = (
        await _configured_next_methodology_stage(tmp_path)
    )
    request = _stage_run_claim_request(
        tasks,
        service,
        contract,
        dispatch,
        stage_gate,
    )
    before = _database_dump(tasks)
    service.revision_resolver = (
        lambda _root, _repository_id: replace(
            REVISION,
            commit_sha="b" * 40,
        )
    )
    with pytest.raises(OrchestrationConflictError, match="repository binding"):
        service.claim_methodology_next_stage_run(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )
    assert _database_dump(tasks) == before

    service.revision_resolver = lambda _root, _repository_id: REVISION
    service.runtimes["codex"] = RuntimeCommand(
        adapter="codex",
        command_template=(sys.executable, "changed", "{prompt}"),
    )
    with pytest.raises(OrchestrationConflictError, match="runtime"):
        service.claim_methodology_next_stage_run(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )
    assert _database_dump(tasks) == before


@pytest.mark.asyncio
async def test_methodology_sequence_two_run_claim_budget_is_zero_write(
    tmp_path,
):
    tasks, service, contract, _, dispatch, stage_gate = (
        await _configured_next_methodology_stage(tmp_path)
    )
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            UPDATE orchestration_plans SET total_token_budget = 1
            WHERE task_id = ?
            """,
            (contract.task_id,),
        )
        db.commit()
    request = _stage_run_claim_request(
        tasks,
        service,
        contract,
        dispatch,
        stage_gate,
    )
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationConflictError,
        match="Token reservation exceeds",
    ):
        service.claim_methodology_next_stage_run(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before


@pytest.mark.asyncio
async def test_methodology_sequence_two_run_claim_cost_budget_is_zero_write(
    tmp_path,
):
    tasks, service, contract, _, dispatch, stage_gate = (
        await _configured_next_methodology_stage(tmp_path)
    )
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            UPDATE orchestration_plans SET total_cost_budget_usd = 0
            WHERE task_id = ?
            """,
            (contract.task_id,),
        )
        db.commit()
    request = _stage_run_claim_request(
        tasks,
        service,
        contract,
        dispatch,
        stage_gate,
    )
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationConflictError,
        match="cost reservation exceeds",
    ):
        service.claim_methodology_next_stage_run(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before


@pytest.mark.asyncio
async def test_methodology_sequence_two_run_claim_rejects_execution_state(
    tmp_path,
):
    tasks, service, contract, _, dispatch, stage_gate = (
        await _configured_next_methodology_stage(tmp_path)
    )
    request = _stage_run_claim_request(
        tasks,
        service,
        contract,
        dispatch,
        stage_gate,
    )
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            INSERT INTO protocol_artifacts (
                artifact_id, version, project_id, task_id, stage_key,
                run_id, kind, storage, sha256, payload, payload_sha256,
                created_at
            ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "unexpected-sequence-two-artifact",
                request.project_id,
                request.task_id,
                request.stage_key,
                "unexpected-sequence-two-run",
                "unexpected-output",
                "managed",
                "0" * 64,
                "{}",
                "0" * 64,
                FIXED_TIME.isoformat(),
            ),
        )
        db.commit()
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationConflictError,
        match="execution state already exists",
    ):
        service.claim_methodology_next_stage_run(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before


@pytest.mark.asyncio
async def test_methodology_sequence_two_run_claim_event_failure_rolls_back(
    tmp_path,
    monkeypatch,
):
    tasks, service, contract, _, dispatch, stage_gate = (
        await _configured_next_methodology_stage(tmp_path)
    )
    request = _stage_run_claim_request(
        tasks,
        service,
        contract,
        dispatch,
        stage_gate,
    )
    stage_before = service.control_plane.get_stage(
        contract.task_id,
        request.stage_key,
    )
    before = _database_dump(tasks)

    def fail_event(*_args, **_kwargs):
        raise RuntimeError("injected Stage Run claim event failure")

    monkeypatch.setattr(service.control_plane, "_event", fail_event)
    with pytest.raises(RuntimeError, match="injected Stage Run claim"):
        service.claim_methodology_next_stage_run(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before
    assert (
        service.control_plane.get_stage(contract.task_id, request.stage_key)
        == stage_before
    )
    assert service.control_plane.get_protocol_run(request.run_id) is None


@pytest.mark.asyncio
async def test_methodology_sequence_two_run_claim_concurrent_replay(
    tmp_path,
):
    tasks, service, contract, _, dispatch, stage_gate = (
        await _configured_next_methodology_stage(tmp_path)
    )
    request = _stage_run_claim_request(
        tasks,
        service,
        contract,
        dispatch,
        stage_gate,
    )
    barrier = Barrier(2)

    def claim():
        barrier.wait()
        return service.claim_methodology_next_stage_run(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(lambda _index: claim(), range(2)))

    assert receipts[0] == receipts[1]
    with sqlite3.connect(tasks.db_path) as db:
        assert (
            db.execute(
                """
                SELECT COUNT(*) FROM orchestration_methodology_stage_run_claims
                WHERE task_id = ?
                """,
                (contract.task_id,),
            ).fetchone()[0]
            == 1
        )


@pytest.mark.asyncio
async def test_methodology_sequence_two_run_claim_loader_and_cli(
    tmp_path,
    monkeypatch,
    capsys,
):
    tasks, service, contract, _, dispatch, stage_gate = (
        await _configured_next_methodology_stage(tmp_path)
    )
    request = _stage_run_claim_request(
        tasks,
        service,
        contract,
        dispatch,
        stage_gate,
    )
    path = tmp_path / "stage-run-claim.json"
    path.write_text(request.model_dump_json(), encoding="utf-8")
    assert load_methodology_stage_run_claim_request(path) == request
    secret = "stage-run-claim-control-secret"
    monkeypatch.setenv("AGORA_TEST_STAGE_RUN_TOKEN", secret)
    monkeypatch.setattr(orchestration_cli, "build_service", lambda: service)
    monkeypatch.setattr(
        "agora.control_plane.auth.get_config",
        lambda: {
            "control_plane": {
                "auth": {
                    "credentials": [
                        {
                            "secret_ref": "AGORA_TEST_STAGE_RUN_TOKEN",
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
            "migration-next-stage-run-claim",
            contract.task_id,
            "--request",
            str(path),
            "--credential-env",
            "AGORA_TEST_STAGE_RUN_TOKEN",
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert code == 0
    assert payload["stage_sequence"] == 2
    assert payload["formal_run_created"] is True
    assert payload["input_artifact_bindings"] == []
    assert payload["process_started"] is False
    assert secret not in output
    assert secret not in _database_dump(tasks)


def test_methodology_sequence_two_run_claim_authenticates_before_store(
    tmp_path,
    monkeypatch,
    capsys,
):
    monkeypatch.delenv("AGORA_MISSING_STAGE_RUN_TOKEN", raising=False)
    built = False

    def build_forbidden():
        nonlocal built
        built = True
        raise AssertionError("service must not be built before authentication")

    monkeypatch.setattr(orchestration_cli, "build_service", build_forbidden)
    code = orchestration_cli.main(
        [
            "migration-next-stage-run-claim",
            "task-successor",
            "--request",
            str(tmp_path / "missing.json"),
            "--credential-env",
            "AGORA_MISSING_STAGE_RUN_TOKEN",
        ]
    )

    assert code == 2
    assert built is False
    assert "credential environment variable is absent" in capsys.readouterr().out


def test_methodology_stage_run_claim_schemas_are_registered():
    assert (
        SCHEMA_MODELS["methodology-stage-run-claim-request"]
        is MethodologyStageRunClaimRequest
    )
    assert (
        SCHEMA_MODELS["methodology-stage-run-claim-receipt"]
        is MethodologyStageRunClaimReceipt
    )


async def _claimed_sequence_two_methodology_run(tmp_path):
    tasks, service, contract, first_claim, first_dispatch, stage_gate = (
        await _configured_next_methodology_stage(tmp_path)
    )
    request = _stage_run_claim_request(
        tasks,
        service,
        contract,
        first_dispatch,
        stage_gate,
    )
    stage_claim = service.claim_methodology_next_stage_run(
        contract.task_id,
        request,
        principal=_migration_principal(),
    )
    return (
        tasks,
        service,
        contract,
        first_claim,
        first_dispatch,
        stage_gate,
        stage_claim,
    )


async def _dispatch_sequence_two_methodology_run(service, task_id):
    return await service.dispatch_methodology_next_stage_run(
        task_id,
        allow_unbounded_native_usage=True,
    )


@pytest.mark.asyncio
async def test_methodology_stage_dispatch_requires_acknowledgement(tmp_path):
    tasks, service, contract, *_ = (
        await _claimed_sequence_two_methodology_run(tmp_path)
    )
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationValidationError,
        match="acknowledgement",
    ):
        await service.dispatch_methodology_next_stage_run(
            contract.task_id,
            allow_unbounded_native_usage=False,
        )

    assert _database_dump(tasks) == before


@pytest.mark.asyncio
async def test_methodology_stage_dispatch_settles_existing_run_atomically(
    tmp_path,
):
    (
        tasks,
        service,
        contract,
        _,
        first_dispatch,
        stage_gate,
        stage_claim,
    ) = await _claimed_sequence_two_methodology_run(tmp_path)
    runner = MethodologyDispatchRunner()
    service.runner = runner

    receipt = await _dispatch_sequence_two_methodology_run(
        service,
        contract.task_id,
    )

    assert isinstance(receipt, MethodologyStageRunDispatchReceipt)
    assert isinstance(receipt.dispatch_claim, MethodologyStageRunDispatchClaim)
    claim = receipt.dispatch_claim
    assert claim.stage_sequence == 2
    assert claim.stage_key == contract.stages[1].stage_key
    assert claim.run_id == stage_claim.run_id
    assert claim.context_pack_id == stage_claim.context_pack_id
    assert claim.stage_gate_receipt_id == stage_gate.receipt_id
    assert claim.stage_run_claim_receipt_id == stage_claim.receipt_id
    assert claim.predecessor_dispatch_receipt_id == first_dispatch.receipt_id
    assert claim.dispatch_policy.dispatchable is True
    assert claim.runtime_preflight.allowed is True
    assert (
        claim.runtime_preflight.routing_policy_decision_id
        == claim.dispatch_policy.decision_id
    )
    assert receipt.process_started is True
    assert receipt.pid == runner.pid
    assert receipt.stage_status.value == "completed"
    assert receipt.gate_status.value == "passed"
    assert receipt.next_stage_key == contract.stages[2].stage_key
    assert receipt.protocol_settled is True
    assert receipt.provider_substitution is False
    assert runner.calls == 1

    dispatch = service.store.get_methodology_stage_run_dispatch(
        contract.task_id
    )
    assert dispatch is not None
    assert dispatch.state == MethodologyDispatchState.SETTLED
    assert dispatch.receipt == receipt
    protocol_run = service.control_plane.get_protocol_run(stage_claim.run_id)
    assert protocol_run is not None
    assert protocol_run.settled_at is not None
    assert protocol_run.protocol_state == receipt.protocol_state
    assert protocol_run.handoff_pack is not None
    assert protocol_run.handoff_pack.pack_id == receipt.handoff_pack_id
    unified = service.unified_status(contract.task_id)
    projected = next(
        item for item in unified.runs if item.run_id == stage_claim.run_id
    )
    assert projected.runtime_preflight == claim.runtime_preflight
    assert projected.usage_observation == receipt.usage_observation
    assert projected.wait_state.value == "settled"
    assert unified.budget.token_reserved == 0
    assert unified.collection_totals["usage"] == 2
    assert {item.run_id for item in unified.usage} == {
        first_dispatch.dispatch_claim.run_id,
        stage_claim.run_id,
    }
    with sqlite3.connect(tasks.db_path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM protocol_runs WHERE task_id = ?",
            (contract.task_id,),
        ).fetchone()[0] == 2
        assert db.execute(
            "SELECT COUNT(*) FROM orchestration_runs WHERE task_id = ?",
            (contract.task_id,),
        ).fetchone()[0] == 0
        assert db.execute(
            """
            SELECT COUNT(*)
            FROM orchestration_methodology_stage_run_dispatches
            WHERE task_id = ? AND state = 'settled'
            """,
            (contract.task_id,),
        ).fetchone()[0] == 1
        assert db.execute(
            """
            SELECT COUNT(*)
            FROM orchestration_methodology_stage_usage_ledger
            WHERE task_id = ?
            """,
            (contract.task_id,),
        ).fetchone()[0] == 1
        assert db.execute(
            """
            SELECT process_started
            FROM orchestration_methodology_stage_run_claims
            WHERE task_id = ? AND stage_sequence = 2
            """,
            (contract.task_id,),
        ).fetchone()[0] == 0

    replay = await _dispatch_sequence_two_methodology_run(
        service,
        contract.task_id,
    )
    assert replay == receipt
    assert runner.calls == 1


@pytest.mark.asyncio
async def test_methodology_stage_dispatch_pre_spawn_failure_is_exact_zero(
    tmp_path,
):
    _, service, contract, *_, stage_claim = (
        await _claimed_sequence_two_methodology_run(tmp_path)
    )
    service.runner = MethodologyDispatchRunner(fail_before_process=True)

    receipt = await _dispatch_sequence_two_methodology_run(
        service,
        contract.task_id,
    )

    assert receipt.dispatch_claim.run_id == stage_claim.run_id
    assert receipt.process_started is False
    assert receipt.pid is None
    assert receipt.exit_code is None
    assert receipt.usage_observation.total_tokens == 0
    assert receipt.usage_observation.token_measurement == "exact"
    assert receipt.usage_observation.cost_usd == 0
    assert receipt.usage_observation.cost_measurement == "exact"
    assert receipt.protocol_state.process_status.value == "launch_failed"
    assert receipt.stage_status.value == "failed"
    assert receipt.handoff_pack_id is None


@pytest.mark.asyncio
async def test_methodology_stage_dispatch_blocked_preflight_is_zero_write(
    tmp_path,
):
    tasks, service, contract, *_ = (
        await _claimed_sequence_two_methodology_run(tmp_path)
    )
    service.runtimes[contract.stages[1].runtime] = RuntimeCommand(
        adapter=contract.stages[1].runtime,
        command_template=("agora-missing-stage-runtime", "{prompt}"),
    )
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationConflictError,
        match="unavailable|changed",
    ):
        await _dispatch_sequence_two_methodology_run(
            service,
            contract.task_id,
        )

    assert _database_dump(tasks) == before
    assert (
        service.store.get_methodology_stage_run_dispatch(contract.task_id)
        is None
    )


@pytest.mark.asyncio
async def test_methodology_stage_dispatch_policy_blocks_unbounded_reservation(
    tmp_path,
):
    _, service, contract, *_ = (
        await _claimed_sequence_two_methodology_run(tmp_path)
    )
    snapshot = service.store.methodology_stage_run_dispatch_snapshot(
        contract.task_id,
        control_plane=service.control_plane,
    )
    budget = snapshot.stage_run_claim_receipt.budget.model_copy(
        update={"max_model_tokens": None}
    )
    claim = snapshot.stage_run_claim_receipt.model_copy(
        update={"budget": budget}
    )

    decision = derive_methodology_stage_run_dispatch_policy(
        snapshot=replace(snapshot, stage_run_claim_receipt=claim),
        repository=REVISION,
        runtimes=service.runtimes,
        evaluated_at=utc_now(),
    )

    assert decision.dispatchable is False
    assert decision.token_reservation == 0
    assert any(
        item.check == "usage_reservation" and not item.satisfied
        for item in decision.checks
    )


@pytest.mark.asyncio
async def test_methodology_stage_dispatch_concurrency_never_spawns_twice(
    tmp_path,
):
    _, service, contract, *_ = (
        await _claimed_sequence_two_methodology_run(tmp_path)
    )
    runner = MethodologyDispatchRunner()
    service.runner = runner

    results = await asyncio.gather(
        _dispatch_sequence_two_methodology_run(service, contract.task_id),
        _dispatch_sequence_two_methodology_run(service, contract.task_id),
        return_exceptions=True,
    )

    assert runner.calls == 1
    assert sum(
        isinstance(item, MethodologyStageRunDispatchReceipt)
        for item in results
    ) == 1
    assert sum(
        isinstance(item, OrchestrationConflictError) for item in results
    ) == 1


@pytest.mark.asyncio
async def test_methodology_stage_dispatch_resume_refuses_live_pid_then_settles(
    tmp_path,
):
    _, service, contract, *_ = (
        await _claimed_sequence_two_methodology_run(tmp_path)
    )

    class CrashingRunner:
        async def run(self, _runtime, _prompt, **kwargs):
            await kwargs["on_process"](818_181)
            raise GeneratorExit("simulated Stage host crash")

    service.runner = CrashingRunner()
    with pytest.raises(GeneratorExit, match="Stage host crash"):
        await _dispatch_sequence_two_methodology_run(
            service,
            contract.task_id,
        )
    dispatch = service.store.get_methodology_stage_run_dispatch(
        contract.task_id
    )
    assert dispatch is not None
    assert dispatch.state == MethodologyDispatchState.RUNNING

    service.process_inspector = lambda _pid: ProcessState.ALIVE
    with pytest.raises(
        OrchestrationConflictError,
        match="refusing duplicate dispatch",
    ):
        service.resume(contract.task_id)

    service.process_inspector = lambda _pid: ProcessState.DEAD
    service.resume(contract.task_id)
    recovered = service.store.get_methodology_stage_run_dispatch(
        contract.task_id
    )
    assert recovered is not None
    assert recovered.state == MethodologyDispatchState.SETTLED
    assert recovered.receipt is not None
    assert recovered.receipt.process_started is True
    assert recovered.receipt.protocol_state.process_status.value == "interrupted"


@pytest.mark.asyncio
async def test_methodology_stage_dispatch_finalization_event_rolls_back(
    tmp_path,
    monkeypatch,
):
    tasks, service, contract, *_, stage_claim = (
        await _claimed_sequence_two_methodology_run(tmp_path)
    )
    service.runner = MethodologyDispatchRunner()
    original_event = service.control_plane._event

    def fail_final_event(*args, **kwargs):
        if kwargs.get("event_type") == "methodology.stage_run_dispatch_settled":
            raise RuntimeError("injected Stage finalization event failure")
        return original_event(*args, **kwargs)

    monkeypatch.setattr(service.control_plane, "_event", fail_final_event)
    with pytest.raises(RuntimeError, match="Stage finalization event failure"):
        await _dispatch_sequence_two_methodology_run(
            service,
            contract.task_id,
        )
    dispatch = service.store.get_methodology_stage_run_dispatch(
        contract.task_id
    )
    assert dispatch is not None
    assert dispatch.state == MethodologyDispatchState.TERMINAL_OBSERVED
    with sqlite3.connect(tasks.db_path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM orchestration_methodology_stage_usage_ledger"
        ).fetchone()[0] == 0
    assert (
        service.unified_status(contract.task_id).budget.token_reserved
        == stage_claim.budget.max_model_tokens
    )

    monkeypatch.setattr(service.control_plane, "_event", original_event)
    receipt = await _dispatch_sequence_two_methodology_run(
        service,
        contract.task_id,
    )
    assert receipt.protocol_settled is True
    assert service.unified_status(contract.task_id).budget.token_reserved == 0


@pytest.mark.asyncio
async def test_methodology_stage_dispatch_persisted_tamper_fails_closed(
    tmp_path,
):
    tasks, service, contract, *_ = (
        await _claimed_sequence_two_methodology_run(tmp_path)
    )
    service.runner = MethodologyDispatchRunner(fail_before_process=True)
    await _dispatch_sequence_two_methodology_run(service, contract.task_id)
    with sqlite3.connect(tasks.db_path) as db:
        payload = json.loads(
            db.execute(
                """
                SELECT dispatch_policy_payload
                FROM orchestration_methodology_stage_run_dispatches
                WHERE task_id = ?
                """,
                (contract.task_id,),
            ).fetchone()[0]
        )
        payload["checks"][0]["detail"] = "tampered Stage dispatch policy"
        db.execute(
            """
            UPDATE orchestration_methodology_stage_run_dispatches
            SET dispatch_policy_payload = ? WHERE task_id = ?
            """,
            (json.dumps(payload), contract.task_id),
        )
        db.commit()

    with pytest.raises(
        (ValidationError, OrchestrationValidationError),
        match="content_sha256|binding drifted",
    ):
        service.store.get_methodology_stage_run_dispatch(contract.task_id)


def test_methodology_stage_dispatch_cli_requires_ack_before_store(
    monkeypatch,
    capsys,
):
    built = False

    def build_forbidden():
        nonlocal built
        built = True
        raise AssertionError("service must not build before acknowledgement")

    monkeypatch.setattr(orchestration_cli, "build_service", build_forbidden)
    code = orchestration_cli.main(
        ["migration-next-stage-run-dispatch", "task-successor"]
    )

    assert code == 2
    assert built is False
    assert "--allow-unbounded-native-usage" in capsys.readouterr().out


def test_methodology_stage_dispatch_cli_emits_terminal_receipt(
    tmp_path,
    monkeypatch,
    capsys,
):
    _, service, contract, *_ = asyncio.run(
        _claimed_sequence_two_methodology_run(tmp_path)
    )
    service.runner = MethodologyDispatchRunner(fail_before_process=True)
    monkeypatch.setattr(orchestration_cli, "build_service", lambda: service)

    code = orchestration_cli.main(
        [
            "migration-next-stage-run-dispatch",
            contract.task_id,
            "--allow-unbounded-native-usage",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["dispatch_claim"]["stage_sequence"] == 2
    assert payload["process_started"] is False
    assert payload["protocol_settled"] is True
    assert payload["provider_substitution"] is False


def test_methodology_stage_run_dispatch_schemas_are_registered():
    assert (
        SCHEMA_MODELS["methodology-stage-run-dispatch-policy-decision"]
        is MethodologyStageRunDispatchPolicyDecision
    )
    assert (
        SCHEMA_MODELS["methodology-stage-run-dispatch-claim"]
        is MethodologyStageRunDispatchClaim
    )
    assert (
        SCHEMA_MODELS["methodology-stage-run-dispatch-receipt"]
        is MethodologyStageRunDispatchReceipt
    )


@pytest.mark.asyncio
async def test_methodology_stage_dispatch_gate_ledger_tamper_is_zero_write(
    tmp_path,
):
    tasks, service, contract, *_ = (
        await _claimed_sequence_two_methodology_run(tmp_path)
    )
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            UPDATE orchestration_methodology_stage_gates
            SET receipt_sha256 = ? WHERE task_id = ? AND stage_sequence = 2
            """,
            ("0" * 64, contract.task_id),
        )
        db.commit()
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationValidationError,
        match="provenance drifted",
    ):
        await _dispatch_sequence_two_methodology_run(
            service,
            contract.task_id,
        )

    assert _database_dump(tasks) == before
    assert (
        service.store.get_methodology_stage_run_dispatch(contract.task_id)
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("attach_process", "expected_process_status", "expected_stage_status"),
    [
        (False, "launch_failed", "failed"),
        (True, "cancelled", "cancelled"),
    ],
)
async def test_methodology_stage_dispatch_cancellation_settles_first(
    tmp_path,
    attach_process,
    expected_process_status,
    expected_stage_status,
):
    _, service, contract, *_ = (
        await _claimed_sequence_two_methodology_run(tmp_path)
    )

    class CancellingRunner:
        async def run(self, _runtime, _prompt, **kwargs):
            if attach_process:
                await kwargs["on_process"](919_191)
            raise asyncio.CancelledError

    service.runner = CancellingRunner()
    with pytest.raises(asyncio.CancelledError):
        await _dispatch_sequence_two_methodology_run(
            service,
            contract.task_id,
        )

    dispatch = service.store.get_methodology_stage_run_dispatch(
        contract.task_id
    )
    assert dispatch is not None
    assert dispatch.state == MethodologyDispatchState.SETTLED
    assert dispatch.receipt is not None
    assert (
        dispatch.receipt.protocol_state.process_status.value
        == expected_process_status
    )
    assert dispatch.receipt.stage_status.value == expected_stage_status


@pytest.mark.asyncio
async def test_methodology_stage_dispatch_terminal_fact_tamper_fails_closed(
    tmp_path,
):
    tasks, service, contract, *_ = (
        await _claimed_sequence_two_methodology_run(tmp_path)
    )
    service.runner = MethodologyDispatchRunner()
    await _dispatch_sequence_two_methodology_run(service, contract.task_id)
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            UPDATE orchestration_methodology_stage_run_dispatches
            SET output = ? WHERE task_id = ?
            """,
            ("tampered Stage terminal output", contract.task_id),
        )
        db.commit()

    with pytest.raises(
        (ValidationError, OrchestrationValidationError),
        match="receipt differs|binding drifted",
    ):
        service.store.get_methodology_stage_run_dispatch(contract.task_id)


@pytest.mark.asyncio
async def test_methodology_stage_dispatch_claim_to_pid_lease_blocks_recovery(
    tmp_path,
):
    _, service, contract, *_ = (
        await _claimed_sequence_two_methodology_run(tmp_path)
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    delegate = MethodologyDispatchRunner()

    class DelayedBeforePidRunner:
        async def run(self, runtime, prompt, **kwargs):
            entered.set()
            await release.wait()
            return await delegate.run(runtime, prompt, **kwargs)

    service.runner = DelayedBeforePidRunner()
    owner = asyncio.create_task(
        _dispatch_sequence_two_methodology_run(service, contract.task_id)
    )
    await entered.wait()
    dispatch = service.store.get_methodology_stage_run_dispatch(
        contract.task_id
    )
    assert dispatch is not None
    assert dispatch.state == MethodologyDispatchState.CLAIMED

    with pytest.raises(
        OrchestrationConflictError,
        match="owner lease is active",
    ):
        await _dispatch_sequence_two_methodology_run(
            service,
            contract.task_id,
        )

    release.set()
    receipt = await owner
    assert receipt.process_started is True
    assert delegate.calls == 1


@pytest.mark.asyncio
async def test_methodology_stage_dispatch_expired_pre_pid_lease_recovers(
    tmp_path,
    monkeypatch,
):
    _, service, contract, *_ = (
        await _claimed_sequence_two_methodology_run(tmp_path)
    )

    class CrashingBeforePidRunner:
        async def run(self, _runtime, _prompt, **_kwargs):
            raise GeneratorExit("simulated crash before Stage PID attachment")

    service.runner = CrashingBeforePidRunner()
    with pytest.raises(GeneratorExit, match="before Stage PID"):
        await _dispatch_sequence_two_methodology_run(
            service,
            contract.task_id,
        )
    dispatch = service.store.get_methodology_stage_run_dispatch(
        contract.task_id
    )
    assert dispatch is not None
    assert dispatch.state == MethodologyDispatchState.CLAIMED

    with pytest.raises(
        OrchestrationConflictError,
        match="owner lease is active",
    ):
        service.resume(contract.task_id)

    future = dispatch.claim.recovery_not_before + timedelta(seconds=1)

    class FutureDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return future if tz is not None else future.replace(tzinfo=None)

    monkeypatch.setattr("agora.orchestration.service.datetime", FutureDateTime)
    monkeypatch.setattr("agora.orchestration.store.datetime", FutureDateTime)
    service.resume(contract.task_id)
    recovered = service.store.get_methodology_stage_run_dispatch(
        contract.task_id
    )
    assert recovered is not None
    assert recovered.state == MethodologyDispatchState.SETTLED
    assert recovered.receipt is not None
    assert recovered.receipt.process_started is False
    assert recovered.receipt.usage_observation.total_tokens == 0
    assert recovered.receipt.protocol_state.process_status.value == "launch_failed"


@pytest.mark.asyncio
async def test_methodology_stage_dispatch_full_runtime_registry_drift_is_zero_write(
    tmp_path,
):
    tasks, service, contract, *_ = (
        await _claimed_sequence_two_methodology_run(tmp_path)
    )
    runtime_name = contract.stages[1].runtime
    current = service.runtimes[runtime_name]
    service.runtimes[runtime_name] = replace(
        current,
        result_format=RuntimeResultFormat.CODEX_JSONL_V1,
        declared_models=("changed-model",),
    )
    before = _database_dump(tasks)

    with pytest.raises(OrchestrationConflictError, match="registry|runtime"):
        await _dispatch_sequence_two_methodology_run(
            service,
            contract.task_id,
        )

    assert _database_dump(tasks) == before
    assert (
        service.store.get_methodology_stage_run_dispatch(contract.task_id)
        is None
    )


@pytest.mark.asyncio
async def test_methodology_stage_dispatch_settlement_uses_sealed_result_format(
    tmp_path,
    monkeypatch,
):
    _, service, contract, *_ = (
        await _claimed_sequence_two_methodology_run(tmp_path)
    )
    runtime_name = contract.stages[1].runtime
    delegate = MethodologyDispatchRunner()
    observed_formats = []
    from agora.orchestration import service as service_module

    original_observation = service_module.settlement_observation

    def capture_observation(**kwargs):
        observed_formats.append(kwargs["result_format"])
        return original_observation(**kwargs)

    class MutatingAfterResultRunner:
        async def run(self, runtime, prompt, **kwargs):
            result = await delegate.run(runtime, prompt, **kwargs)
            service.runtimes[runtime_name] = replace(
                runtime,
                result_format=RuntimeResultFormat.CODEX_JSONL_V1,
            )
            return result

    monkeypatch.setattr(service_module, "settlement_observation", capture_observation)
    service.runner = MutatingAfterResultRunner()
    receipt = await _dispatch_sequence_two_methodology_run(
        service,
        contract.task_id,
    )

    assert receipt.dispatch_claim.result_format == "plain_text"
    assert observed_formats == [RuntimeResultFormat.PLAIN_TEXT]
    assert receipt.protocol_settled is True


@pytest.mark.asyncio
async def test_methodology_stage_reservation_row_tamper_fails_projection(
    tmp_path,
):
    tasks, service, contract, *_ = (
        await _claimed_sequence_two_methodology_run(tmp_path)
    )
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            UPDATE orchestration_methodology_stage_run_claims
            SET token_reserved = token_reserved + 1 WHERE task_id = ?
            """,
            (contract.task_id,),
        )
        db.commit()

    with pytest.raises(
        OrchestrationValidationError,
        match="reservation row differs",
    ):
        service.unified_status(contract.task_id)


@pytest.mark.asyncio
async def test_methodology_stage_usage_row_tamper_fails_projection(tmp_path):
    tasks, service, contract, *_ = (
        await _claimed_sequence_two_methodology_run(tmp_path)
    )
    service.runner = MethodologyDispatchRunner()
    await _dispatch_sequence_two_methodology_run(service, contract.task_id)
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            UPDATE orchestration_methodology_stage_usage_ledger
            SET tokens = COALESCE(tokens, 0) + 1 WHERE task_id = ?
            """,
            (contract.task_id,),
        )
        db.commit()

    with pytest.raises(
        OrchestrationValidationError,
        match="usage row differs",
    ):
        service.unified_status(contract.task_id)


@pytest.mark.asyncio
async def test_methodology_stage_missing_usage_row_fails_projection(tmp_path):
    tasks, service, contract, *_ = (
        await _claimed_sequence_two_methodology_run(tmp_path)
    )
    service.runner = MethodologyDispatchRunner()
    await _dispatch_sequence_two_methodology_run(service, contract.task_id)
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            DELETE FROM orchestration_methodology_stage_usage_ledger
            WHERE task_id = ?
            """,
            (contract.task_id,),
        )
        db.commit()

    with pytest.raises(
        OrchestrationValidationError,
        match="lacks usage",
    ):
        service.unified_status(contract.task_id)


@pytest.mark.asyncio
async def test_methodology_stage_missing_claim_authority_fails_projection(
    tmp_path,
):
    tasks, service, contract, *_ = (
        await _claimed_sequence_two_methodology_run(tmp_path)
    )
    service.runner = MethodologyDispatchRunner()
    await _dispatch_sequence_two_methodology_run(service, contract.task_id)
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            DELETE FROM orchestration_methodology_stage_run_claims
            WHERE task_id = ?
            """,
            (contract.task_id,),
        )
        db.commit()

    with pytest.raises(
        OrchestrationValidationError,
        match="reservation authority is missing",
    ):
        service.unified_status(contract.task_id)


def _create_other_methodology_plan(service):
    task_contract = load_task_contract(CONTRACT_PATH)
    other = service.create(
        project_id="alpha",
        title="Independent plan for cross-plan ledger validation",
        description="Must never receive another Task reservation.",
        total_token_budget=30_000,
        total_cost_budget_usd=None,
        contract=task_contract,
    )
    return other.task_id, service.store.status(other.task_id).plan.plan_id


@pytest.mark.asyncio
async def test_methodology_stage_cross_plan_claim_tamper_fails_admission(
    tmp_path,
):
    tasks, service, contract, *_ = (
        await _claimed_sequence_two_methodology_run(tmp_path)
    )
    original_plan_id = service.store.status(contract.task_id).plan.plan_id
    _, other_plan_id = _create_other_methodology_plan(service)
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            UPDATE orchestration_methodology_stage_run_claims
            SET plan_id = ? WHERE task_id = ?
            """,
            (other_plan_id, contract.task_id),
        )
        db.commit()
    with sqlite3.connect(tasks.db_path) as db:
        db.row_factory = sqlite3.Row
        with pytest.raises(
            OrchestrationValidationError,
            match="reservation row differs",
        ):
            service.store._provider_budget_snapshot(db, original_plan_id)
        with pytest.raises(
            OrchestrationValidationError,
            match="reservation row differs",
        ):
            service.store._provider_budget_snapshot(db, other_plan_id)


@pytest.mark.asyncio
async def test_methodology_stage_cross_plan_claim_tamper_fails_projection(
    tmp_path,
):
    tasks, service, contract, *_ = (
        await _claimed_sequence_two_methodology_run(tmp_path)
    )
    service.runner = MethodologyDispatchRunner()
    await _dispatch_sequence_two_methodology_run(service, contract.task_id)
    other_task_id, other_plan_id = _create_other_methodology_plan(service)
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            UPDATE orchestration_methodology_stage_run_claims
            SET plan_id = ? WHERE task_id = ?
            """,
            (other_plan_id, contract.task_id),
        )
        db.commit()

    with pytest.raises(
        OrchestrationValidationError,
        match="reservation row differs",
    ):
        service.unified_status(contract.task_id)
    with pytest.raises(
        OrchestrationValidationError,
        match="reservation row differs",
    ):
        service.unified_status(other_task_id)


@pytest.mark.asyncio
async def test_methodology_stage_cross_plan_usage_tamper_fails_destination(
    tmp_path,
):
    tasks, service, contract, *_ = (
        await _claimed_sequence_two_methodology_run(tmp_path)
    )
    service.runner = MethodologyDispatchRunner()
    await _dispatch_sequence_two_methodology_run(service, contract.task_id)
    other_task_id, other_plan_id = _create_other_methodology_plan(service)
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            UPDATE orchestration_methodology_stage_usage_ledger
            SET plan_id = ? WHERE task_id = ?
            """,
            (other_plan_id, contract.task_id),
        )
        db.commit()

    with sqlite3.connect(tasks.db_path) as db:
        db.row_factory = sqlite3.Row
        with pytest.raises(
            OrchestrationValidationError,
            match="usage row differs",
        ):
            service.store._provider_budget_snapshot(db, other_plan_id)
    with pytest.raises(
        OrchestrationValidationError,
        match="usage row differs",
    ):
        service.unified_status(other_task_id)


async def _settled_sequence_two_methodology_run(tmp_path):
    (
        tasks,
        service,
        contract,
        first_claim,
        first_dispatch,
        stage_gate,
        stage_claim,
    ) = await _claimed_sequence_two_methodology_run(tmp_path)
    service.runner = MethodologyDispatchRunner()
    stage_dispatch = await _dispatch_sequence_two_methodology_run(
        service,
        contract.task_id,
    )
    assert stage_dispatch.stage_status.value == "completed"
    assert stage_dispatch.gate_status.value == "passed"
    assert stage_dispatch.next_stage_key == contract.stages[2].stage_key
    return (
        tasks,
        service,
        contract,
        first_claim,
        first_dispatch,
        stage_gate,
        stage_claim,
        stage_dispatch,
    )


def _sequence_three_stage_gate_request(
    tasks,
    service,
    contract,
    predecessor_dispatch,
    *,
    stage_sequence=3,
):
    task = tasks.get(contract.task_id)
    control_task = service.control_plane.get_task_state(contract.task_id)
    inventory = service.control_plane.get_stage_inventory(contract.task_id)
    route = service.control_plane.get_stage_route(contract.task_id)
    status = service.status(contract.task_id)
    assert task is not None
    assert control_task is not None
    assert inventory is not None
    assert route is not None
    formal_stage = service.control_plane.get_stage(
        contract.task_id,
        route.stage_key,
    )
    assert formal_stage is not None
    assert predecessor_dispatch.handoff_pack_id is not None
    assert predecessor_dispatch.handoff_pack_sha256 is not None
    predecessor = contract.stages[stage_sequence - 2]
    stage = contract.stages[stage_sequence - 1]
    payload = {
        "schema_version": "1.0",
        "request_id": (
            f"stage-{stage_sequence}-gate-request-"
            f"{contract.content_sha256[:20]}"
        ),
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
        "predecessor_dispatch_receipt_id": predecessor_dispatch.receipt_id,
        "predecessor_dispatch_receipt_sha256": (
            predecessor_dispatch.content_sha256
        ),
        "predecessor_run_id": predecessor_dispatch.dispatch_claim.run_id,
        "predecessor_stage_key": predecessor.stage_key,
        "predecessor_gate_key": predecessor.gate_key,
        "predecessor_handoff_pack_id": predecessor_dispatch.handoff_pack_id,
        "predecessor_handoff_pack_sha256": (
            predecessor_dispatch.handoff_pack_sha256
        ),
        "repository": contract.repository.model_dump(mode="json"),
        "stage_sequence": stage.sequence,
        "stage_key": stage.stage_key,
        "gate_key": stage.gate_key,
        "runtime": stage.runtime,
        "expected_stage_version": formal_stage.version,
        "configure_formal_gate": True,
        "claim_formal_run": False,
        "start_runtime_process": False,
    }
    return MethodologyStageGateRequest.model_validate(
        seal_model_payload(MethodologyStageGateRequest, payload)
    )


def _sequence_three_stage_run_claim_request(
    tasks,
    service,
    contract,
    predecessor_dispatch,
    stage_gate,
    *,
    stage_sequence=3,
):
    task = tasks.get(contract.task_id)
    control_task = service.control_plane.get_task_state(contract.task_id)
    inventory = service.control_plane.get_stage_inventory(contract.task_id)
    route = service.control_plane.get_stage_route(contract.task_id)
    status = service.status(contract.task_id)
    assert task is not None
    assert control_task is not None
    assert inventory is not None
    assert route is not None
    formal_stage = service.control_plane.get_stage(
        contract.task_id,
        route.stage_key,
    )
    formal_gate = service.control_plane.get_gate(
        contract.task_id,
        route.gate_key,
    )
    assert formal_stage is not None
    assert formal_gate is not None
    stage = contract.stages[stage_sequence - 1]
    run_id = methodology_stage_run_id(
        task_id=task.task_id,
        execution_contract_sha256=contract.content_sha256,
        stage_sequence=stage.sequence,
        stage_key=stage.stage_key,
    )
    payload = {
        "schema_version": "1.0",
        "request_id": (
            f"stage-{stage_sequence}-run-request-"
            f"{contract.content_sha256[:20]}"
        ),
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
        "stage_gate_receipt_id": stage_gate.receipt_id,
        "stage_gate_receipt_sha256": stage_gate.content_sha256,
        "predecessor_dispatch_receipt_id": predecessor_dispatch.receipt_id,
        "predecessor_dispatch_receipt_sha256": (
            predecessor_dispatch.content_sha256
        ),
        "repository": contract.repository.model_dump(mode="json"),
        "stage_sequence": stage.sequence,
        "stage_key": stage.stage_key,
        "gate_key": stage.gate_key,
        "runtime": stage.runtime,
        "expected_stage_version": formal_stage.version,
        "expected_gate_version": formal_gate.version,
        "expected_gate_status": formal_gate.status.value,
        "run_id": run_id,
        "context_pack_id": f"context:{run_id}",
        "context_pack_schema_version": "1.0",
        "claim_formal_run": True,
        "start_runtime_process": False,
    }
    return MethodologyStageRunClaimRequest.model_validate(
        seal_model_payload(MethodologyStageRunClaimRequest, payload)
    )


@pytest.mark.asyncio
async def test_methodology_sequence_three_gate_and_run_claim_are_atomic(
    tmp_path,
):
    (
        tasks,
        service,
        contract,
        _,
        first_dispatch,
        _,
        _,
        predecessor_dispatch,
    ) = await _settled_sequence_two_methodology_run(tmp_path)
    gate_request = _sequence_three_stage_gate_request(
        tasks,
        service,
        contract,
        predecessor_dispatch,
    )
    gate_receipt = service.configure_methodology_next_stage_gate(
        contract.task_id,
        gate_request,
        principal=_migration_principal(),
    )

    assert gate_receipt.stage_sequence == 3
    assert gate_receipt.stage_key == contract.stages[2].stage_key
    assert (
        gate_receipt.predecessor_dispatch_receipt_id
        == predecessor_dispatch.receipt_id
    )
    assert gate_receipt.predecessor_stage_key == contract.stages[1].stage_key
    assert gate_receipt.predecessor_gate_key == contract.stages[1].gate_key
    assert gate_receipt.run_created is False
    assert gate_receipt.process_started is False
    assert (
        service.store.get_methodology_stage_gate(
            contract.task_id,
            contract.stages[2].stage_key,
        )
        == gate_receipt
    )
    with sqlite3.connect(tasks.db_path) as db:
        row = db.execute(
            """
            SELECT gate.predecessor_dispatch_id AS lineage_root,
                   binding.*
            FROM orchestration_methodology_stage_gates AS gate
            JOIN orchestration_methodology_successor_predecessors AS binding
              ON binding.stage_gate_request_id = gate.request_id
            WHERE gate.task_id = ? AND gate.stage_sequence = 3
            """,
            (contract.task_id,),
        ).fetchone()
    assert row is not None
    assert row[0] == first_dispatch.dispatch_claim.dispatch_id
    assert row[5] == predecessor_dispatch.dispatch_claim.dispatch_id
    assert row[6] == predecessor_dispatch.receipt_id

    claim_request = _sequence_three_stage_run_claim_request(
        tasks,
        service,
        contract,
        predecessor_dispatch,
        gate_receipt,
    )
    claim_receipt = service.claim_methodology_next_stage_run(
        contract.task_id,
        claim_request,
        principal=_migration_principal(),
    )

    assert claim_receipt.stage_sequence == 3
    assert claim_receipt.stage_key == contract.stages[2].stage_key
    assert claim_receipt.predecessor_dispatch_receipt_id == (
        predecessor_dispatch.receipt_id
    )
    assert claim_receipt.input_artifact_bindings == []
    assert claim_receipt.required_outputs == []
    assert claim_receipt.process_started is False
    assert (
        service.store.get_methodology_stage_run_claim(
            contract.task_id,
            contract.stages[2].stage_key,
        )
        == claim_receipt
    )
    protocol_run = service.control_plane.get_protocol_run(claim_receipt.run_id)
    assert protocol_run is not None
    assert protocol_run.context_pack.input_artifacts == []
    assert protocol_run.context_pack.required_outputs == []
    assert protocol_run.settled_at is None
    unified = service.unified_status(contract.task_id)
    assert {item.run_id for item in unified.runs} == {
        first_dispatch.dispatch_claim.run_id,
        predecessor_dispatch.dispatch_claim.run_id,
        claim_receipt.run_id,
    }
    assert unified.budget.token_reserved == claim_receipt.budget.max_model_tokens


async def _configured_sequence_three_methodology_stage(tmp_path):
    settled = await _settled_sequence_two_methodology_run(tmp_path)
    tasks, service, contract, *_, predecessor_dispatch = settled
    gate_request = _sequence_three_stage_gate_request(
        tasks,
        service,
        contract,
        predecessor_dispatch,
    )
    gate_receipt = service.configure_methodology_next_stage_gate(
        contract.task_id,
        gate_request,
        principal=_migration_principal(),
    )
    return (*settled, gate_request, gate_receipt)


@pytest.mark.asyncio
async def test_methodology_sequence_three_gate_and_claim_replay_exactly(
    tmp_path,
):
    (
        tasks,
        service,
        contract,
        *_,
        predecessor_dispatch,
        gate_request,
        gate_receipt,
    ) = await _configured_sequence_three_methodology_stage(tmp_path)

    assert service.configure_methodology_next_stage_gate(
        contract.task_id,
        gate_request,
        principal=_migration_principal(),
    ) == gate_receipt
    claim_request = _sequence_three_stage_run_claim_request(
        tasks,
        service,
        contract,
        predecessor_dispatch,
        gate_receipt,
    )
    receipt = service.claim_methodology_next_stage_run(
        contract.task_id,
        claim_request,
        principal=_migration_principal(),
    )
    replay = service.claim_methodology_next_stage_run(
        contract.task_id,
        claim_request,
        principal=_migration_principal(),
    )

    assert replay == receipt
    with sqlite3.connect(tasks.db_path) as db:
        assert db.execute(
            """
            SELECT COUNT(*)
            FROM orchestration_methodology_successor_predecessors
            WHERE task_id = ?
            """,
            (contract.task_id,),
        ).fetchone()[0] == 1
        assert db.execute(
            """
            SELECT COUNT(*) FROM orchestration_methodology_stage_run_claims
            WHERE task_id = ? AND stage_sequence = 3
            """,
            (contract.task_id,),
        ).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_methodology_sequence_three_rejects_non_immediate_predecessor(
    tmp_path,
):
    (
        tasks,
        service,
        contract,
        _,
        first_dispatch,
        *_,
    ) = await _settled_sequence_two_methodology_run(tmp_path)
    request = _sequence_three_stage_gate_request(
        tasks,
        service,
        contract,
        first_dispatch,
    )
    before = _database_dump(tasks)

    with pytest.raises(
        (OrchestrationConflictError, OrchestrationValidationError),
        match="predecessor|binding|sequence",
    ):
        service.configure_methodology_next_stage_gate(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before


@pytest.mark.asyncio
async def test_methodology_sequence_three_gate_event_failure_rolls_back_binding(
    tmp_path,
    monkeypatch,
):
    (
        tasks,
        service,
        contract,
        *_,
        predecessor_dispatch,
    ) = await _settled_sequence_two_methodology_run(tmp_path)
    request = _sequence_three_stage_gate_request(
        tasks,
        service,
        contract,
        predecessor_dispatch,
    )
    before = _database_dump(tasks)
    original_event = service.control_plane._event

    def fail_event(*args, **kwargs):
        if kwargs.get("event_type") == "methodology.stage_gate_configured":
            raise RuntimeError("injected sequence-3 Gate event failure")
        return original_event(*args, **kwargs)

    monkeypatch.setattr(service.control_plane, "_event", fail_event)
    with pytest.raises(RuntimeError, match="sequence-3 Gate event failure"):
        service.configure_methodology_next_stage_gate(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before


@pytest.mark.asyncio
async def test_methodology_sequence_three_predecessor_tamper_blocks_claim(
    tmp_path,
):
    (
        tasks,
        service,
        contract,
        *_,
        predecessor_dispatch,
        _,
        gate_receipt,
    ) = await _configured_sequence_three_methodology_stage(tmp_path)
    claim_request = _sequence_three_stage_run_claim_request(
        tasks,
        service,
        contract,
        predecessor_dispatch,
        gate_receipt,
    )
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            UPDATE orchestration_methodology_successor_predecessors
            SET predecessor_dispatch_receipt_sha256 = ?
            WHERE task_id = ? AND stage_sequence = 3
            """,
            ("0" * 64, contract.task_id),
        )
        db.commit()
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationValidationError,
        match="predecessor.*drifted",
    ):
        service.store.get_methodology_stage_gate(
            contract.task_id,
            contract.stages[2].stage_key,
        )
    with pytest.raises(
        OrchestrationValidationError,
        match="predecessor.*drifted",
    ):
        service.claim_methodology_next_stage_run(
            contract.task_id,
            claim_request,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before


@pytest.mark.asyncio
async def test_methodology_sequence_three_missing_predecessor_blocks_replays(
    tmp_path,
):
    (
        tasks,
        service,
        contract,
        *_,
        predecessor_dispatch,
        gate_request,
        gate_receipt,
    ) = await _configured_sequence_three_methodology_stage(tmp_path)
    claim_request = _sequence_three_stage_run_claim_request(
        tasks,
        service,
        contract,
        predecessor_dispatch,
        gate_receipt,
    )
    claim_receipt = service.claim_methodology_next_stage_run(
        contract.task_id,
        claim_request,
        principal=_migration_principal(),
    )
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            DELETE FROM orchestration_methodology_successor_predecessors
            WHERE task_id = ? AND stage_sequence = 3
            """,
            (contract.task_id,),
        )
        db.commit()

    with pytest.raises(
        OrchestrationValidationError,
        match="predecessor is unavailable",
    ):
        service.configure_methodology_next_stage_gate(
            contract.task_id,
            gate_request,
            principal=_migration_principal(),
        )
    with pytest.raises(
        OrchestrationValidationError,
        match="predecessor is unavailable",
    ):
        service.claim_methodology_next_stage_run(
            contract.task_id,
            claim_request,
            principal=_migration_principal(),
        )
    with pytest.raises(
        OrchestrationValidationError,
        match="predecessor.*(unavailable|drifted)",
    ):
        service.store.get_methodology_stage_run_claim(
            contract.task_id,
            claim_receipt.stage_key,
        )
    with sqlite3.connect(tasks.db_path) as db:
        db.row_factory = sqlite3.Row
        with pytest.raises(
            OrchestrationValidationError,
            match="predecessor.*unavailable",
        ):
            service.store._provider_budget_snapshot(db, claim_receipt.plan_id)
    with pytest.raises(
        OrchestrationValidationError,
        match="predecessor.*unavailable",
    ):
        service.unified_status(contract.task_id)


@pytest.mark.asyncio
async def test_methodology_sequence_three_lineage_root_tamper_blocks_reads(
    tmp_path,
):
    (
        tasks,
        service,
        contract,
        *_,
        predecessor_dispatch,
        _,
        gate_receipt,
    ) = await _configured_sequence_three_methodology_stage(tmp_path)
    claim_request = _sequence_three_stage_run_claim_request(
        tasks,
        service,
        contract,
        predecessor_dispatch,
        gate_receipt,
    )
    claim_receipt = service.claim_methodology_next_stage_run(
        contract.task_id,
        claim_request,
        principal=_migration_principal(),
    )
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            UPDATE orchestration_methodology_stage_run_claims
            SET predecessor_dispatch_id = ?
            WHERE task_id = ? AND stage_sequence = 3
            """,
            ("forged-lineage-root", contract.task_id),
        )
        db.commit()

    for read in (
        lambda: service.store.get_methodology_stage_run_claim(
            contract.task_id,
            claim_receipt.stage_key,
        ),
        lambda: service.claim_methodology_next_stage_run(
            contract.task_id,
            claim_request,
            principal=_migration_principal(),
        ),
        lambda: service.unified_status(contract.task_id),
    ):
        with pytest.raises(
            OrchestrationValidationError,
            match="lineage-root binding drifted",
        ):
            read()
    with sqlite3.connect(tasks.db_path) as db:
        db.row_factory = sqlite3.Row
        with pytest.raises(
            OrchestrationValidationError,
            match="lineage-root binding drifted",
        ):
            service.store._provider_budget_snapshot(db, claim_receipt.plan_id)


@pytest.mark.parametrize(
    ("column", "replacement"),
    (
        ("task_id", "tampered-task"),
        ("plan_id", "tampered-plan"),
        ("predecessor_stage_key", "tampered-stage"),
        ("predecessor_gate_key", "tampered-gate"),
    ),
)
@pytest.mark.asyncio
async def test_methodology_sequence_three_predecessor_metadata_tamper_blocks_get(
    tmp_path,
    column,
    replacement,
):
    (
        tasks,
        service,
        contract,
        *_,
        predecessor_dispatch,
        _,
        gate_receipt,
    ) = await _configured_sequence_three_methodology_stage(tmp_path)
    claim_request = _sequence_three_stage_run_claim_request(
        tasks,
        service,
        contract,
        predecessor_dispatch,
        gate_receipt,
    )
    claim_receipt = service.claim_methodology_next_stage_run(
        contract.task_id,
        claim_request,
        principal=_migration_principal(),
    )
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            f"""
            UPDATE orchestration_methodology_successor_predecessors
            SET {column} = ?
            WHERE stage_gate_request_id = ?
            """,
            (replacement, gate_receipt.request_id),
        )
        db.commit()

    with pytest.raises(
        OrchestrationValidationError,
        match="successor predecessor drifted",
    ):
        service.store.get_methodology_stage_run_claim(
            contract.task_id,
            claim_receipt.stage_key,
        )


@pytest.mark.asyncio
async def test_methodology_sequence_three_requires_current_predecessor_metadata(
    tmp_path,
):
    (
        tasks,
        service,
        contract,
        *_,
        predecessor_dispatch,
    ) = await _settled_sequence_two_methodology_run(tmp_path)
    request = _sequence_three_stage_gate_request(
        tasks,
        service,
        contract,
        predecessor_dispatch,
    )
    task = tasks.get(contract.task_id)
    assert task is not None
    metadata = dict(task.metadata)
    metadata["methodology_current_stage_sequence"] = 99
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            "UPDATE tasks SET metadata = ? WHERE task_id = ?",
            (json.dumps(metadata), contract.task_id),
        )
        db.commit()
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationConflictError,
        match="predecessor chain",
    ):
        service.configure_methodology_next_stage_gate(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before


async def _claimed_sequence_three_methodology_run(tmp_path):
    configured = await _configured_sequence_three_methodology_stage(tmp_path)
    tasks, service, contract, *_, predecessor_dispatch, _, gate_receipt = (
        configured
    )
    claim_request = _sequence_three_stage_run_claim_request(
        tasks,
        service,
        contract,
        predecessor_dispatch,
        gate_receipt,
    )
    claim_receipt = service.claim_methodology_next_stage_run(
        contract.task_id,
        claim_request,
        principal=_migration_principal(),
    )
    return (*configured, claim_request, claim_receipt)


@pytest.mark.asyncio
async def test_methodology_sequence_three_dispatch_settles_current_run(
    tmp_path,
):
    (
        tasks,
        service,
        contract,
        _,
        first_dispatch,
        _,
        _,
        predecessor_dispatch,
        _,
        gate_receipt,
        _,
        claim_receipt,
    ) = await _claimed_sequence_three_methodology_run(tmp_path)
    runner = MethodologyDispatchRunner()
    service.runner = runner

    receipt = await service.dispatch_methodology_next_stage_run(
        contract.task_id,
        allow_unbounded_native_usage=True,
    )

    assert receipt.dispatch_claim.stage_sequence == 3
    assert receipt.dispatch_claim.stage_key == contract.stages[2].stage_key
    assert receipt.dispatch_claim.run_id == claim_receipt.run_id
    assert receipt.dispatch_claim.stage_gate_receipt_id == gate_receipt.receipt_id
    assert receipt.dispatch_claim.predecessor_dispatch_receipt_id == (
        predecessor_dispatch.receipt_id
    )
    assert receipt.process_started is True
    assert receipt.stage_status.value == "completed"
    assert receipt.gate_status.value == "passed"
    assert receipt.next_stage_key == contract.stages[3].stage_key
    assert runner.calls == 1
    current = service.store.get_methodology_stage_run_dispatch(contract.task_id)
    assert current is not None
    assert current.claim.stage_sequence == 3
    assert current.receipt == receipt
    unified = service.unified_status(contract.task_id)
    assert unified.budget.token_reserved == 0
    assert {item.run_id for item in unified.runs} == {
        first_dispatch.dispatch_claim.run_id,
        predecessor_dispatch.dispatch_claim.run_id,
        claim_receipt.run_id,
    }
    assert {item.run_id for item in unified.usage} == {
        first_dispatch.dispatch_claim.run_id,
        predecessor_dispatch.dispatch_claim.run_id,
        claim_receipt.run_id,
    }
    with sqlite3.connect(tasks.db_path) as db:
        rows = db.execute(
            """
            SELECT stage_sequence, predecessor_dispatch_id
            FROM orchestration_methodology_stage_run_dispatches
            WHERE task_id = ? ORDER BY stage_sequence
            """,
            (contract.task_id,),
        ).fetchall()
    assert rows == [
        (2, first_dispatch.dispatch_claim.dispatch_id),
        (3, first_dispatch.dispatch_claim.dispatch_id),
    ]

    replay = await service.dispatch_methodology_next_stage_run(
        contract.task_id,
        allow_unbounded_native_usage=True,
    )
    assert replay == receipt
    assert runner.calls == 1


@pytest.mark.asyncio
async def test_methodology_sequence_three_dispatch_binding_tamper_is_zero_write(
    tmp_path,
):
    tasks, service, contract, *_ = await _claimed_sequence_three_methodology_run(
        tmp_path
    )
    runner = MethodologyDispatchRunner()
    service.runner = runner
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            UPDATE orchestration_methodology_successor_predecessors
            SET predecessor_dispatch_receipt_sha256 = ?
            WHERE task_id = ? AND stage_sequence = 3
            """,
            ("0" * 64, contract.task_id),
        )
        db.commit()
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationValidationError,
        match="successor predecessor drifted",
    ):
        await service.dispatch_methodology_next_stage_run(
            contract.task_id,
            allow_unbounded_native_usage=True,
        )

    assert runner.calls == 0
    assert _database_dump(tasks) == before


@pytest.mark.asyncio
async def test_methodology_sequence_three_dispatch_lineage_tamper_blocks_reads(
    tmp_path,
):
    tasks, service, contract, *_ = await _claimed_sequence_three_methodology_run(
        tmp_path
    )
    service.runner = MethodologyDispatchRunner()
    receipt = await service.dispatch_methodology_next_stage_run(
        contract.task_id,
        allow_unbounded_native_usage=True,
    )
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            UPDATE orchestration_methodology_stage_run_dispatches
            SET predecessor_dispatch_id = ?
            WHERE task_id = ? AND stage_sequence = 3
            """,
            ("forged-dispatch-lineage", contract.task_id),
        )
        db.commit()

    with pytest.raises(
        OrchestrationValidationError,
        match="Run-claim binding drifted",
    ):
        service.store.get_methodology_stage_run_dispatch(contract.task_id)
    with sqlite3.connect(tasks.db_path) as db:
        db.row_factory = sqlite3.Row
        with pytest.raises(
            OrchestrationValidationError,
            match="Run-claim binding drifted",
        ):
            service.store._provider_budget_snapshot(db, receipt.dispatch_claim.plan_id)
    with pytest.raises(
        OrchestrationValidationError,
        match="Run-claim binding drifted",
    ):
        service.unified_status(contract.task_id)


@pytest.mark.asyncio
async def test_methodology_sequence_three_dispatch_current_run_tamper_fails_closed(
    tmp_path,
):
    (
        tasks,
        service,
        contract,
        *_,
        predecessor_dispatch,
        _,
        _,
        _,
        _,
    ) = await _claimed_sequence_three_methodology_run(tmp_path)
    task = tasks.get(contract.task_id)
    assert task is not None
    metadata = dict(task.metadata)
    metadata["methodology_current_run_id"] = (
        predecessor_dispatch.dispatch_claim.run_id
    )
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            "UPDATE tasks SET metadata = ? WHERE task_id = ?",
            (json.dumps(metadata), contract.task_id),
        )
        db.commit()
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationValidationError,
        match="current Run binding drifted",
    ):
        await service.dispatch_methodology_next_stage_run(
            contract.task_id,
            allow_unbounded_native_usage=True,
        )

    assert _database_dump(tasks) == before


@pytest.mark.parametrize("cursor_value", [None, "", 3])
@pytest.mark.asyncio
async def test_methodology_sequence_three_running_dispatch_requires_current_run_cursor(
    tmp_path,
    cursor_value,
):
    tasks, service, contract, *_ = await _claimed_sequence_three_methodology_run(
        tmp_path
    )

    class CrashingRunner:
        async def run(self, _runtime, _prompt, **kwargs):
            await kwargs["on_process"](838_383)
            raise GeneratorExit("simulated sequence-3 cursor crash")

    service.runner = CrashingRunner()
    with pytest.raises(GeneratorExit, match="sequence-3 cursor crash"):
        await service.dispatch_methodology_next_stage_run(
            contract.task_id,
            allow_unbounded_native_usage=True,
        )
    task = tasks.get(contract.task_id)
    assert task is not None
    metadata = dict(task.metadata)
    if cursor_value is None:
        metadata.pop("methodology_current_run_id")
    else:
        metadata["methodology_current_run_id"] = cursor_value
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            "UPDATE tasks SET metadata = ? WHERE task_id = ?",
            (json.dumps(metadata), contract.task_id),
        )
        db.commit()
    after_tamper = _database_dump(tasks)
    process_inspections: list[int] = []

    def inspect_process(pid: int) -> ProcessState:
        process_inspections.append(pid)
        return ProcessState.ALIVE

    service.process_inspector = inspect_process
    with pytest.raises(
        OrchestrationValidationError,
        match="current Run binding drifted",
    ):
        service.store.get_methodology_stage_run_dispatch(contract.task_id)
    with pytest.raises(
        OrchestrationValidationError,
        match="current Run binding drifted",
    ):
        service.resume(contract.task_id)
    with pytest.raises(
        OrchestrationValidationError,
        match="current Run binding drifted",
    ):
        await service.dispatch_methodology_next_stage_run(
            contract.task_id,
            allow_unbounded_native_usage=True,
        )

    assert process_inspections == []
    assert _database_dump(tasks) == after_tamper


@pytest.mark.asyncio
async def test_methodology_sequence_three_dispatch_concurrency_spawns_once(
    tmp_path,
):
    _, service, contract, *_ = await _claimed_sequence_three_methodology_run(
        tmp_path
    )
    runner = MethodologyDispatchRunner()
    service.runner = runner

    results = await asyncio.gather(
        service.dispatch_methodology_next_stage_run(
            contract.task_id,
            allow_unbounded_native_usage=True,
        ),
        service.dispatch_methodology_next_stage_run(
            contract.task_id,
            allow_unbounded_native_usage=True,
        ),
        return_exceptions=True,
    )

    assert runner.calls == 1
    assert sum(
        isinstance(item, MethodologyStageRunDispatchReceipt)
        for item in results
    ) == 1
    assert sum(
        isinstance(item, OrchestrationConflictError) for item in results
    ) == 1


@pytest.mark.asyncio
async def test_methodology_sequence_three_dispatch_recovery_never_respawns(
    tmp_path,
):
    _, service, contract, *_ = await _claimed_sequence_three_methodology_run(
        tmp_path
    )

    class CrashingRunner:
        async def run(self, _runtime, _prompt, **kwargs):
            await kwargs["on_process"](828_282)
            raise GeneratorExit("simulated sequence-3 host crash")

    service.runner = CrashingRunner()
    with pytest.raises(GeneratorExit, match="sequence-3 host crash"):
        await service.dispatch_methodology_next_stage_run(
            contract.task_id,
            allow_unbounded_native_usage=True,
        )
    dispatch = service.store.get_methodology_stage_run_dispatch(contract.task_id)
    assert dispatch is not None
    assert dispatch.claim.stage_sequence == 3
    assert dispatch.state == MethodologyDispatchState.RUNNING

    service.process_inspector = lambda _pid: ProcessState.ALIVE
    with pytest.raises(OrchestrationConflictError, match="duplicate dispatch"):
        service.resume(contract.task_id)
    service.process_inspector = lambda _pid: ProcessState.DEAD
    service.resume(contract.task_id)
    recovered = service.store.get_methodology_stage_run_dispatch(contract.task_id)
    assert recovered is not None
    assert recovered.state == MethodologyDispatchState.SETTLED
    assert recovered.receipt is not None
    assert recovered.receipt.protocol_state.process_status.value == "interrupted"


async def _settled_sequence_three_methodology_run(tmp_path):
    claimed = await _claimed_sequence_three_methodology_run(tmp_path)
    _, service, contract, *_ = claimed
    service.runner = MethodologyDispatchRunner()
    dispatch = await service.dispatch_methodology_next_stage_run(
        contract.task_id,
        allow_unbounded_native_usage=True,
    )
    assert dispatch.dispatch_claim.stage_sequence == 3
    assert dispatch.stage_status.value == "completed"
    assert dispatch.gate_status.value == "passed"
    assert dispatch.next_stage_key == contract.stages[3].stage_key
    return (*claimed, dispatch)


async def _claimed_sequence_four_methodology_run(tmp_path):
    settled = await _settled_sequence_three_methodology_run(tmp_path)
    tasks, service, contract, *_, predecessor_dispatch = settled
    gate_request = _sequence_three_stage_gate_request(
        tasks,
        service,
        contract,
        predecessor_dispatch,
        stage_sequence=4,
    )
    gate_receipt = service.configure_methodology_next_stage_gate(
        contract.task_id,
        gate_request,
        principal=_migration_principal(),
    )
    claim_request = _sequence_three_stage_run_claim_request(
        tasks,
        service,
        contract,
        predecessor_dispatch,
        gate_receipt,
        stage_sequence=4,
    )
    claim_receipt = service.claim_methodology_next_stage_run(
        contract.task_id,
        claim_request,
        principal=_migration_principal(),
    )
    return (
        *settled,
        gate_request,
        gate_receipt,
        claim_request,
        claim_receipt,
    )


async def _settled_sequence_four_methodology_run(tmp_path):
    claimed = await _claimed_sequence_four_methodology_run(tmp_path)
    _, service, contract, *_ = claimed
    service.runner = MethodologyDispatchRunner()
    dispatch = await service.dispatch_methodology_next_stage_run(
        contract.task_id,
        allow_unbounded_native_usage=True,
    )
    assert dispatch.dispatch_claim.stage_sequence == 4
    assert dispatch.stage_status.value == "completed"
    assert dispatch.gate_status.value == "passed"
    assert dispatch.next_stage_key == contract.stages[4].stage_key
    return (*claimed, dispatch)


async def _claimed_sequence_five_methodology_run(tmp_path):
    settled = await _settled_sequence_four_methodology_run(tmp_path)
    tasks, service, contract, *_, predecessor_dispatch = settled
    gate_request = _sequence_three_stage_gate_request(
        tasks,
        service,
        contract,
        predecessor_dispatch,
        stage_sequence=5,
    )
    gate_receipt = service.configure_methodology_next_stage_gate(
        contract.task_id,
        gate_request,
        principal=_migration_principal(),
    )
    claim_request = _sequence_three_stage_run_claim_request(
        tasks,
        service,
        contract,
        predecessor_dispatch,
        gate_receipt,
        stage_sequence=5,
    )
    claim_receipt = service.claim_methodology_next_stage_run(
        contract.task_id,
        claim_request,
        principal=_migration_principal(),
    )
    return (
        *settled,
        gate_request,
        gate_receipt,
        claim_request,
        claim_receipt,
    )


async def _settled_sequence_five_methodology_run(tmp_path):
    claimed = await _claimed_sequence_five_methodology_run(tmp_path)
    _, service, contract, *_ = claimed
    service.runner = MethodologyDispatchRunner()
    dispatch = await service.dispatch_methodology_next_stage_run(
        contract.task_id,
        allow_unbounded_native_usage=True,
    )
    assert dispatch.dispatch_claim.stage_sequence == 5
    assert dispatch.stage_status.value == "completed"
    assert dispatch.gate_status.value == "passed"
    assert dispatch.next_stage_key == contract.stages[5].stage_key
    return (*claimed, dispatch)


async def _claimed_sequence_six_methodology_run(tmp_path):
    settled = await _settled_sequence_five_methodology_run(tmp_path)
    tasks, service, contract, *_, predecessor_dispatch = settled
    gate_request = _sequence_three_stage_gate_request(
        tasks,
        service,
        contract,
        predecessor_dispatch,
        stage_sequence=6,
    )
    gate_receipt = service.configure_methodology_next_stage_gate(
        contract.task_id,
        gate_request,
        principal=_migration_principal(),
    )
    claim_request = _sequence_three_stage_run_claim_request(
        tasks,
        service,
        contract,
        predecessor_dispatch,
        gate_receipt,
        stage_sequence=6,
    )
    claim_receipt = service.claim_methodology_next_stage_run(
        contract.task_id,
        claim_request,
        principal=_migration_principal(),
    )
    return (
        *settled,
        gate_request,
        gate_receipt,
        claim_request,
        claim_receipt,
    )


async def _settled_sequence_six_methodology_run(tmp_path):
    claimed = await _claimed_sequence_six_methodology_run(tmp_path)
    _, service, contract, *_ = claimed
    service.runner = MethodologyDispatchRunner()
    dispatch = await service.dispatch_methodology_next_stage_run(
        contract.task_id,
        allow_unbounded_native_usage=True,
    )
    assert dispatch.dispatch_claim.stage_sequence == 6
    assert dispatch.stage_status.value == "completed"
    assert dispatch.gate_status.value == "passed"
    assert dispatch.next_stage_key == contract.stages[6].stage_key
    return (*claimed, dispatch)


async def _claimed_sequence_seven_methodology_run(tmp_path):
    settled = await _settled_sequence_six_methodology_run(tmp_path)
    tasks, service, contract, *_, predecessor_dispatch = settled
    gate_request = _sequence_three_stage_gate_request(
        tasks,
        service,
        contract,
        predecessor_dispatch,
        stage_sequence=7,
    )
    gate_receipt = service.configure_methodology_next_stage_gate(
        contract.task_id,
        gate_request,
        principal=_migration_principal(),
    )
    claim_request = _sequence_three_stage_run_claim_request(
        tasks,
        service,
        contract,
        predecessor_dispatch,
        gate_receipt,
        stage_sequence=7,
    )
    claim_receipt = service.claim_methodology_next_stage_run(
        contract.task_id,
        claim_request,
        principal=_migration_principal(),
    )
    return (
        *settled,
        gate_request,
        gate_receipt,
        claim_request,
        claim_receipt,
    )


@pytest.mark.asyncio
async def test_methodology_sequence_four_produces_required_artifacts_and_settles(
    tmp_path,
):
    claimed = await _claimed_sequence_four_methodology_run(tmp_path)
    tasks, service, contract = claimed[:3]
    first_dispatch = claimed[4]
    sequence_two_dispatch = claimed[7]
    sequence_three_dispatch = claimed[12]
    gate_request = claimed[13]
    gate_receipt = claimed[14]
    claim_request = claimed[15]
    claim_receipt = claimed[16]
    stage = contract.stages[3]

    assert stage.context.output_contracts
    assert gate_receipt.stage_sequence == 4
    assert gate_receipt.predecessor_dispatch_receipt_id == (
        sequence_three_dispatch.receipt_id
    )
    assert claim_receipt.stage_sequence == 4
    assert claim_receipt.input_artifact_bindings == []
    assert len(claim_receipt.required_outputs) == len(
        stage.context.output_contracts
    )
    assert [item.kind for item in claim_receipt.required_outputs] == [
        item.kind for item in stage.context.output_contracts
    ]
    protocol_run = service.control_plane.get_protocol_run(claim_receipt.run_id)
    assert protocol_run is not None
    assert protocol_run.context_pack.input_artifacts == []
    assert protocol_run.context_pack.required_outputs == (
        claim_receipt.required_outputs
    )
    projection_entry = next(
        item
        for item in protocol_run.context_pack.policies
        if item.title == "Pinned methodology Handoff and Gate projection"
    )
    projection = json.loads(projection_entry.content)
    assert projection["handoff_contract_sha256"] == canonical_sha256(
        stage.handoff.model_dump(mode="json")
    )
    assert projection["gate_contract_sha256"] == canonical_sha256(
        stage.gate.model_dump(mode="json")
    )
    assert projection["gate_requirement_ids"] == [
        item.requirement.requirement_id
        for item in stage.gate.evidence_contracts
    ]
    assert service.configure_methodology_next_stage_gate(
        contract.task_id,
        gate_request,
        principal=_migration_principal(),
    ) == gate_receipt
    assert service.claim_methodology_next_stage_run(
        contract.task_id,
        claim_request,
        principal=_migration_principal(),
    ) == claim_receipt

    runner = MethodologyDispatchRunner()
    service.runner = runner
    receipt = await service.dispatch_methodology_next_stage_run(
        contract.task_id,
        allow_unbounded_native_usage=True,
    )

    assert receipt.dispatch_claim.stage_sequence == 4
    assert receipt.dispatch_claim.predecessor_dispatch_receipt_id == (
        sequence_three_dispatch.receipt_id
    )
    assert receipt.stage_status.value == "completed"
    assert receipt.gate_status.value == "passed"
    assert receipt.next_stage_key == contract.stages[4].stage_key
    assert receipt.artifact_ids == sorted(
        item.output_id for item in claim_receipt.required_outputs
    )
    assert len(receipt.evidence_ids) == len(gate_receipt.requirements)
    assert receipt.active_evidence_ids == receipt.evidence_ids
    assert runner.calls == 1
    assert len(runner.prompts[0].encode("utf-8")) <= PROTOCOL_PROMPT_LIMIT
    with sqlite3.connect(tasks.db_path) as db:
        binding = db.execute(
            """
            SELECT predecessor_dispatch_id
            FROM orchestration_methodology_successor_predecessors
            WHERE task_id = ? AND stage_sequence = 4
            """,
            (contract.task_id,),
        ).fetchone()
        dispatch_row = db.execute(
            """
            SELECT predecessor_dispatch_id
            FROM orchestration_methodology_stage_run_dispatches
            WHERE task_id = ? AND stage_sequence = 4
            """,
            (contract.task_id,),
        ).fetchone()
    assert binding == (sequence_three_dispatch.dispatch_claim.dispatch_id,)
    assert dispatch_row == (first_dispatch.dispatch_claim.dispatch_id,)
    unified = service.unified_status(contract.task_id)
    expected_run_ids = {
        first_dispatch.dispatch_claim.run_id,
        sequence_two_dispatch.dispatch_claim.run_id,
        sequence_three_dispatch.dispatch_claim.run_id,
        claim_receipt.run_id,
    }
    assert {item.run_id for item in unified.runs} == expected_run_ids
    assert {item.run_id for item in unified.usage} == expected_run_ids
    assert unified.budget.token_reserved == 0

    replay = await service.dispatch_methodology_next_stage_run(
        contract.task_id,
        allow_unbounded_native_usage=True,
    )
    assert replay == receipt
    assert runner.calls == 1


@pytest.mark.asyncio
async def test_methodology_sequence_four_rejects_tampered_predecessor_chain(
    tmp_path,
):
    settled = await _settled_sequence_three_methodology_run(tmp_path)
    tasks, service, contract, *_, predecessor_dispatch = settled
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            UPDATE orchestration_methodology_stage_run_dispatches
            SET predecessor_dispatch_id = ?
            WHERE task_id = ? AND stage_sequence = 3
            """,
            ("forged-sequence-3-lineage", contract.task_id),
        )
        db.commit()
    request = _sequence_three_stage_gate_request(
        tasks,
        service,
        contract,
        predecessor_dispatch,
        stage_sequence=4,
    )
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationValidationError,
        match="Run-claim binding drifted",
    ):
        service.configure_methodology_next_stage_gate(
            contract.task_id,
            request,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before


@pytest.mark.asyncio
async def test_methodology_sequence_four_binding_tamper_blocks_all_consumers(
    tmp_path,
):
    claimed = await _claimed_sequence_four_methodology_run(tmp_path)
    tasks, service, contract = claimed[:3]
    gate_receipt = claimed[14]
    claim_receipt = claimed[16]
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            UPDATE orchestration_methodology_successor_predecessors
            SET predecessor_dispatch_receipt_sha256 = ?
            WHERE task_id = ? AND stage_sequence = 4
            """,
            ("0" * 64, contract.task_id),
        )
        db.commit()
    before = _database_dump(tasks)

    for read in (
        lambda: service.store.get_methodology_stage_gate(
            contract.task_id,
            gate_receipt.stage_key,
        ),
        lambda: service.store.get_methodology_stage_run_claim(
            contract.task_id,
            claim_receipt.stage_key,
        ),
        lambda: service.unified_status(contract.task_id),
    ):
        with pytest.raises(
            OrchestrationValidationError,
            match="successor predecessor drifted",
        ):
            read()
    with sqlite3.connect(tasks.db_path) as db:
        db.row_factory = sqlite3.Row
        with pytest.raises(
            OrchestrationValidationError,
            match="successor predecessor drifted",
        ):
            service.store._provider_budget_snapshot(db, claim_receipt.plan_id)
    with pytest.raises(
        OrchestrationValidationError,
        match="successor predecessor drifted",
    ):
        await service.dispatch_methodology_next_stage_run(
            contract.task_id,
            allow_unbounded_native_usage=True,
        )

    assert _database_dump(tasks) == before


@pytest.mark.asyncio
async def test_methodology_sequence_five_binds_selected_artifacts_and_settles(
    tmp_path,
):
    claimed = await _claimed_sequence_five_methodology_run(tmp_path)
    tasks, service, contract = claimed[:3]
    sequence_four_claim = claimed[16]
    sequence_four_dispatch = claimed[17]
    gate_receipt = claimed[-3]
    claim_receipt = claimed[-1]
    stage = contract.stages[4]
    selected_inputs = [
        item
        for item in stage.context.input_contracts
        if item.resolution == "selected_stage_output"
    ]

    assert [item.source_artifact_id for item in selected_inputs] == [
        "business-overview",
        "architecture",
        "code-structure",
    ]
    assert gate_receipt.stage_sequence == 5
    assert gate_receipt.predecessor_dispatch_receipt_id == (
        sequence_four_dispatch.receipt_id
    )
    assert claim_receipt.stage_sequence == 5
    assert [
        item.source_artifact_id
        for item in claim_receipt.input_artifact_bindings
    ] == [item.source_artifact_id for item in selected_inputs]
    for binding in claim_receipt.input_artifact_bindings:
        assert binding.consumer_stage_key == stage.stage_key
        assert binding.producer_stage_key == contract.stages[3].stage_key
        assert binding.producer_run_id == sequence_four_claim.run_id
        assert binding.artifact.artifact_id in (
            sequence_four_dispatch.artifact_ids
        )
        artifact = service.control_plane.get_artifact(
            binding.artifact.artifact_id,
            binding.artifact.version,
        )
        assert artifact is not None
        assert artifact.version_ref() == binding.artifact

    protocol_run = service.control_plane.get_protocol_run(claim_receipt.run_id)
    assert protocol_run is not None
    assert protocol_run.context_pack.input_artifacts == [
        item.artifact for item in claim_receipt.input_artifact_bindings
    ]
    assert protocol_run.context_pack.required_outputs == (
        claim_receipt.required_outputs
    )
    assert [item.kind for item in claim_receipt.required_outputs] == [
        "requirements",
        "requirements-analysis-questions",
    ]

    runner = MethodologyDispatchRunner()
    service.runner = runner
    receipt = await service.dispatch_methodology_next_stage_run(
        contract.task_id,
        allow_unbounded_native_usage=True,
    )

    assert receipt.dispatch_claim.stage_sequence == 5
    assert receipt.stage_status.value == "completed"
    assert receipt.gate_status.value == "passed"
    assert receipt.next_stage_key == contract.stages[5].stage_key
    assert receipt.artifact_ids == sorted(
        item.output_id for item in claim_receipt.required_outputs
    )
    assert len(receipt.evidence_ids) == len(gate_receipt.requirements)
    assert receipt.active_evidence_ids == receipt.evidence_ids
    assert runner.calls == 1
    assert len(runner.prompts[0].encode("utf-8")) <= PROTOCOL_PROMPT_LIMIT
    unified = service.unified_status(contract.task_id)
    assert len(unified.runs) == 5
    assert len(unified.usage) == 5
    assert unified.budget.token_reserved == 0

    replay = await service.dispatch_methodology_next_stage_run(
        contract.task_id,
        allow_unbounded_native_usage=True,
    )
    assert replay == receipt
    assert runner.calls == 1


@pytest.mark.asyncio
async def test_methodology_sequence_five_source_artifact_tamper_blocks_consumers(
    tmp_path,
):
    claimed = await _claimed_sequence_five_methodology_run(tmp_path)
    tasks, service, contract = claimed[:3]
    claim_request = claimed[-2]
    claim_receipt = claimed[-1]
    source = claim_receipt.input_artifact_bindings[0].artifact
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            UPDATE protocol_artifacts SET payload_sha256 = ?
            WHERE artifact_id = ? AND version = ?
            """,
            ("0" * 64, source.artifact_id, source.version),
        )
        db.commit()
    before = _database_dump(tasks)

    for read in (
        lambda: service.store.get_methodology_stage_run_claim(
            contract.task_id,
            claim_receipt.stage_key,
        ),
        lambda: service.claim_methodology_next_stage_run(
            contract.task_id,
            claim_request,
            principal=_migration_principal(),
        ),
        lambda: service.unified_status(contract.task_id),
    ):
        with pytest.raises(
            OrchestrationValidationError,
            match="selected input Artifact authority drifted",
        ):
            read()
    with sqlite3.connect(tasks.db_path) as db:
        db.row_factory = sqlite3.Row
        with pytest.raises(
            OrchestrationValidationError,
            match="selected input Artifact authority drifted",
        ):
            service.store._provider_budget_snapshot(db, claim_receipt.plan_id)
    with pytest.raises(
        OrchestrationValidationError,
        match="selected input Artifact authority drifted",
    ):
        await service.dispatch_methodology_next_stage_run(
            contract.task_id,
            allow_unbounded_native_usage=True,
        )

    assert _database_dump(tasks) == before


@pytest.mark.asyncio
async def test_methodology_sequence_five_never_substitutes_same_kind_artifact(
    tmp_path,
):
    settled = await _settled_sequence_four_methodology_run(tmp_path)
    tasks, service, contract, *_, predecessor_dispatch = settled
    producer_claim = settled[16]
    source_output = next(
        item
        for item in producer_claim.required_outputs
        if item.kind == "business-overview"
    )
    source = service.control_plane.get_artifact(source_output.output_id, 1)
    assert source is not None
    substitute = source.model_copy(
        update={"artifact_id": "unbound-business-overview"}
    )
    service.control_plane.register_artifact(substitute, actor="test")
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            DELETE FROM protocol_artifacts
            WHERE artifact_id = ? AND version = 1
            """,
            (source.artifact_id,),
        )
        db.commit()

    gate_request = _sequence_three_stage_gate_request(
        tasks,
        service,
        contract,
        predecessor_dispatch,
        stage_sequence=5,
    )
    gate_receipt = service.configure_methodology_next_stage_gate(
        contract.task_id,
        gate_request,
        principal=_migration_principal(),
    )
    claim_request = _sequence_three_stage_run_claim_request(
        tasks,
        service,
        contract,
        predecessor_dispatch,
        gate_receipt,
        stage_sequence=5,
    )
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationValidationError,
        match="selected input Artifact is unavailable",
    ):
        service.claim_methodology_next_stage_run(
            contract.task_id,
            claim_request,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before


@pytest.mark.asyncio
async def test_methodology_sequence_five_handoff_tamper_blocks_consumers(
    tmp_path,
):
    claimed = await _claimed_sequence_five_methodology_run(tmp_path)
    tasks, service, contract = claimed[:3]
    claim_receipt = claimed[-1]
    producer_run_id = (
        claim_receipt.input_artifact_bindings[0].producer_run_id
    )
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            UPDATE protocol_runs SET handoff_sha256 = ?
            WHERE run_id = ?
            """,
            ("0" * 64, producer_run_id),
        )
        db.commit()
    before = _database_dump(tasks)

    for read in (
        lambda: service.store.get_methodology_stage_run_claim(
            contract.task_id,
            claim_receipt.stage_key,
        ),
        lambda: service.unified_status(contract.task_id),
    ):
        with pytest.raises(
            (OrchestrationValidationError, ControlPlaneValidationError),
            match=(
                "selected input Artifact authority drifted|"
                "Persisted Handoff Pack does not match"
            ),
        ):
            read()
    with pytest.raises(
        (OrchestrationValidationError, ControlPlaneValidationError),
        match=(
            "selected input Artifact authority drifted|"
            "Persisted Handoff Pack does not match"
        ),
    ):
        await service.dispatch_methodology_next_stage_run(
            contract.task_id,
            allow_unbounded_native_usage=True,
        )

    assert _database_dump(tasks) == before


@pytest.mark.asyncio
async def test_methodology_sequence_six_binds_seed_and_prior_output_and_settles(
    tmp_path,
):
    claimed = await _claimed_sequence_six_methodology_run(tmp_path)
    _, service, contract = claimed[:3]
    sequence_five_claim = claimed[21]
    sequence_five_dispatch = claimed[22]
    gate_receipt = claimed[-3]
    claim_receipt = claimed[-1]
    stage = contract.stages[5]

    assert stage.stage_key == "code-generation-unit-001"
    assert gate_receipt.stage_sequence == 6
    assert gate_receipt.predecessor_dispatch_receipt_id == (
        sequence_five_dispatch.receipt_id
    )
    assert claim_receipt.stage_sequence == 6
    assert [
        item.source_artifact_id
        for item in claim_receipt.input_artifact_bindings
    ] == ["unit-of-work", "requirements"]
    seed_binding, requirements_binding = (
        claim_receipt.input_artifact_bindings
    )
    seed_contract = next(
        item
        for item in stage.context.input_contracts
        if item.resolution == "hash_bound_task_seed"
    )
    assert seed_binding.consumer_stage_key == stage.stage_key
    assert seed_binding.producer_stage_key == "units-generation"
    assert seed_binding.producer_run_id is None
    assert seed_binding.artifact == seed_contract.seed_artifact
    assert seed_binding.artifact.location is not None
    assert service.control_plane.get_artifact(
        seed_binding.artifact.artifact_id,
        seed_binding.artifact.version,
    ) is None
    assert requirements_binding.consumer_stage_key == stage.stage_key
    assert requirements_binding.producer_stage_key == contract.stages[4].stage_key
    assert requirements_binding.producer_run_id == sequence_five_claim.run_id
    requirements_artifact = service.control_plane.get_artifact(
        requirements_binding.artifact.artifact_id,
        requirements_binding.artifact.version,
    )
    assert requirements_artifact is not None
    assert requirements_artifact.version_ref() == requirements_binding.artifact

    protocol_run = service.control_plane.get_protocol_run(claim_receipt.run_id)
    assert protocol_run is not None
    assert protocol_run.context_pack.input_artifacts == [
        seed_binding.artifact,
        requirements_binding.artifact,
    ]
    assert [item.kind for item in claim_receipt.required_outputs] == [
        "code-generation-plan",
        "code-summary",
    ]

    runner = MethodologyDispatchRunner()
    service.runner = runner
    receipt = await service.dispatch_methodology_next_stage_run(
        contract.task_id,
        allow_unbounded_native_usage=True,
    )

    assert receipt.dispatch_claim.stage_sequence == 6
    assert receipt.stage_status.value == "completed"
    assert receipt.gate_status.value == "passed"
    assert receipt.next_stage_key == contract.stages[6].stage_key
    assert receipt.artifact_ids == sorted(
        item.output_id for item in claim_receipt.required_outputs
    )
    assert len(receipt.evidence_ids) == len(gate_receipt.requirements)
    assert runner.calls == 1
    assert len(runner.prompts[0].encode("utf-8")) <= PROTOCOL_PROMPT_LIMIT
    unified = service.unified_status(contract.task_id)
    assert len(unified.runs) == 6
    assert len(unified.usage) == 6
    assert unified.budget.token_reserved == 0

    replay = await service.dispatch_methodology_next_stage_run(
        contract.task_id,
        allow_unbounded_native_usage=True,
    )
    assert replay == receipt
    assert runner.calls == 1


@pytest.mark.asyncio
async def test_methodology_sequence_six_rejects_seed_as_unbound_output(
    tmp_path,
):
    claimed = await _claimed_sequence_six_methodology_run(tmp_path)
    tasks, service, contract = claimed[:3]
    claim_receipt = claimed[-1]
    seed = claim_receipt.input_artifact_bindings[0].artifact
    with sqlite3.connect(tasks.db_path) as db:
        artifact_count_before = db.execute(
            "SELECT COUNT(*) FROM protocol_artifacts"
        ).fetchone()[0]

    runner = UnboundSeedOutputMethodologyDispatchRunner()
    service.runner = runner
    receipt = await service.dispatch_methodology_next_stage_run(
        contract.task_id,
        allow_unbounded_native_usage=True,
    )

    assert runner.calls == 1
    assert receipt.protocol_state.schema_status.value == "protocol_failed"
    assert receipt.protocol_state.semantic_stage_result.value == "blocked"
    assert receipt.handoff_pack_id is None
    assert receipt.artifact_ids == []
    assert receipt.evidence_ids == []
    assert receipt.stage_status.value == "blocked"
    assert receipt.gate_status.value == "pending"
    assert service.control_plane.get_artifact(seed.artifact_id, seed.version) is None
    with sqlite3.connect(tasks.db_path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM protocol_artifacts"
        ).fetchone()[0] == artifact_count_before


@pytest.mark.asyncio
async def test_methodology_sequence_seven_isolates_repeated_unit_and_settles(
    tmp_path,
):
    claimed = await _claimed_sequence_seven_methodology_run(tmp_path)
    _, service, contract = claimed[:3]
    sequence_six_claim = claimed[-6]
    sequence_six_dispatch = claimed[-5]
    gate_receipt = claimed[-3]
    claim_receipt = claimed[-1]
    stage = contract.stages[6]

    assert stage.stage_key == "code-generation-unit-002"
    assert gate_receipt.stage_sequence == 7
    assert gate_receipt.predecessor_dispatch_receipt_id == (
        sequence_six_dispatch.receipt_id
    )
    assert claim_receipt.stage_sequence == 7
    assert [
        item.source_artifact_id
        for item in claim_receipt.input_artifact_bindings
    ] == ["unit-of-work", "requirements"]
    seed_binding, requirements_binding = (
        claim_receipt.input_artifact_bindings
    )
    sequence_six_seed, sequence_six_requirements = (
        sequence_six_claim.input_artifact_bindings
    )
    assert seed_binding.consumer_stage_key == stage.stage_key
    assert seed_binding.producer_stage_key == "units-generation"
    assert seed_binding.producer_run_id is None
    assert seed_binding.consumer_stage_key != (
        sequence_six_seed.consumer_stage_key
    )
    assert seed_binding.artifact == sequence_six_seed.artifact
    assert service.control_plane.get_artifact(
        seed_binding.artifact.artifact_id,
        seed_binding.artifact.version,
    ) is None
    assert requirements_binding.consumer_stage_key == stage.stage_key
    assert requirements_binding.producer_run_id == (
        sequence_six_requirements.producer_run_id
    )
    assert requirements_binding.producer_run_id != sequence_six_claim.run_id
    assert requirements_binding.artifact == sequence_six_requirements.artifact

    protocol_run = service.control_plane.get_protocol_run(claim_receipt.run_id)
    assert protocol_run is not None
    assert protocol_run.context_pack.input_artifacts == [
        seed_binding.artifact,
        requirements_binding.artifact,
    ]
    assert [item.kind for item in claim_receipt.required_outputs] == [
        "code-generation-plan",
        "code-summary",
    ]
    assert {
        item.output_id for item in claim_receipt.required_outputs
    }.isdisjoint(
        item.output_id for item in sequence_six_claim.required_outputs
    )

    runner = MethodologyDispatchRunner()
    service.runner = runner
    receipt = await service.dispatch_methodology_next_stage_run(
        contract.task_id,
        allow_unbounded_native_usage=True,
    )

    assert receipt.dispatch_claim.stage_sequence == 7
    assert receipt.stage_status.value == "completed"
    assert receipt.gate_status.value == "passed"
    assert receipt.next_stage_key == contract.stages[7].stage_key
    assert receipt.artifact_ids == sorted(
        item.output_id for item in claim_receipt.required_outputs
    )
    unified = service.unified_status(contract.task_id)
    assert len(unified.runs) == 7
    assert len(unified.usage) == 7
    assert unified.budget.token_reserved == 0

    replay = await service.dispatch_methodology_next_stage_run(
        contract.task_id,
        allow_unbounded_native_usage=True,
    )
    assert replay == receipt
    assert runner.calls == 1


@pytest.mark.asyncio
async def test_methodology_sequence_seven_predecessor_tamper_blocks_consumers(
    tmp_path,
):
    claimed = await _claimed_sequence_seven_methodology_run(tmp_path)
    tasks, service, contract = claimed[:3]
    gate_receipt = claimed[-3]
    claim_receipt = claimed[-1]
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            UPDATE orchestration_methodology_successor_predecessors
            SET predecessor_dispatch_receipt_sha256 = ?
            WHERE task_id = ? AND stage_sequence = 7
            """,
            ("0" * 64, contract.task_id),
        )
        db.commit()
    before = _database_dump(tasks)

    for read in (
        lambda: service.store.get_methodology_stage_gate(
            contract.task_id,
            gate_receipt.stage_key,
        ),
        lambda: service.store.get_methodology_stage_run_claim(
            contract.task_id,
            claim_receipt.stage_key,
        ),
        lambda: service.unified_status(contract.task_id),
    ):
        with pytest.raises(
            OrchestrationValidationError,
            match="successor predecessor drifted",
        ):
            read()
    with sqlite3.connect(tasks.db_path) as db:
        db.row_factory = sqlite3.Row
        with pytest.raises(
            OrchestrationValidationError,
            match="successor predecessor drifted",
        ):
            service.store._provider_budget_snapshot(db, claim_receipt.plan_id)
    with pytest.raises(
        OrchestrationValidationError,
        match="successor predecessor drifted",
    ):
        await service.dispatch_methodology_next_stage_run(
            contract.task_id,
            allow_unbounded_native_usage=True,
        )

    assert _database_dump(tasks) == before


@pytest.mark.asyncio
async def test_methodology_sequence_six_seed_file_tamper_is_zero_write(
    tmp_path,
):
    settled = await _settled_sequence_five_methodology_run(tmp_path)
    tasks, service, contract, *_, predecessor_dispatch = settled
    gate_request = _sequence_three_stage_gate_request(
        tasks,
        service,
        contract,
        predecessor_dispatch,
        stage_sequence=6,
    )
    gate_receipt = service.configure_methodology_next_stage_gate(
        contract.task_id,
        gate_request,
        principal=_migration_principal(),
    )
    claim_request = _sequence_three_stage_run_claim_request(
        tasks,
        service,
        contract,
        predecessor_dispatch,
        gate_receipt,
        stage_sequence=6,
    )
    seed_ref = next(
        item.seed_artifact
        for item in contract.stages[5].context.input_contracts
        if item.resolution == "hash_bound_task_seed"
    )
    assert seed_ref is not None
    assert seed_ref.location is not None
    seed_path = tmp_path / "repo" / seed_ref.location.path
    seed_path.write_text("tampered sequence-6 seed", encoding="utf-8")
    before = _database_dump(tasks)

    with pytest.raises(
        OrchestrationConflictError,
        match="Artifact binding is stale",
    ):
        service.claim_methodology_next_stage_run(
            contract.task_id,
            claim_request,
            principal=_migration_principal(),
        )

    assert _database_dump(tasks) == before


@pytest.mark.asyncio
async def test_methodology_sequence_six_seed_binding_tamper_blocks_consumers(
    tmp_path,
):
    claimed = await _claimed_sequence_six_methodology_run(tmp_path)
    tasks, service, contract = claimed[:3]
    claim_receipt = claimed[-1]
    payload = claim_receipt.model_dump(mode="json")
    payload["input_artifact_bindings"][0]["producer_run_id"] = (
        "forged-seed-producer-run"
    )
    forged = MethodologyStageRunClaimReceipt.model_validate(
        seal_model_payload(MethodologyStageRunClaimReceipt, payload)
    )
    with sqlite3.connect(tasks.db_path) as db:
        db.execute(
            """
            UPDATE orchestration_methodology_stage_run_claims
            SET receipt_payload = ?, receipt_sha256 = ?
            WHERE task_id = ? AND stage_sequence = 6
            """,
            (
                json.dumps(forged.model_dump(mode="json")),
                forged.content_sha256,
                contract.task_id,
            ),
        )
        db.commit()
    before = _database_dump(tasks)

    for read in (
        lambda: service.store.get_methodology_stage_run_claim(
            contract.task_id,
            claim_receipt.stage_key,
        ),
        lambda: service.unified_status(contract.task_id),
    ):
        with pytest.raises(
            OrchestrationValidationError,
            match="Context or input Artifact authority drifted",
        ):
            read()
    with pytest.raises(
        OrchestrationValidationError,
        match="Context or input Artifact authority drifted",
    ):
        await service.dispatch_methodology_next_stage_run(
            contract.task_id,
            allow_unbounded_native_usage=True,
        )

    assert _database_dump(tasks) == before
