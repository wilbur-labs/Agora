from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import pytest

from agora.orchestration import cli as orchestration_cli
from agora.orchestration.consultation import adapt_consultation_output
from agora.orchestration.contracts import load_task_contract
from agora.orchestration.models import ConsultationState, Measurement
from agora.orchestration.processes import ProcessState
from agora.orchestration.protocol_context import RepositoryRevision
from agora.orchestration.runtime import (
    RuntimeCommand,
    RuntimeInterrupted,
    RuntimeResult,
)
from agora.orchestration.service import TaskOrchestrationService
from agora.orchestration.store import OrchestrationConflictError
from agora.projects import ProjectRegistry
from agora.protocol.hashing import seal_model_payload
from agora.protocol.models import ProviderUsageObservation, SchemaStatus
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


def _draft(
    *,
    decision_key: str = "auth.policy",
    source_refs: list[str] | None = None,
) -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "title": "Use fail-closed authentication",
            "decision_key": decision_key,
            "decision_value": "Require an authenticated bearer principal",
            "analysis": "Anonymous access conflicts with the Task boundary.",
            "source_refs": (
                ["requirement:authenticated_api"]
                if source_refs is None
                else source_refs
            ),
        }
    )


class ConsultationRunner:
    def __init__(self, results):
        self.results = list(results)
        self.prompts: list[str] = []
        self.pid = 424_242
        self.before_result = None

    async def run(self, runtime, prompt, **kwargs):
        self.prompts.append(prompt)
        result = self.results.pop(0)
        if result.process_started:
            await kwargs["on_process"](self.pid)
        if self.before_result is not None:
            self.before_result()
        return result


def _system(
    tmp_path,
    results,
    *,
    revision_resolver=lambda _root, _project_id: REVISION,
    process_inspector=lambda _pid: ProcessState.DEAD,
    contract=None,
):
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
                    "workspaces": {},
                }
            },
        }
    }
    tasks = TaskStore(tmp_path / "agora.db")
    runner = ConsultationRunner(results)
    service = TaskOrchestrationService(
        tasks,
        ProjectRegistry(config, project_root=tmp_path),
        {
            name: RuntimeCommand(
                adapter=name,
                command_template=(sys.executable, "{prompt}"),
            )
            for name in ("codex", "claude", "kiro")
        },
        runner=runner,
        revision_resolver=revision_resolver,
        process_inspector=process_inspector,
    )
    task = service.create(
        project_id="alpha",
        title="Ship authenticated API",
        description="Keep every write fail closed.",
        total_token_budget=30_000,
        total_cost_budget_usd=12,
        contract=contract,
    )
    return tasks, service, runner, task


def _claim(service, task_id, *, operation_key="consult:test:running"):
    status = service.status(task_id)
    route = service.control_plane.get_stage_route(task_id)
    return service.store.claim_consultation(
        task_id,
        route=route,
        repository_id=REVISION.repository_id,
        repository_ref=REVISION.ref,
        repository_commit=REVISION.commit_sha,
        expected_plan_version=status.plan.version,
        decision_key="auth.policy",
        prompt_sha256=hashlib.sha256(b"test prompt").hexdigest(),
        token_reserved=1_000,
        cost_reserved_usd=1,
        operation_key=operation_key,
    )[0]


@pytest.mark.asyncio
async def test_native_consultation_registers_only_an_advisory_candidate(tmp_path):
    tasks, service, runner, task = _system(
        tmp_path,
        [RuntimeResult(0, _draft(), "")],
    )
    before = service.status(task.task_id)

    consultation = await service.consult(
        task.task_id,
        decision_key="auth.policy",
        question="Which authentication policy should the API use?",
        token_reserved=1_000,
        cost_reserved_usd=1,
    )

    assert consultation.state == ConsultationState.COMPLETED
    assert consultation.candidate_id is not None
    assert consultation.usage_observation.token_measurement == "estimated"
    assert "READ-ONLY and ADVISORY" in runner.prompts[0]
    assert len(runner.prompts[0].encode("utf-8")) <= 16_000
    after = service.status(task.task_id)
    assert after.plan.version == before.plan.version
    assert after.stages == before.stages
    assert after.runs == before.runs
    assert after.decisions == []
    projection = service.unified_status(task.task_id)
    assert projection.schema_version == "11.0"
    assert projection.consultation_runs == [consultation]
    assert len(projection.consultation_candidates) == 1
    assert projection.consultation_candidate_dispositions == []
    assert projection.artifacts == []
    assert projection.collection_totals["consultation_runs"] == 1
    assert projection.budget.token_measurement == "estimated"
    event_types = [item.event_type for item in tasks.events(task.task_id)]
    assert "orchestration.consultation_started" in event_types
    assert "orchestration.consultation_settled" in event_types


