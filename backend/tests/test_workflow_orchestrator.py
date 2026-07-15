from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agora.execution.adapters import build_adapter_registry
from agora.execution.dispatcher import ExecutionDispatcher
from agora.execution.store import ExecutionStore
from agora.execution.models import CreateRunRequest, RunState
from agora.projects import ProjectRegistry
from agora.tasks.models import CreateTaskRequest, TaskState
from agora.tasks.store import TaskStore
from agora.workflows.models import CreateWorkflowRequest, WorkflowActionRequest, WorkflowState, WorkflowStepState
from agora.workflows.orchestrator import WorkflowOrchestrator
from agora.workflows.store import WorkflowStore
from agora.workflows.supervisor import WorkflowSupervisor


def _system(tmp_path):
    roots = tmp_path / "workspaces"
    for project in ("alpha", "beta"):
        (roots / project).mkdir(parents=True)
    command = [sys.executable, "-c", "print('workflow-ok')", "{prompt}"]
    config = {
        "projects": {"registry_path": str(tmp_path / "projects.yaml"), "default": "alpha", "projects": {
            project: {"name": project.title(), "root": str(tmp_path / project),
                      "workspaces": {"codex": str(roots / project)}}
            for project in ("alpha", "beta")
        }},
        "execution": {"adapters": {
            "codex": {"command": command, "workspace_key": "codex"},
            "claude": {"enabled": False}, "kiro": {"enabled": False},
        }},
    }
    tasks = TaskStore(tmp_path / "agora.sqlite")
    runs = ExecutionStore(tasks)
    dispatcher = ExecutionDispatcher(
        runs, ProjectRegistry(config), build_adapter_registry(config),
        allowed_workspace_roots=[roots], max_concurrent_global=4, max_concurrent_per_project=2,
    )
    workflows = WorkflowStore(tasks)
    return tasks, runs, dispatcher, workflows, WorkflowOrchestrator(workflows, dispatcher)


def _planned(tasks: TaskStore, project_id: str, title: str):
    task = tasks.create(CreateTaskRequest(project_id=project_id, title=title))
    with tasks._transaction() as db:
        db.execute("UPDATE tasks SET state='planned', version=2 WHERE task_id=?", (task.task_id,))
    return tasks.get(task.task_id)


@pytest.mark.asyncio
async def test_dispatch_reconcile_and_cross_project_dependency(tmp_path):
    tasks, runs, dispatcher, workflows, orchestrator = _system(tmp_path)
    first = _planned(tasks, "alpha", "Plan")
    second = _planned(tasks, "beta", "Build")
    workflow = workflows.create(CreateWorkflowRequest(title="Delivery", steps=[
        {"key": "plan", "title": "Plan", "project_id": "alpha", "task_id": first.task_id,
         "adapter": "codex", "prompt": "plan"},
        {"key": "build", "title": "Build", "project_id": "beta", "task_id": second.task_id,
         "adapter": "codex", "prompt": "build", "depends_on": ["plan"]},
    ]))
    workflow = workflows.activate(workflow.workflow_id, WorkflowActionRequest(expected_version=1))

    result = await orchestrator.dispatch(workflow.workflow_id)
    assert len(result.dispatched_run_ids) == 1 and not result.blockers
    await asyncio.gather(*list(dispatcher._scheduled))
    assert runs.require(result.dispatched_run_ids[0]).state.value == "succeeded"

    result = await orchestrator.dispatch(workflow.workflow_id)
    assert len(result.dispatched_run_ids) == 1
    current = workflows.require(workflow.workflow_id)
    assert current.steps[0].state == WorkflowStepState.SUCCEEDED
    assert current.steps[1].run_id == result.dispatched_run_ids[0]
    await asyncio.gather(*list(dispatcher._scheduled))

    completed = await orchestrator.dispatch(workflow.workflow_id)
    assert completed.dispatched_run_ids == []
    assert workflows.require(workflow.workflow_id).state == WorkflowState.COMPLETED
    assert len(runs.list(limit=20)) == 2


