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
from pydantic import BaseModel, Field

from agora.config.settings import get_config
from agora.tasks.router import get_project_registry
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

# One page of runs to summarize. Beyond this the summary is marked truncated
# rather than paging the whole history on every Settings load.
_RUN_SCAN_LIMIT = 500


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
        "items": [
            {
                "project_id": pid,
                "name": project.name,
                "root": str(project.root),
                "worker_agent": project.worker_agent,
                "reviewer_agent": project.reviewer_agent,
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


def _project_summary(project: Any) -> dict[str, Any]:
    return {
        "project_id": project.project_id,
        "name": project.name,
        "root": str(project.root),
        "worker_agent": project.worker_agent,
        "reviewer_agent": project.reviewer_agent,
    }


def _projects_overview(registry: ProjectRegistry) -> dict[str, Any]:
    current = registry.current_project_id()
    items = []
    for project_id, project in sorted(registry.list_projects().items()):
        items.append(
            {
                "project_id": project_id,
                "name": project.name,
                "root": str(project.root),
                "root_exists": project.root.is_dir(),
                "root_is_git": (project.root / ".git").exists(),
                "worker_agent": project.worker_agent,
                "reviewer_agent": project.reviewer_agent,
                "workspaces": [
                    {
                        "adapter": adapter,
                        "path": str(path),
                        "exists": path.is_dir(),
                        "writable": _writable(path),
                    }
                    for adapter, path in sorted(project.workspaces.items())
                ],
            }
        )
    return {"current": current, "projects_base": str(registry.projects_base()), "items": items}


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


def _writable(path: Path) -> bool:
    probe = path if path.is_dir() else path.parent
    try:
        return os.access(probe, os.W_OK)
    except OSError:
        return False
