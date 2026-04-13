"""Docker sandbox — run commands in ephemeral containers."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path

from agora.config.settings import get_config


@dataclass
class SandboxConfig:
    enabled: bool = False
    image: str = "python:3.12-slim"
    timeout: int = 120
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    workspace_dir: str = "/tmp/agora_workspace"
    network: bool = True  # allow network access


def get_sandbox_config() -> SandboxConfig:
    cfg = get_config().get("sandbox", {})
    return SandboxConfig(
        enabled=cfg.get("enabled", False),
        image=cfg.get("image", "python:3.12-slim"),
        timeout=cfg.get("timeout", 120),
        memory_limit=cfg.get("memory_limit", "512m"),
        cpu_limit=cfg.get("cpu_limit", 1.0),
        workspace_dir=cfg.get("workspace_dir", "/tmp/agora_workspace"),
        network=cfg.get("network", True),
    )


class DockerSandbox:
    """Ephemeral Docker container for safe command execution."""

    def __init__(self, config: SandboxConfig | None = None):
        self.config = config or get_sandbox_config()
        self.container_id: str | None = None
        self._name = f"agora-sandbox-{uuid.uuid4().hex[:8]}"

    async def start(self) -> str:
        workspace = Path(self.config.workspace_dir)
        workspace.mkdir(parents=True, exist_ok=True)

        cmd = [
            "docker", "run", "-d",
            "--name", self._name,
            "--memory", self.config.memory_limit,
            f"--cpus={self.config.cpu_limit}",
            "-v", f"{workspace.resolve()}:/workspace",
            "-w", "/workspace",
        ]
        if not self.config.network:
            cmd.append("--network=none")
        cmd.extend([self.config.image, "sleep", str(self.config.timeout)])

        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Failed to start sandbox: {stderr.decode().strip()}")
        self.container_id = stdout.decode().strip()[:12]
        return self.container_id

    async def exec(self, command: str) -> tuple[int, str, str]:
        """Execute a command inside the sandbox. Returns (returncode, stdout, stderr)."""
        if not self.container_id:
            await self.start()

        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", self._name, "sh", "-c", command,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.config.timeout)
        except asyncio.TimeoutError:
            await self._kill_exec()
            return 1, "", f"Command timed out after {self.config.timeout}s"

        return proc.returncode or 0, stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace")

    async def _kill_exec(self):
        proc = await asyncio.create_subprocess_exec(
            "docker", "kill", self._name,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    async def stop(self):
        if not self.container_id:
            return
        proc = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", self._name,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        self.container_id = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *exc):
        await self.stop()