@pytest.mark.asyncio
async def test_consultation_repair_replay_and_protocol_failure_are_bounded(tmp_path):
    fenced = f"```json\n{_draft()}\n```"
    _, service, runner, task = _system(
        tmp_path,
        [RuntimeResult(0, fenced, "")],
    )
    first = await service.consult(
        task.task_id,
        decision_key="auth.policy",
        question="Choose the policy",
        token_reserved=1_000,
        cost_reserved_usd=1,
        operation_key="consult:test:replay",
    )
    replay = await service.consult(
        task.task_id,
        decision_key="auth.policy",
        question="Choose the policy",
        token_reserved=1_000,
        cost_reserved_usd=1,
        operation_key="consult:test:replay",
    )
    assert replay == first
    assert first.schema_status == SchemaStatus.REPAIRED
    assert first.repair_attempts == 1
    assert len(runner.prompts) == 1

    _, invalid_service, _, invalid_task = _system(
        tmp_path / "invalid",
        [RuntimeResult(0, "provider prose before {}", "")],
    )
    failed = await invalid_service.consult(
        invalid_task.task_id,
        decision_key="auth.policy",
        question="Choose the policy",
        token_reserved=1_000,
        cost_reserved_usd=1,
    )
    assert failed.state == ConsultationState.PROTOCOL_FAILED
    assert failed.error_code == "candidate_json_invalid"
    assert invalid_service.unified_status(
        invalid_task.task_id
    ).consultation_candidates == []


def test_consultation_parser_separates_process_transport_and_schema():
    wrong_key = adapt_consultation_output(
        RuntimeResult(0, _draft(decision_key="other.policy"), ""),
        expected_decision_key="auth.policy",
    )
    assert wrong_key.schema_status == SchemaStatus.PROTOCOL_FAILED
    assert wrong_key.error_code == "candidate_decision_key_mismatch"

    nonzero = adapt_consultation_output(
        RuntimeResult(7, _draft(), "native failure"),
        expected_decision_key="auth.policy",
    )
    assert nonzero.schema_status == SchemaStatus.PENDING
    assert nonzero.error_code == "process_nonzero_exit"

    oversized = adapt_consultation_output(
        RuntimeResult(0, "x" * (16 * 1024 + 1), ""),
        expected_decision_key="auth.policy",
    )
    assert oversized.error_code == "candidate_too_large"


@pytest.mark.asyncio
async def test_consultation_protects_reviewers_and_excludes_repository_drift(tmp_path):
    _, service, runner, task = _system(
        tmp_path,
        [RuntimeResult(0, _draft(), "")],
    )
    with pytest.raises(
        OrchestrationConflictError,
        match="protected reviewer Tokens",
    ):
        await service.consult(
            task.task_id,
            decision_key="auth.policy",
            question="Choose the policy",
            token_reserved=15_001,
            cost_reserved_usd=1,
        )
    assert runner.prompts == []

    revisions = iter(
        [
            REVISION,
            REVISION.model_copy(update={"commit_sha": "b" * 40}),
        ]
    )
    _, drift_service, _, drift_task = _system(
        tmp_path / "drift",
        [RuntimeResult(0, _draft(), "")],
        revision_resolver=lambda _root, _project_id: next(revisions),
    )
    drifted = await drift_service.consult(
        drift_task.task_id,
        decision_key="auth.policy",
        question="Choose the policy",
        token_reserved=1_000,
        cost_reserved_usd=1,
    )
    assert drifted.state == ConsultationState.FAILED
    assert drifted.candidate_id is None
    assert "changed the repository revision" in drifted.error_message


@pytest.mark.asyncio
async def test_repository_drift_preserves_an_interrupted_process_dimension(tmp_path):
    revisions = iter(
        [
            REVISION,
            REVISION.model_copy(update={"commit_sha": "b" * 40}),
        ]
    )
    _, service, _, task = _system(
        tmp_path,
        [],
        revision_resolver=lambda _root, _project_id: next(revisions),
    )

    class InterruptedRunner:
        async def run(self, _runtime, _prompt, **kwargs):
            await kwargs["on_process"](424_242)
            raise RuntimeInterrupted("native process became uninspectable")

    service.runner = InterruptedRunner()
    interrupted = await service.consult(
        task.task_id,
        decision_key="auth.policy",
        question="Choose the policy",
        token_reserved=1_000,
        cost_reserved_usd=1,
    )

    assert interrupted.state == ConsultationState.INTERRUPTED
    assert interrupted.process_status == "interrupted"
    assert interrupted.usage_observation.token_measurement == "unavailable"
    assert interrupted.candidate_id is None
    assert "changed the repository revision" in interrupted.error_message


