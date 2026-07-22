from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from agora.orchestration.methodology import FOUNDATION_METHODOLOGY, methodology_sha256
from agora.orchestration.contracts import (
    TaskContract,
    contract_sha256,
    load_task_contract,
)
from agora.orchestration.models import Measurement, PlanState, RunState, StageState
from agora.orchestration.processes import ProcessState, inspect_process
from agora.orchestration.runtime import (
    ReadOnlyCliRunner,
    RuntimeCommand,
    RuntimeInterrupted,
    RuntimeLaunchError,
    RuntimeResult,
    build_runtime_registry,
    resolve_runtime_command,
)
from agora.orchestration import runtime as orchestration_runtime
from agora.orchestration.service import TaskOrchestrationService
from agora.orchestration.store import (
    OrchestrationConflictError,
    OrchestrationValidationError,
)
from agora.orchestration import cli as orchestration_cli
from agora.projects import ProjectRegistry
from agora.protocol.state_machines import TaskStatus
from agora.tasks.models import CreateTaskRequest, TaskState
from agora.tasks.store import TaskStore


PASS = (
    '{"status":"pass","summary":"stage passed","findings":[], '
    '"recommended_next_action":"continue"}'
)

CONTRACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "examples"
    / "bounded-control-plane-api-task-contract.json"
)


class FakeRunner:
    def __init__(self, results: list[RuntimeResult]):
        self.results = list(results)
        self.prompts: list[str] = []
        self.pid = 424_242

    async def run(self, runtime, prompt, **kwargs):
        self.prompts.append(prompt)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            await kwargs["on_process"](self.pid)
            raise result
        if result.process_started:
            await kwargs["on_process"](self.pid)
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


def test_concrete_task_contract_is_strict_bounded_and_method_aligned(tmp_path):
    contract = load_task_contract(CONTRACT_PATH)
    assert contract.schema_version == "1.0"
    assert contract.contract_id == "bounded_control_plane_api_vertical_slice"
    assert [stage.stage_key for stage in contract.workflow] == [
        "solution_design",
        "correctness_review",
        "methodology_review",
    ]
    assert len(contract_sha256(contract)) == 64

    payload = contract.model_dump(mode="json")
    payload["workflow"][0]["gate_requirements"][0]["requirement_id"] = "unknown"
    with pytest.raises(ValueError, match="exactly reference required evidence"):
        TaskContract.model_validate(payload)

    oversized = tmp_path / "oversized.json"
    oversized.write_text("x" * (64 * 1024 + 1), encoding="utf-8")
    with pytest.raises(ValueError, match="exceeds 64 KiB"):
        load_task_contract(oversized)


@pytest.mark.asyncio
async def test_contract_is_persisted_hashed_and_supplied_to_every_runtime(tmp_path):
    tasks, service, runner, _ = _system(tmp_path)
    contract = load_task_contract(CONTRACT_PATH)
    task = service.create(
        project_id="alpha",
        title=contract.title,
        description=contract.goal,
        total_token_budget=30_000,
        total_cost_budget_usd=12,
        contract=contract,
    )

    persisted = tasks.get(task.task_id)
    assert persisted.metadata["task_contract_id"] == contract.contract_id
    assert persisted.metadata["task_contract_schema_version"] == "1.0"
    assert persisted.metadata["task_contract_sha256"] == contract_sha256(contract)
    assert persisted.acceptance == contract.acceptance_criteria
    inventory = service.control_plane.get_stage_inventory(task.task_id)
    assert inventory.contract.contract_id == contract.contract_id
    assert inventory.contract.sha256 == contract_sha256(contract)

    await service.run_next(task.task_id)
    assert '"contract_id":"bounded_control_plane_api_vertical_slice"' in runner.prompts[-1]
    assert "Do not start Task Workbench" in runner.prompts[-1]


