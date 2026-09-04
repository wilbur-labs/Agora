"""Preflight checks for Agora project workers."""
from __future__ import annotations

from pathlib import Path
from shutil import which
from typing import Any

from agora.config.settings import get_config
from agora.fsprobe import probe_path
from agora.projects import ProjectRegistry


WORKER_COMMANDS = {
    "claude-research": "claude",
    "codex-engineering": "codex",
    "kiro-spec": "kiro-cli",
}

WORKER_WORKSPACES = {
    "claude-research": "claude",
    "codex-engineering": "codex",
    "kiro-spec": "kiro",
}


def run_doctor(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or get_config()
    registry = ProjectRegistry(cfg)
    project = registry.current_project()
    workers = []

    for worker, command in WORKER_COMMANDS.items():
        workspace_key = WORKER_WORKSPACES[worker]
        workspace = project.workspaces.get(workspace_key)
        cli_path = which(command)
        # Same question, same answer as everywhere else: absence and "could not
        # find out" are different, and only the first one means "create it".
        probe = probe_path(Path(workspace)) if workspace else None
        workspace_ok = bool(probe is not None and probe.present)
        ready = bool(cli_path and workspace_ok)
        workers.append({
            "worker": worker,
            "command": command,
            "cli_path": cli_path,
            "cli_ok": bool(cli_path),
            "workspace": str(workspace) if workspace else None,
            "workspace_ok": workspace_ok,
            "auth": "not_checked",
            "status": "ready" if ready else "unavailable",
            "action": _action(command, cli_path, workspace, probe),
        })

    return {
        "project": {
            "id": project.project_id,
            "name": project.name,
            "root": str(project.root),
            "research_dir": str(project.research_dir),
        },
        "workers": workers,
        "ready_workers": [w["worker"] for w in workers if w["status"] == "ready"],
        "unavailable_workers": [w["worker"] for w in workers if w["status"] != "ready"],
    }


def _action(command: str, cli_path: str | None, workspace: Path | None, probe) -> str:
    if not cli_path:
        return f"Install or expose `{command}` on PATH."
    if not workspace or probe is None:
        return "Configure a workspace for this worker."
    if probe.unknown:
        # Not ready either way, but "create it" is the wrong instruction for a
        # directory that is already there and merely unreadable.
        return f"Workspace could not be inspected ({probe.reason()}): {workspace}"
    if not probe.present:
        return f"Create workspace directory: {workspace}"
    return "Ready. Auth health check is not implemented yet."
