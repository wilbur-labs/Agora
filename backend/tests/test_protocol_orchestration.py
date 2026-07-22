from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from agora.attention.models import AttentionKind, CreateAttentionRequest
from agora.orchestration import cli as orchestration_cli
from agora.orchestration import routing_policy
from agora.orchestration.contracts import load_task_contract
from agora.orchestration.models import Measurement, PlanState, RunState, StageState
from agora.orchestration.protocol_context import RepositoryRevision, resolve_git_revision
from agora.orchestration.runtime import RuntimeCommand, RuntimeResult
from agora.orchestration.service import TaskOrchestrationService
from agora.orchestration.store import (
    OrchestrationConflictError,
    OrchestrationValidationError,
)
from agora.projects import ProjectRegistry
from agora.protocol.agent_adapter import AgentAdapterResult
from agora.protocol.hashing import seal_model_payload, seal_payload
from agora.protocol.models import ContextPack, HandoffPack
from agora.protocol.state_machines import GateStatus, StageStatus, TaskStatus
from agora.tasks.models import (
    AppendEventRequest,
    CreateTaskRequest,
    TaskRisk,
    TaskState,
    utc_now,
)
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


class ProtocolRunner:
    def __init__(
        self,
        contract,
        *,
        invalid_outputs: int = 0,
        blocked_outputs: int = 0,
        wrong_ref: bool = False,
    ):
        self.contract = contract
        self.invalid_outputs = invalid_outputs
        self.blocked_outputs = blocked_outputs
        self.wrong_ref = wrong_ref
        self.prompts: list[str] = []
        self.contexts: list[ContextPack] = []
        self.pid = 424_242

    async def run(self, runtime, prompt, **kwargs):
        self.prompts.append(prompt)
        await kwargs["on_process"](self.pid)
        if self.invalid_outputs:
            self.invalid_outputs -= 1
            return RuntimeResult(0, '{"status":"pass"}', "")
        context = _context_from_prompt(prompt)
        self.contexts.append(context)
        stage = next(
            item for item in self.contract.workflow if item.stage_key == context.stage_key
        )
        content = json.dumps(
            {"stage": stage.stage_key, "result": "reviewed formal output"},
            ensure_ascii=False,
            sort_keys=True,
        )
        existing_versions = [
            item.version
            for item in context.input_artifacts
            if item.artifact_id == context.required_outputs[0].output_id
        ]
        artifact = {
            "schema_version": "1.0",
            "artifact_id": context.required_outputs[0].output_id,
            "project_id": context.project_id,
            "task_id": context.task_id,
            "stage_key": context.stage_key,
            "producer": {
                "runtime": runtime.adapter,
                "run_id": context.run_id,
                "stage_key": context.stage_key,
            },
            "kind": stage.required_artifacts[0].kind,
            "storage": "managed",
            "version": max(existing_versions, default=0) + 1,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "media_type": "application/json",
            "content": content,
            "location": None,
            "created_at": utc_now(),
        }
        artifact_ref = {
            key: artifact[key]
            for key in ("artifact_id", "version", "sha256", "kind", "location")
        }
        requirement_by_id = {
            item.requirement_id: item for item in stage.required_evidence
        }
        evidence = [
            {
                "schema_version": "1.0",
                "evidence_id": f"evidence:{context.run_id}:{item.requirement_id}",
                "project_id": context.project_id,
                "task_id": context.task_id,
                "stage_key": context.stage_key,
                "producer": artifact["producer"],
                "repository_id": REVISION.repository_id,
                "ref": "refs/heads/wrong" if self.wrong_ref else REVISION.ref,
                "commit_sha": REVISION.commit_sha,
                "requirement_id": item.requirement_id,
                "kind": requirement_by_id[item.requirement_id].kind,
                "status": "passed",
                "artifact_versions": [artifact_ref],
                "summary": requirement_by_id[item.requirement_id].description,
                "observed_at": utc_now(),
                "details": {},
            }
            for item in stage.gate_requirements
        ]
        stage_result = "blocked" if self.blocked_outputs else "succeeded"
        if self.blocked_outputs:
            self.blocked_outputs -= 1
        payload = {
            "schema_version": "1.0",
            "pack_id": f"handoff:{context.run_id}",
            "project_id": context.project_id,
            "task_id": context.task_id,
            "stage_key": context.stage_key,
            "run_id": context.run_id,
            "producer": artifact["producer"],
            "input_artifacts": [
                item.model_dump(mode="json") for item in context.input_artifacts
            ],
            "required_outputs": [
                item.model_dump(mode="json") for item in context.required_outputs
            ],
            "forbidden_constraints": list(context.forbidden_constraints),
            "stage_result": stage_result,
            "output_artifacts": [artifact],
            "evidence": evidence,
            "unresolved_questions": [],
            "native_state_snapshot": None,
            "memory_candidates": [],
            "blocker_requirement_ids": (
                ["agent-blocker"] if stage_result == "blocked" else []
            ),
            "suggested_next_action": "This suggestion is not authoritative.",
        }
        return RuntimeResult(
            0,
            json.dumps(seal_model_payload(HandoffPack, payload), ensure_ascii=False),
            "",
        )


def _context_from_prompt(prompt: str) -> ContextPack:
    value = prompt.split("SEALED CONTEXT PACK (canonical JSON):\n", 1)[1]
    value = value.split("\nEND SEALED CONTEXT PACK", 1)[0]
    return ContextPack.model_validate_json(value)


def _system(
    tmp_path,
    *,
    invalid_outputs: int = 0,
    blocked_outputs: int = 0,
    wrong_ref: bool = False,
    risk: TaskRisk = TaskRisk.MEDIUM,
    cost_budget_usd: float | None = 12,
):
    root = tmp_path / "repo"
    root.mkdir(parents=True)
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
    contract = load_task_contract(CONTRACT_PATH)
    runner = ProtocolRunner(
        contract,
        invalid_outputs=invalid_outputs,
        blocked_outputs=blocked_outputs,
        wrong_ref=wrong_ref,
    )
    service = TaskOrchestrationService(
        tasks,
        projects,
        {
            name: RuntimeCommand(adapter=name, command_template=("fake", "{prompt}"))
            for name in ("codex", "claude", "kiro")
        },
        runner=runner,
        revision_resolver=lambda _root, _repository_id: REVISION,
    )
    task = service.create(
        project_id="alpha",
        title=contract.title,
        description=contract.goal,
        total_token_budget=30_000,
        total_cost_budget_usd=cost_budget_usd,
        risk=risk,
        contract=contract,
    )
    return tasks, service, runner, task


def test_git_revision_is_commit_bound_and_rejects_a_dirty_worktree(tmp_path):
    root = tmp_path / "git-repo"
    root.mkdir()

    def git(*args):
        subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
        )

    git("init", "-b", "main")
    git("config", "user.email", "agora@example.invalid")
    git("config", "user.name", "Agora Test")
    tracked = root / "tracked.txt"
    tracked.write_text("sealed", encoding="utf-8")
    git("add", "tracked.txt")
    git("commit", "-m", "sealed revision")

    revision = resolve_git_revision(root, repository_id="alpha")
    assert revision.ref == "refs/heads/main"
    assert len(revision.commit_sha) == 40

    tracked.write_text("dirty", encoding="utf-8")
    with pytest.raises(ValueError, match="clean Git worktree"):
        resolve_git_revision(root, repository_id="alpha")


def test_schema_adds_budget_amendment_ledger_without_rewriting_existing_data(
    tmp_path,
):
    tasks, _, _, task = _system(tmp_path)
    with tasks._transaction() as db:
        db.execute("DROP INDEX idx_orchestration_budget_amendments_plan_version")
        db.execute("DROP TABLE orchestration_budget_amendments")

    reopened = TaskStore(tasks.db_path)

    assert reopened.get(task.task_id) == tasks.get(task.task_id)
    with reopened._connect() as db:
        columns = {
            row[1]
            for row in db.execute(
                "PRAGMA table_info(orchestration_budget_amendments)"
            )
        }
    assert columns == {
        "amendment_id",
        "plan_id",
        "task_id",
        "version",
        "operation_key",
        "payload",
        "created_at",
    }


