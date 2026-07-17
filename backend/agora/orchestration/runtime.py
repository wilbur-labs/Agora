"""Read-only CLI runtime boundary for the provisional planning workflow."""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable


OUTPUT_LIMIT = 64 * 1024


@dataclass(frozen=True)
class RuntimeCommand:
    adapter: str
    command_template: tuple[str, ...]

    def build(self, prompt: str) -> list[str]:
        return [prompt if item == "{prompt}" else item for item in self.command_template]


@dataclass(frozen=True)
class RuntimeResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False


class RuntimeInterrupted(RuntimeError):
    pass


DEFAULT_RUNTIME_COMMANDS = {
    "codex": (
        "codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only",
        "--ephemeral", "{prompt}",
    ),
    "claude": (
        "claude", "-p", "{prompt}", "--output-format", "text",
        "--permission-mode", "plan", "--no-session-persistence",
    ),
    "kiro": (
        "kiro-cli", "chat", "--no-interactive", "--trust-tools=", "{prompt}",
    ),
}


def build_runtime_registry(config: dict) -> dict[str, RuntimeCommand]:
    configured = config.get("orchestration", {}).get("runtimes", {})
    registry: dict[str, RuntimeCommand] = {}
    for adapter, default in DEFAULT_RUNTIME_COMMANDS.items():
        values = configured.get(adapter, {})
        if values.get("enabled", True) is False:
            continue
        command = values.get("command", list(default))
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(item, str) or not item for item in command)
            or command.count("{prompt}") != 1
        ):
            raise ValueError(
                f"orchestration.runtimes.{adapter}.command must be a non-empty string list "
                "containing exactly one {prompt} item"
            )
        registry[adapter] = RuntimeCommand(adapter=adapter, command_template=tuple(command))
    return registry


class ReadOnlyCliRunner:
    async def run(
        self,
        runtime: RuntimeCommand,
        prompt: str,
        *,
        cwd: Path,
        task_id: str,
        run_id: str,
        stage_key: str,
        timeout_seconds: int,
        on_process: Callable[[int], Awaitable[None]],
    ) -> RuntimeResult:
        command = runtime.build(prompt)
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={
                    **os.environ,
                    "AGORA_TASK_ID": task_id,
                    "AGORA_RUN_ID": run_id,
                    "AGORA_STAGE_KEY": stage_key,
                    "AGORA_ORCHESTRATION_MODE": "read_only_planning",
                },
            )
        except (FileNotFoundError, OSError) as exc:
            return RuntimeResult(
                exit_code=None, stdout="",
                stderr=f"process start failed: {type(exc).__name__}",
            )
        try:
            await on_process(proc.pid)
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_seconds,
                )
            except (TimeoutError, asyncio.TimeoutError):
                await self._stop(proc)
                stdout, stderr = await proc.communicate()
                return RuntimeResult(
                    exit_code=proc.returncode,
                    stdout=self._decode(stdout), stderr=self._decode(stderr), timed_out=True,
                )
            return RuntimeResult(
                exit_code=proc.returncode,
                stdout=self._decode(stdout), stderr=self._decode(stderr),
            )
        except asyncio.CancelledError:
            await self._stop(proc)
            await proc.communicate()
            raise RuntimeInterrupted("Orchestration CLI was interrupted; child process was stopped")
        except Exception:
            await self._stop(proc)
            await proc.communicate()
            raise

    @staticmethod
    async def _stop(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except (TimeoutError, asyncio.TimeoutError):
            try:
                proc.kill()
            except ProcessLookupError:
                return
            await proc.wait()

    @staticmethod
    def _decode(value: bytes) -> str:
        return value.decode("utf-8", errors="replace")[-OUTPUT_LIMIT:]
