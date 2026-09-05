"""Read-only Settings observations: runtime availability, workspaces, run stats.

Additive, observe-only surface for the Settings page. Nothing here writes
durable state or changes configuration; it probes the three native runtimes
(installation + `--version`), reports the configured project workspaces, and
summarizes execution runs. Subscription CLIs expose no remaining-quota API, so
this deliberately reports *runs Agora has dispatched*, never a plan balance.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator

from agora.config.settings import get_config
from agora.fsprobe import PathProbe, probe_path
from agora.tasks.router import get_project_registry, get_task_store
from agora.tasks.store import TaskStore
from agora.execution.adapters import build_adapter_registry
from agora.execution.dispatcher import ExecutionDispatcher
from agora.execution.models import RunState
from agora.execution.router import get_execution_store
from agora.execution.store import ExecutionStore
from agora.orchestration.runtime import build_runtime_registry
from agora.orchestration.runtime_auth import collect_runtime_auth
from agora.orchestration.runtime_capabilities import (
    collect_native_runtime_capabilities,
)
from agora.projects import ProjectRegistry
from agora.workspaces.models import ProvisionRequest
from agora.workspaces.provisioner import WorkspaceProvisioner

router = APIRouter(tags=["settings"])


def _provisioner_for(registry: ProjectRegistry) -> WorkspaceProvisioner:
    """A provisioner sharing the caller's registry view (so a just-created
    project is visible) and the configured workspace allowlist."""
    execution = get_config().get("execution", {})
    roots = []
    for value in execution.get("allowed_workspace_roots", []):
        root = Path(value).expanduser()
        roots.append((root if root.is_absolute() else registry.project_root / root).resolve())
    return WorkspaceProvisioner(registry, allowed_workspace_roots=roots)


async def _provision_project_workspaces(
    registry: ProjectRegistry, project: Any
) -> dict[str, Any]:
    """Provision every adapter workspace as a linked worktree at project
    creation (the spec's explicit provisioning action). Best-effort: report per
    workspace; nothing here (including provisioner construction) rolls back the
    already-created project or turns into a 500."""
    try:
        provisioner = _provisioner_for(registry)
        await provisioner.ensure_source_repo(project.project_id)
    except Exception as exc:  # bad config, git unavailable, permission, etc.
        return {"ok": False, "error": type(exc).__name__, "workspaces": {}}
    workspaces: dict[str, str] = {}
    for adapter in sorted(project.workspaces):
        try:
            result = await provisioner.provision(
                ProvisionRequest(project_id=project.project_id, adapter=adapter)
            )
            workspaces[adapter] = result.status.state.value
        except Exception as exc:  # any provisioning failure is per-workspace, never a 500
            workspaces[adapter] = f"error:{type(exc).__name__}"
    return {"ok": all(v == "ready" for v in workspaces.values()), "workspaces": workspaces}


class SetProjectsBaseRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


_AGENT_PATTERN = r"^(codex|claude|kiro)$"


class CreateProjectRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    name: str | None = Field(default=None, max_length=200)
    worker_agent: str | None = Field(default=None, pattern=_AGENT_PATTERN)
    reviewer_agent: str | None = Field(default=None, pattern=_AGENT_PATTERN)


class SetProjectAgentsRequest(BaseModel):
    worker_agent: str = Field(pattern=_AGENT_PATTERN)
    reviewer_agent: str = Field(pattern=_AGENT_PATTERN)


class SetCharterRequest(BaseModel):
    goal: str = Field(default="", max_length=2000)
    non_goals: list[str] = Field(default_factory=list, max_length=50)
    success_criteria: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("non_goals", "success_criteria")
    @classmethod
    def _no_blank_items(cls, values: list[str]) -> list[str]:
        cleaned = [v.strip() for v in values if v.strip()]
        for v in cleaned:
            if len(v) > 500:
                raise ValueError("each item must be at most 500 characters")
        return cleaned

# One page of runs to summarize. Beyond this the summary is marked truncated
# rather than paging the whole history on every Settings load.
_RUN_SCAN_LIMIT = 500

# Dashboard projection bounds. The rollup marks itself `truncated` past the task
# cap rather than paging every task's whole run history on each open.
_DASHBOARD_TASK_LIMIT = 500
_DASHBOARD_RUN_LIMIT = 200
# Bounded like the dashboard, but scoped to one day: the recap resolves its day
# with an indexed lookup first, then reads only that day, so cost does not grow
# with a task's whole history. Past these caps it reports itself partial.
_RECAP_RUN_LIMIT = 100
_RECAP_EVENT_LIMIT = 500


@router.get("/runtime-capabilities")
async def runtime_capabilities() -> dict[str, Any]:
    """Live-probe the three native runtimes: installed? which version? capability?"""
    config = get_config()
    runtimes = build_runtime_registry(config)
    observation = await collect_native_runtime_capabilities(runtimes)
    # An installed CLI can still be signed out; probe sign-in status too.
    auth = await collect_runtime_auth([item.adapter for item in observation.adapters])
    # Which adapters yield parseable per-run token usage (claude CLI today).
    token_capturable = set(ExecutionDispatcher._USAGE_RESULT_FORMATS)
    # Per-adapter token totals Agora has observed so far.
    tokens = get_execution_store().token_usage_by_adapter()

    # Merge the execution-adapter capability declaration (how attention/approval
    # is delivered) onto each probed adapter, keyed by adapter name.
    try:
        exec_caps = {
            adapter.name: adapter.capability()
            for adapter in build_adapter_registry(config).values()
        }
    except ValueError:
        exec_caps = {}

    adapters = []
    for item in observation.adapters:
        cap = exec_caps.get(item.adapter)
        auth_probe = auth.get(item.adapter)
        adapters.append(
            {
                "adapter": item.adapter,
                "provider": item.provider,
                "installation_status": item.installation_status,
                "version": item.version,
                "version_status": item.version_status,
                "declared_capabilities": list(item.declared_capabilities),
                "execution_mode": cap.execution_mode if cap else None,
                "attention_mode": cap.attention_mode if cap else None,
                "supports_tool_approval": cap.supports_tool_approval if cap else None,
                "detail": cap.detail if cap else None,
                "authenticated": auth_probe.authenticated if auth_probe else None,
                "auth_method": auth_probe.method if auth_probe else None,
                "auth_check_status": auth_probe.check_status if auth_probe else "not_configured",
                "token_capture": "supported" if item.adapter in token_capturable else "unsupported",
                "tokens_used": tokens.get(item.adapter, {}).get("total_tokens", 0),
            }
        )
    adapters.sort(key=lambda entry: entry["adapter"])
    return {
        "platform": observation.platform,
        "collected_at": observation.collected_at,
        "adapters": adapters,
    }


@router.get("/settings/overview")
def settings_overview(
    store: ExecutionStore = Depends(get_execution_store),
) -> dict[str, Any]:
    """Read-only view of where projects live, execution limits, and run stats."""
    config = get_config()
    registry = ProjectRegistry(config)

    return {
        "projects": _projects_overview(registry),
        "execution": _execution_limits(config),
        "runs": _run_stats(store),
        "control_plane": _control_plane_auth(config),
    }


@router.put("/settings/projects-base")
def set_projects_base(
    request: SetProjectsBaseRequest,
    registry: ProjectRegistry = Depends(get_project_registry),
) -> dict[str, Any]:
    """Set the single root folder under which new projects are created."""
    try:
        resolved = registry.set_projects_base(request.path)
    except (ValueError, OSError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return {"projects_base": str(resolved)}


_FOLDER_DIALOG_TIMEOUT_SECONDS = 300
# Runs a native OS folder picker in an isolated child process. tkinter cannot
# share the async server's event loop, and the dialog blocks until the local
# user chooses or cancels, so it must run out-of-process. Returns the chosen
# absolute path on stdout, or nothing when cancelled.
_FOLDER_DIALOG_SCRIPT = textwrap.dedent(
    """
    import sys
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        sys.exit(3)
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    initial = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None
    chosen = filedialog.askdirectory(initialdir=initial, title="Select the Agora projects root")
    root.destroy()
    sys.stdout.write(chosen or "")
    """
)


@router.post("/settings/pick-folder")
async def pick_folder(
    registry: ProjectRegistry = Depends(get_project_registry),
) -> dict[str, Any]:
    """Open the host's native folder picker (local desktop only) and return the path."""
    initial = str(registry.projects_base())
    try:
        path = await run_in_threadpool(_run_folder_dialog, initial)
    except Exception:
        return {"path": None, "available": False}
    return {"path": path or None, "available": True}


def _run_folder_dialog(initial: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", _FOLDER_DIALOG_SCRIPT, initial],
        capture_output=True,
        text=True,
        timeout=_FOLDER_DIALOG_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError("folder dialog unavailable")
    return result.stdout.strip()


@router.get("/projects")
def list_projects(
    registry: ProjectRegistry = Depends(get_project_registry),
) -> dict[str, Any]:
    """Registered projects (independent of whether they have tasks yet)."""
    return {
        "current": registry.current_project_id(),
        "projects_base": str(registry.projects_base()),
        "import_status": registry.import_status(),
        "items": [
            {
                "project_id": pid,
                "name": project.name,
                "root": str(project.root),
                "worker_agent": project.worker_agent,
                "reviewer_agent": project.reviewer_agent,
                "charter": _charter_view(project.charter),
            }
            for pid, project in sorted(registry.list_projects().items())
        ],
    }


@router.post("/projects", status_code=status.HTTP_201_CREATED)
async def create_project(
    request: CreateProjectRequest,
    registry: ProjectRegistry = Depends(get_project_registry),
) -> dict[str, Any]:
    """Create a new project as a subfolder of the configured projects root, then
    provision its agent workspaces as isolated linked Git worktrees."""
    if request.project_id in registry.list_projects():
        raise HTTPException(status.HTTP_409_CONFLICT, f"Project already exists: {request.project_id}")
    try:
        project = await run_in_threadpool(
            registry.create_project,
            request.project_id,
            request.name,
            worker_agent=request.worker_agent,
            reviewer_agent=request.reviewer_agent,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    except OSError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Could not create project folder: {type(exc).__name__}") from None
    summary = _project_summary(project)
    summary["provisioning"] = await _provision_project_workspaces(registry, project)
    return summary


@router.put("/projects/{project_id}/agents")
def set_project_agents(
    project_id: str,
    request: SetProjectAgentsRequest,
    registry: ProjectRegistry = Depends(get_project_registry),
) -> dict[str, Any]:
    """Update which agent develops vs reviews for this project."""
    try:
        project = registry.set_project_agents(
            project_id,
            worker_agent=request.worker_agent,
            reviewer_agent=request.reviewer_agent,
        )
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown project: {project_id}") from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return _project_summary(project)


@router.get("/projects/{project_id}/dashboard")
def project_dashboard(
    project_id: str,
    registry: ProjectRegistry = Depends(get_project_registry),
    task_store: TaskStore = Depends(get_task_store),
    exec_store: ExecutionStore = Depends(get_execution_store),
) -> dict[str, Any]:
    """The project cockpit projection: charter, each task with its acceptance
    criteria + progress vs verified-completion, and the project-wide rollup."""
    from agora.execution.dispatcher import ROLE_KEY, ROLE_REVIEW, ROLE_WORKER
    from agora.execution.models import RunState
    from agora.projects.cr_log import read_cr_log
    from agora.projects.progress_snapshot import read_progress_snapshot
    from agora.requirements.models import SpecState
    from agora.requirements.store import RequirementStore
    from agora.tasks.models import TaskState
    from agora.tasks.next_action import (
        has_pending_clarifications,
        project_next_action,
        task_next_action,
    )
    from agora.tasks.progress import project_metrics, task_metrics

    try:
        project = registry.get(project_id)
    except (KeyError, LookupError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown project: {project_id}") from None

    specs = RequirementStore(task_store)

    task_views: list[dict[str, Any]] = []
    metric_list: list[dict[str, Any]] = []
    action_items: list[dict[str, Any]] = []
    states = []
    task_page = task_store.list(project_id=project_id, limit=_DASHBOARD_TASK_LIMIT)
    truncated = len(task_page) >= _DASHBOARD_TASK_LIMIT
    for task in task_page:
        has_worker = has_review = False
        # Scan only succeeded runs; stop as soon as both roles are seen.
        for summary in exec_store.list(
            task_id=task.task_id, state=RunState.SUCCEEDED, limit=_DASHBOARD_RUN_LIMIT
        ):
            run = exec_store.require(summary.run_id)
            role = str((run.result_metadata or {}).get(ROLE_KEY, ""))
            if role == ROLE_REVIEW:
                has_review = True
            elif role == ROLE_WORKER:
                has_worker = True  # exact match only; unknown/missing roles ignored
            if has_worker and has_review:
                break
        # The spec state drives the next action (draft -> approve, rejected ->
        # revise); metrics still count APPROVED only. current() sees only
        # draft/approved, so fall back to the latest spec of any state to reach
        # the rejected/superseded-only case.
        current_spec = specs.current(task.task_id)
        if current_spec is not None:
            spec_state = current_spec.state
        else:
            history = specs.list_for_task(task.task_id)
            spec_state = history[0].state if history else None
        spec = current_spec if current_spec is not None and current_spec.state == SpecState.APPROVED else None
        # A run finishing leaves the task in RUNNING until the human settles its
        # review; only an actually-active run means "wait".
        #
        # **One query, not two.** Asking for RUNNING and then for QUEUED lets a run
        # that moves `queued -> running` between the two answers be absent from both:
        # it is no longer queued when the second question is asked, and was not yet
        # running when the first one was. `has_active_run` then reads False for a task
        # that is actively running, and `task_next_action` turns that into "investigate
        # failure" or "verify review" — the board telling someone to go and look at
        # work that is still happening. `ExecutionStore.list` already takes `states`
        # for exactly this, and its docstring already names this race.
        has_active_run = task.state == TaskState.RUNNING and bool(
            exec_store.list(
                task_id=task.task_id,
                states=[RunState.RUNNING, RunState.QUEUED],
                limit=1,
            )
        )
        m = task_metrics(task.state, spec, has_worker_success=has_worker, has_review_success=has_review)
        next_action = task_next_action(
            task.state,
            spec_state=spec_state,
            pending_clarifications=has_pending_clarifications(task.clarifications),
            has_worker_success=has_worker,
            has_active_run=has_active_run,
        )
        metric_list.append(m)
        states.append(task.state)
        action_items.append({
            "task_id": task.task_id,
            "title": task.title,
            "priority": task.priority,
            "next_action": next_action,
        })
        task_views.append({
            "task_id": task.task_id,
            "title": task.title,
            "state": task.state.value,
            "status": m["status"],
            "has_spec": m["has_spec"],
            "criteria": m["criteria"],
            "produced_weight": m["produced_weight"],
            "verified_weight": m["verified_weight"],
            "total_weight": m["total_weight"],
            "next_action": next_action,
        })

    # Attention outranks every routine step, so it must never be hidden by page
    # truncation. When the page was capped, pull the project's blocked/failed
    # tasks (few by nature) directly and fold any that fell off the page into the
    # rollup. Their next action is fully determined by state, so no run/spec
    # signals are needed.
    if truncated:
        seen = {it["task_id"] for it in action_items}
        for attn_state in (TaskState.BLOCKED, TaskState.FAILED):
            for task in task_store.list(
                project_id=project_id, state=attn_state, limit=_DASHBOARD_TASK_LIMIT
            ):
                if task.task_id in seen:
                    continue
                seen.add(task.task_id)
                action_items.append({
                    "task_id": task.task_id,
                    "title": task.title,
                    "priority": task.priority,
                    "next_action": task_next_action(task.state),
                })

    metrics = project_metrics(metric_list, states)
    return {
        "project_id": project_id,
        "name": project.name,
        "charter": _charter_view(project.charter),
        "metrics": metrics,
        # The progress bar's data source, kept separate from `metrics` because it is
        # a different kind of claim: `metrics` is derived from run/task signals (and
        # `verified_pct` inherits a known defect — see projects/progress_snapshot.py),
        # while this is the human-maintained snapshot, counted. Always present in the
        # response, carrying either counts or the reason there are none, so the UI can
        # tell "no snapshot" from "could not be read" from "not reported by this
        # server" without deriving any of them from a missing field.
        "progress_snapshot": read_progress_snapshot(project.root).payload(),
        # The project's own CR Log, read from its repository rather than held here —
        # see tasks/agora/adr/0004 in claude-workspace. One file, parsed the same way
        # as the snapshot above, which is why it rides along on this response.
        # Its sibling, the decision records, deliberately does *not*: that one lists a
        # directory and opens every file in it, and this is the request a task switch
        # waits for. It has its own route below, fetched when the panel is opened.
        "cr_log": read_cr_log(project.root).payload(),
        "next_action": project_next_action(action_items, partial=truncated),
        "tasks": task_views,
        "truncated": truncated,
    }


@router.get("/tasks/{task_id}/recap")
def task_recap(
    task_id: str,
    task_store: TaskStore = Depends(get_task_store),
    exec_store: ExecutionStore = Depends(get_execution_store),
) -> dict[str, Any]:
    """What happened on this task's last active day — "where did I leave off".

    Lives with the dashboard because it is the same kind of read model: it needs
    both stores, owns all the I/O, and hands the derivation to a pure module
    (``tasks.recap``) that has no store access. Deliberately not a model call —
    "where did I leave off" is a question about recorded facts.
    """
    from agora.execution.dispatcher import ROLE_KEY, ROLE_WORKER
    from agora.tasks.localday import (
        configured_zone,
        local_day_of,
        local_today,
        next_rollover,
        now_utc,
        zone_offset_minutes,
    )
    from agora.tasks.recap import DAY_ELIGIBLE_EVENTS, last_active_day

    if task_store.get(task_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")

    # One snapshot of (zone, now) for the whole response. Every derivation below
    # is a function of the wall clock, and reading it again per derivation lets a
    # request that crosses this zone's midnight answer from two different days:
    # days_ago from the day before the boundary while next_rollover jumps to the
    # midnight *after* it, so the client waits a full extra day and keeps calling
    # yesterday "today". A config timezone change landing mid-request does the
    # same thing to the zone. Snapshotting once makes that unrepresentable.
    zone = configured_zone()
    now = now_utc()
    today = local_today(zone=zone, now=now)

    def day_of(timestamp: str | None) -> str | None:
        return local_day_of(timestamp, zone=zone)

    # Two phases, so the cost does not grow with a task's whole history: find the
    # candidate day with one indexed lookup on each side, then read only that day.
    event_day = task_store.latest_event_day(task_id, DAY_ELIGIBLE_EVENTS, zone=zone)
    run_day = exec_store.latest_finished_day(task_id, zone=zone)
    day = max([d for d in (event_day, run_day) if d], default=None)
    if day is None:
        empty = last_active_day([], [], today=today, day_of=day_of)
        empty["zone_offset_minutes"] = zone_offset_minutes(zone=zone, now=now)
        empty["next_rollover"] = next_rollover(zone=zone, now=now)
        return empty

    events = task_store.events_on_day(task_id, day, limit=_RECAP_EVENT_LIMIT, zone=zone)
    day_runs, runs_truncated = exec_store.list_finished_on_day(
        task_id, day, limit=_RECAP_RUN_LIMIT, zone=zone
    )

    runs: list[dict[str, Any]] = []
    for run in day_runs:
        snapshot = exec_store.deliverable_snapshot(run.run_id)
        runs.append({
            # Which run this is. Every run-derived recap item carries it through, so a
            # consumer can bind an item to the run it describes instead of guessing
            # from adapter + kind + file count — a guess that stops working the moment
            # a day holds two similar runs, which is the ordinary case for a worker
            # plus its automatic review.
            "run_id": run.run_id,
            "adapter": run.adapter,
            "state": run.state.value,
            "finished_at": run.finished_at,
            "role": str((run.result_metadata or {}).get(ROLE_KEY, ROLE_WORKER)),
            # None when the run has no snapshot: the count is then unknown, and
            # the recap says "delivered work" without inventing a number. An empty
            # list is different — it is a verified "changed nothing".
            "delivered_count": None if snapshot is None else len(snapshot.get("files") or []),
            # The snapshot itself stopped at MAX_FILES, so its count is a floor.
            "delivered_truncated": bool(snapshot and snapshot.get("truncated")),
        })

    result = last_active_day(events, runs, today=today, day_of=day_of)
    # Say so when the day held more than we read, rather than letting a bounded
    # read look like the whole day.
    result["partial"] = bool(runs_truncated or len(events) >= _RECAP_EVENT_LIMIT)
    # When this zone's day next changes, so a page left open past midnight stops
    # saying "today" about yesterday. Computed server-side because the current
    # offset alone cannot locate the next midnight across a DST change; the
    # offset is reported alongside it only for display/diagnostics. Both come from
    # the snapshot above, so they describe the same instant as days_ago.
    result["zone_offset_minutes"] = zone_offset_minutes(zone=zone, now=now)
    result["next_rollover"] = next_rollover(zone=zone, now=now)
    return result


@router.get("/projects/{project_id}/decisions")
def project_decisions(
    project_id: str,
    registry: ProjectRegistry = Depends(get_project_registry),
) -> dict[str, Any]:
    """The project's decision records, read from ``<root>/.agora/decisions/``.

    Its own route rather than a field on the dashboard, because reading it lists a
    directory and opens every record in it, and the dashboard is the request a task
    switch waits for. The payload always carries either the records or the reason
    there are none, so the UI can tell "this project keeps no decision log" from
    "it could not be read" without deriving either from a missing field.
    """
    from agora.projects.decisions import read_decisions

    try:
        project = registry.get(project_id)
    except (KeyError, LookupError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown project: {project_id}") from None
    return {"project_id": project_id, **read_decisions(project.root).payload()}


@router.put("/projects/{project_id}/charter")
def set_project_charter(
    project_id: str,
    request: SetCharterRequest,
    registry: ProjectRegistry = Depends(get_project_registry),
) -> dict[str, Any]:
    """Set the project's human-authored charter (goal / non-goals / success
    criteria). This is the human-facing route; no agent code path writes the
    charter. NOTE: like the other task/settings routes this is not yet
    authenticated (the app's non-control-plane routes gain auth in 1.3), so on a
    shared host it is not a hard guarantee — tracked for 1.3."""
    try:
        project = registry.set_charter(
            project_id,
            goal=request.goal,
            non_goals=request.non_goals,
            success_criteria=request.success_criteria,
        )
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown project: {project_id}") from None
    return _project_summary(project)


def _project_summary(project: Any) -> dict[str, Any]:
    return {
        "project_id": project.project_id,
        "name": project.name,
        "root": str(project.root),
        "worker_agent": project.worker_agent,
        "reviewer_agent": project.reviewer_agent,
        "charter": _charter_view(project.charter),
    }


def _charter_view(charter: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize a stored charter for the UI (always the same shape)."""
    charter = charter or {}
    return {
        "goal": charter.get("goal", ""),
        "non_goals": list(charter.get("non_goals") or []),
        "success_criteria": list(charter.get("success_criteria") or []),
    }


def _projects_overview(registry: ProjectRegistry) -> dict[str, Any]:
    current = registry.current_project_id()
    items = []
    for project_id, project in sorted(registry.list_projects().items()):
        # Tri-state on purpose: True, False, or None for "could not tell". These were
        # `is_dir()` / `exists()`, which answer False both for nothing-is-there and for
        # a stat this process may not complete, so a root behind a denying ACL was
        # reported to Settings as missing — a settled fact that sends the operator to
        # recreate a directory that is in fact there. See `agora.fsprobe`.
        root = probe_path(project.root)
        git = probe_path(project.root / ".git")
        items.append(
            {
                "project_id": project_id,
                "name": project.name,
                "root": str(project.root),
                "root_exists": root.is_dir(),
                "root_is_git": git.exists(),
                "worker_agent": project.worker_agent,
                "reviewer_agent": project.reviewer_agent,
                "charter": _charter_view(project.charter),
                "workspaces": [
                    _workspace_view(adapter, path)
                    for adapter, path in sorted(project.workspaces.items())
                ],
            }
        )
    return {
        "current": current,
        "projects_base": str(registry.projects_base()),
        "items": items,
        # Projects are stored in the control-plane database. This says what the
        # last read of the YAML import source did, so a file that cannot be
        # parsed shows up as a reported failure and never as a shorter list.
        "import_status": registry.import_status(),
    }


def _execution_limits(config: dict[str, Any]) -> dict[str, Any]:
    execution = config.get("execution", {})
    orchestration = config.get("orchestration", {})
    return {
        "max_concurrent_global": int(execution.get("max_concurrent_global", 4)),
        "max_concurrent_per_project": int(execution.get("max_concurrent_per_project", 2)),
        "network_mode": str(orchestration.get("network_mode", "system")),
        "default_timeout_seconds": int(orchestration.get("timeout_seconds", 600)),
    }


def _run_stats(store: ExecutionStore) -> dict[str, Any]:
    summaries = store.list(limit=_RUN_SCAN_LIMIT)
    by_state: dict[str, int] = {}
    by_adapter: dict[str, int] = {}
    for summary in summaries:
        by_state[summary.state.value] = by_state.get(summary.state.value, 0) + 1
        by_adapter[summary.adapter] = by_adapter.get(summary.adapter, 0) + 1
    tokens = store.token_usage_by_adapter()
    tokens_by_adapter = {name: usage["total_tokens"] for name, usage in tokens.items() if usage["total_tokens"] > 0}
    return {
        "total": len(summaries),
        "truncated": len(summaries) >= _RUN_SCAN_LIMIT,
        "by_state": by_state,
        "by_adapter": by_adapter,
        "running": by_state.get(RunState.RUNNING.value, 0),
        "queued": by_state.get(RunState.QUEUED.value, 0),
        # Per-run token usage is captured only where a CLI reports it (claude
        # print-JSON today). codex runs via the app-server bridge and kiro emits
        # plain text, so neither is captured yet. This is consumption Agora
        # observed, never a subscription balance (no CLI exposes a quota API).
        "tokens_by_adapter": tokens_by_adapter,
        "total_tokens": sum(tokens_by_adapter.values()),
        "token_usage_recorded": bool(tokens_by_adapter),
    }


def _control_plane_auth(config: dict[str, Any]) -> dict[str, Any]:
    credentials = (
        config.get("control_plane", {}).get("auth", {}).get("credentials", [])
    )
    configured = 0
    for credential in credentials:
        secret_ref = credential.get("secret_ref")
        if secret_ref and os.environ.get(secret_ref, "").strip():
            configured += 1
    return {
        "auth_configured": configured > 0,
        "principals": len(credentials),
        "principals_with_secret": configured,
    }


def _workspace_view(adapter: str, path: Path) -> dict[str, Any]:
    """One agent workspace, with "could not tell" reported as ``None`` rather than False.

    ``writable`` is unknown whenever ``exists`` is: the old code probed the *parent*
    whenever ``path.is_dir()`` was False, so an unreadable workspace directory was
    tested for the writability of its parent and could be reported as a writable
    workspace that does not exist. Neither half of that is an observation of the
    workspace.
    """
    probe = probe_path(path)
    exists = probe.is_dir()
    return {
        "adapter": adapter,
        "path": str(path),
        "exists": exists,
        "writable": None if exists is None else _writable(path, probe),
    }


def _writable(path: Path, probe: PathProbe) -> bool:
    """Whether a workspace can be written: the directory itself, or where it would go.

    Called only when ``probe`` observed the path, so the fallback to the parent is a
    genuine "can this be created here" and not a stand-in for an unread directory.
    A False from ``os.access`` is an answer about permission, which is the question
    asked here — unlike a False from an existence probe, which is not an answer at all.
    """
    target = path if probe.is_dir() else path.parent
    try:
        return os.access(target, os.W_OK)
    except OSError:
        return False