@pytest.mark.asyncio
async def test_protocol_v1_runs_all_stages_through_authoritative_gates(tmp_path):
    _, service, runner, task = _system(tmp_path)

    initial_route = service.control_plane.get_stage_route(task.task_id)
    assert initial_route.stage_key == "solution_design"
    assert initial_route.stage_status == StageStatus.READY
    assert initial_route.runtime == "codex"

    status = await service.run_until_blocked(task.task_id, protocol_v1=True)

    assert status.plan.state == PlanState.AWAITING_APPROVAL
    assert [stage.state for stage in status.stages] == [StageState.PASSED] * 3
    assert [run.state for run in status.runs] == [RunState.PASSED] * 3
    assert all(run.routing_policy is not None for run in status.runs)
    assert len(status.usage) == 6
    assert all("SEALED CONTEXT PACK" in prompt for prompt in runner.prompts)
    assert all(len(prompt.encode("utf-8")) <= 24 * 1024 for prompt in runner.prompts)
    assert runner.contexts[0].input_artifacts == []
    first_policy = status.runs[0].routing_policy
    assert first_policy.pinned_runtime == "codex"
    assert first_policy.required_reviewers == ["claude", "kiro"]
    assert first_policy.current_run_token_reservation == 13_500
    assert first_policy.protected_future_reviewer_tokens == 16_500
    assert first_policy.current_run_cost_reservation_usd == 5.4
    assert first_policy.protected_future_reviewer_cost_usd == 6.6
    assert all(check.satisfied for check in first_policy.checks)
    assert first_policy.dispatchable is True
    assert runner.contexts[0].budget.max_model_tokens == 13_500
    assert runner.contexts[0].budget.max_cost_usd == 5.4
    assert any(
        item.source_ref
        == f"routing-policy:{first_policy.decision_id}:{first_policy.content_sha256}"
        for item in runner.contexts[0].policies
    )
    assert [item.artifact_id for item in runner.contexts[1].input_artifacts] == [
        runner.contexts[0].required_outputs[0].output_id
    ]
    assert sorted(item.artifact_id for item in runner.contexts[2].input_artifacts) == sorted(
        [
            runner.contexts[0].required_outputs[0].output_id,
            runner.contexts[1].required_outputs[0].output_id,
        ]
    )

    for stage in status.stages:
        control_stage = service.control_plane.get_stage(task.task_id, stage.stage_key)
        gate = service.control_plane.get_gate(task.task_id, f"gate:{stage.stage_key}")
        assert control_stage is not None
        assert control_stage.status == StageStatus.COMPLETED
        assert gate is not None
        assert gate.status == GateStatus.PASSED
        protocol_run = service.control_plane.get_protocol_run(stage.latest_run_id)
        assert protocol_run is not None
        assert protocol_run.handoff_pack is not None
        assert protocol_run.settled_at is not None
    assert service.control_plane.get_task_state(task.task_id).status == (
        TaskStatus.NEEDS_REVIEW
    )
    assert service.control_plane.get_stage_route(task.task_id) is None
    first_run = next(
        run for run in status.runs if run.stage_key == "solution_design"
    )
    persisted = service.control_plane.get_protocol_run(first_run.run_id)
    assert persisted is not None
    assert persisted.protocol_state is not None
    replay = service.control_plane.settle_protocol_run(
        AgentAdapterResult(
            protocol_state=persisted.protocol_state,
            handoff_pack=persisted.handoff_pack,
            error_code=persisted.adapter_error_code,
            attention_required=persisted.attention_required,
        ),
        actor="replay-auditor",
        operation_key=f"protocol-settle:{first_run.run_id}",
    )
    assert replay.replayed is True
    assert replay.next_stage_route.stage_key == "correctness_review"
    assert replay.next_stage_route.stage_status == StageStatus.READY
    assert len(
        [
            event
            for event in service.control_plane.events(task.task_id)
            if event.event_type == "stage.activated"
        ]
    ) == 3


@pytest.mark.asyncio
async def test_high_risk_route_requires_both_independent_reviewers(tmp_path):
    _, service, _, task = _system(tmp_path, risk=TaskRisk.HIGH)

    run = await service.run_next(task.task_id, protocol_v1=True)

    policy = run.routing_policy
    assert policy is not None
    assert policy.task_risk == TaskRisk.HIGH
    assert policy.required_reviewers == ["claude", "kiro"]
    assert {item.runtime for item in policy.reviewer_assignments} == {
        "claude",
        "kiro",
    }
    risk_check = next(
        item for item in policy.checks if item.constraint == "risk_coverage"
    )
    assert risk_check.satisfied is True
    assert "at least 2 independent reviewer" in risk_check.detail


@pytest.mark.asyncio
async def test_policy_blocks_an_unknown_role_capability_without_crashing(
    tmp_path,
    monkeypatch,
):
    _, service, runner, task = _system(tmp_path)
    monkeypatch.delitem(
        routing_policy.ROLE_REQUIRED_CAPABILITIES,
        "engineering_planner",
    )

    with pytest.raises(
        OrchestrationConflictError,
        match="lacks declared capabilities",
    ):
        await service.run_next(task.task_id, protocol_v1=True)

    assert runner.prompts == []
    assert service.status(task.task_id).runs == []


@pytest.mark.asyncio
async def test_policy_blocks_empty_reviewer_assignments_without_crashing(
    tmp_path,
    monkeypatch,
):
    _, service, runner, task = _system(tmp_path)
    monkeypatch.setitem(routing_policy.RUNTIME_CAPABILITIES, "claude", ())
    monkeypatch.setitem(routing_policy.RUNTIME_CAPABILITIES, "kiro", ())

    with pytest.raises(
        OrchestrationConflictError,
        match="capability-complete independent contract binding",
    ):
        await service.run_next(task.task_id, protocol_v1=True)

    assert runner.prompts == []
    assert service.status(task.task_id).runs == []


@pytest.mark.asyncio
async def test_policy_supports_an_explicitly_unbounded_cost_envelope(tmp_path):
    _, service, _, task = _system(tmp_path, cost_budget_usd=None)

    run = await service.run_next(task.task_id, protocol_v1=True)

    policy = run.routing_policy
    assert policy is not None
    assert policy.task_cost_budget_usd is None
    assert policy.current_run_cost_reservation_usd is None
    assert policy.protected_future_reviewer_cost_usd is None
    assert next(
        item for item in policy.checks if item.constraint == "protected_budget"
    ).satisfied is True


@pytest.mark.asyncio
async def test_formal_dispatch_rejects_reviewer_set_reduction_before_claim(tmp_path):
    tasks, service, runner, task = _system(tmp_path)
    with tasks._transaction() as db:
        db.execute(
            "UPDATE tasks SET reviewers = ? WHERE task_id = ?",
            (json.dumps(["claude"]), task.task_id),
        )

    with pytest.raises(
        OrchestrationConflictError,
        match="reviewer declarations differ",
    ):
        await service.run_next(task.task_id, protocol_v1=True)

    assert runner.prompts == []
    assert service.status(task.task_id).runs == []


@pytest.mark.asyncio
async def test_policy_rejects_a_premature_compatibility_reviewer_pass(tmp_path):
    tasks, service, runner, task = _system(tmp_path)
    with tasks._transaction() as db:
        plan_id = db.execute(
            "SELECT plan_id FROM orchestration_plans WHERE task_id = ?",
            (task.task_id,),
        ).fetchone()["plan_id"]
        db.execute(
            """UPDATE orchestration_stages SET state = ?
               WHERE plan_id = ? AND stage_key = 'correctness_review'""",
            (StageState.PASSED.value, plan_id),
        )

    with pytest.raises(
        OrchestrationConflictError,
        match="compatibility assignment do not agree",
    ):
        await service.run_next(task.task_id, protocol_v1=True)

    assert runner.prompts == []
    assert service.status(task.task_id).runs == []


