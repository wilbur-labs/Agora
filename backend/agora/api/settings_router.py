"""Read-only Settings observations: runtime availability, workspaces, run stats.

Additive, observe-only surface for the Settings page. Nothing here writes
durable state or changes configuration; it probes the three native runtimes
(installation + `--version`), reports the configured project workspaces, and
summarizes execution runs. Subscription CLIs expose no remaining-quota API, so
this deliberately reports *runs Agora has dispatched*, never a plan balance.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
import threading
from functools import wraps
from pathlib import Path
from typing import Any, Callable, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from agora.config.settings import get_config, resolve_control_plane_db_path
from agora.fsprobe import PathProbe, probe_path
from agora.tasks.router import get_project_registry, get_task_store
from agora.tasks.store import TaskStore
from agora.execution.adapters import build_adapter_registry
from agora.execution import credential_store
from agora.ssh_config import read_ssh_config
from agora.projects.import_candidates import (
    exclude_agora_worktrees, find_candidates, git_worktree_root_status, recorded_goals,
    tool_directory_check,
)
from agora.execution.remote_wiring import remote_path_within
from agora.execution.credential_store import KeyringUnavailableError
from agora.execution.dispatcher import token_capturable_adapters
from agora.execution.environment_registry_models import (
    ENVIRONMENT_ID_PATTERN,
    RemoteEnvironmentRecord,
)
from agora.execution.environment_registry_store import (
    RemoteEnvironmentExistsError,
    RemoteEnvironmentNotFoundError,
    RemoteEnvironmentStore,
)
from agora.execution.models import RunState
from agora.execution.router import get_execution_store
from agora.execution.store import ExecutionStore
from agora.orchestration.runtime import build_runtime_registry
from agora.orchestration.runtime_auth import collect_runtime_auth
from agora.orchestration.runtime_capabilities import (
    collect_native_runtime_capabilities,
)
from agora.projects import ProjectRegistry
from agora.projects.models import TodayFocus
from agora.projects.registry import (
    LastProjectError,
    ProjectInUseError,
    ProjectListedInImportSourceError,
    TodayFocusFullError,
)
from agora.projects.workspace_tasks import (
    PROJECTS_UNREADABLE,
    ProjectRoots,
    read_workspace_source,
)
from agora.workspaces.models import ProvisionRequest
from agora.workspaces.provisioner import WorkspaceProvisioner

router = APIRouter(tags=["settings"])


def get_remote_environment_store() -> RemoteEnvironmentStore:
    """The registered-remote-environment store, on the one control-plane database."""
    return RemoteEnvironmentStore(resolve_control_plane_db_path(get_config()))


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
    # Per-adapter token totals Agora has observed so far.
    tokens = get_execution_store().token_usage_by_adapter()

    # Merge the execution-adapter capability declaration (how attention/approval
    # is delivered) onto each probed adapter, keyed by adapter name.
    #
    # ``token_capturable`` is None when the registry could not be built, and the
    # payload then reports token capture as "unknown". It used to say
    # "unsupported" in that case — a claim about every adapter, derived from a
    # read that failed.
    token_capturable: set[str] | None
    try:
        registry = build_adapter_registry(config)
    except ValueError:
        exec_caps = {}
        token_capturable = None
    else:
        exec_caps = {adapter.name: adapter.capability() for adapter in registry.values()}
        token_capturable = token_capturable_adapters(registry)

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
                "token_capture": (
                    "unknown" if token_capturable is None
                    else "supported" if item.adapter in token_capturable
                    else "unsupported"
                ),
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


# --------------------------------------------------------------- workspace sources (W7)
#
# Agora reads the tasks a claude-workspace directory already holds, rather than asking
# for the work to be entered a second time. Three endpoints, and the middle one is the
# reason this is not two:
#
#   GET  /workspace-sources          what is registered, and what each one reads today
#   POST /workspace-sources/probe    what a path *would* yield — without registering it
#   POST /workspace-sources          register   /   DELETE ...  forget (touches no file)
#
# `probe` exists so the UI can show what it found *before* anything is stored. Accepting
# a path and then rendering an empty list is the same defect this product keeps
# correcting: the person cannot tell "this directory holds no tasks" from "I could not
# read it" from "wrong directory". The probe answers that first, in their words, and
# only then do they confirm.


class WorkspaceSourcePathRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


def _project_roots(registry: ProjectRegistry) -> ProjectRoots:
    """`{project_id: root}` for the registered projects, for the repo link only.

    A lookup, never a key: `Project.root` says in as many words that nothing may key,
    join or dedupe on it, because it is a project's location in the *local* environment
    and moves to a per-environment table when W3 lands. This mapping is built per
    request, never stored, and only decides whether a task's `primary_repo` is somewhere
    the person can already open. See tasks/agora/adr/0006.
    """
    try:
        return {pid: project.root for pid, project in registry.list_projects().items()}
    except Exception:
        # A registry that cannot list is not "no projects". Returning {} would make every
        # task read "that repository is not registered" — a claim about projects this
        # request never saw. None is not right either: that is "nobody looked", and the
        # panel renders it as nothing at all, so a person watching would see their links
        # disappear with no sentence anywhere saying the project list failed to read.
        return PROJECTS_UNREADABLE


def _source_payload(root: Path, project_roots: ProjectRoots) -> dict[str, Any]:
    return read_workspace_source(root, project_roots=project_roots).payload()


@router.get("/workspace-sources")
def list_workspace_sources(
    registry: ProjectRegistry = Depends(get_project_registry),
) -> dict[str, Any]:
    """Every registered workspace, read now.

    Read on request rather than cached: these files are the user's live working notes —
    the whole point is that Agora shows what they say *today* — and a cache would need
    an invalidation story to be worth its risk of showing yesterday's RESUME as today's.
    """
    sources = registry.workspace_sources()
    # Resolved once for the whole response: the link is the same question for every
    # task, and asking the registry per task would make a listing's cost depend on how
    # much work the person has recorded.
    roots = _project_roots(registry)
    return {"sources": [_source_payload(root, roots) for root in sources]}


@router.post("/workspace-sources/probe")
def probe_workspace_source(
    request: WorkspaceSourcePathRequest,
    registry: ProjectRegistry = Depends(get_project_registry),
) -> dict[str, Any]:
    """What this path would yield, without registering it. Never raises for a bad path:
    an unreadable directory is an answer to show, not a 422 to interpret."""
    path = Path(request.path).expanduser()
    if not path.is_absolute():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "A workspace directory must be given as an absolute path",
        )
    return _source_payload(path, _project_roots(registry))


@router.post("/workspace-sources", status_code=status.HTTP_201_CREATED)
def register_workspace_source(
    request: WorkspaceSourcePathRequest,
    registry: ProjectRegistry = Depends(get_project_registry),
) -> dict[str, Any]:
    """Register a workspace directory. Idempotent."""
    try:
        resolved = registry.register_workspace_source(request.path)
    except (ValueError, OSError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return _source_payload(resolved, _project_roots(registry))


@router.delete("/workspace-sources")
def unregister_workspace_source(
    path: str = Query(min_length=1, max_length=4096),
    registry: ProjectRegistry = Depends(get_project_registry),
) -> dict[str, Any]:
    """Stop reading a workspace directory. **No file is touched**: the tasks stay where
    they are and Agora simply forgets the path."""
    return {"was_registered": registry.unregister_workspace_source(path)}


@router.get("/projects")
def list_projects(
    registry: ProjectRegistry = Depends(get_project_registry),
) -> dict[str, Any]:
    """Registered projects (independent of whether they have tasks yet)."""
    focus = registry.today_focus()
    return {
        "current": registry.current_project_id(),
        "projects_base": str(registry.projects_base()),
        "import_status": registry.import_status(),
        # Today's top tier, travelling with the list it refers to. Two requests would let
        # the cards and the count disagree on screen for a moment, and the count is the
        # thing the design states out loud ("3/3 已满").
        "today_focus": {
            "project_ids": focus.project_ids,
            # Promoted ids that no longer resolve. Reported rather than filtered away, so
            # a smaller count can be explained instead of merely observed.
            "unresolved_ids": focus.unresolved_ids,
            "limit": focus.limit,
            "is_full": focus.is_full,
        },
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


class RegisterProjectRequest(BaseModel):
    """An existing git repository on this machine, registered as a project as it is."""

    project_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    root: str = Field(min_length=1, max_length=4096)
    name: str | None = Field(default=None, max_length=200)


def _registered_roots(registry: ProjectRegistry) -> dict[str, str]:
    return {
        os.path.normcase(str(Path(project.root).resolve(strict=False))): project_id
        for project_id, project in registry.list_projects().items()
    }


@router.get("/projects/import-candidates")
def list_project_import_candidates(
    registry: ProjectRegistry = Depends(get_project_registry),
) -> dict[str, Any]:
    """The repositories the registered workspaces' task records name (CR-52), one per
    repository, each saying whether it can be registered as is — and if not, why.
    Task files that could not be read are listed, not skipped. ``sources`` is how many
    work sources are registered, so "nowhere to read from" is never shown as "nothing to
    import" (CR-57); whether they could be read is in ``unreadable``."""
    sources = [Path(root) for root in registry.workspace_sources()]
    candidates, unreadable = find_candidates(sources, _registered_roots(registry))
    return {"candidates": [c.payload() for c in candidates], "unreadable": unreadable, "sources": len(sources)}