@pytest.mark.asyncio
async def test_cancellation_before_pid_attach_settles_provable_zero_usage(tmp_path):
    _, service, _, task = _system(tmp_path, [])

    class CancelledBeforeSpawnRunner:
        async def run(self, _runtime, _prompt, **_kwargs):
            raise asyncio.CancelledError

    service.runner = CancelledBeforeSpawnRunner()
    with pytest.raises(asyncio.CancelledError):
        await service.consult(
            task.task_id,
            decision_key="auth.policy",
            question="Choose the policy",
            token_reserved=1_000,
            cost_reserved_usd=1,
        )

    consultation = service.store.consultations(
        service.status(task.task_id).plan.plan_id
    )[0]
    assert consultation.state == ConsultationState.FAILED
    assert consultation.process_status == "launch_failed"
    assert consultation.usage_observation.token_measurement == "exact"
    assert consultation.usage_observation.total_tokens == 0


@pytest.mark.asyncio
async def test_settlement_rejects_stale_plan_and_sensitive_source_refs(tmp_path):
    _, service, runner, task = _system(
        tmp_path,
        [RuntimeResult(0, _draft(), "")],
    )
    version = service.status(task.task_id).plan.version
    existing = service.register_consultation_candidate(
        task.task_id,
        consultation_id="consultation:preexisting",
        runtime="codex",
        title="Preexisting candidate",
        decision_key="existing.policy",
        decision_value="Use the existing bounded policy",
        analysis="This candidate exists only to advance the Plan version.",
        expected_plan_version=version,
        operation_key="candidate:test:preexisting",
    )
    runner.before_result = lambda: service.adopt_candidate(
        task.task_id,
        existing.candidate_id,
        expected_plan_version=version,
        reason="Advance the Plan while the consultation is outside SQLite",
        operation_key="candidate:test:advance-during-consultation",
    )

    stale = await service.consult(
        task.task_id,
        decision_key="auth.policy",
        question="Choose the policy",
        token_reserved=1_000,
        cost_reserved_usd=1,
    )

    assert stale.state == ConsultationState.PROTOCOL_FAILED
    assert stale.error_code == "candidate_context_stale"
    assert stale.candidate_id is None
    assert stale.usage_observation.token_measurement == "estimated"
    candidates = service.unified_status(task.task_id).consultation_candidates
    assert [item.candidate_id for item in candidates] == [existing.candidate_id]

    sensitive_ref = "token:sk-abcdefghijklmnopqrst"
    _, sensitive_service, _, sensitive_task = _system(
        tmp_path / "sensitive",
        [
            RuntimeResult(
                0,
                _draft(source_refs=[sensitive_ref]),
                "",
            )
        ],
    )
    sensitive = await sensitive_service.consult(
        sensitive_task.task_id,
        decision_key="auth.policy",
        question="Choose the policy",
        token_reserved=1_000,
        cost_reserved_usd=1,
    )
    assert sensitive.state == ConsultationState.PROTOCOL_FAILED
    assert sensitive.error_code == "candidate_source_ref_sensitive"
    assert sensitive.candidate_id is None
    assert sensitive_ref not in json.dumps(
        [item.payload for item in sensitive_service.tasks.events(
            sensitive_task.task_id
        )]
    )
    assert sensitive_service.unified_status(
        sensitive_task.task_id
    ).consultation_candidates == []


@pytest.mark.asyncio
async def test_consultation_maps_unreadable_repository_to_bounded_conflict(tmp_path):
    _, service, runner, task = _system(
        tmp_path,
        [RuntimeResult(0, _draft(), "")],
        revision_resolver=lambda _root, _project_id: (_ for _ in ()).throw(
            ValueError("dirty repository")
        ),
    )
    with pytest.raises(
        OrchestrationConflictError,
        match="clean, readable repository revision",
    ):
        await service.consult(
            task.task_id,
            decision_key="auth.policy",
            question="Choose the policy",
            token_reserved=1_000,
            cost_reserved_usd=1,
        )
    assert runner.prompts == []
    assert service.store.consultations(
        service.status(task.task_id).plan.plan_id
    ) == []


def test_consultation_recovery_never_duplicates_a_live_or_dead_process(tmp_path):
    _, service, _, task = _system(tmp_path, [])
    running = _claim(service, task.task_id)
    service.store.attach_consultation_pid(running.consultation_id, 101)
    service.resume(task.task_id)
    recovered = service.store.require_consultation(running.consultation_id)
    assert recovered.state == ConsultationState.INTERRUPTED
    assert recovered.usage_observation.token_measurement == "unavailable"

    _, live_service, _, live_task = _system(
        tmp_path / "live",
        [],
        process_inspector=lambda _pid: ProcessState.ALIVE,
    )
    live = _claim(live_service, live_task.task_id)
    live_service.store.attach_consultation_pid(live.consultation_id, 202)
    with pytest.raises(OrchestrationConflictError, match="refusing duplicate"):
        live_service.resume(live_task.task_id)
    assert (
        live_service.store.require_consultation(live.consultation_id).state
        == ConsultationState.RUNNING
    )


