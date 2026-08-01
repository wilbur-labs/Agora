"""Application service for one task-scoped, three-runtime planning loop."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from agora.attention.models import AttentionState, CancelAttentionRequest
from agora.attention.store import AttentionConflictError, AttentionStore
from agora.control_plane.auth import ControlPrincipal
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
from agora.protocol.models import (
    ConsultationCandidate,
    ConsultationCandidateDisposition,
    NativeRuntimeCapabilityObservation,
    StageInventory,
)
from agora.protocol.methodology_migration import (
    MethodologyMigrationActivationReceipt,
    MethodologyMigrationPreviewDecision,
    MethodologyMigrationPreviewRequest,
)
from agora.protocol.methodology_execution import MethodologyExecutionContract
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
    MethodologyRunDispatchReceipt,
)
from agora.protocol.methodology_stage_gate import (
    MethodologyStageGateReceipt,
    MethodologyStageGateRequest,
)
from agora.protocol.methodology_stage_run_claim import (
    MethodologyStageRunClaimReceipt,
    MethodologyStageRunClaimRequest,
)
from agora.protocol.methodology_stage_run_dispatch import (
    MethodologyStageRunDispatchClaim,
    MethodologyStageRunDispatchReceipt,
)
from agora.protocol.state_machines import StageStatus, TaskStatus
from agora.tasks.models import CreateTaskRequest, TaskBudget, TaskManifest, TaskRisk, utc_now
from agora.tasks.store import TaskStore

from .contracts import TaskContract, canonical_contract_json, contract_sha256
from .consultation import adapt_consultation_output
from .methodology import (
    FOUNDATION_METHODOLOGY,
    MethodologyDefinition,
    methodology_sha256,
)
from .methodology_migration import (
    derive_methodology_migration_preview,
    observe_migration_artifacts,
)
from .methodology_migration_activation import (
    build_methodology_successor_materialization,
)
from .methodology_execution_contract import (
    build_methodology_execution_contract,
)
from .methodology_route_activation import (
    validate_methodology_route_activation,
)
from .methodology_run_claim import build_methodology_run_claim_context
from .methodology_run_dispatch import (
    build_methodology_run_dispatch_claim,
    derive_methodology_run_dispatch_policy,
    derive_methodology_runtime_preflight,
)
from .methodology_stage_gate import validate_methodology_stage_gate
from .methodology_stage_run_claim import (
    build_methodology_stage_run_claim_context,
)
from .methodology_stage_run_dispatch import (
    build_methodology_stage_run_dispatch_claim,
    derive_methodology_stage_run_dispatch_policy,
    derive_methodology_stage_runtime_preflight,
)
from .models import (
    BudgetAmendment,
    ConsultationRun,
    ConsultationState,
    Measurement,
    MethodologyDispatchState,
    MethodologyRunDispatchState,
    MethodologyStageRunDispatchState,
    OrchestrationRun,
    OrchestrationStage,
    PlanState,
    RunState,
    RuntimePreflightPreview,
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
    build_protocol_prompt,
    build_protocol_run_definition,
    resolve_git_revision,
)
from .projection import TaskProjectionStore
from .provider_usage import RuntimeResultFormat, settlement_observation
from .runtime import (
    OUTPUT_LIMIT,
    ReadOnlyCliRunner,
    RuntimeCommand,
    RuntimeInterrupted,
    RuntimeResult,
    resolve_runtime_command,
)
from .runtime_capabilities import collect_native_runtime_capabilities
from .runtime_preflight import (
    derive_pinned_runtime_preflight,
    recheck_pinned_runtime_preflight,
    runtime_preflight_remediation,
)
from .store import (
    OrchestrationConflictError,
    OrchestrationStore,
    OrchestrationValidationError,
)


PRIOR_RESULTS_CONTEXT_LIMIT = 7_000
STAGE_CONTRACT_CONTEXT_LIMIT = 6_000
CONSULTATION_PROMPT_LIMIT = 16_000


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
        capability_collector: Callable[
            [dict[str, RuntimeCommand]],
            Awaitable[NativeRuntimeCapabilityObservation],
        ] = collect_native_runtime_capabilities,
        preflight_rechecker: Callable[..., None] = recheck_pinned_runtime_preflight,
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
        self.capability_collector = capability_collector
        self.preflight_rechecker = preflight_rechecker
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

    def register_consultation_candidate(
        self,
        task_id: str,
        *,
        consultation_id: str,
        runtime: str,
        title: str,
        decision_key: str,
        decision_value: str,
        analysis: str,
        source_refs: list[str] | None = None,
        expected_plan_version: int,
        operation_key: str | None = None,
        actor: str = "agora",
    ) -> ConsultationCandidate:
        return self.store.register_consultation_candidate(
            task_id,
            consultation_id=consultation_id,
            runtime=runtime,
            title=title,
            decision_key=decision_key,
            decision_value=decision_value,
            analysis=analysis,
            source_refs=source_refs or [],
            expected_plan_version=expected_plan_version,
            operation_key=operation_key,
            actor=actor,
        )

    def adopt_candidate(
        self,
        task_id: str,
        candidate_id: str,
        *,
        expected_plan_version: int,
        reason: str,
        actor: str = "user",
        operation_key: str | None = None,
    ) -> ConsultationCandidateDisposition:
        return self.store.dispose_consultation_candidate(
            task_id,
            candidate_id,
            action="adopted",
            expected_plan_version=expected_plan_version,
            reason=reason,
            actor=actor,
            operation_key=operation_key,
        )

    def reject_candidate(
        self,
        task_id: str,
        candidate_id: str,
        *,
        expected_plan_version: int,
        reason: str,
        actor: str = "user",
        operation_key: str | None = None,
    ) -> ConsultationCandidateDisposition:
        return self.store.dispose_consultation_candidate(
            task_id,
            candidate_id,
            action="rejected",
            expected_plan_version=expected_plan_version,
            reason=reason,
            actor=actor,
            operation_key=operation_key,
        )

    async def consult(
        self,
        task_id: str,
        *,
        decision_key: str,
        question: str,
        token_reserved: int,
        cost_reserved_usd: float | None,
        operation_key: str | None = None,
    ) -> ConsultationRun:
        """Run one pinned native advisor without claiming or advancing a Stage."""

        safe_question = question.strip()
        if not safe_question or len(safe_question) > 2_000:
            raise OrchestrationValidationError(
                "Consultation question must contain 1 to 2000 characters"
            )
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,127}", decision_key):
            raise OrchestrationValidationError("Invalid consultation decision key")
        task = self.tasks.get(task_id)
        if task is None:
            raise OrchestrationConflictError("Task not found")
        status = self.store.status(task_id)
        if status.plan.state not in {PlanState.ACTIVE, PlanState.BLOCKED}:
            raise OrchestrationConflictError(
                "Consultation requires an active or blocked Plan"
            )
        route = self.control_plane.get_stage_route(task_id)
        if route is None:
            raise OrchestrationConflictError(
                "Authoritative Stage route is unavailable for consultation"
            )
        stage = next(
            (
                item
                for item in status.stages
                if item.stage_key == status.plan.current_stage_key
            ),
            None,
        )
        if (
            stage is None
            or stage.stage_key != route.stage_key
            or stage.role != route.role
            or stage.adapter != route.runtime
        ):
            raise OrchestrationConflictError(
                "Compatibility Stage does not match the authoritative "
                "consultation route"
            )
        runtime = self.runtimes.get(route.runtime)
        if runtime is None:
            raise OrchestrationConflictError(
                f"Runtime is unavailable: {route.runtime}"
            )
        project = self.projects.get(task.project_id)
        try:
            revision = self.revision_resolver(project.root, task.project_id)
        except (TypeError, ValueError) as exc:
            raise OrchestrationConflictError(
                "Consultation requires a clean, readable repository revision"
            ) from exc
        prompt = self._build_consultation_prompt(
            task,
            status,
            route,
            revision,
            decision_key=decision_key,
            question=safe_question,
            token_reserved=token_reserved,
        )
        prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        key = operation_key or "consult:" + canonical_sha256(
            {
                "task_id": task_id,
                "plan_id": status.plan.plan_id,
                "plan_version": status.plan.version,
                "inventory_sha256": route.inventory_sha256,
                "stage_key": route.stage_key,
                "runtime": route.runtime,
                "repository_id": revision.repository_id,
                "repository_ref": revision.ref,
                "repository_commit": revision.commit_sha,
                "decision_key": decision_key,
                "prompt_sha256": prompt_sha256,
                "token_reserved": token_reserved,
                "cost_reserved_usd": cost_reserved_usd,
            }
        )
        consultation, replayed = self.store.claim_consultation(
            task_id,
            route=route,
            repository_id=revision.repository_id,
            repository_ref=revision.ref,
            repository_commit=revision.commit_sha,
            expected_plan_version=status.plan.version,
            decision_key=decision_key,
            prompt_sha256=prompt_sha256,
            token_reserved=token_reserved,
            cost_reserved_usd=cost_reserved_usd,
            operation_key=key,
        )
        if replayed:
            if consultation.state == ConsultationState.RUNNING:
                raise OrchestrationConflictError(
                    "Consultation operation is already running; use task resume"
                )
            return consultation

        process_started = False

        async def attach_pid(pid: int) -> None:
            nonlocal process_started
            process_started = True
            self.store.attach_consultation_pid(
                consultation.consultation_id,
                pid,
            )

        try:
            result = await self.runner.run(
                runtime,
                prompt,
                cwd=project.root,
                task_id=task_id,
                run_id=consultation.consultation_id,
                stage_key=route.stage_key,
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
        except asyncio.CancelledError:  # pragma: no cover - defensive boundary
            result = RuntimeResult(
                exit_code=None,
                stdout="",
                stderr="Consultation task was cancelled",
                process_started=process_started,
            )
            self._settle_consultation(
                consultation,
                runtime,
                prompt,
                result,
            )
            raise
        except Exception as exc:
            result = RuntimeResult(
                exit_code=None,
                stdout="",
                stderr=f"runtime boundary failed: {type(exc).__name__}: {exc}",
                process_started=process_started,
            )

        if result.process_started or process_started:
            try:
                settled_revision = self.revision_resolver(
                    project.root,
                    task.project_id,
                )
            except (TypeError, ValueError):
                revision_error = (
                    "consultation runtime left the repository revision "
                    "unavailable or dirty"
                )
            else:
                revision_error = (
                    None
                    if settled_revision == revision
                    else "consultation runtime changed the repository revision"
                )
            if revision_error is not None:
                result = RuntimeResult(
                    exit_code=(
                        1 if result.exit_code == 0 else result.exit_code
                    ),
                    stdout=result.stdout,
                    stderr=revision_error,
                    timed_out=result.timed_out,
                    process_started=result.process_started or process_started,
                    usage_observation=result.usage_observation,
                )
        return self._settle_consultation(
            consultation,
            runtime,
            prompt,
            result,
        )

    def _settle_consultation(
        self,
        consultation: ConsultationRun,
        runtime: RuntimeCommand,
        prompt: str,
        result: RuntimeResult,
    ) -> ConsultationRun:
        adapted = adapt_consultation_output(
            result,
            expected_decision_key=consultation.decision_key,
        )
        observation = settlement_observation(
            run_id=consultation.consultation_id,
            adapter=runtime.adapter,
            prompt=prompt,
            output=result.stdout,
            process_started=result.process_started,
            exit_code=result.exit_code,
            result_format=runtime.result_format,
            native_observation=result.usage_observation,
        )
        if result.timed_out:
            failure = f"timeout after {self.timeout_seconds}s"
        elif result.exit_code != 0:
            failure = result.stderr.strip() or adapted.error_code
        else:
            failure = adapted.error_code
        return self.store.settle_consultation(
            consultation.consultation_id,
            adapted=adapted,
            output_sha256=hashlib.sha256(
                result.stdout.encode("utf-8")
            ).hexdigest(),
            error_message=failure,
            usage_observation=observation,
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
        if (
            task.metadata.get("methodology_activation_sha256") is not None
            and task.metadata.get("methodology_dispatch_authority") is False
        ):
            raise OrchestrationConflictError(
                "Migrated successor budget amendment is deferred until its "
                "executable routing contract is activated"
            )
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
        observation = settlement_observation(
            run_id=run.run_id,
            adapter=runtime.adapter,
            prompt=prompt,
            output=output,
            process_started=result.process_started if result is not None else True,
            exit_code=exit_code,
            result_format=runtime.result_format,
            native_observation=(result.usage_observation if result is not None else None),
        )
        token_used = observation.total_tokens
        token_measurement = Measurement(observation.token_measurement)
        cost_used_usd = observation.cost_usd
        cost_measurement = Measurement(observation.cost_measurement)
        return self.store.finish_run(
            run.run_id, exit_code=exit_code,
            timed_out=bool(result and result.timed_out), output=output,
            error_message=failure,
            semantic=semantic,
            token_used=token_used,
            token_measurement=token_measurement,
            cost_used_usd=cost_used_usd,
            cost_measurement=cost_measurement,
            usage_observation=observation,
        )

    async def run_next_protocol(self, task_id: str) -> OrchestrationRun:
        """Dispatch one explicit Context/Handoff v1 Run through the formal Gate."""

        task, contract, route, status, stage, runtime = (
            self._protocol_dispatch_inputs(task_id, repair=True)
        )
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
        capability_observation = await self.capability_collector(self.runtimes)
        runtime_preflight = derive_pinned_runtime_preflight(
            observation=capability_observation,
            runtimes=self.runtimes,
            route=route,
            routing_policy=routing_policy,
            run_id=run_id,
        )
        if not runtime_preflight.allowed:
            raise OrchestrationConflictError(runtime_preflight.blockers[0])
        definition = build_protocol_run_definition(
            task=task,
            contract=contract,
            stage=stage,
            run_id=run_id,
            revision=revision,
            prior_artifacts=prior,
            decisions=self.store.latest_decisions(status.plan.plan_id),
            routing_policy=routing_policy,
            runtime_preflight=runtime_preflight,
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
            runtime_preflight=runtime_preflight,
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
                    usage_observation=settlement_observation(
                        run_id=run_id,
                        adapter=runtime.adapter,
                        prompt=definition.prompt,
                        output="",
                        process_started=False,
                        exit_code=None,
                        result_format=runtime.result_format,
                        native_observation=None,
                    ),
                )
                raise OrchestrationConflictError(
                    f"Formal protocol Run could not start: {detail}"
                ) from exc

        process_started = False

        async def attach_pid(pid: int) -> None:
            nonlocal process_started
            process_started = True
            self.store.attach_pid(run_id, pid)

        def before_spawn(
            checked_runtime: RuntimeCommand,
            resolved_command: list[str],
        ) -> None:
            self.preflight_rechecker(
                decision=runtime_preflight,
                observation=capability_observation,
                runtimes=self.runtimes,
                runtime=checked_runtime,
                resolved_command=resolved_command,
            )

        try:
            # Keep a service-boundary check for injected/custom Runners. The
            # default Runner invokes the same callback again after its own
            # command resolution, immediately before process creation.
            before_spawn(
                runtime,
                resolve_runtime_command(runtime.build(definition.prompt)),
            )
            result = await self.runner.run(
                runtime,
                definition.prompt,
                cwd=project.root,
                task_id=task_id,
                run_id=run_id,
                stage_key=stage.stage_key,
                timeout_seconds=self.timeout_seconds,
                on_process=attach_pid,
                before_spawn=before_spawn,
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
        repository_revision_mismatch = False
        if result.process_started or process_started:
            try:
                settled_revision = self.revision_resolver(
                    project.root,
                    task.project_id,
                )
            except (TypeError, ValueError):
                revision_error = (
                    "formal runtime left the repository revision unavailable or dirty"
                )
            else:
                revision_error = (
                    None
                    if settled_revision == revision
                    else "formal runtime changed the repository revision"
                )
            if revision_error is not None:
                repository_revision_mismatch = True
                result = RuntimeResult(
                    exit_code=(
                        result.exit_code
                        if result.exit_code not in {None, 0}
                        else 1
                    ),
                    stdout=result.stdout,
                    stderr=revision_error,
                    timed_out=result.timed_out,
                    process_started=result.process_started or process_started,
                    usage_observation=result.usage_observation,
                )
        return self._settle_protocol_result(
            run,
            definition,
            result,
            repository_revision_mismatch=repository_revision_mismatch,
        )

    def _settle_protocol_result(
        self,
        run: OrchestrationRun,
        definition: ProtocolRunDefinition,
        result: RuntimeResult,
        *,
        cancelled: bool = False,
        repository_revision_mismatch: bool = False,
    ) -> OrchestrationRun:
        adapted = adapt_runtime_result(
            definition.context_pack,
            result,
            gate_requirements=definition.gate_requirements,
            cancelled=cancelled,
            repository_revision_mismatch=repository_revision_mismatch,
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
        runtime = self.runtimes[run.adapter]
        observation = settlement_observation(
            run_id=run.run_id,
            adapter=run.adapter,
            prompt=definition.prompt,
            output=result.stdout,
            process_started=result.process_started,
            exit_code=result.exit_code,
            result_format=runtime.result_format,
            native_observation=result.usage_observation,
        )
        return self.store.finish_protocol_run(
            run.run_id,
            receipt=receipt,
            adapter_result=adapted,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            output=result.stdout,
            error_message=failure,
            token_used=observation.total_tokens,
            token_measurement=Measurement(observation.token_measurement),
            cost_used_usd=observation.cost_usd,
            cost_measurement=Measurement(observation.cost_measurement),
            usage_observation=observation,
        )

    async def preview_runtime_preflight(
        self,
        task_id: str,
    ) -> RuntimePreflightPreview:
        """Collect and explain one exact preflight without claiming or spawning."""

        task, contract, route, _, _, _ = self._protocol_dispatch_inputs(
            task_id,
            repair=False,
        )
        run_id = self.store.new_run_id().replace("orun_", "preview_", 1)
        routing_policy = self.store.preview_routing_policy(
            task_id,
            route=route,
            contract=contract,
            run_id=run_id,
        )
        if not routing_policy.dispatchable:
            raise OrchestrationConflictError(routing_policy.blockers[0])
        capability_observation = await self.capability_collector(self.runtimes)
        decision = derive_pinned_runtime_preflight(
            observation=capability_observation,
            runtimes=self.runtimes,
            route=route,
            routing_policy=routing_policy,
            run_id=run_id,
        )
        return RuntimePreflightPreview(
            generated_at=utc_now(),
            task_id=task.task_id,
            project_id=task.project_id,
            decision=decision,
            remediation=runtime_preflight_remediation(decision),
        )

    def preview_methodology_migration(
        self,
        task_id: str,
        request: MethodologyMigrationPreviewRequest,
    ) -> MethodologyMigrationPreviewDecision:
        """Explain one successor-Task proposal without persistence or mutation."""

        snapshot = self.store.methodology_migration_snapshot(task_id)
        project = None
        try:
            project = self.projects.get(snapshot.task.project_id)
            repository = self.revision_resolver(
                project.root,
                snapshot.task.project_id,
            )
        except (KeyError, ValueError):
            repository = None

        artifacts = list(request.seed_artifacts)
        if request.human_gate is not None:
            artifacts.append(request.human_gate.migration_artifact)
        observed_artifact_sha256s = (
            observe_migration_artifacts(project.root, artifacts)
            if project is not None
            else {artifact.path: None for artifact in artifacts}
        )
        return derive_methodology_migration_preview(
            request=request,
            snapshot=snapshot,
            repository=repository,
            runtimes=self.runtimes,
            observed_artifact_sha256s=observed_artifact_sha256s,
            generated_at=utc_now(),
        )

    def activate_methodology_migration(
        self,
        task_id: str,
        request: MethodologyMigrationPreviewRequest,
        *,
        principal: ControlPrincipal,
    ) -> MethodologyMigrationActivationReceipt:
        """Authenticate, atomically recheck, and create a successor Task."""

        try:
            project = self.projects.get(request.project_id)
        except KeyError as exc:
            raise OrchestrationConflictError(
                "Migration project is not registered"
            ) from exc

        def recheck(
            snapshot,
            successor_task_id: str,
            successor_plan_id: str,
            activated_at: str,
        ):
            try:
                repository_before = self.revision_resolver(
                    project.root,
                    snapshot.task.project_id,
                )
            except (KeyError, TypeError, ValueError):
                repository_before = None
            artifacts = list(request.seed_artifacts)
            if request.human_gate is not None:
                artifacts.append(request.human_gate.migration_artifact)
            observed_artifact_sha256s = observe_migration_artifacts(
                project.root,
                artifacts,
            )
            try:
                repository_after = self.revision_resolver(
                    project.root,
                    snapshot.task.project_id,
                )
            except (KeyError, TypeError, ValueError):
                repository_after = None
            repository = (
                repository_after
                if repository_before is not None
                and repository_after == repository_before
                else None
            )
            decision = derive_methodology_migration_preview(
                request=request,
                snapshot=snapshot,
                repository=repository,
                runtimes=self.runtimes,
                observed_artifact_sha256s=observed_artifact_sha256s,
                generated_at=activated_at,
            )
            if not decision.eligible:
                raise OrchestrationConflictError(
                    "Methodology migration atomic recheck blocked: "
                    + ", ".join(decision.blockers)
                )
            try:
                return build_methodology_successor_materialization(
                    source_task=snapshot.task,
                    request=request,
                    recheck_decision=decision,
                    principal=principal,
                    successor_task_id=successor_task_id,
                    successor_plan_id=successor_plan_id,
                    activated_at=activated_at,
                )
            except ValueError as exc:
                raise OrchestrationValidationError(str(exc)) from exc

        return self.store.activate_methodology_successor(
            task_id,
            request,
            principal=principal,
            control_plane=self.control_plane,
            recheck=recheck,
        )

    def materialize_methodology_execution_contract(
        self,
        task_id: str,
        *,
        principal: ControlPrincipal,
    ) -> MethodologyExecutionContract:
        """Seal successor execution templates without activating a route."""

        task = self.tasks.get(task_id)
        if task is None:
            raise OrchestrationConflictError("Task not found")
        try:
            project = self.projects.get(task.project_id)
        except KeyError as exc:
            raise OrchestrationConflictError(
                "Methodology successor project is not registered"
            ) from exc

        def materialize(snapshot, materialized_at: str):
            try:
                repository_before = self.revision_resolver(
                    project.root,
                    snapshot.task.project_id,
                )
            except (KeyError, TypeError, ValueError):
                repository_before = None
            artifacts = [
                *snapshot.request.seed_artifacts,
                snapshot.gate.assertion.migration_artifact,
            ]
            observed_artifact_sha256s = observe_migration_artifacts(
                project.root,
                artifacts,
            )
            try:
                repository_after = self.revision_resolver(
                    project.root,
                    snapshot.task.project_id,
                )
            except (KeyError, TypeError, ValueError):
                repository_after = None
            repository = (
                repository_after
                if repository_before is not None
                and repository_after == repository_before
                else None
            )
            try:
                return build_methodology_execution_contract(
                    snapshot=snapshot,
                    principal=principal,
                    repository=repository,
                    observed_artifact_sha256s=observed_artifact_sha256s,
                    runtimes=self.runtimes,
                    timeout_seconds=self.timeout_seconds,
                    max_output_bytes=OUTPUT_LIMIT,
                    materialized_at=materialized_at,
                )
            except ValueError as exc:
                raise OrchestrationConflictError(
                    f"Methodology execution contract materialization blocked: {exc}"
                ) from exc

        return self.store.materialize_methodology_execution_contract(
            task_id,
            principal=principal,
            control_plane=self.control_plane,
            materialize=materialize,
        )

    def activate_methodology_first_route(
        self,
        task_id: str,
        request: MethodologyRouteActivationRequest,
        *,
        principal: ControlPrincipal,
    ) -> MethodologyRouteActivationReceipt:
        """Authenticate and atomically activate only the first sealed route."""

        task = self.tasks.get(task_id)
        if task is None:
            raise OrchestrationConflictError("Task not found")
        try:
            project = self.projects.get(task.project_id)
        except KeyError as exc:
            raise OrchestrationConflictError(
                "Methodology successor project is not registered"
            ) from exc

        def recheck(snapshot, _activated_at: str):
            try:
                repository_before = self.revision_resolver(
                    project.root,
                    snapshot.task.project_id,
                )
            except (KeyError, TypeError, ValueError):
                repository_before = None
            artifacts = [
                *snapshot.migration_request.seed_artifacts,
                snapshot.migration_gate.assertion.migration_artifact,
            ]
            observed_artifact_sha256s = observe_migration_artifacts(
                project.root,
                artifacts,
            )
            try:
                repository_after = self.revision_resolver(
                    project.root,
                    snapshot.task.project_id,
                )
            except (KeyError, TypeError, ValueError):
                repository_after = None
            repository = (
                repository_after
                if repository_before is not None
                and repository_after == repository_before
                else None
            )
            return validate_methodology_route_activation(
                snapshot=snapshot,
                request=request,
                principal=principal,
                repository=repository,
                observed_artifact_sha256s=observed_artifact_sha256s,
                runtimes=self.runtimes,
            )

        return self.store.activate_methodology_first_route(
            task_id,
            request,
            principal=principal,
            control_plane=self.control_plane,
            recheck=recheck,
        )

    def claim_methodology_first_run(
        self,
        task_id: str,
        request: MethodologyRunClaimRequest,
        *,
        principal: ControlPrincipal,
    ) -> MethodologyRunClaimReceipt:
        """Authenticate and atomically claim one formal first Run."""

        task = self.tasks.get(task_id)
        if task is None:
            raise OrchestrationConflictError("Task not found")
        try:
            project = self.projects.get(task.project_id)
        except KeyError as exc:
            raise OrchestrationConflictError(
                "Methodology successor project is not registered"
            ) from exc

        def recheck(snapshot, claimed_at: str):
            try:
                repository_before = self.revision_resolver(
                    project.root,
                    snapshot.task.project_id,
                )
            except (KeyError, TypeError, ValueError):
                repository_before = None
            artifacts = [
                *snapshot.migration_request.seed_artifacts,
                snapshot.migration_gate.assertion.migration_artifact,
            ]
            observed_artifact_sha256s = observe_migration_artifacts(
                project.root,
                artifacts,
            )
            try:
                repository_after = self.revision_resolver(
                    project.root,
                    snapshot.task.project_id,
                )
            except (KeyError, TypeError, ValueError):
                repository_after = None
            repository = (
                repository_after
                if repository_before is not None
                and repository_after == repository_before
                else None
            )
            return build_methodology_run_claim_context(
                snapshot=snapshot,
                request=request,
                principal=principal,
                repository=repository,
                observed_artifact_sha256s=observed_artifact_sha256s,
                runtimes=self.runtimes,
                claimed_at=claimed_at,
            )

        return self.store.claim_methodology_first_run(
            task_id,
            request,
            principal=principal,
            control_plane=self.control_plane,
            recheck=recheck,
        )

    def configure_methodology_next_stage_gate(
        self,
        task_id: str,
        request: MethodologyStageGateRequest,
        *,
        principal: ControlPrincipal,
    ) -> MethodologyStageGateReceipt:
        """Authenticate and configure only the exact current next Gate."""

        task = self.tasks.get(task_id)
        if task is None:
            raise OrchestrationConflictError("Task not found")
        try:
            project = self.projects.get(task.project_id)
        except KeyError as exc:
            raise OrchestrationConflictError(
                "Methodology successor project is not registered"
            ) from exc

        def recheck(snapshot, _configured_at: str):
            try:
                repository_before = self.revision_resolver(
                    project.root,
                    snapshot.task.project_id,
                )
            except (KeyError, TypeError, ValueError):
                repository_before = None
            artifacts = [
                *snapshot.migration_request.seed_artifacts,
                snapshot.migration_gate.assertion.migration_artifact,
            ]
            observed_artifact_sha256s = observe_migration_artifacts(
                project.root,
                artifacts,
            )
            try:
                repository_after = self.revision_resolver(
                    project.root,
                    snapshot.task.project_id,
                )
            except (KeyError, TypeError, ValueError):
                repository_after = None
            repository = (
                repository_after
                if repository_before is not None
                and repository_after == repository_before
                else None
            )
            return validate_methodology_stage_gate(
                snapshot=snapshot,
                request=request,
                principal=principal,
                repository=repository,
                observed_artifact_sha256s=observed_artifact_sha256s,
                runtimes=self.runtimes,
            )

        return self.store.configure_methodology_next_stage_gate(
            task_id,
            request,
            principal=principal,
            control_plane=self.control_plane,
            recheck=recheck,
        )

    def claim_methodology_next_stage_run(
        self,
        task_id: str,
        request: MethodologyStageRunClaimRequest,
        *,
        principal: ControlPrincipal,
    ) -> MethodologyStageRunClaimReceipt:
        """Authenticate and atomically claim the sequence-2 formal Run."""

        task = self.tasks.get(task_id)
        if task is None:
            raise OrchestrationConflictError("Task not found")
        try:
            project = self.projects.get(task.project_id)
        except KeyError as exc:
            raise OrchestrationConflictError(
                "Methodology successor project is not registered"
            ) from exc

        def recheck(snapshot, claimed_at: str):
            try:
                repository_before = self.revision_resolver(
                    project.root,
                    snapshot.task.project_id,
                )
            except (KeyError, TypeError, ValueError):
                repository_before = None
            artifacts = [
                *snapshot.migration_request.seed_artifacts,
                snapshot.migration_gate.assertion.migration_artifact,
            ]
            observed_artifact_sha256s = observe_migration_artifacts(
                project.root,
                artifacts,
            )
            try:
                repository_after = self.revision_resolver(
                    project.root,
                    snapshot.task.project_id,
                )
            except (KeyError, TypeError, ValueError):
                repository_after = None
            repository = (
                repository_after
                if repository_before is not None
                and repository_after == repository_before
                else None
            )
            return build_methodology_stage_run_claim_context(
                snapshot=snapshot,
                request=request,
                principal=principal,
                repository=repository,
                observed_artifact_sha256s=observed_artifact_sha256s,
                runtimes=self.runtimes,
                claimed_at=claimed_at,
            )

        return self.store.claim_methodology_next_stage_run(
            task_id,
            request,
            principal=principal,
            control_plane=self.control_plane,
            recheck=recheck,
        )

    async def dispatch_methodology_first_run(
        self,
        task_id: str,
        *,
        allow_unbounded_native_usage: bool,
    ) -> MethodologyRunDispatchReceipt:
        """Attach exactly one native process to an already claimed formal Run."""

        if not allow_unbounded_native_usage:
            raise OrchestrationValidationError(
                "Methodology provider dispatch requires explicit "
                "unbounded-native-usage acknowledgement"
            )
        existing = self.store.get_methodology_run_dispatch(task_id)
        if existing is not None:
            return self._recover_methodology_run_dispatch(existing)

        snapshot = self.store.methodology_run_dispatch_snapshot(
            task_id,
            control_plane=self.control_plane,
        )
        first_stage = snapshot.execution_contract.stages[0]
        runtime = self.runtimes.get(first_stage.runtime)
        if runtime is None:
            raise OrchestrationConflictError(
                "Methodology dispatch pinned runtime is unavailable"
            )
        try:
            project = self.projects.get(snapshot.task.project_id)
        except KeyError as exc:
            raise OrchestrationConflictError(
                "Methodology dispatch project is not registered"
            ) from exc
        repository_before = self._resolve_methodology_dispatch_repository(
            project.root,
            snapshot.task.project_id,
            snapshot.execution_contract.repository,
        )
        prompt = build_protocol_prompt(
            context_pack=snapshot.protocol_run.context_pack,
            runtime=first_stage.runtime,
            requirements=snapshot.formal_gate.requirements,
        )
        prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        dispatch_policy = derive_methodology_run_dispatch_policy(
            snapshot=snapshot,
            repository=repository_before,
            runtimes=self.runtimes,
            evaluated_at=utc_now(),
        )
        if not dispatch_policy.dispatchable:
            raise OrchestrationConflictError(dispatch_policy.blockers[0])
        capability_observation = await self.capability_collector(self.runtimes)
        runtime_preflight = derive_methodology_runtime_preflight(
            snapshot=snapshot,
            dispatch_policy=dispatch_policy,
            observation=capability_observation,
            runtimes=self.runtimes,
        )
        if not runtime_preflight.allowed:
            raise OrchestrationConflictError(runtime_preflight.blockers[0])
        repository_after = self._resolve_methodology_dispatch_repository(
            project.root,
            snapshot.task.project_id,
            snapshot.execution_contract.repository,
        )
        if repository_before != repository_after:
            raise OrchestrationConflictError(
                "Methodology dispatch repository changed during preflight"
            )

        def recheck(current_snapshot, claimed_at: str) -> MethodologyRunDispatchClaim:
            return build_methodology_run_dispatch_claim(
                snapshot=current_snapshot,
                repository=repository_after,
                runtimes=self.runtimes,
                dispatch_policy=dispatch_policy,
                runtime_preflight=runtime_preflight,
                prompt_sha256=prompt_sha256,
                claimed_at=claimed_at,
            )

        dispatch = self.store.claim_methodology_run_dispatch(
            task_id,
            control_plane=self.control_plane,
            recheck=recheck,
        )
        claim = dispatch.claim
        process_started = False

        async def attach_pid(pid: int) -> None:
            nonlocal process_started
            self.store.attach_methodology_run_pid(claim.dispatch_id, pid)
            process_started = True

        def before_spawn(
            checked_runtime: RuntimeCommand,
            resolved_command: list[str],
        ) -> None:
            self._resolve_methodology_dispatch_repository(
                project.root,
                snapshot.task.project_id,
                snapshot.execution_contract.repository,
            )
            self.preflight_rechecker(
                decision=runtime_preflight,
                observation=capability_observation,
                runtimes=self.runtimes,
                runtime=checked_runtime,
                resolved_command=resolved_command,
            )

        cancelled = False
        try:
            # Keep a service-boundary recheck for injected/custom Runners. The
            # default Runner repeats it after resolving the exact spawn argv.
            before_spawn(
                runtime,
                resolve_runtime_command(runtime.build(prompt)),
            )
            result = await self.runner.run(
                runtime,
                prompt,
                cwd=project.root,
                task_id=task_id,
                run_id=claim.run_id,
                stage_key=claim.first_stage_key,
                timeout_seconds=snapshot.protocol_run.context_pack.budget.max_seconds,
                on_process=attach_pid,
                before_spawn=before_spawn,
            )
        except RuntimeInterrupted as exc:
            result = RuntimeResult(
                exit_code=None,
                stdout="",
                stderr=str(exc),
                process_started=True,
            )
        except asyncio.CancelledError:
            cancelled = True
            result = RuntimeResult(
                exit_code=None,
                stdout="",
                stderr="Methodology orchestration task was cancelled",
                process_started=process_started,
            )
        except Exception as exc:
            result = RuntimeResult(
                exit_code=None,
                stdout="",
                stderr=(
                    f"methodology runtime boundary failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
                process_started=process_started,
            )

        repository_unchanged = True
        if result.process_started or process_started:
            try:
                self._resolve_methodology_dispatch_repository(
                    project.root,
                    snapshot.task.project_id,
                    snapshot.execution_contract.repository,
                )
            except OrchestrationConflictError:
                repository_unchanged = False
                result = RuntimeResult(
                    exit_code=(
                        result.exit_code
                        if result.exit_code not in {None, 0}
                        else 1
                    ),
                    stdout=result.stdout,
                    stderr="methodology runtime changed the repository revision",
                    timed_out=result.timed_out,
                    process_started=True,
                    usage_observation=result.usage_observation,
                )
        receipt = self._settle_methodology_run_dispatch(
            dispatch,
            prompt=prompt,
            result=result,
            repository_unchanged=repository_unchanged,
            cancelled=cancelled,
        )
        if cancelled:
            raise asyncio.CancelledError
        return receipt

    def _resolve_methodology_dispatch_repository(
        self,
        project_root: Path,
        project_id: str,
        expected,
    ) -> RepositoryRevision:
        try:
            repository = self.revision_resolver(project_root, project_id)
        except (KeyError, TypeError, ValueError) as exc:
            raise OrchestrationConflictError(
                "Methodology dispatch repository binding is unavailable"
            ) from exc
        if (
            repository.repository_id != expected.repository_id
            or repository.ref != expected.ref
            or repository.commit_sha != expected.commit_sha
        ):
            raise OrchestrationConflictError(
                "Methodology dispatch repository binding is stale"
            )
        return repository

    def _settle_methodology_run_dispatch(
        self,
        dispatch: MethodologyRunDispatchState,
        *,
        prompt: str,
        result: RuntimeResult,
        repository_unchanged: bool,
        cancelled: bool = False,
    ) -> MethodologyRunDispatchReceipt:
        claim = dispatch.claim
        protocol_run = self.control_plane.get_protocol_run(claim.run_id)
        gate = self.control_plane.get_gate(claim.task_id, claim.first_gate_key)
        if protocol_run is None or gate is None:
            raise OrchestrationConflictError(
                "Methodology dispatch formal Run or Gate is unavailable"
            )
        adapted = adapt_runtime_result(
            protocol_run.context_pack,
            result,
            gate_requirements=gate.requirements,
            cancelled=cancelled and result.process_started,
            repository_revision_mismatch=not repository_unchanged,
        )
        observation = settlement_observation(
            run_id=claim.run_id,
            adapter=claim.runtime,
            prompt=prompt,
            output=result.stdout,
            process_started=result.process_started,
            exit_code=result.exit_code,
            result_format=self.runtimes[claim.runtime].result_format,
            native_observation=result.usage_observation,
        )
        observed = self.store.observe_methodology_run_terminal(
            claim.dispatch_id,
            process_started=result.process_started,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            output=result.stdout,
            error_message=(result.stderr.strip() or None),
            repository_unchanged=repository_unchanged,
            adapter_result=adapted,
            usage_observation=observation,
        )
        return self._finish_methodology_run_dispatch(observed)

    def _finish_methodology_run_dispatch(
        self,
        dispatch: MethodologyRunDispatchState,
    ) -> MethodologyRunDispatchReceipt:
        if dispatch.state == MethodologyDispatchState.SETTLED:
            assert dispatch.receipt is not None
            return dispatch.receipt
        if (
            dispatch.state != MethodologyDispatchState.TERMINAL_OBSERVED
            or dispatch.adapter_result is None
        ):
            raise OrchestrationConflictError(
                "Methodology dispatch is not ready for protocol settlement"
            )
        settlement = self.control_plane.settle_protocol_run(
            dispatch.adapter_result,
            actor="orchestrator",
            operation_key=(
                f"methodology-protocol-settle:{dispatch.claim.run_id}"
            ),
        )
        return self.store.finish_methodology_run_dispatch(
            dispatch.dispatch_id,
            settlement=settlement,
            control_plane=self.control_plane,
        )

    def _recover_methodology_run_dispatch(
        self,
        dispatch: MethodologyRunDispatchState,
    ) -> MethodologyRunDispatchReceipt:
        if dispatch.state == MethodologyDispatchState.SETTLED:
            assert dispatch.receipt is not None
            return dispatch.receipt
        if dispatch.state == MethodologyDispatchState.TERMINAL_OBSERVED:
            return self._finish_methodology_run_dispatch(dispatch)
        snapshot = self.store.methodology_run_dispatch_snapshot(
            dispatch.claim.task_id,
            control_plane=self.control_plane,
        )
        prompt = build_protocol_prompt(
            context_pack=snapshot.protocol_run.context_pack,
            runtime=dispatch.claim.runtime,
            requirements=snapshot.formal_gate.requirements,
        )
        if hashlib.sha256(prompt.encode("utf-8")).hexdigest() != (
            dispatch.claim.prompt_sha256
        ):
            raise OrchestrationConflictError(
                "Recovered methodology dispatch prompt binding changed"
            )
        if dispatch.state == MethodologyDispatchState.CLAIMED:
            result = RuntimeResult(
                exit_code=None,
                stdout="",
                stderr=(
                    "Recovered a methodology Run whose process never attached"
                ),
                process_started=False,
            )
        else:
            assert dispatch.pid is not None
            process_state = self.process_inspector(dispatch.pid)
            if process_state != ProcessState.DEAD:
                raise OrchestrationConflictError(
                    f"Methodology dispatch {dispatch.dispatch_id} process "
                    f"{dispatch.pid} is {process_state.value}; refusing "
                    "duplicate dispatch"
                )
            result = RuntimeResult(
                exit_code=None,
                stdout="",
                stderr=(
                    "Recovered a methodology Run whose process was no longer active"
                ),
                process_started=True,
            )
        try:
            project = self.projects.get(dispatch.claim.project_id)
            self._resolve_methodology_dispatch_repository(
                project.root,
                dispatch.claim.project_id,
                dispatch.claim.repository,
            )
            repository_unchanged = True
        except (KeyError, OrchestrationConflictError):
            repository_unchanged = False
        return self._settle_methodology_run_dispatch(
            dispatch,
            prompt=prompt,
            result=result,
            repository_unchanged=repository_unchanged,
        )

    async def dispatch_methodology_next_stage_run(
        self,
        task_id: str,
        *,
        allow_unbounded_native_usage: bool,
    ) -> MethodologyStageRunDispatchReceipt:
        """Attach one process to the already claimed sequence-2 formal Run."""

        if not allow_unbounded_native_usage:
            raise OrchestrationValidationError(
                "Methodology Stage provider dispatch requires explicit "
                "unbounded-native-usage acknowledgement"
            )
        existing = self.store.get_methodology_stage_run_dispatch(task_id)
        if existing is not None:
            return self._recover_methodology_stage_run_dispatch(existing)

        spawn_owner_id = f"stage-dispatch-owner:{uuid.uuid4().hex}"

        snapshot = self.store.methodology_stage_run_dispatch_snapshot(
            task_id,
            control_plane=self.control_plane,
        )
        stage = snapshot.execution_contract.stages[
            snapshot.stage_run_claim_receipt.stage_sequence - 1
        ]
        runtime = self.runtimes.get(stage.runtime)
        if runtime is None:
            raise OrchestrationConflictError(
                "Methodology Stage dispatch pinned runtime is unavailable"
            )
        try:
            project = self.projects.get(snapshot.task.project_id)
        except KeyError as exc:
            raise OrchestrationConflictError(
                "Methodology Stage dispatch project is not registered"
            ) from exc
        repository_before = self._resolve_methodology_dispatch_repository(
            project.root,
            snapshot.task.project_id,
            snapshot.execution_contract.repository,
        )
        prompt = build_protocol_prompt(
            context_pack=snapshot.protocol_run.context_pack,
            runtime=stage.runtime,
            requirements=snapshot.formal_gate.requirements,
        )
        prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        dispatch_policy = derive_methodology_stage_run_dispatch_policy(
            snapshot=snapshot,
            repository=repository_before,
            runtimes=self.runtimes,
            evaluated_at=utc_now(),
        )
        if not dispatch_policy.dispatchable:
            raise OrchestrationConflictError(dispatch_policy.blockers[0])
        capability_observation = await self.capability_collector(self.runtimes)
        runtime_preflight = derive_methodology_stage_runtime_preflight(
            snapshot=snapshot,
            dispatch_policy=dispatch_policy,
            observation=capability_observation,
            runtimes=self.runtimes,
        )
        if not runtime_preflight.allowed:
            raise OrchestrationConflictError(runtime_preflight.blockers[0])
        repository_after = self._resolve_methodology_dispatch_repository(
            project.root,
            snapshot.task.project_id,
            snapshot.execution_contract.repository,
        )
        if repository_before != repository_after:
            raise OrchestrationConflictError(
                "Methodology Stage repository changed during preflight"
            )

        def recheck(
            current_snapshot,
            claimed_at: str,
        ) -> MethodologyStageRunDispatchClaim:
            return build_methodology_stage_run_dispatch_claim(
                snapshot=current_snapshot,
                repository=repository_after,
                runtimes=self.runtimes,
                dispatch_policy=dispatch_policy,
                runtime_preflight=runtime_preflight,
                prompt_sha256=prompt_sha256,
                spawn_owner_id=spawn_owner_id,
                claimed_at=claimed_at,
            )

        dispatch = self.store.claim_methodology_stage_run_dispatch(
            task_id,
            control_plane=self.control_plane,
            recheck=recheck,
        )
        claim = dispatch.claim
        process_started = False

        async def attach_pid(pid: int) -> None:
            nonlocal process_started
            self.store.attach_methodology_stage_run_pid(
                claim.dispatch_id,
                pid,
                spawn_owner_id=spawn_owner_id,
            )
            process_started = True

        def before_spawn(
            checked_runtime: RuntimeCommand,
            resolved_command: list[str],
        ) -> None:
            self._resolve_methodology_dispatch_repository(
                project.root,
                snapshot.task.project_id,
                snapshot.execution_contract.repository,
            )
            self.preflight_rechecker(
                decision=runtime_preflight,
                observation=capability_observation,
                runtimes=self.runtimes,
                runtime=checked_runtime,
                resolved_command=resolved_command,
            )

        cancelled = False
        try:
            before_spawn(
                runtime,
                resolve_runtime_command(runtime.build(prompt)),
            )
            result = await self.runner.run(
                runtime,
                prompt,
                cwd=project.root,
                task_id=task_id,
                run_id=claim.run_id,
                stage_key=claim.stage_key,
                timeout_seconds=(
                    snapshot.protocol_run.context_pack.budget.max_seconds
                ),
                on_process=attach_pid,
                before_spawn=before_spawn,
            )
        except RuntimeInterrupted as exc:
            result = RuntimeResult(
                exit_code=None,
                stdout="",
                stderr=str(exc),
                process_started=True,
            )
        except asyncio.CancelledError:
            cancelled = True
            result = RuntimeResult(
                exit_code=None,
                stdout="",
                stderr="Methodology Stage orchestration task was cancelled",
                process_started=process_started,
            )
        except Exception as exc:
            result = RuntimeResult(
                exit_code=None,
                stdout="",
                stderr=(
                    "methodology Stage runtime boundary failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
                process_started=process_started,
            )

        repository_unchanged = True
        if result.process_started or process_started:
            try:
                self._resolve_methodology_dispatch_repository(
                    project.root,
                    snapshot.task.project_id,
                    snapshot.execution_contract.repository,
                )
            except OrchestrationConflictError:
                repository_unchanged = False
                result = RuntimeResult(
                    exit_code=(
                        result.exit_code
                        if result.exit_code not in {None, 0}
                        else 1
                    ),
                    stdout=result.stdout,
                    stderr=(
                        "methodology Stage runtime changed the repository revision"
                    ),
                    timed_out=result.timed_out,
                    process_started=True,
                    usage_observation=result.usage_observation,
                )
        receipt = self._settle_methodology_stage_run_dispatch(
            dispatch,
            prompt=prompt,
            result=result,
            repository_unchanged=repository_unchanged,
            cancelled=cancelled,
            spawn_owner_id=spawn_owner_id,
        )
        if cancelled:
            raise asyncio.CancelledError
        return receipt

    def _settle_methodology_stage_run_dispatch(
        self,
        dispatch: MethodologyStageRunDispatchState,
        *,
        prompt: str,
        result: RuntimeResult,
        repository_unchanged: bool,
        cancelled: bool = False,
        spawn_owner_id: str | None = None,
    ) -> MethodologyStageRunDispatchReceipt:
        claim = dispatch.claim
        protocol_run = self.control_plane.get_protocol_run(claim.run_id)
        gate = self.control_plane.get_gate(claim.task_id, claim.gate_key)
        if protocol_run is None or gate is None:
            raise OrchestrationConflictError(
                "Methodology Stage formal Run or Gate is unavailable"
            )
        adapted = adapt_runtime_result(
            protocol_run.context_pack,
            result,
            gate_requirements=gate.requirements,
            cancelled=cancelled and result.process_started,
            repository_revision_mismatch=not repository_unchanged,
        )
        observation = settlement_observation(
            run_id=claim.run_id,
            adapter=claim.runtime,
            prompt=prompt,
            output=result.stdout,
            process_started=result.process_started,
            exit_code=result.exit_code,
            result_format=RuntimeResultFormat(claim.result_format),
            native_observation=result.usage_observation,
        )
        observed = self.store.observe_methodology_stage_run_terminal(
            claim.dispatch_id,
            process_started=result.process_started,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            output=result.stdout,
            error_message=(result.stderr.strip() or None),
            repository_unchanged=repository_unchanged,
            adapter_result=adapted,
            usage_observation=observation,
            spawn_owner_id=spawn_owner_id,
        )
        return self._finish_methodology_stage_run_dispatch(observed)

    def _finish_methodology_stage_run_dispatch(
        self,
        dispatch: MethodologyStageRunDispatchState,
    ) -> MethodologyStageRunDispatchReceipt:
        if dispatch.state == MethodologyDispatchState.SETTLED:
            assert dispatch.receipt is not None
            return dispatch.receipt
        if (
            dispatch.state != MethodologyDispatchState.TERMINAL_OBSERVED
            or dispatch.adapter_result is None
        ):
            raise OrchestrationConflictError(
                "Methodology Stage dispatch is not ready for settlement"
            )
        settlement = self.control_plane.settle_protocol_run(
            dispatch.adapter_result,
            actor="orchestrator",
            operation_key=(
                "methodology-stage-protocol-settle:"
                f"{dispatch.claim.run_id}"
            ),
        )
        return self.store.finish_methodology_stage_run_dispatch(
            dispatch.dispatch_id,
            settlement=settlement,
            control_plane=self.control_plane,
        )

    def _recover_methodology_stage_run_dispatch(
        self,
        dispatch: MethodologyStageRunDispatchState,
    ) -> MethodologyStageRunDispatchReceipt:
        if dispatch.state == MethodologyDispatchState.SETTLED:
            assert dispatch.receipt is not None
            return dispatch.receipt
        if dispatch.state == MethodologyDispatchState.TERMINAL_OBSERVED:
            return self._finish_methodology_stage_run_dispatch(dispatch)
        snapshot = self.store.methodology_stage_run_dispatch_snapshot(
            dispatch.claim.task_id,
            control_plane=self.control_plane,
        )
        prompt = build_protocol_prompt(
            context_pack=snapshot.protocol_run.context_pack,
            runtime=dispatch.claim.runtime,
            requirements=snapshot.formal_gate.requirements,
        )
        if hashlib.sha256(prompt.encode("utf-8")).hexdigest() != (
            dispatch.claim.prompt_sha256
        ):
            raise OrchestrationConflictError(
                "Recovered methodology Stage prompt binding changed"
            )
        if dispatch.state == MethodologyDispatchState.CLAIMED:
            if datetime.now(timezone.utc) < dispatch.claim.recovery_not_before:
                raise OrchestrationConflictError(
                    "Methodology Stage dispatch owner lease is active; refusing "
                    "premature recovery before PID attachment"
                )
            result = RuntimeResult(
                exit_code=None,
                stdout="",
                stderr=(
                    "Recovered a methodology Stage Run whose process never attached"
                ),
                process_started=False,
            )
        else:
            assert dispatch.pid is not None
            process_state = self.process_inspector(dispatch.pid)
            if process_state != ProcessState.DEAD:
                raise OrchestrationConflictError(
                    f"Methodology Stage dispatch {dispatch.dispatch_id} process "
                    f"{dispatch.pid} is {process_state.value}; refusing "
                    "duplicate dispatch"
                )
            result = RuntimeResult(
                exit_code=None,
                stdout="",
                stderr=(
                    "Recovered a methodology Stage Run whose process was inactive"
                ),
                process_started=True,
            )
        try:
            project = self.projects.get(dispatch.claim.project_id)
            self._resolve_methodology_dispatch_repository(
                project.root,
                dispatch.claim.project_id,
                dispatch.claim.repository,
            )
            repository_unchanged = True
        except (KeyError, OrchestrationConflictError):
            repository_unchanged = False
        return self._settle_methodology_stage_run_dispatch(
            dispatch,
            prompt=prompt,
            result=result,
            repository_unchanged=repository_unchanged,
            spawn_owner_id=None,
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
        consultations = [
            item
            for item in self.store.consultations(status.plan.plan_id)
            if item.state == ConsultationState.RUNNING
        ]
        for consultation in consultations:
            runtime = self.runtimes.get(consultation.runtime)
            if runtime is None:
                raise OrchestrationConflictError(
                    "Cannot recover consultation because its pinned runtime "
                    f"is unavailable: {consultation.runtime}"
                )
            if consultation.pid is None:
                result = RuntimeResult(
                    exit_code=None,
                    stdout="",
                    stderr=(
                        "Recovered a consultation whose process never attached"
                    ),
                    process_started=False,
                )
            else:
                process_state = self.process_inspector(consultation.pid)
                if process_state != ProcessState.DEAD:
                    raise OrchestrationConflictError(
                        f"Consultation {consultation.consultation_id} process "
                        f"{consultation.pid} is {process_state.value}; "
                        "refusing duplicate dispatch"
                    )
                result = RuntimeResult(
                    exit_code=None,
                    stdout="",
                    stderr=(
                        "Recovered a consultation whose process was no longer active"
                    ),
                    process_started=True,
                )
            self._settle_consultation(
                consultation,
                runtime,
                "",
                result,
            )
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
        methodology_dispatch = self.store.get_methodology_run_dispatch(task_id)
        if (
            methodology_dispatch is not None
            and methodology_dispatch.state != MethodologyDispatchState.SETTLED
        ):
            self._recover_methodology_run_dispatch(methodology_dispatch)
        methodology_stage_dispatch = (
            self.store.get_methodology_stage_run_dispatch(task_id)
        )
        if (
            methodology_stage_dispatch is not None
            and methodology_stage_dispatch.state
            != MethodologyDispatchState.SETTLED
        ):
            self._recover_methodology_stage_run_dispatch(
                methodology_stage_dispatch
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
            runtime = self.runtimes[run.adapter]
            observation = settlement_observation(
                run_id=run.run_id,
                adapter=run.adapter,
                prompt="",
                output=result.stdout,
                process_started=result.process_started,
                exit_code=result.exit_code,
                result_format=runtime.result_format,
                native_observation=None,
            )
            self.store.finish_protocol_run(
                run.run_id,
                receipt=receipt,
                adapter_result=adapted,
                exit_code=result.exit_code,
                timed_out=result.timed_out,
                output=result.stdout,
                error_message=result.stderr,
                token_used=observation.total_tokens,
                token_measurement=Measurement(observation.token_measurement),
                cost_used_usd=observation.cost_usd,
                cost_measurement=Measurement(observation.cost_measurement),
                usage_observation=observation,
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
        runtime = self.runtimes[run.adapter]
        observation = settlement_observation(
            run_id=run.run_id,
            adapter=run.adapter,
            prompt="",
            output=output,
            process_started=process_status != "launch_failed",
            # The native result envelope was lost before compatibility
            # projection, so a recovered process exit cannot recreate usage.
            exit_code=None,
            result_format=runtime.result_format,
            native_observation=None,
        )
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
            token_used=observation.total_tokens,
            token_measurement=Measurement(observation.token_measurement),
            cost_used_usd=observation.cost_usd,
            cost_measurement=Measurement(observation.cost_measurement),
            usage_observation=observation,
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

    def _build_consultation_prompt(
        self,
        task: TaskManifest,
        status: TaskOrchestrationStatus,
        route: StageRouteDecision,
        revision: RepositoryRevision,
        *,
        decision_key: str,
        question: str,
        token_reserved: int,
    ) -> str:
        """Build a bounded advisory context without handing over prior transcripts."""

        decisions = [
            {
                "decision_key": item.decision_key,
                "decision_value": item.decision_value,
                "rationale": item.rationale,
                "version": item.version,
            }
            for item in self.store.latest_decisions(status.plan.plan_id)
        ]
        contract_binding = None
        if task.metadata.get("task_contract") is not None:
            contract_binding = {
                "contract_id": task.metadata.get("task_contract_id"),
                "schema_version": task.metadata.get(
                    "task_contract_schema_version"
                ),
                "sha256": task.metadata.get("task_contract_sha256"),
            }
        context = {
            "task": {
                "task_id": task.task_id,
                "project_id": task.project_id,
                "title": task.title,
                "description": self._truncate(task.description or "", 2_000),
                "acceptance": [
                    self._truncate(item, 500) for item in task.acceptance[:20]
                ],
                "risk": task.risk.value,
                "contract": contract_binding,
            },
            "plan": {
                "plan_id": status.plan.plan_id,
                "version": status.plan.version,
                "state": status.plan.state.value,
                "methodology_id": status.plan.methodology_id,
                "methodology_version": status.plan.methodology_version,
                "methodology_sha256": status.plan.methodology_sha256,
            },
            "authoritative_route": {
                "inventory_id": route.inventory_id,
                "inventory_sha256": route.inventory_sha256,
                "stage_key": route.stage_key,
                "role": route.role,
                "runtime": route.runtime,
            },
            "repository": {
                "repository_id": revision.repository_id,
                "ref": revision.ref,
                "commit_sha": revision.commit_sha,
            },
            "latest_human_decisions": decisions,
            "consultation": {
                "decision_key": decision_key,
                "question": question,
                "token_reservation": token_reserved,
            },
        }
        context_json = json.dumps(
            context,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        prompt = f"""You are the pinned {route.role} advisor for one Agora Task consultation.