@router.get("/projects/{project_id}/recorded-goals")
def list_recorded_goals(
    project_id: str,
    registry: ProjectRegistry = Depends(get_project_registry),
) -> dict[str, Any]:
    """The goals already written for this project's repository in the workspaces' task
    records (CR-35), so an imported project does not start its goal from nothing. Read
    only: adopting one goes through the charter editor, where the person confirms it."""
    try:
        project = registry.get(project_id)
    except (KeyError, LookupError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown project: {project_id}") from None
    goals, unreadable = recorded_goals([Path(root) for root in registry.workspace_sources()], Path(project.root))
    return {"project_id": project_id, "goals": [g.payload() for g in goals], "unreadable": unreadable}


@router.post("/projects/register", status_code=status.HTTP_201_CREATED)
def register_existing_project(
    request: RegisterProjectRequest,
    registry: ProjectRegistry = Depends(get_project_registry),
) -> dict[str, Any]:
    """Register an EXISTING git repository on this machine as a project (CR-52), where it
    is. Nothing in the repository is created, initialised or committed; Agora adds only
    its own (empty) ``.agora/`` folders and a local ``.git/info/exclude`` entry so the
    worktrees it builds later stay out of ``git status``. Worktrees are provisioned when
    work is first dispatched, not now."""
    root = Path(request.root).expanduser()
    probe = probe_path(root)
    if probe.unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Cannot inspect {root}: {probe.reason()}")
    if probe.absent or not probe.is_dir():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Not a directory on this machine: {root}")
    # The same exclusion the candidate list applies (CR-56), here too — otherwise it is
    # a filter on one screen, and any other caller of this endpoint walks past it.
    tool = tool_directory_check(root, [Path(source) for source in registry.workspace_sources()])
    if tool.is_tool:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{root} is Agora itself or a workspace tool's directory ({tool.kind}), "
            "not a project; nothing was registered",
        )
    if tool.undecided:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Cannot tell whether {root} is Agora itself or a workspace tool "
            f"({tool.reason()}); nothing was registered",
        )
    git_status = git_worktree_root_status(root)
    if git_status == "unreadable":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Could not ask git about {root}")
    if git_status != "ok":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Not the root of a git repository: {root} — Agora registers existing repositories "
            f"as they are and never git-inits one",
        )
    already = _registered_roots(registry).get(os.path.normcase(str(root.resolve(strict=False))))
    if already is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Already registered as project {already}")
    if request.project_id in registry.list_projects():
        raise HTTPException(status.HTTP_409_CONFLICT, f"Project already exists: {request.project_id}")
    # Agora's own folders first: if they cannot be created (read-only repository, a FILE
    # named .agora), nothing is registered — a project must never be stored while the
    # response says it was not (review).
    try:
        for sub in ("research", "decisions", "specs", "workspaces/claude", "workspaces/codex", "workspaces/kiro"):
            (root / ".agora" / sub).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Cannot create Agora's .agora/ folders in {root}: {type(exc).__name__} — nothing was registered",
        ) from None
    try:
        project = registry.add(request.project_id, root, request.name)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    summary = _project_summary(project)
    summary["worktrees_excluded"] = exclude_agora_worktrees(Path(project.root))
    return summary


