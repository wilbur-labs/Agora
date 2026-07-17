from __future__ import annotations

import asyncio
import json
import os

import pytest

from agora.orchestration.methodology import FOUNDATION_METHODOLOGY, methodology_sha256
from agora.orchestration.models import Measurement, PlanState, RunState, StageState
from agora.orchestration.processes import ProcessState, inspect_process
from agora.orchestration.runtime import (
    RuntimeCommand,
    RuntimeInterrupted,
    RuntimeResult,
    build_runtime_registry,
)
from agora.orchestration.service import TaskOrchestrationService
from agora.orchestration.store import (
    OrchestrationConflictError,
    OrchestrationValidationError,
)
from agora.orchestration import cli as orchestration_cli
from agora.projects import ProjectRegistry
from agora.tasks.models import TaskState
from agora.tasks.store import TaskStore


PASS = (
    '{"status":"pass","summary":"stage passed","findings":[], '
    '"recommended_next_action":"continue"}'
)


class FakeRunner:
    def __init__(self, results: list[RuntimeResult]):
        self.results = list(results)
        self.prompts: list[str] = []
        self.pid = 424_242

    async def run(self, runtime, prompt, **kwargs):
        self.prompts.append(prompt)
        await kwargs["on_process"](self.pid)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _system(tmp_path, results=None, *, tokens=30_000, process_inspector=inspect_process):
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    config = {
        "projects": {
            "registry_path": str(tmp_path / "projects.yaml"),
            "default": "alpha",
            "projects": {
                "alpha": {
                    "name": "Alpha",
                    "root": str(root),
                    "workspaces": {
                        "codex": str(root / ".agora" / "workspaces" / "codex"),
                        "claude": str(root / ".agora" / "workspaces" / "claude"),
                        "kiro": str(root / ".agora" / "workspaces" / "kiro"),
                    },
                }
            },
        }
    }
    projects = ProjectRegistry(config, project_root=tmp_path)
    tasks = TaskStore(tmp_path / "agora.db")
    runtimes = {
        name: RuntimeCommand(adapter=name, command_template=("fake", "{prompt}"))
        for name in ("codex", "claude", "kiro")
    }
    runner = FakeRunner(results or [RuntimeResult(0, PASS, "") for _ in range(3)])
    service = TaskOrchestrationService(
        tasks, projects, runtimes, runner=runner, process_inspector=process_inspector,
    )
    task = service.create(
        project_id="alpha", title="Plan delivery", description="Build a safe feature",
        total_token_budget=tokens, total_cost_budget_usd=12,
    )
    return tasks, service, runner, task


def test_foundation_method_is_stable_and_explicitly_provisional():
    assert FOUNDATION_METHODOLOGY.provisional is True
    assert [stage.adapter for stage in FOUNDATION_METHODOLOGY.stages] == [
        "codex", "claude", "kiro",
    ]
    assert sum(stage.token_weight for stage in FOUNDATION_METHODOLOGY.stages) == 100
    assert methodology_sha256(FOUNDATION_METHODOLOGY) == methodology_sha256(
        FOUNDATION_METHODOLOGY.model_copy(deep=True)
    )


def test_runtime_defaults_are_read_only_and_bounded():
    runtimes = build_runtime_registry({})
    assert "read-only" in runtimes["codex"].command_template
    assert "plan" in runtimes["claude"].command_template
    assert "--trust-all-tools" not in runtimes["kiro"].command_template
    with pytest.raises(ValueError, match="exactly one"):
        build_runtime_registry({
            "orchestration": {"runtimes": {"codex": {"command": ["codex"]}}}
        })


def test_create_persists_method_budget_and_task_audit(tmp_path):
    tasks, service, _, task = _system(tmp_path)
    status = service.status(task.task_id)
    assert task.state == TaskState.BACKLOG
    assert status.plan.provisional is True
    assert status.plan.methodology_version == "0.1"
    assert [stage.token_budget for stage in status.stages] == [13_500, 9_000, 7_500]
    assert [stage.cost_budget_usd for stage in status.stages] == [5.4, 3.6, 3.0]
    assert status.next_safe_action == "Run stage solution_design with codex."
    events = tasks.events(task.task_id)
    assert events[-1].event_type == "orchestration.plan_created"
    assert events[-1].payload["provisional"] is True


def test_invalid_budget_does_not_leave_a_planless_task(tmp_path):
    tasks, service, _, _ = _system(tmp_path)
    before = [task.task_id for task in tasks.list()]

    with pytest.raises(OrchestrationValidationError, match="between 3000"):
        service.create(
            project_id="alpha", title="Invalid", description="Too small",
            total_token_budget=2_999, total_cost_budget_usd=1,
        )

    assert [task.task_id for task in tasks.list()] == before


