"""Non-interactive unified task entry point for the orchestration foundation."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Sequence

from agora.config.settings import get_config
from agora.control_plane.store import (
    ControlPlaneConflictError,
    ControlPlaneNotFoundError,
    ControlPlaneValidationError,
)
from agora.projects import ProjectRegistry
from agora.tasks.models import TaskRisk
from agora.tasks.store import TaskNotFoundError, TaskStore

from .contracts import load_task_contract
from .models import Measurement, PlanState
from .runtime import ReadOnlyCliRunner, build_runtime_registry
from .service import TaskOrchestrationService
from .store import (
    OrchestrationConflictError,
    OrchestrationNotFoundError,
    OrchestrationValidationError,
)


def build_service(config: dict | None = None) -> TaskOrchestrationService:
    cfg = config or get_config()
    data_dir = Path(cfg.get("memory", {}).get("data_dir", "./data"))
    db_path = cfg.get("control_plane", {}).get("db_path", data_dir / "agora.db")
    orchestration = cfg.get("orchestration", {})
    return TaskOrchestrationService(
        TaskStore(db_path),
        ProjectRegistry(cfg),
        build_runtime_registry(cfg),
        runner=ReadOnlyCliRunner(
            network_mode=str(orchestration.get("network_mode", "system")),
        ),
        timeout_seconds=int(orchestration.get("timeout_seconds", 600)),
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="agora task",
        description="Task-scoped, methodology-driven orchestration foundation",
    )
    commands = root.add_subparsers(dest="command", required=True)

    start = commands.add_parser("start", help="Create a Task and attach the provisional method")
    start.add_argument("title", nargs="?")
    start.add_argument("--description", default="")
    start.add_argument("--contract", type=Path)
    start.add_argument("--project")
    start.add_argument("--risk", choices=[item.value for item in TaskRisk], default="medium")
    start.add_argument("--tokens", type=int, default=30_000)
    start.add_argument("--cost-usd", type=float)
    start.add_argument("--run", action="store_true", help="Run all three planning/review stages")
    start.add_argument(
        "--protocol-v1",
        action="store_true",
        help="Use sealed Context/Handoff Packs and formal Control Plane Gates",
    )

    attach = commands.add_parser("attach", help="Attach the provisional method to an existing Task")
    attach.add_argument("task_id")
    attach.add_argument("--tokens", type=int, default=30_000)
    attach.add_argument("--cost-usd", type=float)

    decide = commands.add_parser(
        "decide",
        help="Record an explicit human decision while a plan is blocked",
    )
    decide.add_argument("task_id")
    decide.add_argument("decision_key")
    decide.add_argument("--value", required=True)
    decide.add_argument("--reason", required=True)
    decide.add_argument("--actor", default="user")

    for name, help_text in (
        ("next", "Run the next safe planning/review stage"),
        ("run", "Run stages until blocked or awaiting human approval"),
        ("status", "Show authoritative orchestration state and usage"),
        ("resume", "Reconcile an interrupted CLI run without duplicate dispatch"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("task_id")
        if name in {"next", "run"}:
            command.add_argument(
                "--protocol-v1",
                action="store_true",
                help="Use sealed Context/Handoff Packs and formal Control Plane Gates",
            )
        if name == "status":
            command.add_argument("--json", action="store_true", dest="as_json")
            command.add_argument(
                "--protocol-v1",
                action="store_true",
                help="Show the unified formal Task projection",
            )
            command.add_argument("--limit", type=int, default=100)
            command.add_argument("--offset", type=int, default=0)

    retry = commands.add_parser("retry", help="Retry a blocked stage when budget remains")
    retry.add_argument("task_id")
    retry.add_argument("stage_key")
    retry.add_argument("--protocol-v1", action="store_true")

    approve = commands.add_parser("approve", help="Human approval after all three stages pass")
    approve.add_argument("task_id")
    approve.add_argument("--reason", required=True)
    approve.add_argument("--actor", default="user")
    return root


def main(argv: Sequence[str] | None = None) -> int:
    _configure_safe_output()
    args = parser().parse_args(argv)
    service = build_service()
    try:
        if args.command == "start":
            contract = load_task_contract(args.contract) if args.contract else None
            if contract and args.title:
                raise ValueError("Provide either a title or --contract, not both")
            if contract and args.description:
                raise ValueError("--description cannot be combined with --contract")
            if not contract and not args.title:
                raise ValueError("A title or --contract is required")
            if args.protocol_v1 and not args.run:
                raise ValueError("--protocol-v1 on start requires --run")
            if args.protocol_v1 and not contract:
                raise ValueError("--protocol-v1 requires --contract")
            project_id = args.project or service.projects.current_project_id()
            task = service.create(
                project_id=project_id,
                title=contract.title if contract else args.title,
                description=contract.goal if contract else args.description,
                total_token_budget=args.tokens,
                total_cost_budget_usd=args.cost_usd,
                risk=TaskRisk(args.risk),
                contract=contract,
            )
            print(task.task_id)
            if args.run:
                status = asyncio.run(
                    service.run_until_blocked(
                        task.task_id,
                        protocol_v1=args.protocol_v1,
                    )
                )
                _print_status(status)
                return 0 if status.plan.state == PlanState.AWAITING_APPROVAL else 2
            _print_status(service.status(task.task_id))
            return 0
        if args.command == "attach":
            service.attach(
                args.task_id,
                total_token_budget=args.tokens,
                total_cost_budget_usd=args.cost_usd,
            )
            _print_status(service.status(args.task_id))
            return 0
        if args.command == "decide":
            decision = service.decide(
                args.task_id,
                decision_key=args.decision_key,
                decision_value=args.value,
                rationale=args.reason,
                actor=args.actor,
            )
            print(json.dumps(decision.model_dump(mode="json"), ensure_ascii=False, indent=2))
            _print_status(service.status(args.task_id))
            return 0
        if args.command == "next":
            run = asyncio.run(
                service.run_next(args.task_id, protocol_v1=args.protocol_v1)
            )
            print(json.dumps(run.model_dump(mode="json"), ensure_ascii=False, indent=2))
            _print_status(service.status(args.task_id))
            return 0 if run.state.value == "passed" else 2
        if args.command == "run":
            status = asyncio.run(
                service.run_until_blocked(
                    args.task_id,
                    protocol_v1=args.protocol_v1,
                )
            )
            _print_status(status)
            return 0 if status.plan.state == PlanState.AWAITING_APPROVAL else 2
        if args.command == "status":
            status = (
                service.unified_status(
                    args.task_id,
                    history_limit=args.limit,
                    history_offset=args.offset,
                )
                if args.protocol_v1
                else service.status(args.task_id)
            )
            if args.as_json:
                print(json.dumps(status.model_dump(mode="json"), ensure_ascii=False, indent=2))
            elif args.protocol_v1:
                _print_unified_status(status)
            else:
                _print_status(status)
            return 0
        if args.command == "resume":
            _print_status(service.resume(args.task_id))
            return 0
        if args.command == "retry":
            if args.protocol_v1:
                service.retry_protocol(args.task_id, args.stage_key, actor="user")
            else:
                service.retry(args.task_id, args.stage_key)
            _print_status(service.status(args.task_id))
            return 0
        if args.command == "approve":
            service.approve(args.task_id, actor=args.actor, reason=args.reason)
            _print_status(service.status(args.task_id))
            return 0
    except (
        KeyError,
        ControlPlaneConflictError,
        ControlPlaneNotFoundError,
        ControlPlaneValidationError,
        OrchestrationConflictError,
        OrchestrationNotFoundError,
        OrchestrationValidationError,
        TaskNotFoundError,
        ValueError,
    ) as exc:
        print(f"error: {exc}")
        return 2
    return 2


def _configure_safe_output() -> None:
    """Avoid UnicodeEncodeError without changing the terminal's byte encoding."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors="backslashreplace")


