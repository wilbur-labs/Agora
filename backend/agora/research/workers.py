"""Worker adapter contracts for research orchestration."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from shutil import which

from .audit import now_iso
from .models import ResearchTask, WorkerResult


@dataclass
class WorkerAdapter:
    name: str
    capabilities: list[str]
    workspace: Path | None = None

    async def run(self, task: ResearchTask) -> WorkerResult:
        now = now_iso()
        notes = (
            f"# {self.name}\n\n"
            "Status: stubbed\n\n"
            "This adapter is registered but real dispatch is not enabled yet.\n"
            f"Workspace: `{self.workspace}`\n"
        )
        return WorkerResult(
            worker=self.name,
            status="stubbed",
            notes=notes,
            started_at=now,
            finished_at=now,
            workspace=str(self.workspace) if self.workspace else None,
            reason="dispatch disabled",
        )


class CliWorkerAdapter(WorkerAdapter):
    command: list[str]
    timeout_seconds: int

    def __init__(
        self,
        name: str,
        capabilities: list[str],
        command: list[str],
        workspace: Path | None = None,
        timeout_seconds: int = 600,
    ):
        super().__init__(name=name, capabilities=capabilities, workspace=workspace)
        self.command = command
        self.timeout_seconds = timeout_seconds

    async def run(self, task: ResearchTask) -> WorkerResult:
        started_at = now_iso()
        resolved = which(self.command[0]) if self.command else None
        if not self.command or not resolved:
            return self._unavailable(started_at, "command not found")
        if self.workspace and not self.workspace.exists():
            return self._unavailable(started_at, "workspace missing")

        prompt = self._prompt(task)
        full_command = self._full_command(prompt)
        try:
            proc = await asyncio.create_subprocess_exec(
                *full_command,
                cwd=str(self.workspace) if self.workspace else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_seconds)
        except TimeoutError:
            return self._unavailable(started_at, f"timeout after {self.timeout_seconds}s", command=full_command)
        except FileNotFoundError as exc:
            return self._unavailable(started_at, f"command not found: {exc.filename}", command=full_command)

        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        status = "ok" if proc.returncode == 0 else "error"
        notes = f"# {self.name}\n\nStatus: {status}\n\n## Output\n\n{out}\n"
        if err:
            notes += f"\n## Stderr\n\n{err}\n"
        return WorkerResult(
            worker=self.name,
            status=status,
            notes=notes,
            started_at=started_at,
            finished_at=now_iso(),
            workspace=str(self.workspace) if self.workspace else None,
            command=full_command,
            exit_code=proc.returncode,
            reason=None if status == "ok" else "non-zero exit code",
            stdout_chars=len(out),
            stderr_chars=len(err),
        )

    def _unavailable(self, started_at: str, reason: str, command: list[str] | None = None) -> WorkerResult:
        finished_at = now_iso()
        notes = (
            f"# {self.name}\n\n"
            "Status: unavailable\n\n"
            f"Reason: {reason}\n"
        )
        return WorkerResult(
            worker=self.name,
            status="unavailable",
            notes=notes,
            started_at=started_at,
            finished_at=finished_at,
            workspace=str(self.workspace) if self.workspace else None,
            command=command or self.command,
            reason=reason,
        )

    def _full_command(self, prompt: str) -> list[str]:
        if any(part == "{prompt}" for part in self.command):
            return [prompt if part == "{prompt}" else part for part in self.command]
        return [*self.command, prompt]

    def _prompt(self, task: ResearchTask) -> str:
        project = task.decision.get("project", {})
        return (
            "You are running as a research worker for Agora.\n\n"
            f"Task ID: {task.task_id}\n"
            f"Worker: {self.name}\n"
            f"Project: {project.get('id', 'unknown')} {project.get('root', '')}\n"
            f"Question: {task.question}\n\n"
            "Expected output:\n"
            "- concise markdown notes\n"
            "- sources with URLs where available\n"
            "- claims and confidence\n"
            "- risks, gaps, and counterarguments\n\n"
            "Do not modify files outside your assigned workspace.\n"
        )


def build_worker_registry(config: dict, dispatch: bool = False) -> dict[str, WorkerAdapter]:
    research_cfg = config.get("research", {})
    workspaces = research_cfg.get("workspaces", {})
    worker_cfg = research_cfg.get("workers", {})
    timeout = int(research_cfg.get("dispatch", {}).get("timeout_seconds", 600))

    specs = {
        "claude-research": {
            "capabilities": ["technical_research", "papers", "official_docs", "synthesis"],
            "workspace_key": "claude",
            "default_command": ["claude", "-p", "--output-format", "text"],
        },
        "codex-engineering": {
            "capabilities": ["repo_inspection", "code_validation", "poc", "benchmark"],
            "workspace_key": "codex",
            "default_command": ["codex", "exec"],
        },
        "kiro-spec": {
            "capabilities": ["requirements", "design", "tasks", "acceptance_criteria"],
            "workspace_key": "kiro",
            "default_command": ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools"],
        },
        "verifier": {
            "capabilities": ["claim_checking", "source_quality", "counterarguments"],
            "workspace_key": None,
            "default_command": [],
        },
        "council": {
            "capabilities": ["multi_agent_deliberation", "architecture_decision"],
            "workspace_key": None,
            "default_command": [],
        },
    }

    registry: dict[str, WorkerAdapter] = {}
    for name, spec in specs.items():
        cfg = worker_cfg.get(name, {})
        workspace_key = cfg.get("workspace_key", spec["workspace_key"])
        workspace = _path(workspaces.get(workspace_key)) if workspace_key else None
        if dispatch and cfg.get("enabled", name in worker_cfg):
            command = _command(cfg, spec["default_command"])
            if command:
                registry[name] = CliWorkerAdapter(
                    name=name,
                    capabilities=spec["capabilities"],
                    command=command,
                    workspace=workspace,
                    timeout_seconds=int(cfg.get("timeout_seconds", timeout)),
                )
                continue
        registry[name] = WorkerAdapter(name=name, capabilities=spec["capabilities"], workspace=workspace)
    return registry


def _command(cfg: dict, default: list[str]) -> list[str]:
    if "command" not in cfg:
        return default
    command = cfg.get("command")
    if isinstance(command, list):
        return [str(part) for part in command]
    args = [str(part) for part in cfg.get("args", [])]
    return [str(command), *args]


def _path(value: str | None) -> Path | None:
    return Path(value).expanduser() if value else None