@pytest.mark.asyncio
async def test_three_runtime_loop_records_usage_and_requires_human_approval(tmp_path):
    tasks, service, runner, task = _system(tmp_path)
    status = await service.run_until_blocked(task.task_id)
    assert status.plan.state == PlanState.AWAITING_APPROVAL
    assert [stage.state for stage in status.stages] == [StageState.PASSED] * 3
    assert [run.adapter for run in status.runs] == ["codex", "claude", "kiro"]
    assert all(run.token_measurement == Measurement.ESTIMATED for run in status.runs)
    assert all(run.cost_measurement == Measurement.UNAVAILABLE for run in status.runs)
    assert status.tokens_used > 0
    assert status.tokens_remaining == status.plan.total_token_budget - status.tokens_used
    assert status.cost_used_usd is None
    assert status.cost_measurement == Measurement.UNAVAILABLE
    assert "stage passed" in runner.prompts[1]
    assert "correctness_review" in runner.prompts[2]
    assert tasks.get(task.task_id).state == TaskState.BACKLOG

    plan = service.approve(task.task_id, actor="owner", reason="Reviewed all results")
    assert plan.state == PlanState.READY_FOR_IMPLEMENTATION
    assert tasks.events(task.task_id)[-1].event_type == "orchestration.plan_approved"


@pytest.mark.asyncio
async def test_invalid_or_negative_semantic_result_blocks_without_false_success(tmp_path):
    results = [RuntimeResult(0, "not json", "")]
    _, service, _, task = _system(tmp_path, results)
    run = await service.run_next(task.task_id)
    status = service.status(task.task_id)
    assert run.state == RunState.BLOCKED
    assert run.exit_code == 0
    assert status.plan.state == PlanState.BLOCKED
    assert "semantic result schema" in status.stages[0].blockers[0]
    with pytest.raises(OrchestrationConflictError, match="not awaiting"):
        service.approve(task.task_id, actor="owner", reason="bypass")


@pytest.mark.asyncio
async def test_timeout_blocks_even_when_process_exits_zero_with_valid_semantic_result(tmp_path):
    _, service, _, task = _system(
        tmp_path, [RuntimeResult(0, PASS, "", timed_out=True)],
    )

    run = await service.run_next(task.task_id)
    status = service.status(task.task_id)

    assert run.state == RunState.FAILED
    assert run.exit_code == 0
    assert run.timed_out is True
    assert run.semantic_status.value == "pass"
    assert status.plan.state == PlanState.BLOCKED
    assert "timeout after" in status.stages[0].blockers[0]


@pytest.mark.asyncio
async def test_semantic_text_is_redacted_across_run_stage_and_audit_boundaries(tmp_path):
    secret = "sk-abcdefghijklmnopqrst"
    semantic = json.dumps({
        "status": "needs_work",
        "summary": f"secret={secret}",
        "findings": [f"access_token={secret}"],
        "recommended_next_action": f"password={secret}",
    })
    tasks, service, _, task = _system(
        tmp_path, [RuntimeResult(0, semantic, "")],
    )

    await service.run_next(task.task_id)
    status = service.status(task.task_id)
    persisted = json.dumps(status.model_dump(mode="json"), ensure_ascii=False)
    events = json.dumps(
        [event.payload for event in tasks.events(task.task_id)], ensure_ascii=False,
    )

    assert secret not in persisted
    assert secret not in events
    assert "[REDACTED]" in persisted
    assert "[REDACTED]" in events


@pytest.mark.asyncio
async def test_needs_work_and_token_overrun_block_the_stage(tmp_path):
    needs_work = (
        '{"status":"needs_work","summary":"unsafe","findings":["missing rollback"],'
        '"recommended_next_action":"revise"}'
    )
    _, service, _, task = _system(tmp_path, [RuntimeResult(0, needs_work, "")])
    run = await service.run_next(task.task_id)
    assert run.state == RunState.BLOCKED
    assert service.status(task.task_id).stages[0].blockers == ["missing rollback"]

    _, small_service, _, small_task = _system(
        tmp_path / "second", [RuntimeResult(0, PASS + ("x" * 20_000), "")], tokens=3_000,
    )
    overrun = await small_service.run_next(small_task.task_id)
    assert overrun.state == RunState.BLOCKED
    assert any("exceeded" in item for item in small_service.status(small_task.task_id).stages[0].blockers)