def _print_status(status) -> None:
    plan = status.plan
    provisional = " provisional" if plan.provisional else ""
    print(
        f"Plan: {plan.methodology_id}@{plan.methodology_version}{provisional} "
        f"[{plan.state.value}]"
    )
    for stage in status.stages:
        print(
            f"  {stage.sequence}. {stage.stage_key:<22} {stage.state.value:<9} "
            f"{stage.adapter:<7} tokens={stage.token_budget} attempts={stage.attempt_count}"
        )
        for blocker in stage.blockers:
            print(f"     blocker: {blocker}")
    if status.decisions:
        print("Decisions:")
        for decision in status.decisions:
            print(
                f"  {decision.decision_key}@{decision.version} by {decision.actor}: "
                f"{decision.decision_value}"
            )
    if status.token_measurement == Measurement.UNAVAILABLE:
        print(f"Tokens: reserved={status.tokens_reserved} used=unavailable remaining=unavailable")
    else:
        marker = "~" if status.token_measurement == Measurement.ESTIMATED else ""
        print(
            f"Tokens: reserved={status.tokens_reserved} used{marker}={status.tokens_used} "
            f"remaining={status.tokens_remaining}"
        )
    if status.cost_measurement.value == "unavailable":
        print("Cost: unavailable from the native CLI outputs (never recorded as zero)")
    else:
        print(f"Cost: {status.cost_used_usd} USD ({status.cost_measurement.value})")
    print(f"Next safe action: {status.next_safe_action}")