@router.delete("/projects/{project_id}")
def remove_project(
    project_id: str,
    registry: ProjectRegistry = Depends(get_project_registry),
) -> dict[str, Any]:
    """Remove a project from Agora (CR-56): **the registration only — no file is
    touched**, not the repository and not the ``.agora/`` folders registering added.

    Protected the way every state-changing route here is: by ``LocalOriginGuard``
    (CR-34), which refuses a cross-site browser request before any router sees it.

    404 for an unknown id. 409 — with the reason, and the counts where there are counts —
    when it is the last project, when rows in the control-plane database still name it,
    or when the YAML import source would bring it back. Nothing is removed on any refusal.
    """
    try:
        current = registry.remove_project(project_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown project: {project_id}") from None
    except LastProjectError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": "last_project", "message": str(exc)},
        ) from None
    except ProjectInUseError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "project_in_use", "message": str(exc), "counts": exc.counts,
             "unreadable": exc.unreadable},
        ) from None
    except ProjectListedInImportSourceError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "listed_in_import_source", "message": str(exc), "source": exc.source},
        ) from None
    return {"removed": project_id, "current_project_id": current}


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


@router.put("/projects/{project_id}/focus")
def promote_project_to_today(
    project_id: str,
    registry: ProjectRegistry = Depends(get_project_registry),
) -> dict[str, Any]:
    """Promote a project into today's top tier. Idempotent.

    409 when the tier is full: the design's rule is that the fourth card cannot come up
    until something goes down ("3/3 已满 —— 第 4 个要上来得先放下一个"), so the server
    refuses rather than evicting — evicting would overwrite the choice this records, and
    do it silently. The response says what the limit is, so the client can state it.
    """
    try:
        focus = registry.promote_to_focus(project_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown project_id {project_id}")
    except TodayFocusFullError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"error": "today_focus_full", "limit": exc.limit},
        )
    return _focus_view(focus)


