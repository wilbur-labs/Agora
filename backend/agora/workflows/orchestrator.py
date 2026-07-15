"""Idempotent workflow-to-execution dispatch and terminal reconciliation."""
from __future__ import annotations

import uuid
import json
import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from contextlib import closing

from agora.execution.dispatcher import ExecutionDispatcher
from agora.execution.models import CancelRunRequest, CreateRunRequest, RunState, TERMINAL_RUN_STATES
from agora.tasks.models import TaskState

from .models import (
    TransitionWorkflowStepRequest, WorkflowDispatchBlocker, WorkflowDispatchResult,
    WorkflowState, WorkflowStepState,
)
from .store import WorkflowConflictError, WorkflowStore


class WorkflowOrchestrator:
    def __init__(self, workflows: WorkflowStore, dispatcher: ExecutionDispatcher):
        self.workflows = workflows
        self.dispatcher = dispatcher
        self._workflow_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def dispatch(self, workflow_id: str) -> WorkflowDispatchResult:
        async with self._workflow_locks[workflow_id]:
            return await self._dispatch_locked(workflow_id)

    async def _dispatch_locked(self, workflow_id: str) -> WorkflowDispatchResult:
        initial = self.workflows.require(workflow_id)
        if initial.state == WorkflowState.FAILED:
            await self._cancel_sibling_runs(initial, except_run_id="")
            return WorkflowDispatchResult(workflow_id=workflow_id, dispatched_run_ids=[], blockers=[])
        if initial.state != WorkflowState.ACTIVE:
            raise WorkflowConflictError("Workflow is not active")
        self._recover_claims(initial)
        await self._reconcile(workflow_id)
        workflow = self.workflows.require(workflow_id)
        if workflow.state != WorkflowState.ACTIVE:
            return WorkflowDispatchResult(workflow_id=workflow_id, dispatched_run_ids=[], blockers=[])
        dispatched: list[str] = []
        blockers: list[WorkflowDispatchBlocker] = []
        for step in workflow.steps:
            if step.state != WorkflowStepState.READY:
                continue
            reason = self._blocker(step.task_id, step.adapter)
            if reason:
                self.workflows.record_blocker(
                    workflow_id, step.step_id, expected_version=step.version, error=reason,
                )
                blockers.append(WorkflowDispatchBlocker(step_id=step.step_id, reason=reason))
                continue
            task = self.dispatcher.store.tasks.get(step.task_id)
            assert task is not None
            token = f"dispatch_{uuid.uuid4().hex}"
            try:
                self.workflows.claim_dispatch(
                    workflow_id, step.step_id, expected_version=step.version, token=token,
                )
            except WorkflowConflictError:
                continue
            try:
                run = self.dispatcher.queue(CreateRunRequest(
                    task_id=task.task_id, adapter=step.adapter, prompt=step.prompt,
                    timeout_seconds=min(7200, task.budget.max_minutes * 60) if task.budget.max_minutes else 600,
                    expected_task_version=task.version, actor="workflow-scheduler",
                    metadata={"workflow_id": workflow_id, "workflow_step_id": step.step_id,
                              "workflow_claim_id": token},
                ))
            except Exception as exc:
                self.workflows.release_dispatch(
                    workflow_id, step.step_id, token=token, error=f"dispatch failed: {exc}",
                )
                blockers.append(WorkflowDispatchBlocker(
                    step_id=step.step_id, reason=f"dispatch failed: {type(exc).__name__}",
                ))
                continue
            self.dispatcher.schedule(run.run_id)
            dispatched.append(run.run_id)
            try:
                self.workflows.bind_run(
                    workflow_id, step.step_id, token=token, run_id=run.run_id,
                )
            except Exception as exc:
                self.workflows.record_binding_error(
                    workflow_id, step.step_id, token=token,
                    error=f"run binding failed: {type(exc).__name__}: {exc}",
                )
                blockers.append(WorkflowDispatchBlocker(
                    step_id=step.step_id,
                    reason="Execution run was queued; workflow binding awaits recovery",
                ))
        return WorkflowDispatchResult(
            workflow_id=workflow_id, dispatched_run_ids=dispatched, blockers=blockers,
        )

    def _recover_claims(self, workflow) -> None:
        claimed = [step for step in workflow.steps if step.state == WorkflowStepState.RUNNING
                   and step.dispatch_token and not step.run_id]
        if not claimed:
            return
        with closing(self.dispatcher.store._connect()) as db:
            rows = db.execute(
                "SELECT run_id, result_metadata FROM execution_runs WHERE result_metadata LIKE ?",
                (f'%"workflow_id":"{workflow.workflow_id}"%',),
            ).fetchall()
        runs_by_token = {}
        for row in rows:
            metadata = json.loads(row["result_metadata"])
            token = metadata.get("workflow_claim_id")
            if token:
                runs_by_token[token] = row["run_id"]
        now = datetime.now(timezone.utc)
        for step in claimed:
            run_id = runs_by_token.get(step.dispatch_token)
            if run_id:
                self.workflows.bind_run(
                    workflow.workflow_id, step.step_id, token=step.dispatch_token, run_id=run_id,
                )
                run = self.dispatcher.store.require(run_id)
                if run.state == RunState.QUEUED:
                    self.dispatcher.schedule(run_id)
                continue
            updated = datetime.fromisoformat(step.updated_at)
            if (now - updated).total_seconds() >= 60:
                self.workflows.release_dispatch(
                    workflow.workflow_id, step.step_id, token=step.dispatch_token,
                    error="Recovered stale dispatch claim with no execution run",
                )

    def _blocker(self, task_id: str | None, adapter: str) -> str | None:
        if task_id is None:
            return "Step requires a task_id before automatic dispatch"
        task = self.dispatcher.store.tasks.get(task_id)
        if task is None:
            return "Referenced task no longer exists"
        if task.state not in {TaskState.PLANNED, TaskState.RUNNING}:
            return f"Task must be planned or running (current: {task.state.value})"
        if adapter not in self.dispatcher.adapters:
            return f"Execution adapter is unavailable: {adapter}"
        return None

    async def _reconcile(self, workflow_id: str) -> None:
        workflow = self.workflows.require(workflow_id)
        if workflow.state != WorkflowState.ACTIVE:
            return
        for step in workflow.steps:
            if step.state != WorkflowStepState.RUNNING or not step.run_id:
                continue
            run = self.dispatcher.store.get(step.run_id)
            if run is None or run.state not in TERMINAL_RUN_STATES:
                continue
            target = WorkflowStepState.SUCCEEDED if run.state == RunState.SUCCEEDED else WorkflowStepState.FAILED
            current = self.workflows.require(workflow_id)
            current_step = next(item for item in current.steps if item.step_id == step.step_id)
            if current_step.state != WorkflowStepState.RUNNING:
                continue
            updated = self.workflows.transition_step(
                workflow_id, step.step_id,
                TransitionWorkflowStepRequest(
                    target_state=target, expected_version=current_step.version,
                    actor="workflow-scheduler", reason=f"Execution run {run.run_id} ended as {run.state.value}",
                ),
            )
            if updated.state == WorkflowState.FAILED:
                await self._cancel_sibling_runs(updated, except_run_id=run.run_id)
                return

    async def _cancel_sibling_runs(self, workflow, *, except_run_id: str) -> None:
        for step in workflow.steps:
            if not step.run_id or step.run_id == except_run_id:
                continue
            last_error: Exception | None = None
            for _ in range(2):
                run = self.dispatcher.store.get(step.run_id)
                if run is None or run.state in TERMINAL_RUN_STATES:
                    last_error = None
                    break
                try:
                    await self.dispatcher.cancel(run.run_id, CancelRunRequest(
                        expected_version=run.version, actor="workflow-scheduler",
                        reason="Sibling workflow step failed",
                    ))
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
            if last_error is not None:
                self.workflows.record_scheduler_error(
                    workflow.workflow_id, step.step_id,
                    error=f"sibling cancellation failed: {type(last_error).__name__}: {last_error}",
                )