@pytest.mark.asyncio
async def test_ready_fanout_dispatches_once_and_records_blockers(tmp_path):
    tasks, runs, dispatcher, workflows, orchestrator = _system(tmp_path)
    alpha = _planned(tasks, "alpha", "Alpha")
    beta = _planned(tasks, "beta", "Beta")
    backlog = tasks.create(CreateTaskRequest(project_id="alpha", title="Not ready"))
    workflow = workflows.create(CreateWorkflowRequest(title="Parallel", steps=[
        {"key": "alpha", "title": "Alpha", "project_id": "alpha", "task_id": alpha.task_id,
         "adapter": "codex", "prompt": "alpha"},
        {"key": "beta", "title": "Beta", "project_id": "beta", "task_id": beta.task_id,
         "adapter": "codex", "prompt": "beta"},
        {"key": "blocked", "title": "Blocked", "project_id": "alpha", "task_id": backlog.task_id,
         "adapter": "codex", "prompt": "blocked"},
        {"key": "missing-task", "title": "Missing task", "project_id": "beta",
         "adapter": "codex", "prompt": "missing"},
    ]))
    workflow = workflows.activate(workflow.workflow_id, WorkflowActionRequest(expected_version=1))
    first, second = await asyncio.gather(
        orchestrator.dispatch(workflow.workflow_id), orchestrator.dispatch(workflow.workflow_id),
    )
    assert len(first.dispatched_run_ids) + len(second.dispatched_run_ids) == 2
    assert len(runs.list(limit=20)) == 2
    blockers = [*first.blockers, *second.blockers]
    assert any("planned or running" in item.reason for item in blockers)
    assert any("requires a task_id" in item.reason for item in blockers)
    await asyncio.gather(*list(dispatcher._scheduled))
    before = sum(event.event_type == "workflow.dispatch_blocked"
                 for event in workflows.events(workflow.workflow_id))
    await orchestrator.dispatch(workflow.workflow_id)
    after = sum(event.event_type == "workflow.dispatch_blocked"
                for event in workflows.events(workflow.workflow_id))
    assert before == after == 2


@pytest.mark.asyncio
async def test_recovers_run_created_before_workflow_binding(tmp_path):
    tasks, runs, dispatcher, workflows, orchestrator = _system(tmp_path)
    task = _planned(tasks, "alpha", "Recover")
    workflow = workflows.activate(workflows.create(CreateWorkflowRequest(title="Recover", steps=[
        {"key": "recover", "title": "Recover", "project_id": "alpha", "task_id": task.task_id,
         "adapter": "codex", "prompt": "recover"},
    ])).workflow_id, WorkflowActionRequest(expected_version=1))
    step = workflow.steps[0]
    token = "dispatch_recovery_test"
    workflows.claim_dispatch(workflow.workflow_id, step.step_id, expected_version=step.version, token=token)
    run = dispatcher.queue(CreateRunRequest(
        task_id=task.task_id, adapter="codex", prompt="recover", expected_task_version=task.version,
        metadata={"workflow_id": workflow.workflow_id, "workflow_step_id": step.step_id,
                  "workflow_claim_id": token},
    ))

    result = await orchestrator.dispatch(workflow.workflow_id)
    assert result.dispatched_run_ids == []
    assert workflows.require(workflow.workflow_id).steps[0].run_id == run.run_id
    assert len(runs.list(limit=20)) == 1
    await asyncio.gather(*list(dispatcher._scheduled))
    assert runs.require(run.run_id).state == RunState.SUCCEEDED


@pytest.mark.asyncio
async def test_failed_parallel_step_cancels_running_sibling(tmp_path):
    tasks, runs, dispatcher, workflows, orchestrator = _system(tmp_path)
    script = (
        "import sys,time; prompt=sys.argv[1]; "
        "time.sleep(10) if prompt == 'slow' else None; "
        "sys.exit(7 if prompt == 'fail' else 0)"
    )
    dispatcher.adapters["codex"] = replace(
        dispatcher.adapters["codex"], command_template=(sys.executable, "-c", script, "{prompt}"),
    )
    failed_task = _planned(tasks, "alpha", "Fail")
    slow_task = _planned(tasks, "beta", "Slow")
    slow_task_two = _planned(tasks, "alpha", "Slow two")
    workflow = workflows.activate(workflows.create(CreateWorkflowRequest(title="Failure", steps=[
        {"key": "fail", "title": "Fail", "project_id": "alpha", "task_id": failed_task.task_id,
         "adapter": "codex", "prompt": "fail"},
        {"key": "slow", "title": "Slow", "project_id": "beta", "task_id": slow_task.task_id,
         "adapter": "codex", "prompt": "slow"},
        {"key": "slow-two", "title": "Slow two", "project_id": "alpha", "task_id": slow_task_two.task_id,
         "adapter": "codex", "prompt": "slow"},
    ])).workflow_id, WorkflowActionRequest(expected_version=1))
    result = await orchestrator.dispatch(workflow.workflow_id)
    assert len(result.dispatched_run_ids) == 3
    fail_run = next(run for run in (runs.require(item) for item in result.dispatched_run_ids)
                    if run.project_id == "alpha")
    for _ in range(100):
        if runs.require(fail_run.run_id).state == RunState.FAILED:
            break
        await asyncio.sleep(0.05)
    assert runs.require(fail_run.run_id).state == RunState.FAILED

    bound = workflows.require(workflow.workflow_id)
    slow_run_ids = [step.run_id for step in bound.steps if step.key.startswith("slow")]
    original_cancel = dispatcher.cancel
    attempts = 0

    async def flaky_cancel(run_id, request):
        nonlocal attempts
        if run_id == slow_run_ids[0] and attempts < 2:
            attempts += 1
            raise RuntimeError("simulated cancellation race")
        return await original_cancel(run_id, request)

    dispatcher.cancel = flaky_cancel  # type: ignore[method-assign]
    await orchestrator.dispatch(workflow.workflow_id)
    current = workflows.require(workflow.workflow_id)
    assert current.state == WorkflowState.FAILED
    assert runs.require(slow_run_ids[0]).state not in {RunState.CANCELLED, RunState.SUCCEEDED}
    assert runs.require(slow_run_ids[1]).state == RunState.CANCELLED
    assert any(event.event_type == "workflow.scheduler_error" for event in workflows.events(workflow.workflow_id))

    dispatcher.cancel = original_cancel  # type: ignore[method-assign]
    cleanup = await orchestrator.dispatch(workflow.workflow_id)
    assert cleanup.dispatched_run_ids == []
    assert runs.require(slow_run_ids[0]).state == RunState.CANCELLED
    await asyncio.gather(*list(dispatcher._scheduled), return_exceptions=True)