def test_contract_must_match_pinned_methodology_order_and_runtime(tmp_path):
    _, service, _, _ = _system(tmp_path)
    contract = load_task_contract(CONTRACT_PATH)
    payload = contract.model_dump(mode="json")
    payload["workflow"] = list(reversed(payload["workflow"]))
    reordered = TaskContract.model_validate(payload)

    with pytest.raises(OrchestrationValidationError, match="stage order"):
        service.create(
            project_id="alpha",
            title=reordered.title,
            description=reordered.goal,
            total_token_budget=30_000,
            total_cost_budget_usd=12,
            contract=reordered,
        )

    payload = contract.model_dump(mode="json")
    payload["roles"][0]["runtime"] = "claude"
    wrong_runtime = TaskContract.model_validate(payload)
    with pytest.raises(OrchestrationValidationError, match="runtime does not match"):
        service.create(
            project_id="alpha",
            title=wrong_runtime.title,
            description=wrong_runtime.goal,
            total_token_budget=30_000,
            total_cost_budget_usd=12,
            contract=wrong_runtime,
        )

    payload = contract.model_dump(mode="json")
    payload["roles"][0]["role_id"] = "renamed_planner"
    payload["workflow"][0]["role_id"] = "renamed_planner"
    for role in payload["roles"]:
        role["independent_from"] = [
            "renamed_planner" if item == "engineering_planner" else item
            for item in role["independent_from"]
        ]
    wrong_role = TaskContract.model_validate(payload)
    with pytest.raises(OrchestrationValidationError, match="role does not match"):
        service.create(
            project_id="alpha",
            title=wrong_role.title,
            description=wrong_role.goal,
            total_token_budget=30_000,
            total_cost_budget_usd=12,
            contract=wrong_role,
        )


@pytest.mark.asyncio
async def test_contract_and_large_prior_results_fit_the_bounded_prompt(tmp_path):
    large_pass = json.dumps({
        "status": "pass",
        "summary": "s" * 4_000,
        "findings": ["f" * 1_000 for _ in range(20)],
        "recommended_next_action": "continue",
    })
    tasks, service, runner, _ = _system(
        tmp_path,
        [RuntimeResult(0, large_pass, "") for _ in range(3)],
        tokens=300_000,
    )
    contract = load_task_contract(CONTRACT_PATH)
    task = service.create(
        project_id="alpha",
        title=contract.title,
        description=contract.goal,
        total_token_budget=300_000,
        total_cost_budget_usd=12,
        contract=contract,
    )

    await service.run_next(task.task_id)
    await service.run_next(task.task_id)
    await service.run_next(task.task_id)

    assert len(runner.prompts[-1]) <= 16_000
    assert "solution_design" in runner.prompts[-1]
    assert "correctness_review" in runner.prompts[-1]


def test_cli_starts_from_a_concrete_contract(tmp_path, monkeypatch, capsys):
    tasks, service, _, _ = _system(tmp_path)
    monkeypatch.setattr(orchestration_cli, "build_service", lambda: service)

    assert orchestration_cli.main([
        "start", "--contract", str(CONTRACT_PATH), "--tokens", "30000",
    ]) == 0

    created = next(
        task for task in tasks.list()
        if task.metadata.get("task_contract_id")
        == "bounded_control_plane_api_vertical_slice"
    )
    assert created.metadata["task_contract_id"] == "bounded_control_plane_api_vertical_slice"
    assert "Next safe action" in capsys.readouterr().out


def test_runtime_defaults_are_read_only_and_bounded():
    runtimes = build_runtime_registry({})
    assert "read-only" in runtimes["codex"].command_template
    assert "plan" in runtimes["claude"].command_template
    assert "--trust-all-tools" not in runtimes["kiro"].command_template
    with pytest.raises(ValueError, match="exactly one"):
        build_runtime_registry({
            "orchestration": {"runtimes": {"codex": {"command": ["codex"]}}}
        })
    with pytest.raises(ValueError, match="prompt as its executable"):
        build_runtime_registry({
            "orchestration": {
                "runtimes": {"codex": {"command": ["{prompt}", "--read-only"]}},
            },
        })