@pytest.mark.asyncio
async def test_policy_is_revalidated_atomically_before_run_claim(
    tmp_path,
    monkeypatch,
):
    tasks, service, runner, task = _system(tmp_path)
    original_claim = service.store.claim_current_stage

    def mutate_reviewer_set_before_claim(*args, **kwargs):
        with tasks._transaction() as db:
            db.execute(
                "UPDATE tasks SET reviewers = ? WHERE task_id = ?",
                (json.dumps(["claude"]), task.task_id),
            )
        return original_claim(*args, **kwargs)

    monkeypatch.setattr(
        service.store,
        "claim_current_stage",
        mutate_reviewer_set_before_claim,
    )
    with pytest.raises(OrchestrationConflictError, match="inputs changed"):
        await service.run_next(task.task_id, protocol_v1=True)

    assert runner.prompts == []
    assert service.status(task.task_id).runs == []


@pytest.mark.asyncio
async def test_persisted_routing_policy_hash_tamper_fails_closed(tmp_path):
    tasks, service, _, task = _system(tmp_path)
    run = await service.run_next(task.task_id, protocol_v1=True)
    with tasks._transaction() as db:
        row = db.execute(
            "SELECT routing_policy_payload FROM orchestration_runs WHERE run_id = ?",
            (run.run_id,),
        ).fetchone()
        payload = json.loads(row["routing_policy_payload"])
        payload["rationale"][0] = "tampered policy rationale"
        db.execute(
            "UPDATE orchestration_runs SET routing_policy_payload = ? WHERE run_id = ?",
            (json.dumps(payload), run.run_id),
        )

    with pytest.raises(ValidationError, match="content_sha256"):
        service.status(task.task_id)


@pytest.mark.asyncio
async def test_explicit_task_approval_completes_frozen_lifecycle_idempotently(
    tmp_path,
):
    _, service, _, task = _system(tmp_path)
    await service.run_until_blocked(task.task_id, protocol_v1=True)

    approved = service.approve(
        task.task_id,
        actor="owner",
        reason="Reviewed all formal results",
    )
    replay = service.approve(
        task.task_id,
        actor="owner",
        reason="Reviewed all formal results",
    )

    assert approved.state == PlanState.READY_FOR_IMPLEMENTATION
    assert replay == approved
    frozen = service.control_plane.get_task_state(task.task_id)
    assert frozen.status == TaskStatus.COMPLETED
    assert service.unified_status(task.task_id).task_state_lifecycle == (
        "control_plane_managed"
    )


@pytest.mark.asyncio
async def test_unified_projection_reports_formal_progress_usage_and_human_action(
    tmp_path,
):
    tasks, service, _, task = _system(tmp_path)
    tasks.transition(
        task.task_id,
        TaskState.REQUIREMENTS,
        actor="legacy-test",
        expected_version=task.version,
    )
    await service.run_until_blocked(task.task_id, protocol_v1=True)

    projection = service.unified_status(task.task_id)

    assert projection.schema_version == "7.0"
    assert projection.task.task_id == task.task_id
    assert projection.task.state == TaskState.REQUIREMENTS
    assert projection.task_state_source == "control_plane"
    assert projection.task_state == TaskStatus.NEEDS_REVIEW
    assert projection.task_state_version == 4
    assert projection.task_state_unavailable_reason is None
    assert projection.task_state_lifecycle == "control_plane_managed"
    assert projection.task_lifecycle_decision.target_status == TaskStatus.NEEDS_REVIEW
    assert projection.task_lifecycle_decision.reason.value == "all_stages_passed"
    assert projection.stage_inventory is not None
    assert projection.stage_inventory_unavailable_reason is None
    assert projection.stage_route is None
    assert projection.stage_route_unavailable_reason is None
    assert projection.progress.source == "control_plane_stage_inventory"
    assert projection.progress.inventory_complete is True
    assert projection.progress.total_stages == 3
    assert projection.progress.completed_stages == 3
    assert projection.progress.current_stage_key is None
    assert projection.progress.current_stage_source is None
    assert projection.progress.remaining_stage_keys == []
    assert len(projection.progress.groups) == 1
    assert projection.progress.groups[0].completed_stages == 3
    assert all(stage.inventory_stage is not None for stage in projection.stages)
    assert all(
        stage.authoritative_stage.status == StageStatus.COMPLETED
        for stage in projection.stages
    )
    assert all(stage.gate.status == GateStatus.PASSED for stage in projection.stages)
    assert len(projection.runs) == 3
    assert all(run.semantic_source == "protocol" for run in projection.runs)
    assert all(run.semantic_result.value == "succeeded" for run in projection.runs)
    assert all(run.wait_state.value == "settled" for run in projection.runs)
    assert projection.collection_totals["artifacts"] == 3
    assert projection.collection_totals["evidence"] == 3
    assert "content" not in projection.artifacts[0].model_dump(mode="json")
    assert projection.budget.token_allocated == 30_000
    assert projection.budget.token_settled is not None
    assert projection.budget.token_measurement == Measurement.ESTIMATED
    assert projection.budget.cost_settled_usd is None
    assert projection.budget.cost_measurement == Measurement.UNAVAILABLE
    assert [item.kind for item in projection.required_human_actions] == [
        "plan_approval"
    ]
    assert projection.next_safe_action.value is None
    assert "Gate" in projection.next_safe_action.unavailable_reason
    assert {item.source for item in projection.audit_events} == {
        "task",
        "control_plane",
    }


def test_unified_projection_fails_explicitly_when_stage_inventory_is_interrupted(
    tmp_path,
):
    tasks, service, _, task = _system(tmp_path)
    with tasks._transaction() as db:
        db.execute(
            "DELETE FROM control_stage_inventories WHERE task_id = ?",
            (task.task_id,),
        )

    projection = service.unified_status(task.task_id)

    assert projection.stage_inventory is None
    assert "task resume" in projection.stage_inventory_unavailable_reason
    assert projection.progress.source == "unavailable"
    assert projection.progress.inventory_complete is False
    assert projection.progress.total_stages is None
    assert projection.progress.completed_stages is None
    assert projection.progress.completed_stage_keys == []
    assert projection.progress.remaining_stage_keys == []
    assert all(stage.inventory_stage is None for stage in projection.stages)
    assert projection.task_state_lifecycle == "unavailable"
    assert projection.task_lifecycle_decision is None


def test_unified_projection_marks_route_unavailable_without_frozen_task_state(
    tmp_path,
):
    tasks, service, _, task = _system(tmp_path)
    with tasks._transaction() as db:
        db.execute(
            "DELETE FROM control_tasks WHERE task_id = ?",
            (task.task_id,),
        )

    projection = service.unified_status(task.task_id)

    assert projection.stage_inventory is not None
    assert projection.stage_route is None
    assert "frozen Task state" in projection.stage_route_unavailable_reason
    assert "task resume" in projection.stage_route_unavailable_reason


def test_unified_projection_reports_lifecycle_drift_and_resume_repairs_it(tmp_path):
    tasks, service, _, task = _system(tmp_path)
    with tasks._transaction() as db:
        db.execute(
            "UPDATE control_tasks SET status = 'backlog' WHERE task_id = ?",
            (task.task_id,),
        )

    drifted = service.unified_status(task.task_id)
    service.resume(task.task_id)
    repaired = service.unified_status(task.task_id)

    assert drifted.task_state == TaskStatus.BACKLOG
    assert drifted.task_state_lifecycle == "reconciliation_required"
    assert drifted.task_lifecycle_decision.target_status == TaskStatus.READY
    assert drifted.stage_route.stage_status == StageStatus.READY
    assert drifted.stage_route.runnable is False
    assert repaired.task_state == TaskStatus.READY
    assert repaired.task_state_lifecycle == "control_plane_managed"
    assert repaired.stage_route.runnable is True