@pytest.mark.asyncio
async def test_workflow_concurrency_cap_and_opt_in_supervisor(tmp_path):
    tasks, runs, dispatcher, workflows, orchestrator = _system(tmp_path)
    auto_tasks = [_planned(tasks, "alpha" if index % 2 == 0 else "beta", f"Auto {index}") for index in range(3)]
    manual_task = _planned(tasks, "beta", "Manual")
    auto = workflows.activate(workflows.create(CreateWorkflowRequest(
        title="Auto", auto_dispatch=True, max_concurrent_runs=1,
        steps=[{"key": f"auto-{index}", "title": f"Auto {index}", "project_id": task.project_id,
                "task_id": task.task_id, "adapter": "codex", "prompt": f"auto-{index}"}
               for index, task in enumerate(auto_tasks)],
    )).workflow_id, WorkflowActionRequest(expected_version=1))
    manual = workflows.activate(workflows.create(CreateWorkflowRequest(
        title="Manual", auto_dispatch=False,
        steps=[{"key": "manual", "title": "Manual", "project_id": "beta",
                "task_id": manual_task.task_id, "adapter": "codex", "prompt": "manual"}],
    )).workflow_id, WorkflowActionRequest(expected_version=1))
    supervisor = WorkflowSupervisor(workflows, orchestrator, interval_seconds=1)

    await supervisor.run_once()
    auto_state = workflows.require(auto.workflow_id)
    manual_state = workflows.require(manual.workflow_id)
    assert sum(step.run_id is not None for step in auto_state.steps) == 1
    assert all(step.run_id is None for step in manual_state.steps)
    assert len(runs.list(limit=20)) == 1
    await asyncio.gather(*list(dispatcher._scheduled))

    await supervisor.run_once()
    auto_state = workflows.require(auto.workflow_id)
    assert sum(step.run_id is not None for step in auto_state.steps) == 2
    assert auto_state.max_concurrent_runs == 1 and auto_state.auto_dispatch is True
    await asyncio.gather(*list(dispatcher._scheduled))


def test_workflow_supervisor_interval_is_bounded(tmp_path):
    _, _, dispatcher, workflows, orchestrator = _system(tmp_path)
    with pytest.raises(ValueError, match="between 1 and 300"):
        WorkflowSupervisor(workflows, orchestrator, interval_seconds=0.5)


@pytest.mark.asyncio
async def test_workflow_supervisor_ticks_and_shuts_down(tmp_path):
    _, _, _, workflows, orchestrator = _system(tmp_path)
    supervisor = WorkflowSupervisor(workflows, orchestrator, interval_seconds=1)
    supervisor.interval_seconds = 0.01
    ticked_twice = asyncio.Event()
    ticks = 0

    async def count_tick():
        nonlocal ticks
        ticks += 1
        if ticks >= 2:
            ticked_twice.set()

    supervisor.run_once = count_tick  # type: ignore[method-assign]
    supervisor.start()
    await asyncio.wait_for(ticked_twice.wait(), timeout=1)
    await supervisor.shutdown()
    assert ticks >= 2 and supervisor._task is None


@pytest.mark.asyncio
async def test_workflow_supervisor_isolates_dispatch_and_audit_failures():
    summaries = [SimpleNamespace(workflow_id=value, auto_dispatch=True) for value in ("bad", "deleted", "good")]

    class FakeWorkflows:
        def __init__(self):
            self.errors = []

        def list(self, **_kwargs):
            return summaries

        def record_scheduler_error(self, workflow_id, _step_id, *, error):
            if workflow_id == "deleted":
                raise RuntimeError("workflow was deleted")
            self.errors.append((workflow_id, error))

    class FakeOrchestrator:
        def __init__(self):
            self.attempted = []

        async def dispatch(self, workflow_id):
            self.attempted.append(workflow_id)
            if workflow_id != "good":
                raise RuntimeError("dispatch failed")

    workflows = FakeWorkflows()
    orchestrator = FakeOrchestrator()
    supervisor = WorkflowSupervisor(workflows, orchestrator, interval_seconds=1)  # type: ignore[arg-type]
    await supervisor.run_once()
    assert orchestrator.attempted == ["bad", "deleted", "good"]
    assert workflows.errors and workflows.errors[0][0] == "bad"