def test_resume_refuses_live_pid_and_recovers_dead_run(tmp_path):
    process_states = {424_242: ProcessState.ALIVE}
    tasks, service, runner, task = _system(
        tmp_path, process_inspector=lambda pid: process_states.get(pid, ProcessState.UNKNOWN),
    )
    status = service.status(task.task_id)
    stage = status.stages[0]
    run = service.store.claim_current_stage(
        task.task_id, prompt_sha256="a" * 64,
        operation_key=f"{status.plan.plan_id}:{stage.stage_key}:manual",
    )
    service.store.attach_pid(run.run_id, runner.pid)
    with pytest.raises(OrchestrationConflictError, match="refusing duplicate"):
        service.resume(task.task_id)

    process_states[runner.pid] = ProcessState.DEAD
    recovered = service.resume(task.task_id)
    assert recovered.plan.state == PlanState.BLOCKED
    assert recovered.runs[0].state == RunState.INTERRUPTED
    assert recovered.stages[0].state == StageState.BLOCKED
    assert tasks.events(task.task_id)[-1].event_type == "orchestration.run_interrupted"


def test_resume_fails_closed_when_pid_was_not_persisted(tmp_path):
    _, service, _, task = _system(tmp_path)
    status = service.status(task.task_id)
    service.store.claim_current_stage(
        task.task_id, prompt_sha256="c" * 64,
        operation_key=f"{status.plan.plan_id}:{status.plan.current_stage_key}:manual",
    )

    with pytest.raises(OrchestrationConflictError, match="process None is unknown"):
        service.resume(task.task_id)


def test_resume_fails_closed_when_process_state_is_unknown(tmp_path):
    _, service, runner, task = _system(
        tmp_path, process_inspector=lambda _pid: ProcessState.UNKNOWN,
    )
    status = service.status(task.task_id)
    run = service.store.claim_current_stage(
        task.task_id, prompt_sha256="b" * 64,
        operation_key=f"{status.plan.plan_id}:{status.plan.current_stage_key}:manual",
    )
    service.store.attach_pid(run.run_id, runner.pid)

    with pytest.raises(OrchestrationConflictError, match="process 424242 is unknown"):
        service.resume(task.task_id)


def test_process_inspection_is_non_destructive_and_detects_missing_pid():
    assert inspect_process(os.getpid()) == ProcessState.ALIVE
    assert inspect_process(999_999_999) == ProcessState.DEAD


def test_duplicate_claim_and_pid_attach_are_rejected_before_redispatch(tmp_path):
    _, service, runner, task = _system(tmp_path)
    status = service.status(task.task_id)
    operation_key = f"{status.plan.plan_id}:{status.plan.current_stage_key}:manual"
    run = service.store.claim_current_stage(
        task.task_id, prompt_sha256="d" * 64, operation_key=operation_key,
    )

    with pytest.raises(OrchestrationConflictError, match="already claimed"):
        service.store.claim_current_stage(
            task.task_id, prompt_sha256="d" * 64, operation_key=operation_key,
        )

    service.store.attach_pid(run.run_id, runner.pid)
    with pytest.raises(OrchestrationConflictError, match="not attachable"):
        service.store.attach_pid(run.run_id, runner.pid + 1)
    assert len(service.status(task.task_id).runs) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "reraised"),
    [
        (RuntimeInterrupted("runtime interrupted"), None),
        (RuntimeError("boundary exploded"), None),
        (asyncio.CancelledError(), asyncio.CancelledError),
    ],
)
async def test_runtime_boundary_failures_are_reconciled(
    tmp_path, failure, reraised,
):
    _, service, _, task = _system(tmp_path, [failure])

    if reraised:
        with pytest.raises(reraised):
            await service.run_next(task.task_id)
    else:
        await service.run_next(task.task_id)

    status = service.status(task.task_id)
    assert len(status.runs) == 1
    if isinstance(failure, RuntimeError) and not isinstance(failure, RuntimeInterrupted):
        assert status.runs[0].state == RunState.FAILED
        assert "runtime boundary failed" in (status.runs[0].error_message or "")
    else:
        assert status.runs[0].state == RunState.INTERRUPTED
        assert service.tasks.events(task.task_id)[-1].event_type == (
            "orchestration.run_interrupted"
        )
    assert status.plan.state == PlanState.BLOCKED


def test_cli_maps_missing_task_to_a_bounded_error(tmp_path, monkeypatch, capsys):
    _, service, _, _ = _system(tmp_path)
    monkeypatch.setattr(orchestration_cli, "build_service", lambda: service)

    assert orchestration_cli.main(["attach", "missing-task"]) == 2
    assert "error: missing-task" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_retry_is_explicit_and_preserves_run_history(tmp_path):
    _, service, runner, task = _system(
        tmp_path, [RuntimeResult(0, "bad", ""), RuntimeResult(0, PASS, "")],
    )
    await service.run_next(task.task_id)
    service.retry(task.task_id, "solution_design")
    retried = await service.run_next(task.task_id)
    status = service.status(task.task_id)
    assert retried.state == RunState.PASSED
    assert len(status.runs) == 2
    assert status.stages[0].attempt_count == 2
    assert len(runner.prompts) == 2