def test_running_consultation_blocks_formal_claim_and_projects_reservation(tmp_path):
    _, service, _, task = _system(tmp_path, [])
    running = _claim(service, task.task_id)

    projection = service.unified_status(task.task_id)
    assert projection.consultation_runs == [running]
    assert projection.budget.token_reserved == 1_000
    assert projection.budget.cost_reserved_usd == 1
    with pytest.raises(
        OrchestrationConflictError,
        match="consultation is already active",
    ):
        service.store.claim_current_stage(
            task.task_id,
            prompt_sha256="f" * 64,
            operation_key="formal:test:consultation-active",
        )
    with pytest.raises(
        OrchestrationConflictError,
        match="already active",
    ):
        _claim(
            service,
            task.task_id,
            operation_key="consult:test:second-running",
        )


def test_running_formal_run_blocks_a_consultation_claim(tmp_path):
    _, service, _, task = _system(tmp_path, [])
    service.store.claim_current_stage(
        task.task_id,
        prompt_sha256="f" * 64,
        operation_key="formal:test:running",
    )

    with pytest.raises(
        OrchestrationConflictError,
        match="formal Run or consultation is already active",
    ):
        _claim(
            service,
            task.task_id,
            operation_key="consult:test:formal-running",
        )


def test_consultation_usage_reduces_later_formal_run_budget(tmp_path):
    contract = load_task_contract(CONTRACT_PATH)
    _, service, _, task = _system(
        tmp_path,
        [],
        contract=contract,
    )
    consultation = _claim(
        service,
        task.task_id,
        operation_key="consult:test:formal-budget",
    )
    output = _draft()
    adapted = adapt_consultation_output(
        RuntimeResult(0, output, ""),
        expected_decision_key="auth.policy",
    )
    observation = ProviderUsageObservation.model_validate(
        seal_model_payload(
            ProviderUsageObservation,
            {
                "schema_version": "1.0",
                "run_id": consultation.consultation_id,
                "adapter": consultation.runtime,
                "provider": "openai",
                "source": "custom_text",
                "input_tokens": 10_000,
                "output_tokens": 7_000,
                "total_tokens": 17_000,
                "token_measurement": "exact",
                "token_method": "provider_input_plus_output",
                "cost_usd": 2,
                "cost_measurement": "exact",
                "cost_method": "provider_reported_total_cost_usd",
            },
        )
    )
    settled = service.store.settle_consultation(
        consultation.consultation_id,
        adapted=adapted,
        output_sha256=hashlib.sha256(output.encode("utf-8")).hexdigest(),
        error_message=None,
        usage_observation=observation,
    )
    route = service.control_plane.get_stage_route(task.task_id)

    policy = service.store.preview_routing_policy(
        task.task_id,
        route=route,
        contract=contract,
        run_id="orun:formal-after-consultation",
    )

    assert settled.state == ConsultationState.COMPLETED
    assert policy.settled_token_debit == 17_000
    assert policy.settled_cost_debit_usd == 2
    assert policy.dispatchable is False
    with pytest.raises(OrchestrationConflictError, match="Token budget is exhausted"):
        service.store.claim_current_stage(
            task.task_id,
            prompt_sha256="e" * 64,
            operation_key="formal:test:budget-after-consultation",
        )


def test_recovery_without_a_pid_settles_provable_launch_failure(tmp_path):
    _, service, _, task = _system(tmp_path, [])
    running = _claim(service, task.task_id)

    service.resume(task.task_id)

    recovered = service.store.require_consultation(running.consultation_id)
    assert recovered.state == ConsultationState.FAILED
    assert recovered.usage_observation.token_measurement == "exact"
    assert recovered.usage_observation.total_tokens == 0


def test_cli_consult_requires_usage_acknowledgement_and_prints_candidate(
    tmp_path,
    monkeypatch,
    capsys,
):
    _, service, _, task = _system(
        tmp_path,
        [RuntimeResult(0, _draft(), "")],
    )
    monkeypatch.setattr(orchestration_cli, "build_service", lambda: service)
    command = [
        "consult",
        task.task_id,
        "auth.policy",
        "--question",
        "Choose the policy",
        "--tokens",
        "1000",
        "--cost-usd",
        "1",
    ]
    assert orchestration_cli.main(command) == 2
    assert "--allow-unbounded-native-usage" in capsys.readouterr().out
    assert orchestration_cli.main(
        [*command, "--allow-unbounded-native-usage"]
    ) == 0
    output = capsys.readouterr().out
    assert '"state": "completed"' in output
    assert "Consultations:" in output