@router.delete("/projects/{project_id}/focus")
def demote_project_from_today(
    project_id: str,
    registry: ProjectRegistry = Depends(get_project_registry),
) -> dict[str, Any]:
    """Take a project out of today's top tier. Idempotent.

    Accepts an id that no longer resolves: that is how a leftover entry is cleared, and
    refusing would leave it stuck in the list.
    """
    return _focus_view(registry.demote_from_focus(project_id))


def _focus_view(focus: TodayFocus) -> dict[str, Any]:
    return {
        "project_ids": focus.project_ids,
        "unresolved_ids": focus.unresolved_ids,
        "limit": focus.limit,
        "is_full": focus.is_full,
    }


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

    events, events_truncated = task_store.events_on_day(
        task_id, day, limit=_RECAP_EVENT_LIMIT, zone=zone
    )
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
    result["partial"] = bool(runs_truncated or events_truncated)
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


@router.get("/projects/{project_id}/defects")
def project_defects(
    project_id: str,
    registry: ProjectRegistry = Depends(get_project_registry),
) -> dict[str, Any]:
    """The project's known defects, read from ``<root>/.agora/defects.yaml``.

    Its own route, parallel to ``/decisions`` and for the same reason: the left column
    reads each resource on its own, so one resource's read cannot make another look
    unreadable. The payload always carries either the defects or the reason there are
    none, so the UI tells "this project keeps no defect list" from "it could not be
    read" — and the count it shows is the open count, never a total that hides how many
    are already fixed.
    """
    from agora.projects.defects import read_defects

    try:
        project = registry.get(project_id)
    except (KeyError, LookupError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown project: {project_id}") from None
    return {"project_id": project_id, **read_defects(project.root).payload()}


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


# ------------------------------------------------------- remote environments (CR-4 slice 1)
#
# The registry the mockup's environment screen sits on. A remote environment is the
# persisted SSH section of an execution adapter (see execution/remote_wiring.py's note:
# the config already binds an adapter to a host, and this screen is a layer over that).
#
# Slice 1 was CRUD + list only. Slice 2 (adr/0001) adds a credential home: a
# `credential_source` per environment — an env var name (slice-1 default), a private-key
# file PATH (stored in the DB, not a secret), or a password kept in the OS keyring
# (never in the DB). Connection testing and TOFU fingerprint confirmation are the rest
# of slice 2 and need a real host. See task.yaml CR-4 anchor.
#
# A credential VALUE is never returned here, and only a keyring password is ever
# accepted — written to the OS keyring and dropped, never stored on the row, echoed in a
# response, or logged. The list reports only whether the credential is present (env var
# set / key file exists / keyring entry exists), never its value.

#: The AGORA_ namespace an env-var secret_ref must live in. `secret_present` probes
#: os.environ for this name, so constraining it keeps the list endpoint from becoming an
#: oracle for whether any arbitrary server env var is set.
_ENV_VAR_SECRET_REF = re.compile(r"^AGORA_[A-Za-z0-9_]+$")


class RemoteEnvironmentRequest(BaseModel):
    """The registerable fields of a remote environment. Mirrors ``RemoteAdapterConfig``
    plus the registry's id + label and the credential home (``credential_source`` +,
    for keyring, a write-only ``password``). No credential VALUE is ever stored on the
    row: a password goes to the OS keyring, a key path is not a secret, an env-var name
    is just a name (adr/0001)."""

    environment_id: str = Field(min_length=1, max_length=64, pattern=ENVIRONMENT_ID_PATTERN.pattern)
    label: str = Field(min_length=1, max_length=200)
    ssh_host: str = Field(min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(min_length=1, max_length=255)
    remote_workspace: str = Field(min_length=1, max_length=4096)
    remote_project_root: str = Field(min_length=1, max_length=4096)
    # The project whose root ``remote_project_root`` is (one environment = one project
    # on one host). Required: an unbound environment could be chosen from any project.
    project_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    # secret_ref means different things per credential_source (see the validator):
    #   env_var  — the NAME of an env var, constrained to the AGORA_ namespace so the
    #              presence probe cannot be pointed at an unrelated variable.
    #   key_path — a private-key file PATH (not a secret, stored in the DB).
    #   keyring  — an unused label; the password lives in the OS keyring.
    # Length only here; the per-source rules are enforced in _validate_credential.
    secret_ref: str = Field(min_length=1, max_length=4096)
    host_key_mode: str = Field(default="strict", pattern=r"^(strict|tofu)$")
    known_hosts_path: str | None = Field(default=None, max_length=4096)
    cleanup_worktree: bool = False
    # Where the credential comes from (CR-4 slice 2, adr/0001). Default env_var keeps
    # slice-1 requests valid unchanged.
    credential_source: str = Field(default="env_var", pattern=r"^(env_var|key_path|keyring|ssh_default)$")
    # A password, present ONLY on a create/update that sets a keyring credential. It is
    # written to the OS keyring and then dropped — never stored on the record, returned
    # in a response, or logged (adr/0001). None on read and on non-keyring sources.
    #
    # No max_length constraint on purpose: a Field length violation raises at FastAPI's
    # request-parse layer, whose default 422 body echoes the offending `input` — which
    # for this field is the password itself. Length is checked in _validate_credential
    # instead, with a message that never interpolates the value.
    password: str | None = Field(default=None)

    @model_validator(mode="after")
    def _validate_credential(self) -> "RemoteEnvironmentRequest":
        # tofu pins the first-seen key into a known_hosts file, so tofu with no file to
        # pin into is refused here, at register time — not deferred to a slice-2 connect
        # failure. This is the authority the frontend echoes; without it the server
        # would accept a config the form blocks and the runtime cannot use
        # (RemoteEnvironment._known_hosts_arg raises at connect for exactly this).
        if self.host_key_mode == "tofu" and not (self.known_hosts_path or "").strip():
            raise ValueError("trust-on-first-use (tofu) requires a known_hosts_path")
        # The remote provisioner only builds worktrees inside the project root (it is
        # constructed with no other allowed root), so a worktree directory elsewhere
        # would fail on the host at the first provision. Refuse it here instead.
        if not remote_path_within(self.remote_workspace, self.remote_project_root):
            raise ValueError(
                "the remote worktree directory must be an absolute path inside the remote "
                "project root (e.g. <project root>/.agora/workspaces)"
            )

        # Per-source credential rules (adr/0001).
        if self.credential_source == "env_var":
            # secret_present probes os.environ for this name, so an unconstrained name
            # would turn the list endpoint into an oracle for any server env var
            # (PATH, AWS_SECRET_ACCESS_KEY, ...). Constrain to the AGORA_ namespace.
            if not _ENV_VAR_SECRET_REF.match(self.secret_ref):
                raise ValueError(
                    "for credential_source 'env_var', secret_ref must be an env var "
                    "name in the AGORA_ namespace (^AGORA_[A-Za-z0-9_]+$)"
                )
            if self.password is not None:
                raise ValueError("a password is only accepted for credential_source 'keyring'")
        elif self.credential_source in ("key_path", "ssh_default"):
            # key_path: secret_ref is a file path. ssh_default: this machine's own SSH
            # keys/agent, secret_ref an unused label (CR-24). No password for either.
            if self.password is not None:
                raise ValueError("a password is only accepted for credential_source 'keyring'")
        elif self.credential_source == "keyring":
            # A create/update that sets a keyring credential must carry the password to
            # store. (An update that keeps the existing keyring password sends no
            # password — allowed, so editing other fields does not require re-entering
            # the secret; see the endpoint.) Length is bounded here, not via a Field
            # max_length, so an over-long value never gets echoed by FastAPI's parse-time
            # 422 — the message names no value.
            if self.password is not None and len(self.password) > 1024:
                raise ValueError("password is too long (max 1024 characters)")
        return self

    def to_record(self) -> RemoteEnvironmentRecord:
        return RemoteEnvironmentRecord(
            environment_id=self.environment_id,
            label=self.label,
            ssh_host=self.ssh_host,
            ssh_port=self.ssh_port,
            ssh_user=self.ssh_user,
            remote_workspace=self.remote_workspace,
            remote_project_root=self.remote_project_root,
            secret_ref=self.secret_ref,
            host_key_mode=self.host_key_mode,  # type: ignore[arg-type]
            known_hosts_path=self.known_hosts_path,
            cleanup_worktree=self.cleanup_worktree,
            credential_source=self.credential_source,  # type: ignore[arg-type]
            project_id=self.project_id,
        )


#: One lock for every remote-environment mutation. A create/update/delete touches two
#: stores — the row and the OS keyring — and FastAPI runs these sync handlers on a
#: thread pool, so two overlapping requests could interleave them (one request's
#: keyring cleanup deleting the password another just saved). Held across both writes,
#: including cleanup and rollback. Mutations are rare and human-driven, so serializing
#: them costs nothing.
_REMOTE_ENV_MUTATION_LOCK = threading.Lock()

_F = TypeVar("_F", bound=Callable[..., Any])


def _serialized(func: _F) -> _F:
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with _REMOTE_ENV_MUTATION_LOCK:
            return func(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


#: The key files the ``ssh`` command (and asyncssh) look for by default, in ~/.ssh.
_DEFAULT_SSH_KEY_FILES = (
    "id_ed25519_sk", "id_ecdsa_sk", "id_ed448", "id_ed25519", "id_ecdsa", "id_rsa", "id_dsa",
)


def _default_ssh_identity_present() -> bool | None:
    """For ``ssh_default`` (CR-24): True when a default key file exists in ~/.ssh; None
    when none does but an agent is configured (it may hold keys — this cannot tell);
    False when there is neither. True means a key exists here, NOT that the host
    accepts it — only a connection can say that."""
    try:
        ssh_dir = Path.home() / ".ssh"
        if any((ssh_dir / name).is_file() for name in _DEFAULT_SSH_KEY_FILES):
            return True
    except (OSError, RuntimeError):
        return None
    return None if os.environ.get("SSH_AUTH_SOCK") else False


def _credential_present(record: RemoteEnvironmentRecord) -> bool | None:
    """Whether this environment's credential is present — read per source, never the
    value (adr/0001). Three-state, honouring the second constitution rule:

    - env_var: the named env var is set on this machine.
    - key_path: the key file at secret_ref exists.
    - keyring: an entry exists in the OS keyring. ``None`` (not False) when this
      machine has NO keyring backend — "cannot tell" is not "not set".
    - ssh_default: a default key file exists in ~/.ssh (see
      ``_default_ssh_identity_present``).
    """
    if record.credential_source == "keyring":
        try:
            return credential_store.has_password(record.environment_id)
        except KeyringUnavailableError:
            return None  # no backend here — unknown, never "absent"
    if record.credential_source == "ssh_default":
        return _default_ssh_identity_present()
    if record.credential_source == "key_path":
        if not record.secret_ref:
            return False
        try:
            return Path(record.secret_ref).expanduser().is_file()
        except (OSError, RuntimeError):
            # Permission denied / unavailable filesystem / no home to expand: the file
            # could not be inspected — unknown, never "absent".
            return None
    # env_var (slice-1 default)
    return bool(os.environ.get(record.secret_ref, "").strip())


def _remote_environment_view(record: RemoteEnvironmentRecord) -> dict[str, Any]:
    """One registered environment for the UI. The credential itself is never here:
    ``credential_source`` says where it lives and ``credential_present`` says only
    whether it is set (env var set / key file exists / keyring entry exists), never its
    value — a three-state fact (True/False/None), where None means "this machine cannot
    tell" (e.g. no keyring backend), kept apart from False ("looked, not set")."""
    return {
        "environment_id": record.environment_id,
        "label": record.label,
        "ssh_host": record.ssh_host,
        "ssh_port": record.ssh_port,
        "ssh_user": record.ssh_user,
        "remote_workspace": record.remote_workspace,
        "remote_project_root": record.remote_project_root,
        "secret_ref": record.secret_ref,
        "credential_source": record.credential_source,
        "credential_present": _credential_present(record),
        "project_id": record.project_id,
        "host_key_mode": record.host_key_mode,
        "known_hosts_path": record.known_hosts_path,
        "cleanup_worktree": record.cleanup_worktree,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


@router.get("/ssh-config/hosts")
def list_ssh_config_hosts() -> dict[str, Any]:
    """The hosts in this machine's ``~/.ssh/config``, to pre-fill a registration (CR-25).
    Read-only, and never used to connect. ``status``: ``absent`` (no file), ``unreadable``
    (exists but could not be read — not "no hosts"), or ``ok``."""
    read = read_ssh_config()
    return {
        "status": read.status,
        "error": read.error,
        "hosts": [
            {"alias": h.alias, "hostname": h.hostname, "user": h.user, "port": h.port,
             "identity_file": h.identity_file}
            for h in read.hosts
        ],
    }


@router.get("/remote-environments")
def list_remote_environments(
    store: RemoteEnvironmentStore = Depends(get_remote_environment_store),
) -> dict[str, Any]:
    """Every registered remote environment. Credential values are never returned; each
    entry reports only its ``credential_source`` and whether the credential is present
    (env var set / key file exists / keyring entry exists), never the value."""
    return {"environments": [_remote_environment_view(r) for r in store.list_environments()]}


@router.post("/remote-environments", status_code=status.HTTP_201_CREATED)
@_serialized
def create_remote_environment(
    request: RemoteEnvironmentRequest,
    store: RemoteEnvironmentStore = Depends(get_remote_environment_store),
    registry: ProjectRegistry = Depends(get_project_registry),
) -> dict[str, Any]:
    """Register a remote environment. Records it (slice 1) and, for a keyring
    credential, stores the password in the OS keyring (slice 2, adr/0001). A duplicate
    id is 409; an invalid SSH field set is 422; a keyring credential with no password,
    or no keyring backend, is 422.

    The password lives in the request body exactly once: it is written to the keyring
    and never placed on the record, returned, or logged.
    """
    if request.credential_source == "keyring" and not request.password:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "a keyring credential requires a password to store",
        )
    _require_project(registry, request.project_id)
    try:
        record = store.insert(request.to_record())
    except RemoteEnvironmentExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except ValidationError:
        # A record re-validation failure: str(ValidationError) includes the offending
        # input_value for type errors pydantic raises itself, which does NOT route
        # through the global RequestValidationError redactor. The record carries no
        # password field today, but do not depend on that invariant here — return a
        # value-free message rather than the raw pydantic string (adr/0001).
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid remote environment fields"
        ) from None
    except ValueError as exc:
        # Store-raised business errors carry our own value-free messages.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    # Row is in; now store the password. If the keyring write fails (no backend),
    # roll the row back so we never leave a keyring environment that can never connect.
    if request.credential_source == "keyring":
        try:
            credential_store.set_password(request.environment_id, request.password)
        except KeyringUnavailableError as exc:
            store.delete(request.environment_id)
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
        record = store.require(request.environment_id)
    return _remote_environment_view(record)


def _require_project(registry: ProjectRegistry, project_id: str) -> None:
    """An environment may only be bound to a project that exists (422 otherwise)."""
    try:
        registry.get(project_id)
    except KeyError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown project: {project_id}"
        ) from None


def _restore_keyring_password(environment_id: str, prior: str | None) -> bool:
    """Put the keyring back to ``prior`` (delete the entry if there was none). Returns
    whether that worked; never raises, because it runs while another error is already
    on its way to the caller."""
    try:
        if prior is None:
            credential_store.delete_password(environment_id)
        else:
            credential_store.set_password(environment_id, prior)
    except KeyringUnavailableError:
        return False
    return True


@router.put("/remote-environments/{environment_id}")
@_serialized
def update_remote_environment(
    environment_id: str,
    request: RemoteEnvironmentRequest,
    store: RemoteEnvironmentStore = Depends(get_remote_environment_store),
    registry: ProjectRegistry = Depends(get_project_registry),
) -> dict[str, Any]:
    """Replace an existing environment's editable fields. The id in the path is the
    row updated; a mismatching id in the body is refused rather than silently creating a
    second row or renaming in place.

    Keyring password (adr/0001): a password sent here is written to the keyring
    (replacing any existing one). A keyring credential with NO password keeps the
    existing keyring entry — so editing a label does not force re-entering the secret —
    *provided one already exists*; changing the source to keyring with neither a new
    password nor an existing keyring entry is refused (422), because it would persist an
    environment that can never connect.
    """
    if request.environment_id != environment_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "environment_id in the body must match the id in the path",
        )
    # Confirm the row exists before touching anything, so a 404 costs no write and the
    # keyring is never mutated for an environment that isn't there.
    existing = store.get(environment_id)
    if existing is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Unknown remote environment: {environment_id}"
        )
    _require_project(registry, request.project_id)
    # A stored password belongs to the place it was entered for. Pointing the environment
    # somewhere else while keeping it would hand it to whatever answers there — and this
    # API has no caller identity of its own yet (CR-68), so another account on this
    # machine could do exactly that (codex review of the remote terminal design).
    moved = (request.ssh_host, request.ssh_port, request.ssh_user) != (
        existing.ssh_host, existing.ssh_port, existing.ssh_user)
    if moved and request.credential_source == "keyring" and not request.password:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "the host, port or user changed: the stored password is not carried to the new "
            "place — enter the password again",
        )
    # Decide the keyring outcome BEFORE mutating anything, so an invalid combination is
    # a clean 422 rather than a half-applied update.
    if request.credential_source == "keyring" and not request.password:
        try:
            has_existing = credential_store.has_password(environment_id)
        except KeyringUnavailableError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
        if not has_existing:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "a keyring credential requires a password (none was provided and none "
                "is already stored for this environment)",
            )
    # Store the password FIRST (when one was sent), so a keyring failure aborts before
    # the row is touched — no half-applied update where the row changed but the secret
    # did not land. The password exists in the request body only; never on the record,
    # returned, or logged.
    wrote_password = request.credential_source == "keyring" and bool(request.password)
    prior_password: str | None = None
    if wrote_password:
        try:
            # Keep what was there so a failed row write can put it back (CR-8).
            prior_password = credential_store.get_password(environment_id)
            credential_store.set_password(environment_id, request.password)
        except KeyringUnavailableError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    try:
        record = store.update(environment_id, request.to_record())
    except Exception as exc:
        # The row did not change, so the keyring must not stay ahead of it: restore
        # the previous password (or remove the new one if there was none) — CR-8.
        restore_failed = wrote_password and not _restore_keyring_password(
            environment_id, prior_password
        )
        suffix = (
            "; the previous keyring password could not be restored, so the stored "
            "password is now the one just sent"
            if restore_failed
            else ""
        )
        if isinstance(exc, RemoteEnvironmentNotFoundError):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"Unknown remote environment: {environment_id}{suffix}",
            ) from None
        if isinstance(exc, ValidationError):
            # Same reasoning as create: never echo a raw pydantic string (it can carry
            # input_value) — it bypasses the global redactor. Value-free message
            # (adr/0001).
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"invalid remote environment fields{suffix}",
            ) from None
        if isinstance(exc, ValueError):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"{exc}{suffix}"
            ) from None
        raise
    # If the credential moved AWAY from keyring, forget the now-orphaned secret so it does
    # not linger in the OS keyring. Best-effort (same posture as delete): if the keyring
    # is unavailable right now the secret is left behind — CR-10, deferred.
    if existing.credential_source == "keyring" and request.credential_source != "keyring":
        try:
            credential_store.delete_password(environment_id)
        except KeyringUnavailableError:
            pass
    return _remote_environment_view(record)


@router.delete("/remote-environments/{environment_id}")
@_serialized
def delete_remote_environment(
    environment_id: str,
    store: RemoteEnvironmentStore = Depends(get_remote_environment_store),
) -> dict[str, Any]:
    """Forget a registered environment. Idempotent: deleting an id that is already
    gone reports ``was_registered: false`` rather than 404."""
    return {"was_registered": store.delete(environment_id)}
