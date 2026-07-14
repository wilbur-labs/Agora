"""Safe argv builders for supported coding-agent CLIs."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ExecutionAdapter:
    name: str
    command_template: tuple[str, ...]
    workspace_key: str

    def build_command(self, prompt: str) -> list[str]:
        """Expand only the dedicated argv element; never invoke a shell."""
        return [prompt if part == "{prompt}" else part for part in self.command_template]

    def stored_command(self) -> list[str]:
        """Return audit-safe argv without expanding user prompt content."""
        return list(self.command_template)

    def workspace(self, workspaces: dict[str, Path]) -> Path:
        if self.workspace_key not in workspaces:
            raise KeyError(f"Project has no {self.workspace_key} workspace")
        return workspaces[self.workspace_key].expanduser().resolve()


DEFAULT_ADAPTERS: dict[str, dict[str, Any]] = {
    "codex": {
        "command": [
            "codex", "exec", "--skip-git-repo-check", "--sandbox", "workspace-write",
            "--ephemeral", "{prompt}",
        ],
        "workspace_key": "codex",
    },
    "claude": {
        "command": [
            "claude", "-p", "{prompt}", "--output-format", "text",
            "--permission-mode", "auto", "--no-session-persistence",
        ],
        "workspace_key": "claude",
    },
    "kiro": {
        "command": ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools", "{prompt}"],
        "workspace_key": "kiro",
    },
}


def build_adapter_registry(config: dict[str, Any]) -> dict[str, ExecutionAdapter]:
    configured = config.get("execution", {}).get("adapters", {})
    registry: dict[str, ExecutionAdapter] = {}
    for name, defaults in DEFAULT_ADAPTERS.items():
        values = configured.get(name, {})
        if values.get("enabled", True) is False:
            continue
        command = values.get("command", defaults["command"])
        if not isinstance(command, list) or not command or any(not isinstance(part, str) for part in command):
            raise ValueError(f"execution.adapters.{name}.command must be a non-empty string list")
        if command.count("{prompt}") != 1:
            raise ValueError(f"execution adapter {name} command must contain exactly one {{prompt}} element")
        registry[name] = ExecutionAdapter(
            name=name,
            command_template=tuple(command),
            workspace_key=str(values.get("workspace_key", defaults["workspace_key"])),
        )
    return registry