@pytest.mark.parametrize(
    ("corruption", "message"),
    [
        ("operational_stage_missing", "Plan Stage ledger"),
        ("formal_stage_outside", "Formal Stage exists outside"),
        ("formal_gate_mismatch", "Formal Gate does not match"),
        ("duplicate_formal_gates", "Multiple formal Gates"),
        ("current_stage_outside", "Current Plan Stage is outside"),
    ],
)
def test_unified_projection_fails_closed_on_stage_inventory_divergence(
    tmp_path,
    corruption,
    message,
):
    tasks, service, _, task = _system(tmp_path)
    now = utc_now()
    if corruption in {"formal_gate_mismatch", "duplicate_formal_gates"}:
        service.control_plane.ensure_stage(
            task_id=task.task_id,
            stage_key="solution_design",
            gate_key="gate:solution_design",
        )
    with tasks._transaction() as db:
        plan_id = db.execute(
            "SELECT plan_id FROM orchestration_plans WHERE task_id = ?",
            (task.task_id,),
        ).fetchone()["plan_id"]
        if corruption == "operational_stage_missing":
            db.execute(
                "DELETE FROM orchestration_stages WHERE plan_id = ? AND stage_key = ?",
                (plan_id, "methodology_review"),
            )
        elif corruption == "formal_stage_outside":
            db.execute(
                """
                INSERT INTO control_stages (
                    task_id, project_id, stage_key, gate_key, status,
                    version, created_at, updated_at
                ) VALUES (?, ?, 'invented', 'gate:invented', 'pending', 1, ?, ?)
                """,
                (task.task_id, task.project_id, now, now),
            )
        elif corruption == "formal_gate_mismatch":
            db.execute(
                """
                INSERT INTO control_gates (
                    task_id, project_id, gate_key, stage_key, status,
                    version, created_at, updated_at
                ) VALUES (?, ?, 'gate:wrong', 'solution_design', 'pending', 1, ?, ?)
                """,
                (task.task_id, task.project_id, now, now),
            )
        elif corruption == "duplicate_formal_gates":
            for gate_key in ("gate:aaa", "gate:solution_design"):
                db.execute(
                    """
                    INSERT INTO control_gates (
                        task_id, project_id, gate_key, stage_key, status,
                        version, created_at, updated_at
                    ) VALUES (?, ?, ?, 'solution_design', 'pending', 1, ?, ?)
                    """,
                    (task.task_id, task.project_id, gate_key, now, now),
                )
        else:
            db.execute(
                "UPDATE orchestration_plans SET current_stage_key = 'invented' "
                "WHERE plan_id = ?",
                (plan_id,),
            )

    with pytest.raises(OrchestrationConflictError, match=message):
        service.unified_status(task.task_id)


def test_unified_projection_revalidates_persisted_stage_inventory_hash(tmp_path):
    tasks, service, _, task = _system(tmp_path)
    with tasks._transaction() as db:
        db.execute(
            "UPDATE control_stage_inventories SET content_sha256 = ? WHERE task_id = ?",
            ("f" * 64, task.task_id),
        )

    with pytest.raises(ValueError, match="ledger binding"):
        service.unified_status(task.task_id)


@pytest.mark.asyncio
async def test_unified_projection_keeps_exit_zero_protocol_failure_blocked(tmp_path):
    _, service, _, task = _system(tmp_path, invalid_outputs=1)
    await service.run_next(task.task_id, protocol_v1=True)

    projection = service.unified_status(task.task_id)

    run = projection.runs[0]
    stage = projection.stages[0]
    assert run.process_exit_code == 0
    assert run.process_status.value == "exited"
    assert run.schema_status.value == "protocol_failed"
    assert run.semantic_result.value == "blocked"
    assert stage.authoritative_stage.status == StageStatus.BLOCKED
    assert projection.progress.completed_stages == 0
    assert projection.task_state == TaskStatus.BLOCKED
    assert projection.task_state_lifecycle == "control_plane_managed"
    assert projection.attention[0].state.value == "open"
    assert projection.required_human_actions[0].kind == "attention"
    assert projection.next_safe_action.value is None
    assert "Resolve blockers" in projection.compatibility_next_action


@pytest.mark.asyncio
async def test_unified_projection_is_one_snapshot_and_pages_histories(
    tmp_path,
    monkeypatch,
):
    tasks, service, _, task = _system(tmp_path)
    await service.run_until_blocked(task.task_id, protocol_v1=True)
    original_connect = tasks._connect
    connection_count = 0

    with original_connect() as db:
        events_before = db.execute(
            """SELECT
                   (SELECT COUNT(*) FROM task_events WHERE task_id = ?) +
                   (SELECT COUNT(*) FROM control_events WHERE task_id = ?)""",
            (task.task_id, task.task_id),
        ).fetchone()[0]

    def counted_connect():
        nonlocal connection_count
        connection_count += 1
        return original_connect()

    monkeypatch.setattr(tasks, "_connect", counted_connect)
    projection = service.unified_status(
        task.task_id,
        history_limit=1,
        history_offset=1,
    )

    assert connection_count == 1
    assert len(projection.stages) == 3
    assert len(projection.runs) == 1
    assert len(projection.artifacts) == 1
    assert len(projection.evidence) == 1
    assert len(projection.usage) == 1
    assert len(projection.audit_events) == 1
    assert projection.collection_pages["runs"].limit == 1
    assert projection.collection_pages["runs"].offset == 1
    assert projection.collection_pages["runs"].total == 3
    assert projection.collection_pages["stages"].offset == 0
    with original_connect() as db:
        events_after = db.execute(
            """SELECT
                   (SELECT COUNT(*) FROM task_events WHERE task_id = ?) +
                   (SELECT COUNT(*) FROM control_events WHERE task_id = ?)""",
            (task.task_id, task.task_id),
        ).fetchone()[0]
    assert events_after == events_before


def test_unified_projection_bounds_oversized_audit_payloads(tmp_path):
    tasks, service, _, task = _system(tmp_path)
    tasks.append_event(
        task.task_id,
        AppendEventRequest(
            event_type="projection.large_event",
            payload={"body": "x" * 20_000},
            actor="test",
        ),
    )

    projection = service.unified_status(task.task_id, history_limit=200)
    event = next(
        item
        for item in projection.audit_events
        if item.event_type == "projection.large_event"
    )

    assert event.payload_truncated is True
    assert event.payload["projection_truncated"] is True
    assert event.payload["payload_sha256"] == event.payload_sha256
    assert event.payload["original_utf8_bytes"] > 16_384
    assert projection.budget.cost_settled_usd == 0
    assert projection.budget.cost_measurement == Measurement.EXACT
    assert projection.budget.cost_remaining_usd == 12


@pytest.mark.asyncio
async def test_cli_exposes_unified_projection_without_changing_legacy_status(
    tmp_path,
    monkeypatch,
    capsys,
):
    _, service, _, task = _system(tmp_path)
    await service.run_next(task.task_id, protocol_v1=True)
    monkeypatch.setattr(orchestration_cli, "build_service", lambda: service)

    assert orchestration_cli.main(
        ["status", task.task_id, "--protocol-v1", "--json", "--limit", "1"]
    ) == 0
    unified = json.loads(capsys.readouterr().out)
    assert unified["schema_version"] == "7.0"
    assert unified["progress"]["source"] == "control_plane_stage_inventory"
    assert unified["progress"]["inventory_complete"] is True
    assert unified["task_state"] == "active"
    assert unified["task_state_source"] == "control_plane"
    assert unified["task_state_version"] == 3
    assert unified["stage_route"]["stage_key"] == "correctness_review"
    assert unified["stage_route"]["runtime"] == "claude"
    assert unified["progress"]["current_stage_source"] == "control_plane_route"
    assert unified["collection_pages"]["runs"]["limit"] == 1
    assert unified["runs"][0]["routing_policy"]["dispatchable"] is True

    assert orchestration_cli.main(
        ["status", task.task_id, "--protocol-v1"]
    ) == 0
    text_status = capsys.readouterr().out
    assert "[active] source=control_plane legacy=backlog" in text_status
    assert "lifecycle=control_plane_managed" in text_status
    assert "routing=agora-foundation-routing-policy@1.0" in text_status

    assert orchestration_cli.main(["status", task.task_id, "--json"]) == 0
    legacy = json.loads(capsys.readouterr().out)
    assert "schema_version" not in legacy
    assert "plan" in legacy