This consultation is READ-ONLY and ADVISORY. Do not modify files, create commits,
change native AI-DLC state, claim or advance a Stage, satisfy a Gate, create a
formal Artifact or Evidence record, or claim that the Task has been delivered.
Agora alone owns Task, Stage, Run, Gate, decision, and candidate authority.

Versioned bounded consultation context (not a prior transcript):
{context_json}

Answer only the requested decision key. Return ONLY one JSON object with exactly
these fields:
{{
  "schema_version": "1.0",
  "title": "concise candidate title",
  "decision_key": "{decision_key}",
  "decision_value": "one bounded proposed value",
  "analysis": "reasoning, tradeoffs, uncertainties, and blockers",
  "source_refs": ["stable requirement, evidence, or colon-normalized path identifier"]
}}

The decision_key must match exactly. Source references must be stable identifiers,
not secrets or credentials. The result remains non-authoritative until a human
explicitly adopts or rejects the immutable candidate.
"""
        if len(prompt.encode("utf-8")) > CONSULTATION_PROMPT_LIMIT:
            raise OrchestrationConflictError(
                "Consultation context exceeds the bounded prompt allocation"
            )
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
        activation_sha256 = task.metadata.get("methodology_activation_sha256")
        if activation_sha256 is not None:
            inventory = self.control_plane.get_stage_inventory(task_id)
            route_activated = task.metadata.get("methodology_route_activated")
            run_claimed = task.metadata.get("methodology_run_claimed", False)
            if (
                task.metadata.get("methodology_dispatch_authority") is not False
                or not isinstance(route_activated, bool)
                or not isinstance(run_claimed, bool)
                or (run_claimed and not route_activated)
                or plan.state != PlanState.READY_FOR_IMPLEMENTATION
                or plan.methodology_id
                != task.metadata.get("methodology", "").partition("@")[0]
                or plan.methodology_sha256 != activation_sha256
                or inventory is None
                or inventory.task_id != task.task_id
                or inventory.project_id != task.project_id
                or inventory.plan_id != plan.plan_id
                or inventory.methodology_id != plan.methodology_id
                or inventory.methodology_version != plan.methodology_version
                or inventory.methodology_sha256 != plan.methodology_sha256
            ):
                raise OrchestrationValidationError(
                    "Migrated successor Plan/inventory binding is unavailable or drifted"
                )
            return inventory
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

    def _protocol_dispatch_inputs(
        self,
        task_id: str,
        *,
        repair: bool,
    ) -> tuple[
        TaskManifest,
        TaskContract,
        StageRouteDecision,
        TaskOrchestrationStatus,
        OrchestrationStage,
        RuntimeCommand,
    ]:
        """Load one dispatchable sealed route, optionally repairing durable state."""

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
        if repair:
            self._ensure_grouped_stage_inventory(task_id, actor="orchestrator")
            route = self._ensure_authoritative_stage_route(
                task_id,
                actor="orchestrator",
            )
        else:
            if self.control_plane.get_stage_inventory(task_id) is None:
                raise OrchestrationConflictError(
                    "Authoritative Stage inventory is unavailable; run task resume"
                )
            route = self.control_plane.get_stage_route(task_id)
        if route is None:
            raise OrchestrationConflictError(
                "Every authoritative inventory Stage already completed"
            )
        status = self.store.status(task_id)
        if status.plan.state != PlanState.ACTIVE:
            raise OrchestrationConflictError(
                f"Plan is {status.plan.state.value}, not active"
            )
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
            status_value = (
                route.stage_status.value if route.stage_status else "unconfigured"
            )
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
                f"Frozen Task lifecycle is {lifecycle.target_status.value}, "
                "not dispatchable"
            )
        if not route.runnable:
            raise OrchestrationConflictError(
                "Authoritative Stage route is not dispatchable; run task resume"
            )
        runtime = self.runtimes.get(route.runtime)
        if runtime is None:
            raise OrchestrationConflictError(
                f"Runtime is unavailable: {route.runtime}"
            )
        return task, contract, route, status, stage, runtime

    def _ensure_authoritative_stage_route(
        self,
        task_id: str,
        *,
        actor: str,
    ) -> StageRouteDecision | None:
        route = self.control_plane.get_stage_route(task_id)
        if route is None:
            return None
        task = self.tasks.get(task_id)
        if (
            task is not None
            and task.metadata.get("methodology_activation_sha256") is not None
            and task.metadata.get("methodology_route_activated") is False
        ):
            return route
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
