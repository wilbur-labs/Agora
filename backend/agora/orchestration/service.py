"""Application service for one task-scoped, three-runtime planning loop."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from agora.attention.models import AttentionState, CancelAttentionRequest
from agora.attention.store import AttentionConflictError, AttentionStore
from agora.control_plane.models import (
    ProtocolRunRecord,
    RunSettlementReceipt,
    StageRouteDecision,
    TaskTransitionCause,
)
from agora.control_plane.store import (
    ControlPlaneConflictError,
    ControlPlaneNotFoundError,
    ControlPlaneStore,
    ControlPlaneValidationError,
)
from agora.projects import ProjectRegistry
from agora.protocol.agent_adapter import AgentAdapterResult
from agora.protocol.hashing import (
    canonical_json_bytes,
    canonical_sha256,
    seal_model_payload,
)
from agora.protocol.models import StageInventory
from agora.protocol.state_machines import StageStatus, TaskStatus
from agora.tasks.models import CreateTaskRequest, TaskBudget, TaskManifest, TaskRisk, utc_now
from agora.tasks.store import TaskStore

from .contracts import TaskContract, canonical_contract_json, contract_sha256
from .methodology import (
    FOUNDATION_METHODOLOGY,
    MethodologyDefinition,
    methodology_sha256,
)
from .models import (
    BudgetAmendment,
    Measurement,
    OrchestrationRun,
    PlanState,
    RunState,
    SemanticResult,
    StageState,
    TaskOrchestrationStatus,
    UnifiedTaskProjection,
)
from .processes import ProcessState, inspect_process
from .protocol_adapter import adapt_runtime_result
from .protocol_context import (
    ProtocolRunDefinition,
    RepositoryRevision,
    build_protocol_run_definition,
    resolve_git_revision,
)
from .projection import TaskProjectionStore
from .runtime import (
    OUTPUT_LIMIT,
    ReadOnlyCliRunner,
    RuntimeCommand,
    RuntimeInterrupted,
    RuntimeResult,
)
from .store import (
    OrchestrationConflictError,
    OrchestrationStore,
    OrchestrationValidationError,
)


PRIOR_RESULTS_CONTEXT_LIMIT = 7_000
STAGE_CONTRACT_CONTEXT_LIMIT = 6_000


class TaskOrchestrationService:
    def __init__(
        self,
        tasks: TaskStore,
        projects: ProjectRegistry,
        runtimes: dict[str, RuntimeCommand],
        *,
        runner: ReadOnlyCliRunner | None = None,
        process_inspector: Callable[[int], ProcessState] = inspect_process,
        revision_resolver: Callable[[Path, str], RepositoryRevision] | None = None,
        methodology: MethodologyDefinition = FOUNDATION_METHODOLOGY,
        timeout_seconds: int = 600,
    ):
        self.tasks = tasks
        self.projects = projects
        self.runtimes = runtimes
        self.runner = runner or ReadOnlyCliRunner()
        self.process_inspector = process_inspector
        self.revision_resolver = revision_resolver or (
            lambda root, repository_id: resolve_git_revision(
                root, repository_id=repository_id
            )
        )
        self.methodology = methodology
        self.timeout_seconds = min(max(timeout_seconds, 1), 7200)
        self.store = OrchestrationStore(tasks)
        self.attention = AttentionStore(tasks)
        self.control_plane = ControlPlaneStore(tasks)
        self.projections = TaskProjectionStore(
            tasks,
            self.store,
            self.control_plane,
        )

    def create(
        self,
        *,
        project_id: str,
        title: str,
        description: str,
        total_token_budget: int,
        total_cost_budget_usd: float | None,
        risk: TaskRisk = TaskRisk.MEDIUM,
        actor: str = "user",
        contract: TaskContract | None = None,
    ) -> TaskManifest:
        self.projects.get(project_id)
        self._assert_runtimes_available()
        self.store.validate_plan_inputs(
            self.methodology,
            total_token_budget=total_token_budget,
            total_cost_budget_usd=total_cost_budget_usd,
        )
        if contract:
            self._validate_contract_alignment(contract)
        contract_payload = contract.model_dump(mode="json") if contract else None
        acceptance = (
            contract.acceptance_criteria
            if contract
            else [
                "Codex engineering plan has a valid semantic result",
                "Claude independent review passes",
                "Kiro methodology review passes",
                "A human explicitly approves the reviewed plan",
            ]
        )
        metadata = {
            "methodology": f"{self.methodology.methodology_id}@{self.methodology.version}",
            "methodology_provisional": self.methodology.provisional,
        }
        if contract:
            canonical_contract_json(contract)
            metadata.update({
                "task_contract": contract_payload,
                "task_contract_id": contract.contract_id,
                "task_contract_schema_version": contract.schema_version,
                "task_contract_sha256": contract_sha256(contract),
            })
        task = self.tasks.create(CreateTaskRequest(
            project_id=project_id,
            title=title,
            description=description,
            kind="aidlc_foundation",
            risk=risk,
            primary_agent="agora",
            reviewers=["claude", "kiro"],
            acceptance=acceptance,
            budget=TaskBudget(max_cost_usd=total_cost_budget_usd),
            metadata=metadata,
            created_by=actor,
        ))
        self.store.create_plan(
            task.task_id, self.methodology,
            total_token_budget=total_token_budget,
            total_cost_budget_usd=total_cost_budget_usd,
            actor=actor,
        )
        self.control_plane.ensure_task_state(task.task_id, actor=actor)
        self._ensure_grouped_stage_inventory(task.task_id, actor=actor)
        self._ensure_authoritative_stage_route(task.task_id, actor=actor)
        return task

    def attach(
        self,
        task_id: str,
        *,
        total_token_budget: int,
        total_cost_budget_usd: float | None,
        actor: str = "user",
    ):
        self._assert_runtimes_available()
        plan = self.store.create_plan(
            task_id, self.methodology,
            total_token_budget=total_token_budget,
            total_cost_budget_usd=total_cost_budget_usd,
            actor=actor,
        )
        self.control_plane.ensure_task_state(task_id, actor=actor)
        self._ensure_grouped_stage_inventory(task_id, actor=actor)
        self._ensure_authoritative_stage_route(task_id, actor=actor)
        return plan

    def status(self, task_id: str) -> TaskOrchestrationStatus:
        return self.store.status(task_id)

    def decide(
        self,
        task_id: str,
        *,
        decision_key: str,
        decision_value: str,
        rationale: str,
        actor: str = "user",
    ):
        return self.store.record_decision(
            task_id,
            decision_key=decision_key,
            decision_value=decision_value,
            rationale=rationale,
            actor=actor,
        )

    def amend_budget(
        self,
        task_id: str,
        *,
        amended_total_token_budget: int,
        amended_total_cost_budget_usd: float | None = None,
        expected_task_version: int,
        expected_plan_version: int,
        reason: str,
        actor: str = "user",
        operation_key: str | None = None,
    ) -> BudgetAmendment:
        task = self.tasks.get(task_id)
        if task is None:
            raise OrchestrationConflictError("Task not found")
        plan = self.store.require_plan(task_id)
        effective_cost_budget = (
            plan.total_cost_budget_usd
            if amended_total_cost_budget_usd is None
            else amended_total_cost_budget_usd
        )
        contract_payload = task.metadata.get("task_contract")
        if contract_payload is None:
            contract = None
        else:
            contract = TaskContract.model_validate(contract_payload)
            if (
                task.metadata.get("task_contract_id") != contract.contract_id
                or task.metadata.get("task_contract_schema_version")
                != contract.schema_version
                or task.metadata.get("task_contract_sha256")
                != contract_sha256(contract)
            ):
                raise OrchestrationValidationError(
                    "Pinned Task contract does not match its Task ledger binding"
                )
        key = operation_key or (
            "budget:"
            + canonical_sha256(
                {
                    "task_id": task_id,
                    "expected_task_version": expected_task_version,
                    "expected_plan_version": expected_plan_version,
                    "amended_total_token_budget": amended_total_token_budget,
                    "amended_total_cost_budget_usd": effective_cost_budget,
                }
            )[:32]
        )
        return self.store.amend_budget(
            task_id,
            amended_total_token_budget=amended_total_token_budget,
            amended_total_cost_budget_usd=effective_cost_budget,
            expected_task_version=expected_task_version,
            expected_plan_version=expected_plan_version,
            operation_key=key,
            route=self.control_plane.get_stage_route(task_id),
            contract=contract,
            actor=actor,
            reason=reason,
        )

    async def run_next(
        self,
        task_id: str,
        *,
        protocol_v1: bool = False,
    ) -> OrchestrationRun:
        if protocol_v1:
            return await self.run_next_protocol(task_id)
        task = self.tasks.get(task_id)
        if task is None:
            raise OrchestrationConflictError("Task not found")
        status = self.store.status(task_id)
        if status.plan.state != PlanState.ACTIVE:
            raise OrchestrationConflictError(f"Plan is {status.plan.state.value}, not active")
        stage = next(
            (item for item in status.stages if item.stage_key == status.plan.current_stage_key),
            None,
        )
        if stage is None or stage.state != StageState.PENDING:
            raise OrchestrationConflictError("Current stage is not ready to run")
        runtime = self.runtimes.get(stage.adapter)
        if runtime is None:
            raise OrchestrationConflictError(f"Runtime is unavailable: {stage.adapter}")
        project = self.projects.get(task.project_id)
        prompt = self._build_prompt(task, status, stage.stage_key)
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        operation_key = f"{status.plan.plan_id}:{stage.stage_key}:{stage.attempt_count + 1}"
        run = self.store.claim_current_stage(
            task_id, prompt_sha256=digest, operation_key=operation_key,
        )

        async def attach_pid(pid: int) -> None:
            self.store.attach_pid(run.run_id, pid)

        try:
            result = await self.runner.run(
                runtime, prompt, cwd=project.root, task_id=task_id,
                run_id=run.run_id, stage_key=stage.stage_key,
                timeout_seconds=self.timeout_seconds, on_process=attach_pid,
            )
        except RuntimeInterrupted as exc:
            return self.store.mark_interrupted(run.run_id, reason=str(exc))
        except asyncio.CancelledError:  # pragma: no cover - defensive outer cancellation boundary
            self.store.mark_interrupted(run.run_id, reason="Orchestration task was cancelled")
            raise
        except Exception as exc:
            result = None
            failure = f"runtime boundary failed: {type(exc).__name__}: {exc}"
        else:
            failure = (
                f"timeout after {self.timeout_seconds}s" if result.timed_out
                else (result.stderr.strip() or None if result.exit_code != 0 else None)
            )
        if result is None:
            output = ""
            exit_code = None
            semantic = None
        else:
            output = result.stdout
            exit_code = result.exit_code
            semantic = self._parse_semantic(output) if exit_code == 0 else None
        if result is None:
            token_used = None
            token_measurement = Measurement.UNAVAILABLE
        elif not result.process_started:
            token_used = 0
            token_measurement = Measurement.EXACT
        else:
            token_used = self._estimate_tokens(prompt, output)
            token_measurement = Measurement.ESTIMATED
        if result is not None and not result.process_started:
            cost_used_usd = 0.0
            cost_measurement = Measurement.EXACT
        else:
            cost_used_usd = None
            cost_measurement = Measurement.UNAVAILABLE
        return self.store.finish_run(
            run.run_id, exit_code=exit_code,
            timed_out=bool(result and result.timed_out), output=output,
            error_message=failure,
            semantic=semantic,
            token_used=token_used,
            token_measurement=token_measurement,
            cost_used_usd=cost_used_usd,
            cost_measurement=cost_measurement,
        )

    async def run_next_protocol(self, task_id: str) -> OrchestrationRun:
        """Dispatch one explicit Context/Handoff v1 Run through the formal Gate."""

        task = self.tasks.get(task_id)
        if task is None:
            raise OrchestrationConflictError("Task not found")
        contract_payload = task.metadata.get("task_contract")
        if contract_payload is None:
            raise OrchestrationValidationError(
                "Formal protocol orchestration requires a pinned concrete Task contract"
            )
        contract = TaskContract.model_validate(contract_payload)
        if task.metadata.get("task_contract_sha256") != contract_sha256(contract):
            raise OrchestrationValidationError(
                "Pinned Task contract hash does not match its content"
            )
        self._ensure_grouped_stage_inventory(task_id, actor="orchestrator")
        route = self._ensure_authoritative_stage_route(
            task_id,
            actor="orchestrator",
        )
        if route is None:
            raise OrchestrationConflictError(
                "Every authoritative inventory Stage already completed"
            )
        status = self.store.status(task_id)
        if status.plan.state != PlanState.ACTIVE:
            raise OrchestrationConflictError(f"Plan is {status.plan.state.value}, not active")
        if status.plan.current_stage_key != route.stage_key:
            raise OrchestrationConflictError(
                "Compatibility Plan route does not match the authoritative Control "
                "Plane route; run task resume"
            )
        stage = next(
            (item for item in status.stages if item.stage_key == route.stage_key),
            None,
        )
        if stage is None or stage.state != StageState.PENDING:
            raise OrchestrationConflictError("Current stage is not ready to run")
        if (
            stage.adapter != route.runtime
            or stage.role != route.role
            or stage.title != route.title
        ):
            raise OrchestrationConflictError(
                "Compatibility Stage metadata does not match the authoritative route"
            )
        if route.stage_status != StageStatus.READY:
            status_value = route.stage_status.value if route.stage_status else "unconfigured"
            raise OrchestrationConflictError(
                f"Authoritative routed Stage is {status_value}, not ready"
            )
        frozen_task = self.control_plane.get_task_state(task_id)
        lifecycle = self.control_plane.get_task_lifecycle_decision(task_id)
        if frozen_task is None or lifecycle is None:
            raise OrchestrationConflictError(
                "Frozen Task lifecycle is unavailable; run task resume"
            )
        if frozen_task.status != lifecycle.target_status:
            raise OrchestrationConflictError(
                "Frozen Task lifecycle drifted from authoritative facts; run task resume"
            )
        if lifecycle.target_status not in {TaskStatus.READY, TaskStatus.ACTIVE}:
            raise OrchestrationConflictError(
                f"Frozen Task lifecycle is {lifecycle.target_status.value}, not dispatchable"
            )
        if not route.runnable:
            raise OrchestrationConflictError(
                "Authoritative Stage route is not dispatchable; run task resume"
            )
        runtime = self.runtimes.get(route.runtime)
        if runtime is None:
            raise OrchestrationConflictError(f"Runtime is unavailable: {route.runtime}")
        project = self.projects.get(task.project_id)
        revision = self.revision_resolver(project.root, task.project_id)
        projection = self.control_plane.projection(task_id)
        prior = projection["artifacts"]
        if projection["collection_totals"]["artifacts"] != len(prior):
            raise OrchestrationConflictError(
                "Formal Artifact history exceeds the bounded Context projection"
            )
        run_id = self.store.new_run_id()
        routing_policy = self.store.preview_routing_policy(
            task_id,
            route=route,
            contract=contract,
            run_id=run_id,
        )
        if not routing_policy.dispatchable:
            raise OrchestrationConflictError(routing_policy.blockers[0])
        definition = build_protocol_run_definition(
            task=task,
            contract=contract,
            stage=stage,
            run_id=run_id,
            revision=revision,
            prior_artifacts=prior,
            decisions=self.store.latest_decisions(status.plan.plan_id),
            routing_policy=routing_policy,
            generated_at=utc_now(),
            timeout_seconds=self.timeout_seconds,
            max_output_bytes=OUTPUT_LIMIT,
        )
        digest = hashlib.sha256(definition.prompt.encode("utf-8")).hexdigest()
        operation_key = (
            f"{status.plan.plan_id}:{stage.stage_key}:protocol:{stage.attempt_count + 1}"
        )
        run = self.store.claim_current_stage(
            task_id,
            prompt_sha256=digest,
            operation_key=operation_key,
            run_id=run_id,
            expected_stage_key=route.stage_key,
            expected_adapter=route.runtime,
            route=route,
            contract=contract,
            routing_policy=routing_policy,
        )
        try:
            self.control_plane.configure_gate(
                task_id=task_id,
                gate_key=definition.gate_key,
                stage_key=stage.stage_key,
                requirements=definition.gate_requirements,
                actor="orchestrator",
            )
            self.control_plane.start_protocol_run(
                definition.context_pack,
                gate_key=definition.gate_key,
                actor="orchestrator",
                operation_key=f"protocol-start:{run_id}",
            )
        except Exception as exc:
            if self.control_plane.get_protocol_run(run_id) is not None:
                pass
            else:
                known = isinstance(
                    exc,
                    (
                        ControlPlaneConflictError,
                        ControlPlaneNotFoundError,
                        ControlPlaneValidationError,
                    ),
                )
                detail = str(exc) if known else type(exc).__name__
                self.store.finish_run(
                    run_id,
                    exit_code=None,
                    timed_out=False,
                    output="",
                    error_message=f"formal protocol start failed: {detail}",
                    semantic=None,
                    token_used=0,
                    token_measurement=Measurement.EXACT,
                    cost_used_usd=0.0,
                    cost_measurement=Measurement.EXACT,
                )
                raise OrchestrationConflictError(
                    f"Formal protocol Run could not start: {detail}"
                ) from exc

        async def attach_pid(pid: int) -> None:
            self.store.attach_pid(run_id, pid)

        try:
            result = await self.runner.run(
                runtime,
                definition.prompt,
                cwd=project.root,
                task_id=task_id,
                run_id=run_id,
                stage_key=stage.stage_key,
                timeout_seconds=self.timeout_seconds,
                on_process=attach_pid,
            )
        except RuntimeInterrupted as exc:
            result = RuntimeResult(
                exit_code=None,
                stdout="",
                stderr=str(exc),
                process_started=True,
            )
        except asyncio.CancelledError:  # pragma: no cover - defensive outer boundary
            result = RuntimeResult(
                exit_code=None,
                stdout="",
                stderr="Orchestration task was cancelled",
                process_started=True,
            )
            self._settle_protocol_result(run, definition, result, cancelled=True)
            raise
        except Exception as exc:
            result = RuntimeResult(
                exit_code=None,
                stdout="",
                stderr=f"runtime boundary failed: {type(exc).__name__}: {exc}",
                process_started=False,
            )
        return self._settle_protocol_result(run, definition, result)

    def _settle_protocol_result(
        self,
        run: OrchestrationRun,
        definition: ProtocolRunDefinition,
        result: RuntimeResult,
        *,
        cancelled: bool = False,
    ) -> OrchestrationRun:
        adapted = adapt_runtime_result(
            definition.context_pack,
            result,
            gate_requirements=definition.gate_requirements,
            cancelled=cancelled,
        )
        receipt = self.control_plane.settle_protocol_run(
            adapted,
            actor="orchestrator",
            operation_key=f"protocol-settle:{run.run_id}",
        )
        failure = (
            f"timeout after {self.timeout_seconds}s"
            if result.timed_out
            else (result.stderr.strip() or None if result.exit_code != 0 else None)
        )
        if not result.process_started:
            token_used = 0
            token_measurement = Measurement.EXACT
        elif result.exit_code is None:
            token_used = None
            token_measurement = Measurement.UNAVAILABLE
        else:
            token_used = self._estimate_tokens(definition.prompt, result.stdout)
            token_measurement = Measurement.ESTIMATED
        return self.store.finish_protocol_run(
            run.run_id,
            receipt=receipt,
            adapter_result=adapted,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            output=result.stdout,
            error_message=failure,
            token_used=token_used,
            token_measurement=token_measurement,
        )

    async def run_until_blocked(
        self,
        task_id: str,
        *,
        protocol_v1: bool = False,
    ) -> TaskOrchestrationStatus:
        while True:
            status = self.store.status(task_id)
            if status.plan.state != PlanState.ACTIVE:
                return status
            await self.run_next(task_id, protocol_v1=protocol_v1)

    def resume(self, task_id: str) -> TaskOrchestrationStatus:
        self.control_plane.ensure_task_state(task_id, actor="reconciler")
        self._ensure_grouped_stage_inventory(task_id, actor="reconciler")
        status = self.store.status(task_id)
        running = [run for run in status.runs if run.state == RunState.RUNNING]
        for run in running:
            protocol_run = self.control_plane.get_protocol_run(run.run_id)
            if protocol_run is not None:
                self._resume_protocol_run(run, protocol_run)
                continue
            process_state = (
                self.process_inspector(run.pid) if run.pid else ProcessState.UNKNOWN
            )
            if process_state != ProcessState.DEAD:
                raise OrchestrationConflictError(
                    f"Run {run.run_id} process {run.pid} is {process_state.value}; "
                    "refusing duplicate dispatch"
                )
            self.store.mark_interrupted(
                run.run_id,
                reason="Recovered a run whose process was no longer active",
            )
        self._ensure_authoritative_stage_route(task_id, actor="reconciler")
        self.control_plane.reconcile_task_lifecycle(
            task_id,
            cause=TaskTransitionCause.RECONCILIATION,
            actor="reconciler",
        )
        return self.store.status(task_id)

    def _resume_protocol_run(
        self,
        run: OrchestrationRun,
        protocol_run: ProtocolRunRecord,
    ) -> None:
        stage = self.control_plane.get_stage(run.task_id, run.stage_key)
        gate = self.control_plane.get_gate(run.task_id, protocol_run.gate_key)
        if stage is None or gate is None:
            raise OrchestrationConflictError(
                "Formal protocol Run is missing its authoritative Stage or Gate"
            )
        if protocol_run.protocol_state is None:
            if run.pid is None:
                result = RuntimeResult(
                    exit_code=None,
                    stdout="",
                    stderr="Recovered a protocol Run whose process never attached",
                    process_started=False,
                )
                token_used = 0
                token_measurement = Measurement.EXACT
            else:
                process_state = self.process_inspector(run.pid)
                if process_state != ProcessState.DEAD:
                    raise OrchestrationConflictError(
                        f"Run {run.run_id} process {run.pid} is {process_state.value}; "
                        "refusing duplicate dispatch"
                    )
                result = RuntimeResult(
                    exit_code=None,
                    stdout="",
                    stderr="Recovered a protocol Run whose process was no longer active",
                    process_started=True,
                )
                token_used = None
                token_measurement = Measurement.UNAVAILABLE
            adapted = adapt_runtime_result(
                protocol_run.context_pack,
                result,
                gate_requirements=gate.requirements,
            )
            receipt = self.control_plane.settle_protocol_run(
                adapted,
                actor="orchestrator",
                operation_key=f"protocol-settle:{run.run_id}",
            )
            self.store.finish_protocol_run(
                run.run_id,
                receipt=receipt,
                adapter_result=adapted,
                exit_code=result.exit_code,
                timed_out=result.timed_out,
                output=result.stdout,
                error_message=result.stderr,
                token_used=token_used,
                token_measurement=token_measurement,
            )
            return

        adapted = AgentAdapterResult(
            protocol_state=protocol_run.protocol_state,
            handoff_pack=protocol_run.handoff_pack,
            error_code=protocol_run.adapter_error_code,
            attention_required=protocol_run.attention_required,
        )
        receipt = RunSettlementReceipt(
            run=protocol_run,
            stage=stage,
            gate=gate,
            artifact_ids=sorted(
                item.artifact_id
                for item in (
                    protocol_run.handoff_pack.output_artifacts
                    if protocol_run.handoff_pack
                    else []
                )
            ),
            evidence_ids=sorted(
                item.evidence_id
                for item in (
                    protocol_run.handoff_pack.evidence
                    if protocol_run.handoff_pack
                    else []
                )
            ),
            active_evidence_ids=gate.active_evidence_ids,
            next_stage_route=(
                self.control_plane.get_stage_route(run.task_id)
                if stage.status == StageStatus.COMPLETED
                else None
            ),
            replayed=True,
        )
        output = (
            canonical_json_bytes(protocol_run.handoff_pack).decode("utf-8")
            if protocol_run.handoff_pack
            else ""
        )
        process_status = protocol_run.protocol_state.process_status.value
        self.store.finish_protocol_run(
            run.run_id,
            receipt=receipt,
            adapter_result=adapted,
            exit_code=protocol_run.protocol_state.process_exit_code,
            timed_out=process_status == "timed_out",
            output=output,
            error_message=(
                f"Recovered formal protocol result: {protocol_run.adapter_error_code.value}"
                if protocol_run.adapter_error_code
                else None
            ),
            token_used=(0 if process_status == "launch_failed" else None),
            token_measurement=(
                Measurement.EXACT
                if process_status == "launch_failed"
                else Measurement.UNAVAILABLE
            ),
        )

    def retry(self, task_id: str, stage_key: str):
        return self.store.retry(task_id, stage_key)

    def unified_status(
        self,
        task_id: str,
        *,
        history_limit: int = 100,
        history_offset: int = 0,
    ) -> UnifiedTaskProjection:
        return self.projections.get(
            task_id,
            history_limit=history_limit,
            history_offset=history_offset,
        )

    def retry_protocol(self, task_id: str, stage_key: str, *, actor: str = "user"):
        status = self.store.status(task_id)
        stage = next((item for item in status.stages if item.stage_key == stage_key), None)
        if stage is None:
            raise OrchestrationConflictError(f"Stage not found: {stage_key}")
        if (
            status.plan.state != PlanState.BLOCKED
            or status.plan.current_stage_key != stage_key
            or stage.state != StageState.BLOCKED
        ):
            raise OrchestrationConflictError(
                "Formal retry requires the current blocked operational Stage"
            )
        control_stage = self.control_plane.get_stage(task_id, stage_key)
        if control_stage is None:
            raise OrchestrationConflictError(
                f"Formal Control Plane Stage not found: {stage_key}"
            )
        gate = self.control_plane.get_gate(task_id, control_stage.gate_key)
        if gate is None:
            raise OrchestrationConflictError(
                f"Formal Control Plane Gate not found: {control_stage.gate_key}"
            )
        task = self.tasks.get(task_id)
        if task is None:
            raise OrchestrationConflictError("Task not found")
        project = self.projects.get(task.project_id)
        revision = self.revision_resolver(project.root, task.project_id)
        configured_scopes = {
            (item.repository_id, item.ref, item.commit_sha)
            for item in gate.requirements
        }
        current_scope = {
            (revision.repository_id, revision.ref, revision.commit_sha)
        }
        if configured_scopes != current_scope:
            raise OrchestrationConflictError(
                "Formal retry cannot rebind an immutable Gate after the repository "
                "ref or commit changed; start a new Task for the new revision"
            )
        if stage.latest_run_id is not None:
            protocol_run = self.control_plane.get_protocol_run(stage.latest_run_id)
            if protocol_run is not None and protocol_run.attention_item_id is not None:
                item = self.attention.get(protocol_run.attention_item_id)
                if item is not None and item.state == AttentionState.OPEN:
                    try:
                        self.attention.cancel(
                            item.item_id,
                            CancelAttentionRequest(
                                actor=actor,
                                reason="Superseded by explicit protocol retry",
                                expected_version=item.version,
                            ),
                        )
                    except AttentionConflictError as exc:
                        current = self.attention.get(item.item_id)
                        if current is not None and current.state == AttentionState.OPEN:
                            raise OrchestrationConflictError(
                                "Protocol Attention changed while preparing retry"
                            ) from exc
        if control_stage.status != StageStatus.READY:
            self.control_plane.prepare_protocol_retry(
                task_id=task_id,
                stage_key=stage_key,
                actor=actor,
                operation_key=(
                    f"protocol-retry:{task_id}:{stage_key}:{stage.attempt_count}"
                ),
            )
        return self.store.retry(task_id, stage_key, actor=actor)

    def approve(self, task_id: str, *, actor: str, reason: str):
        plan = self.store.require_plan(task_id)
        if plan.state not in {
            PlanState.AWAITING_APPROVAL,
            PlanState.READY_FOR_IMPLEMENTATION,
        }:
            raise OrchestrationConflictError("Plan is not awaiting human approval")
        task_state = self.control_plane.get_task_state(task_id)
        if task_state is not None and task_state.status == TaskStatus.NEEDS_REVIEW:
            self.control_plane.transition_task_state(
                task_id,
                TaskStatus.COMPLETED,
                expected_version=task_state.version,
                cause=TaskTransitionCause.USER_ACTION,
                actor=actor,
                reason="User explicitly approved the reviewed Task",
                operation_key=f"task-approve:{task_id}:{task_state.version}",
            )
        return self.store.approve(task_id, actor=actor, reason=reason)

    def _build_prompt(self, task: TaskManifest, status: TaskOrchestrationStatus, stage_key: str) -> str:
        definition = next(item for item in self.methodology.stages if item.stage_key == stage_key)
        passed_runs = [run for run in status.runs if run.state == RunState.PASSED]
        prior_results = []
        multiple_priors = len(passed_runs) > 1
        for run in passed_runs:
            prior_results.append({
                "stage_key": run.stage_key,
                "adapter": run.adapter,
                "summary": self._truncate(
                    run.semantic_summary or "", 400 if multiple_priors else 800,
                ),
                "findings": [
                    self._truncate(item, 250 if multiple_priors else 400)
                    for item in run.findings[: 5 if multiple_priors else 10]
                ],
                "output_excerpt": self._truncate(
                    run.output[-(1_000 if multiple_priors else 1_500):],
                    1_000 if multiple_priors else 1_500,
                ),
            })
        context = json.dumps(prior_results, ensure_ascii=False, separators=(",", ":"))
        if len(context) > PRIOR_RESULTS_CONTEXT_LIMIT:
            raise OrchestrationConflictError(
                "Verified prior-stage context exceeds the bounded prompt allocation"
            )
        decisions = self.store.latest_decisions(status.plan.plan_id)
        decision_context = json.dumps(
            [
                {
                    "decision_key": item.decision_key,
                    "decision_value": item.decision_value,
                    "rationale": item.rationale,
                    "version": item.version,
                    "actor": item.actor,
                }
                for item in decisions
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        contract_payload = task.metadata.get("task_contract")
        if contract_payload is None:
            contract_context = "(no concrete Task contract supplied)"
            task_description = task.description or "(none)"
            acceptance_context = json.dumps(task.acceptance, ensure_ascii=False)
        else:
            contract = TaskContract.model_validate(contract_payload)
            contract_context = self._stage_contract_context(contract, stage_key)
            task_description = "(defined by the concrete Task contract below)"
            acceptance_context = "(defined by the concrete Task contract below)"
        prompt = f"""You are the {definition.role} in an Agora task orchestration run.