@pytest.mark.asyncio
async def test_cli_amends_budget_with_explicit_versions_and_discloses_receipt(
    tmp_path,
    monkeypatch,
    capsys,
):
    tasks, service, runner, task = _system(tmp_path, invalid_outputs=1)
    first = await service.run_next(task.task_id, protocol_v1=True)
    service.retry_protocol(task.task_id, first.stage_key)
    current_task = tasks.get(task.task_id)
    current_plan = service.store.require_plan(task.task_id)
    monkeypatch.setattr(orchestration_cli, "build_service", lambda: service)

    assert orchestration_cli.main(
        [
            "amend-budget",
            task.task_id,
            "--tokens",
            "43500",
            "--cost-usd",
            "17.4",
            "--expected-task-version",
            str(current_task.version),
            "--expected-plan-version",
            str(current_plan.version),
            "--reason",
            "Restore protected review headroom",
            "--actor",
            "owner",
        ]
    ) == 0

    output = capsys.readouterr().out
    assert '"schema_version": "1.0"' in output
    assert '"claim_requires_policy_rederivation": true' in output
    assert "Budget amendments:" in output
    assert "tokens=30000->43500" in output
    assert "cost=12.0->17.4" in output
    assert len(runner.prompts) == 1


@pytest.mark.asyncio
async def test_exit_zero_invalid_handoff_blocks_and_creates_protocol_attention(tmp_path):
    _, service, _, task = _system(tmp_path, invalid_outputs=1)

    run = await service.run_next(task.task_id, protocol_v1=True)

    assert run.exit_code == 0
    assert run.state == RunState.BLOCKED
    status = service.status(task.task_id)
    assert status.plan.state == PlanState.BLOCKED
    assert status.stages[0].state == StageState.BLOCKED
    protocol_run = service.control_plane.get_protocol_run(run.run_id)
    assert protocol_run is not None
    assert protocol_run.adapter_error_code.value == "handoff_schema_invalid"
    assert protocol_run.attention_required is True
    assert (
        service.control_plane.get_stage(task.task_id, run.stage_key).status
        == StageStatus.BLOCKED
    )
    assert service.control_plane.projection(task.task_id)["collection_totals"]["attention"] == 1


@pytest.mark.asyncio
async def test_formal_start_failure_never_dispatches_and_settles_exact_zero(
    tmp_path, monkeypatch
):
    _, service, runner, task = _system(tmp_path)
    original_start = service.control_plane.start_protocol_run

    def fail_start(*_args, **_kwargs):
        raise RuntimeError("database boundary failed")

    monkeypatch.setattr(service.control_plane, "start_protocol_run", fail_start)
    with pytest.raises(OrchestrationConflictError, match="RuntimeError"):
        await service.run_next(task.task_id, protocol_v1=True)

    status = service.status(task.task_id)
    assert runner.prompts == []
    assert status.runs[0].state == RunState.FAILED
    assert status.runs[0].token_used == 0
    assert status.runs[0].token_measurement == Measurement.EXACT
    assert status.runs[0].cost_used_usd == 0.0
    assert status.runs[0].cost_measurement == Measurement.EXACT
    assert service.control_plane.get_protocol_run(status.runs[0].run_id) is None

    service.retry_protocol(task.task_id, status.runs[0].stage_key)
    monkeypatch.setattr(service.control_plane, "start_protocol_run", original_start)
    recovered = await service.run_next(task.task_id, protocol_v1=True)

    assert recovered.attempt == 2
    assert recovered.state == RunState.PASSED
    assert len(runner.prompts) == 1


@pytest.mark.asyncio
async def test_protocol_route_rejects_compatibility_current_stage_tamper(tmp_path):
    tasks, service, runner, task = _system(tmp_path)
    with tasks._transaction() as db:
        db.execute(
            """UPDATE orchestration_plans SET current_stage_key = 'correctness_review'
               WHERE task_id = ?""",
            (task.task_id,),
        )

    with pytest.raises(OrchestrationConflictError, match="does not match"):
        await service.run_next(task.task_id, protocol_v1=True)

    assert runner.prompts == []
    assert service.status(task.task_id).runs == []
    assert service.control_plane.get_stage_route(task.task_id).stage_key == (
        "solution_design"
    )


@pytest.mark.asyncio
async def test_protocol_route_rejects_compatibility_runtime_tamper(tmp_path):
    tasks, service, runner, task = _system(tmp_path)
    with tasks._transaction() as db:
        plan_id = db.execute(
            "SELECT plan_id FROM orchestration_plans WHERE task_id = ?",
            (task.task_id,),
        ).fetchone()["plan_id"]
        db.execute(
            """UPDATE orchestration_stages SET adapter = 'kiro'
               WHERE plan_id = ? AND stage_key = 'solution_design'""",
            (plan_id,),
        )

    with pytest.raises(OrchestrationConflictError, match="metadata"):
        await service.run_next(task.task_id, protocol_v1=True)

    assert runner.prompts == []
    assert service.status(task.task_id).runs == []


@pytest.mark.asyncio
async def test_gate_scope_mismatch_becomes_protocol_failure_instead_of_stuck_run(tmp_path):
    _, service, _, task = _system(tmp_path, wrong_ref=True)

    run = await service.run_next(task.task_id, protocol_v1=True)

    protocol_run = service.control_plane.get_protocol_run(run.run_id)
    assert run.state == RunState.BLOCKED
    assert protocol_run is not None
    assert protocol_run.adapter_error_code.value == "handoff_context_mismatch"
    assert protocol_run.settled_at is not None
    assert protocol_run.handoff_pack is None


@pytest.mark.asyncio
async def test_runtime_cancellation_stays_distinct_in_formal_and_operational_state(
    tmp_path, monkeypatch
):
    _, service, runner, task = _system(tmp_path)

    async def cancel(_runtime, _prompt, **kwargs):
        await kwargs["on_process"](runner.pid)
        raise asyncio.CancelledError

    monkeypatch.setattr(runner, "run", cancel)
    with pytest.raises(asyncio.CancelledError):
        await service.run_next(task.task_id, protocol_v1=True)

    run = service.status(task.task_id).runs[0]
    protocol_run = service.control_plane.get_protocol_run(run.run_id)
    assert run.state == RunState.CANCELLED
    assert protocol_run is not None
    assert protocol_run.protocol_state.process_status.value == "cancelled"
    assert protocol_run.protocol_state.semantic_stage_result.value == "cancelled"
    assert service.control_plane.get_stage(
        task.task_id, run.stage_key
    ).status == StageStatus.CANCELLED


@pytest.mark.asyncio
async def test_protocol_retry_cannot_consume_protected_independent_review_budget(
    tmp_path,
):
    _, service, runner, task = _system(tmp_path, invalid_outputs=1)
    first = await service.run_next(task.task_id, protocol_v1=True)

    service.retry_protocol(task.task_id, first.stage_key)
    assert (
        service.control_plane.get_stage(task.task_id, first.stage_key).status
        == StageStatus.READY
    )
    retry_projection = service.unified_status(task.task_id)
    assert retry_projection.attention[0].state.value == "cancelled"
    assert retry_projection.task_state == TaskStatus.READY
    with pytest.raises(
        OrchestrationConflictError,
        match="protected for required independent review",
    ):
        await service.run_next(task.task_id, protocol_v1=True)

    assert len(runner.prompts) == 1
    assert len(service.status(task.task_id).runs) == 1
    assert service.control_plane.get_stage(
        task.task_id, first.stage_key
    ).status == StageStatus.READY


