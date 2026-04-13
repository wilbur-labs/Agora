"""Shell tool — execute commands with optional Docker sandbox."""
from __future__ import annotations

import asyncio

from .base import Tool, ToolResult

_TIMEOUT = 120
_MAX_OUTPUT = 50_000


class Shell(Tool):
    name = "shell"
    description = "Execute a shell command. Returns stdout and stderr."
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute"},
            "cwd": {"type": "string", "description": "Working directory (optional)"},
        },
        "required": ["command"],
    }

    def __init__(self, sandbox=None):
        self._sandbox = sandbox

    async def execute(self, *, command: str, cwd: str | None = None, **_) -> ToolResult:
        if self._sandbox:
            return await self._exec_sandbox(command, cwd)
        return await self._exec_local(command, cwd)

    async def _exec_local(self, command: str, cwd: str | None) -> ToolResult:
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            return ToolResult(False, "", f"Command timed out after {_TIMEOUT}s")
        except Exception as e:
            return ToolResult(False, "", str(e))

        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        if len(out) > _MAX_OUTPUT:
            out = out[:_MAX_OUTPUT] + f"\n... [truncated, {len(out)} chars total]"

        if proc.returncode == 0:
            return ToolResult(True, out, err)
        return ToolResult(False, out, err or f"Exit code: {proc.returncode}")

    async def _exec_sandbox(self, command: str, cwd: str | None) -> ToolResult:
        cmd = f"cd {cwd} && {command}" if cwd else command
        returncode, stdout, stderr = await self._sandbox.exec(cmd)
        if len(stdout) > _MAX_OUTPUT:
            stdout = stdout[:_MAX_OUTPUT] + f"\n... [truncated]"
        if returncode == 0:
            return ToolResult(True, stdout, stderr)
        return ToolResult(False, stdout, stderr or f"Exit code: {returncode}")