def test_runtime_network_policy_removes_inherited_proxies_case_insensitively(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://bad-proxy")
    monkeypatch.setenv("https_proxy", "http://bad-proxy")
    monkeypatch.setenv("ALL_PROXY", "http://bad-proxy")
    monkeypatch.setenv("NO_PROXY", "localhost")
    direct = ReadOnlyCliRunner(network_mode="direct")._environment({"AGORA_TASK_ID": "task"})
    assert not any(name.upper().endswith("PROXY") for name in direct)
    assert direct["AGORA_TASK_ID"] == "task"

    system = ReadOnlyCliRunner(network_mode="system")._environment({})
    system_by_upper_name = {name.upper(): value for name, value in system.items()}
    assert system_by_upper_name["HTTP_PROXY"] == "http://bad-proxy"
    assert system_by_upper_name["HTTPS_PROXY"] == "http://bad-proxy"
    with pytest.raises(ValueError, match="direct.*system"):
        ReadOnlyCliRunner(network_mode="invalid")


def test_windows_runtime_wrapper_resolution_is_explicit_and_fail_closed(tmp_path, monkeypatch):
    target = tmp_path / "vendor.exe"
    target.touch()
    direct = tmp_path / "vendor.cmd"
    direct.write_text(f'@echo off\n"{target}" %*\n', encoding="utf-8")
    monkeypatch.setattr(orchestration_runtime.shutil, "which", lambda name: str(direct))
    assert resolve_runtime_command(["vendor", "--version"], platform="win32") == [
        str(target), "--version",
    ]

    node = tmp_path / "node.exe"
    node.touch()
    script = tmp_path / "node_modules" / "vendor" / "bin" / "vendor.js"
    script.parent.mkdir(parents=True)
    script.touch()
    npm = tmp_path / "npm-vendor.cmd"
    npm.write_text(
        '@echo off\n"%dp0%\\node_modules\\vendor\\bin\\vendor.js" %*\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(orchestration_runtime.shutil, "which", lambda name: str(npm))
    assert resolve_runtime_command(["npm-vendor", "arg"], platform="win32") == [
        str(node), str(script), "arg",
    ]

    unknown = tmp_path / "unknown.cmd"
    unknown.write_text("@echo off\necho unsafe %*\n", encoding="utf-8")
    monkeypatch.setattr(orchestration_runtime.shutil, "which", lambda name: str(unknown))
    with pytest.raises(RuntimeLaunchError, match="unsupported"):
        resolve_runtime_command(["unknown", "arg"], platform="win32")


def test_windows_runtime_wrapper_rejects_escape_and_unavailable_targets(tmp_path, monkeypatch):
    escaped_script = tmp_path.parent / "escaped.js"
    escaped_script.touch()
    traversal = tmp_path / "traversal.cmd"
    traversal.write_text('@echo off\n"%dp0%\\..\\escaped.js" %*\n', encoding="utf-8")
    monkeypatch.setattr(orchestration_runtime.shutil, "which", lambda name: str(traversal))
    with pytest.raises(RuntimeLaunchError, match="npm wrapper target is unavailable"):
        resolve_runtime_command(["traversal"], platform="win32")

    relative_direct = tmp_path / "relative-direct.cmd"
    relative_direct.write_text('@echo off\n"%dp0%\\vendor.exe" %*\n', encoding="utf-8")
    monkeypatch.setattr(
        orchestration_runtime.shutil, "which", lambda name: str(relative_direct),
    )
    with pytest.raises(RuntimeLaunchError, match="Windows wrapper target is unavailable"):
        resolve_runtime_command(["relative-direct"], platform="win32")

    unreadable = tmp_path / "unreadable.cmd"
    unreadable.mkdir()
    monkeypatch.setattr(orchestration_runtime.shutil, "which", lambda name: str(unreadable))
    with pytest.raises(RuntimeLaunchError, match="wrapper is unreadable"):
        resolve_runtime_command(["unreadable"], platform="win32")


def test_windows_npm_wrapper_requires_a_native_node_executable(tmp_path, monkeypatch):
    script = tmp_path / "vendor.js"
    script.touch()
    wrapper = tmp_path / "npm-vendor.cmd"
    wrapper.write_text('@echo off\n"%dp0%\\vendor.js" %*\n', encoding="utf-8")

    def no_node(name):
        return str(wrapper) if name == "npm-vendor" else None

    monkeypatch.setattr(orchestration_runtime.shutil, "which", no_node)
    with pytest.raises(RuntimeLaunchError, match="Node.js executable is unavailable"):
        resolve_runtime_command(["npm-vendor"], platform="win32")


@pytest.mark.asyncio
async def test_runtime_output_capture_retains_only_a_bounded_tail():
    class ChunkedReader:
        def __init__(self, remaining: int):
            self.remaining = remaining
            self.largest_request = 0

        async def read(self, size: int) -> bytes:
            self.largest_request = max(self.largest_request, size)
            count = min(size, self.remaining)
            self.remaining -= count
            return b"x" * count

    reader = ChunkedReader(orchestration_runtime.OUTPUT_LIMIT * 10)
    captured = await ReadOnlyCliRunner._read_tail(reader)
    assert len(captured) == orchestration_runtime.OUTPUT_LIMIT
    assert captured == b"x" * orchestration_runtime.OUTPUT_LIMIT
    assert reader.largest_request == orchestration_runtime.OUTPUT_READ_SIZE


@pytest.mark.asyncio
async def test_post_stop_capture_drain_is_time_bounded(monkeypatch):
    never_finishes = asyncio.create_task(asyncio.Event().wait())
    monkeypatch.setattr(orchestration_runtime, "POST_STOP_DRAIN_TIMEOUT", 0.01)

    captured = await ReadOnlyCliRunner._finish_capture(never_finishes)

    assert captured is None
    assert never_finishes.cancelled()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="Windows wrapper integration")
async def test_runner_executes_resolved_windows_wrapper_without_prompt_shell_injection(
    tmp_path, monkeypatch,
):
    script = tmp_path / "emit.py"
    script.write_text(f"print({PASS!r})\n", encoding="utf-8")
    wrapper = tmp_path / "fake-runtime.cmd"
    wrapper.write_text(f'@echo off\n"{sys.executable}" %*\n', encoding="utf-8")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    pids: list[int] = []

    async def capture_pid(pid: int) -> None:
        pids.append(pid)

    result = await ReadOnlyCliRunner().run(
        RuntimeCommand(
            adapter="fake-runtime",
            command_template=("fake-runtime", str(script), "{prompt}"),
        ),
        "untrusted & echo INJECTED",
        cwd=tmp_path,
        task_id="task_test",
        run_id="run_test",
        stage_key="test",
        timeout_seconds=10,
        on_process=capture_pid,
    )
    assert result.process_started is True
    assert result.exit_code == 0
    assert result.stdout.strip() == PASS
    assert "INJECTED" not in result.stdout
    assert len(pids) == 1


@pytest.mark.asyncio
async def test_real_runner_cancellation_stops_child_and_propagates(tmp_path):
    script = tmp_path / "sleep.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    attached = asyncio.Event()
    pids: list[int] = []

    async def capture_pid(pid: int) -> None:
        pids.append(pid)
        attached.set()

    pending = asyncio.create_task(ReadOnlyCliRunner().run(
        RuntimeCommand(
            adapter="python",
            command_template=(sys.executable, str(script), "{prompt}"),
        ),
        "cancel safely",
        cwd=tmp_path,
        task_id="task_cancel",
        run_id="run_cancel",
        stage_key="test",
        timeout_seconds=30,
        on_process=capture_pid,
    ))
    await asyncio.wait_for(attached.wait(), timeout=5)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    assert len(pids) == 1
    assert inspect_process(pids[0]) == ProcessState.DEAD


def test_create_persists_method_budget_and_task_audit(tmp_path):
    tasks, service, _, task = _system(tmp_path)
    status = service.status(task.task_id)
    assert task.state == TaskState.BACKLOG
    assert status.plan.provisional is True
    assert status.plan.methodology_version == "0.1"
    assert [stage.token_budget for stage in status.stages] == [13_500, 9_000, 7_500]
    assert [stage.cost_budget_usd for stage in status.stages] == [5.4, 3.6, 3.0]
    assert status.next_safe_action == "Run stage solution_design with codex."
    assert status.decisions == []
    inventory = service.control_plane.get_stage_inventory(task.task_id)
    assert inventory is not None
    assert inventory.plan_id == status.plan.plan_id
    assert inventory.methodology_sha256 == status.plan.methodology_sha256
    assert inventory.contract is None
    assert [item.stage_key for item in inventory.groups[0].stages] == [
        "solution_design",
        "correctness_review",
        "methodology_review",
    ]
    events = tasks.events(task.task_id)
    assert [event.event_type for event in events[-6:]] == [
        "orchestration.plan_created",
        "task.state_initialized",
        "stage.inventory_initialized",
        "task.state_changed",
        "stage.created",
        "stage.activated",
    ]
    assert events[-6].payload["provisional"] is True
    assert events[-5].payload == {"status": "backlog", "version": 1}
    assert events[-4].payload["stage_count"] == 3
    assert events[-3].payload["from"] == "backlog"
    assert events[-3].payload["to"] == "ready"
    assert events[-2].payload["status"] == "pending"
    assert events[-1].payload["from"] == "pending"
    assert events[-1].payload["to"] == "ready"


def test_invalid_budget_does_not_leave_a_planless_task(tmp_path):
    tasks, service, _, _ = _system(tmp_path)
    before = [task.task_id for task in tasks.list()]

    with pytest.raises(OrchestrationValidationError, match="between 3000"):
        service.create(
            project_id="alpha", title="Invalid", description="Too small",
            total_token_budget=2_999, total_cost_budget_usd=1,
        )

    assert [task.task_id for task in tasks.list()] == before


def test_resume_recovers_task_state_initialization_after_create_interruption(
    tmp_path,
    monkeypatch,
):
    tasks, service, _, _ = _system(tmp_path)
    before = {task.task_id for task in tasks.list()}
    original_ensure = service.control_plane.ensure_task_state

    def interrupt_initialization(*_args, **_kwargs):
        raise RuntimeError("task-state initialization interrupted")

    monkeypatch.setattr(
        service.control_plane,
        "ensure_task_state",
        interrupt_initialization,
    )
    with pytest.raises(RuntimeError, match="initialization interrupted"):
        service.create(
            project_id="alpha",
            title="Recover frozen Task initialization",
            description="Simulate a crash after plan creation",
            total_token_budget=30_000,
            total_cost_budget_usd=12,
        )

    created = next(task for task in tasks.list() if task.task_id not in before)
    assert service.status(created.task_id).plan is not None
    assert service.control_plane.get_task_state(created.task_id) is None
    assert service.control_plane.get_stage_inventory(created.task_id) is None

    monkeypatch.setattr(
        service.control_plane,
        "ensure_task_state",
        original_ensure,
    )
    service.resume(created.task_id)
    recovered = service.control_plane.get_task_state(created.task_id)
    assert recovered.status == TaskStatus.READY
    assert recovered.version == 2
    inventory = service.control_plane.get_stage_inventory(created.task_id)
    assert inventory is not None
    assert len(inventory.groups[0].stages) == 3
    service.resume(created.task_id)
    assert service.control_plane.get_task_state(created.task_id) == recovered
    assert service.control_plane.get_stage_inventory(created.task_id) == inventory


def test_resume_recovers_stage_inventory_after_create_interruption(
    tmp_path,
    monkeypatch,
):
    tasks, service, _, _ = _system(tmp_path)
    before = {task.task_id for task in tasks.list()}
    original_ensure = service.control_plane.ensure_stage_inventory

    def interrupt_inventory(*_args, **_kwargs):
        raise RuntimeError("stage-inventory initialization interrupted")

    monkeypatch.setattr(
        service.control_plane,
        "ensure_stage_inventory",
        interrupt_inventory,
    )
    with pytest.raises(RuntimeError, match="initialization interrupted"):
        service.create(
            project_id="alpha",
            title="Recover Stage inventory",
            description="Simulate a crash after frozen Task initialization",
            total_token_budget=30_000,
            total_cost_budget_usd=12,
        )

    created = next(task for task in tasks.list() if task.task_id not in before)
    assert service.control_plane.get_task_state(created.task_id) is not None
    assert service.control_plane.get_stage_inventory(created.task_id) is None

    monkeypatch.setattr(
        service.control_plane,
        "ensure_stage_inventory",
        original_ensure,
    )
    service.resume(created.task_id)
    inventory = service.control_plane.get_stage_inventory(created.task_id)
    assert inventory is not None
    assert service.control_plane.get_task_state(created.task_id).status == (
        TaskStatus.READY
    )
    service.resume(created.task_id)
    assert service.control_plane.get_stage_inventory(created.task_id) == inventory


def test_attach_initializes_frozen_task_state_without_mapping_legacy_state(tmp_path):
    tasks, service, _, _ = _system(tmp_path)
    legacy = tasks.create(
        CreateTaskRequest(
            project_id="alpha",
            title="Attach an existing Task",
            kind="custom",
        )
    )
    tasks.transition(
        legacy.task_id,
        TaskState.REQUIREMENTS,
        actor="legacy-test",
        expected_version=legacy.version,
    )

    service.attach(
        legacy.task_id,
        total_token_budget=30_000,
        total_cost_budget_usd=12,
    )

    frozen = service.control_plane.get_task_state(legacy.task_id)
    assert frozen.status == TaskStatus.READY
    assert frozen.version == 2
    inventory = service.control_plane.get_stage_inventory(legacy.task_id)
    assert inventory is not None
    assert inventory.contract is None
    assert tasks.get(legacy.task_id).state == TaskState.REQUIREMENTS


@pytest.mark.asyncio
async def test_three_runtime_loop_records_usage_and_requires_human_approval(tmp_path):
    tasks, service, runner, task = _system(tmp_path)
    status = await service.run_until_blocked(task.task_id)
    assert status.plan.state == PlanState.AWAITING_APPROVAL
    assert [stage.state for stage in status.stages] == [StageState.PASSED] * 3
    assert [run.adapter for run in status.runs] == ["codex", "claude", "kiro"]
    assert all(run.routing_policy is None for run in status.runs)
    assert all(
        "routing_policy" not in run.model_dump(mode="json")
        for run in status.runs
    )
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


def test_semantic_parser_allows_one_format_only_wrapped_result():
    wrapped = (
        "Review note for {project_id}; no state change.\n```json\n"
        + PASS
        + "\n```"
    )
    parsed = TaskOrchestrationService._parse_semantic(wrapped)
    assert parsed is not None
    assert parsed.status.value == "pass"


def test_semantic_parser_fails_closed_on_multiple_valid_results():
    assert TaskOrchestrationService._parse_semantic(f"{PASS}\n{PASS}") is None
    noisy = "\n".join('{"noise":' for _ in range(101)) + PASS
    assert TaskOrchestrationService._parse_semantic(noisy) is None


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
async def test_process_start_failure_settles_exact_zero_tokens(tmp_path):
    _, service, _, task = _system(
        tmp_path,
        [RuntimeResult(
            None,
            "",
            "process start failed: FileNotFoundError",
            process_started=False,
        )],
    )
    run = await service.run_next(task.task_id)
    status = service.status(task.task_id)

    assert run.state == RunState.FAILED
    assert run.pid is None
    assert run.token_used == 0
    assert run.token_measurement == Measurement.EXACT
    assert status.tokens_used == 0
    assert status.token_measurement == Measurement.EXACT
    assert status.tokens_remaining == status.plan.total_token_budget
    assert status.usage[-1].tokens == 0
    assert status.usage[-1].token_measurement == Measurement.EXACT


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


@pytest.mark.asyncio
async def test_human_decisions_are_versioned_idempotent_and_enter_retry_context(tmp_path):
    blocked = (
        '{"status":"blocked","summary":"policy missing","findings":["need policy"],'
        '"recommended_next_action":"ask human"}'
    )
    tasks, service, runner, task = _system(
        tmp_path,
        [RuntimeResult(0, blocked, ""), RuntimeResult(0, PASS, "")],
    )
    await service.run_next(task.task_id)

    first = service.decide(
        task.task_id,
        decision_key="inbound_authorization_policy",
        decision_value="Use fail-closed Bearer authentication",
        rationale="Approved API boundary",
        actor="owner",
    )
    duplicate = service.decide(
        task.task_id,
        decision_key="inbound_authorization_policy",
        decision_value="Use fail-closed Bearer authentication",
        rationale="Approved API boundary",
        actor="owner",
    )
    revised = service.decide(
        task.task_id,
        decision_key="inbound_authorization_policy",
        decision_value="Use access-policy-v1 fail-closed Bearer authentication",
        rationale="Pins the checked-in policy version",
        actor="owner",
    )

    assert duplicate.decision_id == first.decision_id
    assert revised.version == 2
    assert [item.version for item in service.status(task.task_id).decisions] == [1, 2]
    decision_events = [
        event for event in tasks.events(task.task_id)
        if event.event_type == "orchestration.decision_recorded"
    ]
    assert len(decision_events) == 2
    assert decision_events[-1].payload["decision_sha256"] == revised.decision_sha256

    service.retry(task.task_id, "solution_design")
    await service.run_next(task.task_id)
    retry_prompt = runner.prompts[-1]
    assert "access-policy-v1 fail-closed Bearer authentication" in retry_prompt
    assert '"version":2' in retry_prompt
    assert '"version":1' not in retry_prompt


def test_human_decisions_require_a_blocked_stage_and_bounded_active_context(tmp_path):
    _, service, _, task = _system(tmp_path)
    with pytest.raises(OrchestrationConflictError, match="only while the plan is blocked"):
        service.decide(
            task.task_id,
            decision_key="policy",
            decision_value="not yet allowed",
            rationale="plan is active",
        )

    status = service.status(task.task_id)
    run = service.store.claim_current_stage(
        task.task_id,
        prompt_sha256="e" * 64,
        operation_key=f"{status.plan.plan_id}:{status.plan.current_stage_key}:decision-test",
    )
    service.store.mark_interrupted(run.run_id, reason="block for decision test")
    service.decide(
        task.task_id,
        decision_key="large_policy_one",
        decision_value="a" * 1_000,
        rationale="b" * 500,
    )
    with pytest.raises(OrchestrationValidationError, match="prompt allocation"):
        service.decide(
            task.task_id,
            decision_key="large_policy_two",
            decision_value="c" * 1_000,
            rationale="d" * 500,
        )


@pytest.mark.asyncio
async def test_human_decision_text_is_redacted_before_persistence(tmp_path):
    secret = "sk-abcdefghijklmnopqrst"
    tasks, service, _, task = _system(
        tmp_path,
        [RuntimeResult(0, '{"status":"blocked","summary":"x","findings":["x"],'
                          '"recommended_next_action":"ask"}', "")],
    )
    await service.run_next(task.task_id)
    decision = service.decide(
        task.task_id,
        decision_key="credential_policy",
        decision_value=f"access_token={secret}",
        rationale=f"password={secret}",
    )
    serialized = json.dumps(decision.model_dump(mode="json"))
    events = json.dumps([event.payload for event in tasks.events(task.task_id)])
    assert secret not in serialized
    assert secret not in events
    assert "[REDACTED]" in serialized


@pytest.mark.asyncio
async def test_cli_records_a_human_decision_for_a_blocked_task(tmp_path, monkeypatch, capsys):
    blocked = (
        '{"status":"blocked","summary":"policy missing","findings":["need policy"],'
        '"recommended_next_action":"ask human"}'
    )
    _, service, _, task = _system(tmp_path, [RuntimeResult(0, blocked, "")])
    await service.run_next(task.task_id)
    monkeypatch.setattr(orchestration_cli, "build_service", lambda: service)

    assert orchestration_cli.main([
        "decide",
        task.task_id,
        "invalidation_scope",
        "--value",
        "defer repository-wide invalidation",
        "--reason",
        "keep the first API slice task-scoped",
    ]) == 0
    output = capsys.readouterr().out
    assert '"decision_key": "invalidation_scope"' in output
    assert "Decisions:" in output


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
    assert status.runs[0].token_used is None
    assert status.runs[0].token_measurement == Measurement.UNAVAILABLE
    assert status.tokens_used is None
    assert status.token_measurement == Measurement.UNAVAILABLE
    assert status.tokens_remaining is None


def test_cli_maps_missing_task_to_a_bounded_error(tmp_path, monkeypatch, capsys):
    _, service, _, _ = _system(tmp_path)
    monkeypatch.setattr(orchestration_cli, "build_service", lambda: service)

    assert orchestration_cli.main(["attach", "missing-task"]) == 2
    assert "error: missing-task" in capsys.readouterr().out


def test_cli_configures_safe_output_for_windows_runtime_results(monkeypatch):
    class FakeStream:
        def __init__(self):
            self.calls = []

        def reconfigure(self, **kwargs):
            self.calls.append(kwargs)

    stdout = FakeStream()
    stderr = FakeStream()
    monkeypatch.setattr(orchestration_cli.sys, "stdout", stdout)
    monkeypatch.setattr(orchestration_cli.sys, "stderr", stderr)

    orchestration_cli._configure_safe_output()

    assert stdout.calls == [{"errors": "backslashreplace"}]
    assert stderr.calls == [{"errors": "backslashreplace"}]


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


@pytest.mark.asyncio
async def test_unavailable_settlements_consume_reserved_budget_on_retry(tmp_path):
    _, service, runner, task = _system(
        tmp_path,
        [RuntimeInterrupted("first interrupted"), RuntimeInterrupted("second interrupted")],
        tokens=3_000,
    )

    await service.run_next(task.task_id)
    service.retry(task.task_id, "solution_design")
    await service.run_next(task.task_id)
    service.retry(task.task_id, "solution_design")

    with pytest.raises(OrchestrationConflictError, match="Token budget is exhausted"):
        await service.run_next(task.task_id)

    status = service.status(task.task_id)
    assert len(status.runs) == 2
    assert len(runner.prompts) == 2
    assert all(run.token_measurement == Measurement.UNAVAILABLE for run in status.runs)