@pytest.mark.asyncio
async def test_versioned_budget_amendment_restores_retry_without_reallocating_stages(
    tmp_path,
):
    tasks, service, runner, task = _system(tmp_path, invalid_outputs=1)
    first = await service.run_next(task.task_id, protocol_v1=True)
    service.retry_protocol(task.task_id, first.stage_key)
    before_task = tasks.get(task.task_id)
    before_status = service.status(task.task_id)
    stage_allocations = [
        (stage.stage_key, stage.token_budget, stage.cost_budget_usd)
        for stage in before_status.stages
    ]
    usage_before = [item.model_dump(mode="json") for item in before_status.usage]

    amendment = service.amend_budget(
        task.task_id,
        amended_total_token_budget=43_500,
        amended_total_cost_budget_usd=17.4,
        expected_task_version=before_task.version,
        expected_plan_version=before_status.plan.version,
        operation_key="budget:test-retry-headroom",
        actor="owner",
        reason="Restore retry headroom without weakening independent review",
    )

    assert amendment.amendment_version == 1
    assert amendment.previous_total_token_budget == 30_000
    assert amendment.amended_total_token_budget == 43_500
    assert amendment.previous_total_cost_budget_usd == 12
    assert amendment.amended_total_cost_budget_usd == 17.4
    assert amendment.prior_policy.dispatchable is False
    assert amendment.resulting_policy.dispatchable is True
    assert next(
        item
        for item in amendment.prior_policy.checks
        if item.constraint == "protected_budget"
    ).satisfied is False
    assert all(item.satisfied for item in amendment.resulting_policy.checks)
    assert amendment.prior_policy.required_reviewers == ["claude", "kiro"]
    assert (
        amendment.prior_policy.reviewer_assignments
        == amendment.resulting_policy.reviewer_assignments
    )
    assert amendment.claim_requires_policy_rederivation is True

    after_task = tasks.get(task.task_id)
    after_status = service.status(task.task_id)
    assert after_task.version == before_task.version + 1
    assert after_task.budget.max_cost_usd == 17.4
    assert after_status.plan.version == before_status.plan.version + 1
    assert after_status.plan.total_token_budget == 43_500
    assert after_status.plan.total_cost_budget_usd == 17.4
    assert [
        (stage.stage_key, stage.token_budget, stage.cost_budget_usd)
        for stage in after_status.stages
    ] == stage_allocations
    assert [item.model_dump(mode="json") for item in after_status.usage] == usage_before
    assert service.store.budget_amendments(after_status.plan.plan_id) == [amendment]

    replay = service.amend_budget(
        task.task_id,
        amended_total_token_budget=43_500,
        amended_total_cost_budget_usd=17.4,
        expected_task_version=before_task.version,
        expected_plan_version=before_status.plan.version,
        operation_key="budget:test-retry-headroom",
        actor="owner",
        reason="Restore retry headroom without weakening independent review",
    )
    assert replay == amendment
    assert tasks.get(task.task_id).version == after_task.version
    assert service.store.require_plan(task.task_id).version == after_status.plan.version

    projection = service.unified_status(task.task_id)
    assert projection.schema_version == "7.0"
    assert projection.budget_amendments == [amendment]
    assert projection.collection_totals["budget_amendments"] == 1
    assert projection.collection_pages["budget_amendments"].total == 1
    assert projection.budget.token_allocated == 43_500
    assert projection.budget.cost_allocated_usd == 17.4

    recovered = await service.run_next(task.task_id, protocol_v1=True)
    assert len(runner.prompts) == 2
    assert recovered.token_reserved == 13_500
    assert recovered.cost_reserved_usd == 5.4
    assert recovered.routing_policy.task_token_budget == 43_500
    assert recovered.routing_policy.task_cost_budget_usd == 17.4


@pytest.mark.asyncio
async def test_budget_amendment_versions_chain_across_distinct_retries(tmp_path):
    tasks, service, _, task = _system(tmp_path, invalid_outputs=2)
    first_run = await service.run_next(task.task_id, protocol_v1=True)
    service.retry_protocol(task.task_id, first_run.stage_key)
    first_task = tasks.get(task.task_id)
    first_plan = service.store.require_plan(task.task_id)
    first_amendment = service.amend_budget(
        task.task_id,
        amended_total_token_budget=43_500,
        amended_total_cost_budget_usd=17.4,
        expected_task_version=first_task.version,
        expected_plan_version=first_plan.version,
        operation_key="budget:chain:first",
        actor="owner",
        reason="Restore first retry headroom",
    )

    second_run = await service.run_next(task.task_id, protocol_v1=True)
    service.retry_protocol(task.task_id, second_run.stage_key)
    second_task = tasks.get(task.task_id)
    second_plan = service.store.require_plan(task.task_id)
    second_amendment = service.amend_budget(
        task.task_id,
        amended_total_token_budget=57_000,
        amended_total_cost_budget_usd=22.8,
        expected_task_version=second_task.version,
        expected_plan_version=second_plan.version,
        operation_key="budget:chain:second",
        actor="owner",
        reason="Restore second retry headroom",
    )

    assert first_amendment.amendment_version == 1
    assert second_amendment.amendment_version == 2
    assert second_amendment.task_version_before == first_amendment.task_version_after
    assert second_amendment.plan_version_before > first_amendment.plan_version_after
    assert second_amendment.plan_version_after == (
        second_amendment.plan_version_before + 1
    )
    assert second_amendment.previous_total_token_budget == 43_500
    assert second_amendment.previous_total_cost_budget_usd == 17.4
    assert service.store.budget_amendments(second_plan.plan_id) == [
        first_amendment,
        second_amendment,
    ]


@pytest.mark.asyncio
async def test_budget_amendment_rolls_back_when_increase_is_insufficient(tmp_path):
    tasks, service, _, task = _system(tmp_path, invalid_outputs=1)
    first = await service.run_next(task.task_id, protocol_v1=True)
    service.retry_protocol(task.task_id, first.stage_key)
    before_task = tasks.get(task.task_id)
    before_status = service.status(task.task_id)
    events_before = tasks.events(task.task_id)

    with pytest.raises(
        OrchestrationValidationError,
        match="may not reduce the Task cost envelope",
    ):
        service.amend_budget(
            task.task_id,
            amended_total_token_budget=30_000,
            amended_total_cost_budget_usd=12 - 5e-10,
            expected_task_version=before_task.version,
            expected_plan_version=before_status.plan.version,
            operation_key="budget:tiny-cost-decrease",
            actor="owner",
            reason="Tiny decreases must still fail closed",
        )

    with pytest.raises(
        OrchestrationValidationError,
        match="still does not satisfy protected budget",
    ):
        service.amend_budget(
            task.task_id,
            amended_total_token_budget=30_001,
            amended_total_cost_budget_usd=12.01,
            expected_task_version=before_task.version,
            expected_plan_version=before_status.plan.version,
            operation_key="budget:insufficient",
            actor="owner",
            reason="Too little headroom",
        )

    after_task = tasks.get(task.task_id)
    after_status = service.status(task.task_id)
    assert after_task == before_task
    assert after_status.plan == before_status.plan
    assert after_status.stages == before_status.stages
    assert after_status.usage == before_status.usage
    assert tasks.events(task.task_id) == events_before
    assert service.store.budget_amendments(before_status.plan.plan_id) == []


