"""Read-only CLI runtime boundary for the provisional planning workflow."""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Awaitable, Callable

from agora.protocol.models import ProviderUsageObservation

from .provider_usage import RuntimeResultFormat, normalize_native_output


OUTPUT_LIMIT = 64 * 1024
CAPTURE_LIMIT = OUTPUT_LIMIT + 16 * 1024
OUTPUT_READ_SIZE = 8 * 1024
POST_STOP_DRAIN_TIMEOUT = 5


@dataclass(frozen=True)
class RuntimeCommand:
    adapter: str
    command_template: tuple[str, ...]
    result_format: RuntimeResultFormat = RuntimeResultFormat.PLAIN_TEXT
    version_command: tuple[str, ...] | None = None
    declared_models: tuple[str, ...] = ()

    def build(self, prompt: str) -> list[str]:
        return [prompt if item == "{prompt}" else item for item in self.command_template]


@dataclass(frozen=True)
class RuntimeResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    process_started: bool = True
    usage_observation: ProviderUsageObservation | None = None


class RuntimeInterrupted(RuntimeError):
    pass


class RuntimeLaunchError(RuntimeError):
    pass


DEFAULT_RUNTIME_COMMANDS = {
    "codex": (
        "codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only",
        "--ephemeral", "--json", "{prompt}",
    ),
    "claude": (
        "claude", "-p", "{prompt}", "--output-format", "json",
        "--permission-mode", "plan", "--no-session-persistence",
    ),
    "kiro": (
        "kiro-cli", "chat", "--no-interactive", "--trust-tools=", "{prompt}",
    ),
}

DEFAULT_RESULT_FORMATS = {
    "codex": RuntimeResultFormat.CODEX_JSONL_V1,
    "claude": RuntimeResultFormat.CLAUDE_JSON_V1,
    "kiro": RuntimeResultFormat.PLAIN_TEXT,
}

DEFAULT_VERSION_COMMANDS = {
    "codex": ("codex", "--version"),
    "claude": ("claude", "--version"),
    "kiro": ("kiro-cli", "--version"),
}


def resolve_runtime_command(
    command: list[str],
    *,
    platform: str | None = None,
) -> list[str]:
    """Resolve PATH commands without enabling a general-purpose shell."""
    if not command:
        raise RuntimeLaunchError("runtime command is empty")
    executable = shutil.which(command[0])
    if executable is None:
        return command
    path = Path(executable)
    if (platform or sys.platform) != "win32" or path.suffix.lower() not in {".cmd", ".bat"}:
        return [str(path), *command[1:]]
    prefix = _resolve_windows_wrapper(path)
    return [*prefix, *command[1:]]


