"""Run an isolated, deterministic acceptance of Agora's formal Task mainline.

This launches real local child processes but no AI or provider. See
docs/architecture/deterministic-task-acceptance-v1.md.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from agora.orchestration.contracts import load_task_contract
from agora.orchestration.models import PlanState, RunState, StageState
from agora.orchestration.protocol_context import resolve_git_revision
from agora.orchestration.runtime import ReadOnlyCliRunner, RuntimeCommand
from agora.orchestration.service import TaskOrchestrationService
from agora.projects import ProjectRegistry
from agora.protocol.hashing import seal_model_payload
from agora.protocol.models import ContextPack, HandoffPack
from agora.protocol.state_machines import GateStatus, TaskStatus
from agora.tasks.models import utc_now
from agora.tasks.store import TaskStore


CONTRACT_PATH = (
    REPOSITORY_ROOT
    / "docs"
    / "examples"
    / "bounded-control-plane-api-task-contract.json"
)
RUNTIME_MARKER = "AGORA_DETERMINISTIC_ACCEPTANCE_RUNTIME"


def _progress(message: str) -> None:
    print(f"[agora-acceptance] {message}", file=sys.stderr, flush=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the formal Agora Task mainline with isolated data and a "
            "deterministic non-AI runtime"
        )
    )
    parser.add_argument(
        "--temp-root",
        type=Path,
        help="Existing directory under which the script creates and removes its workspace",
    )
    parser.add_argument(
        "--runtime-output",
        help=argparse.SUPPRESS,
    )
    return parser


def _context_from_prompt(prompt: str) -> ContextPack:
    prefix = "SEALED CONTEXT PACK (canonical JSON):\n"
    suffix = "\nEND SEALED CONTEXT PACK"
    if prefix not in prompt or suffix not in prompt:
        raise ValueError("acceptance runtime did not receive a sealed Context Pack")
    value = prompt.split(prefix, 1)[1].split(suffix, 1)[0]
    return ContextPack.model_validate_json(value)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _runtime_handoff(prompt: str) -> int:
    if os.environ.get(RUNTIME_MARKER) != "1":
        raise RuntimeError("deterministic runtime is restricted to the acceptance harness")

    context = _context_from_prompt(prompt)
    expected = {
        "AGORA_TASK_ID": context.task_id,
        "AGORA_RUN_ID": context.run_id,
        "AGORA_STAGE_KEY": context.stage_key,
        "AGORA_ORCHESTRATION_MODE": "read_only_planning",
    }
    for name, value in expected.items():
        if os.environ.get(name) != value:
            raise RuntimeError(f"acceptance runtime binding mismatch: {name}")

    contract = load_task_contract(CONTRACT_PATH)
    stage = next(
        item for item in contract.workflow if item.stage_key == context.stage_key
    )
    contract_runtime = next(
        item.runtime for item in contract.roles if item.role_id == stage.role_id
    )
    routing_entries = [
        item for item in context.policies if item.source_ref.startswith("routing-policy:")
    ]
    if len(routing_entries) != 1:
        raise RuntimeError("acceptance Context must contain one routing policy")
    routing_policy = json.loads(routing_entries[0].content)
    runtime = routing_policy["route"]["pinned_runtime"]
    if runtime != contract_runtime:
        raise RuntimeError("acceptance routing pin does not match the Task contract")
    if len(context.required_outputs) != 1:
        raise RuntimeError("acceptance contract must require exactly one Stage output")

    revision = resolve_git_revision(Path.cwd(), repository_id=context.project_id)
    content = json.dumps(
        {
            "acceptance_mode": "deterministic_non_ai",
            "stage": stage.stage_key,
            "result": "formal control-plane path exercised",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    output = context.required_outputs[0]
    existing_versions = [
        item.version
        for item in context.input_artifacts
        if item.artifact_id == output.output_id
    ]
    producer = {
        "runtime": runtime,
        "run_id": context.run_id,
        "stage_key": context.stage_key,
    }
    artifact = {
        "schema_version": "1.0",
        "artifact_id": output.output_id,
        "project_id": context.project_id,
        "task_id": context.task_id,
        "stage_key": context.stage_key,
        "producer": producer,
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
    evidence_requirements = {
        item.requirement_id: item for item in stage.required_evidence
    }
    evidence = [
        {
            "schema_version": "1.0",
            "evidence_id": f"acceptance-evidence:{context.run_id}:{requirement.requirement_id}",
            "project_id": context.project_id,
            "task_id": context.task_id,
            "stage_key": context.stage_key,
            "producer": producer,
            "repository_id": revision.repository_id,
            "ref": revision.ref,
            "commit_sha": revision.commit_sha,
            "requirement_id": requirement.requirement_id,
            "kind": evidence_requirements[requirement.requirement_id].kind,
            "status": "passed",
            "artifact_versions": [artifact_ref],
            "summary": evidence_requirements[requirement.requirement_id].description,
            "observed_at": utc_now(),
            "details": {"acceptance_mode": "deterministic_non_ai"},
        }
        for requirement in stage.gate_requirements
    ]
    payload = {
        "schema_version": "1.0",
        "pack_id": f"acceptance-handoff:{context.run_id}",
        "project_id": context.project_id,
        "task_id": context.task_id,
        "stage_key": context.stage_key,
        "run_id": context.run_id,
        "producer": producer,
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
        "suggested_next_action": "Await authoritative Agora routing.",
    }
    sealed = seal_model_payload(HandoffPack, payload)
    print(json.dumps(sealed, ensure_ascii=False, separators=(",", ":")))
    return 0


def _initialize_git_project(root: Path) -> None:
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "acceptance@agora.invalid")
    _git(root, "config", "user.name", "Agora Acceptance")
    (root / "README.md").write_text(
        "# Agora deterministic acceptance project\n",
        encoding="utf-8",
    )
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "Create acceptance revision")


def _runtime_registry(script: Path) -> dict[str, RuntimeCommand]:
    return {
        adapter: RuntimeCommand(
            adapter=adapter,
            command_template=(
                sys.executable,
                str(script),
                "--runtime-output",
                "{prompt}",
            ),
        )
        for adapter in ("codex", "claude", "kiro")
    }


async def _accept(temp_root: Path | None) -> dict[str, object]:
    if temp_root is not None:
        temp_root = temp_root.resolve()
        if not temp_root.is_dir():
            raise ValueError("--temp-root must name an existing directory")

    run_path: Path | None = None
    result: dict[str, object]
    with tempfile.TemporaryDirectory(
        prefix="agora-task-acceptance-",
        dir=str(temp_root) if temp_root is not None else None,
    ) as temporary:
        run_path = Path(temporary)
        _progress("created isolated workspace")
        project_root = run_path / "project"
        _initialize_git_project(project_root)
        _progress("created clean Git revision")
        database = run_path / "agora.db"
        project_id = "acceptance"
        projects = ProjectRegistry(
            {
                "projects": {
                    "registry_path": str(run_path / "projects.yaml"),
                    "default": project_id,
                    "projects": {
                        project_id: {
                            "name": "Deterministic Acceptance",
                            "root": str(project_root),
                            "workspaces": {},
                        }
                    },
                }
            },
            project_root=run_path,
        )
        tasks = TaskStore(database)
        runtimes = _runtime_registry(Path(__file__).resolve())
        service = TaskOrchestrationService(
            tasks,
            projects,
            runtimes,
            runner=ReadOnlyCliRunner(network_mode="direct"),
            timeout_seconds=10,
        )
        contract = load_task_contract(CONTRACT_PATH)
        task = service.create(
            project_id=project_id,
            title=contract.title,
            description=contract.goal,
            total_token_budget=30_000,
            total_cost_budget_usd=12,
            contract=contract,
        )
        _progress(f"created formal Task {task.task_id}")

        previous_marker = os.environ.get(RUNTIME_MARKER)
        os.environ[RUNTIME_MARKER] = "1"
        try:
            for _ in range(len(contract.workflow) + 1):
                status = service.status(task.task_id)
                if status.plan.state != PlanState.ACTIVE:
                    break
                current_stage_key = status.plan.current_stage_key or "unavailable"
                _progress(f"launching Stage {current_stage_key}")
                await service.run_next(task.task_id, protocol_v1=True)
                _progress(f"settled Stage {current_stage_key}")
            else:
                status = service.status(task.task_id)
                raise RuntimeError(
                    "acceptance execution did not converge after the bounded "
                    f"run count; plan_state={status.plan.state.value}"
                )
        finally:
            if previous_marker is None:
                os.environ.pop(RUNTIME_MARKER, None)
            else:
                os.environ[RUNTIME_MARKER] = previous_marker

        before = service.unified_status(task.task_id)
        if status.plan.state != PlanState.AWAITING_APPROVAL:
            raise RuntimeError("acceptance Task did not stop at human approval")
        if [item.state for item in status.stages] != [StageState.PASSED] * 3:
            raise RuntimeError("acceptance Stages did not all pass")
        if [item.state for item in status.runs] != [RunState.PASSED] * 3:
            raise RuntimeError("acceptance Runs did not all pass")
        if before.task_state != TaskStatus.NEEDS_REVIEW:
            raise RuntimeError("formal Task did not enter needs_review")
        if [item.kind for item in before.required_human_actions] != ["plan_approval"]:
            raise RuntimeError("formal Task did not expose exactly plan approval")
        if any(
            stage.gate is None or stage.gate.status != GateStatus.PASSED
            for stage in before.stages
        ):
            raise RuntimeError("one or more formal Stage Gates did not pass")

        service.approve(
            task.task_id,
            actor="acceptance-user",
            reason="Deterministic formal acceptance reviewed",
        )
        _progress("recorded explicit human approval")

        reopened = TaskOrchestrationService(
            TaskStore(database),
            projects,
            runtimes,
            runner=ReadOnlyCliRunner(network_mode="direct"),
            timeout_seconds=10,
        )
        after = reopened.unified_status(task.task_id)
        _progress("reopened SQLite and verified authoritative projection")
        if after.task_state != TaskStatus.COMPLETED:
            raise RuntimeError("reopened authoritative Task is not completed")
        if after.required_human_actions:
            raise RuntimeError("completed Task still requires a human action")
        if after.task_state_lifecycle != "control_plane_managed":
            raise RuntimeError("completed Task lost Control Plane lifecycle authority")

        result = {
            "schema_version": "1.0",
            "acceptance_mode": "deterministic_non_ai",
            "provider_or_model_called": False,
            "project_id": project_id,
            "task_id": task.task_id,
            "task_state_before_approval": before.task_state.value,
            "task_state_after_reopen": after.task_state.value,
            "plan_state_after_approval": after.plan.state.value,
            "task_state_lifecycle": after.task_state_lifecycle,
            "stage_states": [item.operational_state.value for item in after.stages],
            "gate_states": [item.gate.status.value for item in after.stages],
            "run_runtimes": [item.runtime for item in after.runs],
            "run_exit_codes": [item.process_exit_code for item in after.runs],
            "artifact_count": len(after.artifacts),
            "evidence_count": len(after.evidence),
            "required_human_actions_before_approval": [
                item.kind for item in before.required_human_actions
            ],
            "required_human_actions_after_reopen": [
                item.kind for item in after.required_human_actions
            ],
            "persisted_reopen_verified": True,
        }

    assert run_path is not None
    result["temporary_workspace_removed"] = not run_path.exists()
    if not result["temporary_workspace_removed"]:
        raise RuntimeError("acceptance temporary workspace was not removed")
    _progress("removed isolated workspace")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.runtime_output is not None:
        return _runtime_handoff(args.runtime_output)
    result = asyncio.run(_accept(args.temp_root))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
