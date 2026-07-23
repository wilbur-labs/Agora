"""Read-only native runtime installation and declaration observations."""
from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agora.protocol.hashing import canonical_sha256, seal_model_payload
from agora.protocol.models import (
    NATIVE_ADAPTER_PROVIDERS,
    NativeRuntimeCapabilityObservation,
    RuntimeCapabilityAdapterObservation,
)

from .routing_policy import (
    ROUTING_POLICY_ID,
    ROUTING_POLICY_SHA256,
    ROUTING_POLICY_VERSION,
    RUNTIME_CAPABILITIES,
)
from .runtime import RuntimeCommand, RuntimeLaunchError, resolve_runtime_command


VERSION_OUTPUT_LIMIT = 8 * 1024
VERSION_PROBE_TIMEOUT_SECONDS = 10
VERSION_CAPTURE_DRAIN_TIMEOUT_SECONDS = 2


@dataclass(frozen=True)
class RuntimeCapabilityProbe:
    installation_status: str
    version: str | None
    version_status: str
    version_method: str
    resolved_runtime_command_sha256: str | None = None
    resolved_version_command_sha256: str | None = None
    version_output_sha256: str | None = None
    version_probe_exit_code: int | None = None
    version_probe_timed_out: bool = False


RuntimeProbe = Callable[
    [RuntimeCommand, str],
    Awaitable[RuntimeCapabilityProbe],
]


async def collect_native_runtime_capabilities(
    runtimes: dict[str, RuntimeCommand],
    *,
    collected_at: datetime | None = None,
    platform: str | None = None,
    probe: RuntimeProbe | None = None,
) -> NativeRuntimeCapabilityObservation:
    """Observe configured native adapters without changing routing or durable state."""
    if not runtimes:
        raise ValueError("at least one configured runtime is required")
    platform_name = platform or sys.platform
    collected = collected_at or datetime.now(timezone.utc)
    if collected.tzinfo is None or collected.utcoffset() is None:
        raise ValueError("collected_at must include a timezone")
    observe = probe or _probe_runtime
    adapter_names = sorted(runtimes)
    probe_tasks = [
        asyncio.create_task(observe(runtimes[name], platform_name))
        for name in adapter_names
    ]
    try:
        probes = await asyncio.gather(*probe_tasks)
    except BaseException:
        for task in probe_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*probe_tasks, return_exceptions=True)
        raise
    adapter_observations = [
        _adapter_observation(runtimes[name], result)
        for name, result in zip(adapter_names, probes, strict=True)
    ]
    registry_facts = {
        name: {
            "runtime_command_sha256": canonical_sha256(
                list(runtimes[name].command_template)
            ),
            "version_command_sha256": (
                canonical_sha256(list(runtimes[name].version_command))
                if runtimes[name].version_command is not None
                else None
            ),
            "declared_models": list(runtimes[name].declared_models),
            "result_format": runtimes[name].result_format.value,
        }
        for name in adapter_names
    }
    payload = {
        "schema_version": "1.0",
        "collected_at": collected,
        "collector_id": "agora-native-runtime-capability-observer",
        "collector_version": "1.0",
        "platform": platform_name,
        "runtime_registry_sha256": canonical_sha256(registry_facts),
        "capability_declaration_id": ROUTING_POLICY_ID,
        "capability_declaration_version": ROUTING_POLICY_VERSION,
        "capability_declaration_sha256": ROUTING_POLICY_SHA256,
        "adapters": [
            item.model_dump(mode="json") for item in adapter_observations
        ],
        "routing_authority": False,
    }
    return NativeRuntimeCapabilityObservation.model_validate(
        seal_model_payload(NativeRuntimeCapabilityObservation, payload)
    )


def _adapter_observation(
    runtime: RuntimeCommand,
    probe: RuntimeCapabilityProbe,
) -> RuntimeCapabilityAdapterObservation:
    declared_models = sorted(runtime.declared_models)
    return RuntimeCapabilityAdapterObservation(
        adapter=runtime.adapter,
        provider=NATIVE_ADAPTER_PROVIDERS.get(runtime.adapter, "unknown"),
        installation_status=probe.installation_status,
        version=probe.version,
        version_status=probe.version_status,
        version_method=probe.version_method,
        model_availability="declared" if declared_models else "unavailable",
        declared_models=declared_models,
        declared_capabilities=sorted(RUNTIME_CAPABILITIES.get(runtime.adapter, ())),
        runtime_command_sha256=canonical_sha256(list(runtime.command_template)),
        version_command_sha256=(
            canonical_sha256(list(runtime.version_command))
            if runtime.version_command is not None
            else None
        ),
        resolved_runtime_command_sha256=probe.resolved_runtime_command_sha256,
        resolved_version_command_sha256=probe.resolved_version_command_sha256,
        version_output_sha256=probe.version_output_sha256,
        version_probe_exit_code=probe.version_probe_exit_code,
        version_probe_timed_out=probe.version_probe_timed_out,
    )