@pytest.mark.asyncio
async def test_budget_amendment_rejects_stale_or_conflicting_replay_inputs(tmp_path):
    tasks, service, _, task = _system(tmp_path, invalid_outputs=1)
    first = await service.run_next(task.task_id, protocol_v1=True)
    service.retry_protocol(task.task_id, first.stage_key)
    before_task = tasks.get(task.task_id)
    before_plan = service.store.require_plan(task.task_id)
    service.amend_budget(
        task.task_id,
        amended_total_token_budget=43_500,
        amended_total_cost_budget_usd=17.4,
        expected_task_version=before_task.version,
        expected_plan_version=before_plan.version,
        operation_key="budget:replay-guard",
        actor="owner",
        reason="Restore review headroom",
    )

    with pytest.raises(OrchestrationConflictError, match="different inputs"):
        service.amend_budget(
            task.task_id,
            amended_total_token_budget=44_000,
            amended_total_cost_budget_usd=17.4,
            expected_task_version=before_task.version,
            expected_plan_version=before_plan.version,
            operation_key="budget:replay-guard",
            actor="owner",
            reason="Restore review headroom",
        )
    contract = load_task_contract(CONTRACT_PATH)
    current_route = service.control_plane.get_stage_route(task.task_id)
    with pytest.raises(OrchestrationConflictError, match="different inputs"):
        service.store.amend_budget(
            task.task_id,
            amended_total_token_budget=43_500,
            amended_total_cost_budget_usd=17.4,
            expected_task_version=before_task.version,
            expected_plan_version=before_plan.version,
            operation_key="budget:replay-guard",
            route=current_route.model_copy(update={"stage_key": "forged_route"}),
            contract=contract,
            actor="owner",
            reason="Restore review headroom",
        )
    with pytest.raises(OrchestrationConflictError, match="different inputs"):
        service.store.amend_budget(
            task.task_id,
            amended_total_token_budget=43_500,
            amended_total_cost_budget_usd=17.4,
            expected_task_version=before_task.version,
            expected_plan_version=before_plan.version,
            operation_key="budget:replay-guard",
            route=current_route,
            contract=contract.model_copy(update={"contract_id": "forged_contract"}),
            actor="owner",
            reason="Restore review headroom",
        )
    second_task = tasks.create(
        CreateTaskRequest(
            project_id=task.project_id,
            title="Second Task must not share an operation key",
            kind="architecture",
        )
    )
    with pytest.raises(OrchestrationConflictError, match="different inputs"):
        service.store.amend_budget(
            second_task.task_id,
            amended_total_token_budget=43_500,
            amended_total_cost_budget_usd=17.4,
            expected_task_version=before_task.version,
            expected_plan_version=before_plan.version,
            operation_key="budget:replay-guard",
            route=current_route,
            contract=contract,
            actor="owner",
            reason="Restore review headroom",
        )
    assert tasks.get(second_task.task_id) == second_task
    with pytest.raises(OrchestrationConflictError, match="Expected Task version"):
        service.amend_budget(
            task.task_id,
            amended_total_token_budget=44_000,
            amended_total_cost_budget_usd=18,
            expected_task_version=before_task.version,
            expected_plan_version=before_plan.version,
            operation_key="budget:stale-writer",
            actor="owner",
            reason="Stale second amendment",
        )


@pytest.mark.asyncio
async def test_budget_amendment_rejects_active_run_and_unbounded_cost_rebinding(
    tmp_path,
):
    tasks, service, _, task = _system(tmp_path, invalid_outputs=1)
    first = await service.run_next(task.task_id, protocol_v1=True)
    service.retry_protocol(task.task_id, first.stage_key)
    current_task = tasks.get(task.task_id)
    current_plan = service.store.require_plan(task.task_id)
    with tasks._transaction() as db:
        db.execute(
            "UPDATE orchestration_runs SET state = ? WHERE run_id = ?",
            (RunState.RUNNING.value, first.run_id),
        )
    with pytest.raises(OrchestrationConflictError, match="operational Run is active"):
        service.amend_budget(
            task.task_id,
            amended_total_token_budget=43_500,
            amended_total_cost_budget_usd=17.4,
            expected_task_version=current_task.version,
            expected_plan_version=current_plan.version,
            operation_key="budget:active-run",
            actor="owner",
            reason="Must wait for active Run",
        )
    with tasks._transaction() as db:
        db.execute(
            "UPDATE orchestration_runs SET state = ? WHERE run_id = ?",
            (first.state.value, first.run_id),
        )
        db.execute(
            """UPDATE protocol_runs
               SET protocol_state_payload = NULL,
                   handoff_pack_id = NULL,
                   handoff_payload = NULL,
                   handoff_sha256 = NULL,
                   adapter_error_code = NULL,
                   attention_required = 0,
                   attention_item_id = NULL,
                   settled_at = NULL
               WHERE run_id = ?""",
            (first.run_id,),
        )
    with pytest.raises(OrchestrationConflictError, match="formal Run is unsettled"):
        service.amend_budget(
            task.task_id,
            amended_total_token_budget=43_500,
            amended_total_cost_budget_usd=17.4,
            expected_task_version=current_task.version,
            expected_plan_version=current_plan.version,
            operation_key="budget:unsettled-formal-run",
            actor="owner",
            reason="Must wait for formal settlement",
        )

    _, unbounded_service, _, unbounded_task = _system(
        tmp_path / "unbounded",
        invalid_outputs=1,
        cost_budget_usd=None,
    )
    unbounded_first = await unbounded_service.run_next(
        unbounded_task.task_id,
        protocol_v1=True,
    )
    unbounded_service.retry_protocol(
        unbounded_task.task_id,
        unbounded_first.stage_key,
    )
    unbounded_current_task = unbounded_service.tasks.get(unbounded_task.task_id)
    unbounded_plan = unbounded_service.store.require_plan(unbounded_task.task_id)
    with pytest.raises(
        OrchestrationValidationError,
        match="may not replace an unbounded cost envelope",
    ):
        unbounded_service.amend_budget(
            unbounded_task.task_id,
            amended_total_token_budget=43_500,
            amended_total_cost_budget_usd=1,
            expected_task_version=unbounded_current_task.version,
            expected_plan_version=unbounded_plan.version,
            operation_key="budget:unbounded-cost",
            actor="owner",
            reason="Do not introduce a cost ceiling",
        )


@pytest.mark.asyncio
async def test_budget_amendment_hash_tamper_fails_closed_in_projection(tmp_path):
    tasks, service, _, task = _system(tmp_path, invalid_outputs=1)
    first = await service.run_next(task.task_id, protocol_v1=True)
    service.retry_protocol(task.task_id, first.stage_key)
    current_task = tasks.get(task.task_id)
    current_plan = service.store.require_plan(task.task_id)
    amendment = service.amend_budget(
        task.task_id,
        amended_total_token_budget=43_500,
        amended_total_cost_budget_usd=17.4,
        expected_task_version=current_task.version,
        expected_plan_version=current_plan.version,
        operation_key="budget:tamper",
        actor="owner",
        reason="Restore review headroom",
    )
    with tasks._transaction() as db:
        row = db.execute(
            """SELECT payload FROM orchestration_budget_amendments
               WHERE amendment_id = ?""",
            (amendment.amendment_id,),
        ).fetchone()
        payload = json.loads(row["payload"])
        payload["reason"] = "tampered reason"
        db.execute(
            """UPDATE orchestration_budget_amendments SET payload = ?
               WHERE amendment_id = ?""",
            (json.dumps(payload), amendment.amendment_id),
        )

    with pytest.raises(ValidationError, match="content_sha256"):
        service.unified_status(task.task_id)

    payload = amendment.model_dump(mode="json")
    payload["resulting_policy"]["reviewer_assignments"][0]["runtime"] = (
        "forged_reviewer_runtime"
    )
    payload["resulting_policy"] = seal_payload(payload["resulting_policy"])
    payload = seal_payload(payload)
    with tasks._transaction() as db:
        db.execute(
            """UPDATE orchestration_budget_amendments SET payload = ?
               WHERE amendment_id = ?""",
            (json.dumps(payload), amendment.amendment_id),
        )

    with pytest.raises(ValidationError, match="reviewer_assignments"):
        service.unified_status(task.task_id)

    payload = amendment.model_dump(mode="json")
    payload["resulting_policy"]["decision_id"] = payload["prior_policy"][
        "decision_id"
    ]
    payload["resulting_policy"] = seal_payload(payload["resulting_policy"])
    payload = seal_payload(payload)
    with tasks._transaction() as db:
        db.execute(
            """UPDATE orchestration_budget_amendments SET payload = ?
               WHERE amendment_id = ?""",
            (json.dumps(payload), amendment.amendment_id),
        )

    with pytest.raises(ValidationError, match="distinct decision IDs"):
        service.unified_status(task.task_id)


