from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from agora.orchestration import cli as orchestration_cli
from agora.orchestration.contracts import load_task_contract
from agora.orchestration.models import Measurement, PlanState, RunState, StageState
from agora.orchestration.protocol_context import RepositoryRevision, resolve_git_revision
from agora.orchestration.runtime import RuntimeCommand, RuntimeResult
from agora.orchestration.service import TaskOrchestrationService
from agora.orchestration.store import OrchestrationConflictError
from agora.projects import ProjectRegistry
from agora.protocol.hashing import seal_model_payload
from agora.protocol.models import ContextPack, HandoffPack
from agora.protocol.state_machines import GateStatus, StageStatus
from agora.tasks.models import AppendEventRequest, utc_now
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
    def __init__(self, contract, *, invalid_outputs: int = 0, wrong_ref: bool = False):
        self.contract = contract
        self.invalid_outputs = invalid_outputs
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
            "stage_result": "succeeded",
            "output_artifacts": [artifact],
            "evidence": evidence,
            "unresolved_questions": [],
            "native_state_snapshot": None,
            "memory_candidates": [],
            "blocker_requirement_ids": [],
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


def _system(tmp_path, *, invalid_outputs: int = 0, wrong_ref: bool = False):
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
        total_cost_budget_usd=12,
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


@pytest.mark.asyncio
async def test_protocol_v1_runs_all_stages_through_authoritative_gates(tmp_path):
    _, service, runner, task = _system(tmp_path)

    status = await service.run_until_blocked(task.task_id, protocol_v1=True)

    assert status.plan.state == PlanState.AWAITING_APPROVAL
    assert [stage.state for stage in status.stages] == [StageState.PASSED] * 3
    assert [run.state for run in status.runs] == [RunState.PASSED] * 3
    assert len(status.usage) == 6
    assert all("SEALED CONTEXT PACK" in prompt for prompt in runner.prompts)
    assert all(len(prompt.encode("utf-8")) <= 24 * 1024 for prompt in runner.prompts)
    assert runner.contexts[0].input_artifacts == []
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


@pytest.mark.asyncio
async def test_unified_projection_reports_formal_progress_usage_and_human_action(
    tmp_path,
):
    _, service, _, task = _system(tmp_path)
    await service.run_until_blocked(task.task_id, protocol_v1=True)

    projection = service.unified_status(task.task_id)

    assert projection.schema_version == "1.0"
    assert projection.task.task_id == task.task_id
    assert projection.task_state_source == "task_manifest"
    assert projection.progress.total_stages == 3
    assert projection.progress.completed_stages == 3
    assert projection.progress.remaining_stage_keys == []
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
    assert unified["schema_version"] == "1.0"
    assert unified["collection_pages"]["runs"]["limit"] == 1

    assert orchestration_cli.main(["status", task.task_id, "--json"]) == 0
    legacy = json.loads(capsys.readouterr().out)
    assert "schema_version" not in legacy
    assert "plan" in legacy


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
    assert service.control_plane.get_protocol_run(status.runs[0].run_id) is None

    service.retry_protocol(task.task_id, status.runs[0].stage_key)
    monkeypatch.setattr(service.control_plane, "start_protocol_run", original_start)
    recovered = await service.run_next(task.task_id, protocol_v1=True)

    assert recovered.attempt == 2
    assert recovered.state == RunState.PASSED
    assert len(runner.prompts) == 1


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
async def test_protocol_retry_reopens_both_projections_and_reevaluates_gate(tmp_path):
    _, service, _, task = _system(tmp_path, invalid_outputs=1)
    first = await service.run_next(task.task_id, protocol_v1=True)

    service.retry_protocol(task.task_id, first.stage_key)
    assert (
        service.control_plane.get_stage(task.task_id, first.stage_key).status
        == StageStatus.READY
    )
    second = await service.run_next(task.task_id, protocol_v1=True)

    assert second.attempt == 2
    assert second.state == RunState.PASSED
    assert (
        service.control_plane.get_stage(task.task_id, first.stage_key).status
        == StageStatus.COMPLETED
    )
    assert service.control_plane.get_gate(
        task.task_id, f"gate:{first.stage_key}"
    ).status == GateStatus.PASSED


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

    monkeypatch.setattr(service.store, "finish_protocol_run", original)
    recovered = service.resume(task.task_id)

    assert recovered.runs[0].state == RunState.PASSED
    assert recovered.runs[0].token_used is None
    assert recovered.runs[0].token_measurement == Measurement.UNAVAILABLE
    assert len(runner.prompts) == 1


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