def _print_unified_status(status) -> None:
    task_state = (
        status.task_state.value if status.task_state is not None else "unavailable"
    )
    print(
        f"Task: {status.task.title} [{task_state}] "
        f"source={status.task_state_source} legacy={status.task.state.value} "
        f"lifecycle={status.task_state_lifecycle}"
    )
    if status.task_state_unavailable_reason is not None:
        print(f"  Task state unavailable: {status.task_state_unavailable_reason}")
    print(
        f"Plan: {status.plan.methodology_id}@{status.plan.methodology_version} "
        f"[{status.plan.state.value}]"
    )
    progress = status.progress
    if progress.inventory_complete:
        print(
            f"Progress: {progress.completed_stages}/{progress.total_stages} formal Stages "
            f"completed; current={progress.current_stage_key or 'none'} "
            f"current_source={progress.current_stage_source or 'unavailable'}"
        )
    else:
        print(
            "Progress: unavailable; "
            f"{progress.inventory_unavailable_reason or 'Stage inventory is unavailable.'}"
        )
    for stage in status.stages:
        authoritative = (
            stage.authoritative_stage.status.value
            if stage.authoritative_stage is not None
            else "unconfigured"
        )
        gate = stage.gate.status.value if stage.gate is not None else "unconfigured"
        marker = "*" if stage.current else " "
        print(
            f"{marker} {stage.stage_key:<22} group={stage.group_key or 'unavailable'} "
            f"formal={authoritative:<23} "
            f"gate={gate:<10} runtime={stage.runtime or 'unassigned'}"
        )
    if status.runs:
        print("Runs:")
        for run in status.runs:
            semantic = (
                run.semantic_result.value
                if run.semantic_result is not None
                else "unavailable"
            )
            print(
                f"  {run.run_id} {run.runtime or 'unknown'} "
                f"state={run.operational_state.value if run.operational_state else 'formal-only'} "
                f"semantic={semantic} wait={run.wait_state.value} "
                f"elapsed={run.elapsed_seconds:.3f}s"
            )
    if status.required_human_actions:
        print("Required human actions:")
        for action in status.required_human_actions:
            print(f"  {action.kind}: {action.title} ({action.source_id})")
    budget = status.budget
    if budget.token_measurement == Measurement.UNAVAILABLE:
        token_settled = "unavailable"
        token_remaining = "unavailable"
    else:
        token_settled = str(budget.token_settled)
        token_remaining = str(budget.token_remaining)
    print(
        f"Tokens: allocated={budget.token_allocated} reserved={budget.token_reserved} "
        f"settled={token_settled} remaining={token_remaining}"
    )
    if budget.cost_measurement == Measurement.UNAVAILABLE:
        print("Cost: unavailable (never recorded as zero)")
    else:
        print(
            f"Cost: allocated={budget.cost_allocated_usd} "
            f"reserved={budget.cost_reserved_usd} settled={budget.cost_settled_usd} "
            f"remaining={budget.cost_remaining_usd}"
        )
    next_action = status.next_safe_action
    if next_action.value is not None:
        print(
            f"Next safe action: {next_action.value} "
            f"(Gate {next_action.source_gate_key})"
        )
    else:
        print(f"Next safe action: unavailable ({next_action.unavailable_reason})")
    print(f"Compatibility hint (non-authoritative): {status.compatibility_next_action}")