@pytest.mark.asyncio
async def test_protocol_retry_preserves_unrelated_attention_and_rejects_dispatch(
    tmp_path,
):
    _, service, runner, task = _system(tmp_path, invalid_outputs=1)
    first = await service.run_next(task.task_id, protocol_v1=True)
    unrelated = service.attention.create(
        CreateAttentionRequest(
            task_id=task.task_id,
            kind=AttentionKind.BLOCKER,
            title="Independent owner decision",
            requester="owner",
        )
    )

    service.retry_protocol(task.task_id, first.stage_key)
    projection = service.unified_status(task.task_id)

    assert next(
        item for item in projection.attention if item.item_id == unrelated.item_id
    ).state.value == "open"
    assert projection.task_state == TaskStatus.BLOCKED
    assert projection.stage_route.stage_status == StageStatus.READY
    assert projection.stage_route.runnable is False
    with pytest.raises(OrchestrationConflictError, match="not dispatchable"):
        await service.run_next(task.task_id, protocol_v1=True)
    assert len(runner.prompts) == 1
    assert len(service.status(task.task_id).runs) == 1


@pytest.mark.asyncio
async def test_protocol_settlement_rolls_back_next_route_activation_atomically(
    tmp_path,
    monkeypatch,
):
    _, service, runner, task = _system(tmp_path)
    original_event = service.control_plane._event

    def fail_next_activation(*args, **kwargs):
        if (
            kwargs.get("event_type") == "stage.activated"
            and kwargs.get("payload", {}).get("stage_key") == "correctness_review"
        ):
            raise RuntimeError("next route activation failed")
        return original_event(*args, **kwargs)

    monkeypatch.setattr(service.control_plane, "_event", fail_next_activation)
    with pytest.raises(RuntimeError, match="next route activation failed"):
        await service.run_next(task.task_id, protocol_v1=True)

    status = service.status(task.task_id)
    formal_run = service.control_plane.get_protocol_run(status.runs[0].run_id)
    assert len(runner.prompts) == 1
    assert status.runs[0].state == RunState.RUNNING
    assert formal_run.settled_at is None
    assert service.control_plane.get_stage(
        task.task_id, "solution_design"
    ).status == StageStatus.RUNNING
    assert service.control_plane.get_stage(task.task_id, "correctness_review") is None


@pytest.mark.asyncio
async def test_protocol_retry_rejects_changed_revision_before_mutating_state(tmp_path):
    _, service, _, task = _system(tmp_path, invalid_outputs=1)
    first = await service.run_next(task.task_id, protocol_v1=True)
    control_stage_before = service.control_plane.get_stage(task.task_id, first.stage_key)

    service.revision_resolver = lambda _root, repository_id: RepositoryRevision(
        repository_id=repository_id,
        ref=REVISION.ref,
        commit_sha="b" * 40,
    )
    with pytest.raises(OrchestrationConflictError, match="immutable Gate"):
        service.retry_protocol(task.task_id, first.stage_key)

    status = service.status(task.task_id)
    assert status.plan.state == PlanState.BLOCKED
    assert status.stages[0].state == StageState.BLOCKED
    assert service.control_plane.get_stage(
        task.task_id, first.stage_key
    ) == control_stage_before


@pytest.mark.asyncio
async def test_resume_projects_already_settled_protocol_run_without_redispatch(
    tmp_path, monkeypatch
):
    _, service, runner, task = _system(tmp_path)
    original = service.store.finish_protocol_run

    def interrupt_projection(*_args, **_kwargs):
        raise RuntimeError("simulated crash after authoritative settlement")

    monkeypatch.setattr(service.store, "finish_protocol_run", interrupt_projection)
    with pytest.raises(RuntimeError, match="simulated crash"):
        await service.run_next(task.task_id, protocol_v1=True)
    status = service.status(task.task_id)
    assert status.runs[0].state == RunState.RUNNING
    protocol_run = service.control_plane.get_protocol_run(status.runs[0].run_id)
    assert protocol_run is not None and protocol_run.settled_at is not None
    projection = service.unified_status(task.task_id)
    assert projection.runs[0].wait_state.value == "compatibility_projection_pending"
    assert projection.runs[0].semantic_result.value == "succeeded"
    assert projection.stage_route.stage_key == "correctness_review"
    assert projection.stage_route.stage_status == StageStatus.READY
    assert projection.progress.current_stage_key == "correctness_review"
    assert projection.progress.current_stage_source == "control_plane_route"
    assert projection.plan.current_stage_key == "solution_design"

    monkeypatch.setattr(service.store, "finish_protocol_run", original)
    recovered = service.resume(task.task_id)

    assert recovered.runs[0].state == RunState.PASSED
    assert recovered.runs[0].token_used is None
    assert recovered.runs[0].token_measurement == Measurement.UNAVAILABLE
    assert len(runner.prompts) == 1


@pytest.mark.asyncio
async def test_resume_projects_blocked_settlement_without_advancing_or_redispatch(
    tmp_path,
    monkeypatch,
):
    _, service, runner, task = _system(tmp_path, blocked_outputs=1)
    original = service.store.finish_protocol_run

    def interrupt_projection(*_args, **_kwargs):
        raise RuntimeError("simulated crash after blocked authoritative settlement")

    monkeypatch.setattr(service.store, "finish_protocol_run", interrupt_projection)
    with pytest.raises(RuntimeError, match="blocked authoritative settlement"):
        await service.run_next(task.task_id, protocol_v1=True)

    status = service.status(task.task_id)
    protocol_run = service.control_plane.get_protocol_run(status.runs[0].run_id)
    control_stage = service.control_plane.get_stage(task.task_id, "solution_design")
    gate = service.control_plane.get_gate(task.task_id, "gate:solution_design")
    route = service.control_plane.get_stage_route(task.task_id)
    assert status.runs[0].state == RunState.RUNNING
    assert protocol_run is not None and protocol_run.settled_at is not None
    assert control_stage.status == StageStatus.BLOCKED
    assert gate.status == GateStatus.PASSED
    assert route.stage_key == "solution_design"
    assert route.stage_status == StageStatus.BLOCKED
    assert service.control_plane.get_stage(task.task_id, "correctness_review") is None

    monkeypatch.setattr(service.store, "finish_protocol_run", original)
    recovered = service.resume(task.task_id)

    assert recovered.plan.state == PlanState.BLOCKED
    assert recovered.plan.current_stage_key == "solution_design"
    assert recovered.stages[0].state == StageState.BLOCKED
    assert recovered.runs[0].state == RunState.BLOCKED
    assert len(runner.prompts) == 1
    assert service.control_plane.get_stage(task.task_id, "correctness_review") is None


def test_cli_start_can_explicitly_run_the_formal_protocol_path(
    tmp_path, monkeypatch, capsys
):
    _, service, runner, _ = _system(tmp_path)
    monkeypatch.setattr(orchestration_cli, "build_service", lambda: service)

    exit_code = orchestration_cli.main(
        [
            "start",
            "--contract",
            str(CONTRACT_PATH),
            "--tokens",
            "30000",
            "--run",
            "--protocol-v1",
        ]
    )

    assert exit_code == 0
    assert len(runner.prompts) == 3
    assert "awaiting_approval" in capsys.readouterr().out