async def _probe_runtime(
    runtime: RuntimeCommand,
    platform: str,
) -> RuntimeCapabilityProbe:
    try:
        executable = shutil.which(runtime.command_template[0])
        if executable is None:
            candidate = Path(runtime.command_template[0])
            if not candidate.is_file():
                return RuntimeCapabilityProbe(
                    installation_status="not_found",
                    version=None,
                    version_status="unavailable",
                    version_method="not_installed",
                )
            executable = str(candidate)
    except OSError:
        return RuntimeCapabilityProbe(
            installation_status="uninspectable",
            version=None,
            version_status="unavailable",
            version_method="probe_failed",
        )
    try:
        resolved_runtime = resolve_runtime_command([executable], platform=platform)
    except RuntimeLaunchError:
        return RuntimeCapabilityProbe(
            installation_status="uninspectable",
            version=None,
            version_status="unavailable",
            version_method="probe_failed",
        )
    resolved_runtime_sha256 = canonical_sha256(resolved_runtime)
    if runtime.version_command is None:
        return RuntimeCapabilityProbe(
            installation_status="installed",
            version=None,
            version_status="unavailable",
            version_method="not_configured",
            resolved_runtime_command_sha256=resolved_runtime_sha256,
        )
    try:
        resolved_version = resolve_runtime_command(
            list(runtime.version_command),
            platform=platform,
        )
    except RuntimeLaunchError:
        return RuntimeCapabilityProbe(
            installation_status="installed",
            version=None,
            version_status="unavailable",
            version_method="probe_failed",
            resolved_runtime_command_sha256=resolved_runtime_sha256,
        )
    resolved_version_sha256 = canonical_sha256(resolved_version)
    try:
        environment = dict(os.environ)
        proxy_names = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
        for name in list(environment):
            if name.upper() in proxy_names:
                environment.pop(name)
        environment["AGORA_ORCHESTRATION_MODE"] = (
            "runtime_capability_observation"
        )
        process = await asyncio.create_subprocess_exec(
            *resolved_version,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
    except (FileNotFoundError, OSError):
        return RuntimeCapabilityProbe(
            installation_status="installed",
            version=None,
            version_status="unavailable",
            version_method="probe_failed",
            resolved_runtime_command_sha256=resolved_runtime_sha256,
            resolved_version_command_sha256=resolved_version_sha256,
        )

    stdout_capture = asyncio.create_task(_read_bounded_prefix(process.stdout))
    stderr_capture = asyncio.create_task(_read_bounded_prefix(process.stderr))
    timed_out = False
    try:
        exit_code = await asyncio.wait_for(
            process.wait(),
            timeout=VERSION_PROBE_TIMEOUT_SECONDS,
        )
    except asyncio.CancelledError:
        await _stop_probe(process)
        await _cancel_probe_capture(stdout_capture, stderr_capture)
        raise
    except (TimeoutError, asyncio.TimeoutError):
        timed_out = True
        await _stop_probe(process)
        exit_code = None
    captures = await _finish_probe_capture(stdout_capture, stderr_capture)
    if captures is None:
        stdout = b""
        stderr = b""
        output_truncated = True
    else:
        (stdout, stdout_truncated), (stderr, stderr_truncated) = captures
        output_truncated = stdout_truncated or stderr_truncated
    output_sha256 = (
        hashlib.sha256(stdout + b"\0" + stderr).hexdigest()
        if stdout or stderr
        else None
    )
    version = (
        _version_line(stdout, stderr)
        if exit_code == 0 and not timed_out and not output_truncated
        else None
    )
    if version is not None:
        return RuntimeCapabilityProbe(
            installation_status="installed",
            version=version,
            version_status="exact",
            version_method="native_version_command",
            resolved_runtime_command_sha256=resolved_runtime_sha256,
            resolved_version_command_sha256=resolved_version_sha256,
            version_output_sha256=output_sha256,
            version_probe_exit_code=exit_code,
        )
    return RuntimeCapabilityProbe(
        installation_status="installed",
        version=None,
        version_status="unavailable",
        version_method="probe_failed",
        resolved_runtime_command_sha256=resolved_runtime_sha256,
        resolved_version_command_sha256=resolved_version_sha256,
        version_output_sha256=output_sha256,
        version_probe_exit_code=exit_code,
        version_probe_timed_out=timed_out,
    )


async def _read_bounded_prefix(
    stream: asyncio.StreamReader | None,
) -> tuple[bytes, bool]:
    if stream is None:
        return b"", False
    captured = bytearray()
    truncated = False
    while True:
        chunk = await stream.read(2 * 1024)
        if not chunk:
            break
        remaining = VERSION_OUTPUT_LIMIT - len(captured)
        if remaining > 0:
            captured.extend(chunk[:remaining])
        if len(chunk) > remaining:
            truncated = True
    return bytes(captured), truncated


async def _finish_probe_capture(
    stdout_capture: asyncio.Task[tuple[bytes, bool]],
    stderr_capture: asyncio.Task[tuple[bytes, bool]],
) -> tuple[tuple[bytes, bool], tuple[bytes, bool]] | None:
    try:
        results = await asyncio.wait_for(
            asyncio.gather(stdout_capture, stderr_capture),
            timeout=VERSION_CAPTURE_DRAIN_TIMEOUT_SECONDS,
        )
        return results[0], results[1]
    except (TimeoutError, asyncio.TimeoutError):
        await _cancel_probe_capture(stdout_capture, stderr_capture)
        return None


async def _cancel_probe_capture(
    *captures: asyncio.Task[tuple[bytes, bool]],
) -> None:
    for capture in captures:
        capture.cancel()
    await asyncio.gather(*captures, return_exceptions=True)


async def _stop_probe(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except (TimeoutError, asyncio.TimeoutError):
        try:
            process.kill()
        except ProcessLookupError:
            return
        await process.wait()


def _version_line(stdout: bytes, stderr: bytes) -> str | None:
    decoded = stdout.decode("utf-8", errors="replace")
    if not decoded.strip():
        decoded = stderr.decode("utf-8", errors="replace")
    for line in decoded.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        if len(candidate) > 200 or any(ord(char) < 32 or ord(char) == 127 for char in candidate):
            return None
        return candidate
    return None