def _resolve_windows_wrapper(wrapper: Path) -> list[str]:
    """Resolve only audited wrapper shapes; unknown batch files fail closed."""
    try:
        text = wrapper.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise RuntimeLaunchError("Windows command wrapper is unreadable") from exc

    direct = re.search(
        r'^\s*"(?P<target>[^"\r\n]+\.exe)"\s+%\*\s*$',
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if direct:
        target = Path(direct.group("target"))
        if target.is_absolute() and target.is_file():
            return [str(target)]
        raise RuntimeLaunchError("Windows wrapper target is unavailable")

    npm = re.search(
        r'"%dp0%\\(?P<script>[^"\r\n]+\.js)"\s+%\*',
        text,
        flags=re.IGNORECASE,
    )
    if npm:
        wrapper_root = wrapper.parent.resolve()
        script = (wrapper_root / Path(*PureWindowsPath(npm.group("script")).parts)).resolve()
        if not script.is_relative_to(wrapper_root) or not script.is_file():
            raise RuntimeLaunchError("npm wrapper target is unavailable")
        sibling_node = wrapper_root / "node.exe"
        node = str(sibling_node) if sibling_node.is_file() else (
            shutil.which("node.exe") or shutil.which("node")
        )
        if not node or Path(node).suffix.lower() not in {".exe", ".com"}:
            raise RuntimeLaunchError("Node.js executable is unavailable")
        return [node, str(script)]

    raise RuntimeLaunchError("unsupported Windows command wrapper")


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
        if command[0] == "{prompt}":
            raise ValueError(
                f"orchestration.runtimes.{adapter}.command may not use the prompt "
                "as its executable"
            )
        format_default = (
            DEFAULT_RESULT_FORMATS[adapter]
            if "command" not in values
            else RuntimeResultFormat.PLAIN_TEXT
        )
        try:
            result_format = RuntimeResultFormat(
                values.get("result_format", format_default.value)
            )
        except (TypeError, ValueError):
            allowed = ", ".join(item.value for item in RuntimeResultFormat)
            raise ValueError(
                f"orchestration.runtimes.{adapter}.result_format must be one of: {allowed}"
            ) from None
        if (
            result_format == RuntimeResultFormat.CODEX_JSONL_V1
            and adapter != "codex"
        ) or (
            result_format == RuntimeResultFormat.CLAUDE_JSON_V1
            and adapter != "claude"
        ):
            raise ValueError(
                f"orchestration.runtimes.{adapter}.result_format does not match its adapter"
            )
        version_default = DEFAULT_VERSION_COMMANDS[adapter] if "command" not in values else None
        version_command = values.get("version_command", version_default)
        if version_command is not None and (
            not isinstance(version_command, list | tuple)
            or not version_command
            or len(version_command) > 32
            or any(
                not isinstance(item, str) or not item or len(item) > 1_000
                for item in version_command
            )
            or any("{prompt}" in item for item in version_command)
        ):
            raise ValueError(
                f"orchestration.runtimes.{adapter}.version_command must be a bounded "
                "non-empty string list without {prompt}"
            )
        declared_models = values.get("declared_models", [])
        if (
            not isinstance(declared_models, list)
            or len(declared_models) > 50
            or any(
                not isinstance(item, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}", item)
                for item in declared_models
            )
            or len(declared_models) != len(set(declared_models))
        ):
            raise ValueError(
                f"orchestration.runtimes.{adapter}.declared_models must contain "
                "at most 50 unique model identifiers"
            )
        registry[adapter] = RuntimeCommand(
            adapter=adapter,
            command_template=tuple(command),
            result_format=result_format,
            version_command=(
                tuple(version_command) if version_command is not None else None
            ),
            declared_models=tuple(sorted(declared_models)),
        )
    return registry


class ReadOnlyCliRunner:
    def __init__(self, *, network_mode: str = "system"):
        mode = network_mode.lower()
        if mode not in {"direct", "system"}:
            raise ValueError("orchestration.network_mode must be 'direct' or 'system'")
        self.network_mode = mode

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
        try:
            command = resolve_runtime_command(runtime.build(prompt))
            proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._environment({
                    "AGORA_TASK_ID": task_id,
                    "AGORA_RUN_ID": run_id,
                    "AGORA_STAGE_KEY": stage_key,
                    "AGORA_ORCHESTRATION_MODE": "read_only_planning",
                }),
            )
        except RuntimeLaunchError as exc:
            return RuntimeResult(
                exit_code=None, stdout="", stderr=f"process start failed: {exc}",
                process_started=False,
            )
        except (FileNotFoundError, OSError) as exc:
            return RuntimeResult(
                exit_code=None, stdout="",
                stderr=f"process start failed: {type(exc).__name__}",
                process_started=False,
            )
        capture = asyncio.create_task(self._capture_output(proc))
        try:
            await on_process(proc.pid)
            try:
                stdout, stderr = await asyncio.wait_for(
                    asyncio.shield(capture), timeout=timeout_seconds,
                )
            except (TimeoutError, asyncio.TimeoutError):
                await self._stop(proc)
                captured = await self._finish_capture(capture)
                if captured is None:
                    stdout = ""
                    stderr = "process output drain did not close after termination"
                else:
                    stdout, stderr = captured
                stdout, observation = self._normalize_stdout(
                    runtime, stdout, run_id=run_id,
                )
                return RuntimeResult(
                    exit_code=proc.returncode,
                    stdout=stdout,
                    stderr=stderr[-OUTPUT_LIMIT:],
                    timed_out=True,
                    usage_observation=observation,
                )
            stdout, observation = self._normalize_stdout(
                runtime, stdout, run_id=run_id,
            )
            return RuntimeResult(
                exit_code=proc.returncode,
                stdout=stdout,
                stderr=stderr[-OUTPUT_LIMIT:],
                usage_observation=observation,
            )
        except asyncio.CancelledError:
            await self._stop(proc)
            await self._finish_capture(capture)
            raise
        except Exception:
            await self._stop(proc)
            await self._finish_capture(capture)
            raise

    @classmethod
    async def _capture_output(
        cls,
        proc: asyncio.subprocess.Process,
    ) -> tuple[str, str]:
        """Drain both pipes concurrently while retaining only bounded tails."""
        stdout_task = asyncio.create_task(cls._read_tail(proc.stdout))
        stderr_task = asyncio.create_task(cls._read_tail(proc.stderr))
        wait_task = asyncio.create_task(proc.wait())
        tasks = (stdout_task, stderr_task, wait_task)
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return cls._decode(stdout_task.result()), cls._decode(stderr_task.result())

    @staticmethod
    async def _read_tail(stream: asyncio.StreamReader | None) -> bytes:
        if stream is None:
            return b""
        tail = bytearray()
        while chunk := await stream.read(OUTPUT_READ_SIZE):
            if len(chunk) >= CAPTURE_LIMIT:
                tail[:] = chunk[-CAPTURE_LIMIT:]
                continue
            overflow = len(tail) + len(chunk) - CAPTURE_LIMIT
            if overflow > 0:
                del tail[:overflow]
            tail.extend(chunk)
        return bytes(tail)

    @staticmethod
    async def _finish_capture(
        capture: asyncio.Task[tuple[str, str]],
    ) -> tuple[str, str] | None:
        """Bound post-stop pipe drain when descendants retain inherited handles."""
        try:
            return await asyncio.wait_for(
                asyncio.shield(capture), timeout=POST_STOP_DRAIN_TIMEOUT,
            )
        except (TimeoutError, asyncio.TimeoutError):
            capture.cancel()
            await asyncio.gather(capture, return_exceptions=True)
            return None
        except Exception:
            return None

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
        return value.decode("utf-8", errors="replace")

    @staticmethod
    def _normalize_stdout(
        runtime: RuntimeCommand,
        stdout: str,
        *,
        run_id: str,
    ) -> tuple[str, ProviderUsageObservation | None]:
        semantic, observation = normalize_native_output(
            adapter=runtime.adapter,
            result_format=runtime.result_format,
            stdout=stdout,
            run_id=run_id,
        )
        return semantic[-OUTPUT_LIMIT:], observation

    def _environment(self, additions: dict[str, str]) -> dict[str, str]:
        env = dict(os.environ)
        if self.network_mode == "direct":
            proxy_names = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
            for name in list(env):
                if name.upper() in proxy_names:
                    env.pop(name)
        env.update(additions)
        return env
