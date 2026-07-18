"""Application service for one task-scoped, three-runtime planning loop."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Callable

from pydantic import ValidationError

from agora.projects import ProjectRegistry
from agora.tasks.models import CreateTaskRequest, TaskBudget, TaskManifest, TaskRisk
from agora.tasks.store import TaskStore

from .contracts import TaskContract, canonical_contract_json, contract_sha256
from .methodology import FOUNDATION_METHODOLOGY, MethodologyDefinition
from .models import (
    Measurement,
    OrchestrationRun,
    PlanState,
    RunState,
    SemanticResult,
    StageState,
    TaskOrchestrationStatus,
)
from .processes import ProcessState, inspect_process
from .runtime import ReadOnlyCliRunner, RuntimeCommand, RuntimeInterrupted
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
        methodology: MethodologyDefinition = FOUNDATION_METHODOLOGY,
        timeout_seconds: int = 600,
    ):
        self.tasks = tasks
        self.projects = projects
        self.runtimes = runtimes
        self.runner = runner or ReadOnlyCliRunner()
        self.process_inspector = process_inspector
        self.methodology = methodology
        self.timeout_seconds = min(max(timeout_seconds, 1), 7200)
        self.store = OrchestrationStore(tasks)

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
        return self.store.create_plan(
            task_id, self.methodology,
            total_token_budget=total_token_budget,
            total_cost_budget_usd=total_cost_budget_usd,
            actor=actor,
        )

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

    async def run_next(self, task_id: str) -> OrchestrationRun:
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
        return self.store.finish_run(
            run.run_id, exit_code=exit_code,
            timed_out=bool(result and result.timed_out), output=output,
            error_message=failure,
            semantic=semantic,
            token_used=token_used,
            token_measurement=token_measurement,
        )

    async def run_until_blocked(self, task_id: str) -> TaskOrchestrationStatus:
        while True:
            status = self.store.status(task_id)
            if status.plan.state != PlanState.ACTIVE:
                return status
            await self.run_next(task_id)

    def resume(self, task_id: str) -> TaskOrchestrationStatus:
        status = self.store.status(task_id)
        running = [run for run in status.runs if run.state == RunState.RUNNING]
        for run in running:
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
        return self.store.status(task_id)

    def retry(self, task_id: str, stage_key: str):
        return self.store.retry(task_id, stage_key)

    def approve(self, task_id: str, *, actor: str, reason: str):
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

    def _validate_contract_alignment(self, contract: TaskContract) -> None:
        definitions = {stage.stage_key: stage for stage in self.methodology.stages}
        supplied_keys = [stage.stage_key for stage in contract.workflow]
        expected_keys = [stage.stage_key for stage in self.methodology.stages]
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