This is a READ-ONLY planning and review stage. Do not modify files, create commits,
change native AI-DLC state, or claim that the product has been delivered.

Task ID: {task.task_id}
Project ID: {task.project_id}
Task title: {task.title}
Task description: {task_description}
Acceptance expectations: {acceptance_context}
Concrete Task contract (versioned, hash-bound Stage projection):
{contract_context}
Explicit human Task decisions (latest version per key):
{decision_context}
Methodology: {self.methodology.methodology_id}@{self.methodology.version} (provisional)
Stage: {definition.stage_key}
Objective: {definition.objective}
Stage token envelope: {next(s.token_budget for s in status.stages if s.stage_key == stage_key)} tokens

Verified prior stage results (not a full transcript):
{context or '[]'}

Return ONLY one JSON object with exactly these fields:
{{
  "status": "pass" | "needs_work" | "blocked",
  "summary": "concise result",
  "findings": ["specific finding"],
  "recommended_next_action": "one safe next action"
}}

Use status=pass only when this stage objective is satisfied. Unknowns, missing evidence,
or unreviewable assumptions must be explicit. Process success alone is not semantic success.
"""
        if len(prompt) > 16_000:
            raise OrchestrationConflictError("Context for the next stage exceeds the bounded prompt size")
        return prompt

    @staticmethod
    def _parse_semantic(output: str) -> SemanticResult | None:
        value = output.strip()
        if value.startswith("```"):
            lines = value.splitlines()
            if len(lines) >= 3 and lines[-1].strip() == "```":
                value = "\n".join(lines[1:-1])
                if value.lstrip().startswith("json"):
                    value = value.lstrip()[4:].lstrip()
        candidate_starts = [match.start() for match in re.finditer(r'\{\s*"', value)]
        if len(candidate_starts) > 100:
            return None
        valid_results: list[SemanticResult] = []
        decoder = json.JSONDecoder()
        for start in candidate_starts:
            try:
                candidate, _ = decoder.raw_decode(value[start:])
                result = SemanticResult.model_validate(candidate)
            except (json.JSONDecodeError, ValidationError):
                continue
            valid_results.append(result)
        return valid_results[0] if len(valid_results) == 1 else None

    @staticmethod
    def _estimate_tokens(prompt: str, output: str) -> int:
        return max(1, math.ceil(len((prompt + output).encode("utf-8")) / 4))

    def _assert_runtimes_available(self) -> None:
        missing = [stage.adapter for stage in self.methodology.stages if stage.adapter not in self.runtimes]
        if missing:
            raise OrchestrationConflictError(f"Required runtimes are unavailable: {sorted(set(missing))}")

    def _validate_contract_alignment(
        self,
        contract: TaskContract,
        methodology: MethodologyDefinition | None = None,
    ) -> None:
        methodology = methodology or self.methodology
        definitions = {stage.stage_key: stage for stage in methodology.stages}
        supplied_keys = [stage.stage_key for stage in contract.workflow]
        expected_keys = [stage.stage_key for stage in methodology.stages]
        if supplied_keys != expected_keys:
            raise OrchestrationValidationError(
                "Task contract workflow must match the pinned methodology stage order"
            )
        roles = {role.role_id: role for role in contract.roles}
        for stage in contract.workflow:
            if stage.role_id != definitions[stage.stage_key].role:
                raise OrchestrationValidationError(
                    f"Task contract stage {stage.stage_key} role does not match "
                    "the pinned methodology"
                )
            if roles[stage.role_id].runtime != definitions[stage.stage_key].adapter:
                raise OrchestrationValidationError(
                    f"Task contract stage {stage.stage_key} runtime does not match "
                    "the pinned methodology"
                )

    def _ensure_grouped_stage_inventory(
        self,
        task_id: str,
        *,
        actor: str,
    ) -> StageInventory:
        task = self.tasks.get(task_id)
        if task is None:
            raise OrchestrationConflictError("Task not found")
        plan = self.store.require_plan(task_id)
        methodology = self.store.methodology(plan.plan_id)
        digest = methodology_sha256(methodology)
        if (
            plan.task_id != task.task_id
            or plan.project_id != task.project_id
            or plan.methodology_id != methodology.methodology_id
            or plan.methodology_version != methodology.version
            or plan.methodology_sha256 != digest
            or plan.provisional != methodology.provisional
        ):
            raise OrchestrationValidationError(
                "Pinned methodology does not match its Plan ledger binding"
            )

        contract_binding = None
        contract_payload = task.metadata.get("task_contract")
        if contract_payload is not None:
            contract = TaskContract.model_validate(contract_payload)
            contract_digest = contract_sha256(contract)
            if (
                task.metadata.get("task_contract_id") != contract.contract_id
                or task.metadata.get("task_contract_schema_version")
                != contract.schema_version
                or task.metadata.get("task_contract_sha256") != contract_digest
            ):
                raise OrchestrationValidationError(
                    "Pinned Task contract does not match its Task ledger binding"
                )
            self._validate_contract_alignment(contract, methodology)
            contract_binding = {
                "contract_id": contract.contract_id,
                "schema_version": contract.schema_version,
                "sha256": contract_digest,
            }

        payload = {
            "schema_version": "1.0",
            "inventory_id": f"inventory:{plan.plan_id}",
            "task_id": task.task_id,
            "project_id": task.project_id,
            "plan_id": plan.plan_id,
            "methodology_id": methodology.methodology_id,
            "methodology_version": methodology.version,
            "methodology_sha256": digest,
            "provisional": methodology.provisional,
            "contract": contract_binding,
            "groups": [
                {
                    "group_key": plan.plan_id,
                    "sequence": 1,
                    "title": (
                        f"{methodology.methodology_id}@{methodology.version} "
                        "pinned workflow"
                    ),
                    "stages": [
                        {
                            "stage_key": stage.stage_key,
                            "gate_key": f"gate:{stage.stage_key}",
                            "sequence": sequence,
                            "title": stage.title,
                            "role": stage.role,
                            "runtime": stage.adapter,
                        }
                        for sequence, stage in enumerate(methodology.stages, start=1)
                    ],
                }
            ],
        }
        inventory = StageInventory.model_validate(
            seal_model_payload(StageInventory, payload)
        )
        return self.control_plane.ensure_stage_inventory(inventory, actor=actor)

    def _ensure_authoritative_stage_route(
        self,
        task_id: str,
        *,
        actor: str,
    ) -> StageRouteDecision | None:
        route = self.control_plane.get_stage_route(task_id)
        if route is None:
            return None
        if route.stage_status not in {None, StageStatus.PENDING}:
            return route
        operation_key = "stage-activate:" + canonical_sha256(
            {
                "task_id": task_id,
                "inventory_sha256": route.inventory_sha256,
                "stage_key": route.stage_key,
            }
        )
        return self.control_plane.activate_stage_route(
            task_id=task_id,
            expected_stage_key=route.stage_key,
            actor=actor,
            operation_key=operation_key,
        ).route

    @staticmethod
    def _stage_contract_context(contract: TaskContract, stage_key: str) -> str:
        stage = next(item for item in contract.workflow if item.stage_key == stage_key)
        role = next(item for item in contract.roles if item.role_id == stage.role_id)
        payload = {
            "schema_version": contract.schema_version,
            "contract_id": contract.contract_id,
            "contract_sha256": contract_sha256(contract),
            "title": contract.title,
            "goal": contract.goal,
            "role": role.model_dump(mode="json"),
            "stage": stage.model_dump(mode="json"),
            "acceptance_criteria": contract.acceptance_criteria,
            "forbidden_constraints": contract.forbidden_constraints,
        }
        value = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(value) > STAGE_CONTRACT_CONTEXT_LIMIT:
            raise OrchestrationConflictError(
                "Stage-scoped Task contract exceeds the bounded prompt allocation"
            )
        return value

    @staticmethod
    def _truncate(value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return value[: limit - 1] + "…"
