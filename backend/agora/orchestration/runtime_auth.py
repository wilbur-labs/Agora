"""Read-only login/authentication probes for the native runtime CLIs.

`runtime_capabilities` answers "is the CLI installed?"; this answers the next
question, "is it actually signed in?" — because an installed CLI whose
subscription is not authenticated fails only when a real run starts.

Each probe runs the CLI's own cheap status subcommand (no task prompt, no
tokens spent) and reports a boolean plus a short, PII-free method label.
Emails, org identifiers, ARNs, and URLs the CLIs print are never surfaced.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass

from agora.execution.security import redact_text

from .runtime import RuntimeLaunchError, resolve_runtime_command

AUTH_PROBE_TIMEOUT_SECONDS = 10
_METHOD_MAX_LEN = 60

# The CLI's own local status subcommand. These read stored credentials; they do
# not send a prompt or consume subscription tokens.
AUTH_STATUS_COMMANDS: dict[str, tuple[str, ...]] = {
    "claude": ("claude", "auth", "status"),
    "codex": ("codex", "login", "status"),
    "kiro": ("kiro-cli", "whoami"),
}


@dataclass(frozen=True)
class RuntimeAuthProbe:
    adapter: str
    # True = signed in, False = not signed in, None = could not determine.
    authenticated: bool | None
    method: str | None
    check_status: str  # "checked" | "not_configured" | "probe_failed" | "timed_out"


async def collect_runtime_auth(adapters: list[str]) -> dict[str, RuntimeAuthProbe]:
    """Probe sign-in status for each adapter concurrently."""
    names = sorted(set(adapters))
    probes = await asyncio.gather(*(probe_runtime_auth(name) for name in names))
    return {probe.adapter: probe for probe in probes}


async def probe_runtime_auth(
    adapter: str,
    *,
    platform: str | None = None,
) -> RuntimeAuthProbe:
    command = AUTH_STATUS_COMMANDS.get(adapter)
    if command is None:
        return RuntimeAuthProbe(adapter, None, None, "not_configured")
    try:
        resolved = resolve_runtime_command(list(command), platform=platform or sys.platform)
    except RuntimeLaunchError:
        return RuntimeAuthProbe(adapter, None, None, "probe_failed")
    try:
        environment = dict(os.environ)
        for name in list(environment):
            if name.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}:
                environment.pop(name)
        environment["AGORA_ORCHESTRATION_MODE"] = "runtime_auth_observation"
        process = await asyncio.create_subprocess_exec(
            *resolved,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
    except (FileNotFoundError, OSError):
        return RuntimeAuthProbe(adapter, None, None, "probe_failed")

    try:
        stdout_raw, stderr_raw = await asyncio.wait_for(
            process.communicate(), timeout=AUTH_PROBE_TIMEOUT_SECONDS
        )
    except (TimeoutError, asyncio.TimeoutError):
        _terminate(process)
        return RuntimeAuthProbe(adapter, None, None, "timed_out")

    stdout = stdout_raw.decode("utf-8", errors="replace")
    stderr = stderr_raw.decode("utf-8", errors="replace")
    exit_code = process.returncode
    return _interpret(adapter, exit_code, stdout, stderr)


def _interpret(adapter: str, exit_code: int | None, stdout: str, stderr: str) -> RuntimeAuthProbe:
    if adapter == "claude":
        return _interpret_claude(exit_code, stdout)
    # codex/kiro print a human line and exit 0 when signed in.
    if exit_code == 0:
        method = _safe_method(_method_after(stdout or stderr))
        return RuntimeAuthProbe(adapter, True, method, "checked")
    if exit_code is None:
        return RuntimeAuthProbe(adapter, None, None, "probe_failed")
    # A clean non-zero exit is a reliable "not signed in" for these CLIs.
    return RuntimeAuthProbe(adapter, False, None, "checked")


def _interpret_claude(exit_code: int | None, stdout: str) -> RuntimeAuthProbe:
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        if exit_code is None:
            return RuntimeAuthProbe("claude", None, None, "probe_failed")
        return RuntimeAuthProbe("claude", False, None, "checked")
    logged_in = payload.get("loggedIn")
    if not isinstance(logged_in, bool):
        return RuntimeAuthProbe("claude", None, None, "probe_failed")
    # authMethod ("claude.ai") and subscriptionType ("team") are non-PII; email,
    # orgId, and orgName are deliberately dropped.
    parts = [
        str(payload.get(key))
        for key in ("authMethod", "subscriptionType")
        if isinstance(payload.get(key), str) and payload.get(key)
    ]
    method = _safe_method(" · ".join(parts)) if logged_in and parts else None
    return RuntimeAuthProbe("claude", logged_in, method, "checked")


def _method_after(text: str) -> str:
    """Pull the sign-in method phrase from a 'Logged in using/with X' line."""
    for line in text.splitlines():
        match = re.search(r"logged in (?:using|with)\s+(.+)", line, flags=re.IGNORECASE)
        if match:
            # Drop any trailing parenthetical (often a URL or account locator).
            return match.group(1).split("(")[0].strip()
    return ""


def _safe_method(value: str) -> str | None:
    """Redact and bound a method label so no email/URL/ARN can leak through."""
    if not value:
        return None
    cleaned = redact_text(value).strip()
    # Defense in depth: never surface anything shaped like an email/URL/ARN.
    if re.search(r"@|https?://|arn:", cleaned, flags=re.IGNORECASE):
        return None
    cleaned = cleaned[:_METHOD_MAX_LEN].strip()
    return cleaned or None


def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
